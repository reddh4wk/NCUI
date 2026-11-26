#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""NC-UI Centered Overlay — Niri Important Hotkeys (`panels/wp_hotkeys.py`)."""

import os
import shlex

from core.config import get_screen_lock_cmd
from core.i18n import t
from core.state import STATE
from panels.base import BasePanel
from panels.cc_engine_input import (
    _collect_keyboard_state,
    _norm_action_str,
    _std_action_lookup,
)
from ui.canvas import Canvas
from ui.kit import ActionBtn, Box, InlineFilter, Txt, start_inline_input

_CAT_ORDER = ("window", "apps", "system", "custom")

_KEY_NAMES = {
    "Mod": "Super",
    "Slash": "/",
    "Return": "Enter",
    "BackSpace": "Backspace",
    "BackSlash": "\\",
    "Minus": "-",
    "Equal": "=",
}


_GENERIC_LOCKERS = ("swaylock", "swaylock-effects", "hyprlock", "gtklock", "waylock", "i3lock")


def _format_combo(combo: str) -> str:
    return " + ".join(_KEY_NAMES.get(tok, tok) for tok in str(combo or "").split("+") if tok)


def _format_action(action: str, std_map: dict, lock_prog: str = "") -> str:
    norm = _norm_action_str(action)
    std = std_map.get(norm)
    if std:
        return t(f"cc_keyboard.shortcut_{std['id']}")
    if norm.startswith("spawn "):
        try:
            tokens = shlex.split(norm[6:])
        except ValueError:
            tokens = norm[6:].split()
        if tokens:
            prog = os.path.basename(tokens[0])
            if (
                (lock_prog and prog == lock_prog)
                or prog in _GENERIC_LOCKERS
                or (prog == "loginctl" and "lock-session" in tokens)
                or "--lock" in tokens
            ):
                return t("hotkeys.act_lock_screen")
            if prog == "nc-ctl" and len(tokens) >= 3:
                return f"NC-UI {' '.join(tokens[1:])}"
            return t("hotkeys.act_spawn", p0=prog)
    return norm.replace("-", " ").title()


class Panel(BasePanel):
    id = "hotkeys"
    icon = "\uf11c"
    centered_overlay = True
    supports_expand = False
    hover_highlight = False
    refresh_ms = 0

    def __init__(self, shell=None):
        self.title = t("hotkeys.title", p0=0) if shell is not None else self.id
        self.query = ""
        super().__init__(shell)

    def get_cols(self, force_expanded: bool = False) -> int:
        return 68

    def get_max_height(self) -> int:
        return 780

    def get_bar_pill(self) -> dict:
        return {"text": "", "tooltip": "", "class": "module-nc"}

    def build_cards(self, canvas: Canvas, force_expanded: bool = False) -> list:
        target_cols = 68
        canvas.set_cols(target_cols)

        st = _collect_keyboard_state()
        std_map = _std_action_lookup()
        lock_cmd = get_screen_lock_cmd()
        lock_prog = ""
        if lock_cmd:
            try:
                lock_prog = os.path.basename(shlex.split(lock_cmd)[0])
            except ValueError:
                lock_prog = os.path.basename(lock_cmd.split()[0])
        q = self.query.strip().lower()

        grouped = {cat: [] for cat in _CAT_ORDER}
        shown_count = 0
        for b in st.get("binds", []):
            raw_combo = str(b.get("combo", ""))
            raw_act = str(b.get("action", ""))
            cat = str(b.get("cat", "custom"))
            if cat not in grouped:
                cat = "custom"
            c_disp = _format_combo(raw_combo)
            a_desc = _format_action(raw_act, std_map, lock_prog)
            if q:
                hay = f"{c_disp} {raw_combo} {a_desc} {raw_act} {t(f'hotkeys.cat_{cat}')}".lower()
                if q not in hay:
                    continue
            grouped[cat].append((c_disp, a_desc))
            shown_count += 1

        box = Box(
            t("hotkeys.title", p0=shown_count),
            icon=self.icon,
            actions=[
                ActionBtn(t("hotkeys.edit_cc"), "hk:open_cc", tone="neutral"),
                ActionBtn("x", "ui:close", tone="danger_soft"),
            ],
            highlighted=False,
            fixed_width=True,
            min_cols=target_cols,
            max_cols=target_cols,
        )
        box.add_row(" ")
        box.add_kv(
            t("hotkeys.filter_lbl"),
            InlineFilter(
                "wp_hk_flt",
                self.query,
                t("hotkeys.filter_ph"),
                "hk:flt_open",
                clear_uri="hk:flt_clear" if self.query else "",
                width=26,
                active=False,
            ),
            key_width=10,
        )

        if shown_count == 0:
            box.add_row(" ")
            box.add_row(f"  {Txt(t('hotkeys.empty'), role='inactive')}")
        else:
            for cat in _CAT_ORDER:
                items = grouped[cat]
                if not items:
                    continue
                box.add_section(t(f"hotkeys.cat_{cat}"))
                rows = [
                    [
                        Txt(canvas.truncate_plain(c_disp, 24), role="focus"),
                        Txt(canvas.truncate_plain(a_desc, 38), role="val"),
                    ]
                    for c_disp, a_desc in items
                ]
                box.add_table(
                    headers=["", ""],
                    widths=[24, "flex"],
                    rows=rows,
                    aligns=["left", "left"],
                    show_headers=False,
                )

        box.add_row(" ")
        box.add_row(f"  {Txt(t('hotkeys.footer_hint'), role='inactive')}")
        box.add_row(" ")
        return [box]

    def on_link(self, uri: str) -> None:
        if uri == "hk:flt_open":
            start_inline_input(
                "wp_hk_flt",
                shell=self.shell,
                trigger_uri=uri,
                initial_text=self.query,
                max_len=40,
                on_change=lambda s: setattr(self, "query", str(s or "")),
                on_commit=lambda s: setattr(self, "query", str(s or "").strip()),
            )
        elif uri == "hk:flt_clear":
            self.query = ""
            self.request_throttled_refresh()
        elif uri == "hk:open_cc":
            STATE.set("control_center", "subtab_input", "shortcuts")
            if self.shell:
                self.shell.dispatch_uri("ui:open_settings:input")
