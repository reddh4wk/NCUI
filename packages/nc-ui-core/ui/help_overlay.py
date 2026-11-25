#!/usr/bin/env python3
"""Unified composable Cairo floating overlay engine for NC-UI tooltips, toasts, and system notifications."""

import html
import math

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, GLib, Gtk, Pango, PangoCairo  # noqa: E402

from core.config import THEME, _norm_notif_style, get_hover_help_opacity, get_surface_bg_rgb  # noqa: E402
from core.i18n import t  # noqa: E402
from ui.kit import get_semantic_palette  # noqa: E402


def _hex_rgba(hx: str, alpha: float = 1.0) -> tuple[float, float, float, float]:
    s = str(hx or "#ffffff").strip().lstrip("#")
    if len(s) == 8:
        s = s[2:]
    if len(s) == 6:
        try:
            return (int(s[0:2], 16) / 255.0, int(s[2:4], 16) / 255.0, int(s[4:6], 16) / 255.0, float(alpha))
        except ValueError:
            pass
    return (1.0, 1.0, 1.0, float(alpha))


def _norm_rgb(rgb) -> tuple[float, float, float]:
    r, g, b = float(rgb[0]), float(rgb[1]), float(rgb[2])
    if max(r, g, b) > 1.0:
        return (r / 255.0, g / 255.0, b / 255.0)
    return (r, g, b)


def _rounded_rect(cr, x: float, y: float, w: float, h: float, r: float) -> None:
    r = min(float(r), max(0.0, w * 0.5), max(0.0, h * 0.5))
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi * 0.5, 0.0)
    cr.arc(x + w - r, y + h - r, r, 0.0, math.pi * 0.5)
    cr.arc(x + r, y + h - r, r, math.pi * 0.5, math.pi)
    cr.arc(x + r, y + r, r, math.pi, math.pi * 1.5)
    cr.close_path()


def _style_radius(box_style: str, card_style: str) -> float:
    if box_style in ("sharp", "heavy", "double", "ascii"):
        return 0.0
    return 3.0 if card_style == "clean" else 4.0


def _active_box_style() -> str:
    return str((THEME.get("ascii") or {}).get("box_style", "rounded") or "rounded").strip().lower()


def _active_title_brackets() -> tuple[str, str]:
    tb = (THEME.get("ascii") or {}).get("title_brackets", ["[ ", " ]"])
    if isinstance(tb, (list, tuple)) and len(tb) >= 2:
        return str(tb[0] or ""), str(tb[1] or "")
    return ("[ ", " ]")


def _det_btn_label(key: str) -> str:
    raw = str(t(key) or "").strip()
    return raw.lstrip("▼▲▶▸+- ").strip() or "Details"


def _stroke_box_frame(
    cr,
    outer_path_fn,
    rx: float,
    ry: float,
    rw: float,
    rh: float,
    rad: float,
    box_style: str,
    col_hex: str,
    alpha: float,
) -> None:
    """Apply the orthogonal NC-UI `box_style` (rounded/sharp/heavy/double/dashed/ascii) onto an overlay frame."""
    cr.save()
    outer_path_fn()
    cr.set_source_rgba(*_hex_rgba(col_hex, alpha))
    if box_style == "heavy":
        cr.set_line_width(2.2)
        cr.stroke()
    elif box_style == "dashed":
        cr.set_line_width(1.05)
        cr.set_dash([4.5, 2.5])
        cr.stroke()
    elif box_style == "double":
        cr.set_line_width(1.0)
        cr.stroke()
        if rw > 12.0 and rh > 12.0:
            _rounded_rect(cr, rx + 3.0, ry + 3.0, rw - 6.0, rh - 6.0, max(0.0, rad - 1.0))
            cr.set_source_rgba(*_hex_rgba(col_hex, alpha * 0.72))
            cr.set_line_width(0.9)
            cr.stroke()
    elif box_style == "ascii":
        cr.set_line_width(0.9)
        cr.stroke()
        tlen = 4.0
        cr.set_source_rgba(*_hex_rgba(col_hex, min(1.0, alpha * 1.05)))
        cr.set_line_width(1.5)
        for cx, cy, sx, sy in (
            (rx, ry, 1.0, 1.0),
            (rx + rw, ry, -1.0, 1.0),
            (rx, ry + rh, 1.0, -1.0),
            (rx + rw, ry + rh, -1.0, -1.0),
        ):
            cr.move_to(cx, cy)
            cr.line_to(cx + sx * tlen, cy)
            cr.move_to(cx, cy)
            cr.line_to(cx, cy + sy * tlen)
        cr.stroke()
    else:
        cr.set_line_width(1.05)
        cr.stroke()
    cr.restore()


class _OverlayCardBase(Gtk.EventBox):
    """Shared Pango/Cairo geometry, button hit-testing, and frame painting for floating NC-UI cards."""

    _SH_PAD = 7

    def __init__(self, on_link=None) -> None:
        super().__init__()
        self._on_link = on_link
        self._hovered_btn_uri: str | None = None
        self._btn_rects: list[tuple[float, float, float, float, str, str]] = []
        self._geom: dict = {}
        self.set_visible_window(False)
        self.set_can_focus(False)
        self.set_halign(Gtk.Align.START)
        self.set_valign(Gtk.Align.START)
        self.add_events(
            Gdk.EventMask.ENTER_NOTIFY_MASK
            | Gdk.EventMask.LEAVE_NOTIFY_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
        )

    @staticmethod
    def _font() -> Pango.FontDescription:
        typo = THEME.get("typography") or {}
        ff = str(typo.get("font_family", "Courier") or "Courier")
        fw = str(typo.get("font_weight", "bold") or "bold").strip().lower()
        try:
            fs = float(typo.get("font_size", 11) or 11)
        except Exception:
            fs = 11.0
        return Pango.FontDescription.from_string(
            f"{ff}, Font Awesome 6 Free, Font Awesome 6 Brands, Symbols Nerd Font, monospace {fw} {fs:g}"
        )

    @staticmethod
    def _mk_layout(cr, text: str, fdesc: Pango.FontDescription, max_w: float = -1.0, markup: bool = False):
        lay = PangoCairo.create_layout(cr)
        screen = Gdk.Screen.get_default()
        dpi = screen.get_resolution() if screen is not None else 96.0
        if dpi and dpi > 0.0:
            PangoCairo.context_set_resolution(lay.get_context(), dpi)
        lay.set_font_description(fdesc)
        attrs = Pango.AttrList()
        attrs.insert(Pango.AttrFontFeatures.new("liga=0, clig=0, dlig=0, hlig=0"))
        lay.set_attributes(attrs)
        if max_w > 0.0:
            lay.set_width(int(round(max_w * Pango.SCALE)))
            lay.set_wrap(Pango.WrapMode.WORD_CHAR)
        if markup:
            lay.set_markup(text, -1)
        else:
            lay.set_text(text, -1)
        ink, _log = lay.get_pixel_extents()
        w, h = lay.get_pixel_size()
        return lay, float(w), float(h), ink

    def _hit_btn(self, x: float, y: float) -> str | None:
        pad = 4.0
        for bx, by, bw, bh, uri, _tone in self._btn_rects:
            if (bx - pad) <= x <= (bx + bw + pad) and (by - pad) <= y <= (by + bh + pad):
                return uri
        return None

    def _set_cursor_pointer(self, enabled: bool) -> None:
        try:
            win = self._text_da.get_window()
            if win is None:
                return
            win.set_cursor(Gdk.Cursor.new_from_name(self._text_da.get_display(), "pointer") if enabled else None)
        except Exception:
            pass

    def _on_text_motion(self, _w, event) -> bool:
        hit = self._hit_btn(float(event.x), float(event.y))
        if hit != self._hovered_btn_uri:
            self._hovered_btn_uri = hit
            self._set_cursor_pointer(bool(hit or getattr(self, "_body_click_uri", "")))
            self._text_da.queue_draw()
        return False

    def _on_text_leave(self, *_args) -> bool:
        if self._hovered_btn_uri is not None:
            self._hovered_btn_uri = None
            self._set_cursor_pointer(False)
            self._text_da.queue_draw()
        return False

    def _on_text_press(self, _w, event) -> bool:
        if getattr(event, "button", 0) != 1:
            return False
        hit = self._hit_btn(float(event.x), float(event.y))
        target = hit or getattr(self, "_body_click_uri", "")
        if target and callable(self._on_link):
            GLib.idle_add(lambda u=target: self._on_link(u) or False)
            return True
        return False


class HoverHelpCard(_OverlayCardBase):
    """Compact themed Cairo hover info popup supporting tactical/classic/clean/cyberpunk + box_style."""

    def __init__(
        self,
        entry: dict,
        style: str = "cyberpunk",
        *,
        mode: str = "settings",
        expanded: bool = False,
        on_link=None,
        diagram_widget: Gtk.Widget | None = None,
        max_width_px: int = 360,
        tip_side: str = "top",
        tip_offset_px: float = 28.0,
    ) -> None:
        super().__init__(on_link=on_link)
        self._entry = dict(entry or {})
        self._style = _norm_notif_style(style, "cyberpunk")
        self._mode = str(mode or "settings")
        self._diag_w = diagram_widget
        self._max_w = max(240, int(max_width_px))
        self._tip_side = tip_side if tip_side in ("top", "bottom", "left") else "top"
        self._tip_offset = float(tip_offset_px)

        has_more = (self._mode == "settings") and bool(self._entry.get("verbose") or self._entry.get("diagram"))
        self._expanded = bool(expanded) and has_more

        self._btn_defs: list[tuple[str, str, str]] = []
        if has_more:
            if self._expanded:
                self._btn_defs.append((f"- {_det_btn_label('ui.help_less_btn')}", "ncui:help_collapse", "neutral"))
            else:
                self._btn_defs.append((f"+ {_det_btn_label('ui.help_details_btn')}", "ncui:help_expand", "neutral"))
        if self._expanded:
            self._btn_defs.append(("x", "ncui:help_close", "danger_soft"))

        self.connect("draw", self._on_draw_card)
        self._measure_layout()

        self._vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        for s_fn in ("set_margin_start", "set_margin_end", "set_margin_top", "set_margin_bottom"):
            getattr(self._vbox, s_fn)(self._SH_PAD)

        self._text_da = Gtk.DrawingArea()
        self._text_da.set_size_request(int(math.ceil(self._geom["box_w"])), int(math.ceil(self._geom["text_h"])))
        self._text_da.set_can_focus(False)
        self._text_da.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
            | Gdk.EventMask.ENTER_NOTIFY_MASK
            | Gdk.EventMask.LEAVE_NOTIFY_MASK
        )
        self._text_da.connect("draw", self._on_draw_text)
        self._text_da.connect("motion-notify-event", self._on_text_motion)
        self._text_da.connect("leave-notify-event", self._on_text_leave)
        self._text_da.connect("button-press-event", self._on_text_press)
        self._vbox.pack_start(self._text_da, False, False, 0)

        if self._diag_w is not None:
            px = int(self._geom["pad_x"])
            self._diag_w.set_margin_start(px)
            self._diag_w.set_margin_end(px)
            self._diag_w.set_margin_top(4)
            self._diag_w.set_margin_bottom(int(round(self._geom["bot_pad"])))
            self._vbox.pack_start(self._diag_w, False, False, 0)

        self.add(self._vbox)
        self.card_width_px = int(math.ceil(self._geom["box_w"])) + self._SH_PAD * 2
        self.card_height_px = int(math.ceil(self._geom["box_h"])) + self._SH_PAD * 2
        self.set_size_request(self.card_width_px, self.card_height_px)

    def get_hover_widgets(self) -> list[Gtk.Widget]:
        return [w for w in (self, self._text_da, self._diag_w) if w is not None]

    def set_tip(self, side: str, offset_px: float) -> None:
        if side in ("top", "bottom", "left"):
            self._tip_side = side
        self._tip_offset = float(offset_px)
        self.queue_draw()

    def _measure_layout(self) -> None:
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
        cr = cairo.Context(surf)
        title = str(self._entry.get("title") or "")
        short = str(self._entry.get("short") or "")
        verbose = str(self._entry.get("verbose") or "") if self._expanded else ""

        f_btn = self._font()
        btn_items = []
        btn_total_w = 0.0
        for lbl, uri, tone in self._btn_defs:
            _, bw, bh, _ = self._mk_layout(cr, f"[{lbl}]", f_btn)
            btn_items.append((lbl, uri, tone, bw, bh))
            btn_total_w += bw + 6.0
        if btn_items:
            btn_total_w -= 6.0

        diag_h = float(getattr(self._diag_w, "_height_px", 110) or 110) + 4.0 if self._diag_w is not None else 0.0
        st = self._style
        bl, br = _active_title_brackets()

        if st == "classic":
            f_hdr = self._font()
            f_body = self._font()
            pad_x = 12.0
            max_w = float(max(self._max_w, 420 if not self._expanded else 540))
            _, hw, hh, _ = self._mk_layout(cr, f"{bl}\uf05a {title}{br}", f_hdr)
            min_hdr_w = hw + btn_total_w + 42.0
            wrap_w = max(240.0, max(max_w, min_hdr_w) - pad_x * 2.0)

            _, sw, sh, ink_s = self._mk_layout(cr, short, f_body, max_w=wrap_w)
            vw, vh, ink_v = 0.0, 0.0, None
            if verbose:
                _, vw, vh, ink_v = self._mk_layout(cr, verbose, f_body, max_w=wrap_w)

            box_w = min(max(max_w, min_hdr_w), max(min_hdr_w, max(sw, vw) + pad_x * 2.0 + 6.0, 320.0))
            if self._diag_w is not None:
                box_w = max(box_w, 460.0)
            wrap_w = box_w - pad_x * 2.0
            _, sw, sh, ink_s = self._mk_layout(cr, short, f_body, max_w=wrap_w)
            if verbose:
                _, vw, vh, ink_v = self._mk_layout(cr, verbose, f_body, max_w=wrap_w)

            top_y = hh * 0.5
            first_ink_y = float(ink_s.y) if ink_s.height > 0 else 2.0
            short_y = max(hh + 4.0, top_y + 10.0 - first_ink_y)
            top_ink_gap = (short_y + first_ink_y) - top_y
            verb_y = short_y + sh + 7.0
            if verbose and ink_v is not None and ink_v.height > 0:
                end_ink_y = verb_y + float(ink_v.y + ink_v.height)
            else:
                end_ink_y = short_y + (float(ink_s.y + ink_s.height) if ink_s.height > 0 else sh)
            bot_pad = max(8.0, top_ink_gap)
            if diag_h > 0.0:
                text_h = (verb_y + vh if verbose else short_y + sh) + 4.0
                box_h = text_h + diag_h + bot_pad
            else:
                box_h = end_ink_y + top_ink_gap
                text_h = box_h

            self._geom = {
                "box_w": box_w, "box_h": box_h, "text_h": text_h,
                "pad_x": pad_x, "pad_y": 0.0, "bot_pad": bot_pad,
                "top_y": top_y, "hh": hh, "short_y": short_y, "verb_y": verb_y,
                "wrap_w": wrap_w, "f_hdr": f_hdr, "f_btn": f_btn, "f_body": f_body,
                "btns": btn_items, "sep_y": 0.0,
            }
            return

        if st == "clean":
            f_hdr = self._font()
            f_body = self._font()
            pad_x, pad_y = 12.0, 8.0
            max_w = float(max(self._max_w + 30, 440 if not self._expanded else 560))
            _, hw, hh, _ = self._mk_layout(cr, f"\uf05a {title}", f_hdr)
            min_hdr_w = hw + btn_total_w + pad_x * 2.0 + 18.0
            wrap_w = max(240.0, max(max_w, min_hdr_w) - pad_x * 2.0)

            _, sw, sh, _ = self._mk_layout(cr, short, f_body, max_w=wrap_w)
            vw, vh = 0.0, 0.0
            if verbose:
                _, vw, vh, _ = self._mk_layout(cr, verbose, f_body, max_w=wrap_w)
            box_w = min(max(max_w, min_hdr_w), max(min_hdr_w, max(sw, vw) + pad_x * 2.0 + 6.0, 320.0))
            if self._diag_w is not None:
                box_w = max(box_w, 460.0)
            wrap_w = box_w - pad_x * 2.0
            _, sw, sh, _ = self._mk_layout(cr, short, f_body, max_w=wrap_w)
            if verbose:
                _, vw, vh, _ = self._mk_layout(cr, verbose, f_body, max_w=wrap_w)

            short_y = pad_y + hh + 7.0
            verb_y = short_y + sh + 7.0
            text_h = (verb_y + vh if verbose else short_y + sh) + (4.0 if diag_h > 0.0 else pad_y)
            box_h = text_h + (diag_h + pad_y if diag_h > 0.0 else 0.0)
            self._geom = {
                "box_w": box_w, "box_h": box_h, "text_h": text_h,
                "pad_x": pad_x, "pad_y": pad_y, "bot_pad": pad_y,
                "top_y": 0.0, "hh": hh, "short_y": short_y, "verb_y": verb_y,
                "wrap_w": wrap_w, "f_hdr": f_hdr, "f_btn": f_btn, "f_body": f_body,
                "btns": btn_items, "sep_y": 0.0,
            }
            return

        if st == "cyberpunk":
            f_hdr = self._font()
            f_body = self._font()
            pad_x, pad_y = 12.0, 7.0
            max_w = float(max(self._max_w + 30, 440 if not self._expanded else 560))
            _, hw, hh, _ = self._mk_layout(cr, f"HELP // {title.upper()}", f_hdr)
            min_hdr_w = hw + btn_total_w + pad_x * 2.0 + 18.0
            wrap_w = max(240.0, max(max_w, min_hdr_w) - pad_x * 2.0)

            _, sw, sh, _ = self._mk_layout(cr, short, f_body, max_w=wrap_w)
            vw, vh = 0.0, 0.0
            if verbose:
                _, vw, vh, _ = self._mk_layout(cr, verbose, f_body, max_w=wrap_w)
            box_w = max(min_hdr_w, min(max_w, max(sw, vw) + pad_x * 2.0 + 6.0))
            if self._diag_w is not None:
                box_w = max(box_w, 460.0)
            wrap_w = box_w - pad_x * 2.0
            _, sw, sh, _ = self._mk_layout(cr, short, f_body, max_w=wrap_w)
            if verbose:
                _, vw, vh, _ = self._mk_layout(cr, verbose, f_body, max_w=wrap_w)

            sep_y = pad_y + hh + 5.0
            short_y = sep_y + 5.0
            verb_y = short_y + sh + 7.0
            text_h = (verb_y + vh if verbose else short_y + sh) + (4.0 if diag_h > 0.0 else pad_y)
            box_h = text_h + (diag_h + pad_y if diag_h > 0.0 else 0.0)
            self._geom = {
                "box_w": box_w, "box_h": box_h, "text_h": text_h,
                "pad_x": pad_x, "pad_y": pad_y, "bot_pad": pad_y,
                "top_y": 0.0, "hh": hh, "short_y": short_y, "verb_y": verb_y,
                "wrap_w": wrap_w, "f_hdr": f_hdr, "f_btn": f_btn, "f_body": f_body,
                "btns": btn_items, "sep_y": sep_y,
            }
            return

        # tactical
        f_hdr = self._font()
        f_body = self._font()
        pad_x, pad_y = 12.0, 7.0
        max_w = float(max(self._max_w + 24, 430 if not self._expanded else 560))
        _, hw, hh, _ = self._mk_layout(cr, title, f_hdr)
        min_hdr_w = hw + btn_total_w + pad_x * 2.0 + 18.0
        wrap_w = max(240.0, max(max_w, min_hdr_w) - pad_x * 2.0 - 4.0)

        _, sw, sh, _ = self._mk_layout(cr, short, f_body, max_w=wrap_w)
        vw, vh = 0.0, 0.0
        if verbose:
            _, vw, vh, _ = self._mk_layout(cr, verbose, f_body, max_w=wrap_w)
        box_w = min(max(max_w, min_hdr_w), max(min_hdr_w, max(sw, vw) + pad_x * 2.0 + 6.0, 300.0))
        if self._diag_w is not None:
            box_w = max(box_w, 460.0)
        wrap_w = box_w - pad_x * 2.0 - 4.0
        _, sw, sh, _ = self._mk_layout(cr, short, f_body, max_w=wrap_w)
        if verbose:
            _, vw, vh, _ = self._mk_layout(cr, verbose, f_body, max_w=wrap_w)

        short_y = pad_y + hh + 4.5
        verb_y = short_y + sh + 7.0
        text_h = (verb_y + vh if verbose else short_y + sh) + (4.0 if diag_h > 0.0 else pad_y)
        box_h = text_h + (diag_h + pad_y if diag_h > 0.0 else 0.0)
        self._geom = {
            "box_w": box_w, "box_h": box_h, "text_h": text_h,
            "pad_x": pad_x, "pad_y": pad_y, "bot_pad": pad_y,
            "top_y": 0.0, "hh": hh, "short_y": short_y, "verb_y": verb_y,
            "wrap_w": wrap_w, "f_hdr": f_hdr, "f_btn": f_btn, "f_body": f_body,
            "btns": btn_items, "sep_y": 0.0,
        }

    def _trace_callout_path(self, cr, x: float, y: float, w: float, h: float, rad: float) -> None:
        r = min(float(rad), max(0.0, w * 0.5), max(0.0, h * 0.5))
        tip = 5.5
        if self._tip_side == "top":
            tx = max(x + 14.0, min(x + w - 14.0, x + self._tip_offset))
            cr.new_sub_path()
            cr.move_to(x + r, y)
            cr.line_to(tx - tip, y)
            cr.line_to(tx, y - tip)
            cr.line_to(tx + tip, y)
            cr.line_to(x + w - r, y)
            cr.arc(x + w - r, y + r, r, -math.pi * 0.5, 0.0)
            cr.arc(x + w - r, y + h - r, r, 0.0, math.pi * 0.5)
            cr.arc(x + r, y + h - r, r, math.pi * 0.5, math.pi)
            cr.arc(x + r, y + r, r, math.pi, math.pi * 1.5)
            cr.close_path()
        elif self._tip_side == "bottom":
            tx = max(x + 14.0, min(x + w - 14.0, x + self._tip_offset))
            cr.new_sub_path()
            cr.arc(x + r, y + r, r, math.pi, math.pi * 1.5)
            cr.arc(x + w - r, y + r, r, -math.pi * 0.5, 0.0)
            cr.arc(x + w - r, y + h - r, r, 0.0, math.pi * 0.5)
            cr.line_to(tx + tip, y + h)
            cr.line_to(tx, y + h + tip)
            cr.line_to(tx - tip, y + h)
            cr.arc(x + r, y + h - r, r, math.pi * 0.5, math.pi)
            cr.close_path()
        else:
            ty = max(y + 14.0, min(y + h - 14.0, y + self._tip_offset))
            cr.new_sub_path()
            cr.arc(x + r, y + r, r, math.pi, math.pi * 1.5)
            cr.arc(x + w - r, y + r, r, -math.pi * 0.5, 0.0)
            cr.arc(x + w - r, y + h - r, r, 0.0, math.pi * 0.5)
            cr.arc(x + r, y + h - r, r, math.pi * 0.5, math.pi)
            cr.line_to(x, ty + tip)
            cr.line_to(x - tip, ty)
            cr.line_to(x, ty - tip)
            cr.close_path()

    def _on_draw_card(self, _w, cr) -> bool:
        pal = get_semantic_palette()
        bg_rgb = _norm_rgb(get_surface_bg_rgb())
        op = get_hover_help_opacity()
        sh_a = 0.62 * max(0.25, op)
        bs = _active_box_style()
        g = self._geom
        x0 = float(self._SH_PAD)
        y0 = float(self._SH_PAD)
        bw = float(g["box_w"])
        bh = float(g["box_h"])
        st = self._style
        rad = _style_radius(bs, st)

        if st == "classic":
            top_y = y0 + float(g["top_y"])
            fh = bh - float(g["top_y"])
            cr.save()
            _rounded_rect(cr, x0 - 1.5, top_y + 2.5, bw + 3.0, fh + 2.0, rad + 1.0 if rad > 0 else 0.0)
            cr.set_source_rgba(0.0, 0.0, 0.0, sh_a)
            cr.fill()
            cr.restore()
            _rounded_rect(cr, x0 + 0.5, top_y + 0.5, bw - 1.0, fh - 1.0, rad)
            cr.set_source_rgba(*bg_rgb, op)
            cr.fill()
            _stroke_box_frame(
                cr,
                lambda: _rounded_rect(cr, x0 + 0.5, top_y + 0.5, bw - 1.0, fh - 1.0, rad),
                x0 + 0.5, top_y + 0.5, bw - 1.0, fh - 1.0, rad, bs, pal["structure"], 0.98,
            )
            return False

        if st == "cyberpunk":
            cr.save()
            self._trace_callout_path(cr, x0, y0 + 2.5, bw, bh, rad)
            cr.set_source_rgba(0.0, 0.0, 0.0, sh_a)
            cr.fill()
            cr.restore()
            self._trace_callout_path(cr, x0 + 0.5, y0 + 0.5, bw - 1.0, bh - 1.0, rad)
            cr.set_source_rgba(*bg_rgb, op)
            cr.fill()
            _stroke_box_frame(
                cr,
                lambda: self._trace_callout_path(cr, x0 + 0.5, y0 + 0.5, bw - 1.0, bh - 1.0, rad),
                x0 + 0.5, y0 + 0.5, bw - 1.0, bh - 1.0, rad, bs, pal["intent"], 0.78,
            )
            sep_y = math.floor(y0 + float(g["sep_y"])) + 0.5
            cr.set_source_rgba(*_hex_rgba(pal["structure"], 0.65))
            cr.set_line_width(0.8)
            cr.move_to(x0 + float(g["pad_x"]), sep_y)
            cr.line_to(x0 + bw - float(g["pad_x"]), sep_y)
            cr.stroke()
            return False

        cr.save()
        _rounded_rect(cr, x0 - 1.5, y0 + 2.5, bw + 3.0, bh + 2.0, rad + 1.0 if rad > 0 else 0.0)
        cr.set_source_rgba(0.0, 0.0, 0.0, sh_a)
        cr.fill()
        cr.restore()

        _rounded_rect(cr, x0 + 0.5, y0 + 0.5, bw - 1.0, bh - 1.0, rad)
        cr.set_source_rgba(*bg_rgb, op)
        cr.fill()
        _stroke_box_frame(
            cr,
            lambda: _rounded_rect(cr, x0 + 0.5, y0 + 0.5, bw - 1.0, bh - 1.0, rad),
            x0 + 0.5, y0 + 0.5, bw - 1.0, bh - 1.0, rad, bs, pal["structure"], 0.95,
        )

        if st == "tactical":
            cr.save()
            _rounded_rect(cr, x0 + 1.0, y0 + 1.0, bw - 2.0, bh - 2.0, max(0.0, rad - 0.5))
            cr.clip()
            cr.rectangle(x0 + 1.0, y0 + 1.0, 2.5, bh - 2.0)
            cr.set_source_rgba(*_hex_rgba(pal["intent"], 0.96))
            cr.fill()
            cr.restore()
        return False

    def _on_draw_text(self, _w, cr) -> bool:
        pal = get_semantic_palette()
        bg_rgb = _norm_rgb(get_surface_bg_rgb())
        op = get_hover_help_opacity()
        g = self._geom
        st = self._style
        bw = float(g["box_w"])
        pad_x = float(g["pad_x"])
        pad_y = float(g["pad_y"])
        title = str(self._entry.get("title") or "")
        short = str(self._entry.get("short") or "")
        verbose = str(self._entry.get("verbose") or "") if self._expanded else ""

        self._btn_rects = []
        bx_right = bw - (11.0 if st == "classic" else pad_x)
        by_top = 0.0 if st == "classic" else pad_y

        btn_draws = []
        for lbl, uri, tone, btn_w, btn_h in reversed(g["btns"]):
            bx0 = bx_right - btn_w
            is_hov = (uri == self._hovered_btn_uri)
            self._btn_rects.append((bx0, by_top, btn_w, btn_h, uri, tone))
            if is_hov:
                col = pal["focus"] if tone == "neutral" else pal["interrupt"]
            else:
                col = pal["schema"] if tone == "neutral" else pal["interrupt_rest"]
            bm = (
                f"<span color='{pal['focus'] if is_hov else pal['structure']}'>[</span>"
                f"<span color='{col}'>{html.escape(lbl)}</span>"
                f"<span color='{pal['focus'] if is_hov else pal['structure']}'>]</span>"
            )
            lay_b, _, _, _ = self._mk_layout(cr, bm, g["f_btn"], markup=True)
            btn_draws.append((bx0, by_top + (0.5 if st != "classic" else 0.0), btn_w, btn_h, lay_b))
            bx_right = bx0 - 6.0

        if st == "classic":
            bl, br = _active_title_brackets()
            hm = (
                f"<span color='{pal['structure']}'>{html.escape(bl)}</span>"
                f"<span color='{pal['intent']}'>\uf05a</span> "
                f"<span color='{pal['focus']}'>{html.escape(title)}</span>"
                f"<span color='{pal['structure']}'>{html.escape(br)}</span>"
            )
            lay_h, hw, hh, _ = self._mk_layout(cr, hm, g["f_hdr"], markup=True)
            cr.rectangle(10.0, 0.0, hw + 6.0, hh + 1.0)
            cr.set_source_rgba(*bg_rgb, max(0.88, op))
            cr.fill()
            cr.move_to(13.0, 0.0)
            PangoCairo.show_layout(cr, lay_h)
            for bx0, by0, btn_w, btn_h, lay_b in btn_draws:
                cr.rectangle(bx0 - 3.0, 0.0, btn_w + 6.0, hh + 1.0)
                cr.set_source_rgba(*bg_rgb, max(0.88, op))
                cr.fill()
                cr.move_to(bx0, by0)
                PangoCairo.show_layout(cr, lay_b)
        elif st == "clean":
            hm = f"<span color='{pal['intent']}'>\uf05a {html.escape(title)}</span>"
            lay_h, _, _, _ = self._mk_layout(cr, hm, g["f_hdr"], markup=True)
            cr.move_to(pad_x, pad_y)
            PangoCairo.show_layout(cr, lay_h)
            for bx0, by0, _w, _h, lay_b in btn_draws:
                cr.move_to(bx0, by0)
                PangoCairo.show_layout(cr, lay_b)
        elif st == "cyberpunk":
            lay_h, _, _, _ = self._mk_layout(cr, f"HELP // {title.upper()}", g["f_hdr"])
            cr.set_source_rgba(*_hex_rgba(pal["intent"], 0.96))
            cr.move_to(pad_x, pad_y)
            PangoCairo.show_layout(cr, lay_h)
            for bx0, by0, _w, _h, lay_b in btn_draws:
                cr.move_to(bx0, by0)
                PangoCairo.show_layout(cr, lay_b)
        else:
            lay_h, _, _, _ = self._mk_layout(cr, title, g["f_hdr"])
            cr.set_source_rgba(*_hex_rgba(pal["focus"], 0.98))
            cr.move_to(pad_x + 2.0, pad_y)
            PangoCairo.show_layout(cr, lay_h)
            for bx0, by0, _w, _h, lay_b in btn_draws:
                cr.move_to(bx0, by0)
                PangoCairo.show_layout(cr, lay_b)

        tx_x = pad_x + (2.0 if st in ("tactical", "classic") else 0.0)
        lay_s, _, _, _ = self._mk_layout(cr, short, g["f_body"], max_w=float(g["wrap_w"]))
        s_col = pal["focus"] if self._expanded else pal["focus_val"]
        cr.set_source_rgba(*_hex_rgba(s_col, 0.94))
        cr.move_to(tx_x, float(g["short_y"]))
        PangoCairo.show_layout(cr, lay_s)

        if verbose:
            lay_v, _, _, _ = self._mk_layout(cr, verbose, g["f_body"], max_w=float(g["wrap_w"]))
            cr.set_source_rgba(*_hex_rgba(pal["focus_val"], 0.92))
            cr.move_to(tx_x, float(g["verb_y"]))
            PangoCairo.show_layout(cr, lay_v)
        return False


class StyledNotifCard(_OverlayCardBase):
    """Composable Cairo toast/notification card supporting Tactical/Classic/Clean/Cyberpunk + box_style."""

    def __init__(
        self,
        *,
        style: str = "tactical",
        title: str,
        body: str,
        subtitle: str = "",
        icon: str = "\uf0f3",
        accent_hex: str = "",
        badge_text: str = "",
        badge_fg: str = "",
        badge_bg: str = "",
        meta_right: str = "",
        close_uri: str = "",
        body_click_uri: str = "",
        header_prefix: str = "NOTIFY",
        opacity: float = 0.95,
        max_width_px: int = 460,
        on_link=None,
    ) -> None:
        super().__init__(on_link=on_link)
        pal = get_semantic_palette()
        self._style = _norm_notif_style(style, "tactical")
        self._title = str(title or "").strip()
        self._subtitle = str(subtitle or "").strip()
        self._body = str(body or "").strip()
        self._icon = str(icon or "\uf0f3")
        self._accent = str(accent_hex or pal["intent"])
        self._badge_txt = str(badge_text or "").strip()
        self._badge_fg = str(badge_fg or self._accent)
        self._badge_bg = str(badge_bg or pal["structure_bg"])
        self._meta_right = str(meta_right or "").strip()
        self._close_uri = str(close_uri or "").strip()
        self._body_click_uri = str(body_click_uri or "").strip()
        self._header_prefix = str(header_prefix or "NOTIFY").strip().upper()
        self._opacity = max(0.0, min(1.0, float(opacity)))
        self._max_w = max(250, int(max_width_px))

        self._btn_defs: list[tuple[str, str, str]] = []
        if self._close_uri:
            self._btn_defs.append(("x", self._close_uri, "danger_soft"))

        self.connect("draw", self._on_draw_card)
        self._measure_layout()

        self._text_da = Gtk.DrawingArea()
        self._text_da.set_size_request(int(math.ceil(self._geom["box_w"])), int(math.ceil(self._geom["box_h"])))
        self._text_da.set_margin_start(self._SH_PAD)
        self._text_da.set_margin_end(self._SH_PAD)
        self._text_da.set_margin_top(self._SH_PAD)
        self._text_da.set_margin_bottom(self._SH_PAD)
        self._text_da.set_can_focus(False)
        self._text_da.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
            | Gdk.EventMask.ENTER_NOTIFY_MASK
            | Gdk.EventMask.LEAVE_NOTIFY_MASK
        )
        self._text_da.connect("draw", self._on_draw_text)
        self._text_da.connect("motion-notify-event", self._on_text_motion)
        self._text_da.connect("leave-notify-event", self._on_text_leave)
        self._text_da.connect("button-press-event", self._on_text_press)
        self.add(self._text_da)

        self.card_width_px = int(math.ceil(self._geom["box_w"])) + self._SH_PAD * 2
        self.card_height_px = int(math.ceil(self._geom["box_h"])) + self._SH_PAD * 2
        self.set_size_request(self.card_width_px, self.card_height_px)

    def get_hover_widgets(self) -> list[Gtk.Widget]:
        return [self, self._text_da]

    def _measure_layout(self) -> None:
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
        cr = cairo.Context(surf)
        st = self._style
        bl, br = _active_title_brackets()

        f_btn = self._font()
        btn_items = []
        right_chrome_w = 0.0
        for lbl, uri, tone in self._btn_defs:
            _, bw, bh, _ = self._mk_layout(cr, f"[{lbl}]", f_btn)
            btn_items.append((lbl, uri, tone, bw, bh))
            right_chrome_w += bw + 6.0

        f_meta = self._font()
        badge_w, badge_h = 0.0, 0.0
        if self._badge_txt:
            _, tw_b, th_b, _ = self._mk_layout(cr, self._badge_txt, f_meta)
            badge_w = tw_b + 10.0
            badge_h = th_b + 2.0
            right_chrome_w += badge_w + 6.0

        meta_w = 0.0
        if self._meta_right:
            _, meta_w, _mh, _ = self._mk_layout(cr, self._meta_right, f_meta)
            right_chrome_w += meta_w + 6.0

        f_hdr = self._font()
        f_sub = self._font()
        f_body = self._font()

        if st == "classic":
            hdr_plain = f"{bl}{self._icon} {self._title}{br}"
        elif st == "cyberpunk":
            hdr_plain = f"{self._header_prefix} // {self._title.upper()}"
        else:
            hdr_plain = f"{self._icon} {self._title}"

        _, hw, hh, _ = self._mk_layout(cr, hdr_plain, f_hdr)
        pad_x = 12.0 if st in ("tactical", "classic") else 11.0
        pad_y = 0.0 if st == "classic" else (6.0 if st == "cyberpunk" else 7.0)
        extra_left = 2.0 if st in ("tactical", "classic") else 0.0
        min_hdr_w = hw + right_chrome_w + pad_x * 2.0 + extra_left + 16.0
        max_w = float(max(self._max_w, min_hdr_w))
        wrap_w = max(210.0, max_w - pad_x * 2.0 - extra_left)

        sub_w, sub_h, ink_sub = 0.0, 0.0, None
        if self._subtitle:
            _, sub_w, sub_h, ink_sub = self._mk_layout(cr, self._subtitle, f_sub, max_w=wrap_w)
        bw_t, bh_t, ink_b = 0.0, 0.0, None
        if self._body:
            _, bw_t, bh_t, ink_b = self._mk_layout(cr, self._body, f_body, max_w=wrap_w)

        box_w = max(min_hdr_w, min(max_w, max(sub_w, bw_t) + pad_x * 2.0 + extra_left + 6.0, 330.0 if self._subtitle else 300.0))
        if self._subtitle or self._body:
            box_w = max(box_w, min(max_w, max(sub_w, bw_t) + pad_x * 2.0 + extra_left + 6.0))
        wrap_w = box_w - pad_x * 2.0 - extra_left
        if self._subtitle:
            _, sub_w, sub_h, ink_sub = self._mk_layout(cr, self._subtitle, f_sub, max_w=wrap_w)
        if self._body:
            _, bw_t, bh_t, ink_b = self._mk_layout(cr, self._body, f_body, max_w=wrap_w)

        first_ink = ink_sub if (self._subtitle and ink_sub is not None) else ink_b
        first_ink_y = float(first_ink.y) if (first_ink is not None and first_ink.height > 0) else 2.0

        top_y = 0.0
        sep_y = 0.0
        if st == "classic":
            top_y = hh * 0.5
            sub_y = max(hh + 4.0, top_y + 10.0 - first_ink_y)
            top_ink_gap = (sub_y + first_ink_y) - top_y
            body_y = (sub_y + sub_h + 4.0) if self._subtitle else sub_y
            last_y = body_y if self._body else sub_y
            last_ink = ink_b if (self._body and ink_b is not None) else ink_sub
            if last_ink is not None and last_ink.height > 0:
                end_ink_y = last_y + float(last_ink.y + last_ink.height)
            else:
                end_ink_y = (body_y + bh_t) if self._body else (sub_y + sub_h)
            box_h = end_ink_y + max(8.0, top_ink_gap)
        elif st == "cyberpunk":
            sep_y = pad_y + max(hh, badge_h) + 4.0
            sub_y = sep_y + 4.5
            body_y = (sub_y + sub_h + 4.0) if self._subtitle else sub_y
            end_y = (body_y + bh_t) if self._body else (sub_y + sub_h)
            box_h = end_y + pad_y
        elif st == "clean":
            sub_y = pad_y + max(hh, badge_h) + 7.0
            body_y = (sub_y + sub_h + 4.0) if self._subtitle else sub_y
            end_y = (body_y + bh_t) if self._body else (sub_y + sub_h)
            box_h = end_y + pad_y
        else:  # tactical
            sub_y = pad_y + max(hh, badge_h) + 4.5
            body_y = (sub_y + sub_h + 4.0) if self._subtitle else sub_y
            end_y = (body_y + bh_t) if self._body else (sub_y + sub_h)
            box_h = end_y + pad_y

        self._geom = {
            "box_w": box_w, "box_h": box_h,
            "pad_x": pad_x, "pad_y": pad_y, "extra_left": extra_left,
            "top_y": top_y, "sep_y": sep_y, "hh": hh,
            "sub_y": sub_y, "body_y": body_y, "wrap_w": wrap_w,
            "f_hdr": f_hdr, "f_sub": f_sub, "f_body": f_body, "f_btn": f_btn, "f_meta": f_meta,
            "btns": btn_items, "badge_w": badge_w, "badge_h": badge_h, "meta_w": meta_w,
        }

    def _on_draw_card(self, _w, cr) -> bool:
        pal = get_semantic_palette()
        bg_rgb = _norm_rgb(get_surface_bg_rgb())
        op = self._opacity
        sh_a = 0.54 * max(0.22, op)
        bs = _active_box_style()
        g = self._geom
        x0 = float(self._SH_PAD)
        y0 = float(self._SH_PAD)
        bw = float(g["box_w"])
        bh = float(g["box_h"])
        st = self._style
        rad = _style_radius(bs, st)

        ry = (y0 + float(g["top_y"])) if st == "classic" else y0
        rh = bh - (float(g["top_y"]) if st == "classic" else 0.0)
        brd_col = self._accent if st == "cyberpunk" else pal["structure"]
        brd_alpha = 0.80 if st == "cyberpunk" else 0.96

        cr.save()
        _rounded_rect(cr, x0 - 1.5, ry + 2.5, bw + 3.0, rh + 2.0, rad + 1.0 if rad > 0 else 0.0)
        cr.set_source_rgba(0.0, 0.0, 0.0, sh_a)
        cr.fill()
        cr.restore()

        _rounded_rect(cr, x0 + 0.5, ry + 0.5, bw - 1.0, rh - 1.0, rad)
        cr.set_source_rgba(*bg_rgb, op)
        cr.fill()

        _stroke_box_frame(
            cr,
            lambda: _rounded_rect(cr, x0 + 0.5, ry + 0.5, bw - 1.0, rh - 1.0, rad),
            x0 + 0.5, ry + 0.5, bw - 1.0, rh - 1.0, rad, bs, brd_col, brd_alpha,
        )

        if st == "tactical":
            cr.save()
            _rounded_rect(cr, x0 + 1.0, y0 + 1.0, bw - 2.0, bh - 2.0, max(0.0, rad - 0.5))
            cr.clip()
            cr.rectangle(x0 + 1.0, y0 + 1.0, 2.8, bh - 2.0)
            cr.set_source_rgba(*_hex_rgba(self._accent, 0.98))
            cr.fill()
            cr.restore()
        elif st == "cyberpunk":
            sep_y = math.floor(y0 + float(g["sep_y"])) + 0.5
            cr.save()
            cr.set_source_rgba(*_hex_rgba(pal["structure"], 0.65))
            cr.set_line_width(0.8)
            cr.move_to(x0 + float(g["pad_x"]), sep_y)
            cr.line_to(x0 + bw - float(g["pad_x"]), sep_y)
            cr.stroke()
            cr.restore()
        return False

    def _on_draw_text(self, _w, cr) -> bool:
        pal = get_semantic_palette()
        bg_rgb = _norm_rgb(get_surface_bg_rgb())
        g = self._geom
        st = self._style
        bw = float(g["box_w"])
        pad_x = float(g["pad_x"])
        pad_y = float(g["pad_y"])
        extra_left = float(g["extra_left"])

        self._btn_rects = []
        bx_right = bw - (11.0 if st == "classic" else pad_x)
        by_top = 0.0 if st == "classic" else pad_y

        # Reserve & render right-side chrome: [x] <- time <- [BADGE]
        chrome_boxes = []
        for lbl, uri, tone, btn_w, btn_h in reversed(g["btns"]):
            bx0 = bx_right - btn_w
            is_hov = (uri == self._hovered_btn_uri)
            self._btn_rects.append((bx0, by_top, btn_w, btn_h, uri, tone))
            col = pal["interrupt"] if is_hov else pal["interrupt_rest"]
            bm = (
                f"<span color='{pal['focus'] if is_hov else pal['structure']}'>[</span>"
                f"<span color='{col}'>{html.escape(lbl)}</span>"
                f"<span color='{pal['focus'] if is_hov else pal['structure']}'>]</span>"
            )
            lay_b, _, _, _ = self._mk_layout(cr, bm, g["f_btn"], markup=True)
            chrome_boxes.append(("btn", bx0, by_top + (0.5 if st != "classic" else 0.0), btn_w, btn_h, lay_b))
            bx_right = bx0 - 6.0

        if self._meta_right:
            lay_m, mw, mh, _ = self._mk_layout(cr, self._meta_right, g["f_meta"])
            mx0 = bx_right - mw
            chrome_boxes.append(("meta", mx0, by_top + 1.0, mw, mh, lay_m))
            bx_right = mx0 - 6.0

        if self._badge_txt:
            bg_w = float(g["badge_w"])
            bg_h = float(g["badge_h"])
            bg_x0 = bx_right - bg_w
            chrome_boxes.append(("badge", bg_x0, by_top, bg_w, bg_h, None))

        if st == "classic":
            bl, br = _active_title_brackets()
            hm = (
                f"<span color='{pal['structure']}'>{html.escape(bl)}</span>"
                f"<span color='{self._accent}'>{html.escape(self._icon)}</span> "
                f"<span color='{pal['focus']}'>{html.escape(self._title)}</span>"
                f"<span color='{pal['structure']}'>{html.escape(br)}</span>"
            )
            lay_h, hw, hh, _ = self._mk_layout(cr, hm, g["f_hdr"], markup=True)
            cr.rectangle(10.0, 0.0, hw + 6.0, hh + 1.0)
            cr.set_source_rgba(*bg_rgb, max(0.88, self._opacity))
            cr.fill()
            cr.move_to(13.0, 0.0)
            PangoCairo.show_layout(cr, lay_h)
            for kind, cx, cy, cw, ch, obj in chrome_boxes:
                cr.rectangle(cx - 3.0, 0.0, cw + 6.0, max(hh, ch) + 1.0)
                cr.set_source_rgba(*bg_rgb, max(0.88, self._opacity))
                cr.fill()
        else:
            if st == "cyberpunk":
                htext = f"{self._header_prefix} // {self._title.upper()}"
                lay_h, _, _, _ = self._mk_layout(cr, htext, g["f_hdr"])
                cr.set_source_rgba(*_hex_rgba(self._accent, 0.96))
                cr.move_to(pad_x + extra_left, pad_y)
                PangoCairo.show_layout(cr, lay_h)
            else:
                col_t = self._accent if st == "clean" else pal["focus"]
                hm = (
                    f"<span color='{self._accent}'>{html.escape(self._icon)}</span> "
                    f"<span color='{col_t}'>{html.escape(self._title)}</span>"
                )
                lay_h, _, _, _ = self._mk_layout(cr, hm, g["f_hdr"], markup=True)
                cr.move_to(pad_x + extra_left, pad_y)
                PangoCairo.show_layout(cr, lay_h)

        for kind, cx, cy, cw, ch, obj in chrome_boxes:
            if kind == "badge":
                _rounded_rect(cr, cx, cy, cw, ch, 2.5)
                cr.set_source_rgba(*_hex_rgba(self._badge_bg, 0.94))
                cr.fill_preserve()
                cr.set_source_rgba(*_hex_rgba(self._badge_fg, 0.65))
                cr.set_line_width(0.8)
                cr.stroke()
                lay_bg, _, _, _ = self._mk_layout(cr, self._badge_txt, g["f_meta"])
                cr.set_source_rgba(*_hex_rgba(self._badge_fg, 0.98))
                cr.move_to(cx + 5.0, cy + 1.0)
                PangoCairo.show_layout(cr, lay_bg)
            elif kind == "meta":
                cr.set_source_rgba(*_hex_rgba(pal["schema"], 0.90))
                cr.move_to(cx, cy)
                PangoCairo.show_layout(cr, obj)
            else:
                cr.move_to(cx, cy)
                PangoCairo.show_layout(cr, obj)

        tx_x = pad_x + extra_left
        if self._subtitle:
            lay_sub, _, _, _ = self._mk_layout(cr, self._subtitle, g["f_sub"], max_w=float(g["wrap_w"]))
            cr.set_source_rgba(*_hex_rgba(pal["focus"] if self._body_click_uri else pal["focus"], 0.98))
            cr.move_to(tx_x, float(g["sub_y"]))
            PangoCairo.show_layout(cr, lay_sub)
        if self._body:
            lay_b, _, _, _ = self._mk_layout(cr, self._body, g["f_body"], max_w=float(g["wrap_w"]))
            b_col = self._accent if (not self._subtitle and self._accent in (pal["attention"], pal["fault"])) else pal["focus_val"]
            cr.set_source_rgba(*_hex_rgba(b_col, 0.94))
            cr.move_to(tx_x, float(g["body_y"]))
            PangoCairo.show_layout(cr, lay_b)
        return False
