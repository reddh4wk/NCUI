#!/usr/bin/env python3
import os
from core.i18n import t
from core.privileges import build_privilege_modal_card
from core.state import STATE
from ui.canvas import Canvas
from ui.kit import (
    ActionBtn,
    Badge,
    Box,
    InlineField,
    InlineFilter,
    InspectorCell,
    ProgressBar,
    SelectPill,
    Slider,
    StateBadge,
    Txt,
)
from panels.cc_engine_input import (
    _RUNTIME_REC,
    _calc_macro_total_ms,
    _collect_keyboard_state,
    _detect_macro_media_kind,
    _feedback_box,
    _find_saved_macro,
    _get_macro_draft,
    _is_macro_engine_running,
    _load_macros,
    is_macro_draft_dirty,
    parse_youtube_url,
)


def _build_macros_standard(cc_panel, canvas: Canvas, st: dict) -> list:
    cards = _feedback_box() + build_privilege_modal_card(cc_panel, canvas)
    cap_mode = str(STATE.get("keyboard", "capture_mode", "") or "")
    cap_prev = str(STATE.get("keyboard", "capture_preview", "") or "")
    draft = _get_macro_draft()
    macros = _load_macros()
    saved = _find_saved_macro(draft["id"], macros)
    is_dirty = is_macro_draft_dirty(draft, macros)
    uinput_ok = os.access("/dev/uinput", os.W_OK)

    is_rec = (cap_mode == "macro_rec")
    is_trig_cap = (cap_mode == "macro_trigger")
    is_running = _is_macro_engine_running()
    sandbox_out = str(STATE.get("keyboard", "sandbox_output", "") or "")
    steps = _RUNTIME_REC["steps"] if is_rec else draft["steps"]
    bound_combos = {b["combo"] for b in st.get("binds", [])}

    # =====================================================================
    # BOX 1 — Transport & Engine Status
    # =====================================================================
    rec_btn = (
        ActionBtn(t("cc_keyboard.stop_f12"), "cc_kbd:macro_rec_stop", tone="danger_soft", modal_active=True)
        if is_rec
        else ActionBtn(t("cc_keyboard.rec_start"), "cc_kbd:macro_rec_start", tone="apply")
    )
    test_btn = ActionBtn(t("cc_keyboard.test_sandbox"), "cc_kbd:macro_test_sandbox", tone="neutral")
    run_btn = ActionBtn(t("cc_keyboard.run_engine"), "cc_kbd:macro_run_engine", tone="apply")
    eng_badge = StateBadge(is_running, t("cc_keyboard.engine_active"), t("cc_keyboard.engine_standby"))
    sync_badge = Badge(t("cc_keyboard.status_unsaved"), style="warn") if is_dirty else StateBadge(True, t("cc_keyboard.status_synced"))
    tr_box = Box(
        t("cc_keyboard.box_transport_title"),
        icon="\uf0e7",
        actions=[
            StateBadge(uinput_ok, t("cc_keyboard.uinput_ready"), t("cc_keyboard.uinput_locked"), on_tone="ok" if uinput_ok else "warn"),
            sync_badge,
            eng_badge,
        ],
        highlighted=False,
    )
    tr_box.add_row(" ")
    if not uinput_ok:
        tr_box.add_kv(
            t("cc_keyboard.uinput_driver"),
            Badge(t("cc_keyboard.kernel_uinput_permission_required"), style="warn"),
            right_markup=ActionBtn(t("cc_keyboard.unlock_dev_uinput"), "cc_kbd:unlock_uinput", tone="apply"),
        )
    if is_rec:
        tr_box.add_kv(
            t("cc_keyboard.rec_status"),
            Badge(t("cc_keyboard.recording_keyboard_activity_type_y"), style="crit"),
        )

    stop_right = ActionBtn(t("cc_keyboard.panic_stop_btn"), "cc_kbd:macro_panic_stop", tone="danger_soft") if is_running else ""
    tr_box.add_kv(t("cc_keyboard.transport_ctrl_lbl"), f"{rec_btn} {test_btn} {run_btn}", right_markup=stop_right)

    d_panic = saved is not None and (
        draft["panic_key"] != saved["panic_key"] or draft["countdown_s"] != saved["countdown_s"]
    )
    panic_pills = " ".join(
        SelectPill(
            t("cc_keyboard.panic_key_prefix", p0=pk),
            f"cc_kbd:macro_panic_key:{pk}",
            selected=(draft["panic_key"] == pk),
            dirty=d_panic,
        )
        for pk in ("F12", "Pause")
    )
    cd_pills = " ".join(
        SelectPill(
            t("cc_keyboard.countdown_off") if cd == 0 else f"{cd}s",
            f"cc_kbd:macro_countdown:{cd}",
            selected=(draft["countdown_s"] == cd),
            dirty=d_panic,
            style="secondary",
        )
        for cd in (0, 2, 3, 5)
    )
    tr_box.add_kv(t("cc_keyboard.safety_panic_lbl"), f"{panic_pills}  {cd_pills}")

    sb_disp = (
        Txt(canvas.truncate_plain(sandbox_out, 46), role="val")
        if sandbox_out
        else Txt(t("cc_keyboard.sandbox_empty_hint"), role="inactive")
    )
    sb_acts = InlineField(
        "cc_mac_sandbox",
        "cc_kbd:sandbox_inline",
        value="",
        empty_label=t("cc_keyboard.sandbox_open_pad"),
        placeholder=t("cc_keyboard.sandbox_pad_prompt"),
        width=22,
        icon="\u270e",
        tone="neutral",
    )
    if sandbox_out:
        sb_acts = f"{sb_acts} {ActionBtn(t('cc_keyboard.clear_x'), 'cc_kbd:sandbox_clear', tone='danger_soft')}"
    tr_box.add_kv(t("cc_keyboard.sandbox_pad_lbl"), sb_disp, right_markup=sb_acts)
    tr_box.add_row(" ")
    cards.append(tr_box)

    # =====================================================================
    # BOX 2 — Macro Configuration, Video/YouTube Scene & Keystroke Timeline
    # =====================================================================
    cfg_box = Box(
        t("cc_keyboard.box_config_title"),
        icon="\uf0ae",
        actions=[
            ActionBtn(t("cc_keyboard.new_empty"), "cc_kbd:macro_new", tone="neutral"),
            ActionBtn(t("cc_keyboard.duplicate"), "cc_kbd:macro_dup", tone="neutral"),
        ],
        highlighted=False,
    )
    cfg_box.add_row(" ")
    cfg_box.add_kv(
        t("cc_keyboard.macro_name"),
        Txt(draft["name"], role="focus"),
        right_markup=InlineField(
            "cc_mac_name",
            "cc_kbd:macro_inline_name",
            value="",
            empty_label=t("cc_keyboard.rename"),
            placeholder=t("cc_keyboard.macro_name_3"),
            width=18,
            icon="\u270e",
            tone="neutral",
        ),
    )

    if is_trig_cap:
        trig_val = Badge(cap_prev or t("cc_keyboard.press_trigger_hint"), style="warn")
    elif draft["combo"]:
        is_bound_niri = draft["combo"] in bound_combos
        trig_val = (
            f"{Txt(draft['combo'], role='focus')} "
            f"{StateBadge(is_bound_niri, t('cc_keyboard.trigger_bound_ok'), t('cc_keyboard.trigger_draft_only'), on_tone='ok' if is_bound_niri else 'warn')}"
        )
    else:
        trig_val = Txt(t("cc_keyboard.unbound"), role="inactive")

    cap_trig_btn = (
        ActionBtn(t("cc_keyboard.cancel"), "cc_kbd:cap_stop", tone="warn", modal_active=True)
        if is_trig_cap
        else ActionBtn(t("cc_keyboard.capture_trigger"), "cc_kbd:cap_start:macro_trigger", tone="neutral")
    )
    trig_acts = (
        f"{cap_trig_btn} "
        f"{InlineField('cc_mac_combo', 'cc_kbd:macro_inline_combo', value='', empty_label=t('cc_keyboard.manual'), placeholder='Mod+Shift+G', width=18, icon='\u270e', tone='neutral')}"
    )
    if draft["combo"]:
        trig_acts = f"{trig_acts} {ActionBtn(t('cc_keyboard.bind_niri'), 'cc_kbd:macro_bind_draft', tone='apply')}"
    cfg_box.add_kv(t("cc_keyboard.trigger_shortcut"), trig_val, right_markup=trig_acts)

    t_mode = draft["timing_mode"]
    d_tmode = saved is not None and t_mode != saved["timing_mode"]
    cfg_box.add_kv(
        t("cc_keyboard.timing_mode"),
        " ".join(
            SelectPill(t(f"cc_keyboard.tmode_{k}"), f"cc_kbd:macro_tmode:{k}", selected=(t_mode == k), dirty=d_tmode)
            for k in ("recorded", "fixed", "zero")
        ),
    )
    if t_mode == "fixed":
        d_fixed = saved is not None and draft["fixed_delay_ms"] != saved["fixed_delay_ms"]
        cfg_box.add_kv(
            t("cc_keyboard.fixed_step_delay"),
            " ".join(
                SelectPill(f"{v}ms", f"cc_kbd:macro_fixedms:{v}", selected=(draft["fixed_delay_ms"] == v), dirty=d_fixed)
                for v in (15, 25, 50, 100, 200)
            ),
        )

    d_speed = saved is not None and abs(draft["speed_mult"] - saved["speed_mult"]) > 0.01
    cfg_box.add_kv(
        t("cc_keyboard.playback_speed"),
        " ".join(
            SelectPill(f"{s}x", f"cc_kbd:macro_speed:{s}", selected=(abs(draft["speed_mult"] - s) < 0.05), dirty=d_speed)
            for s in (0.5, 1.0, 2.0, 5.0)
        ),
    )

    rmode, rcnt = draft["repeat_mode"], draft["repeat_count"]
    d_rep = saved is not None and (rmode != saved["repeat_mode"] or rcnt != saved["repeat_count"])
    rep_pills = " ".join([
        SelectPill(t("cc_keyboard.once_1x"), "cc_kbd:macro_rep:once:1", selected=(rmode == "once"), dirty=d_rep),
        SelectPill("3x", "cc_kbd:macro_rep:count:3", selected=(rmode == "count" and rcnt == 3), dirty=d_rep),
        SelectPill("10x", "cc_kbd:macro_rep:count:10", selected=(rmode == "count" and rcnt == 10), dirty=d_rep),
        InlineField(
            "cc_mac_rep",
            "cc_kbd:macro_inline_rep",
            value=f"{rcnt}x" if (rmode == "count" and rcnt not in (3, 10)) else "",
            empty_label="N..",
            placeholder="5",
            width=7,
            icon="",
            tone="neutral",
            selected=(rmode == "count" and rcnt not in (3, 10)),
            dirty=d_rep,
        ),
        SelectPill(t("cc_keyboard.loop_toggle"), "cc_kbd:macro_rep:toggle_loop:1", selected=(rmode == "toggle_loop"), dirty=d_rep),
    ])
    cfg_box.add_kv(t("cc_keyboard.repeat_mode"), rep_pills)

    # --- Multimedia / Video & YouTube Scene ---
    cfg_box.add_section(t("cc_keyboard.sec_media_scene"))
    media_src = draft["media_source"]
    mkind = _detect_macro_media_kind(media_src)
    if media_src:
        yt_info = parse_youtube_url(media_src)
        disp_m = yt_info["canonical_url"] if yt_info["ok"] else (os.path.basename(media_src.rstrip("/")) or media_src)
        kind_b = f" {Txt(f'({mkind})', role='schema')}" if mkind else ""
        src_val = f"{Txt(canvas.truncate_plain(disp_m, 26), role='focus')}{kind_b}"
    else:
        src_val = Txt(t("cc_keyboard.media_none_hint"), role="inactive")

    src_btns = f"{ActionBtn(t('cc_keyboard.set_media_btn'), 'cc_kbd:macro_media_prompt', tone='neutral')} {ActionBtn(t('cc_keyboard.paste_yt_btn'), 'cc_kbd:macro_paste_yt', tone='neutral')}"
    if media_src:
        src_btns = f"{src_btns} {ActionBtn(t('cc_keyboard.clear_x'), 'cc_kbd:macro_media_clear', tone='danger_soft')}"
    cfg_box.add_kv(t("cc_keyboard.media_source_lbl"), src_val, right_markup=src_btns)

    if STATE.get("keyboard", "macro_yt_dl", False):
        cfg_box.add_kv("", Txt(t("cc_keyboard.media_dl_active"), role="attention"))

    mdeliv = draft["media_delivery"]
    d_deliv = saved is not None and mdeliv != saved["media_delivery"]
    cfg_box.add_kv(
        t("cc_keyboard.media_deliv_lbl"),
        " ".join([
            SelectPill(t("cc_keyboard.media_dl_cache"), "cc_kbd:macro_media_deliv:download", selected=(mdeliv == "download"), dirty=d_deliv, style="secondary"),
            SelectPill(t("cc_keyboard.media_direct_stream"), "cc_kbd:macro_media_deliv:stream", selected=(mdeliv == "stream"), dirty=d_deliv, style="secondary"),
        ]),
    )

    mloop = draft["media_loop"]
    d_mloop = saved is not None and mloop != saved["media_loop"]
    cfg_box.add_kv(
        t("cc_keyboard.media_loop_lbl"),
        " ".join([
            SelectPill(t("cc_keyboard.media_loop_inf"), "cc_kbd:macro_media_loop:loop", selected=(mloop == "loop"), dirty=d_mloop, style="secondary"),
            SelectPill(t("cc_keyboard.media_play_once"), "cc_kbd:macro_media_loop:once", selected=(mloop == "once"), dirty=d_mloop, style="secondary"),
        ]),
    )

    mmute = bool(draft["media_mute"])
    mvol = int(draft["media_volume"])
    d_mmute = saved is not None and mmute != saved["media_mute"]
    cfg_box.add_kv(
        t("cc_keyboard.media_audio_lbl"),
        " ".join([
            SelectPill(t("cc_keyboard.media_mute"), "cc_kbd:macro_media_mute:1", selected=mmute, dirty=d_mmute, style="secondary"),
            SelectPill(t("cc_keyboard.media_audio_on"), "cc_kbd:macro_media_mute:0", selected=(not mmute), dirty=d_mmute, style="secondary"),
        ]),
    )
    if not mmute:
        vol_sl = Slider(mvol, "cc_kbd:macro_media_vol", min_val=0, max_val=100, width=10, show_pct=True, step_buttons=True, step=10)
        vol_presets = " ".join(
            SelectPill(f"{v}%", f"cc_kbd:macro_media_vol:{v}", selected=(mvol == v), style="secondary")
            for v in (25, 50, 75, 100)
        )
        cfg_box.add_kv(t("cc_keyboard.media_vol_lbl"), f"{vol_sl}  {vol_presets}")

    if media_src:
        cfg_box.add_kv("", Txt(t("cc_keyboard.media_gpu_warn"), role="warn"))

    # --- Keystroke Timeline ---
    tot_ms = _calc_macro_total_ms(steps, draft["timing_mode"], draft["fixed_delay_ms"], draft["speed_mult"])
    tl_actions = [
        InlineField("cc_mac_step_add", "cc_kbd:step_inline_add", value="", empty_label=t("cc_keyboard.key_2"), placeholder="W, 50, 80", width=14, icon="", tone="neutral"),
        ActionBtn(t("cc_keyboard.norm_35ms"), "cc_kbd:macro_norm", tone="neutral"),
        ActionBtn(t("cc_keyboard.clear"), "cc_kbd:macro_clear_steps", tone="danger_soft"),
    ]
    cfg_box.add_section(t("cc_keyboard.keystroke_timeline_steps_ms", p0=len(steps), p1=tot_ms), actions=tl_actions)

    if not steps:
        cfg_box.add_row(f"  {Txt(t('cc_keyboard.no_recorded_steps_yet_click_rec_st'), role='inactive')}")
    else:
        max_hold = max((int(s.get("hold_ms", 50)) for s in steps), default=100) or 100
        tl_rows = []
        for idx, s in enumerate(steps[:16]):
            up_b = ActionBtn("\u2191", f"cc_kbd:step_move:{idx}:-1", tone="neutral") if idx > 0 else "   "
            dn_b = ActionBtn("\u2193", f"cc_kbd:step_move:{idx}:1", tone="neutral") if idx < len(steps) - 1 else "   "
            hold_pct = min(100.0, max(5.0, (float(s.get("hold_ms", 50)) / float(max(max_hold, 120))) * 100.0))
            tl_rows.append([
                Txt(f"{idx + 1:02d}", role="schema"),
                Txt(str(s.get("key", "?"))[:10], role="focus"),
                Txt(str(s.get("keycode", 0)), role="schema"),
                Txt(f"+{int(s.get('delay_ms', 0))} ms", role="val"),
                Txt(f"{int(s.get('hold_ms', 50))} ms", role="val"),
                ProgressBar(hold_pct, width=8, color_by_threshold=False),
                f"{InlineField(f'cc_mac_step_{idx}', f'cc_kbd:step_inline_edit:{idx}', value='', empty_label='\u270e', placeholder='50, 80', width=10, icon='', tone='neutral')} {up_b}{dn_b} {ActionBtn(t('cc_keyboard.clear_x'), f'cc_kbd:step_del:{idx}', tone='danger_soft')}",
            ])
        cfg_box.add_table(
            headers=[
                t("cc_keyboard.col_index"),
                t("cc_keyboard.col_key_action"),
                t("cc_keyboard.col_evdev"),
                t("cc_keyboard.wait_delay"),
                t("cc_keyboard.hold_ms"),
                t("cc_keyboard.profile"),
                t("cc_keyboard.edit_order"),
            ],
            widths=[4, 14, 7, 11, 10, 12, "flex"],
            rows=tl_rows,
            aligns=["left", "left", "left", "left", "left", "left", "right"],
        )

    # --- Ergonomic Commit Footer (Bottom Right) ---
    cfg_box.add_section(t("cc_keyboard.sec_commit_bar"))
    sync_msg = Txt(t("cc_keyboard.commit_unsaved_msg"), role="warn") if is_dirty else Txt(t("cc_keyboard.commit_synced_msg"), role="ok")
    commit_btns = (
        f"{ActionBtn(t('cc_keyboard.reset_btn'), 'cc_kbd:macro_reset_draft', tone='warn' if is_dirty else 'inactive')} "
        f"{ActionBtn(t('cc_keyboard.save_macro_btn'), 'cc_kbd:macro_save_draft', tone='apply' if is_dirty else 'inactive')}"
    )
    cfg_box.add_row(f"  {sync_msg}", right_markup=commit_btns)
    cfg_box.add_row(" ")
    cards.append(cfg_box)

    # =====================================================================
    # BOX 3 — Saved Library & Export
    # =====================================================================
    lib_flt = str(STATE.get("keyboard", "macro_lib_filter", "all") or "all")
    lib_q = str(STATE.get("keyboard", "macro_lib_query", "") or "").strip()
    ql = lib_q.lower()

    filtered = []
    for real_idx, m in enumerate(macros):
        if lib_flt == "bound" and not m["combo"]:
            continue
        if lib_flt == "media" and not m["media_source"]:
            continue
        if ql and ql not in m["name"].lower() and ql not in m["combo"].lower() and ql not in m["media_source"].lower():
            continue
        filtered.append((real_idx, m))

    flt_pills = " ".join([
        SelectPill(t("cc_keyboard.lib_flt_all"), "cc_kbd:lib_flt:all", selected=(lib_flt == "all"), style="secondary"),
        SelectPill(t("cc_keyboard.lib_flt_bound"), "cc_kbd:lib_flt:bound", selected=(lib_flt == "bound"), style="secondary"),
        SelectPill(t("cc_keyboard.lib_flt_media"), "cc_kbd:lib_flt:media", selected=(lib_flt == "media"), style="secondary"),
    ])
    lib_box = Box(
        t("cc_keyboard.saved_gaming_macros_library", p0=len(filtered)),
        icon="\uf0c9",
        actions=[ActionBtn(t("cc_keyboard.export_macros"), "cc_kbd:macro_export", tone="neutral")],
        highlighted=False,
    )
    lib_box.add_row(" ")
    lib_box.add_kv(
        t("cc_keyboard.lib_search_lbl"),
        InlineFilter(
            "cc_kbd_lib_flt",
            lib_q,
            t("cc_keyboard.search_macros_ph"),
            "cc_kbd:lib_search_open",
            clear_uri="cc_kbd:lib_search_clear" if lib_q else "",
            width=26,
            active=bool(lib_q),
        ),
        right_markup=flt_pills,
    )
    lib_box.add_row(" ")

    if not macros:
        lib_box.add_row(f"  {Txt(t('cc_keyboard.no_macros_saved_yet'), role='inactive')}")
    elif not filtered:
        lib_box.add_row(f"  {Txt(t('cc_keyboard.no_matching_macros'), role='inactive')}")
    else:
        l_rows = []
        for real_idx, m in filtered[:12]:
            s_cnt = len(m["steps"])
            tot_m = _calc_macro_total_ms(m["steps"], m["timing_mode"], m["fixed_delay_ms"], m["speed_mult"])
            info_s = t("cc_keyboard.steps_unit", p0=s_cnt, p1=tot_m) if s_cnt else canvas.truncate_plain(m["cmd"] or t("cc_keyboard.cmd_lbl"), 15)
            if m["media_source"]:
                mk = _detect_macro_media_kind(m["media_source"]) or t("cc_keyboard.lib_flt_media")
                info_s = f"{info_s} · {mk}"
            rep_s = "∞" if m["repeat_mode"] == "toggle_loop" else (f"{m['repeat_count']}x" if m["repeat_mode"] == "count" else "1x")
            mode_lbl = {"recorded": "rec", "fixed": "fix", "zero": "0ms"}.get(m["timing_mode"], m["timing_mode"][:4])
            if m["combo"]:
                is_live_b = m["combo"] in bound_combos
                trig_s = StateBadge(True, m["combo"], t("cc_keyboard.unbound"), on_tone="ok" if is_live_b else "warn")
            else:
                trig_s = StateBadge(False, "", t("cc_keyboard.unbound"))
            l_rows.append([
                InspectorCell(canvas.truncate_plain(m["name"], 14), f"cc_kbd:macro_load:{real_idx}", selected=(m["id"] == draft["id"])),
                Txt(canvas.truncate_plain(info_s, 17), role="schema"),
                Txt(f"{mode_lbl} {m['speed_mult']}x {rep_s}", role="val"),
                trig_s,
                f"{ActionBtn('\u25b6', f'cc_kbd:run_macro:{real_idx}', tone='apply')} {ActionBtn(t('cc_keyboard.bind'), f'cc_kbd:bind_macro:{real_idx}', tone='neutral')} {ActionBtn(t('cc_keyboard.clear_x'), f'cc_kbd:del_macro:{real_idx}', tone='danger_soft')}",
            ])
        lib_box.add_table(
            headers=[
                t("cc_keyboard.macro_name_2"),
                t("cc_keyboard.steps_cmd"),
                t("cc_keyboard.mode_rep"),
                t("cc_keyboard.trigger"),
                t("cc_keyboard.actions"),
            ],
            widths=[16, 17, 11, 13, "flex"],
            rows=l_rows,
            aligns=["left", "left", "left", "left", "right"],
        )
    lib_box.add_row(" ")
    cards.append(lib_box)
    return cards


def build_cards(cc_panel, canvas: Canvas) -> list:
    st = _collect_keyboard_state()
    return _build_macros_standard(cc_panel, canvas, st)
