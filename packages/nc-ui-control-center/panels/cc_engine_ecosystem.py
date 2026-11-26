#!/usr/bin/env python3
"""NC-UI Ecosystem Integration Engine — Niri Compositor & Ghostty Terminal."""

from __future__ import annotations

import copy
import os
import re
import shutil
import subprocess
import tomllib

from core.config import THEME, get_config_dir, get_effective_window_radius, get_layout_margins, get_surface_bg_hex, get_ui_transparency_pct
from core.i18n import t
from core.state import STATE
from ui.kit import get_semantic_palette, set_feedback

NIRI_RING_STYLES = ("solid", "gradient", "off")
NIRI_RING_WIDTHS = (1, 2, 3, 4)
NIRI_CORNER_OPTIONS = ("sync", "0", "8", "12", "16")

GHOSTTY_KEYBIND_PROFILES = ("powershell", "standard")
GHOSTTY_OPACITY_OPTIONS = ("sync", "70", "80", "90", "100")
GHOSTTY_CURSOR_STYLES = ("bar", "block", "underline")

_DEFAULT_ECOSYSTEM_CFG: dict = {
    "niri": {
        "auto_sync": True,
        "focus_ring_style": "solid",
        "focus_ring_width": 2,
        "corner_radius": "sync",
        "sync_tab_indicator": True,
        "sync_insert_hint": True,
        "sync_backdrop": True,
    },
    "ghostty": {
        "auto_sync": True,
        "keybind_profile": "powershell",
        "opacity_mode": "sync",
        "cursor_style": "bar",
        "cursor_blink": True,
        "copy_on_select": True,
        "borderless": True,
        "hide_mouse": True,
    },
}


def ecosystem_toml_path(cfg_dir: str | None = None) -> str:
    return os.path.join(cfg_dir or get_config_dir(), "ecosystem.toml")


def niri_theme_kdl_path(cfg_dir: str | None = None) -> str:
    return os.path.join(cfg_dir or get_config_dir(), "niri-theme.kdl")


def niri_main_config_path() -> str:
    return os.path.expanduser("~/.config/niri/config.kdl")


def ghostty_theme_conf_path(cfg_dir: str | None = None) -> str:
    return os.path.join(cfg_dir or get_config_dir(), "ghostty-theme.conf")


def ghostty_main_config_path() -> str:
    return os.path.expanduser("~/.config/ghostty/config")


def _norm_niri(raw: dict | None) -> dict:
    d = raw if isinstance(raw, dict) else {}
    out = copy.deepcopy(_DEFAULT_ECOSYSTEM_CFG["niri"])
    style = str(d.get("focus_ring_style", out["focus_ring_style"])).strip().lower()
    if style in NIRI_RING_STYLES:
        out["focus_ring_style"] = style
    try:
        w = int(d.get("focus_ring_width", out["focus_ring_width"]))
        out["focus_ring_width"] = w if w in NIRI_RING_WIDTHS else max(1, min(8, w))
    except (TypeError, ValueError):
        pass
    cr = str(d.get("corner_radius", out["corner_radius"])).strip().lower()
    if cr in NIRI_CORNER_OPTIONS:
        out["corner_radius"] = cr
    for bk in ("auto_sync", "sync_tab_indicator", "sync_insert_hint", "sync_backdrop"):
        if bk in d:
            out[bk] = bool(d[bk])
    return out


def _norm_ghostty(raw: dict | None) -> dict:
    d = raw if isinstance(raw, dict) else {}
    out = copy.deepcopy(_DEFAULT_ECOSYSTEM_CFG["ghostty"])
    kp = str(d.get("keybind_profile", out["keybind_profile"])).strip().lower()
    if kp in GHOSTTY_KEYBIND_PROFILES:
        out["keybind_profile"] = kp
    om = str(d.get("opacity_mode", out["opacity_mode"])).strip().lower()
    if om in GHOSTTY_OPACITY_OPTIONS:
        out["opacity_mode"] = om
    cs = str(d.get("cursor_style", out["cursor_style"])).strip().lower()
    if cs in GHOSTTY_CURSOR_STYLES:
        out["cursor_style"] = cs
    for bk in ("auto_sync", "cursor_blink", "copy_on_select", "borderless", "hide_mouse"):
        if bk in d:
            out[bk] = bool(d[bk])
    return out


def _norm_cfg(raw: dict | None) -> dict:
    d = raw if isinstance(raw, dict) else {}
    return {
        "niri": _norm_niri(d.get("niri")),
        "ghostty": _norm_ghostty(d.get("ghostty")),
    }


def load_saved_ecosystem_config(cfg_dir: str | None = None) -> dict:
    path = ecosystem_toml_path(cfg_dir)
    if os.path.isfile(path):
        try:
            with open(path, "rb") as f:
                return _norm_cfg(tomllib.load(f))
        except Exception:
            pass
    return copy.deepcopy(_DEFAULT_ECOSYSTEM_CFG)


def _format_toml_section(title: str, data: dict) -> list[str]:
    lines = [f"[{title}]"]
    for k, v in data.items():
        if isinstance(v, bool):
            lines.append(f"{k} = {'true' if v else 'false'}")
        elif isinstance(v, int):
            lines.append(f"{k} = {v}")
        else:
            lines.append(f'{k} = "{v}"')
    lines.append("")
    return lines


def save_ecosystem_config_toml(cfg: dict, cfg_dir: str | None = None) -> str:
    norm = _norm_cfg(cfg)
    path = ecosystem_toml_path(cfg_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lines = ["# Managed by NC-UI Personalization -> WM & Terminal", ""]
    lines.extend(_format_toml_section("niri", norm["niri"]))
    lines.extend(_format_toml_section("ghostty", norm["ghostty"]))
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines).rstrip() + "\n")
    os.replace(tmp, path)
    return path


def get_editor_ecosystem_config() -> dict:
    st = STATE.get("ecosystem", "draft_cfg", None)
    if isinstance(st, dict):
        return _norm_cfg(st)
    saved = load_saved_ecosystem_config()
    STATE.set("ecosystem", "draft_cfg", copy.deepcopy(saved), immediate=False)
    return saved


def _set_editor_ecosystem_config(cfg: dict) -> None:
    STATE.set("ecosystem", "draft_cfg", _norm_cfg(cfg), immediate=False)


def is_niri_dirty() -> bool:
    return get_editor_ecosystem_config()["niri"] != load_saved_ecosystem_config()["niri"]


def is_ghostty_dirty() -> bool:
    return get_editor_ecosystem_config()["ghostty"] != load_saved_ecosystem_config()["ghostty"]


def _resolve_niri_include_target(cfg_dir: str | None = None) -> str:
    return niri_theme_kdl_path(cfg_dir)


def _resolve_ghostty_include_target(cfg_dir: str | None = None) -> str:
    return ghostty_theme_conf_path(cfg_dir)


def is_niri_applied(cfg_dir: str | None = None, main_path: str | None = None) -> bool:
    inc_path = _resolve_niri_include_target(cfg_dir)
    if not os.path.isfile(inc_path):
        return False
    mpath = main_path or niri_main_config_path()
    if not os.path.isfile(mpath):
        return False
    try:
        with open(mpath, "r", encoding="utf-8") as f:
            txt = f.read()
        return os.path.basename(inc_path) in txt or inc_path in txt
    except OSError:
        return False


def is_ghostty_applied(cfg_dir: str | None = None, main_path: str | None = None) -> bool:
    inc_path = _resolve_ghostty_include_target(cfg_dir)
    if not os.path.isfile(inc_path):
        return False
    mpath = main_path or ghostty_main_config_path()
    if not os.path.isfile(mpath):
        return False
    try:
        with open(mpath, "r", encoding="utf-8") as f:
            txt = f.read()
        return os.path.basename(inc_path) in txt or inc_path in txt
    except OSError:
        return False


def _sanitize_hex(col: str, fallback: str) -> str:
    s = str(col or "").strip()
    if re.fullmatch(r"#[0-9a-fA-F]{6}", s):
        return s.lower()
    return fallback


def render_niri_theme_kdl(niri_cfg: dict) -> str:
    n = _norm_niri(niri_cfg)
    pal = get_semantic_palette()
    intent = _sanitize_hex(pal.get("intent"), "#00f0ff")
    struct = _sanitize_hex(pal.get("structure"), "#3e465c")
    fault = _sanitize_hex(pal.get("fault"), "#ff2442")
    focus = _sanitize_hex(pal.get("focus"), "#ffffff")
    surf = _sanitize_hex(get_surface_bg_hex(), "#0d0f14")

    if n["corner_radius"] == "sync":
        rad = get_effective_window_radius()
    else:
        rad = int(n["corner_radius"])

    _, outer_mar = get_layout_margins()
    lines = [
        "// Auto-generated by NC-UI Personalization -> WM & Terminal. Do not edit manually.",
        "layout {",
        f"    gaps {outer_mar}",
    ]
    if n["focus_ring_style"] == "off":
        lines.extend([
            "    focus-ring {",
            "        off",
            "    }",
        ])
    else:
        lines.extend([
            "    focus-ring {",
            f"        width {n['focus_ring_width']}",
        ])
        if n["focus_ring_style"] == "gradient":
            lines.append(f'        active-gradient from="{intent}" to="{focus}" angle=45')
        else:
            lines.append(f'        active-color "{intent}"')
        lines.extend([
            f'        inactive-color "{struct}"',
            f'        urgent-color "{fault}"',
            "    }",
        ])

    if n["sync_tab_indicator"]:
        lines.extend([
            "    tab-indicator {",
            f'        active-color "{intent}"',
            f'        inactive-color "{struct}"',
            f'        urgent-color "{fault}"',
            "    }",
        ])
    if n["sync_insert_hint"]:
        lines.extend([
            "    insert-hint {",
            f'        color "{intent}40"',
            "    }",
        ])
    lines.append("}")

    lines.extend([
        "window-rule {",
        f"    geometry-corner-radius {rad}",
        "    clip-to-geometry true",
        "}",
    ])

    if n["sync_backdrop"]:
        lines.extend([
            "overview {",
            f'    backdrop-color "{surf}"',
            "}",
        ])

    return "\n".join(lines) + "\n"


def render_ghostty_theme_conf(gh_cfg: dict) -> str:
    g = _norm_ghostty(gh_cfg)
    pal = get_semantic_palette()
    surf = _sanitize_hex(get_surface_bg_hex(), "#0d0f14")
    struct = _sanitize_hex(pal.get("structure"), "#3e465c")
    schema = _sanitize_hex(pal.get("schema"), "#8b9bb8")
    focus = _sanitize_hex(pal.get("focus"), "#ffffff")
    focus_val = _sanitize_hex(pal.get("focus_val"), "#dce6f7")
    intent = _sanitize_hex(pal.get("intent"), "#00f0ff")
    intent_bg = _sanitize_hex(pal.get("intent_bg"), "#002a3a")
    vitality = _sanitize_hex(pal.get("vitality"), "#00f57a")
    attention = _sanitize_hex(pal.get("attention"), "#ffe619")
    interrupt = _sanitize_hex(pal.get("interrupt"), "#ff2a9d")
    fault = _sanitize_hex(pal.get("fault"), "#ff2442")

    if g["opacity_mode"] == "sync":
        try:
            op = max(0.40, min(1.00, round(get_ui_transparency_pct() / 100.0, 2)))
        except Exception:
            op = 0.86
    else:
        op = round(int(g["opacity_mode"]) / 100.0, 2)

    lines = [
        "# Auto-generated by NC-UI Personalization -> WM & Terminal. Do not edit manually.",
        f"background = {surf}",
        f"foreground = {focus_val}",
        f"background-opacity = {op:.2f}",
        f"cursor-color = {intent}",
        f"cursor-text = {surf}",
        f"cursor-style = {g['cursor_style']}",
        f"cursor-style-blink = {'true' if g['cursor_blink'] else 'false'}",
        f"selection-background = {intent_bg}",
        f"selection-foreground = {focus}",
        f"palette = 0={struct}",
        f"palette = 1={fault}",
        f"palette = 2={vitality}",
        f"palette = 3={attention}",
        f"palette = 4={intent}",
        f"palette = 5={interrupt}",
        f"palette = 6={schema}",
        f"palette = 7={focus_val}",
        f"palette = 8={struct}",
        f"palette = 9={fault}",
        f"palette = 10={vitality}",
        f"palette = 11={attention}",
        f"palette = 12={intent}",
        f"palette = 13={interrupt}",
        f"palette = 14={intent}",
        f"palette = 15={focus}",
        "window-padding-x = 10",
        "window-padding-y = 8",
        f"window-decoration = {'none' if g['borderless'] else 'auto'}",
        f"gtk-titlebar = {'false' if g['borderless'] else 'true'}",
        "confirm-close-surface = false",
        f"copy-on-select = {'clipboard' if g['copy_on_select'] else 'false'}",
        f"mouse-hide-while-typing = {'true' if g['hide_mouse'] else 'false'}",
        "scrollback-limit = 25000000",
    ]

    if g["keybind_profile"] == "powershell":
        lines.extend([
            "keybind = performable:ctrl+c=copy_to_clipboard",
            "keybind = ctrl+v=paste_from_clipboard",
            "keybind = ctrl+shift+c=copy_to_clipboard",
            "keybind = ctrl+shift+v=paste_from_clipboard",
            "keybind = ctrl+t=new_tab",
            "keybind = ctrl+w=close_surface",
            "keybind = ctrl+n=new_window",
            "keybind = ctrl+tab=next_tab",
            "keybind = ctrl+shift+tab=previous_tab",
            "keybind = ctrl+page_up=previous_tab",
            "keybind = ctrl+page_down=next_tab",
            "keybind = ctrl+f=start_search",
            "keybind = ctrl+shift+a=select_all",
            "keybind = ctrl+equal=increase_font_size:1",
            "keybind = ctrl+minus=decrease_font_size:1",
            "keybind = ctrl+zero=reset_font_size",
            r"keybind = ctrl+backspace=text:\x17",
            "keybind = ctrl+delete=esc:d",
            "keybind = ctrl+left=esc:b",
            "keybind = ctrl+right=esc:f",
            "keybind = ctrl+shift+d=new_split:right",
            "keybind = ctrl+shift+e=new_split:down",
        ])

    return "\n".join(lines) + "\n"


def _write_atomic(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp, path)


def apply_niri_config(
    niri_cfg: dict,
    *,
    cfg_dir: str | None = None,
    main_path: str | None = None,
) -> tuple[bool, str]:
    n = _norm_niri(niri_cfg)
    inc_path = niri_theme_kdl_path(cfg_dir)
    mpath = main_path or niri_main_config_path()
    kdl_text = render_niri_theme_kdl(n)

    os.makedirs(os.path.dirname(inc_path), exist_ok=True)
    tmp_inc = f"{inc_path}.tmp.{os.getpid()}"
    tmp_main = f"{mpath}.validate.{os.getpid()}"
    has_niri = shutil.which("niri") is not None
    inc_line = f'include "{inc_path}"'
    try:
        with open(tmp_inc, "w", encoding="utf-8") as f:
            f.write(kdl_text)

        main_txt = ""
        if os.path.isfile(mpath):
            with open(mpath, "r", encoding="utf-8") as f:
                main_txt = f.read()
        needs_inc = os.path.basename(inc_path) not in main_txt and inc_path not in main_txt
        cand_main = main_txt
        if needs_inc:
            cand_main = (cand_main.rstrip("\n") + "\n\n" + inc_line + "\n").lstrip("\n")

        if has_niri:
            os.makedirs(os.path.dirname(tmp_main), exist_ok=True)
            val_main = cand_main.replace(inc_line, f'include "{tmp_inc}"') if inc_line in cand_main else (
                cand_main.rstrip("\n") + f'\n\ninclude "{tmp_inc}"\n'
            )
            with open(tmp_main, "w", encoding="utf-8") as f:
                f.write(val_main)
            res = subprocess.run(["niri", "validate", "-c", tmp_main], capture_output=True, text=True, timeout=4.0)
            if res.returncode != 0:
                err = (res.stderr or res.stdout or "niri validate error").strip().splitlines()[-1]
                return False, err

        os.replace(tmp_inc, inc_path)
        if needs_inc:
            _write_atomic(mpath, cand_main)
    except Exception as e:
        return False, str(e)
    finally:
        for p in (tmp_inc, tmp_main):
            if os.path.exists(p):
                try:
                    os.unlink(p)
                except OSError:
                    pass
    return True, os.path.basename(inc_path)


def _reload_ghostty_live() -> None:
    if shutil.which("pkill"):
        try:
            subprocess.run(["pkill", "-USR2", "ghostty"], capture_output=True, timeout=2.0)
        except Exception:
            pass


def apply_ghostty_config(
    gh_cfg: dict,
    *,
    cfg_dir: str | None = None,
    main_path: str | None = None,
) -> tuple[bool, str]:
    g = _norm_ghostty(gh_cfg)
    inc_path = ghostty_theme_conf_path(cfg_dir)
    mpath = main_path or ghostty_main_config_path()
    conf_text = render_ghostty_theme_conf(g)

    main_txt = ""
    if os.path.isfile(mpath):
        with open(mpath, "r", encoding="utf-8") as f:
            main_txt = f.read()
    has_inc = (os.path.basename(inc_path) in main_txt or inc_path in main_txt)
    if os.path.isfile(inc_path) and has_inc:
        try:
            with open(inc_path, "r", encoding="utf-8") as f:
                if f.read() == conf_text:
                    return True, os.path.basename(inc_path)
        except OSError:
            pass

    os.makedirs(os.path.dirname(inc_path), exist_ok=True)
    tmp_inc = f"{inc_path}.tmp.{os.getpid()}"
    try:
        with open(tmp_inc, "w", encoding="utf-8") as f:
            f.write(conf_text)
        if shutil.which("ghostty"):
            res = subprocess.run(
                ["ghostty", "+validate-config", f"--config-file={tmp_inc}"],
                capture_output=True,
                text=True,
                timeout=4.0,
            )
            if res.returncode != 0:
                err = (res.stderr or res.stdout or "ghostty validate error").strip().splitlines()[-1]
                return False, err

        os.replace(tmp_inc, inc_path)
        if not has_inc:
            inc_line = f"config-file = ?{inc_path}"
            new_main = (main_txt.rstrip("\n") + "\n\n" + inc_line + "\n").lstrip("\n")
            _write_atomic(mpath, new_main)
        _reload_ghostty_live()
    except Exception as e:
        return False, str(e)
    finally:
        if os.path.exists(tmp_inc):
            try:
                os.unlink(tmp_inc)
            except OSError:
                pass
    return True, os.path.basename(inc_path)


def sync_ecosystem_themes_if_enabled(force_niri: bool = False) -> None:
    saved = load_saved_ecosystem_config()
    if (force_niri or saved["niri"].get("auto_sync")) and is_niri_applied():
        kdl_path = niri_theme_kdl_path()
        try:
            _write_atomic(kdl_path, render_niri_theme_kdl(saved["niri"]))
        except Exception:
            pass
    if saved["ghostty"].get("auto_sync") and is_ghostty_applied():
        conf_path = ghostty_theme_conf_path()
        try:
            new_conf = render_ghostty_theme_conf(saved["ghostty"])
            old_conf = ""
            if os.path.isfile(conf_path):
                with open(conf_path, "r", encoding="utf-8") as f:
                    old_conf = f.read()
            if new_conf != old_conf:
                _write_atomic(conf_path, new_conf)
                _reload_ghostty_live()
        except Exception:
            pass


def handle_cc_ecosystem_link(cc_panel, uri: str) -> bool:
    if not uri.startswith("cc_eco:"):
        return False
    parts = uri.split(":")
    if len(parts) < 3:
        return False
    sec, act = parts[1], parts[2]
    val = ":".join(parts[3:]) if len(parts) > 3 else ""

    draft = get_editor_ecosystem_config()
    saved = load_saved_ecosystem_config()

    if sec == "niri":
        sub = draft["niri"]
        if act == "auto_sync":
            sub["auto_sync"] = (val == "1")
        elif act == "ring_style" and val in NIRI_RING_STYLES:
            sub["focus_ring_style"] = val
        elif act == "ring_width":
            try:
                w = int(val)
                if w in NIRI_RING_WIDTHS:
                    sub["focus_ring_width"] = w
            except ValueError:
                return True
        elif act == "corner" and val in NIRI_CORNER_OPTIONS:
            sub["corner_radius"] = val
        elif act == "toggle" and val in ("sync_tab_indicator", "sync_insert_hint", "sync_backdrop"):
            sub[val] = not bool(sub.get(val, False))
        elif act == "reset":
            draft["niri"] = copy.deepcopy(saved["niri"])
            set_feedback("ecosystem", True, t("cc_ecosystem.fb_niri_reset"))
        elif act == "apply":
            ok, info = apply_niri_config(sub)
            if ok:
                new_saved = copy.deepcopy(saved)
                new_saved["niri"] = copy.deepcopy(sub)
                save_ecosystem_config_toml(new_saved)
                draft["niri"] = copy.deepcopy(new_saved["niri"])
                set_feedback("ecosystem", True, t("cc_ecosystem.fb_niri_applied", p0=info))
            else:
                set_feedback("ecosystem", False, t("cc_ecosystem.fb_niri_err", p0=info))
        else:
            return False
    elif sec == "ghostty":
        sub = draft["ghostty"]
        if act == "auto_sync":
            sub["auto_sync"] = (val == "1")
        elif act == "keybind" and val in GHOSTTY_KEYBIND_PROFILES:
            sub["keybind_profile"] = val
        elif act == "opacity" and val in GHOSTTY_OPACITY_OPTIONS:
            sub["opacity_mode"] = val
        elif act == "cursor" and val in GHOSTTY_CURSOR_STYLES:
            sub["cursor_style"] = val
        elif act == "toggle" and val in ("cursor_blink", "copy_on_select", "borderless", "hide_mouse"):
            sub[val] = not bool(sub.get(val, False))
        elif act == "reset":
            draft["ghostty"] = copy.deepcopy(saved["ghostty"])
            set_feedback("ecosystem", True, t("cc_ecosystem.fb_ghostty_reset"))
        elif act == "apply":
            ok, info = apply_ghostty_config(sub)
            if ok:
                new_saved = copy.deepcopy(saved)
                new_saved["ghostty"] = copy.deepcopy(sub)
                save_ecosystem_config_toml(new_saved)
                draft["ghostty"] = copy.deepcopy(new_saved["ghostty"])
                set_feedback("ecosystem", True, t("cc_ecosystem.fb_ghostty_applied", p0=info))
            else:
                set_feedback("ecosystem", False, t("cc_ecosystem.fb_ghostty_err", p0=info))
        else:
            return False
    else:
        return False

    _set_editor_ecosystem_config(draft)
    if cc_panel and getattr(cc_panel, "shell", None):
        cc_panel.shell.refresh_view()
    return True
