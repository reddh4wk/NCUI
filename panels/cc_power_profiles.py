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
from ui.kit import Box, SelectPill, Txt
from panels.cc_engine_power import (
    _feedback_cards,
    _collect_cpu_power,
)


def _build_general_standard(cc_panel, canvas: Canvas) -> list:
    st = _collect_cpu_power()
    cards = _feedback_cards()

    box = Box(
        t("cc_power.cpu_power_profiles_gpu_telemetry"),
        icon="\uf0e7",
        actions=[Txt(st["gov"].upper(), role="schema")],
        highlighted=False,
    )
    box.add_row(" ")
    gov_btns = " ".join(SelectPill(g, f"cc_pwr:gov:{g}", selected=(st["gov"] == g)) for g in st["avail_gov"][:5])
    box.add_kv(t("cc_power.cpu_governor"), gov_btns, requires=["cpupower"])
    if st["avail_epp"]:
        epp_btns = " ".join(SelectPill(e.replace("_", " "), f"cc_pwr:epp:{e}", selected=(st["epp"] == e)) for e in st["avail_epp"][:4])
        box.add_kv(t("cc_power.energy_perf_epp"), epp_btns)
    else:
        box.add_kv(t("cc_power.energy_perf_epp"), Txt(st["epp"], role="schema"))
    if st["boost"] in ("0", "1"):
        b_on = st["boost"] == "1"
        box.add_kv(t("cc_power.core_boost_cpb"), f"{SelectPill(t("cc_power.boost_on"), 'cc_pwr:boost:1', selected=b_on)} {SelectPill(t("cc_power.eco_off"), 'cc_pwr:boost:0', selected=not b_on)}")
    if st["gpu_info"]:
        parts = [p.strip() for p in st["gpu_info"].split(",")]
        g_txt = f"{parts[0]}C  |  Draw: {parts[1]}W / {parts[2]}W" if len(parts) >= 3 else st["gpu_info"]
        box.add_kv(t("cc_power.gpu_power"), Txt(g_txt, role="val"))
    box.add_row(" ")
    cards.append(box)
    return cards


def build_cards(cc_panel, canvas: Canvas) -> list:
    return _build_general_standard(cc_panel, canvas)
