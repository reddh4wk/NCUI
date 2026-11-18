#!/usr/bin/env python3
import os
from core.i18n import t
from ui.canvas import Canvas
from ui.kit import ActionBtn, Box, Txt
from panels.cc_engine_telemetry import (
    _feedback_cards,
    _discover_nc_scripts,
)

def _build_power_tools_standard(cc_panel, canvas: Canvas) -> list:
    scripts = _discover_nc_scripts()
    cards = _feedback_cards()

    b = Box(t("cc_system.nc_power_tools_usr_local_sbin_nc_u"), icon="\uf0ad", highlighted=False)
    b.add_row(" ")
    rows = []
    for sc in scripts:
        sc_p = sc["path"]
        rows.append([
            Txt(sc["name"], role="focus"),
            Txt(sc["scope"].upper(), role="schema"),
            Txt(canvas.truncate_plain(sc_p, 34), role="schema"),
            f"{ActionBtn(t('cc_system.run_term'), f'cc_sys:run_script:{sc_p}', tone='apply')} {ActionBtn(t('cc_system.args'), f'cc_sys:prompt_script:{sc_p}', tone='neutral')}",
        ])
    b.add_table(headers=[t("cc_system.script"), t("cc_system.scope"), t("cc_system.path"), t("cc_system.actions")], widths=[22, 8, 30, "flex"], rows=rows, aligns=["left", "left", "left", "right"])
    b.add_row(" ")
    cards.append(b)
    return cards


def build_cards(cc_panel, canvas: Canvas) -> list:
    return _build_power_tools_standard(cc_panel, canvas)
