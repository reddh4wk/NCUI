#!/usr/bin/env python3
import json
import os
import re
import time
from core import probe
from core.config import (
    CONFIG,
    ROOT_DIR,
    _unique_existing_paths,
    _update_toml_key_in_section,
    get_screen_lock_cmd,
)
from core.i18n import t
from core.state import STATE
from ui.canvas import Canvas
from ui.kit import ActionBtn, Box
from panels.cc_engine_power import (
    _collect_ups_data,
    _feedback_cards,
    _ups_status_badge,
    _build_ups_connection_rows,
    _append_ups_adaptive_sections,
)


def _build_ups_standard(cc_panel, canvas: Canvas) -> list:
    u = _collect_ups_data()
    cards = _feedback_cards()

    conn_box = Box(
        t("cc_power.nut_server_ups_connection_upsc"),
        icon="\uf1e6",
        actions=[_ups_status_badge(u), ActionBtn(t("cc_power.refresh"), "cc_pwr:refresh", tone="neutral")],
        highlighted=False,
    )
    conn_box.add_row(" ")
    _build_ups_connection_rows(conn_box, u)
    conn_box.add_row(" ")
    cards.append(conn_box)

    tele_title = t("cc_power.ups_tele_target", target=u["target"]) if u["target"] else t("cc_power.ups_tele")
    tele_box = Box(tele_title, icon="\uf240", highlighted=False)
    tele_box.add_row(" ")
    _append_ups_adaptive_sections(tele_box, cc_panel, u)
    tele_box.add_row(" ")
    cards.append(tele_box)
    return cards


def build_cards(cc_panel, canvas: Canvas) -> list:
    return _build_ups_standard(cc_panel, canvas)
