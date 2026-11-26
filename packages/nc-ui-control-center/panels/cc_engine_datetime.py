#!/usr/bin/env python3
"""NC-UI Date, Timezone & NTP Engine (`panels/cc_engine_datetime.py`)."""

import os
import re
import time
from core import probe
from core.completion import catalog_timezones, rank_candidates
from core.config import get_waybar_config_dir
from core.i18n import t
from core.state import STATE
from ui.kit import feedback_cards, set_feedback, start_inline_input

_CLOCK_FMTS = [
    ("cc_datetime.fmt_24h_compact", "{:%H:%M}"),
    ("cc_datetime.fmt_24h_date", "{:%a %d %b  %H:%M}"),
    ("cc_datetime.fmt_iso_full", "{:%Y-%m-%d %H:%M:%S}"),
    ("cc_datetime.fmt_12h_ampm", "{:%I:%M %p}"),
]


def _feedback_cards() -> list:
    return feedback_cards("datetime", "cc_datetime.status_title")


def _probe_timedate() -> dict:
    info = {
        "tz": time.tzname[0] if time.tzname else "UTC",
        "ntp": False,
        "ntp_sync": False,
        "rtc_local": False,
        "ntp_srv": t("cc_datetime.ntp_default"),
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

    wb_cfg = os.path.join(get_waybar_config_dir(), "config.jsonc")
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
    if not isinstance(uri, str) or not uri.startswith("cc_dt:"):
        return False
    parts = uri.split(":", 2)
    cmd = parts[1]
    arg = parts[2] if len(parts) > 2 else ""

    if cmd == "tz_search_open" and not arg:
        if not cc_panel or not cc_panel.shell:
            return False
        cur_q = str(STATE.get("datetime", "tz_query", "") or "")

        def _commit_raw_tz(txt: str) -> None:
            val = str(txt or "").strip()
            STATE.set("datetime", "tz_query", "")
            if not val:
                set_feedback("datetime", False, t("cc_datetime.tz_empty"))
                return
            ranked = rank_candidates(catalog_timezones(), val, limit=1)
            tz = ranked[0][0] if ranked else val
            ok, _, err = probe.run_cmd_full(["timedatectl", "set-timezone", tz], timeout=3.0)
            set_feedback("datetime", ok, t("cc_datetime.tz_set", p0=tz) if ok else t("cc_datetime.tz_err", p0=err or tz))

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
    if cmd == "tz_search_clear" and not arg:
        STATE.set("datetime", "tz_query", "")
    elif cmd == "sync_now" and not arg:
        if not cc_panel:
            return False
        from ui.embedded_tui import start_panel_terminal

        start_panel_terminal(
            cc_panel,
            t("common.term_title", p0="systemd-timesyncd"),
            ["bash", "-c", "sudo systemctl restart systemd-timesyncd && timedatectl status"],
        )
        return True
    elif cmd == "ntp" and arg in ("true", "false"):
        ok, _, err = probe.run_cmd_full(["timedatectl", "set-ntp", arg], timeout=3.0)
        msg = (t("cc_datetime.ntp_on") if arg == "true" else t("cc_datetime.ntp_off")) if ok else t("cc_datetime.ntp_err", p0=err or arg)
        set_feedback("datetime", ok, msg)
    elif cmd == "rtc" and arg in ("0", "1"):
        ok, _, err = probe.run_cmd_full(["timedatectl", "set-local-rtc", arg], timeout=3.0)
        set_feedback("datetime", ok, t("cc_datetime.rtc_set", p0=arg) if ok else t("cc_datetime.rtc_err", p0=err or arg))
    elif cmd == "ntp_term" and not arg:
        ok_ed, ed_info = probe.open_in_editor("/etc/systemd/timesyncd.conf", sudo=True)
        if ok_ed:
            set_feedback("datetime", True, t("cc_datetime.opened_timesyncd_conf_in_editor"))
        elif ed_info == "unset":
            set_feedback("datetime", False, t("common.editor_not_configured"))
        else:
            set_feedback("datetime", False, t("common.editor_not_found", p0=ed_info.split(":", 1)[-1]))
    elif cmd == "set_tz" and arg:
        ok, _, err = probe.run_cmd_full(["timedatectl", "set-timezone", arg], timeout=3.0)
        set_feedback("datetime", ok, t("cc_datetime.tz_set", p0=arg) if ok else t("cc_datetime.tz_err", p0=err or arg))
        STATE.set("datetime", "tz_query", "")
    elif cmd == "lang" and arg:
        ok, _, err = probe.run_cmd_full(["localectl", "set-locale", f"LANG={arg}"], timeout=3.0)
        set_feedback("datetime", ok, t("cc_datetime.lang_set", p0=arg) if ok else t("cc_datetime.locale_err", p0=err or arg))
    elif cmd == "lctime" and arg:
        ok, _, err = probe.run_cmd_full(["localectl", "set-locale", f"LC_TIME={arg}"], timeout=3.0)
        set_feedback("datetime", ok, t("cc_datetime.lctime_set", p0=arg) if ok else t("cc_datetime.locale_err", p0=err or arg))
    elif cmd == "barfmt" and arg.isascii() and arg.isdigit():
        idx = int(arg)
        if not (0 <= idx < len(_CLOCK_FMTS)):
            return False
        new_fmt = _CLOCK_FMTS[idx][1]
        wb_cfg = os.path.join(get_waybar_config_dir(), "config.jsonc")
        if os.path.exists(wb_cfg):
            raw = open(wb_cfg, "r", encoding="utf-8", errors="ignore").read()
            new_raw, n = re.subn(r'("clock"\s*:\s*\{[^}]*?"format"\s*:\s*")([^"]+)(")', rf"\g<1>{new_fmt}\g<3>", raw, count=1, flags=re.DOTALL)
            if n:
                open(wb_cfg, "w", encoding="utf-8").write(new_raw)
                probe.spawn_bg(["pkill", "-SIGUSR2", "waybar"])
                set_feedback("datetime", True, t("cc_datetime.waybar_clock_format_set_to", p0=new_fmt))
            else:
                set_feedback("datetime", False, t("cc_datetime.could_not_locate_clock_format_in_w"))
        else:
            set_feedback("datetime", False, t("cc_datetime.waybar_config_jsonc_not_found"))
    else:
        return False

    if cc_panel and cc_panel.shell:
        cc_panel.shell.refresh_view()
    return True


def handle_cc_datetime_prompt_change(cc_panel, action_id: str, text: str) -> bool:
    return False


def handle_cc_datetime_prompt(cc_panel, action_id: str, text: str) -> bool:
    return False
