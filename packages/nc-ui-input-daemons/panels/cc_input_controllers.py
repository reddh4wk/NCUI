#!/usr/bin/env python3
"""NC-UI Control Center — Gaming Controllers Hub (Xbox Accessories + Steam Input inspired)."""

import fcntl
import glob
import json
import math
import os
import re
import struct
import time
from core import probe
from core.config import THEME, _parse_rgba_components, get_config_dir
from core.i18n import t
from core.privileges import build_privilege_modal_card
from core.pad_mapper_core import (
    _ABS_BRAKE,
    _ABS_GAS,
    _ABS_HAT0X,
    _ABS_HAT0Y,
    _ABS_HAT2X,
    _ABS_HAT2Y,
    _ABS_PROFILE,
    _ABS_RX,
    _ABS_RY,
    _ABS_RZ,
    _ABS_X,
    _ABS_Y,
    _ABS_Z,
    _BTN_DPAD_DOWN,
    _BTN_DPAD_LEFT,
    _BTN_DPAD_RIGHT,
    _BTN_DPAD_UP,
    _BTN_EAST,
    _BTN_GEAR_DOWN,
    _BTN_GEAR_UP,
    _BTN_GRIPL,
    _BTN_GRIPL2,
    _BTN_GRIPR,
    _BTN_GRIPR2,
    _BTN_MODE,
    _BTN_NORTH,
    _BTN_SELECT,
    _BTN_SOUTH,
    _BTN_START,
    _BTN_THUMB,
    _BTN_THUMB2,
    _BTN_THUMBL,
    _BTN_THUMBR,
    _BTN_TL,
    _BTN_TL2,
    _BTN_TR,
    _BTN_TR2,
    _BTN_TRIGGER_HAPPY1,
    _BTN_TRIGGER_HAPPY2,
    _BTN_TRIGGER_HAPPY3,
    _BTN_TRIGGER_HAPPY4,
    _BTN_TRIGGER_HAPPY5,
    _BTN_TRIGGER_HAPPY6,
    _BTN_TRIGGER_HAPPY7,
    _BTN_TRIGGER_HAPPY8,
    _BTN_WEST,
    _CURVES,
    _ELITE_SLOTS,
    _EVIOCRMFF,
    _EVIOCSFF,
    _EV_ABS,
    _EV_FF,
    _EV_KEY,
    _FF_RUMBLE,
    _GYRO_MODES,
    _KEY_BACK,
    _KEY_HOMEPAGE,
    _KEY_RECORD,
    _LED_MODES,
    _PADDLE_ACTIONS,
    _PADDLE_EVDEV_CODES,
    _PADDLE_KEYS,
    _PROBE_AXES,
    _STICK_CALCS,
    _TRIGGER_LOCKS,
    _TRIGGER_LOCK_CUT,
    _TRIGGER_LOCK_IDS,
    _XBOX_ELITE_PIDS,
    ALL_PAD_CONTROLS,
    BIND_MODES,
    MACRO_PAD_MODES,
    MOUSE_TARGET_TO_EVDEV,
    PAD_CONTROL_IDS,
    PAD_MAPPER_BIN,
    STICK_MODES,
    WHEEL_TARGETS,
    _controllers_path,
    _detect_family,
    apply_stick_calibration,
    bitmask_has,
    build_template_profile,
    compile_macro_events,
    eval_response_curve,
    eval_trigger_curve,
    format_action_short,
    get_mapper_runtime_status,
    is_pad_mapper_running,
    iter_proc_input_blocks,
    load_pad_store,
    norm_pad_profile,
    norm_pad_store,
    reload_pad_mapper_if_running,
    resolve_combo_codes,
    resolve_evdev_axis_map,
    resolve_evdev_btn_map,
    save_pad_store,
    stop_pad_mapper,
)
from core.state import STATE
from ui.canvas import Canvas
from ui.kit import (
    ActionBtn,
    Badge,
    Box,
    CyclePill,
    InlineField,
    InspectorCell,
    SelectPill,
    Slider,
    StateBadge,
    Txt,
    feedback_cards,
    get_semantic_palette,
    hex_to_rgba_f,
    rounded_rect_path,
    set_feedback,
)
from ui.widgets import AsciiCard


from panels.cc_engine_controllers_widgets import (
    _CONTROLLER_MODELS,
    _DEFAULT_PROFILE,
    _EV_STRUCT,
    _LAYOUTS,
    _LIVE_INPUT,
    _MODEL_BY_ID,
    ControllerBlueprintWidget,
    ResponseCurveWidget,
)
from panels.cc_engine_controllers import (
    _DISCOVERY_CACHE,
    _active_key_and_device,
    _active_map_profile,
    _apply_led_mode,
    _find_battery_for_device,
    _find_led_paths,
    _get_map_store_draft,
    _get_profile_draft,
    _handle_mp_link,
    _is_map_store_dirty,
    _is_profile_dirty,
    _load_macros_brief,
    _load_saved_profiles,
    _norm_profile,
    _read_sysfs_str,
    _save_profiles,
    _scan_paired_bt_controllers,
    _send_kernel_rumble,
    _set_ctrl_bind_action,
    _set_map_store_draft,
    _set_profile_draft,
    apply_captured_pad_key,
    discover_controllers,
    handle_cc_controllers_link,
    handle_cc_controllers_prompt,
)

def _build_mapping_cards(cc_panel, canvas: Canvas, dev: dict | None, prof_hw: dict) -> list:
    store = _get_map_store_draft()
    _idx, mp = _active_map_profile(store)
    is_dirty = _is_map_store_dirty(store)
    uinput_exists = os.path.exists("/dev/uinput")
    uinput_ok = uinput_exists and os.access("/dev/uinput", os.W_OK)
    rt = get_mapper_runtime_status()
    m_run = bool(rt.get("running"))
    macros = _load_macros_brief()
    macros_by_id = {m["id"]: m["name"] for m in macros}

    # --- CARD 1: Compatibility Layer, Game Profiles & Window Auto-Switch ---
    hdr_acts = [
        ActionBtn(t("cc_input_controllers.mapper_stop_btn"), "cc_ctrl:mp_stop", tone="danger_soft")
        if m_run
        else ActionBtn(t("cc_input_controllers.mapper_start_btn"), "cc_ctrl:mp_start", tone="apply"),
    ]
    comp_box = Box(
        t("cc_input_controllers.mapper_box_title"),
        icon="\uf11c",
        actions=hdr_acts,
        highlighted=False,
    )
    comp_box.add_row(" ")
    if not uinput_exists:
        comp_box.add_kv(
            t("cc_input_controllers.mapper_uinput_locked"),
            Badge(t("cc_keyboard.uinput_missing"), style="crit"),
        )
    elif not uinput_ok:
        comp_box.add_kv(
            t("cc_input_controllers.mapper_uinput_locked"),
            Badge(t("cc_keyboard.kernel_uinput_permission_required"), style="warn"),
            right_markup=ActionBtn(t("cc_input_controllers.mapper_unlock_btn"), "cc_ctrl:unlock_uinput", tone="apply"),
        )
    prof_acts = (
        f"{ActionBtn(t('cc_input_controllers.mp_new_btn'), 'cc_ctrl:mp_new', tone='neutral')} "
        f"{ActionBtn(t('cc_input_controllers.mp_dup_btn'), 'cc_ctrl:mp_dup', tone='neutral')} "
        f"{ActionBtn(t('cc_input_controllers.mp_ren_btn'), 'cc_ctrl:mp_ren', tone='neutral')} "
        f"{ActionBtn('x', 'cc_ctrl:mp_del', tone='danger_soft' if len(store['profiles']) > 1 else 'inactive')}"
    )
    comp_box.add_kv(
        t("cc_input_controllers.mp_active_prof_lbl"),
        CyclePill(mp["name"], "cc_ctrl:mp_next", active=True),
        right_markup=prof_acts,
    )
    tpl_btns = " ".join(
        ActionBtn(t(f"cc_input_controllers.mp_tpl_{tid}"), f"cc_ctrl:mp_tpl:{tid}", tone="neutral")
        for tid in ("fps", "platformer", "twinstick", "desktop")
    )
    comp_box.add_kv(
        t("cc_input_controllers.mp_tpl_lbl"),
        tpl_btns,
        right_markup=ActionBtn(t("cc_input_controllers.mp_tpl_blank"), "cc_ctrl:mp_tpl:blank", tone="danger_soft"),
    )

    def _build_prof_catalog(b: Box) -> None:
        p_rows = []
        for p_item in store["profiles"]:
            is_act_p = p_item["id"] == mp["id"]
            rule_s = p_item.get("match_app_id") or p_item.get("match_title") or (
                t("cc_input_controllers.mp_mode_fallback") if p_item.get("is_fallback") else "-"
            )
            ls_s = t(f"cc_input_controllers.mp_stick_{p_item['l_stick']['mode']}")
            rs_s = t(f"cc_input_controllers.mp_stick_{p_item['r_stick']['mode']}")
            grab_s = t("cc_input_controllers.mp_grab_short") if p_item.get("exclusive_grab", True) else t("cc_input_controllers.mp_pass_short")
            p_rows.append([
                InspectorCell(canvas.truncate_plain(p_item["name"], 22), f"cc_ctrl:mp_select_prof:{p_item['id']}", selected=is_act_p, is_alive=True),
                Txt(canvas.truncate_plain(rule_s, 20), role="val" if rule_s != "-" else "inactive"),
                Txt(canvas.truncate_plain(f"{ls_s} / {rs_s}", 18), role="schema"),
                Txt(f"{grab_s} · {p_item['mouse_sens']}", role="schema"),
            ])
        b.add_table(
            headers=[
                t("cc_input_controllers.mp_col_profile"),
                t("cc_input_controllers.mp_col_window"),
                t("cc_input_controllers.mp_col_sticks"),
                t("cc_input_controllers.mp_col_runtime"),
            ],
            widths=[24, 22, 18, "flex"],
            rows=p_rows,
            aligns=["left", "left", "left", "right"],
        )

    prof_summary = " · ".join(canvas.truncate_plain(p["name"], 18) for p in store["profiles"][:4])
    comp_box.add_collapsible(
        cc_panel,
        "ctrl_mp_profiles_list",
        t("cc_input_controllers.mp_profiles_list_title", p0=len(store["profiles"])),
        _build_prof_catalog,
        summary=prof_summary,
    )

    comp_box.add_section(t("cc_input_controllers.mp_sec_autoswitch"))
    auto_on = bool(store.get("auto_switch", True))
    fb_on = bool(mp.get("is_fallback", False))
    rule_txt = mp.get("match_app_id") or mp.get("match_title") or ""
    act_pills = " ".join([
        SelectPill(t("cc_input_controllers.mp_mode_auto_niri"), "cc_ctrl:mp_set_actmode:auto", selected=(auto_on and not fb_on)),
        SelectPill(t("cc_input_controllers.mp_mode_always"), "cc_ctrl:mp_set_actmode:always", selected=(not auto_on)),
        SelectPill(t("cc_input_controllers.mp_mode_fallback"), "cc_ctrl:mp_set_actmode:fallback", selected=(auto_on and fb_on)),
    ])
    comp_box.add_kv(t("cc_input_controllers.mp_actmode_lbl"), act_pills)

    if auto_on and not fb_on:
        rule_disp = (
            Txt(canvas.truncate_plain(rule_txt, 30), role="val")
            if rule_txt
            else Txt(t("cc_input_controllers.mp_no_rule_hint"), role="inactive")
        )
        rule_acts = (
            f"{InlineField('cc_ctrl_app', 'cc_ctrl:mp_inline_app', value=rule_txt, empty_label=t('cc_input_controllers.mp_set_rule_btn'), placeholder='steam_app_..', width=18, icon='\u270e', tone='neutral', selected=bool(rule_txt))} "
            f"{ActionBtn(t('cc_input_controllers.mp_pick_win_btn'), 'cc_ctrl:mp_pick_win', tone='neutral')}"
        )
        comp_box.add_kv(t("cc_input_controllers.mp_target_win_lbl"), rule_disp, right_markup=rule_acts)

    grab_excl = bool(mp.get("exclusive_grab", True))
    grab_pills = " ".join([
        SelectPill(t("cc_input_controllers.mp_grab_excl_full"), "cc_ctrl:mp_set_grab:1", selected=grab_excl),
        SelectPill(t("cc_input_controllers.mp_grab_pass_full"), "cc_ctrl:mp_set_grab:0", selected=not grab_excl),
    ])
    comp_box.add_kv(t("cc_input_controllers.mp_dev_access_lbl"), grab_pills)

    msens = int(mp.get("mouse_sens", 18))
    sl_w = 15 if canvas.inner_cols < 86 else 16
    sens_sl = Slider(msens, "cc_ctrl:mp_sens", min_val=2, max_val=60, width=sl_w, show_pct=False, step=4)
    turbo_hz = int(mp.get("turbo_hz", 12))
    comp_box.add_kv(
        t("cc_input_controllers.mp_mouse_look_lbl"),
        f"{sens_sl} {Txt(f'{msens}  (125 Hz)', role='val')}",
        right_markup=CyclePill(t("cc_input_controllers.mp_turbo_pill", p0=turbo_hz), "cc_ctrl:mp_turbo_next", active=(turbo_hz != 12)),
    )
    if m_run:
        win_lbl = rt.get("app_id") or rt.get("title") or "-"
        if rt.get("paused"):
            live_val = Txt(t("cc_input_controllers.mp_live_paused", p0=win_lbl), role="warn")
        elif rt.get("active_profile_name"):
            live_val = Txt(t("cc_input_controllers.mp_live_matched", p0=win_lbl, p1=rt["active_profile_name"]), role="ok")
        else:
            live_val = Txt(t("cc_input_controllers.mp_live_standby", p0=win_lbl), role="schema")
        comp_box.add_kv(t("cc_input_controllers.mp_live_lbl"), live_val)
    comp_box.add_row(" ")

    # --- CARD 2: Interactive Binding Blueprint ---
    sel = str(STATE.get("controllers", "mp_selected_ctrl", "south") or "south")
    if sel not in PAD_CONTROL_IDS:
        sel = "south"
    layer = str(STATE.get("controllers", "mp_layer", "base") or "base")
    if layer not in ("base", "shift"):
        layer = "base"
    is_cap = str(STATE.get("keyboard", "capture_mode", "") or "") == "ctrl_bind"

    mapped_ctrls: set[str] = set()
    if mp["l_stick"]["mode"] != "off":
        mapped_ctrls.add("l_stick")
    if mp["r_stick"]["mode"] != "off":
        mapped_ctrls.add("r_stick")
    for cid, entry in (mp.get("binds") or {}).items():
        if isinstance(entry, dict) and (entry.get("base") or entry.get("shift")):
            mapped_ctrls.add(cid)

    sel_lbl = t(f"cc_input_controllers.ctrl_{sel}")
    if sel in ("l_stick", "r_stick"):
        sm_cur = mp[sel]["mode"]
        cur_txt = t(f"cc_input_controllers.mp_stick_{sm_cur}")
        bp_foot_txt = f"{sel_lbl}: {cur_txt}"
    else:
        entry = (mp.get("binds") or {}).get(sel) or {}
        base_s = format_action_short(entry.get("base"), macros_by_id)
        shift_s = format_action_short(entry.get("shift"), macros_by_id)
        cur_act = entry.get(layer)
        cur_txt = format_action_short(cur_act, macros_by_id)
        bp_foot_txt = t("cc_input_controllers.mp_bp_footer", p0=sel_lbl, p1=base_s, p2=shift_s)

    bp_box = Box(
        t("cc_input_controllers.mp_bp_box_title", p0=canvas.truncate_plain(mp["name"], 24)),
        icon="\uf11b",
        actions=[Txt(f"{len(mapped_ctrls)}/{len(ALL_PAD_CONTROLS)}", role="schema")],
        highlighted=False,
    )
    bp_box.add_row(" ")
    if sel in ("l_stick", "r_stick"):
        bp_box.add_kv(
            t("cc_input_controllers.mp_sel_ctrl_lbl"),
            f"{Txt(sel_lbl, role='focus')}  {Txt('·', role='schema')}  {Txt(cur_txt, role='val' if sm_cur != 'off' else 'inactive')}",
            right_markup=ActionBtn(t("cc_input_controllers.mp_unbind_btn"), f"cc_ctrl:mp_unbind:{sel}", tone="danger_soft" if sm_cur != "off" else "inactive"),
        )
        stick_pills = " ".join(
            SelectPill(t(f"cc_input_controllers.mp_stick_long_{sm}"), f"cc_ctrl:mp_stick_mode:{sm}", selected=(sm_cur == sm))
            for sm in STICK_MODES
        )
        bp_box.add_kv(t("cc_input_controllers.mp_stick_mode_lbl"), stick_pills)
        if sm_cur in ("wasd", "arrows"):
            th_val = int(mp[sel].get("threshold", 35))
            th_sl = Slider(th_val, "cc_ctrl:mp_stick_thresh", min_val=15, max_val=80, width=14, show_pct=False, step=5)
            bp_box.add_kv(t("cc_input_controllers.mp_stick_thresh_lbl"), f"{th_sl} {Txt(f'{th_val}%', role='val')}")
        elif sm_cur == "mouse":
            inv_y = bool(mp["r_stick"].get("invert_y", False)) if sel == "r_stick" else False
            if sel == "r_stick":
                inv_pills = " ".join([
                    SelectPill(t("cc_input_controllers.mp_stick_norm_y"), "cc_ctrl:mp_stick_invy", selected=not inv_y),
                    SelectPill(t("cc_input_controllers.mp_stick_inv_y"), "cc_ctrl:mp_stick_invy", selected=inv_y),
                ])
                bp_box.add_kv(t("cc_input_controllers.mp_stick_axis_lbl"), inv_pills)
        elif sm_cur == "scroll":
            sc_val = int(mp.get("scroll_sens", 4))
            sc_sl = Slider(sc_val, "cc_ctrl:mp_scroll_sens", min_val=1, max_val=20, width=19, show_pct=False, step=1)
            bp_box.add_kv(t("cc_input_controllers.mp_scroll_speed_lbl"), f"{sc_sl} {Txt(str(sc_val), role='val')}")
    else:
        layer_summary = Txt(t("cc_input_controllers.mp_bp_summary", p0=base_s, p1=shift_s), role="schema")
        bp_box.add_kv(
            t("cc_input_controllers.mp_sel_ctrl_lbl"),
            f"{Txt(sel_lbl, role='focus')}   {layer_summary}",
            right_markup=ActionBtn(t("cc_input_controllers.mp_unbind_btn"), f"cc_ctrl:mp_unbind:{sel}", tone="danger_soft" if cur_act else "inactive"),
        )
        layer_pills = " ".join([
            SelectPill(t("cc_input_controllers.mp_layer_base_full"), "cc_ctrl:mp_layer:base", selected=(layer == "base")),
            SelectPill(t("cc_input_controllers.mp_layer_shift_full"), "cc_ctrl:mp_layer:shift", selected=(layer == "shift")),
        ])
        bp_box.add_kv(t("cc_input_controllers.mp_edit_layer_lbl"), layer_pills)

        bp_box.add_section(
            t("cc_input_controllers.mp_sec_composer", p0=sel_lbl, p1=t(f"cc_input_controllers.mp_layer_{layer}")),
            actions=[ActionBtn(t("cc_input_controllers.mp_macro_studio_btn"), "cc_ctrl:open_macros", tone="neutral")],
        )

        act_kind = str(STATE.get("controllers", "mp_act_kind", "") or "")
        if act_kind not in ("key", "mouse", "macro", "shift"):
            ck = cur_act.get("kind", "") if isinstance(cur_act, dict) else ""
            if ck in ("mouse_btn", "mouse_wheel"):
                act_kind = "mouse"
            elif ck == "macro":
                act_kind = "macro"
            elif ck == "shift":
                act_kind = "shift"
            else:
                act_kind = "key"

        kind_pills = " ".join([
            SelectPill(t("cc_input_controllers.mp_kind_key"), "cc_ctrl:mp_kind:key", selected=(act_kind == "key")),
            SelectPill(t("cc_input_controllers.mp_kind_mouse"), "cc_ctrl:mp_kind:mouse", selected=(act_kind == "mouse")),
            SelectPill(t("cc_input_controllers.mp_kind_macro"), "cc_ctrl:mp_kind:macro", selected=(act_kind == "macro")),
            SelectPill(t("cc_input_controllers.mp_kind_shift"), "cc_ctrl:mp_kind:shift", selected=(act_kind == "shift")),
        ])
        bp_box.add_kv(t("cc_input_controllers.mp_kind_lbl"), kind_pills)

        if act_kind == "key":
            cap_btn = (
                ActionBtn(t("cc_input_controllers.mp_cap_key_active"), "cc_ctrl:mp_cap_stop", tone="warn", modal_active=True)
                if is_cap
                else ActionBtn(t("cc_input_controllers.mp_cap_key_btn"), "cc_ctrl:mp_cap_key", tone="apply")
            )
            quick_keys = " ".join(
                ActionBtn(lbl, f"cc_ctrl:mp_quick_key:{kcode}", tone="neutral")
                for lbl, kcode in (("Space", "Space"), ("Enter", "Return"), ("Esc", "Escape"), ("Tab", "Tab"), ("Shift", "Shift_L"), ("Ctrl", "Ctrl_L"))
            )
            bp_box.add_kv(t("cc_input_controllers.mp_key_row_lbl"), cap_btn, right_markup=quick_keys)
            bm_cur = cur_act.get("mode", "hold") if isinstance(cur_act, dict) and cur_act.get("kind") == "key" else "hold"
            b_pills = " ".join(
                SelectPill(t(f"cc_input_controllers.mp_mode_long_{bm}"), f"cc_ctrl:mp_mode:{bm}", selected=(bm_cur == bm), style="secondary")
                for bm in BIND_MODES
            )
            bp_box.add_kv(t("cc_input_controllers.mp_behavior_lbl"), b_pills)
        elif act_kind == "mouse":
            cur_tgt = cur_act.get("target", "") if isinstance(cur_act, dict) else ""
            m_pills = " ".join(
                SelectPill(lbl, f"cc_ctrl:mp_bind_mouse:{tgt}", selected=(cur_tgt == tgt))
                for tgt, lbl in (
                    ("LMB", "LMB"),
                    ("RMB", "RMB"),
                    ("MMB", "MMB"),
                    ("Mouse4", "M4"),
                    ("Mouse5", "M5"),
                    ("WheelUp", "Whl\u2191"),
                    ("WheelDown", "Whl\u2193"),
                )
            )
            bp_box.add_kv(t("cc_input_controllers.mp_mouse_row_lbl"), m_pills)
            bm_cur = cur_act.get("mode", "hold") if isinstance(cur_act, dict) and cur_act.get("kind") in ("mouse_btn", "mouse_wheel") else "hold"
            b_pills = " ".join(
                SelectPill(t(f"cc_input_controllers.mp_mode_long_{bm}"), f"cc_ctrl:mp_mode:{bm}", selected=(bm_cur == bm), style="secondary")
                for bm in BIND_MODES
            )
            bp_box.add_kv(t("cc_input_controllers.mp_behavior_lbl"), b_pills)
        elif act_kind == "macro":
            cur_mid = cur_act.get("macro_id", "") if isinstance(cur_act, dict) and cur_act.get("kind") == "macro" else ""
            if macros:
                m_pills_list = [
                    SelectPill(canvas.truncate_plain(m["name"], 15), f"cc_ctrl:mp_bind_macro:{m['id']}", selected=(m["id"] == cur_mid))
                    for m in macros[:3]
                ]
                m_right = ActionBtn(t("cc_input_controllers.mp_macro_more"), "cc_ctrl:mp_cycle_macro", tone="neutral") if len(macros) > 3 else ""
                bp_box.add_kv(t("cc_input_controllers.mp_macro_lbl"), " ".join(m_pills_list), right_markup=m_right)
            else:
                bp_box.add_kv(t("cc_input_controllers.mp_macro_lbl"), Txt(t("cc_input_controllers.mp_macro_none"), role="inactive"))
            pmode = cur_act.get("mode", "once") if isinstance(cur_act, dict) and cur_act.get("kind") == "macro" else "once"
            m_modes = " ".join(
                SelectPill(t(f"cc_input_controllers.mp_macro_mode_{pm}"), f"cc_ctrl:mp_macro_mode:{pm}", selected=(pmode == pm), style="secondary")
                for pm in MACRO_PAD_MODES
            )
            bp_box.add_kv(t("cc_input_controllers.mp_macro_play_lbl"), m_modes)
        else:
            is_shift_now = isinstance(cur_act, dict) and cur_act.get("kind") == "shift"
            bp_box.add_kv(
                t("cc_input_controllers.mp_kind_shift"),
                Txt(t("cc_input_controllers.mp_shift_desc"), role="val" if is_shift_now else "schema"),
                right_markup=ActionBtn(t("cc_input_controllers.mp_apply_shift_btn"), "cc_ctrl:mp_bind_shift", tone="apply" if not is_shift_now else "inactive"),
            )

    bp_box.add_row(" ")
    bp_box.open_bottom = True
    bp_widget = ControllerBlueprintWidget(
        dev=dev,
        profile=prof_hw,
        widget_width=int(round(canvas.total_px)),
        fixed_height=max(222, int(round(canvas.char_h * 11.8))),
        inset_x=max(14, int(round(canvas.char_w * 2.0))),
        font_px=max(10.0, float(canvas.char_w) * 1.38),
        bind_mode=True,
        selected_ctrl=sel,
        mapped_ctrls=mapped_ctrls,
        on_select_ctrl=lambda cid: cc_panel.on_link(f"cc_ctrl:mp_sel:{cid}") if cc_panel else None,
        bind_footer_text=bp_foot_txt,
    )
    bp_foot = Box("", highlighted=False)
    bp_foot.open_top = True
    bp_foot.add_row(" ")

    # --- CARD 3: Profile Bindings Matrix (N) ---
    flt = str(STATE.get("controllers", "mp_filter", "all") or "all")
    if flt not in ("all", "sticks_triggers", "face_dpad", "shoulders_paddles"):
        flt = "all"
    filtered = [(cid, grp) for cid, grp in ALL_PAD_CONTROLS if flt == "all" or grp == flt]
    per_page = 9
    n_pages = max(1, (len(filtered) + per_page - 1) // per_page)
    page = max(0, min(n_pages - 1, int(STATE.get("controllers", "mp_page", 0) or 0)))
    slice_rows = filtered[page * per_page:(page + 1) * per_page]

    mat_box = Box(
        t("cc_input_controllers.mp_matrix_title", p0=len(mapped_ctrls)),
        icon="\uf03a",
        actions=[
            ActionBtn(t("cc_input_controllers.reset_btn"), "cc_ctrl:mp_reset", tone="warn" if is_dirty else "inactive"),
            ActionBtn(t("cc_input_controllers.mp_save_reload_btn"), "cc_ctrl:mp_save", tone="apply" if is_dirty else "inactive"),
        ],
        highlighted=False,
    )
    mat_box.add_row(" ")
    flt_pills = " ".join([
        SelectPill(t("cc_input_controllers.mp_flt_all"), "cc_ctrl:mp_flt:all", selected=(flt == "all")),
        SelectPill(t("cc_input_controllers.mp_flt_sticks"), "cc_ctrl:mp_flt:sticks_triggers", selected=(flt == "sticks_triggers")),
        SelectPill(t("cc_input_controllers.mp_flt_face"), "cc_ctrl:mp_flt:face_dpad", selected=(flt == "face_dpad")),
        SelectPill(t("cc_input_controllers.mp_flt_shoulders"), "cc_ctrl:mp_flt:shoulders_paddles", selected=(flt == "shoulders_paddles")),
    ])
    page_ctrl = ""
    if n_pages > 1:
        p0, p1 = page * per_page + 1, min(len(filtered), (page + 1) * per_page)
        prev_b = ActionBtn("\u2190", f"cc_ctrl:mp_page:{max(0, page - 1)}", tone="neutral" if page > 0 else "inactive")
        next_b = ActionBtn("\u2192", f"cc_ctrl:mp_page:{min(n_pages - 1, page + 1)}", tone="neutral" if page < n_pages - 1 else "inactive")
        page_ctrl = f"{prev_b} {Txt(t('cc_input_controllers.mp_page_range', p0=p0, p1=p1, p2=len(filtered)), role='schema')} {next_b}"
    mat_box.add_kv(t("cc_input_controllers.mp_filter_lbl"), flt_pills, right_markup=page_ctrl)

    t_rows = []
    for cid, _grp in slice_rows:
        c_lbl = t(f"cc_input_controllers.ctrl_{cid}")
        is_s = cid == sel
        if cid in ("l_stick", "r_stick"):
            sm = mp[cid]["mode"]
            b_str = t(f"cc_input_controllers.mp_stick_{sm}") if sm != "off" else "-"
            s_str = "-"
            m_str = "125Hz" if sm == "mouse" else ("8-Way" if sm in ("wasd", "arrows") else "-")
            is_bound = sm != "off"
            act_cell = f"{ActionBtn(t('cc_input_controllers.mp_unbind_btn'), f'cc_ctrl:mp_unbind:{cid}', tone='danger_soft' if is_bound else 'inactive')}"
        else:
            entry = (mp.get("binds") or {}).get(cid) or {}
            b_a = entry.get("base")
            s_a = entry.get("shift")
            b_str = format_action_short(b_a, macros_by_id)
            s_str = format_action_short(s_a, macros_by_id)
            raw_mode = (b_a or s_a or {}).get("mode", "") if isinstance(b_a or s_a, dict) else ""
            m_str = t(f"cc_input_controllers.mp_mode_{raw_mode}") if raw_mode in BIND_MODES else (
                t(f"cc_input_controllers.mp_macro_mode_{raw_mode}") if raw_mode in MACRO_PAD_MODES else "-"
            )
            is_bound = bool(b_a or s_a)
            act_cell = f"{ActionBtn(t('cc_input_controllers.mp_bind_short'), f'cc_ctrl:mp_row_bind:{cid}', tone='neutral')} {ActionBtn('x', f'cc_ctrl:mp_unbind:{cid}', tone='danger_soft' if is_bound else 'inactive')}"
        t_rows.append([
            InspectorCell(c_lbl, f"cc_ctrl:mp_sel:{cid}", selected=is_s, is_alive=is_bound),
            Txt(canvas.truncate_plain(b_str, 18), role="val" if b_str != "-" else "inactive"),
            Txt(canvas.truncate_plain(s_str, 15), role="val" if s_str != "-" else "inactive"),
            Txt(canvas.truncate_plain(m_str, 10), role="schema"),
            act_cell,
        ])
    mat_box.add_table(
        headers=[
            t("cc_input_controllers.mp_col_control"),
            t("cc_input_controllers.mp_col_base"),
            t("cc_input_controllers.mp_col_shift"),
            t("cc_input_controllers.mp_col_mode"),
            t("cc_input_controllers.mp_col_actions"),
        ],
        widths=[18, 19, 16, 11, "flex"],
        rows=t_rows,
        aligns=["left", "left", "left", "left", "right"],
    )
    mat_box.add_row(" ")

    return [comp_box, AsciiCard(bp_box, canvas, embedded_widget=bp_widget, footer_box_model=bp_foot), mat_box]


def build_cards(cc_panel, canvas: Canvas) -> list:
    cards = feedback_cards("controllers", "cc_input_controllers.status_title") + build_privilege_modal_card(cc_panel, canvas)
    devices = discover_controllers()
    key, dev = _active_key_and_device(devices)
    def_fam = dev["family"] if dev else "xbox"
    prof = _get_profile_draft(key, def_fam)
    saved_base = _norm_profile(_load_saved_profiles().get(key), def_fam)
    is_dirty = _is_profile_dirty(key, prof, def_fam)
    active_name = dev["name"] if dev else t("cc_input_controllers.preview_device_name")
    view_mode = str(STATE.get("controllers", "view_mode", "hardware") or "hardware")
    if view_mode not in ("hardware", "mapping"):
        view_mode = "hardware"
    mapper_running, _ = is_pad_mapper_running()

    # =====================================================================
    # BOX 1 — Connected & Paired Controllers + Workbench Switch
    # =====================================================================
    dev_box = Box(
        t("cc_input_controllers.box_devices_title", p0=len(devices)),
        icon="\uf11b",
        actions=[
            ActionBtn(t("cc_input_controllers.rescan_btn"), "cc_ctrl:rescan", tone="neutral"),
            ActionBtn(t("cc_input_controllers.bt_pair_btn"), "cc_ctrl:open_bt", tone="neutral"),
        ],
        highlighted=False,
    )
    dev_box.add_row(" ")
    if not devices:
        dev_box.add_row(f"  {Txt(t('cc_input_controllers.no_ctrl_detected'), role='val')}")
        dev_box.add_row(f"  {Txt(t('cc_input_controllers.no_ctrl_preview_hint'), role='schema')}")
    else:
        d_rows = []
        for d in devices[:6]:
            bus_lbl = t(f"cc_input_controllers.bus_{d['bus']}")
            if d["battery"] is not None:
                bat_role = "fault" if d["battery"] < 15 else ("warn" if d["battery"] < 35 else "ok")
                bat_cell = Txt(f"{d['battery']}%", role=bat_role)
            elif d["bus"] == "usb":
                bat_cell = Txt(t("cc_input_controllers.bat_wired"), role="schema")
            else:
                bat_cell = Txt(t("cc_input_controllers.bat_na"), role="inactive")

            caps = []
            if d["has_rumble"]:
                caps.append(t("cc_input_controllers.cap_rumble"))
            if d.get("has_paddles"):
                caps.append(t("cc_input_controllers.cap_paddles"))
            if d["has_gyro"]:
                caps.append(t("cc_input_controllers.cap_gyro"))
            if d["leds"]:
                caps.append(t("cc_input_controllers.cap_led"))
            caps_txt = " · ".join(caps) if caps else t("cc_input_controllers.cap_basic")

            if d["bt_mac"]:
                act_cell = (
                    ActionBtn(t("cc_input_controllers.disconnect_btn"), f"cc_ctrl:bt_disc:{d['bt_mac']}", tone="warn")
                    if d["connected"]
                    else ActionBtn(t("cc_input_controllers.connect_btn"), f"cc_ctrl:bt_conn:{d['bt_mac']}", tone="apply")
                )
            else:
                act_cell = StateBadge(d["connected"], t("cc_input_controllers.connected_lbl"), t("cc_input_controllers.paired_lbl"))

            d_rows.append([
                InspectorCell(canvas.truncate_plain(d["name"], 24), f"cc_ctrl:select:{d['id']}", selected=(d["id"] == key), is_alive=d["connected"]),
                Txt(bus_lbl, role="schema"),
                bat_cell,
                Txt(canvas.truncate_plain(caps_txt, 20), role="schema"),
                act_cell,
            ])
        dev_box.add_table(
            headers=[
                t("cc_input_controllers.col_controller"),
                t("cc_input_controllers.col_bus"),
                t("cc_input_controllers.col_battery"),
                t("cc_input_controllers.col_caps"),
                t("cc_input_controllers.col_actions"),
            ],
            widths=[26, 8, 9, 20, "flex"],
            rows=d_rows,
            aligns=["left", "left", "left", "left", "right"],
        )
    dev_box.add_row(" ")
    cards.append(dev_box)

    # =====================================================================
    # BOX 1.5 — Cardinal Controller Workbench Switcher (1.5x TabBar)
    # =====================================================================
    wb_box = Box(
        t("cc_input_controllers.workbench_sec"),
        icon="\uf0e8",
        actions=[
            StateBadge(
                mapper_running,
                t("cc_input_controllers.mapper_active"),
                t("cc_input_controllers.mapper_standby"),
            ),
        ],
        highlighted=False,
    )
    wb_box.add_tabs(
        view_mode,
        [
            ("hardware", f"\uf11b  {t('cc_input_controllers.wb_hardware')}"),
            ("mapping", f"\uf11c  {t('cc_input_controllers.wb_mapping')}"),
        ],
        uri_prefix="cc_ctrl:mp_view",
        min_tab_cols=40,
    )
    wb_hint = t(
        "cc_input_controllers.wb_desc_mapping"
        if view_mode == "mapping"
        else "cc_input_controllers.wb_desc_hardware"
    )
    wb_box.add_row(f"  {Txt(wb_hint, role='schema')}")
    wb_box.add_row(" ")
    cards.append(wb_box)

    if view_mode == "mapping":
        cards.extend(_build_mapping_cards(cc_panel, canvas, dev, prof))
        return cards

    # =====================================================================
    # BOX 2 — Interactive Controller Test Deck (Pure-Cairo 3-Zone Blueprint)
    # =====================================================================
    hdr_badges = []
    if dev and dev["connected"] and dev["readable"]:
        hdr_badges.append(StateBadge(True, t("cc_input_controllers.badge_live")))
    elif dev and dev["connected"] and not dev["readable"]:
        hdr_badges.append(Badge(t("cc_input_controllers.badge_no_perm"), style="warn"))
    else:
        hdr_badges.append(StateBadge(False, "", t("cc_input_controllers.badge_preview")))
    if dev and dev.get("battery") is not None:
        hdr_badges.append(Txt(f"{dev['battery']}%", role="ok" if dev["battery"] >= 35 else "warn"))

    deck_box = Box(
        t("cc_input_controllers.test_deck_title", p0=canvas.truncate_plain(active_name, 26)),
        icon="\uf11b",
        actions=hdr_badges,
        highlighted=False,
    )
    deck_box.add_row(" ")
    d_lay = prof["layout"] != saved_base["layout"]
    cur_meta = _MODEL_BY_ID.get(prof["layout"], _CONTROLLER_MODELS[0])
    model_cycle = CyclePill(
        t(f"cc_input_controllers.layout_{prof['layout']}"),
        "cc_ctrl:layout_next",
        active=(prof["layout"] != def_fam),
        dirty=d_lay,
    )
    model_feats = Txt(t(f"cc_input_controllers.{cur_meta['feat_key']}"), role="schema")
    deck_box.add_kv(
        t("cc_input_controllers.layout_lbl"),
        f"{model_cycle}  {model_feats}",
        right_markup=ActionBtn(t("cc_input_controllers.zero_center_btn"), "cc_ctrl:zero_center", tone="neutral"),
    )

    sl_w = 14 if canvas.inner_cols < 86 else 16
    rumble_pct = prof["rumble_pct"]
    rumble_sl = Slider(rumble_pct, "cc_ctrl:rumble_pct", min_val=0, max_val=100, width=sl_w, show_pct=False, step=10)
    rumble_btns = (
        f"{ActionBtn(t('cc_input_controllers.rumble_left_btn'), 'cc_ctrl:rumble_test:left', tone='neutral')} "
        f"{ActionBtn(t('cc_input_controllers.rumble_right_btn'), 'cc_ctrl:rumble_test:right', tone='neutral')} "
        f"{ActionBtn(t('cc_input_controllers.rumble_dual_btn'), 'cc_ctrl:rumble_test:dual', tone='apply')}"
    )
    deck_box.add_kv(
        t("cc_input_controllers.haptics_lbl"),
        f"{rumble_sl} {Txt(f'{rumble_pct}%', role='val')}",
        right_markup=rumble_btns,
    )

    def _build_model_catalog(b: Box) -> None:
        m_rows = []
        for m in _CONTROLLER_MODELS:
            mid = m["id"]
            is_sel = prof["layout"] == mid
            m_rows.append([
                InspectorCell(
                    t(f"cc_input_controllers.model_full_{mid}"),
                    f"cc_ctrl:layout:{mid}",
                    selected=is_sel,
                    is_alive=True,
                ),
                Txt(m["brand"], role="schema"),
                Txt(t(f"cc_input_controllers.{m['arch_key']}"), role="schema"),
                Txt(t(f"cc_input_controllers.{m['feat_key']}"), role="val" if is_sel else "schema"),
            ])
        b.add_table(
            headers=[
                t("cc_input_controllers.col_model"),
                t("cc_input_controllers.col_brand"),
                t("cc_input_controllers.col_arch"),
                t("cc_input_controllers.col_features"),
            ],
            widths=[26, 11, 13, "flex"],
            rows=m_rows,
            aligns=["left", "left", "left", "left"],
        )

    catalog_summary = " · ".join(t(f"cc_input_controllers.layout_{m['id']}") for m in _CONTROLLER_MODELS)
    deck_box.add_collapsible(
        cc_panel,
        "ctrl_model_catalog",
        t("cc_input_controllers.model_catalog_title", p0=len(_CONTROLLER_MODELS)),
        _build_model_catalog,
        summary=catalog_summary,
    )
    deck_box.add_row(" ")
    deck_box.open_bottom = True

    bp_widget = ControllerBlueprintWidget(
        dev=dev,
        profile=prof,
        widget_width=int(round(canvas.total_px)),
        fixed_height=max(226, int(round(canvas.char_h * 12.2))),
        inset_x=max(14, int(round(canvas.char_w * 2.0))),
        font_px=max(10.0, float(canvas.char_w) * 1.38),
    )
    deck_foot = Box("", highlighted=False)
    deck_foot.open_top = True
    deck_foot.add_row(" ")
    cards.append(AsciiCard(deck_box, canvas, embedded_widget=bp_widget, footer_box_model=deck_foot))

    # =====================================================================
    # BOX 2.5 — Conditional Xbox Elite Hardware & Paddles Deck
    # =====================================================================
    if prof["layout"] == "xbox_elite":
        hw_slot = _LIVE_INPUT.get("hw_profile")
        elite_dirty = any(
            prof[k] != saved_base[k]
            for k in ("elite_slot", "p1", "p2", "p3", "p4", "trigger_lock", "stick_calc")
        )
        slot_tag = (
            Txt(t("cc_input_controllers.elite_hw_slot", p0=hw_slot), role="ok")
            if hw_slot is not None
            else Txt(t(f"cc_input_controllers.elite_slot_{prof['elite_slot']}"), role="schema")
        )
        elite_box = Box(
            t("cc_input_controllers.elite_box_title"),
            icon="\uf0e7",
            actions=[
                slot_tag,
                ActionBtn(t("cc_input_controllers.elite_reset_btn"), "cc_ctrl:reset_elite", tone="warn" if elite_dirty else "inactive"),
            ],
            highlighted=False,
        )
        elite_box.add_row(" ")
        d_slot = prof["elite_slot"] != saved_base["elite_slot"]
        slot_pills = " ".join(
            SelectPill(
                t(f"cc_input_controllers.elite_slot_{sl_i}"),
                f"cc_ctrl:elite_slot:{sl_i}",
                selected=(prof["elite_slot"] == sl_i),
                dirty=d_slot,
            )
            for sl_i in _ELITE_SLOTS
        )
        elite_box.add_kv(t("cc_input_controllers.elite_slot_lbl"), slot_pills)

        paddle_pills = []
        for pk in _PADDLE_KEYS:
            p_act = prof[pk]
            act_lbl = t(f"cc_input_controllers.paddle_act_{p_act}")
            paddle_pills.append(
                CyclePill(
                    f"{pk.upper()}: {act_lbl}",
                    f"cc_ctrl:paddle:{pk}",
                    active=(p_act != "none"),
                    dirty=(p_act != saved_base[pk]),
                )
            )
        elite_box.add_kv(t("cc_input_controllers.elite_paddles_lbl"), " ".join(paddle_pills))

        d_tl = prof["trigger_lock"] != saved_base["trigger_lock"]
        lock_pills = " ".join(
            SelectPill(
                t(f"cc_input_controllers.tlock_{tl_id}"),
                f"cc_ctrl:trigger_lock:{tl_id}",
                selected=(prof["trigger_lock"] == tl_id),
                dirty=d_tl,
            )
            for tl_id, _ in _TRIGGER_LOCKS
        )
        elite_box.add_kv(t("cc_input_controllers.elite_tlock_lbl"), lock_pills)

        d_sc = prof["stick_calc"] != saved_base["stick_calc"]
        calc_pills = " ".join(
            SelectPill(
                t(f"cc_input_controllers.scalc_{sc_id}"),
                f"cc_ctrl:stick_calc:{sc_id}",
                selected=(prof["stick_calc"] == sc_id),
                dirty=d_sc,
            )
            for sc_id in _STICK_CALCS
        )
        elite_box.add_kv(t("cc_input_controllers.elite_scalc_lbl"), calc_pills)
        elite_box.add_row(" ")
        cards.append(elite_box)

    # =====================================================================
    # BOX 3 — Calibration, Response Curve & Hardware (Pure-Cairo Graphs)
    # =====================================================================
    cal_box = Box(
        t("cc_input_controllers.cal_box_title"),
        icon="\uf1de",
        actions=[
            ActionBtn(t("cc_input_controllers.reset_btn"), "cc_ctrl:reset_profile", tone="warn" if is_dirty else "inactive"),
            ActionBtn(t("cc_input_controllers.save_profile_btn"), "cc_ctrl:save_profile", tone="apply" if is_dirty else "inactive"),
        ],
        highlighted=False,
    )
    cal_box.add_row(" ")
    d_crv = prof["curve"] != saved_base["curve"]
    cal_box.add_kv(
        t("cc_input_controllers.curve_lbl"),
        " ".join(
            SelectPill(t(f"cc_input_controllers.curve_{cid}"), f"cc_ctrl:curve:{cid}", selected=(prof["curve"] == cid), dirty=d_crv)
            for cid in _CURVES
        ),
    )

    dz_val = prof["inner_dz"]
    ot_val = prof["outer_th"]
    tc_val = prof["trigger_cut"]
    cal_sl_w = 18 if canvas.inner_cols < 86 else 20
    cal_box.add_kv(
        t("cc_input_controllers.inner_dz_lbl"),
        f"{Slider(dz_val, 'cc_ctrl:inner_dz', min_val=0, max_val=40, width=cal_sl_w, show_pct=False, step=2)} {Txt(f'{dz_val}%', role='val')}",
    )
    cal_box.add_kv(
        t("cc_input_controllers.outer_th_lbl"),
        f"{Slider(ot_val, 'cc_ctrl:outer_th', min_val=60, max_val=100, width=cal_sl_w, show_pct=False, step=5)} {Txt(f'{ot_val}%', role='val')}",
    )
    cal_box.add_kv(
        t("cc_input_controllers.trigger_cut_lbl"),
        f"{Slider(tc_val, 'cc_ctrl:trigger_cut', min_val=10, max_val=100, width=cal_sl_w, show_pct=False, step=5)} {Txt(f'{tc_val}%', role='val')}",
    )

    d_led = prof["led_mode"] != saved_base["led_mode"]
    led_pills = " ".join(
        SelectPill(t(f"cc_input_controllers.led_{lm}"), f"cc_ctrl:led:{lm}", selected=(prof["led_mode"] == lm), dirty=d_led, style="secondary")
        for lm in _LED_MODES
    )
    led_hint = Txt(
        t("cc_input_controllers.led_hw_ready") if (dev and dev.get("leds")) else t("cc_input_controllers.led_profile_only"),
        role="ok" if (dev and dev.get("leds")) else "schema",
    )
    cal_box.add_kv(t("cc_input_controllers.led_lbl"), led_pills, right_markup=led_hint)

    def _build_adv(b: Box) -> None:
        ax_pills = " ".join([
            SelectPill(t("cc_input_controllers.inv_ly"), "cc_ctrl:toggle:inv_ly", selected=bool(prof["inv_ly"]), dirty=(prof["inv_ly"] != saved_base["inv_ly"]), style="secondary"),
            SelectPill(t("cc_input_controllers.inv_ry"), "cc_ctrl:toggle:inv_ry", selected=bool(prof["inv_ry"]), dirty=(prof["inv_ry"] != saved_base["inv_ry"]), style="secondary"),
            SelectPill(t("cc_input_controllers.swap_sticks"), "cc_ctrl:toggle:swap_sticks", selected=bool(prof["swap_sticks"]), dirty=(prof["swap_sticks"] != saved_base["swap_sticks"]), style="secondary"),
        ])
        b.add_kv(t("cc_input_controllers.axes_lbl"), ax_pills)
        g_pills = " ".join(
            SelectPill(t(f"cc_input_controllers.gyro_{gm}"), f"cc_ctrl:gyro:{gm}", selected=(prof["gyro_mode"] == gm), dirty=(prof["gyro_mode"] != saved_base["gyro_mode"]), style="secondary")
            for gm in _GYRO_MODES
        )
        b.add_kv(t("cc_input_controllers.gyro_lbl"), g_pills)
        has_joydev = os.path.isdir("/sys/module/joydev")
        uinput_st = "MISSING" if not os.path.exists("/dev/uinput") else ("OK" if os.access("/dev/uinput", os.W_OK) else "LOCKED")
        stack_txt = f"joydev: {'OK' if has_joydev else 'OFF'} · uinput: {uinput_st} · evdev: {len(glob.glob('/dev/input/event*'))}"
        b.add_kv(
            t("cc_input_controllers.stack_lbl"),
            Txt(stack_txt, role="schema"),
            right_markup=ActionBtn(t("cc_input_controllers.export_btn"), "cc_ctrl:export_json", tone="neutral"),
        )

    cal_box.add_collapsible(
        cc_panel,
        "ctrl_adv_stack",
        t("cc_input_controllers.adv_sec_title"),
        _build_adv,
        summary=t("cc_input_controllers.adv_summary"),
    )
    cal_box.add_row(" ")
    cal_box.open_bottom = True

    curve_widget = ResponseCurveWidget(
        profile=prof,
        widget_width=int(round(canvas.total_px)),
        fixed_height=max(116, int(round(canvas.char_h * 6.3))),
        inset_x=max(14, int(round(canvas.char_w * 2.0))),
        font_px=max(10.0, float(canvas.char_w) * 1.38),
    )
    cal_foot = Box("", highlighted=False)
    cal_foot.open_top = True
    cal_foot.add_row(" ")
    cards.append(AsciiCard(cal_box, canvas, embedded_widget=curve_widget, footer_box_model=cal_foot))
    return cards
