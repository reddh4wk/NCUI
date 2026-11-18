#!/usr/bin/env python3
from core.i18n import t
from ui.canvas import Canvas
from ui.kit import ActionBtn, Box, InlineField, SelectPill, StateBadge, Txt
from panels.cc_engine_settings import (
    _get_max_notifications,
    _load_rules,
    _notif_feedback_cards,
    get_active_notifs,
    get_app_click_config,
    get_archive_unique_apps,
    load_notifs,
    resolve_app_launch_cmd,
)


def _build_system_standard(cc_panel, canvas: Canvas) -> list:
    rules = _load_rules()
    max_n = _get_max_notifications()
    active = get_active_notifs()
    total = load_notifs()
    unique_apps = get_archive_unique_apps(rules)
    cards = _notif_feedback_cards()

    b = Box(
        t("cc_notifications.system_notifications_archive"),
        icon="\uf0f3",
        actions=[
            StateBadge(rules["dnd"], t("cc_notifications.dnd_on"), t("cc_notifications.dnd_off"), on_tone="warn"),
            ActionBtn(t("cc_notifications.test_notify"), "cc_notif:test", tone="apply"),
            ActionBtn(t("cc_notifications.clear_archive"), "cc_notif:clear", tone="danger_soft"),
        ],
        highlighted=False,
    )
    b.add_row(" ")
    b.add_kv(
        t("cc_notifications.do_not_disturb"),
        f"{SelectPill(t('common.on'), 'cc_notif:dnd:1', selected=rules['dnd'])} {SelectPill(t('common.off'), 'cc_notif:dnd:0', selected=not rules['dnd'])}",
    )
    q_presets = ("22:00-07:00", "23:00-07:00", "00:00-08:00")
    q_custom = bool(rules["quiet_enabled"] and rules["quiet_window"] not in q_presets)
    q_state = StateBadge(rules["quiet_enabled"], rules["quiet_window"], t("cc_notifications.disabled"))
    q_acts = (
        f"{SelectPill(t('cc_notifications.toggle'), 'cc_notif:quiet_toggle', selected=rules['quiet_enabled'])} "
        f"{InlineField('cc_notif_quiet', 'cc_notif:inline_quiet', value=rules['quiet_window'] if q_custom else '', empty_label=t('cc_notifications.set'), placeholder='22:00-07:00', width=13, icon='\u270e', tone='neutral', selected=q_custom)}"
    )
    b.add_kv(t("cc_notifications.quiet_hours"), q_state, right_markup=q_acts)
    b.add_kv(
        t("cc_notifications.quiet_presets"),
        " ".join(SelectPill(w, f"cc_notif:quiet_win:{w}", selected=(rules["quiet_window"] == w)) for w in q_presets),
    )
    m_pills = " ".join(SelectPill(str(n), f"cc_notif:max:{n}", selected=(max_n == n)) for n in (25, 50, 100, 200))
    b.add_kv(
        t("cc_notifications.history_limit"),
        m_pills,
        right_markup=Txt(t("cc_notifications.active_total_short", p0=len(active), p1=len(total)), role="schema"),
    )
    grp_active = bool(rules.get("group_by_app", False))
    b.add_kv(
        t("cc_notifications.group_by_application"),
        f"{SelectPill(t('cc_notifications.grouped'), 'cc_notif:group_app:1', selected=grp_active)} {SelectPill(t('cc_notifications.chronological'), 'cc_notif:group_app:0', selected=not grp_active)}",
    )

    if cc_panel.get_state("notif_hist", None) is None:
        cc_panel.set_state("notif_hist", True)

    def _fill_hist(bx: Box):
        if not active:
            bx.add_row(f"  {Txt(t('cc_notifications.no_active_notifications_in_archive'), role='schema')}")
            return
        hist_rows = []
        for n in reversed(active[-8:]):
            nid = n.get("id", 0)
            full_app = str(n.get("app_name") or n.get("app") or "sys").strip()
            raw_title = str(n.get("title") or n.get("summary") or "").strip()
            raw_body = str(n.get("body") or "").strip().replace("\n", " ")
            if not raw_title and raw_body:
                raw_title, raw_body = raw_body, ""
            app_b = Txt(canvas.truncate_plain(full_app, 16), role="schema")
            title_s = canvas.truncate_plain(raw_title or full_app, 24)
            is_click = bool(get_app_click_config(full_app, rules))
            title_cell = ActionBtn(title_s, f"cc_notif:trigger_click:{nid}", tone="primary", brackets=False) if is_click else Txt(title_s, role="focus")
            body_cell = Txt(canvas.truncate_plain(raw_body, 38), role="schema") if raw_body else ""
            hist_rows.append([app_b, title_cell, body_cell])
        bx.add_table(
            headers=[],
            widths=[18, 24, "flex"],
            rows=hist_rows,
            aligns=["left", "left", "left"],
            show_headers=False,
        )

    b.add_collapsible(cc_panel, "notif_hist", t("cc_notifications.recent_archive_active", p0=len(active)), _fill_hist)

    if cc_panel.get_state("notif_click_apps", None) is None:
        cc_panel.set_state("notif_click_apps", True)

    def _fill_archive_apps(bx: Box):
        if not unique_apps:
            bx.add_row(f"  {Txt(t('cc_notifications.no_archive_apps'), role='schema')}")
            return
        custom_lbl = t("cc_notifications.custom_action")
        rows = []
        for app in unique_apps:
            cfg = get_app_click_config(app, rules)
            if cfg:
                custom_cmd = cfg.get("cmd", "")
                if custom_cmd:
                    target_m = Txt(t("cc_notifications.custom_launch", p0=custom_cmd), role="val")
                else:
                    auto_cmd = resolve_app_launch_cmd(app) or t("cc_notifications.no_cmd_found")
                    target_m = Txt(t("cc_notifications.auto_launch", p0=auto_cmd), role="val")
                acts_m = (
                    f"{ActionBtn(custom_lbl, f'cc_notif:prompt_click_cmd:{app}', tone='neutral')} "
                    f"{SelectPill(t('cc_notifications.clickable'), f'cc_notif:click_toggle:{app}', selected=True)}"
                )
            else:
                target_m = Txt(t("cc_notifications.not_clickable"), role="inactive")
                acts_m = SelectPill(t("cc_notifications.clickable"), f"cc_notif:click_toggle:{app}", selected=False)
            rows.append([Txt(app, role="focus"), target_m, acts_m])

        col_w = max(29, len(t("cc_notifications.clickable")) + len(custom_lbl) + 8)
        bx.add_table(
            headers=[t("cc_notifications.application"), t("cc_notifications.launch_target"), t("cc_notifications.action")],
            widths=[18, "flex", col_w],
            rows=rows,
            aligns=["left", "left", "right"],
        )

    b.add_collapsible(cc_panel, "notif_click_apps", t("cc_notifications.archive_apps_section", p0=len(unique_apps)), _fill_archive_apps)
    b.add_row(" ")
    cards.append(b)
    return cards


def build_cards(cc_panel, canvas: Canvas) -> list:
    return _build_system_standard(cc_panel, canvas)
