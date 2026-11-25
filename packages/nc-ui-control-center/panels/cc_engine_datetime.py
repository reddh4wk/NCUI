#!/usr/bin/env python3
"""NC-UI Date, Timezone & NTP Engine (`panels/cc_engine_datetime.py`)."""

import os
import re
import time
import zoneinfo
from core import probe
from core.completion import catalog_timezones, rank_candidates
from core.i18n import t
from core.state import STATE
from ui.kit import FeedbackBox, start_inline_input

_MSG = ""
_ERR = ""
_ALL_TZS: list[str] | None = None
_NTP_PRESETS = ["0.arch.pool.ntp.org 1.arch.pool.ntp.org", "time.cloudflare.com", "pool.ntp.org"]
_CLOCK_FMTS = [
    ("24h Compact", "{:%H:%M}"),
    ("24h + Date", "{:%a %d %b  %H:%M}"),
    ("ISO Full", "{:%Y-%m-%d %H:%M:%S}"),
    ("12h AM/PM", "{:%I:%M %p}"),
]


def _load_all_timezones() -> list[str]:
    global _ALL_TZS
    if _ALL_TZS is None:
        tzs = [
            z for z in zoneinfo.available_timezones()
            if ("/" in z and not z.startswith(("posix/", "right/", "SystemV/", "Etc/"))) or z == "UTC"
        ]
        _ALL_TZS = sorted(tzs)
    return _ALL_TZS


def _feedback_cards() -> list:
    if not _MSG and not _ERR:
        return []
    return [FeedbackBox(t("cc_datetime.status_title"), not bool(_ERR), _ERR if _ERR else _MSG)]

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

    ok, out, _ = probe.run_cmd_full(["timedatectl", "show"], timeout=1.5)
    if ok and out:
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



def handle_cc_datetime_link(cc_panel, uri: str) -> bool:
    global _MSG, _ERR
    if not uri.startswith("cc_dt:"):
        return False
    _MSG = ""
    _ERR = ""
    parts = uri.split(":", 2)
    cmd = parts[1]
    arg = parts[2] if len(parts) > 2 else ""

    if cmd == "prompt_tz" and cc_panel:
        cc_panel.request_prompt("cc_dt_tz", t("cc_datetime.enter_timezone_e_g_europe_rome"), _probe_timedate()["tz"])
        return True
    if cmd == "tz_search_open" and cc_panel:
        cur_q = str(STATE.get("datetime", "tz_query", "") or "")
        def _commit_raw_tz(txt: str) -> None:
            global _MSG, _ERR
            val = str(txt or "").strip()
            STATE.set("datetime", "tz_query", "")
            if not val:
                return
            ranked = rank_candidates(catalog_timezones(), val, limit=1)
            tz = ranked[0][0] if ranked else val
            ok, _, err = probe.run_cmd_full(["timedatectl", "set-timezone", tz], timeout=3.0)
            _MSG = f"Timezone set to {tz}" if ok else ""
            _ERR = "" if ok else (err or f"Invalid or denied timezone: {tz}")
        start_inline_input(
            entry_id="cc_dt_tz_picker",
            shell=cc_panel.shell,
            trigger_uri=uri,
            initial_text=cur_q,
            max_len=40,
            is_picker=True,
            on_change=lambda txt: STATE.set("datetime", "tz_query", str(txt or "")),
            on_cancel=lambda: STATE.set("datetime", "tz_query", ""),
            on_commit=_commit_raw_tz,
        )
        return True
    if cmd == "tz_search_clear":
        STATE.set("datetime", "tz_query", "")
    elif cmd == "sync_now":
        probe.open_terminal(["bash", "-c", "sudo systemctl restart systemd-timesyncd && timedatectl status; echo; read -p 'Press Enter to close...'"])
        _MSG = t("cc_datetime.triggered_systemd_timesyncd_resync")
    elif cmd == "ntp" and arg in ("true", "false"):
        ok, _, err = probe.run_cmd_full(["timedatectl", "set-ntp", arg], timeout=3.0)
        _MSG = f"timedatectl set-ntp {arg}" if ok else ""
        _ERR = "" if ok else (err or "timedatectl set-ntp failed (try sudo in terminal)")
    elif cmd == "rtc" and arg in ("0", "1"):
        ok, _, err = probe.run_cmd_full(["timedatectl", "set-local-rtc", arg], timeout=3.0)
        _MSG = f"Set LocalRTC={arg}" if ok else ""
        _ERR = "" if ok else (err or "timedatectl set-local-rtc failed")
    elif cmd == "ntp_term":
        probe.open_terminal(["bash", "-c", "${EDITOR:-nano} /etc/systemd/timesyncd.conf"])
        _MSG = t("cc_datetime.opened_timesyncd_conf_in_editor")
    elif cmd == "set_tz" and arg:
        ok, _, err = probe.run_cmd_full(["timedatectl", "set-timezone", arg], timeout=3.0)
        _MSG = f"Timezone set to {arg}" if ok else ""
        _ERR = "" if ok else (err or f"Failed to set timezone {arg}")
        STATE.set("datetime", "tz_query", "")
    elif cmd == "lang" and arg:
        ok, _, err = probe.run_cmd_full(["localectl", "set-locale", f"LANG={arg}"], timeout=3.0)
        _MSG = f"Set LANG={arg}" if ok else ""
        _ERR = "" if ok else (err or "localectl set-locale failed")
    elif cmd == "lctime" and arg:
        ok, _, err = probe.run_cmd_full(["localectl", "set-locale", f"LC_TIME={arg}"], timeout=3.0)
        _MSG = f"Set LC_TIME={arg}" if ok else ""
        _ERR = "" if ok else (err or "localectl set-locale failed")
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
                    _MSG = t("cc_datetime.waybar_clock_format_set_to", p0=new_fmt)
                else:
                    _ERR = t("cc_datetime.could_not_locate_clock_format_in_w")
            else:
                _ERR = t("cc_datetime.waybar_config_jsonc_not_found")
    else:
        return False

    if cc_panel and cc_panel.shell:
        cc_panel.shell.refresh_view()
    return True


def handle_cc_datetime_prompt_change(cc_panel, action_id: str, text: str) -> bool:
    if action_id == "cc_dt_tz_search":
        STATE.set("datetime", "tz_query", str(text or ""))
        return True
    return False


def handle_cc_datetime_prompt(cc_panel, action_id: str, text: str) -> bool:
    global _MSG, _ERR
    if action_id == "cc_dt_tz_search":
        val = text.strip()
        if val:
            ql = val.lower()
            all_tzs = _load_all_timezones()
            exact = next((z for z in all_tzs if z.lower() == ql), None)
            if exact:
                tz = exact
            else:
                matches = [z for z in all_tzs if ql in z.lower()]
                matches.sort(key=lambda z: (0 if z.split("/")[-1].lower().startswith(ql) else 1, len(z), z))
                tz = matches[0] if matches else val
            ok, _, err = probe.run_cmd_full(["timedatectl", "set-timezone", tz], timeout=3.0)
            _MSG = f"Timezone set to {tz}" if ok else ""
            _ERR = "" if ok else (err or f"Invalid or denied timezone: {tz}")
        STATE.set("datetime", "tz_query", "")
        if cc_panel and cc_panel.shell:
            cc_panel.shell.refresh_view()
        return True
    if action_id != "cc_dt_tz" or not text.strip():
        return False
    tz = text.strip()
    ok, _, err = probe.run_cmd_full(["timedatectl", "set-timezone", tz], timeout=3.0)
    _MSG = f"Timezone set to {tz}" if ok else ""
    _ERR = "" if ok else (err or f"Invalid or denied timezone: {tz}")
    if cc_panel and cc_panel.shell:
        cc_panel.shell.refresh_view()
    return True
