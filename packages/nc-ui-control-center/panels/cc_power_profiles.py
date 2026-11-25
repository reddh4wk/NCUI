#!/usr/bin/env python3
import math
import os
import time
import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GLib", "2.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, GLib, Gtk, Pango, PangoCairo

from core.config import THEME
from core.i18n import t
from core.privileges import build_privilege_modal_card
from core.state import STATE
from ui.canvas import Canvas
from ui.kit import (
    ActionBtn,
    Badge,
    Box,
    FoldToggle,
    InlineField,
    ProgressBar,
    SelectPill,
    Slider,
    StateBadge,
    SwatchTxt,
    Txt,
    get_semantic_palette,
)
from ui.widgets import AsciiCard, _parse_rgba_components
from panels.cc_engine_power import (
    _collect_batteries_and_devices,
    _collect_cpu_power,
    _collect_idle_state,
    _feedback_cards,
    sample_thermal_history,
)

_PPD_MODES = (
    ("performance", "cc_power.mode_performance"),
    ("balanced", "cc_power.mode_balanced"),
    ("power-saver", "cc_power.mode_power_saver"),
)

_EPP_MODES = (
    ("default", "cc_power.epp_default"),
    ("performance", "cc_power.epp_performance"),
    ("balance_performance", "cc_power.epp_bal_perf"),
    ("balance_power", "cc_power.epp_bal_power"),
    ("power", "cc_power.epp_power"),
)

_FAN_PRESETS = (
    ("silent", "cc_power.fan_silent"),
    ("balanced", "cc_power.fan_balanced"),
    ("cool", "cc_power.fan_cool"),
)

_FAN_STAGE_KEYS = (
    "cc_power.fan_st_1",
    "cc_power.fan_st_2",
    "cc_power.fan_st_3",
    "cc_power.fan_st_4",
    "cc_power.fan_st_5",
)


def _thermal_palette(pal: dict) -> tuple[str, ...]:
    return (
        pal["intent"],
        pal["attention"],
        pal["vitality"],
        pal["interrupt"],
        "#7aa2f7",
        "#73daca",
        "#ff9e64",
        "#bb9af7",
        "#9ece6a",
        "#89ddff",
        "#f7768e",
        pal["schema"],
    )


def _trace_rect(cr, x: float, y: float, w: float, h: float, r: float = 4.0) -> None:
    rad = max(0.0, min(r, w * 0.5, h * 0.5))
    cr.new_sub_path()
    cr.arc(x + w - rad, y + rad, rad, -math.pi * 0.5, 0.0)
    cr.arc(x + w - rad, y + h - rad, rad, 0.0, math.pi * 0.5)
    cr.arc(x + rad, y + h - rad, rad, math.pi * 0.5, math.pi)
    cr.arc(x + rad, y + rad, rad, math.pi, 1.5 * math.pi)
    cr.close_path()


class _ThermalGraphWidget(Gtk.DrawingArea):
    """Self-ticking 1 Hz Cairo multi-sensor temperature chart framed inside AsciiCard."""

    def __init__(self, canvas: Canvas, enabled_ids: list[str], width_px: int):
        super().__init__()
        self.canvas = canvas
        self.enabled_ids = list(enabled_ids)
        self._hover_x: float | None = None
        self._hover_y: float | None = None
        self._timer_id: int | None = None

        self.set_size_request(max(320, int(width_px)), 196)
        self.set_hexpand(False)
        self.set_vexpand(False)
        self.add_events(Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK)
        self.connect("draw", self._on_draw)
        self.connect("motion-notify-event", self._on_motion)
        self.connect("leave-notify-event", self._on_leave)
        self.connect("destroy", self._on_destroy)
        self._timer_id = GLib.timeout_add(1000, self._on_tick)

    def _on_tick(self) -> bool:
        if not self.get_realized() and self._timer_id is None:
            return False
        sample_thermal_history(min_interval=0.90)
        self.queue_draw()
        return True

    def _on_destroy(self, *_args) -> None:
        if self._timer_id is not None:
            try:
                GLib.source_remove(self._timer_id)
            except Exception:
                pass
            self._timer_id = None

    def _on_motion(self, _w, ev) -> bool:
        self._hover_x = float(ev.x)
        self._hover_y = float(ev.y)
        self.queue_draw()
        return False

    def _on_leave(self, _w, _ev) -> bool:
        self._hover_x = None
        self._hover_y = None
        self.queue_draw()
        return False

    def _make_font(self) -> Pango.FontDescription:
        return Pango.FontDescription.from_string(
            f"{self.canvas.font_family}, Font Awesome 6 Free, Symbols Nerd Font, monospace {self.canvas.font_weight} {self.canvas.font_size}"
        )

    def _on_draw(self, widget, cr) -> bool:
        alloc = widget.get_allocation()
        w, h = float(alloc.width), float(alloc.height)
        if w < 160 or h < 110:
            return False

        snap = sample_thermal_history(min_interval=0.90)
        sensors = snap["sensors"]
        history = snap["history"]
        enabled_set = set(self.enabled_ids)

        pal = get_semantic_palette()
        colors = _thermal_palette(pal)
        active_sensors = [
            (idx, s, colors[idx % len(colors)])
            for idx, s in enumerate(sensors)
            if s["id"] in enabled_set
        ]

        ax_lay = PangoCairo.create_layout(cr)
        ax_lay.set_attributes(self.canvas._no_liga_attrs)
        ax_lay.set_font_description(self._make_font())
        ax_lay.set_text("100°", -1)
        _, ax_log = ax_lay.get_pixel_extents()
        y_lbl_px = float(ax_log.width)
        ax_h = float(ax_log.height)

        cw = float(self.canvas.char_w)
        inset_x = max(14.0, round(cw * 2.0))
        side_pad = math.floor(inset_x + y_lbl_px + 8.0)
        px0 = side_pad + 0.5
        pw = max(120.0, math.floor(w - 2.0 * side_pad))
        px1 = px0 + pw
        py0 = 8.5
        bot_axis_h = math.ceil(ax_h) + 8.0
        ph = max(90.0, math.floor(h - py0 - bot_axis_h))
        py1 = py0 + ph

        sbg_r, sbg_g, sbg_b, _ = _parse_rgba_components(pal["structure_bg"])
        str_r, str_g, str_b, _ = _parse_rgba_components(pal["structure"])
        foc_r, foc_g, foc_b, _ = _parse_rgba_components(pal["focus_val"])

        cr.set_source_rgba(sbg_r / 255.0, sbg_g / 255.0, sbg_b / 255.0, 0.90)
        _trace_rect(cr, px0, py0, pw, ph, 4.0)
        cr.fill()

        if not active_sensors:
            cr.set_source_rgba(str_r / 255.0, str_g / 255.0, str_b / 255.0, 0.95)
            cr.set_line_width(1.0)
            _trace_rect(cr, px0, py0, pw, ph, 4.0)
            cr.stroke()
            ax_lay.set_markup(f"<span color='{pal['schema']}'>{Canvas.escape(t('cc_power.therm_none_selected'))}</span>", -1)
            _, m_log = ax_lay.get_pixel_extents()
            cr.move_to(px0 + (pw - float(m_log.width)) * 0.5, py0 + (ph - float(m_log.height)) * 0.5)
            PangoCairo.show_layout(cr, ax_lay)
            return False

        # Compute Y-axis range from enabled series history
        vis_vals: list[float] = []
        for _ts, readings in history:
            for _idx, s, _col in active_sensors:
                v = readings.get(s["id"])
                if v is not None:
                    vis_vals.append(v)
        if not vis_vals:
            vis_vals = [30.0, 75.0]

        v_min = min(vis_vals)
        v_max = max(vis_vals)
        y_lo = max(0, int(math.floor((v_min - 4.0) / 10.0) * 10))
        y_hi = int(math.ceil((v_max + 5.0) / 10.0) * 10)
        if (y_hi - y_lo) < 30:
            mid = int(round(((v_min + v_max) * 0.5) / 10.0) * 10)
            y_lo = max(0, mid - 20)
            y_hi = y_lo + 40
        y_span = float(max(10, y_hi - y_lo))
        y_step = 20 if y_span > 65.0 else 10

        def _val_to_y(val_c: float) -> float:
            frac = max(0.0, min(1.0, (val_c - float(y_lo)) / y_span))
            return py1 - 6.0 - frac * (ph - 12.0)

        # Horizontal dashed temperature grid & symmetrical Y-axis labels (left & right)
        first_tick = ((y_lo + y_step - 1) // y_step) * y_step
        for tk in range(first_tick, y_hi + 1, y_step):
            gy = math.floor(_val_to_y(float(tk))) + 0.5
            if (py0 + 4.0) <= gy <= (py1 - 4.0):
                cr.save()
                cr.set_source_rgba(str_r / 255.0, str_g / 255.0, str_b / 255.0, 0.45)
                cr.set_line_width(1.0)
                cr.set_dash([3.0, 4.0])
                cr.move_to(px0 + 1.0, gy)
                cr.line_to(px1 - 1.0, gy)
                cr.stroke()
                cr.restore()

            ax_lay.set_markup(f"<span color='{pal['schema']}'>{tk}°</span>", -1)
            _, t_log = ax_lay.get_pixel_extents()
            ly_txt = max(1.0, min(py1 - float(t_log.height), gy - float(t_log.height) * 0.5))
            cr.move_to(px0 - float(t_log.width) - 6.0, ly_txt)
            PangoCairo.show_layout(cr, ax_lay)
            cr.move_to(px1 + 6.0, ly_txt)
            PangoCairo.show_layout(cr, ax_lay)

        # X-axis time ticks (-60s .. 0s) at full UI font size
        for sec in (-60, -45, -30, -15, 0):
            gx = px0 + ((sec + 60.0) / 60.0) * pw
            lbl = "0s" if sec == 0 else f"{sec}s"
            ax_lay.set_markup(f"<span color='{pal['schema']}'>{lbl}</span>", -1)
            _, x_log = ax_lay.get_pixel_extents()
            xw = float(x_log.width)
            if sec == -60:
                tx = px0
            elif sec == 0:
                tx = px1 - xw
            else:
                tx = max(px0, min(px1 - xw, gx - xw * 0.5))
            cr.move_to(tx, py1 + 4.0)
            PangoCairo.show_layout(cr, ax_lay)

        if history:
            first_ts = history[0][0]
            latest_ts = history[-1][0]

            def _ts_to_x(ts_val: float) -> float:
                return px0 + ((ts_val - (latest_ts - 60.0)) / 60.0) * pw

            cr.save()
            _trace_rect(cr, px0 + 0.5, py0 + 0.5, pw - 1.0, ph - 1.0, 3.5)
            cr.clip()

            # Vertical 15s subtle guide lines
            for sec in (-45, -30, -15):
                vx = math.floor(px0 + ((sec + 60.0) / 60.0) * pw) + 0.5
                cr.set_source_rgba(str_r / 255.0, str_g / 255.0, str_b / 255.0, 0.25)
                cr.set_line_width(1.0)
                cr.move_to(vx, py0 + 1.0)
                cr.line_to(vx, py1 - 1.0)
                cr.stroke()

            use_fill = len(active_sensors) <= 2
            for _idx, s, col_hex in active_sensors:
                pts = [
                    (_ts_to_x(ts_v), _val_to_y(val_c), val_c)
                    for ts_v, readings in history
                    if (val_c := readings.get(s["id"])) is not None
                ]
                if not pts:
                    continue

                sr, sg, sb, _ = _parse_rgba_components(col_hex)
                if len(pts) == 1:
                    cy = pts[0][1]
                    cr.save()
                    cr.set_source_rgba(sr / 255.0, sg / 255.0, sb / 255.0, 0.42)
                    cr.set_line_width(1.2)
                    cr.set_dash([4.0, 4.0])
                    cr.move_to(px0, cy)
                    cr.line_to(px1, cy)
                    cr.stroke()
                    cr.restore()
                    cr.set_source_rgba(sr / 255.0, sg / 255.0, sb / 255.0, 1.0)
                    cr.arc(px1 - 2.5, cy, 3.0, 0.0, 2.0 * math.pi)
                    cr.fill()
                    continue

                if pts[0][0] > px0 and (latest_ts - first_ts) >= 58.0:
                    pts.insert(0, (px0, pts[0][1], pts[0][2]))
                if pts[-1][0] < px1:
                    pts.append((px1, pts[-1][1], pts[-1][2]))

                if use_fill:
                    cr.new_sub_path()
                    cr.move_to(pts[0][0], py1)
                    for px, py, _v in pts:
                        cr.line_to(px, py)
                    cr.line_to(pts[-1][0], py1)
                    cr.close_path()
                    cr.set_source_rgba(sr / 255.0, sg / 255.0, sb / 255.0, 0.07)
                    cr.fill()

                cr.new_sub_path()
                cr.move_to(pts[0][0], pts[0][1])
                for px, py, _v in pts[1:]:
                    cr.line_to(px, py)
                cr.set_source_rgba(sr / 255.0, sg / 255.0, sb / 255.0, 0.95)
                cr.set_line_width(1.6)
                cr.set_line_join(1)
                cr.set_line_cap(1)
                cr.stroke()

                cr.arc(px1 - 2.5, pts[-1][1], 2.4, 0.0, 2.0 * math.pi)
                cr.fill()

            # Hover crosshair + compact tooltip
            hx, hy = self._hover_x, self._hover_y
            if hx is not None and hy is not None and px0 <= hx <= px1 and py0 <= hy <= py1:
                best_item = min(history, key=lambda item: abs(_ts_to_x(item[0]) - hx))
                b_ts, b_read = best_item
                bx = max(px0 + 1.0, min(px1 - 1.0, _ts_to_x(b_ts)))
                rel_s = int(round(b_ts - latest_ts))

                cr.save()
                cr.set_source_rgba(foc_r / 255.0, foc_g / 255.0, foc_b / 255.0, 0.45)
                cr.set_line_width(1.0)
                cr.set_dash([3.0, 3.0])
                cr.move_to(bx, py0 + 2.0)
                cr.line_to(bx, py1 - 2.0)
                cr.stroke()
                cr.restore()

                tip_entries = []
                for _idx, s, col_hex in active_sensors:
                    v = b_read.get(s["id"])
                    if v is None:
                        continue
                    vy = _val_to_y(v)
                    sr, sg, sb, _ = _parse_rgba_components(col_hex)
                    cr.set_source_rgba(sr / 255.0, sg / 255.0, sb / 255.0, 1.0)
                    cr.arc(bx, vy, 3.0, 0.0, 2.0 * math.pi)
                    cr.fill()
                    tip_entries.append((s["label"], f"{v:.1f}°C", col_hex))

                if tip_entries:
                    tip_lay = PangoCairo.create_layout(cr)
                    tip_lay.set_attributes(self.canvas._no_liga_attrs)
                    tip_lay.set_font_description(self._make_font())
                    n_cols = 2 if len(tip_entries) > 5 else 1
                    col_rows = math.ceil(len(tip_entries) / n_cols)
                    sample_row_h = max(14.0, ax_h + 1.0)
                    tip_lay.set_text("CPU CCD1 100.0°C", -1)
                    _, sample_log = tip_lay.get_pixel_extents()
                    col_w = max(116.0, float(sample_log.width) + 18.0)
                    tip_w = n_cols * col_w + 14.0
                    tip_h = sample_row_h + col_rows * sample_row_h + 10.0
                    tx = bx + 10.0
                    if tx + tip_w > px1 - 4.0:
                        tx = max(px0 + 4.0, bx - tip_w - 10.0)
                    ty = max(py0 + 4.0, min(py1 - tip_h - 4.0, py0 + 6.0))

                    cr.set_source_rgba(sbg_r / 255.0, sbg_g / 255.0, sbg_b / 255.0, 0.95)
                    _trace_rect(cr, tx, ty, tip_w, tip_h, 4.0)
                    cr.fill_preserve()
                    cr.set_source_rgba(str_r / 255.0, str_g / 255.0, str_b / 255.0, 1.0)
                    cr.set_line_width(1.0)
                    cr.stroke()

                    hdr_lbl = "0s" if rel_s == 0 else f"{rel_s}s"
                    tip_lay.set_markup(f"<span color='{pal['focus']}'>t = {hdr_lbl}</span>", -1)
                    cr.move_to(tx + 7.0, ty + 4.0)
                    PangoCairo.show_layout(cr, tip_lay)

                    for i, (lbl, v_str, col_hex) in enumerate(tip_entries):
                        c_i = i // col_rows
                        r_i = i % col_rows
                        ex = tx + 7.0 + c_i * col_w
                        ey = ty + 4.0 + sample_row_h + r_i * sample_row_h
                        sr, sg, sb, _ = _parse_rgba_components(col_hex)
                        cr.set_source_rgba(sr / 255.0, sg / 255.0, sb / 255.0, 1.0)
                        cr.arc(ex + 3.0, ey + ax_h * 0.5, 2.5, 0.0, 2.0 * math.pi)
                        cr.fill()
                        tip_lay.set_markup(
                            f"<span color='{pal['schema']}'>{Canvas.escape(lbl)}</span> "
                            f"<span color='{pal['focus_val']}'>{Canvas.escape(v_str)}</span>",
                            -1,
                        )
                        cr.move_to(ex + 9.0, ey)
                        PangoCairo.show_layout(cr, tip_lay)

            cr.restore()

        # Crisp plot border drawn on top so clipped waves sit cleanly flush inside it
        cr.set_source_rgba(str_r / 255.0, str_g / 255.0, str_b / 255.0, 0.95)
        cr.set_line_width(1.0)
        _trace_rect(cr, px0, py0, pw, ph, 4.0)
        cr.stroke()
        return False


def _build_thermal_graph_card(cc_panel, canvas: Canvas):
    therm = sample_thermal_history()
    sensors = therm["sensors"]
    if not sensors:
        return None

    enabled_set = set(therm["enabled"])
    graph_open, graph_fold = FoldToggle(cc_panel, "pwr_therm_graph", default=True, uri_prefix="ui:state")
    hdr_acts = [Txt("1 Hz · 60s", role="schema")]
    if graph_open:
        hdr_acts.append(ActionBtn(t("cc_power.therm_all"), "cc_pwr:therm_all", tone="neutral"))
        hdr_acts.append(ActionBtn(t("cc_power.therm_core"), "cc_pwr:therm_core", tone="neutral"))
    hdr_acts.append(graph_fold)

    if not graph_open:
        box = Box(t("cc_power.therm_box_title"), icon="\uf2c9", actions=hdr_acts, highlighted=False)
        items = [
            f"{s['label']} {therm['latest'][s['id']]:.0f}°C"
            for s in sensors
            if s["id"] in enabled_set and s["id"] in therm["latest"]
        ]
        summary = "  ·  ".join(items) if items else t("cc_power.therm_none_selected")
        box.add_row(f"  {Txt(canvas.truncate_plain(summary, max(20, canvas.inner_cols - 6)), role='schema')}")
        return box

    hdr = Box(t("cc_power.therm_box_title"), icon="\uf2c9", actions=hdr_acts, highlighted=False)
    hdr.open_bottom = True

    graph_w = _ThermalGraphWidget(canvas, enabled_ids=therm["enabled"], width_px=int(round(canvas.total_px)))

    foot = Box("", highlighted=False)
    foot.open_top = True
    colors = _thermal_palette(get_semantic_palette())
    grid_rows: list[list[str]] = []
    for r_start in range(0, len(sensors), 3):
        row_cells: list[str] = []
        for offset in range(3):
            idx = r_start + offset
            if idx < len(sensors):
                s = sensors[idx]
                is_on = s["id"] in enabled_set
                col_hex = colors[idx % len(colors)]
                sw = SwatchTxt(col_hex, show_hex=False) if is_on else Txt("\u25a1", role="inactive")
                pill = SelectPill(s["label"], f"cc_pwr:therm_tog:{s['id']}", selected=is_on)
                val = therm["latest"].get(s["id"])
                t_str = f"{val:.1f}°C" if val is not None else t("common.nd")
                t_cell = Txt(t_str, role="val" if is_on else "inactive")
                row_cells.extend([f"{sw} {pill}", t_cell])
            else:
                row_cells.extend(["", ""])
        grid_rows.append(row_cells)

    foot.add_table(
        headers=["", "", "", "", "", ""],
        widths=[16, 13, 16, 13, 16, "flex"],
        rows=grid_rows,
        aligns=["left", "left", "left", "left", "left", "left"],
        show_headers=False,
    )

    return AsciiCard(hdr, canvas, embedded_widget=graph_w, footer_box_model=foot)


def _temp_role(temp_c: float) -> str:
    if temp_c >= 85.0:
        return "fault"
    if temp_c >= 75.0:
        return "attention"
    return "val"


def _build_power_cpu_box(cc_panel, st: dict) -> Box:
    ppd_avail = st["ppd_avail"]
    active_mode = st["ppd_active"] if ppd_avail else st["native_preset"]
    hdr_lbl = (active_mode or st["gov"] or t("cc_power.mode_custom")).upper()
    gm = st["posture"]

    hdr_actions = []
    if gm["available"]:
        hdr_actions.append(
            Badge(t("cc_system.gaming_badge"), style="warn")
            if gm["is_gaming"]
            else StateBadge(True, t("cc_system.mode_hardened").upper(), on_tone="ok")
        )
    hdr_actions.append(Txt(hdr_lbl, role="schema"))
    if not ppd_avail and not st["cpu_priv_ok"]:
        hdr_actions.append(ActionBtn(t("cc_power.authorize_priv"), "cc_pwr:auth_cpu", tone="apply"))
    if gm["available"] and not gm["priv_ok"]:
        hdr_actions.append(ActionBtn(t("cc_power.authorize_posture"), "priv:request:gaming_mode", tone="apply"))
    hdr_actions.append(ActionBtn(t("cc_power.refresh"), "cc_pwr:refresh", tone="neutral"))

    box = Box(t("cc_power.power_profiles_title"), icon="\uf0e7", actions=hdr_actions, highlighted=False)

    if gm["available"]:
        post_pills = " ".join([
            SelectPill(t("cc_system.mode_hardened"), "cc_pwr:posture:off", selected=(not gm["is_gaming"])),
            SelectPill(t("cc_system.mode_gaming"), "cc_pwr:posture:on", selected=gm["is_gaming"]),
        ])
        post_right = (
            ActionBtn(t("cc_system.gaming_force_off"), "cc_pwr:posture:off-force", tone="warn")
            if (gm["games_warn"] and gm["is_gaming"])
            else Txt(f"hidepid={gm['hidepid']} · ptrace={gm['ptrace']} · ntsync={'on' if gm['ntsync'] else 'off'}", role="schema")
        )
        box.add_kv(t("cc_power.security_posture"), post_pills, right_markup=post_right)

    if ppd_avail:
        lbl_map = dict(_PPD_MODES)
        mode_pills = " ".join(
            SelectPill(t(lbl_map[p]) if p in lbl_map else p, f"cc_pwr:ppd:{p}", selected=(active_mode == p))
            for p in ppd_avail
        )
    else:
        mode_pills = " ".join(
            SelectPill(t(lk), f"cc_pwr:preset:{key}", selected=(active_mode == key))
            for key, lk in _PPD_MODES
        )
    epp_lbl_map = dict(_EPP_MODES)
    epp_raw = str(st["epp"] or "")
    epp_disp = t(epp_lbl_map[epp_raw]) if epp_raw in epp_lbl_map else (epp_raw.replace("_", " ").title() if epp_raw else "")
    gov_disp = (st["gov"] or "-").capitalize()
    boost_disp = ("ON" if st["boost"] == "1" else "OFF") if st["boost"] in ("0", "1") else ""
    recipe_parts = [f"Gov: {gov_disp}"]
    if epp_disp:
        recipe_parts.append(f"EPP: {epp_disp}")
    if boost_disp:
        recipe_parts.append(f"Boost: {boost_disp}")
    recipe_str = " · ".join(recipe_parts)

    if active_mode:
        mode_right = Txt(t("cc_power.preset_sync_hint"), role="schema")
    else:
        mode_right = Txt(t("cc_power.preset_custom_hint"), role="attention")
    box.add_kv(t("cc_power.power_preset_lbl"), mode_pills, right_markup=mode_right)

    drv = st["driver"] or t("common.nd")
    if st["pstate_status"]:
        drv = f"{drv} ({st['pstate_status']})"
    if ppd_avail:
        drv = f"ppd / {drv}"

    clk_parts = []
    if st["max_ghz"] > 0:
        clk_parts.append(Txt(f"{st['cur_ghz']:.2f} GHz", role="val"))
        meta_tail = f"({st['min_ghz']:.2f}–{st['max_ghz']:.2f} GHz)  ·  {st['cores']}T  ·  {drv}"
    else:
        meta_tail = f"{st['cores']}T  ·  {drv}"
    sep = "  " if clk_parts else ""
    box.add_kv(t("cc_power.clock_and_driver"), f"{''.join(clk_parts)}{sep}{Txt(meta_tail, role='schema')}")

    if not st["fans"] and st["cpu_temp"] is not None:
        t_c = st["cpu_temp"]
        ccd = "  ".join(f"{lbl} {val:.0f}°C" for lbl, val in st["cpu_ccd_temps"])
        ccd_txt = f"  {Txt(f'·  {ccd}', role='schema')}" if ccd else ""
        box.add_kv(t("cc_power.cpu_thermal"), f"{Txt(f'{t_c:.1f}°C', role=_temp_role(t_c))}{ccd_txt}")

    def _build_cpu_registers(bx: Box) -> None:
        gov_pills = " ".join(
            SelectPill(g.capitalize(), f"cc_pwr:gov:{g}", selected=(st["gov"] == g))
            for g in st["avail_gov"][:5]
        )
        gov_hint_key = (
            "cc_power.gov_hint_perf"
            if st["gov"] == "performance"
            else ("cc_power.gov_hint_save" if st["gov"] == "powersave" else "cc_power.gov_hint_sched")
        )
        bx.add_kv(
            t("cc_power.cpu_governor"),
            gov_pills,
            right_markup=Txt(t(gov_hint_key), role="schema"),
            requires=["cpupower"],
        )

        epp_right = Txt(t("cc_power.epp_hint_locked"), role="schema") if st["gov"] == "performance" else ""
        std_epp = [(e, lk) for e, lk in _EPP_MODES if e in st["avail_epp"]]
        if std_epp:
            epp_pills = " ".join(SelectPill(t(lk), f"cc_pwr:epp:{e}", selected=(st["epp"] == e)) for e, lk in std_epp)
            bx.add_kv(t("cc_power.energy_perf_epp"), epp_pills, right_markup=epp_right)
        elif st["avail_epp"]:
            epp_pills = " ".join(
                SelectPill(e.replace("_", " ").title(), f"cc_pwr:epp:{e}", selected=(st["epp"] == e))
                for e in st["avail_epp"][:4]
            )
            bx.add_kv(t("cc_power.energy_perf_epp"), epp_pills, right_markup=epp_right)
        else:
            bx.add_kv(
                t("cc_power.energy_perf_epp"),
                Txt(st["epp"] or t("common.nd"), role="val" if st["epp"] else "inactive"),
            )

        if st["boost"] in ("0", "1"):
            b_on = st["boost"] == "1"
            boost_pills = (
                f"{SelectPill(t('common.on'), 'cc_pwr:boost:1', selected=b_on)} "
                f"{SelectPill(t('common.off'), 'cc_pwr:boost:0', selected=not b_on)}"
            )
            b_hint = Txt(t("cc_power.boost_hint_on" if b_on else "cc_power.boost_hint_off"), role="schema")
            bx.add_kv(t("cc_power.core_boost_cpb"), boost_pills, right_markup=b_hint)

    reg_open, reg_fold = FoldToggle(cc_panel, "pwr_cpu_registers", default=True, uri_prefix="ui:state")
    box.add_section(t("cc_power.cpu_registers_sec"), actions=[reg_fold])
    if reg_open:
        _build_cpu_registers(box)
    else:
        box.add_row(f"  {Txt(recipe_str, role='schema')}")

    def _build_posture_sysctl(bx: Box) -> None:
        if gm["available"]:
            pcfg = gm["cfg"]
            knobs = (
                ("ntsync", "ntsync"),
                ("proc_hidepid", "hidepid=0"),
                ("ptrace_relax", "ptrace=0"),
                ("vm_tuning", "VM/Map"),
                ("suspend_audit", "Audit Off"),
                ("nvidia_persistence", "NVIDIA PM"),
            )
            k_pills = " ".join(
                SelectPill(
                    lbl,
                    f"cc_pwr:posture_knob:{k}:{'false' if pcfg[k] else 'true'}",
                    selected=bool(pcfg[k]),
                )
                for k, lbl in knobs
            )
            bx.add_kv(t("cc_power.posture_tunables"), k_pills)
        swp = st["swappiness"]
        drt = st["dirty_ratio"]
        swp_pills = " ".join(SelectPill(v, f"cc_pwr:sysctl:vm.swappiness:{v}", selected=(swp == v)) for v in ("10", "60", "100"))
        drt_pills = " ".join(SelectPill(v, f"cc_pwr:sysctl:vm.dirty_ratio:{v}", selected=(drt == v)) for v in ("5", "8", "10", "20"))
        bx.add_kv("vm.swappiness", swp_pills if swp in ("10", "60", "100") else f"{Txt(swp, role='val')}  {swp_pills}")
        bx.add_kv("vm.dirty_ratio", drt_pills if drt in ("5", "8", "10", "20") else f"{Txt(drt, role='val')}  {drt_pills}")

    box.add_collapsible(cc_panel, "pwr_posture_sysctl", t("cc_power.posture_sysctl_sec"), _build_posture_sysctl)
    return box


def _build_fan_cooling_box(cc_panel, st: dict) -> Box | None:
    fans = st.get("fans")
    if not fans:
        return None

    hdr_txt = f"{fans['hwmon_name']} · Smart Fan IV" if fans["smart_fan"] else fans["hwmon_name"]
    hdr_actions = [
        Badge(t("cc_power.fan_unsaved_badge"), style="warn") if fans["is_dirty"] else Txt(hdr_txt, role="schema")
    ]
    if not fans["fan_priv_ok"]:
        hdr_actions.append(ActionBtn(t("cc_power.authorize_priv"), "cc_pwr:auth_fan", tone="apply"))

    box = Box(t("cc_power.fan_box_title"), icon="\uf863", actions=hdr_actions, highlighted=False)

    preset_pills = [
        SelectPill(
            t(lk),
            f"cc_pwr:fan_prof:{key}",
            selected=(fans["profile"] == key),
            dirty=(fans["is_dirty"] and fans["profile"] == key),
        )
        for key, lk in _FAN_PRESETS
    ]
    custom_pills = [
        SelectPill(
            cinfo["label"],
            f"cc_pwr:fan_prof:{slug}",
            selected=(fans["profile"] == slug),
            dirty=(fans["is_dirty"] and fans["profile"] == slug),
        )
        for slug, cinfo in fans["custom"].items()
    ]
    new_fld = InlineField(
        "cc_fan_new_prof",
        "cc_pwr:fan_new_in",
        value="",
        empty_label=t("cc_power.fan_new_btn"),
        placeholder="quiet-work",
        width=13,
        icon="+",
        tone="apply",
    )
    box.add_kv(t("cc_power.fan_profile"), " ".join(preset_pills + custom_pills), right_markup=new_fld)

    line_parts = []
    if st["cpu_temp"] is not None:
        t_c = st["cpu_temp"]
        line_parts.append(Txt(f"CPU {t_c:.1f}°C", role=_temp_role(t_c)))
        if st["cpu_ccd_temps"]:
            ccd_s = "/".join(f"{val:.0f}°C" for _lbl, val in st["cpu_ccd_temps"][:2])
            line_parts.append(Txt(ccd_s, role="schema"))
    line_parts.append(Txt(f"{fans['cpu_rpm']} RPM ({fans['cpu_pwm_pct']}%)", role="val"))
    if fans["opt_rpm"] is not None:
        line_parts.append(Txt(f"Opt {fans['opt_rpm']} RPM ({fans['opt_pwm_pct']}%)", role="schema"))
    sep_m = Txt("  ·  ", role="schema")
    box.add_kv(t("cc_power.fan_live_speed"), sep_m.join(line_parts))

    curve_open, curve_fold = FoldToggle(cc_panel, "pwr_fan_curve", default=True, uri_prefix="ui:state")
    sec_acts = []
    if curve_open:
        if fans["is_custom_prof"]:
            sec_acts.append(ActionBtn(t("cc_power.fan_del_btn"), f"cc_pwr:fan_del:{fans['profile']}", tone="danger_soft"))
        elif fans["is_preset_modified"]:
            sec_acts.append(ActionBtn(t("cc_power.fan_factory_btn"), "cc_pwr:fan_reset_preset", tone="warn"))
    sec_acts.append(curve_fold)
    box.add_section(t("cc_power.fan_curve_sec"), actions=sec_acts)

    if not curve_open:
        summary_s = "  ·  ".join(f"{t_c}°C→{p_pct}%" for t_c, p_pct in fans["display_pts"])
        box.add_row(f"  {Txt(summary_s, role='schema')}")
        return box

    pt_rows = []
    for idx in range(5):
        t_c, p_pct = fans["display_pts"][idx]
        b_t, _b_p = fans["base_pts"][idx]
        t_dec = max(20, t_c - 2)
        t_inc = min(95, t_c + 2)
        t_fld = InlineField(
            f"cc_fan_t_{idx}",
            f"cc_pwr:fan_t_in:{idx}",
            value=f"{t_c}°C",
            placeholder=str(t_c),
            width=5,
            icon="\u270e",
            tone="neutral",
            dirty=(t_c != b_t),
            min_chars=6,
        )
        t_cell = (
            f"{ActionBtn('-', f'cc_pwr:fan_pt_t:{idx}:{t_dec}', tone='neutral')} "
            f"{t_fld} "
            f"{ActionBtn('+', f'cc_pwr:fan_pt_t:{idx}:{t_inc}', tone='neutral')}"
        )
        pwm_cell = Slider(
            p_pct,
            f"cc_pwr:fan_pt_p:{idx}",
            min_val=(80 if idx == 4 else 20),
            max_val=100,
            width=22,
            show_pct=True,
            step=5,
        )
        pt_rows.append([Txt(t(_FAN_STAGE_KEYS[idx]), role="schema"), t_cell, pwm_cell])

    box.add_table(
        headers=[t("cc_power.fan_col_stage"), t("cc_power.fan_col_temp"), t("cc_power.fan_col_pwm")],
        widths=[15, 20, "flex"],
        rows=pt_rows,
        aligns=["left", "left", "left"],
    )

    if fans["is_dirty"]:
        footer_btns = (
            f"{ActionBtn(t('cc_power.fan_discard_btn'), 'cc_pwr:fan_discard', tone='warn')} "
            f"{ActionBtn(t('cc_power.fan_apply_btn'), 'cc_pwr:fan_apply', tone='apply')}"
        )
    else:
        footer_btns = ActionBtn(t("cc_power.fan_synced_btn"), "cc_pwr:noop_fan", tone="muted")
    box.add_row("", right_markup=footer_btns)
    return box



def _battery_status_badge(status: str) -> str:
    s = status.lower()
    if s == "charging":
        return StateBadge(True, t("cc_power.charging"), on_tone="ok")
    if s == "full":
        return StateBadge(True, t("cc_power.full"), on_tone="ok")
    if s == "not charging":
        return StateBadge(True, t("cc_power.not_charging"), on_tone="warn")
    return StateBadge(False, t("cc_power.discharging"), t("cc_power.discharging"))


def _build_batteries_box(bd: dict) -> Box:
    bats = bd["batteries"]
    periphs = bd["peripherals"]
    total = len(bats) + len(periphs)
    title = t("cc_power.batteries_title_n", p0=total) if total else t("cc_power.batteries_title")
    box = Box(title, icon="\uf240", highlighted=False)

    if not bats and not periphs:
        box.add_kv(
            t("cc_power.power_source"),
            f"{Txt(t('cc_power.ac_mains'), role='val')}  {Txt(t('cc_power.no_batteries_detected'), role='schema')}",
        )
        return box

    for b in bats:
        cap = b["capacity"]
        bat_name = b["name"]
        watts_val = b["watts"]
        ch_role = "val" if cap >= 20 else "fault"
        w_txt = f"  {Txt(f'({watts_val}W)', role='schema')}" if watts_val else ""
        bar = ProgressBar(cap, width=16, color_by_threshold=False)
        box.add_kv(
            t("cc_power.battery_label", p0=bat_name),
            f"{bar} {Txt(f'{cap}%', role=ch_role)}{w_txt}",
            right_markup=_battery_status_badge(b["status"]),
        )
        if b["has_threshold"]:
            thr = b["end_threshold"]
            thr_pills = (
                f"{SelectPill(t('cc_power.charge_max'), f'cc_pwr:bat_thr:{bat_name}:100', selected=(thr == 100))} "
                f"{SelectPill(t('cc_power.charge_preserve'), f'cc_pwr:bat_thr:{bat_name}:80', selected=(thr == 80))}"
            )
            box.add_kv(t("cc_power.charge_health", p0=bat_name), thr_pills)

    if periphs:
        rows = [[Txt(p["name"], role="focus"), Txt(p["type"], role="schema"), Txt(p["charge"], role="val")] for p in periphs]
        box.add_table(
            headers=[t("cc_power.device"), t("cc_power.type"), t("cc_power.charge")],
            widths=[24, 14, "flex"],
            rows=rows,
            aligns=["left", "left", "left"],
        )
    return box



def _build_idle_lock_boxes() -> list:
    st = _collect_idle_state()
    idle_hdr = (
        Badge(t("cc_power.caffeine_on"), style="warn")
        if st["caffeine"]
        else StateBadge(st["idle_daemon"] != "Stopped", t("cc_power.idle_active"), t("cc_power.stopped"), on_tone="ok")
    )
    box = Box(
        t("cc_power.idle_timeout_auto_lock_caffeine_ov"),
        icon="\uf186",
        actions=[idle_hdr],
        highlighted=False,
    )
    box.add_kv(t("cc_power.idle_daemon"), StateBadge(st["idle_daemon"] != "Stopped", st["idle_daemon"].upper(), t("cc_power.stopped")))
    box.add_kv(
        t("cc_power.dim_on_idle"),
        f"{SelectPill(t('common.on'), 'cc_pwr:dim_idle:1', selected=st['dim_idle'])} {SelectPill(t('common.off'), 'cc_pwr:dim_idle:0', selected=not st['dim_idle'])}",
        requires=["brightnessctl"],
    )
    box.add_kv(
        t("cc_power.auto_lock_timeout"),
        " ".join(SelectPill(f"{m}m" if m else t("cc_power.off"), f"cc_pwr:idle_min:{m}", selected=(st["idle_min"] == m), min_chars=0) for m in (5, 10, 15, 30, 0)),
    )
    box.add_kv(
        t("cc_power.dpms_screen_off"),
        " ".join(SelectPill(f"{m}m" if m else t("cc_power.off"), f"cc_pwr:dpms_min:{m}", selected=(st["dpms_min"] == m), min_chars=0) for m in (10, 15, 30, 60, 0)),
    )
    box.add_kv(
        t("cc_power.awake_caffeine"),
        f"{SelectPill(t('cc_power.keep_awake'), 'cc_pwr:caffeine:on', selected=st['caffeine'])} {SelectPill(t('cc_power.allow_idle'), 'cc_pwr:caffeine:off', selected=not st['caffeine'])}",
    )
    box.add_kv(
        t("cc_power.lock_screen_command"),
        Txt(st["lock_cmd"] or t("cc_power.not_set_waybar_options"), role="val" if st["lock_cmd"] else "inactive"),
        right_markup=ActionBtn(t("cc_power.lock_now"), "cc_pwr:lock_now", tone="warn"),
    )

    susp_box = Box(t("cc_power.suspend_and_power_button"), icon="\uf011", highlighted=False)
    susp_box.add_kv(
        t("cc_power.suspend_ac"),
        " ".join(SelectPill(f"{m}m" if m else t("cc_power.never"), f"cc_pwr:susp_ac:{m}", selected=(st["susp_ac"] == m), min_chars=0) for m in (15, 30, 60, 120, 0)),
    )
    susp_box.add_kv(
        t("cc_power.suspend_bat"),
        " ".join(SelectPill(f"{m}m" if m else t("cc_power.never"), f"cc_pwr:susp_bat:{m}", selected=(st["susp_bat"] == m), min_chars=0) for m in (10, 15, 30, 60, 0)),
    )
    pwr_opts = (
        ("poweroff", "cc_power.pwr_btn_poweroff"),
        ("suspend", "cc_power.pwr_btn_suspend"),
        ("hibernate", "cc_power.pwr_btn_hibernate"),
        ("ignore", "cc_power.pwr_btn_ignore"),
    )
    susp_box.add_kv(
        t("cc_power.power_button"),
        " ".join(SelectPill(t(lk), f"cc_pwr:pwr_btn:{k}", selected=(st["power_key"] == k)) for k, lk in pwr_opts),
    )
    return [box, susp_box]


def _build_general_standard(cc_panel, canvas: Canvas) -> list:
    cards = []
    if STATE.get("privileges", "pending_action", ""):
        cards.extend(build_privilege_modal_card(cc_panel, canvas))
    cards.extend(_feedback_cards())
    st = _collect_cpu_power(async_refresh=True, cc_panel=cc_panel)
    bd = _collect_batteries_and_devices(cc_panel=cc_panel)
    cards.append(_build_power_cpu_box(cc_panel, st))
    fan_box = _build_fan_cooling_box(cc_panel, st)
    if fan_box:
        cards.append(fan_box)
    cards.extend(_build_idle_lock_boxes())
    cards.append(_build_batteries_box(bd))
    therm_card = _build_thermal_graph_card(cc_panel, canvas)
    if therm_card:
        cards.append(therm_card)
    return cards


def build_cards(cc_panel, canvas: Canvas) -> list:
    return _build_general_standard(cc_panel, canvas)
