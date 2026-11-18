#!/usr/bin/env python3
import json
import os
import re
import time
from core import probe
from core.i18n import t
from core.state import STATE
from panels.wp_display import NiriOutputMap, resolve_script_path, run_desktop_cli
from ui.canvas import Canvas
from ui.kit import ActionBtn, Badge, Box, Divider, InlineField, SelectPill, Slider, StateBadge, Txt
from ui.widgets import AsciiCard
from panels.cc_engine_display import (
    _feedback_cards,
    get_niri_outputs_data,
)


def _build_layout_standard(cc_panel, canvas: Canvas, outputs_data: dict) -> list:
    cards = _feedback_cards()
    hdr = Box(
        t("cc_display.multi_monitor_spatial_topology_nir"),
        icon="\uf108",
        actions=[
            ActionBtn(t("cc_display.auto_pos_all"), "cc_desk:pos_auto_all", tone="neutral"),
            ActionBtn(t("cc_display.tui"), "cc_desk:open_tui", tone="neutral"),
        ],
        highlighted=False,
    )
    hdr.add_row(" ")
    hdr.add_row(f"  {Txt(t('cc_display.drag_monitors_below_to_snap_positi'), role='schema')}")

    if any(v.get("logical") for v in outputs_data.values()):
        hdr.open_bottom = True
        niri_map = NiriOutputMap(
            outputs_data,
            refresh_cb=(lambda: cc_panel.shell.refresh_view() if cc_panel.shell else None),
            map_width=int(round(canvas.total_px)),
            padding=max(24, int(round(canvas.char_w * 3))),
        )
        foot = Box("", highlighted=False)
        foot.open_top = True
        foot.add_row(" ")
        cards.append(AsciiCard(hdr, canvas, embedded_widget=niri_map, footer_box_model=foot))
    else:
        hdr.add_row(" ")
        cards.append(hdr)

    for out_name, info in sorted(outputs_data.items()):
        logical = info.get("logical")
        is_on = logical is not None
        modes = info.get("modes") or []
        cur_idx = info.get("current_mode")
        cur_str = t("cc_display.disabled") if not is_on else f"{logical.get('width', 0)}x{logical.get('height', 0)}"
        if is_on and cur_idx is not None and 0 <= cur_idx < len(modes):
            cm = modes[cur_idx]
            rr = cm.get("refresh_rate", 0)
            hz = rr / 1000.0 if rr > 1000 else float(rr)
            cur_str = f"{cm.get('width')}x{cm.get('height')}@{hz:.0f}Hz"

        o_box = Box(
            f"{out_name} — {info.get('model', 'Display')}",
            icon="\uf26c",
            actions=[
                StateBadge(is_on, t("cc_display.enabled"), t("common.off")),
                ActionBtn(t("cc_display.disable") if is_on else t("cc_display.enable"), f"cc_desk:power:{out_name}:{'off' if is_on else 'on'}", tone="danger_soft" if is_on else "apply"),
            ],
            highlighted=False,
        )
        o_box.add_row(" ")
        if is_on:
            o_box.add_kv(t("cc_display.active_mode"), Txt(cur_str, role="val"), right_markup=ActionBtn(t("cc_display.native_max"), f"cc_desk:res_max:{out_name}", tone="apply"))
            o_box.add_kv(t("cc_display.logical_pos"), Txt(t("cc_display.scale_x", p0=logical.get('x', 0), p1=logical.get('y', 0), p2=logical.get('scale', 1.0)), role="val"),
                         right_markup=f"{InlineField(f'cc_disp_pos_{out_name}', f'cc_desk:pos_inline:{out_name}', value='', empty_label=t('cc_display.set_pos'), placeholder='0,0', width=10, icon='\u270e', tone='neutral')} {ActionBtn(t('cc_display.auto'), f'cc_desk:pos_auto:{out_name}', tone='neutral')}")
        else:
            o_box.add_kv(t("cc_display.status"), Txt(t("cc_display.output_powered_off_in_niri"), role="inactive"))
        o_box.add_row(" ")
        cards.append(o_box)
    return cards


def build_cards(cc_panel, canvas: Canvas) -> list:
    return _build_layout_standard(cc_panel, canvas, get_niri_outputs_data())
