#!/usr/bin/env python3
from core.completion import catalog_cursor_themes, rank_candidates
from core.i18n import t
from core.state import STATE
from ui.canvas import Canvas
from ui.kit import (
    ActionBtn,
    Badge,
    Box,
    InspectorCell,
    ProgressBar,
    SelectPill,
    Slider,
    StateBadge,
    Txt,
    get_active_inline_entry,
)
from ui.widgets import AsciiCard
from panels.cc_engine_input import (
    _collect_mouse_state,
    _mouse_feedback_cards,
    _mouse_toggle_pair,
)
from panels.cc_engine_personalization import (
    _feedback_cards as _pers_feedback_cards,
    _list_cursor_themes,
    _read_cursor_cfg,
    get_active_yazi_session,
)
from panels.cc_personalization_wallpaper import EmbeddedYaziWidget


def _build_mouse_general_standard(cc_panel, canvas: Canvas) -> list:
    st = _collect_mouse_state()
    b = Box(t("cc_mouse.general_mouse_config_niri_config_k"), icon="\uf245", highlighted=False)
    b.add_row(" ")
    b.add_kv(
        t("cc_mouse.left_handed_mode"),
        _mouse_toggle_pair("cc_mouse:flag:left-handed:1", "cc_mouse:flag:left-handed:0", st["left_handed"]),
        requires=["niri"],
    )
    b.add_kv(
        t("cc_mouse.natural_scroll"),
        _mouse_toggle_pair("cc_mouse:flag:natural-scroll:1", "cc_mouse:flag:natural-scroll:0", st["natural_scroll"]),
        requires=["niri"],
    )
    b.add_kv(
        t("cc_mouse.middle_click_emulation"),
        _mouse_toggle_pair("cc_mouse:flag:middle-emulation:1", "cc_mouse:flag:middle-emulation:0", st["middle_emulation"]),
        requires=["niri"],
    )
    b.add_kv(
        t("cc_mouse.focus_follows_mouse"),
        _mouse_toggle_pair("cc_mouse:inflag:focus-follows-mouse:1", "cc_mouse:inflag:focus-follows-mouse:0", st["focus_follows_mouse"]),
        requires=["niri"],
    )
    sf_presets = (0.5, 1.0, 1.5, 2.0)
    sf_custom = all(abs(st["scroll_factor"] - v) >= 0.05 for v in sf_presets)
    sf_pills = " ".join(SelectPill(f"{v:.1f}x", f"cc_mouse:sf:{v:.1f}", selected=(abs(st["scroll_factor"] - v) < 0.05)) for v in sf_presets)
    b.add_kv(
        t("cc_mouse.scroll_factor"),
        sf_pills,
        right_markup=Txt(f"{st['scroll_factor']:.2f}x", role="val") if sf_custom else None,
        requires=["niri"],
    )
    b.add_row(" ")
    return [b]


def _build_mouse_pointer_standard(cc_panel, canvas: Canvas) -> list:
    st = _collect_mouse_state()
    as_str = f"{st['accel_speed']:+.2f}"
    b = Box(t("cc_mouse.pointer_speed_acceleration_touchpa"), icon="\uf192", highlighted=False)
    b.add_row(" ")
    b.add_kv(
        t("cc_mouse.accel_speed_1_1"),
        Slider(st["accel_speed"], "cc_mouse:accel", min_val=-1.0, max_val=1.0, width=18, show_pct=False, step_buttons=True, step=0.1),
        right_markup=Txt(as_str, role="val"),
        requires=["niri"],
    )
    presets = " ".join(SelectPill(f"{v:+.1f}", f"cc_mouse:accel:{v:.2f}", selected=(abs(st["accel_speed"] - v) < 0.06)) for v in (-0.6, -0.3, 0.0, 0.3, 0.6))
    b.add_kv(t("cc_mouse.speed_presets"), presets, requires=["niri"])
    prof_pills = f"{SelectPill(t('cc_mouse.adaptive'), 'cc_mouse:prof:adaptive', selected=(st['accel_profile'] == 'adaptive'))} {SelectPill(t('cc_mouse.flat_raw_1_1'), 'cc_mouse:prof:flat', selected=(st['accel_profile'] == 'flat'))}"
    b.add_kv(t("cc_mouse.accel_profile"), prof_pills, requires=["niri"])

    def _fill_tp(bx: Box):
        bx.add_kv(t("cc_mouse.tap_to_click"), _mouse_toggle_pair("cc_mouse:tp:tap:1", "cc_mouse:tp:tap:0", st["tp_tap"]), requires=["niri"])
        bx.add_kv(t("cc_mouse.disable_while_typing"), _mouse_toggle_pair("cc_mouse:tp:dwt:1", "cc_mouse:tp:dwt:0", st["tp_dwt"]), requires=["niri"])
        bx.add_kv(t("cc_mouse.touchpad_natural_scroll"), _mouse_toggle_pair("cc_mouse:tp:natural-scroll:1", "cc_mouse:tp:natural-scroll:0", st["tp_natural_scroll"]), requires=["niri"])

    b.add_collapsible(cc_panel, "mouse_touchpad", t("cc_mouse.touchpad_settings_tap_dwt_natural"), _fill_tp)
    b.add_row(" ")
    return [b]


def _build_mouse_cursors_standard(cc_panel, canvas: Canvas) -> list:
    cards = []
    cur_c_theme, cur_c_size = _read_cursor_cfg()
    cur_themes = _list_cursor_themes()

    cur_box = Box(
        t("cc_personalization.box_cursor_studio"),
        icon="\uf245",
        actions=[StateBadge(True, f"{cur_c_theme} ({cur_c_size}px)", on_tone="ok")],
        highlighted=False,
    )
    cur_box.add_row(" ")
    sz_btns = " ".join(SelectPill(f"{cs}px", f"cc_pers:cursor_size:{cs}", selected=(cur_c_size == cs)) for cs in (16, 24, 32, 48))
    cur_box.add_kv(
        t("cc_personalization.cur_active_size"),
        f"{Txt(cur_c_theme, role='val')}  {sz_btns}",
        right_markup=ActionBtn(t("cc_personalization.edit"), "cc_pers:cursor_pick_open", tone="neutral"),
    )
    cur_q = str(STATE.get("personalization", "cursor_query", "") or "").strip()
    if STATE.get("personalization", "cursor_pick_open", False) or get_active_inline_entry() == "cc_pers_cursor_picker":
        c_ranked = rank_candidates(catalog_cursor_themes(), cur_q, limit=9)
        c_pills = [(canvas.truncate_plain(lbl, 18), f"cc_pers:set_cursor:{val}") for val, lbl in c_ranked]
        cur_box.add_inline_picker(
            t("cc_personalization.cursor_theme"),
            query=cur_q,
            placeholder=t("cc_personalization.xcursor_theme_name"),
            trigger_uri="cc_pers:cursor_pick_open",
            results=c_pills,
            clear_uri="cc_pers:cursor_pick_clear" if cur_q else "",
            active=(get_active_inline_entry() == "cc_pers_cursor_picker"),
            max_pills_per_row=3,
            width=26,
            entry_id="cc_pers_cursor_picker",
            show_empty=bool(cur_q),
        )
    cur_box.add_kv(
        t("cc_personalization.cur_win_zip_lbl"),
        f"{ActionBtn(t('cc_personalization.cur_browse_zip'), 'cc_pers:pick_cursor_zip', tone='neutral')} {ActionBtn(t('cc_personalization.cur_import_zip_btn'), 'cc_pers:prompt_cursor_zip', tone='apply')} {ActionBtn(t('cc_personalization.cur_paste_zip_btn'), 'cc_pers:paste_cursor_zip', tone='neutral')}",
    )
    cur_box.add_row(f"  {Txt(t('cc_personalization.cur_zip_bundle_hint'), role='inactive')}")
    cur_box.add_row(" ")

    cur_rows = []
    for c in cur_themes[:14]:
        nm = c["name"]
        is_act = (nm == cur_c_theme)
        src_m = Txt(t("cc_personalization.cur_src_user"), role="schema") if c["is_user"] else Txt(t("cc_personalization.cur_src_sys"), role="schema")
        if c["has_archive"]:
            src_m = f"{src_m} {Txt(t('cc_personalization.cur_archived_zip_badge'), role='schema')}"
        cnt_m = Txt(t("cc_personalization.cur_files_unit", p0=c["count"]) if c["count"] else "-", role="val")
        st_m = StateBadge(is_act, t("cc_personalization.cur_state_active"), t("cc_personalization.cur_state_standby"), on_tone="ok")
        act_parts = []
        if not is_act:
            act_parts.append(ActionBtn(t("cc_personalization.cur_activate_btn"), f"cc_pers:set_cursor:{nm}", tone="apply"))
        if c["is_user"]:
            act_parts.append(ActionBtn(t("cc_personalization.cur_del_x"), f"cc_pers:del_cursor:{nm}", tone="danger_soft"))
        act_m = " ".join(act_parts)
        cur_rows.append([
            InspectorCell(canvas.truncate_plain(nm, 20), f"cc_pers:set_cursor:{nm}", selected=is_act),
            src_m,
            cnt_m,
            st_m,
            act_m,
        ])
    cur_box.add_table(
        headers=[
            t("cc_personalization.cur_col_theme"),
            t("cc_personalization.cur_col_source"),
            t("cc_personalization.cur_col_cursors"),
            t("cc_personalization.cur_col_state"),
            t("cc_personalization.cur_col_actions"),
        ],
        widths=[20, 22, 10, 14, "flex"],
        rows=cur_rows,
        aligns=["left", "left", "left", "left", "right"],
    )
    cur_box.add_row(" ")
    cards.append(cur_box)

    yazi_sess = get_active_yazi_session()
    if STATE.get("desktop", "yazi_open") and yazi_sess is not None and getattr(yazi_sess, "target_mode", "wallpaper") == "cursor_zip":
        yazi_box = Box(
            t("cc_personalization.cur_yazi_box_title"),
            icon="\uf07c",
            actions=[ActionBtn("\uf00d", "cc_desk:close_yazi", tone="danger_soft")],
            highlighted=False,
        )
        yazi_box.add_row(f"  {Txt(t('cc_personalization.cur_yazi_hint'), role='schema')}")
        yazi_box.open_bottom = True
        yazi_w = EmbeddedYaziWidget(
            yazi_sess,
            map_width=int(round(canvas.total_px)),
            char_w=canvas.char_w,
            cell_h=max(14.0, float(canvas.char_h)),
            border_hex=yazi_box._resolve_palette()[0],
        )
        yazi_foot = Box("", highlighted=False)
        yazi_foot.open_top = True
        yazi_foot.add_row(" ")
        cards.append(AsciiCard(yazi_box, canvas, embedded_widget=yazi_w, footer_box_model=yazi_foot))

    return cards


def build_cards(cc_panel, canvas: Canvas) -> list:
    cards = _mouse_feedback_cards() + _pers_feedback_cards()
    cards.extend(_build_mouse_general_standard(cc_panel, canvas))
    cards.extend(_build_mouse_pointer_standard(cc_panel, canvas))
    cards.extend(_build_mouse_cursors_standard(cc_panel, canvas))
    return cards
