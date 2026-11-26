#!/usr/bin/env python3
"""Interactive Niri Monitor Arrangement Blueprint Widget (`ui/output_map.py`)."""

from core import probe
from core.i18n import t
from ui.kit import hex_to_rgba_f as _hex_rgba, rounded_rect_path as _rounded_rect_path, stroke_styled_frame as _stroke_styled_frame


def _output_hz_and_scale(info: dict) -> tuple[float, float]:
    log_d = info.get("logical") or {}
    scale = float(log_d.get("scale", 1.0) or 1.0)
    hz = 0.0
    modes = info.get("modes") or []
    cur_idx = info.get("current_mode")
    if isinstance(cur_idx, int) and 0 <= cur_idx < len(modes):
        rr = float(modes[cur_idx].get("refresh_rate", 0) or 0)
        hz = (rr / 1000.0) if rr > 1000.0 else rr
    return hz, scale


class _NiriOutputMapMixin:
    SNAP_PX = 14

    def __init__(
        self,
        outputs_data: dict,
        refresh_cb,
        map_width: int = 380,
        padding: int = 14,
        fixed_height: int = 0,
        selected_output: str | None = None,
        on_select=None,
        inset_x: int = 12,
        font_px: float = 11.5,
        show_footer: bool = False,
    ):
        super().__init__()
        self.refresh_cb = refresh_cb
        self.outputs = outputs_data
        self.map_width = max(160, int(map_width))
        self.padding = max(8, int(padding))
        self.fixed_height = int(fixed_height or 0)
        self.selected_output = selected_output
        self.on_select = on_select
        self.inset_x = max(0, int(inset_x or 0))
        self.font_px = max(8.0, float(font_px or 11.5))
        self.show_footer = bool(show_footer)
        self.footer_h = max(22, int(round(self.font_px * 2.15))) if self.show_footer else 0
        self.offset_x = self.inset_x + self.padding
        self.offset_y = self.padding
        self.map_h = self.fixed_height if self.fixed_height > 0 else 184
        self.min_x = 0
        self.min_y = 0
        self.scale = 1.0
        self.drag_state = None
        self.items = {}
        self._hovered_out: str | None = None
        self._active_guides: list[tuple[str, float]] = []
        self._live_coords: dict[str, tuple[int, int]] = {}
        self._build_map()
        self.connect("size-allocate", self._on_size_allocate)

    def _canvas_h(self, total_h: int | None = None) -> int:
        h = self.map_h if total_h is None else int(total_h)
        return max(72, h - self.footer_h)

    def _compute_geometry(self, target_w: int) -> dict[str, tuple[int, int, int, int]]:
        valid_outs = {k: v for k, v in self.outputs.items() if v.get("logical")}
        if not valid_outs:
            return {}
        self.min_x = min(v["logical"]["x"] for v in valid_outs.values())
        self.min_y = min(v["logical"]["y"] for v in valid_outs.values())
        max_x = max(v["logical"]["x"] + v["logical"]["width"] for v in valid_outs.values())
        max_y = max(v["logical"]["y"] + v["logical"]["height"] for v in valid_outs.values())
        logical_w = max(1, max_x - self.min_x)
        logical_h = max(1, max_y - self.min_y)

        sum_w = sum(v["logical"]["width"] for v in valid_outs.values())
        sum_h = sum(v["logical"]["height"] for v in valid_outs.values())
        max_w = max(v["logical"]["width"] for v in valid_outs.values())
        max_h = max(v["logical"]["height"] for v in valid_outs.values())
        if len(valid_outs) >= 2:
            pad_w = min(sum_w - max_w, max_w)
            pad_h = min(sum_h - max_h, max_h)
            arena_w = max(sum_w * 1.15, max_w + 2.0 * pad_w, logical_w * 1.05)
            arena_h = max(sum_h * 1.15, max_h + 2.0 * pad_h, logical_h * 1.05)
        else:
            arena_w = max(max_w * 1.5, 2560.0)
            arena_h = max(max_h * 2.2, 1440.0)

        self.map_h = self.fixed_height if self.fixed_height > 0 else (272 if target_w >= 500 else 184)
        inner_w = max(100, target_w - (2 * self.inset_x))
        canvas_h = self._canvas_h(self.map_h)
        usable_w = max(60, inner_w - (self.padding * 2))
        usable_h = max(48, canvas_h - (self.padding * 2))
        self.scale = max(0.01, min(usable_w / float(arena_w), usable_h / float(arena_h)))

        raw = {}
        for out_name, info in valid_outs.items():
            log_d = info["logical"]
            w = max(44, int(round(log_d["width"] * self.scale)))
            h = max(28, int(round(log_d["height"] * self.scale)))
            rel_x = int(round((log_d["x"] - self.min_x) * self.scale))
            rel_y = int(round((log_d["y"] - self.min_y) * self.scale))
            raw[out_name] = (rel_x, rel_y, w, h)

        min_rx = min(rx for rx, _ry, _w, _h in raw.values())
        min_ry = min(ry for _rx, ry, _w, _h in raw.values())
        max_rx = max(rx + w for rx, _ry, w, _h in raw.values())
        max_ry = max(ry + h for _rx, ry, _w, h in raw.values())
        used_w = max(1, max_rx - min_rx)
        used_h = max(1, max_ry - min_ry)

        self.offset_x = self.inset_x + max(self.padding, int(round((inner_w - used_w) / 2.0))) - min_rx
        self.offset_y = max(self.padding, int(round((canvas_h - used_h) / 2.0))) - min_ry
        return {
            out_name: (self.offset_x + rx, self.offset_y + ry, w, h)
            for out_name, (rx, ry, w, h) in raw.items()
        }

    def _build_map(self):
        from gi.repository import Gdk

        geom = self._compute_geometry(self.map_width)
        if not geom:
            return
        self.set_size_request(self.map_width, self.map_h)
        self.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
            | Gdk.EventMask.LEAVE_NOTIFY_MASK
        )
        for out_name, (x, y, w, h) in geom.items():
            info = self.outputs[out_name]
            log_d = info["logical"]
            hz, sc_v = _output_hz_and_scale(info)
            self.items[out_name] = {
                "x": x,
                "y": y,
                "w": w,
                "h": h,
                "log_w": log_d["width"],
                "log_h": log_d["height"],
                "log_x": log_d["x"],
                "log_y": log_d["y"],
                "hz": hz,
                "scale": sc_v,
                "model": str(info.get("model", "") or ""),
            }
        self.connect("draw", self._on_draw)
        self.connect("button-press-event", self._on_press)
        self.connect("motion-notify-event", self._on_motion)
        self.connect("button-release-event", self._on_release)
        self.connect("leave-notify-event", self._on_leave)

    def _on_size_allocate(self, _widget, alloc):
        if self.drag_state is not None or not self.items:
            return
        if alloc.width <= (2 * self.inset_x + 2 * self.padding) or abs(alloc.width - self.map_width) <= 1:
            return
        self.map_width = alloc.width
        geom = self._compute_geometry(self.map_width)
        for out_name, (x, y, w, h) in geom.items():
            it = self.items.get(out_name)
            if not it:
                continue
            it["x"], it["y"], it["w"], it["h"] = x, y, w, h
        self.queue_draw()

    def _hit_test(self, px: float, py: float) -> str | None:
        order = [k for k in self.items if k != self.selected_output]
        if self.selected_output in self.items:
            order.append(self.selected_output)
        for name in reversed(order):
            it = self.items[name]
            if it["x"] <= px <= it["x"] + it["w"] and it["y"] <= py <= it["y"] + it["h"]:
                return name
        return None

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

    def _compute_snapped(self, moving_name: str, raw_x: float, raw_y: float):
        me = self.items[moving_name]
        w, h = me["w"], me["h"]
        snap_x, snap_y = raw_x, raw_y
        best_dx, best_dy = self.SNAP_PX + 1, self.SNAP_PX + 1
        snapped_to = {"x": None, "y": None}
        guide_x: float | None = None
        guide_y: float | None = None
        for other_name, ot in self.items.items():
            if other_name == moving_name:
                continue
            for tx, gx, tag in [
                (ot["x"] - w, ot["x"], ("left_of", other_name)),
                (ot["x"] + ot["w"], ot["x"] + ot["w"], ("right_of", other_name)),
                (ot["x"], ot["x"], ("align_left", other_name)),
                (ot["x"] + ot["w"] - w, ot["x"] + ot["w"], ("align_right", other_name)),
                (ot["x"] + (ot["w"] - w) // 2, ot["x"] + ot["w"] / 2.0, ("align_center_x", other_name)),
            ]:
                d = abs(raw_x - tx)
                if d < best_dx:
                    best_dx, snap_x, snapped_to["x"], guide_x = d, tx, tag, float(gx)
            for ty, gy, tag in [
                (ot["y"] - h, ot["y"], ("above", other_name)),
                (ot["y"] + ot["h"], ot["y"] + ot["h"], ("below", other_name)),
                (ot["y"], ot["y"], ("align_top", other_name)),
                (ot["y"] + ot["h"] - h, ot["y"] + ot["h"], ("align_bottom", other_name)),
                (ot["y"] + (ot["h"] - h) // 2, ot["y"] + ot["h"] / 2.0, ("align_center_y", other_name)),
            ]:
                d = abs(raw_y - ty)
                if d < best_dy:
                    best_dy, snap_y, snapped_to["y"], guide_y = d, ty, tag, float(gy)

        alloc = self.get_allocation()
        max_w = max(self.map_width, alloc.width)
        canvas_h = self._canvas_h(max(self.map_h, alloc.height))
        min_cx = self.inset_x + 4
        max_cx = max(min_cx, max_w - self.inset_x - w - 4)
        min_cy = 4
        max_cy = max(min_cy, canvas_h - h - 4)
        snap_x = max(min_cx, min(snap_x, max_cx))
        snap_y = max(min_cy, min(snap_y, max_cy))
        guides = []
        if guide_x is not None and min_cx <= guide_x <= (max_w - self.inset_x - 2):
            guides.append(("v", guide_x))
        if guide_y is not None and 2 <= guide_y <= (canvas_h - 2):
            guides.append(("h", guide_y))
        return int(round(snap_x)), int(round(snap_y)), snapped_to, guides

    def _snapped_logical(self, out_name: str, sx: int, sy: int, snapped_to: dict) -> tuple[int, int]:
        me = self.items[out_name]
        new_log_x = int(round((sx - self.offset_x) / self.scale)) + self.min_x
        new_log_y = int(round((sy - self.offset_y) / self.scale)) + self.min_y
        if snapped_to["x"]:
            kind, ref_name = snapped_to["x"]
            ref = self.items[ref_name]
            if kind == "left_of":
                new_log_x = ref["log_x"] - me["log_w"]
            elif kind == "right_of":
                new_log_x = ref["log_x"] + ref["log_w"]
            elif kind == "align_left":
                new_log_x = ref["log_x"]
            elif kind == "align_right":
                new_log_x = ref["log_x"] + ref["log_w"] - me["log_w"]
            elif kind == "align_center_x":
                new_log_x = ref["log_x"] + (ref["log_w"] - me["log_w"]) // 2
        if snapped_to["y"]:
            kind, ref_name = snapped_to["y"]
            ref = self.items[ref_name]
            if kind == "above":
                new_log_y = ref["log_y"] - me["log_h"]
            elif kind == "below":
                new_log_y = ref["log_y"] + ref["log_h"]
            elif kind == "align_top":
                new_log_y = ref["log_y"]
            elif kind == "align_bottom":
                new_log_y = ref["log_y"] + ref["log_h"] - me["log_h"]
            elif kind == "align_center_y":
                new_log_y = ref["log_y"] + (ref["log_h"] - me["log_h"]) // 2
        return new_log_x, new_log_y

    def _normalized_coords(self, moved_name: str, new_log_x: int, new_log_y: int) -> dict[str, tuple[int, int]]:
        coords = {k: (v["log_x"], v["log_y"]) for k, v in self.items.items()}
        coords[moved_name] = (new_log_x, new_log_y)
        base_x = min(c[0] for c in coords.values())
        base_y = min(c[1] for c in coords.values())
        return {k: (int(fx - base_x), int(fy - base_y)) for k, (fx, fy) in coords.items()}

    def _on_draw(self, _widget, cr):
        from core.config import THEME
        from gi.repository import Pango, PangoCairo
        from ui.kit import get_semantic_palette

        alloc = self.get_allocation()
        w = max(self.map_width, int(alloc.width))
        h = max(self.map_h, int(alloc.height))
        bx = float(self.inset_x)
        bw = max(60.0, float(w - 2 * self.inset_x))
        bh = float(h)
        canvas_h = float(self._canvas_h(h))
        pal = get_semantic_palette()

        _rounded_rect_path(cr, bx + 0.5, 0.5, bw - 1.0, bh - 1.0, 6.0)
        cr.set_source_rgba(*_hex_rgba(pal["structure_bg"], 0.65))
        cr.fill()
        cr.set_source_rgba(*_hex_rgba(pal["structure"], 0.85))
        cr.set_line_width(1.0)
        _stroke_styled_frame(cr, bx + 0.5, 0.5, bw - 1.0, bh - 1.0, 6.0)

        cr.save()
        _rounded_rect_path(cr, bx + 1.0, 1.0, bw - 2.0, bh - 2.0, 5.5)
        cr.clip()

        if self.drag_state is not None and self._active_guides:
            cr.save()
            cr.set_source_rgba(*_hex_rgba(pal["intent"], 0.78))
            cr.set_line_width(1.0)
            cr.set_dash([4.0, 4.0])
            for axis, pos in self._active_guides:
                if axis == "v":
                    cr.move_to(pos + 0.5, 2.0)
                    cr.line_to(pos + 0.5, canvas_h - 2.0)
                else:
                    cr.move_to(bx + 2.0, pos + 0.5)
                    cr.line_to(bx + bw - 2.0, pos + 0.5)
            cr.stroke()
            cr.restore()

        drag_name = self.drag_state["out_name"] if self.drag_state else None
        draw_order = sorted(
            self.items.keys(),
            key=lambda n: (n == drag_name, n == self.selected_output, n == self._hovered_out),
        )
        font_family = str((THEME.get("typography") or {}).get("font_family", "monospace") or "monospace")
        base_pt = float((THEME.get("typography") or {}).get("font_size", 11) or 11)
        title_sz = int(round(base_pt * 0.98 * Pango.SCALE))
        sub_sz = int(round(base_pt * 0.86 * Pango.SCALE))
        coord_sz = int(round(base_pt * 0.82 * Pango.SCALE))

        for out_name in draw_order:
            it = self.items[out_name]
            ix, iy, iw, ih = float(it["x"]), float(it["y"]), float(it["w"]), float(it["h"])
            is_drag = out_name == drag_name
            is_sel = self.selected_output is None or out_name == self.selected_output
            is_hov = out_name == self._hovered_out

            cr.save()
            _rounded_rect_path(cr, ix + 0.5, iy + 0.5, iw - 1.0, ih - 1.0, 5.0)
            if is_sel or is_drag:
                cr.set_source_rgba(*_hex_rgba(pal["intent_bg"], 0.90 if is_hov or is_drag else 0.78))
            elif is_hov:
                cr.set_source_rgba(*_hex_rgba(pal["structure_bg"], 0.95))
            else:
                cr.set_source_rgba(16 / 255.0, 19 / 255.0, 27 / 255.0, 0.88)
            cr.fill_preserve()

            if is_sel or is_drag:
                cr.set_source_rgba(*_hex_rgba(pal["intent"], 1.0 if is_drag or is_hov else 0.92))
                cr.set_line_width(1.75 if is_drag else 1.5)
            elif is_hov:
                cr.set_source_rgba(*_hex_rgba(pal["focus"], 0.90))
                cr.set_line_width(1.2)
            else:
                cr.set_source_rgba(*_hex_rgba(pal["structure"], 0.95))
                cr.set_line_width(1.0)
            cr.new_path()
            _stroke_styled_frame(cr, ix + 0.5, iy + 0.5, iw - 1.0, ih - 1.0, 5.0)

            _rounded_rect_path(cr, ix + 1.0, iy + 1.0, iw - 2.0, ih - 2.0, 4.5)
            cr.clip()
            if is_sel or is_drag:
                cr.set_source_rgba(*_hex_rgba(pal["intent"], 0.95))
                cr.rectangle(ix + 1.0, iy + 1.0, iw - 2.0, 2.0)
                cr.fill()

            pad_x = max(6.0, round(self.font_px * 0.65))
            dot_r = max(2.4, min(3.5, self.font_px * 0.25))
            show_dot = iw >= 64
            if show_dot:
                cr.set_source_rgba(*_hex_rgba(pal["vitality"], 0.95))
                cr.arc(ix + iw - pad_x - dot_r, iy + max(9.0, pad_x + 3.0), dot_r, 0.0, 6.28318530718)
                cr.fill()

            lx, ly = self._live_coords.get(out_name, (it["log_x"], it["log_y"]))
            sc_s = f" · {it['scale']:g}x" if abs(it["scale"] - 1.0) >= 0.01 else ""
            hz_s = f" @ {int(round(it['hz']))}Hz" if it["hz"] > 1.0 and iw >= 132 else ""
            line1_txt = f"▸ {out_name}" if (is_sel and self.selected_output is not None) else out_name
            line2_txt = f"{it['log_w']}x{it['log_h']}{hz_s}{sc_s}"
            show_coords = ih >= max(56.0, self.font_px * 4.9) and iw >= 100.0
            line3_txt = f"({lx}, {ly})"

            title_col = pal["intent"] if (is_sel and self.selected_output is not None) else (pal["focus"] if (is_sel or is_hov) else pal["focus_val"])
            sub_col = pal["focus_val"] if is_sel else pal["schema"]
            coord_col = pal["intent"] if is_drag else pal["schema"]

            layout = PangoCairo.create_layout(cr)
            layout.set_alignment(Pango.Alignment.LEFT)
            layout.set_ellipsize(Pango.EllipsizeMode.END)
            max_tw = int(max(24.0, iw - (pad_x * 2.0) - (dot_r * 3.0 if show_dot else 0.0)) * Pango.SCALE)
            layout.set_width(max_tw)

            desc1 = Pango.FontDescription()
            desc1.set_family(font_family)
            desc1.set_weight(Pango.Weight.BOLD)
            desc1.set_size(title_sz)
            layout.set_font_description(desc1)
            layout.set_text(line1_txt, -1)
            _, r1 = layout.get_pixel_extents()

            layout2 = PangoCairo.create_layout(cr)
            layout2.set_alignment(Pango.Alignment.LEFT)
            layout2.set_ellipsize(Pango.EllipsizeMode.END)
            layout2.set_width(int(max(24.0, iw - pad_x * 2.0) * Pango.SCALE))
            desc2 = Pango.FontDescription()
            desc2.set_family(font_family)
            desc2.set_size(sub_sz)
            layout2.set_font_description(desc2)
            layout2.set_text(line2_txt, -1)
            _, r2 = layout2.get_pixel_extents()

            total_th = r1.height + 2.0 + r2.height
            if show_coords:
                layout3 = PangoCairo.create_layout(cr)
                layout3.set_alignment(Pango.Alignment.LEFT)
                layout3.set_width(int(max(24.0, iw - pad_x * 2.0) * Pango.SCALE))
                desc3 = Pango.FontDescription()
                desc3.set_family(font_family)
                desc3.set_size(coord_sz)
                layout3.set_font_description(desc3)
                layout3.set_text(line3_txt, -1)
                _, r3 = layout3.get_pixel_extents()
                total_th += 2.0 + r3.height
            else:
                layout3 = None

            ty = iy + max(4.0, (ih - total_th) / 2.0)
            tx = ix + pad_x

            cr.set_source_rgba(*_hex_rgba(title_col, 1.0))
            cr.move_to(tx, ty)
            PangoCairo.show_layout(cr, layout)

            cr.set_source_rgba(*_hex_rgba(sub_col, 0.92))
            cr.move_to(tx, ty + r1.height + 2.0)
            PangoCairo.show_layout(cr, layout2)

            if layout3 is not None:
                cr.set_source_rgba(*_hex_rgba(coord_col, 0.92 if is_drag else 0.72))
                cr.move_to(tx, ty + r1.height + r2.height + 4.0)
                PangoCairo.show_layout(cr, layout3)

            cr.restore()

        if self.show_footer and self.footer_h > 0:
            fy = canvas_h
            fh = bh - canvas_h
            cr.set_source_rgba(12 / 255.0, 15 / 255.0, 22 / 255.0, 0.55)
            cr.rectangle(bx + 1.0, fy, bw - 2.0, fh)
            cr.fill()
            cr.set_source_rgba(*_hex_rgba(pal["structure"], 0.72))
            cr.set_line_width(1.0)
            cr.move_to(bx + 1.0, fy + 0.5)
            cr.line_to(bx + bw - 1.0, fy + 0.5)
            cr.stroke()

            if self.items:
                lmin_x = min(it["log_x"] for it in self.items.values())
                lmin_y = min(it["log_y"] for it in self.items.values())
                lmax_x = max(it["log_x"] + it["log_w"] for it in self.items.values())
                lmax_y = max(it["log_y"] + it["log_h"] for it in self.items.values())
                span_txt = t("cc_display.map_span_summary", p0=lmax_x - lmin_x, p1=lmax_y - lmin_y, p2=len(self.items))
            else:
                span_txt = ""
            hint_txt = t("cc_display.map_footer_hint")

            fdesc = Pango.FontDescription()
            fdesc.set_family(font_family)
            fdesc.set_size(sub_sz)

            fl_right = PangoCairo.create_layout(cr)
            fl_right.set_font_description(fdesc)
            fl_right.set_text(span_txt, -1)
            _, rr_ext = fl_right.get_pixel_extents()

            fpad = max(10.0, round(self.font_px * 0.9))
            fl_left = PangoCairo.create_layout(cr)
            fl_left.set_font_description(fdesc)
            fl_left.set_ellipsize(Pango.EllipsizeMode.END)
            avail_left = max(40.0, bw - (fpad * 3.0) - rr_ext.width)
            fl_left.set_width(int(avail_left * Pango.SCALE))
            fl_left.set_text(hint_txt, -1)
            _, rl_ext = fl_left.get_pixel_extents()

            fty = fy + max(2.0, (fh - max(rl_ext.height, rr_ext.height)) / 2.0)
            cr.set_source_rgba(*_hex_rgba(pal["schema"], 0.88))
            cr.move_to(bx + fpad, fty)
            PangoCairo.show_layout(cr, fl_left)

            cr.set_source_rgba(*_hex_rgba(pal["focus_val"], 0.90))
            cr.move_to(bx + bw - fpad - rr_ext.width, fty)
            PangoCairo.show_layout(cr, fl_right)

        cr.restore()
        return False

    def _on_press(self, _widget, event):
        if event.button != 1:
            return False
        hit = self._hit_test(event.x, event.y)
        if not hit:
            return False
        it = self.items[hit]
        self.drag_state = {
            "out_name": hit,
            "start_x": it["x"],
            "start_y": it["y"],
            "mouse_x": event.x_root,
            "mouse_y": event.y_root,
            "moved": False,
        }
        self._set_cursor("grabbing")
        self.queue_draw()
        return True

    def _on_motion(self, _widget, event):
        if self.drag_state:
            out_name = self.drag_state["out_name"]
            dx = event.x_root - self.drag_state["mouse_x"]
            dy = event.y_root - self.drag_state["mouse_y"]
            if abs(dx) >= 4.0 or abs(dy) >= 4.0:
                self.drag_state["moved"] = True
            sx, sy, snapped_to, guides = self._compute_snapped(
                out_name,
                self.drag_state["start_x"] + dx,
                self.drag_state["start_y"] + dy,
            )
            self.items[out_name]["x"] = sx
            self.items[out_name]["y"] = sy
            lx, ly = self._snapped_logical(out_name, sx, sy, snapped_to)
            self._live_coords = self._normalized_coords(out_name, lx, ly)
            self._active_guides = guides
            self.queue_draw()
            return True

        hit = self._hit_test(event.x, event.y)
        if hit != self._hovered_out:
            self._hovered_out = hit
            self._set_cursor("grab" if hit else None)
            self.queue_draw()
        return False

    def _on_leave(self, _widget, _event):
        if self._hovered_out is not None:
            self._hovered_out = None
            if not self.drag_state:
                self._set_cursor(None)
            self.queue_draw()
        return False

    def _on_release(self, _widget, event):
        if not self.drag_state or event.button != 1:
            return False
        st = self.drag_state
        out_name = st["out_name"]
        dx = event.x_root - st["mouse_x"]
        dy = event.y_root - st["mouse_y"]
        moved = bool(st.get("moved"))
        self.drag_state = None
        self._active_guides = []
        self._live_coords.clear()

        if abs(dx) < 4.0 and abs(dy) < 4.0 and not moved:
            hit = self._hit_test(event.x, event.y)
            self._hovered_out = hit
            self._set_cursor("grab" if hit else None)
            self.queue_draw()
            if callable(self.on_select):
                from gi.repository import GLib

                GLib.idle_add(lambda o=out_name: (self.on_select(o), False)[1])
            return True

        sx, sy, snapped_to, _guides = self._compute_snapped(
            out_name,
            st["start_x"] + dx,
            st["start_y"] + dy,
        )
        new_log_x, new_log_y = self._snapped_logical(out_name, sx, sy, snapped_to)
        final_coords = self._normalized_coords(out_name, new_log_x, new_log_y)
        from panels.cc_engine_display import _invalidate_outputs_cache, _update_niri_output_kdl

        for name, (px, py) in final_coords.items():
            probe.run_cmd(["niri", "msg", "output", name, "position", "set", str(px), str(py)])
            _update_niri_output_kdl(name, position=(px, py))
        _invalidate_outputs_cache()
        if callable(self.on_select):
            from core.state import STATE

            STATE.set("display", "selected_output", out_name)
        hit = self._hit_test(event.x, event.y)
        self._hovered_out = hit
        self._set_cursor("grab" if hit else None)
        self.queue_draw()
        from gi.repository import GLib

        GLib.timeout_add(250, lambda: not self.refresh_cb())
        return True


_NIRI_OUTPUT_MAP_CLS = None


def NiriOutputMap(
    outputs_data: dict,
    refresh_cb,
    map_width: int = 380,
    padding: int = 14,
    fixed_height: int = 0,
    selected_output: str | None = None,
    on_select=None,
    inset_x: int = 12,
    font_px: float = 11.5,
    show_footer: bool = False,
):
    global _NIRI_OUTPUT_MAP_CLS
    if _NIRI_OUTPUT_MAP_CLS is None:
        import gi

        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk

        class _NiriOutputMap(_NiriOutputMapMixin, Gtk.DrawingArea):
            pass

        _NIRI_OUTPUT_MAP_CLS = _NiriOutputMap
    return _NIRI_OUTPUT_MAP_CLS(
        outputs_data,
        refresh_cb,
        map_width,
        padding,
        fixed_height=fixed_height,
        selected_output=selected_output,
        on_select=on_select,
        inset_x=inset_x,
        font_px=font_px,
        show_footer=show_footer,
    )
