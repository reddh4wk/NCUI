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
from panels.cc_engine_display import (
    _feedback_cards,
    _get_night_light_state,
    get_niri_outputs_data,
)


def _build_scaling_standard(cc_panel, canvas: Canvas, outputs_data: dict) -> list:
    cards = _feedback_cards()
    nl = _get_night_light_state()
    dpms_min = int(STATE.get("display", "dpms_timeout", 10) or 10)

    nl_box = Box(
        t("cc_display.night_light_vrr_screen_dpms"),
        icon="\uf186",
        actions=[StateBadge(nl["active"], t("cc_display.night_k", p0=nl['temp']), t("cc_display.daylight"), on_tone="warn")],
    )
    nl_box.add_row(" ")
    nl_box.add_kv(
        t("cc_display.night_light_wlsunset"),
        f"{SelectPill(t('common.on'), 'cc_desk:nl:on', selected=nl['active'])} {SelectPill(t('common.off'), 'cc_desk:nl:off', selected=not nl['active'])}  "
        + Slider(nl["temp"], "cc_desk:nl_temp", min_val=2500, max_val=6500, width=18, show_pct=False, step_buttons=True, step=500, enabled=bool(nl["active"])),
        requires=["wlsunset"],
    )
    nl_box.add_kv(
        t("cc_display.dpms_screen_off"),
        " ".join(SelectPill(f"{m}m" if m else t("cc_display.never"), f"cc_desk:dpms:{m}", selected=(dpms_min == m)) for m in (5, 10, 15, 30, 0)),
        right_markup=ActionBtn(t("cc_display.sleep_now"), "cc_desk:dpms_now", tone="warn"),
    )
    nl_box.add_row(" ")
    cards.append(nl_box)

    for out_name, info in sorted(outputs_data.items()):
        logical = info.get("logical") or {}
        if not logical:
            continue
        cur_scale = float(logical.get("scale", 1.0))
        modes = info.get("modes") or []
        cur_idx = info.get("current_mode")
        vrr_on = bool(info.get("vrr_enabled", False))
        scale_presets = (1.0, 1.25, 1.5, 1.75, 2.0)
        is_custom_scale = all(abs(cur_scale - sv) >= 0.03 for sv in scale_presets)

        s_box = Box(
            f"{out_name} ({info.get('model', 'Monitor')})",
            icon="\uf108",
            actions=[
                Txt(t("cc_display.scale_x_2", p0=cur_scale), role="schema"),
                InlineField(
                    f"cc_disp_scale_{out_name}",
                    f"cc_desk:scale_inline:{out_name}",
                    value=(f"{cur_scale:g}x" if is_custom_scale else ""),
                    empty_label=t("cc_display.custom"),
                    placeholder="1.25",
                    width=8,
                    icon="\u270e",
                    tone="neutral",
                    selected=is_custom_scale,
                ),
            ],
            highlighted=False,
        )
        s_box.add_row(" ")
        scale_btns = " ".join(SelectPill(f"{sv:g}x", f"cc_desk:scale:{out_name}:{sv:g}", selected=(abs(cur_scale - sv) < 0.03)) for sv in scale_presets)
        s_box.add_kv(t("cc_display.scaling_factor"), scale_btns)
        s_box.add_kv(t("cc_display.variable_refresh_vrr"), f"{SelectPill(t('cc_display.vrr_on'), f'cc_desk:vrr:{out_name}:on', selected=vrr_on)} {SelectPill(t('cc_display.vrr_off'), f'cc_desk:vrr:{out_name}:off', selected=not vrr_on)}")

        seen, mode_btns = set(), []
        for idx, m in enumerate(modes):
            w, h = m.get("width", 0), m.get("height", 0)
            rr = m.get("refresh_rate", 0)
            hz = rr / 1000.0 if rr > 1000 else float(rr)
            m_spec = f"{w}x{h}@{hz:.0f}"
            if m_spec in seen:
                continue
            seen.add(m_spec)
            mode_btns.append(SelectPill(f"{m_spec}Hz", f"cc_desk:res:{out_name}:{m_spec}", selected=(idx == cur_idx)))
            if len(mode_btns) >= 8:
                break

        def _build_modes(b: Box, btns=mode_btns):
            for i in range(0, len(btns), 3):
                b.add_row("  " + "  ".join(btns[i:i + 3]))

        s_box.add_collapsible(cc_panel, f"disp_modes_{out_name}", t("cc_display.hardware_modes", p0=len(mode_btns)), _build_modes)
        s_box.add_row(" ")
        cards.append(s_box)
    return cards


def build_cards(cc_panel, canvas: Canvas) -> list:
    return _build_scaling_standard(cc_panel, canvas, get_niri_outputs_data())
