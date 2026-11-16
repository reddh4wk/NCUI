#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""NC-UI Control Center — Interface Settings & Boot (`panels/cc_settings.py`) [Single Backend / Dual View]"""

import glob
import os
import re
import shlex
from core import probe
from core.config import (
    CONFIG,
    _update_toml_key_in_section,
    get_cc_panel_style,
    get_hide_unavailable_settings,
    set_cc_panel_style,
    set_hide_unavailable_settings,
)
from core.i18n import get_active_language, set_language
from ui.canvas import Canvas
from ui.kit import ActionBtn, Badge, Box, SelectPill, StateBadge, Txt

_MSG = ""
_ERR = ""
_LIMINE_CANDIDATES = ("/boot/limine.conf", "/efi/limine/limine.conf", "/boot/limine/limine.conf")
_DEFAULT_ENTRIES = ["linux-cachyos", "linux-cachyos-hardened", "Golden Image Recovery", "Windows"]


def _feedback_cards() -> list:
    if not _MSG and not _ERR:
        return []
    b = Box("SETTINGS // ERROR" if _ERR else "SETTINGS // APPLIED", icon="\uf071" if _ERR else "\uf058")
    b.add_row(f"  {Txt(_ERR if _ERR else _MSG, role='warn' if _ERR else 'vitality')}")
    return [b]


def _read_log_tail(n_lines: int = 12) -> tuple[str, list[str]]:
    lp = str(CONFIG.get("general", {}).get("log_file", "/tmp/nc-ui.log"))
    if not os.path.exists(lp):
        return lp, []
    try:
        lines = open(lp, "r", encoding="utf-8", errors="ignore").read().splitlines()
        return lp, lines[-n_lines:]
    except Exception:
        return lp, []


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


# =====================================================================
# INTERFACE TAB — STANDARD & CYBERPUNK
# =====================================================================

def _build_interface_standard(cc_panel, canvas: Canvas, is_it: bool) -> list:
    style = get_cc_panel_style()
    hide_unavail = get_hide_unavailable_settings()
    gen = CONFIG.get("general", {})
    side = str(gen.get("side", "right"))
    term = str(gen.get("terminal", "auto"))
    log_lvl = int(gen.get("log_level", 3))
    lang = get_active_language()
    log_path, log_tail = _read_log_tail(10)
    cards = _feedback_cards()

    b = Box(
        "Stile Pannelli, Comportamento & Diagnostica" if is_it else "Panel Style, Behavior & Diagnostics",
        icon="\uf013",
        actions=[StateBadge(style == "cyberpunk", "CYBERPUNK VIEW", "STANDARD VIEW")],
        elected=True,
    )
    b.add_row(" ")
    st_pills = f"{SelectPill('Standard', 'cc_set:style:standard', selected=(style == 'standard'))} {SelectPill('Cyberpunk', 'cc_set:style:cyberpunk', selected=(style == 'cyberpunk'))}"
    b.add_kv("Control Center Style", Txt(style.upper(), role="vitality"), right_markup=st_pills)

    hd_pills = f"{SelectPill('Hide Unavailable', 'cc_set:hide_unavail:1', selected=hide_unavail)} {SelectPill('Show [MISSING] Badge', 'cc_set:hide_unavail:0', selected=not hide_unavail)}"
    b.add_kv("Missing Tools Filter", StateBadge(not hide_unavail, "SHOW BADGE", "HIDDEN"), right_markup=hd_pills)

    sd_pills = f"{SelectPill('Left', 'cc_set:side:left', selected=(side == 'left'))} {SelectPill('Right', 'cc_set:side:right', selected=(side == 'right'))}"
    b.add_kv("Window Anchor Side", Txt(side, role="focus"), right_markup=sd_pills)

    lg_pills = f"{SelectPill('English', 'cc_set:lang:en', selected=(lang == 'en'))} {SelectPill('Italiano', 'cc_set:lang:it', selected=(lang == 'it'))}"
    b.add_kv("Interface Language", Txt(lang.upper(), role="intent"), right_markup=lg_pills)

    tm_pills = " ".join(SelectPill(t_opt, f"cc_set:term:{t_opt}", selected=(term == t_opt)) for t_opt in ("auto", "ghostty", "kitty", "foot"))
    b.add_kv("Preferred Terminal", Txt(term, role="val"), right_markup=tm_pills)

    lv_pills = " ".join(SelectPill(str(lvl), f"cc_set:loglevel:{lvl}", selected=(log_lvl == lvl)) for lvl in (0, 1, 2, 3, 4))
    b.add_kv("Log Verbosity (0..4)", Txt(f"Level {log_lvl}", role="schema"), right_markup=f"{lv_pills} {ActionBtn('Clear Log', 'cc_set:clear_log', tone='danger_soft')}")

    def _fill_log(bx: Box):
        for ln in log_tail:
            bx.add_row(f"  {Txt(canvas.truncate_plain(ln, 76), role='schema')}")
        if not log_tail:
            bx.add_row(f"  {Txt(f'{log_path} is empty or absent.', role='inactive')}")

    b.add_collapsible(cc_panel, "set_log_tail", f"Runtime Log Tail ({os.path.basename(log_path)})", _fill_log)
    b.add_row(" ")
    cards.append(b)
    return cards


def _build_interface_cyberpunk(cc_panel, canvas: Canvas, is_it: bool) -> list:
    style = get_cc_panel_style()
    hide_unavail = get_hide_unavailable_settings()
    gen = CONFIG.get("general", {})
    side = str(gen.get("side", "right"))
    term = str(gen.get("terminal", "auto"))
    log_lvl = int(gen.get("log_level", 3))
    lang = get_active_language()
    log_path, log_tail = _read_log_tail(10)
    cards = _feedback_cards()

    b = Box("SHELL MATRIX // RENDER STYLE, GATING & TELEMETRY", icon="\uf013", elected=True)
    b.add_row(" ")
    b.add_row(f"  {Txt('[PANEL STYLE]  ──►', role='vitality')} {SelectPill('STANDARD', 'cc_set:style:standard', selected=(style == 'standard'))} {SelectPill('CYBERPUNK', 'cc_set:style:cyberpunk', selected=(style == 'cyberpunk'))}")
    b.add_row(f"  {Txt('[DEP FILTER]   ──►', role='intent')} {SelectPill('HIDE UNINSTALLED', 'cc_set:hide_unavail:1', selected=hide_unavail)} {SelectPill('BADGE [MISSING]', 'cc_set:hide_unavail:0', selected=not hide_unavail)}")
    b.add_row(f"  {Txt('[ANCHOR/LANG]  ──►', role='schema')} {SelectPill('LEFT', 'cc_set:side:left', selected=(side == 'left'))} {SelectPill('RIGHT', 'cc_set:side:right', selected=(side == 'right'))}   {SelectPill('EN', 'cc_set:lang:en', selected=(lang == 'en'))} {SelectPill('IT', 'cc_set:lang:it', selected=(lang == 'it'))}")
    b.add_row(f"  {Txt('[TERMINAL]     ──►', role='schema')} " + " ".join(SelectPill(t_opt.upper(), f"cc_set:term:{t_opt}", selected=(term == t_opt)) for t_opt in ("auto", "ghostty", "kitty", "foot")))
    b.add_row(f"  {Txt('[LOG LEVEL]    ──►', role='schema')} " + " ".join(SelectPill(f"L{lvl}", f"cc_set:loglevel:{lvl}", selected=(log_lvl == lvl)) for lvl in (0, 1, 2, 3, 4)), right_markup=ActionBtn("FLUSH LOG", "cc_set:clear_log", tone="danger_soft"))

    def _fill_log(bx: Box):
        for ln in log_tail:
            bx.add_row(f"  {Txt('├─', role='structure')} {Txt(canvas.truncate_plain(ln, 72), role='schema')}")
        if not log_tail:
            bx.add_row(f"  {Txt(f'{log_path}: clean.', role='inactive')}")

    b.add_collapsible(cc_panel, "set_log_tail", f"JOURNAL ({os.path.basename(log_path)})", _fill_log)
    b.add_row(" ")
    cards.append(b)
    return cards


# =====================================================================
# BOOT MENU TAB (LIMINE + UKI) — STANDARD & CYBERPUNK
# =====================================================================

def _build_boot_standard(cc_panel, canvas: Canvas, is_it: bool) -> list:
    bm = _probe_limine()
    cards = _feedback_cards()
    b = Box(
        "Bootloader Limine & Kernel UKI Entries",
        icon="\uf2db",
        actions=[StateBadge(bm["readable"], "CONFIG READABLE", "ELEVATED / HIDDEN"), ActionBtn("Edit Limine (Term)", "cc_set:limine_edit", tone="select")],
        elected=True,
    )
    b.add_row(" ")
    b.add_kv("Config File", Txt(bm["conf_path"], role="val"))
    to_pills = " ".join(SelectPill(f"{v}s", f"cc_set:limine_to:{v}", selected=(bm["timeout"] == v)) for v in ("0", "3", "5", "10"))
    b.add_kv("Boot Timeout", Txt(f"{bm['timeout']} seconds", role="focus"), right_markup=to_pills, requires=["limine"])
    b.add_kv("UKI & Snapshots", Txt(f"{len(bm['ukis'])} UKI EFI  |  {bm['snaps']} LVM snapshots", role="intent"))
    b.add_section("Default Boot Entry (Limine)")
    for idx, entry in enumerate(bm["entries"], start=1):
        is_def = (str(idx) == bm["default_entry"])
        b.add_row(
            f"  {Txt(f'{idx}.', role='schema')} {Txt(entry, role='vitality' if is_def else 'focus')}",
            right_markup=SelectPill("Default" if is_def else "Set Default", f"cc_set:limine_def:{idx}", selected=is_def),
            requires=["limine"],
        )
    b.add_row(" ")
    cards.append(b)
    return cards


def _build_boot_cyberpunk(cc_panel, canvas: Canvas, is_it: bool) -> list:
    bm = _probe_limine()
    cards = _feedback_cards()
    b = Box("EFI BOOTCHAIN // LIMINE.CONF & DUAL-UKI MATRIX", icon="\uf2db", actions=[ActionBtn("EDIT LIMINE TERM", "cc_set:limine_edit", tone="select")], elected=True)
    b.add_row(" ")
    b.add_row(f"  {Txt('[LIMINE CONF]──►', role='vitality')} {Txt(bm['conf_path'], role='focus')}  {StateBadge(bm['readable'], 'READ OK', 'SUDO GATE')}")
    to_pills = " ".join(SelectPill(f"{v}s", f"cc_set:limine_to:{v}", selected=(bm["timeout"] == v)) for v in ("0", "3", "5", "10"))
    b.add_row(f"  {Txt('[TIMEOUT]    ──►', role='schema')} {to_pills}   {Txt('SNAPS:' + str(bm['snaps']), role='intent')}", requires=["limine"])
    b.add_section("BOOT STATIONS")
    for idx, entry in enumerate(bm["entries"], start=1):
        is_def = (str(idx) == bm["default_entry"])
        b.add_row(
            f"  {Txt('▶━' if is_def else '┣━', role='vitality' if is_def else 'structure')} {Txt(f'[{idx}] {entry}', role='focus')}",
            right_markup=SelectPill("DEFAULT" if is_def else "SET DEF", f"cc_set:limine_def:{idx}", selected=is_def),
            requires=["limine"],
        )
    b.add_row(" ")
    cards.append(b)
    return cards


def build_cc_settings_cards(cc_panel, canvas: Canvas, active_sub: str = "interface") -> list:
    is_it = (get_active_language() == "it")
    cyber = get_cc_panel_style() == "cyberpunk"
    if active_sub == "boot":
        return _build_boot_cyberpunk(cc_panel, canvas, is_it) if cyber else _build_boot_standard(cc_panel, canvas, is_it)
    return _build_interface_cyberpunk(cc_panel, canvas, is_it) if cyber else _build_interface_standard(cc_panel, canvas, is_it)


def handle_cc_settings_link(cc_panel, uri: str) -> bool:
    global _MSG, _ERR
    if not uri.startswith("cc_set:"):
        return False
    _MSG = ""
    _ERR = ""
    parts = uri.split(":", 2)
    cmd = parts[1]
    arg = parts[2] if len(parts) > 2 else ""

    if cmd == "style" and arg in ("standard", "cyberpunk"):
        set_cc_panel_style(arg)
        _MSG = f"Control Center panel style set to {arg.upper()}."
    elif cmd == "hide_unavail" and arg in ("0", "1"):
        val = (arg == "1")
        set_hide_unavailable_settings(val)
        _MSG = f"hide_unavailable_settings = {val}"
    elif cmd == "side" and arg in ("left", "right"):
        _update_toml_key_in_section("general", "side", f'"{arg}"')
        CONFIG.setdefault("general", {})["side"] = arg
        _MSG = f"Control Center anchor side set to {arg}."
    elif cmd == "lang" and arg in ("en", "it"):
        set_language(arg)
        _MSG = f"Interface language set to {arg.upper()}."
    elif cmd == "term" and arg:
        _update_toml_key_in_section("general", "terminal", f'"{arg}"')
        CONFIG.setdefault("general", {})["terminal"] = arg
        _MSG = f"Preferred terminal set to {arg}."
    elif cmd == "loglevel" and arg.isdigit():
        lvl = int(arg)
        _update_toml_key_in_section("general", "log_level", str(lvl))
        CONFIG.setdefault("general", {})["log_level"] = lvl
        _MSG = f"Log level set to {lvl}."
    elif cmd == "clear_log":
        lp = str(CONFIG.get("general", {}).get("log_file", "/tmp/nc-ui.log"))
        try:
            open(lp, "w", encoding="utf-8").write("")
            _MSG = f"Cleared {lp}."
        except Exception as e:
            _ERR = str(e)
    elif cmd == "limine_edit":
        cp = _probe_limine()["conf_path"]
        probe.open_terminal(["bash", "-c", f"sudo ${{EDITOR:-nano}} {shlex.quote(cp)}; echo; read -p 'Press Enter to close...'"])
        _MSG = f"Opened {cp} in terminal editor."
    elif cmd == "limine_to" and arg.isdigit():
        cp = _probe_limine()["conf_path"]
        probe.open_terminal(["bash", "-c", f"sudo sed -i 's/^timeout:.*/timeout: {int(arg)}/' {shlex.quote(cp)} && grep -n '^timeout:' {shlex.quote(cp)}; read -p 'Press Enter to close...'"])
        _MSG = f"Launched Limine timeout update ({arg}s) in terminal."
    elif cmd == "limine_def" and arg.isdigit():
        cp = _probe_limine()["conf_path"]
        probe.open_terminal(["bash", "-c", f"sudo sed -i 's/^default_entry:.*/default_entry: {int(arg)}/' {shlex.quote(cp)} && grep -n '^default_entry:' {shlex.quote(cp)}; read -p 'Press Enter to close...'"])
        _MSG = f"Launched Limine default_entry={arg} update in terminal."
    else:
        return False

    if cc_panel and cc_panel.shell:
        cc_panel.shell.refresh_view()
    return True
