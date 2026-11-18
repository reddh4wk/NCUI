#!/usr/bin/env python3
import atexit
import ctypes
import fcntl
import glob
import json
import os
import pty
import re
import select
import shlex
import shutil
import signal
import struct
import subprocess
import termios
import threading
import time
import gi

gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib
from core import probe
from core.config import CONFIG, THEME
from core.i18n import get_active_language, t
from core.state import STATE
from ui.canvas import Canvas
from ui.kit import ActionBtn, Badge, Box, Divider, FeedbackBox, ProgressBar, SelectPill, Sparkline, StateBadge, Txt
from ui.markup import format_bytes_short

CPU_BAR_SAMPLE = "/tmp/nc_cpu_bar_sample.json"
_LIMINE_CANDIDATES = ("/boot/limine.conf", "/efi/limine/limine.conf", "/boot/limine/limine.conf")
_DEFAULT_ENTRIES = ["linux-cachyos", "linux-cachyos-hardened", "Golden Image Recovery", "Windows"]

_CPU_MODEL_CACHE: str | None = None
_CPU_FREQ_PATHS: list[str] | None = None
_CPU_GOV_PATH = "/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor"
_CPU_TEMP_PATH: str | None = None
_LAST_PROC_STAT: dict = {}


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
    global _CPU_MODEL_CACHE, _CPU_FREQ_PATHS, _CPU_TEMP_PATH, _LAST_PROC_STAT
    curr_stat = read_proc_stat()
    base_stat = prev_stat or _LAST_PROC_STAT
    if not base_stat:
        time.sleep(0.05)
        base_stat = curr_stat
        curr_stat = read_proc_stat()
    _LAST_PROC_STAT = curr_stat

    usage = {}
    for k, (c_idle, c_tot) in curr_stat.items():
        p_idle, p_tot = base_stat.get(k, (c_idle, c_tot))
        d_tot = c_tot - p_tot
        d_idle = c_idle - p_idle
        usage[k] = max(0.0, min(100.0, (1.0 - (d_idle / float(d_tot))) * 100.0)) if d_tot > 0 else 0.0
    core_keys = sorted([k for k in usage if k != "cpu" and k[3:].isdigit()], key=lambda x: int(x[3:]))
    cores_pct = [usage[k] for k in core_keys]

    if _CPU_MODEL_CACHE is None:
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
        _CPU_MODEL_CACHE = model_name

    if _CPU_FREQ_PATHS is None:
        paths = []
        try:
            for entry in os.listdir("/sys/devices/system/cpu"):
                if re.match(r"^cpu\d+$", entry):
                    p = f"/sys/devices/system/cpu/{entry}/cpufreq/scaling_cur_freq"
                    if os.path.exists(p):
                        paths.append(p)
        except Exception:
            pass
        _CPU_FREQ_PATHS = sorted(paths)

    freqs_khz = []
    for p in _CPU_FREQ_PATHS:
        try:
            with open(p, "r", encoding="utf-8") as f:
                freqs_khz.append(int(f.read().strip()))
        except Exception:
            pass
    max_ghz = (max(freqs_khz) / 1000000.0) if freqs_khz else 0.0
    avg_ghz = (sum(freqs_khz) / float(len(freqs_khz)) / 1000000.0) if freqs_khz else 0.0

    governor = ""
    try:
        if os.path.exists(_CPU_GOV_PATH):
            with open(_CPU_GOV_PATH, "r", encoding="utf-8") as f:
                governor = f.read().strip()
    except Exception:
        pass

    if _CPU_TEMP_PATH is None:
        found_temp = ""
        fallback_temp = ""
        try:
            hwmon_base = "/sys/class/hwmon"
            if os.path.exists(hwmon_base):
                for hm in os.listdir(hwmon_base):
                    hm_dir = os.path.join(hwmon_base, hm)
                    name_p = os.path.join(hm_dir, "name")
                    hm_name = open(name_p, "r", encoding="utf-8").read().strip() if os.path.exists(name_p) else ""
                    t_p = os.path.join(hm_dir, "temp1_input")
                    if os.path.exists(t_p):
                        if hm_name in ("k10temp", "coretemp", "zenpower"):
                            found_temp = t_p
                            break
                        elif not fallback_temp:
                            fallback_temp = t_p
        except Exception:
            pass
        _CPU_TEMP_PATH = found_temp or fallback_temp

    temp_c = 0.0
    if _CPU_TEMP_PATH:
        try:
            with open(_CPU_TEMP_PATH, "r", encoding="utf-8") as f:
                temp_c = int(f.read().strip()) / 1000.0
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
        "model": _CPU_MODEL_CACHE,
        "max_ghz": max_ghz,
        "avg_ghz": avg_ghz,
        "governor": governor,
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
        "shared": mem.get("Shmem", 0),
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
    return [FeedbackBox(t("cc_system.system_control_status"), bool(fb["ok"]), fb["msg"])]


from panels.cc_engine_sysinfo import _collect_about_data, _read_uptime


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


def handle_cc_system_link(cc_panel, uri: str) -> bool:
    if not uri.startswith("cc_sys:"):
        return False
    act = uri[len("cc_sys:"):]

    if act == "sync_disks":
        probe.run_cmd_full(["sync"], timeout=2.0)
        _set_sys_fb(True, t("cc_system.filesystem_buffers_synced"))
    elif act == "btop":
        probe.open_terminal(["btop"])
        return True
    elif act == "nvtop":
        probe.open_terminal(["nvtop"])
        return True
    elif act.startswith("tui_restart:"):
        tui_id = act.split(":", 1)[1]
        restart_tui_session(tui_id, cc_panel=cc_panel)
    elif act.startswith("tui_focus:"):
        tui_id = act.split(":", 1)[1]
        set_active_tui(tui_id)
        return True
    elif act.startswith("proc_sort:"):
        STATE.set("system", "cc_proc_sort", act.split(":", 1)[1])
    elif act.startswith("term:") or act.startswith("kill:"):
        op, pid = act.split(":", 1)
        signal_processes(pid, force_kill=(op == "kill"))
        _set_sys_fb(True, t("cc_system.sent_sig_to_pid", p0='KILL' if op == 'kill' else 'TERM', p1=pid))
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
        cc_panel.request_prompt(f"cc_sys_args|{sp}", t("cc_system.arguments_for", p0=os.path.basename(sp)), "", title=os.path.basename(sp), completion="path_file")
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
            _set_sys_fb(False, t("cc_system.nas_not_mounted_mount_nas_10_10_0"))
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


def _probe_limine() -> dict:
    conf_path = next((p for p in _LIMINE_CANDIDATES if os.path.exists(p)), "")
    timeout = "5"
    default_entry = "1"
    entries = []
    readable = False
    if conf_path:
        try:
            raw = open(conf_path, "r", encoding="utf-8", errors="ignore").read()
            readable = True
            m_to = re.search(r"(?m)^\s*timeout\s*:\s*(\d+)", raw)
            if m_to:
                timeout = m_to.group(1)
            m_df = re.search(r"(?m)^\s*default_entry\s*:\s*(\d+)", raw)
            if m_df:
                default_entry = m_df.group(1)
            for ln in raw.splitlines():
                s = ln.strip()
                if s.startswith("/"):
                    entries.append(s.lstrip("/").strip())
        except Exception:
            pass
    if not entries:
        entries = list(_DEFAULT_ENTRIES)
    ukis = sorted(glob.glob("/boot/EFI/Linux/*.efi") + glob.glob("/efi/EFI/Linux/*.efi"))
    snaps_n = len(glob.glob("/var/lib/nightcity/snapshots/*")) if os.path.isdir("/var/lib/nightcity/snapshots") else 0
    return {
        "conf_path": conf_path or "/efi/limine/limine.conf",
        "readable": readable,
        "timeout": timeout,
        "default_entry": default_entry,
        "entries": entries[:8],
        "ukis": ukis,
        "snaps": snaps_n,
    }


def handle_cc_telemetry_link(cc_panel, uri: str) -> bool:
    if uri.startswith("cc_set:limine_"):
        parts = uri.split(":", 2)
        cmd = parts[1]
        arg = parts[2] if len(parts) > 2 else ""
        if cmd == "limine_edit":
            cp = _probe_limine()["conf_path"]
            probe.open_terminal(["bash", "-c", f"sudo ${{EDITOR:-nano}} {shlex.quote(cp)}; echo; read -p 'Press Enter to close...'"])
            _set_sys_fb(True, t("cc_settings.opened_in_terminal_editor", p0=cp))
        elif cmd == "limine_to" and arg.isdigit():
            cp = _probe_limine()["conf_path"]
            probe.open_terminal(["bash", "-c", f"sudo sed -i 's/^timeout:.*/timeout: {int(arg)}/' {shlex.quote(cp)} && grep -n '^timeout:' {shlex.quote(cp)}; read -p 'Press Enter to close...'"])
            _set_sys_fb(True, t("cc_settings.launched_limine_timeout_update_s_i", p0=arg))
        elif cmd == "limine_def" and arg.isdigit():
            cp = _probe_limine()["conf_path"]
            probe.open_terminal(["bash", "-c", f"sudo sed -i 's/^default_entry:.*/default_entry: {int(arg)}/' {shlex.quote(cp)} && grep -n '^default_entry:' {shlex.quote(cp)}; read -p 'Press Enter to close...'"])
            _set_sys_fb(True, t("cc_settings.launched_limine_default_entry_upda", p0=arg))
        if cc_panel and cc_panel.shell:
            cc_panel.shell.refresh_view()
        return True
    return handle_cc_system_link(cc_panel, uri)


def handle_cc_telemetry_prompt(cc_panel, action_id: str, text: str) -> bool:
    return handle_cc_system_prompt(cc_panel, action_id, text)


class _VTermPos(ctypes.Structure):
    _fields_ = [("row", ctypes.c_int), ("col", ctypes.c_int)]


class _VTermColor(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_uint8),
        ("r", ctypes.c_uint8),
        ("g", ctypes.c_uint8),
        ("b", ctypes.c_uint8),
    ]


class _VTermScreenCell(ctypes.Structure):
    _fields_ = [
        ("chars", ctypes.c_uint32 * 6),
        ("width", ctypes.c_char),
        ("attrs", ctypes.c_uint32),
        ("fg", _VTermColor),
        ("bg", _VTermColor),
    ]


_LIBVTERM = None


def _get_libvterm():
    global _LIBVTERM
    if _LIBVTERM is not None:
        return _LIBVTERM
    lib = ctypes.CDLL("libvterm.so.0")
    lib.vterm_new.argtypes = [ctypes.c_int, ctypes.c_int]
    lib.vterm_new.restype = ctypes.c_void_p
    lib.vterm_free.argtypes = [ctypes.c_void_p]
    lib.vterm_set_size.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
    lib.vterm_set_utf8.argtypes = [ctypes.c_void_p, ctypes.c_int]
    lib.vterm_obtain_screen.argtypes = [ctypes.c_void_p]
    lib.vterm_obtain_screen.restype = ctypes.c_void_p
    lib.vterm_screen_enable_altscreen.argtypes = [ctypes.c_void_p, ctypes.c_int]
    lib.vterm_screen_reset.argtypes = [ctypes.c_void_p, ctypes.c_int]
    lib.vterm_input_write.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t]
    lib.vterm_input_write.restype = ctypes.c_size_t
    lib.vterm_output_read.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t]
    lib.vterm_output_read.restype = ctypes.c_size_t
    lib.vterm_screen_get_cell.argtypes = [ctypes.c_void_p, _VTermPos, ctypes.POINTER(_VTermScreenCell)]
    lib.vterm_screen_get_cell.restype = ctypes.c_int
    lib.vterm_screen_convert_color_to_rgb.argtypes = [ctypes.c_void_p, ctypes.POINTER(_VTermColor)]
    _LIBVTERM = lib
    return lib


def _tui_child_preexec() -> None:
    try:
        ctypes.CDLL("libc.so.6").prctl(1, signal.SIGKILL)
    except Exception:
        pass


_TUI_SESSIONS: dict = {}


def _ensure_btop_config() -> str:
    cfg_path = "/tmp/nc_btop.conf"
    if not os.path.exists(cfg_path):
        try:
            with open(cfg_path, "w", encoding="utf-8") as f:
                f.write(
                    "theme_background = False\n"
                    "truecolor = True\n"
                    "rounded_corners = True\n"
                    "update_ms = 1000\n"
                )
        except OSError:
            pass
    return cfg_path


_NVTOP_BIN_CACHE: tuple[str, str] | None = None


def _ensure_nvtop_bin() -> str:
    """
    Limita l'altezza massima del grafico superiore di `nvtop` a 15 righe
    (invece di 23) per riservare più spazio verticale alla tabella processi.
    Scrive in ~/.cache/nc-ui (mai su /tmp che può essere montato noexec) e verifica execve reale.
    """
    global _NVTOP_BIN_CACHE
    for stale in ("/tmp/nc_nvtop", "/tmp/nc_nvtop.meta"):
        if os.path.exists(stale):
            try:
                os.remove(stale)
            except OSError:
                pass

    src = shutil.which("nvtop") or "/usr/bin/nvtop"
    if not src or not os.path.exists(src):
        return "nvtop"
    cache_dir = os.path.join(os.path.expanduser("~"), ".cache", "nc-ui")
    dst = os.path.join(cache_dir, "nc_nvtop")
    meta_path = os.path.join(cache_dir, "nc_nvtop.meta")
    old_pat = bytes.fromhex("89c389c2b81700000039c30f46c3")
    new_pat = bytes.fromhex("89c389c2b80f00000039c30f46c3")
    try:
        st = os.stat(src)
        sig = f"{st.st_size}:{st.st_mtime_ns}:{dst}"
        if _NVTOP_BIN_CACHE is not None and _NVTOP_BIN_CACHE[0] == sig and os.path.exists(_NVTOP_BIN_CACHE[1]):
            return _NVTOP_BIN_CACHE[1]

        os.makedirs(cache_dir, exist_ok=True)
        need_write = True
        if os.path.exists(dst) and os.path.exists(meta_path) and os.access(dst, os.X_OK):
            with open(meta_path, "r", encoding="utf-8") as mf:
                if mf.read().strip() == sig:
                    need_write = False
        if need_write:
            with open(src, "rb") as f:
                raw = f.read()
            if raw.count(old_pat) != 1:
                _NVTOP_BIN_CACHE = (sig, src)
                return src
            patched = raw.replace(old_pat, new_pat, 1)
            tmp_dst = f"{dst}.tmp.{os.getpid()}"
            with open(tmp_dst, "wb") as f:
                f.write(patched)
            os.chmod(tmp_dst, 0o755)
            os.replace(tmp_dst, dst)
            with open(meta_path, "w", encoding="utf-8") as mf:
                mf.write(sig)

        try:
            rc = subprocess.run(
                [dst, "--version"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=1.5,
            ).returncode
            chosen = dst if rc == 0 else src
        except (PermissionError, OSError, subprocess.SubprocessError):
            chosen = src
        _NVTOP_BIN_CACHE = (sig, chosen)
        return chosen
    except OSError:
        return src


class EmbeddedTuiSession:
    """In-process libvterm + PTY session for interactive TUI panels inside nc-box."""

    def __init__(self, tui_id: str, cmd: list[str], rows: int = 24, cols: int = 96, cc_panel=None):
        self.tui_id = str(tui_id)
        self.cmd = list(cmd)
        self.rows = max(8, int(rows))
        self.cols = max(40, int(cols))
        self.cc_panel = cc_panel
        self.lock = threading.Lock()
        self.grid = self._empty_grid(self.rows, self.cols)
        self.master_fd = -1
        self.proc = None
        self.alive = False
        self.stopped = False
        self.on_redraw = None
        self._redraw_pending = False
        self._lib = _get_libvterm()
        self._vt = self._lib.vterm_new(self.rows, self.cols)
        self._lib.vterm_set_utf8(self._vt, 1)
        self._scr = self._lib.vterm_obtain_screen(self._vt)
        self._lib.vterm_screen_enable_altscreen(self._scr, 1)
        self._lib.vterm_screen_reset(self._scr, 1)

    @staticmethod
    def _empty_grid(rows: int, cols: int) -> list:
        return [[(" ", None, None, False, False) for _ in range(cols)] for _ in range(rows)]

    def _snapshot_locked(self) -> None:
        if not self._vt:
            return
        lib = self._lib
        scr = self._scr
        cell = _VTermScreenCell()
        pos = _VTermPos()
        new_grid = []
        for r in range(self.rows):
            pos.row = r
            row_cells = []
            for c in range(self.cols):
                pos.col = c
                if lib.vterm_screen_get_cell(scr, pos, ctypes.byref(cell)):
                    cp = cell.chars[0]
                    if 0 < cp <= 0x10FFFF:
                        ch = chr(cp)
                        for k in range(1, 6):
                            ck = cell.chars[k]
                            if 0 < ck <= 0x10FFFF:
                                ch += chr(ck)
                            else:
                                break
                    elif cp == 0xFFFFFFFF:
                        ch = ""
                    else:
                        ch = " "
                    fg = None
                    if not (cell.fg.type & 0x02):
                        lib.vterm_screen_convert_color_to_rgb(scr, ctypes.byref(cell.fg))
                        fg = (cell.fg.r / 255.0, cell.fg.g / 255.0, cell.fg.b / 255.0)
                    bg = None
                    if not (cell.bg.type & 0x04):
                        lib.vterm_screen_convert_color_to_rgb(scr, ctypes.byref(cell.bg))
                        bg = (cell.bg.r / 255.0, cell.bg.g / 255.0, cell.bg.b / 255.0)
                    bold = bool(cell.attrs & 0x01)
                    rev = bool(cell.attrs & 0x20)
                    row_cells.append((ch, fg, bg, bold, rev))
                else:
                    row_cells.append((" ", None, None, False, False))
            new_grid.append(row_cells)
        self.grid = new_grid

    def start(self) -> None:
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", self.rows, self.cols, 0, 0))
        env = os.environ.copy()
        env["TERM"] = "xterm-256color"
        env["COLORTERM"] = "truecolor"
        env["LANG"] = "en_US.UTF-8"
        env.setdefault("HOME", os.path.expanduser("~"))
        launch_cmds = [self.cmd]
        if self.tui_id == "nvtop":
            sys_nvtop = shutil.which("nvtop") or "/usr/bin/nvtop"
            if self.cmd and self.cmd[0] != sys_nvtop:
                launch_cmds.append([sys_nvtop, *self.cmd[1:]])

        self.proc = None
        for try_cmd in launch_cmds:
            try:
                self.proc = subprocess.Popen(
                    try_cmd,
                    stdin=slave,
                    stdout=slave,
                    stderr=slave,
                    close_fds=True,
                    env=env,
                    start_new_session=True,
                    preexec_fn=_tui_child_preexec,
                )
                break
            except (PermissionError, FileNotFoundError, OSError):
                self.proc = None

        if self.proc is None:
            os.close(slave)
            os.close(master)
            self.alive = True
            if self.tui_id == "btop":
                self._write_btop_fallback_frame()
                if os.environ.get("NC_UI_OFFSCREEN") != "1":
                    threading.Thread(target=self._fallback_btop_loop, daemon=True).start()
            elif self.tui_id == "nvtop":
                self._write_nvtop_fallback_frame()
            return
        os.close(slave)
        self.master_fd = master
        os.set_blocking(master, False)
        self.alive = True
        if os.environ.get("NC_UI_OFFSCREEN") == "1":
            self._pump_initial_offscreen(timeout_s=0.65)
        threading.Thread(target=self._read_loop, daemon=True).start()

    def _pump_initial_offscreen(self, timeout_s: float = 0.65) -> None:
        fd = self.master_fd
        outbuf = ctypes.create_string_buffer(4096)
        deadline = time.monotonic() + timeout_s
        has_visible = False
        while fd >= 0 and time.monotonic() < deadline:
            rem = deadline - time.monotonic()
            wait_s = min(0.08 if has_visible else 0.14, max(0.01, rem))
            r, _, _ = select.select([fd], [], [], wait_s)
            if not r:
                if has_visible:
                    break
                continue
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                break
            if not chunk:
                break
            with self.lock:
                if not self._vt:
                    break
                self._lib.vterm_input_write(self._vt, chunk, len(chunk))
                n = self._lib.vterm_output_read(self._vt, outbuf, 4096)
                if n > 0:
                    try:
                        os.write(fd, outbuf.raw[:n])
                    except OSError:
                        pass
                self._snapshot_locked()
                if not has_visible:
                    has_visible = any(cell[0].strip() for row in self.grid for cell in row)

    def _read_loop(self) -> None:
        fd = self.master_fd
        outbuf = ctypes.create_string_buffer(4096)
        try:
            while self.alive and not self.stopped and fd >= 0:
                r, _, _ = select.select([fd], [], [], 0.25)
                if not r:
                    if self.proc and self.proc.poll() is not None:
                        break
                    continue
                try:
                    chunk = os.read(fd, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                with self.lock:
                    if not self._vt:
                        break
                    self._lib.vterm_input_write(self._vt, chunk, len(chunk))
                    n = self._lib.vterm_output_read(self._vt, outbuf, 4096)
                    if n > 0:
                        try:
                            os.write(fd, outbuf.raw[:n])
                        except OSError:
                            pass
                    self._snapshot_locked()
                self._request_redraw()
        finally:
            self.alive = False

    def _write_btop_fallback_frame(self) -> None:
        tele = get_cpu_telemetry({})
        rm = get_ram_telemetry()
        procs = get_process_list(sort_by="cpu", group_by_app=False)[: max(4, self.rows - 11)]
        try:
            st_vfs = os.statvfs("/")
            disk_tot = st_vfs.f_blocks * st_vfs.f_frsize
            disk_used = disk_tot - (st_vfs.f_bfree * st_vfs.f_frsize)
            disk_pct = (disk_used / float(disk_tot)) * 100.0 if disk_tot > 0 else 0.0
        except OSError:
            disk_tot, disk_used, disk_pct = 1, 0, 0.0

        def _bar(pct: float, w: int) -> str:
            filled = max(0, min(w, int(round((pct / 100.0) * w))))
            return ("|" * filled) + (" " * (w - filled))

        w = self.cols
        cpu_pct = tele["total_pct"]
        temp_c = tele["temp_c"]
        max_ghz = tele["max_ghz"]
        l1, l5, l15 = tele["loads"]
        ram_pct = rm["pct"]
        sw_pct = rm["sw_pct"]
        cores = tele["cores_pct"]
        spark = "".join("▁▂▃▄▅▆▇█"[min(7, int((c / 100.0) * 8))] for c in cores[:32])

        lines = ["\x1b[2J\x1b[H"]
        hdr = f" btop++  │ {tele['model']}  │ CPU {cpu_pct:4.1f}% │ {max_ghz:.2f} GHz │ {temp_c:.1f}°C │ LAV {l1:.2f} {l5:.2f} {l15:.2f} "
        lines.append(f"\x1b[1;38;2;0;240;255m┌─{hdr[:w-4].ljust(w-4, '─')}─┐\x1b[0m")
        cpu_line = f" CPU [{_bar(cpu_pct, 26)}] {cpu_pct:5.1f}%   Cores ({len(cores):02d}): {spark}"
        lines.append(f"\x1b[38;2;0;255;115m│\x1b[0m{cpu_line[:w-2].ljust(w-2)}\x1b[38;2;0;255;115m│\x1b[0m")
        lines.append(f"\x1b[38;2;67;73;92m├─{' mem & disks '.ljust(w-4, '─')}─┤\x1b[0m")
        mem_line = (
            f" MEM [{_bar(ram_pct, 16)}] {ram_pct:4.1f}% ({format_bytes_short(rm['used'])}/{format_bytes_short(rm['total'])})   "
            f"SWP [{_bar(sw_pct, 8)}] {sw_pct:4.0f}%   "
            f"/ [{_bar(disk_pct, 8)}] {disk_pct:4.0f}%"
        )
        lines.append(f"\x1b[38;2;0;240;255m│\x1b[0m{mem_line[:w-2].ljust(w-2)}\x1b[38;2;0;240;255m│\x1b[0m")
        lines.append(f"\x1b[38;2;67;73;92m├─{' proc (live /proc fallback) '.ljust(w-4, '─')}─┤\x1b[0m")
        ph = f" {'PID':>6}  {'Program':<18}  {'Threads':>7}  {'Mem':>8}  {'Cpu%':>6}  {'Command'}"
        lines.append(f"\x1b[1;38;2;140;155;186m│\x1b[0m\x1b[1;38;2;226;234;252m{ph[:w-2].ljust(w-2)}\x1b[0m\x1b[38;2;67;73;92m│\x1b[0m")
        n_procs_rows = max(1, self.rows - 7)
        for idx in range(n_procs_rows):
            if idx < len(procs):
                p = procs[idx]
                cmd_s = p["args"].replace("\n", " ").strip()[: max(8, w - 55)]
                row_s = f" {p['pid']:>6}  {p['comm'][:18]:<18}  {p['nlwp']:>7}  {format_bytes_short(p['rss']):>8}  {p['cpu']:5.1f}%  {cmd_s}"
                col_seq = "\x1b[38;2;0;255;115m" if p["cpu"] < 25.0 else "\x1b[38;2;255;235;25m"
                lines.append(f"\x1b[38;2;67;73;92m│\x1b[0m{col_seq}{row_s[:w-2].ljust(w-2)}\x1b[0m\x1b[38;2;67;73;92m│\x1b[0m")
            else:
                lines.append(f"\x1b[38;2;67;73;92m│{' ' * (w-2)}│\x1b[0m")
        lines.append(f"\x1b[38;2;67;73;92m└{'─' * (w-2)}┘\x1b[0m")
        payload = "\r\n".join(lines).encode("utf-8")
        with self.lock:
            if self._vt:
                self._lib.vterm_input_write(self._vt, payload, len(payload))
                self._snapshot_locked()
        self._request_redraw()

    def _fallback_btop_loop(self) -> None:
        while self.alive and not self.stopped:
            time.sleep(1.0)
            if not self.alive or self.stopped:
                break
            self._write_btop_fallback_frame()

    def _write_nvtop_fallback_frame(self) -> None:
        ok, out = probe.run_cmd(
            [
                "nvidia-smi",
                "--query-gpu=name,utilization.gpu,utilization.memory,memory.used,memory.total,temperature.gpu,power.draw,power.limit",
                "--format=csv,noheader,nounits",
            ],
            timeout=1.5,
        )
        w = self.cols
        lines = ["\x1b[2J\x1b[H"]
        if ok and out.strip():
            parts = [p.strip() for p in out.strip().splitlines()[0].split(",")]
            if len(parts) >= 8:
                name, ug, _um, mu, mt, tmp, pwr, plim = parts[:8]
                try:
                    ug_f = max(0.0, min(100.0, float(ug)))
                    mu_f, mt_f = float(mu), max(1.0, float(mt))
                    mem_f = max(0.0, min(100.0, (mu_f / mt_f) * 100.0))
                    bw = max(12, min(32, w // 4))
                    g_bar = ("|" * int(round(ug_f / 100.0 * bw))).ljust(bw)
                    m_bar = ("|" * int(round(mem_f / 100.0 * bw))).ljust(bw)
                    hdr = f" Device 0 [{name}]   TEMP {tmp}°C   POW {pwr} / {plim} W"
                    lines.append(f"\x1b[1;38;2;0;240;255m{hdr[:w].ljust(w)}\x1b[0m")
                    lines.append(
                        f"\x1b[38;2;0;255;115m GPU[{g_bar}{ug_f:4.0f}%] "
                        f"\x1b[38;2;255;235;25mMEM[{m_bar}{mu_f/1024.0:6.3f}Gi/{mt_f/1024.0:.3f}Gi]\x1b[0m"
                    )
                except ValueError:
                    lines.append(f"\x1b[38;2;226;234;252m {out.strip().splitlines()[0][:w-2]}\x1b[0m")
        else:
            lines.append("\x1b[1;38;2;255;235;25m nvtop binary unavailable (live nvidia-smi telemetry unavailable)\x1b[0m")
        payload = "\r\n".join(lines).encode("utf-8")
        with self.lock:
            if self._vt:
                self._lib.vterm_input_write(self._vt, payload, len(payload))
                self._snapshot_locked()
        self._request_redraw()

    def resize(self, rows: int, cols: int) -> None:
        nr, nc = max(8, int(rows)), max(40, int(cols))
        if nr == self.rows and nc == self.cols:
            return
        with self.lock:
            self.rows = nr
            self.cols = nc
            if self._vt:
                self._lib.vterm_set_size(self._vt, nr, nc)
                self._snapshot_locked()
        if self.master_fd >= 0 and self.alive:
            try:
                fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, struct.pack("HHHH", nr, nc, 0, 0))
                if self.proc and self.proc.poll() is None:
                    os.killpg(os.getpgid(self.proc.pid), signal.SIGWINCH)
            except Exception:
                pass
        elif self.proc is None and self.alive and self.tui_id == "btop":
            self._write_btop_fallback_frame()

    def send_bytes(self, data: bytes) -> None:
        if self.master_fd >= 0 and self.alive and data:
            try:
                os.write(self.master_fd, data)
            except OSError:
                pass

    def _request_redraw(self) -> None:
        if not self._redraw_pending:
            self._redraw_pending = True
            GLib.idle_add(self._emit_redraw)

    def _emit_redraw(self) -> bool:
        self._redraw_pending = False
        cb = self.on_redraw
        if cb and self.alive and not self.stopped:
            cb()
        return False

    def terminate(self) -> None:
        self.stopped = True
        self.alive = False
        fd = self.master_fd
        self.master_fd = -1
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        proc = self.proc
        self.proc = None
        if proc is not None and proc.poll() is None:
            try:
                pgid = os.getpgid(proc.pid)
                os.killpg(pgid, signal.SIGTERM)
                for _ in range(4):
                    if proc.poll() is not None:
                        break
                    time.sleep(0.025)
                if proc.poll() is None:
                    os.killpg(pgid, signal.SIGKILL)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
            try:
                proc.wait(timeout=0.3)
            except Exception:
                pass
        with self.lock:
            vt = self._vt
            self._vt = None
            self._scr = None
            if vt:
                self._lib.vterm_free(vt)


def get_or_start_tui_session(tui_id: str, cmd: list[str], rows: int, cols: int, cc_panel=None) -> EmbeddedTuiSession:
    sess = _TUI_SESSIONS.get(tui_id)
    if sess is not None and sess.alive and not sess.stopped and sess.cmd == list(cmd):
        if cc_panel is not None:
            sess.cc_panel = cc_panel
        if sess.rows != rows or sess.cols != cols:
            sess.resize(rows, cols)
        return sess
    if sess is not None:
        sess.terminate()
    sess = EmbeddedTuiSession(tui_id, cmd, rows=rows, cols=cols, cc_panel=cc_panel)
    _TUI_SESSIONS[tui_id] = sess
    sess.start()
    return sess


def close_embedded_tui(tui_id: str) -> None:
    sess = _TUI_SESSIONS.pop(tui_id, None)
    if sess is not None:
        sess.terminate()
    if STATE.get("system", "active_tui", "") == tui_id:
        STATE.set("system", "active_tui", "")


def close_embedded_tuis() -> None:
    for tid in list(_TUI_SESSIONS.keys()):
        sess = _TUI_SESSIONS.pop(tid, None)
        if sess is not None:
            sess.terminate()
    STATE.set("system", "active_tui", "")


def restart_tui_session(tui_id: str, cc_panel=None) -> None:
    close_embedded_tui(tui_id)
    if cc_panel and cc_panel.shell:
        cc_panel.shell.refresh_view()


def set_active_tui(tui_id: str) -> None:
    tid = str(tui_id or "")
    prev = STATE.get("system", "active_tui", "")
    STATE.set("system", "active_tui", tid)
    if tid != prev:
        for s in list(_TUI_SESSIONS.values()):
            s._request_redraw()


atexit.register(close_embedded_tuis)


def handle_cc_telemetry_key_press(cc_panel, event) -> bool:
    active_id = str(STATE.get("system", "active_tui", "") or "")
    if active_id not in ("btop", "nvtop"):
        return False
    if event.keyval == Gdk.KEY_Escape:
        set_active_tui("")
        return True
    sess = _TUI_SESSIONS.get(active_id)
    if sess is None or not sess.alive:
        return False

    kv = event.keyval
    state = event.state
    is_ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
    is_alt = bool(state & Gdk.ModifierType.MOD1_MASK)

    special = {
        Gdk.KEY_Up: b"\x1b[A",
        Gdk.KEY_KP_Up: b"\x1b[A",
        Gdk.KEY_Down: b"\x1b[B",
        Gdk.KEY_KP_Down: b"\x1b[B",
        Gdk.KEY_Right: b"\x1b[C",
        Gdk.KEY_KP_Right: b"\x1b[C",
        Gdk.KEY_Left: b"\x1b[D",
        Gdk.KEY_KP_Left: b"\x1b[D",
        Gdk.KEY_Return: b"\r",
        Gdk.KEY_KP_Enter: b"\r",
        Gdk.KEY_BackSpace: b"\x7f",
        Gdk.KEY_Delete: b"\x1b[3~",
        Gdk.KEY_KP_Delete: b"\x1b[3~",
        Gdk.KEY_Tab: b"\t",
        Gdk.KEY_KP_Tab: b"\t",
        Gdk.KEY_ISO_Left_Tab: b"\x1b[Z",
        Gdk.KEY_Home: b"\x1b[H",
        Gdk.KEY_KP_Home: b"\x1b[H",
        Gdk.KEY_End: b"\x1b[F",
        Gdk.KEY_KP_End: b"\x1b[F",
        Gdk.KEY_Page_Up: b"\x1b[5~",
        Gdk.KEY_KP_Page_Up: b"\x1b[5~",
        Gdk.KEY_Page_Down: b"\x1b[6~",
        Gdk.KEY_KP_Page_Down: b"\x1b[6~",
        Gdk.KEY_F1: b"\x1bOP",
        Gdk.KEY_F2: b"\x1bOQ",
        Gdk.KEY_F3: b"\x1bOR",
        Gdk.KEY_F4: b"\x1bOS",
        Gdk.KEY_F5: b"\x1b[15~",
        Gdk.KEY_F6: b"\x1b[17~",
        Gdk.KEY_F7: b"\x1b[18~",
        Gdk.KEY_F8: b"\x1b[19~",
        Gdk.KEY_F9: b"\x1b[20~",
        Gdk.KEY_F10: b"\x1b[21~",
        Gdk.KEY_F11: b"\x1b[23~",
        Gdk.KEY_F12: b"\x1b[24~",
    }
    seq = special.get(kv)
    if seq is not None:
        sess.send_bytes(seq)
        return True

    uc = Gdk.keyval_to_unicode(kv)
    if is_ctrl and 1 <= uc <= 127:
        ch = chr(uc).lower()
        if "a" <= ch <= "z":
            sess.send_bytes(bytes([ord(ch) - 96]))
            return True
    if uc >= 32:
        data = chr(uc).encode("utf-8")
        sess.send_bytes((b"\x1b" + data) if is_alt else data)
        return True
    return False

