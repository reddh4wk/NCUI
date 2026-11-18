#!/usr/bin/env python3
import hashlib
import math
import os
import re
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gtk, Gdk, GdkPixbuf, GLib, Pango, PangoCairo
from core import probe
from core.config import THEME
from core.i18n import t
from core.state import STATE
from ui.canvas import Canvas
from ui.kit import ActionBtn, Badge, Box, InlineEntry, InlineField, SelectPill, StateBadge, SwatchTxt, Txt, get_inline_entry_error, get_semantic_palette
from ui.widgets import AsciiCard
from panels.cc_engine_personalization import (
    SOLID_CLASSIC_PRESETS,
    SOLID_DESIGNER_PRESETS,
    WALL_DIR,
    _feedback_cards,
    _find_media_file,
    _get_niri_outputs,
    _resolve_solid_hex,
    _resolve_yt_source,
    count_folder_media,
    detect_media_kind,
    get_active_yazi_session,
    get_desktop_preview_state,
    get_selected_monitors,
    is_live_15fps_enabled,
    sync_editor_from_live_monitors,
)

_MATRIX_RGB = {
    "green": (0.0, 1.0, 0.45),
    "cyan": (0.0, 0.94, 1.0),
    "blue": (0.30, 0.56, 1.0),
    "red": (1.0, 0.16, 0.25),
    "magenta": (1.0, 0.16, 0.62),
    "yellow": (1.0, 0.92, 0.10),
    "white": (0.94, 0.96, 1.0),
}
_CHARSETS = {
    "katakana": "アイウエオカキクケコサシスセソタチツテト",
    "ascii": "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ*+",
    "binary": "0101010101011001",
    "hex": "0123456789ABCDEF",
}
_PIXBUF_CACHE = {}


def _hex_to_rgb_f(hex_str: str, fallback: tuple = (0.0, 0.94, 1.0)) -> tuple:
    h = str(hex_str or "").strip().lstrip("#")
    if len(h) == 6:
        try:
            return (int(h[0:2], 16) / 255.0, int(h[2:4], 16) / 255.0, int(h[4:6], 16) / 255.0)
        except ValueError:
            pass
    return fallback


def _vivid_rainbow_rgb(seed_b: bytes) -> tuple:
    r, g, b = seed_b[3], seed_b[4], seed_b[5]
    if r < 0xCC and g < 0xCC and b < 0xCC:
        ch = seed_b[6] % 3
        boost = 0xCC + (seed_b[7] % (256 - 0xCC))
        if ch == 0:
            r = boost
        elif ch == 1:
            g = boost
        else:
            b = boost
    return (r / 255.0, g / 255.0, b / 255.0)


def _rounded_rect(cr, x: float, y: float, w: float, h: float, r: float) -> None:
    rad = max(0.0, min(r, w / 2.0, h / 2.0))
    cr.new_sub_path()
    cr.arc(x + w - rad, y + rad, rad, -math.pi / 2, 0)
    cr.arc(x + w - rad, y + h - rad, rad, 0, math.pi / 2)
    cr.arc(x + rad, y + h - rad, rad, math.pi / 2, math.pi)
    cr.arc(x + rad, y + rad, rad, math.pi, 3 * math.pi / 2)
    cr.close_path()


def _draw_open_box_frame(cr, alloc_w: float, alloc_h: float, char_w: float, border_hex: str) -> None:
    x_l = char_w * 0.5
    x_r = alloc_w - (char_w * 0.5)
    y_b = alloc_h - 4.0
    b_rad = 5.0 if THEME["ascii"].get("box_style", "rounded") == "rounded" else 0.0
    br, bg_c, bb = _hex_to_rgb_f(border_hex, (0.26, 0.29, 0.36))
    cr.set_source_rgb(br, bg_c, bb)
    cr.set_line_width(1.2)
    cr.new_sub_path()
    cr.move_to(x_l, 0)
    cr.line_to(x_l, y_b - b_rad)
    if b_rad > 0:
        cr.arc_negative(x_l + b_rad, y_b - b_rad, b_rad, math.pi, math.pi / 2)
    cr.line_to(x_r - b_rad, y_b)
    if b_rad > 0:
        cr.arc_negative(x_r - b_rad, y_b - b_rad, b_rad, math.pi / 2, 0)
    cr.line_to(x_r, 0)
    cr.stroke()


def _get_fitted_pixbuf(path: str, target_w: int, target_h: int, fit: str = "fill"):
    """Scale preview pixbuf according to fit mode ('fill', 'fit', 'center', 'stretch')."""
    if not path or not os.path.isfile(path) or target_w < 8 or target_h < 8:
        return None
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = 0.0
    key = (path, mtime, int(target_w), int(target_h), fit)
    if key in _PIXBUF_CACHE:
        return _PIXBUF_CACHE[key]
    try:
        src = GdkPixbuf.Pixbuf.new_from_file(path)
        ow, oh = src.get_width(), src.get_height()
        if ow <= 0 or oh <= 0:
            return None
        if fit == "stretch":
            pb = src.scale_simple(target_w, target_h, GdkPixbuf.InterpType.BILINEAR)
            ox, oy = 0, 0
        elif fit == "fit":
            sc = min(target_w / float(ow), target_h / float(oh))
            sw, sh = max(4, int(round(ow * sc))), max(4, int(round(oh * sc)))
            pb = src.scale_simple(sw, sh, GdkPixbuf.InterpType.BILINEAR)
            ox, oy = (target_w - sw) // 2, (target_h - sh) // 2
        elif fit == "center":
            sc = min(1.0, min(target_w / float(ow), target_h / float(oh)) * 1.1)
            sw, sh = max(4, min(target_w, int(round(ow * sc)))), max(4, min(target_h, int(round(oh * sc))))
            pb = src.scale_simple(sw, sh, GdkPixbuf.InterpType.BILINEAR)
            ox, oy = (target_w - sw) // 2, (target_h - sh) // 2
        else:
            sc = max(target_w / float(ow), target_h / float(oh))
            sw, sh = max(target_w, int(round(ow * sc))), max(target_h, int(round(oh * sc)))
            scaled = src.scale_simple(sw, sh, GdkPixbuf.InterpType.BILINEAR)
            pb = scaled.new_subpixbuf(max(0, (sw - target_w) // 2), max(0, (sh - target_h) // 2), target_w, target_h)
            ox, oy = 0, 0
        res = (pb, ox, oy)
        if len(_PIXBUF_CACHE) > 48:
            _PIXBUF_CACHE.clear()
        _PIXBUF_CACHE[key] = res
        return res
    except Exception:
        return None


class NiriDesktopPreviewMap(Gtk.EventBox):
    def __init__(
        self,
        outputs_data: dict,
        preview_states: dict,
        selected_monitors: list,
        on_toggle_monitor,
        map_width: int = 840,
        char_w: float = 8.0,
        border_hex: str = "#43495c",
        live_15fps: bool = True,
        max_map_height: int = 230,
        padding: int = 12,
        footer_text: str = "",
    ):
        super().__init__()
        self.set_visible_window(False)
        self.set_above_child(True)
        self.outputs = outputs_data
        self.preview_states = preview_states
        self.selected_monitors = list(selected_monitors)
        self.on_toggle_monitor = on_toggle_monitor
        self.map_width = max(380, int(map_width))
        self.char_w = max(5.0, float(char_w))
        self.border_hex = border_hex
        self.live_15fps = bool(live_15fps)
        self.max_map_height = int(max_map_height)
        self.padding = int(padding)
        self.footer_text = str(footer_text or "").strip()
        self.footer_h = 30 if self.footer_text else 10
        self.rects = []
        self._anim_tick = 0
        self._timer_id = None
        self._has_animated = any(
            st.get("mode") in ("ncmatrix", "cmatrix") or len(st.get("frames") or []) > 1
            for st in preview_states.values()
        )

        self.da = Gtk.DrawingArea()
        self.add(self.da)
        self._compute_layout()

        self.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
        )
        self.connect("button-press-event", self._on_press)
        self.connect("button-release-event", lambda _w, ev: ev.button == 1)
        self.da.connect("draw", self._on_draw)
        self.connect("map", self._on_map)
        self.connect("unmap", self._stop_timer)
        self.connect("destroy", self._stop_timer)

    def set_border_color(self, border_hex: str) -> None:
        if border_hex and border_hex != self.border_hex:
            self.border_hex = border_hex
            self.da.queue_draw()

    def _on_map(self, *_args) -> None:
        self._stop_timer()
        if self.live_15fps and self._has_animated and os.environ.get("NC_UI_OFFSCREEN") != "1":
            self._timer_id = GLib.timeout_add(66, self._on_anim_tick)

    def _stop_timer(self, *_args) -> None:
        if self._timer_id is not None:
            GLib.source_remove(self._timer_id)
            self._timer_id = None

    def _on_anim_tick(self) -> bool:
        if not self.get_mapped() or not self.live_15fps:
            self._timer_id = None
            return False
        self._anim_tick += 1
        self.da.queue_draw()
        return True

    def _compute_layout(self, resize_widgets: bool = True) -> None:
        valid = {k: v for k, v in self.outputs.items() if v.get("logical")}
        wall_margin = max(14, int(math.ceil(self.char_w * 2.0)) + self.padding)
        if not valid:
            if resize_widgets:
                self.set_size_request(self.map_width, 120)
                self.da.set_size_request(self.map_width, 120)
            return

        min_x = min(v["logical"]["x"] for v in valid.values())
        min_y = min(v["logical"]["y"] for v in valid.values())
        max_x = max(v["logical"]["x"] + v["logical"]["width"] for v in valid.values())
        max_y = max(v["logical"]["y"] + v["logical"]["height"] for v in valid.values())
        log_w = max(1, max_x - min_x)
        log_h = max(1, max_y - min_y)

        scale_w = (self.map_width - 2 * wall_margin) / float(max(log_w, 3200))
        scale_h = (self.max_map_height - 2 * self.padding) / float(log_h)
        scale = min(scale_w, scale_h)

        rel_rects = []
        for out_name in sorted(valid.keys()):
            ld = valid[out_name]["logical"]
            rw = max(84, int(round(ld["width"] * scale)))
            rh = max(56, int(round(ld["height"] * scale)))
            rx = int(round((ld["x"] - min_x) * scale))
            ry = int(round((ld["y"] - min_y) * scale))
            rel_rects.append((out_name, rx, ry, rw, rh, ld))

        min_rx = min(rx for _n, rx, _ry, _rw, _rh, _ld in rel_rects)
        min_ry = min(ry for _n, _rx, ry, _rw, _rh, _ld in rel_rects)
        used_w = max(rx + rw for _n, rx, _ry, rw, _rh, _ld in rel_rects) - min_rx
        used_h = max(ry + rh for _n, _rx, ry, _rw, rh, _ld in rel_rects) - min_ry
        body_h = max(126, int(round(used_h)) + 2 * self.padding)
        map_h = body_h + self.footer_h

        x_off = max(float(wall_margin), (self.map_width - used_w) / 2.0) - min_rx
        y_off = max(float(self.padding), (body_h - used_h) / 2.0) - min_ry

        if resize_widgets:
            self.set_size_request(self.map_width, map_h)
            self.da.set_size_request(self.map_width, map_h)

        self.rects = [
            (out_name, int(round(x_off + rx)), int(round(y_off + ry)), rw, rh, ld)
            for out_name, rx, ry, rw, rh, ld in rel_rects
        ]

    def _emit_toggle(self, out_name: str) -> bool:
        if self.on_toggle_monitor:
            self.on_toggle_monitor(out_name)
        return False

    def _on_press(self, _widget, event) -> bool:
        if event.button != 1:
            return False
        for out_name, rx, ry, rw, rh, _ld in self.rects:
            if rx <= event.x <= (rx + rw) and ry <= event.y <= (ry + rh):
                if self.on_toggle_monitor:
                    GLib.idle_add(self._emit_toggle, out_name)
                return True
        return False

    def _draw_ncmatrix_bg(self, cr, rx: int, ry: int, rw: int, rh: int, st: dict) -> None:
        cr.set_source_rgb(0.0, 0.0, 0.0)
        cr.paint()

        col_raw = str(st.get("color", "green") or "green").strip().lower()
        is_rainbow = (col_raw == "rainbow")
        if is_rainbow:
            rgb = _MATRIX_RGB["green"]
        elif col_raw.startswith("#") and len(col_raw) == 7:
            rgb = _hex_to_rgb_f(col_raw, _MATRIX_RGB["green"])
        else:
            rgb = _MATRIX_RGB.get(col_raw, _MATRIX_RGB["green"])
        charset = str(st.get("charset", "katakana"))
        glyphs = _CHARSETS.get(charset, _CHARSETS["katakana"])
        try:
            spd = max(1, min(8, int(st.get("speed", "4"))))
        except ValueError:
            spd = 4

        try:
            fsize = max(5, min(32, int(st.get("size", "9") or 9)))
        except ValueError:
            fsize = 9
        is_cjk = (charset == "katakana")
        prev_pt = max(4, min(12, int(round(fsize * 0.65))))
        step_x = max(5, int(round(prev_pt * (1.65 if is_cjk else 1.2))))
        step_y = max(6, int(round(prev_pt * 1.5)))
        cols = max(4, rw // step_x)
        rows = max(3, rh // step_y)
        tick = self._anim_tick if self.live_15fps else 0
        fall_rate = 0.18 + (spd * 0.12)

        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(Pango.FontDescription.from_string(f"monospace bold {prev_pt}"))

        for ci in range(cols):
            seed_b = hashlib.md5(f"{ci}:{charset}:{col_raw}:{spd}:{fsize}".encode("utf-8")).digest()
            step_offset = int(tick * fall_rate * (0.85 + (seed_b[1] % 5) * 0.1))
            span_rows = max(2, rows)
            total_steps = seed_b[0] + spd * 3 + step_offset
            head_r = total_steps % span_rows
            tail_len = 3 + (seed_b[1] % max(3, min(8, 2 + spd)))
            for ri in range(rows):
                dist = (head_r - ri) % rows
                if dist > tail_len:
                    continue
                if is_rainbow:
                    char_cycle = (total_steps - ri) // span_rows
                    c_seed = hashlib.md5(f"rb:{ci}:{ri}:{char_cycle}".encode("utf-8")).digest()
                    col_rgb = _vivid_rainbow_rgb(c_seed)
                else:
                    col_rgb = rgb
                alpha = max(0.16, 1.0 - (dist / float(tail_len + 1)))
                if dist == 0 and not is_rainbow:
                    cr.set_source_rgba(0.95, 1.0, 0.97, 0.96)
                elif dist == 0:
                    cr.set_source_rgba(col_rgb[0], col_rgb[1], col_rgb[2], 0.96)
                else:
                    cr.set_source_rgba(col_rgb[0], col_rgb[1], col_rgb[2], alpha * 0.88)
                g_idx = (seed_b[2] + ci * 7 + ri * 5 + (tick // 4 if dist == 0 else 0)) % len(glyphs)
                layout.set_text(glyphs[g_idx], -1)
                cr.move_to(rx + 3 + ci * step_x, ry + 8 + ri * step_y)
                PangoCairo.show_layout(cr, layout)

    def _draw_monitor_hud(self, cr, out_name: str, rx: int, ry: int, rw: int, rh: int, ld: dict, st: dict, is_selected: bool = True) -> None:
        is_staged = bool(st.get("staged"))

        badge_txt = t("cc_personalization.wp_badge_preview") if is_staged else t("cc_personalization.wp_badge_live")
        b_rgb = (1.0, 0.92, 0.10) if is_staged else (0.0, 1.0, 0.45)
        b_bg = (0.18, 0.15, 0.02, 0.90) if is_staged else (0.02, 0.14, 0.08, 0.88)

        cr.select_font_face("monospace", 0, 1)
        cr.set_font_size(8.5)
        b_ext = cr.text_extents(badge_txt)
        bw_px = b_ext.width + 10
        bh_px = 14
        bx = rx + rw - bw_px - 6
        by = ry + 6
        _rounded_rect(cr, bx, by, bw_px, bh_px, 3.0)
        cr.set_source_rgba(*b_bg)
        cr.fill_preserve()
        cr.set_source_rgba(b_rgb[0], b_rgb[1], b_rgb[2], 0.88)
        cr.set_line_width(1.0)
        cr.stroke()
        cr.set_source_rgb(*b_rgb)
        cr.move_to(bx + 5, by + 10.5)
        cr.show_text(badge_txt)

        mode = st.get("mode", "none")
        raw_target = str(st.get("target") or st.get("image_path") or "").strip()
        if raw_target == "?":
            raw_target = ""
        if mode == "scene" and raw_target:
            kind = st.get("media_kind") or detect_media_kind(raw_target) or "IMG"
            yt_s = _resolve_yt_source(raw_target)
            if yt_s.get("ok"):
                base = yt_s["playlist_id"] if yt_s["is_playlist"] else yt_s["video_id"]
            else:
                base = os.path.basename(raw_target.rstrip("/")) or raw_target
            tag = f"{kind}: {base}"
            tag_rgb = (1.0, 0.92, 0.10) if is_staged else (0.0, 1.0, 0.45)
        elif mode == "slideshow" and raw_target:
            tag = t("cc_personalization.desktop_mode_slide", p0=os.path.basename(raw_target.rstrip("/")) or "dir")
            tag_rgb = (0.0, 0.94, 1.0)
        elif mode in ("ncmatrix", "cmatrix"):
            cs = st.get("charset", "katakana").upper()[:4]
            col_s = str(st.get("color", "green") or "green").strip()
            col_low = col_s.lower()
            tag = t("cc_personalization.desktop_mode_matrix", p0=f"{cs}·{col_s.upper()}·{st.get('size', '9')}PT")
            if col_low == "rainbow":
                tag_rgb = (0.0, 0.94, 1.0)
            elif col_low.startswith("#"):
                tr, tg, tb = _hex_to_rgb_f(col_low, _MATRIX_RGB["green"])
                tag_rgb = (0.88, 0.92, 0.99) if max(tr, tg, tb) < 0.25 else (tr, tg, tb)
            else:
                tag_rgb = _MATRIX_RGB.get(col_low, _MATRIX_RGB["green"])
        elif mode == "solid":
            tag = t("cc_personalization.desktop_mode_solid", p0=st.get("hex", "#18181a"))
            tag_rgb = (0.85, 0.90, 1.0)
        else:
            tag = t("cc_personalization.desktop_mode_none")
            tag_rgb = _hex_to_rgb_f(THEME["colors"].get("schema", "#8c9bba"), (0.55, 0.61, 0.73))

        hud_h = max(21, min(26, int(rh * 0.24)))
        cr.set_source_rgba(0.03, 0.04, 0.07, 0.86)
        cr.rectangle(rx, ry + rh - hud_h, rw, hud_h)
        cr.fill()

        cr.set_font_size(9.5)
        if is_selected:
            cr.set_source_rgb(1.0, 1.0, 1.0)
        else:
            c_unsel = _hex_to_rgb_f(THEME["colors"].get("schema", "#8c9bba"), (0.55, 0.61, 0.73))
            cr.set_source_rgb(*c_unsel)
        mon_txt = f"{'●' if is_selected else '○'} {out_name} {ld.get('width', 0)}x{ld.get('height', 0)}"
        left_ext = cr.text_extents(mon_txt)
        cr.move_to(rx + 8, ry + rh - (hud_h / 2.0) + 3.5)
        cr.show_text(mon_txt)

        avail_right = rw - left_ext.width - 22
        if avail_right >= 48:
            cr.set_font_size(8.8)
            ext = cr.text_extents(tag)
            while ext.width > avail_right and len(tag) > 4:
                tag = tag[:-2] + "…"
                ext = cr.text_extents(tag)
            cr.set_source_rgb(*tag_rgb)
            cr.move_to(rx + rw - ext.width - 8, ry + rh - (hud_h / 2.0) + 3.2)
            cr.show_text(tag)

    def _on_draw(self, da, cr) -> bool:
        alloc = da.get_allocation()
        ref_lbl = getattr(self, "_ref_label", None)
        ref_lay = ref_lbl.get_layout() if ref_lbl is not None else None
        box_w = ref_lay.get_pixel_size()[0] if ref_lay is not None else 0
        alloc_w = box_w if box_w > 0 else max(self.map_width, alloc.width)
        alloc_h = max(120, alloc.height)
        if abs(int(round(alloc_w)) - self.map_width) > 2:
            self.map_width = int(round(alloc_w))
            self._compute_layout(resize_widgets=False)
        x_l = self.char_w * 0.5
        x_r = alloc_w - (self.char_w * 0.5)
        y_b = alloc_h - 4.0
        b_rad = 5.0 if THEME["ascii"].get("box_style", "rounded") == "rounded" else 0.0

        c_vit = _hex_to_rgb_f(THEME["colors"].get("vitality", "#00ff73"), (0.0, 1.0, 0.45))
        c_struct = _hex_to_rgb_f(THEME["colors"].get("structure", "#43495c"), (0.26, 0.29, 0.36))
        c_attn = _hex_to_rgb_f(THEME["colors"].get("attention", "#ffeb19"), (1.0, 0.92, 0.10))

        for out_name, rx, ry, rw, rh, ld in self.rects:
            st = self.preview_states.get(out_name, {"mode": "none"})
            mode = st.get("mode", "none")
            is_selected = out_name in self.selected_monitors
            is_staged = bool(st.get("staged"))

            cr.save()
            _rounded_rect(cr, rx, ry, rw, rh, 8.0)
            cr.clip()

            if mode in ("scene", "slideshow"):
                frames = st.get("frames") or []
                frame_path = frames[(self._anim_tick // 4) % len(frames)] if (self.live_15fps and frames) else st.get("image_path")
                cr.set_source_rgb(0.05, 0.06, 0.09)
                cr.paint()
                fitted = _get_fitted_pixbuf(frame_path, rw, rh, st.get("fit", "fill"))
                if fitted is not None:
                    pb, ox, oy = fitted
                    Gdk.cairo_set_source_pixbuf(cr, pb, rx + ox, ry + oy)
                    cr.paint()
            elif mode == "solid":
                sr, sg, sb = _hex_to_rgb_f(st.get("hex", "#18181a"), (0.09, 0.09, 0.10))
                cr.set_source_rgb(sr, sg, sb)
                cr.paint()
            elif mode in ("ncmatrix", "cmatrix"):
                self._draw_ncmatrix_bg(cr, rx, ry, rw, rh, st)
            else:
                cr.set_source_rgb(0.04, 0.05, 0.08)
                cr.paint()

            if not is_selected:
                cr.set_source_rgba(0.02, 0.03, 0.05, 0.42)
                cr.paint()

            self._draw_monitor_hud(cr, out_name, rx, ry, rw, rh, ld, st, is_selected=is_selected)
            cr.restore()

            _rounded_rect(cr, rx + 1.0, ry + 1.0, rw - 2.0, rh - 2.0, 8.0)
            if is_staged:
                cr.set_source_rgba(c_attn[0], c_attn[1], c_attn[2], 0.98)
                cr.set_line_width(2.4)
            elif is_selected:
                cr.set_source_rgba(c_vit[0], c_vit[1], c_vit[2], 0.95)
                cr.set_line_width(2.4)
            else:
                cr.set_source_rgba(c_struct[0], c_struct[1], c_struct[2], 0.90)
                cr.set_line_width(1.5)
            cr.stroke()

        if self.footer_text:
            c_sch = _hex_to_rgb_f(THEME["colors"].get("schema", "#8c9bba"), (0.55, 0.61, 0.73))
            cr.select_font_face("monospace", 0, 0)
            cr.set_font_size(11.0)
            cr.set_source_rgb(*c_sch)
            cr.move_to(x_l + self.char_w * 2.0, y_b - 10.0)
            cr.show_text(self.footer_text)

        # Left, bottom, and right box walls matching Box nc-box border
        _draw_open_box_frame(cr, alloc_w, alloc_h, self.char_w, self.border_hex)
        return False


class EmbeddedYaziWidget(Gtk.EventBox):
    def __init__(self, session, map_width: int, char_w: float, cell_h: float, border_hex: str):
        super().__init__()
        self.set_visible_window(False)
        self.set_above_child(True)
        self.session = session
        self.map_width = max(380, int(map_width))
        self.char_w = max(5.0, float(char_w))
        self.cell_h = max(14.0, float(cell_h))
        self.border_hex = border_hex
        self._last_geom = (self.char_w * 2.5, 4.0, self.char_w, self.cell_h)
        self._pix_cache = (None, 0, 0, None)

        total_h = int(round(4.0 + self.session.rows * self.cell_h + 4.0))
        self.da = Gtk.DrawingArea()
        self.da.set_size_request(self.map_width, total_h)
        self.set_size_request(self.map_width, total_h)
        self.add(self.da)

        self.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.SCROLL_MASK
            | Gdk.EventMask.SMOOTH_SCROLL_MASK
        )
        self.connect("button-press-event", self._on_button)
        self.connect("button-release-event", self._on_button)
        self.connect("scroll-event", self._on_scroll)
        self.da.connect("draw", self._on_draw)
        self._redraw_cb = lambda: self.da.queue_draw()
        self.session.on_redraw = self._redraw_cb
        self.connect("destroy", self._on_destroy)

    def _on_destroy(self, *_args) -> None:
        if getattr(self.session, "on_redraw", None) is self._redraw_cb:
            self.session.on_redraw = None

    def set_border_color(self, border_hex: str) -> None:
        if border_hex and border_hex != self.border_hex:
            self.border_hex = border_hex
            self.da.queue_draw()

    def _xy_to_cell(self, ex: float, ey: float):
        x0, y0, cw, ch = self._last_geom
        cols, rows = self.session.cols, self.session.rows
        if x0 <= ex <= (x0 + cols * cw) and y0 <= ey <= (y0 + rows * ch):
            c = max(1, min(cols, int((ex - x0) / cw) + 1))
            r = max(1, min(rows, int((ey - y0) / ch) + 1))
            return c, r
        return None

    def _on_button(self, _w, ev) -> bool:
        cell = self._xy_to_cell(ev.x, ev.y)
        if cell is None or ev.button not in (1, 2, 3):
            return False
        btn = {1: 0, 2: 1, 3: 2}[ev.button]
        kind = "M" if ev.type == Gdk.EventType.BUTTON_PRESS else "m"
        self.session.send_bytes(f"\x1b[<{btn};{cell[0]};{cell[1]}{kind}".encode("ascii"))
        return True

    def _on_scroll(self, _w, ev) -> bool:
        cell = self._xy_to_cell(ev.x, ev.y)
        if cell is None:
            return False
        btn = None
        if ev.direction == Gdk.ScrollDirection.UP:
            btn = 64
        elif ev.direction == Gdk.ScrollDirection.DOWN:
            btn = 65
        elif ev.direction == Gdk.ScrollDirection.SMOOTH:
            ok_d, _dx, dy = ev.get_scroll_deltas()
            if ok_d and abs(dy) >= 0.05:
                btn = 64 if dy < 0 else 65
        if btn is not None:
            self.session.send_bytes(f"\x1b[<{btn};{cell[0]};{cell[1]}M".encode("ascii"))
            return True
        return False

    def _on_draw(self, da, cr) -> bool:
        alloc = da.get_allocation()
        ref_lbl = getattr(self, "_ref_label", None)
        ref_lay = ref_lbl.get_layout() if ref_lbl is not None else None
        box_w = ref_lay.get_pixel_size()[0] if ref_lay is not None else 0
        alloc_w = box_w if box_w > 0 else max(self.map_width, alloc.width)
        if abs(int(round(alloc_w)) - self.map_width) > 2:
            self.map_width = int(round(alloc_w))

        x0 = self.char_w * 2.5
        y0 = 4.0
        term_w = max(200.0, (alloc_w - self.char_w * 2.5) - x0)
        ch = self.cell_h

        with self.session.lock:
            rows = self.session.rows
            cols = self.session.cols
            cw = term_w / float(max(1, cols))
            self._last_geom = (x0, y0, cw, ch)
            grid_snap = [[tuple(cell) for cell in row] for row in self.session.grid]
            pix = self.session.preview_pixbuf

        p = get_semantic_palette()
        def_fg = _hex_to_rgb_f(p["focus_val"], (0.89, 0.92, 0.99))
        def_bg = _hex_to_rgb_f(p["structure_bg"], (0.10, 0.11, 0.14))
        typo = THEME["typography"]
        desc_reg = Pango.FontDescription.from_string(
            f"{typo['font_family']}, Font Awesome 6 Free, Font Awesome 6 Brands, Symbols Nerd Font, monospace {typo['font_weight']} {typo['font_size']}"
        )
        desc_bold = Pango.FontDescription.from_string(
            f"{typo['font_family']}, Font Awesome 6 Free, Font Awesome 6 Brands, Symbols Nerd Font, monospace bold {typo['font_size']}"
        )
        layout = PangoCairo.create_layout(cr)

        img_bounds = [rows, -1, cols, -1]
        for r, row in enumerate(grid_snap):
            ry = y0 + r * ch
            c = 0
            while c < cols:
                txt, fg, bg, bold, rev, is_img = row[c]
                if is_img:
                    img_bounds[0] = min(img_bounds[0], r)
                    img_bounds[1] = max(img_bounds[1], r)
                    img_bounds[2] = min(img_bounds[2], c)
                    img_bounds[3] = max(img_bounds[3], c)
                eff_bg = (fg or def_fg) if rev else bg
                if eff_bg is not None and not is_img:
                    c2 = c + 1
                    while c2 < cols:
                        t2 = row[c2]
                        eb2 = (t2[1] or def_fg) if t2[4] else t2[2]
                        if eb2 != eff_bg or t2[5]:
                            break
                        c2 += 1
                    _rounded_rect(cr, x0 + c * cw, ry + 1.0, (c2 - c) * cw, max(2.0, ch - 2.0), 2.5)
                    cr.set_source_rgba(eff_bg[0], eff_bg[1], eff_bg[2], 0.92)
                    cr.fill()
                    c = c2
                else:
                    c += 1

        for r, row in enumerate(grid_snap):
            ry = y0 + r * ch
            c = 0
            while c < cols:
                txt, fg, bg, bold, rev, is_img = row[c]
                if is_img or not txt or txt == " ":
                    c += 1
                    continue
                eff_fg = (bg or def_bg) if rev else (fg or def_fg)
                layout.set_font_description(desc_bold if bold else desc_reg)
                cr.set_source_rgb(*eff_fg)
                if 32 < ord(txt) < 127:
                    run = [txt]
                    c2 = c + 1
                    while c2 < cols:
                        t2 = row[c2]
                        ef2 = (t2[2] or def_bg) if t2[4] else (t2[1] or def_fg)
                        if t2[5] or not t2[0] or not (32 <= ord(t2[0]) < 127) or ef2 != eff_fg or t2[3] != bold:
                            break
                        run.append(t2[0])
                        c2 += 1
                    layout.set_text("".join(run), -1)
                    cr.move_to(x0 + c * cw, ry)
                    PangoCairo.show_layout(cr, layout)
                    c = c2
                else:
                    layout.set_text(txt, -1)
                    cr.move_to(x0 + c * cw, ry)
                    PangoCairo.show_layout(cr, layout)
                    c += 1

        if pix is not None and img_bounds[1] >= img_bounds[0] and img_bounds[3] >= img_bounds[2]:
            ix = x0 + img_bounds[2] * cw
            iy = y0 + img_bounds[0] * ch
            iw = max(8.0, (img_bounds[3] - img_bounds[2] + 1) * cw)
            ih = max(8.0, (img_bounds[1] - img_bounds[0] + 1) * ch)
            pw, ph = max(1, pix.get_width()), max(1, pix.get_height())
            sc = min(iw / float(pw), ih / float(ph))
            dw, dh = max(4, int(round(pw * sc))), max(4, int(round(ph * sc)))
            cached_src, cw_c, ch_c, cached_pb = self._pix_cache
            if cached_src is not pix or cw_c != dw or ch_c != dh or cached_pb is None:
                cached_pb = pix.scale_simple(dw, dh, GdkPixbuf.InterpType.BILINEAR)
                self._pix_cache = (pix, dw, dh, cached_pb)
            if cached_pb is not None:
                ox = ix + (iw - dw) / 2.0
                oy = iy + (ih - dh) / 2.0
                Gdk.cairo_set_source_pixbuf(cr, cached_pb, ox, oy)
                cr.paint()

        return False


def _build_wallpaper_standard(cc_panel, canvas: Canvas) -> list:
    outputs_data = _get_niri_outputs()
    out_names = sorted(outputs_data.keys())
    sync_editor_from_live_monitors(outputs_data)

    sel_mons = get_selected_monitors(out_names)
    all_selected = (len(out_names) > 0 and len(sel_mons) == len(out_names))
    live_15fps = is_live_15fps_enabled()

    cards = _feedback_cards()
    preview_states = get_desktop_preview_state(outputs_data)
    has_staged = any(bool(st.get("staged")) for st in preview_states.values())

    # CARD 1: Unified Framed Monitor Map & Live 15 FPS Mini-Engine
    fps_lbl = t("cc_personalization.wp_live_15fps" if live_15fps else "cc_personalization.wp_static_preview")
    hdr_acts = [
        SelectPill(fps_lbl, "cc_desk:toggle_fps", selected=live_15fps, style="secondary"),
        Badge(t("cc_personalization.wp_badge_preview"), style="warn") if has_staged else StateBadge(True, t("cc_personalization.wp_badge_live")),
    ]
    if has_staged:
        hdr_acts.append(ActionBtn(t("cc_personalization.wp_reset"), "cc_desk:reset_staged", tone="warn"))
    hdr_acts.append(ActionBtn(t("cc_personalization.wp_apply"), "cc_desk:apply_staged", tone="apply" if has_staged else "inactive"))

    prev_box = Box(t("cc_personalization.desktop_preview_title"), icon="\uf108", actions=hdr_acts, highlighted=False)
    prev_box.add_row(" ")
    prev_box.add_row(f"  {Txt(t('cc_personalization.desktop_preview_hint'), role='schema')}")
    prev_box.add_row(" ")

    valid_outs = [o for o in out_names if outputs_data[o].get("logical")]
    if valid_outs:
        prev_box.open_bottom = True
        summary_str = "  |  ".join(
            f"{o}: {outputs_data[o]['logical'].get('width', 0)}x{outputs_data[o]['logical'].get('height', 0)}"
            for o in valid_outs
        )

        def _on_toggle_mon(out_name: str) -> None:
            if cc_panel:
                cc_panel.on_link(f"cc_desk:sel_mon:{out_name}")

        map_w = NiriDesktopPreviewMap(
            outputs_data,
            preview_states,
            sel_mons,
            on_toggle_monitor=_on_toggle_mon,
            map_width=int(round(canvas.total_px)),
            char_w=canvas.char_w,
            border_hex=prev_box._resolve_palette()[0],
            live_15fps=live_15fps,
            footer_text=summary_str,
        )
        cards.append(AsciiCard(prev_box, canvas, embedded_widget=map_w))
    else:
        cards.append(prev_box)

    # CARD 2: Wallpaper Configuration & Modes
    raw_wp_mode = str(STATE.get("desktop", "wp_mode", "scene"))
    wp_mode = "ncmatrix" if raw_wp_mode == "cmatrix" else raw_wp_mode
    cfg_box = Box(t("cc_personalization.wp_config_title"), icon="\uf03e", highlighted=False)

    # Gruppo A — Target & Operating Mode
    cfg_box.add_row(" ")
    mon_pills = [SelectPill(t("cc_personalization.wp_all_monitors"), "cc_desk:sel_all", selected=all_selected, style="secondary")] + [
        SelectPill(o, f"cc_desk:sel_mon:{o}", selected=(o in sel_mons), style="secondary") for o in out_names
    ]
    cfg_box.add_kv(t("cc_personalization.wp_monitors_row"), " ".join(mon_pills))

    mode_pills = " ".join([
        SelectPill(t("cc_personalization.wp_mode_scene"), "cc_desk:mode:scene", selected=(wp_mode == "scene"), style="secondary"),
        SelectPill(t("cc_personalization.wp_mode_slideshow"), "cc_desk:mode:slideshow", selected=(wp_mode == "slideshow"), style="secondary"),
        SelectPill(t("cc_personalization.wp_mode_ncmatrix"), "cc_desk:mode:ncmatrix", selected=(wp_mode == "ncmatrix"), style="secondary"),
        SelectPill(t("cc_personalization.wp_mode_solid"), "cc_desk:mode:solid", selected=(wp_mode == "solid"), style="secondary"),
    ])
    cfg_box.add_kv(t("cc_personalization.wp_mode_row"), mode_pills)
    cfg_box.add_row(" ")

    has_staged_sel = any(bool(preview_states.get(m, {}).get("staged")) for m in sel_mons)

    has_animated_bg = any(
        st.get("mode") == "scene"
        and (
            (st.get("media_kind") or detect_media_kind(st.get("target", "")))
            in ("GIF", "VIDEO", "YT VIDEO", "YT PLAYLIST")
            or str(st.get("target", "")).startswith("http")
        )
        for st in preview_states.values()
    )

    if wp_mode == "scene":
        scene_path = str(STATE.get("desktop", "scene_path", "") or "")
        scene_fit = str(STATE.get("desktop", "scene_fit", "fill") or "fill")
        kind = detect_media_kind(scene_path)
        yt_src = _resolve_yt_source(scene_path)
        is_yt = kind in ("YT VIDEO", "YT PLAYLIST") or yt_src.get("ok")
        is_video_or_yt = kind in ("VIDEO", "YT VIDEO", "YT PLAYLIST") or scene_path.startswith("http")
        is_playlist = kind == "YT PLAYLIST"
        if kind in ("GIF", "VIDEO", "YT VIDEO", "YT PLAYLIST") or scene_path.startswith("http"):
            has_animated_bg = True

        # Gruppo B — Scene Source & Geometry
        cfg_box.add_section(t("cc_personalization.wp_sec_source"))
        cfg_box.add_row(" ")

        if scene_path:
            disp_scene = scene_path if scene_path.startswith("http") else os.path.basename(scene_path.rstrip("/"))
        else:
            disp_scene = t("cc_personalization.wp_no_scene_selected")
        kind_tag = f" {Txt(f'({kind})', role='schema')}" if kind else ""
        clear_btn = ActionBtn(t("cc_personalization.wp_clear_scene"), "cc_desk:clear_scene", tone="danger_soft") if scene_path else ""
        cfg_box.add_kv(
            t("cc_personalization.wp_selected_scene"),
            f"{Txt(canvas.truncate_plain(disp_scene, 34), role='focus' if scene_path else 'inactive')}{kind_tag}",
            right_markup=clear_btn,
        )

        source_btns = " ".join([
            ActionBtn(t("cc_personalization.wp_set_scene"), "cc_desk:pick_scene", tone="neutral"),
            ActionBtn(t("cc_personalization.wp_type_path"), "cc_desk:type_scene", tone="neutral"),
            ActionBtn(t("cc_personalization.wp_youtube_btn"), "cc_desk:prompt_yt", tone="neutral"),
            ActionBtn(t("cc_personalization.wp_paste_yt_btn"), "cc_desk:paste_yt", tone="neutral"),
        ])
        cfg_box.add_kv(t("cc_personalization.wp_scene_source"), source_btns)

        yt_deliv = str(STATE.get("desktop", "yt_delivery", "stream") or "stream")
        deliv_pills = " ".join([
            SelectPill(t("cc_personalization.wp_yt_stream"), "cc_desk:yt_deliv:stream", selected=(yt_deliv == "stream"), style="secondary"),
            SelectPill(t("cc_personalization.wp_yt_dl_local"), "cc_desk:yt_deliv:download", selected=(yt_deliv == "download"), style="secondary"),
        ])
        cfg_box.add_kv(t("cc_personalization.wp_yt_delivery"), deliv_pills)

        if STATE.get("desktop", "yt_status") == "downloading":
            dl_key = "cc_personalization.wp_yt_downloading_pl" if is_playlist else "cc_personalization.wp_yt_downloading"
            cfg_box.add_kv(t("cc_personalization.wp_yt_status"), Txt(t(dl_key), role="attention"))

        fit_pills = " ".join([
            SelectPill(t("cc_personalization.wp_fit_fill"), "cc_desk:fit:fill", selected=(scene_fit == "fill"), style="secondary"),
            SelectPill(t("cc_personalization.wp_fit_fit"), "cc_desk:fit:fit", selected=(scene_fit == "fit"), style="secondary"),
            SelectPill(t("cc_personalization.wp_fit_center"), "cc_desk:fit:center", selected=(scene_fit == "center"), style="secondary"),
            SelectPill(t("cc_personalization.wp_fit_stretch"), "cc_desk:fit:stretch", selected=(scene_fit == "stretch"), style="secondary"),
        ])
        cfg_box.add_kv(t("cc_personalization.wp_scaling_fit"), fit_pills)
        cfg_box.add_kv(t("cc_personalization.wp_supported_formats"), Txt(t("cc_personalization.wp_supported_val"), role="inactive"))
        cfg_box.add_row(" ")

        # Gruppo C — Video & YouTube Playback Engine
        if is_video_or_yt:
            cfg_box.add_section(t("cc_personalization.wp_sec_playback"))
            cfg_box.add_row(" ")

            vid_loop = str(STATE.get("desktop", "video_loop", "loop") or "loop")
            loop_pills = " ".join([
                SelectPill(t("cc_personalization.wp_play_loop"), "cc_desk:vid_loop:loop", selected=(vid_loop == "loop"), style="secondary"),
                SelectPill(t("cc_personalization.wp_play_once"), "cc_desk:vid_loop:once", selected=(vid_loop == "once"), style="secondary"),
            ])
            cfg_box.add_kv(t("cc_personalization.wp_playback_mode"), loop_pills)

            if is_playlist:
                pl_order = str(STATE.get("desktop", "playlist_order", "sequential") or "sequential")
                pl_pills = " ".join([
                    SelectPill(t("cc_personalization.wp_pl_seq"), "cc_desk:pl_ord:sequential", selected=(pl_order == "sequential"), style="secondary"),
                    SelectPill(t("cc_personalization.wp_pl_shuffle"), "cc_desk:pl_ord:shuffle", selected=(pl_order == "shuffle"), style="secondary"),
                ])
                pl_tracks = " ".join([
                    ActionBtn(t("cc_personalization.wp_pl_prev"), "cc_desk:pl_prev", tone="neutral"),
                    ActionBtn(t("cc_personalization.wp_pl_next"), "cc_desk:pl_next", tone="neutral"),
                ])
                cfg_box.add_kv(t("cc_personalization.wp_playlist_ctrl"), pl_pills, right_markup=pl_tracks)

            vid_mute = bool(STATE.get("desktop", "video_mute", True))
            vid_vol = int(STATE.get("desktop", "video_volume", 60) or 60)
            audio_pills = " ".join([
                SelectPill(t("cc_personalization.wp_audio_mute"), "cc_desk:vid_mute:1", selected=vid_mute, style="secondary"),
                SelectPill(t("cc_personalization.wp_audio_on"), "cc_desk:vid_mute:0", selected=(not vid_mute), style="secondary"),
            ])
            cfg_box.add_kv(t("cc_personalization.wp_video_audio"), audio_pills)
            if not vid_mute:
                vol_pills = " ".join(
                    SelectPill(f"{v}%", f"cc_desk:vid_vol:{v}", selected=(vid_vol == v), style="secondary")
                    for v in (20, 40, 60, 80, 100)
                )
                cfg_box.add_kv(t("cc_personalization.wp_audio_volume"), vol_pills)
            cfg_box.add_row(" ")

    elif wp_mode == "slideshow":
        folder = str(STATE.get("desktop", "slide_folder", WALL_DIR) or WALL_DIR)
        interval = str(STATE.get("desktop", "slide_interval", "5m") or "5m")
        order = str(STATE.get("desktop", "slide_order", "shuffle") or "shuffle")
        trans = str(STATE.get("desktop", "slide_trans", "fade") or "fade")
        spd = str(STATE.get("desktop", "slide_speed", "1.5s") or "1.5s")

        sample_media = _find_media_file(folder)
        if sample_media and detect_media_kind(sample_media) in ("GIF", "VIDEO", "YT VIDEO"):
            has_animated_bg = True

        cfg_box.add_section(t("cc_personalization.wp_sec_source"))
        cfg_box.add_row(" ")

        f_count = count_folder_media(folder)
        f_disp = canvas.truncate_plain(folder.replace(os.path.expanduser("~"), "~"), 28)
        folder_btns = f"{ActionBtn(t('cc_personalization.wp_set_scene_folder'), 'cc_desk:pick_folder', tone='neutral')} {ActionBtn(t('cc_personalization.wp_type_path'), 'cc_desk:type_folder', tone='neutral')}"
        cfg_box.add_kv(
            t("cc_personalization.wp_scene_folder"),
            f"{Txt(f_disp, role='val')} {Txt(t('cc_personalization.wp_files_count', p0=f_count), role='schema')}",
            right_markup=folder_btns,
        )
        cfg_box.add_kv(t("cc_personalization.wp_supported_formats"), Txt(t("cc_personalization.wp_supported_val"), role="inactive"))

        int_pills = " ".join([
            SelectPill(t(f"cc_personalization.wp_interval_{iv}"), f"cc_desk:slide_int:{iv}", selected=(interval == iv), style="secondary")
            for iv in ("1m", "5m", "15m", "30m", "1h")
        ] + [
            InlineField(
                "cc_desk_interval",
                "cc_desk:inline_interval",
                value=interval if interval not in ("1m", "5m", "15m", "30m", "1h") else "",
                empty_label=t("cc_personalization.wp_custom_dots"),
                placeholder="5m",
                width=8,
                icon="",
                tone="neutral",
                selected=(interval not in ("1m", "5m", "15m", "30m", "1h")),
            )
        ])
        cfg_box.add_kv(t("cc_personalization.wp_switch_interval"), int_pills)

        ord_pills = " ".join([
            SelectPill(t("cc_personalization.wp_order_shuffle"), "cc_desk:slide_ord:shuffle", selected=(order == "shuffle"), style="secondary"),
            SelectPill(t("cc_personalization.wp_order_seq"), "cc_desk:slide_ord:sequential", selected=(order == "sequential"), style="secondary"),
        ])
        cfg_box.add_kv(t("cc_personalization.wp_playback_order"), ord_pills)

        tr_pills = " ".join([
            SelectPill(t(f"cc_personalization.wp_trans_{tr}"), f"cc_desk:slide_tr:{tr}", selected=(trans == tr), style="secondary")
            for tr in ("fade", "wipe", "grow", "wave", "none")
        ])
        cfg_box.add_kv(t("cc_personalization.wp_trans_effect"), tr_pills)

        spd_pills = " ".join([
            SelectPill(t("cc_personalization.wp_speed_fast"), "cc_desk:slide_spd:0.5s", selected=(spd == "0.5s"), style="secondary"),
            SelectPill(t("cc_personalization.wp_speed_normal"), "cc_desk:slide_spd:1.5s", selected=(spd == "1.5s"), style="secondary"),
            SelectPill(t("cc_personalization.wp_speed_slow"), "cc_desk:slide_spd:3.0s", selected=(spd == "3.0s"), style="secondary"),
        ])
        cfg_box.add_kv(t("cc_personalization.wp_trans_speed"), spd_pills)

        ctrl_btns = " ".join([
            ActionBtn(t("cc_personalization.wp_prev"), "cc_desk:prev", tone="neutral"),
            ActionBtn(t("cc_personalization.wp_pause"), "cc_desk:pause", tone="warn"),
            ActionBtn(t("cc_personalization.wp_resume"), "cc_desk:resume", tone="apply"),
            ActionBtn(t("cc_personalization.wp_next"), "cc_desk:next", tone="neutral"),
        ])
        cfg_box.add_kv(t("cc_personalization.wp_playback_ctrl"), ctrl_btns)
        cfg_box.add_row(" ")

    elif wp_mode == "solid":
        solid_hex = _resolve_solid_hex(STATE.get("desktop", "solid_hex", "#000000") or "#000000")
        preset_map = dict(SOLID_CLASSIC_PRESETS + SOLID_DESIGNER_PRESETS)
        p_key = preset_map.get(solid_hex)
        p_lbl = t(f"cc_personalization.{p_key}") if p_key else t("cc_personalization.wp_solid_custom_label")

        cfg_box.add_section(t("cc_personalization.wp_solid_section"))
        cfg_box.add_row(" ")
        custom_btn = InlineField(
            "cc_desk_solid_hex",
            "cc_desk:inline_solid_hex",
            value=solid_hex if not p_key else "",
            empty_label=t("cc_personalization.wp_solid_custom_btn"),
            placeholder="#RRGGBB",
            width=9,
            icon="\u270e",
            tone="neutral",
            is_hex_color=True,
            selected=(not p_key),
        )
        cfg_box.add_kv(
            t("cc_personalization.wp_solid_active_color"),
            f"{SwatchTxt(solid_hex)}  {Txt(f'({p_lbl})', role='schema')}  {custom_btn}",
        )
        classic_pills = " ".join(
            SelectPill(
                t(f"cc_personalization.{lkey}"),
                f"cc_desk:solid_hex:{hx}",
                selected=(solid_hex == hx),
                dirty=(has_staged_sel and solid_hex == hx),
                style="secondary",
            )
            for hx, lkey in SOLID_CLASSIC_PRESETS
        )
        cfg_box.add_kv(t("cc_personalization.wp_solid_classic_presets"), classic_pills)
        designer_pills = " ".join(
            SelectPill(
                t(f"cc_personalization.{lkey}"),
                f"cc_desk:solid_hex:{hx}",
                selected=(solid_hex == hx),
                dirty=(has_staged_sel and solid_hex == hx),
                style="secondary",
            )
            for hx, lkey in SOLID_DESIGNER_PRESETS
        )
        cfg_box.add_kv(t("cc_personalization.wp_solid_designer_presets"), designer_pills)
        cfg_box.add_row(" ")

    else:
        cm_cs = str(STATE.get("desktop", "cm_charset", "katakana") or "katakana")
        cm_spd = str(STATE.get("desktop", "cm_speed", "4") or "4")
        cm_sz = str(STATE.get("desktop", "cm_size", "9") or "9")
        cm_tr = str(STATE.get("desktop", "cm_trans", "flow") or "flow")

        cfg_box.add_section(t("cc_personalization.ncmatrix_native_wayland_shader_asci"))
        cfg_box.add_row(" ")

        cs_pills = " ".join([
            SelectPill(t(f"cc_personalization.wp_charset_{cs}"), f"cc_desk:cm_charset:{cs}", selected=(cm_cs == cs), style="secondary")
            for cs in ("katakana", "ascii", "binary", "hex")
        ])
        cfg_box.add_kv(t("cc_personalization.charset_mode"), cs_pills)

        cm_col = str(STATE.get("desktop", "cm_color", "green") or "green").strip()
        cm_col_low = cm_col.lower()
        is_custom_hex = bool(re.match(r"^#[0-9A-Fa-f]{6}$", cm_col))
        col_pills = " ".join(
            SelectPill(
                t(f"cc_personalization.wp_color_{c}"),
                f"cc_desk:cm_col:{c}",
                selected=(cm_col_low == c),
                dirty=(has_staged_sel and cm_col_low == c),
                style="secondary",
            )
            for c in ("green", "cyan", "yellow", "white", "rainbow")
        ) + " " + InlineEntry(
            "cm_col_custom",
            "cc_desk:cm_col_custom",
            label=t("cc_personalization.wp_color_custom"),
            placeholder=t("cc_personalization.wp_custom_placeholder"),
            max_len=6,
            active_value=cm_col.upper() if is_custom_hex else "",
            selected=is_custom_hex,
            dirty=(has_staged_sel and is_custom_hex),
        )
        cfg_box.add_kv(t("cc_personalization.shader_color"), col_pills)
        hex_err = get_inline_entry_error("cm_col_custom")
        if hex_err:
            cfg_box.add_row(f"  {Txt(hex_err, role='crit')}")

        spd_pills = " ".join(
            SelectPill(str(s), f"cc_desk:cm_spd:{s}", selected=(cm_spd == str(s)), style="secondary")
            for s in range(1, 9)
        )
        cfg_box.add_kv(t("cc_personalization.speed_level"), spd_pills)

        sz_pills = " ".join(
            SelectPill(t("cc_personalization.wp_cm_size_pt", p0=sz), f"cc_desk:cm_size:{sz}", selected=(cm_sz == str(sz)), style="secondary")
            for sz in ("6", "8", "9", "10", "12", "14", "16", "20")
        )
        cfg_box.add_kv(t("cc_personalization.wp_cm_font_size"), sz_pills)

        tr_pills = " ".join(
            SelectPill(t(f"cc_personalization.wp_ncmatrix_trans_{tr}"), f"cc_desk:cm_trans:{tr}", selected=(cm_tr == tr), style="secondary")
            for tr in ("flow", "drain", "instant")
        )
        cfg_box.add_kv(t("cc_personalization.wp_ncmatrix_transition"), tr_pills)

        cfg_box.add_row(" ")
        cfg_box.add_row(f"  {Txt('ℹ', role='schema')} {Txt(t('cc_personalization.wp_ncmatrix_efficiency_note'), role='inactive')}")
        cfg_box.add_row(" ")

    # Gruppo D — Barra Commit in Fondo al Box
    cfg_box.add_section(t("cc_personalization.wp_sec_commit"))
    cfg_box.add_row(" ")
    if has_staged:
        st_txt = Txt(t("cc_personalization.wp_staged_footer_warn"), role="warn")
        st_btns = f"{ActionBtn(t('cc_personalization.wp_reset'), 'cc_desk:reset_staged', tone='warn')} {ActionBtn(t('cc_personalization.wp_apply_wallpaper'), 'cc_desk:apply_staged', tone='apply')}"
    else:
        st_txt = Txt(t("cc_personalization.wp_live_footer_ok"), role="ok")
        st_btns = ActionBtn(t("cc_personalization.wp_apply_wallpaper"), "cc_desk:apply_staged", tone="inactive")
    cfg_box.add_row(f"  {st_txt}", right_markup=st_btns)
    if has_animated_bg:
        cfg_box.add_row(f"  {Txt(t('cc_personalization.wp_gif_video_warn'), role='warn')}")
    cfg_box.add_row(" ")

    cards.append(cfg_box)

    yazi_sess = get_active_yazi_session()
    if STATE.get("desktop", "yazi_open") and yazi_sess is not None and getattr(yazi_sess, "target_mode", "wallpaper") == "wallpaper":
        yazi_title = t("cc_personalization.wp_yazi_box_folder_title" if yazi_sess.is_folder else "cc_personalization.wp_yazi_box_title")
        yazi_box = Box(
            yazi_title,
            icon="\uf07c",
            actions=[ActionBtn("\uf00d", "cc_desk:close_yazi", tone="danger_soft")],
            highlighted=False,
        )
        yazi_box.add_row(f"  {Txt(t('cc_personalization.wp_yazi_box_hint'), role='schema')}")
        yazi_box.open_bottom = True
        yazi_w = EmbeddedYaziWidget(
            yazi_sess,
            map_width=int(round(canvas.total_px)),
            char_w=canvas.char_w,
            cell_h=max(14.0, float(canvas.char_h)),
            border_hex=yazi_box._resolve_palette()[0],
        )
        yazi_foot = Box("", highlighted=False)
        yazi_foot.open_top = True
        yazi_foot.add_row(" ")
        cards.append(AsciiCard(yazi_box, canvas, embedded_widget=yazi_w, footer_box_model=yazi_foot))

    return cards


def build_cards(cc_panel, canvas: Canvas) -> list:
    return _build_wallpaper_standard(cc_panel, canvas)
