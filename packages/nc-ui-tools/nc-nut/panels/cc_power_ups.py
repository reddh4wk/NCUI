#!/usr/bin/env python3
from core.i18n import t
from ui.canvas import Canvas
from ui.kit import ActionBtn, Box
from panels.cc_engine_ups import (
    _append_ups_status_section,
    _build_ups_connection_rows,
    _build_ups_electrical_box,
    _build_ups_waybar_box,
    _collect_ups_data,
    _feedback_cards,
    _ups_status_badge,
)


def _build_ups_standard(cc_panel, canvas: Canvas) -> list:
    u = _collect_ups_data(async_refresh=True, cc_panel=cc_panel)
    cards = _feedback_cards()

    conn_box = Box(
        t("cc_power.nut_server_ups_connection_upsc"),
        icon="\uf1e6",
        actions=[_ups_status_badge(u), ActionBtn(t("cc_power.refresh"), "cc_pwr:refresh", tone="neutral")],
        highlighted=False,
    )
    _build_ups_connection_rows(conn_box, u)
    cards.append(conn_box)

    tele_title = t("cc_power.ups_tele_target", target=u["target"]) if u["target"] else t("cc_power.ups_tele")
    tele_box = Box(tele_title, icon="\uf240", highlighted=False)
    _append_ups_status_section(tele_box, cc_panel, u)
    cards.append(tele_box)

    elec_box = _build_ups_electrical_box(u)
    if elec_box:
        cards.append(elec_box)

    cards.append(_build_ups_waybar_box(cc_panel, u))
    return cards


def build_cards(cc_panel, canvas: Canvas) -> list:
    return _build_ups_standard(cc_panel, canvas)
