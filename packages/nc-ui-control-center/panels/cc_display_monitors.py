#!/usr/bin/env python3
from core.i18n import t
from core.state import STATE
from ui.output_map import NiriOutputMap
from ui.canvas import Canvas
from ui.kit import ActionBtn, Box, InlineField, SelectPill, Slider, StateBadge, Txt
from ui.widgets import AsciiCard
from panels.cc_engine_display import (
    _feedback_cards,
    _get_night_light_state,
    get_backlight_info,
    get_niri_outputs_data,
)


def _parse_transform(raw: str) -> tuple[str, bool]:
    s = str(raw or "normal").strip().lower().replace("_", "-")
    is_flipped = "flipped" in s
    s = s.replace("flipped-", "").replace("flipped", "").strip("-")
    base = s if s in ("90", "180", "270") else "normal"
    return base, is_flipped


def _compose_transform(base: str, flipped: bool) -> str:
    if not flipped:
        return base
    return "flipped" if base == "normal" else f"flipped-{base}"


def _fmt_hz(hz_val: float) -> str:
    return f"{hz_val:.3f}".rstrip("0").rstrip(".")


def _build_night_light_dpms_box(canvas: Canvas) -> Box:
    nl = _get_night_light_state()
    dpms_min = int(STATE.get("display", "dpms_timeout", 10) or 10)

    if nl["active"] and nl.get("in_night", True):
        nl_on_lbl = t("cc_display.night_k", p0=nl["temp"])
    elif nl["active"] and nl["schedule"] == "manual":
        nl_on_lbl = t("cc_display.nl_scheduled_badge", p0=nl["sunset"])
    else:
        nl_on_lbl = t("cc_display.nl_scheduled_sunset_badge")

    nl_box = Box(
        t("cc_display.night_light_screen_dpms"),
        icon="\uf186",
        actions=[StateBadge(nl["active"], nl_on_lbl, t("cc_display.daylight"), on_tone="warn")],
    )
    nl_temp_txt = Txt(f"{nl['temp']}K", role="val" if nl["active"] else "inactive")
    slider_w = 16 if canvas.inner_cols < 76 else 20
    nl_step = 300 if slider_w < 20 else 250
    nl_box.add_kv(
        t("cc_display.night_light_wlsunset"),
        f"{SelectPill(t('common.on'), 'cc_desk:nl:on', selected=nl['active'])} "
        f"{SelectPill(t('common.off'), 'cc_desk:nl:off', selected=not nl['active'])}  "
        + Slider(nl["temp"], "cc_desk:nl_temp", min_val=1700, max_val=6500, width=slider_w, show_pct=False, step_buttons=True, step=nl_step, enabled=bool(nl["active"]))
        + f" {nl_temp_txt}",
    )
    sched = nl["schedule"]
    if sched == "sunset":
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
    elif sched == "manual":
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
    else:
        sched_field = ""
    nl_box.add_kv(
        t("cc_display.nl_schedule"),
        f"{SelectPill(t('cc_display.nl_sched_always'), 'cc_desk:nl_sched:always', selected=(sched == 'always'))} "
        f"{SelectPill(t('cc_display.nl_sched_manual'), 'cc_desk:nl_sched:manual', selected=(sched == 'manual'))} "
        f"{SelectPill(t('cc_display.nl_sched_sunset'), 'cc_desk:nl_sched:sunset', selected=(sched == 'sunset'))}",
        right_markup=sched_field,
    )
    if nl["active"]:
        if sched == "sunset" and not (nl["lat"] and nl["lon"]):
            nl_box.add_row(f"  {Txt(t('cc_display.nl_coords_missing_hint'), role='warn')}")
        elif not nl.get("in_night", True):
            hint_txt = t("cc_display.nl_daytime_hint", p0=nl["sunset"]) if sched == "manual" else t("cc_display.nl_sunset_daytime_hint")
            nl_box.add_row(f"  {Txt(hint_txt, role='warn')}")
    nl_box.add_kv(
        t("cc_display.dpms_screen_off"),
        " ".join(SelectPill(f"{m}m" if m else t("cc_display.never"), f"cc_desk:dpms:{m}", selected=(dpms_min == m)) for m in (5, 10, 15, 30, 0)),
        right_markup=ActionBtn(t("cc_display.sleep_now"), "cc_desk:dpms_now", tone="warn"),
        requires=["swayidle"],
    )
    return nl_box


def _build_selected_output_box(cc_panel, canvas: Canvas, out_name: str, info: dict) -> Box:
    logical = info.get("logical")
    is_on = logical is not None
    modes = info.get("modes") or []
    cur_idx = info.get("current_mode")
    scale_presets = (1.0, 1.25, 1.5, 1.75, 2.0)
    col_w = 18 if canvas.inner_cols < 84 else 20
    max_hz_pills = 3 if canvas.inner_cols < 76 else (4 if canvas.inner_cols < 84 else 6)

    o_box = Box(
        f"{out_name} \u2014 {info.get('model') or t('cc_display.display_fallback')}",
        icon="\uf26c",
        actions=[
            StateBadge(is_on, t("cc_display.enabled"), t("common.off")),
            ActionBtn(
                t("cc_display.disable") if is_on else t("cc_display.enable"),
                f"cc_desk:power:{out_name}:{'off' if is_on else 'on'}",
                tone="danger_soft" if is_on else "apply",
            ),
        ],
        highlighted=False,
    )
    if not is_on:
        o_box.add_kv(t("cc_display.status"), Txt(t("cc_display.output_powered_off_in_niri"), role="inactive"))
        return o_box

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

    hz_pills, seen_hz = [], set()
    for idx, hz in sorted(res_groups.get((cur_w, cur_h), []), key=lambda x: x[1], reverse=True):
        hz_spec = _fmt_hz(hz)
        if hz_spec in seen_hz:
            continue
        seen_hz.add(hz_spec)
        hz_lbl = f"{hz:.0f}Hz" if abs(hz - round(hz)) < 0.05 else f"{hz:.2f}Hz"
        hz_pills.append(SelectPill(hz_lbl, f"cc_desk:res:{out_name}:{cur_w}x{cur_h}@{hz_spec}", selected=(idx == cur_idx)))

    cur_summary = f"{cur_w}x{cur_h}@{cur_hz:.0f}Hz" if cur_hz > 0 else f"{cur_w}x{cur_h}"
    mode_val = f"{Txt(f'{cur_w}x{cur_h}', role='val')}  {' '.join(hz_pills[:max_hz_pills])}" if hz_pills else Txt(cur_summary, role="val")
    o_box.add_kv(
        t("cc_display.active_mode"),
        mode_val,
        right_markup=ActionBtn(t("cc_display.native_max"), f"cc_desk:res_max:{out_name}", tone="apply"),
    )

    cur_scale = float(logical.get("scale", 1.0))
    is_custom_scale = all(abs(cur_scale - sv) >= 0.03 for sv in scale_presets)
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
    eff_res_r = Txt(f"{logical.get('width', 0)}x{logical.get('height', 0)}", role="schema") if canvas.inner_cols >= 84 else ""
    o_box.add_kv(t("cc_display.scaling_factor"), f"{scale_btns} {custom_scale_field}", right_markup=eff_res_r)

    pos_placeholder = f"{logical.get('x', 0)},{logical.get('y', 0)}"
    o_box.add_kv(
        t("cc_display.logical_pos"),
        Txt(t("cc_display.pos_coords", p0=logical.get("x", 0), p1=logical.get("y", 0)), role="val"),
        right_markup=(
            f"{InlineField(f'cc_disp_pos_{out_name}', f'cc_desk:pos_inline:{out_name}', value='', empty_label=t('cc_display.set_pos'), placeholder=pos_placeholder, width=10, icon='\u270e', tone='neutral')} "
            f"{ActionBtn(t('cc_display.auto'), f'cc_desk:pos_auto:{out_name}', tone='neutral')}"
        ),
    )

    base_rot, is_flipped = _parse_transform(logical.get("transform", "normal"))
    rot_pills = " ".join(
        SelectPill(
            t(f"cc_display.tr_{r}"),
            f"cc_desk:transform:{out_name}:{_compose_transform(r, is_flipped)}",
            selected=(base_rot == r),
        )
        for r in ("normal", "90", "180", "270")
    )
    flip_pill = SelectPill(
        t("cc_display.tr_flipped"),
        f"cc_desk:transform:{out_name}:{_compose_transform(base_rot, not is_flipped)}",
        selected=is_flipped,
    )
    o_box.add_kv(t("cc_display.transform"), f"{rot_pills}  {flip_pill}")

    sorted_res = sorted(res_groups.keys(), key=lambda wh: (wh[0] * wh[1], wh[0]), reverse=True)
    res_btns = []
    for w, h in sorted_res:
        _best_idx, best_hz = max(res_groups[(w, h)], key=lambda x: x[1])
        m_spec = f"{w}x{h}@{_fmt_hz(best_hz)}"
        res_btns.append(SelectPill(f"{w}x{h}", f"cc_desk:res:{out_name}:{m_spec}", selected=(w == cur_w and h == cur_h)))

    def _build_modes(b: Box, btns=res_btns, cw=col_w):
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
            widths=[cw, cw, cw],
            rows=rows,
            aligns=["<", "<", "<"],
            show_headers=False,
        )

    o_box.add_collapsible(
        cc_panel,
        f"disp_modes_{out_name}",
        t("cc_display.hardware_modes", p0=len(res_btns)),
        _build_modes,
        summary=cur_summary,
    )
    return o_box


def _build_backlight_box(canvas: Canvas) -> Box | None:
    bl = get_backlight_info()
    if not bl:
        return None
    bl_box = Box(
        t("cc_display.backlight"),
        icon="\uf185",
        actions=[Txt(bl["device"], role="schema")],
        highlighted=False,
    )
    sw = 16 if canvas.inner_cols < 76 else 20
    bl_step = 10 if sw < 20 else 5
    presets = " ".join(
        SelectPill(f"{p}%", f"cc_desk:backlight_preset:{p}", selected=(abs(bl["pct"] - p) <= 2))
        for p in (25, 50, 75, 100)
    )
    bl_box.add_kv(
        t("cc_display.brightness"),
        Slider(bl["pct"], "cc_desk:backlight", min_val=5, max_val=100, width=sw, show_pct=True, step_buttons=True, step=bl_step)
        + f"  {presets}",
    )
    return bl_box


def _build_layout_standard(cc_panel, canvas: Canvas, outputs_data: dict) -> list:
    cards = _feedback_cards()
    cards.append(_build_night_light_dpms_box(canvas))
    bl_box = _build_backlight_box(canvas)
    if bl_box is not None:
        cards.append(bl_box)

    out_names = sorted(outputs_data.keys())
    hdr = Box(
        t("cc_display.topology_title", p0=len(out_names)),
        icon="\uf108",
        actions=[
            ActionBtn(t("cc_display.auto_pos_all"), "cc_desk:pos_auto_all", tone="neutral"),
        ],
        highlighted=False,
    )

    if not out_names:
        hdr.add_row(f"  {Txt(t('cc_display.no_outputs_detected'), role='inactive')}")
        cards.append(hdr)
        return cards

    sel_out = str(STATE.get("display", "selected_output", "") or "")
    if sel_out not in outputs_data:
        sel_out = next((o for o in out_names if outputs_data[o].get("logical") is not None), out_names[0])

    off_tag = t("common.off").upper()
    out_items = []
    for o in out_names:
        lbl = o if outputs_data[o].get("logical") is not None else f"{o} ({off_tag})"
        out_items.append((lbl, SelectPill(lbl, f"cc_desk:select_out:{o}", selected=(o == sel_out))))

    max_row_w = max(36, canvas.inner_cols - 22)
    chunks: list[list[str]] = []
    cur_chunk: list[str] = []
    cur_w = 0
    for lbl, pill in out_items:
        pw = len(lbl) + 5
        if cur_chunk and cur_w + pw > max_row_w:
            chunks.append(cur_chunk)
            cur_chunk, cur_w = [pill], pw
        else:
            cur_chunk.append(pill)
            cur_w += pw
    if cur_chunk:
        chunks.append(cur_chunk)

    hdr.add_kv(t("cc_display.select_output_kv"), " ".join(chunks[0]))
    cont_indent = " " * max(18, len(t("cc_display.select_output_kv")) + 6)
    for extra in chunks[1:]:
        hdr.add_row(f"{cont_indent}{' '.join(extra)}")

    if any(v.get("logical") for v in outputs_data.values()):
        hdr.add_row(" ")
        hdr.open_bottom = True
        map_h = max(264, int(round(canvas.char_h * 15.5)))
        topo_widget = NiriOutputMap(
            outputs_data,
            refresh_cb=(lambda: cc_panel.shell.refresh_view() if cc_panel.shell else None),
            map_width=int(round(canvas.total_px)),
            padding=max(12, int(round(canvas.char_w * 1.5))),
            fixed_height=map_h,
            selected_output=sel_out,
            on_select=(lambda target_out: cc_panel.on_link(f"cc_desk:select_out:{target_out}") if cc_panel else None),
            inset_x=max(14, int(round(canvas.char_w * 2))),
            font_px=max(10.0, float(canvas.char_w) * 1.45),
            show_footer=True,
        )
        foot = Box("", highlighted=False)
        foot.open_top = True
        foot.add_row(" ")
        cards.append(AsciiCard(hdr, canvas, embedded_widget=topo_widget, footer_box_model=foot))
    else:
        hdr.add_row(f"  {Txt(t('cc_display.no_active_outputs'), role='inactive')}")
        cards.append(hdr)

    cards.append(_build_selected_output_box(cc_panel, canvas, sel_out, outputs_data[sel_out]))
    return cards


def build_cards(cc_panel, canvas: Canvas) -> list:
    return _build_layout_standard(cc_panel, canvas, get_niri_outputs_data())
