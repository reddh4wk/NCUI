#!/usr/bin/env python3
"""NC-UI Control Center — Power Profiles, Generic NUT Server / UPS & Idle (`panels/cc_power.py`)."""

from collections import deque
import ctypes
import glob
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import threading
import time
from core import probe
from core.config import (
    CONFIG,
    get_config_dir,
    get_screen_lock_cmd,
)
from core.i18n import t
from core.privileges import get_privileged_action, has_polkit_agent, request_privilege_modal
from core.resilience import load_resilience_config, resolve_sbin_helper
from core.state import STATE
from ui.kit import (
    ActionBtn,
    Badge,
    Box,
    InlineField,
    ProgressBar,
    SelectPill,
    StateBadge,
    Txt,
    cancel_inline_entry,
    feedback_cards,
    set_feedback,
    start_inline_input,
)
from panels.cc_engine_resources import get_gpu_telemetry, has_nvidia_gpu



_CACHE_LOCK = threading.Lock()
_GPU_CACHE = {"ts": 0.0, "info": "", "data": None, "pending": False}
_PSU_CACHE = {"ts": 0.0, "data": None}
_PPD_CACHE = {"ts": 0.0, "cur": "", "avail": []}
_UPOWER_CACHE = {"ts": 0.0, "peripherals": [], "pending": False}
_EPP_STD_TOKENS = frozenset({"default", "performance", "balance_performance", "balance_power", "power"})


def _fan_profiles_path() -> str:
    return os.path.join(get_config_dir(), "fan_profiles.json")
_FACTORY_FAN_PRESETS: dict[str, list[tuple[int, int]]] = {
    "silent":   [(35, 25), (62, 38), (74, 58), (82, 82), (88, 100)],
    "balanced": [(30, 35), (55, 50), (70, 72), (78, 90), (85, 100)],
    "cool":     [(25, 45), (50, 65), (65, 85), (72, 100), (85, 100)],
}


def _sanitize_curve_points(pts) -> list[tuple[int, int]]:
    base = list(_FACTORY_FAN_PRESETS["balanced"])
    out: list[list[int]] = []
    for i in range(5):
        dt, dp = base[i]
        try:
            rt, rp = pts[i]
            tc = max(20, min(95, int(round(float(rt)))))
            pp = max(20, min(100, int(round(float(rp)))))
        except Exception:
            tc, pp = dt, dp
        if i > 0:
            tc = max(out[i - 1][0], tc)
            pp = max(out[i - 1][1], pp)
        if i == 4:
            pp = max(80, pp)
        out.append([tc, pp])
    return [(t_v, p_v) for t_v, p_v in out]


def _load_fan_profiles() -> dict:
    data = {
        "active": "balanced",
        "presets": {k: list(v) for k, v in _FACTORY_FAN_PRESETS.items()},
        "custom": {},
    }
    try:
        fan_path = _fan_profiles_path()
        if os.path.isfile(fan_path):
            with open(fan_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                raw_p = raw.get("presets")
                if isinstance(raw_p, dict):
                    for k in _FACTORY_FAN_PRESETS:
                        if isinstance(raw_p.get(k), list) and len(raw_p[k]) == 5:
                            data["presets"][k] = _sanitize_curve_points(raw_p[k])
                raw_c = raw.get("custom")
                if isinstance(raw_c, dict):
                    for slug, item in raw_c.items():
                        s_clean = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(slug or "").strip()).strip("-").lower()
                        if not s_clean or s_clean in _FACTORY_FAN_PRESETS or not isinstance(item, dict):
                            continue
                        lbl = str(item.get("label") or s_clean).strip()[:18]
                        pts = item.get("points")
                        if lbl and isinstance(pts, list) and len(pts) == 5:
                            data["custom"][s_clean] = {"label": lbl, "points": _sanitize_curve_points(pts)}
                act = str(raw.get("active") or "balanced").strip()
                if act in data["presets"] or act in data["custom"] or act == "":
                    data["active"] = act
    except Exception:
        pass
    return data


def _save_fan_profiles(data: dict) -> None:
    payload = {
        "active": str(data.get("active") or ""),
        "presets": {
            k: [[t_v, p_v] for t_v, p_v in _sanitize_curve_points(data.get("presets", {}).get(k, v))]
            for k, v in _FACTORY_FAN_PRESETS.items()
        },
        "custom": {
            slug: {
                "label": str(item.get("label") or slug)[:18],
                "points": [[t_v, p_v] for t_v, p_v in _sanitize_curve_points(item.get("points", []))],
            }
            for slug, item in (data.get("custom") or {}).items()
            if slug and slug not in _FACTORY_FAN_PRESETS
        },
    }
    try:
        fan_path = _fan_profiles_path()
        os.makedirs(os.path.dirname(fan_path), exist_ok=True)
        with open(fan_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
    except Exception:
        pass


def _update_curve_point(base_pts: list[tuple[int, int]], idx: int, new_t: int | None = None, new_p: int | None = None) -> list[tuple[int, int]]:
    pts = [[t_v, p_v] for t_v, p_v in _sanitize_curve_points(base_pts)]
    idx = max(0, min(4, int(idx)))
    if new_t is not None:
        clamped_t = max(20, min(95, int(new_t)))
        pts[idx][0] = clamped_t
        for j in range(idx + 1, 5):
            pts[j][0] = max(pts[j][0], clamped_t)
        for j in range(idx - 1, -1, -1):
            pts[j][0] = min(pts[j][0], clamped_t)
    if new_p is not None:
        min_p = 80 if idx == 4 else 20
        clamped_p = max(min_p, min(100, int(new_p)))
        pts[idx][1] = clamped_p
        for j in range(idx + 1, 5):
            pts[j][1] = max(pts[j][1], clamped_p)
        for j in range(idx - 1, -1, -1):
            pts[j][1] = min(pts[j][1], clamped_p)
        pts[4][1] = max(80, pts[4][1])
    return _sanitize_curve_points(pts)


def _curves_match(a: list[tuple[int, int]], b: list[tuple[int, int]]) -> bool:
    return (
        len(a) == 5
        and len(b) == 5
        and all(ta == tb and abs(pa - pb) <= 1 for (ta, pa), (tb, pb) in zip(a, b))
    )



def _schedule_ui_refresh(cc_panel) -> None:
    shell = getattr(cc_panel, "shell", None) if cc_panel else None
    if not shell:
        return
    try:
        from gi.repository import GLib
        GLib.idle_add(lambda: bool(shell.refresh_view()) and False)
    except Exception:
        pass




_CPU_GOV_PATH = "/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor"

def _read_first_line(path: str, default: str = "-") -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            val = f.readline().strip()
            return val if val else default
    except Exception:
        return default



def _read_proc_hidepid() -> str:
    try:
        with open("/proc/self/mounts", "r", encoding="utf-8", errors="ignore") as f:
            for ln in f:
                parts = ln.split()
                if len(parts) >= 4 and parts[1] == "/proc":
                    m = re.search(r"hidepid=([^\s,]+)", parts[3])
                    return m.group(1) if m else "0"
    except Exception:
        pass
    return "0"


_AUDIT_CACHE = {"ts": 0.0, "val": "absent"}


def _probe_gaming_mode() -> dict:
    ptrace = _read_first_line("/proc/sys/kernel/yama/ptrace_scope", "n/a")
    max_map = _read_first_line("/proc/sys/vm/max_map_count", "n/a")
    dirty_bg = _read_first_line("/proc/sys/vm/dirty_background_ratio", "n/a")
    dirty_r = _read_first_line("/proc/sys/vm/dirty_ratio", "n/a")
    gov = _read_first_line(_CPU_GOV_PATH, "n/a")
    hidepid = _read_proc_hidepid()
    ntsync = os.path.exists("/dev/ntsync")
    now = time.monotonic()
    if (now - _AUDIT_CACHE["ts"]) >= 15.0:
        audit_val = "absent"
        if probe.has_bin("auditctl"):
            ok_a, out_a = probe.run_cmd(["auditctl", "-s"], timeout=0.6)
            if ok_a:
                m_a = re.search(r"(?m)^enabled\s+(\S+)", out_a)
                audit_val = m_a.group(1) if m_a else "unknown"
            else:
                audit_val = "locked"
        _AUDIT_CACHE["ts"] = now
        _AUDIT_CACHE["val"] = audit_val
    audit = _AUDIT_CACHE["val"]
    is_gaming = os.path.isfile("/run/nc-gamemode/state.env") or (max_map == "2147483642" and dirty_r == "8")
    priv = get_privileged_action("gaming_mode")
    helper = resolve_sbin_helper("nc-posture")
    return {
        "available": os.path.isfile(helper),
        "helper": helper,
        "is_gaming": is_gaming,
        "ptrace": ptrace,
        "max_map": max_map,
        "dirty_bg": dirty_bg,
        "dirty_r": dirty_r,
        "hidepid": hidepid,
        "audit": audit,
        "gov": gov,
        "ntsync": ntsync,
        "priv_ok": bool(priv and priv.check_installed()),
        "cfg": load_resilience_config()["posture"],
        "games_warn": bool(STATE.get("system", "posture_games_warn", False)),
    }



def _set_pwr_fb(ok: bool, msg: str) -> None:
    set_feedback("power", ok, msg)


def _feedback_cards() -> list:
    return feedback_cards("power", "cc_power.power_ups_status")


def _read_file_str(path: str, default: str = "") -> str:
    try:
        return open(path, "r", encoding="utf-8").read().strip()
    except Exception:
        return default


def _fetch_gpu_info_sync() -> tuple[str, dict | None]:
    g = get_gpu_telemetry(include_procs=False)
    if not g.get("available"):
        return "", None
    temp = g.get("temp_c_opt")
    draw = g.get("power_w_opt")
    limit = g.get("power_limit_w_opt")
    dpm = g.get("dpm") or ""
    gpu_data = {
        "name": g["model"],
        "category": g.get("category") or "nvidia_dgpu",
        "vendor": g.get("vendor") or "generic",
        "temp": temp,
        "draw": draw,
        "limit": limit,
        "pstate": g.get("pstate") or "",
        "clock_gr": float(g["gfx_mhz"]) if g.get("gfx_mhz") else None,
        "fan": float(g["fan_pct"]) if g.get("fan_pct") is not None else None,
        "fan_rpm": g.get("fan_rpm"),
        "util": g.get("util_pct"),
    }
    if temp is not None and draw is not None and limit is not None:
        info_str = t("cc_power.gpu_draw_fmt", temp=f"{temp:.0f}", draw=f"{draw:.1f}", limit=f"{limit:.0f}")
    elif temp is not None and dpm:
        info_str = t("cc_power.gpu_dpm_fmt", temp=int(round(temp)), level=dpm)
    elif dpm:
        info_str = t("cc_power.gpu_sysfs_fmt", level=dpm)
    elif temp is not None:
        info_str = f"{g['model']} · {temp:.0f}°C"
    else:
        info_str = g["model"]
    return info_str, gpu_data


def _collect_cpu_telemetry() -> dict:
    base = "/sys/devices/system/cpu/cpu0/cpufreq"
    driver = _read_file_str(f"{base}/scaling_driver", "")
    pstate_status = (
        _read_file_str("/sys/devices/system/cpu/amd_pstate/status", "")
        or _read_file_str("/sys/devices/system/cpu/intel_pstate/status", "")
    )
    cores = os.cpu_count() or 0

    freqs = []
    for p in glob.glob("/sys/devices/system/cpu/cpu[0-9]*/cpufreq/scaling_cur_freq"):
        raw = _read_file_str(p, "")
        if raw.isdigit():
            freqs.append(int(raw))
    cur_khz = (sum(freqs) / len(freqs)) if freqs else 0.0
    min_s = _read_file_str(f"{base}/scaling_min_freq", "")
    max_s = _read_file_str(f"{base}/scaling_max_freq", "") or _read_file_str(f"{base}/cpuinfo_max_freq", "")
    min_khz = float(min_s) if min_s.isdigit() else 0.0
    max_khz = float(max_s) if max_s.isdigit() else 0.0
    freq_pct = max(0, min(100, int(round((cur_khz / max_khz) * 100)))) if max_khz > 0 else 0

    temp_c = None
    ccd_temps: list[tuple[str, float]] = []
    hw_dirs = sorted(glob.glob("/sys/class/hwmon/hwmon*"))
    for prio_names in (("k10temp", "coretemp", "zenpower"), ("thinkpad", "dell_smm", "asus-ec-sensors", "asusec", "acpitz")):
        for hw in hw_dirs:
            hw_name = _read_file_str(os.path.join(hw, "name"), "").lower()
            if hw_name not in prio_names:
                continue
            fallback_t = None
            for tp in sorted(glob.glob(os.path.join(hw, "temp*_input"))):
                raw_t = _read_file_str(tp, "")
                if not raw_t.lstrip("-").isdigit():
                    continue
                val_c = int(raw_t) / 1000.0
                if not (1.0 < val_c < 115.0):
                    continue
                lbl = _read_file_str(tp[:-6] + "_label", "")
                if fallback_t is None:
                    fallback_t = val_c
                if lbl in ("Tctl", "Package id 0", "Tdie", "CPU"):
                    temp_c = val_c
                elif lbl.startswith(("Tccd", "Core")):
                    ccd_temps.append((lbl, val_c))
            if temp_c is None:
                temp_c = fallback_t
            if temp_c is not None:
                break
        if temp_c is not None:
            break

    return {
        "driver": driver,
        "pstate_status": pstate_status,
        "cores": cores,
        "cur_ghz": cur_khz / 1_000_000.0 if cur_khz > 0 else 0.0,
        "min_ghz": min_khz / 1_000_000.0 if min_khz > 0 else 0.0,
        "max_ghz": max_khz / 1_000_000.0 if max_khz > 0 else 0.0,
        "freq_pct": freq_pct,
        "cpu_temp": temp_c,
        "cpu_ccd_temps": ccd_temps[:4],
    }


def _collect_psu_telemetry() -> dict | None:
    for hw in sorted(glob.glob("/sys/class/hwmon/hwmon*")):
        hw_name = _read_file_str(os.path.join(hw, "name"), "").lower()
        if "psu" not in hw_name:
            continue
        data: dict = {"name": hw_name, "total_w": None, "r12_w": None, "r5_w": None, "r3_w": None, "vrm_c": None, "case_c": None, "fan_rpm": None, "vin_v": None}
        for pp in sorted(glob.glob(os.path.join(hw, "power*_input"))):
            raw = _read_file_str(pp, "")
            if not raw.isdigit():
                continue
            watts = int(raw) / 1_000_000.0
            lbl = _read_file_str(pp[:-6] + "_label", "").lower()
            if "total" in lbl or (not lbl and data["total_w"] is None):
                data["total_w"] = watts
            elif "12v" in lbl:
                data["r12_w"] = watts
            elif "5v" in lbl:
                data["r5_w"] = watts
            elif "3.3v" in lbl:
                data["r3_w"] = watts
        for tp in sorted(glob.glob(os.path.join(hw, "temp*_input"))):
            raw = _read_file_str(tp, "")
            if not raw.lstrip("-").isdigit():
                continue
            val_c = int(raw) / 1000.0
            lbl = _read_file_str(tp[:-6] + "_label", "").lower()
            if "vrm" in lbl:
                data["vrm_c"] = val_c
            elif "case" in lbl:
                data["case_c"] = val_c
        fan_s = _read_file_str(os.path.join(hw, "fan1_input"), "")
        if fan_s.isdigit():
            data["fan_rpm"] = int(fan_s)
        vin_s = _read_file_str(os.path.join(hw, "in0_input"), "")
        if vin_s.isdigit():
            data["vin_v"] = int(vin_s) / 1000.0
        if data["total_w"] is not None or data["vrm_c"] is not None:
            return data
    return None


def _collect_fan_telemetry() -> dict | None:
    """Read Nuvoton nct67* Smart Fan IV hardware curve, saved profiles, and live fan RPM/PWM telemetry."""
    for hw in sorted(glob.glob("/sys/class/hwmon/hwmon*")):
        hw_name = _read_file_str(os.path.join(hw, "name"), "").lower()
        if not hw_name.startswith("nct67"):
            continue
        en2 = _read_file_str(os.path.join(hw, "pwm2_enable"), "")
        hw_pts_raw = []
        for idx in range(1, 6):
            t_s = _read_file_str(os.path.join(hw, f"pwm2_auto_point{idx}_temp"), "")
            p_s = _read_file_str(os.path.join(hw, f"pwm2_auto_point{idx}_pwm"), "")
            t_c = (int(t_s) // 1000) if t_s.lstrip("-").isdigit() else _FACTORY_FAN_PRESETS["balanced"][idx - 1][0]
            p_pct = int(round((int(p_s) * 100.0) / 255.0)) if p_s.isdigit() else _FACTORY_FAN_PRESETS["balanced"][idx - 1][1]
            hw_pts_raw.append((t_c, p_pct))
        hw_pts = _sanitize_curve_points(hw_pts_raw)

        prof_cfg = _load_fan_profiles()
        active_id = ""
        if en2 == "5":
            pref = prof_cfg.get("active", "")
            if pref in prof_cfg["presets"] and _curves_match(prof_cfg["presets"][pref], hw_pts):
                active_id = pref
            elif pref in prof_cfg["custom"] and _curves_match(prof_cfg["custom"][pref]["points"], hw_pts):
                active_id = pref
            else:
                for key in ("silent", "balanced", "cool"):
                    if _curves_match(prof_cfg["presets"][key], hw_pts):
                        active_id = key
                        break
                if not active_id:
                    for slug, item in prof_cfg["custom"].items():
                        if _curves_match(item["points"], hw_pts):
                            active_id = slug
                            break

        if active_id in prof_cfg["presets"]:
            base_pts = list(prof_cfg["presets"][active_id])
        elif active_id in prof_cfg["custom"]:
            base_pts = list(prof_cfg["custom"][active_id]["points"])
        else:
            base_pts = list(hw_pts)

        staged_raw = STATE.get("power", "fan_staged_pts", None)
        if isinstance(staged_raw, list) and len(staged_raw) == 5:
            display_pts = _sanitize_curve_points(staged_raw)
        else:
            display_pts = list(base_pts)
        is_dirty = bool(isinstance(staged_raw, list) and len(staged_raw) == 5 and display_pts != base_pts)
        is_preset_modified = bool(
            active_id in _FACTORY_FAN_PRESETS and prof_cfg["presets"][active_id] != _FACTORY_FAN_PRESETS[active_id]
        )
        is_custom_prof = bool(active_id in prof_cfg["custom"])

        f2_s = _read_file_str(os.path.join(hw, "fan2_input"), "")
        f5_s = _read_file_str(os.path.join(hw, "fan5_input"), "")
        pwm2_s = _read_file_str(os.path.join(hw, "pwm2"), "")
        pwm5_s = _read_file_str(os.path.join(hw, "pwm5"), "")
        pwm2_pct = max(0, min(100, int(round((int(pwm2_s) / 255.0) * 100)))) if pwm2_s.isdigit() else 0
        pwm5_pct = max(0, min(100, int(round((int(pwm5_s) / 255.0) * 100)))) if pwm5_s.isdigit() else 0
        fan_priv = get_privileged_action("power_fan_profile")
        return {
            "mode": "smart_fan",
            "hwmon_name": hw_name.upper(),
            "profile": active_id,
            "smart_fan": (en2 == "5"),
            "cpu_rpm": int(f2_s) if f2_s.isdigit() else 0,
            "opt_rpm": int(f5_s) if f5_s.isdigit() else None,
            "cpu_pwm_pct": pwm2_pct,
            "opt_pwm_pct": pwm5_pct,
            "fan_priv_ok": bool(fan_priv and fan_priv.check_installed()),
            "has_helper": os.path.isfile(resolve_sbin_helper("nc-fan-profile")),
            "hw_pts": hw_pts,
            "base_pts": base_pts,
            "display_pts": display_pts,
            "is_dirty": is_dirty,
            "is_preset_modified": is_preset_modified,
            "is_custom_prof": is_custom_prof,
            "presets": prof_cfg["presets"],
            "custom": prof_cfg["custom"],
        }

    # Read-only fallback for laptops / desktops without nct67* Smart Fan IV
    ro_fans: list[dict] = []
    for hw in sorted(glob.glob("/sys/class/hwmon/hwmon*")):
        hw_name = _read_file_str(os.path.join(hw, "name"), "").lower()
        if not hw_name or hw_name in ("amdgpu", "nouveau", "i915", "xe") or "psu" in hw_name:
            continue
        for fp in sorted(glob.glob(os.path.join(hw, "fan*_input"))):
            rpm_s = _read_file_str(fp, "")
            if not rpm_s.isdigit():
                continue
            idx_s = os.path.basename(fp)[3:-6]
            lbl = _read_file_str(fp[:-6] + "_label", "") or f"Fan {idx_s}"
            pwm_s = _read_file_str(os.path.join(hw, f"pwm{idx_s}"), "")
            pwm_pct = max(0, min(100, int(round((int(pwm_s) / 255.0) * 100)))) if pwm_s.isdigit() else None
            ro_fans.append({"hwmon": hw_name, "label": lbl, "rpm": int(rpm_s), "pwm_pct": pwm_pct})
    if ro_fans:
        return {
            "mode": "readonly",
            "hwmon_name": ro_fans[0]["hwmon"].upper(),
            "fans": ro_fans[:6],
            "smart_fan": False,
            "is_dirty": False,
            "fan_priv_ok": True,
        }
    return None


_THERM_LOCK = threading.Lock()
_THERM_CATALOG: dict = {"ts": 0.0, "sensors": []}
_THERM_HISTORY: deque = deque(maxlen=62)
_THERM_LAST_TS: float = 0.0
_NVML_STATE: dict = {"init": False, "lib": None, "handle": None}


def _read_nvml_gpu_temp() -> float | None:
    if not _NVML_STATE["init"]:
        _NVML_STATE["init"] = True
        try:
            lib = ctypes.CDLL("libnvidia-ml.so.1")
            if lib.nvmlInit_v2() == 0:
                h = ctypes.c_void_p()
                if lib.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(h)) == 0:
                    _NVML_STATE["lib"] = lib
                    _NVML_STATE["handle"] = h
        except Exception:
            _NVML_STATE["lib"] = None
            _NVML_STATE["handle"] = None

    lib = _NVML_STATE["lib"]
    h = _NVML_STATE["handle"]
    if lib is not None and h is not None:
        try:
            temp = ctypes.c_uint()
            if lib.nvmlDeviceGetTemperature(h, 0, ctypes.byref(temp)) == 0:
                val = float(temp.value)
                if 1.0 < val < 115.0:
                    return val
        except Exception:
            pass
    with _CACHE_LOCK:
        gdata = _GPU_CACHE.get("data")
        if isinstance(gdata, dict) and gdata.get("temp") is not None:
            return float(gdata["temp"])
    return None


def _read_sysfs_temp_c(path: str) -> float | None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read().strip()
        if not raw.lstrip("-").isdigit():
            return None
        val = int(raw) / 1000.0
        return round(val, 1) if (1.0 < val < 115.0) else None
    except Exception:
        return None


def _discover_thermal_sensors() -> list[dict]:
    hw_dirs = sorted(glob.glob("/sys/class/hwmon/hwmon*"))
    names = {hw: _read_file_str(os.path.join(hw, "name"), "").lower() for hw in hw_dirs}
    has_asusec = any(n == "asusec" for n in names.values())
    has_cpu_hwmon = any(n in ("k10temp", "coretemp", "zenpower") for n in names.values())

    sensors: list[dict] = []
    seen_ids: set[str] = set()
    nvme_idx = 0
    gpu_added = False

    def _add(sid: str, label: str, path: str | None, kind: str, order: int) -> None:
        if sid in seen_ids:
            return
        seen_ids.add(sid)
        sensors.append({"id": sid, "label": label, "path": path, "kind": kind, "order": order})

    for hw in hw_dirs:
        hw_name = names.get(hw, "")
        if hw_name.startswith(("nct67", "it87")) and has_asusec:
            continue
        nvme_done = False
        for tp in sorted(glob.glob(os.path.join(hw, "temp*_input"))):
            val = _read_sysfs_temp_c(tp)
            if val is None:
                continue
            raw_lbl = _read_file_str(tp[:-6] + "_label", "") or os.path.basename(tp)[:-6]
            lbl_low = raw_lbl.lower()

            if hw_name in ("k10temp", "coretemp", "zenpower"):
                if lbl_low in ("tctl", "package id 0", "tdie"):
                    _add("cpu_pkg", "CPU Pkg", tp, "sysfs", 10)
                elif lbl_low.startswith("tccd"):
                    suffix = lbl_low[4:] or "0"
                    ord_n = 11 + int(suffix) if suffix.isdigit() else 15
                    _add(f"cpu_{lbl_low}", f"CPU {raw_lbl.replace('Tccd', 'CCD')}", tp, "sysfs", ord_n)
                elif lbl_low.startswith("core "):
                    num = lbl_low.split(" ", 1)[1]
                    _add(f"cpu_core_{num}", f"Core {num}", tp, "sysfs", 15)
            elif hw_name in ("amdgpu", "nouveau", "i915", "xe"):
                if not gpu_added and lbl_low in ("edge", "temp1", "junction"):
                    _add("gpu", "GPU", tp, "sysfs", 20)
                    gpu_added = True
            elif hw_name in ("asusec", "asus-ec-sensors"):
                if lbl_low == "vrm":
                    _add("mb_vrm", "MB VRM", tp, "sysfs", 30)
                elif lbl_low == "chipset":
                    _add("mb_chipset", "Chipset", tp, "sysfs", 31)
                elif lbl_low == "motherboard":
                    _add("mb_board", "Board", tp, "sysfs", 32)
                elif lbl_low == "cpu":
                    if not has_cpu_hwmon:
                        _add("cpu_pkg", "CPU", tp, "sysfs", 10)
                elif lbl_low in ("t_sensor", "water_in", "water_out"):
                    _add(f"mb_{lbl_low}", raw_lbl.replace("_", " "), tp, "sysfs", 34)
                else:
                    slug = re.sub(r"[^a-z0-9]+", "_", lbl_low).strip("_") or "aux"
                    _add(f"mb_{slug}", raw_lbl[:12], tp, "sysfs", 35)
            elif hw_name in ("thinkpad", "dell_smm"):
                if (lbl_low == "cpu" or lbl_low == "temp1") and not has_cpu_hwmon and "cpu_pkg" not in seen_ids:
                    _add("cpu_pkg", "CPU", tp, "sysfs", 10)
                else:
                    slug = re.sub(r"[^a-z0-9]+", "_", f"{hw_name}_{lbl_low}").strip("_")
                    short = raw_lbl if raw_lbl and not lbl_low.startswith("temp") else hw_name
                    _add(f"hw_{slug}", short[:12], tp, "sysfs", 33)
            elif hw_name.startswith(("nct67", "it87")):
                if lbl_low == "systin":
                    _add("mb_board", "Board", tp, "sysfs", 32)
                elif lbl_low == "cputin" and not has_cpu_hwmon:
                    _add("mb_cpu", "MB CPU", tp, "sysfs", 33)
            elif "psu" in hw_name:
                if "vrm" in lbl_low:
                    _add("psu_vrm", "PSU VRM", tp, "sysfs", 40)
                elif "case" in lbl_low:
                    _add("psu_case", "PSU Case", tp, "sysfs", 41)
                else:
                    slug = re.sub(r"[^a-z0-9]+", "_", lbl_low).strip("_") or "aux"
                    _add(f"psu_{slug}", f"PSU {raw_lbl[:8]}", tp, "sysfs", 42)
            elif hw_name == "nvme":
                if not nvme_done and lbl_low in ("composite", "temp1"):
                    nvme_idx += 1
                    _add(f"nvme_{nvme_idx}", f"NVMe {nvme_idx}", tp, "sysfs", 50 + nvme_idx)
                    nvme_done = True
            elif hw_name.startswith("r8169"):
                _add("nic", "NIC", tp, "sysfs", 60)
            elif hw_name == "acpitz":
                if not has_cpu_hwmon and "acpi_tz" not in seen_ids:
                    _add("acpi_tz", "ACPI", tp, "sysfs", 36)
            else:
                slug = re.sub(r"[^a-z0-9]+", "_", f"{hw_name}_{lbl_low}").strip("_")
                short = raw_lbl if raw_lbl and not lbl_low.startswith("temp") else hw_name
                _add(f"hw_{slug}", short[:12], tp, "sysfs", 70)

    if not gpu_added and _read_nvml_gpu_temp() is not None:
        _add("gpu", "GPU", None, "nvml", 20)

    sensors.sort(key=lambda s: (s["order"], s["id"]))
    return sensors


def get_enabled_thermal_sensors(catalog: list[dict]) -> list[str]:
    valid_ids = [s["id"] for s in catalog]
    valid_set = set(valid_ids)
    saved = STATE.get("power", "therm_enabled", None)
    if saved is None:
        return valid_ids
    if not isinstance(saved, list):
        return valid_ids
    return [sid for sid in saved if sid in valid_set]


def sample_thermal_history(force: bool = False, min_interval: float = 0.90) -> dict:
    """Collect 1 Hz readings across all valid temperature sensors and return the 60s ring buffer snapshot."""
    global _THERM_LAST_TS
    now = time.monotonic()
    with _THERM_LOCK:
        if force or not _THERM_CATALOG["sensors"] or (now - _THERM_CATALOG["ts"]) >= 30.0:
            _THERM_CATALOG["sensors"] = _discover_thermal_sensors()
            _THERM_CATALOG["ts"] = now
        catalog = list(_THERM_CATALOG["sensors"])

        if force or not _THERM_HISTORY or (now - _THERM_LAST_TS) >= float(min_interval):
            readings: dict[str, float] = {}
            for s in catalog:
                if s["kind"] == "nvml":
                    val = _read_nvml_gpu_temp()
                elif s["path"]:
                    val = _read_sysfs_temp_c(s["path"])
                else:
                    val = None
                if val is not None:
                    readings[s["id"]] = float(val)
            if readings:
                _THERM_HISTORY.append((now, readings))
                _THERM_LAST_TS = now

        history = list(_THERM_HISTORY)
        latest = dict(_THERM_HISTORY[-1][1]) if _THERM_HISTORY else {}

    return {
        "sensors": catalog,
        "history": history,
        "latest": latest,
        "enabled": get_enabled_thermal_sensors(catalog),
    }


def _detect_native_preset(gov: str, epp: str, boost: str, avail_epp: list[str]) -> str:
    b_ok_hi = boost not in ("0", "1") or boost == "1"
    b_ok_lo = boost not in ("0", "1") or boost == "0"
    if gov == "performance" and b_ok_hi:
        return "performance"
    if gov == "powersave" and (not avail_epp or epp in ("power", "balance_power")) and b_ok_lo:
        return "power-saver"
    if gov in ("schedutil", "powersave", "ondemand") and (not avail_epp or epp == "balance_performance") and b_ok_hi:
        return "balanced"
    return ""


def _collect_power_profiles(force: bool = False) -> tuple[str, list[str]]:
    if not probe.has_bin("powerprofilesctl"):
        return "", []
    now = time.monotonic()
    if not force and (now - _PPD_CACHE["ts"]) < 10.0:
        return _PPD_CACHE["cur"], list(_PPD_CACHE["avail"])
    ok_cur, out_cur = probe.run_cmd(["powerprofilesctl", "get"], timeout=1.0)
    cur = out_cur.strip() if ok_cur and out_cur else ""
    ok_lst, out_lst = probe.run_cmd(["powerprofilesctl", "list"], timeout=1.0)
    avail = []
    if ok_lst and out_lst:
        for ln in out_lst.splitlines():
            m = re.match(r"^\s*\*?\s*(performance|balanced|power-saver)\s*:", ln)
            if m and m.group(1) not in avail:
                avail.append(m.group(1))
    if not avail and cur:
        avail = ["performance", "balanced", "power-saver"]
    _PPD_CACHE["ts"] = now
    _PPD_CACHE["cur"] = cur
    _PPD_CACHE["avail"] = list(avail)
    return cur, avail


def refresh_hw_telemetry_async(cc_panel=None) -> None:
    """Refresh GPU & PSU caches asynchronously without triggering a full widget-tree rebuild."""
    now = time.monotonic()
    with _CACHE_LOCK:
        if (now - _GPU_CACHE["ts"]) < 2.5 or _GPU_CACHE["pending"]:
            return
        _GPU_CACHE["pending"] = True
        had_initial = _GPU_CACHE["data"] is not None

    def _hw_worker():
        info, gdata = _fetch_gpu_info_sync()
        pdata = _collect_psu_telemetry()
        with _CACHE_LOCK:
            _GPU_CACHE["ts"] = time.monotonic()
            _GPU_CACHE["info"] = info
            _GPU_CACHE["data"] = gdata
            _GPU_CACHE["pending"] = False
            _PSU_CACHE["ts"] = _GPU_CACHE["ts"]
            _PSU_CACHE["data"] = pdata
        if not had_initial and gdata is not None and cc_panel is not None:
            _schedule_ui_refresh(cc_panel)

    threading.Thread(target=_hw_worker, daemon=True).start()


def get_power_profiles_tick_sig() -> tuple:
    """Fast subprocess-free state signature for gating `power/profiles` periodic rebuilds."""
    base = "/sys/devices/system/cpu/cpu0/cpufreq"
    return (
        _read_file_str(f"{base}/scaling_governor", ""),
        _read_file_str(f"{base}/energy_performance_preference", ""),
        _read_file_str("/sys/devices/system/cpu/cpufreq/boost", ""),
        os.path.isfile("/run/nc-gamemode/state.env"),
        _read_file_str("/proc/sys/vm/swappiness", ""),
        _read_file_str("/proc/sys/vm/dirty_ratio", ""),
    )


def _collect_cpu_power(async_refresh: bool = False, cc_panel=None) -> dict:
    base = "/sys/devices/system/cpu/cpu0/cpufreq"
    gov = _read_file_str(f"{base}/scaling_governor", "")
    avail_gov = _read_file_str(f"{base}/scaling_available_governors", "").split()
    epp = _read_file_str(f"{base}/energy_performance_preference", "")
    avail_epp = _read_file_str(f"{base}/energy_performance_available_preferences", "").split()
    boost = _read_file_str("/sys/devices/system/cpu/cpufreq/boost", "")
    ppd_active, ppd_avail = _collect_power_profiles()
    tlp_active = False
    if probe.has_bin("tlp"):
        ok_tlp, out_tlp = probe.run_cmd(["systemctl", "is-active", "tlp"], timeout=0.6)
        tlp_active = bool(ok_tlp and out_tlp.strip() == "active")
    cpu_tele = _collect_cpu_telemetry()

    with _CACHE_LOCK:
        no_gpu_yet = (_GPU_CACHE["ts"] <= 0.0)
        no_psu_yet = (_PSU_CACHE["ts"] <= 0.0)

    if (not async_refresh) or no_gpu_yet or os.environ.get("NC_UI_OFFSCREEN") == "1":
        gpu_info, gpu_data = _fetch_gpu_info_sync()
        psu_tele = _collect_psu_telemetry()
        with _CACHE_LOCK:
            now_m = time.monotonic()
            _GPU_CACHE["ts"] = now_m
            _GPU_CACHE["info"] = gpu_info
            _GPU_CACHE["data"] = gpu_data
            _PSU_CACHE["ts"] = now_m
            _PSU_CACHE["data"] = psu_tele
    else:
        if no_psu_yet:
            psu_tele = _collect_psu_telemetry()
            with _CACHE_LOCK:
                _PSU_CACHE["ts"] = time.monotonic()
                _PSU_CACHE["data"] = psu_tele
        refresh_hw_telemetry_async(cc_panel)
        with _CACHE_LOCK:
            gpu_info = _GPU_CACHE["info"]
            gpu_data = _GPU_CACHE["data"]
            psu_tele = _PSU_CACHE["data"]

    cpu_priv = get_privileged_action("power_cpu_tune")
    return {
        "ppd_active": ppd_active,
        "ppd_avail": ppd_avail,
        "tlp_active": tlp_active,
        "has_nvidia": has_nvidia_gpu(),
        "gov": gov,
        "avail_gov": avail_gov,
        "epp": epp,
        "avail_epp": avail_epp,
        "boost": boost,
        "gpu_info": gpu_info,
        "gpu": gpu_data,
        "psu": psu_tele,
        "fans": _collect_fan_telemetry(),
        "native_preset": _detect_native_preset(gov, epp, boost, avail_epp),
        "cpu_priv_ok": bool(cpu_priv and cpu_priv.check_installed()),
        "posture": _probe_gaming_mode(),
        "swappiness": _read_file_str("/proc/sys/vm/swappiness", "60"),
        "dirty_ratio": _read_file_str("/proc/sys/vm/dirty_ratio", "20"),
        **cpu_tele,
    }


def get_cached_gpu_data() -> dict | None:
    """Return latest cached GPU telemetry dict without blocking or spawning subprocesses."""
    with _CACHE_LOCK:
        return dict(_GPU_CACHE["data"]) if isinstance(_GPU_CACHE.get("data"), dict) else None


def get_cached_psu_data() -> dict | None:
    """Return latest cached PSU telemetry dict without blocking or spawning subprocesses."""
    with _CACHE_LOCK:
        return dict(_PSU_CACHE["data"]) if isinstance(_PSU_CACHE.get("data"), dict) else None


def _collect_sysfs_batteries() -> tuple[list[dict], bool | None, list[dict]]:
    bats: list[dict] = []
    ac_supplies: list[dict] = []
    ac_online: bool | None = None
    for path in sorted(glob.glob("/sys/class/power_supply/*")):
        name = os.path.basename(path)
        stype = _read_file_str(os.path.join(path, "type"), "")
        if stype == "Mains":
            on_s = _read_file_str(os.path.join(path, "online"), "")
            if on_s in ("0", "1"):
                is_on = (on_s == "1")
                ac_supplies.append({"name": name, "online": is_on})
                ac_online = True if is_on else (False if ac_online is None else ac_online)
            continue
        if stype != "Battery" or _read_file_str(os.path.join(path, "scope"), "") == "Device":
            continue
        cap_s = _read_file_str(os.path.join(path, "capacity"), "")
        if not cap_s.isdigit():
            continue
        cap = max(0, min(100, int(cap_s)))
        status = _read_file_str(os.path.join(path, "status"), "Unknown")
        pwr_uw = _read_file_str(os.path.join(path, "power_now"), "")
        cur_ua = _read_file_str(os.path.join(path, "current_now"), "")
        volt_uv = _read_file_str(os.path.join(path, "voltage_now"), "")
        watts = None
        if pwr_uw.isdigit() and int(pwr_uw) > 0:
            watts = round(int(pwr_uw) / 1_000_000.0, 1)
        elif cur_ua.isdigit() and volt_uv.isdigit() and int(cur_ua) > 0 and int(volt_uv) > 0:
            watts = round((int(cur_ua) * int(volt_uv)) / 1_000_000_000_000.0, 1)

        def _int_attr(attr: str) -> int | None:
            v = _read_file_str(os.path.join(path, attr), "")
            return int(v) if v.isdigit() else None

        e_full = _int_attr("energy_full")
        e_des = _int_attr("energy_full_design")
        e_now = _int_attr("energy_now")
        c_full = _int_attr("charge_full")
        c_des = _int_attr("charge_full_design")
        c_now = _int_attr("charge_now")
        health_pct = None
        if e_full and e_des and e_des > 0:
            health_pct = max(1, min(100, int(round((e_full / float(e_des)) * 100.0))))
        elif c_full and c_des and c_des > 0:
            health_pct = max(1, min(100, int(round((c_full / float(c_des)) * 100.0))))

        cyc = _int_attr("cycle_count")
        cycle_count = cyc if (cyc is not None and cyc > 0) else None

        est_time_str = ""
        rate = int(pwr_uw) if pwr_uw.isdigit() and int(pwr_uw) > 0 else (int(cur_ua) if cur_ua.isdigit() and int(cur_ua) > 0 else 0)
        now_val = e_now if (pwr_uw.isdigit() and e_now is not None) else c_now
        full_val = e_full if (pwr_uw.isdigit() and e_full is not None) else c_full
        if rate > 0 and now_val is not None:
            hrs = None
            if status.lower() == "discharging" and now_val > 0:
                hrs = now_val / float(rate)
            elif status.lower() == "charging" and full_val is not None and full_val > now_val:
                hrs = (full_val - now_val) / float(rate)
            if hrs is not None and 0.02 <= hrs <= 48.0:
                mins = int(round(hrs * 60.0))
                est_time_str = f"{mins // 60}h {mins % 60:02d}m"

        end_thr = _read_file_str(os.path.join(path, "charge_control_end_threshold"), "")
        start_thr = _read_file_str(os.path.join(path, "charge_control_start_threshold"), "")
        bats.append({
            "name": name,
            "capacity": cap,
            "status": status,
            "watts": watts,
            "health_pct": health_pct,
            "cycle_count": cycle_count,
            "est_time_str": est_time_str,
            "end_threshold": int(end_thr) if end_thr.isdigit() else None,
            "has_threshold": os.path.exists(os.path.join(path, "charge_control_end_threshold")),
            "start_threshold": int(start_thr) if start_thr.isdigit() else None,
            "has_start_threshold": os.path.exists(os.path.join(path, "charge_control_start_threshold")),
        })
    return bats, ac_online, ac_supplies


def _fetch_upower_peripherals_sync() -> list[dict]:
    if not probe.has_bin("upower"):
        return []
    ok, out = probe.run_cmd(["upower", "-d"], timeout=1.5)
    if not ok or not out:
        return []
    devices = []
    cur: dict = {}
    for line in out.splitlines():
        s = line.strip()
        if line.startswith("Device:"):
            if cur:
                devices.append(cur)
            cur = {"path": s.split(":", 1)[1].strip()}
        elif cur and ":" in s:
            k, v = s.split(":", 1)
            cur[k.strip().lower()] = v.strip()
    if cur:
        devices.append(cur)

    peripherals = []
    for d in devices:
        p = d.get("path", "")
        if "DisplayDevice" in p or "line_power" in p or "/battery_BAT" in p:
            continue
        pct = d.get("percentage", "")
        if not pct:
            continue
        model = d.get("model") or os.path.basename(p)
        kind = next((k for k in ("mouse", "keyboard", "headset", "headphones", "gaming-input", "touchpad", "ups", "phone") if k in p.lower()), "device")
        peripherals.append({"name": model, "type": kind.replace("-", " ").title(), "charge": pct})
    return peripherals


def _collect_batteries_and_devices(cc_panel=None) -> dict:
    bats, ac_online, ac_supplies = _collect_sysfs_batteries()
    now = time.monotonic()
    need_fetch = False
    with _CACHE_LOCK:
        peripherals = list(_UPOWER_CACHE["peripherals"])
        if (now - _UPOWER_CACHE["ts"]) >= 10.0 and not _UPOWER_CACHE["pending"]:
            _UPOWER_CACHE["pending"] = True
            need_fetch = True
    if need_fetch:
        def _upower_worker():
            res = _fetch_upower_peripherals_sync()
            with _CACHE_LOCK:
                _UPOWER_CACHE["ts"] = time.monotonic()
                _UPOWER_CACHE["peripherals"] = res
                _UPOWER_CACHE["pending"] = False
            _schedule_ui_refresh(cc_panel)
        threading.Thread(target=_upower_worker, daemon=True).start()
    return {"batteries": bats, "peripherals": peripherals, "ac_online": ac_online, "ac_supplies": ac_supplies}


def _read_logind_power_key() -> str:
    for path in ("/etc/systemd/logind.conf.d/ncui.conf", "/etc/systemd/logind.conf"):
        txt = _read_file_str(path, "")
        for ln in txt.splitlines():
            m = re.match(r"^\s*HandlePowerKey\s*=\s*([a-z-]+)", ln)
            if m:
                return m.group(1)
    return "poweroff"


def _is_caffeine_active() -> bool:
    ok_unit, out_unit = probe.run_cmd(["systemctl", "--user", "is-active", "ncui-caffeine"], timeout=0.5)
    if ok_unit and out_unit.strip() == "active":
        return True
    pid = STATE.get("power", "caffeine_pid", None)
    if isinstance(pid, int) and pid > 1 and os.path.exists(f"/proc/{pid}"):
        return True
    ok_pg, _ = probe.run_cmd(["pgrep", "-f", "systemd-inhibit.*NC-UI"], timeout=0.5)
    return bool(ok_pg)


def _collect_idle_state() -> dict:
    idle_min = int(STATE.get("power", "idle_lock_min", 10) or 0)
    dpms_min = int(STATE.get("display", "dpms_timeout", 15) or 0)
    dim_idle = bool(STATE.get("power", "dim_on_idle", False))
    susp_ac = int(STATE.get("power", "suspend_ac_min", 0) or 0)
    susp_bat = int(STATE.get("power", "suspend_bat_min", 0) or 0)
    caffeine = _is_caffeine_active()
    idle_daemon = "swayidle" if probe.run_cmd(["pgrep", "-x", "swayidle"], timeout=0.5)[0] else (
        "hypridle" if probe.run_cmd(["pgrep", "-x", "hypridle"], timeout=0.5)[0] else "Stopped"
    )
    return {
        "idle_min": idle_min,
        "dpms_min": dpms_min,
        "dim_idle": dim_idle,
        "susp_ac": susp_ac,
        "susp_bat": susp_bat,
        "power_key": _read_logind_power_key(),
        "caffeine": caffeine,
        "idle_daemon": idle_daemon,
        "lock_cmd": get_screen_lock_cmd(),
    }


def _apply_idle_daemon() -> tuple[bool, str]:
    st = _collect_idle_state()
    idle_min = st["idle_min"]
    dpms_min = st["dpms_min"]
    dim_idle = st["dim_idle"]
    susp_ac = st["susp_ac"]
    susp_bat = st["susp_bat"]
    lock_cmd = st["lock_cmd"]

    has_hypr = probe.has_bin("hypridle")
    has_sway = probe.has_bin("swayidle")
    hypr_running = probe.run_cmd(["pgrep", "-x", "hypridle"], timeout=0.4)[0]
    sway_running = probe.run_cmd(["pgrep", "-x", "swayidle"], timeout=0.4)[0]
    xdg_cfg = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    hypr_conf = os.path.join(xdg_cfg, "hypr", "hypridle.conf")

    if idle_min == 0 and dpms_min == 0 and not dim_idle and susp_ac == 0 and susp_bat == 0:
        probe.run_cmd(["pkill", "-x", "swayidle"], timeout=0.6)
        probe.run_cmd(["pkill", "-x", "hypridle"], timeout=0.6)
        return True, t("cc_power.fb_idle_stopped")

    use_hypr = hypr_running or (not sway_running and has_hypr and (os.path.exists(hypr_conf) or not has_sway))
    if not use_hypr and not has_sway:
        return False, t("cc_power.fb_idle_missing")

    dim_sec = max(60, (idle_min * 60) // 2 if idle_min > 0 else 150)

    if use_hypr:
        lines = ["general {"]
        if lock_cmd:
            lines.append(f"    lock_cmd = pidof hyprlock || {lock_cmd}" if "hyprlock" in lock_cmd else f"    lock_cmd = {lock_cmd}")
            lines.append("    before_sleep_cmd = loginctl lock-session")
        lines.append("    after_sleep_cmd = niri msg action power-on-monitors")
        lines.append("}")
        if dim_idle and probe.has_bin("brightnessctl"):
            lines += ["listener {", f"    timeout = {dim_sec}", "    on-timeout = brightnessctl -s set 10%", "    on-resume = brightnessctl -r", "}"]
        if idle_min > 0 and lock_cmd:
            lines += ["listener {", f"    timeout = {idle_min * 60}", f"    on-timeout = {lock_cmd}", "}"]
        if dpms_min > 0:
            lines += ["listener {", f"    timeout = {dpms_min * 60}", "    on-timeout = niri msg action power-off-monitors", "    on-resume = niri msg action power-on-monitors", "}"]
        if susp_bat > 0 and susp_bat == susp_ac:
            lines += ["listener {", f"    timeout = {susp_ac * 60}", "    on-timeout = systemctl suspend", "}"]
        else:
            if susp_bat > 0:
                lines += ["listener {", f"    timeout = {susp_bat * 60}", "    on-timeout = systemd-ac-power || systemctl suspend", "}"]
            if susp_ac > 0:
                lines += ["listener {", f"    timeout = {susp_ac * 60}", "    on-timeout = systemd-ac-power && systemctl suspend", "}"]
        os.makedirs(os.path.dirname(hypr_conf), exist_ok=True)
        with open(hypr_conf, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        probe.run_cmd(["pkill", "-x", "swayidle"], timeout=0.6)
        probe.run_cmd(["pkill", "-x", "hypridle"], timeout=0.6)
        probe.spawn_bg(["hypridle"])
        time.sleep(0.15)
        ok, _ = probe.run_cmd(["pgrep", "-x", "hypridle"], timeout=0.5)
        return ok, t("cc_power.fb_idle_applied", p0="hypridle")

    argv = ["swayidle", "-w"]
    if dim_idle and probe.has_bin("brightnessctl"):
        argv.extend(["timeout", str(dim_sec), "brightnessctl -s set 10%", "resume", "brightnessctl -r"])
    if idle_min > 0 and lock_cmd:
        argv.extend(["timeout", str(idle_min * 60), lock_cmd])
    if dpms_min > 0:
        argv.extend(["timeout", str(dpms_min * 60), "niri msg action power-off-monitors", "resume", "niri msg action power-on-monitors"])
    if susp_bat > 0 and susp_bat == susp_ac:
        argv.extend(["timeout", str(susp_ac * 60), "systemctl suspend"])
    else:
        if susp_bat > 0:
            argv.extend(["timeout", str(susp_bat * 60), "systemd-ac-power || systemctl suspend"])
        if susp_ac > 0:
            argv.extend(["timeout", str(susp_ac * 60), "systemd-ac-power && systemctl suspend"])
    if lock_cmd:
        argv.extend(["before-sleep", lock_cmd])
    probe.run_cmd(["pkill", "-x", "hypridle"], timeout=0.6)
    probe.run_cmd(["pkill", "-x", "swayidle"], timeout=0.6)
    probe.spawn_bg(argv)
    time.sleep(0.15)
    ok, _ = probe.run_cmd(["pgrep", "-x", "swayidle"], timeout=0.5)
    return ok, t("cc_power.fb_idle_applied", p0="swayidle")



def _run_cpu_priv(cc_panel, argv: list[str], stdin_text: str | None = None) -> tuple[bool, str, bool]:
    """Execute a CPU tuning command via sudoers (`power_cpu_tune`) or graphical Polkit; open modal if neither is ready."""
    cpu_priv = get_privileged_action("power_cpu_tune")
    if cpu_priv and cpu_priv.check_installed():
        cmd = ["sudo", "-n"] + argv
        if stdin_text is not None:
            try:
                res = subprocess.run(cmd, input=stdin_text, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=3.0)
                ok, err = (res.returncode == 0), (res.stdout or "").strip()
            except Exception as e:
                ok, err = False, str(e)
        else:
            ok, _o, err = probe.run_cmd_full(cmd, timeout=3.0)
        low_err = err.lower()
        if not ok and any(tok in low_err for tok in ("password is required", "not allowed", "no tty present")):
            request_privilege_modal(cc_panel, "power_cpu_tune")
            return False, "", True
        return ok, err, False

    if has_polkit_agent() and probe.has_bin("pkexec"):
        pk_argv = list(argv)
        if pk_argv and pk_argv[0] == "/usr/bin/cpupower":
            pk_argv[0] = shutil.which("cpupower") or "/usr/bin/cpupower"
        cmd = ["pkexec"] + pk_argv
        if stdin_text is not None:
            try:
                res = subprocess.run(cmd, input=stdin_text, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=12.0)
                return (res.returncode == 0), (res.stdout or "").strip(), False
            except Exception as e:
                return False, str(e), False
        ok, _o, err = probe.run_cmd_full(cmd, timeout=12.0)
        return ok, err, False

    request_privilege_modal(cc_panel, "power_cpu_tune")
    return False, "", True


def _apply_epp_value(cc_panel, epp: str) -> tuple[bool, str, bool]:
    try:
        for p in glob.glob("/sys/devices/system/cpu/cpu*/cpufreq/energy_performance_preference"):
            with open(p, "w", encoding="utf-8") as f:
                f.write(epp)
        return True, "", False
    except PermissionError:
        if probe.has_bin("cpupower") and epp in _EPP_STD_TOKENS:
            return _run_cpu_priv(cc_panel, ["/usr/bin/cpupower", "set", "-e", epp])
        if has_polkit_agent() and probe.has_bin("pkexec"):
            ok, _o, err = probe.run_cmd_full(
                ["pkexec", "bash", "-c", 'for f in /sys/devices/system/cpu/cpu*/cpufreq/energy_performance_preference; do printf "%s" "$1" > "$f"; done', "_", epp],
                timeout=12.0,
            )
            return ok, err, False
        request_privilege_modal(cc_panel, "power_cpu_tune")
        return False, "", True
    except Exception as e:
        return False, str(e), False


def _apply_boost_value(cc_panel, val: str) -> tuple[bool, str, bool]:
    boost_path = "/sys/devices/system/cpu/cpufreq/boost"
    try:
        with open(boost_path, "w", encoding="utf-8") as f:
            f.write(val)
        return True, "", False
    except PermissionError:
        return _run_cpu_priv(cc_panel, ["/usr/bin/tee", boost_path], stdin_text=val)
    except Exception as e:
        return False, str(e), False


def _apply_fan_curve_priv(cc_panel, pts: list[tuple[int, int]]) -> tuple[bool, str, bool]:
    """Apply a 5-point Smart Fan IV curve via sudoers (`power_fan_profile`) or Polkit and verify sysfs readback."""
    clean_pts = _sanitize_curve_points(pts)
    payload = ",".join(f"{t_c}:{p_pct}" for t_c, p_pct in clean_pts) + "\n"
    helper = resolve_sbin_helper("nc-fan-profile")
    fan_priv = get_privileged_action("power_fan_profile")

    if fan_priv and fan_priv.check_installed():
        try:
            res = subprocess.run(
                ["sudo", "-n", helper, "apply-stdin"],
                input=payload,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=4.0,
            )
            ok, err = (res.returncode == 0), (res.stdout or "").strip()
        except Exception as e:
            ok, err = False, str(e)
        low_err = err.lower()
        if not ok and any(tok in low_err for tok in ("password is required", "not allowed", "no tty present")):
            request_privilege_modal(cc_panel, "power_fan_profile")
            return False, "", True
    elif has_polkit_agent() and probe.has_bin("pkexec"):
        try:
            res = subprocess.run(
                ["pkexec", helper, "apply-stdin"],
                input=payload,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=12.0,
            )
            ok, err = (res.returncode == 0), (res.stdout or "").strip()
        except Exception as e:
            ok, err = False, str(e)
    else:
        request_privilege_modal(cc_panel, "power_fan_profile")
        return False, "", True

    if ok:
        fresh = _collect_fan_telemetry()
        ok = bool(fresh and fresh["smart_fan"] and _curves_match(clean_pts, fresh["hw_pts"]))
        if not ok and not err:
            err = "Sysfs readback mismatch"
    return ok, err, False


def _fan_profile_display_name(prof_id: str, prof_cfg: dict) -> str:
    if prof_id in _FACTORY_FAN_PRESETS:
        return t(f"cc_power.fan_{prof_id}")
    if prof_id in prof_cfg.get("custom", {}):
        return str(prof_cfg["custom"][prof_id].get("label") or prof_id)
    return prof_id or t("cc_power.mode_custom")


def handle_cc_power_link(cc_panel, uri: str) -> bool:
    if not uri.startswith("cc_pwr:"):
        return False
    act = uri[len("cc_pwr:"):]
    cancel_inline_entry(refresh=False)

    if act == "refresh":
        with _CACHE_LOCK:
            _GPU_CACHE["ts"] = 0.0
            _UPOWER_CACHE["ts"] = 0.0
        with _THERM_LOCK:
            _THERM_CATALOG["ts"] = 0.0
    elif act == "therm_all":
        STATE.set("power", "therm_enabled", None)
    elif act == "therm_core":
        snap = sample_thermal_history(min_interval=0.90)
        valid_ids = [s["id"] for s in snap["sensors"]]
        core_order = ("cpu_pkg", "gpu", "mb_vrm", "psu_vrm", "nvme_1")
        picked = [sid for sid in core_order if sid in valid_ids]
        if not picked:
            picked = valid_ids[:4]
        STATE.set("power", "therm_enabled", picked)
    elif act.startswith("therm_tog:"):
        sid = act.split(":", 1)[1].strip()
        snap = sample_thermal_history(min_interval=0.90)
        valid_ids = [s["id"] for s in snap["sensors"]]
        if sid in valid_ids:
            cur_list = list(get_enabled_thermal_sensors(snap["sensors"]))
            if sid in cur_list:
                cur_set = set(cur_list) - {sid}
            else:
                cur_set = set(cur_list) | {sid}
            ordered = [x for x in valid_ids if x in cur_set]
            STATE.set("power", "therm_enabled", ordered)
    elif act == "noop_fan":
        return True
    elif act == "auth_cpu":
        request_privilege_modal(cc_panel, "power_cpu_tune")
    elif act == "auth_fan":
        request_privilege_modal(cc_panel, "power_fan_profile")
    elif act.startswith("fan_prof:"):
        prof_id = act.split(":", 1)[1].strip()
        prof_cfg = _load_fan_profiles()
        if prof_id in prof_cfg["presets"]:
            pts = prof_cfg["presets"][prof_id]
        elif prof_id in prof_cfg["custom"]:
            pts = prof_cfg["custom"][prof_id]["points"]
        else:
            pts = None
        if pts is not None:
            STATE.set("power", "fan_staged_pts", None)
            ok, err, opened_modal = _apply_fan_curve_priv(cc_panel, pts)
            if not opened_modal:
                if ok:
                    prof_cfg["active"] = prof_id
                    _save_fan_profiles(prof_cfg)
                prof_lbl = _fan_profile_display_name(prof_id, prof_cfg)
                _set_pwr_fb(ok, t("cc_power.fb_fan_set", p0=prof_lbl) if ok else t("cc_power.fb_fan_err", p0=err or t("cc_power.priv_required")))
    elif act.startswith("fan_pt_t:"):
        parts = act.split(":")
        if len(parts) == 3:
            try:
                idx = int(parts[1])
                new_t = int(round(float(parts[2])))
                fans = _collect_fan_telemetry()
                if fans:
                    nxt = _update_curve_point(fans["display_pts"], idx, new_t=new_t)
                    STATE.set("power", "fan_staged_pts", None if nxt == fans["base_pts"] else [[a, b] for a, b in nxt])
            except Exception:
                pass
    elif act.startswith("fan_t_in:"):
        try:
            idx = max(0, min(4, int(act.split(":", 1)[1])))
        except Exception:
            return True
        fans = _collect_fan_telemetry()
        cur_t = str(fans["display_pts"][idx][0]) if fans else "50"
        start_inline_input(
            f"cc_fan_t_{idx}",
            shell=cc_panel.shell,
            trigger_uri=uri,
            initial_text=cur_t,
            max_len=3,
            validator=lambda s: s.strip().isdigit() and 20 <= int(s.strip()) <= 95,
            on_commit=lambda s, i=idx: handle_cc_power_prompt(cc_panel, f"cc_fan_t|{i}", s),
        )
        return True
    elif act.startswith("fan_pt_p:"):
        parts = act.split(":")
        if len(parts) == 3:
            try:
                idx = int(parts[1])
                new_p = int(round(float(parts[2])))
                fans = _collect_fan_telemetry()
                if fans:
                    nxt = _update_curve_point(fans["display_pts"], idx, new_p=new_p)
                    STATE.set("power", "fan_staged_pts", None if nxt == fans["base_pts"] else [[a, b] for a, b in nxt])
            except Exception:
                pass
        if cc_panel and hasattr(cc_panel, "request_throttled_refresh"):
            cc_panel.request_throttled_refresh()
        elif cc_panel and cc_panel.shell:
            cc_panel.shell.refresh_view()
        return True
    elif act == "fan_discard":
        STATE.set("power", "fan_staged_pts", None)
    elif act == "fan_apply":
        fans = _collect_fan_telemetry()
        if fans and fans["is_dirty"]:
            pts = fans["display_pts"]
            ok, err, opened_modal = _apply_fan_curve_priv(cc_panel, pts)
            if not opened_modal:
                if ok:
                    prof_cfg = _load_fan_profiles()
                    aid = fans["profile"]
                    if aid in prof_cfg["presets"]:
                        prof_cfg["presets"][aid] = list(pts)
                    elif aid in prof_cfg["custom"]:
                        prof_cfg["custom"][aid]["points"] = list(pts)
                    else:
                        aid = "custom"
                        prof_cfg["custom"][aid] = {"label": "Custom", "points": list(pts)}
                    prof_cfg["active"] = aid
                    _save_fan_profiles(prof_cfg)
                    STATE.set("power", "fan_staged_pts", None)
                    lbl = _fan_profile_display_name(aid, prof_cfg)
                    _set_pwr_fb(True, t("cc_power.fb_fan_curve_applied", p0=lbl))
                else:
                    _set_pwr_fb(False, t("cc_power.fb_fan_err", p0=err or t("cc_power.priv_required")))
    elif act == "fan_reset_preset":
        fans = _collect_fan_telemetry()
        aid = fans["profile"] if fans else ""
        if aid in _FACTORY_FAN_PRESETS:
            pts = list(_FACTORY_FAN_PRESETS[aid])
            STATE.set("power", "fan_staged_pts", None)
            ok, err, opened_modal = _apply_fan_curve_priv(cc_panel, pts)
            if not opened_modal:
                if ok:
                    prof_cfg = _load_fan_profiles()
                    prof_cfg["presets"][aid] = pts
                    prof_cfg["active"] = aid
                    _save_fan_profiles(prof_cfg)
                    _set_pwr_fb(True, t("cc_power.fb_fan_preset_reset", p0=t(f"cc_power.fan_{aid}")))
                else:
                    _set_pwr_fb(False, t("cc_power.fb_fan_err", p0=err or t("cc_power.priv_required")))
    elif act == "fan_new_in":
        start_inline_input(
            "cc_fan_new_prof",
            shell=cc_panel.shell,
            trigger_uri=uri,
            initial_text="",
            max_len=18,
            validator=lambda s: bool(re.match(r"^[A-Za-z0-9 _.-]{1,18}$", s.strip())),
            on_commit=lambda s: handle_cc_power_prompt(cc_panel, "cc_fan_new_prof", s),
        )
        return True
    elif act.startswith("fan_del:"):
        slug = act.split(":", 1)[1].strip()
        prof_cfg = _load_fan_profiles()
        if slug in prof_cfg["custom"]:
            old_lbl = prof_cfg["custom"][slug].get("label", slug)
            del prof_cfg["custom"][slug]
            prof_cfg["active"] = "balanced"
            STATE.set("power", "fan_staged_pts", None)
            _save_fan_profiles(prof_cfg)
            ok, err, opened_modal = _apply_fan_curve_priv(cc_panel, prof_cfg["presets"]["balanced"])
            if not opened_modal:
                _set_pwr_fb(ok, t("cc_power.fb_fan_prof_deleted", p0=old_lbl) if ok else t("cc_power.fb_fan_err", p0=err or t("cc_power.priv_required")))
    elif act.startswith("ppd:"):
        prof = act.split(":", 1)[1]
        _, avail = _collect_power_profiles(force=True)
        if prof in avail:
            ok, _o, err = probe.run_cmd_full(["powerprofilesctl", "set", prof], timeout=2.0)
            cur, _ = _collect_power_profiles(force=True)
            ok = ok and (cur == prof)
            _set_pwr_fb(ok, t("cc_power.fb_ppd_set", p0=prof) if ok else t("cc_power.fb_ppd_err", p0=err or prof))
    elif act.startswith("preset:"):
        prof = act.split(":", 1)[1]
        if prof in ("performance", "balanced", "power-saver"):
            st = _collect_cpu_power()
            avail_gov, avail_epp = st["avail_gov"], st["avail_epp"]
            has_boost = st["boost"] in ("0", "1")
            if prof == "performance":
                t_gov = "performance" if "performance" in avail_gov else ""
                t_epp = "performance" if "performance" in avail_epp else ""
                t_boost = "1" if has_boost else ""
            elif prof == "balanced":
                t_gov = "schedutil" if "schedutil" in avail_gov else ("powersave" if "powersave" in avail_gov else "")
                t_epp = "balance_performance" if "balance_performance" in avail_epp else ""
                t_boost = "1" if has_boost else ""
            else:
                t_gov = "powersave" if "powersave" in avail_gov else ""
                t_epp = "power" if "power" in avail_epp else ("balance_power" if "balance_power" in avail_epp else "")
                t_boost = "0" if has_boost else ""

            ok, err, opened_modal = True, "", False
            # Apply governor BEFORE EPP so amd-pstate-epp unlocks non-performance EPP when leaving performance governor
            if t_gov and st["gov"] != t_gov:
                ok, err, opened_modal = _run_cpu_priv(cc_panel, ["/usr/bin/cpupower", "frequency-set", "-g", t_gov])
            if ok and not opened_modal and t_epp and st["epp"] != t_epp:
                ok, err, opened_modal = _apply_epp_value(cc_panel, t_epp)
            if ok and not opened_modal and t_boost and st["boost"] != t_boost:
                ok, err, opened_modal = _apply_boost_value(cc_panel, t_boost)

            if not opened_modal:
                cur_gov = _read_file_str("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor", "")
                cur_epp = _read_file_str("/sys/devices/system/cpu/cpu0/cpufreq/energy_performance_preference", "")
                cur_boost = _read_file_str("/sys/devices/system/cpu/cpufreq/boost", "")
                ok = (
                    ok
                    and (not t_gov or cur_gov == t_gov)
                    and (not t_epp or cur_epp == t_epp)
                    and (not t_boost or cur_boost == t_boost)
                )
                prof_lbl = t(f"cc_power.mode_{prof.replace('-', '_')}")
                _set_pwr_fb(ok, t("cc_power.fb_ppd_set", p0=prof_lbl) if ok else t("cc_power.fb_ppd_err", p0=err or t("cc_power.priv_required")))
    elif act.startswith("gov:"):
        gov = act.split(":", 1)[1]
        st = _collect_cpu_power()
        if gov in st["avail_gov"]:
            ok, err, opened_modal = _run_cpu_priv(cc_panel, ["/usr/bin/cpupower", "frequency-set", "-g", gov])
            if not opened_modal:
                cur_gov = _read_file_str("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor", "")
                ok = ok and (cur_gov == gov)
                _set_pwr_fb(ok, t("cc_power.fb_gov_set", p0=gov) if ok else t("cc_power.fb_gov_err", p0=err or t("cc_power.priv_required")))
    elif act.startswith("epp:"):
        epp = act.split(":", 1)[1]
        st = _collect_cpu_power()
        if epp in st["avail_epp"]:
            ok, err, opened_modal = True, "", False
            if st["gov"] == "performance" and epp != "performance" and "powersave" in st["avail_gov"]:
                ok, err, opened_modal = _run_cpu_priv(cc_panel, ["/usr/bin/cpupower", "frequency-set", "-g", "powersave"])
            if ok and not opened_modal:
                ok, err, opened_modal = _apply_epp_value(cc_panel, epp)
            if not opened_modal:
                cur_epp = _read_file_str("/sys/devices/system/cpu/cpu0/cpufreq/energy_performance_preference", "")
                ok = ok and (cur_epp == epp)
                _set_pwr_fb(ok, t("cc_power.fb_epp_set", p0=epp) if ok else t("cc_power.fb_epp_err", p0=err or t("cc_power.priv_required")))
    elif act.startswith("boost:"):
        val = act.split(":", 1)[1]
        if val in ("0", "1"):
            ok, err, opened_modal = _apply_boost_value(cc_panel, val)
            if not opened_modal:
                cur_boost = _read_file_str("/sys/devices/system/cpu/cpufreq/boost", "")
                ok = ok and (cur_boost == val)
                lbl = t("common.on") if val == "1" else t("common.off")
                _set_pwr_fb(ok, t("cc_power.fb_boost_set", p0=lbl) if ok else t("cc_power.fb_boost_err", p0=err or t("cc_power.priv_required")))
    elif act.startswith("posture:"):
        target = act.split(":", 1)[1]
        if target in ("on", "off", "off-force"):
            gm = _probe_gaming_mode()
            helper = gm["helper"]
            priv = get_privileged_action("gaming_mode")
            opened_modal = False
            rc = 1
            out = ""
            if priv and priv.check_installed() and os.path.isfile(helper):
                res = subprocess.run(["sudo", "-n", helper, target], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=12.0)
                rc, out = res.returncode, (res.stdout or "").strip()
                low = out.lower()
                if rc != 0 and any(tok in low for tok in ("password is required", "not allowed", "no tty present")):
                    request_privilege_modal(cc_panel, "gaming_mode")
                    opened_modal = True
            elif has_polkit_agent() and probe.has_bin("pkexec") and os.path.isfile(helper):
                res = subprocess.run(["pkexec", helper, target], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=18.0)
                rc, out = res.returncode, (res.stdout or "").strip()
            else:
                request_privilege_modal(cc_panel, "gaming_mode")
                opened_modal = True

            if not opened_modal:
                gm2 = _probe_gaming_mode()
                want_gaming = (target == "on")
                if rc == 3 or ("running" in out.lower() and not want_gaming):
                    STATE.set("system", "posture_games_warn", True)
                    _set_pwr_fb(False, t("cc_system.gaming_running_warn"))
                elif rc == 0 and gm2["is_gaming"] == want_gaming:
                    STATE.set("system", "posture_games_warn", False)
                    msg_key = "cc_system.gaming_switched_on" if want_gaming else "cc_system.gaming_switched_off"
                    _set_pwr_fb(True, t(msg_key, p0=gm2["gov"]))
                else:
                    _set_pwr_fb(False, t("cc_system.gaming_switch_err", p0=(out or "error")[:120]))
    elif act.startswith("posture_knob:") and ":" in act[len("posture_knob:"):]:
        k, v = act[len("posture_knob:"):].split(":", 1)
        valid_bool = {"ntsync", "proc_hidepid", "ptrace_relax", "vm_tuning", "suspend_audit", "nvidia_persistence"}
        if k in valid_bool and v in ("true", "false"):
            helper = resolve_sbin_helper("nc-posture")
            priv = get_privileged_action("gaming_mode")
            payload = f"{k}={v}\n"
            opened_modal = False
            ok, err = False, ""
            if priv and priv.check_installed() and os.path.isfile(helper):
                res = subprocess.run(["sudo", "-n", helper, "config-stdin"], input=payload, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=5.0)
                ok, err = (res.returncode == 0), (res.stdout or "").strip()
                if not ok and any(tok in err.lower() for tok in ("password is required", "not allowed", "no tty present")):
                    request_privilege_modal(cc_panel, "gaming_mode")
                    opened_modal = True
            elif has_polkit_agent() and probe.has_bin("pkexec") and os.path.isfile(helper):
                res = subprocess.run(["pkexec", helper, "config-stdin"], input=payload, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=12.0)
                ok, err = (res.returncode == 0), (res.stdout or "").strip()
            else:
                request_privilege_modal(cc_panel, "gaming_mode")
                opened_modal = True
            if not opened_modal:
                _set_pwr_fb(ok, t("cc_system.fb_posture_saved", p0=k, p1=v) if ok else (err or t("cc_system.gaming_switch_err", p0="config")))
    elif act.startswith("sysctl:"):
        parts = act.split(":", 2)
        if len(parts) == 3:
            _pre, key, val = parts
            if (key == "vm.swappiness" and val in ("10", "60", "100")) or (key == "vm.dirty_ratio" and val in ("5", "8", "10", "20")):
                ok, _o, err = probe.run_cmd_full(["pkexec", "sysctl", "-w", f"{key}={val}"], timeout=8.0)
                _set_pwr_fb(ok, f"{key}={val}" if ok else (err or "sysctl failed"))
    elif act.startswith("bat_thr:") or act.startswith("bat_start:"):
        is_start = act.startswith("bat_start:")
        rest = act.split(":", 1)[1]
        if ":" in rest:
            bat_name, thr = rest.split(":", 1)
            valid_vals = ("40", "60", "75") if is_start else ("80", "100")
            attr = "charge_control_start_threshold" if is_start else "charge_control_end_threshold"
            if re.fullmatch(r"[A-Za-z0-9_.-]{1,32}", bat_name) and ".." not in bat_name and thr in valid_vals:
                thr_path = f"/sys/class/power_supply/{bat_name}/{attr}"
                if os.path.exists(thr_path):
                    ok, err = True, ""
                    try:
                        with open(thr_path, "w", encoding="utf-8") as f:
                            f.write(thr)
                    except PermissionError:
                        if has_polkit_agent() and probe.has_bin("pkexec"):
                            try:
                                res = subprocess.run(["pkexec", "/usr/bin/tee", thr_path], input=thr, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=12.0)
                                ok = res.returncode == 0
                                err = (res.stdout or "").strip()
                            except Exception as e:
                                ok, err = False, str(e)
                        else:
                            ok, err = False, t("cc_power.priv_required")
                    except Exception as e:
                        ok, err = False, str(e)
                    ok = ok and (_read_file_str(thr_path, "") == thr)
                    _set_pwr_fb(ok, t("cc_power.fb_charge_limit_set", p0=bat_name, p1=thr) if ok else t("cc_power.fb_charge_limit_err", p0=err or t("cc_power.priv_required")))
    elif act.startswith("idle_min:"):
        STATE.set("power", "idle_lock_min", int(act.split(":", 1)[1]))
        ok, msg = _apply_idle_daemon()
        _set_pwr_fb(ok, msg)
    elif act.startswith("dpms_min:"):
        STATE.set("display", "dpms_timeout", int(act.split(":", 1)[1]))
        ok, msg = _apply_idle_daemon()
        _set_pwr_fb(ok, msg)
    elif act.startswith("dim_idle:"):
        on = act.split(":", 1)[1] == "1"
        STATE.set("power", "dim_on_idle", on)
        ok, msg = _apply_idle_daemon()
        _set_pwr_fb(ok, msg if not ok else t("cc_power.fb_dim_set", p0=t("common.enabled").lower() if on else t("common.disabled").lower()))
    elif act.startswith("susp_ac:"):
        mins = int(act.split(":", 1)[1])
        STATE.set("power", "suspend_ac_min", mins)
        ok, msg = _apply_idle_daemon()
        lbl = f"{mins}m" if mins else t("cc_power.never")
        _set_pwr_fb(ok, t("cc_power.fb_suspend_ac_set", p0=lbl) if ok else msg)
    elif act.startswith("susp_bat:"):
        mins = int(act.split(":", 1)[1])
        STATE.set("power", "suspend_bat_min", mins)
        ok, msg = _apply_idle_daemon()
        lbl = f"{mins}m" if mins else t("cc_power.never")
        _set_pwr_fb(ok, t("cc_power.fb_suspend_bat_set", p0=lbl) if ok else msg)
    elif act.startswith("pwr_btn:"):
        key = act.split(":", 1)[1]
        if key in ("poweroff", "suspend", "hibernate", "ignore"):
            logind_priv = get_privileged_action("power_logind")
            conf_path = "/etc/systemd/logind.conf.d/ncui.conf"
            content = f"[Login]\nHandlePowerKey={key}\n"
            ok, err = False, ""
            if logind_priv and logind_priv.check_installed():
                ok1, _o1, err1 = probe.run_cmd_full(["sudo", "-n", "/usr/bin/mkdir", "-p", "/etc/systemd/logind.conf.d"], timeout=2.0)
                if ok1:
                    res = subprocess.run(["sudo", "-n", "/usr/bin/tee", conf_path], input=content, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=3.0)
                    ok = res.returncode == 0
                    err = (res.stdout or "").strip()
                else:
                    err = err1
            elif has_polkit_agent() and probe.has_bin("pkexec"):
                ok, _o, err = probe.run_cmd_full(
                    ["pkexec", "bash", "-c", 'mkdir -p /etc/systemd/logind.conf.d && printf "%s" "$1" > /etc/systemd/logind.conf.d/ncui.conf', "_", content],
                    timeout=15.0,
                )
            else:
                request_privilege_modal(cc_panel, "power_logind")
            if ok or err:
                ok = ok and (_read_logind_power_key() == key)
                _set_pwr_fb(ok, t("cc_power.fb_pwr_btn_set", p0=key) if ok else t("cc_power.fb_pwr_btn_err", p0=err or t("cc_power.priv_required")))
    elif act.startswith("caffeine:"):
        on = act.split(":", 1)[1] == "on"
        probe.run_cmd(["systemctl", "--user", "stop", "ncui-caffeine"], timeout=1.0)
        old_pid = STATE.get("power", "caffeine_pid", None)
        if isinstance(old_pid, int) and old_pid > 1:
            try:
                if "systemd-inhibit" in _read_file_str(f"/proc/{old_pid}/cmdline", ""):
                    os.kill(old_pid, signal.SIGTERM)
            except Exception:
                pass
            STATE.set("power", "caffeine_pid", None)
        probe.run_cmd(["pkill", "-f", "systemd-inhibit.*NC-UI"], timeout=0.6)
        if on:
            started_unit = False
            if probe.has_bin("systemd-run"):
                started_unit, _ = probe.run_cmd(
                    ["systemd-run", "--user", "--unit=ncui-caffeine", "--description=NC-UI Caffeine Mode", "systemd-inhibit", "--what=idle", "--who=NC-UI", "--why=Caffeine Mode", "sleep", "infinity"],
                    timeout=1.5,
                )
            if not started_unit:
                proc = probe.spawn_bg(["systemd-inhibit", "--what=idle", "--who=NC-UI", "--why=Caffeine Mode", "sleep", "infinity"])
                pid = getattr(proc, "pid", proc)
                if isinstance(pid, int):
                    STATE.set("power", "caffeine_pid", pid)
        state_lbl = t("common.enabled").lower() if on else t("common.disabled").lower()
        _set_pwr_fb(True, t("cc_power.caffeine", p0=state_lbl))
    elif act == "lock_now":
        cmd = get_screen_lock_cmd()
        if not cmd:
            _set_pwr_fb(False, t("cc_power.no_screen_lock_command_configured"))
        else:
            try:
                argv = shlex.split(cmd)
            except Exception as e:
                argv, err_s = [], str(e)
            else:
                err_s = ""
            if argv and (probe.has_bin(argv[0]) or os.path.isfile(argv[0])):
                probe.spawn_bg(argv)
            else:
                _set_pwr_fb(False, t("cc_power.fb_lock_err", p0=err_s or (argv[0] if argv else cmd)))

    if cc_panel and cc_panel.shell:
        cc_panel.shell.refresh_view()
    return True


def handle_cc_power_prompt(cc_panel, action_id: str, text: str) -> bool:
    val = str(text or "").strip()
    if not val:
        return False
    if action_id.startswith("cc_fan_t|"):
        try:
            idx = max(0, min(4, int(action_id.split("|", 1)[1])))
            new_t = int(val)
        except Exception:
            return True
        if 20 <= new_t <= 95:
            fans = _collect_fan_telemetry()
            if fans:
                nxt = _update_curve_point(fans["display_pts"], idx, new_t=new_t)
                STATE.set("power", "fan_staged_pts", None if nxt == fans["base_pts"] else [[a, b] for a, b in nxt])
        return True
    if action_id == "cc_fan_new_prof":
        if not re.match(r"^[A-Za-z0-9 _.-]{1,18}$", val):
            _set_pwr_fb(False, t("cc_power.fb_fan_invalid_name"))
            return True
        slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", val.lower()).strip("-") or "custom"
        if slug in _FACTORY_FAN_PRESETS:
            slug = f"u-{slug}"
        fans = _collect_fan_telemetry()
        pts = fans["display_pts"] if fans else list(_FACTORY_FAN_PRESETS["balanced"])
        prof_cfg = _load_fan_profiles()
        prof_cfg["custom"][slug] = {"label": val[:18], "points": list(pts)}
        STATE.set("power", "fan_staged_pts", None)
        ok, err, opened_modal = _apply_fan_curve_priv(cc_panel, pts)
        if not opened_modal:
            if ok:
                prof_cfg["active"] = slug
                _save_fan_profiles(prof_cfg)
                _set_pwr_fb(True, t("cc_power.fb_fan_prof_created", p0=val[:18]))
            else:
                _save_fan_profiles(prof_cfg)
                _set_pwr_fb(False, t("cc_power.fb_fan_err", p0=err or t("cc_power.priv_required")))
        return True
    return False
