#!/usr/bin/env python3
"""Reusable Cairo + libvterm Embedded Terminal Widget for NC-UI (`ui/embedded_tui.py`)."""

import math
import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, GLib, Gtk, Pango, PangoCairo  # noqa: E402
from core.config import THEME, get_layout_margins  # noqa: E402
from core.i18n import t  # noqa: E402
from ui.canvas import Canvas  # noqa: E402
from ui.kit import ActionBtn, Badge, Box, Divider, FoldIconBtn, StateBadge, Txt, get_semantic_palette  # noqa: E402
from ui.widgets import AsciiCard, _NO_LIGA_SPAN_OPEN, _extract_and_strip_bg_spans  # noqa: E402
from panels.cc_engine_resources import (  # noqa: E402
    _tui_copy_text,
    _tui_read_clipboard,
    close_embedded_tui,
    get_active_tui,
    get_or_start_tui_session,
    get_tui_session,
    set_active_tui,
    start_tui_session,
)

TUI_VERT_CHROME_PX = 22
TUI_FLUSH_VERT_CHROME_PX = 14
PANEL_TERM_ID = "panel_term"
_PANEL_TERM_META: dict = {}


def _hex_to_rgb_f(hex_str: str, fallback: tuple = (0.0, 0.94, 1.0)) -> tuple:
    h = str(hex_str or "").strip().lstrip("#")
    if len(h) == 6:
        try:
            return (int(h[0:2], 16) / 255.0, int(h[2:4], 16) / 255.0, int(h[4:6], 16) / 255.0)
        except ValueError:
            pass
    return fallback


def _rounded_rect(cr, x: float, y: float, w: float, h: float, r: float) -> None:
    rad = max(0.0, min(r, w / 2.0, h / 2.0))
    cr.new_sub_path()
    cr.arc(x + w - rad, y + rad, rad, -math.pi / 2, 0)
    cr.arc(x + w - rad, y + h - rad, rad, 0, math.pi / 2)
    cr.arc(x + rad, y + h - rad, rad, math.pi / 2, math.pi)
    cr.arc(x + rad, y + rad, rad, math.pi, 3 * math.pi / 2)
    cr.close_path()


def _compute_tui_grid_metrics(target_w: int, char_w: float, font_size_pt: float = 9.0, flush: bool = False):
    typo = THEME["typography"]
    ff = str(typo.get("font_family", "Courier") or "Courier")
    pt = max(7.0, float(font_size_pt or 9.0))
    no_liga = Pango.AttrList()
    no_liga.insert(Pango.AttrFontFeatures.new("liga=0, clig=0, dlig=0, hlig=0"))
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
    cr = cairo.Context(surf)
    lay = PangoCairo.create_layout(cr)
    lay.set_attributes(no_liga)
    desc_reg = Pango.FontDescription.from_string(
        f"{ff}, Font Awesome 6 Free, Symbols Nerd Font, monospace {pt:.1f}"
    )
    desc_bold = Pango.FontDescription.from_string(
        f"{ff}, Font Awesome 6 Free, Symbols Nerd Font, monospace bold {pt:.1f}"
    )
    lay.set_font_description(desc_reg)
    lay.set_text("0" * 40, -1)
    _, log_r = lay.get_pixel_extents()
    cw = max(5.0, float(log_r.width) / 40.0)
    ch = max(11.0, float(log_r.height))
    if flush:
        inset_each = (char_w * 0.5) + max(10.0, char_w * 1.25)
    else:
        inset_each = (char_w * 2.0) + max(6.0, char_w * 0.95)
    avail_w = max(240.0, float(target_w) - 2.0 * inset_each)
    min_cols = 72 if pt >= 10.5 else 84
    cols = max(min_cols, int(avail_w // cw))
    return cols, cw, ch, desc_reg, desc_bold


class EmbeddedTuiWidget(Gtk.EventBox):
    def __init__(
        self,
        tui_id: str,
        session,
        map_width: int,
        char_w: float,
        target_height_px: int = 0,
        mouse_reporting: bool = True,
        show_cursor: bool = False,
        font_size_pt: float = 9.0,
        flush: bool = False,
    ):
        super().__init__()
        self.set_visible_window(False)
        self.set_above_child(True)
        self.tui_id = str(tui_id)
        self.session = session
        self.map_width = max(380, int(map_width))
        self.char_w = max(5.0, float(char_w))
        self.mouse_reporting = bool(mouse_reporting)
        self.show_cursor = bool(show_cursor)
        self.font_size_pt = max(7.0, float(font_size_pt or 9.0))
        self.flush = bool(flush)
        self._no_liga_attrs = Pango.AttrList()
        self._no_liga_attrs.insert(Pango.AttrFontFeatures.new("liga=0, clig=0, dlig=0, hlig=0"))
        self._metrics_sig = None
        self._cw = 7.0
        self._ch = 13.0
        self._desc_reg = None
        self._desc_bold = None
        self._target_height_px = int(target_height_px or 0)
        self._update_metrics(self.map_width)
        vert_chrome = TUI_FLUSH_VERT_CHROME_PX if self.flush else TUI_VERT_CHROME_PX
        self._ch_draw = (
            max(float(self._ch), (float(target_height_px) - vert_chrome) / float(max(1, self.session.rows)))
            if target_height_px > 0
            else float(self._ch)
        )
        self._last_geom = (
            (self.char_w * 0.5 + max(10.0, self.char_w * 1.25)) if self.flush else (self.char_w * 2.0 + max(6.0, self.char_w * 0.95)),
            7.0 if self.flush else 10.5,
            self._cw,
            self._ch_draw,
        )

        total_h = max(int(round(vert_chrome + self.session.rows * self._ch)), int(target_height_px))
        self.da = Gtk.DrawingArea()
        self.da.set_size_request(self.map_width, total_h)
        self.set_size_request(self.map_width, total_h)
        self.add(self.da)

        self.set_can_focus(True)
        self.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
            | Gdk.EventMask.ENTER_NOTIFY_MASK
            | Gdk.EventMask.LEAVE_NOTIFY_MASK
            | Gdk.EventMask.KEY_PRESS_MASK
            | Gdk.EventMask.FOCUS_CHANGE_MASK
            | Gdk.EventMask.SCROLL_MASK
            | Gdk.EventMask.SMOOTH_SCROLL_MASK
        )
        self.connect("map", self._on_map)
        self.connect("focus-out-event", self._on_focus_out)
        self.connect("enter-notify-event", self._on_enter_leave)
        self.connect("leave-notify-event", self._on_enter_leave)
        self.connect("button-press-event", self._on_button)
        self.connect("button-release-event", self._on_button)
        self.connect("motion-notify-event", self._on_motion)
        self.connect("scroll-event", self._on_scroll)
        self.da.connect("draw", self._on_draw)
        self._redraw_cb = lambda: self.da.queue_draw()
        self.session.on_redraw = self._redraw_cb
        self.connect("destroy", self._on_destroy)

    def _on_map(self, *_args) -> None:
        if get_active_tui() != self.tui_id:
            return

        def _claim() -> bool:
            if get_active_tui() == self.tui_id and self.get_mapped():
                self.grab_focus()
            return False

        GLib.idle_add(_claim)

    def _on_focus_out(self, *_args) -> bool:
        if get_active_tui() == self.tui_id:
            set_active_tui("")
        return False

    def sync_from(self, other: "EmbeddedTuiWidget") -> None:
        old_sess = self.session
        new_sess = other.session
        if old_sess is not new_sess and old_sess is not None:
            if getattr(old_sess, "on_redraw", None) is self._redraw_cb:
                old_sess.on_redraw = None
        self.tui_id = other.tui_id
        self.session = new_sess
        self.mouse_reporting = other.mouse_reporting
        self.show_cursor = other.show_cursor
        self.font_size_pt = other.font_size_pt
        self.flush = other.flush
        self.map_width = other.map_width
        self.char_w = other.char_w
        self._ch_draw = other._ch_draw
        self._target_height_px = other._target_height_px
        self._metrics_sig = None
        self._update_metrics(self.map_width)
        vert_chrome = TUI_FLUSH_VERT_CHROME_PX if self.flush else TUI_VERT_CHROME_PX
        total_h = max(
            int(round(vert_chrome + self.session.rows * self._ch)),
            int(self._target_height_px),
        )
        if self.get_size_request() != (self.map_width, total_h):
            self.da.set_size_request(self.map_width, total_h)
            self.set_size_request(self.map_width, total_h)
        if self.session is not None:
            self.session.on_redraw = self._redraw_cb
        if get_active_tui() == self.tui_id and self.get_mapped():
            self.grab_focus()
        self.da.queue_draw()

    def _resolve_box_rails(self, alloc_w: float) -> tuple[float, float]:
        ref_lbl = getattr(self, "_ref_label", None)
        layout = ref_lbl.get_layout() if ref_lbl is not None else None
        if layout is not None:
            text = layout.get_text() or ""
            first_nl = text.find("\n")
            line0_str = text[:first_nl] if first_nl >= 0 else text
            if len(line0_str) >= 2:
                p_first = layout.index_to_pos(0)
                b_last = len(line0_str[:-1].encode("utf-8"))
                p_last = layout.index_to_pos(b_last)
                x_left = (p_first.x + p_first.width * 0.5) / Pango.SCALE
                x_right = max(x_left + 2.0, (p_last.x + p_last.width * 0.5) / Pango.SCALE)
                return x_left, x_right
        x_left = self.char_w * 0.5
        x_right = max(x_left + 2.0, alloc_w - self.char_w * 0.5)
        return x_left, x_right

    def _update_metrics(self, target_w: int) -> None:
        typo = THEME["typography"]
        ff = str(typo.get("font_family", "Courier") or "Courier")
        sig = (ff, int(target_w), round(self.font_size_pt, 2), bool(self.flush))
        if self._metrics_sig == sig and self._desc_reg is not None:
            return
        self._metrics_sig = sig
        cols, cw, ch, desc_reg, desc_bold = _compute_tui_grid_metrics(
            target_w, self.char_w, font_size_pt=self.font_size_pt, flush=self.flush
        )
        self._cw = cw
        self._ch = ch
        self._desc_reg = desc_reg
        self._desc_bold = desc_bold
        if self.session.cols != cols:
            self.session.resize(self.session.rows, cols)

    def _on_destroy(self, *_args) -> None:
        if getattr(self.session, "on_redraw", None) is self._redraw_cb:
            self.session.on_redraw = None

    def _xy_to_cell(self, ex: float, ey: float):
        x0, y0, cw, ch = self._last_geom
        cols, rows = self.session.cols, self.session.rows
        if x0 <= ex <= (x0 + cols * cw) and y0 <= ey <= (y0 + rows * ch):
            c = max(1, min(cols, int((ex - x0) / cw) + 1))
            r = max(1, min(rows, int((ey - y0) / ch) + 1))
            return c, r
        return None

    def _xy_to_view_rc(self, ex: float, ey: float) -> tuple[int, int]:
        x0, y0, cw, ch = self._last_geom
        cols = max(1, int(self.session.cols))
        rows = max(1, int(self.session.rows))
        vc = max(0, min(cols - 1, int((ex - x0) / max(1.0, cw))))
        vr = max(0, min(rows - 1, int((ey - y0) / max(1.0, ch))))
        return vr, vc

    def _on_enter_leave(self, _w, ev) -> bool:
        win = self.get_window()
        if win is not None and not self.mouse_reporting:
            if ev.type == Gdk.EventType.ENTER_NOTIFY:
                cur = Gdk.Cursor.new_for_display(win.get_display(), Gdk.CursorType.XTERM)
                win.set_cursor(cur)
            elif ev.type == Gdk.EventType.LEAVE_NOTIFY:
                win.set_cursor(None)
        return False

    def _on_button(self, _w, ev) -> bool:
        toplevel = self.get_toplevel()
        shell = toplevel if (toplevel is not None and hasattr(toplevel, "set_key_capture_grab")) else None
        sel_mode = (not self.mouse_reporting) or bool(ev.state & Gdk.ModifierType.SHIFT_MASK)

        if ev.button == 1:
            if ev.type in (Gdk.EventType.BUTTON_PRESS, Gdk.EventType._2BUTTON_PRESS, Gdk.EventType._3BUTTON_PRESS):
                set_active_tui(self.tui_id, shell=shell)
                self.grab_focus()
                self.da.queue_draw()
            if sel_mode:
                vr, vc = self._xy_to_view_rc(ev.x, ev.y)
                if ev.type == Gdk.EventType.BUTTON_PRESS:
                    self.session.begin_selection(vr, vc)
                    self.da.queue_draw()
                    return True
                if ev.type == Gdk.EventType._2BUTTON_PRESS:
                    self.session.select_word_at(vr, vc)
                    txt = self.session.get_selected_text()
                    if txt:
                        _tui_copy_text(txt, primary=True)
                    self.da.queue_draw()
                    return True
                if ev.type == Gdk.EventType._3BUTTON_PRESS:
                    self.session.select_line_at(vr)
                    txt = self.session.get_selected_text()
                    if txt:
                        _tui_copy_text(txt, primary=True)
                    self.da.queue_draw()
                    return True
                if ev.type == Gdk.EventType.BUTTON_RELEASE:
                    self.session.end_selection()
                    txt = self.session.get_selected_text()
                    if txt:
                        _tui_copy_text(txt, primary=True)
                    self.da.queue_draw()
                    return True

        if sel_mode and ev.type == Gdk.EventType.BUTTON_PRESS:
            if ev.button == 2:
                set_active_tui(self.tui_id, shell=shell)
                self.grab_focus()
                if self.session.alive:
                    clip = _tui_read_clipboard(primary=True) or _tui_read_clipboard(primary=False)
                    if clip:
                        self.session.paste_text(clip)
                self.da.queue_draw()
                return True
            if ev.button == 3:
                set_active_tui(self.tui_id, shell=shell)
                self.grab_focus()
                if self.session.has_selection():
                    txt = self.session.get_selected_text()
                    if txt:
                        _tui_copy_text(txt, primary=False)
                    self.session.clear_selection()
                elif self.session.alive:
                    clip = _tui_read_clipboard(primary=False)
                    if clip:
                        self.session.paste_text(clip)
                self.da.queue_draw()
                return True

        if not self.mouse_reporting:
            return False
        cell = self._xy_to_cell(ev.x, ev.y)
        if cell is None or ev.button not in (1, 2, 3):
            return ev.type == Gdk.EventType.BUTTON_PRESS and ev.button == 1
        btn = {1: 0, 2: 1, 3: 2}[ev.button]
        kind = "M" if ev.type == Gdk.EventType.BUTTON_PRESS else "m"
        self.session.send_bytes(f"\x1b[<{btn};{cell[0]};{cell[1]}{kind}".encode("ascii"))
        return True

    def _on_motion(self, _w, ev) -> bool:
        if not self.session.sel_active:
            return False
        _x0, y0, _cw, ch = self._last_geom
        rows = max(1, int(self.session.rows))
        if ev.y < y0:
            self.session.scroll_by(1)
        elif ev.y > (y0 + rows * ch):
            self.session.scroll_by(-1)
        vr, vc = self._xy_to_view_rc(ev.x, ev.y)
        self.session.update_selection(vr, vc)
        self.da.queue_draw()
        return True

    def _on_scroll(self, _w, ev) -> bool:
        sel_mode = (not self.mouse_reporting) or bool(ev.state & Gdk.ModifierType.SHIFT_MASK)
        if sel_mode:
            step = 0
            if ev.direction == Gdk.ScrollDirection.UP:
                step = 3
            elif ev.direction == Gdk.ScrollDirection.DOWN:
                step = -3
            elif ev.direction == Gdk.ScrollDirection.SMOOTH:
                ok_d, _dx, dy = ev.get_scroll_deltas()
                if ok_d and abs(dy) >= 0.05:
                    step = 3 if dy < 0 else -3
            if step != 0:
                self.session.scroll_by(step)
                self.da.queue_draw()
                return True
            return False
        if get_active_tui() != self.tui_id or not self.mouse_reporting:
            return False
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
        alloc_w = float(box_w if box_w > 0 else max(self.map_width, alloc.width))
        if abs(int(round(alloc_w)) - self.map_width) > 2:
            self.map_width = int(round(alloc_w))
            self._update_metrics(self.map_width)

        rows, cols, grid_snap, (cur_r, cur_c), scroll_offset, sb_len, sel_bounds = self.session.get_viewport_snapshot()

        cw = self._cw
        ch = self._ch_draw
        term_w = cols * cw
        term_h = rows * ch
        x_left, x_right = self._resolve_box_rails(alloc_w)
        p = get_semantic_palette()
        is_active = get_active_tui() == self.tui_id
        def_fg = _hex_to_rgb_f(p["focus_val"], (0.89, 0.92, 0.99))
        def_bg = _hex_to_rgb_f(p["structure_bg"], (0.10, 0.11, 0.14))

        if self.flush:
            pad_x = max(10.0, self.char_w * 1.25)
            pad_y = 6.5
            vx0 = math.floor(x_left) + 0.5
            vx1 = math.floor(x_right) + 0.5
            fw = max(40.0, vx1 - vx0)
            fy0 = 0.5
            y_bot = max(20.0, math.floor(max(float(alloc.height), term_h + TUI_FLUSH_VERT_CHROME_PX)) - 0.5)
            fh = max(20.0, y_bot - fy0)
            bs = str(THEME.get("ascii", {}).get("box_style", "rounded"))
            rad = (
                min(self.char_w * 0.65, self._ch * 0.45, fw * 0.25, fh * 0.25)
                if bs in ("rounded", "dashed")
                else 0.0
            )
            x0 = vx0 + max(pad_x, (fw - term_w) * 0.5)
            y0 = fy0 + pad_y
            self._last_geom = (x0, y0, cw, ch)

            cr.save()
            _rounded_rect(cr, vx0, fy0, fw, fh, rad)
            cr.set_source_rgba(def_bg[0], def_bg[1], def_bg[2], 0.55)
            cr.fill_preserve()
            cr.clip()
        else:
            pad_x = max(6.0, self.char_w * 0.95)
            pad_y = 6.0
            fx0 = math.floor(x_left + self.char_w * 1.5) + 0.5
            fx1 = math.floor(x_right - self.char_w * 1.5) + 0.5
            fw = max(40.0, fx1 - fx0)
            fy0 = 4.5
            fy1 = max(fy0 + 20.0, math.floor(max(float(alloc.height), term_h + TUI_VERT_CHROME_PX) - 6.0) + 0.5)
            fh = fy1 - fy0

            x0 = fx0 + max(pad_x, (fw - term_w) * 0.5)
            y0 = fy0 + pad_y
            self._last_geom = (x0, y0, cw, ch)

            cr.save()
            _rounded_rect(cr, fx0, fy0, fw, fh, 4.0)
            cr.set_source_rgba(def_bg[0], def_bg[1], def_bg[2], 0.50)
            cr.fill_preserve()
            cr.clip()

        layout = PangoCairo.create_layout(cr)
        layout.set_attributes(self._no_liga_attrs)

        sel_rgb = _hex_to_rgb_f(p["intent"], (0.0, 0.94, 1.0))
        view_base_abs = max(0, sb_len - scroll_offset)
        for r, row in enumerate(grid_snap):
            ry = y0 + r * ch
            c = 0
            while c < cols:
                txt, fg, bg, bold, rev = row[c]
                eff_bg = (fg or def_fg) if rev else bg
                if eff_bg is not None:
                    c2 = c + 1
                    while c2 < cols:
                        t2 = row[c2]
                        eb2 = (t2[1] or def_fg) if t2[4] else t2[2]
                        if eb2 != eff_bg:
                            break
                        c2 += 1
                    cr.set_source_rgb(*eff_bg)
                    cr.rectangle(x0 + c * cw, ry, (c2 - c) * cw + 0.5, ch + 0.5)
                    cr.fill()
                    c = c2
                else:
                    c += 1
            if sel_bounds is not None:
                sr, sc, er, ec = sel_bounds
                abs_r = view_base_abs + r
                if sr <= abs_r <= er:
                    c0 = sc if abs_r == sr else 0
                    c1 = min(cols - 1, ec) if abs_r == er else (cols - 1)
                    if c0 <= c1:
                        cr.set_source_rgba(sel_rgb[0], sel_rgb[1], sel_rgb[2], 0.28)
                        cr.rectangle(x0 + c0 * cw, ry, (c1 - c0 + 1) * cw, ch)
                        cr.fill()

        for r, row in enumerate(grid_snap):
            ry = y0 + r * ch
            c = 0
            while c < cols:
                txt, fg, bg, bold, rev = row[c]
                if not txt or txt == " ":
                    c += 1
                    continue
                eff_fg = (bg or def_bg) if rev else (fg or def_fg)
                layout.set_font_description(self._desc_bold if bold else self._desc_reg)
                cr.set_source_rgb(*eff_fg)
                if len(txt) == 1 and 32 < ord(txt) < 127:
                    run = [txt]
                    c2 = c + 1
                    while c2 < cols:
                        t2 = row[c2]
                        ef2 = (t2[2] or def_bg) if t2[4] else (t2[1] or def_fg)
                        if not t2[0] or len(t2[0]) != 1 or not (32 <= ord(t2[0]) < 127) or ef2 != eff_fg or t2[3] != bold:
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

        if self.show_cursor and is_active and self.session.alive and scroll_offset == 0 and 0 <= cur_r < rows and 0 <= cur_c < cols:
            cr_rgb = _hex_to_rgb_f(p["intent"], (0.0, 0.94, 1.0))
            cr.set_source_rgba(*cr_rgb, 0.85)
            cr.rectangle(x0 + cur_c * cw, y0 + cur_r * ch + 1.0, max(2.0, min(2.5, cw * 0.3)), max(8.0, ch - 2.0))
            cr.fill()
        if sb_len > 0 and scroll_offset > 0:
            sb_right = (vx1 if self.flush else fx1) - 5.5
            sb_top = (fy0 + 4.0) if self.flush else (fy0 + 4.0)
            sb_h = max(12.0, (fh - 8.0))
            total_lines = sb_len + rows
            thumb_h = max(18.0, sb_h * (rows / float(max(1, total_lines))))
            frac = 1.0 - (scroll_offset / float(max(1, sb_len)))
            thumb_y = sb_top + frac * max(0.0, sb_h - thumb_h)
            cr.set_source_rgba(sel_rgb[0], sel_rgb[1], sel_rgb[2], 0.55)
            _rounded_rect(cr, sb_right, thumb_y, 3.0, thumb_h, 1.5)
            cr.fill()
        cr.restore()

        if self.flush:
            _rounded_rect(cr, vx0, fy0, fw, fh, rad)
            if is_active:
                br, bg_c, bb = _hex_to_rgb_f(p["intent"], (0.0, 0.94, 1.0))
                cr.set_source_rgba(br, bg_c, bb, 0.90)
            else:
                br, bg_c, bb = _hex_to_rgb_f(p["structure"], (0.26, 0.29, 0.36))
                cr.set_source_rgba(br, bg_c, bb, 1.0)
            cr.set_line_width(1.0)
            cr.stroke()
            return False

        _rounded_rect(cr, fx0, fy0, fw, fh, 4.0)
        if is_active:
            br, bg_c, bb = _hex_to_rgb_f(p["intent"], (0.0, 0.94, 1.0))
            cr.set_source_rgba(br, bg_c, bb, 0.90)
        else:
            br, bg_c, bb = _hex_to_rgb_f(p["structure"], (0.26, 0.29, 0.36))
            cr.set_source_rgba(br, bg_c, bb, 0.75)
        cr.set_line_width(1.0)
        cr.stroke()
        return False


def _build_tui_ncbox(
    cc_panel,
    canvas: Canvas,
    tui_id: str,
    cmd: list[str],
    rows: int,
    cols: int,
    target_height_px: int,
    title: str,
    icon: str,
    open_key: str,
    desc_key: str,
):
    is_open = bool(cc_panel.get_state(open_key, True))
    acts = [
        ActionBtn(t("cc_system.restart_tui"), f"cc_sys:tui_restart:{tui_id}", tone="neutral"),
        ActionBtn(t("cc_system.open_window"), f"cc_sys:{tui_id}", tone="neutral"),
        FoldIconBtn(is_open, f"ui:state:{open_key}:{'false' if is_open else 'true'}"),
    ]
    box = Box(title, icon=icon, actions=acts, highlighted=False)
    if not is_open:
        close_embedded_tui(tui_id)
        box.add_row(" ")
        box.add_row(f"  {Txt(t(desc_key), role='inactive')}")
        box.add_row(" ")
        return box

    sess = get_or_start_tui_session(tui_id, cmd, rows=rows, cols=cols, cc_panel=cc_panel)
    if sess.rows != rows or sess.cols != cols:
        sess.resize(rows, cols)
    w = EmbeddedTuiWidget(
        tui_id,
        sess,
        map_width=int(round(canvas.total_px)),
        char_w=canvas.char_w,
        target_height_px=target_height_px,
        mouse_reporting=True,
        show_cursor=False,
        flush=True,
    )
    box.open_bottom = True
    box.cairo_close_bottom = True
    return AsciiCard(box, canvas, embedded_widget=w, footer_box_model=None)


def _measure_box_gtk_px(shell, item) -> int:
    bm = item.box_model if hasattr(item, "box_model") else item
    if not isinstance(bm, (Box, Divider)):
        return 0
    try:
        c = shell.content_canvas
        rendered = bm.render(c, forced_cols=c.total_cols)
        sanitized = Canvas.sanitize_markup(rendered)
        stripped, _ = _extract_and_strip_bg_spans(sanitized)
        fdesc = c.layout.get_font_description().to_string()
        lbl = Gtk.Label()
        lbl.set_xalign(0.0)
        lbl.set_line_wrap(False)
        lbl.set_markup(
            f"<span font_desc='{fdesc}' font_features='liga=0, clig=0, dlig=0, hlig=0'>{stripped}</span>"
            if stripped
            else ""
        )
        lbl.show()
        h_px = int(lbl.get_preferred_height()[1])
        if h_px > 0:
            return h_px
    except Exception:
        pass
    return int(shell._measure_cc_box_px(item))


def _compute_single_tui_size(
    cc_panel,
    canvas: Canvas,
    cards_above: list,
    title: str,
    icon: str,
    min_rows: int = 16,
    font_size_pt: float = 9.0,
    flush: bool = False,
    actions: list | None = None,
    has_footer: bool = True,
) -> tuple[int, int, int]:
    vert_chrome = TUI_FLUSH_VERT_CHROME_PX if flush else TUI_VERT_CHROME_PX
    cols, _cw, ch, _dr, _db = _compute_tui_grid_metrics(
        int(round(canvas.total_px)), canvas.char_w, font_size_pt=font_size_pt, flush=flush
    )
    shell = getattr(cc_panel, "shell", None)
    if shell is not None and hasattr(shell, "_get_cc_target_height_px"):
        _ml, _mr, m_top, m_bot, _gap = shell._get_inner_box_margins()
        bw = int(shell.geom.get("window_border_width", 1))
        avail_total_h = shell._get_cc_target_height_px() - m_top - m_bot - (2 * bw)
        spacing = int(shell.geom.get("card_spacing", 4))
        hdr_box = getattr(cc_panel, "_current_hdr_box", None)
        live_hdrs = shell.header_listbox.get_children() if getattr(shell, "header_listbox", None) is not None else []
        live_body = shell.listbox.get_children() if getattr(shell, "listbox", None) is not None else []
        used_h = 0
        if hdr_box is not None:
            if live_hdrs and isinstance(live_hdrs[0], AsciiCard):
                used_h += int(live_hdrs[0].lbl.get_preferred_height()[1]) + spacing
            else:
                used_h += _measure_box_gtk_px(shell, hdr_box) + spacing
        body_idx = 0
        for it in cards_above:
            if getattr(it, "_toast_meta", None):
                continue
            live_row = live_body[body_idx] if body_idx < len(live_body) else None
            body_idx += 1
            if (
                live_row is not None
                and isinstance(live_row, AsciiCard)
                and live_row.embedded_widget is None
                and getattr(live_row.box_model, "is_divider", False) == getattr((it.box_model if hasattr(it, "box_model") else it), "is_divider", False)
            ):
                item_h = int(live_row.lbl.get_preferred_height()[1])
            else:
                item_h = _measure_box_gtk_px(shell, it)
                if isinstance(it, AsciiCard):
                    emb = getattr(it, "embedded_widget", None)
                    if emb is not None:
                        ew_h = emb.get_size_request()[1]
                        if ew_h > 0:
                            item_h += ew_h
                    foot_bm = getattr(it, "footer_box_model", None)
                    if foot_bm is not None:
                        item_h += _measure_box_gtk_px(shell, foot_bm)
            used_h += item_h + spacing
        probe_acts = (
            list(actions)
            if actions is not None
            else [
                ActionBtn(t("cc_system.restart_tui"), "x", tone="neutral"),
                ActionBtn(t("cc_system.open_window"), "y", tone="neutral"),
                FoldIconBtn(True, "z"),
            ]
        )
        hdr_probe = Box(
            title,
            icon=icon,
            actions=probe_acts,
            highlighted=False,
            open_bottom=True,
        )
        chrome_px = _measure_box_gtk_px(shell, hdr_probe)
        if has_footer:
            foot_probe = Box("", highlighted=False, open_top=True)
            chrome_px += _measure_box_gtk_px(shell, foot_probe)
        if has_footer:
            max_bottom_h = avail_total_h
        else:
            inner_pad, _ = get_layout_margins()
            nav_bot_inset = max(0, bw + inner_pad - m_bot)
            max_bottom_h = avail_total_h - nav_bot_inset
        per_card_px = max_bottom_h - used_h
        target_widget_h = max(int(round(min_rows * ch + vert_chrome)), int(per_card_px - chrome_px))
        rows = max(min_rows, int((target_widget_h - vert_chrome) // ch))
        return cols, rows, target_widget_h
    rows = max(min_rows, 26)
    return cols, rows, int(round(rows * ch + vert_chrome))


def start_panel_terminal(
    cc_panel,
    title: str,
    cmd: list[str],
    *,
    icon: str = "\uf120",
    scope: tuple[str, str] | None = None,
    on_exit=None,
) -> None:
    """Launch an interactive command in the reusable Control Center bottom terminal slot."""
    global _PANEL_TERM_META
    shell = getattr(cc_panel, "shell", None) if cc_panel is not None else None
    sec = getattr(cc_panel, "section", "") if cc_panel is not None else ""
    sub = cc_panel.get_subtab() if (cc_panel is not None and hasattr(cc_panel, "get_subtab")) else "main"
    target_scope = tuple(scope) if scope else (sec, sub)
    _PANEL_TERM_META = {
        "title": str(title),
        "icon": str(icon or "\uf120"),
        "scope": target_scope,
        "cmd": list(cmd),
    }

    term_pt = float((THEME.get("typography") or {}).get("font_size", 11) or 11)
    cols = 88
    canvas = getattr(shell, "content_canvas", None)
    if canvas is not None:
        try:
            cols = _compute_tui_grid_metrics(
                int(round(canvas.total_px)), canvas.char_w, font_size_pt=term_pt, flush=True
            )[0]
        except Exception:
            pass

    def _wrapped_on_exit() -> bool:
        if cc_panel and getattr(cc_panel, "shell", None):
            cc_panel.shell.refresh_view()
        if callable(on_exit):
            try:
                on_exit()
            except Exception:
                pass
        return False

    start_tui_session(PANEL_TERM_ID, list(cmd), rows=20, cols=cols, cc_panel=cc_panel, on_exit=_wrapped_on_exit)
    if shell is not None:
        shell.refresh_view()
        sw = getattr(shell, "scrolled_window", None)
        if sw is not None:
            def _scroll_to_term() -> bool:
                try:
                    vadj = sw.get_vadjustment()
                    if vadj is not None:
                        target = max(0.0, float(vadj.get_upper()) - float(vadj.get_page_size()))
                        if target > float(vadj.get_value()) + 2.0:
                            vadj.set_value(target)
                except Exception:
                    pass
                return False

            GLib.idle_add(_scroll_to_term)


def close_panel_terminal(cc_panel=None) -> None:
    """Terminate the reusable Control Center bottom terminal slot and clear its metadata."""
    global _PANEL_TERM_META
    close_embedded_tui(PANEL_TERM_ID)
    _PANEL_TERM_META = {}
    if cc_panel and getattr(cc_panel, "shell", None):
        cc_panel.shell.refresh_view()


def build_panel_terminal_card(cc_panel, canvas: Canvas, cards_above: list):
    """Build the flush rounded bottom terminal card if active for the current section/subtab."""
    if not _PANEL_TERM_META:
        return None
    sec = getattr(cc_panel, "section", "")
    sub = cc_panel.get_subtab() if hasattr(cc_panel, "get_subtab") else "main"
    if tuple(_PANEL_TERM_META.get("scope") or ()) != (sec, sub):
        return None
    sess = get_tui_session(PANEL_TERM_ID)
    if sess is None:
        return None

    if sess.alive:
        st_badge = Badge(t("common.term_running"), style="warn")
    elif sess.exit_code == 0 or sess.exit_code is None:
        st_badge = StateBadge(True, t("common.term_done"))
    else:
        st_badge = Badge(t("common.term_failed", p0=sess.exit_code), style="crit")

    acts = [
        st_badge,
        ActionBtn(t("common.term_close"), "cc_term:close", tone="danger_soft"),
    ]
    title = str(_PANEL_TERM_META.get("title") or "")
    icon = str(_PANEL_TERM_META.get("icon") or "\uf120")
    term_pt = float((THEME.get("typography") or {}).get("font_size", 11) or 11)
    cols, rows, target_h = _compute_single_tui_size(
        cc_panel,
        canvas,
        cards_above,
        title,
        icon,
        min_rows=10,
        font_size_pt=term_pt,
        flush=True,
        actions=acts,
        has_footer=False,
    )
    if sess.rows != rows or sess.cols != cols:
        sess.resize(rows, cols)

    box_top = Box(
        title,
        icon=icon,
        actions=acts,
        highlighted=False,
        open_bottom=True,
    )
    box_top.cairo_close_bottom = True
    term_w = EmbeddedTuiWidget(
        PANEL_TERM_ID,
        sess,
        map_width=int(round(canvas.total_px)),
        char_w=canvas.char_w,
        target_height_px=target_h,
        mouse_reporting=False,
        show_cursor=True,
        font_size_pt=term_pt,
        flush=True,
    )
    return AsciiCard(box_top, canvas, embedded_widget=term_w, footer_box_model=None)
