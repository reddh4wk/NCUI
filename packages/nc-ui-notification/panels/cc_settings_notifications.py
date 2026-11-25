#!/usr/bin/env python3
from core.completion import catalog_window_app_ids, rank_candidates
from core.config import get_sys_notif_opacity_pct
from core.i18n import t
from core.state import STATE
from ui.canvas import Canvas
from ui.kit import ActionBtn, Box, InlineField, SelectPill, Slider, StateBadge, Txt, get_active_inline_entry
from panels.cc_engine_notifications import (
    _collected_apps,
    _get_app_icon,
    _get_max_notifications,
    _load_rules,
    _notif_feedback_cards,
    get_active_notifs,
    get_app_click_config,
    get_archive_unique_apps,
    load_notifs,
    resolve_app_launch_cmd,
)

_TIMEOUT_PRESETS = (3, 5, 8, 12, 0)
_TOP_POS_DEFS = (
    ("top-left", "cc_notifications.pos_top_left", "\u2196"),
    ("top-center", "cc_notifications.pos_top_center", "\u2191"),
    ("top-right", "cc_notifications.pos_top_right", "\u2197"),
)
_BOT_POS_DEFS = (
    ("bottom-left", "cc_notifications.pos_bot_left", "\u2199"),
    ("bottom-center", "cc_notifications.pos_bot_center", "\u2193"),
    ("bottom-right", "cc_notifications.pos_bot_right", "\u2198"),
)
_EFF_DEFS = (
    ("slide", "cc_notifications.eff_slide", "\uf061"),
    ("fade", "cc_notifications.eff_fade", "\uf042"),
    ("slide_fade", "cc_notifications.eff_slide_fade", "\uf0d0"),
    ("none", "cc_notifications.eff_none", "\uf0e7"),
)
_URG_DEFS = (
    ("low", "cc_notifications.urg_low", "\uf063"),
    ("normal", "cc_notifications.urg_normal", "\uf0f3"),
    ("critical", "cc_notifications.urg_critical", "\uf071"),
)


def _build_delivery_box(rules: dict, max_n: int, active: list, total: list) -> Box:
    b = Box(
        t("cc_notifications.system_notifications_archive"),
        icon="\uf0f3",
        actions=[
            StateBadge(rules["dnd"], t("cc_notifications.dnd_on"), t("cc_notifications.dnd_off"), on_tone="warn"),
            ActionBtn(f"\uf1d8 {t('cc_notifications.test_notify')}", "cc_notif:test", tone="apply"),
            ActionBtn(f"\uf1f8 {t('cc_notifications.clear_archive')}", "cc_notif:clear", tone="danger_soft"),
        ],
        highlighted=False,
    )
    dnd_on = SelectPill(f"\uf1f6 {t('common.on')}", "cc_notif:dnd:1", selected=rules["dnd"])
    dnd_off = SelectPill(f"\uf0f3 {t('common.off')}", "cc_notif:dnd:0", selected=not rules["dnd"])
    b.add_kv(t("cc_notifications.do_not_disturb"), f"{dnd_on} {dnd_off}", icon="\uf1f6")
    q_presets = ("22:00-07:00", "23:00-07:00", "00:00-08:00")
    q_custom = bool(rules["quiet_enabled"] and rules["quiet_window"] not in q_presets)
    q_state = StateBadge(rules["quiet_enabled"], rules["quiet_window"], t("cc_notifications.disabled"))
    q_acts = (
        f"{SelectPill(t('cc_notifications.toggle'), 'cc_notif:quiet_toggle', selected=rules['quiet_enabled'])} "
        f"{InlineField('cc_notif_quiet', 'cc_notif:inline_quiet', value=rules['quiet_window'] if q_custom else '', empty_label=t('cc_notifications.set'), placeholder='22:00-07:00', width=13, icon='\u270e', tone='neutral', selected=q_custom)}"
    )
    b.add_kv(t("cc_notifications.quiet_hours"), q_state, right_markup=q_acts, icon="\uf186")
    b.add_kv(
        t("cc_notifications.quiet_presets"),
        " ".join(SelectPill(w, f"cc_notif:quiet_win:{w}", selected=(rules["quiet_window"] == w)) for w in q_presets),
        icon="\uf017",
    )
    m_pills = " ".join(SelectPill(str(n), f"cc_notif:max:{n}", selected=(max_n == n)) for n in (25, 50, 100, 200))
    b.add_kv(
        t("cc_notifications.history_limit"),
        m_pills,
        right_markup=Txt(t("cc_notifications.active_total_short", p0=len(active), p1=len(total)), role="schema"),
        icon="\uf1da",
    )
    grp_active = bool(rules.get("group_by_app", False))
    p_grp = SelectPill(f"\uf03a {t('cc_notifications.grouped')}", "cc_notif:group_app:1", selected=grp_active)
    p_chr = SelectPill(f"\uf017 {t('cc_notifications.chronological')}", "cc_notif:group_app:0", selected=not grp_active)
    b.add_kv(t("cc_notifications.group_by_application"), f"{p_grp} {p_chr}", icon="\uf03a")
    return b


def _build_popup_box(rules: dict) -> Box:
    p_on = bool(rules.get("popup_enabled", False))
    p_to = int(rules.get("popup_timeout", 5))
    p_pos = str(rules.get("popup_position", "top-right"))
    p_eff = str(rules.get("popup_effect", "slide"))

    box = Box(
        t("cc_notifications.popup_box_title"),
        icon="\uf27a",
        actions=[
            StateBadge(p_on, t("cc_notifications.popup_active"), t("cc_notifications.popup_off"), on_tone="ok"),
            ActionBtn(f"\uf06e {t('cc_notifications.popup_preview')}", "cc_notif:popup_preview", tone="neutral"),
        ],
        highlighted=False,
    )
    en_on = SelectPill(f"\uf0f3 {t('common.on')}", "cc_notif:popup_en:1", selected=p_on)
    en_off = SelectPill(f"\uf1f6 {t('common.off')}", "cc_notif:popup_en:0", selected=not p_on)
    box.add_kv(t("cc_notifications.popup_spawning"), f"{en_on} {en_off}", icon="\uf0f3")

    p_custom = p_to not in _TIMEOUT_PRESETS
    to_pills = []
    for sec in _TIMEOUT_PRESETS:
        lbl = f"\uf08d {t('cc_notifications.popup_sticky')}" if sec == 0 else f"{sec}s"
        to_pills.append(SelectPill(lbl, f"cc_notif:popup_timeout:{sec}", selected=(p_to == sec)))
    to_pills.append(
        InlineField(
            "cc_notif_popup_timeout",
            "cc_notif:inline_popup_timeout",
            value=f"{p_to}s" if p_custom else "",
            empty_label=t("cc_notifications.popup_custom"),
            placeholder="5",
            width=5,
            icon="\u270e",
            tone="neutral",
            selected=p_custom,
        )
    )
    box.add_kv(t("cc_notifications.popup_duration"), " ".join(to_pills), icon="\uf252")

    top_pills = " ".join(SelectPill(f"{ic} {t(lk)}", f"cc_notif:popup_pos:{key}", selected=(p_pos == key)) for key, lk, ic in _TOP_POS_DEFS)
    box.add_kv(t("cc_notifications.popup_top_anchor"), top_pills, icon="\uf05b")

    bot_pills = " ".join(SelectPill(f"{ic} {t(lk)}", f"cc_notif:popup_pos:{key}", selected=(p_pos == key)) for key, lk, ic in _BOT_POS_DEFS)
    box.add_kv(t("cc_notifications.popup_bot_anchor"), bot_pills, icon="\uf05b")

    eff_pills = " ".join(SelectPill(f"{ic} {t(lk)}", f"cc_notif:popup_effect:{key}", selected=(p_eff == key)) for key, lk, ic in _EFF_DEFS)
    box.add_kv(t("cc_notifications.popup_effect"), eff_pills, icon="\uf0d0")

    op_sl = Slider(get_sys_notif_opacity_pct(), "cc_notif:popup_op", min_val=0, max_val=100, width=22, show_pct=True, step=5)
    box.add_kv(t("cc_notifications.popup_opacity"), op_sl, icon="\uf042")
    return box


def _build_rules_box(cc_panel, canvas: Canvas, rules: dict, unique_apps: list) -> Box:
    muted = {a.lower() for a in rules.get("muted_apps", [])}
    apps = _collected_apps(rules)
    urg = str(rules.get("min_urgency", "low")).lower()

    box = Box(t("cc_notifications.per_app_urgency_notification_filte"), icon="\uf1de", highlighted=False)
    u_pills = " ".join(SelectPill(f"{ic} {t(lk)}", f"cc_notif:urg:{u}", selected=(urg == u)) for u, lk, ic in _URG_DEFS)
    box.add_kv(t("cc_notifications.minimum_urgency"), u_pills, icon="\uf071")

    app_q = str(STATE.get("settings", "notif_app_query", "") or "").strip()
    app_pick_active = (get_active_inline_entry() == "cc_notif_app_picker")
    if app_q or app_pick_active:
        cands = [it for it in catalog_window_app_ids(extra_apps=apps) if it[0].lower() not in muted]
        ranked = rank_candidates(cands, app_q, limit=6)
        app_pills = [(canvas.truncate_plain(lb, 22), f"cc_notif:mute_app:{val.lower()}") for val, lb in ranked]
    else:
        app_pills = []
    box.add_inline_picker(
        t("cc_notifications.mute_app"),
        query=app_q,
        placeholder=t("cc_notifications.app_name_to_suppress_e_g_discord"),
        trigger_uri="cc_notif:app_pick_open",
        results=app_pills,
        clear_uri="cc_notif:app_pick_clear" if app_q else "",
        active=app_pick_active,
        max_pills_per_row=3,
        width=28,
        entry_id="cc_notif_app_picker",
        show_empty=bool(app_q),
    )

    if cc_panel.get_state("notif_app_rules", None) is None:
        cc_panel.set_state("notif_app_rules", True)

    def _fill_app_rules(bx: Box):
        allow_lbl = f"\uf00c {t('cc_notifications.allow')}"
        mute_lbl = f"\uf05e {t('cc_notifications.mute')}"
        rows = []
        for a in apps:
            is_muted = a in muted
            app_lbl = f"{_get_app_icon(a)} {a}"
            rows.append([
                Txt(canvas.truncate_plain(app_lbl, 22), role="focus"),
                StateBadge(not is_muted, t("cc_notifications.allowed"), t("cc_notifications.suppressed"), on_tone="ok"),
                f"{SelectPill(allow_lbl, f'cc_notif:allow_app:{a}', selected=not is_muted)} {SelectPill(mute_lbl, f'cc_notif:mute_app:{a}', selected=is_muted)}",
            ])
        bx.add_table(
            headers=[t("cc_notifications.application"), t("cc_notifications.policy"), t("cc_notifications.action")],
            widths=[22, 16, "flex"],
            rows=rows,
            aligns=["left", "left", "right"],
        )

    box.add_collapsible(cc_panel, "notif_app_rules", t("cc_notifications.app_rules_count", p0=len(apps)), _fill_app_rules)

    if cc_panel.get_state("notif_click_apps", None) is None:
        cc_panel.set_state("notif_click_apps", True)

    def _fill_archive_apps(bx: Box):
        if not unique_apps:
            bx.add_row(f"  {Txt(t('cc_notifications.no_archive_apps'), role='schema')}")
            return
        custom_lbl = t("cc_notifications.custom_action")
        click_lbl = f"\uf245 {t('cc_notifications.clickable')}"
        rows = []
        for app in unique_apps:
            cfg = get_app_click_config(app, rules)
            app_lbl = f"{_get_app_icon(app)} {app}"
            if cfg:
                custom_cmd = cfg.get("cmd", "")
                if custom_cmd:
                    target_m = Txt(t("cc_notifications.custom_launch", p0=custom_cmd), role="val")
                else:
                    auto_cmd = resolve_app_launch_cmd(app) or t("cc_notifications.no_cmd_found")
                    target_m = Txt(t("cc_notifications.auto_launch", p0=auto_cmd), role="val")
                acts_m = (
                    f"{ActionBtn(custom_lbl, f'cc_notif:prompt_click_cmd:{app}', tone='neutral')} "
                    f"{SelectPill(click_lbl, f'cc_notif:click_toggle:{app}', selected=True)}"
                )
            else:
                target_m = Txt(t("cc_notifications.not_clickable"), role="inactive")
                acts_m = SelectPill(click_lbl, f"cc_notif:click_toggle:{app}", selected=False)
            rows.append([Txt(canvas.truncate_plain(app_lbl, 20), role="focus"), target_m, acts_m])

        col_w = max(31, len(click_lbl) + len(custom_lbl) + 8)
        bx.add_table(
            headers=[t("cc_notifications.application"), t("cc_notifications.launch_target"), t("cc_notifications.action")],
            widths=[20, "flex", col_w],
            rows=rows,
            aligns=["left", "left", "right"],
        )

    box.add_collapsible(cc_panel, "notif_click_apps", t("cc_notifications.archive_apps_section", p0=len(unique_apps)), _fill_archive_apps)
    return box


def _build_archive_box(canvas: Canvas, rules: dict, active: list, max_n: int) -> Box:
    box = Box(
        t("cc_notifications.recent_archive_box", p0=len(active)),
        icon="\uf1da",
        actions=[Txt(f"{len(active)} / {max_n}", role="schema")],
        highlighted=False,
    )
    if not active:
        box.add_row(f"  {Txt(t('cc_notifications.no_active_notifications_in_archive'), role='inactive')}")
        return box

    hist_rows = []
    for n in reversed(active[-8:]):
        nid = n.get("id", 0)
        full_app = str(n.get("app_name") or n.get("app") or "sys").strip()
        raw_title = str(n.get("title") or n.get("summary") or "").strip()
        raw_body = str(n.get("body") or "").strip().replace("\n", " ")
        time_s = str(n.get("time") or "").strip()
        if not raw_title and raw_body:
            raw_title, raw_body = raw_body, ""
        app_b = Txt(f"{_get_app_icon(full_app)} {canvas.truncate_plain(full_app, 15)}", role="schema")
        title_s = canvas.truncate_plain(raw_title or full_app, 22)
        is_click = bool(get_app_click_config(full_app, rules))
        title_cell = ActionBtn(title_s, f"cc_notif:trigger_click:{nid}", tone="primary", brackets=False) if is_click else Txt(title_s, role="focus")
        body_cell = Txt(canvas.truncate_plain(raw_body, 32), role="schema") if raw_body else ""
        right_cell = f"{Txt(time_s, role='schema')} {ActionBtn('x', f'cc_notif:del:{nid}', tone='danger_soft')}" if time_s else ActionBtn("x", f"cc_notif:del:{nid}", tone="danger_soft")
        hist_rows.append([app_b, title_cell, body_cell, right_cell])

    box.add_table(
        headers=[],
        widths=[18, 22, "flex", 10],
        rows=hist_rows,
        aligns=["left", "left", "left", "right"],
        show_headers=False,
    )
    return box


def _build_system_standard(cc_panel, canvas: Canvas) -> list:
    rules = _load_rules()
    max_n = _get_max_notifications()
    active = get_active_notifs()
    total = load_notifs()
    unique_apps = get_archive_unique_apps(rules)

    cards = _notif_feedback_cards()
    cards.append(_build_delivery_box(rules, max_n, active, total))
    cards.append(_build_popup_box(rules))
    cards.append(_build_rules_box(cc_panel, canvas, rules, unique_apps))
    cards.append(_build_archive_box(canvas, rules, active, max_n))
    return cards


def build_cards(cc_panel, canvas: Canvas) -> list:
    return _build_system_standard(cc_panel, canvas)
