#!/usr/bin/env python3
import atexit
import base64
import colorsys
import copy
import ctypes
import fcntl
import hashlib
import json
import math
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
try:
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
except ImportError:
    ASCIIFY_DEFAULTS = {}
    ASCII_CHAR_SETS = {}
    ASCII_COLOR_MODES = {}
    clear_ascii_cache = lambda: None
    get_ascii_cache_stats = lambda: {"count": 0, "bytes": 0}
    get_cached_ascii_path = lambda *a, **k: ""
    image_cache_key = lambda *a, **k: ""
    is_pillow_available = lambda: False
    normalize_asciify_cfg = lambda *a, **k: {}
    render_ascii_folder = None
    render_ascii_image = None
from core import wallpaper as _wp
from core.paths import CORE_ROOT, find_bin, find_package_file
from core.config import (
    BOX_STYLES,
    DEFAULT_THEME,
    NOTIF_STYLES,
    THEME,
    VOLUME_OVERDRIVE_STYLES,
    _deep_merge,
    _load_toml,
    _parse_rgba_components,
    _theme_path,
    _unique_existing_paths,
    _update_toml_key_in_section,
    apply_waybar_palette,
    apply_waybar_transparency,
    derive_axis_companions,
    get_cache_dir,
    get_config_dir,
    get_layout_margins,
    get_surface_bg_hex,
    get_ui_transparency_pct,
    get_waybar_sync_color,
    get_waybar_sync_opacity,
    get_window_opacity,
    is_ncmatrix_available,
    resolve_ncmatrix_bin,
    sanitize_theme_dict,
    set_box_opacity_pct,
    set_hover_help_opacity_pct,
    set_hover_help_style,
    set_layout_margins,
    set_row_gap,
    set_surface_bg,
    set_sys_notif_opacity_pct,
    set_sys_notif_style,
    set_toast_opacity_pct,
    set_toast_style,
    set_ui_transparency_pct,
    set_volume_overdrive_style,
    set_waybar_sync_color,
    set_waybar_sync_opacity,
    sync_waybar_colors_from_theme,
)
from core.i18n import get_active_language, t
from core.state import STATE
from panels.cc_engine_display import get_niri_outputs_data
try:
    from panels.cc_engine_waybar import handle_cc_waybar_link, handle_cc_waybar_prompt
except ImportError:
    handle_cc_waybar_link = lambda *_a, **_k: False
    handle_cc_waybar_prompt = lambda *_a, **_k: False
from ui.canvas import Canvas
from ui.kit import (
    PALETTE,
    ActionBtn,
    Badge,
    Box,
    Divider,
    ProgressBar,
    SelectPill,
    Slider,
    Sparkline,
    StateBadge,
    Txt,
    cancel_inline_entry,
    feedback_cards,
    get_semantic_palette,
    set_feedback,
    set_inline_entry_error,
    set_slider_meta_value,
    start_inline_input,
)

def _fmt_cm_spawn(raw, default: float = 1.5) -> str:
    """Format ncmatrix trail spawn rate clamped to [0.0, 3.0] with 0.1 precision."""
    if raw is None or str(raw).strip() == "":
        return f"{default:.1f}"
    try:
        v = float(str(raw).strip().replace(",", "."))
    except (TypeError, ValueError):
        v = default
    v = max(0.0, min(3.0, round(v, 1)))
    if v == 0.0:
        return "0.0"
    return f"{v:.1f}"


def _norm_cm_head(raw) -> str:
    """Normalize trail head color option ('white', 'match', or 'blend')."""
    s = str("" if raw is None else raw).strip().lower()
    if s in ("white", "1", "on", "true"):
        return "white"
    if s in ("match", "color", "trail", "shader", "0", "false", "off"):
        return "match"
    return "blend"


def _fmt_cm_var(raw, default: float = 5.0) -> str:
    """Format speed variance clamped to [0.0, 10.0] with 0.1 precision."""
    try:
        v = float(str(default if (raw is None or str(raw).strip() == "") else raw).strip().replace(",", "."))
    except (TypeError, ValueError):
        v = default
    v = max(0.0, min(10.0, round(v, 1)))
    return "0.0" if v == 0.0 else f"{v:.1f}"


def _fmt_cm_glow(raw, default: float = 0.60) -> str:
    """Format CRT phosphor glow clamped to [0.00, 1.00] in 0.05 steps."""
    try:
        v = float(str(default if (raw is None or str(raw).strip() == "") else raw).strip().replace(",", "."))
    except (TypeError, ValueError):
        v = default
    v = max(0.0, min(1.0, round(v * 20.0) / 20.0))
    return f"{v:.2f}"


def _norm_cm_overlap(raw) -> str:
    s = str(raw or "").strip().lower()
    if s in ("parallax", "overwrite", "cover", "1"):
        return "parallax"
    if s in ("additive", "screen", "boost", "2"):
        return "additive"
    return "hybrid"


def _fmt_cm_parallax(raw, default: float = 0.50) -> str:
    """Format speed-linked luminescence attenuation strength in [0.00, 1.00] (0.05 steps)."""
    s = str(default if (raw is None or str(raw).strip() == "") else raw).strip().lower().replace(",", ".")
    if s in ("on", "true", "yes"):
        return f"{default:.2f}"
    if s in ("off", "false", "no"):
        return "0.00"
    try:
        v = float(s)
        if v > 1.0 and v <= 100.0:
            v *= 0.01
    except (TypeError, ValueError):
        v = default
    v = max(0.0, min(1.0, round(v * 20.0) / 20.0))
    return f"{v:.2f}"


def _norm_cm_decay(raw) -> str:
    s = str(raw or "").strip().lower()
    return "linear" if s in ("linear", "classic", "1") else "exponential"


def _norm_cm_shimmer(raw) -> str:
    s = str(raw or "").strip().lower()
    if s in ("off", "none", "0", "false"):
        return "off"
    if s in ("low", "1"):
        return "low"
    if s in ("med", "medium", "2"):
        return "med"
    if s in ("high", "3", "on", "true"):
        return "high"
    return "insane"


def _norm_cm_mirror(raw) -> str:
    s = str(raw or "").strip().lower()
    if s in ("off", "normal", "0", "false"):
        return "off"
    if s in ("on", "all", "mirror", "1", "true"):
        return "on"
    return "random"


def _norm_cm_tail(raw) -> str:
    s = str(raw or "").strip().lower()
    if s in ("short", "0"):
        return "short"
    if s in ("normal", "medium", "1"):
        return "normal"
    return "long"


def _norm_cm_density(raw) -> str:
    s = str(raw or "").strip().lower()
    return "classic" if s in ("classic", "0", "off", "false", "no") else "dense"

NC_DESKTOP_BASE = os.path.expanduser("~/.local/nc-desktop")
WALL_DIR = os.path.join(NC_DESKTOP_BASE, "wallpapers")
PROF_DIR = os.path.join(NC_DESKTOP_BASE, "profiles")


def _xdg_cfg_dir() -> str:
    val = os.environ.get("XDG_CONFIG_HOME", "").strip()
    return os.path.expanduser(val) if val else os.path.expanduser("~/.config")


def _xdg_data_dir() -> str:
    val = os.environ.get("XDG_DATA_HOME", "").strip()
    return os.path.expanduser(val) if val else os.path.expanduser("~/.local/share")


def _themes_dir() -> str:
    return os.path.join(get_config_dir(), "themes")


def _cursors_store_dir() -> str:
    return os.path.join(get_config_dir(), "cursors")


def _user_icons_dir() -> str:
    return os.path.join(_xdg_data_dir(), "icons")


def _sys_icons_dirs() -> tuple[str, ...]:
    return (_user_icons_dir(), os.path.expanduser("~/.icons"), "/usr/share/icons")


def _niri_config_file() -> str:
    return os.path.join(_xdg_cfg_dir(), "niri", "config.kdl")


def _yt_wall_dir() -> str:
    return os.path.join(get_cache_dir(), "wallpapers")

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
    # 1. CYBERDECK — Tema Capostipite di NC-UI
    "cyberdeck": {
        "label": "Cyberdeck",
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
    set_feedback("personalization", ok, msg)


def _feedback_cards() -> list:
    return feedback_cards("personalization", "cc_personalization.theme_personalization_status")


def _persist_theme_key(section: str, key: str, raw_val: str) -> None:
    _update_toml_key_in_section(
        section,
        key,
        raw_val,
        target_files=(_theme_path(),),
        create_fallback=True,
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


def _sync_theme_aliases() -> None:
    sanitize_theme_dict(THEME)
    get_semantic_palette()
    try:
        from panels.cc_engine_zsh import sync_zsh_theme_if_enabled
        sync_zsh_theme_if_enabled()
    except Exception:
        pass
    try:
        from panels.cc_engine_ecosystem import sync_ecosystem_themes_if_enabled
        sync_ecosystem_themes_if_enabled()
    except Exception:
        pass
    try:
        from core.wallpaper import sync_ncmatrix_theme_wallpapers
        sync_ncmatrix_theme_wallpapers()
    except Exception:
        pass


def _apply_full_preset(cc_panel, pid: str) -> bool:
    spec = THEME_PRESETS.get(pid)
    if not spec:
        return False
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
    _save_theme_file(_theme_path(), THEME)
    _cur.sync_ncui_cursor_if_active()
    if cc_panel and cc_panel.shell:
        cc_panel.shell._apply_css()
    _set_pers_fb(True, t("cc_personalization.applied_preset", p0=spec["label"]))
    return True


def _list_saved_themes() -> list[str]:
    t_dir = _themes_dir()
    if not os.path.exists(t_dir):
        return []
    return sorted([f[:-5] for f in os.listdir(t_dir) if f.endswith(".toml")])


def _norm_hex_arg(raw: str, allow_alpha: bool = False) -> str | None:
    s = str(raw or "").strip().lstrip("#").lower()
    if re.fullmatch(r"[0-9a-f]{3}", s):
        return "#" + "".join(c * 2 for c in s)
    if re.fullmatch(r"[0-9a-f]{6}", s):
        return "#" + s
    if re.fullmatch(r"[0-9a-f]{8}", s):
        return "#" + (s if allow_alpha else s[:6])
    return None


def handle_cc_personalization_link(cc_panel, uri: str) -> bool:
    if not isinstance(uri, str) or not uri:
        return False
    if handle_cc_waybar_link(cc_panel, uri):
        return True

    if uri.startswith("cc_desk:"):
        act = uri[len("cc_desk:"):]
        if act != "cm_col_custom":
            cancel_inline_entry(refresh=False)
        outs = sorted(_media._get_niri_outputs().keys())
        sel = _media.get_selected_monitors(outs)
        target_cli = "all" if len(sel) == len(outs) else (sel[0] if sel else "all")

        if act == "toggle_fps":
            STATE.set("desktop", "preview_15fps", not _media.is_live_15fps_enabled())
        elif act == "close_yazi":
            _yazi._close_embedded_yazi(cancelled=True)
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
                if _media.is_wp_user_touched():
                    _media._stage_current_to_selected()
                else:
                    cur_staged = STATE.get("desktop", "staged_preview", {}) or {}
                    if cur_staged:
                        STATE.set("desktop", "staged_preview", {k: v for k, v in cur_staged.items() if k in new_sel})
                    _media.sync_editor_from_live_monitors()
        elif act.startswith("mode:"):
            m_id = act.split(":", 1)[1]
            if m_id == "cmatrix":
                m_id = "ncmatrix"
            if m_id not in ("scene", "slideshow", "ncmatrix", "solid"):
                return False
            if m_id == "ncmatrix" and not is_ncmatrix_available():
                _set_pers_fb(False, t("cc_personalization.wp_optdep_ncmatrix"))
                if cc_panel and getattr(cc_panel, "shell", None):
                    cc_panel.shell.refresh_view()
                return True
            _media._cancel_pending_yt_dl()
            _media._cancel_pending_ascii_apply()
            _yazi._close_embedded_yazi(cancelled=True)
            STATE.set("desktop", "wp_mode", m_id)
            if m_id == "solid" and not STATE.get("desktop", "solid_hex"):
                STATE.set("desktop", "solid_hex", "#000000")
            _media.set_wp_user_touched(True)
            _media._stage_current_to_selected()
        elif act.startswith("solid_hex:"):
            hex_col = _media._resolve_solid_hex(act.split(":", 1)[1])
            STATE.set("desktop", "solid_hex", hex_col)
            STATE.set("desktop", "wp_mode", "solid")
            _media.set_wp_user_touched(True)
            _media._stage_current_to_selected()
        elif act == "inline_solid_hex":
            if not cc_panel or not cc_panel.shell:
                return False
            cur_hx = _media._resolve_solid_hex(STATE.get("desktop", "solid_hex", "#000000") or "#000000")
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
            fit_v = act.split(":", 1)[1]
            if fit_v not in ("fill", "fit", "center", "stretch"):
                return False
            STATE.set("desktop", "scene_fit", fit_v)
            _media.set_wp_user_touched(True)
            _media._stage_current_to_selected()
        elif act.startswith("vid_loop:"):
            loop_v = act.split(":", 1)[1]
            if not loop_v:
                return False
            STATE.set("desktop", "video_loop", loop_v)
            _media.set_wp_user_touched(True)
            _media._stage_current_to_selected()
        elif act.startswith("pl_ord:"):
            ord_v = act.split(":", 1)[1]
            if not ord_v:
                return False
            STATE.set("desktop", "playlist_order", ord_v)
            _media.set_wp_user_touched(True)
            _media._stage_current_to_selected()
        elif act.startswith("yt_deliv:"):
            deliv = act.split(":", 1)[1]
            if deliv not in ("download", "stream"):
                return False
            STATE.set("desktop", "yt_delivery", deliv)
            cur_sc = str(STATE.get("desktop", "scene_path", "") or "")
            yt = _media._resolve_yt_source(cur_sc)
            if not yt["ok"] and (not cur_sc or _media.detect_media_kind(cur_sc) in ("YT VIDEO", "YT PLAYLIST", "")):
                yt = _media._resolve_yt_source(str(STATE.get("desktop", "yt_url", "") or ""))
            if deliv == "stream":
                _media._cancel_pending_yt_dl()
            if yt["ok"]:
                STATE.set("desktop", "yt_url", yt["canonical_url"])
                _media._ensure_youtube_thumbnail(yt, cc_panel)
                if deliv == "stream":
                    STATE.set("desktop", "scene_path", yt["canonical_url"])
                    _media.set_wp_user_touched(True)
                    _media._stage_current_to_selected()
                else:
                    cached = _media._find_cached_yt_media(yt)
                    if cached:
                        STATE.set("desktop", "scene_path", cached)
                        _media.set_wp_user_touched(True)
                        _media._stage_current_to_selected()
                    else:
                        _media._start_youtube_download(cc_panel, yt["canonical_url"], yt_info=yt)
            else:
                _media.set_wp_user_touched(True)
                _media._stage_current_to_selected()
        elif act == "clear_scene":
            _media._cancel_pending_yt_dl()
            _yazi._close_embedded_yazi(cancelled=True)
            STATE.set("desktop", "scene_path", "")
            STATE.set("desktop", "yt_url", "")
            _media.set_wp_user_touched(True)
            _media._stage_current_to_selected()
            _set_pers_fb(True, t("cc_personalization.wp_scene_cleared"))
        elif act == "paste_yt":
            _yazi._close_embedded_yazi(cancelled=True)
            clip = _media._read_clipboard_text()
            _media._handle_youtube_input(cc_panel, clip, from_clipboard=True)
        elif act in ("pl_prev", "pl_next"):
            mons = sel or outs
            ipc_cmd = "playlist-prev" if act == "pl_prev" else "playlist-next"
            sent = any(_media._send_mpv_ipc(m, [ipc_cmd]) for m in mons)
            if not sent:
                for m in mons:
                    if act == "pl_prev":
                        _wp.restore_all(m)
                    else:
                        _wp.control_slideshow(m, "next")
            _set_pers_fb(True, t("cc_personalization.wp_pl_track_prev_msg" if act == "pl_prev" else "cc_personalization.wp_pl_track_next_msg"))
        elif act.startswith("vid_mute:"):
            m_val = (act.split(":", 1)[1] == "1")
            STATE.set("desktop", "video_mute", m_val)
            if not _media._update_live_video_audio_if_running(sel or outs, mute=m_val):
                _media.set_wp_user_touched(True)
            _media._stage_current_to_selected()
        elif act.startswith("vid_vol:"):
            try:
                v_val = max(0, min(100, int(round(float(act.split(":", 1)[1])))))
            except (ValueError, TypeError, OverflowError):
                return False
            STATE.set("desktop", "video_volume", v_val)
            if not _media._update_live_video_audio_if_running(sel or outs, volume=v_val):
                _media.set_wp_user_touched(True)
            _media._stage_current_to_selected()
        elif act.startswith("slide_int:"):
            iv = act.split(":", 1)[1]
            if not iv:
                return False
            STATE.set("desktop", "slide_interval", iv)
            _media.set_wp_user_touched(True)
            _media._stage_current_to_selected()
        elif act.startswith("slide_ord:"):
            so = act.split(":", 1)[1]
            if not so:
                return False
            STATE.set("desktop", "slide_order", so)
            _media.set_wp_user_touched(True)
            _media._stage_current_to_selected()
        elif act.startswith("slide_tr:"):
            st_v = act.split(":", 1)[1]
            if not st_v:
                return False
            STATE.set("desktop", "slide_trans", st_v)
            _media.set_wp_user_touched(True)
            _media._stage_current_to_selected()
        elif act.startswith("slide_spd:"):
            ss = act.split(":", 1)[1]
            if not ss:
                return False
            STATE.set("desktop", "slide_speed", ss)
            _media.set_wp_user_touched(True)
            _media._stage_current_to_selected()
        elif act.startswith("cm_charset:"):
            cs = act.split(":", 1)[1]
            if not cs:
                return False
            if cs == "katakana" and not _wp.has_katakana_font():
                _set_pers_fb(False, t("cc_personalization.wp_optdep_katakana"))
                if cc_panel and getattr(cc_panel, "shell", None):
                    cc_panel.shell.refresh_view()
                return True
            STATE.set("desktop", "cm_charset", cs)
            _media.set_wp_user_touched(True)
            _media._stage_current_to_selected()
        elif act.startswith("cm_col:"):
            col_v = act.split(":", 1)[1]
            if not col_v:
                return False
            set_inline_entry_error("cm_col_custom", "")
            STATE.set("desktop", "cm_color", col_v)
            _media.set_wp_user_touched(True)
            _media._stage_current_to_selected()
        elif act == "cm_col_custom":
            if not cc_panel or not cc_panel.shell:
                return False
            def _on_cm_hex_commit(hex_digits: str) -> None:
                STATE.set("desktop", "cm_color", f"#{hex_digits.upper()}")
                _media.set_wp_user_touched(True)
                _media._stage_current_to_selected()

            def _on_cm_hex_invalid(_raw: str) -> None:
                set_inline_entry_error("cm_col_custom", t("cc_personalization.wp_err_invalid_hex"))

            start_inline_input(
                "cm_col_custom",
                shell=cc_panel.shell,
                trigger_uri=uri,
                max_len=6,
                strip_leading_hash=True,
                uppercase=True,
                validator=lambda s: bool(re.match(r"^[0-9A-Fa-f]{6}$", s)),
                on_commit=_on_cm_hex_commit,
                on_invalid=_on_cm_hex_invalid,
            )
        elif act.startswith("cm_spd:"):
            try:
                sp_v = max(1, min(10, int(act.split(":", 1)[1])))
            except ValueError:
                sp_v = 4
            STATE.set("desktop", "cm_speed", str(sp_v))
            _media.set_wp_user_touched(True)
            _media._stage_current_to_selected()
        elif act.startswith("cm_head:"):
            STATE.set("desktop", "cm_head_color", _norm_cm_head(act.split(":", 1)[1]))
            _media.set_wp_user_touched(True)
            _media._stage_current_to_selected()
        elif act.startswith("cm_spawn:"):
            sp_s = _fmt_cm_spawn(act.split(":", 1)[1])
            STATE.set("desktop", "cm_spawn_rate", sp_s, immediate=False)
            set_slider_meta_value("cc_desk:cm_spawn", sp_s)
            _media.set_wp_user_touched(True)
            _media._stage_current_to_selected(fast_live=True)
            if cc_panel:
                cc_panel.request_throttled_refresh()
            return True
        elif act.startswith("cm_var:"):
            var_s = _fmt_cm_var(act.split(":", 1)[1])
            STATE.set("desktop", "cm_speed_variance", var_s, immediate=False)
            set_slider_meta_value("cc_desk:cm_var", var_s)
            _media.set_wp_user_touched(True)
            _media._stage_current_to_selected(fast_live=True)
            if cc_panel:
                cc_panel.request_throttled_refresh()
            return True
        elif act.startswith("cm_overlap:"):
            STATE.set("desktop", "cm_overlap", _norm_cm_overlap(act.split(":", 1)[1]))
            _media.set_wp_user_touched(True)
            _media._stage_current_to_selected()
        elif act.startswith("cm_parallax:"):
            par_s = _fmt_cm_parallax(act.split(":", 1)[1])
            STATE.set("desktop", "cm_parallax", par_s, immediate=False)
            set_slider_meta_value("cc_desk:cm_parallax", par_s)
            _media.set_wp_user_touched(True)
            _media._stage_current_to_selected(fast_live=True)
            if cc_panel:
                cc_panel.request_throttled_refresh()
            return True
        elif act.startswith("cm_glow:"):
            glow_s = _fmt_cm_glow(act.split(":", 1)[1])
            STATE.set("desktop", "cm_glow", glow_s, immediate=False)
            set_slider_meta_value("cc_desk:cm_glow", glow_s)
            _media.set_wp_user_touched(True)
            _media._stage_current_to_selected(fast_live=True)
            if cc_panel:
                cc_panel.request_throttled_refresh()
            return True
        elif act.startswith("cm_decay:"):
            STATE.set("desktop", "cm_decay", _norm_cm_decay(act.split(":", 1)[1]))
            _media.set_wp_user_touched(True)
            _media._stage_current_to_selected()
        elif act.startswith("cm_shimmer:"):
            STATE.set("desktop", "cm_shimmer", _norm_cm_shimmer(act.split(":", 1)[1]))
            _media.set_wp_user_touched(True)
            _media._stage_current_to_selected()
        elif act.startswith("cm_mirror:"):
            STATE.set("desktop", "cm_mirror", _norm_cm_mirror(act.split(":", 1)[1]))
            _media.set_wp_user_touched(True)
            _media._stage_current_to_selected()
        elif act.startswith("cm_tail:"):
            STATE.set("desktop", "cm_tail_len", _norm_cm_tail(act.split(":", 1)[1]))
            _media.set_wp_user_touched(True)
            _media._stage_current_to_selected()
        elif act.startswith("cm_density:"):
            STATE.set("desktop", "cm_col_density", _norm_cm_density(act.split(":", 1)[1]))
            _media.set_wp_user_touched(True)
            _media._stage_current_to_selected()
        elif act.startswith("cm_size:"):
            sz_v = act.split(":", 1)[1]
            if not sz_v:
                return False
            STATE.set("desktop", "cm_size", sz_v)
            _media.set_wp_user_touched(True)
            _media._stage_current_to_selected()
        elif act.startswith("cm_trans:"):
            tr_val = act.split(":", 1)[1]
            if tr_val in ("flow", "drain", "instant"):
                STATE.set("desktop", "cm_trans", tr_val)
                _media.set_wp_user_touched(True)
                _media._stage_current_to_selected()
        elif act == "apply_staged":
            _yazi._close_embedded_yazi(cancelled=True)
            pending_staged = dict(STATE.get("desktop", "staged_preview", {}) or {})
            if not pending_staged:
                sel_m = _media.get_selected_monitors(outs)
                if sel_m:
                    d_spec = _media._get_editor_draft_spec()
                    pending_staged = {m: d_spec for m in sel_m}
            if any(_media._spec_needs_asciify(sp) for sp in pending_staged.values()):
                _media._start_apply_staged_wallpapers(cc_panel, pending_staged)
            else:
                _media._cancel_pending_ascii_apply()
                ok, msg = _media._apply_staged_wallpapers()
                _set_pers_fb(ok, msg)
        elif act == "reset_staged":
            _media._cancel_pending_yt_dl()
            _media._cancel_pending_ascii_apply()
            _yazi._close_embedded_yazi(cancelled=True)
            set_inline_entry_error("cm_col_custom", "")
            STATE.set("desktop", "staged_preview", {})
            _media.set_wp_user_touched(False)
            _media.sync_editor_from_live_monitors()
            _set_pers_fb(True, t("cc_personalization.wp_reset_msg"))
        elif act.startswith("align:"):
            a_val = act.split(":", 1)[1]
            if a_val in ("top", "center", "bottom"):
                STATE.set("desktop", "scene_align", a_val)
                _media.set_wp_user_touched(True)
                _media._stage_current_to_selected()
        elif act.startswith("ascii_toggle:"):
            t_val = act.split(":", 1)[1]
            if t_val not in ("0", "1"):
                return False
            STATE.set("desktop", "ascii_enabled", t_val == "1")
            _media.set_wp_user_touched(True)
            _media._stage_current_to_selected()
        elif act.startswith("ascii_mode:"):
            m_val = act.split(":", 1)[1]
            if m_val in ASCII_COLOR_MODES:
                STATE.set("desktop", "ascii_mode", m_val)
                _media.set_wp_user_touched(True)
                _media._stage_current_to_selected()
        elif act.startswith("ascii_chars:"):
            c_val = act.split(":", 1)[1]
            if c_val in ASCII_CHAR_SETS:
                STATE.set("desktop", "ascii_chars", c_val)
                _media.set_wp_user_touched(True)
                _media._stage_current_to_selected()
        elif act == "inline_ascii_chars":
            if not cc_panel or not cc_panel.shell:
                return False
            cur_c = str(_media.get_editor_asciify_cfg().get("chars", "standard") or "")
            init_c = "" if cur_c in ASCII_CHAR_SETS else cur_c

            def _commit_ascii_chars(raw_s: str) -> None:
                # Do NOT .strip(): leading spaces represent empty dark cells.
                STATE.set("desktop", "ascii_chars", raw_s)
                _media.set_wp_user_touched(True)
                _media._stage_current_to_selected()

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
        elif ":" in act and act.split(":", 1)[0] in ("ascii_density", "ascii_tint", "ascii_scale", "ascii_contrast", "ascii_brightness"):
            k_cmd, raw_v = act.split(":", 1)
            lo, hi = {
                "ascii_density": (0.25, 2.5),
                "ascii_tint": (0.0, 1.0),
                "ascii_scale": (0.25, 1.5),
                "ascii_contrast": (0.5, 2.0),
                "ascii_brightness": (0.5, 2.0),
            }[k_cmd]
            try:
                f_v = float(raw_v)
                if not math.isfinite(f_v):
                    return False
                v = round(max(lo, min(hi, f_v)), 2)
                STATE.set("desktop", k_cmd, v)
                _media.set_wp_user_touched(True)
                _media._stage_current_to_selected()
            except (ValueError, TypeError, OverflowError):
                return False
            if cc_panel:
                cc_panel.request_throttled_refresh()
            return True
        elif act.startswith("ascii_invert:"):
            STATE.set("desktop", "ascii_invert", act.split(":", 1)[1] == "1")
            _media.set_wp_user_touched(True)
            _media._stage_current_to_selected()
        elif act == "ascii_defaults":
            cfg_def = dict(ASCIIFY_DEFAULTS)
            cfg_def["enabled"] = True
            _media._set_editor_asciify_cfg(cfg_def)
            _media.set_wp_user_touched(True)
            _media._stage_current_to_selected()
            _set_pers_fb(True, t("cc_personalization.wp_ascii_defaults_msg"))
        elif act == "ascii_cancel":
            _media._cancel_pending_ascii_apply()
            _set_pers_fb(True, t("cc_personalization.wp_ascii_cancelled"))
        elif act == "ascii_clear_cache":
            keep_paths = set()
            for mon_n in outs:
                l_src = _media._read_live_src(mon_n)
                rt = str(l_src.get("rendered_target") or "")
                if rt:
                    keep_paths.add(rt)
            removed_n, _freed_b = clear_ascii_cache(keep_paths=keep_paths)
            _media._LAST_ASCII_PREVIEW.clear()
            _set_pers_fb(True, t("cc_personalization.wp_ascii_cache_cleared", p0=removed_n))
        elif act == "prompt_yt":
            if not cc_panel:
                return False
            _yazi._close_embedded_yazi(cc_panel=cc_panel, cancelled=True)
            cur_u = str(STATE.get("desktop", "yt_url", "") or "https://youtube.com/watch?v=")
            cc_panel.request_prompt("cc_desk_yt", t("cc_personalization.wp_prompt_yt_ph"), cur_u, title=t("cc_personalization.wp_prompt_yt_title"), required=True)
            return True
        elif act == "pick_scene":
            if _yazi._launch_yazi_picker(cc_panel, is_folder=False):
                if cc_panel and cc_panel.shell:
                    cc_panel.shell.refresh_view()
                return True
            if not cc_panel:
                return False
            cur_s = str(STATE.get("desktop", "scene_path", "") or "~/")
            cc_panel.request_prompt("cc_desk_scene", t("cc_personalization.wp_prompt_scene_ph"), cur_s, title=t("cc_personalization.wp_prompt_scene_title"), required=True, completion="path_file")
            return True
        elif act == "type_scene":
            if not cc_panel:
                return False
            _yazi._close_embedded_yazi(cc_panel=cc_panel, cancelled=True)
            cur_s = str(STATE.get("desktop", "scene_path", "") or "~/")
            cc_panel.request_prompt("cc_desk_scene", t("cc_personalization.wp_prompt_scene_ph"), cur_s, title=t("cc_personalization.wp_prompt_scene_title"), required=True, completion="path_file")
            return True
        elif act == "pick_folder":
            if _yazi._launch_yazi_picker(cc_panel, is_folder=True):
                if cc_panel and cc_panel.shell:
                    cc_panel.shell.refresh_view()
                return True
            if not cc_panel:
                return False
            cur_d = str(STATE.get("desktop", "slide_folder", WALL_DIR) or WALL_DIR)
            cc_panel.request_prompt("cc_desk_folder", t("cc_personalization.wp_prompt_folder_ph"), cur_d, title=t("cc_personalization.wp_prompt_folder_title"), required=True, completion="path_dir")
            return True
        elif act == "type_folder":
            if not cc_panel:
                return False
            _yazi._close_embedded_yazi(cc_panel=cc_panel, cancelled=True)
            cur_d = str(STATE.get("desktop", "slide_folder", WALL_DIR) or WALL_DIR)
            cc_panel.request_prompt("cc_desk_folder", t("cc_personalization.wp_prompt_folder_ph"), cur_d, title=t("cc_personalization.wp_prompt_folder_title"), required=True, completion="path_dir")
            return True
        elif act == "inline_interval":
            if not cc_panel or not cc_panel.shell:
                return False
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
                    _wp.stop_output(m)
            elif act in ("pause", "resume"):
                if not any(_media._send_mpv_ipc(m, [act]) for m in mons):
                    for m in mons:
                        _wp.control_slideshow(m, act)
            elif act == "next":
                for m in mons:
                    _wp.control_slideshow(m, "next")
            else:
                for m in mons:
                    _wp.restore_all(m)
        else:
            return False
        if cc_panel and cc_panel.shell:
            cc_panel.shell.refresh_view()
        return True

    if not uri.startswith("cc_pers:"):
        return False
    act = uri[len("cc_pers:"):]
    cancel_inline_entry(refresh=False)

    if act == "noop":
        return True
    elif act.startswith(("box_op:", "sys_notif_op:", "hover_help_op:", "toast_op:")):
        op_cmd, raw_op = act.split(":", 1)
        setter = {
            "box_op": set_box_opacity_pct,
            "sys_notif_op": set_sys_notif_opacity_pct,
            "hover_help_op": set_hover_help_opacity_pct,
            "toast_op": set_toast_opacity_pct,
        }[op_cmd]
        try:
            clamped = setter(int(round(float(raw_op))))
            if op_cmd == "toast_op" and cc_panel and cc_panel.shell and hasattr(cc_panel.shell, "show_toast"):
                cc_panel.shell.show_toast(
                    t("cc_personalization.toast_opacity_preview", p0=clamped),
                    level="ok",
                    title=t("cc_personalization.toast_layer_opacity"),
                )
        except (ValueError, TypeError, OverflowError):
            return False
        if cc_panel:
            cc_panel.request_throttled_refresh()
        return True
    elif act == "test_sys_notif":
        import time as _time
        try:
            from panels.cc_engine_notifications import spawn_notification_popup
        except ImportError:
            spawn_notification_popup = None
        if spawn_notification_popup is not None:
            spawn_notification_popup(
                {
                    "id": int(_time.time() * 1000),
                    "app": "NC-UI",
                    "title": t("cc_notifications.preview_title"),
                    "body": t("cc_notifications.preview_body"),
                    "urgency": "normal",
                    "time": _time.strftime("%H:%M"),
                },
                force=True,
            )
        return True
    elif act == "wb_sync_op":
        set_waybar_sync_opacity(not get_waybar_sync_opacity())
    elif act == "wb_sync_col":
        set_waybar_sync_color(not get_waybar_sync_color())
    elif act == "wb_apply_preset_palette":
        sync_waybar_colors_from_theme(THEME["colors"])
        _save_theme_file(_theme_path(), THEME)
        apply_waybar_palette(force_palette=True)
        _set_pers_fb(True, t("cc_personalization.wb_palette_applied"))
    elif act == "wb_apply_palette":
        apply_waybar_palette(force_palette=True)
        _set_pers_fb(True, t("cc_personalization.wb_palette_applied"))
    elif act == "wb_sync_from_theme":
        sync_waybar_colors_from_theme(THEME["colors"])
        _save_theme_file(_theme_path(), THEME)
        _set_pers_fb(True, t("cc_personalization.wb_palette_synced"))
    elif act.startswith("wb_color:"):
        parts = act.split(":", 2)
        if len(parts) != 3 or not parts[1] or not parts[2]:
            return False
        _, wk, hx = parts
        hx_norm = _norm_hex_arg(hx, allow_alpha=True)
        if wk not in ("wb_bg", "wb_pill_bg", "wb_fg", "wb_accent", "wb_active", "wb_warn", "wb_crit") or not hx_norm:
            return False
        THEME.setdefault("colors", {})[wk] = hx_norm
        _sync_theme_aliases()
        _save_theme_file(_theme_path(), THEME)
        _set_pers_fb(True, t("cc_personalization.color_updated_to", p0=wk, p1=hx_norm.upper()))
    elif act.startswith("inline_wb_col:"):
        wk = act.split(":", 1)[1]
        if wk not in ("wb_bg", "wb_pill_bg", "wb_fg", "wb_accent", "wb_active", "wb_warn", "wb_crit") or not cc_panel or not cc_panel.shell:
            return False
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
            if cc_panel and cc_panel.shell:
                cc_panel.shell._apply_css()
        except (ValueError, TypeError):
            return False
    elif act.startswith("preset:"):
        if not _apply_full_preset(cc_panel, act.split(":", 1)[1]):
            return False
    elif act.startswith("box_style:"):
        st = act.split(":", 1)[1]
        if st not in BOX_STYLES:
            return False
        THEME["ascii"]["box_style"] = st
        THEME["ascii"]["chars"] = dict(BOX_STYLES[st])
        _persist_theme_key("ascii", "box_style", f'"{st}"')
        _save_theme_file(_theme_path(), THEME)
        if cc_panel and cc_panel.shell:
            cc_panel.shell._apply_css()
        _set_pers_fb(True, t("cc_personalization.box_style_set_to", p0=st))
    elif act.startswith("sys_notif_style:"):
        st = act.split(":", 1)[1].strip().lower()
        if st not in NOTIF_STYLES:
            return False
        chosen = set_sys_notif_style(st)
        _save_theme_file(_theme_path(), THEME)
        import time as _time
        try:
            from panels.cc_engine_notifications import spawn_notification_popup
        except ImportError:
            spawn_notification_popup = None
        if spawn_notification_popup is not None:
            spawn_notification_popup(
                {
                    "id": int(_time.time() * 1000),
                    "app": "NC-UI",
                    "title": t("cc_notifications.preview_title"),
                    "body": t("cc_personalization.sys_notif_style_set", p0=t(f"cc_personalization.notif_style_{chosen}")),
                    "urgency": "normal",
                    "time": _time.strftime("%H:%M"),
                },
                force=True,
            )
        _set_pers_fb(True, t("cc_personalization.sys_notif_style_set", p0=t(f"cc_personalization.notif_style_{chosen}")))
    elif act.startswith("toast_style:"):
        st = act.split(":", 1)[1].strip().lower()
        if st not in NOTIF_STYLES:
            return False
        chosen = set_toast_style(st)
        _save_theme_file(_theme_path(), THEME)
        _set_pers_fb(True, t("cc_personalization.toast_style_set", p0=t(f"cc_personalization.notif_style_{chosen}")))
    elif act.startswith("help_style:"):
        st = act.split(":", 1)[1].strip().lower()
        if st not in NOTIF_STYLES:
            return False
        chosen = set_hover_help_style(st)
        _save_theme_file(_theme_path(), THEME)
        if cc_panel and cc_panel.shell:
            cc_panel.shell.dismiss_hover_help()
        _set_pers_fb(True, t("cc_personalization.hover_help_style_set", p0=t(f"cc_personalization.notif_style_{chosen}")))
    elif act.startswith("vol_od_style:"):
        st = act.split(":", 1)[1].strip().lower()
        if st not in VOLUME_OVERDRIVE_STYLES:
            return False
        chosen = set_volume_overdrive_style(st)
        _save_theme_file(_theme_path(), THEME)
        _set_pers_fb(True, t("cc_personalization.vol_od_style_set", p0=t(f"cc_personalization.vol_od_{chosen}")))
    elif act.startswith("vol_od_demo:"):
        try:
            val = max(0, min(200, int(round(float(act.split(":", 1)[1])))))
        except (TypeError, ValueError):
            return False
        STATE.set("personalization", "vol_od_demo", val, immediate=False)
        if cc_panel:
            cc_panel.request_throttled_refresh()
        return True
    elif act.startswith("vol_od_scroll:"):
        cur = int(STATE.get("personalization", "vol_od_demo", 165) or 165)
        step = 5 if act.rsplit(":", 1)[1] == "up" else -5
        STATE.set("personalization", "vol_od_demo", max(0, min(200, cur + step)), immediate=False)
        if cc_panel:
            cc_panel.request_throttled_refresh()
        return True
    elif act.startswith("set_color:"):
        parts = act.split(":", 2)
        if len(parts) != 3 or not parts[1] or not parts[2]:
            return False
        _, k, val = parts
        valid_color_keys = {
            ck for ck, cv in DEFAULT_THEME.get("colors", {}).items()
            if isinstance(cv, str) and cv.startswith("#")
        } | {"surface_bg", "accent"}
        val_rgb = _norm_hex_arg(val, allow_alpha=False)
        if k not in valid_color_keys or not val_rgb:
            return False
        if k == "surface_bg":
            norm_hx = set_surface_bg(val_rgb, sync_waybar=False)
            _refresh_surface_companions(norm_hx)
            _save_theme_file(_theme_path(), THEME)
            if cc_panel and cc_panel.shell:
                cc_panel.shell._apply_css()
            _set_pers_fb(True, t("cc_personalization.color_updated_to", p0="surface_bg", p1=norm_hx))
        else:
            target_axis = "intent" if k == "accent" else k
            THEME["colors"].update(derive_axis_companions(target_axis, val_rgb, get_surface_bg_hex()))
            _sync_theme_aliases()
            _save_theme_file(_theme_path(), THEME)
            if target_axis in ("intent", "fault"):
                _cur.sync_ncui_cursor_if_active()
            if cc_panel and cc_panel.shell:
                cc_panel.shell._apply_css()
            _set_pers_fb(True, t("cc_personalization.color_updated_to", p0=target_axis, p1=val_rgb))
    elif act.startswith("inline_col:"):
        k = act.split(":", 1)[1]
        valid_color_keys = {
            ck for ck, cv in DEFAULT_THEME.get("colors", {}).items()
            if isinstance(cv, str) and cv.startswith("#")
        }
        if k not in valid_color_keys or not cc_panel or not cc_panel.shell:
            return False
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
        _save_theme_file(_theme_path(), THEME)
        _cur.sync_ncui_cursor_if_active()
        if cc_panel and cc_panel.shell:
            cc_panel.shell._apply_css()
        _set_pers_fb(True, t("cc_personalization.reset_theme_to_cyberdeck_def"))
    elif act.startswith("font:"):
        f = act.split(":", 1)[1]
        if not f:
            return False
        STATE.set("personalization", "font_pick_open", False)
        STATE.set("personalization", "font_query", "")
        THEME["typography"]["font_family"] = f
        _persist_theme_key("typography", "font_family", f'"{f}"')
        if cc_panel and cc_panel.shell:
            cc_panel.shell._apply_css()
        _set_pers_fb(True, t("cc_personalization.font_family_set_to", p0=f))
    elif act.startswith("font_size:"):
        try:
            sz = int(act.split(":", 1)[1])
        except (ValueError, TypeError):
            return False
        THEME["typography"]["font_size"] = sz
        _persist_theme_key("typography", "font_size", str(sz))
        if cc_panel and cc_panel.shell:
            cc_panel.shell._apply_css()
        _set_pers_fb(True, t("cc_personalization.font_size_set_to_pt", p0=sz))
    elif act == "font_pick_open":
        if not cc_panel or not cc_panel.shell:
            return False
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
        if not cc_panel or not cc_panel.shell:
            return False
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
        try:
            c_sz = int(act.split(":", 1)[1])
        except (ValueError, TypeError):
            return False
        cur_t, _ = _cur._read_cursor_cfg()
        ok, msg = _cur._apply_cursor_cfg(cur_t, c_sz)
        _set_pers_fb(ok, msg)
    elif act.startswith("set_cursor:"):
        c_name = act.split(":", 1)[1]
        if not c_name:
            return False
        _, cur_sz = _cur._read_cursor_cfg()
        STATE.set("personalization", "cursor_pick_open", False)
        STATE.set("personalization", "cursor_query", "")
        ok, msg = _cur._apply_cursor_cfg(c_name, cur_sz)
        _set_pers_fb(ok, msg)
    elif act.startswith("del_cursor:"):
        c_name = act.split(":", 1)[1]
        if not c_name:
            return False
        ok, msg = _cur._delete_cursor_theme(c_name)
        _set_pers_fb(ok, msg)
    elif act == "prompt_cursor_zip":
        if not cc_panel:
            return False
        cc_panel.request_prompt("cc_pers_cursor_zip", t("cc_personalization.cur_zip_prompt"), "~/Downloads/", title=t("cc_personalization.cur_zip_title"), required=True, completion=("path_ext", [".zip", ".tar.gz", ".tgz", ".tar.xz"]))
        return True
    elif act == "paste_cursor_zip":
        clip = _media._read_clipboard_text()
        ok, msg, _nm = _cur.convert_windows_cursor_zip(clip)
        _set_pers_fb(ok, msg)
    elif act == "pick_cursor_zip":
        if _yazi._launch_yazi_picker(cc_panel, is_folder=False, target_mode="cursor_zip"):
            if cc_panel and cc_panel.shell:
                cc_panel.shell.refresh_view()
            return True
        if not cc_panel:
            return False
        cc_panel.request_prompt("cc_pers_cursor_zip", t("cc_personalization.cur_zip_prompt"), "~/Downloads/", title=t("cc_personalization.cur_zip_title"), required=True, completion=("path_ext", [".zip", ".tar.gz", ".tgz", ".tar.xz"]))
        return True
    elif act.startswith(("tbrk:", "sbrk:", "pdots:", "tcaps:", "cicons:", "slglyph:", "sbox:")):
        cmd_k, arg_k = act.split(":", 1)
        ascii_presets = {
            "tbrk": (_TITLE_BRK_PRESETS, ("title_brackets",), True),
            "sbrk": (_SLIDER_BRK_PRESETS, ("slider_brackets",), True),
            "pdots": (_PILL_DOT_PRESETS, ("pill_dots",), True),
            "tcaps": (_TAB_CAP_PRESETS, ("tab_caps",), True),
            "cicons": (_COLLAPSE_PRESETS, ("collapsible_icons",), True),
            "slglyph": (_SLIDER_GLYPH_PRESETS, ("slider_fill", "slider_empty"), False),
            "sbox": (_SEARCH_BOX_PRESETS, ("search_prefix", "search_cursor"), False),
        }
        pmap, keys_t, as_list = ascii_presets[cmd_k]
        spec = pmap.get(arg_k)
        if not spec:
            return False
        if as_list:
            THEME["ascii"][keys_t[0]] = [spec[0], spec[1]]
        else:
            THEME["ascii"][keys_t[0]] = spec[0]
            THEME["ascii"][keys_t[1]] = spec[1]
        _save_theme_file(_theme_path(), THEME)
    elif act.startswith("trule:"):
        try:
            idx = int(act.split(":", 1)[1])
        except (ValueError, TypeError):
            return False
        if not (0 <= idx < len(_TABLE_RULE_PRESETS)):
            return False
        THEME["ascii"]["table_rule"] = _TABLE_RULE_PRESETS[idx]
        _save_theme_file(_theme_path(), THEME)
    elif act.startswith("kvsep:"):
        try:
            idx = int(act.split(":", 1)[1])
        except (ValueError, TypeError):
            return False
        if not (0 <= idx < len(_KV_SEP_PRESETS)):
            return False
        THEME["ascii"]["kv_separator"] = _KV_SEP_PRESETS[idx]
        _save_theme_file(_theme_path(), THEME)
    elif act.startswith("kwidth:"):
        try:
            kw = max(14, min(32, int(act.split(":", 1)[1])))
        except (ValueError, TypeError):
            return False
        THEME["geometry"]["default_key_width"] = kw
        _persist_theme_key("geometry", "default_key_width", str(kw))
    elif act.startswith("winrad:"):
        try:
            rad = max(0, min(32, int(act.split(":", 1)[1])))
        except (ValueError, TypeError):
            return False
        THEME["geometry"]["window_border_radius"] = rad
        _persist_theme_key("geometry", "window_border_radius", str(rad))
        if cc_panel and cc_panel.shell:
            cc_panel.shell._apply_css()
    elif act.startswith("spacing:"):
        try:
            sp = int(act.split(":", 1)[1])
        except (ValueError, TypeError):
            return False
        THEME["geometry"]["card_spacing"] = sp
        _persist_theme_key("geometry", "card_spacing", str(sp))
    elif act.startswith("inner_pad:"):
        try:
            pad_v = int(act.split(":", 1)[1])
        except (ValueError, TypeError):
            return False
        set_layout_margins(inner_padding=pad_v, sync_bar=False)
        if cc_panel and cc_panel.shell and hasattr(cc_panel.shell, "apply_layout_margins"):
            cc_panel.shell.apply_layout_margins()
    elif act.startswith("outer_mar:"):
        try:
            mar_v = int(act.split(":", 1)[1])
        except (ValueError, TypeError):
            return False
        set_layout_margins(outer_margin=mar_v, sync_bar=True, reload_bar=True)
        if cc_panel and cc_panel.shell and hasattr(cc_panel.shell, "apply_layout_margins"):
            cc_panel.shell.apply_layout_margins()
    elif act == "inline_inner_pad":
        if not cc_panel or not cc_panel.shell:
            return False
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
        if not cc_panel or not cc_panel.shell:
            return False
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
        if not cc_panel or not cc_panel.shell:
            return False
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
        if not cc_panel or not cc_panel.shell:
            return False
        start_inline_input(
            entry_id="cc_pers_save_theme",
            shell=cc_panel.shell,
            trigger_uri=uri,
            initial_text="cyberdeck-custom",
            max_len=28,
            validator=lambda s: bool(s.strip()),
            on_commit=lambda s: handle_cc_personalization_prompt(cc_panel, "cc_pers_save_theme", s.strip()),
        )
        return True
    elif act == "export_theme_prompt":
        if not cc_panel:
            return False
        cc_panel.request_prompt("cc_pers_exp_theme", t("cc_personalization.export_path_toml"), "~/ncui-theme-export.toml", title=t("cc_personalization.export_theme"), required=True, completion=("path_ext", [".toml"]))
        return True
    elif act == "import_theme_prompt":
        if not cc_panel:
            return False
        cc_panel.request_prompt("cc_pers_imp_theme", t("cc_personalization.import_toml_theme_path"), "~/", title=t("cc_personalization.import_theme"), required=True, completion=("path_ext", [".toml"]))
        return True
    elif act.startswith("load_theme:"):
        nm = act.split(":", 1)[1]
        if not nm or "/" in nm or ".." in nm:
            return False
        p = os.path.join(_themes_dir(), f"{nm}.toml")
        loaded = _load_toml(p)
        if not loaded:
            return False
        _apply_loaded_theme(cc_panel, loaded, src_dir=os.path.dirname(p))
        _set_pers_fb(True, t("cc_personalization.loaded_theme", p0=nm))
    elif act.startswith("del_theme:"):
        nm = act.split(":", 1)[1]
        if not nm or "/" in nm or ".." in nm:
            return False
        p = os.path.join(_themes_dir(), f"{nm}.toml")
        if not os.path.exists(p):
            return False
        os.remove(p)
        _set_pers_fb(True, t("cc_personalization.deleted_theme", p0=nm))
    else:
        return False

    if cc_panel and cc_panel.shell:
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
    c_store = _cursors_store_dir()
    if ct:
        installed = _cur.is_ncui_cursor_name(ct) or any(os.path.isdir(os.path.join(r, ct, "cursors")) for r in _sys_icons_dirs())
        if not installed and ca:
            for cand_dir in (src_dir, c_store):
                cand_zip = os.path.join(cand_dir, ca) if cand_dir else ""
                if cand_zip and os.path.isfile(cand_zip):
                    _cur.convert_windows_cursor_zip(cand_zip, custom_name=ct)
                    break
        _cur._apply_cursor_cfg(ct, cs)
    else:
        _cur.sync_ncui_cursor_if_active()
    _save_theme_file(_theme_path(), THEME)
    if cc_panel and cc_panel.shell:
        cc_panel.shell._apply_css()


def handle_cc_personalization_prompt(cc_panel, action_id: str, text: str) -> bool:
    if handle_cc_waybar_prompt(cc_panel, action_id, text):
        return True
    val = str(text or "").strip()
    if not val:
        return False
    target_out = str(STATE.get("desktop", "target_out", "all"))

    if action_id in ("cc_desk_scene", "cc_desk_img"):
        p = os.path.expanduser(val)
        if os.path.isfile(p):
            _media._cancel_pending_yt_dl()
            STATE.set("desktop", "wp_mode", "scene")
            STATE.set("desktop", "scene_path", p)
            _media.set_wp_user_touched(True)
            _media._stage_current_to_selected()
            _set_pers_fb(True, t("cc_personalization.wp_staged_scene_msg", p0=os.path.basename(p)))
        else:
            _set_pers_fb(False, t("cc_personalization.wp_invalid_path_err", p0=val))
        return True
    if action_id == "cc_desk_yt":
        _media._handle_youtube_input(cc_panel, val, from_clipboard=False)
        return True
    if action_id == "cc_desk_folder":
        p = os.path.expanduser(val)
        if os.path.isdir(p):
            _media._cancel_pending_yt_dl()
            STATE.set("desktop", "wp_mode", "slideshow")
            STATE.set("desktop", "slide_folder", p)
            _media.set_wp_user_touched(True)
            _media._stage_current_to_selected()
            _set_pers_fb(True, t("cc_personalization.wp_staged_scene_msg", p0=os.path.basename(p.rstrip("/")) or p))
        else:
            _set_pers_fb(False, t("cc_personalization.wp_invalid_path_err", p0=val))
        return True
    if action_id == "cc_desk_interval":
        STATE.set("desktop", "slide_interval", val)
        _media.set_wp_user_touched(True)
        _media._stage_current_to_selected()
        return True
    if action_id == "cc_desk_solid_hex":
        cleaned = val.strip()
        if re.match(r"^#?[0-9a-fA-F]{6}$", cleaned):
            hex_col = f"#{cleaned.lstrip('#').upper()}"
            STATE.set("desktop", "solid_hex", hex_col)
            STATE.set("desktop", "wp_mode", "solid")
            _media.set_wp_user_touched(True)
            _media._stage_current_to_selected()
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
                _save_theme_file(_theme_path(), THEME)
                if cc_panel and cc_panel.shell:
                    cc_panel.shell._apply_css()
                _set_pers_fb(True, t("cc_personalization.color_updated_to", p0="surface_bg", p1=norm_hx))
            else:
                target_axis = "intent" if k == "accent" else k
                THEME["colors"].update(derive_axis_companions(target_axis, val.lower(), get_surface_bg_hex()))
                _sync_theme_aliases()
                _save_theme_file(_theme_path(), THEME)
                if target_axis in ("intent", "fault"):
                    _cur.sync_ncui_cursor_if_active()
                if cc_panel and cc_panel.shell:
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
            _save_theme_file(_theme_path(), THEME)
            _set_pers_fb(True, t("cc_personalization.color_updated_to", p0=wk, p1=val.upper()))
        else:
            _set_pers_fb(False, t("cc_personalization.invalid_hex_color", p0=val))
        return True
    if action_id == "cc_pers_font":
        THEME["typography"]["font_family"] = val
        _persist_theme_key("typography", "font_family", f'"{val}"')
        if cc_panel and cc_panel.shell:
            cc_panel.shell._apply_css()
        _set_pers_fb(True, t("cc_personalization.font_set_to", p0=val))
        return True
    if action_id == "cc_pers_cursor":
        _, cur_s = _cur._read_cursor_cfg()
        ok, msg = _cur._apply_cursor_cfg(val, cur_s)
        _set_pers_fb(ok, msg)
        return True
    if action_id == "cc_pers_cursor_zip":
        ok, msg, _nm = _cur.convert_windows_cursor_zip(val)
        _set_pers_fb(ok, msg)
        return True
    if action_id == "cc_pers_inner_pad" and val.isdigit():
        in_p, _ = set_layout_margins(inner_padding=int(val), sync_bar=False)
        if cc_panel and cc_panel.shell and hasattr(cc_panel.shell, "apply_layout_margins"):
            cc_panel.shell.apply_layout_margins()
        _set_pers_fb(True, f"{t('cc_personalization.inner_pad_lbl')}: {in_p}px")
        return True
    if action_id == "cc_pers_outer_mar" and val.isdigit():
        _, out_m = set_layout_margins(outer_margin=int(val), sync_bar=True, reload_bar=True)
        if cc_panel and cc_panel.shell and hasattr(cc_panel.shell, "apply_layout_margins"):
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
        cur_t, cur_s = _cur._read_cursor_cfg()
        c_store = _cursors_store_dir()
        THEME.setdefault("typography", {}).update({
            "cursor_theme": cur_t,
            "cursor_size": cur_s,
            "cursor_archive": f"{cur_t}.zip" if os.path.isfile(os.path.join(c_store, f"{cur_t}.zip")) else "",
        })
        ok, msg = _save_theme_file(os.path.join(_themes_dir(), f"{safe}.toml"), THEME)
        _set_pers_fb(ok, msg)
        return True
    if action_id == "cc_pers_exp_theme":
        exp_p = os.path.abspath(os.path.expanduser(val))
        cur_t, cur_s = _cur._read_cursor_cfg()
        c_store = _cursors_store_dir()
        arch = f"{cur_t}.zip" if os.path.isfile(os.path.join(c_store, f"{cur_t}.zip")) else ""
        THEME.setdefault("typography", {}).update({"cursor_theme": cur_t, "cursor_size": cur_s, "cursor_archive": arch})
        ok, msg = _save_theme_file(exp_p, THEME)
        if ok and arch:
            src_z = os.path.join(c_store, arch)
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


# --- Re-exports for backward compatibility with personalization & mouse views ---
import panels.cc_engine_personalization_cursors as _cur
import panels.cc_engine_personalization_media as _media
import panels.cc_engine_personalization_yazi as _yazi
from panels.cc_engine_personalization_cursors import (  # noqa: F401,E402
    NCUI_CURSOR_ASSETS_DIR,
    NCUI_CURSOR_NAME,
    NCUI_CURSOR_PREFIX,
    _WIN_ROLE_TO_XCUR,
    _XCUR_SYMLINKS,
    _apply_cursor_cfg,
    _decode_win_img_payload,
    _delete_cursor_theme,
    _hex_to_hsv,
    _list_cursor_themes,
    _match_cursor_role,
    _parse_ani_frames,
    _parse_cur_frames,
    _parse_inf_roles,
    _parse_xcursor_frames,
    _pixbuf_to_xcur_bgra,
    _read_cursor_cfg,
    _recolor_xcursor_bgra,
    _update_gtk_ini_cursor,
    _write_xcursor_file,
    convert_windows_cursor_zip,
    ensure_ncui_cursor_installed,
    is_ncui_cursor_name,
    sync_ncui_cursor_if_active,
)
from panels.cc_engine_personalization_media import (  # noqa: F401,E402
    SOLID_CLASSIC_PRESETS,
    SOLID_DESIGNER_PRESETS,
    _ASCII_APPLY_SEQ,
    _ASCII_APPLY_STATUS,
    _ASCII_PREVIEW_PENDING,
    _BG_FRAME_EXTRACTING,
    _BG_YT_THUMBS,
    _GIF_EXTS,
    _IMAGE_EXTS,
    _LAST_ASCII_PREVIEW,
    _MEDIA_DIM_CACHE,
    _MEDIA_EXTS,
    _NCMATRIX_CPU_PCT,
    _NCMATRIX_CPU_SAMPLES,
    _SOLID_NAMED_COLORS,
    _THUMB_CACHE_DIR,
    _VIDEO_EXTS,
    _WP_USER_TOUCHED,
    _YAZI_PICK_FILE,
    _YT_DL_SEQ,
    _YT_HOSTS,
    _YT_LOCAL_FILE_RE,
    _YT_LOCAL_PL_RE,
    _YT_PL_RAW_RE,
    _YT_PL_RE,
    _YT_PREFIX_FILE_RE,
    _YT_VID_RE,
    _apply_single_non_ascii_monitor,
    _apply_staged_wallpapers,
    _cancel_pending_ascii_apply,
    _cancel_pending_yt_dl,
    _cleanup_orphan_monitors,
    _collect_output_wallpaper_pids,
    _ensure_ascii_preview,
    _ensure_youtube_thumbnail,
    _find_cached_yt_media,
    _find_media_file,
    _find_static_image_file,
    _get_editor_draft_spec,
    _get_monitor_resolution,
    _get_niri_outputs,
    _get_running_ncmatrix_pid,
    _handle_youtube_input,
    _is_in_yt_wall_dir,
    _kill_pids_after_overlap,
    _match_yt_local_vid,
    _merge_desktop_json_meta,
    _norm_align,
    _norm_asc_diff,
    _norm_spec_for_diff,
    _parse_youtube_input,
    _read_clipboard_text,
    _read_live_src,
    _read_proc_stat_ticks,
    _resolve_image_candidate,
    _resolve_solid_hex,
    _resolve_yt_source,
    _save_desktop_json_state,
    _send_mpv_ipc,
    _set_editor_asciify_cfg,
    _spawn_bg_pid,
    _spec_needs_asciify,
    _specs_differ,
    _stage_current_to_selected,
    _start_apply_staged_wallpapers,
    _start_youtube_download,
    _stop_mpvpaper,
    _target_yt_height,
    _target_yt_height_for_monitors,
    _update_live_video_audio_if_running,
    _wallpaper_pid_files,
    _yt_thumb_cache_path,
    count_folder_media,
    detect_media_kind,
    extract_media_preview_frame,
    extract_media_preview_frames,
    get_ascii_apply_status,
    get_desktop_preview_state,
    get_editor_asciify_cfg,
    get_media_dimensions,
    get_ncmatrix_live_cpu_usage,
    get_selected_monitors,
    is_live_15fps_enabled,
    reset_ncmatrix_cpu_telemetry,
    should_show_scene_alignment,
    sync_editor_from_live_monitors,
)
from panels.cc_engine_personalization_yazi import (  # noqa: F401,E402
    EmbeddedYaziSession,
    _XTERM_256,
    _XTERM_ANSI_16,
    _YAZI_CFG_DIR,
    _YAZI_SESSION,
    _build_xterm_256,
    _close_embedded_yazi,
    _launch_yazi_picker,
    _write_yazi_ncui_theme,
    _yazi_child_preexec,
    get_active_yazi_session,
    handle_cc_personalization_key_press,
)
