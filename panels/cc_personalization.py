#!/usr/bin/env python3
import copy
import json
import os
import re
import time
from core import probe
from core.config import (
    BOX_STYLES,
    CONFIG_DIR,
    DEFAULT_THEME,
    ROOT_DIR,
    THEME,
    THEME_PATH,
    _deep_merge,
    _load_toml,
    _unique_existing_paths,
    _update_toml_key_in_section,
    apply_waybar_transparency,
    get_cc_panel_style,
    get_ui_transparency_pct,
    set_ui_transparency_pct,
)
from core.i18n import get_active_language
from core.state import STATE
from panels.base import CONTROL_CENTER_SECTIONS
from panels.cc_waybar import (
    _build_waybar_options_tab,
    build_cc_waybar_cards,
    handle_cc_waybar_link,
    handle_cc_waybar_prompt,
)
from panels.display import run_desktop_cli
from ui.canvas import Canvas
from ui.kit import ActionBtn, Badge, Box, Divider, SelectPill, Slider, StateBadge, Txt

NC_DESKTOP_BASE = os.path.expanduser("~/.local/nc-desktop")
WALL_DIR = os.path.join(NC_DESKTOP_BASE, "wallpapers")
PROF_DIR = os.path.join(NC_DESKTOP_BASE, "profiles")
THEMES_DIR = os.path.join(CONFIG_DIR, "themes")
NIRI_CONFIG_FILE = os.path.expanduser("~/.config/niri/config.kdl")


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
    b = Box("Theme & Personalization Status", icon="\uf00c" if fb["ok"] else "\uf071")
    b.add_row(" ")
    b.add_row(f"  {Txt(' ' + fb['msg'] + ' ', role='vitality' if fb['ok'] else 'fault', bg=True)}")
    b.add_row(" ")
    return [b]


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
        return True, f"Saved theme to {os.path.basename(path)}"
    except Exception as e:
        return False, str(e)


def _read_cursor_cfg() -> tuple[str, int]:
    theme_n = str(STATE.get("personalization", "cursor_theme", "") or "")
    size_v = int(STATE.get("personalization", "cursor_size", 24) or 24)
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
    if not theme_n and probe.has_bin("gsettings"):
        ok, out = probe.run_cmd(["gsettings", "get", "org.gnome.desktop.interface", "cursor-theme"], timeout=0.8)
        if ok and out:
            theme_n = out.strip().strip("'")
    return theme_n or "Adwaita", size_v


def _apply_cursor_cfg(theme_name: str, size_val: int) -> tuple[bool, str]:
    STATE.set("personalization", "cursor_theme", theme_name)
    STATE.set("personalization", "cursor_size", int(size_val))
    if probe.has_bin("gsettings"):
        probe.run_cmd(["gsettings", "set", "org.gnome.desktop.interface", "cursor-theme", theme_name], timeout=1.0)
        probe.run_cmd(["gsettings", "set", "org.gnome.desktop.interface", "cursor-size", str(int(size_val))], timeout=1.0)
    if os.path.exists(NIRI_CONFIG_FILE):
        try:
            txt = open(NIRI_CONFIG_FILE, "r", encoding="utf-8").read()
            if re.search(r"cursor\s*\{", txt):
                txt = re.sub(r'xcursor-theme\s+"[^"]+"', f'xcursor-theme "{theme_name}"', txt)
                txt = re.sub(r"xcursor-size\s+\d+", f"xcursor-size {int(size_val)}", txt)
            else:
                txt = txt.rstrip() + f'\n\ncursor {{\n    xcursor-theme "{theme_name}"\n    xcursor-size {int(size_val)}\n}}\n'
            open(NIRI_CONFIG_FILE, "w", encoding="utf-8").write(txt)
        except Exception as e:
            return False, str(e)
    return True, f"Cursor set: {theme_name} ({size_val}px)"


def _get_niri_outputs() -> dict:
    ok, out = probe.run_cmd(["niri", "msg", "-j", "outputs"], timeout=1.2)
    if ok and out:
        try:
            return json.loads(out)
        except Exception:
            pass
    return {}


# =====================================================================
# SUBTAB 1: THEME, COLORS, FONTS, ICONS & CURSOR
# =====================================================================

def _build_theme_standard(cc_panel, canvas: Canvas, is_it: bool) -> list:
    cards = _feedback_cards()
    trans_pct = get_ui_transparency_pct()
    alpha_val = (100 - trans_pct) / 100.0
    box_style = str(THEME["ascii"].get("box_style", "rounded"))
    colors = THEME["colors"]
    th = colors.get("thresholds", {})
    typo = THEME["typography"]
    cur_c_theme, cur_c_size = _read_cursor_cfg()

    tr_box = Box(
        "Trasparenza Globale UI & Bordi ASCII" if is_it else "Global UI Transparency & ASCII Frame Style",
        icon="\uf042",
        actions=[
            Badge(f"alpha={alpha_val:.2f}", style="info"),
            ActionBtn("\uf00c Apply Waybar", "cc:trans_apply", tone="apply", active=True),
        ],
        scroll_action="cc:trans_scroll",
        elected=True,
    )
    tr_box.add_row(" ")
    tr_sl = Slider(trans_pct, "cc:trans", min_val=0, max_val=95, width=26, show_pct=True, step_buttons=True, step=5)
    tr_box.add_kv("Transparency" if not is_it else "Trasparenza", tr_sl)
    p_btns = " ".join(SelectPill(f"{p}%", f"cc:trans:{p}", selected=(abs(trans_pct - p) <= 2)) for p in (0, 15, 22, 35, 50, 75))
    tr_box.add_kv("Quick Presets" if not is_it else "Preset Rapidi", p_btns)

    bs_btns = " ".join(
        SelectPill(s.title(), f"cc_pers:box_style:{s}", selected=(box_style == s))
        for s in ("rounded", "sharp", "heavy", "double")
    )
    tr_box.add_kv("Box Border Style" if not is_it else "Stile Bordi Box", bs_btns)
    tr_box.add_row(" ")
    cards.append(tr_box)

    pal_box = Box(
        "Palette Semantica, Soglie & Stati Box (theme.toml)" if is_it else "Semantic Palette, Thresholds & Box States (theme.toml)",
        icon="\uf53f",
        actions=[ActionBtn("Reset Theme", "cc_pers:reset_theme", tone="warn")],
    )
    pal_box.add_row(" ")
    acc_cur = str(colors.get("intent", colors.get("accent", ""))).lower()
    acc_presets = [("Cyan", "#00f0ff"), ("Matrix", "#00ff73"), ("Amber", "#ffeb19"), ("Magenta", "#ff2a9d"), ("Lavender", "#b4befe")]
    acc_btns = " ".join(SelectPill(lbl, f"cc_pers:set_color:accent:{hx}", selected=(acc_cur == hx)) for lbl, hx in acc_presets)
    pal_box.add_kv("Accent / Intent", f"{acc_btns} {ActionBtn('Hex..', 'cc_pers:prompt_color:accent', tone='neutral')}")

    pal_box.add_kv("Threshold Colors", f"{Txt('Normal: ' + str(th.get('normal')), role='vitality')}  {Txt('Warn: ' + str(th.get('warning')), role='attention')}  {Txt('Alert: ' + str(th.get('alert')), role='interrupt')}  {Txt('Crit: ' + str(th.get('critical')), role='fault')}")
    pal_box.add_kv(
        "Edit Thresholds",
        f"{ActionBtn('Normal..', 'cc_pers:prompt_th:normal', tone='apply')} "
        f"{ActionBtn('Warn..', 'cc_pers:prompt_th:warning', tone='warn')} "
        f"{ActionBtn('Alert..', 'cc_pers:prompt_th:alert', tone='danger_soft')} "
        f"{ActionBtn('Crit..', 'cc_pers:prompt_th:critical', tone='danger_soft')}",
    )
    pal_box.add_kv(
        "Box State Colors",
        f"{ActionBtn('Selected..', 'cc_pers:prompt_color:box_selected', tone='select')} "
        f"{ActionBtn('Elected..', 'cc_pers:prompt_color:box_elected', tone='apply')} "
        f"{ActionBtn('Highlight..', 'cc_pers:prompt_color:box_highlight', tone='neutral')}",
    )

    def _build_typo_cursor(b: Box):
        fonts = ["Courier", "JetBrainsMono Nerd Font", "FiraCode Nerd Font", "Iosevka", "Hack", "monospace"]
        f_btns = " ".join(SelectPill(f.split()[0], f"cc_pers:font:{f}", selected=(typo.get("font_family") == f)) for f in fonts[:4])
        b.add_kv("Monospace Font", f"{f_btns} {ActionBtn('Custom..', 'cc_pers:prompt_font', tone='neutral')}")
        fs_btns = " ".join(SelectPill(f"{sz}pt", f"cc_pers:font_size:{sz}", selected=(int(typo.get("font_size", 11)) == sz)) for sz in (9, 10, 11, 12, 13, 14))
        b.add_kv("Font Size", fs_btns)
        sz_btns = " ".join(SelectPill(f"{cs}px", f"cc_pers:cursor_size:{cs}", selected=(cur_c_size == cs)) for cs in (16, 24, 32, 48))
        b.add_kv("Cursor Theme", f"{Txt(cur_c_theme, role='focus')}  {ActionBtn('Edit..', 'cc_pers:prompt_cursor', tone='neutral')}  {sz_btns}")

    pal_box.add_collapsible(cc_panel, "pers_typo", "Tipografia Monospace, Cursore & Icone" if is_it else "Monospace Typography, Cursor & System Icons", _build_typo_cursor)
    pal_box.add_row(" ")
    cards.append(pal_box)
    return cards


def _build_theme_cyberpunk(cc_panel, canvas: Canvas, is_it: bool) -> list:
    cards = _feedback_cards()
    trans_pct = get_ui_transparency_pct()
    box_style = str(THEME["ascii"].get("box_style", "rounded"))
    typo = THEME["typography"]

    b = Box(
        "CRT PHOSPHOR & CHROMATIC CALIBRATION CONSOLE",
        icon="\uf53f",
        actions=[ActionBtn("\uf00c SYNC WAYBAR", "cc:trans_apply", tone="apply", active=True)],
        scroll_action="cc:trans_scroll",
        elected=True,
    )
    b.add_row(" ")
    tr_sl = Slider(trans_pct, "cc:trans", min_val=0, max_val=95, width=20, show_pct=True, step_buttons=True, step=5)
    b.add_row(f"  {Txt('[ALPHA DAC]──►', role='intent')} {tr_sl}")
    b.add_row(f"  {Txt('[BOX STYLE]──►', role='schema')} " + " ".join(SelectPill(s.upper(), f"cc_pers:box_style:{s}", selected=(box_style == s)) for s in ("rounded", "sharp", "heavy", "double")))
    b.add_section("SEMANTIC COLOR PHOSPHOR GUNS")
    acc_presets = [("CYAN", "#00f0ff"), ("MATRIX", "#00ff73"), ("AMBER", "#ffeb19"), ("MAGENTA", "#ff2a9d"), ("ICE", "#89dceb")]
    b.add_row(f"  {Txt('[INTENT] ───►', role='schema')} " + " ".join(SelectPill(lbl, f"cc_pers:set_color:accent:{hx}", selected=(THEME['colors'].get('intent', '').lower() == hx)) for lbl, hx in acc_presets) + f" {ActionBtn('HEX..', 'cc_pers:prompt_color:accent', tone='neutral')}")
    b.add_row(f"  {Txt('[BOX RINGS]─►', role='schema')} {ActionBtn('SELECTED..', 'cc_pers:prompt_color:box_selected', tone='select')} {ActionBtn('ELECTED..', 'cc_pers:prompt_color:box_elected', tone='apply')} {ActionBtn('HIGHLIGHT..', 'cc_pers:prompt_color:box_highlight', tone='neutral')}")
    b.add_row(f"  {Txt('[THRESHOLDS]►', role='schema')} {ActionBtn('NORM..', 'cc_pers:prompt_th:normal', tone='apply')} {ActionBtn('WARN..', 'cc_pers:prompt_th:warning', tone='warn')} {ActionBtn('ALERT..', 'cc_pers:prompt_th:alert', tone='danger_soft')} {ActionBtn('CRIT..', 'cc_pers:prompt_th:critical', tone='danger_soft')}")
    b.add_section("GLYPH RASTERIZER & XCURSOR")
    b.add_row(
        f"  {Txt('[FONT]', role='schema')} {Txt(typo.get('font_family', 'Courier') + ' ' + str(typo.get('font_size', 11)) + 'pt', role='focus')}  "
        + " ".join(SelectPill(f"{sz}", f"cc_pers:font_size:{sz}", selected=(int(typo.get("font_size", 11)) == sz)) for sz in (10, 11, 12, 13))
        + f"  {ActionBtn('FONT..', 'cc_pers:prompt_font', tone='neutral')}  {ActionBtn('CURSOR..', 'cc_pers:prompt_cursor', tone='neutral')}"
    )
    b.add_row(" ")
    cards.append(b)
    return cards


# =====================================================================
# SUBTAB 2: THEME CREATOR TOOL (standard + cyberpunk)
# =====================================================================

def _list_saved_themes() -> list[str]:
    if not os.path.exists(THEMES_DIR):
        return []
    return sorted([f[:-5] for f in os.listdir(THEMES_DIR) if f.endswith(".toml")])


def _build_creator_standard(cc_panel, canvas: Canvas, is_it: bool) -> list:
    cards = _feedback_cards()
    asc = THEME["ascii"]
    geom = THEME["geometry"]
    saved = _list_saved_themes()

    c_box = Box(
        "Theme Creator Tool — Elementi Grafici ASCII (.toml)" if is_it else "Theme Creator Tool — ASCII UI Primitives (.toml)",
        icon="\uf1fc",
        actions=[
            ActionBtn("+ Save Theme..", "cc_pers:save_theme_prompt", tone="apply", active=True),
        ],
        elected=True,
    )
    c_box.add_row(" ")
    tb = asc.get("title_brackets", ["[ ", " ]"])
    bb = asc.get("button_brackets", ["[", "]"])
    sb = asc.get("slider_brackets", ["[", "]"])

    c_box.add_kv("Title Brackets", f"{Txt(repr(tb[0]) + ' ... ' + repr(tb[1]), role='focus')}", right_markup=f"{SelectPill('[  ]', 'cc_pers:tbrk:[ : ]', selected=(tb == ['[ ', ' ]']))} {SelectPill(':: ::', 'cc_pers:tbrk::: ::', selected=(tb == [':: ', ' ::']))} {SelectPill('<  >', 'cc_pers:tbrk:< : >', selected=(tb == ['< ', ' >']))}")
    c_box.add_kv("Button Brackets", f"{Txt(bb[0] + 'btn' + bb[1], role='focus')}", right_markup=f"{SelectPill('[btn]', 'cc_pers:bbrk:[:]', selected=(bb == ['[', ']']))} {SelectPill('<btn>', 'cc_pers:bbrk:<:>', selected=(bb == ['<', '>']))} {SelectPill('{btn}', 'cc_pers:bbrk:{:}', selected=(bb == ['{', '}']))}")
    c_box.add_kv("Slider Glyphs", f"{Txt(sb[0] + asc.get('slider_fill', '#') * 5 + asc.get('slider_empty', '-') * 3 + sb[1], role='intent')}", right_markup=f"{SelectPill('#/-', 'cc_pers:slglyph:#:-', selected=(asc.get('slider_fill') == '#'))} {SelectPill('█/░', 'cc_pers:slglyph:█:░', selected=(asc.get('slider_fill') == '█'))} {SelectPill('=/·', 'cc_pers:slglyph:=:·', selected=(asc.get('slider_fill') == '='))}")
    c_box.add_kv("Card Spacing", f"{Txt(str(geom.get('card_spacing', 6)) + ' px', role='val')}", right_markup=" ".join(SelectPill(f"{sp}px", f"cc_pers:spacing:{sp}", selected=(int(geom.get('card_spacing', 6)) == sp)) for sp in (2, 4, 6, 10)))

    def _build_custom_glyphs(b: Box):
        ch = asc.get("chars", {})
        b.add_kv("Box Corners", Txt(f"TL={ch.get('tl')} TR={ch.get('tr')} BL={ch.get('bl')} BR={ch.get('br')} H={ch.get('h')} V={ch.get('v')}", role="schema"), right_markup=ActionBtn("Edit Corners..", "cc_pers:prompt_corners", tone="neutral"))
        b.add_kv("Sparkline Ramp", Txt(asc.get("sparkline_chars", ""), role="vitality"), right_markup=ActionBtn("Edit Ramp..", "cc_pers:prompt_spark", tone="neutral"))
        b.add_kv("Import Theme", Txt("Load external .toml file", role="schema"), right_markup=ActionBtn("Import .toml..", "cc_pers:import_theme_prompt", tone="select"))

    c_box.add_collapsible(cc_panel, "pers_creator_adv", "Definizione Granulare Glifi & Import .toml" if is_it else "Granular Glyphs & .toml Import", _build_custom_glyphs)
    c_box.add_row(" ")
    cards.append(c_box)

    p_box = Box(f"Saved Theme Profiles (~/.config/nc-ui/themes) [{len(saved)}]", icon="\uf0c7")
    p_box.add_row(" ")
    if not saved:
        p_box.add_row(f"  {Txt('No custom themes saved yet. Click [+ Save Theme..] above.', role='inactive')}")
    else:
        for nm in saved[:10]:
            p_box.add_row(
                f"  {Txt(nm + '.toml', role='focus')}",
                right_markup=f"{ActionBtn('Load', f'cc_pers:load_theme:{nm}', tone='apply')} {ActionBtn('x', f'cc_pers:del_theme:{nm}', tone='danger_soft')}",
            )
    p_box.add_row(" ")
    cards.append(p_box)
    return cards


def _build_creator_cyberpunk(cc_panel, canvas: Canvas, is_it: bool) -> list:
    cards = _feedback_cards()
    asc = THEME["ascii"]
    saved = _list_saved_themes()
    ch = asc.get("chars", {})

    b = Box(
        "ASCII GLYPH SYNTHESIZER // THEME.TOML CAD",
        icon="\uf1fc",
        actions=[ActionBtn("+ SNAPSHOT THEME", "cc_pers:save_theme_prompt", tone="apply", active=True)],
        elected=True,
    )
    b.add_row(" ")
    tl = ch.get("tl", "╭")
    tr = ch.get("tr", "╮")
    bl = ch.get("bl", "╰")
    br = ch.get("br", "╯")
    hc = ch.get("h", "─")
    vc = ch.get("v", "│")
    b.add_row(f"  {Txt(tl + hc * 18 + '[ LIVE PRIMITIVE MATRIX ]' + hc * 18 + tr, role='structure')}")
    b.add_row(f"  {Txt(vc, role='structure')} TITLE  : {SelectPill('[ ]', 'cc_pers:tbrk:[ : ]', selected=(asc['title_brackets']==['[ ',' ]']))} {SelectPill(':: ::', 'cc_pers:tbrk::: ::', selected=(asc['title_brackets']==[':: ',' ::']))} {SelectPill('< >', 'cc_pers:tbrk:< : >', selected=(asc['title_brackets']==['< ',' >']))}")
    b.add_row(f"  {Txt(vc, role='structure')} BUTTON : {SelectPill('[btn]', 'cc_pers:bbrk:[:]', selected=(asc['button_brackets']==['[',']']))} {SelectPill('<btn>', 'cc_pers:bbrk:<:>', selected=(asc['button_brackets']==['<','>']))} {SelectPill('{btn}', 'cc_pers:bbrk:{:}', selected=(asc['button_brackets']==['{','}']))}")
    b.add_row(f"  {Txt(vc, role='structure')} SLIDER : {SelectPill('#/-', 'cc_pers:slglyph:#:-', selected=(asc['slider_fill']=='#'))} {SelectPill('█/░', 'cc_pers:slglyph:█:░', selected=(asc['slider_fill']=='█'))} {ActionBtn('CORNERS..', 'cc_pers:prompt_corners', tone='neutral')} {ActionBtn('IMPORT..', 'cc_pers:import_theme_prompt', tone='select')}")
    b.add_row(f"  {Txt(bl + hc * 61 + br, role='structure')}")
    b.add_section(f"THEME VAULT ({len(saved)} TOML FILES)")
    if not saved:
        b.add_row(f"  {Txt('[ VAULT EMPTY — CLICK + SNAPSHOT THEME ]', role='inactive')}")
    else:
        for nm in saved[:6]:
            b.add_row(f"  {Txt('┣━ ' + nm + '.toml', role='focus')}", right_markup=f"{ActionBtn('LOAD', f'cc_pers:load_theme:{nm}', tone='apply')} {ActionBtn('DEL', f'cc_pers:del_theme:{nm}', tone='danger_soft')}")
    b.add_row(" ")
    cards.append(b)
    return cards


# =====================================================================
# SUBTAB 3: WALLPAPER & CMATRIX (Scorporated from cc_integrations.py)
# =====================================================================

def _build_wallpaper_standard(cc_panel, canvas: Canvas, is_it: bool) -> list:
    outputs_data = _get_niri_outputs()
    out_names = sorted(outputs_data.keys())
    target_out = str(STATE.get("desktop", "target_out", "all"))
    if target_out != "all" and target_out not in out_names:
        target_out = "all"

    cards = _feedback_cards()
    out_btns = [SelectPill("ALL", "cc_desk:target:all", selected=(target_out == "all"))] + [
        SelectPill(o, f"cc_desk:target:{o}", selected=(target_out == o)) for o in out_names
    ]
    set_img_lbl = "\uf03e Imposta immagine..." if is_it else "\uf03e Set image..."

    ctrl_box = Box(
        "NC-Desktop Wallpaper & Matrix Engine" if not is_it else "Motore Sfondi, Slideshow & Matrix (nc_util_desktop.sh)",
        icon="\uf03e",
        actions=[ActionBtn("Swap 1\u21942", "cc_desk:swap", tone="select"), ActionBtn("Restore", "cc_desk:restore", tone="neutral")],
        elected=True,
    )
    ctrl_box.add_row(" ")
    ctrl_box.add_kv("Target Output" if not is_it else "Schermo Target", " ".join(out_btns))
    ctrl_box.add_kv(
        "Wallpaper Action",
        f"{ActionBtn(set_img_lbl, 'cc_desk:prompt_img', tone='select', active=True)} {ActionBtn('\uf005 Fav', 'cc_desk:fav', tone='neutral')} {ActionBtn('Stop', 'cc_desk:stop', tone='danger_soft')}",
    )
    ctrl_box.add_kv(
        "Slideshow",
        f"{ActionBtn('\uf051 Next', 'cc_desk:next', tone='select')} {ActionBtn('\uf04c Pause', 'cc_desk:pause', tone='warn')} {ActionBtn('\uf04b Resume', 'cc_desk:resume', tone='apply')}",
    )
    ctrl_box.add_row(" ")
    cards.append(ctrl_box)

    cm_col = str(STATE.get("desktop", "cm_color", "green"))
    cm_spd = str(STATE.get("desktop", "cm_speed", "4"))
    cm_jp = bool(STATE.get("desktop", "cm_jp", True))
    cm_box = Box(
        "CMatrix Native Wayland Shader (ASCII / Katakana JP)",
        icon="\uf120",
        actions=[
            ActionBtn("\uf04b Launch Matrix", "cc_desk:cm_launch", tone="apply", active=True),
        ],
    )
    cm_box.add_row(" ")
    col_btns = " ".join(SelectPill(c.title(), f"cc_desk:cm_col:{c}", selected=(cm_col == c)) for c in ("green", "cyan", "blue", "red", "magenta", "yellow", "white"))
    spd_btns = " ".join(SelectPill(str(s), f"cc_desk:cm_spd:{s}", selected=(cm_spd == str(s))) for s in (1, 2, 4, 6, 8))
    cm_box.add_kv("Charset Mode", SelectPill("\uf00c Katakana JP" if cm_jp else "ASCII Standard", "cc_desk:cm_toggle_jp", selected=cm_jp))
    cm_box.add_kv("Shader Color", col_btns)
    cm_box.add_kv("Speed Level", spd_btns)

    def _build_folders_profiles(b: Box):
        uni_presets = " ".join(ActionBtn(c, f"cc_desk:solid:{c}", tone="neutral") for c in ("cyber", "anthracite", "mocha", "crust", "black", "lavender", "mauve", "sky"))
        b.add_kv("Unicolor", f"{uni_presets} {ActionBtn('Hex..', 'cc_desk:prompt_hex', tone='neutral')}")
        dirs = sorted([d for d in os.listdir(WALL_DIR) if os.path.isdir(os.path.join(WALL_DIR, d))]) if os.path.exists(WALL_DIR) else []
        if dirs:
            b.add_kv("Folders", " ".join(ActionBtn(f"\uf07b {d}", f"cc_desk:slide:{d}", tone="select") for d in dirs[:6]))
        profs = sorted([d for d in os.listdir(PROF_DIR) if os.path.isdir(os.path.join(PROF_DIR, d))]) if os.path.exists(PROF_DIR) else []
        b.add_kv("Profiles", (" ".join(ActionBtn(f"\uf07c {p}", f"cc_desk:load_prof:{p}", tone="select") for p in profs[:6]) if profs else Txt("None saved", role="inactive")), right_markup=ActionBtn("+ Save Profile", "cc_desk:prompt_save_prof", tone="apply"))

    cm_box.add_collapsible(cc_panel, "pers_wall_extra", "Sfondo Unicolor, Cartelle Slideshow & Profili" if is_it else "Unicolor, Slideshow Folders & Desktop Profiles", _build_folders_profiles)
    cm_box.add_row(" ")
    cards.append(cm_box)
    return cards


def _build_wallpaper_cyberpunk(cc_panel, canvas: Canvas, is_it: bool) -> list:
    outputs_data = _get_niri_outputs()
    out_names = sorted(outputs_data.keys())
    target_out = str(STATE.get("desktop", "target_out", "all"))
    cm_col = str(STATE.get("desktop", "cm_color", "green"))
    cm_spd = str(STATE.get("desktop", "cm_speed", "4"))
    cm_jp = bool(STATE.get("desktop", "cm_jp", True))

    cards = _feedback_cards()
    b = Box(
        "COMPOSITOR FRAMEBUFFER & CMATRIX SHADER PIPELINE",
        icon="\uf03e",
        actions=[ActionBtn("\uf04b LAUNCH MATRIX", "cc_desk:cm_launch", tone="apply", active=True)],
        elected=True,
    )
    b.add_row(" ")
    out_btns = " ".join([SelectPill("ALL", "cc_desk:target:all", selected=(target_out == "all"))] + [SelectPill(o, f"cc_desk:target:{o}", selected=(target_out == o)) for o in out_names])
    b.add_row(f"  {Txt('[OUTPUT BUS]──►', role='intent')} {out_btns}")
    b.add_row(f"  {Txt('[TRANSPORT] ──►', role='schema')} {ActionBtn('IMG..', 'cc_desk:prompt_img', tone='select')} {ActionBtn('NEXT', 'cc_desk:next', tone='select')} {ActionBtn('PAUSE', 'cc_desk:pause', tone='warn')} {ActionBtn('RESUME', 'cc_desk:resume', tone='apply')} {ActionBtn('SWAP', 'cc_desk:swap', tone='neutral')} {ActionBtn('STOP', 'cc_desk:stop', tone='danger_soft')}")
    b.add_section("KATAKANA / ASCII RAIN SHADER MATRIX")
    b.add_row(f"  {Txt('[CHARSET] ───►', role='schema')} {SelectPill('KATAKANA JP' if cm_jp else 'ASCII', 'cc_desk:cm_toggle_jp', selected=cm_jp)}  " + " ".join(SelectPill(c.upper(), f"cc_desk:cm_col:{c}", selected=(cm_col == c)) for c in ("green", "cyan", "red", "magenta")))
    b.add_row(f"  {Txt('[CLK SPEED] ─►', role='schema')} " + " ".join(SelectPill(f"x{s}", f"cc_desk:cm_spd:{s}", selected=(cm_spd == str(s))) for s in (1, 2, 4, 6, 8)) + f"  {ActionBtn('UNICOLOR CYBER', 'cc_desk:solid:cyber', tone='neutral')} {ActionBtn('HEX..', 'cc_desk:prompt_hex', tone='neutral')}")
    b.add_row(" ")
    cards.append(b)
    return cards


# =====================================================================
# PUBLIC ENTRY POINTS (Single Backend, Dual View)
# =====================================================================

def build_cc_personalization_cards(cc_panel, canvas: Canvas, active_sub: str = "theme") -> list:
    is_it = (get_active_language() == "it")
    style = get_cc_panel_style()

    if active_sub == "creator":
        return _build_creator_cyberpunk(cc_panel, canvas, is_it) if style == "cyberpunk" else _build_creator_standard(cc_panel, canvas, is_it)
    if active_sub == "wallpaper":
        return _build_wallpaper_cyberpunk(cc_panel, canvas, is_it) if style == "cyberpunk" else _build_wallpaper_standard(cc_panel, canvas, is_it)
    if active_sub == "waybar":
        wb_mode = str(cc_panel.get_state("wb_sub_view", "layout"))
        toggle_row = Box("Waybar Sub-View", icon="\uf0db")
        toggle_row.add_row(" ")
        toggle_row.add_row(
            f"  {SelectPill('Bar Layout & Order' if not is_it else 'Layout & Ordine Barra', 'cc_pers:wb_view:layout', selected=(wb_mode == 'layout'))}  "
            f"{SelectPill('Widget Options & Widths' if not is_it else 'Opzioni & Larghezze Widget', 'cc_pers:wb_view:options', selected=(wb_mode == 'options'))}"
        )
        toggle_row.add_row(" ")
        return [toggle_row] + build_cc_waybar_cards(cc_panel, canvas, active_sub=wb_mode)
    return _build_theme_cyberpunk(cc_panel, canvas, is_it) if style == "cyberpunk" else _build_theme_standard(cc_panel, canvas, is_it)


def handle_cc_personalization_link(cc_panel, uri: str) -> bool:
    if handle_cc_waybar_link(cc_panel, uri):
        return True

    if uri.startswith("cc_desk:"):
        act = uri[len("cc_desk:"):]
        target_out = str(STATE.get("desktop", "target_out", "all"))
        if act == "swap":
            run_desktop_cli(["--swap", "1", "2"])
        elif act in ("restore", "stop", "next", "pause", "resume", "fav"):
            run_desktop_cli(["-o", target_out, f"--{act}"])
        elif act.startswith("target:"):
            STATE.set("desktop", "target_out", act.split(":", 1)[1])
        elif act == "cm_toggle_jp":
            STATE.set("desktop", "cm_jp", not bool(STATE.get("desktop", "cm_jp", True)))
        elif act.startswith("cm_col:"):
            STATE.set("desktop", "cm_color", act.split(":", 1)[1])
        elif act.startswith("cm_spd:"):
            STATE.set("desktop", "cm_speed", act.split(":", 1)[1])
        elif act == "cm_launch":
            col = str(STATE.get("desktop", "cm_color", "green"))
            spd = str(STATE.get("desktop", "cm_speed", "4"))
            jp = bool(STATE.get("desktop", "cm_jp", True))
            run_desktop_cli(["-o", target_out, "-c", "-C", col, "-u", spd] + (["-j"] if jp else []))
        elif act.startswith("solid:"):
            run_desktop_cli(["-o", target_out, "--unicolor", act.split(":", 1)[1]])
        elif act.startswith("slide:"):
            run_desktop_cli(["-o", target_out, "-d", act.split(":", 1)[1]])
        elif act.startswith("load_prof:"):
            run_desktop_cli(["--load-profile", act.split(":", 1)[1]])
        elif act == "prompt_img":
            cc_panel.request_prompt("cc_desk_img", "Enter image path:", "", title="Set Wallpaper", required=True)
            return True
        elif act == "prompt_hex":
            cc_panel.request_prompt("cc_desk_hex", "Solid color name or #hex (e.g. #18181a):", "#18181a", title="Unicolor", required=True)
            return True
        elif act == "prompt_save_prof":
            cc_panel.request_prompt("cc_desk_save_prof", "Profile name:", "", title="Save Desktop Profile", required=True)
            return True
        if cc_panel.shell:
            cc_panel.shell.refresh_view()
        return True

    if not uri.startswith("cc_pers:"):
        return False
    act = uri[len("cc_pers:"):]

    if act.startswith("wb_view:"):
        cc_panel.set_state("wb_sub_view", act.split(":", 1)[1])
    elif act.startswith("box_style:"):
        st = act.split(":", 1)[1]
        if st in BOX_STYLES:
            THEME["ascii"]["box_style"] = st
            THEME["ascii"]["chars"] = BOX_STYLES[st]
            _persist_theme_key("ascii", "box_style", f'"{st}"')
            _set_pers_fb(True, f"Box style set to {st}")
    elif act.startswith("set_color:"):
        _, k, val = act.split(":", 2)
        THEME["colors"][k] = val
        if k == "accent":
            THEME["colors"]["intent"] = val
            _persist_theme_key("colors", "intent", f'"{val}"')
        _persist_theme_key("colors", k, f'"{val}"')
        if cc_panel.shell:
            cc_panel.shell._apply_css()
        _set_pers_fb(True, f"Color '{k}' updated to {val}")
    elif act.startswith("prompt_color:"):
        k = act.split(":", 1)[1]
        cc_panel.request_prompt(f"cc_pers_col|{k}", f"Hex color for {k} (#rrggbb):", str(THEME["colors"].get(k, "#00f0ff")), title=f"Color: {k}", required=True)
        return True
    elif act.startswith("prompt_th:"):
        k = act.split(":", 1)[1]
        cc_panel.request_prompt(f"cc_pers_th|{k}", f"Hex color for threshold {k} (#rrggbb):", str(THEME["colors"]["thresholds"].get(k, "#00ff73")), title=f"Threshold: {k}", required=True)
        return True
    elif act == "reset_theme":
        THEME.clear()
        THEME.update(copy.deepcopy(DEFAULT_THEME))
        THEME["ascii"]["chars"] = BOX_STYLES[THEME["ascii"]["box_style"]]
        _save_theme_file(THEME_PATH, THEME)
        if cc_panel.shell:
            cc_panel.shell._apply_css()
        _set_pers_fb(True, "Reset theme to Cyber-Nightcity defaults")
    elif act.startswith("font:"):
        f = act.split(":", 1)[1]
        THEME["typography"]["font_family"] = f
        _persist_theme_key("typography", "font_family", f'"{f}"')
        if cc_panel.shell:
            cc_panel.shell._apply_css()
        _set_pers_fb(True, f"Font family set to {f}")
    elif act.startswith("font_size:"):
        sz = int(act.split(":", 1)[1])
        THEME["typography"]["font_size"] = sz
        _persist_theme_key("typography", "font_size", str(sz))
        if cc_panel.shell:
            cc_panel.shell._apply_css()
        _set_pers_fb(True, f"Font size set to {sz}pt")
    elif act == "prompt_font":
        cc_panel.request_prompt("cc_pers_font", "Monospace font family:", THEME["typography"].get("font_family", "Courier"), title="Monospace Font", required=True)
        return True
    elif act == "prompt_cursor":
        cur_t, _ = _read_cursor_cfg()
        cc_panel.request_prompt("cc_pers_cursor", "XCursor theme name:", cur_t, title="Cursor Theme", required=True)
        return True
    elif act.startswith("cursor_size:"):
        cur_t, _ = _read_cursor_cfg()
        ok, msg = _apply_cursor_cfg(cur_t, int(act.split(":", 1)[1]))
        _set_pers_fb(ok, msg)
    elif act.startswith("tbrk:"):
        l, r = act.split(":", 1)[1].split(":", 1)
        THEME["ascii"]["title_brackets"] = [l, r]
        _persist_theme_key("ascii", "title_brackets", f'["{l}", "{r}"]')
    elif act.startswith("bbrk:"):
        l, r = act.split(":", 1)[1].split(":", 1)
        THEME["ascii"]["button_brackets"] = [l, r]
        _persist_theme_key("ascii", "button_brackets", f'["{l}", "{r}"]')
    elif act.startswith("slglyph:"):
        f_g, e_g = act.split(":", 1)[1].split(":", 1)
        THEME["ascii"]["slider_fill"] = f_g
        THEME["ascii"]["slider_empty"] = e_g
        _persist_theme_key("ascii", "slider_fill", f'"{f_g}"')
        _persist_theme_key("ascii", "slider_empty", f'"{e_g}"')
    elif act.startswith("spacing:"):
        sp = int(act.split(":", 1)[1])
        THEME["geometry"]["card_spacing"] = sp
        _persist_theme_key("geometry", "card_spacing", str(sp))
    elif act == "prompt_corners":
        ch = THEME["ascii"]["chars"]
        cur = f"{ch['tl']},{ch['tr']},{ch['bl']},{ch['br']},{ch['h']},{ch['v']}"
        cc_panel.request_prompt("cc_pers_corners", "6 chars tl,tr,bl,br,h,v:", cur, title="Box Glyph Set", required=True)
        return True
    elif act == "prompt_spark":
        cc_panel.request_prompt("cc_pers_spark", "Sparkline chars (low->high):", THEME["ascii"]["sparkline_chars"], title="Sparkline Ramp", required=True)
        return True
    elif act == "save_theme_prompt":
        cc_panel.request_prompt("cc_pers_save_theme", "Theme name (alphanumeric/-/_):", "nightcity-custom", title="Save Theme", required=True)
        return True
    elif act == "export_theme_prompt":
        cc_panel.request_prompt("cc_pers_exp_theme", "Export path (.toml):", "~/ncui-theme-export.toml", title="Export Theme", required=True)
        return True
    elif act == "import_theme_prompt":
        cc_panel.request_prompt("cc_pers_imp_theme", "Import .toml theme path:", "~/", title="Import Theme", required=True)
        return True
    elif act.startswith("load_theme:"):
        nm = act.split(":", 1)[1]
        p = os.path.join(THEMES_DIR, f"{nm}.toml")
        loaded = _load_toml(p)
        if loaded:
            merged = _deep_merge(DEFAULT_THEME, loaded)
            merged["ascii"]["chars"] = _deep_merge(BOX_STYLES.get(merged["ascii"].get("box_style", "rounded"), BOX_STYLES["rounded"]), merged["ascii"].get("chars", {}))
            THEME.clear()
            THEME.update(merged)
            _save_theme_file(THEME_PATH, THEME)
            if cc_panel.shell:
                cc_panel.shell._apply_css()
            _set_pers_fb(True, f"Loaded theme '{nm}'")
    elif act.startswith("del_theme:"):
        nm = act.split(":", 1)[1]
        p = os.path.join(THEMES_DIR, f"{nm}.toml")
        if os.path.exists(p):
            os.remove(p)
            _set_pers_fb(True, f"Deleted theme '{nm}'")

    if cc_panel.shell:
        cc_panel.shell.refresh_view()
    return True


def handle_cc_personalization_prompt(cc_panel, action_id: str, text: str) -> bool:
    if handle_cc_waybar_prompt(cc_panel, action_id, text):
        return True
    val = str(text or "").strip()
    if not val:
        return False
    target_out = str(STATE.get("desktop", "target_out", "all"))

    if action_id == "cc_desk_img":
        run_desktop_cli(["-o", target_out, "-i", val])
        return True
    if action_id == "cc_desk_hex":
        run_desktop_cli(["-o", target_out, "--unicolor", val])
        return True
    if action_id == "cc_desk_save_prof":
        run_desktop_cli(["--save-profile", val])
        return True

    if action_id.startswith("cc_pers_col|"):
        k = action_id.split("|", 1)[1]
        if re.match(r"^#[0-9a-fA-F]{6}$", val):
            THEME["colors"][k] = val
            if k == "accent":
                THEME["colors"]["intent"] = val
                _persist_theme_key("colors", "intent", f'"{val}"')
            _persist_theme_key("colors", k, f'"{val}"')
            if cc_panel.shell:
                cc_panel.shell._apply_css()
            _set_pers_fb(True, f"Updated {k} -> {val}")
        else:
            _set_pers_fb(False, f"Invalid hex color: {val}")
        return True
    if action_id.startswith("cc_pers_th|"):
        k = action_id.split("|", 1)[1]
        if re.match(r"^#[0-9a-fA-F]{6}$", val):
            THEME["colors"]["thresholds"][k] = val
            _persist_theme_key("colors.thresholds", k, f'"{val}"')
            _set_pers_fb(True, f"Threshold {k} -> {val}")
        else:
            _set_pers_fb(False, f"Invalid hex color: {val}")
        return True
    if action_id == "cc_pers_font":
        THEME["typography"]["font_family"] = val
        _persist_theme_key("typography", "font_family", f'"{val}"')
        if cc_panel.shell:
            cc_panel.shell._apply_css()
        _set_pers_fb(True, f"Font set to {val}")
        return True
    if action_id == "cc_pers_cursor":
        _, cur_s = _read_cursor_cfg()
        ok, msg = _apply_cursor_cfg(val, cur_s)
        _set_pers_fb(ok, msg)
        return True
    if action_id == "cc_pers_corners":
        parts = [p.strip() for p in val.split(",")]
        if len(parts) == 6 and all(len(p) == 1 for p in parts):
            for k, v in zip(("tl", "tr", "bl", "br", "h", "v"), parts):
                THEME["ascii"]["chars"][k] = v
            _save_theme_file(THEME_PATH, THEME)
            _set_pers_fb(True, "Custom box corner glyphs updated")
        return True
    if action_id == "cc_pers_spark" and len(val) >= 2:
        THEME["ascii"]["sparkline_chars"] = val
        _persist_theme_key("ascii", "sparkline_chars", f'"{val}"')
        _set_pers_fb(True, "Sparkline ramp updated")
        return True
    if action_id == "cc_pers_save_theme":
        safe = re.sub(r"[^a-zA-Z0-9_-]+", "-", val).strip("-") or "custom"
        ok, msg = _save_theme_file(os.path.join(THEMES_DIR, f"{safe}.toml"), THEME)
        _set_pers_fb(ok, msg)
        return True
    if action_id == "cc_pers_exp_theme":
        ok, msg = _save_theme_file(os.path.expanduser(val), THEME)
        _set_pers_fb(ok, msg)
        return True
    if action_id == "cc_pers_imp_theme":
        fp = os.path.expanduser(val)
        loaded = _load_toml(fp)
        if loaded:
            merged = _deep_merge(DEFAULT_THEME, loaded)
            THEME.clear()
            THEME.update(merged)
            _save_theme_file(THEME_PATH, THEME)
            if cc_panel.shell:
                cc_panel.shell._apply_css()
            _set_pers_fb(True, f"Imported theme from {os.path.basename(fp)}")
        else:
            _set_pers_fb(False, f"Could not load TOML: {fp}")
        return True
    return False
