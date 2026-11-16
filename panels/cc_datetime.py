# -*- coding: utf-8 -*-
"""NC-UI Control Center — Date, Time & Region (`panels/cc_datetime.py`) [Single Backend / Dual View]"""

import os
import re
import shlex
import time
from core import probe
from core.config import get_cc_panel_style
from core.i18n import get_active_language
from ui.canvas import Canvas
from ui.kit import ActionBtn, Badge, Box, SelectPill, StateBadge, Txt

_MSG = ""
_ERR = ""
_TZ_PRESETS = ["Europe/Rome", "UTC", "Europe/London", "America/New_York", "America/Los_Angeles", "Asia/Tokyo"]
_NTP_PRESETS = ["0.arch.pool.ntp.org 1.arch.pool.ntp.org", "time.cloudflare.com", "pool.ntp.org"]
_CLOCK_FMTS = [
    ("24h Compact", "{:%H:%M}"),
    ("24h + Date", "{:%a %d %b  %H:%M}"),
    ("ISO Full", "{:%Y-%m-%d %H:%M:%S}"),
    ("12h AM/PM", "{:%I:%M %p}"),
]


def _feedback_cards() -> list:
    if not _MSG and not _ERR:
        return []
    b = Box("DATETIME // ERROR" if _ERR else "DATETIME // STATUS", icon="\uf071" if _ERR else "\uf058")
    b.add_row(f"  {Txt(_ERR if _ERR else _MSG, role='warn' if _ERR else 'vitality')}")
    return [b]


def _probe_timedate() -> dict:
    info = {
        "tz": time.tzname[0] if time.tzname else "UTC",
        "ntp": False,
        "ntp_sync": False,
        "rtc_local": False,
        "ntp_srv": "default (arch pool)",
        "lang": os.environ.get("LANG", "en_US.UTF-8"),
        "lc_time": os.environ.get("LC_TIME", "C.UTF-8"),
        "bar_fmt": "{:%H:%M}",
    }
    if os.path.islink("/etc/localtime"):
        target = os.path.realpath("/etc/localtime")
        if "/zoneinfo/" in target:
            info["tz"] = target.split("/zoneinfo/", 1)[1]

    rc, out, _ = probe.run_cmd_full(["timedatectl", "show"], timeout=1.5)
    if rc == 0 and out:
        for ln in out.splitlines():
            if "=" in ln:
                k, v = ln.split("=", 1)
                if k == "Timezone" and v:
                    info["tz"] = v
                elif k == "NTP":
                    info["ntp"] = (v == "yes")
                elif k == "NTPSynchronized":
                    info["ntp_sync"] = (v == "yes")
                elif k == "LocalRTC":
                    info["rtc_local"] = (v == "yes")

    if os.path.exists("/etc/systemd/timesyncd.conf"):
        try:
            for ln in open("/etc/systemd/timesyncd.conf", "r", encoding="utf-8", errors="ignore"):
                s = ln.strip()
                if s.startswith("NTP="):
                    info["ntp_srv"] = s.split("=", 1)[1].strip() or info["ntp_srv"]
        except Exception:
            pass

    if os.path.exists("/etc/locale.conf"):
        try:
            for ln in open("/etc/locale.conf", "r", encoding="utf-8", errors="ignore"):
                s = ln.strip()
                if s.startswith("LANG="):
                    info["lang"] = s.split("=", 1)[1].strip().strip('"')
                elif s.startswith("LC_TIME="):
                    info["lc_time"] = s.split("=", 1)[1].strip().strip('"')
        except Exception:
            pass

    wb_cfg = os.path.expanduser("~/.config/waybar/config.jsonc")
    if os.path.exists(wb_cfg):
        try:
            raw = open(wb_cfg, "r", encoding="utf-8", errors="ignore").read()
            m = re.search(r'"clock"\s*:\s*\{[^}]*?"format"\s*:\s*"([^"]+)"', raw, re.DOTALL)
            if m:
                info["bar_fmt"] = m.group(1)
        except Exception:
            pass
    return info


def _build_time_standard(cc_panel, canvas: Canvas, is_it: bool) -> list:
    d = _probe_timedate()
    cards = _feedback_cards()
    now_s = time.strftime("%Y-%m-%d  %H:%M:%S %Z")
    b = Box(
        "Sincronizzazione Ora & NTP (systemd-timesyncd)" if is_it else "Time Synchronization & NTP (systemd-timesyncd)",
        icon="\uf017",
        actions=[StateBadge(d["ntp_sync"], "NTP SYNCED", "UNSYNCED" if d["ntp"] else "MANUAL"), ActionBtn("Sync Now", "cc_dt:sync_now", tone="apply")],
        elected=True,
    )
    b.add_row(" ")
    b.add_kv("System Clock", Txt(now_s, role="vitality"))
    b.add_kv("Automatic NTP", StateBadge(d["ntp"], "ENABLED", "DISABLED"), right_markup=f"{SelectPill('ON', 'cc_dt:ntp:true', selected=d['ntp'])} {SelectPill('OFF', 'cc_dt:ntp:false', selected=not d['ntp'])}", requires=["timedatectl"])
    b.add_kv("Hardware RTC", StateBadge(not d["rtc_local"], "UTC (Recommended)", "LOCAL-RTC"), right_markup=f"{SelectPill('UTC', 'cc_dt:rtc:0', selected=not d['rtc_local'])} {SelectPill('Local', 'cc_dt:rtc:1', selected=d['rtc_local'])}")
    b.add_kv("NTP Pool Server", Txt(d["ntp_srv"], role="intent"), right_markup=ActionBtn("Configure (Term)", "cc_dt:ntp_term", tone="select"))
    b.add_row(" ")
    cards.append(b)
    return cards


def _build_time_cyberpunk(cc_panel, canvas: Canvas, is_it: bool) -> list:
    d = _probe_timedate()
    cards = _feedback_cards()
    now_s = time.strftime("%Y-%m-%d %H:%M:%S %Z")
    b = Box("CHRONO DAEMON // TIMESYNCD, NTP & RTC MATRIX", icon="\uf017", actions=[ActionBtn("SYNC NOW", "cc_dt:sync_now", tone="apply")], elected=True)
    b.add_row(" ")
    b.add_row(f"  {Txt('[SYS CLOCK]──►', role='vitality')} {Txt(now_s, role='focus')}  {StateBadge(d['ntp_sync'], 'LOCKED', 'DRIFT')}")
    b.add_row(f"  {Txt('[NTP POOL] ──►', role='intent')} {SelectPill('AUTO', 'cc_dt:ntp:true', selected=d['ntp'])} {SelectPill('MANUAL', 'cc_dt:ntp:false', selected=not d['ntp'])}  {Txt(d['ntp_srv'], role='schema')}", requires=["timedatectl"])
    b.add_row(f"  {Txt('[HW RTC]   ──►', role='schema')} {SelectPill('UTC', 'cc_dt:rtc:0', selected=not d['rtc_local'])} {SelectPill('LOCAL', 'cc_dt:rtc:1', selected=d['rtc_local'])}  {ActionBtn('EDIT TIMESYNCD', 'cc_dt:ntp_term', tone='select')}")
    b.add_row(" ")
    cards.append(b)
    return cards


def _build_timezone_standard(cc_panel, canvas: Canvas, is_it: bool) -> list:
    d = _probe_timedate()
    cards = _feedback_cards()
    b = Box(
        "Fuso Orario Attivo" if is_it else "Active Timezone & Geographic Presets",
        icon="\uf0ac",
        actions=[ActionBtn("Search / Set TZ...", "cc_dt:prompt_tz", tone="select")],
        elected=True,
    )
    b.add_row(" ")
    b.add_kv("Current Timezone", Txt(d["tz"], role="vitality"))
    pills = " ".join(SelectPill(tz, f"cc_dt:set_tz:{tz}", selected=(d["tz"] == tz)) for tz in _TZ_PRESETS[:4])
    b.add_kv("Quick Zones", pills, requires=["timedatectl"])
    pills2 = " ".join(SelectPill(tz, f"cc_dt:set_tz:{tz}", selected=(d["tz"] == tz)) for tz in _TZ_PRESETS[4:])
    b.add_kv("More Zones", pills2, requires=["timedatectl"])
    b.add_row(" ")
    cards.append(b)
    return cards


def _build_timezone_cyberpunk(cc_panel, canvas: Canvas, is_it: bool) -> list:
    d = _probe_timedate()
    cards = _feedback_cards()
    b = Box("GEOTEMPORAL GRID // ZONEINFO SELECTOR", icon="\uf0ac", actions=[ActionBtn("CUSTOM TZ..", "cc_dt:prompt_tz", tone="select")], elected=True)
    b.add_row(" ")
    b.add_row(f"  {Txt('[ACTIVE ZONE]─►', role='vitality')} {Txt(d['tz'], role='focus')}")
    b.add_row(f"  {Txt('[PRESETS 1]  ─►', role='schema')} " + " ".join(SelectPill(tz, f"cc_dt:set_tz:{tz}", selected=(d["tz"] == tz)) for tz in _TZ_PRESETS[:3]), requires=["timedatectl"])
    b.add_row(f"  {Txt('[PRESETS 2]  ─►', role='schema')} " + " ".join(SelectPill(tz, f"cc_dt:set_tz:{tz}", selected=(d["tz"] == tz)) for tz in _TZ_PRESETS[3:]), requires=["timedatectl"])
    b.add_row(" ")
    cards.append(b)
    return cards


def _build_region_standard(cc_panel, canvas: Canvas, is_it: bool) -> list:
    d = _probe_timedate()
    cards = _feedback_cards()
    b = Box("Impostazioni Regionali & Formato Barra" if is_it else "Region Settings & Bar Clock Format", icon="\uf1ab", elected=True)
    b.add_row(" ")
    b.add_kv("LANG (System)", Txt(d["lang"], role="focus"), right_markup=f"{SelectPill('en_US.UTF-8', 'cc_dt:lang:en_US.UTF-8', selected=('en_US' in d['lang']))} {SelectPill('it_IT.UTF-8', 'cc_dt:lang:it_IT.UTF-8', selected=('it_IT' in d['lang']))}", requires=["localectl"])
    b.add_kv("LC_TIME (24h/12h)", Txt(d["lc_time"], role="val"), right_markup=f"{SelectPill('24h (it_IT)', 'cc_dt:lctime:it_IT.UTF-8', selected=('it_IT' in d['lc_time']))} {SelectPill('POSIX/C', 'cc_dt:lctime:C.UTF-8', selected=(d['lc_time'].startswith('C')))}", requires=["localectl"])
    b.add_kv("Waybar Clock Format", Txt(d["bar_fmt"], role="intent"))
    fmt_pills = " ".join(SelectPill(lbl, f"cc_dt:barfmt:{i}", selected=(d["bar_fmt"] == fmt)) for i, (lbl, fmt) in enumerate(_CLOCK_FMTS))
    b.add_kv("Bar Format Presets", fmt_pills)
    b.add_row(" ")
    cards.append(b)
    return cards


def _build_region_cyberpunk(cc_panel, canvas: Canvas, is_it: bool) -> list:
    d = _probe_timedate()
    cards = _feedback_cards()
    b = Box("LOCALE & WAYBAR CHRONO FORMATTER", icon="\uf1ab", elected=True)
    b.add_row(" ")
    b.add_row(f"  {Txt('[LANG]    ──►', role='focus')} {Txt(d['lang'], role='val')}  {SelectPill('en_US', 'cc_dt:lang:en_US.UTF-8', selected=('en_US' in d['lang']))} {SelectPill('it_IT', 'cc_dt:lang:it_IT.UTF-8', selected=('it_IT' in d['lang']))}", requires=["localectl"])
    b.add_row(f"  {Txt('[LC_TIME] ──►', role='schema')} {Txt(d['lc_time'], role='val')}  {SelectPill('24H', 'cc_dt:lctime:it_IT.UTF-8', selected=('it_IT' in d['lc_time']))} {SelectPill('C.UTF8', 'cc_dt:lctime:C.UTF-8', selected=(d['lc_time'].startswith('C')))}", requires=["localectl"])
    b.add_row(f"  {Txt('[WAYBAR]  ──►', role='intent')} {Txt(d['bar_fmt'], role='vitality')}")
    b.add_row(f"  {Txt('[FORMAT]  ──►', role='schema')} " + " ".join(SelectPill(lbl, f"cc_dt:barfmt:{i}", selected=(d["bar_fmt"] == fmt)) for i, (lbl, fmt) in enumerate(_CLOCK_FMTS)))
    b.add_row(" ")
    cards.append(b)
    return cards


def build_cc_datetime_cards(cc_panel, canvas: Canvas, active_sub: str = "time") -> list:
    is_it = (get_active_language() == "it")
    cyber = get_cc_panel_style() == "cyberpunk"
    if active_sub == "timezone":
        return _build_timezone_cyberpunk(cc_panel, canvas, is_it) if cyber else _build_timezone_standard(cc_panel, canvas, is_it)
    if active_sub == "region":
        return _build_region_cyberpunk(cc_panel, canvas, is_it) if cyber else _build_region_standard(cc_panel, canvas, is_it)
    return _build_time_cyberpunk(cc_panel, canvas, is_it) if cyber else _build_time_standard(cc_panel, canvas, is_it)


def handle_cc_datetime_link(cc_panel, uri: str) -> bool:
    global _MSG, _ERR
    if not uri.startswith("cc_dt:"):
        return False
    _MSG = ""
    _ERR = ""
    parts = uri.split(":", 2)
    cmd = parts[1]
    arg = parts[2] if len(parts) > 2 else ""

    if cmd == "prompt_tz" and cc_panel and cc_panel.shell:
        cc_panel.shell.open_prompt("cc_dt_tz", "Enter timezone (e.g. Europe/Rome):", _probe_timedate()["tz"])
        return True
    if cmd == "sync_now":
        probe.open_terminal(["bash", "-c", "sudo systemctl restart systemd-timesyncd && timedatectl status; echo; read -p 'Press Enter to close...'"])
        _MSG = "Triggered systemd-timesyncd resync in terminal."
    elif cmd == "ntp" and arg in ("true", "false"):
        rc, _, err = probe.run_cmd_full(["timedatectl", "set-ntp", arg], timeout=3.0)
        _MSG = f"timedatectl set-ntp {arg}" if rc == 0 else ""
        _ERR = "" if rc == 0 else (err or f"timedatectl set-ntp failed (try sudo in terminal)")
    elif cmd == "rtc" and arg in ("0", "1"):
        rc, _, err = probe.run_cmd_full(["timedatectl", "set-local-rtc", arg], timeout=3.0)
        _MSG = f"Set LocalRTC={arg}" if rc == 0 else ""
        _ERR = "" if rc == 0 else (err or "timedatectl set-local-rtc failed")
    elif cmd == "ntp_term":
        probe.open_terminal(["bash", "-c", "${EDITOR:-nano} /etc/systemd/timesyncd.conf"])
        _MSG = "Opened timesyncd.conf in editor."
    elif cmd == "set_tz" and arg:
        rc, _, err = probe.run_cmd_full(["timedatectl", "set-timezone", arg], timeout=3.0)
        _MSG = f"Timezone set to {arg}" if rc == 0 else ""
        _ERR = "" if rc == 0 else (err or f"Failed to set timezone {arg}")
    elif cmd == "lang" and arg:
        rc, _, err = probe.run_cmd_full(["localectl", "set-locale", f"LANG={arg}"], timeout=3.0)
        _MSG = f"Set LANG={arg}" if rc == 0 else ""
        _ERR = "" if rc == 0 else (err or "localectl set-locale failed")
    elif cmd == "lctime" and arg:
        rc, _, err = probe.run_cmd_full(["localectl", "set-locale", f"LC_TIME={arg}"], timeout=3.0)
        _MSG = f"Set LC_TIME={arg}" if rc == 0 else ""
        _ERR = "" if rc == 0 else (err or "localectl set-locale failed")
    elif cmd == "barfmt" and arg.isdigit():
        idx = int(arg)
        if 0 <= idx < len(_CLOCK_FMTS):
            new_fmt = _CLOCK_FMTS[idx][1]
            wb_cfg = os.path.expanduser("~/.config/waybar/config.jsonc")
            if os.path.exists(wb_cfg):
                raw = open(wb_cfg, "r", encoding="utf-8", errors="ignore").read()
                new_raw, n = re.subn(r'("clock"\s*:\s*\{[^}]*?"format"\s*:\s*")([^"]+)(")', rf"\g<1>{new_fmt}\g<3>", raw, count=1, flags=re.DOTALL)
                if n:
                    open(wb_cfg, "w", encoding="utf-8").write(new_raw)
                    probe.spawn_bg(["pkill", "-SIGUSR2", "waybar"])
                    _MSG = f"Waybar clock format set to {new_fmt}"
                else:
                    _ERR = "Could not locate clock.format in waybar config.jsonc"
            else:
                _ERR = "waybar config.jsonc not found"
    else:
        return False

    if cc_panel and cc_panel.shell:
        cc_panel.shell.refresh_view()
    return True


def handle_cc_datetime_prompt(cc_panel, action_id: str, text: str) -> bool:
    global _MSG, _ERR
    if action_id != "cc_dt_tz" or not text.strip():
        return False
    tz = text.strip()
    rc, _, err = probe.run_cmd_full(["timedatectl", "set-timezone", tz], timeout=3.0)
    _MSG = f"Timezone set to {tz}" if rc == 0 else ""
    _ERR = "" if rc == 0 else (err or f"Invalid or denied timezone: {tz}")
    if cc_panel and cc_panel.shell:
        cc_panel.shell.refresh_view()
    return True
