#!/usr/bin/env python3
import os
import re
import time
from core import probe
from core.config import CONFIG, _update_toml_key_in_section, get_cc_panel_style, get_screen_lock_cmd
from core.i18n import get_active_language
from core.state import STATE
from ui.canvas import Canvas
from ui.kit import ActionBtn, Badge, Box, ProgressBar, SelectPill, StateBadge, Txt


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
    boost_p = "/sys/devices/system/cpu/cpufreq/boost"
    boost = _read_file_str(boost_p, "")
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


def _collect_ups_data() -> dict:
    nut_cfg = CONFIG.get("power", {}).get("nut", {})
    enabled = bool(STATE.get("power", "nut_enabled", nut_cfg.get("enabled", False)))
    ups_name = str(STATE.get("power", "nut_ups_name", nut_cfg.get("ups_name", "ecoflow@10.10.0.20")) or "")
    low_pct = int(STATE.get("power", "ups_low_pct", 20) or 20)
    data = {
        "enabled": enabled,
        "ups_name": ups_name,
        "low_pct": low_pct,
        "reachable": False,
        "status": "OFFLINE",
        "charge": 0,
        "load": 0,
        "power_w": "N/A",
        "runtime_m": 0,
        "vin": "N/A",
        "vout": "N/A",
        "model": ups_name,
    }
    if enabled and ups_name and probe.has_bin("upsc"):
        ok, out = probe.run_cmd(["upsc", ups_name], timeout=1.5)
        if ok and out:
            kv = {}
            for line in out.splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    kv[k.strip()] = v.strip()
            data["reachable"] = True
            data["status"] = kv.get("ups.status", "OL")
            data["charge"] = int(float(kv.get("battery.charge", 0)))
            data["load"] = int(float(kv.get("ups.load", 0)))
            data["power_w"] = kv.get("ups.realpower", kv.get("ups.power", "N/A"))
            data["runtime_m"] = int(float(kv.get("battery.runtime", 0)) // 60)
            data["vin"] = kv.get("input.voltage", "N/A")
            data["vout"] = kv.get("output.voltage", "N/A")
            data["model"] = kv.get("ups.model", kv.get("device.model", ups_name))
    return data


def _collect_idle_state() -> dict:
    idle_min = int(STATE.get("power", "idle_lock_min", 10) or 10)
    dpms_min = int(STATE.get("display", "dpms_timeout", 15) or 15)
    caffeine = False
    ok, _ = probe.run_cmd(["pgrep", "-f", "systemd-inhibit.*idle"], timeout=0.6)
    caffeine = ok
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
        box.add_kv("RTX 3090 Power", Txt(g_txt, role="vitality"))
    box.add_row(" ")
    cards.append(box)
    return cards


def _build_general_cyberpunk(cc_panel, canvas: Canvas, is_it: bool) -> list:
    st = _collect_cpu_power()
    cards = _feedback_cards()
    b = Box("VRM & P-STATE POWER RAIL // CPU/GPU VOLTAGE BUS", icon="\uf0e7", elected=True)
    b.add_row(" ")
    gov_btns = " ".join(SelectPill(g.upper(), f"cc_pwr:gov:{g}", selected=(st["gov"] == g)) for g in st["avail_gov"][:4])
    b.add_row(f"  {Txt('[P-STATE GOV]──►', role='intent')} {gov_btns}")
    if st["boost"] in ("0", "1"):
        b_on = st["boost"] == "1"
        b.add_row(f"  {Txt('[CORE BOOST] ──►', role='schema')} {SelectPill('TURBO ON', 'cc_pwr:boost:1', selected=b_on)} {SelectPill('CLAMP OFF', 'cc_pwr:boost:0', selected=not b_on)}")
    if st["gpu_info"]:
        b.add_row(f"  {Txt('[RTX 3090 VRM]─►', role='vitality')} {Txt(st['gpu_info'], role='focus')}")
    b.add_row(" ")
    cards.append(b)
    return cards


# =====================================================================
# SUBTAB 2: UPS (NUT / EcoFlow)
# =====================================================================

def _build_ups_standard(cc_panel, canvas: Canvas, is_it: bool) -> list:
    u = _collect_ups_data()
    cards = _feedback_cards()

    st_b = StateBadge(u["reachable"], u["status"], "OFFLINE", on_tone="ok" if "OL" in u["status"] else "warn")
    box = Box(
        "Telemetria UPS / NUT Server (upsc)" if is_it else "UPS Telemetry & NUT Client (upsc)",
        icon="\uf240",
        actions=[st_b, ActionBtn("Refresh", "cc_pwr:refresh", tone="neutral")],
        elected=u["reachable"],
    )
    box.add_row(" ")
    box.add_kv("NUT Monitor", f"{SelectPill('ENABLED', 'cc_pwr:nut_en:1', selected=u['enabled'])} {SelectPill('DISABLED', 'cc_pwr:nut_en:0', selected=not u['enabled'])}", requires=["upsc"])
    box.add_kv("UPS Target", Txt(u["ups_name"], role="focus"), right_markup=ActionBtn("Edit IP/Name..", "cc_pwr:prompt_ups", tone="select"))
    if u["reachable"]:
        box.add_kv("Battery Charge", f"{ProgressBar(u['charge'], width=18)} {Txt(str(u['charge']) + '%', role='vitality')}  {Txt('(' + str(u['runtime_m']) + ' min)', role='schema')}")
        box.add_kv("Output Load", f"{ProgressBar(u['load'], width=18)} {Txt(str(u['load']) + '%', role='intent')}  {Txt(str(u['power_w']) + ' W', role='focus')}")
        box.add_kv("AC Voltage IN/OUT", Txt(f"IN: {u['vin']}V  |  OUT: {u['vout']}V", role="val"))
    else:
        box.add_kv("Telemetry", Txt("UPS unreachable or NUT disabled (non-blocking fallback)", role="inactive"))

    def _build_ups_alerts(b: Box):
        b.add_kv("Low Battery Alarm", " ".join(SelectPill(f"{p}%", f"cc_pwr:ups_low:{p}", selected=(u["low_pct"] == p)) for p in (10, 15, 20, 30)))
        b.add_kv("Notify Test", ActionBtn("Send Test Alert (nc_util_notify.sh)", "cc_pwr:ups_test_notify", tone="select"))

    box.add_collapsible(cc_panel, "pwr_ups_adv", "Soglie Allarme, Graceful Shutdown & Notifiche" if is_it else "Alarm Thresholds, Shutdown & Custom Notifications", _build_ups_alerts)
    box.add_row(" ")
    cards.append(box)
    return cards


def _build_ups_cyberpunk(cc_panel, canvas: Canvas, is_it: bool) -> list:
    u = _collect_ups_data()
    cards = _feedback_cards()
    b = Box(
        f"INVERTER BATTERY TELEMETRY // {u['ups_name']}",
        icon="\uf240",
        actions=[StateBadge(u["reachable"], u["status"], "OFFLINE"), ActionBtn("EDIT TARGET", "cc_pwr:prompt_ups", tone="select")],
        elected=u["reachable"],
    )
    b.add_row(" ")
    b.add_row(f"  {Txt('[AC GRID]─►', role='schema')} {Txt(str(u['vin']) + 'V', role='val')} {Txt('──► [ECOFLOW INVERTER] ──►', role='intent')} {Txt(str(u['vout']) + 'V / ' + str(u['power_w']) + 'W', role='focus')}")
    b.add_row(f"  {Txt('[BATTERY]─►', role='vitality')} {ProgressBar(u['charge'], width=22)} {Txt(str(u['charge']) + '% (' + str(u['runtime_m']) + 'm)', role='vitality')}")
    b.add_row(f"  {Txt('[LOAD]   ─►', role='attention')} {ProgressBar(u['load'], width=22)} {Txt(str(u['load']) + '%', role='focus')}  {ActionBtn('TEST NOTIFY', 'cc_pwr:ups_test_notify', tone='neutral')}")
    b.add_row(" ")
    cards.append(b)
    return cards


# =====================================================================
# SUBTAB 3: WAKEUP, IDLE & LOCK
# =====================================================================

def _build_wakeup_standard(cc_panel, canvas: Canvas, is_it: bool) -> list:
    st = _collect_idle_state()
    cards = _feedback_cards()
    box = Box(
        "Standby, Blocco Automatico & Caffeine" if is_it else "Idle Timeout, Auto-Lock & Caffeine Override",
        icon="\uf186",
        actions=[StateBadge(st["caffeine"], "CAFFEINE ON", "IDLE ACTIVE", on_tone="warn")],
        elected=True,
    )
    box.add_row(" ")
    box.add_kv("Idle Daemon", StateBadge(st["idle_daemon"] != "Stopped", st["idle_daemon"].upper(), "STOPPED"))
    box.add_kv("Auto-Lock Timeout", " ".join(SelectPill(f"{m}m" if m else "Off", f"cc_pwr:idle_min:{m}", selected=(st["idle_min"] == m)) for m in (5, 10, 15, 30, 0)))
    box.add_kv("DPMS Screen Off", " ".join(SelectPill(f"{m}m" if m else "Off", f"cc_pwr:dpms_min:{m}", selected=(st["dpms_min"] == m)) for m in (10, 15, 30, 60, 0)))
    box.add_kv("Awake / Caffeine", f"{SelectPill('Keep Awake', 'cc_pwr:caffeine:on', selected=st['caffeine'])} {SelectPill('Allow Idle', 'cc_pwr:caffeine:off', selected=not st['caffeine'])}")
    box.add_kv("Lock Screen Command", Txt(st["lock_cmd"] or "Not set (Waybar Options)", role="focus" if st["lock_cmd"] else "attention"), right_markup=ActionBtn("Lock Now", "cc_pwr:lock_now", tone="apply"))
    box.add_row(" ")
    cards.append(box)
    return cards


def _build_wakeup_cyberpunk(cc_panel, canvas: Canvas, is_it: bool) -> list:
    st = _collect_idle_state()
    cards = _feedback_cards()
    b = Box("SLEEP WATCHDOG TIMER // SWAYIDLE & CAFFEINE INHIBITOR", icon="\uf186", elected=True)
    b.add_row(" ")
    b.add_row(f"  {Txt('[WATCHDOG] ──►', role='intent')} {StateBadge(st['idle_daemon'] != 'Stopped', st['idle_daemon'].upper(), 'STOPPED')}  {SelectPill('CAFFEINE OVERRIDE', 'cc_pwr:caffeine:' + ('off' if st['caffeine'] else 'on'), selected=st['caffeine'])}")
    b.add_row(f"  {Txt('[LOCK TMR] ──►', role='schema')} " + " ".join(SelectPill(f"{m}m" if m else "OFF", f"cc_pwr:idle_min:{m}", selected=(st["idle_min"] == m)) for m in (5, 10, 15, 30, 0)) + f"  {ActionBtn('LOCK NOW', 'cc_pwr:lock_now', tone='apply')}")
    b.add_row(f"  {Txt('[DPMS OFF] ──►', role='schema')} " + " ".join(SelectPill(f"{m}m" if m else "OFF", f"cc_pwr:dpms_min:{m}", selected=(st["dpms_min"] == m)) for m in (10, 15, 30, 60, 0)))
    b.add_row(" ")
    cards.append(b)
    return cards


# =====================================================================
# PUBLIC ENTRY POINTS
# =====================================================================

def build_cc_power_cards(cc_panel, canvas: Canvas, active_sub: str = "general") -> list:
    is_it = (get_active_language() == "it")
    cyber = get_cc_panel_style() == "cyberpunk"
    if active_sub == "ups":
        return _build_ups_cyberpunk(cc_panel, canvas, is_it) if cyber else _build_ups_standard(cc_panel, canvas, is_it)
    if active_sub == "wakeup":
        return _build_wakeup_cyberpunk(cc_panel, canvas, is_it) if cyber else _build_wakeup_standard(cc_panel, canvas, is_it)
    return _build_general_cyberpunk(cc_panel, canvas, is_it) if cyber else _build_general_standard(cc_panel, canvas, is_it)


def handle_cc_power_link(cc_panel, uri: str) -> bool:
    if not uri.startswith("cc_pwr:"):
        return False
    act = uri[len("cc_pwr:"):]

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
        STATE.set("power", "nut_enabled", en)
        CONFIG.setdefault("power", {}).setdefault("nut", {})["enabled"] = en
        _update_toml_key_in_section("power.nut", "enabled", "true" if en else "false")
        _set_pwr_fb(True, f"NUT UPS monitor {'enabled' if en else 'disabled'}")
    elif act == "prompt_ups":
        cur = str(STATE.get("power", "nut_ups_name", CONFIG.get("power", {}).get("nut", {}).get("ups_name", "ecoflow@10.10.0.20")))
        cc_panel.request_prompt("cc_pwr_ups", "NUT UPS name@host (e.g. ecoflow@10.10.0.20):", cur, title="NUT UPS Target", required=True)
        return True
    elif act.startswith("ups_low:"):
        STATE.set("power", "ups_low_pct", int(act.split(":", 1)[1]))
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

    if cc_panel.shell:
        cc_panel.shell.refresh_view()
    return True


def handle_cc_power_prompt(cc_panel, action_id: str, text: str) -> bool:
    val = str(text or "").strip()
    if action_id == "cc_pwr_ups" and val:
        STATE.set("power", "nut_ups_name", val)
        CONFIG.setdefault("power", {}).setdefault("nut", {})["ups_name"] = val
        _update_toml_key_in_section("power.nut", "ups_name", f'"{val}"')
        _set_pwr_fb(True, f"UPS target set to {val}")
        return True
    return False
