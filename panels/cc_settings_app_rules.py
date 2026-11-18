#!/usr/bin/env python3
import os
from core.completion import catalog_window_app_ids, rank_candidates
from core.i18n import t
from core.state import STATE
from ui.canvas import Canvas
from ui.kit import Box, SelectPill, StateBadge, Txt, get_active_inline_entry
from panels.cc_engine_settings import (
    _load_rules,
    _notif_feedback_cards,
    _collected_apps,
)

def _build_apps_standard(cc_panel, canvas: Canvas) -> list:
    rules = _load_rules()
    muted = set(a.lower() for a in rules.get("muted_apps", []))
    apps = _collected_apps(rules)
    urg = rules.get("min_urgency", "low")
    cards = _notif_feedback_cards()

    b = Box(t("cc_notifications.per_app_urgency_notification_filte"), icon="\uf1de", highlighted=False)
    b.add_row(" ")
    u_pills = " ".join(SelectPill(u.upper(), f"cc_notif:urg:{u}", selected=(urg == u)) for u in ("low", "normal", "critical"))
    b.add_kv(t("cc_notifications.minimum_urgency"), u_pills)
    app_q = str(STATE.get("settings", "notif_app_query", "") or "").strip()
    app_pick_active = (get_active_inline_entry() == "cc_notif_app_picker")
    if app_q or app_pick_active:
        cands = [it for it in catalog_window_app_ids(extra_apps=apps) if it[0].lower() not in muted]
        ranked = rank_candidates(cands, app_q, limit=6)
        app_pills = [(canvas.truncate_plain(lb, 22), f"cc_notif:mute_app:{val.lower()}") for val, lb in ranked]
    else:
        app_pills = []
    b.add_inline_picker(
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
    b.add_section(t("cc_notifications.application_rules"))
    rows = []
    for a in apps:
        is_muted = a in muted
        rows.append([
            Txt(a, role="focus"),
            StateBadge(not is_muted, t("cc_notifications.allowed"), t("cc_notifications.suppressed"), on_tone="ok"),
            f"{SelectPill(t('cc_notifications.allow'), f'cc_notif:allow_app:{a}', selected=not is_muted)} {SelectPill(t('cc_notifications.mute'), f'cc_notif:mute_app:{a}', selected=is_muted)}",
        ])
    b.add_table(headers=[t("cc_notifications.application"), t("cc_notifications.policy"), t("cc_notifications.action")], widths=[22, 16, "flex"], rows=rows, aligns=["left", "left", "right"])
    b.add_row(" ")
    cards.append(b)
    return cards


def build_cards(cc_panel, canvas: Canvas) -> list:
    return _build_apps_standard(cc_panel, canvas)
