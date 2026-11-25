#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""NC-UI Control Center — Interface Settings Engine (`panels/cc_engine_settings.py`)."""

import os
from core.config import (
    CONFIG,
    _update_toml_key_in_section,
    get_hide_unavailable_settings,
    set_hide_unavailable_settings,
    set_hover_tooltip_delay,
    set_hover_tooltips_enabled,
)
from core.i18n import set_language, t
from core.state import STATE
from ui.kit import FeedbackBox

_MSG = ""
_ERR = ""


def backup_all_ui_settings(dest_path: str = "") -> tuple[bool, str]:
    """Skeleton: backup all NC-UI settings & themes (to be implemented)."""
    return True, t("cc_settings.backup_skeleton_msg")


def build_distro_package(dest_path: str = "") -> tuple[bool, str]:
    """Skeleton: build a distro package bundling NC-UI and current settings (to be implemented)."""
    return True, t("cc_settings.distro_pkg_skeleton_msg")


def _feedback_cards() -> list:
    if not _MSG and not _ERR:
        return []
    return [FeedbackBox(t("cc_settings.status_title"), not bool(_ERR), _ERR if _ERR else _MSG)]

def _read_log_window(offset: int = 0, window_size: int = 30) -> tuple[str, list[tuple[int, str]], int, int]:
    lp = str(CONFIG.get("general", {}).get("log_file", "/tmp/nc-ui.log"))
    if not os.path.exists(lp):
        return lp, [], 0, 0
    try:
        lines = open(lp, "r", encoding="utf-8", errors="ignore").read().splitlines()
        total = len(lines)
        max_off = max(0, total - window_size)
        clamped_off = max(0, min(int(offset), max_off))
        end_idx = total - clamped_off
        start_idx = max(0, end_idx - window_size)
        win_lines = [(i + 1, lines[i]) for i in range(start_idx, end_idx)]
        return lp, win_lines, total, clamped_off
    except Exception:
        return lp, [], 0, 0

def handle_cc_settings_link(cc_panel, uri: str) -> bool:
    global _MSG, _ERR
    if uri.startswith("cc_notif:"):
        return handle_cc_notifications_link(cc_panel, uri)
    if not uri.startswith("cc_set:"):
        return False
    _MSG = ""
    _ERR = ""
    parts = uri.split(":", 2)
    cmd = parts[1]
    arg = parts[2] if len(parts) > 2 else ""

    if cmd == "hide_unavail" and arg in ("0", "1"):
        val = (arg == "1")
        set_hide_unavailable_settings(val)
        _MSG = t("cc_settings.hide_unavailable_settings", p0=val)
    elif cmd == "side" and arg in ("left", "right"):
        _update_toml_key_in_section("general", "side", f'"{arg}"')
        CONFIG.setdefault("general", {})["side"] = arg
        _MSG = t("cc_settings.control_center_anchor_side_set_to", p0=arg)
    elif cmd == "lang" and arg in ("en", "it"):
        set_language(arg)
        _MSG = t("cc_settings.interface_language_set_to", p0=arg.upper())
    elif cmd == "log_scroll":
        cur_off = int(STATE.get("settings", "log_scroll_offset", 0) or 0)
        if arg == "up":
            STATE.set("settings", "log_scroll_offset", cur_off + 5)
        elif arg == "down":
            STATE.set("settings", "log_scroll_offset", max(0, cur_off - 5))
        elif arg == "top":
            STATE.set("settings", "log_scroll_offset", 999999)
        elif arg == "bottom":
            STATE.set("settings", "log_scroll_offset", 0)
    elif cmd == "loglevel" and arg.isdigit():
        lvl = int(arg)
        _update_toml_key_in_section("general", "log_level", str(lvl))
        CONFIG.setdefault("general", {})["log_level"] = lvl
        _MSG = t("cc_settings.log_level_set_to", p0=lvl)
    elif cmd == "hover_help" and arg in ("0", "1"):
        val = (arg == "1")
        set_hover_tooltips_enabled(val)
        if not val and cc_panel and cc_panel.shell and hasattr(cc_panel.shell, "dismiss_hover_help"):
            cc_panel.shell.dismiss_hover_help()
        _MSG = t("cc_settings.hover_help_set", p0=t("common.on") if val else t("common.off"))
    elif cmd == "hover_delay":
        try:
            v = set_hover_tooltip_delay(float(arg))
            _MSG = t("cc_settings.hover_delay_set", p0=f"{v:.1f}s")
        except (TypeError, ValueError):
            pass
    elif cmd == "clear_log":
        lp = str(CONFIG.get("general", {}).get("log_file", "/tmp/nc-ui.log"))
        try:
            open(lp, "w", encoding="utf-8").write("")
            STATE.set("settings", "log_scroll_offset", 0)
            _MSG = t("cc_settings.cleared", p0=lp)
        except Exception as e:
            _ERR = str(e)
    elif cmd == "backup_ui":
        ok_b, msg_b = backup_all_ui_settings()
        if ok_b:
            _MSG = msg_b
        else:
            _ERR = msg_b
    elif cmd == "build_distro_pkg":
        ok_p, msg_p = build_distro_package()
        if ok_p:
            _MSG = msg_p
        else:
            _ERR = msg_p
    else:

        return False

    if cc_panel and cc_panel.shell:
        cc_panel.shell.refresh_view()
    return True


def handle_cc_settings_prompt(cc_panel, action_id: str, text: str) -> bool:
    return False
