#!/usr/bin/env python3
import os
import copy
import json
import re
import shutil
import subprocess

try:
    import tomllib
except ImportError:
    tomllib = None

from core.paths import (
    CORE_ROOT,
    bootstrap_sys_path,
    find_all_package_files,
    find_bin,
    find_package_file,
)

bootstrap_sys_path(__file__)

ROOT_DIR = CORE_ROOT
CONFIG_DIR = os.path.expanduser("~/.config/nc-ui")
THEME_PATH = os.path.join(CONFIG_DIR, "theme.toml")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.toml")

# MEMENTO ARCHITETTURALE (Golden Standard §5.1 / §5.2):
# Sono ammessi SOLO stili di cornice a cella singola con metriche verticali continue.
# Stili a mezzo-blocco ("block" ▛▀▜) o puntinati sugli angoli ("dotted") sono vietati
# perché spezzano le protrusioni Cairo sul titolo e l'allineamento dei montanti verticali.
BOX_STYLES = {
    "rounded": {
        "tl": "\u256d", "tr": "\u256e", "bl": "\u2570", "br": "\u256f",
        "h": "\u2500", "v": "\u2502", "ml": "\u251c", "mr": "\u2524",
        "mt": "\u252c", "mb": "\u2534", "mc": "\u253c",
    },
    "sharp": {
        "tl": "\u250c", "tr": "\u2510", "bl": "\u2514", "br": "\u2518",
        "h": "\u2500", "v": "\u2502", "ml": "\u251c", "mr": "\u2524",
        "mt": "\u252c", "mb": "\u2534", "mc": "\u253c",
    },
    "heavy": {
        "tl": "\u250f", "tr": "\u2513", "bl": "\u2517", "br": "\u251b",
        "h": "\u2501", "v": "\u2503", "ml": "\u2523", "mr": "\u252b",
        "mt": "\u2533", "mb": "\u253b", "mc": "\u254b",
    },
    "double": {
        "tl": "\u2554", "tr": "\u2557", "bl": "\u255a", "br": "\u255d",
        "h": "\u2550", "v": "\u2551", "ml": "\u2560", "mr": "\u2563",
        "mt": "\u2566", "mb": "\u2569", "mc": "\u256c",
    },
    "dashed": {
        "tl": "\u256d", "tr": "\u256e", "bl": "\u2570", "br": "\u256f",
        "h": "\u2504", "v": "\u2506", "ml": "\u251c", "mr": "\u2524",
        "mt": "\u252c", "mb": "\u2534", "mc": "\u253c",
    },
    "ascii": {
        "tl": "+", "tr": "+", "bl": "+", "br": "+",
        "h": "-", "v": "|", "ml": "+", "mr": "+",
        "mt": "+", "mb": "+", "mc": "+",
    },
}

# MEMENTO MORFOLOGICO (Golden Standard §2 — Canale 1):
# Whitelist tassative per impedire che modifiche manuali a theme.toml o preset futuri
# introducano collisioni semantiche tra delimitatori (es. "(btn)" vs conteggi "(N)",
# "▸/·" su SelectPill vs InspectorCell, "-/+" su sezioni vs steppers Slider, "│" su KV vs bordi).
VALID_TITLE_BRACKETS = (("[ ", " ]"), (":: ", " ::"), ("\u2039 ", " \u203a"), ("\u00ab ", " \u00bb"))
VALID_SLIDER_BRACKETS = (("[", "]"), ("\u2590", "\u258c"))
VALID_SLIDER_GLYPHS = (("#", "-"), ("\u2588", "\u2591"), ("\u2501", "\u2500"), ("=", "\u00b7"))
VALID_PILL_DOTS = (("\u25cf", "\u25cb"), ("\u25c6", "\u25c7"), ("\u25a0", "\u25a1"))
VALID_TAB_CAPS = (("\u258f", "\u2595"), ("\u2590", "\u258c"), ("\u2039", "\u203a"))
VALID_COLLAPSIBLE_ICONS = (("\u25bc", "\u25b6"), ("\uf078", "\uf054"), ("\u229f", "\u229e"))
VALID_TABLE_RULES = ("\u2500", "\u2501", "\u2550", "\u2504")
VALID_KV_SEPARATORS = (":", "\u203a", "\u2237")
NOTIF_STYLES = ("tactical", "classic", "clean", "cyberpunk")
HOVER_HELP_STYLES = NOTIF_STYLES
_NOTIF_STYLE_ALIASES = {
    "micro_tag": "tactical",
    "slim_ascii": "classic",
    "hud_strip": "clean",
    "blueprint_callout": "cyberpunk",
}


def _norm_notif_style(val, default: str = "tactical") -> str:
    s = str(val or "").strip().lower()
    s = _NOTIF_STYLE_ALIASES.get(s, s)
    return s if s in NOTIF_STYLES else default

# =====================================================================
# TEMA CAPOSTIPITE: NIGHTCITY (Il Gioiello / Riferimento Canonico NC-UI)
# =====================================================================
DEFAULT_THEME = {
    "typography": {
        "font_family": "Courier",
        "font_size": 11,
        "font_weight": "bold",
        "cursor_theme": "NCUI Cursor",
        "cursor_size": 24,
        "cursor_archive": "",
    },
    "geometry": {
        "compact_cols": 44,
        "expanded_cols": 96,
        "sidebar_nav_cols": 24,
        "popup_gap_top": 8,
        "popup_max_height": 900,
        "window_border_width": 1,
        "window_border_radius": 16,
        "panel_padding": 10,
        "card_spacing": 6,
        "screen_margin": 8,
        "default_key_width": 20,
        "row_gap": 2,
    },
    "ascii": {
        "box_style": "rounded",
        "title_brackets": ["[ ", " ]"],
        # INVARIANTE ASSOLUTO (§2.1): i controlli cliccabili discreti usano SEMPRE '[' e ']'.
        "button_brackets": ["[", "]"],
        "slider_brackets": ["[", "]"],
        "slider_fill": "#",
        "slider_empty": "-",
        "sparkline_chars": "\u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588",
        "pill_dots": ["\u25cf", "\u25cb"],
        "tab_caps": ["\u258f", "\u2595"],
        "collapsible_icons": ["\u25bc", "\u25b6"],
        "table_rule": "\u2500",
        "kv_separator": ":",
        "search_prefix": "\uf002 >",
        "search_brackets": ["\u258f", "\u2595"],
        "search_cursor": "\u2502",
        "sys_notif_style": "clean",
        "toast_style": "tactical",
        "hover_help_style": "cyberpunk",
    },
    "colors": {
        "surface_bg": "#080a0f",
        "window_opacity": 0.86,
        "box_opacity": 0.72,
        "toast_opacity": 0.95,
        "sys_notif_opacity": 0.95,
        "hover_help_opacity": 0.96,
        "waybar_sync_opacity": True,
        "waybar_sync_color": True,
        "window_bg": "rgba(8, 10, 15, 0.86)",
        "window_border": "rgba(255, 255, 255, 0.14)",
        "background": "#06080c",
        # Asse 1 — Structure (Grafite Titanio: cornici, griglie, parentesi a riposo)
        "structure": "#3e465c",
        "structure_bg": "#141824",
        # Asse 3 — Inactive (Grafite Spento: opzioni non scelte, standby, hint)
        "inactive": "#596278",
        # Asse 2 — Schema (Grigio Ardesia Acciaio: chiavi KV, header tabelle, azioni neutre)
        "schema": "#8b9bb8",
        # Asse 4 — Focus & Readout (Bianco Puro titoli / Bianco Ghiaccio valori di lettura)
        "focus": "#ffffff",
        "focus_val": "#dce6f7",
        # Asse 5 — Intent (Ciano Laser Nightcity: ESCLUSIVO per selezione/navigazione/focus)
        "intent": "#00f0ff",
        "intent_soft": "#3ec8d6",
        "intent_bg": "#002b36",
        # Asse 6 — Vitality (Smeraldo Bio-Monitor: salute attiva OS e comandi costruttivi)
        "vitality": "#00f57a",
        "vitality_soft": "#3ebd7c",
        "vitality_bg": "#002915",
        # Asse 7a — Attention (Giallo Cyber: modifiche staged/dirty, warning, cautela/reset)
        "attention": "#ffe619",
        "attention_soft": "#c7b438",
        "attention_bg": "#332b00",
        # Asse 7b — Interrupt (Rosa-Magenta Cyberpunk: stop, disabilita, rimuovi, [x])
        "interrupt": "#ff2a9d",
        "interrupt_rest": "#c45b8c",
        "interrupt_bg": "#36081e",
        # Asse 7c — Fault (Cremisi Arasaka: errore critico, offline, timeout, DROP)
        "fault": "#ff2442",
        "fault_soft": "#c9495b",
        "fault_bg": "#38060e",
        # Waybar Color Palette (sincronizzata su style.css)
        "wb_bg": "#080a0f",
        "wb_pill_bg": "#141824",
        "wb_fg": "#dce6f7",
        "wb_accent": "#00f0ff",
        "wb_active": "#00f57a",
        "wb_warn": "#ffe619",
        "wb_crit": "#ff2442",
        # Alias legacy sincronizzati automaticamente da sanitize_theme_dict()
        "primary": "#ffffff",
        "secondary": "#8b9bb8",
        "muted": "#596278",
        "accent": "#00f0ff",
        "info": "#00f0ff",
        "box_inactive": "#3e465c",
        "box_elected": "#3e465c",
        "box_highlight": "#ffffff",
        "box_selected": "#ffffff",
        "input_bg": "#0b0e15",
        "input_fg": "#ffffff",
        "input_placeholder": "#596278",
        "input_border": "#3e465c",
        "input_border_focus": "#00f0ff",
        "thresholds": {
            "normal": "#00f57a",
            "warning": "#ffe619",
            "alert": "#ff2a9d",
            "critical": "#ff2442",
        },
    },
}

DEFAULT_CONFIG = {
    "general": {
        "language": "en",
        "log_file": "/tmp/nc-ui.log",
        "log_level": 3,
        "enable_hover_tooltips": True,
        "hover_tooltip_delay": 0.7,
        "terminal": "auto",
        "pkg_manager": "auto",
    },
    "control_center": {
        "width_px": 960,
        "side": "right",
        "panel_style": "standard",
        "hide_unavailable_settings": False,
        "inner_padding": 10,
        "outer_margin": 8,
    },
    "capture": {
        "action": "clipboard_satty",
    },
    "waybar_plugins": {
        "window_width_pct": 45,
        "media_width_pct": 35,
        "lock_cmd": "",
    },
    "wallpaper": {
        "nc_matrix_bin": "",
    },
    "panels": {
        "refresh_ms": {
            "calendar":      0,
            "notifications": 1500,
            "ram":           1000,
            "cpu":           1000,
            "gpu":           1000,
            "network":       1500,
            "audio":         1500,
            "capture":       0,
            "display":       2000,
            "clipboard":     1500,
            "power":         3000,
            "ups":           3000,
            "kb_layout":     2000,
            "storage":       5000,
            "session":       0,
        }
    },
    "network": {
        "monitored_interfaces": "auto",
        "default_speed_unit": "bytes",
        "show_bar_speed": False,
        "wan": {"primary_ip": "", "primary_label": "Primary WAN", "failover_label": "Failover WAN"},
        "watchdog": [],
    },
    "power": {
        "nut": {
            "enabled": False,
            "host": "127.0.0.1",
            "port": 3493,
            "ups_name": "",
            "poll_interval": 5,
            "low_pct": 20,
        }
    },
    "storage": {"nas": {"enabled": False, "host": "", "ssh_user": ""}},
}


def _unique_existing_paths(*paths: str) -> list[str]:
    seen, out = set(), []
    for p in paths:
        if p and os.path.exists(p):
            rp = os.path.realpath(p)
            if rp not in seen:
                seen.add(rp)
                out.append(rp)
    return out


def _deep_merge(base: dict, override: dict) -> dict:
    res = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(res.get(k), dict):
            res[k] = _deep_merge(res[k], v)
        else:
            res[k] = copy.deepcopy(v)
    return res


def _load_toml(path: str) -> dict:
    if not os.path.exists(path) or tomllib is None:
        return {}
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except Exception:
        return {}


def _norm_hex_color(val, default: str = "#080a0f") -> str:
    s = str(val or "").strip()
    if re.match(r"^#[0-9a-fA-F]{6}$", s):
        return s.lower()
    return default


def _blend_hex(fg_hex: str, bg_hex: str, fg_weight: float) -> str:
    """Miscela linearmente `fg_hex` e `bg_hex` in RGB per derivare capsule `*_bg` e toni `*_soft` coerenti."""
    fg = _norm_hex_color(fg_hex, "#ffffff")
    bg = _norm_hex_color(bg_hex, "#080a0f")
    w = max(0.0, min(1.0, float(fg_weight)))
    r = int(round(int(fg[1:3], 16) * w + int(bg[1:3], 16) * (1.0 - w)))
    g = int(round(int(fg[3:5], 16) * w + int(bg[3:5], 16) * (1.0 - w)))
    b = int(round(int(fg[5:7], 16) * w + int(bg[5:7], 16) * (1.0 - w)))
    return f"#{max(0, min(255, r)):02x}{max(0, min(255, g)):02x}{max(0, min(255, b)):02x}"


def derive_axis_companions(axis_key: str, hex_val: str, surface_hex: str = "#080a0f") -> dict[str, str]:
    """
    MEMENTO CROMATICO (Golden Standard §4.1):
    Quando un utente personalizza il colore primario di un singolo asse semantico, i relativi
    sotto-toni (`*_bg`, `*_soft`, `interrupt_rest`, `focus_val`, `structure_bg`) DEVONO essere
    ricalcolati armonicamente rispetto a `surface_hex`, evitando che rimangano orfani su vecchie tinte.
    """
    hx = _norm_hex_color(hex_val, DEFAULT_THEME["colors"].get(axis_key, "#ffffff"))
    surf = _norm_hex_color(surface_hex, "#080a0f")
    out: dict[str, str] = {axis_key: hx}
    if axis_key == "structure":
        out["structure_bg"] = _blend_hex(hx, surf, 0.24)
    elif axis_key == "focus":
        out["focus_val"] = _blend_hex(hx, "#9cb4dc", 0.78)
    elif axis_key == "intent":
        out["intent_soft"] = _blend_hex(hx, "#60708a", 0.68)
        out["intent_bg"] = _blend_hex(hx, surf, 0.17)
    elif axis_key == "vitality":
        out["vitality_soft"] = _blend_hex(hx, "#507060", 0.68)
        out["vitality_bg"] = _blend_hex(hx, surf, 0.16)
    elif axis_key == "attention":
        out["attention_soft"] = _blend_hex(hx, "#605c40", 0.72)
        out["attention_bg"] = _blend_hex(hx, surf, 0.17)
    elif axis_key == "interrupt":
        out["interrupt_rest"] = _blend_hex(hx, "#685870", 0.66)
        out["interrupt_bg"] = _blend_hex(hx, surf, 0.18)
    elif axis_key == "fault":
        out["fault_soft"] = _blend_hex(hx, "#684850", 0.70)
        out["fault_bg"] = _blend_hex(hx, surf, 0.19)
    return out


def sanitize_theme_dict(theme_dict: dict) -> dict:
    """
    MEMENTO DI GUARDIA (Golden Standard §2, §4, §5):
    Sanitizza in-place qualsiasi dizionario tema (caricato da disco, importato da `.toml` esterno
    o applicato da preset), bloccando qualsiasi tentativo di:
      1. sostituire `button_brackets = ["[", "]"]` con `(...)`, `<...>`, `{...}` o stringhe vuote;
      2. usare glifi ambigui/collidenti su `pill_dots`, `slider_fill`, `tab_caps`, `collapsible_icons`, `kv_separator`;
      3. usare `box_style` fuori metrica o lasciare disallineati `thresholds` e alias colore legacy.
    """
    theme_dict.setdefault("typography", {}).pop("notif_font_scale", None)

    asc = theme_dict.setdefault("ascii", {})
    st_name = str(asc.get("box_style", "rounded") or "rounded").strip().lower()
    if st_name not in BOX_STYLES:
        st_name = "rounded"
    asc["box_style"] = st_name
    asc["chars"] = dict(BOX_STYLES[st_name])

    # Invariante morfologico assoluto: tutti i controlli cliccabili discreti usano '[' e ']'
    asc["button_brackets"] = ["[", "]"]
    asc["search_brackets"] = ["\u258f", "\u2595"]

    def _norm_pair(raw, valid_pairs: tuple, default_pair: tuple[str, str]) -> list[str]:
        if isinstance(raw, (list, tuple)) and len(raw) >= 2:
            cand = (str(raw[0] or ""), str(raw[1] or ""))
            if cand in valid_pairs:
                return [cand[0], cand[1]]
        return [default_pair[0], default_pair[1]]

    asc["title_brackets"] = _norm_pair(asc.get("title_brackets"), VALID_TITLE_BRACKETS, ("[ ", " ]"))
    asc["slider_brackets"] = _norm_pair(asc.get("slider_brackets"), VALID_SLIDER_BRACKETS, ("[", "]"))
    asc["pill_dots"] = _norm_pair(asc.get("pill_dots"), VALID_PILL_DOTS, ("\u25cf", "\u25cb"))
    asc["tab_caps"] = _norm_pair(asc.get("tab_caps"), VALID_TAB_CAPS, ("\u258f", "\u2595"))
    asc["collapsible_icons"] = _norm_pair(asc.get("collapsible_icons"), VALID_COLLAPSIBLE_ICONS, ("\u25bc", "\u25b6"))

    sl_pair = (str(asc.get("slider_fill") or "#")[:1], str(asc.get("slider_empty") or "-")[:1])
    if sl_pair not in VALID_SLIDER_GLYPHS:
        sl_pair = ("#", "-")
    asc["slider_fill"], asc["slider_empty"] = sl_pair[0], sl_pair[1]

    tr = str(asc.get("table_rule") or "\u2500")[:1]
    asc["table_rule"] = tr if tr in VALID_TABLE_RULES else "\u2500"

    kvs = str(asc.get("kv_separator") or ":")[:2].strip()
    asc["kv_separator"] = kvs if kvs in VALID_KV_SEPARATORS else ":"

    sp_chars = str(asc.get("sparkline_chars") or "")
    if len(sp_chars) < 2:
        asc["sparkline_chars"] = DEFAULT_THEME["ascii"]["sparkline_chars"]

    scur = str(asc.get("search_cursor") or "\u2502")[:1]
    if scur in ("\u2588", "|"):
        scur = "\u2502"
    asc["search_cursor"] = scur if scur in ("\u2502", "_") else "\u2502"
    spref = str(asc.get("search_prefix") or "\uf002 >").strip()
    asc["search_prefix"] = spref if spref in ("\uf002 >", "/ >", "\u203a", "? >") else "\uf002 >"
    asc["sys_notif_style"] = _norm_notif_style(asc.get("sys_notif_style"), "clean")
    asc["toast_style"] = _norm_notif_style(asc.get("toast_style"), "tactical")
    asc["hover_help_style"] = _norm_notif_style(asc.get("hover_help_style"), "cyberpunk")

    colors = theme_dict.setdefault("colors", {})
    def_c = DEFAULT_THEME["colors"]
    surf_hex = _norm_hex_color(colors.get("surface_bg"), def_c["surface_bg"])
    colors["surface_bg"] = surf_hex
    try:
        win_op = max(0.05, min(1.0, float(colors.get("window_opacity", def_c["window_opacity"]))))
    except Exception:
        win_op = def_c["window_opacity"]
    colors["window_opacity"] = win_op
    try:
        box_op = max(0.0, min(1.0, float(colors.get("box_opacity", def_c["box_opacity"]))))
    except Exception:
        box_op = def_c["box_opacity"]
    colors["box_opacity"] = box_op
    try:
        toast_op = max(0.0, min(1.0, float(colors.get("toast_opacity", def_c["toast_opacity"]))))
    except Exception:
        toast_op = def_c["toast_opacity"]
    colors["toast_opacity"] = toast_op
    try:
        sys_notif_op = max(0.0, min(1.0, float(colors.get("sys_notif_opacity", def_c["sys_notif_opacity"]))))
    except Exception:
        sys_notif_op = def_c["sys_notif_opacity"]
    colors["sys_notif_opacity"] = sys_notif_op
    try:
        hover_op = max(0.0, min(1.0, float(colors.get("hover_help_opacity", def_c["hover_help_opacity"]))))
    except Exception:
        hover_op = def_c["hover_help_opacity"]
    colors["hover_help_opacity"] = hover_op
    colors["waybar_sync_opacity"] = bool(colors.get("waybar_sync_opacity", True))
    colors["waybar_sync_color"] = bool(colors.get("waybar_sync_color", True))

    for ck in (
        "structure", "structure_bg", "inactive", "schema", "focus", "focus_val",
        "intent", "intent_soft", "intent_bg", "vitality", "vitality_soft", "vitality_bg",
        "attention", "attention_soft", "attention_bg", "interrupt", "interrupt_rest", "interrupt_bg",
        "fault", "fault_soft", "fault_bg",
    ):
        colors[ck] = _norm_hex_color(colors.get(ck), def_c[ck])

    wb_fallbacks = {
        "wb_bg": surf_hex,
        "wb_pill_bg": colors["structure_bg"],
        "wb_fg": colors["focus_val"],
        "wb_accent": colors["intent"],
        "wb_active": colors["vitality"],
        "wb_warn": colors["attention"],
        "wb_crit": colors["fault"],
    }
    for wk, wdef in wb_fallbacks.items():
        colors[wk] = _norm_hex_color(colors.get(wk), wdef)

    r, g, b = int(surf_hex[1:3], 16), int(surf_hex[3:5], 16), int(surf_hex[5:7], 16)
    colors["window_bg"] = f"rgba({r}, {g}, {b}, {win_op:.2f})"
    colors["primary"] = colors["focus"]
    colors["secondary"] = colors["schema"]
    colors["muted"] = colors["inactive"]
    colors["accent"] = colors["intent"]
    colors["info"] = colors["intent"]
    colors["window_border"] = colors["structure"]
    colors["box_inactive"] = colors["structure"]
    colors["box_elected"] = colors["structure"]
    colors["box_highlight"] = colors["focus"]
    colors["box_selected"] = colors["focus"]
    colors["input_border"] = colors["structure"]
    colors["input_border_focus"] = colors["intent"]

    th = colors.setdefault("thresholds", {})
    th["normal"] = colors["vitality"]
    th["warning"] = colors["attention"]
    th["alert"] = colors["interrupt"]
    th["critical"] = colors["fault"]

    geom = theme_dict.setdefault("geometry", {})
    try:
        geom["row_gap"] = max(0, min(6, int(geom.get("row_gap", 2))))
    except Exception:
        geom["row_gap"] = 2
    return theme_dict


def load_theme() -> dict:
    repo_theme = find_package_file("config", "ncui", "theme.toml")
    merged = _deep_merge(DEFAULT_THEME, _load_toml(repo_theme) if repo_theme else {})
    merged = _deep_merge(merged, _load_toml(THEME_PATH))
    return sanitize_theme_dict(merged)


def load_config() -> dict:
    repo_cfg = find_package_file("config", "ncui", "config.toml")
    merged = _deep_merge(DEFAULT_CONFIG, _load_toml(repo_cfg) if repo_cfg else {})
    merged = _deep_merge(merged, _load_toml(CONFIG_PATH))
    if isinstance(merged.get("waybar_widgets"), dict):
        merged["waybar_plugins"] = _deep_merge(merged.get("waybar_plugins", {}), merged.pop("waybar_widgets"))
    return merged


def get_waybar_look() -> dict:
    res = {"top": 34, "bg": None, "rad": None}
    h = 30
    wb_conf = os.path.expanduser("~/.config/waybar/config.jsonc")
    if os.path.exists(wb_conf):
        try:
            with open(wb_conf, "r", encoding="utf-8") as f:
                c = f.read()
                m_h = re.search(r'"height"\s*:\s*(\d+)', c)
                if m_h:
                    h = int(m_h.group(1))
                m_mt = re.search(r'"margin-top"\s*:\s*(\d+)', c)
                mt = int(m_mt.group(1)) if m_mt else 0
                res["top"] = h + mt
        except Exception:
            pass

    wb_css = os.path.expanduser("~/.config/waybar/style.css")
    if os.path.exists(wb_css):
        try:
            with open(wb_css, "r", encoding="utf-8") as f:
                c = f.read()
                m_blk = re.search(r'window#waybar\s*\{([^}]+)\}', c)
                if m_blk:
                    blk = m_blk.group(1)
                    m_bg = re.search(r'background-color\s*:\s*([^;]+);', blk)
                    if m_bg:
                        res["bg"] = m_bg.group(1).strip()
                    m_rad = re.search(r'border-radius\s*:\s*(\d+)', blk)
                    if m_rad:
                        res["rad"] = min(int(m_rad.group(1)), h // 2)
        except Exception:
            pass
    return res


def get_layout_margins() -> tuple[int, int]:
    ctrl = CONFIG.get("control_center", {})
    geom = THEME.get("geometry", {})
    inner_raw = ctrl.get("inner_padding", geom.get("panel_padding", 10))
    outer_raw = ctrl.get("outer_margin", geom.get("screen_margin", 8))
    try:
        inner_i = max(2, min(64, int(inner_raw)))
    except Exception:
        inner_i = 10
    try:
        outer_i = max(0, min(64, int(outer_raw)))
    except Exception:
        outer_i = 8
    return inner_i, outer_i


def sync_waybar_outer_margin(outer_margin: int, reload_bar: bool = False) -> None:
    val = max(0, min(64, int(outer_margin)))
    wb_paths = [os.path.expanduser("~/.config/waybar/config.jsonc")] + find_all_package_files("config", "waybar", "config.jsonc")
    changed = False
    for rp in _unique_existing_paths(*wb_paths):
        try:
            with open(rp, "r", encoding="utf-8") as f:
                content = f.read()
            new_c = re.sub(
                r'("margin-(?:top|left|right)"\s*:\s*)\d+',
                lambda m: f"{m.group(1)}{val}",
                content,
            )
            if new_c != content:
                with open(rp, "w", encoding="utf-8") as f:
                    f.write(new_c)
                changed = True
        except Exception:
            pass
    if reload_bar and changed:
        try:
            subprocess.Popen(
                ["pkill", "-SIGUSR2", "waybar"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except Exception:
            pass


def set_layout_margins(
    inner_padding: int = None,
    outer_margin: int = None,
    sync_bar: bool = True,
    reload_bar: bool = False,
) -> tuple[int, int]:
    cur_inner, cur_outer = get_layout_margins()
    new_inner = max(2, min(64, int(inner_padding))) if inner_padding is not None else cur_inner
    new_outer = max(0, min(64, int(outer_margin))) if outer_margin is not None else cur_outer

    theme_targets = (THEME_PATH,) + tuple(find_all_package_files("config", "ncui", "theme.toml"))

    if inner_padding is not None:
        CONFIG.setdefault("control_center", {})["inner_padding"] = new_inner
        THEME.setdefault("geometry", {})["panel_padding"] = new_inner
        _update_toml_key_in_section("control_center", "inner_padding", str(new_inner))
        _update_toml_key_in_section(
            "geometry", "panel_padding", str(new_inner),
            target_files=theme_targets, create_fallback=False,
        )

    if outer_margin is not None:
        CONFIG.setdefault("control_center", {})["outer_margin"] = new_outer
        THEME.setdefault("geometry", {})["screen_margin"] = new_outer
        THEME.setdefault("geometry", {})["popup_gap_top"] = new_outer
        _update_toml_key_in_section("control_center", "outer_margin", str(new_outer))
        _update_toml_key_in_section(
            "geometry", "screen_margin", str(new_outer),
            target_files=theme_targets, create_fallback=False,
        )
        _update_toml_key_in_section(
            "geometry", "popup_gap_top", str(new_outer),
            target_files=theme_targets, create_fallback=False,
        )
        if sync_bar:
            sync_waybar_outer_margin(new_outer, reload_bar=reload_bar)

    return new_inner, new_outer


THEME = load_theme()
CONFIG = load_config()
_init_in_pad, _init_out_mar = get_layout_margins()
THEME.setdefault("geometry", {})["panel_padding"] = _init_in_pad
THEME["geometry"]["screen_margin"] = _init_out_mar
THEME["geometry"]["popup_gap_top"] = _init_out_mar

VALID_CAPTURE_ACTIONS = ("clipboard", "satty", "save", "clipboard_satty")


def _update_toml_key_in_section(
    section: str, key: str, raw_toml_val: str, target_files: tuple = None, create_fallback: bool = True
) -> None:
    if target_files is None:
        target_files = (CONFIG_PATH,) + tuple(find_all_package_files("config", "ncui", "config.toml"))
    sec_hdr_re = re.compile(rf"^\[{re.escape(section)}\]\s*$", flags=re.MULTILINE)
    key_in_sec_re = re.compile(
        rf"(^\[{re.escape(section)}\]\s*\n(?:(?!^\[)[^\n]*\n)*?^\s*{re.escape(key)}\s*=\s*)(?:\"[^\n\"]*\"|\[[^\n\]]*\]|[^\n#]+)",
        flags=re.MULTILINE,
    )
    updated_any = False

    for rp in _unique_existing_paths(*target_files):
        try:
            with open(rp, "r", encoding="utf-8") as f:
                content = f.read()
            if sec_hdr_re.search(content):
                if key_in_sec_re.search(content):
                    new_c = key_in_sec_re.sub(lambda m: m.group(1) + raw_toml_val, content, count=1)
                else:
                    new_c = sec_hdr_re.sub(f"[{section}]\n{key} = {raw_toml_val}", content, count=1)
            else:
                new_c = content.rstrip() + f"\n\n[{section}]\n{key} = {raw_toml_val}\n"
            with open(rp, "w", encoding="utf-8") as f:
                f.write(new_c)
            updated_any = True
        except Exception:
            pass

    if not updated_any and create_fallback:
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                f.write(f"[{section}]\n{key} = {raw_toml_val}\n")
        except Exception:
            pass


def get_hide_unavailable_settings() -> bool:
    from core.state import STATE
    st_val = STATE.get("control_center", "hide_unavailable_settings", None)
    if st_val is not None:
        return bool(st_val)
    return bool(CONFIG.get("control_center", {}).get("hide_unavailable_settings", False))


def set_hide_unavailable_settings(val: bool) -> bool:
    from core.state import STATE
    STATE.set("control_center", "hide_unavailable_settings", bool(val))
    CONFIG.setdefault("control_center", {})["hide_unavailable_settings"] = bool(val)
    _update_toml_key_in_section("control_center", "hide_unavailable_settings", "true" if val else "false")
    return bool(val)


def get_hover_tooltips_enabled() -> bool:
    from core.state import STATE
    st_val = STATE.get("general", "enable_hover_tooltips", None)
    if st_val is not None:
        return bool(st_val)
    return bool(CONFIG.get("general", {}).get("enable_hover_tooltips", True))


def set_hover_tooltips_enabled(val: bool) -> bool:
    from core.state import STATE
    b = bool(val)
    STATE.set("general", "enable_hover_tooltips", b)
    CONFIG.setdefault("general", {})["enable_hover_tooltips"] = b
    _update_toml_key_in_section("general", "enable_hover_tooltips", "true" if b else "false")
    return b


def get_hover_tooltip_delay() -> float:
    from core.state import STATE
    st_val = STATE.get("general", "hover_tooltip_delay", None)
    raw = st_val if st_val is not None else CONFIG.get("general", {}).get("hover_tooltip_delay", 0.7)
    try:
        v = float(raw)
    except (TypeError, ValueError):
        v = 0.7
    return round(max(0.2, min(3.0, v)), 1)


def set_hover_tooltip_delay(val: float) -> float:
    from core.state import STATE
    try:
        v = float(val)
    except (TypeError, ValueError):
        v = 0.7
    v = round(max(0.2, min(3.0, v)), 1)
    STATE.set("general", "hover_tooltip_delay", v)
    CONFIG.setdefault("general", {})["hover_tooltip_delay"] = v
    _update_toml_key_in_section("general", "hover_tooltip_delay", f"{v:.1f}")
    return v


def get_sys_notif_style() -> str:
    return _norm_notif_style((THEME.get("ascii") or {}).get("sys_notif_style"), "clean")


def set_sys_notif_style(style: str) -> str:
    clean = _norm_notif_style(style, "clean")
    THEME.setdefault("ascii", {})["sys_notif_style"] = clean
    targets = _theme_toml_targets()
    _update_toml_key_in_section("ascii", "sys_notif_style", f'"{clean}"', target_files=targets, create_fallback=False)
    return clean


def get_toast_style() -> str:
    return _norm_notif_style((THEME.get("ascii") or {}).get("toast_style"), "tactical")


def set_toast_style(style: str) -> str:
    clean = _norm_notif_style(style, "tactical")
    THEME.setdefault("ascii", {})["toast_style"] = clean
    targets = _theme_toml_targets()
    _update_toml_key_in_section("ascii", "toast_style", f'"{clean}"', target_files=targets, create_fallback=False)
    return clean


def get_hover_help_style() -> str:
    return _norm_notif_style((THEME.get("ascii") or {}).get("hover_help_style"), "cyberpunk")


def set_hover_help_style(style: str) -> str:
    clean = _norm_notif_style(style, "cyberpunk")
    THEME.setdefault("ascii", {})["hover_help_style"] = clean
    targets = _theme_toml_targets()
    _update_toml_key_in_section("ascii", "hover_help_style", f'"{clean}"', target_files=targets, create_fallback=False)
    return clean


def get_capture_action() -> str:
    act = str(CONFIG.get("capture", {}).get("action", "clipboard_satty")).strip().lower()
    return act if act in VALID_CAPTURE_ACTIONS else "clipboard_satty"


def set_capture_action(action: str) -> str:
    clean = str(action or "").strip().lower()
    if clean not in VALID_CAPTURE_ACTIONS:
        clean = "clipboard_satty"
    CONFIG.setdefault("capture", {})["action"] = clean
    _update_toml_key_in_section("capture", "action", f'"{clean}"')
    return clean


def get_network_show_bar_speed() -> bool:
    from core.state import STATE
    st_val = STATE.get("network", "show_bar_speed", None)
    if st_val is not None:
        return bool(st_val)
    return bool(CONFIG.get("network", {}).get("show_bar_speed", False))


def set_network_show_bar_speed(enabled: bool) -> bool:
    from core.state import STATE
    val = bool(enabled)
    STATE.set("network", "show_bar_speed", val)
    CONFIG.setdefault("network", {})["show_bar_speed"] = val
    _update_toml_key_in_section("network", "show_bar_speed", "true" if val else "false")
    return val


def get_waybar_width_pcts() -> tuple[int, int]:
    from core.state import STATE
    w_cfg = CONFIG.get("waybar_plugins", {})
    win_pct = STATE.get("waybar_plugins", "window_width_pct", STATE.get("waybar_widgets", "window_width_pct", w_cfg.get("window_width_pct", 45)))
    med_pct = STATE.get("waybar_plugins", "media_width_pct", STATE.get("waybar_widgets", "media_width_pct", w_cfg.get("media_width_pct", 35)))
    try:
        win_i = max(5, min(100, int(win_pct)))
    except Exception:
        win_i = 45
    try:
        med_i = max(5, min(100, int(med_pct)))
    except Exception:
        med_i = 35
    return win_i, med_i


def set_waybar_width_pct(target: str, pct: int, both_active_on_bar: bool = True) -> tuple[int, int]:
    from core.state import STATE
    win_i, med_i = get_waybar_width_pcts()
    clamped = max(5, min(100, int(pct)))
    if target == "window":
        win_i = clamped
        if both_active_on_bar and (win_i + med_i > 100):
            med_i = max(5, 100 - win_i)
    elif target == "media":
        med_i = clamped
        if both_active_on_bar and (win_i + med_i > 100):
            win_i = max(5, 100 - med_i)

    STATE.set("waybar_plugins", "window_width_pct", win_i)
    STATE.set("waybar_plugins", "media_width_pct", med_i)
    CONFIG.setdefault("waybar_plugins", {})["window_width_pct"] = win_i
    CONFIG.setdefault("waybar_plugins", {})["media_width_pct"] = med_i
    _update_toml_key_in_section("waybar_plugins", "window_width_pct", str(win_i))
    _update_toml_key_in_section("waybar_plugins", "media_width_pct", str(med_i))
    return win_i, med_i


def get_screen_lock_cmd() -> str:
    from core.state import STATE
    st_val = STATE.get("waybar_plugins", "lock_cmd", None)
    if st_val is None:
        st_val = STATE.get("waybar_widgets", "lock_cmd", None)
    if st_val is not None:
        return str(st_val).strip()
    return str(CONFIG.get("waybar_plugins", {}).get("lock_cmd", "")).strip()


def set_screen_lock_cmd(cmd_str: str) -> str:
    from core.state import STATE
    clean = str(cmd_str or "").strip()
    STATE.set("waybar_plugins", "lock_cmd", clean)
    CONFIG.setdefault("waybar_plugins", {})["lock_cmd"] = clean
    escaped = clean.replace("\\", "\\\\").replace('"', '\\"')
    _update_toml_key_in_section("waybar_plugins", "lock_cmd", f'"{escaped}"')
    return clean


def estimate_waybar_free_chars_budget() -> dict:
    mon_w = 1920
    try:
        res = subprocess.run(
            ["niri", "msg", "-j", "focused-output"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=0.4,
            check=False,
        )
        if res.returncode == 0 and res.stdout:
            data = json.loads(res.stdout.decode("utf-8", errors="replace"))
            lw = (data.get("logical") or {}).get("width")
            if lw and int(lw) > 320:
                mon_w = int(lw)
    except Exception:
        pass

    wb_conf = os.path.expanduser("~/.config/waybar/config.jsonc")
    active_mods = []
    if os.path.exists(wb_conf):
        try:
            with open(wb_conf, "r", encoding="utf-8") as f:
                raw = f.read()
            cleaned = re.sub(r'("(?:\\.|[^"\\])*")|//[^\n]*', lambda m: m.group(1) if m.group(1) else "", raw)
            cleaned = re.sub(r",(\s*[}\]])", r"\1", cleaned)
            d = json.loads(cleaned)
            if isinstance(d, dict):
                active_mods = (
                    list(d.get("modules-left", []) or [])
                    + list(d.get("modules-center", []) or [])
                    + list(d.get("modules-right", []) or [])
                )
        except Exception:
            pass

    if not active_mods:
        active_mods = [
            "niri/workspaces", "niri/window", "custom/settings", "custom/calendar",
            "custom/desktop", "custom/cliphist", "custom/screenshot", "custom/audio",
            "custom/netcycle", "custom/cpu", "custom/ram", "custom/gpu", "custom/notifications",
        ]

    from core.state import STATE
    fixed_px = 40
    show_spd = get_network_show_bar_speed()
    mod_px_estimates = {
        "niri/workspaces": 135,
        "custom/settings": 52,
        "custom/calendar": 240,
        "custom/desktop": 52,
        "custom/cliphist": 52,
        "custom/screenshot": 52,
        "custom/audio": 54,
        "custom/netcycle": 270 if show_spd else 108,
        "custom/cpu": 88,
        "custom/ram": 88,
        "custom/gpu": 88,
        "custom/notifications": 54,
        "custom/printers": 76 if STATE.get("waybar", "printers_bar_mode", "auto") == "always" else 0,
        "custom/ups": 115,
        "custom/kb_layout": 68,
        "custom/telegram": 52,
        "custom/session": 52,
        "tray": 0,
    }
    for m in active_mods:
        if m in ("niri/window", "custom/media"):
            fixed_px += 40
        else:
            fixed_px += mod_px_estimates.get(m, 64)

    free_px = max(240, int((mon_w - fixed_px) * 0.88))
    char_px = 8.0
    total_free_chars = max(30, int(round(free_px / char_px)))

    has_window = ("niri/window" in active_mods)
    has_media = ("custom/media" in active_mods)
    win_pct, med_pct = get_waybar_width_pcts()

    if has_window and has_media and (win_pct + med_pct > 100):
        tot_p = float(win_pct + med_pct)
        eff_win_pct = int(round((win_pct / tot_p) * 100.0))
        eff_med_pct = max(5, 100 - eff_win_pct)
    else:
        eff_win_pct = win_pct
        eff_med_pct = med_pct

    win_chars = max(10, int(round(total_free_chars * (eff_win_pct / 100.0))))
    med_chars = max(10, int(round(total_free_chars * (eff_med_pct / 100.0))))

    return {
        "mon_w": mon_w,
        "free_px": free_px,
        "total_free_chars": total_free_chars,
        "has_window": has_window,
        "has_media": has_media,
        "window_pct": win_pct,
        "media_pct": med_pct,
        "window_chars": win_chars,
        "media_chars": med_chars,
    }


def _parse_rgba_components(color_str: str) -> tuple[int, int, int, float]:
    s = str(color_str or "").strip()
    m_rgba = re.match(
        r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([0-9.]+)\s*)?\)",
        s,
        flags=re.IGNORECASE,
    )
    if m_rgba:
        r = max(0, min(255, int(m_rgba.group(1))))
        g = max(0, min(255, int(m_rgba.group(2))))
        b = max(0, min(255, int(m_rgba.group(3))))
        a = float(m_rgba.group(4)) if m_rgba.group(4) is not None else 0.88
        return r, g, b, max(0.0, min(1.0, a))
    m_hex = re.match(r"^#([0-9a-fA-F]{6})$", s)
    if m_hex:
        hx = m_hex.group(1)
        return int(hx[0:2], 16), int(hx[2:4], 16), int(hx[4:6], 16), 0.88
    return 13, 13, 15, 0.88


def _theme_toml_targets() -> tuple[str, ...]:
    return (THEME_PATH,) + tuple(find_all_package_files("config", "ncui", "theme.toml"))


def get_surface_bg_hex() -> str:
    return _norm_hex_color(THEME.get("colors", {}).get("surface_bg"), "#080a0f")


def get_surface_bg_rgb() -> tuple[int, int, int]:
    hx = get_surface_bg_hex()
    return int(hx[1:3], 16), int(hx[3:5], 16), int(hx[5:7], 16)


def get_window_opacity() -> float:
    try:
        return max(0.0, min(1.0, float(THEME.get("colors", {}).get("window_opacity", 0.86))))
    except Exception:
        return 0.86


def get_ui_transparency_pct() -> int:
    return max(5, min(100, int(round(get_window_opacity() * 100.0))))


def get_box_opacity() -> float:
    try:
        return max(0.0, min(1.0, float(THEME.get("colors", {}).get("box_opacity", 0.72))))
    except Exception:
        return 0.72


def get_box_opacity_pct() -> int:
    return max(0, min(100, int(round(get_box_opacity() * 100.0))))


def get_toast_opacity() -> float:
    try:
        return max(0.0, min(1.0, float(THEME.get("colors", {}).get("toast_opacity", 0.95))))
    except Exception:
        return 0.95


def get_toast_opacity_pct() -> int:
    return max(0, min(100, int(round(get_toast_opacity() * 100.0))))


def get_sys_notif_opacity() -> float:
    try:
        return max(0.0, min(1.0, float(THEME.get("colors", {}).get("sys_notif_opacity", 0.95))))
    except Exception:
        return 0.95


def get_sys_notif_opacity_pct() -> int:
    return max(0, min(100, int(round(get_sys_notif_opacity() * 100.0))))


def get_hover_help_opacity() -> float:
    try:
        return max(0.0, min(1.0, float(THEME.get("colors", {}).get("hover_help_opacity", 0.96))))
    except Exception:
        return 0.96


def get_hover_help_opacity_pct() -> int:
    return max(0, min(100, int(round(get_hover_help_opacity() * 100.0))))


def get_waybar_sync_opacity() -> bool:
    return bool(THEME.get("colors", {}).get("waybar_sync_opacity", True))


def get_waybar_sync_color() -> bool:
    return bool(THEME.get("colors", {}).get("waybar_sync_color", True))


def sync_waybar_colors_from_theme(colors_dict: dict = None) -> dict:
    """Deriva i 7 canali cromatici Waybar dalla palette corrente del tema NC-UI."""
    c = colors_dict if isinstance(colors_dict, dict) else THEME.setdefault("colors", {})
    c["wb_bg"] = _norm_hex_color(c.get("surface_bg"), "#080a0f")
    c["wb_pill_bg"] = _norm_hex_color(c.get("structure_bg"), "#141824")
    c["wb_fg"] = _norm_hex_color(c.get("focus_val"), "#dce6f7")
    c["wb_accent"] = _norm_hex_color(c.get("intent"), "#00f0ff")
    c["wb_active"] = _norm_hex_color(c.get("vitality"), "#00f57a")
    c["wb_warn"] = _norm_hex_color(c.get("attention"), "#ffe619")
    c["wb_crit"] = _norm_hex_color(c.get("fault"), "#ff2442")
    return c


def _hex_to_rgb(hx: str) -> tuple[int, int, int]:
    clean = _norm_hex_color(hx, "#080a0f")
    return int(clean[1:3], 16), int(clean[3:5], 16), int(clean[5:7], 16)


def apply_waybar_palette(
    sync_opacity: bool = None,
    sync_color: bool = None,
    force_palette: bool = False,
    css_targets: tuple = None,
) -> bool:
    if sync_opacity is None:
        sync_opacity = get_waybar_sync_opacity()
    if sync_color is None:
        sync_color = get_waybar_sync_color()
    if not force_palette and not sync_opacity and not sync_color:
        return False

    c = THEME.get("colors", {})
    surf_hex = get_surface_bg_hex()
    wb_bg = _norm_hex_color(c.get("wb_bg"), surf_hex) if force_palette else surf_hex
    wb_pill = _norm_hex_color(c.get("wb_pill_bg"), c.get("structure_bg", "#141824"))
    wb_fg = _norm_hex_color(c.get("wb_fg"), c.get("focus_val", "#dce6f7"))
    wb_acc = _norm_hex_color(c.get("wb_accent"), c.get("intent", "#00f0ff"))
    wb_act = _norm_hex_color(c.get("wb_active"), c.get("vitality", "#00f57a"))
    wb_warn = _norm_hex_color(c.get("wb_warn"), c.get("attention", "#ffe619"))
    wb_crit = _norm_hex_color(c.get("wb_crit"), c.get("fault", "#ff2442"))
    focus_hex = _norm_hex_color(c.get("focus"), "#ffffff")
    schema_hex = _norm_hex_color(c.get("schema"), "#8b9bb8")

    bg_r, bg_g, bg_b = _hex_to_rgb(wb_bg)
    pr, pg, pb = _hex_to_rgb(wb_pill)
    ar, ag, ab = _hex_to_rgb(wb_acc)
    vr, vg, vb = _hex_to_rgb(wb_act)
    wr, wg, wb_c = _hex_to_rgb(wb_warn)
    cr, cg, cb = _hex_to_rgb(wb_crit)
    win_alpha = max(0.05, min(1.0, get_window_opacity()))

    def _set_css_prop(block: str, prop: str, val: str) -> str:
        return re.sub(rf"((?<![\w-]){prop}\s*:\s*)[^;]+(;)", lambda m: m.group(1) + val + m.group(2), block, count=1)

    def _sub_block(src: str, header_pat: str, updates: dict[str, str]) -> str:
        pat = rf"(^{header_pat}\s*\{{)([^}}]*)(\}})"

        def _repl(m):
            body = m.group(2)
            for k, v in updates.items():
                body = _set_css_prop(body, k, v)
            return m.group(1) + body + m.group(3)

        return re.sub(pat, _repl, src, count=1, flags=re.MULTILINE)

    def _sub_keyframes(src: str, kf_name: str, transform) -> str:
        pat = rf"(@keyframes\s+{re.escape(kf_name)}\s*\{{)([\s\S]*?)(\n\}})"
        return re.sub(pat, lambda m: m.group(1) + transform(m.group(2)) + m.group(3), src, count=1)

    if css_targets is not None:
        paths = _unique_existing_paths(*css_targets)
    else:
        paths = _unique_existing_paths(
            os.path.expanduser("~/.config/waybar/style.css"),
            *find_all_package_files("config", "waybar", "style.css"),
        )

    for rp in paths:
        try:
            with open(rp, "r", encoding="utf-8") as f:
                css_content = f.read()

            def _repl_wb(m):
                block = m.group(0)
                m_bg = re.search(r"background-color\s*:\s*([^;]+);", block)
                cur_r, cur_g, cur_b, cur_a = _parse_rgba_components(m_bg.group(1)) if m_bg else (11, 11, 14, 0.88)
                r = bg_r if (force_palette or sync_color) else cur_r
                g = bg_g if (force_palette or sync_color) else cur_g
                b = bg_b if (force_palette or sync_color) else cur_b
                a = win_alpha if (force_palette or sync_opacity) else cur_a
                out = _set_css_prop(block, "background-color", f"rgba({r}, {g}, {b}, {a:.2f})")
                if force_palette:
                    out = _set_css_prop(out, "color", wb_fg)
                return out

            new_css = re.sub(r"window#waybar\s*\{[^}]+\}", _repl_wb, css_content, count=1)
            if force_palette:
                new_css = _sub_keyframes(
                    new_css,
                    "nc-glow-pulse",
                    lambda body: re.sub(
                        r"rgba\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*,\s*(?:0\.08|0\.62)\)",
                        f"rgba({pr}, {pg}, {pb}, 0.62)",
                        re.sub(
                            r"rgba\(\s*0\s*,\s*255\s*,\s*(?:115|102)\s*,|rgba\(\s*166\s*,\s*227\s*,\s*161\s*,|rgba\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*,\s*(?=(?:0\.0|0\.16|0\.22|0\.44|0\.45|0\.90)\))",
                            f"rgba({vr}, {vg}, {vb}, ",
                            body,
                        ),
                    ),
                )
                new_css = _sub_keyframes(
                    new_css,
                    "nc-glow-crit",
                    lambda body: re.sub(
                        r"rgba\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*,\s*(?:0\.08|0\.62)\)",
                        f"rgba({pr}, {pg}, {pb}, 0.62)",
                        re.sub(
                            r"rgba\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*,\s*(?!(?:0\.08|0\.62)\))",
                            f"rgba({cr}, {cg}, {cb}, ",
                            body,
                        ),
                    ),
                )
                block_rules = [
                    (r"#workspaces", {"background-color": f"rgba({pr}, {pg}, {pb}, 0.85)"}),
                    (r"#workspaces\s+button", {"color": schema_hex}),
                    (r"#workspaces\s+button:hover", {
                        "background-color": f"rgba({ar}, {ag}, {ab}, 0.22)",
                        "color": focus_hex,
                        "box-shadow": f"0 0 2.5px 0 rgba({ar}, {ag}, {ab}, 0.30), inset 0 0 5px rgba({ar}, {ag}, {ab}, 0.20)",
                    }),
                    (r"#clock,[\s\S]*?\.module-nc", {"color": wb_fg, "background-color": f"rgba({pr}, {pg}, {pb}, 0.62)"}),
                    (r"#clock:hover,[\s\S]*?eventbox:hover\s*>\s*\.module-nc", {
                        "background-color": f"rgba({ar}, {ag}, {ab}, 0.22)",
                        "color": focus_hex,
                        "box-shadow": f"0 0 2.5px 0 rgba({ar}, {ag}, {ab}, 0.30), inset 0 0 6px rgba({ar}, {ag}, {ab}, 0.20)",
                    }),
                    (r"#window", {"color": wb_fg}),
                    (r"#custom-media", {"color": wb_fg}),
                    (r"#wireplumber\.muted", {"color": wb_crit, "background-color": f"rgba({cr}, {cg}, {cb}, 0.15)"}),
                    (r"#workspaces\s+button\.active,\s*#workspaces\s+button\.focused", {
                        "color": focus_hex,
                        "background-color": f"rgba({vr}, {vg}, {vb}, 0.22)",
                        "background-image": f"radial-gradient(ellipse closest-side at center, rgba({vr}, {vg}, {vb}, 0.44) 0%, rgba({vr}, {vg}, {vb}, 0.16) 65%, rgba({vr}, {vg}, {vb}, 0.0) 100%)",
                        "box-shadow": f"0 0 2.5px 0 rgba({vr}, {vg}, {vb}, 0.45), inset 0 0 5px 0 rgba({vr}, {vg}, {vb}, 0.45)",
                        "text-shadow": f"0 0 6px rgba({vr}, {vg}, {vb}, 0.90)",
                    }),
                    (r"\.nc-glow,\s*#custom-notifications\.nc-glow,\s*\.module-nc-alert,\s*#custom-notifications\.module-nc-alert", {
                        "color": focus_hex,
                        "background-color": f"rgba({vr}, {vg}, {vb}, 0.16)",
                        "background-image": f"radial-gradient(ellipse closest-side at center, rgba({vr}, {vg}, {vb}, 0.30) 0%, rgba({vr}, {vg}, {vb}, 0.10) 65%, rgba({vr}, {vg}, {vb}, 0.0) 100%)",
                        "box-shadow": f"0 0 2px 0 rgba({vr}, {vg}, {vb}, 0.35), inset 0 0 4px 0 rgba({vr}, {vg}, {vb}, 0.35)",
                        "text-shadow": f"0 0 5px rgba({vr}, {vg}, {vb}, 0.70)",
                    }),
                    (r"#custom-ups\.online", {"color": wb_act}),
                    (r"#custom-ups\.on-battery", {
                        "color": wb_warn,
                        "background-color": f"rgba({wr}, {wg}, {wb_c}, 0.18)",
                        "box-shadow": f"inset 0 0 4px rgba({wr}, {wg}, {wb_c}, 0.45)",
                    }),
                    (r"\.nc-glow-crit,\s*#custom-cpu\.nc-glow-crit,\s*#custom-ram\.nc-glow-crit,\s*#custom-gpu\.nc-glow-crit,\s*#custom-ups\.low-battery", {
                        "color": focus_hex,
                        "background-color": f"rgba({cr}, {cg}, {cb}, 0.24)",
                        "background-image": f"radial-gradient(ellipse closest-side at center, rgba({cr}, {cg}, {cb}, 0.42) 0%, rgba({cr}, {cg}, {cb}, 0.16) 65%, rgba({cr}, {cg}, {cb}, 0.0) 100%)",
                        "box-shadow": f"0 0 2.5px 0 rgba({cr}, {cg}, {cb}, 0.45), inset 0 0 5px 0 rgba({cr}, {cg}, {cb}, 0.45)",
                        "text-shadow": f"0 0 6px rgba({cr}, {cg}, {cb}, 0.85)",
                    }),
                    (r"#custom-ups\.offline", {"color": wb_crit, "background-color": f"rgba({cr}, {cg}, {cb}, 0.14)"}),
                    (r"#custom-printers\.printing", {"color": wb_acc}),
                    (r"#custom-printers\.warn", {
                        "color": wb_warn,
                        "background-color": f"rgba({wr}, {wg}, {wb_c}, 0.18)",
                        "box-shadow": f"inset 0 0 4px rgba({wr}, {wg}, {wb_c}, 0.45)",
                    }),
                    (r"#custom-printers\.crit,\s*#custom-printers\.offline", {
                        "color": wb_crit,
                        "background-color": f"rgba({cr}, {cg}, {cb}, 0.14)",
                    }),
                ]
                for hdr_pat, upd in block_rules:
                    new_css = _sub_block(new_css, hdr_pat, upd)

            if new_css != css_content:
                with open(rp, "w", encoding="utf-8") as f:
                    f.write(new_css)
        except Exception:
            pass

    if css_targets is not None or os.environ.get("NC_UI_OFFSCREEN") == "1":
        return True
    try:
        subprocess.Popen(
            ["pkill", "-SIGUSR2", "waybar"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return True
    except Exception:
        return False


def apply_waybar_transparency(sync_opacity: bool = None, sync_color: bool = None) -> bool:
    return apply_waybar_palette(sync_opacity=sync_opacity, sync_color=sync_color, force_palette=False)


def set_ui_transparency_pct(opacity_pct: int, sync_waybar: bool = False) -> int:
    from core import probe
    from core.state import STATE
    clamped = max(5, min(100, int(opacity_pct)))
    STATE.set("appearance", "transparency_pct", clamped)
    alpha = round(max(0.05, min(1.0, clamped / 100.0)), 2)
    r, g, b = get_surface_bg_rgb()
    new_rgba = f"rgba({r}, {g}, {b}, {alpha:.2f})"
    colors = THEME.setdefault("colors", {})
    colors["window_opacity"] = alpha
    colors["window_bg"] = new_rgba

    targets = _theme_toml_targets()

    def _persist(a=alpha, rgba=new_rgba, tgts=targets) -> None:
        _update_toml_key_in_section("colors", "window_opacity", f"{a:.2f}", target_files=tgts, create_fallback=False)
        _update_toml_key_in_section("colors", "window_bg", f'"{rgba}"', target_files=tgts, create_fallback=False)

    probe.enqueue_latest("theme_window_op_save", _persist, debounce_ms=150)

    if sync_waybar is True:
        apply_waybar_transparency(sync_opacity=True, sync_color=get_waybar_sync_color())
    return clamped


def set_box_opacity_pct(opacity_pct: int) -> int:
    from core import probe
    clamped = max(0, min(100, int(opacity_pct)))
    box_op = round(clamped / 100.0, 2)
    THEME.setdefault("colors", {})["box_opacity"] = box_op
    targets = _theme_toml_targets()
    probe.enqueue_latest(
        "theme_box_op_save",
        lambda b=box_op, tgts=targets: _update_toml_key_in_section("colors", "box_opacity", f"{b:.2f}", target_files=tgts, create_fallback=False),
        debounce_ms=150,
    )
    return clamped


def set_toast_opacity_pct(opacity_pct: int) -> int:
    from core import probe
    clamped = max(0, min(100, int(opacity_pct)))
    toast_op = round(clamped / 100.0, 2)
    THEME.setdefault("colors", {})["toast_opacity"] = toast_op
    targets = _theme_toml_targets()
    probe.enqueue_latest(
        "theme_toast_op_save",
        lambda b=toast_op, tgts=targets: _update_toml_key_in_section("colors", "toast_opacity", f"{b:.2f}", target_files=tgts, create_fallback=False),
        debounce_ms=150,
    )
    return clamped


def set_sys_notif_opacity_pct(opacity_pct: int) -> int:
    from core import probe
    clamped = max(0, min(100, int(opacity_pct)))
    sys_op = round(clamped / 100.0, 2)
    THEME.setdefault("colors", {})["sys_notif_opacity"] = sys_op
    targets = _theme_toml_targets()
    probe.enqueue_latest(
        "theme_sys_notif_op_save",
        lambda b=sys_op, tgts=targets: _update_toml_key_in_section("colors", "sys_notif_opacity", f"{b:.2f}", target_files=tgts, create_fallback=False),
        debounce_ms=150,
    )
    return clamped


def set_hover_help_opacity_pct(opacity_pct: int) -> int:
    from core import probe
    clamped = max(0, min(100, int(opacity_pct)))
    hov_op = round(clamped / 100.0, 2)
    THEME.setdefault("colors", {})["hover_help_opacity"] = hov_op
    targets = _theme_toml_targets()
    probe.enqueue_latest(
        "theme_hover_help_op_save",
        lambda b=hov_op, tgts=targets: _update_toml_key_in_section("colors", "hover_help_opacity", f"{b:.2f}", target_files=tgts, create_fallback=False),
        debounce_ms=150,
    )
    return clamped


def set_surface_bg(hex_color: str, sync_waybar: bool = False) -> str:
    norm_hex = _norm_hex_color(hex_color, get_surface_bg_hex())
    r, g, b = int(norm_hex[1:3], 16), int(norm_hex[3:5], 16), int(norm_hex[5:7], 16)
    alpha = get_window_opacity()
    new_rgba = f"rgba({r}, {g}, {b}, {alpha:.2f})"
    colors = THEME.setdefault("colors", {})
    colors["surface_bg"] = norm_hex
    colors["window_bg"] = new_rgba

    targets = _theme_toml_targets()
    _update_toml_key_in_section("colors", "surface_bg", f'"{norm_hex}"', target_files=targets, create_fallback=False)
    _update_toml_key_in_section("colors", "window_bg", f'"{new_rgba}"', target_files=targets, create_fallback=False)

    if sync_waybar is True:
        apply_waybar_transparency(sync_opacity=get_waybar_sync_opacity(), sync_color=True)
    return norm_hex


def set_waybar_sync_opacity(enabled: bool, sync_waybar: bool = False) -> bool:
    val = bool(enabled)
    THEME.setdefault("colors", {})["waybar_sync_opacity"] = val
    _update_toml_key_in_section("colors", "waybar_sync_opacity", "true" if val else "false", target_files=_theme_toml_targets(), create_fallback=False)
    if val and sync_waybar is True:
        apply_waybar_transparency(sync_opacity=True, sync_color=get_waybar_sync_color())
    return val


def set_waybar_sync_color(enabled: bool, sync_waybar: bool = False) -> bool:
    val = bool(enabled)
    THEME.setdefault("colors", {})["waybar_sync_color"] = val
    _update_toml_key_in_section("colors", "waybar_sync_color", "true" if val else "false", target_files=_theme_toml_targets(), create_fallback=False)
    if val and sync_waybar is True:
        apply_waybar_transparency(sync_opacity=get_waybar_sync_opacity(), sync_color=True)
    return val


def get_row_gap() -> int:
    try:
        return max(0, min(6, int(THEME.get("geometry", {}).get("row_gap", 2))))
    except Exception:
        return 2


def set_row_gap(val: int) -> int:
    clamped = max(0, min(6, int(val)))
    THEME.setdefault("geometry", {})["row_gap"] = clamped
    _update_toml_key_in_section("geometry", "row_gap", str(clamped), target_files=_theme_toml_targets(), create_fallback=False)
    return clamped


def get_effective_window_bg(wb_look_bg: str = None) -> str:
    r, g, b = get_surface_bg_rgb()
    alpha = max(0.05, min(1.0, get_window_opacity()))
    return f"rgba({r}, {g}, {b}, {alpha:.2f})"


def color_for_pct(pct: float) -> str:
    """
    MEMENTO SOGLIE TELEMETRICHE (Golden Standard §4.1 / §4.3):
    Soglie unificate in tutto il sistema: <50% vitality, <75% attention, <90% interrupt, >=90% fault.
    """
    c = THEME.get("colors", {})
    th = c.get("thresholds", {})
    p = float(pct)
    if p < 50.0:
        return _norm_hex_color(c.get("vitality", th.get("normal")), "#00f57a")
    if p < 75.0:
        return _norm_hex_color(c.get("attention", th.get("warning")), "#ffe619")
    if p < 90.0:
        return _norm_hex_color(c.get("interrupt", th.get("alert")), "#ff2a9d")
    return _norm_hex_color(c.get("fault", th.get("critical")), "#ff2442")


def resolve_ncmatrix_bin() -> str:
    """Resolve optional `nc-matrix` binary via explicit config path or PATH."""
    wp = CONFIG.get("wallpaper") or {}
    raw = str(wp.get("nc_matrix_bin") or wp.get("ncmatrix_bin") or "").strip()
    if raw:
        p = os.path.expanduser(raw)
        return p if (os.path.isfile(p) and os.access(p, os.X_OK)) else ""
    return find_bin("nc-matrix")


def is_ncmatrix_available() -> bool:
    return bool(resolve_ncmatrix_bin())
