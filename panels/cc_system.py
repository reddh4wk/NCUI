#!/usr/bin/env python3
import json
import os
import re
import time
from core import probe
from core.config import CONFIG, THEME
from core.i18n import get_active_language
from core.state import STATE
from ui.canvas import Canvas
from ui.kit import ActionBtn, Badge, Box, Divider, ProgressBar, SelectPill, Sparkline, StateBadge, Txt
from ui.markup import format_bytes_short

CPU_BAR_SAMPLE = "/tmp/nc_cpu_bar_sample.json"


def read_proc_stat() -> dict:
    res = {}
    try:
        with open("/proc/stat", "r", encoding="utf-8") as f:
            for line in f:
                if not line.startswith("cpu"):
                    continue
                parts = line.split()
                vals = [int(x) for x in parts[1:]]
                idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
                res[parts[0]] = (idle, sum(vals))
    except Exception:
        pass
    return res


def get_cpu_bar_pct() -> float:
    curr = read_proc_stat().get("cpu", (0, 0))
    prev = curr
    if os.path.exists(CPU_BAR_SAMPLE):
        try:
            with open(CPU_BAR_SAMPLE, "r", encoding="utf-8") as f:
                prev = tuple(json.load(f))
        except Exception:
            pass
    try:
        with open(CPU_BAR_SAMPLE, "w", encoding="utf-8") as f:
            json.dump(list(curr), f)
    except Exception:
        pass
    d_tot = curr[1] - prev[1]
    return 0.0 if d_tot <= 0 else (1.0 - (curr[0] - prev[0]) / float(d_tot)) * 100.0


def get_cpu_telemetry(prev_stat: dict) -> dict:
    curr_stat = read_proc_stat()
    if not prev_stat:
        time.sleep(0.05)
        prev_stat = curr_stat
        curr_stat = read_proc_stat()
    usage = {}
    for k, (c_idle, c_tot) in curr_stat.items():
        p_idle, p_tot = prev_stat.get(k, (c_idle, c_tot))
        d_tot = c_tot - p_tot
        d_idle = c_idle - p_idle
        usage[k] = max(0.0, min(100.0, (1.0 - (d_idle / float(d_tot))) * 100.0)) if d_tot > 0 else 0.0
    core_keys = sorted([k for k in usage if k != "cpu" and k[3:].isdigit()], key=lambda x: int(x[3:]))
    cores_pct = [usage[k] for k in core_keys]
    model_name = "CPU"
    try:
        with open("/proc/cpuinfo", "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("model name"):
                    raw_m = re.sub(r"\b(AMD|Processor|\d+-Core)\b", "", line.split(":", 1)[1]).strip()
                    model_name = re.sub(r"\s+", " ", raw_m)[:24]
                    break
    except Exception:
        pass
    freqs_khz = []
    try:
        for entry in os.listdir("/sys/devices/system/cpu"):
            if re.match(r"^cpu\d+$", entry):
                p = f"/sys/devices/system/cpu/{entry}/cpufreq/scaling_cur_freq"
                if os.path.exists(p):
                    freqs_khz.append(int(open(p, "r", encoding="utf-8").read().strip()))
    except Exception:
        pass
    max_ghz = (max(freqs_khz) / 1000000.0) if freqs_khz else 0.0
    temp_c = 0.0
    try:
        hwmon_base = "/sys/class/hwmon"
        fallback_temp = 0.0
        if os.path.exists(hwmon_base):
            for hm in os.listdir(hwmon_base):
                hm_dir = os.path.join(hwmon_base, hm)
                name_p = os.path.join(hm_dir, "name")
                hm_name = open(name_p, "r", encoding="utf-8").read().strip() if os.path.exists(name_p) else ""
                t_p = os.path.join(hm_dir, "temp1_input")
                if os.path.exists(t_p):
                    val = int(open(t_p, "r", encoding="utf-8").read().strip()) / 1000.0
                    if hm_name in ("k10temp", "coretemp", "zenpower"):
                        temp_c = val
                        break
                    elif fallback_temp == 0.0:
                        fallback_temp = val
        if temp_c == 0.0:
            temp_c = fallback_temp
    except Exception:
        pass
    try:
        loads = os.getloadavg()
    except Exception:
        loads = (0.0, 0.0, 0.0)
    return {
        "stat": curr_stat,
        "total_pct": usage.get("cpu", 0.0),
        "cores_pct": cores_pct,
        "model": model_name,
        "max_ghz": max_ghz,
        "temp_c": temp_c,
        "loads": loads,
    }


def get_ram_telemetry() -> dict:
    mem = {}
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as f:
            for line in f:
                if ":" in line:
                    k, v = line.split(":", 1)
                    parts = v.strip().split()
                    if parts and parts[0].isdigit():
                        mem[k.strip()] = int(parts[0]) * 1024
    except Exception:
        pass
    tot = mem.get("MemTotal", 1)
    avail = mem.get("MemAvailable", 0)
    used = max(0, tot - avail)
    sw_tot = mem.get("SwapTotal", 0)
    sw_used = max(0, sw_tot - mem.get("SwapFree", 0))
    return {
        "total": tot,
        "avail": avail,
        "used": used,
        "pct": (used / float(tot)) * 100.0,
        "cache": mem.get("Cached", 0) + mem.get("SReclaimable", 0) + mem.get("Buffers", 0),
        "dirty": mem.get("Dirty", 0),
        "sw_total": sw_tot,
        "sw_used": sw_used,
        "sw_pct": (sw_used / float(sw_tot)) * 100.0 if sw_tot > 0 else 0.0,
    }


def get_process_list(sort_by: str = "cpu", group_by_app: bool = False) -> list[dict]:
    sort_flag = "-pcpu" if sort_by == "cpu" else "-rss"
    ok, out = probe.run_cmd(["ps", "-eo", "pid=,pcpu=,pmem=,rss=,nlwp=,stat=,comm=,args=", "--sort", sort_flag], timeout=2.0)
    if not ok or not out:
        return []
    procs = []
    for line in out.splitlines():
        parts = line.strip().split(None, 7)
        if len(parts) < 7:
            continue
        pid_s, cpu_s, mem_s, rss_s, nlwp_s, stat_s, comm = parts[:7]
        args = parts[7] if len(parts) > 7 else comm
        if comm == "ps":
            continue
        try:
            pid = int(pid_s)
            procs.append({
                "pid": pid,
                "pids": [pid],
                "cpu": float(cpu_s),
                "mem": float(mem_s),
                "rss": int(rss_s) * 1024,
                "nlwp": int(nlwp_s),
                "stat": stat_s,
                "comm": comm,
                "args": args,
                "count": 1,
            })
        except ValueError:
            continue
    if not group_by_app:
        return procs
    grouped = {}
    for p in procs:
        k = p["comm"]
        if k not in grouped:
            grouped[k] = dict(p, pids=list(p["pids"]))
        else:
            g = grouped[k]
            g["pids"].append(p["pid"])
            g["cpu"] += p["cpu"]
            g["mem"] += p["mem"]
            g["rss"] += p["rss"]
            g["nlwp"] += p["nlwp"]
            g["count"] += 1
    res = list(grouped.values())
    res.sort(key=lambda x: x["cpu"] if sort_by == "cpu" else x["rss"], reverse=True)
    return res


def signal_processes(pids_csv: str, force_kill: bool = False) -> None:
    sig = "-9" if force_kill else "-15"
    pids = [p for p in str(pids_csv).split(",") if p.isdigit()]
    if pids:
        probe.run_cmd(["kill", sig] + pids, timeout=1.5)


# =====================================================================
# CONTROL CENTER SYSTEM BACKEND & DUAL VIEW
# =====================================================================

def _set_sys_fb(ok: bool, msg: str) -> None:
    STATE.set("system", "cc_fb", {"ok": bool(ok), "msg": str(msg), "ts": time.time()})


def _get_sys_fb() -> dict:
    fb = STATE.get("system", "cc_fb", {})
    if isinstance(fb, dict) and fb.get("msg") and (time.time() - float(fb.get("ts", 0))) < 15.0:
        return fb
    return {}


def _feedback_cards() -> list:
    fb = _get_sys_fb()
    if not fb:
        return []
    b = Box("System Control Status", icon="\uf00c" if fb["ok"] else "\uf071")
    b.add_row(" ")
    b.add_row(f"  {Txt(' ' + fb['msg'] + ' ', role='vitality' if fb['ok'] else 'fault', bg=True)}")
    b.add_row(" ")
    return [b]


def _read_uptime() -> str:
    try:
        sec = int(float(open("/proc/uptime").read().split()[0]))
        d, r = divmod(sec, 86400)
        h, r = divmod(r, 3600)
        m, _ = divmod(r, 60)
        return f"{d}d {h:02d}h {m:02d}m" if d else f"{h:02d}h {m:02d}m"
    except Exception:
        return "N/A"


def _collect_about_data() -> dict:
    un = os.uname()
    kernels = []
    ok, out = probe.run_cmd(["pacman", "-Q", "linux-cachyos", "linux-cachyos-hardened"], timeout=1.0)
    if ok and out:
        kernels = [l.strip() for l in out.splitlines() if l.strip()]
    limine_ver = "N/A"
    ok_l, out_l = probe.run_cmd(["pacman", "-Q", "limine"], timeout=0.8)
    if ok_l and out_l:
        limine_ver = out_l.strip()
    niri_ver = "N/A"
    if probe.has_bin("niri"):
        ok_n, out_n = probe.run_cmd(["niri", "--version"], timeout=0.8)
        if ok_n and out_n:
            niri_ver = out_n.strip()
    return {
        "host": un.nodename,
        "kernel_rel": un.release,
        "uptime": _read_uptime(),
        "kernels": kernels,
        "limine": limine_ver,
        "niri": niri_ver,
    }


def _discover_nc_scripts() -> list[dict]:
    scripts = []
    for base in ("/usr/local/sbin", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "utils")):
        if os.path.isdir(base):
            for fn in sorted(os.listdir(base)):
                if fn.startswith("nc") and fn.endswith(".sh"):
                    scripts.append({"name": fn, "path": os.path.join(base, fn), "scope": "sbin" if "/sbin" in base else "utils"})
    return scripts


def _is_nas_mounted() -> bool:
    try:
        mounts = open("/proc/mounts", "r", encoding="utf-8").read()
        return "/nas" in mounts or "10.10.0.20" in mounts
    except Exception:
        return False


def _collect_storage_data() -> dict:
    blk = []
    ok, out = probe.run_cmd(["lsblk", "-J", "-o", "NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT,FSUSE%"], timeout=1.5)
    if ok and out:
        try:
            def _walk(nodes):
                for n in nodes:
                    if n.get("type") in ("disk", "part", "lvm", "crypt"):
                        blk.append(n)
                    if n.get("children"):
                        _walk(n["children"])
            _walk(json.loads(out).get("blockdevices", []))
        except Exception:
            pass
    lvs = []
    if probe.has_bin("lvs"):
        ok_lv, out_lv = probe.run_cmd(["lvs", "--noheadings", "-o", "vg_name,lv_name,lv_size,data_percent,lv_attr"], timeout=1.5)
        if ok_lv and out_lv:
            lvs = [l.strip().split() for l in out_lv.splitlines() if l.strip()]
    nas_cfg = CONFIG.get("storage", {}).get("nas", {})
    return {
        "blk": blk,
        "lvs": lvs,
        "nas_host": nas_cfg.get("host", "10.10.0.20"),
        "nas_mounted": _is_nas_mounted(),
    }


def _collect_backup_data() -> dict:
    nas_mounted = _is_nas_mounted()
    snaps = []
    if os.path.isdir("/var/lib/nightcity/snapshots"):
        try:
            snaps = sorted(os.listdir("/var/lib/nightcity/snapshots"))
        except Exception:
            pass
    hanoi_env = {}
    if os.path.exists("/var/lib/nc-nas-conf/status.env"):
        try:
            for l in open("/var/lib/nc-nas-conf/status.env").read().splitlines():
                if "=" in l and not l.startswith("#"):
                    k, v = l.split("=", 1)
                    hanoi_env[k.strip()] = v.strip().strip('"')
        except Exception:
            pass
    return {
        "nas_mounted": nas_mounted,
        "snaps": snaps,
        "hanoi_env": hanoi_env,
        "duskcity_exists": os.path.exists("/efi/EFI/Linux/duskcity.efi") or os.path.exists("/boot/EFI/Linux/duskcity.efi"),
    }


# =====================================================================
# SUBTAB RENDERERS
# =====================================================================

def _build_about_standard(cc_panel, canvas: Canvas, is_it: bool) -> list:
    ab = _collect_about_data()
    cpu = get_cpu_telemetry({})
    ram = get_ram_telemetry()
    cards = _feedback_cards()

    b = Box("Nightcity Workstation — Info di Sistema" if is_it else "Nightcity Workstation — About This PC", icon="\uf129", elected=True)
    b.add_row(" ")
    b.add_kv("Hostname / OS", Txt(f"{ab['host']} (Arch Linux x86_64)", role="focus"))
    b.add_kv("Active Kernel", Txt(ab["kernel_rel"], role="vitality"))
    b.add_kv("Dual Kernels", Txt(" | ".join(ab["kernels"]) or "linux-cachyos / hardened", role="val"))
    b.add_kv("Bootloader / WM", Txt(f"{ab['limine']}  |  {ab['niri']}", role="schema"))
    b.add_kv("CPU / RAM / Uptime", Txt(f"{cpu['model']}  |  {ram['total']//(1024**3)} GiB  |  {ab['uptime']}", role="focus"))
    b.add_row(" ")
    cards.append(b)
    return cards



def _build_power_tools_standard(cc_panel, canvas: Canvas, is_it: bool) -> list:
    scripts = _discover_nc_scripts()
    cards = _feedback_cards()

    b = Box("NC Power Tools (/usr/local/sbin/nc-* & utils)", icon="\uf0ad", elected=True)
    b.add_row(" ")
    rows = []
    for sc in scripts:
        sc_p = sc["path"]
        rows.append([
            Txt(sc["name"], role="focus"),
            Badge(sc["scope"].upper(), style="info" if sc["scope"] == "sbin" else "secondary"),
            Txt(canvas.truncate_plain(sc_p, 34), role="schema"),
            f"{ActionBtn('Run Term', f'cc_sys:run_script:{sc_p}', tone='apply')} {ActionBtn('Args..', f'cc_sys:prompt_script:{sc_p}', tone='select')}",
        ])
    b.add_table(headers=["SCRIPT", "SCOPE", "PATH", "ACTIONS"], widths=[22, 8, 30, "flex"], rows=rows, aligns=["left", "left", "left", "right"])
    b.add_row(" ")
    cards.append(b)
    return cards


def _build_resources_standard(cc_panel, canvas: Canvas, is_it: bool) -> list:
    cpu = get_cpu_telemetry({})
    ram = get_ram_telemetry()
    sort_by = str(STATE.get("system", "cc_proc_sort", "cpu"))
    procs = get_process_list(sort_by=sort_by, group_by_app=False)[:10]
    cards = _feedback_cards()

    top = Box(
        f"CPU, RAM & Swap Telemetry — {cpu['model']}",
        icon="\uf2db",
        actions=[ActionBtn("Sync Disks", "cc_sys:sync_disks", tone="apply")],
        elected=True,
    )
    top.add_row(" ")
    c_pct, c_tmp, c_ghz = cpu["total_pct"], cpu["temp_c"], cpu["max_ghz"]
    r_used_g, r_tot_g = ram["used"] / (1024 ** 3), ram["total"] / (1024 ** 3)
    s_used_g, s_tot_g = ram["sw_used"] / (1024 ** 3), ram["sw_total"] / (1024 ** 3)
    top.add_kv("CPU Load", f"{ProgressBar(c_pct, width=22)} {Txt(f'{c_pct:4.1f}%', role='vitality')}  {Txt(f'{c_tmp:.1f}C  {c_ghz:.2f}GHz', role='schema')}")
    top.add_kv("Core Sparkline", Sparkline(cpu["cores_pct"], max_items=32))
    top.add_kv("RAM Usage", f"{ProgressBar(ram['pct'], width=22)} {Txt(f'{r_used_g:.1f}/{r_tot_g:.1f} GiB', role='intent')}")
    top.add_kv("Swap / ZRAM", f"{ProgressBar(ram['sw_pct'], width=22)} {Txt(f'{s_used_g:.1f}/{s_tot_g:.1f} GiB', role='val')}")

    def _build_sysctl(bx: Box):
        swp = open("/proc/sys/vm/swappiness").read().strip() if os.path.exists("/proc/sys/vm/swappiness") else "60"
        drt = open("/proc/sys/vm/dirty_ratio").read().strip() if os.path.exists("/proc/sys/vm/dirty_ratio") else "20"
        bx.add_kv("vm.swappiness", Txt(swp, role="focus"), right_markup=" ".join(SelectPill(v, f"cc_sys:sysctl:vm.swappiness:{v}", selected=(swp == v)) for v in ("10", "60", "100")))
        bx.add_kv("vm.dirty_ratio", Txt(drt, role="focus"), right_markup=" ".join(SelectPill(v, f"cc_sys:sysctl:vm.dirty_ratio:{v}", selected=(drt == v)) for v in ("5", "10", "20")))

    top.add_collapsible(cc_panel, "sys_sysctl", "Parametri Kernel sysctl (ZRAM / VM)" if is_it else "Kernel sysctl VM Tuning", _build_sysctl)
    top.add_row(" ")
    cards.append(top)

    p_box = Box(
        "Top Processi Attivi" if is_it else "Top Active Processes",
        icon="\uf0ae",
        actions=[SelectPill("Sort CPU", "cc_sys:proc_sort:cpu", selected=(sort_by == "cpu")), SelectPill("Sort RAM", "cc_sys:proc_sort:ram", selected=(sort_by == "ram"))],
    )
    p_box.add_row(" ")
    rows = []
    for p in procs:
        p_pid = p["pid"]
        p_cpu = p["cpu"]
        rows.append([
            Txt(str(p_pid), role="schema"),
            Txt(p["comm"][:18], role="focus"),
            Txt(f"{p_cpu:5.1f}%", role="vitality"),
            Txt(format_bytes_short(p["rss"]), role="intent"),
            f"{ActionBtn('TERM', f'cc_sys:term:{p_pid}', tone='warn')} {ActionBtn('KILL', f'cc_sys:kill:{p_pid}', tone='danger_soft')}",
        ])
    p_box.add_table(headers=["PID", "PROCESS", "CPU%", "RSS", "SIGNAL"], widths=[8, 22, 10, 10, "flex"], rows=rows, aligns=["left", "left", "right", "right", "right"])
    p_box.add_row(" ")
    cards.append(p_box)
    return cards


def _build_storage_standard(cc_panel, canvas: Canvas, is_it: bool) -> list:
    st = _collect_storage_data()
    cards = _feedback_cards()

    nas_box = Box(
        f"NASUS Storage ({st['nas_host']})",
        icon="\uf233",
        actions=[StateBadge(st["nas_mounted"], "MOUNTED", "UNMOUNTED"), ActionBtn("Mount NAS", "cc_sys:mount_nas", tone="apply")],
        elected=st["nas_mounted"],
    )
    nas_box.add_row(" ")
    nas_box.add_kv("Mount Target", Txt("/nas/* (SMB/CIFS)", role="val"))
    nas_box.add_kv("SMART Health", ActionBtn("Run NVMe SMART Check", "cc_sys:smart_check", tone="select"), requires=["smartctl"])
    nas_box.add_row(" ")
    cards.append(nas_box)

    b_box = Box(f"Dischi, Partizioni & Volumi LVM ({len(st['blk'])})" if is_it else f"Block Devices & LVM ({len(st['blk'])})", icon="\uf0a0")
    b_box.add_row(" ")
    rows = []
    for d in st["blk"][:12]:
        rows.append([
            Txt(d.get("name", "")[:16], role="focus"),
            Txt(d.get("size", ""), role="val"),
            Txt(d.get("fstype") or d.get("type", ""), role="schema"),
            Txt(canvas.truncate_plain(d.get("mountpoint") or "-", 24), role="vitality" if d.get("mountpoint") else "inactive"),
            Txt(d.get("fsuse%") or "-", role="attention"),
        ])
    b_box.add_table(headers=["DEVICE", "SIZE", "FS", "MOUNTPOINT", "USE%"], widths=[16, 8, 12, 24, "flex"], rows=rows)

    if st["lvs"]:
        def _build_lvm(bx: Box):
            lv_rows = [[Txt(r[0], role="schema"), Txt(r[1], role="focus"), Txt(r[2], role="val"), Txt(r[3] if len(r) > 3 else "-", role="intent")] for r in st["lvs"][:10] if len(r) >= 3]
            bx.add_table(headers=["VG", "LV", "SIZE", "DATA%"], widths=[14, 22, 10, "flex"], rows=lv_rows)
        b_box.add_collapsible(cc_panel, "sys_lvm", f"Volumi Logici LVM ({len(st['lvs'])})", _build_lvm)

    b_box.add_row(" ")
    cards.append(b_box)
    return cards


# =====================================================================
# PUBLIC ENTRY POINTS
# =====================================================================

def build_cc_system_cards(cc_panel, canvas: Canvas, active_sub: str = "about") -> list:
    is_it = (get_active_language() == "it")
    if active_sub == "power_tools":
        return _build_power_tools_standard(cc_panel, canvas, is_it)
    if active_sub in ("resources", "cpu", "memory"):
        return _build_resources_standard(cc_panel, canvas, is_it)
    if active_sub == "storage":
        return _build_storage_standard(cc_panel, canvas, is_it)
    if active_sub == "backup":
        return _build_backup_standard(cc_panel, canvas, is_it)
    return _build_about_standard(cc_panel, canvas, is_it)


def handle_cc_system_link(cc_panel, uri: str) -> bool:
    if not uri.startswith("cc_sys:"):
        return False
    act = uri[len("cc_sys:"):]

    if act == "sync_disks":
        probe.run_cmd_full(["sync"], timeout=2.0)
        _set_sys_fb(True, "Filesystem buffers synced")
    elif act.startswith("proc_sort:"):
        STATE.set("system", "cc_proc_sort", act.split(":", 1)[1])
    elif act.startswith("term:") or act.startswith("kill:"):
        op, pid = act.split(":", 1)
        signal_processes(pid, force_kill=(op == "kill"))
        _set_sys_fb(True, f"Sent SIG{'KILL' if op == 'kill' else 'TERM'} to PID {pid}")
    elif act.startswith("sysctl:"):
        _, key, val = act.split(":", 2)
        ok, _o, err = probe.run_cmd_full(["pkexec", "sysctl", "-w", f"{key}={val}"], timeout=4.0)
        _set_sys_fb(ok, f"{key}={val}" if ok else (err or "sysctl failed"))
    elif act.startswith("run_script:"):
        sp = act.split(":", 1)[1]
        probe.open_terminal(["bash", "-c", f"{sp}; echo; read -p 'Done. Press Enter...'"])
        return True
    elif act.startswith("prompt_script:"):
        sp = act.split(":", 1)[1]
        cc_panel.request_prompt(f"cc_sys_args|{sp}", f"Arguments for {os.path.basename(sp)}:", "", title=os.path.basename(sp))
        return True
    elif act == "mount_nas":
        probe.open_terminal(["bash", "-c", "sudo /usr/local/sbin/nc-nas-mount.sh; read -p 'Press Enter...'"])
        return True
    elif act == "smart_check":
        probe.open_terminal(["bash", "-c", "for d in /dev/nvme*n1; do echo \"=== $d ===\"; sudo smartctl -H \"$d\"; done; read -p 'Press Enter...'"])
        return True
    elif act == "snap_create":
        probe.open_terminal(["bash", "-c", "sudo /usr/local/sbin/nc-snapshot.sh --help; read -p 'Press Enter...'"])
        return True
    elif act == "hanoi_backup":
        if not _is_nas_mounted():
            _set_sys_fb(False, "NAS not mounted. Mount NAS 10.10.0.20 first.")
        else:
            probe.open_terminal(["bash", "-c", "/usr/local/sbin/nc-nas-conf.sh; read -p 'Press Enter...'"])
            return True

    if cc_panel.shell:
        cc_panel.shell.refresh_view()
    return True


def handle_cc_system_prompt(cc_panel, action_id: str, text: str) -> bool:
    if action_id.startswith("cc_sys_args|"):
        sp = action_id.split("|", 1)[1]
        probe.open_terminal(["bash", "-c", f"{sp} {text}; echo; read -p 'Press Enter...'"])
        return True
    return False
