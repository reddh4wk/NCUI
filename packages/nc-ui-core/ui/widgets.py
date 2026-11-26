#!/usr/bin/env python3
from collections import deque
import html
import math
import os
import re
import time
import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gtk, Gdk, GdkPixbuf, GLib, Pango, PangoCairo
from core.config import THEME, _parse_rgba_components, get_box_opacity, get_surface_bg_rgb, get_sys_notif_opacity, get_toast_opacity
from core.i18n import t
from ui.canvas import Canvas
from ui.kit import PALETTE, ActionBtn, Box, Txt, get_active_inline_entry, get_active_inline_uri, get_semantic_palette, get_slider_step_uris, is_slider_enabled, set_slider_meta_value

_ANCHOR_RE = re.compile(r"(<a\s+href=['\"]([^'\"]+)['\"]>)(.*?)(</a>)", re.IGNORECASE | re.DOTALL)
_INNER_COLOR_RE = re.compile(r"\b(?:fg)?color=['\"][^'\"]*['\"]", re.IGNORECASE)
_SEG_COLOR_RE = re.compile(r"\bcolor=['\"]([^'\"]+)['\"]", re.IGNORECASE)
_INNER_WEIGHT_RE = re.compile(r"\bfont_weight=['\"][^'\"]*['\"]", re.IGNORECASE)
_ANY_TAG_RE = re.compile(r"<[^>]+>")
_BG_TOK_RE = re.compile(r"(<[^>]+>|&[a-zA-Z0-9#]+;|.)", re.DOTALL)
_SPAN_BG_ATTR_RE = re.compile(r"""\bbackground=['"]([^'"]+)['"]""", re.IGNORECASE)
_SPAN_UL_COL_RE = re.compile(r"""\bunderline_color=['"]([^'"]+)['"]""", re.IGNORECASE)
_SPAN_ST_COL_RE = re.compile(r"""\bstrikethrough_color=['"]([^'"]+)['"]""", re.IGNORECASE)
_STRIP_BG_ATTR_RE = re.compile(r"""\s*\bbackground=['"][^'"]*['"]""", re.IGNORECASE)
_STRIP_SPAN_CAIRO_ATTR_RE = re.compile(
    r"""\s*\b(?:background|underline_color|strikethrough_color|strikethrough)=['"][^'"]*['"]""",
    re.IGNORECASE,
)
_HREF_ATTR_RE = re.compile(r"""\bhref=['"]([^'"]+)['"]""", re.IGNORECASE)
_COLOR_ATTR_RE = re.compile(r"""\b(?:fg)?color=['"]([^'"]+)['"]""", re.IGNORECASE)
_NO_LIGA_SPAN_OPEN = "<span font_features='liga=0, clig=0, dlig=0, hlig=0'>"


def _unpack_translated_coords(res) -> tuple[int, int] | None:
    """Normalize PyGObject Gtk.Widget.translate_coordinates() return values (2-tuple or 3-tuple)."""
    if res is None:
        return None
    if len(res) >= 3:
        if res[0] is False:
            return None
        return (int(res[1]), int(res[2]))
    if len(res) == 2:
        return (int(res[0]), int(res[1]))
    return None
_STEP_INPUT_TIMESTAMPS: deque[float] = deque()
_LAST_CONTINUOUS_INPUT_TS: float = 0.0
_LAST_POINTER_ROOT_XY: tuple[int, int] | None = None
_LAST_POINTER_MOVE_TS: float = 0.0


def _record_pointer_coords(event) -> bool:
    """Registra lo spostamento fisico del puntatore (ignora eventi sintetici a coordinate invariate)."""
    global _LAST_POINTER_ROOT_XY, _LAST_POINTER_MOVE_TS
    xr = getattr(event, "x_root", None)
    yr = getattr(event, "y_root", None)
    if xr is None or yr is None:
        return False
    xy = (int(round(xr)), int(round(yr)))
    if _LAST_POINTER_ROOT_XY == xy:
        return False
    _LAST_POINTER_ROOT_XY = xy
    _LAST_POINTER_MOVE_TS = time.monotonic()
    return True


def pointer_moved_recently(window_sec: float = 0.25) -> bool:
    return (time.monotonic() - _LAST_POINTER_MOVE_TS) <= window_sec


def clear_pointer_move_flag() -> None:
    global _LAST_POINTER_MOVE_TS
    _LAST_POINTER_MOVE_TS = 0.0


def allow_step_input(max_per_sec: int = 24, continuous: bool = False) -> bool:
    """Limita gli input a `max_per_sec` (default 24) in qualsiasi finestra mobile di 1.0s.
    Se `continuous=True` (trackpad/key-repeat), distribuisce gli step ad almeno 95ms."""
    global _LAST_CONTINUOUS_INPUT_TS
    now = time.monotonic()
    if continuous and (now - _LAST_CONTINUOUS_INPUT_TS) < 0.095:
        return False
    cutoff = now - 1.0
    while _STEP_INPUT_TIMESTAMPS and _STEP_INPUT_TIMESTAMPS[0] <= cutoff:
        _STEP_INPUT_TIMESTAMPS.popleft()
    if len(_STEP_INPUT_TIMESTAMPS) >= max_per_sec:
        return False
    _STEP_INPUT_TIMESTAMPS.append(now)
    if continuous:
        _LAST_CONTINUOUS_INPUT_TS = now
    return True


def _is_mouse_wheel_smooth_event(event, last_discrete_ts: float, now: float) -> bool:
    """Riconosce eventi SMOOTH ridondanti emessi da rotelle mouse insieme a eventi discreti."""
    if event.direction != Gdk.ScrollDirection.SMOOTH:
        return False
    src_dev = event.get_source_device() if hasattr(event, "get_source_device") else getattr(event, "device", None)
    dev_name = ""
    src_kind = None
    if src_dev is not None:
        try:
            dev_name = (src_dev.get_name() or "").lower()
        except Exception:
            dev_name = ""
        try:
            src_kind = src_dev.get_source()
        except Exception:
            src_kind = None
    if ("wheel" in dev_name or src_dev is None or src_kind == Gdk.InputSource.MOUSE) and (now - last_discrete_ts) < 0.050:
        return True
    return False


def _is_continuous_scroll_event(event) -> bool:
    """Return True only for continuous touchpad / fractional smooth streams, not discrete mouse wheel notches."""
    if event.direction != Gdk.ScrollDirection.SMOOTH:
        return False
    src_dev = event.get_source_device() if hasattr(event, "get_source_device") else getattr(event, "device", None)
    src_kind = None
    if src_dev is not None:
        try:
            src_kind = src_dev.get_source()
        except Exception:
            src_kind = None
    if src_kind == Gdk.InputSource.TOUCHPAD:
        return True
    _ok_d, dx, dy = event.get_scroll_deltas()
    delta = abs(dy) if abs(dy) >= abs(dx) else abs(dx)
    return delta < 0.85


def _extract_and_strip_bg_spans(markup_str: str) -> tuple[str, list[tuple[int, int, str, float, str]]]:
    """Rimuove background='...' e marker cursore da Pango e registra gli span per Cairo."""
    low_m = markup_str.lower() if markup_str else ""
    if not low_m or ("background=" not in low_m and "underline_color=" not in low_m and "strikethrough_color=" not in low_m):
        return markup_str or "", []
    out_parts = []
    spans: list[tuple[int, int, str, float, str]] = []
    cursor_spans: list[tuple[int, int, str, float, str]] = []
    stack: list[tuple[str, str, bool, str, float, str]] = []
    cur_byte = 0
    for tok in _BG_TOK_RE.findall(markup_str):
        if tok.startswith("<"):
            if tok.startswith("</"):
                if stack:
                    stack.pop()
                out_parts.append(tok)
            elif tok.endswith("/>"):
                out_parts.append(tok)
            else:
                m_tag = re.match(r"<\s*([a-zA-Z0-9_:-]+)", tok)
                tname = m_tag.group(1).lower() if m_tag else "span"
                m_bg = _SPAN_BG_ATTR_RE.search(tok)
                bg_val = m_bg.group(1).strip() if m_bg else ""
                m_ul = _SPAN_UL_COL_RE.search(tok)
                m_st = _SPAN_ST_COL_RE.search(tok)
                cur_mode, cur_col = (
                    (-1.0, m_ul.group(1).strip()) if m_ul
                    else ((-2.0, m_st.group(1).strip()) if m_st else (0.0, ""))
                )
                is_tab_a = False
                if tname == "a":
                    m_href = _HREF_ATTR_RE.search(tok)
                    if m_href and m_href.group(1).strip().startswith(("cc:subtab:", "ui:tab:")):
                        is_tab_a = True
                m_fg = _COLOR_ATTR_RE.search(tok)
                fg_val = m_fg.group(1).strip() if m_fg else ""
                stack.append((tname, bg_val, is_tab_a, fg_val, cur_mode, cur_col))
                out_parts.append(_STRIP_SPAN_CAIRO_ATTR_RE.sub("", tok) if (bg_val or cur_mode) else tok)
        else:
            out_parts.append(tok)
            plain_tok = html.unescape(tok)
            b_len = len(plain_tok.encode("utf-8"))
            if b_len > 0 and plain_tok != "\n":
                active_bg = ""
                active_fg = ""
                active_cm = 0.0
                active_cc = ""
                is_tab = False
                for _tn, bg_v, tab_f, fg_v, cm_v, cc_v in reversed(stack):
                    if tab_f:
                        is_tab = True
                    if not active_cm and cm_v:
                        active_cm, active_cc = cm_v, cc_v
                    if not active_bg and bg_v:
                        active_bg = bg_v
                        if not active_fg and fg_v:
                            active_fg = fg_v
                if active_bg:
                    h_mult = 1.5 if is_tab else 1.0
                    cap_col = active_fg if is_tab else ""
                    if spans and spans[-1][1] == cur_byte and spans[-1][2] == active_bg and spans[-1][3] == h_mult:
                        spans[-1] = (spans[-1][0], cur_byte + b_len, active_bg, h_mult, spans[-1][4] or cap_col)
                    else:
                        spans.append((cur_byte, cur_byte + b_len, active_bg, h_mult, cap_col))
                if active_cm and active_cc:
                    if cursor_spans and cursor_spans[-1][1] == cur_byte and cursor_spans[-1][3] == active_cm and cursor_spans[-1][4] == active_cc:
                        cursor_spans[-1] = (cursor_spans[-1][0], cur_byte + b_len, "", active_cm, active_cc)
                    else:
                        cursor_spans.append((cur_byte, cur_byte + b_len, "", active_cm, active_cc))
            cur_byte += b_len
    return "".join(out_parts), spans + cursor_spans


def _is_slider_segment_content(inner_markup: str) -> bool:
    plain = Canvas.strip_tags(inner_markup).strip()
    if not plain:
        return False
    asc = THEME["ascii"]
    s_fill = str(asc.get("slider_fill") or "#")[0]
    s_empty = str(asc.get("slider_empty") or "-")[0]
    return len(plain) == 1 and plain in (s_fill, s_empty, "\u2502")


def _is_focusable_anchor_content(inner_markup: str) -> bool:
    """
    Esclude i singoli caratteri '#' e '-' delle barre degli slider dal conteggio degli
    anchor non-slider così hover e focus tastiera rimangono allineati.
    """
    plain = Canvas.strip_tags(inner_markup).strip()
    if not plain:
        return False
    return not _is_slider_segment_content(inner_markup)


def _parse_line_focus_items(line_str: str) -> tuple[list[dict], dict[int, int]]:
    """
    Unifica ogni barra Slider (con eventuali pulsanti [-] / [+]) in un singolo target
    navigabile da tastiera, restituendo `(items, match_idx_to_focus_idx)`.
    """
    matches = list(_ANCHOR_RE.finditer(line_str))
    if not matches:
        return [], {}

    asc = THEME["ascii"]
    s_fill = str(asc.get("slider_fill") or "#")[0]
    b_l, b_r = (asc.get("button_brackets") or ["[", "]"])[:2]
    dec_lbl = f"{b_l}-{b_r}"
    inc_lbl = f"{b_l}+{b_r}"

    parsed = []
    for m_idx, m in enumerate(matches):
        uri = m.group(2)
        inner = m.group(3)
        plain = Canvas.strip_tags(inner).strip()
        prefix = uri.rsplit(":", 1)[0] if ":" in uri else ""
        parsed.append({
            "m_idx": m_idx,
            "start": m.start(),
            "uri": uri,
            "plain": plain,
            "prefix": prefix,
            "is_seg": _is_slider_segment_content(inner),
            "is_filled": (plain == s_fill),
        })

    items = []
    match_to_focus: dict[int, int] = {}
    i = 0
    n = len(parsed)
    while i < n:
        cur = parsed[i]
        seg_start = None
        dec_uri = None
        if cur["is_seg"] and cur["prefix"]:
            seg_start = i
        elif (
            cur["plain"] == dec_lbl
            and cur["prefix"]
            and i + 1 < n
            and parsed[i + 1]["is_seg"]
            and parsed[i + 1]["prefix"] == cur["prefix"]
        ):
            dec_uri = cur["uri"]
            seg_start = i + 1

        if seg_start is not None:
            sl_prefix = parsed[seg_start]["prefix"]
            first_m = i if (dec_uri is not None and seg_start == i + 1) else seg_start
            j = seg_start
            while j < n and parsed[j]["is_seg"] and parsed[j]["prefix"] == sl_prefix:
                j += 1
            if (j - seg_start) >= 2:
                segs = parsed[seg_start:j]
                inc_uri = None
                end_i = j
                if j < n and parsed[j]["plain"] == inc_lbl and parsed[j]["prefix"] == sl_prefix:
                    inc_uri = parsed[j]["uri"]
                    end_i = j + 1
                meta_uris = get_slider_step_uris(sl_prefix)
                if meta_uris is not None:
                    dec_uri, inc_uri = meta_uris
                elif dec_uri is None or inc_uri is None:
                    seg_vals = []
                    for sg in segs:
                        try:
                            seg_vals.append(int(sg["uri"].rsplit(":", 1)[1]))
                        except Exception:
                            seg_vals.append(0)
                    step_est = abs(seg_vals[1] - seg_vals[0]) if len(seg_vals) >= 2 else 5
                    last_f = max((idx for idx, sg in enumerate(segs) if sg["is_filled"]), default=-1)
                    if dec_uri is None:
                        dv = max(0, seg_vals[0] - step_est) if last_f <= 0 else seg_vals[last_f - 1]
                        dec_uri = f"{sl_prefix}:{dv}"
                    if inc_uri is None:
                        iv = seg_vals[min(len(seg_vals) - 1, last_f + 1)]
                        inc_uri = f"{sl_prefix}:{iv}"

                f_idx = len(items)
                for k in range(first_m, end_i):
                    match_to_focus[parsed[k]["m_idx"]] = f_idx
                col_pos = len(Canvas.strip_tags(line_str[: parsed[seg_start]["start"]]))
                items.append({
                    "uri": f"{sl_prefix}:slider",
                    "col": col_pos,
                    "is_slider": True,
                    "dec_uri": dec_uri,
                    "inc_uri": inc_uri,
                    "action_prefix": sl_prefix,
                    "first_m_idx": first_m,
                    "last_m_idx": end_i - 1,
                    "bar_first_m_idx": seg_start,
                    "bar_last_m_idx": j - 1,
                })
                i = end_i
                continue

        if cur["plain"]:
            f_idx = len(items)
            match_to_focus[cur["m_idx"]] = f_idx
            col_pos = len(Canvas.strip_tags(line_str[: cur["start"]]))
            items.append({
                "uri": cur["uri"],
                "col": col_pos,
                "is_slider": False,
                "dec_uri": "",
                "inc_uri": "",
                "action_prefix": "",
                "first_m_idx": i,
                "last_m_idx": i,
            })
        i += 1

    return items, match_to_focus


def _disable_label_context_menu(lbl: Gtk.Label) -> None:
    """Suppress GTK3's built-in right-click 'Open Link' / 'Copy Link Address' popup on Gtk.Label."""
    lbl.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK)
    lbl.connect("button-press-event", lambda _w, ev: True if (ev.button != 1 or ev.triggers_context_menu()) else False)
    lbl.connect("button-release-event", lambda _w, ev: True if ev.button != 1 else False)
    lbl.connect("popup-menu", lambda *_: True)
    lbl.connect("populate-popup", lambda _l, m: (m.foreach(lambda w: m.remove(w)), m.hide()) and None)


_BORDER_CHARS_NO_CUTOUT = frozenset((
    "\u256d", "\u256e", "\u2570", "\u256f",
    "\u250c", "\u2510", "\u2514", "\u2518",
    "\u251c", "\u2524", "\u2500", "\u200b",
    "\u2002", "\u2003", "\u2004", "\u2005",
    "\u2006", "\u2007", "\u2008", "\u2009", "\u200a", "\ufeff",
))


def _layout_line_cutout_spans(lay, line_idx: int) -> list[tuple[float, float]]:
    """Return horizontal `(x0, x1)` pixel spans of non-border content on line `line_idx`."""
    if lay is None:
        return []
    txt = lay.get_text() or ""
    lines = txt.split("\n")
    if line_idx < 0 or line_idx >= len(lines):
        return []
    line_s = lines[line_idx]
    if not line_s:
        return []
    line_b0 = len("\n".join(lines[:line_idx]).encode("utf-8")) + (1 if line_idx > 0 else 0)
    spans: list[tuple[float, float]] = []
    run_start_b: int | None = None
    last_char_b: int | None = None
    cur_b = line_b0
    for ch in line_s:
        ch_len = len(ch.encode("utf-8"))
        if ch in _BORDER_CHARS_NO_CUTOUT:
            if run_start_b is not None and last_char_b is not None:
                ps = lay.index_to_pos(run_start_b)
                pe = lay.index_to_pos(last_char_b)
                x0 = min(ps.x, ps.x + ps.width) / Pango.SCALE
                x1 = max(pe.x, pe.x + pe.width) / Pango.SCALE
                if x1 > x0 + 0.5:
                    spans.append((x0, x1))
                run_start_b = None
                last_char_b = None
        else:
            if run_start_b is None:
                run_start_b = cur_b
            last_char_b = cur_b
        cur_b += ch_len
    if run_start_b is not None and last_char_b is not None:
        ps = lay.index_to_pos(run_start_b)
        pe = lay.index_to_pos(last_char_b)
        x0 = min(ps.x, ps.x + ps.width) / Pango.SCALE
        x1 = max(pe.x, pe.x + pe.width) / Pango.SCALE
        if x1 > x0 + 0.5:
            spans.append((x0, x1))
    return spans


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
        embedded_widget: Gtk.Widget = None,
        footer_box_model: Box = None,
    ):
        super().__init__()
        self.set_selectable(False)
        self.set_can_focus(False)
        self.box_model = box_model
        self.canvas = canvas
        self.on_link = on_link
        self.on_row_click = on_row_click
        self.pixbuf = pixbuf
        self.embedded_widget = embedded_widget
        self.footer_box_model = footer_box_model
        self.footer_lbl = None
        self.is_hovered = False
        self.hover_enable = hover_enable and not getattr(box_model, "is_divider", False)
        self.on_hover_change = on_hover_change
        self.on_anchor_hover_change = None
        self.on_pointer_activity = on_pointer_activity
        self.on_scroll_intercept = None
        self._link_activated = False
        self._button_down = False
        self._img_widget = None
        self._last_markup = ""
        self._focused_anchor_idx = None
        self._hovered_anchor_idx = None
        self._last_pointer_lbl_xy = None
        self._anchor_byte_spans = []
        self._slider_byte_spans = []
        self._last_drag_slider_uri = None
        self._press_handled_slider_uri = None
        self._active_drag_slider = None
        self._last_drag_ts = 0.0
        self._scroll_accum = 0.0
        self._last_scroll_ev_time = 0
        self._last_discrete_scroll_ts = 0.0
        self._last_smooth_wheel_ts = 0.0
        self.on_scroll_can_consume = None
        self._passthrough_scroll_ev_time = 0
        self._bg_byte_spans = []
        self._footer_bg_byte_spans = []
        self._focusable_links = []
        self._cached_crop = None
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
            | Gdk.EventMask.SCROLL_MASK
            | Gdk.EventMask.SMOOTH_SCROLL_MASK
        )
        self.lbl.connect("activate-link", self._handle_link)
        self.lbl.connect("motion-notify-event", self._on_motion)
        self.lbl.connect("scroll-event", self._on_scroll)
        self.lbl.connect("event-after", self._on_event_after)
        _disable_label_context_menu(self.lbl)

        content_widget = self._wrap_pixbuf(self.lbl) if self.pixbuf else self.lbl
        if self.embedded_widget is not None:
            vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            vbox.pack_start(content_widget, False, False, 0)
            self.embedded_widget._ref_label = self.lbl
            self.embedded_widget.set_halign(Gtk.Align.FILL)
            vbox.pack_start(self.embedded_widget, False, False, 0)
            if self.footer_box_model is not None:
                self.footer_box_model.open_top = True
                self.footer_lbl = Gtk.Label()
                self.footer_lbl.set_xalign(0.0)
                self.footer_lbl.set_line_wrap(False)
                self.footer_lbl.set_can_focus(False)
                self.footer_lbl.add_events(
                    Gdk.EventMask.SCROLL_MASK | Gdk.EventMask.SMOOTH_SCROLL_MASK
                )
                self.footer_lbl.connect("activate-link", self._handle_link)
                self.footer_lbl.connect("scroll-event", self._on_scroll)
                _disable_label_context_menu(self.footer_lbl)
                vbox.pack_start(self.footer_lbl, False, False, 0)
            content_widget = vbox
        self.refresh_markup()

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
        self.eb.set_app_paintable(True)
        self.eb.set_above_child(False)
        self.eb.set_can_focus(False)
        self.eb.add_events(ev_mask)
        self.eb.connect("draw", self._on_draw_box_bg)
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
        slider_spans = []
        focus_links = []
        if not raw:
            self._anchor_byte_spans = spans
            self._slider_byte_spans = slider_spans
            self._focusable_links = focus_links
            return

        asc = THEME["ascii"]
        b_l, b_r = (asc.get("slider_brackets") or ["[", "]"])[:2]
        bl_bytes = len(b_l.encode("utf-8"))
        br_bytes = len(b_r.encode("utf-8"))

        lines = raw.split("\n")
        cur_byte = 0
        base_focus = 0
        for l_idx, line_str in enumerate(lines):
            line_start_b = cur_byte
            line_items, _ = _parse_line_focus_items(line_str)
            pos = 0
            match_byte_ranges = []
            for m in _ANCHOR_RE.finditer(line_str):
                before_plain = html.unescape(_ANY_TAG_RE.sub("", line_str[pos : m.start()]))
                cur_byte += len(before_plain.encode("utf-8"))
                inner = m.group(3)
                inner_plain = html.unescape(_ANY_TAG_RE.sub("", inner))
                inner_bytes = len(inner_plain.encode("utf-8"))
                m_sb, m_eb = cur_byte, cur_byte + inner_bytes
                match_byte_ranges.append((m_sb, m_eb))
                if _is_slider_segment_content(inner):
                    slider_spans.append((m_sb, m_eb, m.group(2)))
                cur_byte += inner_bytes
                pos = m.end()
            tail_plain = html.unescape(_ANY_TAG_RE.sub("", line_str[pos:]))
            cur_byte += len(tail_plain.encode("utf-8"))
            line_end_b = cur_byte

            for f_local, it in enumerate(line_items):
                f_idx = base_focus + f_local
                fm = it["first_m_idx"]
                lm = it["last_m_idx"]
                if 0 <= fm < len(match_byte_ranges) and 0 <= lm < len(match_byte_ranges):
                    sb = match_byte_ranges[fm][0]
                    eb = match_byte_ranges[lm][1]
                    if it["is_slider"]:
                        if fm == it["bar_first_m_idx"]:
                            sb = max(line_start_b, sb - bl_bytes)
                        if lm == it["bar_last_m_idx"]:
                            eb = min(line_end_b, eb + br_bytes)
                    spans.append((sb, eb, f_idx))
                focus_links.append({
                    "anchor_idx": f_idx,
                    "uri": it["uri"],
                    "line": l_idx,
                    "col": it["col"],
                    "is_slider": it["is_slider"],
                    "dec_uri": it["dec_uri"],
                    "inc_uri": it["inc_uri"],
                    "action_prefix": it["action_prefix"],
                })

            base_focus += len(line_items)
            if l_idx < len(lines) - 1:
                cur_byte += 1

        self._anchor_byte_spans = spans
        self._slider_byte_spans = slider_spans
        self._focusable_links = focus_links

    def _drag_slider_from_xy(self, widget: Gtk.Widget, ev_x: float, ev_y: float, immediate: bool = False) -> bool:
        if not self._button_down or not self._slider_byte_spans or not self.on_link:
            return False
        try:
            if widget is not self.lbl:
                coords = _unpack_translated_coords(widget.translate_coordinates(self.lbl, int(ev_x), int(ev_y)))
                if coords is None:
                    return False
                lx, ly = coords
            else:
                lx, ly = int(ev_x), int(ev_y)
            layout = self.lbl.get_layout()
            if layout is None:
                return False
            lbl_alloc = self.lbl.get_allocation()
            off_x, off_y = self.lbl.get_layout_offsets()
            rel_x = int(lx) - max(0, int(off_x - lbl_alloc.x))
            rel_y = int(ly) - max(0, int(off_y - lbl_alloc.y))

            if not immediate and self._active_drag_slider is not None:
                prefix, x_min, x_max, uris = self._active_drag_slider
                if prefix and not is_slider_enabled(prefix):
                    self._link_activated = True
                    return True
                if uris:
                    frac = max(0.0, min(1.0, (float(rel_x) - x_min) / max(1.0, x_max - x_min)))
                    idx = int(round(frac * (len(uris) - 1)))
                    uri = uris[idx]
                    if uri != self._last_drag_slider_uri:
                        now = time.monotonic()
                        if (now - self._last_drag_ts) < 0.025:
                            return True
                        self._last_drag_ts = now
                        self._last_drag_slider_uri = uri
                        self._link_activated = True
                        if prefix:
                            set_slider_meta_value(prefix, uri.rpartition(":")[2])
                        self.on_link(uri)
                    return True

            inside, byte_idx, _ = layout.xy_to_index(int(rel_x * Pango.SCALE), int(rel_y * Pango.SCALE))
            if not inside:
                return False
            for sb, eb, uri in self._slider_byte_spans:
                if sb <= byte_idx < eb:
                    prefix, _, val_s = uri.rpartition(":")
                    if prefix and not is_slider_enabled(prefix):
                        self._link_activated = True
                        return True
                    same = [s for s in self._slider_byte_spans if s[2].rpartition(":")[0] == prefix]
                    if len(same) >= 2:
                        p0 = layout.index_to_pos(same[0][0])
                        p1 = layout.index_to_pos(max(same[-1][0], same[-1][1] - 1))
                        x_min = p0.x / float(Pango.SCALE)
                        x_max = (p1.x + p1.width) / float(Pango.SCALE)
                        self._active_drag_slider = (prefix, x_min, x_max, [s[2] for s in same])
                    if uri != self._last_drag_slider_uri:
                        now = time.monotonic()
                        if not immediate and (now - self._last_drag_ts) < 0.025:
                            return True
                        self._last_drag_ts = now
                        self._last_drag_slider_uri = uri
                        if immediate:
                            self._press_handled_slider_uri = uri
                        self._link_activated = True
                        if prefix:
                            set_slider_meta_value(prefix, val_s)
                        self.on_link(uri)
                    return True
        except Exception:
            pass
        return False

    def _update_hovered_anchor_from_xy(
        self,
        widget: Gtk.Widget,
        ev_x: float,
        ev_y: float,
        from_motion: bool = True,
    ) -> None:
        if self._button_down or not self._anchor_byte_spans:
            return
        try:
            if widget is not self.lbl:
                coords = _unpack_translated_coords(widget.translate_coordinates(self.lbl, int(ev_x), int(ev_y)))
                if coords is None:
                    return
                lx, ly = coords
            else:
                lx, ly = int(ev_x), int(ev_y)

            self._last_pointer_lbl_xy = (lx, ly)
            layout = self.lbl.get_layout()
            if layout is None:
                return
            lbl_alloc = self.lbl.get_allocation()
            off_x, off_y = self.lbl.get_layout_offsets()
            rel_x = int(lx) - max(0, int(off_x - lbl_alloc.x))
            rel_y = int(ly) - max(0, int(off_y - lbl_alloc.y))
            inside, byte_idx, _ = layout.xy_to_index(int(rel_x * Pango.SCALE), int(rel_y * Pango.SCALE))
            found_idx = None
            if inside:
                for sb, eb, f_idx in self._anchor_byte_spans:
                    if sb <= byte_idx < eb:
                        found_idx = f_idx
                        break
            if found_idx != self._hovered_anchor_idx:
                self._hovered_anchor_idx = found_idx
                self._apply_label_markup()
                if callable(self.on_anchor_hover_change):
                    self.on_anchor_hover_change(self, found_idx, from_motion)
            elif from_motion and callable(self.on_anchor_hover_change):
                self.on_anchor_hover_change(self, found_idx, True)
        except Exception:
            pass

    def get_focusable_links(self) -> list[dict]:
        """
        Estrae tutti i target navigabili da tastiera nella card, unificando ogni barra
        Slider (con eventuali [-] / [+]) in un singolo controllo azionabile via Frecce/Scroll.
        """
        return list(self._focusable_links)

    def get_anchor_rect_in(self, target_widget: Gtk.Widget, anchor_idx: int) -> tuple[int, int, int, int] | None:
        """Return ``(x, y, w, h)`` of ``anchor_idx`` translated into ``target_widget`` coordinates."""
        if anchor_idx is None or target_widget is None:
            return None
        layout = self.lbl.get_layout()
        if layout is None:
            return None
        span = next(((sb, eb) for sb, eb, f_idx in self._anchor_byte_spans if f_idx == anchor_idx), None)
        if span is None:
            return None
        sb, eb = span
        try:
            p0 = layout.index_to_pos(sb)
            p1 = layout.index_to_pos(max(sb, eb - 1))
            lbl_alloc = self.lbl.get_allocation()
            off_x, off_y = self.lbl.get_layout_offsets()
            dx = max(0, int(off_x - lbl_alloc.x))
            dy = max(0, int(off_y - lbl_alloc.y))
            lx0 = int(round(p0.x / Pango.SCALE)) + dx
            ly0 = int(round(p0.y / Pango.SCALE)) + dy
            lx1 = int(round((p1.x + p1.width) / Pango.SCALE)) + dx
            lh = max(14, int(round(max(p0.height, p1.height) / Pango.SCALE)))
            coords = _unpack_translated_coords(self.lbl.translate_coordinates(target_widget, lx0, ly0))
            if coords is None:
                return None
            tx, ty = coords
            return (int(tx), int(ty), max(16, lx1 - lx0), lh)
        except Exception:
            return None

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
        if not raw:
            self._bg_byte_spans = []
            self.lbl.set_markup("")
            return
        if self._focused_anchor_idx is None and self._hovered_anchor_idx is None:
            stripped, bg_spans = _extract_and_strip_bg_spans(raw)
            self._bg_byte_spans = bg_spans
            self.lbl.set_markup(f"{_NO_LIGA_SPAN_OPEN}{stripped}</span>" if stripped else "")
            if getattr(self, "eb", None) is not None:
                self.eb.queue_draw()
            return

        p = get_semantic_palette()
        asc = THEME["ascii"]
        s_fill = str(asc.get("slider_fill") or "#")[0]
        s_empty = str(asc.get("slider_empty") or "-")[0]
        # MEMENTO GOLDEN STANDARD (§5.1 & §8): I segmenti dello Slider devono passare da
        # Canvas.sanitize_markup anche in hover/focus, così glifi blocco come '█'/'░'
        # (RobCo UOS, Ghost Net, Phosphor P31) mantengono la scala 62% senza allargare il Box.
        san_fill = Canvas.sanitize_markup(Canvas.escape(s_fill))
        san_empty = Canvas.sanitize_markup(Canvas.escape(s_empty))
        target_focus = self._focused_anchor_idx
        target_hover = self._hovered_anchor_idx
        c_bg = p["intent"]
        c_fg = p["intent_bg"]
        c_sel = p["focus"]
        focus_offset = [0]
        active_inline = get_active_inline_entry()
        active_inline_uri = get_active_inline_uri()

        out_lines = []
        for line_str in raw.split("\n"):
            line_items, match_to_focus = _parse_line_focus_items(line_str)
            base_focus = focus_offset[0]
            focus_offset[0] += len(line_items)
            local_m = [0]

            def _repl(m, _m2f=match_to_focus, _bf=base_focus, _lm=local_m):
                m_idx = _lm[0]
                _lm[0] += 1
                open_a, _uri, inner, close_a = m.group(1), m.group(2), m.group(3), m.group(4)
                f_local = _m2f.get(m_idx)
                f_idx = (_bf + f_local) if f_local is not None else None
                if active_inline and (
                    (active_inline_uri and _uri == active_inline_uri)
                    or (
                        "background=" in inner
                        and inner.count("<span") >= 3
                        and ("\u258f" in inner or "underline_color=" in inner or "strikethrough_color=" in inner)
                    )
                ):
                    return m.group(0)
                if f_idx is not None and f_idx == target_focus:
                    if _is_slider_segment_content(inner):
                        raw_ch = Canvas.strip_tags(inner).strip()
                        is_empty = (raw_ch == s_empty)
                        seg_ch = san_fill if raw_ch == s_fill else (san_empty if is_empty else Canvas.sanitize_markup(Canvas.escape(raw_ch)))
                        m_col = _SEG_COLOR_RE.search(inner)
                        orig_col = m_col.group(1) if m_col else None
                        seg_bg = p["structure_bg"] if is_empty else p["intent_bg"]
                        seg_fg = p["intent"] if is_empty else (orig_col or p["intent"])
                        return f"{open_a}<span background='{seg_bg}' foreground='{seg_fg}' underline='none'>{seg_ch}</span>{close_a}"
                    clean_inner = _STRIP_BG_ATTR_RE.sub("", _INNER_COLOR_RE.sub("", inner))
                    return f"{open_a}<span background='{c_bg}' foreground='{c_fg}'>{clean_inner}</span>{close_a}"
                if f_idx is not None and f_idx == target_hover:
                    if _is_slider_segment_content(inner):
                        raw_ch = Canvas.strip_tags(inner).strip()
                        is_empty = (raw_ch == s_empty)
                        seg_ch = san_fill if raw_ch == s_fill else (san_empty if is_empty else Canvas.sanitize_markup(Canvas.escape(raw_ch)))
                        m_col = _SEG_COLOR_RE.search(inner)
                        orig_col = m_col.group(1) if m_col else None
                        seg_fg = p["schema"] if is_empty else (orig_col or p["intent"])
                        return f"{open_a}<span background='{p['intent_bg']}' foreground='{seg_fg}' underline='none'>{seg_ch}</span>{close_a}"
                    # MEMENTO GOLDEN STANDARD (§4.1.7 & §4.3): I comandi danger_soft usano
                    # interrupt_rest a riposo e virano in interrupt acceso in hover; tutti gli
                    # altri controlli interattivi virano in focus (#ffffff) sotto il puntatore.
                    hover_col = p["interrupt"] if p["interrupt_rest"].lower() in inner.lower() else c_sel
                    clean_inner = _INNER_COLOR_RE.sub("", inner)
                    clean_inner = _INNER_WEIGHT_RE.sub("", clean_inner)
                    return f"{open_a}<span color='{hover_col}'>{clean_inner}</span>{close_a}"
                return m.group(0)

            out_lines.append(_ANCHOR_RE.sub(_repl, line_str))

        highlighted = "\n".join(out_lines)
        stripped, bg_spans = _extract_and_strip_bg_spans(highlighted)
        self._bg_byte_spans = bg_spans
        self.lbl.set_markup(f"{_NO_LIGA_SPAN_OPEN}{stripped}</span>" if stripped else "")
        if getattr(self, "eb", None) is not None:
            self.eb.queue_draw()

    def _fit_and_crop_pixbuf(self, alloc_w: int = 0, alloc_h: int = 0, scale_factor: int = 1) -> GdkPixbuf.Pixbuf:
        orig_w = self.pixbuf.get_width()
        orig_h = self.pixbuf.get_height()
        if orig_w <= 0 or orig_h <= 0:
            return self.pixbuf

        raw_m = getattr(self, "_last_markup", "") or ""
        n_lines = max(3, raw_m.count("\n") + 1) if raw_m else 3
        if alloc_w > 40 and alloc_h > 30:
            lbl_w, lbl_h = alloc_w, alloc_h
        else:
            lbl_layout = self.lbl.get_layout() if hasattr(self, "lbl") else None
            if lbl_layout is not None and (lbl_layout.get_text() or "").strip():
                lbl_w, lbl_h = lbl_layout.get_pixel_size()
            else:
                s = Canvas.sanitize_markup(raw_m)
                pango_only = re.sub(r"</?a\b[^>]*>", "", s, flags=re.IGNORECASE)
                self.canvas.layout.set_width(-1)
                try:
                    self.canvas.layout.set_markup(f"{_NO_LIGA_SPAN_OPEN}{pango_only}</span>" if pango_only else "", -1)
                    lbl_w, lbl_h = self.canvas.layout.get_pixel_size()
                except Exception:
                    lbl_w = int(self.canvas.total_px)
                    lbl_h = int(n_lines * self.canvas.char_h)

        line_h = lbl_h / float(n_lines)
        sf = max(1, int(scale_factor or 1))
        inner_w = max(24, int(round(lbl_w - (self.canvas.char_w * 2.5)))) * sf
        inner_h = max(18, int(round(lbl_h - (line_h * 2.15)))) * sf

        cached = self._cached_crop
        if cached is not None and cached[0] is self.pixbuf and cached[1] == inner_w and cached[2] == inner_h and cached[3] == sf:
            return cached[4]

        if orig_w == inner_w and orig_h == inner_h:
            res = self.pixbuf
        else:
            scale = max(inner_w / float(orig_w), inner_h / float(orig_h))
            scaled_w = max(inner_w, int(round(orig_w * scale)))
            scaled_h = max(inner_h, int(round(orig_h * scale)))
            scaled = self.pixbuf.scale_simple(scaled_w, scaled_h, GdkPixbuf.InterpType.BILINEAR)
            off_x = max(0, (scaled_w - inner_w) // 2)
            off_y = max(0, (scaled_h - inner_h) // 2)
            res = scaled.new_subpixbuf(off_x, off_y, inner_w, inner_h)
        self._cached_crop = (self.pixbuf, inner_w, inner_h, sf, res)
        return res

    def _wrap_pixbuf(self, label_widget: Gtk.Label) -> Gtk.Overlay:
        overlay = Gtk.Overlay()
        overlay.add(label_widget)
        img = Gtk.Image()
        self._img_widget = img
        self._last_crop_size = (0, 0, 1)
        img.set_halign(Gtk.Align.CENTER)
        img.set_valign(Gtk.Align.CENTER)
        overlay.add_overlay(img)
        overlay.set_overlay_pass_through(img, True)

        def _on_lbl_alloc(_lbl, alloc):
            if alloc.width > 40 and alloc.height > 30:
                sf = max(1, int(img.get_scale_factor() or 1))
                cur_sz = (alloc.width, alloc.height, sf)
                if cur_sz != self._last_crop_size:
                    self._last_crop_size = cur_sz
                    pb = self._fit_and_crop_pixbuf(alloc.width, alloc.height, scale_factor=sf)
                    if sf > 1 and pb is not None:
                        try:
                            surf = Gdk.cairo_surface_create_from_pixbuf(pb, sf, img.get_window())
                            img.set_from_surface(surf)
                            return
                        except Exception:
                            pass
                    img.set_from_pixbuf(pb)

        label_widget.connect("size-allocate", _on_lbl_alloc)
        return overlay

    def set_hovered_visual(self, hovered: bool) -> None:
        self.is_hovered = bool(hovered)
        if not hovered:
            self._last_pointer_lbl_xy = None
            if self._hovered_anchor_idx is not None:
                self._hovered_anchor_idx = None
                if not self._button_down:
                    self._apply_label_markup()
        if not self.hover_enable or self._button_down:
            return
        new_sel = bool(hovered)
        if getattr(self.box_model, "selected", False) != new_sel:
            self.box_model.selected = new_sel
            self.refresh_markup()

    @staticmethod
    def _trace_cw_rounded_rect(cr, x: float, y: float, w: float, h: float, r_tl: float, r_tr: float, r_br: float, r_bl: float) -> None:
        r_tl = max(0.0, min(r_tl, w * 0.49, h * 0.49))
        r_tr = max(0.0, min(r_tr, w * 0.49, h * 0.49))
        r_br = max(0.0, min(r_br, w * 0.49, h * 0.49))
        r_bl = max(0.0, min(r_bl, w * 0.49, h * 0.49))
        cr.new_sub_path()
        cr.move_to(x + r_tl, y)
        cr.line_to(x + w - r_tr, y)
        if r_tr > 0.3:
            cr.arc(x + w - r_tr, y + r_tr, r_tr, -math.pi / 2.0, 0.0)
        cr.line_to(x + w, y + h - r_br)
        if r_br > 0.3:
            cr.arc(x + w - r_br, y + h - r_br, r_br, 0.0, math.pi / 2.0)
        cr.line_to(x + r_bl, y + h)
        if r_bl > 0.3:
            cr.arc(x + r_bl, y + h - r_bl, r_bl, math.pi / 2.0, math.pi)
        cr.line_to(x, y + r_tl)
        if r_tl > 0.3:
            cr.arc(x + r_tl, y + r_tl, r_tl, math.pi, 1.5 * math.pi)
        cr.close_path()

    def _draw_bg_spans_on_layout(self, cr, layout, off_y: float, bg_spans: list) -> None:
        if not bg_spans or layout is None:
            return
        text_bytes = (layout.get_text() or "").encode("utf-8")
        lines_info = []
        it = layout.get_iter()
        while True:
            ln = it.get_line_readonly()
            l_s = ln.start_index
            l_e = l_s + ln.length
            y0_u, y1_u = it.get_line_yrange()
            b_u = it.get_baseline()
            lines_info.append((l_s, l_e, y0_u / Pango.SCALE, y1_u / Pango.SCALE, b_u / Pango.SCALE))
            if not it.next_line():
                break

        cap_mid_rel = float(getattr(self.canvas, "cap_mid_from_baseline", -3.5))
        cap_ink_h = float(getattr(self.canvas, "cap_ink_h", 11.0))
        tc = THEME.get("ascii", {}).get("tab_caps", ("\u258f", "\u2595"))
        use_cairo_caps = (isinstance(tc, (list, tuple)) and len(tc) >= 2 and (str(tc[0]), str(tc[1])) == ("\u258f", "\u2595"))
        s_cur = str(THEME.get("ascii", {}).get("search_cursor") or "\u2502")[:1]

        for b_s, b_e, bg_hex, h_mult, cap_col in bg_spans:
            if h_mult < 0.0:
                if not cap_col:
                    continue
            elif not bg_hex:
                continue
            target_line = None
            for l_info in lines_info:
                if l_info[0] <= b_s < l_info[1]:
                    target_line = l_info
                    break
            if target_line is None:
                continue
            l_s, l_e, y0_px, y1_px, base_px = target_line
            ce = min(b_e, l_e)
            if ce <= b_s:
                continue
            ps = layout.index_to_pos(b_s)
            px0 = min(ps.x, ps.x + ps.width) / Pango.SCALE
            if ce < l_e:
                pe = layout.index_to_pos(ce)
                px1 = min(pe.x, pe.x + pe.width) / Pango.SCALE
            else:
                k = ce - 1
                while k > b_s and (text_bytes[k] & 0xC0) == 0x80:
                    k -= 1
                pp = layout.index_to_pos(k)
                px1 = max(pp.x, pp.x + pp.width) / Pango.SCALE
            if px1 <= px0 + 0.5:
                continue
            y_mid = off_y + base_px + cap_mid_rel
            line_h = max(8.0, y1_px - y0_px)
            std_pill_h = min(cap_ink_h + 2.0, max(10.0, line_h - 2.0))
            if h_mult < 0.0:
                py0 = y_mid - std_pill_h * 0.5
                cr_r, cr_g, cr_b, _ = _parse_rgba_components(cap_col)
                cr.set_source_rgba(cr_r / 255.0, cr_g / 255.0, cr_b / 255.0, 1.0)
                if s_cur == "_":
                    cr.rectangle(px0, py0 + std_pill_h - 2.0, max(4.0, px1 - px0), 2.0)
                else:
                    edge_x = px0 if h_mult == -1.0 else px1
                    cx = max(0.0, round(edge_x) - (1.0 if h_mult == -1.0 else 0.0))
                    cr.rectangle(cx, round(py0 + 1.0), 1.8, max(8.0, round(std_pill_h - 2.0)))
                cr.fill()
                continue
            pill_h = std_pill_h * float(h_mult)
            py0 = y_mid - pill_h * 0.5
            bs_pill = str(THEME.get("ascii", {}).get("box_style", "rounded") or "rounded")
            r_c = (2.0 if h_mult > 1.0 else 1.5) if bs_pill in ("rounded", "dashed") else 0.0
            hr, hg, hb, ha = _parse_rgba_components(bg_hex)
            if not bg_hex.lower().startswith("rgb") and "," not in bg_hex:
                ha = 1.0
            cr.set_source_rgba(hr / 255.0, hg / 255.0, hb / 255.0, ha)
            self._trace_cw_rounded_rect(cr, px0, py0, px1 - px0, pill_h, r_c, r_c, r_c, r_c)
            cr.fill()
            if h_mult > 1.0 and cap_col and use_cairo_caps:
                cr_r, cr_g, cr_b, _ = _parse_rgba_components(cap_col)
                cr.set_source_rgba(cr_r / 255.0, cr_g / 255.0, cr_b / 255.0, 1.0)
                cr.rectangle(px0, py0, 2.0, pill_h)
                cr.rectangle(max(px0, px1 - 2.0), py0, 2.0, pill_h)
                cr.fill()

    def _on_draw_box_bg(self, eb: Gtk.Widget, cr) -> bool:
        bs = str(THEME.get("ascii", {}).get("box_style", "rounded") or "rounded")
        is_div = bool(getattr(self.box_model, "is_divider", False))
        if is_div and bs != "dashed":
            return False
        layout = self.lbl.get_layout()
        if layout is None:
            return False

        box_op = get_box_opacity()
        n_lines = max(1, layout.get_line_count())
        lbl_w, lbl_h = layout.get_pixel_size()
        char_w = float(self.canvas.char_w)
        char_h = float(self.canvas.char_h)

        # Resolve Y origin of top label inside EventBox
        res_top = self.lbl.translate_coordinates(eb, 0, 0)
        top_off_y = float(res_top[-1]) if (res_top and len(res_top) >= 2 and res_top[0] is not False) else 0.0
        line0 = layout.get_line(0)
        if line0 is not None:
            _, l0_ext = line0.get_extents()
            line_0_h = l0_ext.height / Pango.SCALE
        else:
            line_0_h = char_h

        # Resolve bottom edge across optional embedded widget + footer label
        bot_off_y = top_off_y
        f_layout = None
        if self.footer_lbl is not None and self.footer_lbl.get_layout() is not None:
            f_layout = self.footer_lbl.get_layout()
            res_bot = self.footer_lbl.translate_coordinates(eb, 0, 0)
            _, f_h = f_layout.get_pixel_size()
            bot_off_y = float(res_bot[-1]) if (res_bot and len(res_bot) >= 2 and res_bot[0] is not False) else float(eb.get_allocated_height() - f_h)
            total_h = bot_off_y + float(f_h)
            f_n = max(1, f_layout.get_line_count())
            last_line = f_layout.get_line(f_n - 1)
            last_line_h = (last_line.get_extents()[1].height / Pango.SCALE) if last_line is not None else char_h
        else:
            if self.embedded_widget is not None:
                total_h = max(top_off_y + float(lbl_h), float(eb.get_allocated_height()))
            else:
                total_h = top_off_y + float(lbl_h)
            last_line = layout.get_line(n_lines - 1)
            last_line_h = (last_line.get_extents()[1].height / Pango.SCALE) if last_line is not None else char_h

        layout_text = layout.get_text() or ""
        first_nl = layout_text.find("\n")
        line0_str = layout_text[:first_nl] if first_nl >= 0 else layout_text
        line0_bytes_len = len(line0_str.encode("utf-8"))

        if len(line0_str) >= 2:
            b_first = 0
            b_last = len(line0_str[:-1].encode("utf-8"))
            p_first = layout.index_to_pos(b_first)
            p_last = layout.index_to_pos(b_last)
            x_left = (p_first.x + p_first.width * 0.5) / Pango.SCALE
            x_right = max(x_left + 2.0, (p_last.x + p_last.width * 0.5) / Pango.SCALE)
        else:
            total_px = float(getattr(self.box_model, "_render_total_px", 0.0) or lbl_w or self.canvas.total_px)
            x_left = char_w * 0.5
            x_right = max(x_left + 2.0, total_px - (char_w * 0.5))

        y_top = top_off_y if getattr(self.box_model, "open_top", False) else (top_off_y + line_0_h * 0.5)
        try:
            dev_sx = float(cr.get_target().get_device_scale()[0])
        except Exception:
            dev_sx = 1.0
        if dev_sx <= 1.01:
            vx0 = math.ceil(x_left) - 0.5
            vx1 = math.ceil(x_right) - 0.5
        else:
            vx0 = (math.ceil(x_left * dev_sx) - 0.5) / dev_sx
            vx1 = (math.ceil(x_right * dev_sx) - 0.5) / dev_sx

        if is_div:
            c_div = THEME["colors"]["structure"]
            dv_r, dv_g, dv_b, _ = _parse_rgba_components(c_div)
            vy_mid = math.floor(y_top) + 0.5
            cr.save()
            cr.set_source_rgba(dv_r / 255.0, dv_g / 255.0, dv_b / 255.0, 1.0)
            cr.set_fill_rule(cairo.FillRule.EVEN_ODD)
            cr.rectangle(vx0 - 4.0, top_off_y - 2.0, (vx1 - vx0) + 8.0, line_0_h + 4.0)
            for cx0, cx1 in _layout_line_cutout_spans(layout, 0):
                cr.rectangle(cx0, top_off_y - 2.0, max(0.0, cx1 - cx0), line_0_h + 4.0)
            cr.clip()
            cr.set_line_width(1.0)
            cr.set_dash([4.0, 2.5])
            cr.move_to(vx0, vy_mid)
            cr.line_to(vx1, vy_mid)
            cr.stroke()
            cr.restore()
            return False

        cairo_close_bot = bool(getattr(self.box_model, "cairo_close_bottom", False))
        is_open_bot = getattr(self.box_model, "open_bottom", False) and (self.footer_lbl is None) and not cairo_close_bot
        if cairo_close_bot:
            toplevel = self.get_toplevel()
            bot_inset = (
                float(toplevel.get_cairo_bottom_inset_px())
                if hasattr(toplevel, "get_cairo_bottom_inset_px")
                else float(last_line_h * 0.5)
            )
            y_bot = max(y_top + 2.0, total_h - 1.0 - bot_inset)
        elif is_open_bot:
            y_bot = total_h
        else:
            y_bot = max(y_top + 2.0, total_h - (last_line_h * 0.5))

        r_base = min(char_w * 0.65, line_0_h * 0.45, (x_right - x_left) * 0.25, (y_bot - y_top) * 0.25)
        if bs in ("rounded", "dashed"):
            r_tl = r_tr = r_br = r_bl = r_base
            rad = r_base
        elif bs == "hybrid":
            cm = getattr(self.box_model, "corner_mask", None) or (False, False, False, False)
            r_tl = r_base if cm[0] else 0.0
            r_tr = r_base if cm[1] else 0.0
            r_br = r_base if cm[2] else 0.0
            r_bl = r_base if cm[3] else 0.0
            rad = 0.0
        else:
            r_tl = r_tr = r_br = r_bl = 0.0
            rad = 0.0
        is_overlay = bool(getattr(self.box_model, "is_overlay", False))
        is_toast = bool(getattr(self.box_model, "is_toast", False))
        is_sys_notif = bool(getattr(self.box_model, "is_sys_notif", False))
        custom_op = getattr(self.box_model, "custom_opacity", None)
        if custom_op is not None:
            box_op = max(0.0, min(1.0, float(custom_op)))
        elif is_sys_notif:
            box_op = get_sys_notif_opacity()
        elif is_toast:
            box_op = get_toast_opacity()
        elif is_overlay:
            box_op = max(0.97, box_op)

        cr.save()
        if is_overlay:
            shadow_a = 0.50 * max(0.18, box_op) if (is_toast or is_sys_notif) else 0.55
            cr.set_source_rgba(0.0, 0.0, 0.0, shadow_a)
            cr.set_fill_rule(cairo.FillRule.EVEN_ODD)
            self._trace_cw_rounded_rect(cr, x_left - 2.0, y_top + 3.0, (x_right - x_left) + 4.0, (y_bot - y_top) + 2.0, r_tl, r_tr, r_br, r_bl)
            self._trace_cw_rounded_rect(cr, x_left, y_top, x_right - x_left, y_bot - y_top, r_tl, r_tr, r_br, r_bl)
            cr.fill()
            cr.set_fill_rule(cairo.FillRule.WINDING)
        if box_op > 0.001:
            r_i, g_i, b_i = get_surface_bg_rgb()
            cr.set_operator(cairo.OPERATOR_OVER)
            cr.set_fill_rule(cairo.FillRule.WINDING)
            cr.set_source_rgba(r_i / 255.0, g_i / 255.0, b_i / 255.0, box_op)

            self._trace_cw_rounded_rect(cr, x_left, y_top, x_right - x_left, y_bot - y_top, r_tl, r_tr, r_br, r_bl)

            if not getattr(self.box_model, "open_top", False) and line_0_h > 1.0:
                p_rad = min(2.5, rad) if rad > 0.5 else 0.0
                byte_spans = getattr(self.box_model, "_top_protrusion_bytes", None)
                if byte_spans and line0_bytes_len > 0:
                    for b_s, b_e in byte_spans:
                        if 0 <= b_s < b_e <= line0_bytes_len:
                            ps = layout.index_to_pos(b_s)
                            xs = ps.x / Pango.SCALE
                            if b_e < line0_bytes_len:
                                pe = layout.index_to_pos(b_e)
                                xe = pe.x / Pango.SCALE
                            else:
                                xe = lbl_w
                            px0 = max(x_left + 1.0, xs)
                            px1 = min(x_right - 1.0, xe)
                            if px1 > px0 + 1.0:
                                self._trace_cw_rounded_rect(cr, px0, top_off_y, px1 - px0, (y_top - top_off_y) + 2.0, p_rad, p_rad, 0.0, 0.0)
                else:
                    for xs, xe in getattr(self.box_model, "_top_protrusions", ()):
                        px0 = max(x_left + 1.0, float(xs))
                        px1 = min(x_right - 1.0, float(xe))
                        if px1 > px0 + 1.0:
                            self._trace_cw_rounded_rect(cr, px0, top_off_y, px1 - px0, (y_top - top_off_y) + 2.0, p_rad, p_rad, 0.0, 0.0)

            bot_prots = getattr(self.box_model, "_bot_protrusions", None)
            bot_prot_bytes = getattr(self.box_model, "_bot_protrusion_bytes", None)
            if not is_open_bot and last_line_h > 1.0 and (bot_prots or bot_prot_bytes):
                p_rad = min(2.5, rad) if rad > 0.5 else 0.0
                b_layout = f_layout if f_layout is not None else layout
                b_text = (b_layout.get_text() or "") if b_layout is not None else ""
                b_nl = b_text.rfind("\n")
                b_line_str = b_text[b_nl + 1:] if b_nl >= 0 else b_text
                b_start = len(b_text[:b_nl + 1].encode("utf-8")) if b_nl >= 0 else 0
                b_len = len(b_line_str.encode("utf-8"))
                b_rect_h = (total_h - y_bot) + 2.0
                if bot_prot_bytes and b_len > 0 and b_layout is not None:
                    for b_s, b_e in bot_prot_bytes:
                        if 0 <= b_s < b_e <= b_len:
                            ps = b_layout.index_to_pos(b_start + b_s)
                            xs = ps.x / Pango.SCALE
                            if b_e < b_len:
                                pe = b_layout.index_to_pos(b_start + b_e)
                                xe = pe.x / Pango.SCALE
                            else:
                                xe = lbl_w
                            px0 = max(x_left + 1.0, xs)
                            px1 = min(x_right - 1.0, xe)
                            if px1 > px0 + 1.0:
                                self._trace_cw_rounded_rect(cr, px0, y_bot - 2.0, px1 - px0, b_rect_h, 0.0, 0.0, p_rad, p_rad)
                else:
                    for xs, xe in (bot_prots or ()):
                        px0 = max(x_left + 1.0, float(xs))
                        px1 = min(x_right - 1.0, float(xe))
                        if px1 > px0 + 1.0:
                            self._trace_cw_rounded_rect(cr, px0, y_bot - 2.0, px1 - px0, b_rect_h, 0.0, 0.0, p_rad, p_rad)

            cr.fill()

        # MEMENTO GOLDEN STANDARD (§5.1.3): Montanti verticali continui tracciati in Cairo
        # per saldare qualsiasi micro-gap verticale tra righe Pango (incluso su TabBar 1.5x e widget embedded).
        if bs in ("rounded", "sharp", "hybrid", "double", "dashed") and y_bot - y_top > 4.0:
            c_brd = self.box_model._resolve_palette()[0]
            br_r, br_g, br_b, _ = _parse_rgba_components(c_brd)
            cr.set_source_rgba(br_r / 255.0, br_g / 255.0, br_b / 255.0, 1.0)
            open_top = bool(getattr(self.box_model, "open_top", False))
            if bs == "dashed":
                vy_top = top_off_y if open_top else (math.floor(y_top) + 0.5)
                vy_bot = total_h if is_open_bot else (math.floor(y_bot) + 0.5)
                w_box = max(4.0, vx1 - vx0)
                h_box = max(4.0, vy_bot - vy_top)
                b_layout = f_layout if f_layout is not None else layout
                b_line_idx = max(0, b_layout.get_line_count() - 1) if b_layout is not None else 0

                cr.save()
                cr.set_fill_rule(cairo.FillRule.EVEN_ODD)
                cr.rectangle(vx0 - 4.0, top_off_y - 4.0, w_box + 8.0, (total_h - top_off_y) + 8.0)
                if not open_top:
                    for cx0, cx1 in _layout_line_cutout_spans(layout, 0):
                        cr.rectangle(cx0, top_off_y - 2.0, max(0.0, cx1 - cx0), line_0_h + 4.0)
                if not is_open_bot and b_layout is not None:
                    for cx0, cx1 in _layout_line_cutout_spans(b_layout, b_line_idx):
                        cr.rectangle(cx0, total_h - last_line_h - 2.0, max(0.0, cx1 - cx0), last_line_h + 4.0)
                cr.clip()

                cr.set_line_width(1.0)
                cr.set_dash([4.0, 2.5])
                if not open_top and not is_open_bot:
                    self._trace_cw_rounded_rect(cr, vx0, vy_top, w_box, h_box, r_tl, r_tr, r_br, r_bl)
                elif open_top and not is_open_bot:
                    cr.new_sub_path()
                    cr.move_to(vx0, vy_top)
                    cr.line_to(vx0, vy_bot - r_bl)
                    if r_bl > 0.3:
                        cr.arc_negative(vx0 + r_bl, vy_bot - r_bl, r_bl, math.pi, math.pi / 2.0)
                    cr.line_to(vx1 - r_br, vy_bot)
                    if r_br > 0.3:
                        cr.arc_negative(vx1 - r_br, vy_bot - r_br, r_br, math.pi / 2.0, 0.0)
                    cr.line_to(vx1, vy_top)
                elif is_open_bot and not open_top:
                    cr.new_sub_path()
                    cr.move_to(vx0, vy_bot)
                    cr.line_to(vx0, vy_top + r_tl)
                    if r_tl > 0.3:
                        cr.arc(vx0 + r_tl, vy_top + r_tl, r_tl, math.pi, 1.5 * math.pi)
                    cr.line_to(vx1 - r_tr, vy_top)
                    if r_tr > 0.3:
                        cr.arc(vx1 - r_tr, vy_top + r_tr, r_tr, -math.pi / 2.0, 0.0)
                    cr.line_to(vx1, vy_bot)
                else:
                    cr.move_to(vx0, vy_top)
                    cr.line_to(vx0, vy_bot)
                    cr.move_to(vx1, vy_top)
                    cr.line_to(vx1, vy_bot)
                cr.stroke()
                cr.restore()

                # Internal dashed section dividers (├...┤ rows)
                lay_groups = [(layout, layout_text, top_off_y)]
                if f_layout is not None:
                    lay_groups.append((f_layout, f_layout.get_text() or "", bot_off_y))
                for lay_g, ltxt_g, off_g in lay_groups:
                    tlines = ltxt_g.split("\n")
                    it_g = lay_g.get_iter()
                    li_g = 0
                    while True:
                        if li_g < len(tlines):
                            ls_g = tlines[li_g]
                            if ls_g.startswith("\u251c") or ls_g.endswith("\u2524"):
                                jy0, jy1 = it_g.get_line_yrange()
                                y_div = math.floor(off_g + (jy0 + jy1) * 0.5 / Pango.SCALE) + 0.5
                                y_r0 = off_g + jy0 / Pango.SCALE
                                y_rh = max(2.0, (jy1 - jy0) / Pango.SCALE)
                                cr.save()
                                cr.set_fill_rule(cairo.FillRule.EVEN_ODD)
                                cr.rectangle(vx0 - 4.0, y_r0 - 2.0, w_box + 8.0, y_rh + 4.0)
                                for cx0, cx1 in _layout_line_cutout_spans(lay_g, li_g):
                                    cr.rectangle(cx0, y_r0 - 2.0, max(0.0, cx1 - cx0), y_rh + 4.0)
                                cr.clip()
                                cr.set_line_width(1.0)
                                cr.set_dash([4.0, 2.5])
                                cr.move_to(vx0, y_div)
                                cr.line_to(vx1, y_div)
                                cr.stroke()
                                cr.restore()
                        li_g += 1
                        if not it_g.next_line():
                            break
            else:
                y_v0_l = y_top if open_top else (y_top + max(2.0, r_tl))
                y_v1_l = y_bot if is_open_bot else (y_bot - max(2.0, r_bl))
                y_v0_r = y_top if open_top else (y_top + max(2.0, r_tr))
                y_v1_r = y_bot if is_open_bot else (y_bot - max(2.0, r_br))
                if y_v1_l > y_v0_l or y_v1_r > y_v0_r:
                    cr.save()
                    if bs == "double":
                        cr.set_line_width(1.0)
                        for dx in (-1.05, 1.05):
                            if y_v1_l > y_v0_l:
                                cr.move_to(vx0 + dx, y_v0_l)
                                cr.line_to(vx0 + dx, y_v1_l)
                            if y_v1_r > y_v0_r:
                                cr.move_to(vx1 + dx, y_v0_r)
                                cr.line_to(vx1 + dx, y_v1_r)
                        cr.stroke()
                    else:
                        cr.set_line_width(1.0)
                        if y_v1_l > y_v0_l:
                            cr.move_to(vx0, y_v0_l)
                            cr.line_to(vx0, y_v1_l)
                        if y_v1_r > y_v0_r:
                            cr.move_to(vx1, y_v0_r)
                            cr.line_to(vx1, y_v1_r)
                        cr.stroke()
                    cr.restore()

        # Vertically centered Cairo highlight pills
        self._draw_bg_spans_on_layout(cr, layout, top_off_y, self._bg_byte_spans)
        if f_layout is not None and self._footer_bg_byte_spans:
            self._draw_bg_spans_on_layout(cr, f_layout, bot_off_y, self._footer_bg_byte_spans)

        if getattr(self.box_model, "side_emblem", None):
            self._draw_side_emblem(cr, layout, x_left, x_right, y_top, y_bot, top_off_y, line_0_h, char_w, char_h, rad)

        cr.restore()
        return False

    def _draw_side_emblem(
        self,
        cr,
        layout,
        x_left: float,
        x_right: float,
        y_top: float,
        y_bot: float,
        top_off_y: float,
        line_0_h: float,
        char_w: float,
        char_h: float,
        rad: float,
    ) -> None:
        emb = getattr(self.box_model, "side_emblem", None)
        if not emb or not emb.get("icon"):
            return
        p = get_semantic_palette()
        max_rows = float(emb.get("max_rows", 6.0))
        h_bar_rel = getattr(self.canvas, "_h_bar_rel_baseline", None)
        if h_bar_rel is None:
            tmp_lay = Pango.Layout.new(layout.get_context())
            tmp_lay.set_font_description(layout.get_font_description())
            tmp_lay.set_text(self.canvas.chars.get("h", "\u2500"), -1)
            h_ink, _ = tmp_lay.get_pixel_extents()
            t_base = tmp_lay.get_baseline() / Pango.SCALE
            h_bar_rel = ((h_ink.y + h_ink.height * 0.5) - t_base) if h_ink.height > 0 else (-line_0_h * 0.25)
            self.canvas._h_bar_rel_baseline = h_bar_rel
        l0_base = layout.get_iter().get_baseline() / Pango.SCALE
        y_top_line = top_off_y + l0_base + h_bar_rel

        margin = max(6, int(round(char_w * 1.8)))
        avail_h = (y_bot - y_top_line) - margin * 2.0
        if avail_h < char_h * 2.2:
            return
        h_emb = min(avail_h, char_h * max_rows)
        y_emb = y_top_line + margin
        w_emb = max(char_w * 11.0, min(char_w * 12.2, h_emb * 1.30))
        bw = math.floor(w_emb)
        bh = math.floor(h_emb)
        bx = math.floor(x_right) + 0.5 - margin - bw
        by = math.floor(y_top_line) + 0.5 + margin
        x_emb = bx
        if x_emb <= x_left + char_w * 12.0:
            return

        # Verify no layout line inside [y_emb, y_emb + h_emb] collides horizontally with x_emb
        full_bytes = (layout.get_text() or "").encode("utf-8")
        it = layout.get_iter()
        max_content_x = x_left
        while True:
            ln = it.get_line_readonly()
            y0_u, y1_u = it.get_line_yrange()
            ly0 = top_off_y + (y0_u / Pango.SCALE)
            ly1 = top_off_y + (y1_u / Pango.SCALE)
            if ly1 > y_emb - 2.0 and ly0 < y_emb + h_emb + 2.0 and ln.start_index > 0:
                l_txt = full_bytes[ln.start_index : ln.start_index + ln.length].decode("utf-8", "ignore")
                inner_txt = l_txt.rstrip(" \u200b\u2502\n")
                if inner_txt.lstrip(" \u2502"):
                    b_end = ln.start_index + len(inner_txt.encode("utf-8")) - 1
                    while b_end > ln.start_index and (full_bytes[b_end] & 0xC0) == 0x80:
                        b_end -= 1
                    pos = layout.index_to_pos(b_end)
                    px_end = max(pos.x, pos.x + pos.width) / Pango.SCALE
                    max_content_x = max(max_content_x, px_end)
            if not it.next_line():
                break

        if max_content_x + char_w * 0.8 > x_emb:
            return

        # Pixel-aligned nested nc-box geometry (bx, by, bw, bh computed symmetrically above)
        emb_rad = min(4.5, rad)

        bg_r, bg_g, bg_b, _ = _parse_rgba_components(p["structure_bg"])
        cr.set_source_rgba(bg_r / 255.0, bg_g / 255.0, bg_b / 255.0, 0.92)
        self._trace_cw_rounded_rect(cr, bx, by, bw, bh, emb_rad, emb_rad, emb_rad, emb_rad)
        cr.fill()

        lbl_txt = emb.get("label", "")
        lbl_layout = None
        lbl_w = 0.0
        lbl_h = 0.0
        lbl_y_off = 0.0
        top_pad = char_h * 0.25
        gap_x0 = None
        gap_x1 = None
        if lbl_txt:
            lbl_layout = PangoCairo.create_layout(cr)
            lbl_desc = Pango.FontDescription.from_string(f"{self.canvas.font_family}, monospace bold")
            lbl_desc.set_size(int(round(max(8.5, self.canvas.font_size * 0.85) * Pango.SCALE)))
            lbl_layout.set_font_description(lbl_desc)
            b_l, b_r = self.canvas.title_brackets
            lbl_markup = (
                f"<span color='{p['structure']}'>{Canvas.escape(b_l)}</span>"
                f"<span color='{p['schema']}'>{Canvas.escape(lbl_txt)}</span>"
                f"<span color='{p['structure']}'>{Canvas.escape(b_r)}</span>"
            )
            lbl_layout.set_markup(lbl_markup, -1)
            _, l_log = lbl_layout.get_pixel_extents()
            if l_log.width + 14 < bw:
                lbl_w = float(l_log.width)
                lbl_h = float(l_log.height)
                lbl_y_off = float(l_log.y)
                lx = bx + (bw - lbl_w) * 0.5
                gap_x0 = max(bx + emb_rad + 2.0, lx - 3.0)
                gap_x1 = min(bx + bw - emb_rad - 2.0, lx + lbl_w + 3.0)
                top_pad = max(top_pad, lbl_h * 0.55)

        br_r, br_g, br_b, _ = _parse_rgba_components(p["structure"])
        cr.set_source_rgba(br_r / 255.0, br_g / 255.0, br_b / 255.0, 1.0)
        cr.set_line_width(1.0)
        cr.new_sub_path()
        if gap_x0 is not None and gap_x1 is not None and gap_x1 > gap_x0:
            cr.move_to(gap_x1, by)
        else:
            cr.move_to(bx + emb_rad, by)
        cr.line_to(bx + bw - emb_rad, by)
        if emb_rad > 0.3:
            cr.arc(bx + bw - emb_rad, by + emb_rad, emb_rad, -math.pi / 2.0, 0.0)
        cr.line_to(bx + bw, by + bh - emb_rad)
        if emb_rad > 0.3:
            cr.arc(bx + bw - emb_rad, by + bh - emb_rad, emb_rad, 0.0, math.pi / 2.0)
        cr.line_to(bx + emb_rad, by + bh)
        if emb_rad > 0.3:
            cr.arc(bx + emb_rad, by + bh - emb_rad, emb_rad, math.pi / 2.0, math.pi)
        cr.line_to(bx, by + emb_rad)
        if emb_rad > 0.3:
            cr.arc(bx + emb_rad, by + emb_rad, emb_rad, math.pi, 1.5 * math.pi)
        if gap_x0 is not None and gap_x1 is not None and gap_x1 > gap_x0:
            cr.line_to(gap_x0, by)
        else:
            cr.close_path()
        emb_bs = str(THEME.get("ascii", {}).get("box_style", "rounded") or "rounded")
        cr.save()
        if emb_bs == "dashed":
            cr.set_dash([4.0, 2.5])
        cr.stroke()
        cr.restore()

        if lbl_layout is not None and gap_x0 is not None:
            lx = bx + (bw - lbl_w) * 0.5
            ly = by - (lbl_y_off + lbl_h * 0.5)
            cr.move_to(lx, ly)
            PangoCairo.show_layout(cr, lbl_layout)

        inner_w = max(16.0, bw - char_w * 1.6)
        inner_h = max(16.0, bh - top_pad - char_h * 0.35)
        target_px = max(18.0, min(inner_w * 0.62, inner_h * 0.64))

        ic_layout = PangoCairo.create_layout(cr)
        ic_desc = Pango.FontDescription.from_string(
            f"Symbols Nerd Font, Font Awesome 7 Free, Font Awesome 7 Brands, {self.canvas.font_family}"
        )
        ic_desc.set_absolute_size(int(round(target_px * Pango.SCALE)))
        ic_layout.set_font_description(ic_desc)
        ic_layout.set_text(emb["icon"], -1)
        ink_r, _ = ic_layout.get_pixel_extents()

        if ink_r.width > 0 and ink_r.height > 0:
            scale_fit = min(inner_w * 0.72 / float(ink_r.width), inner_h * 0.74 / float(ink_r.height))
            if scale_fit < 0.92:
                ic_desc.set_absolute_size(int(round(target_px * scale_fit * Pango.SCALE)))
                ic_layout.set_font_description(ic_desc)
                ink_r, _ = ic_layout.get_pixel_extents()

        cx = bx + bw * 0.5
        cy = by + top_pad + (bh - top_pad) * 0.5
        ic_hex = p.get(emb.get("role", "intent"), p["intent"])
        ir, ig, ib, _ = _parse_rgba_components(ic_hex)
        cr.set_source_rgba(ir / 255.0, ig / 255.0, ib / 255.0, 1.0)
        cr.move_to(cx - (ink_r.x + ink_r.width * 0.5), cy - (ink_r.y + ink_r.height * 0.5))
        PangoCairo.show_layout(cr, ic_layout)

    def _handle_link(self, _label, uri: str) -> bool:
        if uri and uri == self._press_handled_slider_uri:
            self._press_handled_slider_uri = None
            self._button_down = False
            self._last_drag_slider_uri = None
            self._active_drag_slider = None
            self._link_activated = True
            return True
        self._press_handled_slider_uri = None
        self._link_activated = True
        self._button_down = False
        self._last_drag_slider_uri = None
        self._active_drag_slider = None
        if self.on_pointer_activity:
            self.on_pointer_activity("click")
        if uri and ":" in uri:
            prefix, _, val_s = uri.rpartition(":")
            if not is_slider_enabled(prefix):
                return True
            set_slider_meta_value(prefix, val_s)
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
        _record_pointer_coords(event)
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
        _record_pointer_coords(event)
        if self._button_down:
            self._drag_slider_from_xy(widget, event.x, event.y)
            return False
        if not self.is_hovered:
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
        coords = _unpack_translated_coords(widget.translate_coordinates(self, int(event.x), int(event.y)))
        if coords is not None:
            rx, ry = coords
            if 1 <= rx < (alloc.width - 1) and 1 <= ry < (alloc.height - 1):
                return False
        self.is_hovered = False
        self._last_pointer_lbl_xy = None
        if self._hovered_anchor_idx is not None:
            self._hovered_anchor_idx = None
            self._apply_label_markup()
            if callable(self.on_anchor_hover_change):
                self.on_anchor_hover_change(self, None, False)
        if self.hover_enable:
            if self.on_hover_change:
                self.on_hover_change(self, False)
            else:
                self.set_hovered_visual(False)
        return False

    def _on_button_press(self, widget, event):
        if event.button != 1 or event.triggers_context_menu():
            return True
        self._button_down = True
        self._link_activated = False
        self._last_drag_slider_uri = None
        self._press_handled_slider_uri = None
        self._active_drag_slider = None
        if self.on_pointer_activity:
            self.on_pointer_activity("press")
        self._drag_slider_from_xy(widget, event.x, event.y, immediate=True)
        return False

    def _on_button_release(self, _widget, event):
        if event.button != 1:
            return True
        self._button_down = False
        self._last_drag_slider_uri = None
        self._active_drag_slider = None
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

    def _on_event_after(self, widget, event) -> None:
        if event.type == Gdk.EventType.BUTTON_PRESS:
            ok_b, btn = event.get_button()
            if ok_b and btn == 1:
                self._button_down = True
                self._last_drag_slider_uri = None
                if self.on_pointer_activity:
                    self.on_pointer_activity("press")
                _ok_c, ex, ey = event.get_coords()
                if _ok_c and self._press_handled_slider_uri is None:
                    self._active_drag_slider = None
                    self._drag_slider_from_xy(widget, ex, ey, immediate=True)
        elif event.type == Gdk.EventType.BUTTON_RELEASE:
            ok_b, btn = event.get_button()
            if ok_b and btn == 1:
                self._button_down = False
                self._last_drag_slider_uri = None
                self._active_drag_slider = None
                if self.on_pointer_activity:
                    self.on_pointer_activity("release")

    def _resolve_scroll_direction(self, event) -> str | None:
        now = time.monotonic()
        ev_time = int(getattr(event, "time", 0) or (event.get_time() if hasattr(event, "get_time") else 0))
        if ev_time > 0 and ev_time == self._last_scroll_ev_time:
            return ""
        direction = None
        if event.direction in (Gdk.ScrollDirection.UP, Gdk.ScrollDirection.RIGHT):
            if (now - self._last_smooth_wheel_ts) < 0.005:
                return ""
            self._last_discrete_scroll_ts = now
            self._scroll_accum = 0.0
            direction = "up"
        elif event.direction in (Gdk.ScrollDirection.DOWN, Gdk.ScrollDirection.LEFT):
            if (now - self._last_smooth_wheel_ts) < 0.005:
                return ""
            self._last_discrete_scroll_ts = now
            self._scroll_accum = 0.0
            direction = "down"
        elif event.direction == Gdk.ScrollDirection.SMOOTH:
            if _is_mouse_wheel_smooth_event(event, self._last_discrete_scroll_ts, now):
                return ""
            _ok_d, dx, dy = event.get_scroll_deltas()
            delta = dy if abs(dy) >= abs(dx) else -dx
            if abs(delta) < 1e-6:
                is_stop = bool(getattr(event, "is_stop", False))
                if not is_stop and hasattr(event, "is_scroll_stop_event"):
                    try:
                        is_stop = bool(event.is_scroll_stop_event())
                    except Exception:
                        pass
                if is_stop:
                    self._scroll_accum = 0.0
                return ""
            if delta * self._scroll_accum < 0.0:
                self._scroll_accum = 0.0
            self._scroll_accum += delta
            if abs(self._scroll_accum) < 0.20:
                return ""
            direction = "up" if self._scroll_accum < 0 else "down"
            src_dev = event.get_source_device() if hasattr(event, "get_source_device") else getattr(event, "device", None)
            if src_dev is None or (hasattr(src_dev, "get_source") and src_dev.get_source() == Gdk.InputSource.MOUSE):
                self._last_smooth_wheel_ts = now
        if direction in ("up", "down"):
            if ev_time > 0:
                self._last_scroll_ev_time = ev_time
        return direction

    def _on_scroll(self, widget, event):
        ev_time = int(getattr(event, "time", 0) or (event.get_time() if hasattr(event, "get_time") else 0))
        if ev_time > 0 and ev_time == self._passthrough_scroll_ev_time:
            return False
        if self.on_pointer_activity:
            self.on_pointer_activity("scroll")
        if not self.is_hovered:
            _record_pointer_coords(event)
            self.is_hovered = True
            if self.hover_enable:
                if self.on_hover_change:
                    self.on_hover_change(self, True)
                else:
                    self.set_hovered_visual(True)
        if hasattr(event, "x") and hasattr(event, "y"):
            self._update_hovered_anchor_from_xy(widget, event.x, event.y, from_motion=False)
        if self.on_scroll_intercept and self.on_scroll_intercept(self, event):
            return True
        scroll_act = getattr(self.box_model, "scroll_action", None)
        if not scroll_act or not self.on_link:
            return False
        direction = self._resolve_scroll_direction(event)
        if direction is None:
            return False
        if direction == "":
            return True
        can_dir = (
            getattr(self.box_model, "scroll_can_up", True)
            if direction == "up"
            else getattr(self.box_model, "scroll_can_down", True)
        )
        if not can_dir or (self.on_scroll_can_consume and not self.on_scroll_can_consume(self, direction)):
            if ev_time > 0:
                self._passthrough_scroll_ev_time = ev_time
            self._scroll_accum = 0.0
            return False
        is_cont = _is_continuous_scroll_event(event)
        if allow_step_input(continuous=is_cont):
            self._scroll_accum = 0.0
            self.on_link(f"{scroll_act}:{direction}")
        elif not is_cont:
            self._scroll_accum = 0.0
        return True

    def refresh_markup(self) -> None:
        new_markup = self.box_model.render(self.canvas)
        if (
            new_markup == self._last_markup
            and new_markup != ""
            and self.footer_box_model is None
            and self.embedded_widget is None
            and self.pixbuf is None
        ):
            if self.is_hovered and self._last_pointer_lbl_xy is not None:
                self._update_hovered_anchor_from_xy(
                    self.lbl,
                    self._last_pointer_lbl_xy[0],
                    self._last_pointer_lbl_xy[1],
                    from_motion=False,
                )
            return
        self._last_markup = new_markup
        self._rebuild_anchor_byte_spans()
        self._apply_label_markup()
        if self.is_hovered and self._last_pointer_lbl_xy is not None:
            self._update_hovered_anchor_from_xy(
                self.lbl,
                self._last_pointer_lbl_xy[0],
                self._last_pointer_lbl_xy[1],
                from_motion=False,
            )
        if self.footer_box_model is not None and self.footer_lbl is not None:
            self.footer_box_model._forced_cols = self.box_model._forced_cols
            self.footer_box_model.min_cols = self.box_model.min_cols
            self.footer_box_model.max_cols = self.box_model.max_cols
            self.footer_box_model.width_mode = self.box_model.width_mode
            self.footer_box_model.highlighted = self.box_model.highlighted
            self.footer_box_model.selected = self.box_model.selected
            f_stripped, f_spans = _extract_and_strip_bg_spans(self.footer_box_model.render(self.canvas))
            self._footer_bg_byte_spans = f_spans
            self.footer_lbl.set_markup(f"{_NO_LIGA_SPAN_OPEN}{f_stripped}</span>" if f_stripped else "")
        if self.embedded_widget is not None and hasattr(self.embedded_widget, "set_border_color"):
            self.embedded_widget.set_border_color(self.box_model._resolve_palette()[0])
        if self.pixbuf is not None and getattr(self, "_img_widget", None) is not None:
            self._img_widget.set_from_pixbuf(self._fit_and_crop_pixbuf())
        if getattr(self, "eb", None) is not None:
            self.eb.queue_draw()


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
    def __init__(self, canvas: Canvas, on_submit, on_cancel, on_visibility_change=None, on_change=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.set_margin_top(6)
        self.canvas = canvas
        self.on_submit = on_submit
        self.on_cancel = on_cancel
        self.on_visibility_change = on_visibility_change
        self.on_change = on_change
        self._suppress_change = False

        self.action_id = ""
        self.base_placeholder = t("common.input_placeholder")
        self.placeholder = self.base_placeholder
        self.title = t("common.input_title")
        self.is_secret = False
        self.show_secret = False
        self.is_required = False
        self.has_error = False
        self.completion_spec = None
        self.completion_candidates = []
        self.completion_idx = -1
        self.completion_hint = ""
        self._suppress_complete_reset = False

        self.entry = Gtk.Entry()
        self.entry.set_opacity(0.0)
        self.entry.set_size_request(1, 1)
        self.entry.set_can_focus(True)
        self.entry.set_halign(Gtk.Align.START)
        self.entry.set_valign(Gtk.Align.START)
        self.entry.connect("changed", self._on_text_changed)
        self.entry.connect("activate", self._on_activate)
        self.entry.connect("key-press-event", self._on_entry_key_press)

        self.lbl = Gtk.Label()
        self.lbl.set_xalign(0.0)
        self.lbl.set_can_focus(False)
        self.lbl.connect("activate-link", self._on_link)
        _disable_label_context_menu(self.lbl)

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
        completion=None,
    ) -> None:
        self.action_id = action_id
        self.is_secret = bool(secret)
        self.show_secret = False
        self.is_required = bool(required)
        self.has_error = False
        self.completion_spec = completion
        self.completion_candidates = []
        self.completion_idx = -1
        self.completion_hint = ""
        self.base_placeholder = placeholder or (
            t("common.password_placeholder") if secret else t("common.input_placeholder")
        )
        self.placeholder = self.base_placeholder
        self.title = title or (t("common.password_title") if secret else t("common.input_title"))
        self._suppress_change = True
        try:
            self.entry.set_text(default_txt or "")
        finally:
            self._suppress_change = False
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
        self.completion_spec = None
        self.completion_candidates = []
        self.completion_idx = -1
        self.completion_hint = ""
        self.entry.set_text("")
        self.hide()
        if was_visible and self.on_visibility_change:
            self.on_visibility_change()

    def _resolve_completion_spec(self, text: str):
        if self.completion_spec is not None:
            return self.completion_spec
        aid = str(self.action_id or "").lower()
        if any(k in aid for k in ("folder", "dir")):
            return "path_dir"
        if text.startswith(("/", "~", "./")) or any(k in aid for k in ("scene", "path", "img", "file", "import", "export", "ics", "db", "vpn")):
            return "path_file"
        return None

    def handle_tab_complete(self, reverse: bool = False) -> bool:
        cur = self.entry.get_text()
        spec = self._resolve_completion_spec(cur)
        if spec is None:
            return False

        from core.completion import step_tab_completion
        picked, new_cands, new_idx, hint = step_tab_completion(
            cur,
            spec,
            self.completion_candidates,
            self.completion_idx,
            reverse=reverse,
        )
        self.completion_candidates = new_cands
        self.completion_idx = new_idx
        self.completion_hint = hint
        if picked != cur:
            self._suppress_complete_reset = True
            try:
                self.entry.set_text(picked)
                self.entry.set_position(-1)
            finally:
                self._suppress_complete_reset = False
        self._render()
        return True

    def _on_entry_key_press(self, _widget, event) -> bool:
        if event.keyval in (Gdk.KEY_Tab, Gdk.KEY_KP_Tab, Gdk.KEY_ISO_Left_Tab):
            rev = bool(event.state & Gdk.ModifierType.SHIFT_MASK) or (event.keyval == Gdk.KEY_ISO_Left_Tab)
            return self.handle_tab_complete(reverse=rev)
        return False

    def _on_text_changed(self, _entry) -> None:
        if not self._suppress_complete_reset:
            self.completion_candidates = []
            self.completion_idx = -1
            self.completion_hint = ""
        self._render()
        if not self._suppress_change and self.action_id and self.on_change:
            self.on_change(self.action_id, self.entry.get_text())

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
        p = get_semantic_palette()
        txt = self.entry.get_text()
        err = self.has_error and not txt
        c_brd = p["fault"] if err else p["intent"]

        right_parts = []
        if self._resolve_completion_spec(txt) is not None:
            # MEMENTO GOLDEN STANDARD (§2.1.2): Il suggerimento tastiera 'Tab ↹' non è cliccabile,
            # quindi non può mai essere racchiuso tra parentesi quadre [...].
            right_parts.append(Txt("Tab \u21b9", role="schema"))
        if self.is_secret:
            eye = "\uf06e" if self.show_secret else "\uf070"
            right_parts.append(ActionBtn(eye, "prompt:toggle_vis", tone="warn" if self.show_secret else "neutral"))
        right_parts.append(ActionBtn("\uf00d", "prompt:cancel", tone="danger_soft"))
        right_markup = "  ".join(right_parts)

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
        rows = [
            self.canvas.top(hdr, c_box=c_brd, c_txt=c_brd),
            self.canvas.row(f"{prompt_gt} {disp_txt}", right_markup=right_markup, c_box=c_brd),
        ]
        if self.completion_hint:
            hint_s = self.canvas.truncate_plain(self.completion_hint, max(16, self.canvas.inner_cols - 6))
            rows.append(self.canvas.row(f"  {Txt(hint_s, role='schema')}", c_box=c_brd))
        rows.append(self.canvas.bot(c_box=c_brd))
        joined_rows = "\n".join(rows)
        self.lbl.set_markup(f"{_NO_LIGA_SPAN_OPEN}{joined_rows}</span>" if joined_rows else "")
