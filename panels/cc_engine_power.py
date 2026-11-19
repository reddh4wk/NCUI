#!/usr/bin/env python3
"""NC-UI Control Center — Power Profiles, Generic NUT Server / UPS & Idle (`panels/cc_power.py`)."""

import glob
import os
import re
import shlex
import signal
import subprocess
import threading
import time
from core import probe
from core.config import (
    CONFIG,
    ROOT_DIR,
    _unique_existing_paths,
    _update_toml_key_in_section,
    get_screen_lock_cmd,
)
from core.i18n import t
from core.privileges import get_privileged_action, request_privilege_modal
from core.state import STATE
from ui.kit import ActionBtn, Badge, Box, FeedbackBox, InlineField, ProgressBar, SelectPill, StateBadge, Txt, start_inline_input

_NUT_TOKEN_RE = re.compile(r"^[a-zA-Z0-9._-]+$")
_CACHE_LOCK = threading.Lock()
_UPS_CACHE = {"ts": 0.0, "target": "", "data": None, "pending": False}
_GPU_CACHE = {"ts": 0.0, "info": "", "pending": False}
_UPOWER_CACHE = {"ts": 0.0, "peripherals": [], "pending": False}


def invalidate_ups_cache() -> None:
    with _CACHE_LOCK:
        _UPS_CACHE["ts"] = 0.0


def _schedule_ui_refresh(cc_panel) -> None:
    shell = getattr(cc_panel, "shell", None) if cc_panel else None
    if not shell:
        return
    try:
        from gi.repository import GLib
        GLib.idle_add(shell.refresh_view)
    except Exception:
        pass


def _is_valid_nut_token(val: str) -> bool:
    s = str(val or "").strip()
    return bool(s and not s.startswith("-") and _NUT_TOKEN_RE.match(s))


def _is_valid_nut_ups_input(raw_val: str) -> bool:
    s = str(raw_val or "").strip()
    if not s:
        return False
    if "@" not in s:
        return _is_valid_nut_token(s)
    name, hp = s.split("@", 1)
    if not _is_valid_nut_token(name):
        return False
    if ":" in hp:
        h_part, p_part = hp.rsplit(":", 1)
        return _is_valid_nut_token(h_part) and p_part.strip().isdigit() and 1 <= int(p_part.strip()) <= 65535
    return _is_valid_nut_token(hp)

POPUP_FIELD_DEFS = [
    ("status",  True),
    ("charge",  True),
    ("runtime", True),
    ("load",    True),
    ("power",   True),
    ("voltage", True),
    ("model",   True),
    ("mfr",     False),
    ("serial",  False),
    ("temp",    False),
]

DEFAULT_POPUP_FIELDS = {k: default for k, default in POPUP_FIELD_DEFS}

_STATUS_LOCALE_KEYS = {
    "OL":      ("status_online",          "Online"),
    "OB":      ("status_on_battery",      "On Battery"),
    "LB":      ("status_low_battery",     "Low Battery"),
    "CHRG":    ("status_charging",        "Charging"),
    "DISCHRG": ("status_discharging",     "Discharging"),
    "BYPASS":  ("status_bypass",          "Bypass"),
    "CAL":     ("status_calibrating",     "Calibrating"),
    "OVER":    ("status_overloaded",      "Overloaded"),
    "RB":      ("status_replace_battery", "Replace Battery"),
    "TRIM":    ("status_trim",            "Trimming"),
    "BOOST":   ("status_boost",           "Boosting"),
    "FSD":     ("status_fsd",             "Forced Shutdown"),
    "OFF":     ("status_offline",         "Offline"),
}



def format_runtime_top2(seconds, is_it: bool = False) -> str:
    """Convert seconds into the top 2 largest non-zero units (years..seconds)."""
    try:
        rem = max(0, int(float(seconds)))
    except Exception:
        return ""
    if rem == 0:
        return t("cc_power.zero_seconds")

    units = (
        (31536000, "year"),
        (2592000,  "month"),
        (86400,    "day"),
        (3600,     "hour"),
        (60,       "minute"),
        (1,        "second"),
    )
    parts = []
    for div, ukey in units:
        val, rem = divmod(rem, div)
        if val > 0:
            unit_lbl = t(f"cc_power.unit_{ukey}_1") if val == 1 else t(f"cc_power.unit_{ukey}_n")
            parts.append(f"{val} {unit_lbl}")
            if len(parts) == 2:
                break
    return ", ".join(parts)


def _split_nut_target(raw_name: str, host: str, port: int) -> tuple[str, str, int]:
    """Normalize legacy `ups@host[:port]` strings into separate fields."""
    name = str(raw_name or "").strip()
    if "@" in name:
        ups_part, hp_part = name.split("@", 1)
        name = ups_part.strip()
        if ":" in hp_part:
            h_part, p_part = hp_part.rsplit(":", 1)
            if h_part.strip() and host == "127.0.0.1":
                host = h_part.strip()
            if p_part.strip().isdigit() and port == 3493:
                port = int(p_part.strip())
        elif hp_part.strip() and host == "127.0.0.1":
            host = hp_part.strip()
    return name, host, port


def get_nut_config() -> dict:
    nut_cfg = CONFIG.get("power", {}).get("nut", {})
    if not isinstance(nut_cfg, dict):
        nut_cfg = {}

    enabled = bool(STATE.get("power", "nut_enabled", nut_cfg.get("enabled", False)))
    host = str(STATE.get("power", "nut_host", nut_cfg.get("host", "127.0.0.1")) or "127.0.0.1").strip()
    try:
        port = int(STATE.get("power", "nut_port", nut_cfg.get("port", 3493)) or 3493)
    except Exception:
        port = 3493
    ups_raw = str(STATE.get("power", "nut_ups_name", nut_cfg.get("ups_name", "")) or "")
    ups_name, host, port = _split_nut_target(ups_raw, host, port)

    try:
        poll_interval = max(1, int(STATE.get("power", "nut_poll_interval", nut_cfg.get("poll_interval", 5)) or 5))
    except Exception:
        poll_interval = 5
    try:
        low_pct = max(1, min(99, int(STATE.get("power", "ups_low_pct", nut_cfg.get("low_pct", 20)) or 20)))
    except Exception:
        low_pct = 20

    discovered = STATE.get("power", "nut_discovered", [])
    if not isinstance(discovered, list):
        discovered = []

    saved_flds = STATE.get("power", "nut_popup_fields", {})
    popup_fields = dict(DEFAULT_POPUP_FIELDS)
    if isinstance(saved_flds, dict):
        for k in popup_fields:
            if k in saved_flds:
                popup_fields[k] = bool(saved_flds[k])

    return {
        "enabled": enabled,
        "host": host,
        "port": port,
        "ups_name": ups_name,
        "poll_interval": poll_interval,
        "low_pct": low_pct,
        "discovered": [str(x).strip() for x in discovered if str(x).strip()],
        "popup_fields": popup_fields,
    }


def _save_nut_toml(key: str, raw_val: str, py_val) -> None:
    CONFIG.setdefault("power", {}).setdefault("nut", {})[key] = py_val
    _update_toml_key_in_section("power.nut", key, raw_val)


def save_nut_config(**kwargs) -> dict:
    invalidate_cache = False
    if "enabled" in kwargs:
        val = bool(kwargs["enabled"])
        STATE.set("power", "nut_enabled", val)
        _save_nut_toml("enabled", "true" if val else "false", val)
        invalidate_cache = True
    if "host" in kwargs:
        val = str(kwargs["host"] or "").strip() or "127.0.0.1"
        if _is_valid_nut_token(val):
            STATE.set("power", "nut_host", val)
            _save_nut_toml("host", f'"{val}"', val)
            invalidate_cache = True
    if "port" in kwargs:
        val = max(1, min(65535, int(kwargs["port"])))
        STATE.set("power", "nut_port", val)
        _save_nut_toml("port", str(val), val)
        invalidate_cache = True
    if "ups_name" in kwargs:
        val = str(kwargs["ups_name"] or "").strip()
        if val == "" or _is_valid_nut_token(val):
            STATE.set("power", "nut_ups_name", val)
            _save_nut_toml("ups_name", f'"{val}"', val)
            invalidate_cache = True
    if "poll_interval" in kwargs:
        val = max(1, min(3600, int(kwargs["poll_interval"])))
        STATE.set("power", "nut_poll_interval", val)
        _save_nut_toml("poll_interval", str(val), val)
        _sync_waybar_ups_interval(val)
    if "low_pct" in kwargs:
        val = max(1, min(99, int(kwargs["low_pct"])))
        STATE.set("power", "ups_low_pct", val)
        _save_nut_toml("low_pct", str(val), val)
    if invalidate_cache:
        invalidate_ups_cache()
    return get_nut_config()


def _find_jsonc_object_span(raw: str, key: str) -> tuple[int, int] | None:
    """Locate `{...}` bounds for a JSONC object property without stripping comments."""
    m = re.search(rf'"{re.escape(key)}"\s*:\s*\{{', raw)
    if not m:
        return None
    start = m.end() - 1
    depth = 0
    i = start
    n = len(raw)
    in_str = False
    while i < n:
        ch = raw[i]
        if in_str:
            if ch == "\\":
                i += 2
                continue
            if ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == "/" and i + 1 < n and raw[i + 1] == "/":
                nl = raw.find("\n", i + 2)
                i = nl if nl != -1 else n
                continue
            elif ch == "/" and i + 1 < n and raw[i + 1] == "*":
                end_c = raw.find("*/", i + 2)
                i = (end_c + 2) if end_c != -1 else n
                continue
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return start, i + 1
        i += 1
    return None


def _sync_waybar_ups_interval(sec: int) -> None:
    """Update `custom/ups` interval in-place across Waybar JSONC configs."""
    paths = _unique_existing_paths(
        os.path.expanduser("~/.config/waybar/config.jsonc"),
        os.path.join(ROOT_DIR, "config", "waybar", "config.jsonc"),
        os.path.expanduser("~/Projects/ncui/config/waybar/config.jsonc"),
    )
    for rp in paths:
        try:
            with open(rp, "r", encoding="utf-8") as f:
                raw = f.read()
            span = _find_jsonc_object_span(raw, "custom/ups")
            if not span:
                continue
            s_idx, e_idx = span
            block = raw[s_idx:e_idx]
            new_block, count = re.subn(r'("interval"\s*:\s*)\d+', rf"\g<1>{int(sec)}", block, count=1)
            if count == 0:
                new_block = '{\n        "interval": ' + str(int(sec)) + "," + block[1:]
            if new_block != block:
                with open(rp, "w", encoding="utf-8") as f:
                    f.write(raw[:s_idx] + new_block + raw[e_idx:])
        except Exception:
            pass
    probe.run_cmd(["pkill", "-SIGUSR2", "waybar"], timeout=0.6)


def _format_nut_target(cfg: dict) -> str:
    if not cfg["ups_name"]:
        return ""
    host = cfg["host"] or "127.0.0.1"
    port = cfg["port"] or 3493
    return f"{cfg['ups_name']}@{host}:{port}"


def discover_nut_ups() -> tuple[bool, list[str], str]:
    cfg = get_nut_config()
    if not probe.has_bin("upsc"):
        return False, [], "upsc binary not found"
    target = f"{cfg['host']}:{cfg['port']}"
    ok, out, err = probe.run_cmd_full(["upsc", "-l", target], timeout=2.5)
    if not ok:
        return False, [], err or f"upsc -l {target} failed"
    found = [ln.strip() for ln in out.splitlines() if ln.strip() and not ln.strip().startswith("Init SSL")]
    STATE.set("power", "nut_discovered", found)
    if found and (not cfg["ups_name"] or cfg["ups_name"] not in found):
        save_nut_config(ups_name=found[0])
    return True, found, ""


def _decode_ups_status(raw_status: str, low_pct: int, charge: int | None, reachable: bool) -> tuple[str, str]:
    if not reachable:
        return t("ups.status_offline", "Offline"), "offline"
    tokens = [tok.strip().upper() for tok in str(raw_status or "").split() if tok.strip()]
    labels = []
    for tok in tokens:
        if tok in _STATUS_LOCALE_KEYS:
            lk, fb = _STATUS_LOCALE_KEYS[tok]
            labels.append(t(f"ups.{lk}", fb))
        else:
            labels.append(tok)
    decoded = ", ".join(labels) if labels else t("ups.status_online", "Online")

    if "LB" in tokens or ("OB" in tokens and charge is not None and charge <= low_pct):
        css_state = "low-battery"
    elif "OB" in tokens or "DISCHRG" in tokens:
        css_state = "on-battery"
    elif "OFF" in tokens:
        css_state = "offline"
    else:
        css_state = "online"
    return decoded, css_state


def _parse_opt_int(raw: dict, *keys: str) -> int | None:
    for k in keys:
        if k in raw:
            try:
                return int(round(float(raw[k])))
            except Exception:
                pass
    return None


def _first_raw(raw: dict, *keys: str) -> str | None:
    for k in keys:
        val = str(raw.get(k, "")).strip()
        if val:
            return val
    return None


def _base_ups_dict(cfg: dict, target: str) -> dict:
    return {
        **cfg,
        "target": target,
        "reachable": False,
        "polling": False,
        "error": "",
        "raw": {},
        "status_raw": "",
        "status_text": t("ups.status_offline", "Offline"),
        "css_state": "offline",
        "charge": None,
        "runtime_sec": None,
        "runtime_str": None,
        "load": None,
        "realpower": None,
        "apparent_power": None,
        "vin": None,
        "vout": None,
        "vbat": None,
        "freq_in": None,
        "freq_out": None,
        "temp": None,
        "mfr": None,
        "model": None,
        "serial": None,
        "nominal_w": None,
        "firmware": None,
        "dev_type": None,
    }


def _fetch_ups_sync(cfg: dict, target: str) -> dict:
    data = _base_ups_dict(cfg, target)
    if not cfg["enabled"] or not target or not probe.has_bin("upsc"):
        return data
    if not _is_valid_nut_token(cfg["host"]) or not _is_valid_nut_token(cfg["ups_name"]):
        data["error"] = "Invalid NUT target"
        return data

    ok, out, err = probe.run_cmd_full(["upsc", target], timeout=2.0)
    if not ok or not out:
        data["error"] = err or "Connection failed"
        return data

    raw = {}
    for line in out.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            raw[k.strip()] = v.strip()

    if not raw:
        data["error"] = "Empty upsc response"
        return data

    charge = _parse_opt_int(raw, "battery.charge")
    runtime_sec = _parse_opt_int(raw, "battery.runtime")
    raw_status = raw.get("ups.status", "OL")
    status_text, css_state = _decode_ups_status(raw_status, cfg["low_pct"], charge, True)

    data.update({
        "reachable": True,
        "raw": raw,
        "status_raw": raw_status,
        "status_text": status_text,
        "css_state": css_state,
        "charge": charge,
        "runtime_sec": runtime_sec,
        "runtime_str": format_runtime_top2(runtime_sec) if runtime_sec is not None else None,
        "load": _parse_opt_int(raw, "ups.load"),
        "realpower": _first_raw(raw, "ups.realpower"),
        "apparent_power": _first_raw(raw, "ups.power"),
        "vin": _first_raw(raw, "input.voltage"),
        "vout": _first_raw(raw, "output.voltage"),
        "vbat": _first_raw(raw, "battery.voltage"),
        "freq_in": _first_raw(raw, "input.frequency"),
        "freq_out": _first_raw(raw, "output.frequency"),
        "temp": _first_raw(raw, "ups.temperature", "battery.temperature"),
        "mfr": _first_raw(raw, "ups.mfr", "device.mfr"),
        "model": _first_raw(raw, "ups.model", "device.model"),
        "serial": _first_raw(raw, "ups.serial", "device.serial"),
        "nominal_w": _first_raw(raw, "ups.realpower.nominal", "ups.power.nominal"),
        "firmware": _first_raw(raw, "ups.firmware", "driver.version"),
        "dev_type": _first_raw(raw, "device.type"),
    })
    return data


def _collect_ups_data(async_refresh: bool = False, cc_panel=None) -> dict:
    cfg = get_nut_config()
    target = _format_nut_target(cfg)
    if not async_refresh:
        return _fetch_ups_sync(cfg, target)

    if not cfg["enabled"] or not target or not probe.has_bin("upsc"):
        return _base_ups_dict(cfg, target)

    now = time.monotonic()
    need_fetch = False
    cached_copy = None
    with _CACHE_LOCK:
        if _UPS_CACHE["target"] == target and _UPS_CACHE["data"] is not None:
            cached_copy = dict(_UPS_CACHE["data"])
            cached_copy.update(cfg)
            if (now - _UPS_CACHE["ts"]) >= max(2.0, float(cfg["poll_interval"])) and not _UPS_CACHE["pending"]:
                _UPS_CACHE["pending"] = True
                need_fetch = True
        elif not _UPS_CACHE["pending"]:
            _UPS_CACHE["pending"] = True
            need_fetch = True

    if need_fetch:
        def _worker():
            fresh = _fetch_ups_sync(cfg, target)
            with _CACHE_LOCK:
                _UPS_CACHE["ts"] = time.monotonic()
                _UPS_CACHE["target"] = target
                _UPS_CACHE["data"] = fresh
                _UPS_CACHE["pending"] = False
            _schedule_ui_refresh(cc_panel)

        threading.Thread(target=_worker, daemon=True).start()

    if cached_copy is not None:
        return cached_copy
    pending_data = _base_ups_dict(cfg, target)
    pending_data["polling"] = True
    pending_data["status_text"] = t("cc_power.polling")
    return pending_data


def _set_pwr_fb(ok: bool, msg: str) -> None:
    STATE.set("power", "cc_fb", {"ok": bool(ok), "msg": str(msg), "ts": time.time()})


def _get_pwr_fb() -> dict:
    fb = STATE.get("power", "cc_fb", {})
    if isinstance(fb, dict) and fb.get("msg") and (time.time() - float(fb.get("ts", 0))) < 15.0:
        return fb
    return {}


def _feedback_cards() -> list:
    fb = _get_pwr_fb()
    if not fb:
        return []
    return [FeedbackBox(t("cc_power.power_ups_status"), bool(fb["ok"]), fb["msg"])]


def _read_file_str(path: str, default: str = "") -> str:
    try:
        return open(path, "r", encoding="utf-8").read().strip()
    except Exception:
        return default


def _collect_sysfs_gpu_info() -> str:
    """Fallback GPU telemetry for AMD/Intel sysfs when nvidia-smi is absent."""
    for card in sorted(glob.glob("/sys/class/drm/card[0-9]*/device")):
        dpm = _read_file_str(os.path.join(card, "power_dpm_force_performance_level"), "")
        temp_c = None
        for tp in sorted(glob.glob(os.path.join(card, "hwmon", "hwmon*", "temp1_input"))):
            raw_t = _read_file_str(tp, "")
            if raw_t.isdigit():
                temp_c = int(raw_t) // 1000
                break
        if temp_c is not None and dpm:
            return t("cc_power.gpu_dpm_fmt", temp=temp_c, level=dpm)
        if dpm:
            return t("cc_power.gpu_sysfs_fmt", level=dpm)
        if temp_c is not None:
            return f"{temp_c}°C"
    return ""


def _fetch_gpu_info_sync() -> str:
    if probe.has_bin("nvidia-smi"):
        ok, out = probe.run_cmd(
            ["nvidia-smi", "--query-gpu=temperature.gpu,power.draw,power.limit", "--format=csv,noheader,nounits"],
            timeout=1.2,
        )
        if ok and out:
            parts = [p.strip() for p in out.splitlines()[0].split(",")]
            if len(parts) >= 3:
                return t("cc_power.gpu_draw_fmt", temp=parts[0], draw=parts[1], limit=parts[2])
            return out.splitlines()[0].strip()
    return _collect_sysfs_gpu_info()


def _collect_power_profiles() -> tuple[str, list[str]]:
    if not probe.has_bin("powerprofilesctl"):
        return "", []
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
    return cur, avail


def _collect_cpu_power(async_refresh: bool = False, cc_panel=None) -> dict:
    base = "/sys/devices/system/cpu/cpu0/cpufreq"
    gov = _read_file_str(f"{base}/scaling_governor", "unknown")
    avail_gov = _read_file_str(f"{base}/scaling_available_governors", "performance schedutil powersave").split()
    epp = _read_file_str(f"{base}/energy_performance_preference", "")
    avail_epp = _read_file_str(f"{base}/energy_performance_available_preferences", "").split()
    boost = _read_file_str("/sys/devices/system/cpu/cpufreq/boost", "")
    ppd_active, ppd_avail = _collect_power_profiles()

    if not async_refresh:
        gpu_info = _fetch_gpu_info_sync()
    else:
        now = time.monotonic()
        need_fetch = False
        with _CACHE_LOCK:
            gpu_info = _GPU_CACHE["info"]
            if (now - _GPU_CACHE["ts"]) >= 5.0 and not _GPU_CACHE["pending"]:
                _GPU_CACHE["pending"] = True
                need_fetch = True
        if need_fetch:
            def _gpu_worker():
                info = _fetch_gpu_info_sync()
                with _CACHE_LOCK:
                    _GPU_CACHE["ts"] = time.monotonic()
                    _GPU_CACHE["info"] = info
                    _GPU_CACHE["pending"] = False
                _schedule_ui_refresh(cc_panel)
            threading.Thread(target=_gpu_worker, daemon=True).start()

    cpu_priv = get_privileged_action("power_cpu_tune")
    return {
        "ppd_active": ppd_active,
        "ppd_avail": ppd_avail,
        "gov": gov,
        "avail_gov": avail_gov,
        "epp": epp,
        "avail_epp": avail_epp,
        "boost": boost,
        "gpu_info": gpu_info,
        "cpu_priv_ok": bool(cpu_priv and cpu_priv.check_installed()),
    }


def _collect_sysfs_batteries() -> list[dict]:
    bats = []
    for path in sorted(glob.glob("/sys/class/power_supply/BAT*")):
        name = os.path.basename(path)
        cap_s = _read_file_str(os.path.join(path, "capacity"), "")
        if not cap_s.isdigit():
            continue
        cap = max(0, min(100, int(cap_s)))
        status = _read_file_str(os.path.join(path, "status"), "Unknown")
        pwr_uw = _read_file_str(os.path.join(path, "power_now"), "")
        watts = None
        if pwr_uw.isdigit() and int(pwr_uw) > 0:
            watts = round(int(pwr_uw) / 1_000_000.0, 1)
        end_thr = _read_file_str(os.path.join(path, "charge_control_end_threshold"), "")
        bats.append({
            "name": name,
            "capacity": cap,
            "status": status,
            "watts": watts,
            "end_threshold": int(end_thr) if end_thr.isdigit() else None,
            "has_threshold": os.path.exists(os.path.join(path, "charge_control_end_threshold")),
        })
    return bats


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
    bats = _collect_sysfs_batteries()
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
    return {"batteries": bats, "peripherals": peripherals}


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
    hypr_conf = os.path.expanduser("~/.config/hypr/hypridle.conf")

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


def _ups_status_badge(u: dict) -> str:
    if not u["enabled"]:
        return StateBadge(False, "ON", t("cc_power.disabled"))
    if u.get("polling"):
        return Badge(t("cc_power.polling").upper(), style="warn")
    if not u["reachable"]:
        return Badge(u["status_text"].upper(), style="crit")
    st_up = u["status_text"].upper()
    if u["css_state"] == "online":
        return StateBadge(True, st_up, on_tone="ok")
    if u["css_state"] == "on-battery":
        return StateBadge(True, st_up, on_tone="warn")
    return StateBadge(True, st_up, on_tone="crit")


def _build_ups_connection_rows(b: Box, u: dict) -> None:
    en_pills = f"{SelectPill(t('cc_power.enabled'), 'cc_pwr:nut_en:1', selected=u['enabled'])} {SelectPill(t('cc_power.disabled'), 'cc_pwr:nut_en:0', selected=not u['enabled'])}"
    b.add_kv(t("cc_power.nut_monitor"), en_pills, requires=["upsc"])

    b.add_kv(
        t("cc_power.server_host_ip"),
        InlineField("cc_pwr_host", "cc_pwr:inline_host", value=u["host"], empty_label=t("cc_power.edit"), placeholder="127.0.0.1", width=16, icon="\u270e", tone="neutral", selected=False),
    )
    b.add_kv(
        t("cc_power.server_port"),
        InlineField("cc_pwr_port", "cc_pwr:inline_port", value=str(u["port"]), empty_label=t("cc_power.edit"), placeholder="3493", width=7, icon="\u270e", tone="neutral", selected=False),
    )

    ups_field = InlineField("cc_pwr_ups", "cc_pwr:inline_ups", value=u["ups_name"], empty_label=t("cc_power.edit"), placeholder="ecoflow", width=16, icon="\u270e", tone="neutral", selected=False)
    ups_val = ups_field if u["ups_name"] else f"{ups_field} {Badge(t('cc_power.not_set_badge'), style='warn')}"
    b.add_kv(t("cc_power.ups_name"), ups_val, right_markup=ActionBtn(t("cc_power.auto_discover"), "cc_pwr:discover", tone="apply"))

    if u["discovered"]:
        disc_pills = " ".join(
            SelectPill(name, f"cc_pwr:sel_ups:{name}", selected=(name == u["ups_name"]))
            for name in u["discovered"][:8]
        )
        b.add_kv(t("cc_power.discovered_ups"), disc_pills)

    poll_custom = f"{u['poll_interval']}s" if u["poll_interval"] not in (2, 5, 10, 15, 30) else ""
    poll_pills = " ".join(
        SelectPill(f"{s}s", f"cc_pwr:poll:{s}", selected=(u["poll_interval"] == s))
        for s in (2, 5, 10, 15, 30)
    ) + " " + InlineField(
        "cc_pwr_poll",
        "cc_pwr:inline_poll",
        value=poll_custom,
        empty_label=t("cc_power.custom"),
        placeholder="5",
        width=6,
        icon="",
        tone="neutral",
        selected=bool(poll_custom),
    )
    b.add_kv(t("cc_power.waybar_polling"), poll_pills)


def _append_ups_status_section(b: Box, cc_panel, u: dict) -> None:
    if not u["enabled"]:
        b.add_kv(t("cc_power.telemetry"), Txt(t("ups.disabled_hint", "NUT UPS monitor is disabled."), role="inactive"))
        return
    if u.get("polling"):
        b.add_kv(t("cc_power.telemetry"), Txt(t("cc_power.polling"), role="inactive"))
        return
    if not u["reachable"]:
        msg = t("ups.unreachable_hint", "NUT server or UPS unreachable.")
        if u["error"]:
            msg += f" ({u['error'][:48]})"
        b.add_kv(t("cc_power.telemetry"), Txt(msg, role="attention"))
        return

    st_role = "vitality" if u["css_state"] == "online" else ("attention" if u["css_state"] == "on-battery" else "fault")
    b.add_kv(t("cc_power.operational_status"), Txt(f"{u['status_text']} ({u['status_raw']})", role=st_role))

    if u["charge"] is not None:
        rt_part = f"  {Txt('(' + u['runtime_str'] + ')', role='schema')}" if u["runtime_str"] else ""
        ch_role = "vitality" if int(u["charge"]) >= 50 else ("attention" if int(u["charge"]) >= 15 else "fault")
        b.add_kv(t("cc_power.battery_charge"), f"{ProgressBar(u['charge'], width=18, color_by_threshold=False)} {Txt(str(u['charge']) + '%', role=ch_role)}{rt_part}")
    elif u["runtime_str"]:
        b.add_kv(t("cc_power.popup_fld_runtime"), Txt(u["runtime_str"], role="val"))

    if u["load"] is not None:
        b.add_kv(t("cc_power.output_load"), f"{ProgressBar(u['load'], width=18, color_by_threshold=True)} {Txt(str(u['load']) + '%', role='val')}")

    id_items = [
        (t("cc_power.popup_fld_mfr"), u["mfr"]),
        (t("cc_power.popup_fld_model"), u["model"]),
        (t("cc_power.serial_number"), u["serial"]),
        (t("cc_power.nominal_power"), f"{u['nominal_w']} W" if u["nominal_w"] else None),
        (t("cc_power.firmware_driver"), u["firmware"]),
        (t("cc_power.device_type"), u["dev_type"]),
    ]
    id_present = [(lbl, val) for lbl, val in id_items if val]
    if id_present:
        def _build_identity(sub: Box):
            for lbl, val in id_present:
                sub.add_kv(lbl, Txt(val, role="val"))

        b.add_collapsible(
            cc_panel,
            "pwr_ups_identity",
            t("cc_power.device_identity", p0=len(id_present)),
            _build_identity,
        )


def _build_ups_electrical_box(u: dict) -> Box | None:
    if not u["enabled"] or not u["reachable"]:
        return None
    pwr_parts = [f"{u['realpower']} W"] if u["realpower"] else []
    if u["apparent_power"]:
        pwr_parts.append(f"{u['apparent_power']} VA")
    v_parts = [f"IN: {u['vin']}V"] if u["vin"] else []
    if u["vout"]:
        v_parts.append(f"OUT: {u['vout']}V")
    if u["vbat"]:
        v_parts.append(f"BAT: {u['vbat']}V")
    f_parts = [f"IN: {u['freq_in']}Hz"] if u["freq_in"] else []
    if u["freq_out"]:
        f_parts.append(f"OUT: {u['freq_out']}Hz")

    if not (pwr_parts or v_parts or f_parts or u["temp"]):
        return None

    eb = Box(t("cc_power.electrical_telemetry"), icon="\uf0e7", highlighted=False)
    if pwr_parts:
        eb.add_kv(t("cc_power.output_power"), Txt("  |  ".join(pwr_parts), role="val"))
    if v_parts:
        eb.add_kv(t("cc_power.voltages"), Txt("  |  ".join(v_parts), role="val"))
    if f_parts:
        eb.add_kv(t("cc_power.frequency"), Txt("  |  ".join(f_parts), role="val"))
    if u["temp"]:
        eb.add_kv(t("cc_power.popup_fld_temp"), Txt(f"{u['temp']} °C", role="val"))
    return eb


def _build_ups_waybar_box(cc_panel, u: dict) -> Box:
    wb = Box(t("cc_power.waybar_popup_settings"), icon="\uf075", highlighted=False)
    flds = u["popup_fields"]
    pills = " ".join(
        SelectPill(t(f"cc_power.popup_fld_{key}"), f"cc_pwr:wb_fld:{key}", selected=bool(flds.get(key, False)))
        for key, _ in POPUP_FIELD_DEFS
    )
    wb.add_row(f"  {Txt(t('cc_power.visible_rows_in_waybar_popup'), role='schema')}")
    wb.add_row(f"  {pills}")
    low_pills = " ".join(SelectPill(f"{p}%", f"cc_pwr:ups_low:{p}", selected=(u["low_pct"] == p)) for p in (10, 15, 20, 30, 50))
    wb.add_kv(t("cc_power.low_battery_threshold"), low_pills)
    wb.add_kv(t("cc_power.notify_test"), ActionBtn(t("cc_power.send_test_alert"), "cc_pwr:ups_test_notify", tone="neutral"))

    if u["raw"]:
        def _build_raw_diag(sub: Box):
            rows = [[Txt(k, role="schema"), Txt(v, role="val")] for k, v in sorted(u["raw"].items())]
            sub.add_table(headers=[t("cc_power.variable"), t("cc_power.value")], widths=[30, "flex"], rows=rows, aligns=["left", "left"])

        wb.add_collapsible(
            cc_panel,
            "pwr_ups_raw",
            t("cc_power.raw_diagnostics_variables", p0=len(u["raw"])),
            _build_raw_diag,
        )
    return wb


def handle_cc_power_link(cc_panel, uri: str) -> bool:
    if not uri.startswith("cc_pwr:"):
        return False
    act = uri[len("cc_pwr:"):]
    cfg = get_nut_config()

    if act == "refresh":
        invalidate_ups_cache()
        with _CACHE_LOCK:
            _GPU_CACHE["ts"] = 0.0
            _UPOWER_CACHE["ts"] = 0.0
    elif act == "auth_cpu":
        request_privilege_modal(cc_panel, "power_cpu_tune")
    elif act.startswith("ppd:"):
        prof = act.split(":", 1)[1]
        _, avail = _collect_power_profiles()
        if prof in avail:
            ok, _o, err = probe.run_cmd_full(["powerprofilesctl", "set", prof], timeout=2.0)
            cur, _ = _collect_power_profiles()
            ok = ok and (cur == prof)
            _set_pwr_fb(ok, t("cc_power.fb_ppd_set", p0=prof) if ok else t("cc_power.fb_ppd_err", p0=err or prof))
    elif act.startswith("gov:"):
        gov = act.split(":", 1)[1]
        st = _collect_cpu_power()
        if gov in st["avail_gov"]:
            cpu_priv = get_privileged_action("power_cpu_tune")
            if cpu_priv and cpu_priv.check_installed():
                ok, _o, err = probe.run_cmd_full(["sudo", "-n", "/usr/bin/cpupower", "frequency-set", "-g", gov], timeout=2.0)
            elif probe.has_bin("pkexec"):
                ok, _o, err = probe.run_cmd_full(["pkexec", "/usr/bin/cpupower", "frequency-set", "-g", gov], timeout=12.0)
            else:
                request_privilege_modal(cc_panel, "power_cpu_tune")
                ok, err = False, ""
            if ok or err:
                cur_gov = _read_file_str("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor", "")
                ok = ok and (cur_gov == gov)
                _set_pwr_fb(ok, t("cc_power.fb_gov_set", p0=gov) if ok else t("cc_power.fb_gov_err", p0=err or t("cc_power.priv_required")))
    elif act.startswith("epp:"):
        epp = act.split(":", 1)[1]
        st = _collect_cpu_power()
        if epp in st["avail_epp"]:
            ok, err = True, ""
            try:
                for p in glob.glob("/sys/devices/system/cpu/cpu*/cpufreq/energy_performance_preference"):
                    with open(p, "w", encoding="utf-8") as f:
                        f.write(epp)
            except PermissionError:
                ok, _o, err = probe.run_cmd_full(
                    ["pkexec", "bash", "-c", 'for f in /sys/devices/system/cpu/cpu*/cpufreq/energy_performance_preference; do printf "%s" "$1" > "$f"; done', "_", epp],
                    timeout=12.0,
                )
            except Exception as e:
                ok, err = False, str(e)
            cur_epp = _read_file_str("/sys/devices/system/cpu/cpu0/cpufreq/energy_performance_preference", "")
            ok = ok and (cur_epp == epp)
            _set_pwr_fb(ok, t("cc_power.fb_epp_set", p0=epp) if ok else t("cc_power.fb_epp_err", p0=err or t("cc_power.priv_required")))
    elif act.startswith("boost:"):
        val = act.split(":", 1)[1]
        if val in ("0", "1"):
            ok, err = True, ""
            boost_path = "/sys/devices/system/cpu/cpufreq/boost"
            try:
                with open(boost_path, "w", encoding="utf-8") as f:
                    f.write(val)
            except PermissionError:
                cpu_priv = get_privileged_action("power_cpu_tune")
                cmd = (["sudo", "-n"] if (cpu_priv and cpu_priv.check_installed()) else ["pkexec"]) + ["/usr/bin/tee", boost_path]
                try:
                    res = subprocess.run(cmd, input=val, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=12.0)
                    ok = res.returncode == 0
                    err = (res.stdout or "").strip()
                except Exception as e:
                    ok, err = False, str(e)
            except Exception as e:
                ok, err = False, str(e)
            cur_boost = _read_file_str(boost_path, "")
            ok = ok and (cur_boost == val)
            lbl = t("common.on") if val == "1" else t("common.off")
            _set_pwr_fb(ok, t("cc_power.fb_boost_set", p0=lbl) if ok else t("cc_power.fb_boost_err", p0=err or t("cc_power.priv_required")))
    elif act.startswith("bat_thr:"):
        rest = act.split(":", 1)[1]
        if ":" in rest:
            bat_name, thr = rest.split(":", 1)
            if re.fullmatch(r"BAT[0-9]+", bat_name) and thr in ("80", "100"):
                thr_path = f"/sys/class/power_supply/{bat_name}/charge_control_end_threshold"
                ok, err = True, ""
                try:
                    with open(thr_path, "w", encoding="utf-8") as f:
                        f.write(thr)
                except PermissionError:
                    try:
                        res = subprocess.run(["pkexec", "/usr/bin/tee", thr_path], input=thr, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=12.0)
                        ok = res.returncode == 0
                        err = (res.stdout or "").strip()
                    except Exception as e:
                        ok, err = False, str(e)
                except Exception as e:
                    ok, err = False, str(e)
                ok = ok and (_read_file_str(thr_path, "") == thr)
                _set_pwr_fb(ok, t("cc_power.fb_charge_limit_set", p0=bat_name, p1=thr) if ok else t("cc_power.fb_charge_limit_err", p0=err or t("cc_power.priv_required")))
    elif act.startswith("nut_en:"):
        en = act.split(":", 1)[1] == "1"
        save_nut_config(enabled=en)
        probe.run_cmd(["pkill", "-SIGUSR2", "waybar"], timeout=0.6)
        state_lbl = t("common.enabled").lower() if en else t("common.disabled").lower()
        _set_pwr_fb(True, t("cc_power.nut_ups_monitor", p0=state_lbl))
    elif act == "inline_host":
        start_inline_input("cc_pwr_host", shell=cc_panel.shell, trigger_uri=uri, initial_text=cfg["host"], max_len=36, validator=_is_valid_nut_token, on_commit=lambda s: handle_cc_power_prompt(cc_panel, "cc_pwr_host", s))
        return True
    elif act == "inline_port":
        start_inline_input("cc_pwr_port", shell=cc_panel.shell, trigger_uri=uri, initial_text=str(cfg["port"]), max_len=6, validator=lambda s: s.strip().isdigit() and 1 <= int(s.strip()) <= 65535, on_commit=lambda s: handle_cc_power_prompt(cc_panel, "cc_pwr_port", s))
        return True
    elif act == "inline_ups":
        start_inline_input("cc_pwr_ups", shell=cc_panel.shell, trigger_uri=uri, initial_text=cfg["ups_name"], max_len=40, validator=_is_valid_nut_ups_input, on_commit=lambda s: handle_cc_power_prompt(cc_panel, "cc_pwr_ups", s))
        return True
    elif act == "inline_poll":
        start_inline_input("cc_pwr_poll", shell=cc_panel.shell, trigger_uri=uri, initial_text=str(cfg["poll_interval"]), max_len=5, validator=lambda s: s.strip().isdigit() and 1 <= int(s.strip()) <= 3600, on_commit=lambda s: handle_cc_power_prompt(cc_panel, "cc_pwr_poll", s))
        return True
    elif act == "discover":
        _set_pwr_fb(True, t("cc_power.fb_discover_started", p0=cfg["host"], p1=cfg["port"]))
        def _disc_worker():
            ok, found, err = discover_nut_ups()
            msg = t("cc_power.fb_discover_ok", p0=len(found), p1=", ".join(found)) if (ok and found) else (err or t("cc_power.fb_discover_none", p0=cfg["host"], p1=cfg["port"]))
            _set_pwr_fb(ok and bool(found), msg)
            invalidate_ups_cache()
            _schedule_ui_refresh(cc_panel)
        threading.Thread(target=_disc_worker, daemon=True).start()
    elif act.startswith("sel_ups:"):
        name = act.split(":", 1)[1].strip()
        if _is_valid_nut_token(name):
            save_nut_config(ups_name=name)
            _set_pwr_fb(True, t("cc_power.selected_ups", p0=name))
    elif act.startswith("poll:"):
        try:
            sec = int(act.split(":", 1)[1])
            save_nut_config(poll_interval=sec)
            _set_pwr_fb(True, t("cc_power.waybar_ups_polling_interval_set_to", p0=sec))
        except Exception:
            pass
    elif act.startswith("wb_fld:"):
        key = act.split(":", 1)[1]
        if key in DEFAULT_POPUP_FIELDS:
            flds = dict(cfg["popup_fields"])
            flds[key] = not bool(flds.get(key, False))
            STATE.set("power", "nut_popup_fields", flds)
    elif act.startswith("ups_low:"):
        save_nut_config(low_pct=int(act.split(":", 1)[1]))
    elif act == "ups_test_notify":
        if probe.has_bin("notify-send"):
            probe.spawn_bg(["notify-send", t("cc_power.fb_ups_test_title"), t("cc_power.fb_ups_test_body")])
        _set_pwr_fb(True, t("cc_power.sent_ups_test_notification"))
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
            elif probe.has_bin("pkexec"):
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
    if action_id == "cc_pwr_host":
        if not _is_valid_nut_token(val):
            _set_pwr_fb(False, t("cc_power.fb_invalid_host"))
            return True
        save_nut_config(host=val)
        _set_pwr_fb(True, t("cc_power.nut_host_set_to", p0=val))
        return True
    if action_id == "cc_pwr_port":
        if val.isdigit() and 1 <= int(val) <= 65535:
            save_nut_config(port=int(val))
            _set_pwr_fb(True, t("cc_power.nut_port_set_to", p0=val))
        else:
            _set_pwr_fb(False, t("cc_power.invalid_port_must_be_1_65535"))
        return True
    if action_id == "cc_pwr_ups":
        if not _is_valid_nut_ups_input(val):
            _set_pwr_fb(False, t("cc_power.fb_invalid_ups_name"))
            return True
        cfg = get_nut_config()
        name, host, port = _split_nut_target(val, cfg["host"], cfg["port"])
        if "@" in val:
            _, hp = val.split("@", 1)
            if ":" in hp:
                h_p, p_p = hp.rsplit(":", 1)
                save_nut_config(ups_name=name, host=h_p.strip() or cfg["host"], port=int(p_p) if p_p.strip().isdigit() else cfg["port"])
            else:
                save_nut_config(ups_name=name, host=hp.strip() or cfg["host"])
        else:
            save_nut_config(ups_name=name)
        _set_pwr_fb(True, t("cc_power.ups_name_set_to", p0=name))
        return True
    if action_id == "cc_pwr_poll":
        if val.isdigit() and 1 <= int(val) <= 3600:
            save_nut_config(poll_interval=int(val))
            _set_pwr_fb(True, t("cc_power.waybar_ups_polling_set_to_s", p0=val))
        else:
            _set_pwr_fb(False, t("cc_power.invalid_polling_interval_1_3600s"))
        return True
    return False
