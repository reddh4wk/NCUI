#!/usr/bin/env python3
"""NC-UI Control Center — CPU, RAM & Swap Telemetry + Embedded btop (`panels/cc_power_cpu_ram.py`)."""

from core.i18n import t
from ui.canvas import Canvas
from ui.embedded_tui import (
    EmbeddedTuiWidget,
    _build_tui_ncbox,
    _compute_single_tui_size,
    _compute_tui_grid_metrics,
)
from ui.kit import (
    ActionBtn,
    Badge,
    Box,
    ProgressBar,
    Sparkline,
    StateBadge,
    Txt,
)
from panels.cc_engine_power import _probe_gaming_mode
from panels.cc_engine_resources import (
    _ensure_btop_config,
    _feedback_cards,
    get_cpu_telemetry,
    get_ram_telemetry,
)


def _pct_role(pct: float) -> str:
    if pct < 50.0:
        return "vitality"
    if pct < 75.0:
        return "attention"
    if pct < 90.0:
        return "interrupt"
    return "fault"


def _build_cpu_ram_standard(cc_panel, canvas: Canvas) -> list:
    cpu = get_cpu_telemetry({})
    ram = get_ram_telemetry()
    gm = _probe_gaming_mode()
    cards = _feedback_cards()

    top_acts = []
    if gm["available"]:
        top_acts.append(
            Badge(t("cc_system.gaming_badge"), style="warn")
            if gm["is_gaming"]
            else StateBadge(True, t("cc_system.mode_hardened").upper(), on_tone="ok")
        )
        top_acts.append(ActionBtn(t("cc_system.goto_power_profiles"), "cc_sys:goto_power_profiles", tone="neutral"))
    top_acts.append(ActionBtn(t("cc_system.sync_disks"), "cc_sys:sync_disks", tone="apply"))

    top = Box(
        t("cc_system.cpu_ram_swap_telemetry", p0=cpu["model"]),
        icon="\uf2db",
        actions=top_acts,
        highlighted=False,
    )
    top.add_row(" ")
    c_pct, c_tmp, c_ghz = cpu["total_pct"], cpu["temp_c"], cpu["max_ghz"]
    tmp_role = "vitality" if c_tmp < 65.0 else ("attention" if c_tmp < 80.0 else "fault")
    r_used_g, r_tot_g = ram["used"] / (1024 ** 3), ram["total"] / (1024 ** 3)
    s_used_g, s_tot_g = ram["sw_used"] / (1024 ** 3), ram["sw_total"] / (1024 ** 3)
    top.add_kv(
        t("cc_system.cpu_load"),
        f"{ProgressBar(c_pct, width=20)} {Txt(f'{c_pct:4.1f}%', role=_pct_role(c_pct))}  "
        f"{Txt(f'{c_tmp:.1f}°C', role=tmp_role)}  {Txt(f'{c_ghz:.2f} GHz', role='val')}",
    )
    top.add_kv(t("cc_system.core_sparkline"), Sparkline(cpu["cores_pct"], max_items=32))
    top.add_kv(
        t("cc_system.ram_usage"),
        f"{ProgressBar(ram['pct'], width=20)} {Txt(f'{r_used_g:.1f}/{r_tot_g:.1f} GiB', role='val')}  "
        f"{Txt('SWAP', role='schema')} {ProgressBar(ram['sw_pct'], width=10)} {Txt(f'{s_used_g:.1f}/{s_tot_g:.1f} GiB', role='val')}",
    )
    top.add_row(" ")
    cards.append(top)

    cols, rows, target_widget_h = _compute_single_tui_size(
        cc_panel, canvas, cards, t("cc_system.btop_box_title"), "\uf0ae", min_rows=20, flush=True, has_footer=False
    )
    cards.append(
        _build_tui_ncbox(
            cc_panel,
            canvas,
            tui_id="btop",
            cmd=["btop", "--force-utf", "--config", _ensure_btop_config()],
            rows=rows,
            cols=cols,
            target_height_px=target_widget_h,
            title=t("cc_system.btop_box_title"),
            icon="\uf0ae",
            open_key="tui_btop_open",
            desc_key="cc_system.btop_collapsed_desc",
        )
    )
    return cards


def build_cards(cc_panel, canvas: Canvas) -> list:
    return _build_cpu_ram_standard(cc_panel, canvas)
