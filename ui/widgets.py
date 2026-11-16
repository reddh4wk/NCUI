#!/usr/bin/env python3
import html
import re
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gtk, Gdk, GdkPixbuf, GLib, Pango
from core.config import THEME
from core.i18n import t
from ui.canvas import Canvas
from ui.kit import PALETTE, Box, Txt

_ANCHOR_RE = re.compile(r"(<a\s+href=['\"]([^'\"]+)['\"]>)(.*?)(</a>)", re.IGNORECASE | re.DOTALL)
_INNER_COLOR_RE = re.compile(r"\b(?:fg)?color=['\"][^'\"]*['\"]", re.IGNORECASE)
_INNER_WEIGHT_RE = re.compile(r"\bfont_weight=['\"][^'\"]*['\"]", re.IGNORECASE)
_ANY_TAG_RE = re.compile(r"<[^>]+>")


def _is_focusable_anchor_content(inner_markup: str) -> bool:
    """
    Esclude i singoli caratteri '#' e '-' delle barre degli slider dal ciclo Tab/Frecce,
    così la navigazione da tastiera salta direttamente sui pulsanti [-] / [+] e sui controlli reali.
    """
    plain = Canvas.strip_tags(inner_markup).strip()
    if not plain:
        return False
    asc = THEME["ascii"]
    if len(plain) == 1 and plain in (asc.get("slider_fill", "#"), asc.get("slider_empty", "-")):
        return False
    return True


class AsciiCard(Gtk.ListBoxRow):
    def __init__(
        self,
        box_model: Box,
        canvas: Canvas,
        on_link=None,
        on_row_click=None,
        pixbuf: GdkPixbuf.Pixbuf = None,
        hover_enable: bool = True,
        on_hover_change=None,
        on_pointer_activity=None,
    ):
        super().__init__()
        self.set_selectable(False)
        self.set_can_focus(False)
        self.box_model = box_model
        self.canvas = canvas
        self.on_link = on_link
        self.on_row_click = on_row_click
        self.pixbuf = pixbuf
        self.is_hovered = False
        self.hover_enable = hover_enable and not getattr(box_model, "is_divider", False)
        self.on_hover_change = on_hover_change
        self.on_pointer_activity = on_pointer_activity
        self._link_activated = False
        self._button_down = False
        self._img_widget = None
        self._last_markup = ""
        self._focused_anchor_idx = None
        self._hovered_anchor_idx = None
        self._anchor_byte_spans = []
        self._last_crop_size = (0, 0)

        if getattr(box_model, "is_divider", False):
            self.set_margin_top(4)
            self.set_margin_bottom(2)

        self.lbl = Gtk.Label()
        self.lbl.set_xalign(0.0)
        self.lbl.set_line_wrap(False)
        self.lbl.set_can_focus(False)
        self.lbl.add_events(
            Gdk.EventMask.POINTER_MOTION_MASK
            | Gdk.EventMask.LEAVE_NOTIFY_MASK
        )
        self.lbl.connect("activate-link", self._handle_link)
        self.lbl.connect("motion-notify-event", self._on_motion)
        self.lbl.connect("event-after", self._on_event_after)

        self.refresh_markup()
        content_widget = self._wrap_pixbuf(self.lbl) if self.pixbuf else self.lbl

        ev_mask = (
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.SCROLL_MASK
            | Gdk.EventMask.SMOOTH_SCROLL_MASK
            | Gdk.EventMask.ENTER_NOTIFY_MASK
            | Gdk.EventMask.LEAVE_NOTIFY_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
        )
        self.eb = Gtk.EventBox()
        self.eb.set_name("nc-card-eb")
        self.eb.set_visible_window(True)
        self.eb.set_above_child(False)
        self.eb.set_can_focus(False)
        self.eb.add_events(ev_mask)
        self.eb.connect("enter-notify-event", self._on_enter)
        self.eb.connect("motion-notify-event", self._on_motion)
        self.eb.connect("leave-notify-event", self._on_leave)
        self.eb.connect("scroll-event", self._on_scroll)
        self.eb.connect("button-press-event", self._on_button_press)
        self.eb.connect("button-release-event", self._on_button_release)
        self.eb.connect("event-after", self._on_event_after)
        self.eb.add(content_widget)
        self.add(self.eb)

    def _rebuild_anchor_byte_spans(self) -> None:
        raw = self._last_markup or ""
        spans = []
        if not raw:
            self._anchor_byte_spans = spans
            return
        pos = 0
        cur_byte = 0
        focus_idx = 0
        for m in _ANCHOR_RE.finditer(raw):
            before_plain = html.unescape(_ANY_TAG_RE.sub("", raw[pos : m.start()]))
            cur_byte += len(before_plain.encode("utf-8"))
            inner = m.group(3)
            inner_plain = html.unescape(_ANY_TAG_RE.sub("", inner))
            inner_bytes = len(inner_plain.encode("utf-8"))
            if _is_focusable_anchor_content(inner):
                spans.append((cur_byte, cur_byte + inner_bytes, focus_idx))
                focus_idx += 1
            cur_byte += inner_bytes
            pos = m.end()
        self._anchor_byte_spans = spans

    def _update_hovered_anchor_from_xy(self, widget: Gtk.Widget, ev_x: float, ev_y: float) -> None:
        if self._button_down or not self._anchor_byte_spans:
            return
        try:
            if widget is not self.lbl:
                res = widget.translate_coordinates(self.lbl, int(ev_x), int(ev_y))
                if not res or not res[0]:
                    return
                _, lx, ly = res
            else:
                lx, ly = int(ev_x), int(ev_y)

            layout = self.lbl.get_layout()
            if layout is None:
                return
            inside, byte_idx, _ = layout.xy_to_index(int(lx * Pango.SCALE), int(ly * Pango.SCALE))
            found_idx = None
            if inside:
                for sb, eb, f_idx in self._anchor_byte_spans:
                    if sb <= byte_idx < eb:
                        found_idx = f_idx
                        break
            if found_idx != self._hovered_anchor_idx:
                self._hovered_anchor_idx = found_idx
                self._apply_label_markup()
        except Exception:
            pass

    def get_focusable_links(self) -> list[dict]:
        """
        Estrae tutti i link `<a href='...'>` navigabili da tastiera presenti nella card,
        restituendo `[{"anchor_idx": int, "uri": str, "line": int, "col": int}]`.
        """
        res = []
        raw = self._last_markup or ""
        if not raw:
            return res

        focus_idx = 0
        for line_no, line_str in enumerate(raw.splitlines()):
            for m in _ANCHOR_RE.finditer(line_str):
                uri = m.group(2)
                inner = m.group(3)
                if not _is_focusable_anchor_content(inner):
                    continue
                prefix_plain = Canvas.strip_tags(line_str[: m.start()])
                col_pos = len(prefix_plain)
                res.append({
                    "anchor_idx": focus_idx,
                    "uri": uri,
                    "line": line_no,
                    "col": col_pos,
                })
                focus_idx += 1
        return res

    def set_focused_anchor(self, anchor_idx: int = None) -> None:
        """
        Evidenzia il pulsante `anchor_idx` con un cursore inverso ad alto contrasto
        senza aggiungere alcun carattere (zero spostamenti di layout).
        """
        if self._focused_anchor_idx == anchor_idx:
            return
        self._focused_anchor_idx = anchor_idx
        self._apply_label_markup()

    def _apply_label_markup(self) -> None:
        raw = self._last_markup or ""
        if (self._focused_anchor_idx is None and self._hovered_anchor_idx is None) or not raw:
            self.lbl.set_markup(raw)
            return

        p = PALETTE
        target_focus = self._focused_anchor_idx
        target_hover = self._hovered_anchor_idx
        c_bg = p["intent"]
        c_fg = p["intent_bg"]
        c_sel = p["focus"]
        cur_focus_i = [0]

        def _repl(m):
            open_a, _uri, inner, close_a = m.group(1), m.group(2), m.group(3), m.group(4)
            if not _is_focusable_anchor_content(inner):
                return m.group(0)
            idx = cur_focus_i[0]
            cur_focus_i[0] += 1
            if idx == target_focus:
                clean_inner = _INNER_COLOR_RE.sub("", inner)
                return f"{open_a}<span background='{c_bg}' foreground='{c_fg}'>{clean_inner}</span>{close_a}"
            if idx == target_hover:
                clean_inner = _INNER_COLOR_RE.sub("", inner)
                clean_inner = _INNER_WEIGHT_RE.sub("", clean_inner)
                return f"{open_a}<span color='{c_sel}'>{clean_inner}</span>{close_a}"
            return m.group(0)

        highlighted = _ANCHOR_RE.sub(_repl, raw)
        self.lbl.set_markup(highlighted)

    def _fit_and_crop_pixbuf(self, alloc_w: int = 0, alloc_h: int = 0) -> GdkPixbuf.Pixbuf:
        orig_w = self.pixbuf.get_width()
        orig_h = self.pixbuf.get_height()
        if orig_w <= 0 or orig_h <= 0:
            return self.pixbuf

        s = Canvas.sanitize_markup(getattr(self, "_last_markup", "") or "")
        pango_only = re.sub(r"</?a\b[^>]*>", "", s, flags=re.IGNORECASE)
        self.canvas.layout.set_width(-1)
        try:
            self.canvas.layout.set_markup(pango_only, -1)
            canv_w, canv_h = self.canvas.layout.get_pixel_size()
        except Exception:
            canv_w = int(self.canvas.total_px)
            canv_h = int(len(s.splitlines()) * self.canvas.char_h)

        lbl_w = alloc_w if alloc_w > 40 else canv_w
        lbl_h = alloc_h if alloc_h > 30 else canv_h
        n_lines = max(3, len(s.splitlines()))
        line_h = lbl_h / float(n_lines)

        inner_w = max(24, int(round(lbl_w - (self.canvas.char_w * 2.5))))
        inner_h = max(18, int(round(lbl_h - (line_h * 2.15))))

        scale = max(inner_w / float(orig_w), inner_h / float(orig_h))
        scaled_w = max(inner_w, int(round(orig_w * scale)))
        scaled_h = max(inner_h, int(round(orig_h * scale)))

        scaled = self.pixbuf.scale_simple(scaled_w, scaled_h, GdkPixbuf.InterpType.BILINEAR)
        off_x = max(0, (scaled_w - inner_w) // 2)
        off_y = max(0, (scaled_h - inner_h) // 2)
        return scaled.new_subpixbuf(off_x, off_y, inner_w, inner_h)

    def _wrap_pixbuf(self, label_widget: Gtk.Label) -> Gtk.Overlay:
        overlay = Gtk.Overlay()
        overlay.add(label_widget)
        img = Gtk.Image()
        self._img_widget = img
        self._last_crop_size = (0, 0)
        img.set_from_pixbuf(self._fit_and_crop_pixbuf())
        img.set_halign(Gtk.Align.CENTER)
        img.set_valign(Gtk.Align.CENTER)
        overlay.add_overlay(img)
        overlay.set_overlay_pass_through(img, True)

        def _on_lbl_alloc(_lbl, alloc):
            if alloc.width > 40 and alloc.height > 30:
                cur_sz = (alloc.width, alloc.height)
                if cur_sz != self._last_crop_size:
                    self._last_crop_size = cur_sz
                    img.set_from_pixbuf(self._fit_and_crop_pixbuf(alloc.width, alloc.height))

        label_widget.connect("size-allocate", _on_lbl_alloc)
        return overlay

    def set_hovered_visual(self, hovered: bool) -> None:
        self.is_hovered = bool(hovered)
        if not hovered and self._hovered_anchor_idx is not None:
            self._hovered_anchor_idx = None
            if not self._button_down:
                self._apply_label_markup()
        if not self.hover_enable or self._button_down:
            return
        new_sel = bool(hovered)
        if getattr(self.box_model, "selected", False) != new_sel:
            self.box_model.selected = new_sel
            self.refresh_markup()

    def _handle_link(self, _label, uri: str) -> bool:
        self._link_activated = True
        self._button_down = False
        if self.on_pointer_activity:
            self.on_pointer_activity("click")
        if self.on_link and uri:
            GLib.idle_add(self._dispatch_link_deferred, uri)
            return True
        return False

    def _dispatch_link_deferred(self, uri: str) -> bool:
        if self.on_link:
            self.on_link(uri)
        return False

    def _on_enter(self, widget, event):
        if self.on_pointer_activity:
            self.on_pointer_activity("enter")
        if self._button_down:
            return False
        if not self.is_hovered:
            self.is_hovered = True
            if self.hover_enable:
                if self.on_hover_change:
                    self.on_hover_change(self, True)
                else:
                    self.set_hovered_visual(True)
        self._update_hovered_anchor_from_xy(widget, event.x, event.y)
        return False

    def _on_motion(self, widget, event):
        if self.on_pointer_activity:
            self.on_pointer_activity("motion")
        if not self.is_hovered and not self._button_down:
            self._on_enter(widget, event)
        self._update_hovered_anchor_from_xy(widget, event.x, event.y)
        return False

    def _on_leave(self, widget, event):
        if self._button_down:
            return False
        if event.detail == Gdk.NotifyType.INFERIOR:
            return False
        if event.mode != Gdk.CrossingMode.NORMAL:
            return False
        alloc = self.get_allocation()
        res = widget.translate_coordinates(self, int(event.x), int(event.y))
        if res and len(res) == 3:
            ok_c, rx, ry = res
            if ok_c and 1 <= rx < (alloc.width - 1) and 1 <= ry < (alloc.height - 1):
                return False
        self.is_hovered = False
        if self._hovered_anchor_idx is not None:
            self._hovered_anchor_idx = None
            self._apply_label_markup()
        if self.hover_enable:
            if self.on_hover_change:
                self.on_hover_change(self, False)
            else:
                self.set_hovered_visual(False)
        return False

    def _on_button_press(self, _widget, event):
        if event.button == 1:
            self._button_down = True
            self._link_activated = False
            if self.on_pointer_activity:
                self.on_pointer_activity("press")
        return False

    def _on_button_release(self, _widget, event):
        if event.button == 1:
            self._button_down = False
            if self.on_pointer_activity:
                self.on_pointer_activity("release")
            if self.on_row_click and not self._link_activated:
                GLib.idle_add(self._dispatch_row_click_deferred)
                return True
        return False

    def _dispatch_row_click_deferred(self) -> bool:
        if self.on_row_click:
            self.on_row_click()
        return False

    def _on_event_after(self, _widget, event) -> None:
        if event.type == Gdk.EventType.BUTTON_PRESS:
            ok_b, btn = event.get_button()
            if ok_b and btn == 1:
                self._button_down = True
                if self.on_pointer_activity:
                    self.on_pointer_activity("press")
        elif event.type == Gdk.EventType.BUTTON_RELEASE:
            ok_b, btn = event.get_button()
            if ok_b and btn == 1:
                self._button_down = False
                if self.on_pointer_activity:
                    self.on_pointer_activity("release")

    def _on_scroll(self, _widget, event):
        if self.on_pointer_activity:
            self.on_pointer_activity("scroll")
        scroll_act = getattr(self.box_model, "scroll_action", None)
        if not scroll_act or not self.on_link:
            return False
        up = False
        if event.direction == Gdk.ScrollDirection.UP:
            up = True
        elif event.direction == Gdk.ScrollDirection.DOWN:
            up = False
        elif event.direction == Gdk.ScrollDirection.SMOOTH:
            ok_d, dx, dy = event.get_scroll_deltas()
            delta = dy if abs(dy) >= abs(dx) else -dx
            if abs(delta) < 0.05:
                return False
            up = (delta < 0)
        else:
            return False
        self.on_link(f"{scroll_act}:{'up' if up else 'down'}")
        return True

    def refresh_markup(self) -> None:
        new_markup = self.box_model.render(self.canvas)
        self._last_markup = new_markup
        self._rebuild_anchor_byte_spans()
        self._apply_label_markup()
        if self.pixbuf is not None and getattr(self, "_img_widget", None) is not None:
            self._img_widget.set_from_pixbuf(self._fit_and_crop_pixbuf())


class CustomWidgetCard(Gtk.ListBoxRow):
    def __init__(self, child_widget: Gtk.Widget, margin_v: int = 8):
        super().__init__()
        self.set_selectable(False)
        self.set_can_focus(False)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.set_margin_top(margin_v)
        box.set_margin_bottom(margin_v)
        box.set_halign(Gtk.Align.CENTER)
        box.pack_start(child_widget, True, True, 0)
        self.add(box)


class NcPromptBar(Gtk.Box):
    def __init__(self, canvas: Canvas, on_submit, on_cancel, on_visibility_change=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.set_margin_top(6)
        self.canvas = canvas
        self.on_submit = on_submit
        self.on_cancel = on_cancel
        self.on_visibility_change = on_visibility_change

        self.action_id = ""
        self.base_placeholder = t("common.input_placeholder")
        self.placeholder = self.base_placeholder
        self.title = t("common.input_title")
        self.is_secret = False
        self.show_secret = False
        self.is_required = False
        self.has_error = False

        self.entry = Gtk.Entry()
        self.entry.set_opacity(0.0)
        self.entry.set_size_request(1, 1)
        self.entry.set_can_focus(True)
        self.entry.set_halign(Gtk.Align.START)
        self.entry.set_valign(Gtk.Align.START)
        self.entry.connect("changed", self._on_text_changed)
        self.entry.connect("activate", self._on_activate)

        self.lbl = Gtk.Label()
        self.lbl.set_xalign(0.0)
        self.lbl.set_can_focus(False)
        self.lbl.connect("activate-link", self._on_link)

        self.eb = Gtk.EventBox()
        self.eb.set_name("nc-card-eb")
        self.eb.set_visible_window(False)
        self.eb.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self.eb.connect("button-press-event", self._on_bar_click)
        self.eb.add(self.lbl)

        self.overlay = Gtk.Overlay()
        self.overlay.add(self.eb)
        self.overlay.add_overlay(self.entry)
        self.add(self.overlay)
        self.overlay.show_all()

    def _focus_entry(self) -> bool:
        if self.get_visible():
            self.entry.grab_focus()
            self.entry.set_position(-1)
        return False

    def _on_bar_click(self, _widget, _event) -> bool:
        self.entry.grab_focus()
        self.entry.set_position(-1)
        return False

    def activate(
        self,
        action_id: str,
        placeholder: str,
        default_txt: str = "",
        secret: bool = False,
        title: str = None,
        required: bool = False,
    ) -> None:
        self.action_id = action_id
        self.is_secret = bool(secret)
        self.show_secret = False
        self.is_required = bool(required)
        self.has_error = False
        self.base_placeholder = placeholder or (
            t("common.password_placeholder") if secret else t("common.input_placeholder")
        )
        self.placeholder = self.base_placeholder
        self.title = title or (t("common.password_title") if secret else t("common.input_title"))
        self.entry.set_text(default_txt or "")
        self._render()
        self.set_no_show_all(False)
        self.show_all()
        self.show()
        self.set_no_show_all(True)
        self.entry.grab_focus()
        self.entry.set_position(-1)
        GLib.idle_add(self._focus_entry)
        if self.on_visibility_change:
            self.on_visibility_change()

    def deactivate(self) -> None:
        was_visible = self.get_visible()
        self.action_id = ""
        self.is_required = False
        self.has_error = False
        self.entry.set_text("")
        self.hide()
        if was_visible and self.on_visibility_change:
            self.on_visibility_change()

    def _on_text_changed(self, _entry) -> None:
        self._render()

    def _on_activate(self, entry) -> None:
        txt = entry.get_text().strip()
        if not txt and self.is_required:
            self.has_error = True
            base_clean = self.base_placeholder.rstrip(" *")
            self.placeholder = f"{base_clean} *"
            self._render()
            self.entry.grab_focus()
            self.entry.set_position(-1)
            return
        act = self.action_id
        self.deactivate()
        if act and self.on_submit:
            self.on_submit(act, txt)

    def _on_link(self, _lbl, uri: str) -> bool:
        if uri == "prompt:toggle_vis":
            self.show_secret = not self.show_secret
            self._render()
            self.entry.grab_focus()
            self.entry.set_position(-1)
            return True
        if uri == "prompt:cancel":
            self.deactivate()
            if self.on_cancel:
                self.on_cancel()
            return True
        return False

    def _render(self) -> None:
        p = PALETTE
        txt = self.entry.get_text()
        err = self.has_error and not txt
        c_brd = p["fault"] if err else p["intent"]

        right_parts = []
        if self.is_secret:
            eye = "[\uf06e]" if self.show_secret else "[\uf070]"
            right_parts.append(
                f"<a href='prompt:toggle_vis'><span underline='none'>{Txt(eye, role='intent' if self.show_secret else 'schema')}</span></a>"
            )
        right_parts.append(
            f"<a href='prompt:cancel'><span underline='none'>{Txt('[\uf00d]', role='interrupt_rest')}</span></a>"
        )
        right_markup = " ".join(right_parts)

        avail_cols = max(8, self.canvas.inner_cols - self.canvas.measure_cols(right_markup) - 4)
        if not txt:
            shown = self.canvas.truncate_plain(self.placeholder, avail_cols)
            disp_txt = Txt(shown, role="fault" if self.has_error else "inactive")
        else:
            raw_disp = ("\u25cf" * len(txt)) if (self.is_secret and not self.show_secret) else txt
            if len(raw_disp) > avail_cols:
                raw_disp = "\u2026" + raw_disp[-(avail_cols - 1):]
            disp_txt = Txt(raw_disp, role="focus")

        icon = "\uf084" if self.is_secret else "\uf120"
        hdr = f"{icon}  {Canvas.escape(self.title)}"
        prompt_gt = Txt(">", role="fault" if err else "intent")
        markup = (
            f"{self.canvas.top(hdr, c_box=c_brd, c_txt=c_brd)}\n"
            f"{self.canvas.row(f'{prompt_gt} {disp_txt}', right_markup=right_markup, c_box=c_brd)}\n"
            f"{self.canvas.bot(c_box=c_brd)}"
        )
        self.lbl.set_markup(markup)
