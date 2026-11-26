#!/usr/bin/env python3
import os
import re
from core.config import (
    CONFIG,
    get_hide_unavailable_settings,
    get_hover_tooltip_delay,
    get_hover_tooltips_enabled,
)
from core.i18n import get_active_language, list_available_languages, t
from core.state import STATE
from ui.canvas import Canvas
from ui.kit import ActionBtn, Box, SelectPill, Slider, Txt, get_semantic_palette
from panels.cc_engine_settings import _feedback_cards, _read_log_window

_HERO_LOGO_LINES = (
    "███╗   ██╗ ██████╗      ██╗   ██╗██╗",
    "████╗  ██║██╔════╝      ██║   ██║██║",
    "██╔██╗ ██║██║     █████╗██║   ██║██║",
    "██║╚██╗██║██║     ╚════╝██║   ██║██║",
    "██║ ╚████║╚██████╗      ╚██████╔╝██║",
    "╚═╝  ╚═══╝ ╚═════╝       ╚═════╝ ╚═╝",
)


def _build_hero_banner_box(canvas: Canvas) -> Box:
    p = get_semantic_palette()
    pt = int(canvas.base_pango_size)
    b_hero = Box("", highlighted=False)
    b_hero.add_row(" ")
    pills_line = (
        f"{ActionBtn(t('cc_settings.hero_online'), 'cc:section:sysinfo', tone='neutral')}  "
        f"{ActionBtn(t('cc_settings.hero_deterministic'), 'cc:section:personalization:theme', tone='apply')}"
    )
    right_lines = (
        "",
        Txt(t("cc_settings.hero_tag_1"), role="val"),
        Txt(t("cc_settings.hero_tag_2"), role="val"),
        Txt(t("cc_settings.hero_tag_3"), role="val"),
        "",
        pills_line,
    )
    pad = " " * max(2, (canvas.inner_cols - 69) // 2)
    for raw_logo, r_txt in zip(_HERO_LOGO_LINES, right_lines):
        logo_m = re.sub(r"(█+)", f"<span color='{p['focus']}' size='{pt}'>\\1</span>", raw_logo)
        logo_m = re.sub(r"([╔╗╚╝═║]+)", f"<span color='{p['schema']}' size='{pt}'>\\1</span>", logo_m)
        row_m = f"{pad}{logo_m}    {r_txt}" if r_txt else f"{pad}{logo_m}"
        b_hero.add_row(row_m)
    b_hero.add_row(" ")
    return b_hero


def _build_interface_standard(cc_panel, canvas: Canvas) -> list:
    hide_unavail = get_hide_unavailable_settings()
    gen = CONFIG.get("general", {})
    cc_cfg = CONFIG.get("control_center", {})
    side = str(cc_cfg.get("side", gen.get("side", "right")))
    log_lvl = int(gen.get("log_level", 3))
    lang = get_active_language()
    cards = _feedback_cards()
    cards.append(_build_hero_banner_box(canvas))

    b = Box(
        t("cc_settings.behavior_diagnostics"),
        icon="\uf013",
        highlighted=False,
    )
    b.add_row(" ")

    hd_pills = f"{SelectPill(t('cc_settings.hide_unavailable'), 'cc_set:hide_unavail:1', selected=hide_unavail)} {SelectPill(t('cc_settings.show_missing_badge'), 'cc_set:hide_unavail:0', selected=not hide_unavail)}"
    b.add_kv(t("cc_settings.missing_tools_filter"), hd_pills)

    sd_pills = f"{SelectPill(t('cc_settings.left'), 'cc_set:side:left', selected=(side == 'left'))} {SelectPill(t('cc_settings.right'), 'cc_set:side:right', selected=(side == 'right'))}"
    b.add_kv(t("cc_settings.window_anchor_side"), sd_pills)

    lang_names = {"en": "English", "it": "Italiano"}
    lg_pills = " ".join(SelectPill(lang_names.get(lg, lg.upper()), f"cc_set:lang:{lg}", selected=(lang == lg)) for lg in list_available_languages())
    b.add_kv(t("cc_settings.interface_language"), lg_pills)

    lv_pills = " ".join(SelectPill(str(lvl), f"cc_set:loglevel:{lvl}", selected=(log_lvl == lvl)) for lvl in (0, 1, 2, 3, 4))
    b.add_kv(t("cc_settings.log_verbosity_0_4"), lv_pills, right_markup=ActionBtn(t("cc_settings.clear_log"), "cc_set:clear_log", tone="danger_soft"))

    help_on = get_hover_tooltips_enabled()
    help_delay = get_hover_tooltip_delay()
    hh_pills = (
        f"{SelectPill(t('common.on'), 'cc_set:hover_help:1', selected=help_on)} "
        f"{SelectPill(t('common.off'), 'cc_set:hover_help:0', selected=not help_on)}"
    )
    b.add_kv(t("cc_settings.action_descriptions"), hh_pills)

    hd_slider = Slider(
        help_delay,
        "cc_set:hover_delay",
        min_val=0.2,
        max_val=3.0,
        width=28,
        show_pct=False,
        step=0.1,
        enabled=help_on,
    )
    b.add_kv(
        t("cc_settings.hover_delay"),
        f"{hd_slider} {Txt(f'{help_delay:.1f}s', role='val' if help_on else 'inactive')}",
    )

    b.add_row(" ")
    cards.append(b)

    b_bak = Box(
        t("cc_settings.sec_ui_backup"),
        icon="\uf0c7",
        highlighted=False,
    )
    b_bak.add_row(" ")
    b_bak.add_kv(
        t("cc_settings.ui_backup_lbl"),
        Txt(t("cc_settings.ui_backup_desc"), role="schema"),
        right_markup=ActionBtn(t("cc_settings.ui_backup_btn"), "cc_set:backup_ui", tone="apply"),
    )
    b_bak.add_kv(
        t("cc_settings.distro_pkg_lbl"),
        Txt(t("cc_settings.distro_pkg_desc"), role="schema"),
        right_markup=ActionBtn(t("cc_settings.distro_pkg_btn"), "cc_set:build_distro_pkg", tone="apply"),
    )
    b_bak.add_row(" ")
    cards.append(b_bak)

    win_size = 30
    cur_off = int(STATE.get("settings", "log_scroll_offset", 0) or 0)
    log_path, win_lines, total, clamped_off = _read_log_window(cur_off, window_size=win_size)
    range_lbl = f"L{win_lines[0][0]}-{win_lines[-1][0]}/{total}" if win_lines else t("cc_settings.zero_lines")
    log_box = Box(
        t("cc_settings.runtime_log_tail", p0=os.path.basename(log_path)),
        icon="\uf15c",
        highlighted=False,
    )
    max_txt_cols = max(40, canvas.inner_cols - 12)
    fmt_rows: list[str] = []
    for ln_no, ln_txt in win_lines:
        ln_u = ln_txt.upper()
        is_err_ln = any(k in ln_u for k in ("[ERROR]", "[STDERR]", "CRITICAL", "TRACEBACK", "NAMEERROR", "ERROR:"))
        ln_role = "fault" if is_err_ln else ("attention" if "WARN" in ln_u else "schema")
        fmt_rows.append(f"  {Txt(f'{ln_no:04d}│', role='structure')} {Txt(canvas.truncate_plain(ln_txt, max_txt_cols), role=ln_role)}")

    log_box.add_scroll_viewport(
        fmt_rows,
        total_items=total,
        win_rows=win_size,
        offset=clamped_off,
        action_prefix="cc_set:log_scroll",
        state_ns="settings",
        state_key="log_scroll_offset",
        anchor="bottom",
        bottom_label=t("cc_settings.tail"),
        range_label=range_lbl,
        trailing_actions=[ActionBtn(t("cc_settings.clear"), "cc_set:clear_log", tone="danger_soft")],
        empty_markup=f"  {Txt(t('cc_settings.is_empty_or_absent', p0=log_path), role='inactive')}",
    )
    cards.append(log_box)
    return cards


def build_cards(cc_panel, canvas: Canvas) -> list:
    return _build_interface_standard(cc_panel, canvas)
