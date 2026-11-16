#!/usr/bin/env python3
"""NC-UI Control Center — Power Profiles, Generic NUT Server / UPS & Idle (`panels/cc_power.py`)."""

import json
import os
import re
import time
from core import probe
from core.config import (
    CONFIG,
    ROOT_DIR,
    _unique_existing_paths,
    _update_toml_key_in_section,
    get_screen_lock_cmd,
)
from core.i18n import get_active_language, t
from core.state import STATE
from ui.canvas import Canvas
from ui.kit import ActionBtn, Badge, Box, ProgressBar, SelectPill, StateBadge, Txt

POPUP_FIELD_DEFS = [
    ("status",  "Status",       "Stato Operativo",  True),
    ("charge",  "Charge",       "Carica Batteria",  True),
    ("runtime", "Runtime",      "Autonomia",        True),
    ("load",    "Load",         "Carico Output",    True),
    ("power",   "Power (W/VA)", "Potenza (W/VA)",   True),
    ("voltage", "Voltage (V)",  "Tensioni (V)",     True),
    ("model",   "Model",        "Modello",          True),
    ("mfr",     "Manufacturer", "Produttore",       False),
    ("serial",  "Serial",       "Seriale",          False),
    ("temp",    "Temperature",  "Temperatura",      False),
]

DEFAULT_POPUP_FIELDS = {k: default for k, _, _, default in POPUP_FIELD_DEFS}

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
        return "0 secondi" if is_it else "0 seconds"

    units = (
        (31536000, ("anno", "anni") if is_it else ("year", "years")),
        (2592000,  ("mese", "mesi") if is_it else ("month", "months")),
        (86400,    ("giorno", "giorni") if is_it else ("day", "days")),
        (3600,     ("ora", "ore") if is_it else ("hour", "hours")),
        (60,       ("minuto", "minuti") if is_it else ("minute", "minutes")),
        (1,        ("secondo", "secondi") if is_it else ("second", "seconds")),
    )
    parts = []
    for div, (singular, plural) in units:
        val, rem = divmod(rem, div)
        if val > 0:
            parts.append(f"{val} {singular if val == 1 else plural}")
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
    if "enabled" in kwargs:
        val = bool(kwargs["enabled"])
        STATE.set("power", "nut_enabled", val)
        _save_nut_toml("enabled", "true" if val else "false", val)
    if "host" in kwargs:
        val = str(kwargs["host"] or "").strip() or "127.0.0.1"
        STATE.set("power", "nut_host", val)
        _save_nut_toml("host", f'"{val}"', val)
    if "port" in kwargs:
        val = max(1, min(65535, int(kwargs["port"])))
        STATE.set("power", "nut_port", val)
        _save_nut_toml("port", str(val), val)
    if "ups_name" in kwargs:
        val = str(kwargs["ups_name"] or "").strip()
        STATE.set("power", "nut_ups_name", val)
        _save_nut_toml("ups_name", f'"{val}"', val)
    if "poll_interval" in kwargs:
        val = max(1, min(3600, int(kwargs["poll_interval"])))
        STATE.set("power", "nut_poll_interval", val)
        _save_nut_toml("poll_interval", str(val), val)
        _sync_waybar_ups_interval(val)
    if "low_pct" in kwargs:
        val = max(1, min(99, int(kwargs["low_pct"])))
        STATE.set("power", "ups_low_pct", val)
        _save_nut_toml("low_pct", str(val), val)
    return get_nut_config()


def _sync_waybar_ups_interval(sec: int) -> None:
    """Update `custom/ups` interval across Waybar configs and reload Waybar."""
    paths = _unique_existing_paths(
        os.path.expanduser("~/.config/waybar/config.jsonc"),
        os.path.join(ROOT_DIR, "config", "waybar", "config.jsonc"),
        os.path.expanduser("~/Projects/ncui/config/waybar/config.jsonc"),
    )
    for rp in paths:
        try:
            with open(rp, "r", encoding="utf-8") as f:
                raw = f.read()
            cleaned = re.sub(r'("(?:\\.|[^"\\])*")|//[^\n]*|/\*[\s\S]*?\*/', lambda m: m.group(1) or "", raw)
            cleaned = re.sub(r",(\s*[}\]])", r"\1", cleaned)
            data = json.loads(cleaned)
            if isinstance(data, dict) and isinstance(data.get("custom/ups"), dict):
                data["custom/ups"]["interval"] = int(sec)
                with open(rp, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=4)
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


def _collect_ups_data() -> dict:
    cfg = get_nut_config()
    is_it = (get_active_language() == "it")
    target = _format_nut_target(cfg)

    data = {
        **cfg,
        "target": target,
        "reachable": False,
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

    if not cfg["enabled"] or not target or not probe.has_bin("upsc"):
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
        "runtime_str": format_runtime_top2(runtime_sec, is_it=is_it) if runtime_sec is not None else None,
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
    b = Box("Power & UPS Status", icon="\uf00c" if fb["ok"] else "\uf071")
    b.add_row(" ")
    b.add_row(f"  {Txt(' ' + fb['msg'] + ' ', role='vitality' if fb['ok'] else 'fault', bg=True)}")
    b.add_row(" ")
    return [b]


def _read_file_str(path: str, default: str = "") -> str:
    try:
        return open(path, "r", encoding="utf-8").read().strip()
    except Exception:
        return default


def _collect_cpu_power() -> dict:
    base = "/sys/devices/system/cpu/cpu0/cpufreq"
    gov = _read_file_str(f"{base}/scaling_governor", "unknown")
    avail_gov = _read_file_str(f"{base}/scaling_available_governors", "performance schedutil powersave").split()
    epp = _read_file_str(f"{base}/energy_performance_preference", "N/A")
    avail_epp = _read_file_str(f"{base}/energy_performance_available_preferences", "").split()
    boost = _read_file_str("/sys/devices/system/cpu/cpufreq/boost", "")
    gpu_info = ""
    if probe.has_bin("nvidia-smi"):
        ok, out = probe.run_cmd(["nvidia-smi", "--query-gpu=temperature.gpu,power.draw,power.limit", "--format=csv,noheader,nounits"], timeout=1.2)
        if ok and out:
            gpu_info = out.splitlines()[0].strip()
    return {
        "gov": gov,
        "avail_gov": avail_gov,
        "epp": epp,
        "avail_epp": avail_epp,
        "boost": boost,
        "gpu_info": gpu_info,
    }


def _collect_idle_state() -> dict:
    idle_min = int(STATE.get("power", "idle_lock_min", 10) or 10)
    dpms_min = int(STATE.get("display", "dpms_timeout", 15) or 15)
    caffeine, _ = probe.run_cmd(["pgrep", "-f", "systemd-inhibit.*idle"], timeout=0.6)
    idle_daemon = "swayidle" if probe.run_cmd(["pgrep", "-x", "swayidle"], timeout=0.5)[0] else (
        "hypridle" if probe.run_cmd(["pgrep", "-x", "hypridle"], timeout=0.5)[0] else "Stopped"
    )
    return {
        "idle_min": idle_min,
        "dpms_min": dpms_min,
        "caffeine": caffeine,
        "idle_daemon": idle_daemon,
        "lock_cmd": get_screen_lock_cmd(),
    }


# =====================================================================
# SUBTAB 1: GENERAL POWER PROFILES
# =====================================================================

def _build_general_standard(cc_panel, canvas: Canvas, is_it: bool) -> list:
    st = _collect_cpu_power()
    cards = _feedback_cards()

    box = Box(
        "Profili Energetici CPU & Telemetria GPU" if is_it else "CPU Power Profiles & GPU Telemetry",
        icon="\uf0e7",
        actions=[Badge(st["gov"].upper(), style="accent")],
        elected=True,
    )
    box.add_row(" ")
    gov_btns = " ".join(SelectPill(g, f"cc_pwr:gov:{g}", selected=(st["gov"] == g)) for g in st["avail_gov"][:5])
    box.add_kv("CPU Governor", gov_btns, requires=["cpupower"])
    if st["avail_epp"]:
        epp_btns = " ".join(SelectPill(e.replace("_", " "), f"cc_pwr:epp:{e}", selected=(st["epp"] == e)) for e in st["avail_epp"][:4])
        box.add_kv("Energy Perf (EPP)", epp_btns)
    else:
        box.add_kv("Energy Perf (EPP)", Txt(st["epp"], role="schema"))
    if st["boost"] in ("0", "1"):
        b_on = st["boost"] == "1"
        box.add_kv("Core Boost (CPB)", f"{SelectPill('BOOST ON', 'cc_pwr:boost:1', selected=b_on)} {SelectPill('ECO OFF', 'cc_pwr:boost:0', selected=not b_on)}")
    if st["gpu_info"]:
        parts = [p.strip() for p in st["gpu_info"].split(",")]
        g_txt = f"{parts[0]}C  |  Draw: {parts[1]}W / {parts[2]}W" if len(parts) >= 3 else st["gpu_info"]
        box.add_kv("GPU Power", Txt(g_txt, role="vitality"))
    box.add_row(" ")
    cards.append(box)
    return cards



# =====================================================================
# SUBTAB 2: NUT SERVER / UPS (Generic, Dual View)
# =====================================================================

def _ups_status_badge(u: dict) -> str:
    if not u["enabled"]:
        return StateBadge(False, "ON", "DISABLED")
    if not u["reachable"]:
        return Badge(u["status_text"].upper(), style="crit")
    tone_map = {"online": "ok", "on-battery": "warn", "low-battery": "crit", "offline": "crit"}
    return Badge(u["status_text"].upper(), style=tone_map.get(u["css_state"], "info"))


def _build_ups_connection_rows(b: Box, u: dict, is_it: bool) -> None:
    en_pills = f"{SelectPill('ENABLED', 'cc_pwr:nut_en:1', selected=u['enabled'])} {SelectPill('DISABLED', 'cc_pwr:nut_en:0', selected=not u['enabled'])}"
    b.add_kv("NUT Monitor", en_pills, requires=["upsc"])

    b.add_kv(
        "Server Host / IP" if not is_it else "Host / IP Server",
        Txt(u["host"], role="focus"),
        right_markup=ActionBtn("Edit.." if not is_it else "Modifica..", "cc_pwr:prompt_host", tone="select"),
    )
    b.add_kv(
        "Server Port" if not is_it else "Porta Server",
        Txt(str(u["port"]), role="val"),
        right_markup=ActionBtn("Edit.." if not is_it else "Modifica..", "cc_pwr:prompt_port", tone="select"),
    )

    ups_lbl = u["ups_name"] or ("(non impostato)" if is_it else "(not set)")
    ups_acts = f"{ActionBtn('Auto-Discover', 'cc_pwr:discover', tone='apply')} {ActionBtn('Edit..', 'cc_pwr:prompt_ups', tone='select')}"
    b.add_kv("UPS Name" if not is_it else "Nome UPS", Txt(ups_lbl, role="focus" if u["ups_name"] else "attention"), right_markup=ups_acts)

    if u["discovered"]:
        disc_pills = " ".join(
            SelectPill(name, f"cc_pwr:sel_ups:{name}", selected=(name == u["ups_name"]))
            for name in u["discovered"][:8]
        )
        b.add_kv("UPS Rilevati" if is_it else "Discovered UPS", disc_pills)

    poll_pills = " ".join(
        SelectPill(f"{s}s", f"cc_pwr:poll:{s}", selected=(u["poll_interval"] == s))
        for s in (2, 5, 10, 15, 30)
    ) + f" {ActionBtn('Custom..', 'cc_pwr:prompt_poll', tone='neutral')}"
    b.add_kv("Waybar Polling", poll_pills)


def _append_ups_adaptive_sections(b: Box, cc_panel, u: dict, is_it: bool) -> None:
    if not u["enabled"]:
        b.add_kv("Telemetry", Txt(t("ups.disabled_hint", "NUT UPS monitor is disabled."), role="inactive"))
    elif not u["reachable"]:
        msg = t("ups.unreachable_hint", "NUT server or UPS unreachable.")
        if u["error"]:
            msg += f" ({u['error'][:48]})"
        b.add_kv("Telemetry", Txt(msg, role="attention"))
    else:
        st_role = "vitality" if u["css_state"] == "online" else ("attention" if u["css_state"] == "on-battery" else "fault")
        b.add_kv("Stato Operativo" if is_it else "Operational Status", Txt(f"{u['status_text']} [{u['status_raw']}]", role=st_role))

        if u["charge"] is not None:
            rt_part = f"  {Txt('(' + u['runtime_str'] + ')', role='schema')}" if u["runtime_str"] else ""
            b.add_kv("Carica Batteria" if is_it else "Battery Charge", f"{ProgressBar(u['charge'], width=18, color_by_threshold=False)} {Txt(str(u['charge']) + '%', role='vitality')}{rt_part}")
        elif u["runtime_str"]:
            b.add_kv("Autonomia" if is_it else "Runtime", Txt(u["runtime_str"], role="vitality"))

        if u["load"] is not None:
            b.add_kv("Carico Output" if is_it else "Output Load", f"{ProgressBar(u['load'], width=18, color_by_threshold=True)} {Txt(str(u['load']) + '%', role='intent')}")

        pwr_parts = []
        if u["realpower"]:
            pwr_parts.append(f"{u['realpower']} W")
        if u["apparent_power"]:
            pwr_parts.append(f"{u['apparent_power']} VA")
        if pwr_parts:
            b.add_kv("Potenza Output" if is_it else "Output Power", Txt("  |  ".join(pwr_parts), role="focus"))

        v_parts = []
        if u["vin"]:
            v_parts.append(f"IN: {u['vin']}V")
        if u["vout"]:
            v_parts.append(f"OUT: {u['vout']}V")
        if u["vbat"]:
            v_parts.append(f"BAT: {u['vbat']}V")
        if v_parts:
            b.add_kv("Tensioni" if is_it else "Voltages", Txt("  |  ".join(v_parts), role="val"))

        f_parts = []
        if u["freq_in"]:
            f_parts.append(f"IN: {u['freq_in']}Hz")
        if u["freq_out"]:
            f_parts.append(f"OUT: {u['freq_out']}Hz")
        if f_parts:
            b.add_kv("Frequenza" if is_it else "Frequency", Txt("  |  ".join(f_parts), role="schema"))

        if u["temp"]:
            b.add_kv("Temperatura" if is_it else "Temperature", Txt(f"{u['temp']} °C", role="val"))

        id_items = [
            ("Produttore" if is_it else "Manufacturer", u["mfr"]),
            ("Modello" if is_it else "Model", u["model"]),
            ("Seriale" if is_it else "Serial Number", u["serial"]),
            ("Potenza Nominale" if is_it else "Nominal Power", f"{u['nominal_w']} W" if u["nominal_w"] else None),
            ("Firmware / Driver", u["firmware"]),
            ("Tipo Dispositivo" if is_it else "Device Type", u["dev_type"]),
        ]
        id_present = [(lbl, val) for lbl, val in id_items if val]
        if id_present:
            def _build_identity(sub: Box):
                for lbl, val in id_present:
                    sub.add_kv(lbl, Txt(val, role="focus"))

            b.add_collapsible(
                cc_panel,
                "pwr_ups_identity",
                "Identita Dispositivo" if is_it else "Device Identity",
                _build_identity,
            )

    def _build_wb_settings(sub: Box):
        flds = u["popup_fields"]
        pills = " ".join(
            SelectPill(lbl_it if is_it else lbl_en, f"cc_pwr:wb_fld:{key}", selected=bool(flds.get(key, False)))
            for key, lbl_en, lbl_it, _ in POPUP_FIELD_DEFS
        )
        sub.add_row(f"  {Txt('Righe visibili nel popup Waybar:' if is_it else 'Visible rows in Waybar popup:', role='schema')}")
        sub.add_row(f"  {pills}")
        low_pills = " ".join(SelectPill(f"{p}%", f"cc_pwr:ups_low:{p}", selected=(u["low_pct"] == p)) for p in (10, 15, 20, 30, 50))
        sub.add_kv("Soglia Low-Battery" if is_it else "Low-Battery Threshold", low_pills)
        sub.add_kv("Test Notifica" if is_it else "Notify Test", ActionBtn("Send Test Alert", "cc_pwr:ups_test_notify", tone="select"))

    b.add_collapsible(
        cc_panel,
        "pwr_ups_wb_cfg",
        "Impostazioni Popup Waybar" if is_it else "Waybar Popup Settings",
        _build_wb_settings,
    )

    if u["raw"]:
        def _build_raw_diag(sub: Box):
            rows = [[Txt(k, role="schema"), Txt(v, role="val")] for k, v in sorted(u["raw"].items())]
            sub.add_table(headers=["VARIABLE", "VALUE"], widths=[30, "flex"], rows=rows, aligns=["left", "left"])

        b.add_collapsible(
            cc_panel,
            "pwr_ups_raw",
            f"Raw Diagnostics ({len(u['raw'])} variables)",
            _build_raw_diag,
        )


def _build_ups_standard(cc_panel, canvas: Canvas, is_it: bool) -> list:
    u = _collect_ups_data()
    cards = _feedback_cards()

    conn_box = Box(
        "NUT Server & Connessione UPS (upsc)" if is_it else "NUT Server & UPS Connection (upsc)",
        icon="\uf1e6",
        actions=[_ups_status_badge(u), ActionBtn("Refresh", "cc_pwr:refresh", tone="neutral")],
        elected=u["enabled"],
    )
    conn_box.add_row(" ")
    _build_ups_connection_rows(conn_box, u, is_it)
    conn_box.add_row(" ")
    cards.append(conn_box)

    tele_title = f"Telemetria UPS ({u['target']})" if (is_it and u["target"]) else (f"UPS Telemetry ({u['target']})" if u["target"] else "UPS Telemetry")
    tele_box = Box(tele_title, icon="\uf240", elected=u["reachable"])
    tele_box.add_row(" ")
    _append_ups_adaptive_sections(tele_box, cc_panel, u, is_it)
    tele_box.add_row(" ")
    cards.append(tele_box)
    return cards


def build_cc_power_cards(cc_panel, canvas: Canvas, active_sub: str = "general") -> list:
    is_it = (get_active_language() == "it")
    if active_sub == "ups":
        return _build_ups_standard(cc_panel, canvas, is_it)
    if active_sub == "wakeup":
        return _build_wakeup_standard(cc_panel, canvas, is_it)
    return _build_general_standard(cc_panel, canvas, is_it)


def handle_cc_power_link(cc_panel, uri: str) -> bool:
    if not uri.startswith("cc_pwr:"):
        return False
    act = uri[len("cc_pwr:"):]
    cfg = get_nut_config()

    if act == "refresh":
        pass
    elif act.startswith("gov:"):
        gov = act.split(":", 1)[1]
        ok, _o, err = probe.run_cmd_full(["cpupower", "frequency-set", "-g", gov], timeout=2.0)
        _set_pwr_fb(ok, f"Governor set to {gov}" if ok else (err or "Requires root/cpupower"))
    elif act.startswith("epp:"):
        epp = act.split(":", 1)[1]
        ok, _o, err = probe.run_cmd_full(["bash", "-c", f"for f in /sys/devices/system/cpu/cpu*/cpufreq/energy_performance_preference; do echo {epp} > \"$f\"; done"], timeout=2.0)
        _set_pwr_fb(ok, f"EPP set to {epp}" if ok else (err or "Permission denied"))
    elif act.startswith("boost:"):
        val = act.split(":", 1)[1]
        ok, _o, err = probe.run_cmd_full(["bash", "-c", f"echo {val} > /sys/devices/system/cpu/cpufreq/boost"], timeout=1.5)
        _set_pwr_fb(ok, f"CPU Boost -> {'ON' if val == '1' else 'OFF'}" if ok else (err or "Requires root privilege"))
    elif act.startswith("nut_en:"):
        en = act.split(":", 1)[1] == "1"
        save_nut_config(enabled=en)
        probe.run_cmd(["pkill", "-SIGUSR2", "waybar"], timeout=0.6)
        _set_pwr_fb(True, f"NUT UPS monitor {'enabled' if en else 'disabled'}")
    elif act == "prompt_host":
        cc_panel.request_prompt("cc_pwr_host", "NUT Server Host / IP:", cfg["host"], title="NUT Server Host", required=True)
        return True
    elif act == "prompt_port":
        cc_panel.request_prompt("cc_pwr_port", "NUT Server Port (1-65535):", str(cfg["port"]), title="NUT Server Port", required=True)
        return True
    elif act == "prompt_ups":
        cc_panel.request_prompt("cc_pwr_ups", "UPS Name (or name@host:port):", cfg["ups_name"], title="NUT UPS Name", required=True)
        return True
    elif act == "prompt_poll":
        cc_panel.request_prompt("cc_pwr_poll", "Waybar polling interval (seconds):", str(cfg["poll_interval"]), title="Waybar Polling Interval", required=True)
        return True
    elif act == "discover":
        ok, found, err = discover_nut_ups()
        msg = f"Discovered UPS ({len(found)}): {', '.join(found)}" if (ok and found) else (err or f"No UPS found on {cfg['host']}:{cfg['port']}")
        _set_pwr_fb(ok and bool(found), msg)
    elif act.startswith("sel_ups:"):
        name = act.split(":", 1)[1].strip()
        if name:
            save_nut_config(ups_name=name)
            _set_pwr_fb(True, f"Selected UPS: {name}")
    elif act.startswith("poll:"):
        try:
            sec = int(act.split(":", 1)[1])
            save_nut_config(poll_interval=sec)
            _set_pwr_fb(True, f"Waybar UPS polling interval set to {sec}s")
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
        probe.spawn_bg(["bash", "-c", "notify-send 'NC-UI UPS Alert' 'Test UPS battery notification'"])
        _set_pwr_fb(True, "Sent UPS test notification")
    elif act.startswith("idle_min:"):
        STATE.set("power", "idle_lock_min", int(act.split(":", 1)[1]))
    elif act.startswith("dpms_min:"):
        STATE.set("display", "dpms_timeout", int(act.split(":", 1)[1]))
    elif act.startswith("caffeine:"):
        on = act.split(":", 1)[1] == "on"
        probe.run_cmd(["pkill", "-f", "systemd-inhibit.*idle"], timeout=1.0)
        if on:
            probe.spawn_bg(["systemd-inhibit", "--what=idle", "--who=NC-UI", "--why=Caffeine Mode", "sleep", "86400"])
        _set_pwr_fb(True, f"Caffeine {'enabled' if on else 'disabled'}")
    elif act == "lock_now":
        cmd = get_screen_lock_cmd()
        if cmd:
            probe.spawn_bg(["bash", "-c", cmd])
        else:
            _set_pwr_fb(False, "No screen lock command configured")

    if cc_panel and cc_panel.shell:
        cc_panel.shell.refresh_view()
    return True


def handle_cc_power_prompt(cc_panel, action_id: str, text: str) -> bool:
    val = str(text or "").strip()
    if not val:
        return False
    if action_id == "cc_pwr_host":
        save_nut_config(host=val)
        _set_pwr_fb(True, f"NUT host set to {val}")
        return True
    if action_id == "cc_pwr_port":
        if val.isdigit() and 1 <= int(val) <= 65535:
            save_nut_config(port=int(val))
            _set_pwr_fb(True, f"NUT port set to {val}")
        else:
            _set_pwr_fb(False, "Invalid port (must be 1..65535)")
        return True
    if action_id == "cc_pwr_ups":
        cfg = get_nut_config()
        name, host, port = _split_nut_target(val, cfg["host"], cfg["port"])
        # If user typed explicit host/port inside ups@host[:port], update them too
        if "@" in val:
            _, hp = val.split("@", 1)
            if ":" in hp:
                h_p, p_p = hp.rsplit(":", 1)
                save_nut_config(ups_name=name, host=h_p.strip() or cfg["host"], port=int(p_p) if p_p.strip().isdigit() else cfg["port"])
            else:
                save_nut_config(ups_name=name, host=hp.strip() or cfg["host"])
        else:
            save_nut_config(ups_name=name)
        _set_pwr_fb(True, f"UPS name set to {name}")
        return True
    if action_id == "cc_pwr_poll":
        if val.isdigit() and 1 <= int(val) <= 3600:
            save_nut_config(poll_interval=int(val))
            _set_pwr_fb(True, f"Waybar UPS polling set to {val}s")
        else:
            _set_pwr_fb(False, "Invalid polling interval (1..3600s)")
        return True
    return False
