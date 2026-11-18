#!/usr/bin/env python3
from core.completion import catalog_xkb_layouts, rank_candidates
from core.i18n import get_active_language, t
from core.state import STATE
from ui.canvas import Canvas
from ui.kit import ActionBtn, Badge, Box, Divider, InlineField, SelectPill, Slider, StateBadge, Txt, get_active_inline_entry
from panels.cc_engine_input import (
    NIRI_CONFIG_FILE,
    _collect_keyboard_state,
    _feedback_box,
    get_live_keyboard_layouts,
)


def _build_keyboard_standard(cc_panel, canvas: Canvas, st: dict) -> list:
    cards = []
    cap_mode = str(STATE.get("keyboard", "capture_mode", "") or "")
    insp = STATE.get("keyboard", "inspected_key", {}) or {}
    test_str = str(STATE.get("keyboard", "test_typing", "") or "")

    box = Box(
        t("cc_keyboard.keyboard_repeat_behavior_niri"),
        icon="\uf11c",
        highlighted=False,
    )
    box.add_row(" ")
    rate_sl = Slider(st["repeat_rate"], "cc_kbd:rate", min_val=10, max_val=100, width=24, show_pct=False, step_buttons=True, step=5)
    del_sl = Slider(st["repeat_delay"], "cc_kbd:delay", min_val=150, max_val=1000, width=24, show_pct=False, step_buttons=True, step=50)
    box.add_kv(t("cc_keyboard.repeat_rate"), f"{rate_sl}  {Txt(str(st['repeat_rate']) + ' char/s', role='val')}")
    box.add_kv(t("cc_keyboard.repeat_delay"), f"{del_sl}  {Txt(str(st['repeat_delay']) + ' ms', role='val')}")
    box.add_kv(t("cc_keyboard.numlock_on_boot"), f"{SelectPill(t('common.on'), 'cc_kbd:numlock:on', selected=st['numlock'])}  {SelectPill(t('common.off'), 'cc_kbd:numlock:off', selected=not st['numlock'])}")
    box.add_kv(t("cc_keyboard.track_layout"), f"{SelectPill(t('cc_keyboard.global'), 'cc_kbd:track:global', selected=(st['track_layout'] == 'global'))}  {SelectPill(t('cc_keyboard.per_window'), 'cc_kbd:track:window', selected=(st['track_layout'] == 'window'))}")
    box.add_kv(
        t("cc_keyboard.typing_test"),
        InlineField(
            "cc_kbd_test_inline",
            "cc_kbd:test_inline",
            value=test_str,
            empty_label=t("cc_keyboard.typing_test_btn"),
            placeholder=t("cc_keyboard.type_here_to_test_key_repeat_rate"),
            width=28,
            icon="\uf11c",
            tone="neutral",
        ),
    )

    def _build_adv(b: Box):
        b.add_row(
            f"  {Txt(t('cc_keyboard.presets_gaming_fast_50_200ms_vs_st'), role='schema')}",
            right_markup=f"{ActionBtn(t('cc_keyboard.gaming_fast'), 'cc_kbd:preset:fast', tone='apply')} {ActionBtn(t('cc_keyboard.default'), 'cc_kbd:preset:std', tone='neutral')}",
        )
        b.add_row(
            f"  {Txt(t('cc_keyboard.config_path'), role='schema')} {Txt(NIRI_CONFIG_FILE, role='val')}",
            right_markup=ActionBtn(t("cc_keyboard.validate_kdl"), "cc_kbd:validate", tone="neutral"),
            requires=["niri"],
        )

    box.add_collapsible(cc_panel, "kbd_adv", t("cc_keyboard.advanced_parameters_presets"), _build_adv)
    box.add_row(" ")
    cards.append(box)

    is_insp = (cap_mode == "inspector")
    cap_btn = (
        ActionBtn(t("cc_keyboard.stop_capture"), "cc_kbd:cap_stop", tone="danger_soft", modal_active=True)
        if is_insp
        else ActionBtn(t("cc_keyboard.capture_keys"), "cc_kbd:cap_start:inspector", tone="apply")
    )
    cap_box = Box(
        t("cc_keyboard.live_in_gui_key_inspector"),
        icon="\uf002",
        actions=[cap_btn, ActionBtn(t("cc_keyboard.macros"), "cc:subtab:macros", tone="neutral")],
        highlighted=False,
    )
    cap_box.add_row(" ")
    if is_insp:
        cap_box.add_row(f"  {Badge(t('cc_keyboard.listening_for_keystrokes_press_any'), style='warn')}")
    if insp.get("combo"):
        cap_box.add_kv(
            t("cc_keyboard.niri_shortcut_combo"),
            Txt(insp["combo"], role="focus"),
            right_markup=ActionBtn(t("cc_keyboard.use_in_shortcut_builder"), "cc_kbd:use_inspected", tone="apply"),
        )
        cap_box.add_kv(t("cc_keyboard.xkb_keysym_hex"), f"{Txt(insp.get('keysym', ''), role='focus')}  {Txt('(' + insp.get('keyval_hex', '') + ')', role='schema')}")
        cap_box.add_kv(t("cc_keyboard.linux_evdev_mods"), f"{Txt('code ' + str(insp.get('evdev', 0)), role='val')}  |  {Txt(t('cc_keyboard.modifiers_lbl') + ': ' + str(insp.get('mods') or '-'), role='val')}")
    else:
        cap_box.add_row(f"  {Txt(t('cc_keyboard.click_capture_keys_above_and_press'), role='schema')}")

    def _build_ext_probes(b: Box):
        b.add_kv(t("cc_keyboard.external_probes"), f"{ActionBtn('wev (Term)', 'cc_kbd:launch_wev', tone='neutral')} {ActionBtn('libinput (Term)', 'cc_kbd:launch_libinput', tone='neutral')} {ActionBtn('xkbcli', 'cc_kbd:launch_xkbcli', tone='neutral')}")
        b.add_table(
            headers=[t("cc_keyboard.special_key"), t("cc_keyboard.xkb_keysym"), t("cc_keyboard.suggested_niri_action")],
            widths=[18, 26, "flex"],
            rows=[
                [Txt(t("cc_keyboard.vol_up_down"), role="focus"), Txt("XF86AudioRaise/LowerVolume", role="val"), Txt("spawn wpctl/pactl", role="schema")],
                [Txt(t("cc_keyboard.mic_mute"), role="focus"), Txt("XF86AudioMicMute", role="val"), Txt("pactl set-source-mute", role="schema")],
                [Txt(t("cc_keyboard.screenshot"), role="focus"), Txt(t("cc_keyboard.print"), role="val"), Txt("nc-ctl popup capture", role="schema")],
                [Txt(t("cc_keyboard.calc_menu"), role="focus"), Txt("XF86Calculator / Menu", role="val"), Txt(t("cc_keyboard.spawn_custom_app"), role="schema")],
            ],
        )

    cap_box.add_collapsible(cc_panel, "kbd_keysyms", t("cc_keyboard.external_cli_probes_media_keysym_r"), _build_ext_probes)
    cap_box.add_row(" ")
    cards.append(cap_box)
    return cards


def _build_layout_standard(cc_panel, canvas: Canvas, st: dict) -> list:
    live = get_live_keyboard_layouts()
    items = live["items"]
    codes = live["codes"]
    cur_idx = live["current_idx"]
    cur_code = live["current"]["code"]
    cur_var = st["variant"]
    cur_opts = [x.strip() for x in st["options"].split(",") if x.strip()]
    cur_lang = get_active_language()

    box = Box(
        t("cc_keyboard.active_keyboard_layouts_config_kdl"),
        icon="\uf0ac",
        actions=[
            Txt(t("cc_keyboard.current", p0=cur_code.upper()), role="schema"),
            ActionBtn(t("cc_keyboard.cycle"), "cc_kbd:cycle_layout", tone="neutral"),
        ],
        highlighted=False,
    )
    box.add_row(" ")

    act_rows = []
    for it in items:
        idx = it["idx"]
        code = it["code"]
        is_now = (idx == cur_idx)
        up_b = ActionBtn("\u2191", f"cc_kbd:move_layout:{idx}:-1", tone="neutral") if idx > 0 else "   "
        dn_b = ActionBtn("\u2193", f"cc_kbd:move_layout:{idx}:1", tone="neutral") if idx < len(items) - 1 else "   "
        rem_b = ActionBtn("x", f"cc_kbd:rem_layout:{code}", tone="danger_soft") if len(items) > 1 else ""
        use_b = "" if is_now else ActionBtn(t("cc_keyboard.use_now"), f"cc_kbd:switch_layout:{idx}", tone="apply")
        act_rows.append([
            Txt(f"{idx + 1:02d}", role="schema"),
            Txt(code.upper(), role="val"),
            Txt(canvas.truncate_plain(it["name"], 26), role="focus" if is_now else "val"),
            StateBadge(is_now, t("cc_keyboard.active_2"), t("cc_keyboard.standby"), on_tone="ok"),
            f"{use_b} {up_b}{dn_b} {rem_b}".strip(),
        ])
    box.add_table(
        headers=["#", t("cc_keyboard.code"), t("cc_keyboard.keyboard_layout"), t("cc_keyboard.state"), t("cc_keyboard.actions")],
        widths=[4, 8, "flex", 12, 20],
        rows=act_rows,
        aligns=["left", "left", "left", "left", "right"],
    )

    q_str = str(STATE.get("keyboard", "layout_query", "") or "").strip()
    is_searching = (get_active_inline_entry() == "cc_kbd_xkb_picker")
    if q_str or is_searching:
        ranked = rank_candidates(catalog_xkb_layouts(exclude=set(codes)), q_str, limit=9)
        res_pills = [(canvas.truncate_plain(lbl, 21), f"cc_kbd:add_layout:{val}") for val, lbl in ranked]
    else:
        res_pills = []
    box.add_section(t("cc_keyboard.add_new_layout_live_xkb_search"))
    box.add_search_box(
        t("cc_keyboard.filter_xkb"),
        query=q_str,
        placeholder=t("cc_keyboard.type_code_or_language_es_us_german"),
        trigger_uri="cc_kbd:layout_search_open",
        results=res_pills,
        clear_uri="cc_kbd:layout_search_clear",
        active=is_searching,
        max_pills_per_row=3,
        width=40,
        entry_id="cc_kbd_xkb_picker",
        show_empty=bool(q_str),
    )

    vars_list = [("", t("cc_keyboard.var_default")), ("nodeadkeys", "NoDeadKeys"), ("intl", "Intl"), ("dvorak", "Dvorak"), ("colemak", "Colemak")]
    box.add_section(t("cc_keyboard.locale_modifiers"))
    box.add_kv(t("cc_keyboard.variant"), " ".join(SelectPill(lbl, f"cc_kbd:variant:{v}", selected=(cur_var == v)) for v, lbl in vars_list))
    box.add_kv(t("cc_keyboard.nc_ui_language"), f"{SelectPill('English (en)', 'cc_kbd:ui_lang:en', selected=(cur_lang == 'en'))}  {SelectPill('Italiano (it)', 'cc_kbd:ui_lang:it', selected=(cur_lang == 'it'))}")

    def _build_xkb_opts(b: Box):
        opts = [
            ("grp:alt_shift_toggle", t("cc_keyboard.opt_alt_shift")),
            ("grp:win_space_toggle", t("cc_keyboard.opt_win_space")),
            ("caps:escape", t("cc_keyboard.opt_caps_esc")),
            ("caps:swapescape", t("cc_keyboard.opt_swap_caps")),
            ("compose:ralt", t("cc_keyboard.opt_compose_ralt")),
        ]
        for ok_code, ok_lbl in opts:
            is_on = ok_code in cur_opts
            b.add_kv(ok_lbl, SelectPill(t("common.on") if is_on else t("common.off"), f"cc_kbd:toggle_opt:{ok_code}", selected=is_on))
        b.add_kv(t("cc_keyboard.waybar_plugin"), Txt(t("cc_keyboard.use_custom_kb_layout_in_waybar_lay"), role="schema"), right_markup=ActionBtn(t("cc_keyboard.waybar_plugins"), "ui:open_settings:waybar", tone="neutral"))

    box.add_collapsible(cc_panel, "kbd_xkb_opts", t("cc_keyboard.xkb_switch_modifier_options"), _build_xkb_opts)
    box.add_row(" ")
    return [box]


def build_cards(cc_panel, canvas: Canvas) -> list:
    ks = _collect_keyboard_state()
    cards = _feedback_box()
    cards.extend(_build_keyboard_standard(cc_panel, canvas, ks))
    cards.extend(_build_layout_standard(cc_panel, canvas, ks))
    return cards
