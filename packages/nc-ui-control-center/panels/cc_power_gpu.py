#!/usr/bin/env python3
"""NC-UI Control Center — GPU Blueprint, Telemetry & Embedded nvtop (`panels/cc_power_gpu.py`)."""

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
from gi.repository import Gdk, GLib, Gtk, Pango, PangoCairo  # noqa: E402

from core.config import THEME  # noqa: E402
from core.i18n import t  # noqa: E402
from ui.canvas import Canvas  # noqa: E402
from ui.embedded_tui import (  # noqa: E402
    _build_tui_ncbox,
    _compute_single_tui_size,
)
from ui.kit import (  # noqa: E402
    ActionBtn,
    Box,
    ProgressBar,
    Txt,
    get_semantic_palette,
    hex_to_rgba_f as _bp_hex_rgba,
    rounded_rect_path as _trace_rect,
    stroke_styled_frame as _stroke_styled_frame,
    temp_role as _temp_role,
)
from ui.widgets import AsciiCard  # noqa: E402
from panels.cc_engine_power import (  # noqa: E402
    _collect_cpu_power,
    _feedback_cards,
    get_cached_gpu_data,
    get_cached_psu_data,
    refresh_hw_telemetry_async,
)
from panels.cc_engine_resources import (  # noqa: E402
    _ensure_nvtop_bin,
    get_gpu_telemetry,
)
from panels.cc_power_cpu_ram import _pct_role  # noqa: E402


_GPU_FAN_STATE = {
    "angle": 0.42,
    "last_ts": 0.0,
    "target_pct": 0.0,
    "smooth_pct": 0.0,
    "last_poll_ts": 0.0,
}


class _GpuHardwareBlueprintWidget(Gtk.DrawingArea):
    """VSync-locked, offscreen-cached 3-zone Cairo Multi-Vendor GPU + PSU Blueprint."""

    def __init__(self, st: dict, inset_x: float = 16.0, fixed_height: int = 246) -> None:
        super().__init__()
        self._st = st
        self._gpu = dict(st.get("gpu") or {})
        self._psu = dict(st.get("psu") or {})
        self._inset_x = max(8.0, float(inset_x))
        self._widget_w = 860
        self._widget_h = int(fixed_height)
        self._footer_h = 28.0
        self._tick_id: int | None = None
        self._destroyed = False

        self._bg_surface = None
        self._bg_sig = None
        self._rotor_cw = None
        self._rotor_ccw = None
        self._rotor_sig = None
        self._rotor_half = 0.0
        self._fans_geom: list[tuple[float, float, float, bool, float]] = []
        self._igpu_pulse_rect: tuple[float, float, float, float] | None = None

        target_pct = float(self._gpu.get("fan") or 0.0)
        _GPU_FAN_STATE["target_pct"] = target_pct
        if _GPU_FAN_STATE["smooth_pct"] <= 0.0 and target_pct > 0.0:
            _GPU_FAN_STATE["smooth_pct"] = target_pct
        _GPU_FAN_STATE["last_ts"] = time.monotonic()

        self.set_size_request(360, self._widget_h)
        self.set_hexpand(True)
        self.connect("size-allocate", self._on_size_allocate)
        self.connect("draw", self._on_draw)
        self.connect("destroy", self._on_destroy)

        if os.environ.get("NC_UI_OFFSCREEN") != "1":
            self._tick_id = self.add_tick_callback(self._on_frame_tick)

    def _on_size_allocate(self, _w, alloc) -> None:
        if alloc.width > 120 and abs(alloc.width - self._widget_w) > 1:
            self._widget_w = alloc.width
            self._bg_sig = None
            self.queue_draw()

    def _on_destroy(self, *_args) -> None:
        self._destroyed = True
        if self._tick_id is not None:
            try:
                self.remove_tick_callback(self._tick_id)
            except Exception:
                pass
            self._tick_id = None

    def _on_frame_tick(self, _widget, frame_clock) -> bool:
        if self._destroyed:
            return False
        now_fc = frame_clock.get_frame_time() / 1_000_000.0
        if not self.get_mapped():
            _GPU_FAN_STATE["last_ts"] = now_fc
            return True

        last_ts = _GPU_FAN_STATE["last_ts"] or now_fc
        dt = now_fc - last_ts
        if dt <= 0.0 or dt > 0.25:
            dt = 1.0 / 60.0
        else:
            dt = min(0.05, max(0.001, dt))
        _GPU_FAN_STATE["last_ts"] = now_fc

        need_full = False
        now_wall = time.monotonic()
        if (now_wall - _GPU_FAN_STATE["last_poll_ts"]) >= 2.5:
            _GPU_FAN_STATE["last_poll_ts"] = now_wall
            refresh_hw_telemetry_async()

        live_gpu = get_cached_gpu_data()
        if live_gpu and live_gpu != self._gpu:
            self._gpu = live_gpu
            need_full = True
        live_psu = get_cached_psu_data()
        if live_psu is not None and live_psu != self._psu:
            self._psu = live_psu
            need_full = True

        cat = str(self._gpu.get("category") or "nvidia_dgpu")
        target_pct = float(self._gpu.get("fan") or 0.0)
        _GPU_FAN_STATE["target_pct"] = target_pct

        # Smooth exponential rotational inertia prevents speed jumps on duty changes
        alpha = 1.0 - math.exp(-dt * 4.5)
        smooth_pct = _GPU_FAN_STATE["smooth_pct"] + (target_pct - _GPU_FAN_STATE["smooth_pct"]) * alpha
        if smooth_pct < 0.15 and target_pct <= 0.0:
            smooth_pct = 0.0
        _GPU_FAN_STATE["smooth_pct"] = smooth_pct

        if need_full:
            self.queue_draw()
        elif cat == "igpu":
            util_pct = float(self._gpu.get("util") or 0.0)
            if util_pct > 1.0 and self._igpu_pulse_rect is not None:
                omega = 0.8 + util_pct / 45.0
                _GPU_FAN_STATE["angle"] = (_GPU_FAN_STATE["angle"] + dt * omega) % math.tau
                rx, ry, rw, rh = self._igpu_pulse_rect
                self.queue_draw_area(int(rx - 2), int(ry - 2), int(rw + 4), int(rh + 4))
        elif smooth_pct > 0.05:
            omega = (smooth_pct / 100.0) * 10.5
            _GPU_FAN_STATE["angle"] = (_GPU_FAN_STATE["angle"] + dt * omega) % math.tau
            if self._fans_geom:
                x0 = min(fx - fr - 4.0 for fx, _fy, fr, _cw, _ph in self._fans_geom)
                x1 = max(fx + fr + 4.0 for fx, _fy, fr, _cw, _ph in self._fans_geom)
                y0 = min(fy - fr - 4.0 for _fx, fy, fr, _cw, _ph in self._fans_geom)
                y1 = max(fy + fr + 4.0 for _fx, fy, fr, _cw, _ph in self._fans_geom)
                self.queue_draw_area(int(x0), int(y0), int(x1 - x0) + 2, int(y1 - y0) + 2)
            else:
                self.queue_draw()
        return True

    @staticmethod
    def _text(cr, text: str, x: float, y: float, font_desc, color_hex: str, alpha: float = 1.0, max_w: float = 0.0, align=None) -> tuple[float, float]:
        lay = PangoCairo.create_layout(cr)
        lay.set_font_description(font_desc)
        if max_w > 0.0:
            lay.set_width(int(max_w * Pango.SCALE))
            lay.set_ellipsize(Pango.EllipsizeMode.END)
        if align is not None:
            lay.set_alignment(align)
        lay.set_text(str(text), -1)
        _, ext = lay.get_pixel_extents()
        cr.set_source_rgba(*_bp_hex_rgba(color_hex, alpha))
        cr.move_to(x, y)
        PangoCairo.show_layout(cr, lay)
        return float(ext.width), float(ext.height)

    @staticmethod
    def _draw_fan_well(cr, cx: float, cy: float, r: float, tach_hex: str, pal: dict, surf_hex: str) -> None:
        cr.save()
        cr.new_sub_path()
        cr.arc(cx, cy, r + 2.5, 0, math.tau)
        cr.set_source_rgba(*_bp_hex_rgba(surf_hex, 0.96))
        cr.fill_preserve()
        cr.set_source_rgba(*_bp_hex_rgba(pal["schema"], 0.85))
        cr.set_line_width(1.6)
        cr.stroke()

        cr.new_sub_path()
        cr.arc(cx, cy, r - 1.2, 0, math.tau)
        cr.set_source_rgba(*_bp_hex_rgba(tach_hex, 0.42))
        cr.set_line_width(1.1)
        cr.stroke()
        cr.restore()

    @staticmethod
    def _build_rotor_surface(fan_r: float, fan_pct: float, tach_hex: str, pal: dict, clockwise: bool):
        side = max(32, int(math.ceil(fan_r * 2.0 + 6.0)))
        half = side * 0.5
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, side, side)
        cr = cairo.Context(surf)
        cx, cy = half, half

        if fan_pct > 0.5:
            sweep = max(0.32, min(1.35, (fan_pct / 100.0) * 1.45))
            for k_arc in range(3):
                a0 = k_arc * (math.tau / 3.0)
                cr.new_sub_path()
                cr.arc(cx, cy, fan_r - 3.2, a0, a0 + sweep)
                cr.set_source_rgba(*_bp_hex_rgba(tach_hex, 0.48))
                cr.set_line_width(1.3)
                cr.stroke()

        blades = 9
        r_hub = fan_r * 0.28
        r_tip = fan_r - 2.8
        dir_sign = 1.0 if clockwise else -1.0
        for i in range(blades):
            theta = i * (math.tau / blades)
            a_root1 = theta - 0.14 * dir_sign
            a_root2 = theta + 0.16 * dir_sign
            a_mid1 = theta + 0.10 * dir_sign
            a_mid2 = theta + 0.40 * dir_sign
            a_tip1 = theta + 0.30 * dir_sign
            a_tip2 = theta + 0.58 * dir_sign
            r_mid = (r_hub + r_tip) * 0.54

            x0 = cx + r_hub * math.cos(a_root1)
            y0 = cy + r_hub * math.sin(a_root1)
            xm1 = cx + r_mid * math.cos(a_mid1)
            ym1 = cy + r_mid * math.sin(a_mid1)
            xt1 = cx + r_tip * math.cos(a_tip1)
            yt1 = cy + r_tip * math.sin(a_tip1)
            xt2 = cx + r_tip * math.cos(a_tip2)
            yt2 = cy + r_tip * math.sin(a_tip2)
            xm2 = cx + r_mid * math.cos(a_mid2)
            ym2 = cy + r_mid * math.sin(a_mid2)
            x1 = cx + r_hub * math.cos(a_root2)
            y1 = cy + r_hub * math.sin(a_root2)

            cr.new_path()
            cr.move_to(x0, y0)
            cr.curve_to(xm1, ym1, xm1, ym1, xt1, yt1)
            cr.line_to(xt2, yt2)
            cr.curve_to(xm2, ym2, xm2, ym2, x1, y1)
            cr.close_path()
            cr.set_source_rgba(*_bp_hex_rgba(pal["schema"], 0.42))
            cr.fill_preserve()
            cr.set_source_rgba(*_bp_hex_rgba(pal["focus_val"], 0.65))
            cr.set_line_width(0.85)
            cr.stroke()

        return surf, half

    def _ensure_rotor_surfaces(self, fan_r: float, fan_pct: float, tach_hex: str, pal: dict) -> None:
        sig = (round(fan_r, 1), tach_hex, pal["schema"], pal["focus_val"], int(round(fan_pct / 5.0)))
        if sig == self._rotor_sig and self._rotor_cw is not None and self._rotor_ccw is not None:
            return
        self._rotor_cw, self._rotor_half = self._build_rotor_surface(fan_r, fan_pct, tach_hex, pal, True)
        self._rotor_ccw, _ = self._build_rotor_surface(fan_r, fan_pct, tach_hex, pal, False)
        self._rotor_sig = sig

    @staticmethod
    def _draw_pcie_chrome(
        cr,
        card_x: float,
        card_y: float,
        card_w: float,
        card_h: float,
        surf_hex: str,
        pal: dict,
        pwr_count: int = 1,
    ) -> None:
        br_x = card_x - 7.0
        _trace_rect(cr, br_x, card_y - 6.0, 14.0, 7.0, 1.5)
        cr.set_source_rgba(*_bp_hex_rgba(pal["schema"], 0.78))
        cr.fill()
        _trace_rect(cr, br_x, card_y, 7.0, card_h + 14.0, 1.5)
        cr.set_source_rgba(*_bp_hex_rgba(pal["schema"], 0.65))
        cr.fill()
        for sy_slot in range(int(card_y + 16), int(card_y + card_h - 14), 14):
            cr.set_source_rgba(*_bp_hex_rgba(surf_hex, 0.90))
            cr.rectangle(br_x + 1.5, sy_slot, 4.0, 7.0)
            cr.fill()

        pcie_x = card_x + 36.0
        pcie_w = min(168.0, card_w * 0.46)
        _trace_rect(cr, pcie_x, card_y + card_h - 1.0, 24.0, 7.0, 1.5)
        cr.set_source_rgba(*_bp_hex_rgba(pal["attention"], 0.72))
        cr.fill()
        _trace_rect(cr, pcie_x + 30.0, card_y + card_h - 1.0, pcie_w - 30.0, 7.0, 1.5)
        cr.set_source_rgba(*_bp_hex_rgba(pal["attention"], 0.72))
        cr.fill()

        pwr_x0 = card_x + card_w * (0.56 if pwr_count == 1 else 0.58)
        for p_idx in range(pwr_count):
            px = pwr_x0 + p_idx * 24.0
            _trace_rect(cr, px, card_y - 6.0, 20.0 if pwr_count > 1 else 24.0, 7.0, 1.5)
            cr.set_source_rgba(*_bp_hex_rgba(pal["structure_bg"], 0.95))
            cr.fill_preserve()
            cr.set_source_rgba(*_bp_hex_rgba(pal["schema"], 0.80))
            cr.set_line_width(1.0)
            cr.stroke()
            cr.new_sub_path()
            cr.arc(px + (23.0 if pwr_count > 1 else 29.0), card_y - 2.5, 2.1, 0, math.tau)
            cr.set_source_rgba(*_bp_hex_rgba(pal["vitality"], 0.95))
            cr.fill()

    def _draw_zone2_nvidia(
        self, cr, div_l: float, div_r: float, zx_c: float, zw_c: float,
        gpu: dict, temp_c: float, fan_pct: float, temp_col: str,
        pal: dict, surf_hex: str, f_hdr, f_val, f_crest,
    ) -> tuple[list[tuple[float, float, float, bool, float]], tuple[float, float, float, float] | None]:
        self._text(cr, t("cc_power.gpu_bp_z2_nvidia"), zx_c, 10.0, f_hdr, pal["vitality"], alpha=0.95, max_w=zw_c * 0.58)
        model_short = str(gpu.get("name") or "GeForce RTX").replace("NVIDIA ", "")
        self._text(cr, model_short, zx_c + zw_c * 0.58, 10.0, f_val, pal["focus_val"], alpha=0.92, max_w=zw_c * 0.42, align=Pango.Alignment.RIGHT)

        card_w = min(378.0, zw_c - 20.0)
        card_h = 130.0
        card_cx = (div_l + div_r) * 0.5 + 3.0
        card_x = card_cx - card_w * 0.5
        card_y = 40.0
        card_cy = card_y + card_h * 0.5

        cr.save()
        self._draw_pcie_chrome(cr, card_x, card_y, card_w, card_h, surf_hex, pal, pwr_count=1)

        _trace_rect(cr, card_x + 0.5, card_y + 0.5, card_w - 1.0, card_h - 1.0, 8.0)
        cr.set_source_rgba(*_bp_hex_rgba(surf_hex, 0.92))
        cr.fill_preserve()
        cr.set_source_rgba(*_bp_hex_rgba(pal["schema"], 0.90))
        cr.set_line_width(1.6)
        cr.stroke()

        cr.save()
        _trace_rect(cr, card_x + 3.0, card_y + 3.0, card_w - 6.0, card_h - 6.0, 6.0)
        cr.clip()
        cr.set_source_rgba(*_bp_hex_rgba(pal["structure"], 0.42))
        cr.set_line_width(1.0)
        fx = card_x + 8.0
        while fx < card_x + card_w - 8.0:
            cr.move_to(fx, card_y + 5.0)
            cr.line_to(fx, card_y + card_h - 5.0)
            fx += 4.5
        cr.stroke()

        fan_dx = card_w * 0.275
        fan_r = min(40.0, card_h * 0.335, card_w * 0.125)
        cr.set_line_cap(1)
        cr.set_line_join(1)
        cr.move_to(card_x + 12.0, card_y + 6.0)
        cr.curve_to(card_cx - fan_dx * 0.45, card_y + 12.0, card_cx - 22.0, card_cy - 10.0, card_cx, card_cy)
        cr.curve_to(card_cx + 22.0, card_cy + 10.0, card_cx + fan_dx * 0.45, card_y + card_h - 12.0, card_x + card_w - 12.0, card_y + card_h - 6.0)
        cr.move_to(card_x + 12.0, card_y + card_h - 6.0)
        cr.curve_to(card_cx - fan_dx * 0.45, card_y + card_h - 12.0, card_cx - 22.0, card_cy + 10.0, card_cx, card_cy)
        cr.curve_to(card_cx + 22.0, card_cy - 10.0, card_cx + fan_dx * 0.45, card_y + 12.0, card_x + card_w - 12.0, card_y + 6.0)
        cr.set_source_rgba(*_bp_hex_rgba(pal["schema"], 0.88))
        cr.set_line_width(3.4)
        cr.stroke_preserve()
        cr.set_source_rgba(*_bp_hex_rgba(pal["focus_val"], 0.72))
        cr.set_line_width(1.1)
        cr.stroke()
        cr.restore()

        crest_w = 108.0
        _trace_rect(cr, card_cx - crest_w * 0.5, card_y + 4.0, crest_w, 16.0, 2.5)
        cr.set_source_rgba(*_bp_hex_rgba(pal["vitality_bg"], 0.88))
        cr.fill_preserve()
        cr.set_source_rgba(*_bp_hex_rgba(pal["vitality"], 0.72))
        cr.set_line_width(0.8)
        cr.stroke()
        self._text(cr, "GEFORCE RTX", card_cx - crest_w * 0.5, card_y + 5.0, f_crest, pal["vitality"], alpha=0.98, max_w=crest_w, align=Pango.Alignment.CENTER)

        fx1, fx2, fy_fans = card_cx - fan_dx, card_cx + fan_dx, card_cy + 2.0
        self._draw_fan_well(cr, fx1, fy_fans, fan_r, temp_col, pal, surf_hex)
        self._draw_fan_well(cr, fx2, fy_fans, fan_r, temp_col, pal, surf_hex)
        cr.restore()

        fan_s = f"FAN 1/2 · {fan_pct:.0f}%" if gpu.get("fan") is not None else "FAN · N/A"
        temp_s = f"CORE {temp_c:.0f}°C" if temp_c > 0 else "CORE -"
        self._text(cr, f"{fan_s}   ·   {temp_s}", zx_c, card_y + card_h + 15.0, f_val, temp_col, alpha=0.95, max_w=zw_c, align=Pango.Alignment.CENTER)
        return [(fx1, fy_fans, fan_r, True, 0.0), (fx2, fy_fans, fan_r, False, 0.35)], None

    def _draw_zone2_amd(
        self, cr, div_l: float, div_r: float, zx_c: float, zw_c: float,
        gpu: dict, temp_c: float, fan_pct: float, temp_col: str,
        pal: dict, surf_hex: str, f_hdr, f_val, f_crest,
    ) -> tuple[list[tuple[float, float, float, bool, float]], tuple[float, float, float, float] | None]:
        self._text(cr, t("cc_power.gpu_bp_z2_amd"), zx_c, 10.0, f_hdr, pal["fault"], alpha=0.95, max_w=zw_c * 0.56)
        model_short = str(gpu.get("name") or "Radeon RX").replace("Advanced Micro Devices, Inc. ", "").replace("AMD ", "")
        self._text(cr, model_short, zx_c + zw_c * 0.56, 10.0, f_val, pal["focus_val"], alpha=0.92, max_w=zw_c * 0.44, align=Pango.Alignment.RIGHT)

        card_w = min(388.0, zw_c - 18.0)
        card_h = 126.0
        card_cx = (div_l + div_r) * 0.5 + 3.0
        card_x = card_cx - card_w * 0.5
        card_y = 42.0
        card_cy = card_y + card_h * 0.5

        cr.save()
        self._draw_pcie_chrome(cr, card_x, card_y, card_w, card_h, surf_hex, pal, pwr_count=2)

        _trace_rect(cr, card_x + 0.5, card_y + 0.5, card_w - 1.0, card_h - 1.0, 4.0)
        cr.set_source_rgba(*_bp_hex_rgba(surf_hex, 0.94))
        cr.fill_preserve()
        cr.set_source_rgba(*_bp_hex_rgba(pal["schema"], 0.88))
        cr.set_line_width(1.6)
        cr.stroke()

        cr.save()
        _trace_rect(cr, card_x + 3.0, card_y + 3.0, card_w - 6.0, card_h - 6.0, 3.0)
        cr.clip()
        # Horizontal heatsink fin slats
        cr.set_source_rgba(*_bp_hex_rgba(pal["structure"], 0.44))
        cr.set_line_width(1.0)
        fy_line = card_y + 9.0
        while fy_line < card_y + card_h - 8.0:
            cr.move_to(card_x + 5.0, fy_line)
            cr.line_to(card_x + card_w - 5.0, fy_line)
            fy_line += 5.0
        cr.stroke()

        # Iconic AMD Radeon crimson top & bottom racing pinstripes
        cr.set_source_rgba(*_bp_hex_rgba(pal["fault"], 0.90))
        cr.set_line_width(2.0)
        cr.move_to(card_x + 6.0, card_y + 6.5)
        cr.line_to(card_x + card_w - 6.0, card_y + 6.5)
        cr.move_to(card_x + 6.0, card_y + card_h - 6.5)
        cr.line_to(card_x + card_w - 6.0, card_y + card_h - 6.5)
        cr.stroke()
        cr.restore()

        crest_w = 96.0
        _trace_rect(cr, card_cx - crest_w * 0.5, card_y + 4.0, crest_w, 16.0, 2.0)
        cr.set_source_rgba(*_bp_hex_rgba(pal["fault_bg"], 0.92))
        cr.fill_preserve()
        cr.set_source_rgba(*_bp_hex_rgba(pal["fault"], 0.85))
        cr.set_line_width(0.9)
        cr.stroke()
        self._text(cr, "RADEON", card_cx - crest_w * 0.5, card_y + 5.0, f_crest, pal["fault"], alpha=0.98, max_w=crest_w, align=Pango.Alignment.CENTER)

        fan_r = min(33.0, card_h * 0.28, card_w * 0.095)
        fan_dx = card_w * 0.305
        fy_fans = card_cy + 4.0
        fans = [
            (card_cx - fan_dx, fy_fans, fan_r, True, 0.0),
            (card_cx, fy_fans, fan_r, False, 0.25),
            (card_cx + fan_dx, fy_fans, fan_r, True, 0.50),
        ]
        for fx_p, fy_p, fr_p, _cw, _ph in fans:
            self._draw_fan_well(cr, fx_p, fy_p, fr_p, pal["fault"] if temp_c <= 0 else temp_col, pal, surf_hex)
        cr.restore()

        rpm_val = gpu.get("fan_rpm")
        if gpu.get("fan") is not None and rpm_val is not None:
            fan_s = f"TRI-FAN · {fan_pct:.0f}% ({rpm_val} RPM)"
        elif gpu.get("fan") is not None:
            fan_s = f"TRI-FAN · {fan_pct:.0f}%"
        elif rpm_val is not None:
            fan_s = f"TRI-FAN · {rpm_val} RPM"
        else:
            fan_s = "TRI-FAN · N/A"
        temp_s = f"EDGE {temp_c:.0f}°C" if temp_c > 0 else "EDGE -"
        self._text(cr, f"{fan_s}   ·   {temp_s}", zx_c, card_y + card_h + 15.0, f_val, temp_col, alpha=0.95, max_w=zw_c, align=Pango.Alignment.CENTER)
        return fans, None

    def _draw_zone2_intel(
        self, cr, div_l: float, div_r: float, zx_c: float, zw_c: float,
        gpu: dict, temp_c: float, fan_pct: float, temp_col: str,
        pal: dict, surf_hex: str, f_hdr, f_val, f_crest,
    ) -> tuple[list[tuple[float, float, float, bool, float]], tuple[float, float, float, float] | None]:
        self._text(cr, t("cc_power.gpu_bp_z2_intel"), zx_c, 10.0, f_hdr, pal["intent"], alpha=0.95, max_w=zw_c * 0.56)
        model_short = str(gpu.get("name") or "Arc Graphics").replace("Intel Corporation ", "").replace("Intel ", "")
        self._text(cr, model_short, zx_c + zw_c * 0.56, 10.0, f_val, pal["focus_val"], alpha=0.92, max_w=zw_c * 0.44, align=Pango.Alignment.RIGHT)

        card_w = min(378.0, zw_c - 20.0)
        card_h = 130.0
        card_cx = (div_l + div_r) * 0.5 + 3.0
        card_x = card_cx - card_w * 0.5
        card_y = 40.0
        card_cy = card_y + card_h * 0.5

        cr.save()
        self._draw_pcie_chrome(cr, card_x, card_y, card_w, card_h, surf_hex, pal, pwr_count=2)

        _trace_rect(cr, card_x + 0.5, card_y + 0.5, card_w - 1.0, card_h - 1.0, 12.0)
        cr.set_source_rgba(*_bp_hex_rgba(surf_hex, 0.94))
        cr.fill_preserve()
        cr.set_source_rgba(*_bp_hex_rgba(pal["schema"], 0.88))
        cr.set_line_width(1.5)
        cr.stroke()

        # Electric cyan perimeter illumination ring (Intel Arc Limited Edition signature)
        _trace_rect(cr, card_x + 4.5, card_y + 4.5, card_w - 9.0, card_h - 9.0, 9.0)
        cr.set_source_rgba(*_bp_hex_rgba(pal["intent"], 0.78))
        cr.set_line_width(1.3)
        cr.stroke()

        # Diagonal stealth louver vents across center bridge
        cr.save()
        _trace_rect(cr, card_cx - 32.0, card_y + 24.0, 64.0, card_h - 32.0, 4.0)
        cr.clip()
        cr.set_source_rgba(*_bp_hex_rgba(pal["structure"], 0.55))
        cr.set_line_width(1.4)
        for k in range(-6, 12):
            x0 = card_cx - 36.0 + k * 9.0
            cr.move_to(x0, card_y + card_h - 8.0)
            cr.line_to(x0 + 26.0, card_y + 24.0)
        cr.stroke()
        cr.restore()

        crest_w = 104.0
        _trace_rect(cr, card_cx - crest_w * 0.5, card_y + 5.0, crest_w, 16.0, 3.0)
        cr.set_source_rgba(*_bp_hex_rgba(pal["intent_bg"], 0.92))
        cr.fill_preserve()
        cr.set_source_rgba(*_bp_hex_rgba(pal["intent"], 0.85))
        cr.set_line_width(0.9)
        cr.stroke()
        self._text(cr, "INTEL ARC", card_cx - crest_w * 0.5, card_y + 6.0, f_crest, pal["intent"], alpha=0.98, max_w=crest_w, align=Pango.Alignment.CENTER)

        fan_dx = card_w * 0.275
        fan_r = min(40.0, card_h * 0.33, card_w * 0.125)
        fx1, fx2, fy_fans = card_cx - fan_dx, card_cx + fan_dx, card_cy + 3.0
        well_col = pal["intent"] if temp_c <= 0 else temp_col
        self._draw_fan_well(cr, fx1, fy_fans, fan_r, well_col, pal, surf_hex)
        self._draw_fan_well(cr, fx2, fy_fans, fan_r, well_col, pal, surf_hex)
        cr.restore()

        fan_s = f"DUAL-FAN · {fan_pct:.0f}%" if gpu.get("fan") is not None else "DUAL-FAN · N/A"
        temp_s = f"CORE {temp_c:.0f}°C" if temp_c > 0 else "CORE -"
        self._text(cr, f"{fan_s}   ·   {temp_s}", zx_c, card_y + card_h + 15.0, f_val, temp_col, alpha=0.95, max_w=zw_c, align=Pango.Alignment.CENTER)
        return [(fx1, fy_fans, fan_r, True, 0.0), (fx2, fy_fans, fan_r, False, 0.35)], None

    def _draw_zone2_igpu(
        self, cr, div_l: float, div_r: float, zx_c: float, zw_c: float,
        gpu: dict, temp_c: float, temp_col: str,
        pal: dict, surf_hex: str, f_hdr, f_val, f_sub, f_crest,
    ) -> tuple[list[tuple[float, float, float, bool, float]], tuple[float, float, float, float] | None]:
        vendor = str(gpu.get("vendor") or "generic")
        v_col = pal["intent"] if vendor == "intel" else (pal["fault"] if vendor == "amd" else pal["vitality"])
        v_bg = pal["intent_bg"] if vendor == "intel" else (pal["fault_bg"] if vendor == "amd" else pal["vitality_bg"])
        crest_lbl = "INTEL iGPU" if vendor == "intel" else ("AMD RADEON iGPU" if vendor == "amd" else "INTEGRATED GPU")

        self._text(cr, t("cc_power.gpu_bp_z2_igpu"), zx_c, 10.0, f_hdr, pal["intent"], alpha=0.95, max_w=zw_c * 0.52)
        model_short = str(gpu.get("name") or crest_lbl).replace("Intel Corporation ", "").replace("Advanced Micro Devices, Inc. ", "")
        self._text(cr, model_short, zx_c + zw_c * 0.52, 10.0, f_val, pal["focus_val"], alpha=0.92, max_w=zw_c * 0.48, align=Pango.Alignment.RIGHT)

        pkg_w = min(286.0, max(200.0, zw_c - 86.0))
        pkg_h = 124.0
        pkg_cx = (div_l + div_r) * 0.5
        pkg_x = pkg_cx - pkg_w * 0.5
        pkg_y = 42.0

        cr.save()
        # Horizontal PCB bus traces linking package to UMA Memory (left) and Display (right)
        cr.set_source_rgba(*_bp_hex_rgba(pal["structure"], 0.72))
        cr.set_line_width(1.2)
        for ti in range(5):
            ty = pkg_y + 26.0 + ti * 18.0
            cr.move_to(pkg_x - 30.0, ty)
            cr.line_to(pkg_x, ty)
            cr.move_to(pkg_x + pkg_w, ty)
            cr.line_to(pkg_x + pkg_w + 30.0, ty)
        cr.stroke()
        cr.set_source_rgba(*_bp_hex_rgba(v_col, 0.75))
        for ti in range(5):
            ty = pkg_y + 26.0 + ti * 18.0
            cr.new_sub_path()
            cr.arc(pkg_x - 30.0, ty, 2.0, 0, math.tau)
            cr.new_sub_path()
            cr.arc(pkg_x + pkg_w + 30.0, ty, 2.0, 0, math.tau)
        cr.fill()
        self._text(cr, "UMA", pkg_x - 36.0, pkg_y + 12.0, f_crest, pal["schema"], alpha=0.88, max_w=34.0, align=Pango.Alignment.RIGHT)
        self._text(cr, "DISP", pkg_x + pkg_w + 4.0, pkg_y + 12.0, f_crest, pal["schema"], alpha=0.88, max_w=38.0, align=Pango.Alignment.LEFT)

        # Green/graphite SoC organic package substrate
        _trace_rect(cr, pkg_x + 0.5, pkg_y + 0.5, pkg_w - 1.0, pkg_h - 1.0, 7.0)
        cr.set_source_rgba(*_bp_hex_rgba(surf_hex, 0.94))
        cr.fill_preserve()
        cr.set_source_rgba(*_bp_hex_rgba(pal["schema"], 0.88))
        cr.set_line_width(1.5)
        cr.stroke()

        # Pin-1 gold corner marker + SMD micro-capacitors along left/right substrate edges
        cr.move_to(pkg_x + 6.0, pkg_y + 6.0)
        cr.line_to(pkg_x + 14.0, pkg_y + 6.0)
        cr.line_to(pkg_x + 6.0, pkg_y + 14.0)
        cr.close_path()
        cr.set_source_rgba(*_bp_hex_rgba(pal["attention"], 0.92))
        cr.fill()
        cr.set_source_rgba(*_bp_hex_rgba(pal["attention"], 0.60))
        for ci in range(4):
            cy_c = pkg_y + 28.0 + ci * 18.0
            cr.rectangle(pkg_x + 7.0, cy_c, 4.0, 9.0)
            cr.rectangle(pkg_x + pkg_w - 11.0, cy_c, 4.0, 9.0)
        cr.fill()

        # Top vendor crest badge
        crest_w = min(142.0, pkg_w - 36.0)
        _trace_rect(cr, pkg_cx - crest_w * 0.5, pkg_y + 4.0, crest_w, 16.0, 2.5)
        cr.set_source_rgba(*_bp_hex_rgba(v_bg, 0.90))
        cr.fill_preserve()
        cr.set_source_rgba(*_bp_hex_rgba(v_col, 0.82))
        cr.set_line_width(0.85)
        cr.stroke()
        self._text(cr, crest_lbl, pkg_cx - crest_w * 0.5, pkg_y + 5.0, f_crest, v_col, alpha=0.98, max_w=crest_w, align=Pango.Alignment.CENTER)

        # Silicon Die boundary inside substrate
        die_x = pkg_x + 18.0
        die_y = pkg_y + 25.0
        die_w = pkg_w - 36.0
        die_h = pkg_h - 33.0
        _trace_rect(cr, die_x, die_y, die_w, die_h, 4.0)
        cr.set_source_rgba(*_bp_hex_rgba(pal["structure_bg"], 0.92))
        cr.fill_preserve()
        cr.set_source_rgba(*_bp_hex_rgba(pal["structure"], 0.90))
        cr.set_line_width(1.1)
        cr.stroke()

        # Split Die: Left = CPU Cores (2x4 tile grid), Right = iGPU Engine (CU array), Bottom = UMA Fabric
        uma_h = 19.0
        top_h = die_h - uma_h - 10.0
        half_w = (die_w - 18.0) * 0.5
        cpu_x = die_x + 6.0
        igpu_x = cpu_x + half_w + 6.0
        blk_y = die_y + 5.0

        # CPU block
        _trace_rect(cr, cpu_x, blk_y, half_w, top_h, 3.0)
        cr.set_source_rgba(*_bp_hex_rgba(surf_hex, 0.86))
        cr.fill_preserve()
        cr.set_source_rgba(*_bp_hex_rgba(pal["schema"], 0.55))
        cr.set_line_width(0.9)
        cr.stroke()
        self._text(cr, t("cc_power.gpu_bp_die_cpu"), cpu_x + 4.0, blk_y + 3.0, f_crest, pal["schema"], alpha=0.95, max_w=half_w - 8.0, align=Pango.Alignment.CENTER)
        cw_tile = (half_w - 18.0) / 4.0
        ch_tile = (top_h - 28.0) / 2.0
        cr.set_source_rgba(*_bp_hex_rgba(pal["structure"], 0.65))
        for r_i in range(2):
            for c_i in range(4):
                _trace_rect(cr, cpu_x + 5.0 + c_i * (cw_tile + 2.5), blk_y + 20.0 + r_i * (ch_tile + 3.0), cw_tile, ch_tile, 1.5)
        cr.fill()

        # iGPU Engine block
        _trace_rect(cr, igpu_x, blk_y, half_w, top_h, 3.0)
        cr.set_source_rgba(*_bp_hex_rgba(v_bg, 0.85))
        cr.fill_preserve()
        cr.set_source_rgba(*_bp_hex_rgba(v_col, 0.88))
        cr.set_line_width(1.1)
        cr.stroke()
        self._text(cr, t("cc_power.gpu_bp_die_gpu"), igpu_x + 4.0, blk_y + 3.0, f_crest, v_col, alpha=0.98, max_w=half_w - 8.0, align=Pango.Alignment.CENTER)
        gw_tile = (half_w - 15.0) / 3.0
        gh_tile = (top_h - 28.0) / 2.0
        cr.set_source_rgba(*_bp_hex_rgba(v_col, 0.42))
        for r_i in range(2):
            for c_i in range(3):
                _trace_rect(cr, igpu_x + 5.0 + c_i * (gw_tile + 2.5), blk_y + 20.0 + r_i * (gh_tile + 3.0), gw_tile, gh_tile, 1.5)
        cr.fill()

        # UMA fabric strip across bottom of die
        uma_y = blk_y + top_h + 4.0
        _trace_rect(cr, cpu_x, uma_y, die_w - 12.0, uma_h, 2.5)
        cr.set_source_rgba(*_bp_hex_rgba(pal["structure"], 0.35))
        cr.fill_preserve()
        cr.set_source_rgba(*_bp_hex_rgba(pal["schema"], 0.60))
        cr.set_line_width(0.8)
        cr.stroke()
        self._text(cr, t("cc_power.gpu_bp_die_uma"), cpu_x + 4.0, uma_y + 2.0, f_crest, pal["focus_val"], alpha=0.90, max_w=die_w - 20.0, align=Pango.Alignment.CENTER)
        cr.restore()

        temp_s = f"DIE {temp_c:.0f}°C" if temp_c > 0 else "DIE -"
        self._text(cr, f"{t('cc_power.gpu_bp_igpu_sub')}   ·   {temp_s}", zx_c, pkg_y + pkg_h + 17.0, f_val, temp_col, alpha=0.95, max_w=zw_c, align=Pango.Alignment.CENTER)
        pulse_rect = (igpu_x + 3.0, blk_y + 18.0, half_w - 6.0, top_h - 21.0)
        return [], pulse_rect

    def _ensure_bg_surface(self, w: float, h: float, pal: dict, surf_hex: str, font_family: str) -> None:
        gpu = self._gpu
        psu = self._psu
        cat = str(gpu.get("category") or "nvidia_dgpu")
        vendor = str(gpu.get("vendor") or "nvidia")
        util_pct = float(gpu.get("util") or 0.0)
        has_draw = gpu.get("draw") is not None
        draw_w = float(gpu.get("draw") or 0.0)
        lim_w = float(gpu.get("limit") or (65.0 if cat == "igpu" else 350.0))
        temp_c = float(gpu.get("temp") or 0.0)
        fan_pct = float(gpu.get("fan") or 0.0)
        fan_rpm = gpu.get("fan_rpm")
        clk_mhz = float(gpu.get("clock_gr") or 0.0)
        pstate = str(gpu.get("pstate") or "-")
        temp_col = pal[_temp_role(temp_c)] if temp_c > 0 else pal["schema"]

        psu_sig = (
            round(float(psu.get("total_w") or 0.0), 1),
            round(float(psu.get("r12_w") or 0.0), 1),
            round(float(psu.get("r5_w") or 0.0), 1),
            round(float(psu.get("r3_w") or 0.0), 1),
            round(float(psu.get("vrm_c") or 0.0), 1),
            round(float(psu.get("case_c") or 0.0), 1),
            round(float(psu.get("vin_v") or 0.0), 1),
            int(psu.get("fan_rpm")) if psu.get("fan_rpm") is not None else None,
        ) if psu else None

        base_pt = float((THEME.get("typography") or {}).get("font_size", 11) or 11)
        sig = (
            int(w), int(h), surf_hex, font_family, round(base_pt, 1), cat, vendor,
            str((THEME.get("ascii") or {}).get("box_style", "rounded")),
            pal["intent"], pal["vitality"], pal["attention"], pal["interrupt"], pal["fault"], pal["schema"], pal["structure"], pal["focus"], pal["focus_val"],
            round(util_pct), has_draw, round(draw_w, 1), round(lim_w), round(temp_c), round(fan_pct), fan_rpm, round(clk_mhz), pstate,
            str(gpu.get("name") or ""), psu_sig,
        )
        if sig == self._bg_sig and self._bg_surface is not None:
            return

        bx = float(self._inset_x)
        bw = max(360.0, w - 2.0 * bx)
        footer_h = float(self._footer_h)
        stage_h = h - footer_h

        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, int(math.ceil(w)), int(math.ceil(h)))
        cr = cairo.Context(surf)

        def _mk_font(pt: float, bold: bool = False) -> Pango.FontDescription:
            fd = Pango.FontDescription()
            fd.set_family(font_family)
            if bold:
                fd.set_weight(Pango.Weight.BOLD)
            fd.set_size(int(round(pt * Pango.SCALE)))
            return fd

        f_hdr = _mk_font(base_pt * 0.88, bold=True)
        f_title = _mk_font(base_pt * 1.05, bold=True)
        f_val = _mk_font(base_pt * 0.90, bold=True)
        f_sub = _mk_font(base_pt * 0.85, bold=False)
        f_crest = _mk_font(base_pt * 0.78, bold=True)

        # 1. Outer recessed Tactical Blueprint frame
        _trace_rect(cr, bx + 0.5, 0.5, bw - 1.0, h - 1.0, 6.0)
        cr.set_source_rgba(*_bp_hex_rgba(pal["structure_bg"], 0.64))
        cr.fill()
        cr.set_source_rgba(*_bp_hex_rgba(pal["structure"], 0.90))
        cr.set_line_width(1.0)
        _stroke_styled_frame(cr, bx + 0.5, 0.5, bw - 1.0, h - 1.0, 6.0)

        cr.save()
        _trace_rect(cr, bx + 1.0, 1.0, bw - 2.0, h - 2.0, 5.5)
        cr.clip()

        # Subtle background technical dots
        cr.save()
        cr.set_source_rgba(*_bp_hex_rgba(pal["structure"], 0.22))
        gx = bx + 18.0
        while gx < bx + bw - 12.0:
            gy = 20.0
            while gy < stage_h - 8.0:
                cr.rectangle(gx, gy, 1.1, 1.1)
                gy += 18.0
            gx += 18.0
        cr.fill()
        cr.restore()

        # 2. 3-Zone geometry (Side bays 24% each, Central GPU bay 52%)
        div_l = bx + bw * 0.24
        div_r = bx + bw * 0.76
        cr.save()
        cr.set_source_rgba(*_bp_hex_rgba(pal["structure"], 0.42))
        cr.set_line_width(1.0)
        cr.set_dash([2.5, 4.0])
        for dx_line in (div_l, div_r):
            cr.move_to(math.floor(dx_line) + 0.5, 12.0)
            cr.line_to(math.floor(dx_line) + 0.5, stage_h - 10.0)
        cr.stroke()
        cr.restore()

        # ── Left Zone: GPU Core Load Scope & Vertical TDP / Temp Bar ───────────
        lx = bx + 12.0
        lw = div_l - lx - 10.0
        self._text(cr, t("cc_power.gpu_bp_z1"), lx, 10.0, f_hdr, pal["intent"], alpha=0.95, max_w=lw)

        sc_r = min(40.0, max(30.0, lw * 0.25))
        sc_cx = lx + sc_r + 6.0
        sc_cy = 98.0
        cr.save()
        cr.new_sub_path()
        cr.arc(sc_cx, sc_cy, sc_r, 0, math.tau)
        cr.set_source_rgba(*_bp_hex_rgba(surf_hex, 0.88))
        cr.fill_preserve()
        cr.set_source_rgba(*_bp_hex_rgba(pal["structure"], 0.90))
        cr.set_line_width(1.3)
        cr.stroke()

        cr.new_sub_path()
        cr.arc(sc_cx, sc_cy, sc_r * 0.65, 0, math.tau)
        cr.set_source_rgba(*_bp_hex_rgba(pal["structure"], 0.40))
        cr.set_dash([2.0, 2.5])
        cr.set_line_width(0.9)
        cr.stroke()
        cr.set_dash([])

        if util_pct > 0.0:
            u_col = pal["vitality"] if util_pct < 60 else (pal["attention"] if util_pct < 85 else pal["interrupt"])
            cr.new_sub_path()
            cr.arc(sc_cx, sc_cy, sc_r, -math.pi * 0.5, -math.pi * 0.5 + math.tau * min(1.0, util_pct / 100.0))
            cr.set_source_rgba(*_bp_hex_rgba(u_col, 0.95))
            cr.set_line_width(2.4)
            cr.stroke()
        cr.restore()

        self._text(cr, f"{util_pct:.0f}%", sc_cx - sc_r, sc_cy - 15.0, f_title, pal["focus"], alpha=0.96, max_w=sc_r * 2.0, align=Pango.Alignment.CENTER)
        self._text(cr, pstate, sc_cx - sc_r, sc_cy + 3.0, f_val, pal["intent"], alpha=0.92, max_w=sc_r * 2.0, align=Pango.Alignment.CENTER)
        self._text(cr, f"{clk_mhz:.0f} MHz" if clk_mhz > 0 else "-", sc_cx - sc_r - 10.0, sc_cy + sc_r + 12.0, f_val, pal["focus_val"], alpha=0.92, max_w=sc_r * 2.0 + 20.0, align=Pango.Alignment.CENTER)

        tdp_cx = lx + lw - 32.0
        bar_w = 14.0
        bar_x = tdp_cx - bar_w * 0.5
        bar_y = 56.0
        bar_h = 84.0
        _trace_rect(cr, bar_x + 0.5, bar_y + 0.5, bar_w - 1.0, bar_h - 1.0, 3.0)
        cr.set_source_rgba(*_bp_hex_rgba(surf_hex, 0.90))
        cr.fill_preserve()
        cr.set_source_rgba(*_bp_hex_rgba(pal["structure"], 0.85))
        cr.set_line_width(1.0)
        cr.stroke()

        if has_draw:
            bar_frac = max(0.0, min(1.0, draw_w / lim_w)) if lim_w > 0 else 0.0
            bar_col = pal["intent"]
            bar_lbl = "TDP"
            bar_top_v = f"{draw_w:.0f}W"
            bar_bot_v = f"/{lim_w:.0f}W"
        else:
            bar_frac = max(0.0, min(1.0, temp_c / 95.0)) if temp_c > 0 else 0.0
            bar_col = temp_col
            bar_lbl = t("cc_power.gpu_bp_temp_bar")
            bar_top_v = f"{temp_c:.0f}°C" if temp_c > 0 else "-"
            bar_bot_v = "/95°C"

        if bar_frac > 0.0:
            fh = max(3.0, (bar_h - 4.0) * bar_frac)
            _trace_rect(cr, bar_x + 2.0, bar_y + bar_h - 2.0 - fh, bar_w - 4.0, fh, 2.0)
            cr.set_source_rgba(*_bp_hex_rgba(bar_col, 0.92))
            cr.fill()
        self._text(cr, bar_lbl, tdp_cx - 32.0, 35.0, f_sub, pal["schema"], alpha=0.92, max_w=64.0, align=Pango.Alignment.CENTER)
        self._text(cr, bar_top_v, tdp_cx - 32.0, bar_y + bar_h + 7.0, f_val, pal["focus_val"], alpha=0.95, max_w=64.0, align=Pango.Alignment.CENTER)
        self._text(cr, bar_bot_v, tdp_cx - 32.0, bar_y + bar_h + 24.0, f_sub, pal["schema"], alpha=0.86, max_w=64.0, align=Pango.Alignment.CENTER)

        # ── Center Zone: Multi-Category Hardware Blueprint ─────────────────────
        zx_c = div_l + 12.0
        zw_c = div_r - div_l - 24.0
        if cat == "amd_dgpu":
            fans_geom, pulse_rect = self._draw_zone2_amd(cr, div_l, div_r, zx_c, zw_c, gpu, temp_c, fan_pct, temp_col, pal, surf_hex, f_hdr, f_val, f_crest)
        elif cat == "intel_dgpu":
            fans_geom, pulse_rect = self._draw_zone2_intel(cr, div_l, div_r, zx_c, zw_c, gpu, temp_c, fan_pct, temp_col, pal, surf_hex, f_hdr, f_val, f_crest)
        elif cat == "igpu":
            fans_geom, pulse_rect = self._draw_zone2_igpu(cr, div_l, div_r, zx_c, zw_c, gpu, temp_c, temp_col, pal, surf_hex, f_hdr, f_val, f_sub, f_crest)
        else:
            fans_geom, pulse_rect = self._draw_zone2_nvidia(cr, div_l, div_r, zx_c, zw_c, gpu, temp_c, fan_pct, temp_col, pal, surf_hex, f_hdr, f_val, f_crest)

        # ── Right Zone: PSU Power Rails & Thermal ──────────────────────────────
        rx = div_r + 10.0
        rw = bx + bw - 12.0 - rx
        self._text(cr, t("cc_power.gpu_bp_z3"), rx, 10.0, f_hdr, pal["attention"], alpha=0.95, max_w=rw)

        if psu and psu.get("total_w") is not None:
            tot_w = float(psu["total_w"])
            rails = [
                ("+12V", psu.get("r12_w"), pal["intent"], max(120.0, tot_w)),
                ("+5V", psu.get("r5_w"), pal["vitality"], 40.0),
                ("+3.3V", psu.get("r3_w"), pal["attention"], 30.0),
            ]
            ry = 34.0
            self._text(cr, f"PSU {tot_w:.0f} W", rx, ry, f_title, pal["focus"], alpha=0.96, max_w=rw * 0.60)
            if psu.get("vin_v") is not None:
                self._text(cr, f"{psu['vin_v']:.0f}V AC", rx + rw * 0.60, ry + 2.0, f_sub, pal["schema"], alpha=0.90, max_w=rw * 0.40, align=Pango.Alignment.RIGHT)
            ry += 23.0
            for r_lbl, r_val, r_col, r_max in rails:
                val_w = float(r_val or 0.0)
                self._text(cr, r_lbl, rx, ry, f_sub, pal["schema"], alpha=0.94, max_w=54.0)
                self._text(cr, f"{val_w:.0f}W" if r_val is not None else "-", rx + rw - 52.0, ry, f_val, pal["focus_val"], alpha=0.94, max_w=52.0, align=Pango.Alignment.RIGHT)
                ry += 16.0
                _trace_rect(cr, rx, ry, rw, 6.0, 2.0)
                cr.set_source_rgba(*_bp_hex_rgba(pal["structure"], 0.45))
                cr.fill()
                frac = max(0.04, min(1.0, val_w / r_max)) if r_val is not None else 0.0
                if frac > 0.0:
                    _trace_rect(cr, rx, ry, rw * frac, 6.0, 2.0)
                    cr.set_source_rgba(*_bp_hex_rgba(r_col, 0.92))
                    cr.fill()
                ry += 14.0

            vrm_s = f"VRM {psu['vrm_c']:.1f}°C" if psu.get("vrm_c") is not None else ""
            case_s = f"Case {psu['case_c']:.1f}°C" if psu.get("case_c") is not None else ""
            meta_r = " · ".join(x for x in (vrm_s, case_s) if x)
            has_fan_rpm = psu.get("fan_rpm") is not None
            if meta_r:
                meta_y = stage_h - 38.0 if has_fan_rpm else stage_h - 22.0
                self._text(cr, meta_r, rx, meta_y, f_sub, pal["focus_val"], alpha=0.90, max_w=rw)
            if has_fan_rpm:
                self._text(cr, f"FAN {psu['fan_rpm']} RPM", rx, stage_h - 20.0, f_sub, pal["schema"], alpha=0.88, max_w=rw)
        else:
            sc2_cx = rx + rw * 0.5
            sc2_cy = 96.0
            cr.new_sub_path()
            cr.arc(sc2_cx, sc2_cy, 32.0, 0, math.tau)
            cr.set_source_rgba(*_bp_hex_rgba(surf_hex, 0.88))
            cr.fill_preserve()
            cr.set_source_rgba(*_bp_hex_rgba(temp_col, 0.90))
            cr.set_line_width(2.0)
            cr.stroke()
            self._text(cr, f"{temp_c:.0f}°C" if temp_c > 0 else "-", rx, sc2_cy - 10.0, f_title, pal["focus"], alpha=0.96, max_w=rw, align=Pango.Alignment.CENTER)
            r_sub_txt = f"{clk_mhz:.0f} MHz" if cat == "igpu" and clk_mhz > 0 else (f"{fan_pct:.0f}% FAN" if gpu.get("fan") is not None else "PASSIVE / N/A")
            self._text(cr, r_sub_txt, rx, sc2_cy + 40.0, f_val, pal["schema"], alpha=0.90, max_w=rw, align=Pango.Alignment.CENTER)

        # 3. Integrated Bottom Telemetry Strip
        fy = stage_h
        cr.set_source_rgba(*_bp_hex_rgba(surf_hex, 0.82))
        cr.rectangle(bx + 1.0, fy, bw - 2.0, footer_h)
        cr.fill()
        cr.set_source_rgba(*_bp_hex_rgba(pal["structure"], 0.72))
        cr.set_line_width(1.0)
        cr.move_to(bx + 1.0, fy + 0.5)
        cr.line_to(bx + bw - 1.0, fy + 0.5)
        cr.stroke()

        hint_key = "cc_power.gpu_bp_hint_igpu" if cat == "igpu" else "cc_power.gpu_bp_hint"
        self._text(cr, t(hint_key), bx + 12.0, fy + 6.0, f_sub, pal["schema"], alpha=0.90, max_w=bw * 0.62)
        if gpu:
            rt_parts = []
            if has_draw:
                rt_parts.append(f"{draw_w:.1f}W")
            if temp_c > 0:
                rt_parts.append(f"{temp_c:.0f}°C")
            if cat == "igpu":
                if clk_mhz > 0:
                    rt_parts.append(f"{clk_mhz:.0f} MHz")
            elif gpu.get("fan") is not None:
                rt_parts.append(f"FAN {fan_pct:.0f}%")
            elif fan_rpm is not None:
                rt_parts.append(f"{fan_rpm} RPM")
            rt_stat = " · ".join(rt_parts) or "-"
        else:
            rt_stat = "-"
        self._text(cr, rt_stat, bx + bw * 0.62, fy + 6.0, f_val, pal["vitality"], alpha=0.95, max_w=bw * 0.38 - 14.0, align=Pango.Alignment.RIGHT)

        cr.restore()
        self._bg_surface = surf
        self._bg_sig = sig
        self._fans_geom = fans_geom
        self._igpu_pulse_rect = pulse_rect

    def _on_draw(self, _widget, cr) -> bool:
        alloc = self.get_allocation()
        w = max(480.0, float(alloc.width or self._widget_w))
        h = max(190.0, float(alloc.height or self._widget_h))

        pal = get_semantic_palette()
        surf_hex = str((THEME.get("colors") or {}).get("bg_panel") or pal["structure_bg"])
        font_family = str((THEME.get("typography") or {}).get("font_family", "monospace") or "monospace")

        gpu = self._gpu
        cat = str(gpu.get("category") or "nvidia_dgpu")
        temp_c = float(gpu.get("temp") or 0.0)
        fan_pct = float(gpu.get("fan") or 0.0)
        temp_col = pal[_temp_role(temp_c)] if temp_c > 0 else pal["schema"]

        self._ensure_bg_surface(w, h, pal, surf_hex, font_family)

        # 1. Blit pre-rendered static 3-zone blueprint
        cr.set_source_surface(self._bg_surface, 0.0, 0.0)
        cr.paint()

        # 2. Composite rotating pre-rendered rotor sprites OR live iGPU CU shimmer
        base_ang = float(_GPU_FAN_STATE["angle"])
        if self._fans_geom:
            fr_ref = self._fans_geom[0][2]
            ring_col = (
                pal["fault"]
                if (cat == "amd_dgpu" and temp_c <= 0)
                else (pal["intent"] if (cat == "intel_dgpu" and temp_c <= 0) else temp_col)
            )
            self._ensure_rotor_surfaces(fr_ref, fan_pct, ring_col, pal)
            half = self._rotor_half
            for fx, fy_fans, fan_r, clockwise, phase in self._fans_geom:
                ang = (base_ang + phase) if clockwise else (-base_ang + phase)
                rsurf = self._rotor_cw if clockwise else self._rotor_ccw
                cr.save()
                cr.new_sub_path()
                cr.arc(fx, fy_fans, fan_r - 1.5, 0, math.tau)
                cr.clip()
                cr.translate(fx, fy_fans)
                cr.rotate(ang)
                cr.set_source_surface(rsurf, -half, -half)
                cr.paint()
                cr.restore()

                cr.new_sub_path()
                cr.arc(fx, fy_fans, fan_r * 0.29, 0, math.tau)
                cr.set_source_rgba(*_bp_hex_rgba(pal["structure_bg"], 0.96))
                cr.fill_preserve()
                cr.set_source_rgba(*_bp_hex_rgba(ring_col, 0.90))
                cr.set_line_width(1.3)
                cr.stroke()

                cr.new_sub_path()
                cr.arc(fx, fy_fans, 3.2, 0, math.tau)
                cr.set_source_rgba(*_bp_hex_rgba(pal["focus"], 0.92))
                cr.fill()
        elif cat == "igpu" and self._igpu_pulse_rect is not None and float(gpu.get("util") or 0.0) > 1.0:
            px, py, pw, ph = self._igpu_pulse_rect
            vendor = str(gpu.get("vendor") or "generic")
            v_col = pal["intent"] if vendor == "intel" else (pal["fault"] if vendor == "amd" else pal["vitality"])
            scan_frac = 0.5 + 0.5 * math.sin(base_ang)
            sx = px + 6.0 + scan_frac * max(1.0, pw - 22.0)
            cr.save()
            _trace_rect(cr, px, py, pw, ph, 2.0)
            cr.clip()
            _trace_rect(cr, sx, py + 2.0, 10.0, ph - 4.0, 2.0)
            cr.set_source_rgba(*_bp_hex_rgba(v_col, 0.34))
            cr.fill()
            cr.restore()

        return False


_GpuFoundersBlueprintWidget = _GpuHardwareBlueprintWidget


def _build_hw_power_box(canvas: Canvas, st: dict) -> AsciiCard | Box | None:
    gpu = st["gpu"]
    psu = st["psu"]
    if not gpu and not st["gpu_info"] and not psu:
        return None

    hdr_hw = (gpu.get("name") if gpu else "") or (psu.get("name", "").upper() if psu else "")
    actions = [Txt(hdr_hw, role="schema")] if hdr_hw else None
    box = Box(t("cc_power.hw_power_title"), icon="\uf108", actions=actions, highlighted=False, open_bottom=True)
    box.add_row(" ")

    foot = Box("", highlighted=False, open_top=True)
    if not gpu and not psu and st["gpu_info"]:
        foot.add_row(" ")
        foot.add_kv(t("cc_power.gpu_power"), Txt(st["gpu_info"], role="val"))
    foot.add_row(" ")
    inset_px = max(14.0, float(canvas.char_w) * 2.0)
    return AsciiCard(box, canvas, embedded_widget=_GpuHardwareBlueprintWidget(st, inset_x=inset_px), footer_box_model=foot)



def _build_gpu_summary_box(canvas: Canvas, gpu: dict) -> Box:
    box = Box(t("cc_power.gpu_telemetry_title"), icon="\uf2db", highlighted=False)
    box.add_row(" ")
    if gpu.get("available"):
        gp = float(gpu["util_pct"])
        gt = float(gpu["temp_c"])
        gt_role = _temp_role(gt, warn_c=65.0, fault_c=80.0, ok_role="vitality")
        vu = float(gpu["vram_used"]) / (1024 ** 3)
        vt = float(gpu["vram_total"]) / (1024 ** 3)
        vram_str = f"{vu:.1f}/{vt:.0f} GiB" if vt > 0 else "UMA"
        pw = float(gpu.get("power_w") or 0.0)
        gpu_right = Txt(f"{pw:.0f}W", role="schema") if pw > 0 else None
        box.add_kv(
            t("cc_system.gpu_vram_row"),
            f"{ProgressBar(gp, width=20)} {Txt(f'{gp:4.1f}%', role=_pct_role(gp))}  "
            f"{Txt(f'{gt:.0f}°C', role=gt_role)}  {Txt(vram_str, role='val')}",
            right_markup=gpu_right,
        )
    else:
        box.add_row(f"  {Txt(t('cc_power.gpu_not_detected'), role='inactive')}")
    box.add_row(" ")
    return box


def _build_gpu_standard(cc_panel, canvas: Canvas) -> list:
    cards = _feedback_cards()
    st = _collect_cpu_power(async_refresh=True, cc_panel=cc_panel)
    hw_box = _build_hw_power_box(canvas, st)
    if hw_box:
        cards.append(hw_box)

    gpu = get_gpu_telemetry(include_procs=False)
    cards.append(_build_gpu_summary_box(canvas, gpu))

    cols, rows, target_widget_h = _compute_single_tui_size(
        cc_panel, canvas, cards, t("cc_system.nvtop_box_title"), "\uf2db", min_rows=12, flush=True, has_footer=False
    )
    cards.append(
        _build_tui_ncbox(
            cc_panel,
            canvas,
            tui_id="nvtop",
            cmd=[_ensure_nvtop_bin()],
            rows=rows,
            cols=cols,
            target_height_px=target_widget_h,
            title=t("cc_system.nvtop_box_title"),
            icon="\uf2db",
            open_key="tui_nvtop_open",
            desc_key="cc_system.nvtop_collapsed_desc",
        )
    )
    return cards


def build_cards(cc_panel, canvas: Canvas) -> list:
    return _build_gpu_standard(cc_panel, canvas)
