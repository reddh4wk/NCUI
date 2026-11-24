#!/usr/bin/env python3
import atexit
import base64
import colorsys
import copy
import ctypes
import fcntl
import hashlib
import json
import os
import pty
import re
import select
import shlex
import shutil
import signal
import socket
import struct
import subprocess
import sys
import termios
import threading
import time
import unicodedata
import urllib.request
from urllib.parse import parse_qs, urlparse
import zipfile
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib
from core import probe
from core.asciifier import (
    ASCIIFY_DEFAULTS,
    CHAR_SETS as ASCII_CHAR_SETS,
    COLOR_MODES as ASCII_COLOR_MODES,
    clear_ascii_cache,
    get_ascii_cache_stats,
    get_cached_ascii_path,
    image_cache_key,
    is_pillow_available,
    normalize_cfg as normalize_asciify_cfg,
    render_ascii_folder,
    render_ascii_image,
)
from core.config import (
    BOX_STYLES,
    CONFIG_DIR,
    DEFAULT_THEME,
    ROOT_DIR,
    THEME,
    THEME_PATH,
    _deep_merge,
    _load_toml,
    _parse_rgba_components,
    _unique_existing_paths,
    _update_toml_key_in_section,
    apply_waybar_palette,
    apply_waybar_transparency,
    derive_axis_companions,
    get_layout_margins,
    get_surface_bg_hex,
    get_ui_transparency_pct,
    get_waybar_sync_color,
    get_waybar_sync_opacity,
    get_window_opacity,
    sanitize_theme_dict,
    set_box_opacity_pct,
    set_layout_margins,
    set_row_gap,
    set_surface_bg,
    set_sys_notif_opacity_pct,
    set_toast_opacity_pct,
    set_ui_transparency_pct,
    set_waybar_sync_color,
    set_waybar_sync_opacity,
    sync_waybar_colors_from_theme,
)
from core.i18n import get_active_language, t
from core.state import STATE
from panels.cc_engine_display import get_niri_outputs_data
from panels.cc_engine_waybar import handle_cc_waybar_link, handle_cc_waybar_prompt
from panels.wp_display import resolve_script_path, run_desktop_cli
from ui.canvas import Canvas
from ui.kit import (
    PALETTE,
    ActionBtn,
    Badge,
    Box,
    Divider,
    FeedbackBox,
    ProgressBar,
    SearchBox,
    SelectPill,
    Slider,
    Sparkline,
    StateBadge,
    Txt,
    cancel_inline_entry,
    get_semantic_palette,
    set_inline_entry_error,
    start_inline_entry,
    start_inline_input,
)

NC_DESKTOP_BASE = os.path.expanduser("~/.local/nc-desktop")
WALL_DIR = os.path.join(NC_DESKTOP_BASE, "wallpapers")
PROF_DIR = os.path.join(NC_DESKTOP_BASE, "profiles")
THEMES_DIR = os.path.join(CONFIG_DIR, "themes")
CURSORS_STORE_DIR = os.path.join(CONFIG_DIR, "cursors")
USER_ICONS_DIR = os.path.expanduser("~/.local/share/icons")
SYS_ICONS_DIRS = (USER_ICONS_DIR, os.path.expanduser("~/.icons"), "/usr/share/icons")
NIRI_CONFIG_FILE = os.path.expanduser("~/.config/niri/config.kdl")

# MEMENTO GOLDEN STANDARD (§2 — Canale 1):
# Solo opzioni morfologiche ortogonali e prive di collisioni con altri archetipi interattivi:
# - button_brackets è bloccato a ["[", "]"] dal motore (mai esposto come preset modificabile).
# - Vietati "▸/·" o "✓/×" su pill_dots, "●/○" su slider_fill, "-/+" su collapsible_icons,
#   "[..]" su tab_caps, "( )" / "< >" su slider_brackets e "│" su kv_separator.
_TITLE_BRK_PRESETS = {
    "sq": ("[ ", " ]", "[ Title ]"),
    "col": (":: ", " ::", ":: Title ::"),
    "chev": ("\u2039 ", " \u203a", "\u2039 Title \u203a"),
    "guil": ("\u00ab ", " \u00bb", "\u00ab Title \u00bb"),
}

_SLIDER_GLYPH_PRESETS = {
    "hash": ("#", "-", "# / -"),
    "blk": ("\u2588", "\u2591", "\u2588 / \u2591"),
    "bar": ("\u2501", "\u2500", "\u2501 / \u2500"),
    "eq": ("=", "\u00b7", "= / \u00b7"),
}

_SLIDER_BRK_PRESETS = {
    "sq": ("[", "]", "[ ]"),
    "blk": ("\u2590", "\u258c", "\u2590 \u258c"),
}

_PILL_DOT_PRESETS = {
    "circ": ("\u25cf", "\u25cb", "\u25cf / \u25cb"),
    "diam": ("\u25c6", "\u25c7", "\u25c6 / \u25c7"),
    "sq": ("\u25a0", "\u25a1", "\u25a0 / \u25a1"),
}

_TAB_CAP_PRESETS = {
    "edge": ("\u258f", "\u2595", "\u258f..\u2595"),
    "blk": ("\u2590", "\u258c", "\u2590..\u258c"),
    "chev": ("\u2039", "\u203a", "\u2039..\u203a"),
}

_COLLAPSE_PRESETS = {
    "big": ("\u25bc", "\u25b6", "\u25bc / \u25b6"),
    "chev": ("\uf078", "\uf054", "\uf078 / \uf054"),
    "box": ("\u229f", "\u229e", "\u229f / \u229e"),
}

_TABLE_RULE_PRESETS = ("\u2500", "\u2501", "\u2550", "\u2504")
_KV_SEP_PRESETS = (":", "\u203a", "\u2237")

_SEARCH_BOX_PRESETS = {
    "def": ("\uf002 >", "\u2502", "\uf002 > \u2502"),
    "line": ("\uf002 >", "_", "\uf002 > _"),
    "sl": ("/ >", "\u2502", "/ > \u2502"),
    "arr": ("\u203a", "\u2502", "\u203a \u2502"),
}

# =====================================================================
# 1-CLICK ARCHITECTURAL THEME PRESETS (Golden Standard §1-§5)
# Ogni preset è un'architettura completa (superficie, opacità a doppio strato,
# cornici, glifi ortogonali e tutti i 7 assi semantici con sotto-toni calibrati).
# =====================================================================
THEME_PRESETS = {
    # 1. NIGHTCITY — Il Gioiello / Tema Capostipite di NC-UI
    "nightcity": {
        "label": "Nightcity",
        "group": "cyber",
        "box_style": "rounded",
        "title_brackets": ["[ ", " ]"],
        "button_brackets": ["[", "]"],
        "slider_brackets": ["[", "]"],
        "slider_fill": "#",
        "slider_empty": "-",
        "pill_dots": ["\u25cf", "\u25cb"],
        "tab_caps": ["\u258f", "\u2595"],
        "collapsible_icons": ["\u25bc", "\u25b6"],
        "table_rule": "\u2500",
        "kv_separator": ":",
        "search_prefix": "\uf002 >",
        "search_cursor": "\u2502",
        "colors": {
            "surface_bg": "#080a0f",
            "window_opacity": 0.86,
            "box_opacity": 0.72,
            "structure": "#3e465c",
            "structure_bg": "#141824",
            "inactive": "#596278",
            "schema": "#8b9bb8",
            "focus": "#ffffff",
            "focus_val": "#dce6f7",
            "intent": "#00f0ff",
            "intent_soft": "#3ec8d6",
            "intent_bg": "#002b36",
            "vitality": "#00f57a",
            "vitality_soft": "#3ebd7c",
            "vitality_bg": "#002915",
            "attention": "#ffe619",
            "attention_soft": "#c7b438",
            "attention_bg": "#332b00",
            "interrupt": "#ff2a9d",
            "interrupt_rest": "#c45b8c",
            "interrupt_bg": "#36081e",
            "fault": "#ff2442",
            "fault_soft": "#c9495b",
            "fault_bg": "#38060e",
        },
    },
    # 2. ARASAKA OPS — Corporate Tactical Zaibatsu HUD
    "arasaka": {
        "label": "Arasaka Ops",
        "group": "cyber",
        "box_style": "sharp",
        "title_brackets": [":: ", " ::"],
        "button_brackets": ["[", "]"],
        "slider_brackets": ["[", "]"],
        "slider_fill": "\u2501",
        "slider_empty": "\u2500",
        "pill_dots": ["\u25c6", "\u25c7"],
        "tab_caps": ["\u2590", "\u258c"],
        "collapsible_icons": ["\u25bc", "\u25b6"],
        "table_rule": "\u2501",
        "kv_separator": ":",
        "search_prefix": "/ >",
        "search_cursor": "\u2502",
        "colors": {
            "surface_bg": "#0b0a0e",
            "window_opacity": 0.88,
            "box_opacity": 0.78,
            "structure": "#47424f",
            "structure_bg": "#17141d",
            "inactive": "#665f73",
            "schema": "#9a94ab",
            "focus": "#f7f5fa",
            "focus_val": "#e0dce8",
            "intent": "#ffb800",
            "intent_soft": "#c99418",
            "intent_bg": "#2e2000",
            "vitality": "#2ee68a",
            "vitality_soft": "#2bb36e",
            "vitality_bg": "#072918",
            "attention": "#ff7b1c",
            "attention_soft": "#c76218",
            "attention_bg": "#331700",
            "interrupt": "#ff3377",
            "interrupt_rest": "#c75078",
            "interrupt_bg": "#36091a",
            "fault": "#ff1e38",
            "fault_soft": "#c73849",
            "fault_bg": "#3b050c",
        },
    },
    # 3. GHOST NET — Deep-Trench Ultraviolet Netrunner
    "ghost_net": {
        "label": "Ghost Net",
        "group": "cyber",
        "box_style": "heavy",
        "title_brackets": ["\u00ab ", " \u00bb"],
        "button_brackets": ["[", "]"],
        "slider_brackets": ["\u2590", "\u258c"],
        "slider_fill": "\u2588",
        "slider_empty": "\u2591",
        "pill_dots": ["\u25cf", "\u25cb"],
        "tab_caps": ["\u258f", "\u2595"],
        "collapsible_icons": ["\u25bc", "\u25b6"],
        "table_rule": "\u2501",
        "kv_separator": "\u2237",
        "search_prefix": "\uf002 >",
        "search_cursor": "\u2502",
        "colors": {
            "surface_bg": "#070d17",
            "window_opacity": 0.86,
            "box_opacity": 0.74,
            "structure": "#2d4263",
            "structure_bg": "#0f1a2c",
            "inactive": "#4a6285",
            "schema": "#7fa0c9",
            "focus": "#eef5ff",
            "focus_val": "#cfe0fa",
            "intent": "#b57aff",
            "intent_soft": "#8d60cc",
            "intent_bg": "#22123d",
            "vitality": "#00e699",
            "vitality_soft": "#2eb386",
            "vitality_bg": "#002b1d",
            "attention": "#ffd93d",
            "attention_soft": "#c7ab36",
            "attention_bg": "#302805",
            "interrupt": "#ff4da6",
            "interrupt_rest": "#c46296",
            "interrupt_bg": "#360d24",
            "fault": "#ff3855",
            "fault_soft": "#c9495c",
            "fault_bg": "#380812",
        },
    },
    # 4. VT-AMBER 84 — DEC Engineering Cathode Terminal
    "amber_deck": {
        "label": "VT-Amber 84",
        "group": "retro",
        "box_style": "sharp",
        "title_brackets": ["[ ", " ]"],
        "button_brackets": ["[", "]"],
        "slider_brackets": ["[", "]"],
        "slider_fill": "=",
        "slider_empty": "\u00b7",
        "pill_dots": ["\u25a0", "\u25a1"],
        "tab_caps": ["\u2039", "\u203a"],
        "collapsible_icons": ["\u229f", "\u229e"],
        "table_rule": "\u2500",
        "kv_separator": ":",
        "search_prefix": "/ >",
        "search_cursor": "\u2502",
        "colors": {
            "surface_bg": "#120b04",
            "window_opacity": 0.90,
            "box_opacity": 0.80,
            "structure": "#593e18",
            "structure_bg": "#1f1407",
            "inactive": "#785626",
            "schema": "#ba8c49",
            "focus": "#fff0cc",
            "focus_val": "#ffd98a",
            "intent": "#ffb700",
            "intent_soft": "#c79008",
            "intent_bg": "#362200",
            "vitality": "#8ce63c",
            "vitality_soft": "#6fb332",
            "vitality_bg": "#1c2e08",
            "attention": "#ff7714",
            "attention_soft": "#c75e10",
            "attention_bg": "#361600",
            "interrupt": "#ff5e4d",
            "interrupt_rest": "#bf5649",
            "interrupt_bg": "#360e0b",
            "fault": "#ff2929",
            "fault_soft": "#c43131",
            "fault_bg": "#380707",
        },
    },
    # 6. ARCTIC STUDIO — Low-Glare Ice Workstation
    "arctic_studio": {
        "label": "Arctic Studio",
        "group": "retro",
        "box_style": "rounded",
        "title_brackets": ["\u2039 ", " \u203a"],
        "button_brackets": ["[", "]"],
        "slider_brackets": ["[", "]"],
        "slider_fill": "\u2501",
        "slider_empty": "\u2500",
        "pill_dots": ["\u25cf", "\u25cb"],
        "tab_caps": ["\u258f", "\u2595"],
        "collapsible_icons": ["\u25bc", "\u25b6"],
        "table_rule": "\u2500",
        "kv_separator": ":",
        "search_prefix": "\uf002 >",
        "search_cursor": "\u2502",
        "colors": {
            "surface_bg": "#141923",
            "window_opacity": 0.88,
            "box_opacity": 0.75,
            "structure": "#3e4859",
            "structure_bg": "#1f2633",
            "inactive": "#5b677d",
            "schema": "#90a0ba",
            "focus": "#eceff4",
            "focus_val": "#d8dee9",
            "intent": "#7dcfff",
            "intent_soft": "#62a8d4",
            "intent_bg": "#152e40",
            "vitality": "#7ee09b",
            "vitality_soft": "#62b37b",
            "vitality_bg": "#142e1f",
            "attention": "#f0c674",
            "attention_soft": "#bfa05e",
            "attention_bg": "#332812",
            "interrupt": "#e38ec5",
            "interrupt_rest": "#ad7197",
            "interrupt_bg": "#301c2a",
            "fault": "#eb5768",
            "fault_soft": "#ba4957",
            "fault_bg": "#36151a",
        },
    },
    # 7. ROBCO UOS — Vault-Tec / RobCo Industries TERMLINK Terminal
    "robco": {
        "label": "RobCo UOS",
        "group": "retro",
        "box_style": "heavy",
        "title_brackets": ["[ ", " ]"],
        "button_brackets": ["[", "]"],
        "slider_brackets": ["[", "]"],
        "slider_fill": "\u2588",
        "slider_empty": "\u2591",
        "pill_dots": ["\u25a0", "\u25a1"],
        "tab_caps": ["\u2590", "\u258c"],
        "collapsible_icons": ["\u25bc", "\u25b6"],
        "table_rule": "\u2501",
        "kv_separator": ":",
        "search_prefix": "/ >",
        "search_cursor": "\u2502",
        "colors": {
            "surface_bg": "#041107",
            "window_opacity": 0.90,
            "box_opacity": 0.82,
            "structure": "#19542c",
            "structure_bg": "#082110",
            "inactive": "#2c7542",
            "schema": "#55ba73",
            "focus": "#d6ffe0",
            "focus_val": "#9ef7b4",
            "intent": "#1aff80",
            "intent_soft": "#25c468",
            "intent_bg": "#053318",
            "vitality": "#7dff3b",
            "vitality_soft": "#60bf30",
            "vitality_bg": "#132e05",
            "attention": "#ffe135",
            "attention_soft": "#c7af2c",
            "attention_bg": "#302904",
            "interrupt": "#ff8c2b",
            "interrupt_rest": "#bf6a24",
            "interrupt_bg": "#331905",
            "fault": "#ff3636",
            "fault_soft": "#c43333",
            "fault_bg": "#380707",
        },
    },
}


def get_active_theme_preset_id() -> str:
    """Restituisce l'ID del preset architetturale attualmente attivo (o stringa vuota se custom)."""
    asc = THEME.get("ascii", {})
    col = THEME.get("colors", {})
    cur_surf = get_surface_bg_hex().lower()
    check_axes = ("structure", "inactive", "schema", "focus", "intent", "vitality", "attention", "interrupt", "fault")
    for pid, spec in THEME_PRESETS.items():
        sc = spec["colors"]
        if str(asc.get("box_style", "")) != spec["box_style"]:
            continue
        if list(asc.get("title_brackets", [])) != list(spec["title_brackets"]):
            continue
        if list(asc.get("pill_dots", [])) != list(spec["pill_dots"]):
            continue
        if str(asc.get("slider_fill", "")) != spec["slider_fill"]:
            continue
        if cur_surf != str(sc.get("surface_bg", "")).lower():
            continue
        if any(str(col.get(k, "")).lower() != str(sc.get(k, "")).lower() for k in check_axes):
            continue
        return pid
    return ""


def _refresh_surface_companions(new_surf_hex: str) -> None:
    """Ricalibra le capsule *_bg e structure_bg rispetto al nuovo colore di superficie base."""
    c = THEME.setdefault("colors", {})
    for ax in ("structure", "intent", "vitality", "attention", "interrupt", "fault"):
        comp = derive_axis_companions(ax, str(c.get(ax, "")), new_surf_hex)
        for ck, cv in comp.items():
            if ck.endswith("_bg"):
                c[ck] = cv
    _sync_theme_aliases()


def _set_pers_fb(ok: bool, msg: str) -> None:
    STATE.set("personalization", "cc_fb", {"ok": bool(ok), "msg": str(msg), "ts": time.time()})


def _get_pers_fb() -> dict:
    fb = STATE.get("personalization", "cc_fb", {})
    if isinstance(fb, dict) and fb.get("msg") and (time.time() - float(fb.get("ts", 0))) < 15.0:
        return fb
    return {}


def _feedback_cards() -> list:
    fb = _get_pers_fb()
    if not fb:
        return []
    return [FeedbackBox(t("cc_personalization.theme_personalization_status"), bool(fb["ok"]), fb["msg"])]


def _theme_targets() -> tuple:
    return (
        THEME_PATH,
        os.path.join(ROOT_DIR, "config", "ncui", "theme.toml"),
        os.path.expanduser("~/Projects/ncui/config/ncui/theme.toml"),
    )


def _persist_theme_key(section: str, key: str, raw_val: str) -> None:
    _update_toml_key_in_section(
        section,
        key,
        raw_val,
        target_files=_theme_targets(),
        create_fallback=False,
    )


def _format_toml_value(val) -> str:
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, (int, float)):
        return str(val)
    if isinstance(val, list):
        return "[" + ", ".join(_format_toml_value(x) for x in val) + "]"
    s = str(val).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'


def _save_theme_file(path: str, theme_dict: dict) -> tuple[bool, str]:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        lines = []
        for sec in ("typography", "geometry", "ascii", "colors"):
            sub = theme_dict.get(sec, {})
            lines.append(f"[{sec}]")
            for k, v in sub.items():
                if k == "chars" and isinstance(v, dict):
                    continue
                if isinstance(v, dict):
                    continue
                lines.append(f"{k} = {_format_toml_value(v)}")
            lines.append("")
        th = theme_dict.get("colors", {}).get("thresholds", {})
        if th:
            lines.append("[colors.thresholds]")
            for k, v in th.items():
                lines.append(f"{k} = {_format_toml_value(v)}")
            lines.append("")
        chars = theme_dict.get("ascii", {}).get("chars", {})
        if isinstance(chars, dict) and chars:
            lines.append("[ascii.chars]")
            for k, v in chars.items():
                lines.append(f"{k} = {_format_toml_value(v)}")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines).rstrip() + "\n")
        return True, t("cc_personalization.saved_theme_to", p0=os.path.basename(path))
    except Exception as e:
        return False, str(e)


_WIN_ROLE_TO_XCUR = {
    "pointer": "left_ptr",
    "arrow": "left_ptr",
    "normal": "left_ptr",
    "default": "left_ptr",
    "help": "question_arrow",
    "work": "left_ptr_watch",
    "working": "left_ptr_watch",
    "appstarting": "left_ptr_watch",
    "progress": "left_ptr_watch",
    "busy": "watch",
    "wait": "watch",
    "cross": "crosshair",
    "precision": "crosshair",
    "text": "xterm",
    "beam": "xterm",
    "ibeam": "xterm",
    "hand": "pencil",
    "pen": "pencil",
    "handwriting": "pencil",
    "unavailiable": "not-allowed",
    "unavailable": "not-allowed",
    "no": "not-allowed",
    "forbid": "not-allowed",
    "vert": "sb_v_double_arrow",
    "ns": "sb_v_double_arrow",
    "sizens": "sb_v_double_arrow",
    "horz": "sb_h_double_arrow",
    "ew": "sb_h_double_arrow",
    "sizewe": "sb_h_double_arrow",
    "dgn1": "bd_double_arrow",
    "nwse": "bd_double_arrow",
    "sizenwse": "bd_double_arrow",
    "dgn2": "fd_double_arrow",
    "nesw": "fd_double_arrow",
    "sizenesw": "fd_double_arrow",
    "move": "fleur",
    "sizeall": "fleur",
    "alternate": "center_ptr",
    "up": "center_ptr",
    "link": "pointer",
    "hand2": "pointer",
    "pin": "pointer",
    "person": "pointer",
}

_XCUR_SYMLINKS = {
    "left_ptr": ("default", "arrow", "top_left_arrow"),
    "pointer": ("hand", "hand1", "hand2", "pointing_hand"),
    "xterm": ("text", "ibeam"),
    "watch": ("wait",),
    "left_ptr_watch": ("progress", "half-busy"),
    "question_arrow": ("help", "whats_this", "left_ptr_help"),
    "crosshair": ("cross", "tcross"),
    "not-allowed": ("crossed_circle", "forbidden", "no-drop", "pirate"),
    "fleur": ("move", "all-scroll", "size_all", "grab", "grabbing"),
    "sb_v_double_arrow": ("ns-resize", "n-resize", "s-resize", "v_double_arrow", "row-resize", "size_ver"),
    "sb_h_double_arrow": ("ew-resize", "e-resize", "w-resize", "h_double_arrow", "col-resize", "size_hor"),
    "bd_double_arrow": ("nwse-resize", "nw-resize", "se-resize", "size_fdiag"),
    "fd_double_arrow": ("nesw-resize", "ne-resize", "sw-resize", "size_bdiag"),
    "center_ptr": ("up_arrow",),
    "pencil": ("draft",),
}


NCUI_CURSOR_NAME = "NCUI Cursor"
NCUI_CURSOR_PREFIX = "NCUI-Cursor"
NCUI_CURSOR_ASSETS_DIR = os.path.join(ROOT_DIR, "assets", "cursors", "ncui-cursor")


def is_ncui_cursor_name(name: str) -> bool:
    s = str(name or "").strip().lower()
    return s in ("ncui cursor", "ncui-cursor") or s.startswith("ncui-cursor-")


def _hex_to_hsv(hex_color: str, fallback: str) -> tuple[float, float, float]:
    h = str(hex_color or "").strip().lstrip("#")
    if len(h) != 6 or not re.match(r"^[0-9a-fA-F]{6}$", h):
        h = fallback.lstrip("#")
    return colorsys.rgb_to_hsv(int(h[0:2], 16) / 255.0, int(h[2:4], 16) / 255.0, int(h[4:6], 16) / 255.0)


def _parse_xcursor_frames(path: str) -> list[tuple[int, int, int, int, int, int, bytes]]:
    with open(path, "rb") as f:
        data = f.read()
    if len(data) < 16:
        return []
    magic, hdr_sz, _ver, ntoc = struct.unpack_from("<4sIII", data, 0)
    if magic != b"Xcur":
        return []
    frames = []
    for i in range(ntoc):
        off = hdr_sz + i * 12
        if off + 12 > len(data):
            break
        ctype, subtype, pos = struct.unpack_from("<III", data, off)
        if ctype != 0xFFFD0002 or pos + 36 > len(data):
            continue
        chdr, _ct2, csub, _cv, w, h, xhot, yhot, delay = struct.unpack_from("<IIIIIIIII", data, pos)
        pix_start = pos + chdr
        pix_len = w * h * 4
        if pix_start + pix_len > len(data):
            continue
        frames.append((int(csub or subtype or max(w, h)), w, h, xhot, yhot, delay, data[pix_start : pix_start + pix_len]))
    return frames


def _recolor_xcursor_bgra(
    raw: bytes,
    intent_hsv: tuple[float, float, float],
    fault_hsv: tuple[float, float, float],
) -> bytes:
    ih, isat, ival = intent_hsv
    fh, fsat, fval = fault_hsv
    out = bytearray(raw)
    n = len(out)
    i = 0
    while i < n:
        a = out[i + 3]
        if a > 0:
            b, g, r = out[i], out[i + 1], out[i + 2]
            inv_a = 1.0 / float(a)
            ur = min(1.0, r * inv_a)
            ug = min(1.0, g * inv_a)
            ub = min(1.0, b * inv_a)
            hh, ss, vv = colorsys.rgb_to_hsv(ur, ug, ub)
            deg = hh * 360.0
            if ss >= 0.15 and 130.0 <= deg <= 240.0:
                nr, ng, nb = colorsys.hsv_to_rgb(ih, min(1.0, isat * (ss / 0.63)), min(1.0, ival * vv))
                out[i] = min(a, int(round(nb * a)))
                out[i + 1] = min(a, int(round(ng * a)))
                out[i + 2] = min(a, int(round(nr * a)))
            elif ss >= 0.15 and (deg <= 40.0 or deg >= 320.0):
                nr, ng, nb = colorsys.hsv_to_rgb(fh, min(1.0, fsat * (ss / 0.65)), min(1.0, fval * vv))
                out[i] = min(a, int(round(nb * a)))
                out[i + 1] = min(a, int(round(ng * a)))
                out[i + 2] = min(a, int(round(nr * a)))
        i += 4
    return bytes(out)


def ensure_ncui_cursor_installed(intent_hex: str = "", fault_hex: str = "") -> str:
    if not os.path.isdir(NCUI_CURSOR_ASSETS_DIR):
        return "Adwaita"
    colors = THEME.get("colors", {})
    raw_i = str(intent_hex or colors.get("intent", "#00f0ff")).strip().lstrip("#").lower()
    raw_f = str(fault_hex or colors.get("fault", "#ff2442")).strip().lstrip("#").lower()
    if not re.match(r"^[0-9a-f]{6}$", raw_i):
        raw_i = "00f0ff"
    if not re.match(r"^[0-9a-f]{6}$", raw_f):
        raw_f = "ff2442"

    disk_name = f"{NCUI_CURSOR_PREFIX}-{raw_i}-{raw_f}"
    os.makedirs(USER_ICONS_DIR, exist_ok=True)
    dest_dir = os.path.join(USER_ICONS_DIR, disk_name)
    if not os.path.isfile(os.path.join(dest_dir, "cursors", "left_ptr")):
        intent_hsv = _hex_to_hsv(f"#{raw_i}", "#00f0ff")
        fault_hsv = _hex_to_hsv(f"#{raw_f}", "#ff2442")
        tmp_dir = os.path.join(USER_ICONS_DIR, f".{disk_name}.tmp-{os.getpid()}")
        cur_dir = os.path.join(tmp_dir, "cursors")
        shutil.rmtree(tmp_dir, ignore_errors=True)
        os.makedirs(cur_dir, exist_ok=True)
        try:
            for fn in sorted(os.listdir(NCUI_CURSOR_ASSETS_DIR)):
                sp = os.path.join(NCUI_CURSOR_ASSETS_DIR, fn)
                if not os.path.isfile(sp):
                    continue
                frames = _parse_xcursor_frames(sp)
                if not frames:
                    continue
                recolored = [
                    (nom, w, h, xhot, yhot, delay, _recolor_xcursor_bgra(raw, intent_hsv, fault_hsv))
                    for (nom, w, h, xhot, yhot, delay, raw) in frames
                ]
                _write_xcursor_file(os.path.join(cur_dir, fn), recolored)
            for target_role, aliases in _XCUR_SYMLINKS.items():
                if not os.path.exists(os.path.join(cur_dir, target_role)):
                    continue
                for alias in aliases:
                    ap = os.path.join(cur_dir, alias)
                    if not os.path.exists(ap):
                        try:
                            os.symlink(target_role, ap)
                        except OSError:
                            pass
            with open(os.path.join(tmp_dir, "index.theme"), "w", encoding="utf-8") as f:
                f.write(
                    f"[Icon Theme]\nName={NCUI_CURSOR_NAME}\n"
                    f"Comment=NC-UI built-in theme-synced cursor (intent=#{raw_i}, fault=#{raw_f})\n"
                    f"Inherits=Adwaita\n"
                )
            shutil.rmtree(dest_dir, ignore_errors=True)
            os.replace(tmp_dir, dest_dir)
        except Exception:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise

    # Prune stale color-tagged variants so ~/.local/share/icons stays tidy
    try:
        for entry in os.listdir(USER_ICONS_DIR):
            if entry.startswith(f"{NCUI_CURSOR_PREFIX}-") and entry != disk_name:
                shutil.rmtree(os.path.join(USER_ICONS_DIR, entry), ignore_errors=True)
    except OSError:
        pass

    # Maintain stable fallback alias directory inheriting the active color-tagged build
    alias_dir = os.path.join(USER_ICONS_DIR, NCUI_CURSOR_PREFIX)
    try:
        os.makedirs(alias_dir, exist_ok=True)
        with open(os.path.join(alias_dir, "index.theme"), "w", encoding="utf-8") as f:
            f.write(f"[Icon Theme]\nName={NCUI_CURSOR_NAME}\nInherits={disk_name}\n")
    except OSError:
        pass
    return disk_name


def sync_ncui_cursor_if_active() -> bool:
    if os.environ.get("NC_UI_OFFSCREEN") == "1":
        return False
    cur_t, cur_sz = _read_cursor_cfg()
    if not is_ncui_cursor_name(cur_t):
        return False
    ok, _msg = _apply_cursor_cfg(NCUI_CURSOR_NAME, cur_sz)
    return ok


def _list_cursor_themes() -> list[dict]:
    found = {}
    if os.path.isdir(NCUI_CURSOR_ASSETS_DIR):
        try:
            n_master = sum(1 for fn in os.listdir(NCUI_CURSOR_ASSETS_DIR) if os.path.isfile(os.path.join(NCUI_CURSOR_ASSETS_DIR, fn)))
        except OSError:
            n_master = 15
        n_links = sum(len(v) for k, v in _XCUR_SYMLINKS.items() if os.path.isfile(os.path.join(NCUI_CURSOR_ASSETS_DIR, k)))
        found[NCUI_CURSOR_NAME] = {
            "name": NCUI_CURSOR_NAME,
            "path": NCUI_CURSOR_ASSETS_DIR,
            "is_builtin": True,
            "is_user": False,
            "count": n_master + n_links,
            "has_archive": False,
        }
    for root in SYS_ICONS_DIRS:
        if not os.path.isdir(root):
            continue
        try:
            entries = sorted(os.listdir(root))
        except OSError:
            continue
        for name in entries:
            if name in ("default", "hicolor", NCUI_CURSOR_PREFIX) or name.startswith(f"{NCUI_CURSOR_PREFIX}-") or name in found:
                continue
            cur_dir = os.path.join(root, name, "cursors")
            if not os.path.isdir(cur_dir):
                continue
            try:
                cnt = len(os.listdir(cur_dir))
            except OSError:
                cnt = 0
            if cnt <= 0:
                continue
            found[name] = {
                "name": name,
                "path": os.path.join(root, name),
                "is_builtin": False,
                "is_user": os.path.abspath(root) == os.path.abspath(USER_ICONS_DIR),
                "count": cnt,
                "has_archive": os.path.isfile(os.path.join(CURSORS_STORE_DIR, f"{name}.zip")),
            }
    if "Adwaita" not in found:
        found["Adwaita"] = {"name": "Adwaita", "path": "/usr/share/icons/Adwaita", "is_builtin": False, "is_user": False, "count": 0, "has_archive": False}
    return sorted(found.values(), key=lambda x: (0 if x.get("is_builtin") else (1 if x["is_user"] else 2), x["name"].lower()))


def _read_cursor_cfg() -> tuple[str, int]:
    typo = THEME.get("typography", {})
    theme_n = str(typo.get("cursor_theme") or STATE.get("personalization", "cursor_theme", "") or "")
    size_v = int(typo.get("cursor_size") or STATE.get("personalization", "cursor_size", 24) or 24)
    if os.path.exists(NIRI_CONFIG_FILE):
        try:
            txt = open(NIRI_CONFIG_FILE, "r", encoding="utf-8").read()
            m_t = re.search(r'xcursor-theme\s+"([^"]+)"', txt)
            m_s = re.search(r"xcursor-size\s+(\d+)", txt)
            if m_t:
                theme_n = m_t.group(1)
            if m_s:
                size_v = int(m_s.group(1))
        except Exception:
            pass
    if is_ncui_cursor_name(theme_n):
        theme_n = NCUI_CURSOR_NAME
    return theme_n or "Adwaita", max(16, min(64, int(size_v or 24)))


def _update_gtk_ini_cursor(ini_path: str, theme_name: str, size_val: int) -> None:
    os.makedirs(os.path.dirname(ini_path), exist_ok=True)
    txt = ""
    if os.path.isfile(ini_path):
        try:
            txt = open(ini_path, "r", encoding="utf-8").read()
        except OSError:
            txt = ""
    lines = txt.splitlines()
    if not any(ln.strip().lower() == "[settings]" for ln in lines):
        lines = ["[Settings]"] + lines
    out = []
    in_sec = False
    wrote_t = wrote_s = False
    for ln in lines:
        s = ln.strip()
        if s.startswith("[") and s.endswith("]"):
            if in_sec:
                if not wrote_t:
                    out.append(f"gtk-cursor-theme-name={theme_name}")
                    wrote_t = True
                if not wrote_s:
                    out.append(f"gtk-cursor-theme-size={int(size_val)}")
                    wrote_s = True
            in_sec = (s.lower() == "[settings]")
            out.append(ln)
            continue
        if in_sec and s.startswith("gtk-cursor-theme-name="):
            out.append(f"gtk-cursor-theme-name={theme_name}")
            wrote_t = True
        elif in_sec and s.startswith("gtk-cursor-theme-size="):
            out.append(f"gtk-cursor-theme-size={int(size_val)}")
            wrote_s = True
        else:
            out.append(ln)
    if in_sec:
        if not wrote_t:
            out.append(f"gtk-cursor-theme-name={theme_name}")
        if not wrote_s:
            out.append(f"gtk-cursor-theme-size={int(size_val)}")
    with open(ini_path, "w", encoding="utf-8") as f:
        f.write("\n".join(out).rstrip() + "\n")


def _apply_cursor_cfg(theme_name: str, size_val: int) -> tuple[bool, str]:
    raw_t = str(theme_name or "Adwaita").strip() or "Adwaita"
    is_ncui = is_ncui_cursor_name(raw_t)
    clean_t = NCUI_CURSOR_NAME if is_ncui else raw_t
    disk_t = ensure_ncui_cursor_installed() if is_ncui else clean_t
    clean_sz = max(16, min(64, int(size_val or 24)))
    STATE.set("personalization", "cursor_theme", clean_t)
    STATE.set("personalization", "cursor_size", clean_sz)
    arch_name = "" if is_ncui else (f"{clean_t}.zip" if os.path.isfile(os.path.join(CURSORS_STORE_DIR, f"{clean_t}.zip")) else "")
    typo = THEME.setdefault("typography", {})
    typo["cursor_theme"] = clean_t
    typo["cursor_size"] = clean_sz
    typo["cursor_archive"] = arch_name
    _save_theme_file(THEME_PATH, THEME)

    for ini_p in (
        os.path.expanduser("~/.config/gtk-3.0/settings.ini"),
        os.path.expanduser("~/.config/gtk-4.0/settings.ini"),
    ):
        try:
            _update_gtk_ini_cursor(ini_p, disk_t, clean_sz)
        except OSError:
            pass

    for def_p in (
        os.path.expanduser("~/.icons/default/index.theme"),
        os.path.join(USER_ICONS_DIR, "default", "index.theme"),
    ):
        try:
            os.makedirs(os.path.dirname(def_p), exist_ok=True)
            with open(def_p, "w", encoding="utf-8") as f:
                f.write(f"[Icon Theme]\nName=Default\nInherits={disk_t}\n")
        except OSError:
            pass

    os.environ["XCURSOR_THEME"] = disk_t
    os.environ["XCURSOR_SIZE"] = str(clean_sz)
    try:
        from gi.repository import Gtk
        settings = Gtk.Settings.get_default()
        if settings is not None:
            settings.set_property("gtk-cursor-theme-name", disk_t)
            settings.set_property("gtk-cursor-theme-size", clean_sz)
    except Exception:
        pass

    if os.path.exists(NIRI_CONFIG_FILE):
        try:
            old_txt = open(NIRI_CONFIG_FILE, "r", encoding="utf-8").read()
            txt = old_txt
            c_blk = f'cursor {{\n    xcursor-theme "{disk_t}"\n    xcursor-size {clean_sz}\n}}'
            m_blk = re.search(r"(?ms)^[ \t]*cursor\s*\{[^\}]*\}", txt)
            if m_blk:
                blk = m_blk.group(0)
                if re.search(r"xcursor-theme\s+\"[^\"]*\"", blk):
                    blk = re.sub(r'xcursor-theme\s+"[^"]*"', f'xcursor-theme "{disk_t}"', blk)
                else:
                    blk = blk.rstrip("}") + f'    xcursor-theme "{disk_t}"\n}}'
                if re.search(r"xcursor-size\s+\d+", blk):
                    blk = re.sub(r"xcursor-size\s+\d+", f"xcursor-size {clean_sz}", blk)
                else:
                    blk = blk.rstrip("}") + f"    xcursor-size {clean_sz}\n}}"
                txt = txt[:m_blk.start()] + blk + txt[m_blk.end():]
            else:
                txt = txt.rstrip() + f"\n\n{c_blk}\n"
            with open(NIRI_CONFIG_FILE, "w", encoding="utf-8") as f:
                f.write(txt)
            if probe.has_bin("niri"):
                ok_v, _o, err_v = probe.run_cmd_full(["niri", "validate", "-c", NIRI_CONFIG_FILE], timeout=2.0)
                if not ok_v:
                    with open(NIRI_CONFIG_FILE, "w", encoding="utf-8") as f:
                        f.write(old_txt)
                    return False, err_v or "niri validate failed"
                probe.run_cmd(["niri", "msg", "action", "load-config-file"], timeout=1.5)
        except Exception as e:
            return False, str(e)
    return True, t("cc_personalization.cur_applied_msg", p0=clean_t, p1=clean_sz)


def _pixbuf_to_xcur_bgra(pb) -> tuple[int, int, bytes]:
    w, h = int(pb.get_width()), int(pb.get_height())
    nc = int(pb.get_n_channels())
    stride = int(pb.get_rowstride())
    px = pb.get_pixels()
    out = bytearray(w * h * 4)
    dst = 0
    for y in range(h):
        row_off = y * stride
        for x in range(w):
            off = row_off + x * nc
            r, g, b = px[off], px[off + 1], px[off + 2]
            a = px[off + 3] if nc >= 4 else 255
            if a == 0:
                out[dst : dst + 4] = b"\x00\x00\x00\x00"
            elif a < 255:
                out[dst] = (b * a + 127) // 255
                out[dst + 1] = (g * a + 127) // 255
                out[dst + 2] = (r * a + 127) // 255
                out[dst + 3] = a
            else:
                out[dst] = b
                out[dst + 1] = g
                out[dst + 2] = r
                out[dst + 3] = 255
            dst += 4
    return w, h, bytes(out)


def _decode_win_img_payload(payload: bytes, w_hint: int, h_hint: int) -> tuple[int, int, bytes] | None:
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        ldr = GdkPixbuf.PixbufLoader.new_with_type("png")
        ldr.write(payload)
        ldr.close()
        pb = ldr.get_pixbuf()
        return _pixbuf_to_xcur_bgra(pb) if pb else None
    wb = 0 if w_hint >= 256 else w_hint
    hb = 0 if h_hint >= 256 else h_hint
    ico_blob = struct.pack("<HHHBBBBHHII", 0, 1, 1, wb, hb, 0, 0, 1, 32, len(payload), 22) + payload
    try:
        ldr = GdkPixbuf.PixbufLoader.new_with_type("ico")
        ldr.write(ico_blob)
        ldr.close()
        pb = ldr.get_pixbuf()
        if pb:
            return _pixbuf_to_xcur_bgra(pb)
    except Exception:
        pass
    return None


def _parse_cur_frames(raw: bytes, default_delay_ms: int = 50) -> list[tuple[int, int, int, int, int, int, bytes]]:
    if len(raw) < 6:
        return []
    _res, ico_type, count = struct.unpack_from("<HHH", raw, 0)
    if ico_type not in (1, 2) or count <= 0 or len(raw) < 6 + count * 16:
        return []
    frames = []
    for i in range(count):
        wb, hb, _cols, _r, p_xh, b_yh, img_sz, img_off = struct.unpack_from("<BBBBHHII", raw, 6 + i * 16)
        if img_off + img_sz > len(raw) or img_sz <= 0:
            continue
        w_hint, h_hint = (wb or 256), (hb or 256)
        decoded = _decode_win_img_payload(raw[img_off : img_off + img_sz], w_hint, h_hint)
        if not decoded:
            continue
        w, h, bgra = decoded
        xhot = min(max(0, p_xh if ico_type == 2 else 0), max(0, w - 1))
        yhot = min(max(0, b_yh if ico_type == 2 else 0), max(0, h - 1))
        frames.append((max(w, h), w, h, xhot, yhot, max(16, int(default_delay_ms)), bgra))
    return frames


def _parse_ani_frames(raw: bytes) -> list[tuple[int, int, int, int, int, int, bytes]]:
    if len(raw) < 12 or raw[:4] != b"RIFF" or raw[8:12] != b"ACON":
        return []
    pos = 12
    default_jif = 10
    rates = []
    seq = []
    icons = []
    while pos + 8 <= len(raw):
        ck_id = raw[pos : pos + 4]
        ck_sz = struct.unpack_from("<I", raw, pos + 4)[0]
        ck_data = raw[pos + 8 : pos + 8 + ck_sz]
        pos += 8 + ck_sz + (ck_sz & 1)
        if ck_id == b"anih" and len(ck_data) >= 36:
            vals = struct.unpack_from("<9I", ck_data, 0)
            default_jif = vals[7] or 10
        elif ck_id == b"rate":
            cnt = len(ck_data) // 4
            rates = list(struct.unpack_from(f"<{cnt}I", ck_data, 0))
        elif ck_id == b"seq ":
            cnt = len(ck_data) // 4
            seq = list(struct.unpack_from(f"<{cnt}I", ck_data, 0))
        elif ck_id == b"LIST" and len(ck_data) >= 4 and ck_data[:4] == b"fram":
            sub_p = 4
            while sub_p + 8 <= len(ck_data):
                sid = ck_data[sub_p : sub_p + 4]
                ssz = struct.unpack_from("<I", ck_data, sub_p + 4)[0]
                if sid == b"icon":
                    icons.append(ck_data[sub_p + 8 : sub_p + 8 + ssz])
                sub_p += 8 + ssz + (ssz & 1)
    if not icons:
        return []
    steps = seq if seq else list(range(len(icons)))
    out = []
    for step_i, frame_idx in enumerate(steps):
        if not (0 <= frame_idx < len(icons)):
            continue
        jif = rates[step_i] if step_i < len(rates) else default_jif
        delay_ms = max(16, int(round((jif or default_jif) * 1000.0 / 60.0)))
        out.extend(_parse_cur_frames(icons[frame_idx], default_delay_ms=delay_ms))
    return out


def _write_xcursor_file(out_path: str, frames: list[tuple[int, int, int, int, int, int, bytes]]) -> None:
    ntoc = len(frames)
    hdr_end = 16 + ntoc * 12
    toc_buf = bytearray()
    chunks_buf = bytearray()
    cur_pos = hdr_end
    for nom, w, h, xhot, yhot, delay_ms, bgra in frames:
        toc_buf.extend(struct.pack("<III", 0xFFFD0002, nom, cur_pos))
        ch = struct.pack("<IIIIIIIII", 36, 0xFFFD0002, nom, 1, w, h, xhot, yhot, delay_ms)
        chunks_buf.extend(ch)
        chunks_buf.extend(bgra)
        cur_pos += len(ch) + len(bgra)
    with open(out_path, "wb") as f:
        f.write(struct.pack("<4sIII", b"Xcur", 16, 0x10000, ntoc))
        f.write(toc_buf)
        f.write(chunks_buf)


def _match_cursor_role(stem: str) -> str:
    s = stem.lower().strip()
    for token, xcur_name in _WIN_ROLE_TO_XCUR.items():
        if s == token or s.endswith(f"_{token}") or s.endswith(f"-{token}"):
            return xcur_name
    for token, xcur_name in _WIN_ROLE_TO_XCUR.items():
        if token in s:
            return xcur_name
    return ""


def _parse_inf_roles(inf_text: str) -> dict[str, str]:
    roles = {}
    in_strings = False
    for raw_ln in inf_text.splitlines():
        ln = raw_ln.strip()
        if not ln or ln.startswith(";"):
            continue
        if ln.startswith("[") and ln.endswith("]"):
            in_strings = (ln[1:-1].strip().lower() == "strings")
            continue
        if in_strings and "=" in ln:
            k, v = ln.split("=", 1)
            k_low = k.strip().lower()
            v_clean = os.path.basename(v.strip().strip('"').strip("'").replace("\\", "/")).lower()
            if k_low in _WIN_ROLE_TO_XCUR and v_clean:
                roles[v_clean] = _WIN_ROLE_TO_XCUR[k_low]
    return roles


def convert_windows_cursor_zip(zip_path: str, custom_name: str = "") -> tuple[bool, str, str]:
    zp = os.path.abspath(os.path.expanduser(str(zip_path or "").strip().strip('"').strip("'")))
    if zp.startswith("file://"):
        zp = zp[7:]
    if not os.path.isfile(zp) or not zipfile.is_zipfile(zp):
        return False, t("cc_personalization.cur_zip_invalid", p0=zip_path), ""

    raw_base = custom_name or os.path.splitext(os.path.basename(zp))[0]
    theme_name = re.sub(r"[^a-zA-Z0-9_.-]+", "-", raw_base).strip("-._") or "win-cursor"
    dest_dir = os.path.join(USER_ICONS_DIR, theme_name)
    cur_dir = os.path.join(dest_dir, "cursors")
    os.makedirs(cur_dir, exist_ok=True)

    inf_map = {}
    win_entries = []
    precompiled_xcur = []
    with zipfile.ZipFile(zp, "r") as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            bn = os.path.basename(info.filename)
            if not bn:
                continue
            low = bn.lower()
            data = zf.read(info.filename)
            if low.endswith(".inf"):
                inf_map.update(_parse_inf_roles(data.decode("utf-8", errors="ignore")))
            elif low.endswith((".cur", ".ani")):
                win_entries.append((bn, data))
            elif data[:4] == b"Xcur" and "/cursors/" in info.filename.replace("\\", "/"):
                precompiled_xcur.append((bn, data))

    written_roles = set()
    if win_entries:
        fallback_frames = None
        for bn, data in win_entries:
            frames = _parse_ani_frames(data) if bn.lower().endswith(".ani") else _parse_cur_frames(data)
            if not frames:
                continue
            if fallback_frames is None:
                fallback_frames = frames
            role = inf_map.get(bn.lower()) or _match_cursor_role(os.path.splitext(bn)[0])
            if not role or role in written_roles:
                continue
            _write_xcursor_file(os.path.join(cur_dir, role), frames)
            written_roles.add(role)
        if "left_ptr" not in written_roles and fallback_frames:
            _write_xcursor_file(os.path.join(cur_dir, "left_ptr"), fallback_frames)
            written_roles.add("left_ptr")
    elif precompiled_xcur:
        for bn, data in precompiled_xcur:
            with open(os.path.join(cur_dir, bn), "wb") as f:
                f.write(data)
            written_roles.add(bn)

    if not written_roles:
        shutil.rmtree(dest_dir, ignore_errors=True)
        return False, t("cc_personalization.cur_zip_no_cursors"), ""

    for target_role, aliases in _XCUR_SYMLINKS.items():
        if not os.path.exists(os.path.join(cur_dir, target_role)):
            continue
        for alias in aliases:
            ap = os.path.join(cur_dir, alias)
            if not os.path.exists(ap):
                try:
                    os.symlink(target_role, ap)
                except OSError:
                    pass

    with open(os.path.join(dest_dir, "index.theme"), "w", encoding="utf-8") as f:
        f.write(f"[Icon Theme]\nName={theme_name}\nComment=Converted from Windows cursor archive by NC-UI\nInherits=Adwaita\n")

    os.makedirs(CURSORS_STORE_DIR, exist_ok=True)
    stored_zip = os.path.join(CURSORS_STORE_DIR, f"{theme_name}.zip")
    if os.path.abspath(zp) != os.path.abspath(stored_zip):
        shutil.copy2(zp, stored_zip)

    _cur_t, cur_sz = _read_cursor_cfg()
    ok_app, msg_app = _apply_cursor_cfg(theme_name, cur_sz)
    if not ok_app:
        return False, msg_app, theme_name
    return True, t("cc_personalization.cur_zip_installed", p0=theme_name, p1=len(written_roles)), theme_name


def _delete_cursor_theme(name: str) -> tuple[bool, str]:
    if is_ncui_cursor_name(name):
        return False, t("cc_personalization.cur_del_builtin_err")
    clean = os.path.basename(str(name or "").strip())
    if not clean:
        return False, "Invalid cursor theme"
    target = os.path.join(USER_ICONS_DIR, clean)
    if not os.path.isdir(target):
        return False, t("cc_personalization.cur_del_system_err")
    shutil.rmtree(target, ignore_errors=True)
    arch = os.path.join(CURSORS_STORE_DIR, f"{clean}.zip")
    if os.path.isfile(arch):
        try:
            os.remove(arch)
        except OSError:
            pass
    cur_t, cur_sz = _read_cursor_cfg()
    if cur_t == clean:
        _apply_cursor_cfg("Adwaita", cur_sz)
    return True, t("cc_personalization.cur_deleted_msg", p0=clean)


_SOLID_NAMED_COLORS = {
    "white": "#ffffff", "black": "#000000", "red": "#ff0000", "green": "#00ff00",
    "lime": "#00ff00", "blue": "#0000ff", "yellow": "#ffff00", "cyan": "#00ffff",
    "aqua": "#00ffff", "magenta": "#ff00ff", "fuchsia": "#ff00ff", "gray": "#808080",
    "grey": "#808080", "darkgray": "#2a2a2e", "darkgrey": "#2a2a2e",
    "lightgray": "#c0c0c0", "lightgrey": "#c0c0c0", "silver": "#c0c0c0",
    "orange": "#ff8800", "purple": "#800080", "violet": "#800080", "pink": "#ffc0cb",
    "brown": "#8b4513", "navy": "#000080", "teal": "#008080", "olive": "#808000",
    "maroon": "#800000", "indigo": "#4b0082", "coral": "#ff7f50", "gold": "#ffd700",
    "cyber": "#0d0e15", "anthracite": "#18181a", "dark": "#18181a",
    "mocha": "#1e1e2e", "catppuccin": "#1e1e2e", "mantle": "#181825",
    "crust": "#11111b", "lavender": "#b4befe", "mauve": "#cba6f7",
    "peach": "#fab387", "sky": "#89dceb", "sapphire": "#74c7ec",
    "rosewater": "#f5e0dc", "flamingo": "#f2cdcd",
}
_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
_GIF_EXTS = (".gif",)
_VIDEO_EXTS = (".mp4", ".mkv", ".webm", ".mov", ".avi")
_MEDIA_EXTS = _IMAGE_EXTS + _GIF_EXTS + _VIDEO_EXTS
_THUMB_CACHE_DIR = "/tmp/nc_wp_thumbs"
_YT_WALL_DIR = os.path.expanduser("~/.cache/nc-ui/wallpapers")
_YAZI_PICK_FILE = "/tmp/nc_yazi_pick"
_BG_FRAME_EXTRACTING: set = set()
_BG_YT_THUMBS: set = set()
_WP_USER_TOUCHED: bool = False
_YT_DL_SEQ: int = 0
_ASCII_APPLY_SEQ: int = 0
_ASCII_APPLY_STATUS: str = ""
_ASCII_PREVIEW_PENDING: set = set()
_LAST_ASCII_PREVIEW: dict[str, str] = {}
_MEDIA_DIM_CACHE: dict[tuple[str, int], tuple[int, int]] = {}
_YT_VID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_YT_PL_RE = re.compile(r"^[A-Za-z0-9_-]{10,}$")
_YT_PL_RAW_RE = re.compile(r"^(?:PL|UU|FL|LP|RD|OLAK5uy_)[A-Za-z0-9_-]{10,}$")
_YT_PREFIX_FILE_RE = re.compile(r"^yt_([A-Za-z0-9_-]{11})(?:-[0-9]+)?\.[A-Za-z0-9]+$")
_YT_LOCAL_FILE_RE = re.compile(r"^(?:yt_)?([A-Za-z0-9_-]{11})(?:-[0-9]+)?\.[A-Za-z0-9]+$")
_YT_LOCAL_PL_RE = re.compile(r"^yt_pl_([A-Za-z0-9_-]{10,})$")


def _is_in_yt_wall_dir(path_str: str) -> bool:
    s = str(path_str or "").strip()
    if not s:
        return False
    p = os.path.abspath(os.path.expanduser(s))
    yt_d = os.path.abspath(_YT_WALL_DIR)
    return p == yt_d or p.startswith(yt_d + os.sep)


def _match_yt_local_vid(path_str: str) -> str:
    base = os.path.basename(str(path_str or "").strip().rstrip("/"))
    m = _YT_PREFIX_FILE_RE.match(base)
    if m:
        return m.group(1)
    if _is_in_yt_wall_dir(path_str):
        m = _YT_LOCAL_FILE_RE.match(base)
        if m:
            return m.group(1)
    return ""
_YT_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtube-nocookie.com",
    "www.youtube-nocookie.com",
    "youtu.be",
    "www.youtu.be",
}


def _parse_youtube_input(raw_str: str) -> dict:
    s = str(raw_str or "").strip()
    empty = {"ok": False, "video_id": "", "playlist_id": "", "is_playlist": False, "canonical_url": "", "kind": ""}
    if not s:
        return empty
    if _YT_VID_RE.match(s):
        return {
            "ok": True,
            "video_id": s,
            "playlist_id": "",
            "is_playlist": False,
            "canonical_url": f"https://www.youtube.com/watch?v={s}",
            "kind": "YT VIDEO",
        }
    if _YT_PL_RAW_RE.match(s):
        return {
            "ok": True,
            "video_id": "",
            "playlist_id": s,
            "is_playlist": True,
            "canonical_url": f"https://www.youtube.com/playlist?list={s}",
            "kind": "YT PLAYLIST",
        }
    cand = s if re.match(r"^https?://", s, re.IGNORECASE) else (f"https://{s}" if "." in s else "")
    if not cand:
        return empty
    try:
        parsed = urlparse(cand)
    except Exception:
        return empty
    host = (parsed.hostname or "").lower()
    if host not in _YT_HOSTS:
        return empty
    qs = parse_qs(parsed.query)
    parts = [p for p in parsed.path.split("/") if p]
    vid = ""
    plid = (qs.get("list") or [""])[0].strip()
    if host.endswith("youtu.be"):
        if parts and _YT_VID_RE.match(parts[0]):
            vid = parts[0]
    else:
        if parsed.path.startswith("/watch") or "v" in qs:
            v_cand = (qs.get("v") or [""])[0].strip()
            if _YT_VID_RE.match(v_cand):
                vid = v_cand
        elif len(parts) >= 2 and parts[0] in ("shorts", "embed", "live", "v"):
            if _YT_VID_RE.match(parts[1]):
                vid = parts[1]
    if plid and not _YT_PL_RE.match(plid):
        plid = ""
    is_pl = bool(plid)
    if not vid and not plid:
        return empty
    if is_pl and vid:
        canonical = f"https://www.youtube.com/watch?v={vid}&list={plid}"
    elif is_pl:
        canonical = f"https://www.youtube.com/playlist?list={plid}"
    else:
        canonical = f"https://www.youtube.com/watch?v={vid}"
    return {
        "ok": True,
        "video_id": vid,
        "playlist_id": plid,
        "is_playlist": is_pl,
        "canonical_url": canonical,
        "kind": "YT PLAYLIST" if is_pl else "YT VIDEO",
    }


def _resolve_yt_source(path_or_url: str) -> dict:
    yt = _parse_youtube_input(path_or_url)
    if yt["ok"]:
        return yt
    vid = _match_yt_local_vid(path_or_url)
    if vid:
        return _parse_youtube_input(vid)
    base = os.path.basename(str(path_or_url or "").strip().rstrip("/"))
    m_p = _YT_LOCAL_PL_RE.match(base)
    if m_p:
        return {
            "ok": True,
            "video_id": "",
            "playlist_id": m_p.group(1),
            "is_playlist": True,
            "canonical_url": f"https://www.youtube.com/playlist?list={m_p.group(1)}",
            "kind": "YT PLAYLIST",
        }
    return {"ok": False, "video_id": "", "playlist_id": "", "is_playlist": False, "canonical_url": "", "kind": ""}


def _yt_thumb_cache_path(yt: dict) -> str:
    key = yt.get("video_id") or (f"pl_{yt.get('playlist_id')}" if yt.get("playlist_id") else "")
    return os.path.join(_THUMB_CACHE_DIR, f"yt_thumb_{key}.jpg") if key else ""


def _ensure_youtube_thumbnail(yt: dict, cc_panel=None) -> str:
    if not yt or not yt.get("ok"):
        return ""
    thumb = _yt_thumb_cache_path(yt)
    if not thumb:
        return ""
    try:
        if os.path.isfile(thumb) and os.path.getsize(thumb) > 512:
            return thumb
    except OSError:
        pass
    if thumb in _BG_YT_THUMBS:
        return ""
    _BG_YT_THUMBS.add(thumb)

    def _fetch():
        try:
            os.makedirs(_THUMB_CACHE_DIR, exist_ok=True)
            img_url = ""
            if yt.get("video_id"):
                img_url = f"https://i.ytimg.com/vi/{yt['video_id']}/hqdefault.jpg"
            elif yt.get("canonical_url") and probe.has_bin("yt-dlp"):
                ok_u, out_u = probe.run_cmd(
                    ["yt-dlp", "--flat-playlist", "--playlist-items", "1", "--get-thumbnail", yt["canonical_url"]],
                    timeout=8.0,
                )
                if ok_u and out_u:
                    img_url = out_u.splitlines()[0].strip()
            if not img_url:
                return ""
            req = urllib.request.Request(img_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                data = resp.read()
            if data and len(data) > 512:
                tmp_t = f"{thumb}.tmp"
                with open(tmp_t, "wb") as f:
                    f.write(data)
                os.replace(tmp_t, thumb)
                return thumb
        except Exception:
            pass
        finally:
            _BG_YT_THUMBS.discard(thumb)
        return ""

    def _done(path: str):
        if path and cc_panel and cc_panel.shell:
            cc_panel.shell.refresh_view()

    probe.run_async(_fetch, on_done=_done)
    return ""


SOLID_CLASSIC_PRESETS = (
    ("#000000", "wp_solid_preset_black"),
    ("#FFFFFF", "wp_solid_preset_white"),
    ("#2E3440", "wp_solid_preset_slate"),
    ("#008080", "wp_solid_preset_win98"),
)
SOLID_DESIGNER_PRESETS = (
    ("#002B36", "wp_solid_preset_solarized"),
    ("#1A1B26", "wp_solid_preset_tokyo"),
    ("#1D2021", "wp_solid_preset_carbon"),
)


def _resolve_solid_hex(raw: str) -> str:
    s = str(raw or "").strip().lower()
    if s in _SOLID_NAMED_COLORS:
        return _SOLID_NAMED_COLORS[s].upper()
    hx = s.lstrip("#")
    if re.match(r"^[0-9a-f]{6}$", hx):
        return f"#{hx.upper()}"
    return "#18181A"


def detect_media_kind(path: str) -> str:
    s = str(path or "").strip()
    if not s:
        return ""
    yt = _parse_youtube_input(s)
    if yt["ok"]:
        return yt["kind"]
    base = os.path.basename(s.rstrip("/"))
    if _YT_LOCAL_PL_RE.match(base):
        return "YT PLAYLIST"
    if _match_yt_local_vid(s):
        return "YT VIDEO"
    ext = os.path.splitext(s.lower())[1]
    if ext in _GIF_EXTS:
        return "GIF"
    if ext in _VIDEO_EXTS:
        return "VIDEO"
    if ext in _IMAGE_EXTS:
        return "IMAGE"
    return ""


def count_folder_media(folder_path: str) -> int:
    d = os.path.expanduser(str(folder_path or "").strip())
    if not os.path.isdir(d):
        return 0
    cnt = 0
    try:
        with os.scandir(d) as it:
            for ent in it:
                if ent.is_file() and ent.name.lower().endswith(_MEDIA_EXTS):
                    cnt += 1
    except OSError:
        pass
    return cnt


def extract_media_preview_frame(path: str, cc_panel=None) -> str:
    """Return image/GIF path directly, cached YouTube thumb, or ffmpeg video frame."""
    raw = str(path or "").strip()
    if not raw:
        return ""
    yt = _parse_youtube_input(raw)
    if yt["ok"]:
        cached_yt = _yt_thumb_cache_path(yt)
        if cached_yt and os.path.isfile(cached_yt):
            return cached_yt
        _ensure_youtube_thumbnail(yt, cc_panel)
        return ""
    p = os.path.expanduser(raw)
    if not p or not os.path.isfile(p):
        return ""
    kind = detect_media_kind(p)
    if kind in ("IMAGE", "GIF"):
        return p
    if kind in ("VIDEO", "YT VIDEO") and probe.has_bin("ffmpeg"):
        try:
            os.makedirs(_THUMB_CACHE_DIR, exist_ok=True)
            mtime = os.path.getmtime(p)
            key = hashlib.md5(f"{p}:{mtime}".encode("utf-8")).hexdigest()[:16]
            thumb = os.path.join(_THUMB_CACHE_DIR, f"{key}.png")
            if os.path.isfile(thumb):
                return thumb
            for ts_seek in ("00:00:01", "00:00:00"):
                subprocess.run(
                    ["ffmpeg", "-y", "-ss", ts_seek, "-i", p, "-frames:v", "1", "-vf", "scale=640:-1", thumb],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=6.0,
                )
                if os.path.isfile(thumb):
                    return thumb
        except Exception:
            pass
    return ""


def _find_media_file(target: str, current_image: str = "") -> str:
    t_str = os.path.expanduser(str(target or "").strip())
    if current_image:
        if os.path.isabs(current_image) and os.path.exists(current_image):
            return current_image
        for d in (t_str, WALL_DIR, os.path.join(WALL_DIR, t_str)):
            cand = os.path.join(d, current_image)
            if os.path.exists(cand):
                return cand
    if t_str:
        if os.path.isfile(t_str):
            return t_str
        w_cand = os.path.join(WALL_DIR, t_str)
        if os.path.isfile(w_cand):
            return w_cand
        for d in (t_str, w_cand):
            if os.path.isdir(d):
                try:
                    for fn in sorted(os.listdir(d)):
                        if fn.lower().endswith(_MEDIA_EXTS):
                            return os.path.join(d, fn)
                except OSError:
                    pass
    return ""


def _resolve_image_candidate(target: str, current_image: str = "") -> str:
    media = _find_media_file(target, current_image)
    return extract_media_preview_frame(media) if media else ""


def extract_media_preview_frames(path: str, count: int = 6, cc_panel=None) -> list[str]:
    """Return cached multi-frame thumbnails for GIF/Video/YouTube."""
    raw = str(path or "").strip()
    if not raw:
        return []
    yt = _parse_youtube_input(raw)
    if yt["ok"]:
        t_img = extract_media_preview_frame(raw, cc_panel=cc_panel)
        return [t_img] if t_img else []
    p = os.path.expanduser(raw)
    if not p or not os.path.isfile(p):
        return []
    kind = detect_media_kind(p)
    if kind == "IMAGE":
        return [p]
    if kind not in ("GIF", "VIDEO", "YT VIDEO"):
        return []
    try:
        mtime = os.path.getmtime(p)
    except OSError:
        mtime = 0.0
    key = hashlib.md5(f"{p}:{mtime}".encode("utf-8")).hexdigest()[:16]
    os.makedirs(_THUMB_CACHE_DIR, exist_ok=True)
    cached = [
        os.path.join(_THUMB_CACHE_DIR, f"{key}_f{i}.png")
        for i in range(count)
        if os.path.isfile(os.path.join(_THUMB_CACHE_DIR, f"{key}_f{i}.png"))
    ]
    if len(cached) >= 2:
        return cached
    if probe.has_bin("ffmpeg") and key not in _BG_FRAME_EXTRACTING:
        _BG_FRAME_EXTRACTING.add(key)
        pattern = os.path.join(_THUMB_CACHE_DIR, f"{key}_f%d.png")
        vf = "fps=6,scale=320:-1" if kind == "GIF" else "fps=2,scale=320:-1"

        def _extract():
            try:
                subprocess.run(
                    ["ffmpeg", "-y", "-i", p, "-vf", vf, "-frames:v", str(count), "-start_number", "0", pattern],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=10.0,
                )
            except Exception:
                pass
            finally:
                _BG_FRAME_EXTRACTING.discard(key)

        probe.run_async(_extract)
    single = extract_media_preview_frame(p)
    return [single] if single else []


def is_live_15fps_enabled() -> bool:
    default_fps = (os.cpu_count() or 4) >= 6
    return bool(STATE.get("desktop", "preview_15fps", default_fps))


def _get_niri_outputs() -> dict:
    data = get_niri_outputs_data()
    if not isinstance(data, dict):
        return {}
    active = {k: v for k, v in data.items() if isinstance(v, dict) and v.get("logical")}
    return active or data


def _target_yt_height(mon_w: int, mon_h: int) -> int:
    short_side = min(max(320, int(mon_w or 1920)), max(240, int(mon_h or 1080)))
    for tier in (720, 1080, 1440, 2160):
        if short_side <= tier + 120:
            return tier
    return 2160


def _get_monitor_resolution(mon: str, outputs_data: dict = None) -> tuple[int, int]:
    data = outputs_data if isinstance(outputs_data, dict) else _get_niri_outputs()
    out = data.get(mon) if isinstance(data, dict) else None
    if isinstance(out, dict):
        modes = out.get("modes") or []
        cur_idx = out.get("current_mode")
        if isinstance(cur_idx, int) and 0 <= cur_idx < len(modes):
            m = modes[cur_idx] or {}
            mw, mh = int(m.get("width") or 0), int(m.get("height") or 0)
            if mw >= 320 and mh >= 240:
                return mw, mh
        logical = out.get("logical") or {}
        lw, lh = int(logical.get("width") or 0), int(logical.get("height") or 0)
        if lw >= 320 and lh >= 240:
            sc = float(logical.get("scale") or 1.0)
            return int(round(lw * sc)), int(round(lh * sc))
    return 1920, 1080


def _target_yt_height_for_monitors(mons: list[str], outputs_data: dict = None) -> int:
    return max((_target_yt_height(*_get_monitor_resolution(m, outputs_data)) for m in mons), default=1080)


def get_selected_monitors(out_names: list[str] = None) -> list[str]:
    if out_names is None:
        out_names = sorted(_get_niri_outputs().keys())
    raw = STATE.get("desktop", "selected_monitors", None)
    if raw is None or not isinstance(raw, list):
        return list(out_names)
    return [o for o in out_names if o in raw]


def get_ascii_apply_status() -> str:
    return _ASCII_APPLY_STATUS


def _norm_align(raw) -> str:
    v = str(raw or "center").strip().lower()
    return v if v in ("top", "center", "bottom") else "center"


def get_media_dimensions(path: str) -> tuple[int, int]:
    """Return cached (width, height) of a local image/GIF/video header, or (0, 0)."""
    raw = str(path or "").strip()
    if not raw or raw.startswith("http"):
        return 0, 0
    ap = os.path.abspath(os.path.expanduser(raw))
    if not os.path.isfile(ap):
        return 0, 0
    try:
        mtime_ns = int(os.stat(ap).st_mtime_ns)
    except OSError:
        mtime_ns = 0
    ck = (ap, mtime_ns)
    if ck in _MEDIA_DIM_CACHE:
        return _MEDIA_DIM_CACHE[ck]

    w, h = 0, 0
    kind = detect_media_kind(ap)
    if kind in ("IMAGE", "GIF"):
        try:
            from PIL import Image
            with Image.open(ap) as im:
                w, h = int(im.size[0]), int(im.size[1])
        except Exception:
            try:
                _fmt, pw, ph = GdkPixbuf.Pixbuf.get_file_info(ap)
                w, h = int(pw or 0), int(ph or 0)
            except Exception:
                w, h = 0, 0
    elif kind in ("VIDEO", "YT VIDEO") and probe.has_bin("ffprobe"):
        ok_p, out_p = probe.run_cmd(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", ap],
            timeout=1.5,
        )
        if ok_p and "x" in out_p:
            try:
                ps = out_p.splitlines()[0].strip().split("x", 1)
                w, h = int(ps[0]), int(ps[1])
            except (ValueError, IndexError):
                w, h = 0, 0

    if len(_MEDIA_DIM_CACHE) > 128:
        _MEDIA_DIM_CACHE.clear()
    _MEDIA_DIM_CACHE[ck] = (w, h)
    return w, h


def should_show_scene_alignment(scene_path: str, fit: str, sel_mons: list[str], outputs_data: dict) -> bool:
    """Return True when fit is 'fill' and the local scene is proportionally taller than >=1 selected monitor."""
    if str(fit or "fill") != "fill":
        return False
    mw, mh = get_media_dimensions(scene_path)
    if mw <= 0 or mh <= 0:
        return False
    media_ar = mw / float(mh)
    mons = list(sel_mons or sorted((outputs_data or {}).keys()))
    for m in mons:
        ow, oh = _get_monitor_resolution(m, outputs_data)
        if ow > 0 and oh > 0 and media_ar < (ow / float(oh)) - 0.02:
            return True
    return False


def _cancel_pending_ascii_apply() -> None:
    global _ASCII_APPLY_SEQ, _ASCII_APPLY_STATUS
    _ASCII_APPLY_SEQ += 1
    _ASCII_APPLY_STATUS = ""


def get_editor_asciify_cfg() -> dict:
    return normalize_asciify_cfg({
        "enabled": STATE.get("desktop", "ascii_enabled", False),
        "mode": STATE.get("desktop", "ascii_mode", "color"),
        "chars": STATE.get("desktop", "ascii_chars", "standard"),
        "density": STATE.get("desktop", "ascii_density", 1.0),
        "tint": STATE.get("desktop", "ascii_tint", 0.22),
        "scale": STATE.get("desktop", "ascii_scale", 1.0),
        "contrast": STATE.get("desktop", "ascii_contrast", 1.0),
        "brightness": STATE.get("desktop", "ascii_brightness", 1.0),
        "invert": STATE.get("desktop", "ascii_invert", False),
    })


def _set_editor_asciify_cfg(cfg: dict) -> dict:
    c = normalize_asciify_cfg(cfg)
    for k in ("enabled", "mode", "chars", "density", "tint", "scale", "contrast", "brightness", "invert"):
        STATE.set("desktop", f"ascii_{k}", c[k])
    return c


def _find_static_image_file(target: str, current_image: str = "") -> str:
    t_str = os.path.expanduser(str(target or "").strip())
    if current_image:
        for cand in (
            current_image if os.path.isabs(current_image) else "",
            os.path.join(t_str, current_image) if t_str else "",
            os.path.join(WALL_DIR, current_image),
        ):
            if cand and os.path.isfile(cand) and cand.lower().endswith(_IMAGE_EXTS):
                return cand
    if t_str:
        for cand in (t_str, os.path.join(WALL_DIR, t_str)):
            if os.path.isfile(cand) and cand.lower().endswith(_IMAGE_EXTS):
                return cand
        for d in (t_str, os.path.join(WALL_DIR, t_str)):
            if os.path.isdir(d):
                found = []
                for base, dirs, files in os.walk(d, followlinks=True):
                    rel = os.path.relpath(base, d)
                    if (0 if rel == "." else len(rel.split(os.sep))) >= 1:
                        dirs[:] = []
                    for fn in files:
                        if fn.lower().endswith(_IMAGE_EXTS):
                            found.append(os.path.join(base, fn))
                if found:
                    found.sort()
                    return found[0]
    return ""


def _ensure_ascii_preview(src_img: str, asc_cfg: dict, cc_panel=None) -> str:
    ap = os.path.abspath(os.path.expanduser(str(src_img or "")))
    cached = get_cached_ascii_path(ap, asc_cfg, preview=True) or get_cached_ascii_path(ap, asc_cfg, preview=False)
    if cached:
        _LAST_ASCII_PREVIEW[ap] = cached
        return cached
    if os.environ.get("NC_UI_OFFSCREEN") == "1":
        try:
            out_p = render_ascii_image(ap, asc_cfg, preview=True)
            if out_p:
                _LAST_ASCII_PREVIEW[ap] = out_p
            return out_p
        except Exception:
            return ""
    ck = image_cache_key(ap, asc_cfg, preview=True)
    if ck not in _ASCII_PREVIEW_PENDING:
        _ASCII_PREVIEW_PENDING.add(ck)

        def _work():
            try:
                return render_ascii_image(ap, asc_cfg, preview=True)
            except Exception:
                return ""
            finally:
                _ASCII_PREVIEW_PENDING.discard(ck)

        def _done(path: str):
            if path and os.path.isfile(path):
                _LAST_ASCII_PREVIEW[ap] = path
                if cc_panel and cc_panel.shell:
                    cc_panel.shell.refresh_view()

        probe.run_async(_work, on_done=_done)
    prev = _LAST_ASCII_PREVIEW.get(ap, "")
    return prev if (prev and os.path.isfile(prev)) else ""


def _norm_asc_diff(src: dict) -> tuple:
    a = normalize_asciify_cfg(src.get("asciify"))
    if not a["enabled"]:
        return (False,)
    return (
        True,
        a["mode"],
        a["chars"],
        a["density"],
        a["tint"],
        a["scale"],
        a["contrast"],
        a["brightness"],
        a["invert"],
    )


def _get_editor_draft_spec() -> dict:
    wp_mode = str(STATE.get("desktop", "wp_mode", "scene"))
    if wp_mode == "scene":
        path = str(STATE.get("desktop", "scene_path", "") or "")
        fit = str(STATE.get("desktop", "scene_fit", "fill") or "fill")
        kind = detect_media_kind(path)
        yt_deliv = str(STATE.get("desktop", "yt_delivery", "stream") or "stream")
        img_p = extract_media_preview_frame(path)
        frms = extract_media_preview_frames(path)
        if not img_p and os.path.isdir(path):
            first_m = _find_media_file(path)
            if first_m:
                img_p = extract_media_preview_frame(first_m)
                frms = extract_media_preview_frames(first_m)
        if not img_p and kind in ("YT VIDEO", "YT PLAYLIST"):
            yt_s = _resolve_yt_source(path)
            if yt_s.get("ok"):
                img_p = extract_media_preview_frame(yt_s["canonical_url"])
                if img_p:
                    frms = [img_p]
        return {
            "mode": "scene" if path else "none",
            "target": path,
            "fit": fit,
            "align": _norm_align(STATE.get("desktop", "scene_align", "center")),
            "image_path": img_p,
            "frames": frms,
            "media_kind": kind,
            "yt_delivery": yt_deliv,
            "video_loop": str(STATE.get("desktop", "video_loop", "loop") or "loop"),
            "playlist_order": str(STATE.get("desktop", "playlist_order", "sequential") or "sequential"),
            "video_mute": bool(STATE.get("desktop", "video_mute", True)),
            "video_volume": int(STATE.get("desktop", "video_volume", 60) or 60),
            "asciify": get_editor_asciify_cfg() if (kind == "IMAGE" or not path) else dict(ASCIIFY_DEFAULTS),
        }
    if wp_mode == "slideshow":
        folder = str(STATE.get("desktop", "slide_folder", WALL_DIR) or WALL_DIR)
        first_media = _find_media_file(folder)
        return {
            "mode": "slideshow",
            "target": folder,
            "fit": str(STATE.get("desktop", "scene_fit", "fill") or "fill"),
            "interval": str(STATE.get("desktop", "slide_interval", "5m") or "5m"),
            "order": str(STATE.get("desktop", "slide_order", "shuffle") or "shuffle"),
            "trans": str(STATE.get("desktop", "slide_trans", "fade") or "fade"),
            "slide_speed": str(STATE.get("desktop", "slide_speed", "1.5s") or "1.5s"),
            "image_path": extract_media_preview_frame(first_media) if first_media else "",
            "frames": extract_media_preview_frames(first_media) if first_media else [],
            "asciify": get_editor_asciify_cfg(),
        }
    if wp_mode == "solid":
        hex_col = _resolve_solid_hex(STATE.get("desktop", "solid_hex", "#000000") or "#000000")
        return {
            "mode": "solid",
            "target": hex_col,
            "hex": hex_col,
            "bgcolor": hex_col.lstrip("#").lower(),
        }
    return {
        "mode": "ncmatrix",
        "charset": str(STATE.get("desktop", "cm_charset", "katakana") or "katakana"),
        "color": str(STATE.get("desktop", "cm_color", "green") or "green"),
        "speed": str(STATE.get("desktop", "cm_speed", "4") or "4"),
        "size": str(STATE.get("desktop", "cm_size", "9") or "9"),
        "cm_trans": str(STATE.get("desktop", "cm_trans", "flow") or "flow"),
    }


def _norm_spec_for_diff(src: dict) -> tuple:
    raw_mode = str(src.get("mode", "none") or "none").lower()
    mode = "ncmatrix" if raw_mode == "cmatrix" else ("scene" if raw_mode == "image" else raw_mode)
    target = str(src.get("target", "") or "").strip()
    if mode == "scene" and not target:
        mode = "none"
    if mode == "none":
        return ("none",)
    if mode == "scene":
        mk = str(src.get("media_kind") or detect_media_kind(target))
        fit_val = str(src.get("fit") or "fill")
        base = [
            "scene",
            target,
            fit_val,
        ]
        if fit_val == "fill":
            base.append(_norm_align(src.get("align", "center")))
        if mk in ("VIDEO", "YT VIDEO", "YT PLAYLIST") or target.startswith("http"):
            mute = bool(src.get("video_mute", True))
            base.extend([
                str(src.get("video_loop") or "loop"),
                mute,
                0 if mute else int(src.get("video_volume", 60) or 60),
            ])
        if mk in ("YT VIDEO", "YT PLAYLIST") or _resolve_yt_source(target).get("ok"):
            base.append(str(src.get("yt_delivery") or ("stream" if target.startswith("http") else "download")))
        if mk == "YT PLAYLIST":
            base.append(str(src.get("playlist_order") or "sequential"))
        if mk == "IMAGE":
            base.append(_norm_asc_diff(src))
        return tuple(base)
    if mode == "slideshow":
        shuf_val = str(src.get("shuffle", "true")).lower()
        order = str(src.get("order") or ("sequential" if shuf_val == "false" else "shuffle"))
        return (
            "slideshow",
            target,
            str(src.get("fit") or "fill"),
            str(src.get("interval") or "5m"),
            order,
            str(src.get("trans") or src.get("transition") or "fade"),
            str(src.get("slide_speed") or "1.5s"),
            _norm_asc_diff(src),
        )
    if mode == "ncmatrix":
        charset = str(src.get("charset") or ("katakana" if str(src.get("japanese", True)).lower() == "true" else "ascii"))
        return (
            "ncmatrix",
            charset,
            str(src.get("color") or "green"),
            str(src.get("speed") or "4"),
            str(src.get("size") or "9"),
            str(src.get("cm_trans") or src.get("transition") or "flow"),
        )
    if mode == "solid":
        return ("solid", _resolve_solid_hex(src.get("hex") or target))
    return (mode, target)


def _specs_differ(spec_a: dict, spec_b: dict) -> bool:
    return _norm_spec_for_diff(spec_a or {}) != _norm_spec_for_diff(spec_b or {})


def _read_live_src(out_name: str, live_cache: dict = None) -> dict:
    if live_cache is None:
        live_cache = STATE.get("desktop", "out_live", {})
        live_cache = live_cache if isinstance(live_cache, dict) else {}
    disk_file = os.path.join(NC_DESKTOP_BASE, "state", f"{out_name}.json")
    disk_mtime = 0.0
    disk_st = {}
    if os.path.exists(disk_file):
        try:
            disk_mtime = os.path.getmtime(disk_file)
            with open(disk_file, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                disk_st = loaded
        except Exception:
            disk_st = {}
    ov = live_cache.get(out_name) if isinstance(live_cache.get(out_name), dict) else {}
    ov_ts = float(ov.get("ts", 0.0) or 0.0) if ov else 0.0
    if disk_st.get("source_target"):
        disk_st = dict(disk_st)
        disk_st["rendered_target"] = str(disk_st.get("rendered_target") or disk_st.get("target") or "")
        disk_st["target"] = str(disk_st["source_target"])
        disk_st["asciify"] = normalize_asciify_cfg(disk_st.get("asciify"))
    elif (not disk_st.get("source_target")) and ov.get("rendered_target") and disk_st.get("target") == ov.get("rendered_target"):
        disk_st = dict(disk_st)
        disk_st["source_target"] = str(ov.get("source_target") or ov.get("target") or "")
        disk_st["rendered_target"] = str(ov.get("rendered_target") or "")
        disk_st["target"] = str(disk_st["source_target"])
        disk_st["asciify"] = normalize_asciify_cfg(ov.get("asciify"))
    return ov if (ov and ov_ts >= disk_mtime) else (disk_st or ov)


def _stage_current_to_selected() -> None:
    outs = sorted(_get_niri_outputs().keys())
    sel = get_selected_monitors(outs)
    staged = {
        k: v
        for k, v in (STATE.get("desktop", "staged_preview", {}) or {}).items()
        if k in sel
    }
    if not sel:
        STATE.set("desktop", "staged_preview", staged)
        return
    live_cache = STATE.get("desktop", "out_live", {})
    live_cache = live_cache if isinstance(live_cache, dict) else {}
    spec = _get_editor_draft_spec()
    now_ts = time.time()
    for out_n in sel:
        live_src = _read_live_src(out_n, live_cache)
        if _specs_differ(spec, live_src):
            ent = dict(spec)
            ent["ts"] = now_ts
            staged[out_n] = ent
        else:
            staged.pop(out_n, None)
    STATE.set("desktop", "staged_preview", staged)


def get_desktop_preview_state(outputs_data: dict, cc_panel=None) -> dict:
    """Resolve current [LIVE] or staged [PREVIEW *] desktop state per monitor."""
    staged_map = STATE.get("desktop", "staged_preview", {})
    staged_map = staged_map if isinstance(staged_map, dict) else {}
    live_cache = STATE.get("desktop", "out_live", {})
    live_cache = live_cache if isinstance(live_cache, dict) else {}

    result = {}
    for out_name in outputs_data:
        live_src = _read_live_src(out_name, live_cache)
        raw_staged = staged_map.get(out_name) if isinstance(staged_map.get(out_name), dict) else None
        is_staged = bool(raw_staged and _specs_differ(raw_staged, live_src))
        src = raw_staged if is_staged else live_src

        raw_mode = str(src.get("mode", "none") or "none").lower()
        mode = "ncmatrix" if raw_mode == "cmatrix" else ("scene" if raw_mode == "image" else raw_mode)
        target = str(src.get("target", "") or "").strip()
        cur_img = str(src.get("current_image", "") or "")
        if mode == "scene" and not target and not cur_img and not str(src.get("image_path", "") or ""):
            mode = "none"

        img_path, frames = "", []
        media_kind = str(detect_media_kind(target) or src.get("media_kind") or "")
        asc = normalize_asciify_cfg(src.get("asciify"))
        if mode in ("scene", "slideshow"):
            yt_src = _resolve_yt_source(target) if mode == "scene" else {"ok": False}
            if yt_src["ok"] and (target.startswith("http") or not os.path.exists(os.path.expanduser(target))):
                cand_img = str(src.get("image_path", "") or "")
                img_path = cand_img if (cand_img and os.path.isfile(cand_img)) else extract_media_preview_frame(yt_src["canonical_url"])
                frames = [img_p for img_p in (img_path,) if img_p]
            else:
                media_src = _find_media_file(target, cur_img)
                cand_img = str(src.get("image_path", "") or "")
                img_path = cand_img if (cand_img and os.path.isfile(cand_img)) else (extract_media_preview_frame(media_src) if media_src else "")
                if media_src:
                    frames = extract_media_preview_frames(media_src)
                if not frames and img_path:
                    frames = [img_path]

            if asc["enabled"] and is_pillow_available():
                cur_rendered = ""
                if not is_staged and mode == "slideshow" and src.get("rendered_target") and cur_img:
                    cand_r = os.path.join(str(src["rendered_target"]), cur_img)
                    if os.path.isfile(cand_r):
                        cur_rendered = cand_r
                if cur_rendered:
                    img_path = cur_rendered
                    frames = [cur_rendered]
                else:
                    prev_src = _find_static_image_file(target, cur_img) if (mode == "slideshow" or media_kind == "IMAGE") else ""
                    if prev_src:
                        asc_prev = _ensure_ascii_preview(prev_src, asc, cc_panel=cc_panel)
                        img_path = asc_prev
                        frames = [asc_prev] if asc_prev else []

        hex_col = _resolve_solid_hex(src.get("hex") or target) if mode == "solid" else ""
        charset = str(src.get("charset") or ("katakana" if str(src.get("japanese", True)).lower() == "true" else "ascii"))
        shuf_val = str(src.get("shuffle", "true")).lower()
        order = str(src.get("order") or ("sequential" if shuf_val == "false" else "shuffle"))
        yt_deliv = str(src.get("yt_delivery") or ("stream" if target.startswith("http") else "download"))
        result[out_name] = {
            "mode": mode,
            "staged": is_staged,
            "target": target,
            "fit": str(src.get("fit") or "fill"),
            "align": _norm_align(src.get("align", "center")),
            "image_path": img_path,
            "frames": frames,
            "media_kind": media_kind,
            "yt_delivery": yt_deliv,
            "video_loop": str(src.get("video_loop") or "loop"),
            "playlist_order": str(src.get("playlist_order") or "sequential"),
            "hex": hex_col,
            "color": str(src.get("color") or "green"),
            "speed": str(src.get("speed") or "4"),
            "size": str(src.get("size") or "9"),
            "cm_trans": str(src.get("cm_trans") or src.get("transition") or "flow"),
            "charset": charset,
            "interval": str(src.get("interval") or "5m"),
            "order": order,
            "trans": str(src.get("trans") or src.get("transition") or "fade"),
            "slide_speed": str(src.get("slide_speed") or "1.5s"),
            "video_mute": bool(src.get("video_mute", True)),
            "video_volume": int(src.get("video_volume", 60) or 60),
            "asciify": asc,
        }
    return result


def sync_editor_from_live_monitors(outputs_data: dict = None) -> None:
    """Sync Card 2 operating mode & options to selected monitor's preview/live state."""
    if _WP_USER_TOUCHED:
        return
    if outputs_data is None:
        outputs_data = _get_niri_outputs()
    outs = sorted(outputs_data.keys())
    if not outs:
        return
    live_map = get_desktop_preview_state(outputs_data)
    sel = get_selected_monitors(outs)
    cands = [m for m in sel if m in live_map and live_map[m].get("mode") in ("scene", "slideshow", "ncmatrix", "cmatrix", "solid")]
    if not cands:
        cands = [m for m in outs if m in live_map and live_map[m].get("mode") in ("scene", "slideshow", "ncmatrix", "cmatrix", "solid")]
    if not cands:
        return
    st = live_map[cands[0]]
    m = "ncmatrix" if st["mode"] == "cmatrix" else st["mode"]
    updates = {"wp_mode": m}
    if m == "scene":
        if st.get("target"):
            updates["scene_path"] = st["target"]
        updates["scene_fit"] = st.get("fit") or "fill"
        updates["scene_align"] = _norm_align(st.get("align", "center"))
        updates["yt_delivery"] = st.get("yt_delivery") or ("stream" if str(st.get("target", "")).startswith("http") else "download")
        updates["video_loop"] = st.get("video_loop") or "loop"
        updates["playlist_order"] = st.get("playlist_order") or "sequential"
        updates["video_mute"] = bool(st.get("video_mute", True))
        updates["video_volume"] = int(st.get("video_volume", 60) or 60)
        asc = normalize_asciify_cfg(st.get("asciify"))
        for ak in ("enabled", "mode", "chars", "density", "tint", "scale", "contrast", "brightness", "invert"):
            updates[f"ascii_{ak}"] = asc[ak]
    elif m == "slideshow":
        if st.get("target"):
            updates["slide_folder"] = st["target"]
        updates["slide_interval"] = st.get("interval") or "5m"
        updates["slide_order"] = st.get("order") or "shuffle"
        updates["slide_trans"] = st.get("trans") or "fade"
        updates["slide_speed"] = st.get("slide_speed") or "1.5s"
        asc = normalize_asciify_cfg(st.get("asciify"))
        for ak in ("enabled", "mode", "chars", "density", "tint", "scale", "contrast", "brightness", "invert"):
            updates[f"ascii_{ak}"] = asc[ak]
    elif m == "solid":
        updates["solid_hex"] = _resolve_solid_hex(st.get("hex") or st.get("target") or "#000000")
    elif m == "ncmatrix":
        updates["cm_charset"] = st.get("charset") or "katakana"
        updates["cm_color"] = st.get("color") or "green"
        updates["cm_speed"] = str(st.get("speed") or "4")
        updates["cm_size"] = str(st.get("size") or "9")
        updates["cm_trans"] = str(st.get("cm_trans") or "flow")

    ns = STATE.get_namespace("desktop")
    for k, v in updates.items():
        if ns.get(k) != v:
            STATE.set("desktop", k, v)


_XTERM_ANSI_16 = (
    (26, 28, 35), (255, 33, 63), (0, 255, 115), (255, 235, 25),
    (61, 130, 246), (187, 154, 247), (0, 240, 255), (205, 214, 244),
    (95, 101, 123), (255, 92, 119), (79, 255, 158), (255, 242, 117),
    (122, 162, 247), (210, 184, 255), (125, 249, 255), (255, 255, 255),
)


def _build_xterm_256() -> tuple:
    pal = [(r / 255.0, g / 255.0, b / 255.0) for r, g, b in _XTERM_ANSI_16]
    steps = (0, 95, 135, 175, 215, 255)
    for r in range(6):
        for g in range(6):
            for b in range(6):
                pal.append((steps[r] / 255.0, steps[g] / 255.0, steps[b] / 255.0))
    for i in range(24):
        v = (8 + i * 10) / 255.0
        pal.append((v, v, v))
    return tuple(pal)


_XTERM_256 = _build_xterm_256()
_YAZI_SESSION = None
STATE.set("desktop", "yazi_open", False)


def _yazi_child_preexec() -> None:
    try:
        ctypes.CDLL("libc.so.6").prctl(1, signal.SIGKILL)
    except Exception:
        pass


_YAZI_CFG_DIR = "/tmp/nc_yazi_config"


def _write_yazi_ncui_theme() -> str:
    p = get_semantic_palette()
    os.makedirs(_YAZI_CFG_DIR, exist_ok=True)
    toml_txt = f'''# Auto-generated by NC-UI from active 7-axis semantic palette
[mgr]
cwd = {{ fg = "{p['intent']}", bold = true }}
hovered = {{ fg = "{p['intent']}", bg = "{p['intent_bg']}", bold = true }}
preview_hovered = {{ fg = "{p['focus_val']}", bg = "{p['structure_bg']}" }}
find_keyword = {{ fg = "{p['attention']}", bold = true, underline = true }}
find_position = {{ fg = "{p['interrupt']}", bg = "{p['interrupt_bg']}", bold = true }}
marker_copied = {{ fg = "{p['vitality']}", bg = "{p['vitality_bg']}" }}
marker_cut = {{ fg = "{p['fault']}", bg = "{p['fault_bg']}" }}
marker_marked = {{ fg = "{p['intent']}", bg = "{p['intent_bg']}" }}
marker_selected = {{ fg = "{p['attention']}", bg = "{p['attention_bg']}" }}
count_copied = {{ fg = "{p['vitality']}", bg = "{p['vitality_bg']}" }}
count_cut = {{ fg = "{p['fault']}", bg = "{p['fault_bg']}" }}
count_selected = {{ fg = "{p['attention']}", bg = "{p['attention_bg']}" }}
border_symbol = "│"
border_style = {{ fg = "{p['structure']}" }}

[indicator]
parent  = {{ fg = "{p['focus_val']}", bg = "{p['structure_bg']}" }}
current = {{ fg = "{p['intent']}", bg = "{p['intent_bg']}", bold = true }}
preview = {{ fg = "{p['schema']}", bg = "{p['structure_bg']}" }}
padding = {{ open = " ", close = " " }}

[mode]
normal_main = {{ fg = "{p['intent']}", bg = "{p['intent_bg']}", bold = true }}
normal_alt  = {{ fg = "{p['schema']}", bg = "{p['structure_bg']}" }}
select_main = {{ fg = "{p['attention']}", bg = "{p['attention_bg']}", bold = true }}
select_alt  = {{ fg = "{p['schema']}", bg = "{p['structure_bg']}" }}
unset_main  = {{ fg = "{p['fault']}", bg = "{p['fault_bg']}", bold = true }}
unset_alt   = {{ fg = "{p['schema']}", bg = "{p['structure_bg']}" }}

[status]
overall   = {{ fg = "{p['schema']}" }}
sep_left  = {{ open = "", close = "" }}
sep_right = {{ open = "", close = "" }}
perm_type = {{ fg = "{p['intent']}" }}
perm_read = {{ fg = "{p['vitality']}" }}
perm_write = {{ fg = "{p['attention']}" }}
perm_exec = {{ fg = "{p['fault']}" }}
perm_sep = {{ fg = "{p['structure']}" }}
progress_label  = {{ fg = "{p['intent']}", bold = true }}
progress_normal = {{ fg = "{p['intent']}", bg = "{p['structure_bg']}" }}
progress_error  = {{ fg = "{p['fault']}", bg = "{p['fault_bg']}" }}

[filetype]
rules = [
  {{ mime = "image/*", fg = "{p['vitality']}" }},
  {{ mime = "{{audio,video}}/*", fg = "{p['intent']}" }},
  {{ mime = "application/{{zip,rar,7z*,tar,gzip,xz,zstd,bzip*,lzma,compress,archive,cpio,arj,xar,ms-cab*}}", fg = "{p['attention']}" }},
  {{ url = "*/", fg = "{p['schema']}", bold = true }},
  {{ url = "*", fg = "{p['focus_val']}" }},
]
'''
    with open(os.path.join(_YAZI_CFG_DIR, "theme.toml"), "w", encoding="utf-8") as f:
        f.write(toml_txt)
    return _YAZI_CFG_DIR


class EmbeddedYaziSession:
    """In-process PTY + ANSI/Kitty-graphics terminal session running Yazi inside an nc-box."""

    def __init__(self, yazi_bin: str, start_dir: str, is_folder: bool, cc_panel, rows: int = 18, cols: int = 92, target_mode: str = "wallpaper"):
        self.yazi_bin = yazi_bin
        self.start_dir = start_dir
        self.is_folder = bool(is_folder)
        self.target_mode = target_mode
        self.cc_panel = cc_panel
        self.rows = max(8, int(rows))
        self.cols = max(40, int(cols))
        self.cell_w = 8
        self.cell_h = 16
        self.lock = threading.Lock()
        self.grid = self._make_grid(self.rows, self.cols)
        self.cur_r = 0
        self.cur_c = 0
        self._saved_cur = (0, 0, None, None, False, False)
        self.fg = None
        self.bg = None
        self.bold = False
        self.rev = False
        self._last_was_img_ph = False
        self._rx_buf = bytearray()
        self._img_meta = {}
        self._img_acc = []
        self.preview_pixbuf = None
        self.master_fd = -1
        self.proc = None
        self.alive = False
        self.cancelled = False
        self.on_redraw = None
        self._redraw_pending = False

    @staticmethod
    def _make_grid(rows: int, cols: int) -> list:
        return [[[" ", None, None, False, False, False] for _ in range(cols)] for _ in range(rows)]

    def start(self) -> None:
        master, slave = pty.openpty()
        self.master_fd = master
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", self.rows, self.cols, self.cols * self.cell_w, self.rows * self.cell_h))
        env = os.environ.copy()
        for k in list(env.keys()):
            if any(p in k for p in ("KITTY", "GHOSTTY", "WEZTERM", "ITERM", "TERM_PROGRAM", "KONSOLE", "ALACRITTY")):
                env.pop(k, None)
        env["TERM"] = "xterm-kitty"
        env["KITTY_WINDOW_ID"] = "1"
        env["COLORTERM"] = "truecolor"
        env["YAZI_CONFIG_HOME"] = _write_yazi_ncui_theme()
        flag = "--cwd-file" if self.is_folder else "--chooser-file"
        try:
            self.proc = subprocess.Popen(
                [self.yazi_bin, f"{flag}={_YAZI_PICK_FILE}", self.start_dir],
                stdin=slave,
                stdout=slave,
                stderr=slave,
                close_fds=True,
                env=env,
                start_new_session=True,
                preexec_fn=_yazi_child_preexec,
            )
        finally:
            os.close(slave)
        self.alive = True
        threading.Thread(target=self._read_loop, daemon=True).start()

    def resize(self, rows: int, cols: int, cell_w: int = 8, cell_h: int = 16) -> None:
        nr, nc = max(8, int(rows)), max(40, int(cols))
        cw, ch = max(4, int(cell_w)), max(8, int(cell_h))
        changed = (nr != self.rows or nc != self.cols or cw != self.cell_w or ch != self.cell_h)
        if not changed:
            return
        with self.lock:
            new_g = self._make_grid(nr, nc)
            for r in range(min(nr, self.rows)):
                for c in range(min(nc, self.cols)):
                    new_g[r][c] = self.grid[r][c]
            self.grid = new_g
            self.rows, self.cols, self.cell_w, self.cell_h = nr, nc, cw, ch
            self.cur_r = min(self.cur_r, nr - 1)
            self.cur_c = min(self.cur_c, nc - 1)
        if self.master_fd >= 0 and self.alive:
            try:
                fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, struct.pack("HHHH", nr, nc, nc * cw, nr * ch))
                if self.proc and self.proc.poll() is None:
                    os.killpg(os.getpgid(self.proc.pid), signal.SIGWINCH)
            except Exception:
                pass

    def send_bytes(self, data: bytes) -> None:
        if self.master_fd >= 0 and self.alive and data:
            try:
                os.write(self.master_fd, data)
            except OSError:
                pass

    def _request_redraw(self) -> None:
        if not self._redraw_pending:
            self._redraw_pending = True
            GLib.idle_add(self._emit_redraw)

    def _emit_redraw(self) -> bool:
        self._redraw_pending = False
        cb = self.on_redraw
        if cb and self.alive:
            cb()
        return False

    def _read_loop(self) -> None:
        fd = self.master_fd
        try:
            while self.alive and fd >= 0:
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
                    self._rx_buf.extend(chunk)
                    self._process_rx()
                self._request_redraw()
        finally:
            if self.alive and not self.cancelled:
                GLib.idle_add(self._on_process_exit)

    def _put_char(self, ch: str) -> None:
        if ch == "\U0010eeee":
            if 0 <= self.cur_r < self.rows and 0 <= self.cur_c < self.cols:
                self.grid[self.cur_r][self.cur_c] = [" ", None, None, False, False, True]
                self.cur_c += 1
            self._last_was_img_ph = True
            return
        o = ord(ch)
        if unicodedata.combining(ch) > 0 or 0xFE00 <= o <= 0xFE0F or 0x1AB0 <= o <= 0x1AFF:
            return
        self._last_was_img_ph = False
        if 0 <= self.cur_r < self.rows and 0 <= self.cur_c < self.cols:
            w = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
            self.grid[self.cur_r][self.cur_c] = [ch, self.fg, self.bg, self.bold, self.rev, False]
            if w == 2 and self.cur_c + 1 < self.cols:
                self.grid[self.cur_r][self.cur_c + 1] = ["", self.fg, self.bg, self.bold, self.rev, False]
            self.cur_c += w

    def _apply_sgr(self, raw_params: str) -> None:
        if not raw_params:
            vals = [0]
        else:
            norm = raw_params.replace("::", ":").replace(":", ";")
            vals = [int(p) if p.isdigit() else 0 for p in norm.split(";")]
        i = 0
        while i < len(vals):
            p = vals[i]
            if p == 0:
                self.fg = self.bg = None
                self.bold = self.rev = False
            elif p == 1:
                self.bold = True
            elif p in (2, 22):
                self.bold = False
            elif p == 7:
                self.rev = True
            elif p == 27:
                self.rev = False
            elif 30 <= p <= 37:
                self.fg = _XTERM_256[p - 30]
            elif p == 39:
                self.fg = None
            elif 40 <= p <= 47:
                self.bg = _XTERM_256[p - 40]
            elif p == 49:
                self.bg = None
            elif 90 <= p <= 97:
                self.fg = _XTERM_256[p - 90 + 8]
            elif 100 <= p <= 107:
                self.bg = _XTERM_256[p - 100 + 8]
            elif p in (38, 48) and i + 1 < len(vals):
                is_fg = (p == 38)
                mode = vals[i + 1]
                col = None
                if mode == 5 and i + 2 < len(vals):
                    col = _XTERM_256[max(0, min(255, vals[i + 2]))]
                    i += 2
                elif mode == 2 and i + 4 < len(vals):
                    col = (
                        max(0, min(255, vals[i + 2])) / 255.0,
                        max(0, min(255, vals[i + 3])) / 255.0,
                        max(0, min(255, vals[i + 4])) / 255.0,
                    )
                    i += 4
                if col is not None:
                    if is_fg:
                        self.fg = col
                    else:
                        self.bg = col
            i += 1

    def _erase_cell(self, r: int, c: int) -> None:
        self.grid[r][c] = [" ", self.fg, self.bg, False, self.rev, False]

    def _handle_csi(self, seq_str: str, final: str) -> None:
        priv = ""
        if seq_str and seq_str[0] in ("?", ">", "<", "="):
            priv, seq_str = seq_str[0], seq_str[1:]
        nums = [int(p) if p.isdigit() else 0 for p in seq_str.replace(":", ";").split(";")] if seq_str else []
        p1 = nums[0] if len(nums) >= 1 and nums[0] > 0 else 1
        p2 = nums[1] if len(nums) >= 2 and nums[1] > 0 else 1

        if priv == "?" and final in ("h", "l"):
            if "1049" in seq_str or "47" in seq_str:
                self.grid = self._make_grid(self.rows, self.cols)
                self.cur_r = self.cur_c = 0
                if final == "l":
                    self.preview_pixbuf = None
            return
        if priv:
            return
        if final in ("H", "f"):
            self.cur_r = max(0, min(self.rows - 1, p1 - 1))
            self.cur_c = max(0, min(self.cols - 1, p2 - 1))
        elif final == "A":
            self.cur_r = max(0, self.cur_r - p1)
        elif final == "B":
            self.cur_r = min(self.rows - 1, self.cur_r + p1)
        elif final == "C":
            self.cur_c = min(self.cols - 1, self.cur_c + p1)
        elif final == "D":
            self.cur_c = max(0, self.cur_c - p1)
        elif final == "G":
            self.cur_c = max(0, min(self.cols - 1, p1 - 1))
        elif final == "d":
            self.cur_r = max(0, min(self.rows - 1, p1 - 1))
        elif final == "J":
            m = nums[0] if nums else 0
            if m == 2:
                self.grid = self._make_grid(self.rows, self.cols)
            elif m == 0:
                for c in range(self.cur_c, self.cols):
                    self._erase_cell(self.cur_r, c)
                for r in range(self.cur_r + 1, self.rows):
                    for c in range(self.cols):
                        self._erase_cell(r, c)
        elif final == "K":
            m = nums[0] if nums else 0
            rng = range(self.cur_c, self.cols) if m == 0 else (range(0, self.cur_c + 1) if m == 1 else range(self.cols))
            for c in rng:
                if 0 <= self.cur_r < self.rows:
                    self._erase_cell(self.cur_r, c)
        elif final == "m":
            self._apply_sgr(seq_str)
        elif final == "s":
            self._saved_cur = (self.cur_r, self.cur_c, self.fg, self.bg, self.bold, self.rev)
        elif final == "u":
            self.cur_r, self.cur_c, self.fg, self.bg, self.bold, self.rev = self._saved_cur
        elif final == "c":
            self.send_bytes(b"\x1b[?62;c")
        elif final == "n" and nums and nums[0] == 6:
            self.send_bytes(f"\x1b[{self.cur_r + 1};{self.cur_c + 1}R".encode("ascii"))
        elif final == "t" and nums:
            if nums[0] == 16:
                self.send_bytes(f"\x1b[6;{self.cell_h};{self.cell_w}t".encode("ascii"))
            elif nums[0] == 14:
                self.send_bytes(f"\x1b[4;{self.rows * self.cell_h};{self.cols * self.cell_w}t".encode("ascii"))

    def _handle_apc(self, body: bytes) -> None:
        if not body.startswith(b"G"):
            return
        rest = body[1:]
        if b";" in rest:
            hdr_b, payload_b = rest.split(b";", 1)
        else:
            hdr_b, payload_b = rest, b""
        kv = {}
        for item in hdr_b.decode("ascii", "ignore").split(","):
            if "=" in item:
                k, v = item.split("=", 1)
                kv[k] = v
        act = kv.get("a", "")
        if act == "q" and kv.get("t") == "d" and "i" in kv:
            self.send_bytes(f"\x1b_Gi={kv['i']};OK\x1b\\".encode("ascii"))
            return
        if act == "d":
            self.preview_pixbuf = None
            self._img_acc = []
            self._img_meta = {}
            return
        if act in ("T", "t"):
            self._img_meta = kv
            self._img_acc = [payload_b]
        elif self._img_acc:
            self._img_meta.update(kv)
            self._img_acc.append(payload_b)
        else:
            return
        if kv.get("m", "0") == "0":
            try:
                raw = base64.b64decode(b"".join(self._img_acc))
                fmt = self._img_meta.get("f", "32")
                if fmt == "100":
                    ldr = GdkPixbuf.PixbufLoader.new_with_type("png")
                    ldr.write(raw)
                    ldr.close()
                    self.preview_pixbuf = ldr.get_pixbuf()
                elif fmt in ("24", "32"):
                    sw = int(self._img_meta.get("s", 0))
                    sh = int(self._img_meta.get("v", 0))
                    has_a = (fmt == "32")
                    bpp = 4 if has_a else 3
                    if sw > 0 and sh > 0 and len(raw) >= sw * sh * bpp:
                        self.preview_pixbuf = GdkPixbuf.Pixbuf.new_from_bytes(
                            GLib.Bytes.new(raw[: sw * sh * bpp]),
                            GdkPixbuf.Colorspace.RGB,
                            has_a,
                            8,
                            sw,
                            sh,
                            sw * bpp,
                        )
            except Exception:
                pass
            self._img_acc = []
            self._img_meta = {}

    def _process_rx(self) -> None:
        buf = self._rx_buf
        n = len(buf)
        idx = 0
        while idx < n:
            b = buf[idx]
            if b == 0x1B:
                if idx + 1 >= n:
                    break
                nxt = buf[idx + 1]
                if nxt == ord("["):
                    j = idx + 2
                    while j < n and not (0x40 <= buf[j] <= 0x7E):
                        j += 1
                    if j >= n:
                        break
                    self._handle_csi(buf[idx + 2 : j].decode("ascii", "ignore"), chr(buf[j]))
                    idx = j + 1
                elif nxt in (ord("_"), ord("P")):
                    end = buf.find(b"\x1b\\", idx + 2)
                    if end < 0:
                        if n - idx > 8 * 1024 * 1024:
                            idx = n
                        break
                    if nxt == ord("_"):
                        self._handle_apc(bytes(buf[idx + 2 : end]))
                    idx = end + 2
                elif nxt == ord("]"):
                    bel = buf.find(0x07, idx + 2)
                    st = buf.find(b"\x1b\\", idx + 2)
                    ends = [e for e in (bel, st) if e >= 0]
                    if not ends:
                        break
                    end = min(ends)
                    osc = bytes(buf[idx + 2 : end])
                    if osc.startswith(b"11;?"):
                        shx = str(get_surface_bg_hex() or "#06070c").strip().lstrip("#")
                        if len(shx) == 6:
                            reply = f"\x1b]11;rgb:{shx[0:2]*2}/{shx[2:4]*2}/{shx[4:6]*2}\x07".encode("ascii")
                        else:
                            reply = b"\x1b]11;rgb:0606/0707/0c0c\x07"
                        self.send_bytes(reply)
                    idx = end + (1 if end == bel else 2)
                elif nxt == ord("7"):
                    self._saved_cur = (self.cur_r, self.cur_c, self.fg, self.bg, self.bold, self.rev)
                    idx += 2
                elif nxt == ord("8"):
                    self.cur_r, self.cur_c, self.fg, self.bg, self.bold, self.rev = self._saved_cur
                    idx += 2
                else:
                    idx += 2
            elif b < 0x20:
                if b == 0x0D:
                    self.cur_c = 0
                elif b == 0x0A:
                    if self.cur_r + 1 < self.rows:
                        self.cur_r += 1
                elif b == 0x08:
                    self.cur_c = max(0, self.cur_c - 1)
                elif b == 0x09:
                    self.cur_c = min(self.cols - 1, ((self.cur_c // 8) + 1) * 8)
                idx += 1
            else:
                # Consume plain run up to next control/ESC char cleanly (preserving trailing partial UTF-8)
                end = idx
                while end < n and buf[end] >= 0x20:
                    end += 1
                raw_slice = bytes(buf[idx:end])
                try:
                    txt = raw_slice.decode("utf-8")
                    consumed = len(raw_slice)
                except UnicodeDecodeError as e:
                    if e.end == len(raw_slice) and len(raw_slice) - e.start < 4:
                        txt = raw_slice[: e.start].decode("utf-8", "replace")
                        consumed = e.start
                        for ch in txt:
                            self._put_char(ch)
                        idx += consumed
                        break
                    txt = raw_slice.decode("utf-8", "replace")
                    consumed = len(raw_slice)
                for ch in txt:
                    self._put_char(ch)
                idx += consumed
        if idx > 0:
            del self._rx_buf[:idx]

    def _on_process_exit(self) -> bool:
        global _WP_USER_TOUCHED, _YAZI_SESSION
        if self.cancelled:
            return False
        self.alive = False
        picked = ""
        if os.path.isfile(_YAZI_PICK_FILE):
            try:
                raw = open(_YAZI_PICK_FILE, "r", encoding="utf-8", errors="ignore").read().strip()
                if raw:
                    picked = raw.splitlines()[0].strip()
            except OSError:
                pass
        panel = self.cc_panel
        is_dir = self.is_folder
        t_mode = self.target_mode
        _close_embedded_yazi(cc_panel=None, cancelled=True)
        if picked and os.path.exists(picked):
            if t_mode == "cursor_zip" and os.path.isfile(picked):
                ok_c, msg_c, _name = convert_windows_cursor_zip(picked)
                _set_pers_fb(ok_c, msg_c)
            else:
                _cancel_pending_yt_dl()
                valid = (is_dir and os.path.isdir(picked)) or ((not is_dir) and os.path.isfile(picked))
                if valid:
                    STATE.set("desktop", "wp_mode", "slideshow" if is_dir else "scene")
                    STATE.set("desktop", "slide_folder" if is_dir else "scene_path", picked)
                    _WP_USER_TOUCHED = True
                    _stage_current_to_selected()
                    _set_pers_fb(True, t("cc_personalization.wp_staged_scene_msg", p0=os.path.basename(picked.rstrip("/")) or picked))
        if panel and panel.shell:
            panel.shell.refresh_view()
        return False

    def terminate(self) -> None:
        self.cancelled = True
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
                for _ in range(5):
                    if proc.poll() is not None:
                        break
                    time.sleep(0.03)
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


def get_active_yazi_session():
    return _YAZI_SESSION


def _close_embedded_yazi(cc_panel=None, cancelled: bool = True) -> None:
    global _YAZI_SESSION
    sess = _YAZI_SESSION
    _YAZI_SESSION = None
    if sess is not None:
        if cancelled:
            sess.cancelled = True
        sess.terminate()
    try:
        if os.path.exists(_YAZI_PICK_FILE):
            os.remove(_YAZI_PICK_FILE)
    except OSError:
        pass
    STATE.set("desktop", "yazi_open", False)
    if cc_panel and cc_panel.shell:
        cc_panel.shell.refresh_view()


atexit.register(lambda: _close_embedded_yazi(cancelled=True))


def handle_cc_personalization_key_press(cc_panel, event) -> bool:
    sess = _YAZI_SESSION
    if not STATE.get("desktop", "yazi_open") or sess is None or not sess.alive:
        return False
    kv = event.keyval
    state = event.state
    is_ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
    is_alt = bool(state & Gdk.ModifierType.MOD1_MASK)

    special = {
        Gdk.KEY_Up: b"\x1b[A",
        Gdk.KEY_KP_Up: b"\x1b[A",
        Gdk.KEY_Down: b"\x1b[B",
        Gdk.KEY_KP_Down: b"\x1b[B",
        Gdk.KEY_Right: b"\x1b[C",
        Gdk.KEY_KP_Right: b"\x1b[C",
        Gdk.KEY_Left: b"\x1b[D",
        Gdk.KEY_KP_Left: b"\x1b[D",
        Gdk.KEY_Return: b"\r",
        Gdk.KEY_KP_Enter: b"\r",
        Gdk.KEY_BackSpace: b"\x7f",
        Gdk.KEY_Delete: b"\x1b[3~",
        Gdk.KEY_KP_Delete: b"\x1b[3~",
        Gdk.KEY_Tab: b"\t",
        Gdk.KEY_KP_Tab: b"\t",
        Gdk.KEY_ISO_Left_Tab: b"\x1b[Z",
        Gdk.KEY_Home: b"\x1b[H",
        Gdk.KEY_KP_Home: b"\x1b[H",
        Gdk.KEY_End: b"\x1b[F",
        Gdk.KEY_KP_End: b"\x1b[F",
        Gdk.KEY_Page_Up: b"\x1b[5~",
        Gdk.KEY_KP_Page_Up: b"\x1b[5~",
        Gdk.KEY_Page_Down: b"\x1b[6~",
        Gdk.KEY_KP_Page_Down: b"\x1b[6~",
        Gdk.KEY_Escape: b"\x1b",
    }
    seq = special.get(kv)
    if seq is not None:
        sess.send_bytes(seq)
        return True

    uc = Gdk.keyval_to_unicode(kv)
    if is_ctrl and 1 <= uc <= 127:
        ch = chr(uc).lower()
        if "a" <= ch <= "z":
            sess.send_bytes(bytes([ord(ch) - 96]))
            return True
    if uc >= 32:
        data = chr(uc).encode("utf-8")
        sess.send_bytes((b"\x1b" + data) if is_alt else data)
        return True
    return False


def _launch_yazi_picker(cc_panel, is_folder: bool, target_mode: str = "wallpaper") -> bool:
    global _YAZI_SESSION
    yazi_bin = "yazi" if probe.has_bin("yazi") else ("yuzi" if probe.has_bin("yuzi") else "")
    if not yazi_bin:
        return False
    _close_embedded_yazi(cancelled=True)
    if target_mode == "cursor_zip":
        dl_dir = os.path.expanduser("~/Downloads")
        start_p = dl_dir if os.path.isdir(dl_dir) else os.path.expanduser("~")
    else:
        cur_raw = STATE.get("desktop", "slide_folder" if is_folder else "scene_path", WALL_DIR)
        start_p = os.path.expanduser(str(cur_raw or WALL_DIR))
        if not is_folder and os.path.isfile(start_p):
            start_p = os.path.dirname(start_p)
        if not os.path.isdir(start_p):
            start_p = WALL_DIR if os.path.isdir(WALL_DIR) else (_YT_WALL_DIR if os.path.isdir(_YT_WALL_DIR) else os.path.expanduser("~"))

    cols = 92
    if cc_panel and cc_panel.shell and hasattr(cc_panel.shell, "content_canvas"):
        cols = max(60, int(cc_panel.shell.content_canvas.inner_cols) - 4)
    sess = EmbeddedYaziSession(yazi_bin, start_p, is_folder=is_folder, cc_panel=cc_panel, rows=18, cols=cols, target_mode=target_mode)
    try:
        sess.start()
    except Exception:
        return False
    _YAZI_SESSION = sess
    STATE.set("desktop", "yazi_open", True)
    STATE.set("desktop", "yazi_is_folder", bool(is_folder))
    fb_key = "cc_personalization.cur_yazi_opened" if target_mode == "cursor_zip" else ("cc_personalization.wp_yazi_folder_opened" if is_folder else "cc_personalization.wp_yazi_opened")
    _set_pers_fb(True, t(fb_key))
    return True


def _cancel_pending_yt_dl() -> None:
    global _YT_DL_SEQ
    _YT_DL_SEQ += 1
    if STATE.get("desktop", "yt_status"):
        STATE.set("desktop", "yt_status", "")


def _wallpaper_pid_files(mon: str) -> tuple[str, ...]:
    return (
        f"slideshow_{mon}.pid",
        f"video_{mon}.pid",
        f"ncmatrix_{mon}.pid",
        f"cmatrix_{mon}.pid",
        f"solid_{mon}.pid",
    )


def _collect_output_wallpaper_pids(mon: str) -> list[int]:
    pids: set[int] = set()
    for pf_name in _wallpaper_pid_files(mon):
        pid_f = os.path.join(NC_DESKTOP_BASE, "pids", pf_name)
        if not os.path.isfile(pid_f):
            continue
        try:
            pid = int(open(pid_f, "r", encoding="utf-8").read().strip())
            if pid > 0:
                os.kill(pid, 0)
                pids.add(pid)
        except Exception:
            pass
    if probe.has_bin("pgrep"):
        for pat in (
            f"^([^ ]*/)?ncmatrix .*--output[= ]{mon}( |$)",
            f"^([^ ]*/)?swaybg .*-o[= ]{mon}( |$)",
            f"^([^ ]*/)?(python[^ ]* |env python[^ ]* )?([^ ]*/)?nc-video-wallpaper.*--output[= ]{mon}( |$)",
            f"^([^ ]*/)?ffplay .*-window_title nc-vidwp-audio-{mon}( |$)",
            f"^([^ ]*/)?pacat .*nc-vidwp-audio-{mon}( |$)",
            f"^([^ ]*/)?mpvpaper .*( |^){mon}( |$)",
        ):
            ok_p, out_p = probe.run_cmd(["pgrep", "-f", pat], timeout=1.0)
            if ok_p and out_p:
                for ln in out_p.splitlines():
                    try:
                        p = int(ln.strip())
                        if p > 0 and p != os.getpid():
                            pids.add(p)
                    except ValueError:
                        pass
    return sorted(pids)


def _get_running_ncmatrix_pid(mon: str) -> int:
    pid_f = os.path.join(NC_DESKTOP_BASE, "pids", f"ncmatrix_{mon}.pid")
    if not os.path.isfile(pid_f):
        return 0
    try:
        pid = int(open(pid_f, "r", encoding="utf-8").read().strip())
        if pid <= 0:
            return 0
        os.kill(pid, 0)
        with open(f"/proc/{pid}/cmdline", "rb") as cf:
            cmd = cf.read().replace(b"\x00", b" ").decode("utf-8", errors="ignore")
        if "ncmatrix" in cmd and f"--output {mon}" in cmd:
            return pid
    except Exception:
        pass
    return 0


def _spawn_bg_pid(cmd: list) -> int:
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return int(proc.pid or 0)
    except Exception:
        return 0


def _kill_pids_after_overlap(old_pids: list[int], new_pid: int = 0, delay: float = 0.30, remove_pf: tuple[str, ...] = ()) -> None:
    targets = [int(p) for p in set(old_pids or ()) if int(p) > 0 and int(p) != int(new_pid or 0) and int(p) != os.getpid()]
    if not targets and not remove_pf:
        return

    def _worker() -> None:
        if delay > 0:
            time.sleep(delay)
        if new_pid > 0:
            try:
                os.kill(new_pid, 0)
            except OSError:
                return
        for pf in remove_pf:
            try:
                os.remove(pf)
            except OSError:
                pass
        alive = []
        for pid in targets:
            try:
                if probe.has_bin("pkill"):
                    subprocess.run(["pkill", "-TERM", "-P", str(pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                os.kill(pid, signal.SIGTERM)
                alive.append(pid)
            except OSError:
                pass
        if alive:
            time.sleep(0.15)
            for pid in alive:
                try:
                    os.kill(pid, 0)
                    if probe.has_bin("pkill"):
                        subprocess.run(["pkill", "-KILL", "-P", str(pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass

    threading.Thread(target=_worker, daemon=True).start()


def _stop_mpvpaper(mon: str) -> None:
    _send_mpv_ipc(mon, ["quit"])
    for pf_name in _wallpaper_pid_files(mon):
        pid_f = os.path.join(NC_DESKTOP_BASE, "pids", pf_name)
        if not os.path.isfile(pid_f):
            continue
        try:
            pid = int(open(pid_f, "r", encoding="utf-8").read().strip())
            if probe.has_bin("pkill"):
                subprocess.run(["pkill", "-TERM", "-P", str(pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            os.kill(pid, signal.SIGTERM)
            for _ in range(10):
                time.sleep(0.03)
                os.kill(pid, 0)
            if probe.has_bin("pkill"):
                subprocess.run(["pkill", "-KILL", "-P", str(pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            os.kill(pid, signal.SIGKILL)
        except Exception:
            pass
        try:
            os.remove(pid_f)
        except OSError:
            pass
    sock_p = f"/tmp/nc_mpvpaper_{mon}.sock"
    if os.path.exists(sock_p):
        try:
            os.remove(sock_p)
        except OSError:
            pass
    if probe.has_bin("pkill"):
        for pat in (
            f"^([^ ]*/)?ncmatrix .*--output[= ]{mon}( |$)",
            f"^([^ ]*/)?swaybg .*-o[= ]{mon}( |$)",
            f"^([^ ]*/)?(python[^ ]* |env python[^ ]* )?([^ ]*/)?nc-video-wallpaper.*--output[= ]{mon}( |$)",
            "^([^ ]*/)?(python[^ ]* |env python[^ ]* )?([^ ]*/)?nc-video-wallpaper.*--mode[= ]cmatrix",
            f"^([^ ]*/)?ffplay .*-window_title nc-vidwp-audio-{mon}( |$)",
            f"^([^ ]*/)?pacat .*nc-vidwp-audio-{mon}( |$)",
            f"^([^ ]*/)?mpvpaper .*( |^){mon}( |$)",
            "^([^ ]*/)?ffplay -nodisp -vn -loglevel quiet -autoexit ",
        ):
            subprocess.run(["pkill", "-9", "-f", pat], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _update_live_video_audio_if_running(mons: list[str], mute: bool = None, volume: int = None) -> bool:
    """Update live video audio via IPC immediately when video wallpaper is running on target monitors."""
    if not mons:
        return False
    live = dict(STATE.get("desktop", "out_live", {}) or {})
    staged = dict(STATE.get("desktop", "staged_preview", {}) or {})
    cur_target = str(STATE.get("desktop", "scene_path", "") or "").strip()
    cur_mode = str(STATE.get("desktop", "wp_mode", "scene") or "scene")
    use_mpv = probe.has_bin("mpvpaper")
    updated_any = False

    for mon in mons:
        sock_path = f"/tmp/nc_mpvpaper_{mon}.sock"
        if not os.path.exists(sock_path):
            continue
        live_src = _read_live_src(mon, live)
        is_same_target = (
            cur_mode == "scene"
            and live_src.get("mode") in ("scene", "image")
            and str(live_src.get("target", "") or "").strip() == cur_target
            and not staged.get(mon)
        )
        sent_mute = False
        if mute is True:
            sent_mute = _send_mpv_ipc(mon, ["set_property", "mute", "yes"] if use_mpv else ["set_mute", True])
            if probe.has_bin("pkill"):
                if not sent_mute:
                    for pat in (
                        f"^([^ ]*/)?ffplay .*-window_title nc-vidwp-audio-{mon}( |$)",
                        f"^([^ ]*/)?pacat .*nc-vidwp-audio-{mon}( |$)",
                    ):
                        subprocess.run(["pkill", "-9", "-f", pat], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                subprocess.run(["pkill", "-9", "-f", "^([^ ]*/)?ffplay -nodisp -vn -loglevel quiet -autoexit "], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if not is_same_target:
            continue

        sent = False
        if mute is not None:
            sent = sent_mute if mute is True else _send_mpv_ipc(mon, ["set_property", "mute", "no"] if use_mpv else ["set_mute", False])
        elif volume is not None:
            v_clamped = max(0, min(100, int(volume)))
            sent = _send_mpv_ipc(mon, ["set_property", "volume", v_clamped] if use_mpv else ["set_volume", v_clamped])

        if sent:
            ent = dict(live_src)
            if mute is not None:
                ent["video_mute"] = bool(mute)
            if volume is not None:
                ent["video_volume"] = max(0, min(100, int(volume)))
            _save_desktop_json_state(mon, {
                "output": mon,
                "mode": "scene",
                "target": ent.get("target", cur_target),
                "fit": ent.get("fit", "fill"),
                "media_kind": ent.get("media_kind") or detect_media_kind(ent.get("target", cur_target)),
                "yt_delivery": ent.get("yt_delivery", "stream"),
                "video_loop": ent.get("video_loop", "loop"),
                "playlist_order": ent.get("playlist_order", "sequential"),
                "video_mute": bool(ent.get("video_mute", True)),
                "video_volume": int(ent.get("video_volume", 60) or 60),
            })
            ent["ts"] = time.time()
            live[mon] = ent
            updated_any = True

    if updated_any:
        STATE.set("desktop", "out_live", live)
    return updated_any


def _send_mpv_ipc(mon: str, cmd_args: list) -> bool:
    sock_path = f"/tmp/nc_mpvpaper_{mon}.sock"
    if not os.path.exists(sock_path):
        return False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(1.0)
            s.connect(sock_path)
            s.sendall((json.dumps({"command": cmd_args}) + "\n").encode("utf-8"))
        return True
    except Exception:
        return False


def _read_clipboard_text() -> str:
    if probe.has_bin("wl-paste"):
        ok, out = probe.run_cmd(["wl-paste", "-n"], timeout=1.5)
        if ok and out:
            return out.strip()
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gdk, Gtk
        txt = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).wait_for_text()
        if txt:
            return txt.strip()
    except Exception:
        pass
    return ""


def _find_cached_yt_media(yt: dict) -> str:
    if not yt or not yt.get("ok"):
        return ""
    if yt.get("is_playlist") and yt.get("playlist_id"):
        pl_dir = os.path.join(_YT_WALL_DIR, f"yt_pl_{yt['playlist_id']}")
        if os.path.isdir(pl_dir) and count_folder_media(pl_dir) > 0:
            return pl_dir
        return ""
    vid = yt.get("video_id") or ""
    if vid and os.path.isdir(_YT_WALL_DIR):
        for ext in _VIDEO_EXTS:
            cand = os.path.join(_YT_WALL_DIR, f"yt_{vid}{ext}")
            if os.path.isfile(cand):
                return cand
        try:
            for fn in sorted(os.listdir(_YT_WALL_DIR)):
                fp = os.path.join(_YT_WALL_DIR, fn)
                if os.path.isfile(fp) and fn.lower().endswith(_VIDEO_EXTS) and _match_yt_local_vid(fp) == vid:
                    return fp
        except OSError:
            pass
    return ""


def _save_desktop_json_state(mon: str, payload: dict) -> None:
    try:
        st_dir = os.path.join(NC_DESKTOP_BASE, "state")
        os.makedirs(st_dir, exist_ok=True)
        with open(os.path.join(st_dir, f"{mon}.json"), "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
    except Exception:
        pass


def _start_youtube_download(cc_panel, url: str, yt_info: dict = None) -> None:
    yt = yt_info if (yt_info and yt_info.get("ok")) else _parse_youtube_input(url)
    target_url = yt["canonical_url"] if yt["ok"] else str(url or "").strip()
    if not re.match(r"^https?://\S+$", target_url, re.IGNORECASE):
        _set_pers_fb(False, t("cc_personalization.wp_yt_invalid"))
        return
    _cancel_pending_yt_dl()
    dl_seq = _YT_DL_SEQ
    os.makedirs(_YT_WALL_DIR, exist_ok=True)
    is_pl = bool(yt.get("is_playlist"))
    outs_data = _get_niri_outputs()
    sel_mons = get_selected_monitors(sorted(outs_data.keys()))
    target_h = _target_yt_height_for_monitors(sel_mons or list(outs_data.keys()), outs_data)
    STATE.set("desktop", "yt_status", "downloading")
    STATE.set("desktop", "yt_url", target_url)
    start_ts = time.time()

    def _download():
        if is_pl and yt.get("playlist_id"):
            pl_dir = os.path.join(_YT_WALL_DIR, f"yt_pl_{yt['playlist_id']}")
            os.makedirs(pl_dir, exist_ok=True)
            cmd = [
                "yt-dlp",
                "--yes-playlist",
                "--ignore-errors",
                "-S",
                f"res:{target_h},fps",
                "-f",
                f"bv*[height<={target_h}]+ba/b[height<={target_h}]/b",
                "--merge-output-format",
                "mp4",
                "-o",
                os.path.join(pl_dir, "%(playlist_index)03d_%(id)s.%(ext)s"),
                target_url,
            ]
            try:
                res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=600)
                cnt = count_folder_media(pl_dir)
                err_msg = (res.stderr or "").strip().splitlines()[-1] if res.stderr else "exit != 0"
                return (cnt > 0, pl_dir if cnt > 0 else "", err_msg)
            except Exception as e:
                return (False, "", str(e))

        cmd = [
            "yt-dlp",
            "--no-playlist",
            "-S",
            f"res:{target_h},fps",
            "-f",
            f"bv*[height<={target_h}]+ba/b[height<={target_h}]/b",
            "--merge-output-format",
            "mp4",
            "--print",
            "after_move:filepath",
            "-o",
            os.path.join(_YT_WALL_DIR, "yt_%(id)s.%(ext)s"),
            target_url,
        ]
        try:
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=300)
            lines = [ln.strip() for ln in (res.stdout or "").splitlines() if ln.strip()]
            out_path = next((ln for ln in reversed(lines) if os.path.isfile(ln)), "")
            if not out_path and os.path.isdir(_YT_WALL_DIR):
                cands = [
                    os.path.join(_YT_WALL_DIR, fn)
                    for fn in os.listdir(_YT_WALL_DIR)
                    if fn.lower().endswith(_VIDEO_EXTS)
                ]
                recent = [c for c in cands if os.path.getmtime(c) >= (start_ts - 2.0)]
                if recent:
                    out_path = max(recent, key=os.path.getmtime)
            return (res.returncode == 0 and bool(out_path), out_path, (res.stderr or "").strip().splitlines()[-1] if res.stderr else "exit != 0")
        except Exception as e:
            return (False, "", str(e))

    def _done(res):
        global _WP_USER_TOUCHED
        if dl_seq != _YT_DL_SEQ:
            return
        ok, path, err = res
        STATE.set("desktop", "yt_status", "")
        if ok and path and os.path.exists(path):
            STATE.set("desktop", "wp_mode", "scene")
            STATE.set("desktop", "scene_path", path)
            STATE.set("desktop", "yt_delivery", "download")
            _WP_USER_TOUCHED = True
            _stage_current_to_selected()
            _set_pers_fb(True, t("cc_personalization.wp_yt_done", p0=os.path.basename(path.rstrip("/")) or path))
        else:
            _set_pers_fb(False, t("cc_personalization.wp_yt_failed", p0=err[:60]))
        if cc_panel and cc_panel.shell:
            cc_panel.shell.refresh_view()

    probe.run_async(_download, on_done=_done)


def _handle_youtube_input(cc_panel, raw_input: str, from_clipboard: bool = False) -> bool:
    global _WP_USER_TOUCHED
    _cancel_pending_yt_dl()
    yt = _parse_youtube_input(raw_input)
    if not yt["ok"]:
        if from_clipboard:
            _set_pers_fb(False, t("cc_personalization.wp_yt_clip_invalid"))
            return False
        if not re.match(r"^https?://\S+$", str(raw_input or "").strip(), re.IGNORECASE):
            _set_pers_fb(False, t("cc_personalization.wp_yt_invalid"))
            return False
        canon = str(raw_input or "").strip()
        STATE.set("desktop", "wp_mode", "scene")
        STATE.set("desktop", "scene_path", canon)
        STATE.set("desktop", "yt_url", canon)
        STATE.set("desktop", "yt_delivery", "stream")
        _WP_USER_TOUCHED = True
        _stage_current_to_selected()
        _set_pers_fb(True, t("cc_personalization.wp_yt_stream_staged", p0=canon))
        return True

    canon = yt["canonical_url"]
    STATE.set("desktop", "yt_url", canon)
    _ensure_youtube_thumbnail(yt, cc_panel)
    deliv = str(STATE.get("desktop", "yt_delivery", "stream") or "stream")
    if deliv == "stream" or not probe.has_bin("yt-dlp"):
        STATE.set("desktop", "wp_mode", "scene")
        STATE.set("desktop", "scene_path", canon)
        STATE.set("desktop", "yt_delivery", "stream")
        _WP_USER_TOUCHED = True
        _stage_current_to_selected()
        _set_pers_fb(True, t("cc_personalization.wp_yt_stream_staged", p0=canon))
        return True

    cached = _find_cached_yt_media(yt)
    if cached:
        STATE.set("desktop", "wp_mode", "scene")
        STATE.set("desktop", "scene_path", cached)
        _WP_USER_TOUCHED = True
        _stage_current_to_selected()
        _set_pers_fb(True, t("cc_personalization.wp_yt_done", p0=os.path.basename(cached.rstrip("/")) or cached))
        return True

    _start_youtube_download(cc_panel, canon, yt_info=yt)
    return True


def _merge_desktop_json_meta(mon: str, extra: dict) -> None:
    st_dir = os.path.join(NC_DESKTOP_BASE, "state")
    sf = os.path.join(st_dir, f"{mon}.json")
    data = {}
    if os.path.isfile(sf):
        try:
            with open(sf, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                data = loaded
        except Exception:
            data = {}
    data.update(extra)
    data.setdefault("output", mon)
    try:
        os.makedirs(st_dir, exist_ok=True)
        tmp_sf = f"{sf}.tmp.{os.getpid()}.{threading.get_ident()}"
        with open(tmp_sf, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_sf, sf)
    except Exception:
        pass


def _spec_needs_asciify(spec: dict) -> bool:
    if not isinstance(spec, dict):
        return False
    m = spec.get("mode", "")
    target = str(spec.get("target", "") or "")
    if not target:
        return False
    asc = normalize_asciify_cfg(spec.get("asciify"))
    if not asc["enabled"]:
        return False
    if m == "slideshow":
        return True
    if m in ("scene", "image"):
        return (spec.get("media_kind") or detect_media_kind(target)) == "IMAGE"
    return False


def _cleanup_orphan_monitors(outs: list[str], live: dict) -> None:
    st_dir = os.path.join(NC_DESKTOP_BASE, "state")
    pid_dir = os.path.join(NC_DESKTOP_BASE, "pids")
    orphan_outs = set()
    for d, ext in ((st_dir, ".json"), (pid_dir, ".pid")):
        if os.path.isdir(d):
            for fn in os.listdir(d):
                if fn.endswith(ext):
                    base = fn[:-len(ext)]
                    mon_name = base.split("_", 1)[1] if "_" in base else base
                    if mon_name and mon_name not in outs:
                        orphan_outs.add(mon_name)
    for orph in orphan_outs:
        _stop_mpvpaper(orph)
        try:
            os.remove(os.path.join(st_dir, f"{orph}.json"))
        except OSError:
            pass
        live.pop(orph, None)


def _apply_single_non_ascii_monitor(mon: str, spec: dict, outs_data: dict, live: dict) -> tuple[bool, float, str]:
    """Apply a single non-ASCIIfied monitor wallpaper spec; returns (ok, async_grace, err_msg)."""
    pid_dir = os.path.join(NC_DESKTOP_BASE, "pids")
    m_kind = spec.get("mode", "scene")
    async_grace = 0.0
    if m_kind == "scene" and spec.get("target"):
        target_p = spec["target"]
        media_kind = spec.get("media_kind") or detect_media_kind(target_p)
        is_video_or_yt = media_kind in ("VIDEO", "YT VIDEO", "YT PLAYLIST") or target_p.startswith("http")
        video_bin = os.path.join(ROOT_DIR, "bin", "nc-video-wallpaper")
        if is_video_or_yt and (probe.has_bin("mpvpaper") or os.path.isfile(video_bin)):
            old_pids = _collect_output_wallpaper_pids(mon)
            is_mute = bool(spec.get("video_mute", True))
            vol = max(0, min(100, int(spec.get("video_volume", 60) or 60)))
            yt_deliv = str(spec.get("yt_delivery") or ("stream" if target_p.startswith("http") else "download"))
            vid_loop = str(spec.get("video_loop") or "loop")
            pl_order = str(spec.get("playlist_order") or "sequential")
            fit_mode = str(spec.get("fit") or "fill")
            ipc_sock = f"/tmp/nc_mpvpaper_{mon}.sock"
            try:
                if os.path.exists(ipc_sock):
                    os.remove(ipc_sock)
            except OSError:
                pass
            mon_w, mon_h = _get_monitor_resolution(mon, outs_data)
            target_h = _target_yt_height(mon_w, mon_h)

            if probe.has_bin("mpvpaper"):
                opts = [f"input-ipc-server={ipc_sock}"]
                if target_p.startswith("http") or yt_deliv == "stream":
                    opts.append("ytdl=yes")
                    opts.append(f"ytdl-format=bv*[height<={target_h}]+ba/b[height<={target_h}]/b")
                if vid_loop == "once":
                    opts.append("keep-open=yes")
                elif media_kind == "YT PLAYLIST" or os.path.isdir(target_p):
                    opts.append("loop-playlist=inf")
                else:
                    opts.append("loop=inf")
                if media_kind == "YT PLAYLIST" and pl_order == "shuffle":
                    opts.append("shuffle")
                if fit_mode == "fit":
                    opts.append("panscan=0.0")
                elif fit_mode == "stretch":
                    opts.append("keepaspect=no")
                elif fit_mode == "center":
                    opts.append("video-unscaled=yes")
                else:
                    opts.append("panscan=1.0")
                    al_v = _norm_align(spec.get("align", "center"))
                    opts.append(f"video-align-y={-1 if al_v == 'top' else (1 if al_v == 'bottom' else 0)}")
                opts.append("no-audio" if is_mute else f"volume={vol}")
                new_pid = _spawn_bg_pid(["mpvpaper", "-o", " ".join(opts), mon, target_p])
            else:
                new_pid = _spawn_bg_pid([
                    sys.executable,
                    video_bin,
                    "--output", mon,
                    "--target", target_p,
                    "--fit", fit_mode,
                    "--align", _norm_align(spec.get("align", "center")),
                    "--loop", vid_loop,
                    "--order", pl_order,
                    "--mute", "1" if is_mute else "0",
                    "--volume", str(vol),
                    "--width", str(mon_w),
                    "--height", str(mon_h),
                    "--max-height", str(target_h),
                    "--ipc-socket", ipc_sock,
                ])

            if new_pid > 0:
                vid_pf = os.path.join(pid_dir, f"video_{mon}.pid")
                try:
                    os.makedirs(pid_dir, exist_ok=True)
                    with open(vid_pf, "w", encoding="utf-8") as pf:
                        pf.write(f"{new_pid}\n")
                except OSError:
                    pass
            stale_pfs = tuple(
                os.path.join(pid_dir, pfn)
                for pfn in _wallpaper_pid_files(mon)
                if pfn != f"video_{mon}.pid"
            )
            _kill_pids_after_overlap(old_pids, new_pid=new_pid, delay=0.35, remove_pf=stale_pfs)

            _save_desktop_json_state(mon, {
                "output": mon,
                "mode": "scene",
                "target": target_p,
                "fit": fit_mode,
                "align": _norm_align(spec.get("align", "center")),
                "media_kind": media_kind,
                "yt_delivery": yt_deliv,
                "video_loop": vid_loop,
                "playlist_order": pl_order,
                "video_mute": is_mute,
                "video_volume": vol,
            })
        else:
            run_desktop_cli(["-o", mon, "-f", spec.get("fit", "fill"), "--align", _norm_align(spec.get("align", "center")), "-i", target_p])
            async_grace = 2.5
    elif m_kind == "none" or (m_kind == "scene" and not spec.get("target")):
        _stop_mpvpaper(mon)
        probe.run_cmd(["bash", resolve_script_path(), "-o", mon, "--stop"], timeout=2.0)
        _save_desktop_json_state(mon, {"output": mon, "mode": "none"})
    elif m_kind == "slideshow" and spec.get("target"):
        args = ["-o", mon, "-d", spec["target"], "-f", spec.get("fit", "fill"), "-n", spec.get("interval", "5m"), "-t", spec.get("trans", "fade")]
        if spec.get("order") == "sequential":
            args.append("--no-shuffle")
        run_desktop_cli(args)
        async_grace = 2.5
    elif m_kind == "solid":
        hex_val = _resolve_solid_hex(spec.get("hex") or spec.get("target") or "#000000")
        old_pids = _collect_output_wallpaper_pids(mon)
        if probe.has_bin("swaybg"):
            new_pid = _spawn_bg_pid(["swaybg", "-o", mon, "-c", hex_val, "-m", "solid_color"])
            sol_pf = os.path.join(pid_dir, f"solid_{mon}.pid")
            if new_pid > 0:
                try:
                    os.makedirs(pid_dir, exist_ok=True)
                    with open(sol_pf, "w", encoding="utf-8") as pf:
                        pf.write(f"{new_pid}\n")
                except OSError:
                    pass
            stale_pfs = tuple(
                os.path.join(pid_dir, pfn)
                for pfn in _wallpaper_pid_files(mon)
                if pfn != f"solid_{mon}.pid"
            )
            _kill_pids_after_overlap(old_pids, new_pid=new_pid, delay=0.30, remove_pf=stale_pfs)
        else:
            run_desktop_cli(["-o", mon, "--solid", hex_val])
            if old_pids:
                stale_pfs = tuple(os.path.join(pid_dir, pfn) for pfn in _wallpaper_pid_files(mon))
                _kill_pids_after_overlap(old_pids, new_pid=0, delay=0.25, remove_pf=stale_pfs)
        _save_desktop_json_state(mon, {
            "output": mon,
            "mode": "solid",
            "target": hex_val,
            "hex": hex_val,
            "bgcolor": hex_val.lstrip("#").lower(),
        })
    elif m_kind in ("ncmatrix", "cmatrix"):
        ncmatrix_bin = os.path.join(ROOT_DIR, "bin", "ncmatrix")
        if not (os.path.isfile(ncmatrix_bin) and os.access(ncmatrix_bin, os.X_OK)):
            return False, 0.0, f"Binary not found: {ncmatrix_bin}"
        cs = str(spec.get("charset") or "katakana")
        col = str(spec.get("color") or "green")
        spd = str(spec.get("speed") or "4")
        sz = str(spec.get("size") or "9")
        cm_trans = str(spec.get("cm_trans") or spec.get("transition") or "flow")
        try:
            with open(f"/tmp/ncmatrix_{mon}.conf", "w", encoding="utf-8") as cf:
                cf.write(
                    f"charset={cs}\ncolor={col}\nspeed={spd}\nfont_size={sz}\n"
                    f"transition={'drain' if cm_trans == 'drain' else 'flow'}\nbold=2\nasync=1\nfade=1\n"
                )
        except OSError:
            pass

        running_pid = _get_running_ncmatrix_pid(mon)
        prev_live = _read_live_src(mon, live)
        prev_size = str(prev_live.get("size") or "9")
        pid_file = os.path.join(pid_dir, f"ncmatrix_{mon}.pid")

        if running_pid > 0 and cm_trans != "instant":
            other_pids = [p for p in _collect_output_wallpaper_pids(mon) if p != running_pid]
            if other_pids:
                stale_pfs = tuple(
                    os.path.join(pid_dir, pfn)
                    for pfn in _wallpaper_pid_files(mon)
                    if pfn != f"ncmatrix_{mon}.pid"
                )
                _kill_pids_after_overlap(other_pids, new_pid=running_pid, delay=0.05, remove_pf=stale_pfs)
            sig = signal.SIGUSR2 if (cm_trans == "drain" or prev_size != sz) else signal.SIGUSR1
            try:
                os.kill(running_pid, sig)
            except OSError:
                running_pid = 0

        if running_pid <= 0 or cm_trans == "instant":
            old_pids = _collect_output_wallpaper_pids(mon)
            mon_w, mon_h = _get_monitor_resolution(mon, outs_data)
            logical = (outs_data.get(mon) or {}).get("logical") or {}
            lx, ly = logical.get("x"), logical.get("y")
            ipc_sock = f"/tmp/nc_mpvpaper_{mon}.sock"
            try:
                if os.path.exists(ipc_sock):
                    os.remove(ipc_sock)
            except OSError:
                pass
            cmd = [
                ncmatrix_bin,
                "--wallpaper",
                "--output", mon,
                "--charset", cs,
                "--color", col,
                "--speed", spd,
                "--font-size", sz,
                "--width", str(mon_w),
                "--height", str(mon_h),
                "--pid-file", pid_file,
                "--ipc-socket", ipc_sock,
                "-a", "-b",
            ]
            if lx is not None and ly is not None:
                cmd.extend(["--x", str(int(lx)), "--y", str(int(ly))])
            new_pid = _spawn_bg_pid(cmd)
            if new_pid > 0:
                try:
                    os.makedirs(pid_dir, exist_ok=True)
                    with open(pid_file, "w", encoding="utf-8") as pf:
                        pf.write(f"{new_pid}\n")
                except OSError:
                    pass
            stale_pfs = tuple(
                os.path.join(pid_dir, pfn)
                for pfn in _wallpaper_pid_files(mon)
                if pfn != f"ncmatrix_{mon}.pid"
            )
            _kill_pids_after_overlap(old_pids, new_pid=new_pid, delay=0.35, remove_pf=stale_pfs)

        _save_desktop_json_state(mon, {
            "output": mon,
            "mode": "ncmatrix",
            "target": "ncmatrix",
            "charset": cs,
            "color": col,
            "speed": spd,
            "size": sz,
            "cm_trans": cm_trans,
            "japanese": (cs == "katakana"),
        })
    return True, async_grace, ""


def _apply_staged_wallpapers() -> tuple[bool, str]:
    global _WP_USER_TOUCHED
    outs_data = _get_niri_outputs()
    outs = sorted(outs_data.keys())
    staged = dict(STATE.get("desktop", "staged_preview", {}) or {})
    if not staged:
        sel = get_selected_monitors(outs)
        if not sel:
            return False, t("cc_personalization.wp_no_selection")
        spec = _get_editor_draft_spec()
        staged = {m: spec for m in sel}

    live = dict(STATE.get("desktop", "out_live", {}) or {})
    _cleanup_orphan_monitors(outs, live)

    applied_mons = []
    modes_used = set()
    for mon, spec in staged.items():
        if not isinstance(spec, dict):
            continue
        modes_used.add(spec.get("mode", "scene"))
        ok, async_grace, err = _apply_single_non_ascii_monitor(mon, spec, outs_data, live)
        if not ok:
            return False, err
        ent = dict(spec)
        ent["ts"] = time.time() + async_grace
        live[mon] = ent
        applied_mons.append(mon)

    STATE.set("desktop", "out_live", live)
    STATE.set("desktop", "staged_preview", {})
    _WP_USER_TOUCHED = False
    if not applied_mons:
        return False, t("cc_personalization.wp_no_selection")
    return True, t("cc_personalization.wp_applied_msg", p0=", ".join(sorted(modes_used)), p1=", ".join(applied_mons))


def _start_apply_staged_wallpapers(cc_panel, staged_input: dict = None) -> None:
    global _ASCII_APPLY_SEQ, _ASCII_APPLY_STATUS
    if not is_pillow_available():
        _set_pers_fb(False, t("cc_personalization.wp_ascii_pillow_missing"))
        return

    outs_data = _get_niri_outputs()
    outs = sorted(outs_data.keys())
    staged = dict(staged_input if staged_input is not None else (STATE.get("desktop", "staged_preview", {}) or {}))
    if not staged:
        sel = get_selected_monitors(outs)
        if not sel:
            _set_pers_fb(False, t("cc_personalization.wp_no_selection"))
            return
        spec = _get_editor_draft_spec()
        staged = {m: spec for m in sel}

    live_snap = dict(STATE.get("desktop", "out_live", {}) or {})
    _ASCII_APPLY_SEQ += 1
    my_seq = _ASCII_APPLY_SEQ
    _ASCII_APPLY_STATUS = t("cc_personalization.wp_ascii_rendering_init")
    last_ui_ts = [0.0]

    def _set_status(msg: str, force: bool = False) -> None:
        global _ASCII_APPLY_STATUS
        if my_seq != _ASCII_APPLY_SEQ:
            return
        _ASCII_APPLY_STATUS = msg
        now = time.monotonic()
        if force or (now - last_ui_ts[0]) >= 0.12:
            last_ui_ts[0] = now
            if cc_panel and cc_panel.shell:
                GLib.idle_add(lambda: (cc_panel.shell.refresh_view() if cc_panel and cc_panel.shell else None) and False)

    def _work():
        live = dict(live_snap)
        applied_mons = []
        modes_used = set()
        try:
            _cleanup_orphan_monitors(outs, live)
            script = resolve_script_path()
            for mon, spec in staged.items():
                if my_seq != _ASCII_APPLY_SEQ:
                    return ("cancelled", "", live, [])
                if not isinstance(spec, dict):
                    continue
                m_kind = spec.get("mode", "scene")
                modes_used.add(m_kind)
                if _spec_needs_asciify(spec):
                    asc = normalize_asciify_cfg(spec.get("asciify"))
                    target_p = str(spec["target"])
                    if m_kind in ("scene", "image"):
                        align_mode = _norm_align(spec.get("align", "center"))
                        _set_status(t("cc_personalization.wp_ascii_rendering_single", p0=os.path.basename(target_p)), force=True)
                        rendered = render_ascii_image(target_p, asc, preview=False)
                        if my_seq != _ASCII_APPLY_SEQ:
                            return ("cancelled", "", live, [])
                        ok_cmd, out_cmd, err_cmd = probe.run_cmd_full(["bash", script, "-o", mon, "-f", spec.get("fit", "fill"), "--align", align_mode, "-i", rendered], timeout=20.0)
                        if not ok_cmd:
                            return ("err", (err_cmd or out_cmd or "apply failed").strip(), live, applied_mons)
                        _merge_desktop_json_meta(mon, {"source_target": target_p, "rendered_target": rendered, "align": align_mode, "asciify": asc})
                        ent = dict(spec)
                        ent["rendered_target"] = rendered
                        ent["ts"] = time.time() + 1.0
                        live[mon] = ent
                    else:
                        def _on_prog(idx: int, tot: int, fname: str) -> None:
                            _set_status(t("cc_personalization.wp_ascii_rendering_slide", p0=idx, p1=tot, p2=fname))

                        out_dir, _n_ok, _n_tot = render_ascii_folder(
                            target_p,
                            asc,
                            progress_cb=_on_prog,
                            cancel_check=lambda: my_seq != _ASCII_APPLY_SEQ,
                        )
                        if my_seq != _ASCII_APPLY_SEQ:
                            return ("cancelled", "", live, [])
                        if not out_dir:
                            return ("err", t("cc_personalization.wp_ascii_no_images"), live, applied_mons)
                        args = ["bash", script, "-o", mon, "-d", out_dir, "-f", spec.get("fit", "fill"), "-n", spec.get("interval", "5m"), "-t", spec.get("trans", "fade")]
                        if spec.get("order") == "sequential":
                            args.append("--no-shuffle")
                        ok_cmd, out_cmd, err_cmd = probe.run_cmd_full(args, timeout=20.0)
                        if not ok_cmd:
                            return ("err", (err_cmd or out_cmd or "apply failed").strip(), live, applied_mons)
                        time.sleep(0.25)
                        _merge_desktop_json_meta(mon, {"source_target": target_p, "rendered_target": out_dir, "asciify": asc})
                        ent = dict(spec)
                        ent["rendered_target"] = out_dir
                        ent["ts"] = time.time() + 2.5
                        live[mon] = ent
                else:
                    ok, async_grace, err = _apply_single_non_ascii_monitor(mon, spec, outs_data, live)
                    if not ok:
                        return ("err", err, live, applied_mons)
                    ent = dict(spec)
                    ent["ts"] = time.time() + async_grace
                    live[mon] = ent
                applied_mons.append(mon)
            if not applied_mons:
                return ("err", t("cc_personalization.wp_no_selection"), live, [])
            return ("ok", t("cc_personalization.wp_applied_msg", p0=", ".join(sorted(modes_used)), p1=", ".join(applied_mons)), live, applied_mons)
        except Exception as e:
            return ("err", str(e), live, applied_mons)

    def _done(res):
        global _WP_USER_TOUCHED, _ASCII_APPLY_STATUS
        if my_seq != _ASCII_APPLY_SEQ:
            return
        _ASCII_APPLY_STATUS = ""
        kind, msg, live_out, _mons = res
        if kind == "ok":
            STATE.set("desktop", "out_live", live_out)
            STATE.set("desktop", "staged_preview", {})
            _WP_USER_TOUCHED = False
            _set_pers_fb(True, msg)
        elif kind == "err":
            _set_pers_fb(False, msg)
        if cc_panel and cc_panel.shell:
            cc_panel.shell.refresh_view()

    probe.run_async(_work, on_done=_done)


def _sync_theme_aliases() -> None:
    sanitize_theme_dict(THEME)
    get_semantic_palette()


def _apply_full_preset(cc_panel, pid: str) -> None:
    spec = THEME_PRESETS.get(pid)
    if not spec:
        return
    asc = THEME.setdefault("ascii", {})
    asc["box_style"] = spec["box_style"]
    asc["chars"] = dict(BOX_STYLES.get(spec["box_style"], BOX_STYLES["rounded"]))
    for k in (
        "title_brackets",
        "button_brackets",
        "slider_brackets",
        "slider_fill",
        "slider_empty",
        "pill_dots",
        "tab_caps",
        "collapsible_icons",
        "table_rule",
        "kv_separator",
        "search_prefix",
        "search_cursor",
    ):
        if k in spec:
            asc[k] = list(spec[k]) if isinstance(spec[k], list) else spec[k]
    THEME.setdefault("colors", {}).update(copy.deepcopy(spec["colors"]))
    sync_waybar_colors_from_theme(THEME["colors"])
    win_op = float(THEME["colors"].get("window_opacity", 0.86))
    STATE.set("appearance", "transparency_pct", max(5, min(100, int(round(win_op * 100.0)))))
    _sync_theme_aliases()
    _save_theme_file(THEME_PATH, THEME)
    sync_ncui_cursor_if_active()
    if cc_panel.shell:
        cc_panel.shell._apply_css()
    _set_pers_fb(True, t("cc_personalization.applied_preset", p0=spec["label"]))


def _list_saved_themes() -> list[str]:
    if not os.path.exists(THEMES_DIR):
        return []
    return sorted([f[:-5] for f in os.listdir(THEMES_DIR) if f.endswith(".toml")])


def handle_cc_personalization_link(cc_panel, uri: str) -> bool:
    if handle_cc_waybar_link(cc_panel, uri):
        return True

    if uri.startswith("cc_desk:"):
        global _WP_USER_TOUCHED
        act = uri[len("cc_desk:"):]
        if act != "cm_col_custom":
            cancel_inline_entry(refresh=False)
        outs = sorted(_get_niri_outputs().keys())
        sel = get_selected_monitors(outs)
        target_cli = "all" if len(sel) == len(outs) else (sel[0] if sel else "all")

        if act == "toggle_fps":
            STATE.set("desktop", "preview_15fps", not is_live_15fps_enabled())
        elif act == "close_yazi":
            _close_embedded_yazi(cancelled=True)
            _set_pers_fb(True, t("cc_personalization.wp_yazi_closed_msg"))
        elif act == "sel_all" or act.startswith("sel_mon:"):
            if act == "sel_all":
                new_sel = [] if (outs and len(sel) == len(outs)) else list(outs)
            else:
                picked = act.split(":", 1)[1]
                if picked not in outs:
                    new_sel = None
                elif picked in sel:
                    new_sel = [o for o in sel if o != picked]
                else:
                    new_sel = [o for o in outs if o in sel or o == picked]
            if new_sel is not None:
                STATE.set("desktop", "selected_monitors", new_sel)
                if _WP_USER_TOUCHED:
                    _stage_current_to_selected()
                else:
                    cur_staged = STATE.get("desktop", "staged_preview", {}) or {}
                    if cur_staged:
                        STATE.set("desktop", "staged_preview", {k: v for k, v in cur_staged.items() if k in new_sel})
                    sync_editor_from_live_monitors()
        elif act.startswith("mode:"):
            m_id = act.split(":", 1)[1]
            if m_id == "cmatrix":
                m_id = "ncmatrix"
            if m_id in ("scene", "slideshow", "ncmatrix", "solid"):
                _cancel_pending_yt_dl()
                _cancel_pending_ascii_apply()
                _close_embedded_yazi(cancelled=True)
                STATE.set("desktop", "wp_mode", m_id)
                if m_id == "solid" and not STATE.get("desktop", "solid_hex"):
                    STATE.set("desktop", "solid_hex", "#000000")
                _WP_USER_TOUCHED = True
                _stage_current_to_selected()
        elif act.startswith("solid_hex:"):
            hex_col = _resolve_solid_hex(act.split(":", 1)[1])
            STATE.set("desktop", "solid_hex", hex_col)
            STATE.set("desktop", "wp_mode", "solid")
            _WP_USER_TOUCHED = True
            _stage_current_to_selected()
        elif act == "inline_solid_hex":
            cur_hx = _resolve_solid_hex(STATE.get("desktop", "solid_hex", "#000000") or "#000000")
            start_inline_input(
                entry_id="cc_desk_solid_hex",
                shell=cc_panel.shell,
                trigger_uri=uri,
                initial_text=cur_hx,
                max_len=6,
                strip_leading_hash=True,
                uppercase=True,
                validator=lambda s: bool(re.match(r"^[0-9a-fA-F]{6}$", s.strip())),
                on_commit=lambda s: handle_cc_personalization_prompt(cc_panel, "cc_desk_solid_hex", "#" + s.strip().upper()),
            )
            return True
        elif act.startswith("fit:"):
            STATE.set("desktop", "scene_fit", act.split(":", 1)[1])
            _WP_USER_TOUCHED = True
            _stage_current_to_selected()
        elif act.startswith("vid_loop:"):
            STATE.set("desktop", "video_loop", act.split(":", 1)[1])
            _WP_USER_TOUCHED = True
            _stage_current_to_selected()
        elif act.startswith("pl_ord:"):
            STATE.set("desktop", "playlist_order", act.split(":", 1)[1])
            _WP_USER_TOUCHED = True
            _stage_current_to_selected()
        elif act.startswith("yt_deliv:"):
            deliv = act.split(":", 1)[1]
            if deliv in ("download", "stream"):
                STATE.set("desktop", "yt_delivery", deliv)
                cur_sc = str(STATE.get("desktop", "scene_path", "") or "")
                yt = _resolve_yt_source(cur_sc)
                if not yt["ok"] and (not cur_sc or detect_media_kind(cur_sc) in ("YT VIDEO", "YT PLAYLIST", "")):
                    yt = _resolve_yt_source(str(STATE.get("desktop", "yt_url", "") or ""))
                if deliv == "stream":
                    _cancel_pending_yt_dl()
                if yt["ok"]:
                    STATE.set("desktop", "yt_url", yt["canonical_url"])
                    _ensure_youtube_thumbnail(yt, cc_panel)
                    if deliv == "stream":
                        STATE.set("desktop", "scene_path", yt["canonical_url"])
                        _WP_USER_TOUCHED = True
                        _stage_current_to_selected()
                    else:
                        cached = _find_cached_yt_media(yt)
                        if cached:
                            STATE.set("desktop", "scene_path", cached)
                            _WP_USER_TOUCHED = True
                            _stage_current_to_selected()
                        else:
                            _start_youtube_download(cc_panel, yt["canonical_url"], yt_info=yt)
                else:
                    _WP_USER_TOUCHED = True
                    _stage_current_to_selected()
        elif act == "clear_scene":
            _cancel_pending_yt_dl()
            _close_embedded_yazi(cancelled=True)
            STATE.set("desktop", "scene_path", "")
            STATE.set("desktop", "yt_url", "")
            _WP_USER_TOUCHED = True
            _stage_current_to_selected()
            _set_pers_fb(True, t("cc_personalization.wp_scene_cleared"))
        elif act == "paste_yt":
            _close_embedded_yazi(cancelled=True)
            clip = _read_clipboard_text()
            _handle_youtube_input(cc_panel, clip, from_clipboard=True)
        elif act in ("pl_prev", "pl_next"):
            ipc_cmd = "playlist-prev" if act == "pl_prev" else "playlist-next"
            sent = any(_send_mpv_ipc(m, [ipc_cmd]) for m in (sel or outs))
            if not sent:
                run_desktop_cli(["-o", target_cli, "--restore" if act == "pl_prev" else "--next"])
            _set_pers_fb(True, t("cc_personalization.wp_pl_track_prev_msg" if act == "pl_prev" else "cc_personalization.wp_pl_track_next_msg"))
        elif act.startswith("vid_mute:"):
            m_val = (act.split(":", 1)[1] == "1")
            STATE.set("desktop", "video_mute", m_val)
            if not _update_live_video_audio_if_running(sel or outs, mute=m_val):
                _WP_USER_TOUCHED = True
            _stage_current_to_selected()
        elif act.startswith("vid_vol:"):
            v_val = max(0, min(100, int(act.split(":", 1)[1])))
            STATE.set("desktop", "video_volume", v_val)
            if not _update_live_video_audio_if_running(sel or outs, volume=v_val):
                _WP_USER_TOUCHED = True
            _stage_current_to_selected()
        elif act.startswith("slide_int:"):
            STATE.set("desktop", "slide_interval", act.split(":", 1)[1])
            _WP_USER_TOUCHED = True
            _stage_current_to_selected()
        elif act.startswith("slide_ord:"):
            STATE.set("desktop", "slide_order", act.split(":", 1)[1])
            _WP_USER_TOUCHED = True
            _stage_current_to_selected()
        elif act.startswith("slide_tr:"):
            STATE.set("desktop", "slide_trans", act.split(":", 1)[1])
            _WP_USER_TOUCHED = True
            _stage_current_to_selected()
        elif act.startswith("slide_spd:"):
            STATE.set("desktop", "slide_speed", act.split(":", 1)[1])
            _WP_USER_TOUCHED = True
            _stage_current_to_selected()
        elif act.startswith("cm_charset:"):
            STATE.set("desktop", "cm_charset", act.split(":", 1)[1])
            _WP_USER_TOUCHED = True
            _stage_current_to_selected()
        elif act.startswith("cm_col:"):
            set_inline_entry_error("cm_col_custom", "")
            STATE.set("desktop", "cm_color", act.split(":", 1)[1])
            _WP_USER_TOUCHED = True
            _stage_current_to_selected()
        elif act == "cm_col_custom":
            def _on_cm_hex_commit(hex_digits: str) -> None:
                global _WP_USER_TOUCHED
                STATE.set("desktop", "cm_color", f"#{hex_digits.upper()}")
                _WP_USER_TOUCHED = True
                _stage_current_to_selected()

            def _on_cm_hex_invalid(_raw: str) -> None:
                set_inline_entry_error("cm_col_custom", t("cc_personalization.wp_err_invalid_hex"))

            start_inline_entry(
                "cm_col_custom",
                shell=cc_panel.shell,
                max_len=6,
                validator=lambda s: bool(re.match(r"^[0-9A-Fa-f]{6}$", s)),
                on_commit=_on_cm_hex_commit,
                on_invalid=_on_cm_hex_invalid,
            )
        elif act.startswith("cm_spd:"):
            STATE.set("desktop", "cm_speed", act.split(":", 1)[1])
            _WP_USER_TOUCHED = True
            _stage_current_to_selected()
        elif act.startswith("cm_size:"):
            STATE.set("desktop", "cm_size", act.split(":", 1)[1])
            _WP_USER_TOUCHED = True
            _stage_current_to_selected()
        elif act.startswith("cm_trans:"):
            tr_val = act.split(":", 1)[1]
            if tr_val in ("flow", "drain", "instant"):
                STATE.set("desktop", "cm_trans", tr_val)
                _WP_USER_TOUCHED = True
                _stage_current_to_selected()
        elif act == "apply_staged":
            _close_embedded_yazi(cancelled=True)
            pending_staged = dict(STATE.get("desktop", "staged_preview", {}) or {})
            if not pending_staged:
                sel_m = get_selected_monitors(outs)
                if sel_m:
                    d_spec = _get_editor_draft_spec()
                    pending_staged = {m: d_spec for m in sel_m}
            if any(_spec_needs_asciify(sp) for sp in pending_staged.values()):
                _start_apply_staged_wallpapers(cc_panel, pending_staged)
            else:
                _cancel_pending_ascii_apply()
                ok, msg = _apply_staged_wallpapers()
                _set_pers_fb(ok, msg)
        elif act == "reset_staged":
            _cancel_pending_yt_dl()
            _cancel_pending_ascii_apply()
            _close_embedded_yazi(cancelled=True)
            set_inline_entry_error("cm_col_custom", "")
            STATE.set("desktop", "staged_preview", {})
            _WP_USER_TOUCHED = False
            sync_editor_from_live_monitors()
            _set_pers_fb(True, t("cc_personalization.wp_reset_msg"))
        elif act.startswith("align:"):
            a_val = act.split(":", 1)[1]
            if a_val in ("top", "center", "bottom"):
                STATE.set("desktop", "scene_align", a_val)
                _WP_USER_TOUCHED = True
                _stage_current_to_selected()
        elif act.startswith("ascii_toggle:"):
            STATE.set("desktop", "ascii_enabled", act.split(":", 1)[1] == "1")
            _WP_USER_TOUCHED = True
            _stage_current_to_selected()
        elif act.startswith("ascii_mode:"):
            m_val = act.split(":", 1)[1]
            if m_val in ASCII_COLOR_MODES:
                STATE.set("desktop", "ascii_mode", m_val)
                _WP_USER_TOUCHED = True
                _stage_current_to_selected()
        elif act.startswith("ascii_chars:"):
            c_val = act.split(":", 1)[1]
            if c_val in ASCII_CHAR_SETS:
                STATE.set("desktop", "ascii_chars", c_val)
                _WP_USER_TOUCHED = True
                _stage_current_to_selected()
        elif act == "inline_ascii_chars":
            cur_c = str(get_editor_asciify_cfg().get("chars", "standard") or "")
            init_c = "" if cur_c in ASCII_CHAR_SETS else cur_c

            def _commit_ascii_chars(raw_s: str) -> None:
                global _WP_USER_TOUCHED
                # Do NOT .strip(): leading spaces represent empty dark cells.
                STATE.set("desktop", "ascii_chars", raw_s)
                _WP_USER_TOUCHED = True
                _stage_current_to_selected()

            start_inline_input(
                entry_id="cc_desk_ascii_chars",
                shell=cc_panel.shell,
                trigger_uri=uri,
                initial_text=init_c,
                max_len=96,
                validator=lambda s: len(s) >= 2 and s.strip() != "",
                on_commit=_commit_ascii_chars,
            )
            return True
        elif act.startswith("ascii_density:"):
            try:
                v = round(max(0.25, min(2.5, float(act.split(":", 1)[1]))), 2)
                STATE.set("desktop", "ascii_density", v)
                _WP_USER_TOUCHED = True
                _stage_current_to_selected()
            except ValueError:
                pass
            if cc_panel:
                cc_panel.request_throttled_refresh()
            return True
        elif act.startswith("ascii_tint:"):
            try:
                v = round(max(0.0, min(1.0, float(act.split(":", 1)[1]))), 2)
                STATE.set("desktop", "ascii_tint", v)
                _WP_USER_TOUCHED = True
                _stage_current_to_selected()
            except ValueError:
                pass
            if cc_panel:
                cc_panel.request_throttled_refresh()
            return True
        elif act.startswith("ascii_scale:"):
            try:
                v = round(max(0.25, min(1.5, float(act.split(":", 1)[1]))), 2)
                STATE.set("desktop", "ascii_scale", v)
                _WP_USER_TOUCHED = True
                _stage_current_to_selected()
            except ValueError:
                pass
            if cc_panel:
                cc_panel.request_throttled_refresh()
            return True
        elif act.startswith("ascii_contrast:"):
            try:
                v = round(max(0.5, min(2.0, float(act.split(":", 1)[1]))), 2)
                STATE.set("desktop", "ascii_contrast", v)
                _WP_USER_TOUCHED = True
                _stage_current_to_selected()
            except ValueError:
                pass
            if cc_panel:
                cc_panel.request_throttled_refresh()
            return True
        elif act.startswith("ascii_brightness:"):
            try:
                v = round(max(0.5, min(2.0, float(act.split(":", 1)[1]))), 2)
                STATE.set("desktop", "ascii_brightness", v)
                _WP_USER_TOUCHED = True
                _stage_current_to_selected()
            except ValueError:
                pass
            if cc_panel:
                cc_panel.request_throttled_refresh()
            return True
        elif act.startswith("ascii_invert:"):
            STATE.set("desktop", "ascii_invert", act.split(":", 1)[1] == "1")
            _WP_USER_TOUCHED = True
            _stage_current_to_selected()
        elif act == "ascii_defaults":
            cfg_def = dict(ASCIIFY_DEFAULTS)
            cfg_def["enabled"] = True
            _set_editor_asciify_cfg(cfg_def)
            _WP_USER_TOUCHED = True
            _stage_current_to_selected()
            _set_pers_fb(True, t("cc_personalization.wp_ascii_defaults_msg"))
        elif act == "ascii_cancel":
            _cancel_pending_ascii_apply()
            _set_pers_fb(True, t("cc_personalization.wp_ascii_cancelled"))
        elif act == "ascii_clear_cache":
            keep_paths = set()
            for mon_n in outs:
                l_src = _read_live_src(mon_n)
                rt = str(l_src.get("rendered_target") or "")
                if rt:
                    keep_paths.add(rt)
            removed_n, _freed_b = clear_ascii_cache(keep_paths=keep_paths)
            _LAST_ASCII_PREVIEW.clear()
            _set_pers_fb(True, t("cc_personalization.wp_ascii_cache_cleared", p0=removed_n))
        elif act == "prompt_yt":
            _close_embedded_yazi(cc_panel=cc_panel, cancelled=True)
            cur_u = str(STATE.get("desktop", "yt_url", "") or "https://youtube.com/watch?v=")
            cc_panel.request_prompt("cc_desk_yt", t("cc_personalization.wp_prompt_yt_ph"), cur_u, title=t("cc_personalization.wp_prompt_yt_title"), required=True)
            return True
        elif act == "pick_scene":
            if _launch_yazi_picker(cc_panel, is_folder=False):
                if cc_panel.shell:
                    cc_panel.shell.refresh_view()
                return True
            cur_s = str(STATE.get("desktop", "scene_path", "") or "~/")
            cc_panel.request_prompt("cc_desk_scene", t("cc_personalization.wp_prompt_scene_ph"), cur_s, title=t("cc_personalization.wp_prompt_scene_title"), required=True, completion="path_file")
            return True
        elif act == "type_scene":
            _close_embedded_yazi(cc_panel=cc_panel, cancelled=True)
            cur_s = str(STATE.get("desktop", "scene_path", "") or "~/")
            cc_panel.request_prompt("cc_desk_scene", t("cc_personalization.wp_prompt_scene_ph"), cur_s, title=t("cc_personalization.wp_prompt_scene_title"), required=True, completion="path_file")
            return True
        elif act == "pick_folder":
            if _launch_yazi_picker(cc_panel, is_folder=True):
                if cc_panel.shell:
                    cc_panel.shell.refresh_view()
                return True
            cur_d = str(STATE.get("desktop", "slide_folder", WALL_DIR) or WALL_DIR)
            cc_panel.request_prompt("cc_desk_folder", t("cc_personalization.wp_prompt_folder_ph"), cur_d, title=t("cc_personalization.wp_prompt_folder_title"), required=True, completion="path_dir")
            return True
        elif act == "type_folder":
            _close_embedded_yazi(cc_panel=cc_panel, cancelled=True)
            cur_d = str(STATE.get("desktop", "slide_folder", WALL_DIR) or WALL_DIR)
            cc_panel.request_prompt("cc_desk_folder", t("cc_personalization.wp_prompt_folder_ph"), cur_d, title=t("cc_personalization.wp_prompt_folder_title"), required=True, completion="path_dir")
            return True
        elif act == "inline_interval":
            cur_i = str(STATE.get("desktop", "slide_interval", "5m") or "5m")
            start_inline_input(
                entry_id="cc_desk_interval",
                shell=cc_panel.shell,
                trigger_uri=uri,
                initial_text=cur_i,
                max_len=12,
                validator=lambda s: bool(s.strip()),
                on_commit=lambda s: handle_cc_personalization_prompt(cc_panel, "cc_desk_interval", s.strip()),
            )
            return True
        elif act in ("next", "prev", "pause", "resume", "restore", "stop"):
            mons = sel or outs
            if act == "stop":
                for m in mons:
                    _stop_mpvpaper(m)
                run_desktop_cli(["-o", target_cli, "--stop"])
            elif act in ("pause", "resume"):
                if not any(_send_mpv_ipc(m, [act]) for m in mons):
                    run_desktop_cli(["-o", target_cli, f"--{act}"])
            else:
                cli_flag = "restore" if act == "prev" else act
                run_desktop_cli(["-o", target_cli, f"--{cli_flag}"])
        else:
            return False
        if cc_panel.shell:
            cc_panel.shell.refresh_view()
        return True

    if not uri.startswith("cc_pers:"):
        return False
    act = uri[len("cc_pers:"):]
    cancel_inline_entry(refresh=False)

    if act.startswith("wb_view:"):
        cc_panel.set_state("wb_sub_view", act.split(":", 1)[1])
    elif act == "noop":
        return True
    elif act.startswith("box_op:"):
        try:
            set_box_opacity_pct(int(round(float(act.split(":", 1)[1]))))
        except Exception:
            pass
        if cc_panel:
            cc_panel.request_throttled_refresh()
        return True
    elif act.startswith("toast_op:"):
        try:
            clamped = set_toast_opacity_pct(int(round(float(act.split(":", 1)[1]))))
            if cc_panel and cc_panel.shell and hasattr(cc_panel.shell, "show_toast"):
                cc_panel.shell.show_toast(
                    t("cc_personalization.toast_opacity_preview", p0=clamped),
                    level="ok",
                    title=t("cc_personalization.toast_layer_opacity"),
                )
        except Exception:
            pass
        if cc_panel:
            cc_panel.request_throttled_refresh()
        return True
    elif act.startswith("sys_notif_op:"):
        try:
            set_sys_notif_opacity_pct(int(round(float(act.split(":", 1)[1]))))
        except Exception:
            pass
        if cc_panel:
            cc_panel.request_throttled_refresh()
        return True
    elif act == "wb_sync_op":
        set_waybar_sync_opacity(not get_waybar_sync_opacity())
    elif act == "wb_sync_col":
        set_waybar_sync_color(not get_waybar_sync_color())
    elif act == "wb_apply_preset_palette":
        sync_waybar_colors_from_theme(THEME["colors"])
        _save_theme_file(THEME_PATH, THEME)
        apply_waybar_palette(force_palette=True)
        _set_pers_fb(True, t("cc_personalization.wb_palette_applied"))
    elif act == "wb_apply_palette":
        apply_waybar_palette(force_palette=True)
        _set_pers_fb(True, t("cc_personalization.wb_palette_applied"))
    elif act == "wb_sync_from_theme":
        sync_waybar_colors_from_theme(THEME["colors"])
        _save_theme_file(THEME_PATH, THEME)
        _set_pers_fb(True, t("cc_personalization.wb_palette_synced"))
    elif act.startswith("wb_color:"):
        _, wk, hx = act.split(":", 2)
        if wk in ("wb_bg", "wb_pill_bg", "wb_fg", "wb_accent", "wb_active", "wb_warn", "wb_crit"):
            THEME.setdefault("colors", {})[wk] = hx.lower()
            _sync_theme_aliases()
            _save_theme_file(THEME_PATH, THEME)
            _set_pers_fb(True, t("cc_personalization.color_updated_to", p0=wk, p1=hx.upper()))
    elif act.startswith("inline_wb_col:"):
        wk = act.split(":", 1)[1]
        start_inline_input(
            entry_id=f"cc_wb_col_{wk}",
            shell=cc_panel.shell,
            trigger_uri=uri,
            initial_text=str(THEME["colors"].get(wk, "#080a0f")),
            max_len=6,
            strip_leading_hash=True,
            uppercase=True,
            validator=lambda s: bool(re.match(r"^[0-9a-fA-F]{6}$", s.strip())),
            on_commit=lambda s, key=wk: handle_cc_personalization_prompt(cc_panel, f"cc_pers_wb_col|{key}", "#" + s.strip().lower()),
        )
        return True
    elif act.startswith("row_gap:"):
        try:
            set_row_gap(int(act.split(":", 1)[1]))
            if cc_panel.shell:
                cc_panel.shell._apply_css()
        except Exception:
            pass
    elif act.startswith("preset:"):
        _apply_full_preset(cc_panel, act.split(":", 1)[1])
    elif act.startswith("box_style:"):
        st = act.split(":", 1)[1]
        if st in BOX_STYLES:
            THEME["ascii"]["box_style"] = st
            THEME["ascii"]["chars"] = dict(BOX_STYLES[st])
            _persist_theme_key("ascii", "box_style", f'"{st}"')
            _save_theme_file(THEME_PATH, THEME)
            if cc_panel.shell:
                cc_panel.shell._apply_css()
            _set_pers_fb(True, t("cc_personalization.box_style_set_to", p0=st))
    elif act.startswith("set_color:"):
        _, k, val = act.split(":", 2)
        if k == "surface_bg":
            norm_hx = set_surface_bg(val, sync_waybar=False)
            _refresh_surface_companions(norm_hx)
            _save_theme_file(THEME_PATH, THEME)
            if cc_panel.shell:
                cc_panel.shell._apply_css()
            _set_pers_fb(True, t("cc_personalization.color_updated_to", p0="surface_bg", p1=norm_hx))
        else:
            target_axis = "intent" if k == "accent" else k
            THEME["colors"].update(derive_axis_companions(target_axis, val, get_surface_bg_hex()))
            _sync_theme_aliases()
            _save_theme_file(THEME_PATH, THEME)
            if target_axis in ("intent", "fault"):
                sync_ncui_cursor_if_active()
            if cc_panel.shell:
                cc_panel.shell._apply_css()
            _set_pers_fb(True, t("cc_personalization.color_updated_to", p0=target_axis, p1=val))
    elif act.startswith("inline_col:"):
        k = act.split(":", 1)[1]
        start_inline_input(
            entry_id=f"cc_col_{k}",
            shell=cc_panel.shell,
            trigger_uri=uri,
            initial_text=str(THEME["colors"].get(k, "#00f0ff")),
            max_len=6,
            strip_leading_hash=True,
            uppercase=True,
            validator=lambda s: bool(re.match(r"^[0-9a-fA-F]{6}$", s.strip())),
            on_commit=lambda s, key=k: handle_cc_personalization_prompt(cc_panel, f"cc_pers_col|{key}", "#" + s.strip().lower()),
        )
        return True
    elif act == "reset_theme":
        THEME.clear()
        THEME.update(copy.deepcopy(DEFAULT_THEME))
        THEME["ascii"]["chars"] = dict(BOX_STYLES[THEME["ascii"]["box_style"]])
        win_op = float(THEME["colors"].get("window_opacity", 0.86))
        STATE.set("appearance", "transparency_pct", max(5, min(100, int(round(win_op * 100.0)))))
        _sync_theme_aliases()
        _save_theme_file(THEME_PATH, THEME)
        sync_ncui_cursor_if_active()
        if cc_panel.shell:
            cc_panel.shell._apply_css()
        _set_pers_fb(True, t("cc_personalization.reset_theme_to_cyber_nightcity_def"))
    elif act.startswith("font:"):
        f = act.split(":", 1)[1]
        STATE.set("personalization", "font_pick_open", False)
        STATE.set("personalization", "font_query", "")
        THEME["typography"]["font_family"] = f
        _persist_theme_key("typography", "font_family", f'"{f}"')
        if cc_panel.shell:
            cc_panel.shell._apply_css()
        _set_pers_fb(True, t("cc_personalization.font_family_set_to", p0=f))
    elif act.startswith("font_size:"):
        sz = int(act.split(":", 1)[1])
        THEME["typography"]["font_size"] = sz
        _persist_theme_key("typography", "font_size", str(sz))
        if cc_panel.shell:
            cc_panel.shell._apply_css()
        _set_pers_fb(True, t("cc_personalization.font_size_set_to_pt", p0=sz))
    elif act == "font_pick_open":
        STATE.set("personalization", "font_pick_open", True)
        cur_q = str(STATE.get("personalization", "font_query", "") or "")
        def _commit_raw_font(txt: str) -> None:
            STATE.set("personalization", "font_pick_open", False)
            STATE.set("personalization", "font_query", "")
            if txt.strip():
                handle_cc_personalization_prompt(cc_panel, "cc_pers_font", txt.strip())
        def _cancel_font() -> None:
            STATE.set("personalization", "font_pick_open", False)
            STATE.set("personalization", "font_query", "")
        start_inline_input(
            entry_id="cc_pers_font_picker",
            shell=cc_panel.shell,
            trigger_uri=uri,
            initial_text=cur_q,
            max_len=48,
            is_picker=True,
            on_change=lambda txt: STATE.set("personalization", "font_query", str(txt or "")),
            on_cancel=_cancel_font,
            on_commit=_commit_raw_font,
        )
        return True
    elif act == "font_pick_clear":
        STATE.set("personalization", "font_query", "")
    elif act == "cursor_pick_open":
        STATE.set("personalization", "cursor_pick_open", True)
        cur_q = str(STATE.get("personalization", "cursor_query", "") or "")
        def _commit_raw_cursor(txt: str) -> None:
            STATE.set("personalization", "cursor_pick_open", False)
            STATE.set("personalization", "cursor_query", "")
            if txt.strip():
                handle_cc_personalization_prompt(cc_panel, "cc_pers_cursor", txt.strip())
        def _cancel_cursor() -> None:
            STATE.set("personalization", "cursor_pick_open", False)
            STATE.set("personalization", "cursor_query", "")
        start_inline_input(
            entry_id="cc_pers_cursor_picker",
            shell=cc_panel.shell,
            trigger_uri=uri,
            initial_text=cur_q,
            max_len=48,
            is_picker=True,
            on_change=lambda txt: STATE.set("personalization", "cursor_query", str(txt or "")),
            on_cancel=_cancel_cursor,
            on_commit=_commit_raw_cursor,
        )
        return True
    elif act == "cursor_pick_clear":
        STATE.set("personalization", "cursor_query", "")
    elif act.startswith("cursor_size:"):
        cur_t, _ = _read_cursor_cfg()
        ok, msg = _apply_cursor_cfg(cur_t, int(act.split(":", 1)[1]))
        _set_pers_fb(ok, msg)
    elif act.startswith("set_cursor:"):
        _, cur_sz = _read_cursor_cfg()
        STATE.set("personalization", "cursor_pick_open", False)
        STATE.set("personalization", "cursor_query", "")
        ok, msg = _apply_cursor_cfg(act.split(":", 1)[1], cur_sz)
        _set_pers_fb(ok, msg)
    elif act.startswith("del_cursor:"):
        ok, msg = _delete_cursor_theme(act.split(":", 1)[1])
        _set_pers_fb(ok, msg)
    elif act == "prompt_cursor_zip":
        cc_panel.request_prompt("cc_pers_cursor_zip", t("cc_personalization.cur_zip_prompt"), "~/Downloads/", title=t("cc_personalization.cur_zip_title"), required=True, completion=("path_ext", [".zip", ".tar.gz", ".tgz", ".tar.xz"]))
        return True
    elif act == "paste_cursor_zip":
        clip = _read_clipboard_text()
        ok, msg, _nm = convert_windows_cursor_zip(clip)
        _set_pers_fb(ok, msg)
    elif act == "pick_cursor_zip":
        if _launch_yazi_picker(cc_panel, is_folder=False, target_mode="cursor_zip"):
            if cc_panel.shell:
                cc_panel.shell.refresh_view()
            return True
        cc_panel.request_prompt("cc_pers_cursor_zip", t("cc_personalization.cur_zip_prompt"), "~/Downloads/", title=t("cc_personalization.cur_zip_title"), required=True, completion=("path_ext", [".zip", ".tar.gz", ".tgz", ".tar.xz"]))
        return True
    elif act.startswith("tbrk:"):
        spec = _TITLE_BRK_PRESETS.get(act.split(":", 1)[1])
        if spec:
            THEME["ascii"]["title_brackets"] = [spec[0], spec[1]]
            _save_theme_file(THEME_PATH, THEME)
    elif act.startswith("sbrk:"):
        spec = _SLIDER_BRK_PRESETS.get(act.split(":", 1)[1])
        if spec:
            THEME["ascii"]["slider_brackets"] = [spec[0], spec[1]]
            _save_theme_file(THEME_PATH, THEME)
    elif act.startswith("slglyph:"):
        spec = _SLIDER_GLYPH_PRESETS.get(act.split(":", 1)[1])
        if spec:
            THEME["ascii"]["slider_fill"] = spec[0]
            THEME["ascii"]["slider_empty"] = spec[1]
            _save_theme_file(THEME_PATH, THEME)
    elif act.startswith("pdots:"):
        spec = _PILL_DOT_PRESETS.get(act.split(":", 1)[1])
        if spec:
            THEME["ascii"]["pill_dots"] = [spec[0], spec[1]]
            _save_theme_file(THEME_PATH, THEME)
    elif act.startswith("tcaps:"):
        spec = _TAB_CAP_PRESETS.get(act.split(":", 1)[1])
        if spec:
            THEME["ascii"]["tab_caps"] = [spec[0], spec[1]]
            _save_theme_file(THEME_PATH, THEME)
    elif act.startswith("cicons:"):
        spec = _COLLAPSE_PRESETS.get(act.split(":", 1)[1])
        if spec:
            THEME["ascii"]["collapsible_icons"] = [spec[0], spec[1]]
            _save_theme_file(THEME_PATH, THEME)
    elif act.startswith("sbox:"):
        spec = _SEARCH_BOX_PRESETS.get(act.split(":", 1)[1])
        if spec:
            THEME["ascii"]["search_prefix"] = spec[0]
            THEME["ascii"]["search_cursor"] = spec[1]
            _save_theme_file(THEME_PATH, THEME)
    elif act.startswith("trule:"):
        idx = int(act.split(":", 1)[1])
        if 0 <= idx < len(_TABLE_RULE_PRESETS):
            THEME["ascii"]["table_rule"] = _TABLE_RULE_PRESETS[idx]
            _save_theme_file(THEME_PATH, THEME)
    elif act.startswith("kvsep:"):
        idx = int(act.split(":", 1)[1])
        if 0 <= idx < len(_KV_SEP_PRESETS):
            THEME["ascii"]["kv_separator"] = _KV_SEP_PRESETS[idx]
            _save_theme_file(THEME_PATH, THEME)
    elif act.startswith("kwidth:"):
        kw = max(14, min(32, int(act.split(":", 1)[1])))
        THEME["geometry"]["default_key_width"] = kw
        _persist_theme_key("geometry", "default_key_width", str(kw))
    elif act.startswith("winrad:"):
        rad = max(0, min(32, int(act.split(":", 1)[1])))
        THEME["geometry"]["window_border_radius"] = rad
        _persist_theme_key("geometry", "window_border_radius", str(rad))
        if cc_panel.shell:
            cc_panel.shell._apply_css()
    elif act.startswith("spacing:"):
        sp = int(act.split(":", 1)[1])
        THEME["geometry"]["card_spacing"] = sp
        _persist_theme_key("geometry", "card_spacing", str(sp))
    elif act.startswith("inner_pad:"):
        pad_v = int(act.split(":", 1)[1])
        set_layout_margins(inner_padding=pad_v, sync_bar=False)
        if cc_panel.shell and hasattr(cc_panel.shell, "apply_layout_margins"):
            cc_panel.shell.apply_layout_margins()
    elif act.startswith("outer_mar:"):
        mar_v = int(act.split(":", 1)[1])
        set_layout_margins(outer_margin=mar_v, sync_bar=True, reload_bar=True)
        if cc_panel.shell and hasattr(cc_panel.shell, "apply_layout_margins"):
            cc_panel.shell.apply_layout_margins()
    elif act == "inline_inner_pad":
        in_p, _ = get_layout_margins()
        start_inline_input(
            entry_id="cc_pers_inpad",
            shell=cc_panel.shell,
            trigger_uri=uri,
            initial_text=str(in_p),
            max_len=4,
            validator=lambda s: s.strip().isdigit() and 0 <= int(s.strip()) <= 64,
            on_commit=lambda s: handle_cc_personalization_prompt(cc_panel, "cc_pers_inner_pad", s.strip()),
        )
        return True
    elif act == "inline_outer_mar":
        _, out_m = get_layout_margins()
        start_inline_input(
            entry_id="cc_pers_outmar",
            shell=cc_panel.shell,
            trigger_uri=uri,
            initial_text=str(out_m),
            max_len=4,
            validator=lambda s: s.strip().isdigit() and 0 <= int(s.strip()) <= 64,
            on_commit=lambda s: handle_cc_personalization_prompt(cc_panel, "cc_pers_outer_mar", s.strip()),
        )
        return True
    elif act == "inline_spark":
        start_inline_input(
            entry_id="cc_pers_spark",
            shell=cc_panel.shell,
            trigger_uri=uri,
            initial_text=str(THEME["ascii"]["sparkline_chars"]),
            max_len=16,
            validator=lambda s: len(s.strip()) >= 2,
            on_commit=lambda s: handle_cc_personalization_prompt(cc_panel, "cc_pers_spark", s.strip()),
        )
        return True
    elif act == "inline_save_theme":
        start_inline_input(
            entry_id="cc_pers_save_theme",
            shell=cc_panel.shell,
            trigger_uri=uri,
            initial_text="nightcity-custom",
            max_len=28,
            validator=lambda s: bool(s.strip()),
            on_commit=lambda s: handle_cc_personalization_prompt(cc_panel, "cc_pers_save_theme", s.strip()),
        )
        return True
    elif act == "export_theme_prompt":
        cc_panel.request_prompt("cc_pers_exp_theme", t("cc_personalization.export_path_toml"), "~/ncui-theme-export.toml", title=t("cc_personalization.export_theme"), required=True, completion=("path_ext", [".toml"]))
        return True
    elif act == "import_theme_prompt":
        cc_panel.request_prompt("cc_pers_imp_theme", t("cc_personalization.import_toml_theme_path"), "~/", title=t("cc_personalization.import_theme"), required=True, completion=("path_ext", [".toml"]))
        return True
    elif act.startswith("load_theme:"):
        nm = act.split(":", 1)[1]
        p = os.path.join(THEMES_DIR, f"{nm}.toml")
        loaded = _load_toml(p)
        if loaded:
            _apply_loaded_theme(cc_panel, loaded, src_dir=os.path.dirname(p))
            _set_pers_fb(True, t("cc_personalization.loaded_theme", p0=nm))
    elif act.startswith("del_theme:"):
        nm = act.split(":", 1)[1]
        p = os.path.join(THEMES_DIR, f"{nm}.toml")
        if os.path.exists(p):
            os.remove(p)
            _set_pers_fb(True, t("cc_personalization.deleted_theme", p0=nm))

    if cc_panel.shell:
        cc_panel.shell.refresh_view()
    return True


def _apply_loaded_theme(cc_panel, loaded: dict, src_dir: str = "") -> None:
    merged = sanitize_theme_dict(_deep_merge(DEFAULT_THEME, loaded))
    THEME.clear()
    THEME.update(merged)
    win_op = float(THEME["colors"].get("window_opacity", 0.86))
    STATE.set("appearance", "transparency_pct", max(5, min(100, int(round(win_op * 100.0)))))
    _sync_theme_aliases()
    typo = THEME.get("typography", {})
    ct = str(typo.get("cursor_theme") or "").strip()
    cs = int(typo.get("cursor_size") or 24)
    ca = os.path.basename(str(typo.get("cursor_archive") or "").strip())
    if ct:
        installed = is_ncui_cursor_name(ct) or any(os.path.isdir(os.path.join(r, ct, "cursors")) for r in SYS_ICONS_DIRS)
        if not installed and ca:
            for cand_dir in (src_dir, CURSORS_STORE_DIR):
                cand_zip = os.path.join(cand_dir, ca) if cand_dir else ""
                if cand_zip and os.path.isfile(cand_zip):
                    convert_windows_cursor_zip(cand_zip, custom_name=ct)
                    break
        _apply_cursor_cfg(ct, cs)
    else:
        sync_ncui_cursor_if_active()
    _save_theme_file(THEME_PATH, THEME)
    if cc_panel and cc_panel.shell:
        cc_panel.shell._apply_css()


def handle_cc_personalization_prompt(cc_panel, action_id: str, text: str) -> bool:
    global _WP_USER_TOUCHED
    if handle_cc_waybar_prompt(cc_panel, action_id, text):
        return True
    val = str(text or "").strip()
    if not val:
        return False
    target_out = str(STATE.get("desktop", "target_out", "all"))

    if action_id in ("cc_desk_scene", "cc_desk_img"):
        p = os.path.expanduser(val)
        if os.path.isfile(p):
            _cancel_pending_yt_dl()
            STATE.set("desktop", "wp_mode", "scene")
            STATE.set("desktop", "scene_path", p)
            _WP_USER_TOUCHED = True
            _stage_current_to_selected()
            _set_pers_fb(True, t("cc_personalization.wp_staged_scene_msg", p0=os.path.basename(p)))
        else:
            _set_pers_fb(False, t("cc_personalization.wp_invalid_path_err", p0=val))
        return True
    if action_id == "cc_desk_yt":
        _handle_youtube_input(cc_panel, val, from_clipboard=False)
        return True
    if action_id == "cc_desk_folder":
        p = os.path.expanduser(val)
        if os.path.isdir(p):
            _cancel_pending_yt_dl()
            STATE.set("desktop", "wp_mode", "slideshow")
            STATE.set("desktop", "slide_folder", p)
            _WP_USER_TOUCHED = True
            _stage_current_to_selected()
            _set_pers_fb(True, t("cc_personalization.wp_staged_scene_msg", p0=os.path.basename(p.rstrip("/")) or p))
        else:
            _set_pers_fb(False, t("cc_personalization.wp_invalid_path_err", p0=val))
        return True
    if action_id == "cc_desk_interval":
        STATE.set("desktop", "slide_interval", val)
        _WP_USER_TOUCHED = True
        _stage_current_to_selected()
        return True
    if action_id == "cc_desk_solid_hex":
        cleaned = val.strip()
        if re.match(r"^#?[0-9a-fA-F]{6}$", cleaned):
            hex_col = f"#{cleaned.lstrip('#').upper()}"
            STATE.set("desktop", "solid_hex", hex_col)
            STATE.set("desktop", "wp_mode", "solid")
            _WP_USER_TOUCHED = True
            _stage_current_to_selected()
            _set_pers_fb(True, t("cc_personalization.wp_solid_staged_msg", p0=hex_col))
        else:
            _set_pers_fb(False, t("cc_personalization.wp_invalid_hex_err", p0=val))
        return True

    if action_id.startswith("cc_pers_col|"):
        k = action_id.split("|", 1)[1]
        if re.match(r"^#[0-9a-fA-F]{6}$", val):
            if k == "surface_bg":
                norm_hx = set_surface_bg(val.lower(), sync_waybar=False)
                _refresh_surface_companions(norm_hx)
                _save_theme_file(THEME_PATH, THEME)
                if cc_panel.shell:
                    cc_panel.shell._apply_css()
                _set_pers_fb(True, t("cc_personalization.color_updated_to", p0="surface_bg", p1=norm_hx))
            else:
                target_axis = "intent" if k == "accent" else k
                THEME["colors"].update(derive_axis_companions(target_axis, val.lower(), get_surface_bg_hex()))
                _sync_theme_aliases()
                _save_theme_file(THEME_PATH, THEME)
                if target_axis in ("intent", "fault"):
                    sync_ncui_cursor_if_active()
                if cc_panel.shell:
                    cc_panel.shell._apply_css()
                _set_pers_fb(True, t("cc_personalization.updated", p0=target_axis, p1=val.lower()))
        else:
            _set_pers_fb(False, t("cc_personalization.invalid_hex_color", p0=val))
        return True
    if action_id.startswith("cc_pers_wb_col|"):
        wk = action_id.split("|", 1)[1]
        if wk in ("wb_bg", "wb_pill_bg", "wb_fg", "wb_accent", "wb_active", "wb_warn", "wb_crit") and re.match(r"^#[0-9a-fA-F]{6}$", val):
            THEME.setdefault("colors", {})[wk] = val.lower()
            _sync_theme_aliases()
            _save_theme_file(THEME_PATH, THEME)
            _set_pers_fb(True, t("cc_personalization.color_updated_to", p0=wk, p1=val.upper()))
        else:
            _set_pers_fb(False, t("cc_personalization.invalid_hex_color", p0=val))
        return True
    if action_id == "cc_pers_font":
        THEME["typography"]["font_family"] = val
        _persist_theme_key("typography", "font_family", f'"{val}"')
        if cc_panel.shell:
            cc_panel.shell._apply_css()
        _set_pers_fb(True, t("cc_personalization.font_set_to", p0=val))
        return True
    if action_id == "cc_pers_cursor":
        _, cur_s = _read_cursor_cfg()
        ok, msg = _apply_cursor_cfg(val, cur_s)
        _set_pers_fb(ok, msg)
        return True
    if action_id == "cc_pers_cursor_zip":
        ok, msg, _nm = convert_windows_cursor_zip(val)
        _set_pers_fb(ok, msg)
        return True
    if action_id == "cc_pers_inner_pad" and val.isdigit():
        in_p, _ = set_layout_margins(inner_padding=int(val), sync_bar=False)
        if cc_panel.shell and hasattr(cc_panel.shell, "apply_layout_margins"):
            cc_panel.shell.apply_layout_margins()
        _set_pers_fb(True, f"{t('cc_personalization.inner_pad_lbl')}: {in_p}px")
        return True
    if action_id == "cc_pers_outer_mar" and val.isdigit():
        _, out_m = set_layout_margins(outer_margin=int(val), sync_bar=True, reload_bar=True)
        if cc_panel.shell and hasattr(cc_panel.shell, "apply_layout_margins"):
            cc_panel.shell.apply_layout_margins()
        _set_pers_fb(True, f"{t('cc_personalization.outer_mar_lbl')}: {out_m}px")
        return True
    if action_id == "cc_pers_spark" and len(val) >= 2:
        THEME["ascii"]["sparkline_chars"] = val
        _persist_theme_key("ascii", "sparkline_chars", f'"{val}"')
        _set_pers_fb(True, t("cc_personalization.sparkline_ramp_updated"))
        return True
    if action_id == "cc_pers_save_theme":
        safe = re.sub(r"[^a-zA-Z0-9_-]+", "-", val).strip("-") or "custom"
        cur_t, cur_s = _read_cursor_cfg()
        THEME.setdefault("typography", {}).update({
            "cursor_theme": cur_t,
            "cursor_size": cur_s,
            "cursor_archive": f"{cur_t}.zip" if os.path.isfile(os.path.join(CURSORS_STORE_DIR, f"{cur_t}.zip")) else "",
        })
        ok, msg = _save_theme_file(os.path.join(THEMES_DIR, f"{safe}.toml"), THEME)
        _set_pers_fb(ok, msg)
        return True
    if action_id == "cc_pers_exp_theme":
        exp_p = os.path.abspath(os.path.expanduser(val))
        cur_t, cur_s = _read_cursor_cfg()
        arch = f"{cur_t}.zip" if os.path.isfile(os.path.join(CURSORS_STORE_DIR, f"{cur_t}.zip")) else ""
        THEME.setdefault("typography", {}).update({"cursor_theme": cur_t, "cursor_size": cur_s, "cursor_archive": arch})
        ok, msg = _save_theme_file(exp_p, THEME)
        if ok and arch:
            src_z = os.path.join(CURSORS_STORE_DIR, arch)
            dst_z = os.path.join(os.path.dirname(exp_p), arch)
            if os.path.isfile(src_z) and os.path.abspath(src_z) != os.path.abspath(dst_z):
                shutil.copy2(src_z, dst_z)
        _set_pers_fb(ok, msg)
        return True
    if action_id == "cc_pers_imp_theme":
        fp = os.path.expanduser(val)
        loaded = _load_toml(fp)
        if loaded:
            _apply_loaded_theme(cc_panel, loaded, src_dir=os.path.dirname(os.path.abspath(fp)))
            _set_pers_fb(True, t("cc_personalization.imported_theme_from", p0=os.path.basename(fp)))
        else:
            _set_pers_fb(False, t("cc_personalization.could_not_load_toml", p0=fp))
        return True
    return False
