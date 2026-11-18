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
from ui.kit import ActionBtn, Badge, Box, SelectPill, StateBadge, Txt
from panels.cc_engine_power import (
    _feedback_cards,
    _collect_idle_state,
)


def _build_wakeup_standard(cc_panel, canvas: Canvas) -> list:
    st = _collect_idle_state()
    cards = _feedback_cards()
    idle_hdr = (
        Badge(t("cc_power.caffeine_on"), style="warn")
        if st["caffeine"]
        else StateBadge(True, t("cc_power.idle_active"), on_tone="ok")
    )
    box = Box(
        t("cc_power.idle_timeout_auto_lock_caffeine_ov"),
        icon="\uf186",
        actions=[idle_hdr],
        highlighted=False,
    )
    box.add_row(" ")
    box.add_kv(t("cc_power.idle_daemon"), StateBadge(st["idle_daemon"] != "Stopped", st["idle_daemon"].upper(), t("cc_power.stopped")))
    box.add_kv(t("cc_power.auto_lock_timeout"), " ".join(SelectPill(f"{m}m" if m else t("cc_power.off"), f"cc_pwr:idle_min:{m}", selected=(st["idle_min"] == m), min_chars=0) for m in (5, 10, 15, 30, 0)))
    box.add_kv(t("cc_power.dpms_screen_off"), " ".join(SelectPill(f"{m}m" if m else t("cc_power.off"), f"cc_pwr:dpms_min:{m}", selected=(st["dpms_min"] == m), min_chars=0) for m in (10, 15, 30, 60, 0)))
    box.add_kv(t("cc_power.awake_caffeine"), f"{SelectPill(t("cc_power.keep_awake"), 'cc_pwr:caffeine:on', selected=st['caffeine'])} {SelectPill(t("cc_power.allow_idle"), 'cc_pwr:caffeine:off', selected=not st['caffeine'])}")
    box.add_kv(t("cc_power.lock_screen_command"), Txt(st["lock_cmd"] or t("cc_power.not_set_waybar_options"), role="val" if st["lock_cmd"] else "attention"), right_markup=ActionBtn(t("cc_power.lock_now"), "cc_pwr:lock_now", tone="apply"))
    box.add_row(" ")
    cards.append(box)
    return cards


def build_cards(cc_panel, canvas: Canvas) -> list:
    return _build_wakeup_standard(cc_panel, canvas)
