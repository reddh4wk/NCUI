#!/usr/bin/env python3
import os
import copy
import json
import re
import subprocess

try:
    import tomllib
except ImportError:
    tomllib = None

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_DIR = os.path.expanduser("~/.config/nc-ui")
THEME_PATH = os.path.join(CONFIG_DIR, "theme.toml")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.toml")

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
}

DEFAULT_THEME = {
    "typography": {"font_family": "Courier", "font_size": 11, "font_weight": "bold"},
    "geometry": {
        "compact_cols": 44,
        "expanded_cols": 96,
        "sidebar_nav_cols": 24,
        "popup_gap_top": 4,
        "popup_max_height": 900,
        "window_border_width": 1,
        "window_border_radius": 16,
        "panel_padding": 10,
        "card_spacing": 6,
        "screen_margin": 12,
    },
    "ascii": {
        "box_style": "rounded",
        "title_brackets": ["[ ", " ]"],
        "button_brackets": ["[", "]"],
        "slider_brackets": ["[", "]"],
        "slider_fill": "#",
        "slider_empty": "-",
        "sparkline_chars": "\u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588",
    },
    "colors": {
        "window_bg": "rgba(13, 13, 15, 0.88)",
        "window_border": "rgba(255, 255, 255, 0.12)",
        "background": "#09090b",
        # 1. Tono Grafite — INERZIA (frame, grid, none)
        "structure": "#43495c",
        "structure_bg": "#1a1c23",
        "inactive": "#5f657b",
        # 2. Tono Ardesia — METADATO (keys, headers, split)
        "schema": "#8c9bba",
        # 3. Tono Bianco — DATO (focus, values, titles)
        "focus": "#ffffff",
        "focus_val": "#e2eafc",
        # 4. Tono Ciano — UI (tabs, selection, user-input)
        "intent": "#00f0ff",
        "intent_soft": "#4db8c4",
        "intent_bg": "#002a3a",
        # 5. Tono Verde — HW/OS (up, active, done)
        "vitality": "#00ff73",
        "vitality_soft": "#4fac73",
        "vitality_bg": "#002813",
        # 6. Tono Giallo — MUTAZIONE (dirty, failover, warn)
        "attention": "#ffeb19",
        "attention_soft": "#c7b848",
        "attention_bg": "#332d00",
        # 7a. Tono Fucsia — INTERRUZIONE (stop, kill, down)
        "interrupt": "#ff2a9d",
        "interrupt_rest": "#bd5b86",
        "interrupt_bg": "#3a091d",
        # 7b. Tono Rosso — GUASTO (timeout, err, crit)
        "fault": "#ff213f",
        "fault_soft": "#c94f5f",
        "fault_bg": "#36050b",
        # Mapping globale legacy / core
        "primary": "#ffffff",
        "secondary": "#8c9bba",
        "muted": "#5f657b",
        "accent": "#00f0ff",
        "info": "#00f0ff",
        # Stati semantici cornici nc-boxes (Grafite a riposo, Bianco su Hover/Header)
        "box_inactive": "#43495c",
        "box_elected": "#43495c",
        "box_highlight": "#ffffff",
        "box_selected": "#ffffff",
        # Stile Command Line / Prompt / Password Bar
        "input_bg": "#0c0c0e",
        "input_fg": "#ffffff",
        "input_placeholder": "#5f657b",
        "input_border": "#43495c",
        "input_border_focus": "#00f0ff",
        "thresholds": {
            "normal": "#00ff73",
            "warning": "#ffeb19",
            "alert": "#ff2a9d",
            "critical": "#ff213f",
        },
    },
}

DEFAULT_CONFIG = {
    "general": {
        "language": "en",
        "log_file": "/tmp/nc-ui.log",
        "log_level": 3,
        "enable_hover_tooltips": False,
        "terminal": "auto",
    },
    "control_center": {
        "width_px": 960,
        "side": "right",
        "panel_style": "standard",
        "hide_unavailable_settings": False,
    },
    "capture": {
        "action": "clipboard_satty",
    },
    "waybar_widgets": {
        "window_width_pct": 45,
        "media_width_pct": 35,
        "lock_cmd": "",
    },
    "panels": {
        "refresh_ms": {
            "calendar":      0,
            "notifications": 0,
            "ram":           2000,
            "cpu":           2000,
            "network":       1500,
            "audio":         0,
            "capture":       0,
            "display":       2000,
            "clipboard":     0,
            "power":         3000,
            "ups":           3000,
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


def load_theme() -> dict:
    repo_theme = os.path.join(ROOT_DIR, "config", "ncui", "theme.toml")
    merged = _deep_merge(DEFAULT_THEME, _load_toml(repo_theme))
    merged = _deep_merge(merged, _load_toml(THEME_PATH))
    style_name = merged["ascii"].get("box_style", "rounded")
    merged["ascii"]["chars"] = BOX_STYLES.get(style_name, BOX_STYLES["rounded"])
    return merged


def load_config() -> dict:
    repo_cfg = os.path.join(ROOT_DIR, "config", "ncui", "config.toml")
    merged = _deep_merge(DEFAULT_CONFIG, _load_toml(repo_cfg))
    return _deep_merge(merged, _load_toml(CONFIG_PATH))


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


THEME = load_theme()
CONFIG = load_config()

VALID_CAPTURE_ACTIONS = ("clipboard", "satty", "save", "clipboard_satty")


def _update_toml_key_in_section(
    section: str, key: str, raw_toml_val: str, target_files: tuple = None, create_fallback: bool = True
) -> None:
    if target_files is None:
        target_files = (
            CONFIG_PATH,
            os.path.join(ROOT_DIR, "config", "ncui", "config.toml"),
            os.path.expanduser("~/Projects/ncui/config/ncui/config.toml"),
        )
    sec_hdr_re = re.compile(rf"^\[{re.escape(section)}\]\s*$", flags=re.MULTILINE)
    key_in_sec_re = re.compile(
        rf"(^\[{re.escape(section)}\][^\[]*?^\s*{re.escape(key)}\s*=\s*)[^\n#]+",
        flags=re.MULTILINE | re.DOTALL,
    )
    updated_any = False

    for rp in _unique_existing_paths(*target_files):
        try:
            with open(rp, "r", encoding="utf-8") as f:
                content = f.read()
            if sec_hdr_re.search(content):
                if key_in_sec_re.search(content):
                    new_c = key_in_sec_re.sub(f"\\g<1>{raw_toml_val}", content, count=1)
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


def get_cc_panel_style() -> str:
    from core.state import STATE
    st_val = STATE.get("control_center", "panel_style", None)
    if st_val is not None:
        return str(st_val).strip().lower()
    return str(CONFIG.get("control_center", {}).get("panel_style", "standard")).strip().lower()


def set_cc_panel_style(style: str) -> str:
    from core.state import STATE
    clean = str(style or "standard").strip().lower()
    if clean not in ("standard", "cyberpunk"):
        clean = "standard"
    STATE.set("control_center", "panel_style", clean)
    CONFIG.setdefault("control_center", {})["panel_style"] = clean
    _update_toml_key_in_section("control_center", "panel_style", f'"{clean}"')
    return clean


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
    w_cfg = CONFIG.get("waybar_widgets", {})
    win_pct = STATE.get("waybar_widgets", "window_width_pct", w_cfg.get("window_width_pct", 45))
    med_pct = STATE.get("waybar_widgets", "media_width_pct", w_cfg.get("media_width_pct", 35))
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

    STATE.set("waybar_widgets", "window_width_pct", win_i)
    STATE.set("waybar_widgets", "media_width_pct", med_i)
    CONFIG.setdefault("waybar_widgets", {})["window_width_pct"] = win_i
    CONFIG.setdefault("waybar_widgets", {})["media_width_pct"] = med_i
    _update_toml_key_in_section("waybar_widgets", "window_width_pct", str(win_i))
    _update_toml_key_in_section("waybar_widgets", "media_width_pct", str(med_i))
    return win_i, med_i


def get_screen_lock_cmd() -> str:
    from core.state import STATE
    st_val = STATE.get("waybar_widgets", "lock_cmd", None)
    if st_val is not None:
        return str(st_val).strip()
    return str(CONFIG.get("waybar_widgets", {}).get("lock_cmd", "")).strip()


def set_screen_lock_cmd(cmd_str: str) -> str:
    from core.state import STATE
    clean = str(cmd_str or "").strip()
    STATE.set("waybar_widgets", "lock_cmd", clean)
    CONFIG.setdefault("waybar_widgets", {})["lock_cmd"] = clean
    escaped = clean.replace("\\", "\\\\").replace('"', '\\"')
    _update_toml_key_in_section("waybar_widgets", "lock_cmd", f'"{escaped}"')
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
            "custom/netcycle", "custom/cpu", "custom/ram", "custom/notifications",
        ]

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
        "custom/notifications": 54,
        "custom/ups": 115,
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


def get_ui_transparency_pct() -> int:
    from core.state import STATE
    saved = STATE.get("appearance", "transparency_pct", None)
    if saved is not None:
        try:
            return max(0, min(95, int(saved)))
        except Exception:
            pass
    _, _, _, alpha = _parse_rgba_components(THEME["colors"].get("window_bg", "rgba(13, 13, 15, 0.88)"))
    return max(0, min(95, int(round((1.0 - alpha) * 100.0))))


def apply_waybar_transparency() -> bool:
    trans_pct = get_ui_transparency_pct()
    alpha = max(0.05, min(1.0, (100 - trans_pct) / 100.0))
    r, g, b, _ = _parse_rgba_components(THEME["colors"].get("window_bg", "rgba(13, 13, 15, 0.88)"))
    new_rgba = f"rgba({r}, {g}, {b}, {alpha:.2f})"

    css_paths = (
        os.path.expanduser("~/.config/waybar/style.css"),
        os.path.join(ROOT_DIR, "config", "waybar", "style.css"),
        os.path.expanduser("~/Projects/ncui/config/waybar/style.css"),
    )
    for rp in _unique_existing_paths(*css_paths):
        try:
            with open(rp, "r", encoding="utf-8") as f:
                css_content = f.read()

            def _repl_wb(m):
                block = m.group(0)
                return re.sub(
                    r"(background-color\s*:\s*)[^;]+(;)",
                    f"\\1{new_rgba}\\2",
                    block,
                    count=1,
                )

            new_css = re.sub(r"window#waybar\s*\{[^}]+\}", _repl_wb, css_content, count=1)
            with open(rp, "w", encoding="utf-8") as f:
                f.write(new_css)
        except Exception:
            pass

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


def set_ui_transparency_pct(transparency_pct: int, sync_waybar: bool = False) -> int:
    from core.state import STATE
    clamped = max(0, min(95, int(transparency_pct)))
    STATE.set("appearance", "transparency_pct", clamped)
    alpha = max(0.05, min(1.0, (100 - clamped) / 100.0))
    r, g, b, _ = _parse_rgba_components(THEME["colors"].get("window_bg", "rgba(13, 13, 15, 0.88)"))
    new_rgba = f"rgba({r}, {g}, {b}, {alpha:.2f})"
    THEME["colors"]["window_bg"] = new_rgba

    _update_toml_key_in_section(
        "colors",
        "window_bg",
        f'"{new_rgba}"',
        target_files=(
            THEME_PATH,
            os.path.join(ROOT_DIR, "config", "ncui", "theme.toml"),
            os.path.expanduser("~/Projects/ncui/config/ncui/theme.toml"),
        ),
        create_fallback=False,
    )

    if sync_waybar:
        apply_waybar_transparency()
    return clamped


def get_effective_window_bg(wb_look_bg: str = None) -> str:
    base_str = wb_look_bg or THEME["colors"].get("window_bg", "rgba(13, 13, 15, 0.88)")
    r, g, b, _ = _parse_rgba_components(base_str)
    trans_pct = get_ui_transparency_pct()
    alpha = max(0.05, min(1.0, (100 - trans_pct) / 100.0))
    return f"rgba({r}, {g}, {b}, {alpha:.2f})"


def color_for_pct(pct: float) -> str:
    t = THEME["colors"]["thresholds"]
    if pct < 55.0:
        return t["normal"]
    if pct < 80.0:
        return t["warning"]
    if pct < 92.0:
        return t["alert"]
    return t["critical"]
