#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""NC-UI Waybar Plugin & Popup — Active Keyboard Layout Switcher (`panels/wp_kb_layout.py`)."""

from core.i18n import t
from core.state import STATE
from panels.base import BasePanel
from panels.cc_engine_input import (
    cycle_active_keyboard_layout,
    get_live_keyboard_layouts,
    switch_active_keyboard_layout,
)
from ui.canvas import Canvas
from ui.kit import ActionBtn, Box, SelectPill, Txt


class Panel(BasePanel):
    id = "kb_layout"
    icon = "\uf11c"
    hover_highlight = True
    refresh_ms = 2000

    def __init__(self, shell=None):
        self.title = t("kb_layout.title") if shell is not None else self.id
        super().__init__(shell)

    def get_cols(self, force_expanded: bool = False) -> int:
        return 50

    def get_bar_pill(self) -> dict:
        live = get_live_keyboard_layouts()
        cur = live["current"]
        code = cur["code"].upper()
        return {
            "text": f"\uf11c {code}",
            "tooltip": t("kb_layout.tooltip", name=cur["name"], code=code),
            "class": "module-nc",
        }

    def on_bar_action(self, action: str, args: list = None) -> None:
        if action == "cycle":
            cycle_active_keyboard_layout()

    def build_cards(self, canvas: Canvas, force_expanded: bool = False) -> list:
        target_cols = 50
        canvas.set_cols(target_cols)

        live = get_live_keyboard_layouts()
        items = live["items"]
        cur_idx = live["current_idx"]

        box = Box(
            t("kb_layout.title"),
            icon="\uf11c",
            actions=[ActionBtn(t("kb_layout.cycle"), "kbl:cycle", tone="neutral")],
            highlighted=False,
            fixed_width=True,
            min_cols=target_cols,
            max_cols=target_cols,
        )
        box.add_row(" ")
        for it in items:
            idx = it["idx"]
            code = it["code"].upper()
            is_now = (idx == cur_idx)
            pill = SelectPill(f"{code} - {canvas.truncate_plain(it['name'], 18)}", f"kbl:set:{idx}", selected=is_now)
            box.add_row(f"  {pill}", right_markup=Txt(code, role="schema"))

        box.add_row(" ")
        cfg_lbl = t("kb_layout.manage_active_layouts")
        box.add_row(f"  {ActionBtn(cfg_lbl, 'kbl:open_cc', tone='neutral')}")
        box.add_row(" ")
        return [box]

    def on_link(self, uri: str) -> None:
        if uri == "kbl:cycle":
            cycle_active_keyboard_layout()
            self.request_throttled_refresh()
        elif uri.startswith("kbl:set:"):
            switch_active_keyboard_layout(uri.split(":", 2)[2])
            self.request_throttled_refresh()
        elif uri == "kbl:open_cc":
            STATE.set("control_center", "subtab_input", "keyboard")
            if self.shell:
                self.shell.dispatch_uri("ui:open_settings:input")
