#!/usr/bin/env python3
import math
import os
from core.config import THEME, _parse_rgba_components
from core.i18n import t
from core.privileges import build_privilege_modal_card
from core.state import STATE
from ui.canvas import Canvas
from ui.kit import (
    ActionBtn,
    Badge,
    Box,
    InlineField,
    InlineFilter,
    InspectorCell,
    SelectPill,
    StateBadge,
    Txt,
    get_semantic_palette,
)
from ui.widgets import AsciiCard
from core.pad_mapper_core import get_macro_pad_usage
from panels.cc_engine_input import (
    _RUNTIME_REC,
    _collect_keyboard_state,
    _feedback_box,
    _find_saved_macro,
    _get_macro_draft,
    _is_macro_engine_running,
    _load_macros,
    compile_macro_timeline,
    is_macro_draft_dirty,
)

_TICK_STEPS_MS = (5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 5000)


def _hex_rgba(hex_str: str, alpha: float = 1.0) -> tuple[float, float, float, float]:
    r, g, b, _ = _parse_rgba_components(hex_str)
    return (r / 255.0, g / 255.0, b / 255.0, alpha)


def _rounded_rect(cr, x: float, y: float, w: float, h: float, r: float) -> None:
    rad = max(0.0, min(float(r), float(w) * 0.5, float(h) * 0.5))
    if rad <= 0.0:
        cr.rectangle(x, y, w, h)
        return
    cr.new_sub_path()
    cr.arc(x + w - rad, y + rad, rad, -math.pi * 0.5, 0.0)
    cr.arc(x + w - rad, y + h - rad, rad, 0.0, math.pi * 0.5)
    cr.arc(x + rad, y + h - rad, rad, math.pi * 0.5, math.pi)
    cr.arc(x + rad, y + rad, rad, math.pi, math.pi * 1.5)
    cr.close_path()


class _MacroTimelineMixin:
    """Interactive pure-Cairo horizontal keystroke timeline (gaming macro studio)."""

    EDGE_GRAB_PX = 9.0

    def __init__(
        self,
        raw_steps: list[dict],
        timing_mode: str,
        fixed_ms: int,
        speed_mult: float,
        selected_idx: int,
        is_rec: bool,
        panic_key: str,
        on_select=None,
        on_timing_commit=None,
        widget_width: int = 760,
        fixed_height: int = 152,
        inset_x: int = 14,
        font_px: float = 11.0,
    ):
        super().__init__()
        self.raw_steps = [dict(s) for s in (raw_steps or [])]
        self.timing_mode = str(timing_mode or "recorded")
        self.fixed_ms = max(5, int(fixed_ms or 25))
        self.speed_mult = max(0.1, float(speed_mult or 1.0))
        self.selected_idx = int(selected_idx or 0)
        self.is_rec = bool(is_rec)
        self.panic_key = str(panic_key or "F12")
        self.on_select = on_select
        self.on_timing_commit = on_timing_commit
        self.widget_width = max(240, int(widget_width))
        self.widget_height = max(124, int(fixed_height))
        self.inset_x = max(4, int(inset_x))
        self.font_px = max(8.5, float(font_px))
        self.ruler_h = 24
        self.footer_h = 24
        self._blocks: list[dict] = []
        self._span_ms = 200.0
        self._px_per_ms = 1.0
        self._hovered_idx: int | None = None
        self._hovered_edge: bool = False
        self._drag: dict | None = None
        self._init_widget()

    def _init_widget(self) -> None:
        from gi.repository import Gdk

        self.set_size_request(self.widget_width, self.widget_height)
        self.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
            | Gdk.EventMask.LEAVE_NOTIFY_MASK
        )
        self.connect("size-allocate", self._on_size_allocate)
        self.connect("draw", self._on_draw)
        self.connect("button-press-event", self._on_press)
        self.connect("motion-notify-event", self._on_motion)
        self.connect("button-release-event", self._on_release)
        self.connect("leave-notify-event", self._on_leave)

    def _on_size_allocate(self, _widget, alloc) -> None:
        if alloc.width > (2 * self.inset_x + 40) and abs(alloc.width - self.widget_width) > 1:
            self.widget_width = alloc.width
            self.queue_draw()

    def _set_cursor(self, name: str | None) -> None:
        try:
            win = self.get_window()
            if win is None:
                return
            if not name:
                win.set_cursor(None)
                return
            from gi.repository import Gdk

            win.set_cursor(Gdk.Cursor.new_from_name(win.get_display(), name))
        except Exception:
            pass

    def _active_steps(self) -> list[dict]:
        steps = [dict(s) for s in self.raw_steps]
        if self._drag is not None:
            idx = self._drag["idx"]
            if 0 <= idx < len(steps):
                steps[idx]["delay_ms"] = self._drag["live_delay"]
                steps[idx]["hold_ms"] = self._drag["live_hold"]
        return steps

    def _compute_layout(self, w: float, h: float) -> tuple[list[dict], float, float, float, float]:
        bx = float(self.inset_x)
        bw = max(120.0, float(w) - 2.0 * bx)
        pad_l, pad_r = 14.0, 16.0
        usable_w = max(60.0, bw - pad_l - pad_r)
        steps = self._active_steps()
        compiled, tot_ms = compile_macro_timeline(steps, self.timing_mode, self.fixed_ms, self.speed_mult)

        if self._drag is not None and self._drag.get("locked_span_ms"):
            span_ms = max(float(self._drag["locked_span_ms"]), float(tot_ms) * 1.04)
        else:
            span_ms = max(120.0, float(tot_ms) * 1.10) if compiled else 200.0
        px_per_ms = usable_w / max(1.0, span_ms)

        # Assign visual lanes preventing both temporal overlap and min-width visual collisions
        lane_px_ends: list[float] = []
        blocks: list[dict] = []
        min_bw = 26.0
        max_x_right = bx + bw - pad_r
        for item in compiled:
            x = bx + pad_l + item["start_ms"] * px_per_ms
            raw_w = max(4.0, (item["end_ms"] - item["start_ms"]) * px_per_ms)
            bw_item = max(min_bw, raw_w)
            if x + bw_item > max_x_right:
                bw_item = max(18.0, max_x_right - x)
            lane = -1
            for li, end_px in enumerate(lane_px_ends):
                if x >= end_px + 3.0:
                    lane = li
                    lane_px_ends[li] = x + bw_item
                    break
            if lane < 0:
                if len(lane_px_ends) < 3:
                    lane = len(lane_px_ends)
                    lane_px_ends.append(x + bw_item)
                else:
                    lane = min(range(len(lane_px_ends)), key=lambda li: lane_px_ends[li])
                    lane_px_ends[lane] = max(lane_px_ends[lane], x + bw_item)
            blk = dict(item)
            blk["x"] = x
            blk["w"] = bw_item
            blk["vis_lane"] = lane
            blocks.append(blk)

        n_lanes = max(1, len(lane_px_ends))
        track_y = float(self.ruler_h) + 6.0
        track_h = max(36.0, float(h) - float(self.ruler_h) - float(self.footer_h) - 12.0)
        gap_y = 6.0 if n_lanes > 1 else 0.0
        block_h = min(44.0, max(22.0, (track_h - gap_y * (n_lanes - 1)) / float(n_lanes)))
        total_lanes_h = n_lanes * block_h + gap_y * (n_lanes - 1)
        start_y = track_y + max(0.0, (track_h - total_lanes_h) * 0.5)
        for blk in blocks:
            blk["y"] = start_y + blk["vis_lane"] * (block_h + gap_y)
            blk["h"] = block_h

        self._blocks = blocks
        self._span_ms = span_ms
        self._px_per_ms = px_per_ms
        return blocks, bx, bw, pad_l, span_ms

    def _hit_test(self, px: float, py: float) -> tuple[int | None, bool]:
        if not self._blocks:
            return None, False
        order = sorted(
            range(len(self._blocks)),
            key=lambda i: (self._blocks[i]["idx"] == self.selected_idx, i),
        )
        for bi in reversed(order):
            b = self._blocks[bi]
            if b["x"] <= px <= b["x"] + b["w"] + 2.0 and b["y"] <= py <= b["y"] + b["h"]:
                on_edge = (
                    self.timing_mode == "recorded"
                    and not self.is_rec
                    and b["w"] >= 20.0
                    and px >= (b["x"] + b["w"] - self.EDGE_GRAB_PX)
                )
                return b["idx"], on_edge
        return None, False

    def _on_draw(self, _widget, cr) -> bool:
        from gi.repository import Pango, PangoCairo

        alloc = self.get_allocation()
        w = max(240.0, float(alloc.width or self.widget_width))
        h = max(124.0, float(alloc.height or self.widget_height))
        blocks, bx, bw, pad_l, span_ms = self._compute_layout(w, h)
        pal = get_semantic_palette()
        font_family = str((THEME.get("typography") or {}).get("font_family", "monospace") or "monospace")

        ruler_h = float(self.ruler_h)
        footer_h = float(self.footer_h)
        track_bot = h - footer_h

        # 1. Outer recessed Tactical Blueprint container
        _rounded_rect(cr, bx + 0.5, 0.5, bw - 1.0, h - 1.0, 6.0)
        cr.set_source_rgba(*_hex_rgba(pal["structure_bg"], 0.62))
        cr.fill_preserve()
        cr.set_source_rgba(*_hex_rgba(pal["fault"] if self.is_rec else pal["structure"], 0.90))
        cr.set_line_width(1.2 if self.is_rec else 1.0)
        cr.stroke()

        cr.save()
        _rounded_rect(cr, bx + 1.0, 1.0, bw - 2.0, h - 2.0, 5.5)
        cr.clip()

        # 2. Top Millisecond Ruler Band
        surf_hex = str((THEME.get("colors") or {}).get("bg_panel") or pal["structure_bg"])
        cr.set_source_rgba(*_hex_rgba(surf_hex, 0.72))
        cr.rectangle(bx + 1.0, 1.0, bw - 2.0, ruler_h - 1.0)
        cr.fill()
        cr.set_source_rgba(*_hex_rgba(pal["structure"], 0.72))
        cr.set_line_width(1.0)
        cr.move_to(bx + 1.0, ruler_h + 0.5)
        cr.line_to(bx + bw - 1.0, ruler_h + 0.5)
        cr.stroke()

        micro_sz = int(round(max(8.0, self.font_px * 0.80) * Pango.SCALE))
        key_sz = int(round(max(9.0, self.font_px * 0.96) * Pango.SCALE))
        sub_sz = int(round(max(7.8, self.font_px * 0.76) * Pango.SCALE))

        f_micro = Pango.FontDescription()
        f_micro.set_family(font_family)
        f_micro.set_absolute_size(micro_sz)

        # Right mode pill in ruler (measure first so tick labels never overlap it)
        spd_lbl = f"{self.speed_mult:.1f}"
        if self.is_rec:
            mode_txt = t("cc_keyboard.tl_mode_live", p0=self.panic_key)
            mode_col = pal["fault"]
        elif self.timing_mode == "fixed":
            mode_txt = t("cc_keyboard.tl_mode_fixed", p0=self.fixed_ms, p1=spd_lbl)
            mode_col = pal["intent"]
        elif self.timing_mode == "zero":
            mode_txt = t("cc_keyboard.tl_mode_zero")
            mode_col = pal["attention"]
        else:
            mode_txt = t("cc_keyboard.tl_mode_rec", p0=spd_lbl)
            mode_col = pal["schema"]

        ml_lay = PangoCairo.create_layout(cr)
        ml_lay.set_font_description(f_micro)
        ml_lay.set_text(mode_txt, -1)
        _, ml_ext = ml_lay.get_pixel_extents()
        mode_x = bx + bw - 12.0 - ml_ext.width
        cr.set_source_rgba(*_hex_rgba(mode_col, 0.95))
        cr.move_to(mode_x, max(3.0, (ruler_h - ml_ext.height) * 0.5))
        PangoCairo.show_layout(cr, ml_lay)

        # Ruler ticks & vertical gridlines
        min_ms_gap = 82.0 / max(0.05, self._px_per_ms)
        step_ms = next((s for s in _TICK_STEPS_MS if s >= min_ms_gap), 5000)
        t_cur = 0.0
        while t_cur <= span_ms + 0.1:
            tx = bx + pad_l + t_cur * self._px_per_ms
            if tx > bx + bw - 10.0:
                break
            # Vertical dashed gridline in track area
            cr.save()
            cr.set_source_rgba(*_hex_rgba(pal["structure"], 0.34 if t_cur > 0 else 0.52))
            cr.set_line_width(1.0)
            if t_cur > 0:
                cr.set_dash([2.5, 4.0])
            cr.move_to(math.floor(tx) + 0.5, ruler_h + 1.0)
            cr.line_to(math.floor(tx) + 0.5, track_bot - 1.0)
            cr.stroke()
            cr.restore()

            # Major tick mark on ruler
            cr.set_source_rgba(*_hex_rgba(pal["schema"], 0.65))
            cr.set_line_width(1.0)
            cr.move_to(math.floor(tx) + 0.5, ruler_h - 6.0)
            cr.line_to(math.floor(tx) + 0.5, ruler_h)
            cr.stroke()

            lbl_str = f"{int(round(t_cur))}ms" if t_cur < 1000 else f"{t_cur / 1000.0:.1f}s"
            tl = PangoCairo.create_layout(cr)
            tl.set_font_description(f_micro)
            tl.set_text(lbl_str, -1)
            _, te = tl.get_pixel_extents()
            if tx + 4.0 + te.width < mode_x - 10.0:
                cr.set_source_rgba(*_hex_rgba(pal["schema"], 0.85))
                cr.move_to(tx + 4.0, max(3.0, (ruler_h - te.height) * 0.5))
                PangoCairo.show_layout(cr, tl)

            # Halfway minor tick
            mid_x = tx + (step_ms * 0.5) * self._px_per_ms
            if mid_x < bx + bw - 12.0:
                cr.set_source_rgba(*_hex_rgba(pal["structure"], 0.70))
                cr.move_to(math.floor(mid_x) + 0.5, ruler_h - 3.5)
                cr.line_to(math.floor(mid_x) + 0.5, ruler_h)
                cr.stroke()
            t_cur += float(step_ms)

        # 3. Track Contents (Empty State vs Keycap Event Blocks)
        if not blocks:
            el = PangoCairo.create_layout(cr)
            f_empty = Pango.FontDescription()
            f_empty.set_family(font_family)
            f_empty.set_absolute_size(key_sz)
            el.set_font_description(f_empty)
            el.set_alignment(Pango.Alignment.CENTER)
            el.set_width(int(max(80.0, bw - 32.0) * Pango.SCALE))
            el.set_text(t("cc_keyboard.tl_empty_hint"), -1)
            _, ee = el.get_pixel_extents()
            cr.set_source_rgba(*_hex_rgba(pal["inactive"], 0.92))
            cr.move_to(bx + 16.0, ruler_h + max(8.0, (track_bot - ruler_h - ee.height) * 0.5))
            PangoCairo.show_layout(cr, el)
        else:
            # Wait-gap connectors between sequential blocks
            for bi in range(1, len(blocks)):
                prev_b = blocks[bi - 1]
                cur_b = blocks[bi]
                gap_x1 = prev_b["x"] + prev_b["w"]
                gap_x2 = cur_b["x"]
                if prev_b["vis_lane"] == cur_b["vis_lane"] and gap_x2 - gap_x1 >= 8.0:
                    cy = cur_b["y"] + cur_b["h"] * 0.5
                    cr.save()
                    cr.set_source_rgba(*_hex_rgba(pal["structure"], 0.78))
                    cr.set_line_width(1.0)
                    cr.set_dash([3.0, 3.0])
                    cr.move_to(gap_x1 + 2.0, cy)
                    cr.line_to(gap_x2 - 2.0, cy)
                    cr.stroke()
                    cr.restore()
                    if gap_x2 - gap_x1 >= 44.0:
                        gl = PangoCairo.create_layout(cr)
                        gl.set_font_description(f_micro)
                        gl.set_text(f"+{int(round(cur_b['eff_delay']))}ms", -1)
                        _, ge = gl.get_pixel_extents()
                        if ge.width <= (gap_x2 - gap_x1 - 6.0):
                            cr.set_source_rgba(*_hex_rgba(pal["schema"], 0.78))
                            cr.move_to(gap_x1 + (gap_x2 - gap_x1 - ge.width) * 0.5, cy - ge.height - 2.0)
                            PangoCairo.show_layout(cr, gl)

            f_key = Pango.FontDescription()
            f_key.set_family(font_family)
            f_key.set_weight(Pango.Weight.BOLD)
            f_key.set_absolute_size(key_sz)
            f_sub = Pango.FontDescription()
            f_sub.set_family(font_family)
            f_sub.set_absolute_size(sub_sz)

            drag_idx = self._drag["idx"] if self._drag else None
            draw_order = sorted(
                blocks,
                key=lambda b: (b["idx"] == drag_idx, b["idx"] == self.selected_idx, b["idx"] == self._hovered_idx),
            )
            for b in draw_order:
                ix, iy, iw, ih = b["x"], b["y"], b["w"], b["h"]
                is_drag = b["idx"] == drag_idx
                is_sel = b["idx"] == self.selected_idx
                is_hov = b["idx"] == self._hovered_idx
                is_mod = b["is_mod"]

                cr.save()
                _rounded_rect(cr, ix + 0.5, iy + 0.5, iw - 1.0, ih - 1.0, 4.5)
                if is_sel or is_drag:
                    cr.set_source_rgba(*_hex_rgba(pal["intent_bg"], 0.92 if is_drag else 0.84))
                elif is_hov:
                    cr.set_source_rgba(*_hex_rgba(pal["structure_bg"], 0.98))
                else:
                    cr.set_source_rgba(*_hex_rgba(surf_hex, 0.88))
                cr.fill_preserve()

                if is_sel or is_drag:
                    cr.set_source_rgba(*_hex_rgba(pal["intent"], 1.0))
                    cr.set_line_width(1.6 if is_drag else 1.4)
                elif is_hov:
                    cr.set_source_rgba(*_hex_rgba(pal["focus"], 0.92))
                    cr.set_line_width(1.2)
                else:
                    cr.set_source_rgba(*_hex_rgba(pal["attention"] if is_mod else pal["structure"], 0.72 if is_mod else 0.95))
                    cr.set_line_width(1.0)
                cr.stroke()

                # Clip interior for onset accent stripe, key label, hold telemetry, and resize grip
                _rounded_rect(cr, ix + 1.0, iy + 1.0, max(2.0, iw - 2.0), max(2.0, ih - 2.0), 4.0)
                cr.clip()

                # Left press-onset vertical bar (▼ Key Down marker)
                accent_col = pal["intent"] if (is_sel or is_drag) else (pal["attention"] if is_mod else pal["vitality"])
                cr.set_source_rgba(*_hex_rgba(accent_col, 0.95))
                cr.rectangle(ix + 1.0, iy + 1.0, 3.2, ih - 2.0)
                cr.fill()

                # Keycap text + hold duration
                pad_in = 8.0
                avail_txt_w = max(10.0, iw - pad_in - (10.0 if (is_sel or is_hov) else 5.0))
                kl = PangoCairo.create_layout(cr)
                kl.set_font_description(f_key)
                kl.set_ellipsize(Pango.EllipsizeMode.END)
                kl.set_width(int(avail_txt_w * Pango.SCALE))
                kl.set_text(b["key"], -1)
                _, ke = kl.get_pixel_extents()

                show_sub = ih >= 33.0 and iw >= 48.0
                if show_sub:
                    sl = PangoCairo.create_layout(cr)
                    sl.set_font_description(f_sub)
                    sl.set_ellipsize(Pango.EllipsizeMode.END)
                    sl.set_width(int(avail_txt_w * Pango.SCALE))
                    sl.set_text(f"{int(round(b['eff_hold']))}ms", -1)
                    _, se = sl.get_pixel_extents()
                    tot_th = ke.height + 1.5 + se.height
                    ty = iy + max(3.0, (ih - tot_th) * 0.5)
                else:
                    sl = None
                    ty = iy + max(2.0, (ih - ke.height) * 0.5)

                cr.set_source_rgba(*_hex_rgba(pal["focus"] if (is_sel or is_hov or is_drag) else pal["focus_val"], 1.0))
                cr.move_to(ix + pad_in, ty)
                PangoCairo.show_layout(cr, kl)

                if sl is not None:
                    cr.set_source_rgba(*_hex_rgba(pal["intent"] if (is_sel or is_drag) else pal["schema"], 0.92))
                    cr.move_to(ix + pad_in, ty + ke.height + 1.5)
                    PangoCairo.show_layout(cr, sl)

                # Step index tag in top-right when block is wide enough
                if iw >= 76.0 and ih >= 30.0:
                    il = PangoCairo.create_layout(cr)
                    il.set_font_description(f_sub)
                    il.set_text(f"#{b['idx'] + 1:02d}", -1)
                    _, ie = il.get_pixel_extents()
                    cr.set_source_rgba(*_hex_rgba(pal["intent"] if is_sel else pal["schema"], 0.72))
                    cr.move_to(ix + iw - ie.width - 10.0, iy + 3.0)
                    PangoCairo.show_layout(cr, il)

                # Subtle right-edge drag handle dots when editable in recorded mode
                if self.timing_mode == "recorded" and not self.is_rec and (is_sel or is_hov or is_drag) and iw >= 30.0:
                    gx = ix + iw - 5.5
                    cy_mid = iy + ih * 0.5
                    grip_col = pal["intent"] if (is_drag or self._hovered_edge) else pal["schema"]
                    cr.set_source_rgba(*_hex_rgba(grip_col, 0.90))
                    for dy in (-4.0, 0.0, 4.0):
                        cr.arc(gx, cy_mid + dy, 1.15, 0.0, 2.0 * math.pi)
                        cr.fill()
                cr.restore()

            # Live recording red playhead cursor at end of last captured step
            if self.is_rec:
                last_end_x = max(b["x"] + b["w"] for b in blocks) + 4.0
                ph_x = min(bx + bw - 8.0, last_end_x)
                cr.set_source_rgba(*_hex_rgba(pal["fault"], 0.95))
                cr.set_line_width(1.6)
                cr.move_to(ph_x, ruler_h + 1.0)
                cr.line_to(ph_x, track_bot - 1.0)
                cr.stroke()

        # 4. Integrated Telemetry Footer Strip
        fy = track_bot
        cr.set_source_rgba(*_hex_rgba(surf_hex, 0.78))
        cr.rectangle(bx + 1.0, fy, bw - 2.0, footer_h)
        cr.fill()
        cr.set_source_rgba(*_hex_rgba(pal["structure"], 0.72))
        cr.set_line_width(1.0)
        cr.move_to(bx + 1.0, fy + 0.5)
        cr.line_to(bx + bw - 1.0, fy + 0.5)
        cr.stroke()

        if self._drag is not None and 0 <= self._drag["idx"] < len(blocks):
            db = next((b for b in blocks if b["idx"] == self._drag["idx"]), None)
            left_txt = (
                t("cc_keyboard.tl_footer_drag", p0=f"{db['idx'] + 1:02d}", p1=db["key"], p2=self._drag["live_delay"], p3=self._drag["live_hold"])
                if db else ""
            )
            left_col = pal["intent"]
        elif blocks:
            focus_idx = self._hovered_idx if self._hovered_idx is not None else self.selected_idx
            fb = next((b for b in blocks if b["idx"] == focus_idx), blocks[0])
            left_txt = t(
                "cc_keyboard.tl_footer_step",
                p0=f"{fb['idx'] + 1:02d}",
                p1=fb["key"],
                p2=fb["raw_delay"],
                p3=fb["raw_hold"],
                p4=fb["keycode"],
            )
            left_col = pal["focus_val"]
        else:
            left_txt = t("cc_keyboard.tl_footer_empty")
            left_col = pal["inactive"]

        hint_txt = t("cc_keyboard.tl_footer_hint") if self.timing_mode == "recorded" else t("cc_keyboard.tl_footer_fixed_hint")

        fl_r = PangoCairo.create_layout(cr)
        fl_r.set_font_description(f_micro)
        fl_r.set_text(hint_txt, -1)
        _, re_r = fl_r.get_pixel_extents()

        fpad = 12.0
        fl_l = PangoCairo.create_layout(cr)
        fl_l.set_font_description(f_micro)
        fl_l.set_ellipsize(Pango.EllipsizeMode.END)
        fl_l.set_width(int(max(40.0, bw - fpad * 3.0 - re_r.width) * Pango.SCALE))
        fl_l.set_text(left_txt, -1)
        _, re_l = fl_l.get_pixel_extents()

        fty = fy + max(2.0, (footer_h - max(re_l.height, re_r.height)) * 0.5)
        cr.set_source_rgba(*_hex_rgba(left_col, 0.94))
        cr.move_to(bx + fpad, fty)
        PangoCairo.show_layout(cr, fl_l)

        cr.set_source_rgba(*_hex_rgba(pal["schema"], 0.85))
        cr.move_to(bx + bw - fpad - re_r.width, fty)
        PangoCairo.show_layout(cr, fl_r)

        cr.restore()
        return False

    def _on_press(self, _widget, event) -> bool:
        if event.button != 1:
            return False
        hit_idx, on_edge = self._hit_test(event.x, event.y)
        if hit_idx is None or hit_idx >= len(self.raw_steps):
            return False
        st = self.raw_steps[hit_idx]
        self._drag = {
            "idx": hit_idx,
            "mode": "resize_hold" if on_edge else "move_delay",
            "mouse_x": float(event.x_root),
            "orig_delay": int(st.get("delay_ms", 0) or 0),
            "orig_hold": max(10, int(st.get("hold_ms", 50) or 50)),
            "live_delay": int(st.get("delay_ms", 0) or 0),
            "live_hold": max(10, int(st.get("hold_ms", 50) or 50)),
            "locked_span_ms": self._span_ms,
            "moved": False,
        }
        if self.timing_mode == "recorded" and not self.is_rec:
            self._set_cursor("ew-resize" if on_edge else "grabbing")
        self.queue_draw()
        return True

    def _on_motion(self, _widget, event) -> bool:
        if self._drag is not None:
            dx = float(event.x_root) - self._drag["mouse_x"]
            if self.timing_mode == "recorded" and not self.is_rec and abs(dx) >= 4.0:
                self._drag["moved"] = True
                delta_eff = dx / max(0.05, self._px_per_ms)
                delta_raw = int(round((delta_eff * self.speed_mult) / 5.0) * 5)
                if self._drag["mode"] == "resize_hold":
                    self._drag["live_hold"] = max(10, min(10000, self._drag["orig_hold"] + delta_raw))
                else:
                    self._drag["live_delay"] = max(0, min(10000, self._drag["orig_delay"] + delta_raw))
                self.queue_draw()
            return True

        hit_idx, on_edge = self._hit_test(event.x, event.y)
        if hit_idx != self._hovered_idx or on_edge != self._hovered_edge:
            self._hovered_idx = hit_idx
            self._hovered_edge = on_edge
            if hit_idx is None:
                self._set_cursor(None)
            elif on_edge:
                self._set_cursor("ew-resize")
            elif self.timing_mode == "recorded" and not self.is_rec:
                self._set_cursor("grab")
            else:
                self._set_cursor("pointer")
            self.queue_draw()
        return False

    def _on_leave(self, _widget, _event) -> bool:
        if self._hovered_idx is not None:
            self._hovered_idx = None
            self._hovered_edge = False
            if self._drag is None:
                self._set_cursor(None)
            self.queue_draw()
        return False

    def _on_release(self, _widget, event) -> bool:
        if self._drag is None or event.button != 1:
            return False
        drag = self._drag
        self._drag = None
        self._set_cursor(None)
        if drag["moved"] and callable(self.on_timing_commit):
            self.on_timing_commit(drag["idx"], drag["live_delay"], drag["live_hold"])
        elif callable(self.on_select):
            self.on_select(drag["idx"])
        self.queue_draw()
        return True


_MACRO_TIMELINE_CLS = None


def MacroTimelineWidget(*args, **kwargs):
    global _MACRO_TIMELINE_CLS
    if _MACRO_TIMELINE_CLS is None:
        import gi

        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk

        class _MacroTimeline(_MacroTimelineMixin, Gtk.DrawingArea):
            pass

        _MACRO_TIMELINE_CLS = _MacroTimeline
    return _MACRO_TIMELINE_CLS(*args, **kwargs)


def _build_macros_standard(cc_panel, canvas: Canvas, st: dict) -> list:
    cards = _feedback_box() + build_privilege_modal_card(cc_panel, canvas)
    cap_mode = str(STATE.get("keyboard", "capture_mode", "") or "")
    cap_prev = str(STATE.get("keyboard", "capture_preview", "") or "")
    draft = _get_macro_draft()
    macros = _load_macros()
    saved = _find_saved_macro(draft["id"], macros)
    is_dirty = is_macro_draft_dirty(draft, macros)
    uinput_ok = os.access("/dev/uinput", os.W_OK)

    is_rec = (cap_mode == "macro_rec")
    is_trig_cap = (cap_mode == "macro_trigger")
    is_running = _is_macro_engine_running()
    sandbox_out = str(STATE.get("keyboard", "sandbox_output", "") or "")
    steps = _RUNTIME_REC["steps"] if is_rec else draft["steps"]
    bound_combos = {b["combo"] for b in st.get("binds", [])}
    pad_usage = get_macro_pad_usage()
    panic_key = str(draft.get("panic_key") or "F12")

    sel_idx = int(STATE.get("keyboard", "macro_sel_step", 0) or 0)
    if steps:
        sel_idx = max(0, min(len(steps) - 1, sel_idx))
    else:
        sel_idx = 0

    _compiled, tot_ms = compile_macro_timeline(steps, draft["timing_mode"], draft["fixed_delay_ms"], draft["speed_mult"])

    # =====================================================================
    # BOX 1 — Macro Library (Master-Detail Workspace Selector)
    # =====================================================================
    lib_flt = str(STATE.get("keyboard", "macro_lib_filter", "all") or "all")
    if lib_flt not in ("all", "bound"):
        lib_flt = "all"
    lib_q = str(STATE.get("keyboard", "macro_lib_query", "") or "").strip()
    ql = lib_q.lower()

    filtered = []
    for real_idx, m in enumerate(macros):
        if lib_flt == "bound" and not m["combo"] and not pad_usage.get(m["id"]):
            continue
        if ql and ql not in m["name"].lower() and ql not in m["combo"].lower():
            continue
        filtered.append((real_idx, m))

    flt_pills = " ".join([
        SelectPill(t("cc_keyboard.lib_flt_all"), "cc_kbd:lib_flt:all", selected=(lib_flt == "all"), style="secondary"),
        SelectPill(t("cc_keyboard.lib_flt_bound"), "cc_kbd:lib_flt:bound", selected=(lib_flt == "bound"), style="secondary"),
    ])
    lib_box = Box(
        t("cc_keyboard.macro_lib_title", p0=len(filtered)),
        icon="\uf0c9",
        actions=[
            ActionBtn(t("cc_keyboard.new_btn"), "cc_kbd:macro_new", tone="apply"),
            ActionBtn(t("cc_keyboard.duplicate"), "cc_kbd:macro_dup", tone="neutral"),
            ActionBtn(t("cc_keyboard.export_macros"), "cc_kbd:macro_export", tone="neutral"),
        ],
        highlighted=False,
    )
    lib_box.add_kv(
        t("cc_keyboard.lib_search_lbl"),
        InlineFilter(
            "cc_kbd_lib_flt",
            lib_q,
            t("cc_keyboard.search_macros_ph"),
            "cc_kbd:lib_search_open",
            clear_uri="cc_kbd:lib_search_clear" if lib_q else "",
            width=26,
            active=bool(lib_q),
        ),
        right_markup=flt_pills,
    )
    lib_box.add_row(" ")

    if not macros:
        lib_box.add_row(f"  {Txt(t('cc_keyboard.no_macros_saved_yet'), role='inactive')}")
    elif not filtered:
        lib_box.add_row(f"  {Txt(t('cc_keyboard.no_matching_macros'), role='inactive')}")
    else:
        l_rows = []
        for real_idx, m in filtered[:10]:
            s_cnt = len(m["steps"])
            _cm, tot_m = compile_macro_timeline(m["steps"], m["timing_mode"], m["fixed_delay_ms"], m["speed_mult"])
            info_s = t("cc_keyboard.steps_unit", p0=s_cnt, p1=tot_m) if s_cnt else canvas.truncate_plain(m["cmd"] or t("cc_keyboard.cmd_lbl"), 16)
            rep_s = "∞" if m["repeat_mode"] == "toggle_loop" else (f"{m['repeat_count']}x" if m["repeat_mode"] == "count" else "1x")
            mode_lbl = {"recorded": "rec", "fixed": "fix", "zero": "0ms"}.get(m["timing_mode"], m["timing_mode"][:4])
            pad_refs = pad_usage.get(m["id"], [])
            if m["combo"]:
                is_live_b = m["combo"] in bound_combos
                lbl_t = f"{m['combo']} \u00b7 Pad" if pad_refs else m["combo"]
                trig_s = StateBadge(True, canvas.truncate_plain(lbl_t, 15), t("cc_keyboard.unbound"), on_tone="ok" if is_live_b else "warn")
            elif pad_refs:
                first_pname, first_cid, _ = pad_refs[0]
                pad_lbl = f"Pad: {first_cid.upper()}" if len(pad_refs) == 1 else f"Pad: {len(pad_refs)}x"
                trig_s = StateBadge(True, canvas.truncate_plain(pad_lbl, 15), t("cc_keyboard.unbound"), on_tone="ok")
            else:
                trig_s = StateBadge(False, "", t("cc_keyboard.unbound"))
            l_rows.append([
                InspectorCell(canvas.truncate_plain(m["name"], 15), f"cc_kbd:macro_load:{real_idx}", selected=(m["id"] == draft["id"])),
                Txt(canvas.truncate_plain(info_s, 17), role="schema"),
                Txt(f"{mode_lbl} {m['speed_mult']}x {rep_s}", role="val"),
                trig_s,
                f"{ActionBtn('\u25b6', f'cc_kbd:run_macro:{real_idx}', tone='apply')} {ActionBtn(t('cc_keyboard.bind'), f'cc_kbd:bind_macro:{real_idx}', tone='neutral')} {ActionBtn(t('cc_keyboard.clear_x'), f'cc_kbd:del_macro:{real_idx}', tone='danger_soft')}",
            ])
        lib_box.add_table(
            headers=[
                t("cc_keyboard.macro_name_2"),
                t("cc_keyboard.steps_cmd"),
                t("cc_keyboard.mode_rep"),
                t("cc_keyboard.trigger"),
                t("cc_keyboard.actions"),
            ],
            widths=[17, 17, 11, 13, "flex"],
            rows=l_rows,
            aligns=["left", "left", "left", "left", "right"],
        )
    cards.append(lib_box)

    # =====================================================================
    # BOX 2 — Macro Studio & Interactive Pure-Cairo Visual Timeline
    # =====================================================================
    eng_badge = StateBadge(is_running, t("cc_keyboard.engine_active"), t("cc_keyboard.engine_standby"))
    studio_hdr_actions = [
        StateBadge(uinput_ok, t("cc_keyboard.uinput_ready"), t("cc_keyboard.uinput_locked"), on_tone="ok" if uinput_ok else "warn"),
    ]
    if is_dirty:
        studio_hdr_actions.append(Badge(t("cc_keyboard.status_unsaved"), style="warn"))
    studio_hdr_actions.append(eng_badge)
    studio_box = Box(
        t("cc_keyboard.studio_box_title", p0=canvas.truncate_plain(draft["name"], 24)),
        icon="\uf0e7",
        actions=studio_hdr_actions,
        highlighted=False,
    )
    if not uinput_ok:
        studio_box.add_kv(
            t("cc_keyboard.uinput_driver"),
            Badge(t("cc_keyboard.kernel_uinput_permission_required"), style="warn"),
            right_markup=ActionBtn(t("cc_keyboard.unlock_dev_uinput"), "cc_kbd:unlock_uinput", tone="apply"),
        )
    if is_rec:
        studio_box.add_kv(
            t("cc_keyboard.rec_status"),
            Badge(t("cc_keyboard.rec_live_banner", p0=panic_key), style="crit"),
        )

    studio_box.add_kv(
        t("cc_keyboard.macro_name"),
        Txt(draft["name"], role="focus"),
        right_markup=InlineField(
            "cc_mac_name",
            "cc_kbd:macro_inline_name",
            value="",
            empty_label=t("cc_keyboard.rename"),
            placeholder=t("cc_keyboard.macro_name_3"),
            width=18,
            icon="\u270e",
            tone="neutral",
        ),
    )

    is_bound_niri = bool(draft["combo"] and draft["combo"] in bound_combos)
    if is_trig_cap:
        trig_val = Badge(cap_prev or t("cc_keyboard.press_trigger_hint"), style="warn")
    elif draft["combo"]:
        trig_val = (
            f"{Txt(draft['combo'], role='focus')} "
            f"{StateBadge(is_bound_niri, t('cc_keyboard.trigger_bound_ok'), t('cc_keyboard.trigger_draft_only'), on_tone='ok' if is_bound_niri else 'warn')}"
        )
    else:
        trig_val = Txt(t("cc_keyboard.unbound"), role="inactive")

    cap_trig_btn = (
        ActionBtn(t("cc_keyboard.cancel"), "cc_kbd:cap_stop", tone="warn", modal_active=True)
        if is_trig_cap
        else ActionBtn(t("cc_keyboard.capture_trigger"), "cc_kbd:cap_start:macro_trigger", tone="neutral")
    )
    trig_acts = (
        f"{cap_trig_btn} "
        f"{InlineField('cc_mac_combo', 'cc_kbd:macro_inline_combo', value='', empty_label=t('cc_keyboard.manual'), placeholder='Mod+Shift+G', width=16, icon='\u270e', tone='neutral')}"
    )
    if draft["combo"]:
        if is_bound_niri:
            trig_acts = f"{trig_acts} {ActionBtn(t('cc_keyboard.unbind'), 'cc_kbd:macro_unbind_draft', tone='warn')}"
        else:
            trig_acts = f"{trig_acts} {ActionBtn(t('cc_keyboard.bind_niri'), 'cc_kbd:macro_bind_draft', tone='apply')}"
    studio_box.add_kv(t("cc_keyboard.trigger_shortcut"), trig_val, right_markup=trig_acts)
    draft_pad_refs = pad_usage.get(draft["id"], [])
    if draft_pad_refs:
        ref_summary = " \u00b7 ".join(
            f"{pname} ({cid.upper()})"
            for pname, cid, _ in draft_pad_refs[:3]
        )
        if len(draft_pad_refs) > 3:
            ref_summary += f" (+{len(draft_pad_refs) - 3})"
        studio_box.add_kv(
            t("cc_keyboard.gamepad_trigger_lbl"),
            Txt(canvas.truncate_plain(ref_summary, 46), role="val"),
            right_markup=ActionBtn(t("cc_keyboard.open_controllers_btn"), "cc_ctrl:open_from_macro", tone="neutral"),
        )

    # Capture & Transport deck
    rec_btn = (
        ActionBtn(t("cc_keyboard.rec_stop_key", p0=panic_key), "cc_kbd:macro_rec_stop", tone="danger_soft", modal_active=True)
        if is_rec
        else ActionBtn(t("cc_keyboard.rec_btn"), "cc_kbd:macro_rec_start", tone="apply")
    )
    deck_actions = [rec_btn]
    if steps and not is_rec:
        deck_actions.append(ActionBtn(t("cc_keyboard.rec_append_btn"), "cc_kbd:macro_rec_append", tone="neutral"))
    deck_actions.append(ActionBtn(t("cc_keyboard.sandbox_btn"), "cc_kbd:macro_test_sandbox", tone="neutral"))
    deck_actions.append(ActionBtn(t("cc_keyboard.run_live_btn"), "cc_kbd:macro_run_engine", tone="apply"))
    if is_running:
        deck_actions.append(ActionBtn(t("cc_keyboard.panic_stop_btn"), "cc_kbd:macro_panic_stop", tone="danger_soft"))
    studio_box.add_section(t("cc_keyboard.sec_capture_deck"), actions=deck_actions)

    sb_disp = (
        Txt(canvas.truncate_plain(sandbox_out, 50), role="val")
        if sandbox_out
        else Txt(t("cc_keyboard.sandbox_empty_hint"), role="inactive")
    )
    sb_acts = InlineField(
        "cc_mac_sandbox",
        "cc_kbd:sandbox_inline",
        value="",
        empty_label=t("cc_keyboard.sandbox_open_pad"),
        placeholder=t("cc_keyboard.sandbox_pad_prompt"),
        width=22,
        icon="\u270e",
        tone="neutral",
    )
    if sandbox_out:
        sb_acts = f"{sb_acts} {ActionBtn(t('cc_keyboard.clear_x'), 'cc_kbd:sandbox_clear', tone='danger_soft')}"
    studio_box.add_kv(t("cc_keyboard.sandbox_pad_lbl"), sb_disp, right_markup=sb_acts)

    # Visual Timeline header & embedded pure-Cairo interactive canvas
    tl_actions = [
        InlineField("cc_mac_step_add", "cc_kbd:step_inline_add", value="", empty_label=t("cc_keyboard.key_2"), placeholder="W, 50, 80", width=13, icon="", tone="neutral"),
        InlineField("cc_mac_text_add", "cc_kbd:macro_inline_text", value="", empty_label=t("cc_keyboard.add_text_btn"), placeholder=t("cc_keyboard.add_text_ph"), width=14, icon="", tone="neutral"),
        ActionBtn(t("cc_keyboard.norm_35ms"), "cc_kbd:macro_norm", tone="neutral"),
        ActionBtn(t("cc_keyboard.clear"), "cc_kbd:macro_clear_steps", tone="danger_soft"),
    ]
    studio_box.add_section(t("cc_keyboard.sec_visual_timeline", p0=len(steps), p1=tot_ms), actions=tl_actions)
    studio_box.add_row(" ")
    studio_box.open_bottom = True

    tl_widget = MacroTimelineWidget(
        raw_steps=steps,
        timing_mode=draft["timing_mode"],
        fixed_ms=draft["fixed_delay_ms"],
        speed_mult=draft["speed_mult"],
        selected_idx=sel_idx,
        is_rec=is_rec,
        panic_key=panic_key,
        on_select=(lambda idx: cc_panel.on_link(f"cc_kbd:step_select:{idx}") if cc_panel else None),
        on_timing_commit=(lambda idx, d_ms, h_ms: cc_panel.on_link(f"cc_kbd:step_set_timing:{idx}:{d_ms}:{h_ms}") if cc_panel else None),
        widget_width=int(round(canvas.total_px)),
        fixed_height=max(146, int(round(canvas.char_h * 7.8))),
        inset_x=max(14, int(round(canvas.char_w * 2.0))),
        font_px=max(10.0, float(canvas.char_w) * 1.40),
    )
    studio_foot = Box("", highlighted=False)
    studio_foot.open_top = True
    studio_foot.add_row(" ")
    cards.append(AsciiCard(studio_box, canvas, embedded_widget=tl_widget, footer_box_model=studio_foot))

    # =====================================================================
    # BOX 3 — Selected Step Inspector, Step Sequence Table & Playback Engine
    # =====================================================================
    ed_box = Box(
        t("cc_keyboard.box_step_playback_title"),
        icon="\uf0ae",
        highlighted=False,
    )

    if steps:
        sel_s = steps[sel_idx]
        mv_l = ActionBtn("\u2190", f"cc_kbd:step_move:{sel_idx}:-1", tone="neutral") if sel_idx > 0 else "   "
        mv_r = ActionBtn("\u2192", f"cc_kbd:step_move:{sel_idx}:1", tone="neutral") if sel_idx < len(steps) - 1 else "   "
        sel_evdev = int(sel_s.get("keycode", 0) or 0)
        sel_meta = (
            f"{Txt(f'#{sel_idx + 1:02d}', role='schema')}  "
            f"{Txt(str(sel_s.get('key', '?')), role='focus')}  "
            f"{Txt(f'\u00b7  evdev {sel_evdev}', role='schema')}"
        )
        sel_acts = (
            f"{mv_l} {mv_r} "
            f"{ActionBtn(t('cc_keyboard.dup_step_btn'), f'cc_kbd:step_dup:{sel_idx}', tone='neutral')} "
            f"{ActionBtn(t('cc_keyboard.clear_x'), f'cc_kbd:step_del:{sel_idx}', tone='danger_soft')}"
        )
        ed_box.add_kv(t("cc_keyboard.sel_step_lbl"), sel_meta, right_markup=sel_acts)

        w_ms = int(sel_s.get("delay_ms", 0) or 0)
        h_ms = int(sel_s.get("hold_ms", 50) or 50)
        timing_ctrl = (
            f"{Txt(t('cc_keyboard.wait_lbl'), role='schema')} "
            f"{ActionBtn('-', f'cc_kbd:step_nudge:{sel_idx}:delay_ms:-10', tone='neutral')} "
            f"{Txt(f'+{w_ms}ms', role='val')} "
            f"{ActionBtn('+', f'cc_kbd:step_nudge:{sel_idx}:delay_ms:10', tone='neutral')}   "
            f"{Txt(t('cc_keyboard.hold_lbl'), role='schema')} "
            f"{ActionBtn('-', f'cc_kbd:step_nudge:{sel_idx}:hold_ms:-10', tone='neutral')} "
            f"{Txt(f'{h_ms}ms', role='val')} "
            f"{ActionBtn('+', f'cc_kbd:step_nudge:{sel_idx}:hold_ms:10', tone='neutral')}"
        )
        exact_btn = InlineField(
            f"cc_mac_step_{sel_idx}",
            f"cc_kbd:step_inline_edit:{sel_idx}",
            value="",
            empty_label=t("cc_keyboard.exact_ms_btn"),
            placeholder=f"{w_ms}, {h_ms}",
            width=14,
            icon="\u270e",
            tone="neutral",
        )
        ed_box.add_kv(t("cc_keyboard.step_timing_lbl"), timing_ctrl, right_markup=exact_btn)

        preview_keys = " \u2192 ".join(str(s.get("key", "?"))[:8] for s in steps[:6])
        if len(steps) > 6:
            preview_keys += f" (+{len(steps) - 6})"

        def _build_step_table(b: Box) -> None:
            tl_rows = []
            for idx, s in enumerate(steps[:24]):
                up_b = ActionBtn("\u2191", f"cc_kbd:step_move:{idx}:-1", tone="neutral") if idx > 0 else "   "
                dn_b = ActionBtn("\u2193", f"cc_kbd:step_move:{idx}:1", tone="neutral") if idx < len(steps) - 1 else "   "
                tl_rows.append([
                    InspectorCell(
                        f"{idx + 1:02d} {str(s.get('key', '?'))[:10]}",
                        f"cc_kbd:step_select:{idx}",
                        selected=(idx == sel_idx),
                    ),
                    Txt(str(s.get("keycode", 0)), role="schema"),
                    Txt(f"+{int(s.get('delay_ms', 0))} ms", role="val"),
                    Txt(f"{int(s.get('hold_ms', 50))} ms", role="val"),
                    f"{InlineField(f'cc_mac_step_{idx}', f'cc_kbd:step_inline_edit:{idx}', value='', empty_label='\u270e', placeholder='50, 80', width=10, icon='', tone='neutral')} {up_b}{dn_b} {ActionBtn(t('cc_keyboard.clear_x'), f'cc_kbd:step_del:{idx}', tone='danger_soft')}",
                ])
            b.add_table(
                headers=[
                    t("cc_keyboard.col_key_action"),
                    t("cc_keyboard.col_evdev"),
                    t("cc_keyboard.wait_delay"),
                    t("cc_keyboard.hold_ms"),
                    t("cc_keyboard.edit_order"),
                ],
                widths=[18, 8, 12, 11, "flex"],
                rows=tl_rows,
                aligns=["left", "left", "left", "left", "right"],
            )

        ed_box.add_collapsible(
            cc_panel,
            "macro_step_table",
            t("cc_keyboard.step_table_title", p0=len(steps)),
            _build_step_table,
            summary=preview_keys,
        )
        ed_box.add_section(t("cc_keyboard.sec_playback_safety"))

    t_mode = draft["timing_mode"]
    d_tmode = saved is not None and t_mode != saved["timing_mode"]
    ed_box.add_kv(
        t("cc_keyboard.timing_mode"),
        " ".join(
            SelectPill(t(f"cc_keyboard.tmode_{k}"), f"cc_kbd:macro_tmode:{k}", selected=(t_mode == k), dirty=d_tmode)
            for k in ("recorded", "fixed", "zero")
        ),
    )
    if t_mode == "fixed":
        d_fixed = saved is not None and draft["fixed_delay_ms"] != saved["fixed_delay_ms"]
        ed_box.add_kv(
            t("cc_keyboard.fixed_step_delay"),
            " ".join(
                SelectPill(f"{v}ms", f"cc_kbd:macro_fixedms:{v}", selected=(draft["fixed_delay_ms"] == v), dirty=d_fixed)
                for v in (15, 25, 50, 100, 200)
            ),
        )

    d_speed = saved is not None and abs(draft["speed_mult"] - saved["speed_mult"]) > 0.01
    ed_box.add_kv(
        t("cc_keyboard.playback_speed"),
        " ".join(
            SelectPill(f"{s}x", f"cc_kbd:macro_speed:{s}", selected=(abs(draft["speed_mult"] - s) < 0.05), dirty=d_speed)
            for s in (0.5, 1.0, 2.0, 5.0)
        ),
    )

    rmode, rcnt = draft["repeat_mode"], draft["repeat_count"]
    d_rep = saved is not None and (rmode != saved["repeat_mode"] or rcnt != saved["repeat_count"])
    rep_pills = " ".join([
        SelectPill(t("cc_keyboard.once_1x"), "cc_kbd:macro_rep:once:1", selected=(rmode == "once"), dirty=d_rep),
        SelectPill("3x", "cc_kbd:macro_rep:count:3", selected=(rmode == "count" and rcnt == 3), dirty=d_rep),
        SelectPill("10x", "cc_kbd:macro_rep:count:10", selected=(rmode == "count" and rcnt == 10), dirty=d_rep),
        InlineField(
            "cc_mac_rep",
            "cc_kbd:macro_inline_rep",
            value=f"{rcnt}x" if (rmode == "count" and rcnt not in (3, 10)) else "",
            empty_label="N..",
            placeholder="5",
            width=7,
            icon="",
            tone="neutral",
            selected=(rmode == "count" and rcnt not in (3, 10)),
            dirty=d_rep,
        ),
        SelectPill(t("cc_keyboard.loop_toggle"), "cc_kbd:macro_rep:toggle_loop:1", selected=(rmode == "toggle_loop"), dirty=d_rep),
    ])
    ed_box.add_kv(t("cc_keyboard.repeat_mode"), rep_pills)

    d_panic = saved is not None and draft["panic_key"] != saved["panic_key"]
    ed_box.add_kv(
        t("cc_keyboard.panic_key_lbl"),
        " ".join(
            SelectPill(t("cc_keyboard.panic_key_prefix", p0=pk), f"cc_kbd:macro_panic_key:{pk}", selected=(panic_key == pk), dirty=d_panic)
            for pk in ("F12", "Pause")
        ),
    )
    d_cd = saved is not None and draft["countdown_s"] != saved["countdown_s"]
    ed_box.add_kv(
        t("cc_keyboard.countdown_lbl"),
        " ".join(
            SelectPill(
                t("cc_keyboard.countdown_instant") if cd == 0 else f"{cd}s",
                f"cc_kbd:macro_countdown:{cd}",
                selected=(draft["countdown_s"] == cd),
                dirty=d_cd,
                style="secondary",
            )
            for cd in (0, 2, 3, 5)
        ),
    )

    ed_box.add_section(t("cc_keyboard.sec_commit_bar"))
    sync_msg = Txt(t("cc_keyboard.commit_unsaved_msg"), role="warn") if is_dirty else Txt(t("cc_keyboard.commit_synced_msg"), role="ok")
    commit_btns = (
        f"{ActionBtn(t('cc_keyboard.reset_btn'), 'cc_kbd:macro_reset_draft', tone='warn' if is_dirty else 'inactive')} "
        f"{ActionBtn(t('cc_keyboard.save_macro_btn'), 'cc_kbd:macro_save_draft', tone='apply' if is_dirty else 'inactive')}"
    )
    ed_box.add_row(f"  {sync_msg}", right_markup=commit_btns)
    cards.append(ed_box)
    return cards


def build_cards(cc_panel, canvas: Canvas) -> list:
    st = _collect_keyboard_state()
    return _build_macros_standard(cc_panel, canvas, st)
