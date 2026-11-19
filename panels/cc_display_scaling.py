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
    nl_temp_txt = Txt(f"{nl['temp']}K", role="val" if nl["active"] else "inactive")
    nl_box.add_kv(
        t("cc_display.night_light_wlsunset"),
        f"{SelectPill(t('common.on'), 'cc_desk:nl:on', selected=nl['active'])} "
        f"{SelectPill(t('common.off'), 'cc_desk:nl:off', selected=not nl['active'])}  "
        + Slider(nl["temp"], "cc_desk:nl_temp", min_val=1700, max_val=6500, width=16, show_pct=False, step_buttons=True, step=250, enabled=bool(nl["active"]))
        + f" {nl_temp_txt}",
        requires=["wlsunset"],
    )
    is_sunset = nl["schedule"] == "sunset"
    if is_sunset:
        coords_val = f"{nl['lat']},{nl['lon']}" if (nl["lat"] and nl["lon"]) else ""
        sched_field = InlineField(
            "cc_disp_nl_coords",
            "cc_desk:nl_coords_inline",
            value=coords_val,
            empty_label=t("cc_display.nl_coords_edit"),
            placeholder="41.9,12.5",
            width=14,
            icon="\u270e",
            tone="neutral",
            selected=False,
        )
    else:
        sched_field = InlineField(
            "cc_disp_nl_hours",
            "cc_desk:nl_hours_inline",
            value=f"{nl['sunset']}-{nl['sunrise']}",
            empty_label=t("cc_display.nl_hours_edit"),
            placeholder="20:00-06:30",
            width=13,
            icon="\u270e",
            tone="neutral",
            selected=False,
        )
    nl_box.add_kv(
        t("cc_display.nl_schedule"),
        f"{SelectPill(t('cc_display.nl_sched_manual'), 'cc_desk:nl_sched:manual', selected=not is_sunset)} "
        f"{SelectPill(t('cc_display.nl_sched_sunset'), 'cc_desk:nl_sched:sunset', selected=is_sunset)}",
        right_markup=sched_field,
        requires=["wlsunset"],
    )
    nl_box.add_kv(
        t("cc_display.dpms_screen_off"),
        " ".join(SelectPill(f"{m}m" if m else t("cc_display.never"), f"cc_desk:dpms:{m}", selected=(dpms_min == m)) for m in (5, 10, 15, 30, 0)),
        right_markup=ActionBtn(t("cc_display.sleep_now"), "cc_desk:dpms_now", tone="warn"),
        requires=["swayidle"],
    )
    cards.append(nl_box)

    has_active_output = False
    for out_name, info in sorted(outputs_data.items()):
        logical = info.get("logical") or {}
        if not logical:
            continue
        has_active_output = True
        cur_scale = float(logical.get("scale", 1.0))
        modes = info.get("modes") or []
        cur_idx = info.get("current_mode")
        vrr_on = bool(info.get("vrr_enabled", False))
        vrr_od = bool(info.get("vrr_on_demand", False))
        vrr_state = "on-demand" if vrr_od else ("on" if vrr_on else "off")
        scale_presets = (1.0, 1.25, 1.5, 1.75, 2.0)
        is_custom_scale = all(abs(cur_scale - sv) >= 0.03 for sv in scale_presets)

        s_box = Box(
            f"{out_name} ({info.get('model', 'Monitor')})",
            icon="\uf108",
            highlighted=False,
        )
        scale_btns = " ".join(SelectPill(f"{sv:g}x", f"cc_desk:scale:{out_name}:{sv:g}", selected=(abs(cur_scale - sv) < 0.03)) for sv in scale_presets)
        custom_scale_field = InlineField(
            f"cc_disp_scale_{out_name}",
            f"cc_desk:scale_inline:{out_name}",
            value=(f"{cur_scale:g}x" if is_custom_scale else ""),
            empty_label=t("cc_display.custom"),
            placeholder="1.25",
            width=8,
            icon="\u270e",
            tone="neutral",
            selected=is_custom_scale,
        )
        s_box.add_kv(t("cc_display.scaling_factor"), f"{scale_btns} {custom_scale_field}")
        s_box.add_kv(
            t("cc_display.variable_refresh_vrr"),
            f"{SelectPill(t('cc_display.vrr_off'), f'cc_desk:vrr:{out_name}:off', selected=(vrr_state == 'off'))} "
            f"{SelectPill(t('cc_display.vrr_on'), f'cc_desk:vrr:{out_name}:on', selected=(vrr_state == 'on'))} "
            f"{SelectPill(t('cc_display.vrr_on_demand'), f'cc_desk:vrr:{out_name}:on-demand', selected=(vrr_state == 'on-demand'))}",
        )

        res_groups = {}
        for idx, m in enumerate(modes):
            w, h = int(m.get("width", 0)), int(m.get("height", 0))
            if w <= 0 or h <= 0:
                continue
            rr = m.get("refresh_rate", 0)
            hz = rr / 1000.0 if rr > 1000 else float(rr)
            res_groups.setdefault((w, h), []).append((idx, hz))

        cur_w, cur_h, cur_hz = int(logical.get("width", 0)), int(logical.get("height", 0)), 0.0
        if cur_idx is not None and 0 <= cur_idx < len(modes):
            cm = modes[cur_idx]
            cur_w, cur_h = int(cm.get("width", cur_w)), int(cm.get("height", cur_h))
            crr = cm.get("refresh_rate", 0)
            cur_hz = crr / 1000.0 if crr > 1000 else float(crr)

        def _fmt_hz(hz_val: float) -> str:
            return f"{hz_val:.3f}".rstrip("0").rstrip(".")

        hz_pills, seen_hz = [], set()
        for idx, hz in sorted(res_groups.get((cur_w, cur_h), []), key=lambda x: x[1], reverse=True):
            hz_spec = _fmt_hz(hz)
            if hz_spec in seen_hz:
                continue
            seen_hz.add(hz_spec)
            hz_lbl = f"{hz:.0f}Hz" if abs(hz - round(hz)) < 0.05 else f"{hz:.2f}Hz"
            hz_pills.append(SelectPill(hz_lbl, f"cc_desk:res:{out_name}:{cur_w}x{cur_h}@{hz_spec}", selected=(idx == cur_idx)))
        if hz_pills:
            s_box.add_kv(t("cc_display.refresh_rate"), " ".join(hz_pills[:6]))

        sorted_res = sorted(res_groups.keys(), key=lambda wh: (wh[0] * wh[1], wh[0]), reverse=True)
        res_btns = []
        for w, h in sorted_res:
            _best_idx, best_hz = max(res_groups[(w, h)], key=lambda x: x[1])
            m_spec = f"{w}x{h}@{_fmt_hz(best_hz)}"
            res_btns.append(SelectPill(f"{w}x{h}", f"cc_desk:res:{out_name}:{m_spec}", selected=(w == cur_w and h == cur_h)))

        cur_summary = f"{cur_w}x{cur_h}@{cur_hz:.0f}Hz" if cur_hz > 0 else f"{cur_w}x{cur_h}"

        def _build_modes(b: Box, btns=res_btns):
            if not btns:
                return
            rows = []
            for i in range(0, len(btns), 3):
                chunk = btns[i:i + 3]
                while len(chunk) < 3:
                    chunk.append("")
                rows.append(chunk)
            b.add_table(
                headers=["", "", ""],
                widths=[20, 20, 20],
                rows=rows,
                aligns=["<", "<", "<"],
                show_headers=False,
            )

        s_box.add_collapsible(
            cc_panel,
            f"disp_modes_{out_name}",
            t("cc_display.hardware_modes", p0=len(res_btns)),
            _build_modes,
            summary=cur_summary,
        )
        cards.append(s_box)

    if not has_active_output:
        empty_box = Box(t("cc_display.status"), icon="\uf108", highlighted=False)
        empty_box.add_row(f"  {Txt(t('cc_display.no_active_outputs_scaling'), role='inactive')}")
        cards.append(empty_box)
    return cards


def build_cards(cc_panel, canvas: Canvas) -> list:
    return _build_scaling_standard(cc_panel, canvas, get_niri_outputs_data())
