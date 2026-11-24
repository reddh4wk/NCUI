#!/usr/bin/env python3
import os
import re
from core.config import CONFIG, get_hide_unavailable_settings
from core.i18n import get_active_language, t
from core.state import STATE
from ui.canvas import Canvas
from ui.kit import ActionBtn, Box, SelectPill, Txt, get_semantic_palette
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
        f"{SelectPill(t('cc_settings.hero_online'), 'cc:section:sysinfo', selected=True)}  "
        f"{ActionBtn(t('cc_settings.hero_deterministic'), 'cc:section:personalization', tone='apply')}"
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
    side = str(gen.get("side", "right"))
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

    lg_pills = f"{SelectPill('English', 'cc_set:lang:en', selected=(lang == 'en'))} {SelectPill('Italiano', 'cc_set:lang:it', selected=(lang == 'it'))}"
    b.add_kv(t("cc_settings.interface_language"), lg_pills)

    lv_pills = " ".join(SelectPill(str(lvl), f"cc_set:loglevel:{lvl}", selected=(log_lvl == lvl)) for lvl in (0, 1, 2, 3, 4))
    b.add_kv(t("cc_settings.log_verbosity_0_4"), lv_pills, right_markup=ActionBtn(t("cc_settings.clear_log"), "cc_set:clear_log", tone="danger_soft"))

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
    b_bak.add_row(" ")
    cards.append(b_bak)

    win_size = 30
    cur_off = int(STATE.get("settings", "log_scroll_offset", 0) or 0)
    log_path, win_lines, total, clamped_off = _read_log_window(cur_off, window_size=win_size)
    if clamped_off != cur_off:
        STATE.set("settings", "log_scroll_offset", clamped_off)

    range_lbl = f"L{win_lines[0][0]}-{win_lines[-1][0]}/{total}" if win_lines else t("cc_settings.zero_lines")
    log_box = Box(
        t("cc_settings.runtime_log_tail", p0=os.path.basename(log_path)),
        icon="\uf15c",
        actions=[
            Txt(range_lbl, role="schema"),
            ActionBtn("\uf100", "cc_set:log_scroll:top", tone="neutral"),
            ActionBtn("\uf077", "cc_set:log_scroll:up", tone="neutral"),
            ActionBtn("\uf078", "cc_set:log_scroll:down", tone="neutral"),
            ActionBtn(t("cc_settings.tail"), "cc_set:log_scroll:bottom", tone="neutral"),
            ActionBtn(t("cc_settings.clear"), "cc_set:clear_log", tone="danger_soft"),
        ],
        scroll_action="cc_set:log_scroll",
        highlighted=False,
    )
    log_box.add_row(" ")
    max_off = max(0, total - win_size)
    thumb_row = int(round((1.0 - (clamped_off / float(max(1, max_off)))) * (win_size - 1))) if total > win_size else (win_size - 1)
    max_txt_cols = max(40, canvas.inner_cols - 12)
    for row_i in range(win_size):
        sb_ch = Txt("█", role="intent") if row_i == thumb_row else Txt("│", role="structure")
        if row_i < len(win_lines):
            ln_no, ln_txt = win_lines[row_i]
            ln_u = ln_txt.upper()
            is_err_ln = any(k in ln_u for k in ("[ERROR]", "[STDERR]", "CRITICAL", "TRACEBACK", "NAMEERROR", "ERROR:"))
            ln_role = "fault" if is_err_ln else ("attention" if "WARN" in ln_u else "schema")
            left_m = f"  {Txt(f'{ln_no:04d}│', role='structure')} {Txt(canvas.truncate_plain(ln_txt, max_txt_cols), role=ln_role)}"
        elif row_i == 0 and not win_lines:
            left_m = f"  {Txt(t('cc_settings.is_empty_or_absent', p0=log_path), role='inactive')}"
        else:
            left_m = " "
        log_box.add_row(left_m, right_markup=sb_ch)
    log_box.add_row(" ")
    cards.append(log_box)
    return cards


def build_cards(cc_panel, canvas: Canvas) -> list:
    return _build_interface_standard(cc_panel, canvas)
