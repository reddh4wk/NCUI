#!/usr/bin/env python3
import os
from core import probe
from core.config import CONFIG_DIR
from core.i18n import t
from core.privileges import build_privilege_modal_card, get_privileged_action
from core.state import STATE
from ui.canvas import Canvas
from ui.kit import ActionBtn, Badge, Box, InlineField, InlineFilter, ProgressBar, SelectPill, Slider, StateBadge, Txt
from panels.cc_engine_input import (
    DEFAULT_NIRI_SHORTCUTS,
    _norm_action_str,
    _collect_keyboard_state,
    _load_shortcuts_store,
    _get_bind_draft,
    _build_niri_action_from_draft,
    _feedback_box,
)


def _build_shortcuts_standard(cc_panel, canvas: Canvas, st: dict) -> list:
    cards = _feedback_box()
    binds = st["binds"]
    cap_mode = str(STATE.get("keyboard", "capture_mode", "") or "")
    cap_prev = str(STATE.get("keyboard", "capture_preview", "") or "")
    wiz = STATE.get("keyboard", "conflict_wizard", {}) or {}

    # Conflict Resolution Wizard Card (shown when restoring defaults hits collisions)
    if isinstance(wiz, dict) and wiz.get("conflicts"):
        confs = wiz["conflicts"]
        w_mode = wiz.get("mode", "choice")
        w_step = int(wiz.get("step", 0))
        cw_box = Box(
            t("cc_keyboard.default_restore_conflicts", p0=len(confs)),
            icon="\uf071",
            actions=[
                ActionBtn(t("cc_keyboard.overwrite_save_orphans"), "cc_kbd:conflict_overwrite", tone="warn"),
                ActionBtn(t("cc_keyboard.guided_rebind"), "cc_kbd:conflict_guided", tone="apply") if w_mode != "guided" else Txt(t("cc_keyboard.step", p0=w_step + 1, p1=len(confs)), role="schema"),
                ActionBtn(t("cc_keyboard.cancel"), "cc_kbd:conflict_cancel", tone="neutral"),
            ],
            highlighted=False,
        )
        cw_box.add_row(" ")
        if w_mode == "guided" and 0 <= w_step < len(confs):
            cur_c = confs[w_step]
            cw_box.add_row(f"  {Badge(t('cc_keyboard.guided_step_press_new_shortcut_key', p0=w_step + 1, p1=len(confs)), style='warn')}  {Txt(cap_prev, role='focus')}")
            cw_box.add_kv(t("cc_keyboard.default_wants_combo"), f"{Txt(cur_c['combo'], role='focus')} -> {Txt(cur_c['default_label'], role='focus')}")
            cw_box.add_kv(
                t("cc_keyboard.displaced_user_action"),
                Txt(cur_c["current_action"], role="attention"),
                right_markup=f"{ActionBtn(t('cc_keyboard.move_to_orphans'), 'cc_kbd:conflict_step_orphan', tone='neutral')} {ActionBtn(t('cc_keyboard.skip_default'), 'cc_kbd:conflict_step_skip', tone='danger_soft')}",
            )
        else:
            cw_box.add_table(
                headers=[t("cc_keyboard.combo"), t("cc_keyboard.current_user_action"), t("cc_keyboard.default_action_to_restore")],
                widths=[18, 28, "flex"],
                rows=[[Txt(c["combo"], role="focus"), Txt(canvas.truncate_plain(c["current_action"], 26), role="attention"), Txt(canvas.truncate_plain(c["default_label"], 28), role="vitality")] for c in confs[:8]],
            )
        cw_box.add_row(" ")
        cards.append(cw_box)

    bd = _get_bind_draft()
    flt_cat = str(STATE.get("keyboard", "bind_filter", "ALL") or "ALL")
    is_cap = (cap_mode == "shortcut_combo")
    by_combo = {b["combo"]: b["action"] for b in binds}
    by_norm_act = {_norm_action_str(b["action"]) for b in binds}
    combo_disp = bd["combo"]
    conflict_act = by_combo.get(combo_disp, "") if combo_disp and combo_disp != bd["edit_orig"] else ""
    kdl_act = _build_niri_action_from_draft(bd)

    # Card 1: Autonomous Shortcut Capture & Action Builder
    cap_btn = (
        ActionBtn(t("cc_keyboard.cancel_esc"), "cc_kbd:cap_stop", tone="warn", modal_active=True)
        if is_cap
        else ActionBtn(t("cc_keyboard.capture_keys_2"), "cc_kbd:cap_start:shortcut_combo", tone="apply")
    )
    sb_box = Box(
        t("cc_keyboard.in_gui_shortcut_capture_builder"),
        icon="\uf084",
        actions=[cap_btn, ActionBtn(t("cc_keyboard.reload_niri"), "cc_kbd:reload_niri", tone="neutral")],
        highlighted=False,
    )
    sb_box.add_row(" ")
    if is_cap:
        sb_box.add_row(f"  {Badge(t('cc_keyboard.press_desired_key_combination_on_k'), style='warn')}  {Txt(cap_prev, role='focus')}")

    if combo_disp:
        status_b = Badge(t("cc_keyboard.overwrites", p0=canvas.truncate_plain(conflict_act, 20)), style="warn") if conflict_act else StateBadge(True, t("cc_keyboard.free_in_niri"))
        combo_ui = f"{Txt(combo_disp, role='focus')}  {status_b}"
    else:
        combo_ui = Txt(t("cc_keyboard.none_yet_click_capture_keys"), role="inactive")
    sb_box.add_kv(
        t("cc_keyboard.1_shortcut_combo"),
        combo_ui,
        right_markup=InlineField(
            "cc_bind_combo",
            "cc_kbd:bind_inline_combo",
            value="",
            empty_label=t("cc_keyboard.manual"),
            placeholder="Mod+Shift+G",
            width=18,
            icon="\u270e",
            tone="neutral",
        ),
    )

    mode_pills = (
        f"{SelectPill(t('cc_keyboard.standard_action'), 'cc_kbd:bind_mode:standard', selected=(bd['mode'] == 'standard'))} "
        f"{SelectPill(t('cc_keyboard.custom_command'), 'cc_kbd:bind_mode:spawn', selected=(bd['mode'] == 'spawn'))} "
        f"{SelectPill(t('cc_keyboard.nc_ui_panel'), 'cc_kbd:bind_mode:ncui', selected=(bd['mode'] == 'ncui'))}"
    )
    sb_box.add_kv(t("cc_keyboard.2_action_source"), mode_pills)

    if bd["mode"] == "standard":
        cat_pills = " ".join(
            SelectPill(t(f"cc_keyboard.cat_{cid}"), f"cc_kbd:bind_std_cat:{cid}", selected=(bd["std_cat"] == cid))
            for cid in ("window", "system", "apps")
        )
        sb_box.add_kv(t("cc_keyboard.3a_category"), cat_pills)
        std_items = [d for d in DEFAULT_NIRI_SHORTCUTS if d["cat"] == bd["std_cat"]]
        step = 3 if bd["std_cat"] == "system" else 4
        for i in range(0, min(8, len(std_items)), step):
            chunk = std_items[i : i + step]
            row_lbl = t("cc_keyboard.3b_pick_action") if i == 0 else ""
            sb_box.add_kv(row_lbl, " ".join(SelectPill(d["action"].split()[0] if bd["std_cat"] != "system" else t(f"cc_keyboard.shortcut_{d['id']}")[:18], f"cc_kbd:bind_std_act:{d['id']}", selected=(bd["std_act"] == d["action"])) for d in chunk))
    elif bd["mode"] == "spawn":
        cmd_disp = Txt(canvas.truncate_plain(bd["cmd"], 42), role="focus") if bd["cmd"] else Txt(t("cc_keyboard.click_set_command_to_choose_comman"), role="inactive")
        sb_box.add_kv(t("cc_keyboard.3_custom_command"), cmd_disp, right_markup=ActionBtn(t("cc_keyboard.set_command"), "cc_kbd:bind_prompt_cmd", tone="neutral"))
    else:
        nc_1 = [(t("cc_keyboard.nc_cc"), "settings system"), (t("cc_keyboard.nc_net"), "popup network"), (t("cc_keyboard.nc_audio"), "popup audio"), (t("cc_keyboard.nc_fw"), "settings firewall")]
        nc_2 = [(t("cc_keyboard.nc_kbl"), "popup kb_layout"), (t("cc_keyboard.nc_clip"), "popup clipboard"), (t("cc_keyboard.nc_shot"), "popup capture"), (t("cc_keyboard.nc_ups"), "popup ups")]
        sb_box.add_kv(t("cc_keyboard.3_nc_ui_target"), " ".join(SelectPill(lbl, f"cc_kbd:bind_nc:{tgt}", selected=(bd["nc_target"] == tgt)) for lbl, tgt in nc_1))
        sb_box.add_kv(t("cc_keyboard.more_popups"), " ".join(SelectPill(lbl, f"cc_kbd:bind_nc:{tgt}", selected=(bd["nc_target"] == tgt)) for lbl, tgt in nc_2))

    kdl_line = f"{combo_disp or '<keys>'} {{ {kdl_act or '<command>'}; }}"
    can_save = bool(combo_disp and kdl_act)
    save_btn = ActionBtn(t("cc_keyboard.save_reload_niri"), "cc_kbd:bind_save", tone="apply" if can_save else "inactive")
    sb_box.add_kv(t("cc_keyboard.4_kdl_preview"), Txt(canvas.truncate_plain(kdl_line, 44), role="val" if can_save else "inactive"), right_markup=f"{save_btn} {ActionBtn(t('cc_keyboard.clear'), 'cc_kbd:bind_clear', tone='neutral')}")
    sb_box.add_row(" ")
    cards.append(sb_box)

    # Orphans & Unbound Standard Actions computation
    store = _load_shortcuts_store()
    orphans = [o for o in store["orphans"] if _norm_action_str(o.get("action")) not in by_norm_act]
    unbound_std = [d for d in DEFAULT_NIRI_SHORTCUTS if _norm_action_str(d["action"]) not in by_norm_act]
    tot_orphans = len(orphans) + len(unbound_std)

    flt_pills = " ".join(
        SelectPill(t("cc_keyboard.flt_orphans", count=tot_orphans) if cid == "orphans" else t(f"cc_keyboard.flt_{cid}"), f"cc_kbd:bind_flt:{cid}", selected=(flt_cat == cid))
        for cid in ("ALL", "window", "system", "apps", "custom", "orphans")
    )
    b_box = Box(
        t("cc_keyboard.active_keybindings", p0=len(binds)),
        icon="\uf0c9",
        actions=[ActionBtn(t("cc_keyboard.restore_window_defaults"), "cc_kbd:restore_defaults:window", tone="warn")],
        highlighted=False,
    )
    b_box.add_row(" ")
    b_box.add_kv(t("cc_keyboard.filter_section"), flt_pills)
    shortcut_q = str(STATE.get("keyboard", "shortcut_query", "") or "").strip()
    sql = shortcut_q.lower()
    if sql:
        orphans = [o for o in orphans if sql in f"{o.get('label', '')} {o.get('action', '')}".lower()]
        unbound_std = [d for d in unbound_std if sql in f"{t('cc_keyboard.shortcut_' + d['id'])} {d['combo']} {d['action']}".lower()]
    b_box.add_kv(
        t("cc_keyboard.filter_shortcuts_lbl"),
        InlineFilter(
            "cc_bind_flt",
            shortcut_q,
            t("cc_keyboard.filter_shortcuts_ph"),
            "cc_kbd:bind_flt_open",
            clear_uri="cc_kbd:bind_flt_clear" if shortcut_q else "",
            width=26,
            active=bool(shortcut_q),
        ),
    )

    if flt_cat == "orphans":
        if not orphans and not unbound_std:
            b_box.add_row(f"  {Txt(t('cc_keyboard.no_orphaned_or_missing_standard_ac'), role='vitality')}")
        else:
            o_rows = []
            for idx, o in enumerate(orphans):
                o_rows.append([
                    Txt(t("cc_keyboard.orphan"), role="attention"),
                    Txt(canvas.truncate_plain(o.get("label", o["action"]), 38), role="focus"),
                    f"{ActionBtn(t('cc_keyboard.bind_key'), f'cc_kbd:bind_orphan:{idx}', tone='apply')} {ActionBtn('x', f'cc_kbd:del_orphan:{idx}', tone='danger_soft')}",
                ])
            for d in unbound_std:
                lbl = t(f"cc_keyboard.shortcut_{d['id']}")
                d_id = d["id"]
                o_rows.append([
                    Txt(t("cc_keyboard.default_2"), role="schema"),
                    Txt(canvas.truncate_plain(f"{lbl} ({d['combo']})", 38), role="schema"),
                    f"{ActionBtn('\u21ba ' + d['combo'], f'cc_kbd:bind_std_default:{d_id}', tone='apply')} {ActionBtn(t('cc_keyboard.custom'), f'cc_kbd:bind_std_pick:{d_id}', tone='neutral')}",
                ])
            b_box.add_table(
                headers=[t("cc_keyboard.kind"), t("cc_keyboard.unbound_action_command"), t("cc_keyboard.rebind_actions")],
                widths=[10, "flex", 26],
                rows=o_rows,
                aligns=["left", "left", "right"],
            )
    else:
        shown = [b for b in binds if flt_cat == "ALL" or b["cat"] == flt_cat]
        if sql:
            shown = [b for b in shown if sql in f"{b['combo']} {b['cat']} {b['action']}".lower()]
        if not shown:
            b_box.add_row(f"  {Txt(t('cc_keyboard.no_matching_bindings_in_this_categ'), role='inactive')}")
        else:
            rows = []
            for b in shown[:18]:
                idx = binds.index(b)
                cat_b = Txt(b["cat"].upper()[:6], role="schema")
                rows.append([
                    Txt(b["combo"], role="focus"),
                    cat_b,
                    Txt(canvas.truncate_plain(b["action"], 36), role="val"),
                    f"{ActionBtn('\u270e', f'cc_kbd:bind_edit:{idx}', tone='neutral')} {ActionBtn(t('cc_keyboard.unbind'), f'cc_kbd:unbind:{idx}', tone='warn')} {ActionBtn('x', f'cc_kbd:del_bind:{b['combo']}', tone='danger_soft')}",
                ])
            b_box.add_table(
                headers=[t("cc_keyboard.shortcut"), t("cc_keyboard.cat"), t("cc_keyboard.niri_action_command"), t("cc_keyboard.actions")],
                widths=[20, 8, "flex", 18],
                rows=rows,
                aligns=["left", "left", "left", "right"],
            )

    def _build_nc_nav(b: Box):
        b.add_table(
            headers=[t("cc_keyboard.keys"), t("cc_keyboard.nc_ui_navigation_action")],
            widths=[22, "flex"],
            rows=[
                [Txt(t("cc_keyboard.arrows_tab"), role="focus"), Txt(t("cc_keyboard.2d_spatial_focus_across_buttons_sl"), role="val")],
                [Txt(t("cc_keyboard.shift_left_right"), role="focus"), Txt(t("cc_keyboard.cycle_subtabs_waybar_bar_surfing"), role="val")],
                [Txt(t("cc_keyboard.shift_up_down"), role="focus"), Txt(t("cc_keyboard.switch_control_center_section_expa"), role="val")],
                [Txt(t("cc_keyboard.enter_space"), role="focus"), Txt(t("cc_keyboard.trigger_focused_control_or_prompt"), role="val")],
            ],
        )

    b_box.add_collapsible(cc_panel, "kbd_nc_nav", t("cc_keyboard.nc_ui_internal_2d_navigation_keys"), _build_nc_nav)
    b_box.add_row(" ")
    cards.append(b_box)
    return cards


def build_cards(cc_panel, canvas: Canvas) -> list:
    st = _collect_keyboard_state()
    return _build_shortcuts_standard(cc_panel, canvas, st)
