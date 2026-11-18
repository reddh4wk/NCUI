#!/usr/bin/env python3
from core.i18n import t
from ui.canvas import Canvas
from ui.kit import Badge, Box, Txt


def build_cards(cc_panel, canvas: Canvas) -> list:
    b = Box(
        t("cc_input_controllers.title"),
        icon="\uf11b",
        actions=[Txt(t("cc_input_controllers.badge"), role="schema")],
        highlighted=False,
    )
    b.add_row(" ")
    b.add_kv(t("cc_input_controllers.title"), Txt(t("cc_input_controllers.status"), role="val"))
    b.add_row(f"  {Txt(t('cc_input_controllers.empty_hint'), role='schema')}")
    b.add_row(" ")
    return [b]
