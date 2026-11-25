#!/usr/bin/env python3
"""NC-UI System Resources & Embedded Terminal TUI Engine (`panels/cc_engine_resources.py`)."""

import atexit
from collections import deque
import ctypes
import fcntl
import glob
import json
import os
import pty
import re
import select
import shutil
import signal
import struct
import subprocess
import termios
import threading
import time
from core import probe
from core.config import CONFIG
from core.i18n import t
from core.state import STATE
from ui.kit import FeedbackBox, get_active_inline_entry, handle_inline_entry_key_press

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


def _safe_float(val: str, default: float = 0.0) -> float:
    s = (val or "").strip()
    if not s or s.startswith("[") or s.upper() == "N/A":
        return default
    try:
        return float(s)
    except Exception:
        return default


def _safe_int(val: str, default: int = 0) -> int:
    s = (val or "").strip()
    if not s or s.startswith("[") or s.upper() == "N/A":
        return default
    try:
        return int(round(float(s)))
    except Exception:
        return default


def _read_sysfs_txt(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


def _read_nvidia_gpu(include_procs: bool = False) -> dict | None:
    if not shutil.which("nvidia-smi"):
        return None
    ok, out = probe.run_cmd(
        [
            "nvidia-smi",
            "--query-gpu=name,utilization.gpu,utilization.memory,memory.used,memory.total,"
            "temperature.gpu,fan.speed,power.draw,power.limit,clocks.current.graphics,"
            "clocks.current.memory,pstate",
            "--format=csv,noheader,nounits",
        ],
        timeout=1.5,
    )
    if not ok or not out:
        return None
    parts = []
    for line in out.splitlines():
        cand = [p.strip() for p in line.split(",")]
        if len(cand) >= 12 and _safe_float(cand[4]) > 0.0:
            parts = cand
            break
    if not parts:
        return None

    mem_used_mib = _safe_float(parts[3])
    mem_tot_mib = _safe_float(parts[4])
    fan_raw = parts[6].strip()
    fan_pct = None if (not fan_raw or fan_raw.startswith("[") or fan_raw.upper() == "N/A") else _safe_int(fan_raw)
    pstate_raw = parts[11].strip()
    pstate = "" if (not pstate_raw or pstate_raw.startswith("[") or pstate_raw.upper() == "N/A") else pstate_raw

    procs: list[dict] = []
    if include_procs:
        p_ok, p_out = probe.run_cmd(["nvidia-smi", "pmon", "-c", "1", "-s", "m"], timeout=1.5)
        if p_ok and p_out:
            by_pid: dict[int, dict] = {}
            for pline in p_out.splitlines():
                ps = pline.strip()
                if not ps or ps.startswith("#"):
                    continue
                cols = ps.split(None, 5)
                if len(cols) < 6 or cols[0] != "0" or not cols[1].isdigit():
                    continue
                pid = int(cols[1])
                vram_mib = _safe_int(cols[3], 0)
                name = _read_sysfs_txt(f"/proc/{pid}/comm") or os.path.basename(cols[5].split()[0])
                if pid not in by_pid or vram_mib > by_pid[pid]["vram_mib"]:
                    by_pid[pid] = {"pid": pid, "name": name, "vram_mib": vram_mib}
            procs = sorted(by_pid.values(), key=lambda x: (-x["vram_mib"], x["name"]))

    return {
        "available": True,
        "vendor": "nvidia",
        "model": parts[0] or "NVIDIA GPU",
        "util_pct": max(0.0, min(100.0, _safe_float(parts[1]))),
        "mem_ctrl_pct": max(0.0, min(100.0, _safe_float(parts[2]))),
        "vram_used": int(mem_used_mib * 1024 * 1024),
        "vram_total": int(mem_tot_mib * 1024 * 1024),
        "vram_pct": max(0.0, min(100.0, (mem_used_mib / mem_tot_mib) * 100.0)),
        "temp_c": _safe_float(parts[5]),
        "fan_pct": fan_pct,
        "power_w": _safe_float(parts[7]),
        "power_limit_w": _safe_float(parts[8]),
        "gfx_mhz": _safe_int(parts[9]),
        "mem_mhz": _safe_int(parts[10]),
        "pstate": pstate,
        "procs": procs,
    }


def _read_sysfs_gpu() -> dict | None:
    for dev_dir in sorted(glob.glob("/sys/class/drm/card[0-9]*/device")):
        card_name = os.path.basename(os.path.dirname(dev_dir))
        if "-" in card_name:
            continue
        vram_tot = _safe_int(_read_sysfs_txt(os.path.join(dev_dir, "mem_info_vram_total")))
        if vram_tot <= 0:
            vram_tot = _safe_int(_read_sysfs_txt(os.path.join(dev_dir, "tile0/vram0/physical_vram_size_bytes")))
        # Require dedicated VRAM (>= 256 MiB) to filter out integrated GPUs.
        if vram_tot < 256 * 1024 * 1024:
            continue

        vendor_hex = _read_sysfs_txt(os.path.join(dev_dir, "vendor")).lower()
        vendor = {"0x1002": "amd", "0x8086": "intel", "0x10de": "nvidia"}.get(vendor_hex, "gpu")
        vram_used = _safe_int(_read_sysfs_txt(os.path.join(dev_dir, "mem_info_vram_used")))
        util_pct = max(0.0, min(100.0, _safe_float(_read_sysfs_txt(os.path.join(dev_dir, "gpu_busy_percent")))))
        model = _read_sysfs_txt(os.path.join(dev_dir, "product_name")) or {
            "amd": "AMD Radeon GPU",
            "intel": "Intel Arc GPU",
            "nvidia": "NVIDIA GPU",
        }.get(vendor, "Dedicated GPU")

        temp_c = 0.0
        fan_pct = None
        power_w = 0.0
        power_limit_w = 0.0
        for hw in sorted(glob.glob(os.path.join(dev_dir, "hwmon", "hwmon*"))):
            t_raw = _safe_float(_read_sysfs_txt(os.path.join(hw, "temp1_input")))
            if t_raw > 0:
                temp_c = t_raw / 1000.0 if t_raw > 200 else t_raw
            p_raw = _safe_float(_read_sysfs_txt(os.path.join(hw, "power1_average"))) or _safe_float(_read_sysfs_txt(os.path.join(hw, "power1_input")))
            if p_raw > 0:
                power_w = p_raw / 1_000_000.0
            cap_raw = _safe_float(_read_sysfs_txt(os.path.join(hw, "power1_cap")))
            if cap_raw > 0:
                power_limit_w = cap_raw / 1_000_000.0
            fan_cur = _safe_float(_read_sysfs_txt(os.path.join(hw, "fan1_input")))
            fan_max = _safe_float(_read_sysfs_txt(os.path.join(hw, "fan1_max")))
            if fan_max > 0 and fan_cur >= 0:
                fan_pct = max(0, min(100, int(round((fan_cur / fan_max) * 100.0))))
            else:
                pwm_raw = _safe_float(_read_sysfs_txt(os.path.join(hw, "pwm1")), -1.0)
                if pwm_raw >= 0:
                    fan_pct = max(0, min(100, int(round((pwm_raw / 255.0) * 100.0))))
            break

        gfx_mhz = 0
        mem_mhz = 0
        for clk_file, target in (("pp_dpm_sclk", "gfx"), ("pp_dpm_mclk", "mem")):
            for cline in _read_sysfs_txt(os.path.join(dev_dir, clk_file)).splitlines():
                if "*" in cline:
                    m = re.search(r"(\d+)\s*Mhz", cline, re.IGNORECASE)
                    if m:
                        if target == "gfx":
                            gfx_mhz = int(m.group(1))
                        else:
                            mem_mhz = int(m.group(1))
                    break

        return {
            "available": True,
            "vendor": vendor,
            "model": model,
            "util_pct": util_pct,
            "mem_ctrl_pct": 0.0,
            "vram_used": vram_used,
            "vram_total": vram_tot,
            "vram_pct": max(0.0, min(100.0, (vram_used / float(vram_tot)) * 100.0)),
            "temp_c": temp_c,
            "fan_pct": fan_pct,
            "power_w": power_w,
            "power_limit_w": power_limit_w,
            "gfx_mhz": gfx_mhz,
            "mem_mhz": mem_mhz,
            "pstate": "",
            "procs": [],
        }
    return None


def get_gpu_telemetry(include_procs: bool = False) -> dict:
    nv = _read_nvidia_gpu(include_procs=include_procs)
    if nv is not None:
        return nv
    sys_gpu = _read_sysfs_gpu()
    if sys_gpu is not None:
        return sys_gpu
    return {
        "available": False,
        "vendor": "",
        "model": "",
        "util_pct": 0.0,
        "mem_ctrl_pct": 0.0,
        "vram_used": 0,
        "vram_total": 0,
        "vram_pct": 0.0,
        "temp_c": 0.0,
        "fan_pct": None,
        "power_w": 0.0,
        "power_limit_w": 0.0,
        "gfx_mhz": 0,
        "mem_mhz": 0,
        "pstate": "",
        "procs": [],
    }


def get_gpu_bar_pct() -> tuple[bool, float, float]:
    g = get_gpu_telemetry(include_procs=False)
    return bool(g["available"]), float(g["util_pct"]), float(g["vram_pct"])


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


class _VTermRect(ctypes.Structure):
    _fields_ = [
        ("start_row", ctypes.c_int),
        ("end_row", ctypes.c_int),
        ("start_col", ctypes.c_int),
        ("end_col", ctypes.c_int),
    ]


_VTERM_SB_PUSH_CB = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_int, ctypes.POINTER(_VTermScreenCell), ctypes.c_void_p)
_VTERM_SB_CLEAR_CB = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p)


class _VTermScreenCallbacks(ctypes.Structure):
    _fields_ = [
        ("damage", ctypes.c_void_p),
        ("moverect", ctypes.c_void_p),
        ("movecursor", ctypes.c_void_p),
        ("settermprop", ctypes.c_void_p),
        ("bell", ctypes.c_void_p),
        ("resize", ctypes.c_void_p),
        ("sb_pushline", _VTERM_SB_PUSH_CB),
        ("sb_popline", ctypes.c_void_p),
        ("sb_clear", _VTERM_SB_CLEAR_CB),
    ]


VTERM_MOD_NONE = 0x00
VTERM_MOD_SHIFT = 0x01
VTERM_MOD_ALT = 0x02
VTERM_MOD_CTRL = 0x04

VTERM_KEY_ENTER = 1
VTERM_KEY_TAB = 2
VTERM_KEY_BACKSPACE = 3
VTERM_KEY_ESCAPE = 4
VTERM_KEY_UP = 5
VTERM_KEY_DOWN = 6
VTERM_KEY_LEFT = 7
VTERM_KEY_RIGHT = 8
VTERM_KEY_INS = 9
VTERM_KEY_DEL = 10
VTERM_KEY_HOME = 11
VTERM_KEY_END = 12
VTERM_KEY_PAGEUP = 13
VTERM_KEY_PAGEDOWN = 14
VTERM_KEY_FUNCTION_0 = 256
VTERM_KEY_KP_0 = 512
VTERM_KEY_KP_1 = 513
VTERM_KEY_KP_2 = 514
VTERM_KEY_KP_3 = 515
VTERM_KEY_KP_4 = 516
VTERM_KEY_KP_5 = 517
VTERM_KEY_KP_6 = 518
VTERM_KEY_KP_7 = 519
VTERM_KEY_KP_8 = 520
VTERM_KEY_KP_9 = 521
VTERM_KEY_KP_MULT = 522
VTERM_KEY_KP_PLUS = 523
VTERM_KEY_KP_COMMA = 524
VTERM_KEY_KP_MINUS = 525
VTERM_KEY_KP_PERIOD = 526
VTERM_KEY_KP_DIVIDE = 527
VTERM_KEY_KP_ENTER = 528
VTERM_KEY_KP_EQUAL = 529


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
    lib.vterm_obtain_state.argtypes = [ctypes.c_void_p]
    lib.vterm_obtain_state.restype = ctypes.c_void_p
    lib.vterm_state_get_cursorpos.argtypes = [ctypes.c_void_p, ctypes.POINTER(_VTermPos)]
    lib.vterm_screen_set_callbacks.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(_VTermScreenCallbacks),
        ctypes.c_void_p,
    ]
    lib.vterm_screen_enable_altscreen.argtypes = [ctypes.c_void_p, ctypes.c_int]
    lib.vterm_screen_reset.argtypes = [ctypes.c_void_p, ctypes.c_int]
    lib.vterm_input_write.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t]
    lib.vterm_input_write.restype = ctypes.c_size_t
    lib.vterm_output_read.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t]
    lib.vterm_output_read.restype = ctypes.c_size_t
    lib.vterm_keyboard_unichar.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int]
    lib.vterm_keyboard_key.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
    lib.vterm_keyboard_start_paste.argtypes = [ctypes.c_void_p]
    lib.vterm_keyboard_end_paste.argtypes = [ctypes.c_void_p]
    lib.vterm_screen_get_cell.argtypes = [ctypes.c_void_p, _VTermPos, ctypes.POINTER(_VTermScreenCell)]
    lib.vterm_screen_get_cell.restype = ctypes.c_int
    lib.vterm_screen_convert_color_to_rgb.argtypes = [ctypes.c_void_p, ctypes.POINTER(_VTermColor)]
    _LIBVTERM = lib
    return lib


def _decode_vterm_cell(lib, scr, cell) -> tuple:
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
        fg_c = _VTermColor(cell.fg.type, cell.fg.r, cell.fg.g, cell.fg.b)
        lib.vterm_screen_convert_color_to_rgb(scr, ctypes.byref(fg_c))
        fg = (fg_c.r / 255.0, fg_c.g / 255.0, fg_c.b / 255.0)
    bg = None
    if not (cell.bg.type & 0x04):
        bg_c = _VTermColor(cell.bg.type, cell.bg.r, cell.bg.g, cell.bg.b)
        lib.vterm_screen_convert_color_to_rgb(scr, ctypes.byref(bg_c))
        bg = (bg_c.r / 255.0, bg_c.g / 255.0, bg_c.b / 255.0)
    bold = bool(cell.attrs & 0x01)
    rev = bool(cell.attrs & 0x20)
    return (ch, fg, bg, bold, rev)


def _tui_child_preexec() -> None:
    try:
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)
    except Exception:
        pass
    try:
        ctypes.CDLL("libc.so.6").prctl(1, signal.SIGKILL)
    except Exception:
        pass


def _reap_orphan_btop() -> None:
    """Termina eventuali processi btop avviati con /tmp/nc_btop.conf rimasti orfani sotto init/systemd --user."""
    live_pids = {s.proc.pid for s in _TUI_SESSIONS.values() if s.proc is not None and s.proc.poll() is None}
    for proc_dir in glob.glob("/proc/[0-9]*"):
        try:
            pid = int(os.path.basename(proc_dir))
            if pid in live_pids:
                continue
            with open(os.path.join(proc_dir, "cmdline"), "rb") as f:
                cmd = f.read().replace(b"\x00", b" ")
            if b"/tmp/nc_btop.conf" not in cmd or b"btop" not in cmd:
                continue
            ppid = 0
            with open(os.path.join(proc_dir, "status"), "r", encoding="utf-8", errors="ignore") as f:
                for ln in f:
                    if ln.startswith("PPid:"):
                        ppid = int(ln.split(":", 1)[1].strip() or 0)
                        break
            is_orphan = ppid <= 1
            if not is_orphan and ppid > 1:
                try:
                    with open(f"/proc/{ppid}/comm", "r", encoding="utf-8", errors="ignore") as f:
                        is_orphan = f.read().strip() in ("systemd", "init")
                except OSError:
                    is_orphan = True
            if is_orphan:
                os.kill(pid, signal.SIGKILL)
        except Exception:
            pass


_TUI_SESSIONS: dict = {}
_ACTIVE_TUI_ID: str = ""


def get_active_tui() -> str:
    return _ACTIVE_TUI_ID


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
        self.lock = threading.RLock()
        self.grid = self._empty_grid(self.rows, self.cols)
        self.scrollback = deque(maxlen=4000)
        self.scroll_offset = 0
        self.sel_anchor = None
        self.sel_end = None
        self.sel_active = False
        self._press_anchor = None
        self._sel_dragged = False
        self.master_fd = -1
        self.proc = None
        self.alive = False
        self.stopped = False
        self.exit_code = None
        self.on_redraw = None
        self.on_exit = None
        self._redraw_pending = False
        self._outbuf = ctypes.create_string_buffer(4096)
        self._lib = _get_libvterm()
        self._vt = self._lib.vterm_new(self.rows, self.cols)
        self._lib.vterm_set_utf8(self._vt, 1)
        self._scr = self._lib.vterm_obtain_screen(self._vt)
        self._state = self._lib.vterm_obtain_state(self._vt)
        self.cursor_pos = (0, 0)
        self._sb_push_cb = _VTERM_SB_PUSH_CB(self._on_sb_pushline)
        self._sb_clear_cb = _VTERM_SB_CLEAR_CB(self._on_sb_clear)
        self._cb_struct = _VTermScreenCallbacks(
            sb_pushline=self._sb_push_cb,
            sb_clear=self._sb_clear_cb,
        )
        self._lib.vterm_screen_set_callbacks(self._scr, ctypes.byref(self._cb_struct), None)
        self._lib.vterm_screen_enable_altscreen(self._scr, 1)
        self._lib.vterm_screen_reset(self._scr, 1)

    @staticmethod
    def _empty_grid(rows: int, cols: int) -> list:
        return [[(" ", None, None, False, False) for _ in range(cols)] for _ in range(rows)]

    def _on_sb_pushline(self, cols: int, cells_ptr, _user) -> int:
        if not self._scr or cols <= 0 or not cells_ptr:
            return 1
        row_cells = [_decode_vterm_cell(self._lib, self._scr, cells_ptr[c]) for c in range(cols)]
        dropped = self.scrollback.maxlen is not None and len(self.scrollback) >= self.scrollback.maxlen
        self.scrollback.append(row_cells)
        if dropped:
            if self.sel_anchor is not None:
                self.sel_anchor = (self.sel_anchor[0] - 1, self.sel_anchor[1])
            if self.sel_end is not None:
                self.sel_end = (self.sel_end[0] - 1, self.sel_end[1])
            if self._press_anchor is not None:
                self._press_anchor = (self._press_anchor[0] - 1, self._press_anchor[1])
            if (self.sel_anchor and self.sel_anchor[0] < 0) or (self.sel_end and self.sel_end[0] < 0):
                self.sel_anchor = None
                self.sel_end = None
        elif self.scroll_offset > 0:
            self.scroll_offset = min(len(self.scrollback), self.scroll_offset + 1)
        return 1

    def _on_sb_clear(self, _user) -> int:
        self.scrollback.clear()
        self.scroll_offset = 0
        self.sel_anchor = None
        self.sel_end = None
        self._press_anchor = None
        self._sel_dragged = False
        return 1

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
                    row_cells.append(_decode_vterm_cell(lib, scr, cell))
                else:
                    row_cells.append((" ", None, None, False, False))
            new_grid.append(row_cells)
        self.grid = new_grid
        cpos = _VTermPos()
        lib.vterm_state_get_cursorpos(self._state, ctypes.byref(cpos))
        self.cursor_pos = (int(cpos.row), int(cpos.col))

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
            self.master_fd = -1
            try:
                os.close(master)
            except OSError:
                pass
            proc = self.proc
            self.proc = None
            if proc is not None and proc.poll() is None:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                    proc.wait(timeout=0.3)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
            return
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
            if self.proc is not None and self.exit_code is None:
                try:
                    self.exit_code = self.proc.wait(timeout=0.5)
                except Exception:
                    rc = self.proc.poll()
                    self.exit_code = 0 if rc is None else rc
            self.alive = False
            if not self.stopped:
                self._request_redraw()
                if _ACTIVE_TUI_ID == self.tui_id:
                    from gi.repository import GLib
                    GLib.idle_add(lambda: (set_active_tui("", shell=getattr(self.cc_panel, "shell", None)), False)[1])
                if self.on_exit is not None:
                    from gi.repository import GLib
                    GLib.idle_add(self.on_exit)

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

    def _flush_vterm_output_locked(self) -> None:
        if not self._vt or self.master_fd < 0 or not self.alive:
            return
        while True:
            n = self._lib.vterm_output_read(self._vt, self._outbuf, 4096)
            if n <= 0:
                break
            try:
                os.write(self.master_fd, self._outbuf.raw[:n])
            except OSError:
                break

    def _fit_row_locked(self, row: list, cols: int) -> list:
        if len(row) == cols:
            return list(row)
        if len(row) > cols:
            return list(row[:cols])
        return list(row) + [(" ", None, None, False, False) for _ in range(cols - len(row))]

    def _norm_selection_locked(self):
        if self.sel_anchor is None or self.sel_end is None:
            return None
        a = (int(self.sel_anchor[0]), int(self.sel_anchor[1]))
        b = (int(self.sel_end[0]), int(self.sel_end[1]))
        if a <= b:
            return (a[0], a[1], b[0], b[1])
        return (b[0], b[1], a[0], a[1])

    def has_selection(self) -> bool:
        with self.lock:
            return self.sel_anchor is not None and self.sel_end is not None

    def clear_selection(self) -> None:
        with self.lock:
            changed = self.sel_anchor is not None or self.sel_end is not None or self.sel_active
            self.sel_anchor = None
            self.sel_end = None
            self.sel_active = False
            self._press_anchor = None
            self._sel_dragged = False
        if changed:
            self._request_redraw()

    def _view_to_abs_locked(self, view_row: int, col: int) -> tuple[int, int]:
        vr = max(0, min(self.rows - 1, int(view_row)))
        vc = max(0, min(self.cols - 1, int(col)))
        sb_len = len(self.scrollback)
        off = max(0, min(sb_len, int(self.scroll_offset)))
        abs_row = max(0, (sb_len - off) + vr)
        return abs_row, vc

    def begin_selection(self, view_row: int, col: int) -> None:
        with self.lock:
            changed = self.sel_anchor is not None or self.sel_end is not None
            self.sel_anchor = None
            self.sel_end = None
            self._press_anchor = self._view_to_abs_locked(view_row, col)
            self._sel_dragged = False
            self.sel_active = True
        if changed:
            self._request_redraw()

    def update_selection(self, view_row: int, col: int) -> None:
        with self.lock:
            if not self.sel_active or self._press_anchor is None:
                return
            cur = self._view_to_abs_locked(view_row, col)
            if cur != self._press_anchor:
                self._sel_dragged = True
            if self._sel_dragged:
                self.sel_anchor = self._press_anchor
                self.sel_end = cur
            else:
                return
        self._request_redraw()

    def end_selection(self) -> None:
        with self.lock:
            self.sel_active = False
            if not self._sel_dragged:
                self.sel_anchor = None
                self.sel_end = None
            self._press_anchor = None
        self._request_redraw()

    def _all_rows_locked(self) -> list:
        return list(self.scrollback) + list(self.grid)

    def select_word_at(self, view_row: int, col: int) -> None:
        word_re = re.compile(r"[A-Za-z0-9_./:~+\-@%&=?!#]")
        with self.lock:
            abs_r, vc = self._view_to_abs_locked(view_row, col)
            all_rows = self._all_rows_locked()
            if not (0 <= abs_r < len(all_rows)):
                return
            row = all_rows[abs_r]
            if not row:
                return
            vc = max(0, min(len(row) - 1, vc))
            ch0 = row[vc][0] or " "
            if ch0 == " ":
                c0 = c1 = vc
                while c0 > 0 and (row[c0 - 1][0] or " ") == " ":
                    c0 -= 1
                while c1 + 1 < len(row) and (row[c1 + 1][0] or " ") == " ":
                    c1 += 1
            elif word_re.match(ch0):
                c0 = c1 = vc
                while c0 > 0 and word_re.match(row[c0 - 1][0] or " "):
                    c0 -= 1
                while c1 + 1 < len(row) and word_re.match(row[c1 + 1][0] or " "):
                    c1 += 1
            else:
                c0 = c1 = vc
            self.sel_anchor = (abs_r, c0)
            self.sel_end = (abs_r, c1)
            self.sel_active = False
            self._sel_dragged = True
        self._request_redraw()

    def select_line_at(self, view_row: int) -> None:
        with self.lock:
            abs_r, _ = self._view_to_abs_locked(view_row, 0)
            all_rows = self._all_rows_locked()
            if not (0 <= abs_r < len(all_rows)):
                return
            row = all_rows[abs_r]
            last_non_empty = -1
            for idx, cell in enumerate(row):
                if cell[0] and cell[0] != " ":
                    last_non_empty = idx
            end_c = last_non_empty if last_non_empty >= 0 else max(0, self.cols - 1)
            self.sel_anchor = (abs_r, 0)
            self.sel_end = (abs_r, end_c)
            self.sel_active = False
            self._sel_dragged = True
        self._request_redraw()

    def select_all(self) -> None:
        with self.lock:
            all_rows = self._all_rows_locked()
            if not all_rows:
                return
            last_r = len(all_rows) - 1
            while last_r > 0 and not any(cell[0] and cell[0] != " " for cell in all_rows[last_r]):
                last_r -= 1
            self.sel_anchor = (0, 0)
            self.sel_end = (last_r, max(0, len(all_rows[last_r]) - 1))
            self.sel_active = False
            self._sel_dragged = True
        self._request_redraw()

    def get_selected_text(self) -> str:
        with self.lock:
            bounds = self._norm_selection_locked()
            if bounds is None:
                return ""
            sr, sc, er, ec = bounds
            all_rows = self._all_rows_locked()
            if not all_rows:
                return ""
            sr = max(0, min(len(all_rows) - 1, sr))
            er = max(sr, min(len(all_rows) - 1, er))
            lines = []
            for r in range(sr, er + 1):
                row = all_rows[r]
                r_len = len(row)
                c_start = sc if r == sr else 0
                c_end = min(r_len - 1, ec) if r == er else (r_len - 1)
                if c_start > c_end or c_start >= r_len:
                    lines.append("")
                    continue
                chars = []
                for c in range(c_start, c_end + 1):
                    txt = row[c][0]
                    chars.append(txt if txt else "")
                lines.append("".join(chars).rstrip())
            return "\n".join(lines)

    def scroll_by(self, delta: int) -> None:
        with self.lock:
            sb_len = len(self.scrollback)
            new_off = max(0, min(sb_len, int(self.scroll_offset) + int(delta)))
            if new_off == self.scroll_offset:
                return
            self.scroll_offset = new_off
        self._request_redraw()

    def scroll_to_top(self) -> None:
        with self.lock:
            sb_len = len(self.scrollback)
            if self.scroll_offset == sb_len:
                return
            self.scroll_offset = sb_len
        self._request_redraw()

    def scroll_to_bottom(self) -> None:
        with self.lock:
            if self.scroll_offset == 0:
                return
            self.scroll_offset = 0
        self._request_redraw()

    def get_viewport_snapshot(self) -> tuple:
        with self.lock:
            rows = self.rows
            cols = self.cols
            sb_len = len(self.scrollback)
            off = max(0, min(sb_len, int(self.scroll_offset)))
            self.scroll_offset = off
            if off == 0:
                vis = [list(r) for r in self.grid]
            else:
                all_rows = self._all_rows_locked()
                start_idx = max(0, sb_len - off)
                slice_rows = all_rows[start_idx : start_idx + rows]
                vis = [self._fit_row_locked(r, cols) for r in slice_rows]
                while len(vis) < rows:
                    vis.append([(" ", None, None, False, False) for _ in range(cols)])
            cur_pos = getattr(self, "cursor_pos", (0, 0))
            sel_bounds = self._norm_selection_locked()
            return rows, cols, vis, cur_pos, off, sb_len, sel_bounds

    def send_bytes(self, data: bytes) -> None:
        if self.master_fd >= 0 and self.alive and data:
            with self.lock:
                self.scroll_offset = 0
                if self.sel_anchor is not None or self.sel_end is not None:
                    self.sel_anchor = None
                    self.sel_end = None
            try:
                os.write(self.master_fd, data)
            except OSError:
                pass
            self._request_redraw()

    def send_vterm_key(self, vkey: int, vmod: int = 0) -> None:
        if self.master_fd >= 0 and self.alive:
            with self.lock:
                self.scroll_offset = 0
                if self.sel_anchor is not None or self.sel_end is not None:
                    self.sel_anchor = None
                    self.sel_end = None
                if self._vt:
                    self._lib.vterm_keyboard_key(self._vt, int(vkey), int(vmod))
                    self._flush_vterm_output_locked()
            self._request_redraw()

    def send_vterm_unichar(self, uc: int, vmod: int = 0) -> None:
        if self.master_fd >= 0 and self.alive and uc >= 0:
            with self.lock:
                self.scroll_offset = 0
                if self.sel_anchor is not None or self.sel_end is not None:
                    self.sel_anchor = None
                    self.sel_end = None
                if self._vt:
                    self._lib.vterm_keyboard_unichar(self._vt, int(uc), int(vmod))
                    self._flush_vterm_output_locked()
            self._request_redraw()

    def paste_text(self, text: str) -> None:
        if self.master_fd >= 0 and self.alive and text:
            norm = text.replace("\r\n", "\r").replace("\n", "\r")
            payload = norm.encode("utf-8", errors="ignore")
            with self.lock:
                self.scroll_offset = 0
                if self.sel_anchor is not None or self.sel_end is not None:
                    self.sel_anchor = None
                    self.sel_end = None
                if self._vt:
                    self._lib.vterm_keyboard_start_paste(self._vt)
                    self._flush_vterm_output_locked()
                    try:
                        os.write(self.master_fd, payload)
                    except OSError:
                        pass
                    self._lib.vterm_keyboard_end_paste(self._vt)
                    self._flush_vterm_output_locked()
            self._request_redraw()

    def _request_redraw(self) -> None:
        if not self._redraw_pending:
            self._redraw_pending = True
            from gi.repository import GLib
            GLib.idle_add(self._emit_redraw)

    def _emit_redraw(self) -> bool:
        self._redraw_pending = False
        cb = self.on_redraw
        if cb and not self.stopped:
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
            if self._scr:
                try:
                    self._lib.vterm_screen_set_callbacks(self._scr, None, None)
                except Exception:
                    pass
            vt = self._vt
            self._vt = None
            self._scr = None
            self._sb_push_cb = None
            self._sb_clear_cb = None
            self._cb_struct = None
            if vt:
                self._lib.vterm_free(vt)


def get_tui_session(tui_id: str) -> EmbeddedTuiSession | None:
    return _TUI_SESSIONS.get(str(tui_id))


def start_tui_session(
    tui_id: str,
    cmd: list[str],
    rows: int,
    cols: int,
    cc_panel=None,
    on_exit=None,
) -> EmbeddedTuiSession:
    tid = str(tui_id)
    old = _TUI_SESSIONS.pop(tid, None)
    if old is not None:
        old.terminate()
    sess = EmbeddedTuiSession(tid, cmd, rows=rows, cols=cols, cc_panel=cc_panel)
    sess.on_exit = on_exit
    _TUI_SESSIONS[tid] = sess
    sess.start()
    set_active_tui(tid, shell=getattr(cc_panel, "shell", None))
    return sess


def get_or_start_tui_session(tui_id: str, cmd: list[str], rows: int, cols: int, cc_panel=None) -> EmbeddedTuiSession:
    if tui_id == "btop":
        _reap_orphan_btop()
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
    sess_shell = getattr(getattr(sess, "cc_panel", None), "shell", None) if sess is not None else None
    if sess is not None:
        sess.terminate()
    if _ACTIVE_TUI_ID == tui_id:
        set_active_tui("", shell=sess_shell)


def close_embedded_tuis() -> None:
    if not _TUI_SESSIONS and not _ACTIVE_TUI_ID:
        return
    sess_shell = None
    for tid in list(_TUI_SESSIONS.keys()):
        sess = _TUI_SESSIONS.pop(tid, None)
        if sess is not None:
            if sess_shell is None:
                sess_shell = getattr(getattr(sess, "cc_panel", None), "shell", None)
            sess.terminate()
    _reap_orphan_btop()
    if _ACTIVE_TUI_ID:
        set_active_tui("", shell=sess_shell)


def restart_tui_session(tui_id: str, cc_panel=None) -> None:
    close_embedded_tui(tui_id)
    if cc_panel and cc_panel.shell:
        cc_panel.shell.refresh_view()


def set_active_tui(tui_id: str, shell=None) -> None:
    global _ACTIVE_TUI_ID
    tid = str(tui_id or "")
    prev = _ACTIVE_TUI_ID
    _ACTIVE_TUI_ID = tid
    if tid != prev:
        for s in list(_TUI_SESSIONS.values()):
            s._request_redraw()


atexit.register(close_embedded_tuis)


def _tui_copy_text(text: str, primary: bool = False) -> None:
    if not text:
        return
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        gi.require_version("Gdk", "3.0")
        from gi.repository import Gdk, Gtk
        sel_atom = Gdk.SELECTION_PRIMARY if primary else Gdk.SELECTION_CLIPBOARD
        cb = Gtk.Clipboard.get(sel_atom)
        if cb is not None:
            cb.set_text(text, -1)
            cb.store()
    except Exception:
        pass
    if not primary:
        probe.copy_clipboard(text)


def _tui_read_clipboard(primary: bool = False) -> str:
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        gi.require_version("Gdk", "3.0")
        from gi.repository import Gdk, Gtk
        sel_atom = Gdk.SELECTION_PRIMARY if primary else Gdk.SELECTION_CLIPBOARD
        cb = Gtk.Clipboard.get(sel_atom)
        if cb is not None:
            txt = cb.wait_for_text()
            if txt:
                return txt
    except Exception:
        pass
    cmd = ["wl-paste", "--no-newline"] + (["--primary"] if primary else [])
    ok, out = probe.run_cmd(cmd, timeout=1.0)
    return out if ok and out else ""


def handle_cc_resources_key_press(cc_panel, event) -> bool:
    if get_active_inline_entry():
        if handle_inline_entry_key_press(event, shell=cc_panel.shell):
            return True
    active_id = get_active_tui()
    if not active_id or active_id not in _TUI_SESSIONS:
        return False
    import gi
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk

    kv = event.keyval
    state = event.state
    is_shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
    is_ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
    is_alt = bool(state & Gdk.ModifierType.MOD1_MASK)

    if kv in (
        Gdk.KEY_Shift_L,
        Gdk.KEY_Shift_R,
        Gdk.KEY_Control_L,
        Gdk.KEY_Control_R,
        Gdk.KEY_Alt_L,
        Gdk.KEY_Alt_R,
        Gdk.KEY_Meta_L,
        Gdk.KEY_Meta_R,
        Gdk.KEY_Super_L,
        Gdk.KEY_Super_R,
        Gdk.KEY_ISO_Level3_Shift,
        Gdk.KEY_Caps_Lock,
        Gdk.KEY_Num_Lock,
    ):
        return False

    sess = _TUI_SESSIONS.get(active_id)
    if sess is None:
        return False

    if kv == Gdk.KEY_Escape:
        if sess.has_selection():
            sess.clear_selection()
            return True
        set_active_tui("", shell=getattr(cc_panel, "shell", None))
        return True

    # Copy shortcuts (work even after command exit)
    if is_ctrl and is_shift and kv in (Gdk.KEY_c, Gdk.KEY_C):
        sel_txt = sess.get_selected_text()
        if sel_txt:
            _tui_copy_text(sel_txt, primary=False)
        return True
    if is_ctrl and kv == Gdk.KEY_Insert:
        sel_txt = sess.get_selected_text()
        if sel_txt:
            _tui_copy_text(sel_txt, primary=False)
        return True
    if is_ctrl and not is_shift and not is_alt and kv in (Gdk.KEY_c, Gdk.KEY_C) and sess.has_selection():
        sel_txt = sess.get_selected_text()
        if sel_txt:
            _tui_copy_text(sel_txt, primary=False)
        sess.clear_selection()
        return True

    # Select all (Ctrl+Shift+A)
    if is_ctrl and is_shift and kv in (Gdk.KEY_a, Gdk.KEY_A):
        sess.select_all()
        return True

    # Scrollback navigation (work even after command exit)
    if is_shift and kv in (Gdk.KEY_Page_Up, Gdk.KEY_KP_Page_Up):
        sess.scroll_by(max(3, sess.rows // 2))
        return True
    if is_shift and kv in (Gdk.KEY_Page_Down, Gdk.KEY_KP_Page_Down):
        sess.scroll_by(-max(3, sess.rows // 2))
        return True
    if is_shift and kv in (Gdk.KEY_Home, Gdk.KEY_KP_Home):
        sess.scroll_to_top()
        return True
    if is_shift and kv in (Gdk.KEY_End, Gdk.KEY_KP_End):
        sess.scroll_to_bottom()
        return True

    if not sess.alive:
        return True

    # Paste shortcuts (Ctrl+Shift+V, Shift+Insert)
    if (is_ctrl and is_shift and kv in (Gdk.KEY_v, Gdk.KEY_V)) or (is_shift and kv in (Gdk.KEY_Insert, Gdk.KEY_KP_Insert)):
        clip = _tui_read_clipboard(primary=False)
        if clip:
            sess.paste_text(clip)
        return True

    vmod = (VTERM_MOD_SHIFT if is_shift else 0) | (VTERM_MOD_ALT if is_alt else 0) | (VTERM_MOD_CTRL if is_ctrl else 0)
    if kv == Gdk.KEY_ISO_Left_Tab:
        sess.send_vterm_key(VTERM_KEY_TAB, vmod | VTERM_MOD_SHIFT)
        return True

    key_map = {
        Gdk.KEY_Return: VTERM_KEY_ENTER,
        Gdk.KEY_KP_Enter: VTERM_KEY_ENTER,
        Gdk.KEY_Tab: VTERM_KEY_TAB,
        Gdk.KEY_KP_Tab: VTERM_KEY_TAB,
        Gdk.KEY_BackSpace: VTERM_KEY_BACKSPACE,
        Gdk.KEY_Up: VTERM_KEY_UP,
        Gdk.KEY_KP_Up: VTERM_KEY_UP,
        Gdk.KEY_Down: VTERM_KEY_DOWN,
        Gdk.KEY_KP_Down: VTERM_KEY_DOWN,
        Gdk.KEY_Left: VTERM_KEY_LEFT,
        Gdk.KEY_KP_Left: VTERM_KEY_LEFT,
        Gdk.KEY_Right: VTERM_KEY_RIGHT,
        Gdk.KEY_KP_Right: VTERM_KEY_RIGHT,
        Gdk.KEY_Insert: VTERM_KEY_INS,
        Gdk.KEY_KP_Insert: VTERM_KEY_INS,
        Gdk.KEY_Delete: VTERM_KEY_DEL,
        Gdk.KEY_KP_Delete: VTERM_KEY_DEL,
        Gdk.KEY_Home: VTERM_KEY_HOME,
        Gdk.KEY_KP_Home: VTERM_KEY_HOME,
        Gdk.KEY_End: VTERM_KEY_END,
        Gdk.KEY_KP_End: VTERM_KEY_END,
        Gdk.KEY_Page_Up: VTERM_KEY_PAGEUP,
        Gdk.KEY_KP_Page_Up: VTERM_KEY_PAGEUP,
        Gdk.KEY_Page_Down: VTERM_KEY_PAGEDOWN,
        Gdk.KEY_KP_Page_Down: VTERM_KEY_PAGEDOWN,
        Gdk.KEY_F1: VTERM_KEY_FUNCTION_0 + 1,
        Gdk.KEY_F2: VTERM_KEY_FUNCTION_0 + 2,
        Gdk.KEY_F3: VTERM_KEY_FUNCTION_0 + 3,
        Gdk.KEY_F4: VTERM_KEY_FUNCTION_0 + 4,
        Gdk.KEY_F5: VTERM_KEY_FUNCTION_0 + 5,
        Gdk.KEY_F6: VTERM_KEY_FUNCTION_0 + 6,
        Gdk.KEY_F7: VTERM_KEY_FUNCTION_0 + 7,
        Gdk.KEY_F8: VTERM_KEY_FUNCTION_0 + 8,
        Gdk.KEY_F9: VTERM_KEY_FUNCTION_0 + 9,
        Gdk.KEY_F10: VTERM_KEY_FUNCTION_0 + 10,
        Gdk.KEY_F11: VTERM_KEY_FUNCTION_0 + 11,
        Gdk.KEY_F12: VTERM_KEY_FUNCTION_0 + 12,
    }
    vkey = key_map.get(kv)
    if vkey is not None:
        sess.send_vterm_key(vkey, vmod)
        return True

    uc = Gdk.keyval_to_unicode(kv)
    if is_ctrl:
        ctrl_special = {
            Gdk.KEY_space: b"\x00",
            Gdk.KEY_bracketleft: b"\x1b",
            Gdk.KEY_backslash: b"\x1c",
            Gdk.KEY_bracketright: b"\x1d",
            Gdk.KEY_asciicircum: b"\x1e",
            Gdk.KEY_6: b"\x1e",
            Gdk.KEY_underscore: b"\x1f",
        }
        if kv in ctrl_special:
            sess.send_bytes(ctrl_special[kv])
            return True
        if 1 <= uc <= 127:
            ch = chr(uc).lower()
            if "a" <= ch <= "z":
                sess.send_bytes(bytes([ord(ch) - 96]))
                return True
    if uc >= 32:
        sess.send_vterm_unichar(uc, VTERM_MOD_ALT if is_alt else VTERM_MOD_NONE)
        return True
    ev_str = getattr(event, "string", "") or ""
    if ev_str and (ord(ev_str[0]) >= 32 or len(ev_str) > 1):
        data = ev_str.encode("utf-8")
        sess.send_bytes((b"\x1b" + data) if is_alt else data)
        return True
    return True



def handle_cc_resources_link(cc_panel, uri: str) -> bool:
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
        set_active_tui(tui_id, shell=getattr(cc_panel, "shell", None))
        return True
    elif act.startswith("proc_sort:"):
        STATE.set("system", "cc_proc_sort", act.split(":", 1)[1])
    elif act.startswith("term:") or act.startswith("kill:"):
        op, pid = act.split(":", 1)
        signal_processes(pid, force_kill=(op == "kill"))
        _set_sys_fb(True, t("cc_system.sent_sig_to_pid", p0="KILL" if op == "kill" else "TERM", p1=pid))
    elif act == "goto_power_profiles":
        if cc_panel and hasattr(cc_panel, "set_section"):
            cc_panel.set_section("power")
            cc_panel.set_subtab("profiles")
            if cc_panel.shell:
                cc_panel.shell.refresh_view()
        return True
    elif act == "goto_usbguard":
        if cc_panel and hasattr(cc_panel, "set_section"):
            cc_panel.set_section("auth")
            cc_panel.set_subtab("usbguard")
            if cc_panel.shell:
                cc_panel.shell.refresh_view()
        return True
    else:
        return False

    if cc_panel and cc_panel.shell:
        cc_panel.shell.refresh_view()
    return True
