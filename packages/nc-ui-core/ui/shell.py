#!/usr/bin/env python3
import json
import math
import os
import re
import sys
import time
import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gtk, Gdk, GLib, Pango

try:
    gi.require_version("GtkLayerShell", "0.1")
    from gi.repository import GtkLayerShell
    HAS_LAYER_SHELL = True
except ValueError:
    HAS_LAYER_SHELL = False

from core.action_help import lookup_action_help
from core.config import (
    CONFIG,
    THEME,
    get_box_opacity,
    get_hover_help_style,
    get_hover_tooltip_delay,
    get_hover_tooltips_enabled,
    get_surface_bg_rgb,
    get_toast_opacity,
    get_toast_style,
    get_waybar_config_dir,
    get_waybar_look,
    get_effective_window_bg,
    get_layout_margins,
    strip_jsonc_comments,
    _parse_rgba_components,
)
from core.completion import search_control_center
from core.i18n import t
from core.paths import CORE_ROOT, find_bin
from core import probe, log
from core.state import STATE
from ui.canvas import Canvas
from ui.help_diagrams import create_help_diagram
from ui.help_overlay import HoverHelpCard, StyledNotifCard
from ui.kit import (
    PALETTE,
    ActionBtn,
    Badge,
    Box,
    Divider,
    InspectorCell,
    StateBadge,
    Txt,
    advance_slider_uri,
    cancel_inline_entry,
    commit_active_inline_entry,
    get_active_inline_entry,
    get_active_inline_uri,
    get_inline_candidate_index,
    get_inline_entry_buffer,
    get_scroll_zone_meta,
    get_semantic_palette,
    handle_inline_entry_key_press,
    set_inline_candidates,
    start_inline_input,
    step_inline_completion,
    sync_boxes_width,
    sync_inline_from_gtk_entry,
)
from ui.widgets import AsciiCard, CustomWidgetCard, NcPromptBar, _is_continuous_scroll_event, _unpack_translated_coords, allow_step_input, pointer_moved_recently, clear_pointer_move_flag
from panels.base import load_panel


def _get_active_tui_id() -> str:
    mod = sys.modules.get("panels.cc_engine_resources")
    return mod.get_active_tui() if (mod is not None and hasattr(mod, "get_active_tui")) else ""


def _get_cc_sections() -> list:
    try:
        from panels.control_center import get_control_center_sections
        return get_control_center_sections()
    except ImportError:
        try:
            from panels.control_center import CONTROL_CENTER_SECTIONS
            return list(CONTROL_CENTER_SECTIONS)
        except ImportError:
            return []

PILL_CACHE_FILE = "/tmp/nc_waybar_pills.json"
LEAVE_STAMP_FILE = "/tmp/nc_ui_last_leave.json"

DEFAULT_PANEL_TO_WB_MODULE = {
    "settings": "custom/settings",
    "calendar": "custom/calendar",
    "display": "custom/desktop",
    "clipboard": "custom/cliphist",
    "capture": "custom/screenshot",
    "audio": "custom/audio",
    "network": "custom/netcycle",
    "cpu": "custom/cpu",
    "ram": "custom/ram",
    "gpu": "custom/gpu",
    "notifications": "custom/notifications",
    "session": "custom/session",
    "power": "custom/power",
    "printers": "custom/printers",
    "ups": "custom/ups",
    "kb_layout": "custom/kb_layout",
    "storage": "custom/storage",
}

_LINK_TAG_RE = re.compile(r"</?a\b[^>]*>", re.IGNORECASE)
_AGENT_CMD_RE = re.compile(r"nc-bar-agent\s+([a-zA-Z0-9_-]+)")
_CTL_CMD_RE = re.compile(r"nc-ctl\s+(?:popup|action)\s+([a-zA-Z0-9_-]+)")

SIDEBAR_ICON_COLS = 8


class ShellWindow(Gtk.Window):
    def __init__(self, panel_id: str, mode: str = "popup", anchor_x: int = None):
        self.mode = mode
        self.panel_id = str(panel_id or ("" if mode == "settings" else "network")).strip()
        super().__init__(title=f"NC-UI: {self.panel_id or 'settings'}")
        self.panel = load_panel(self.panel_id, shell=self, mode=self.mode)
        if self.mode == "settings" and not self.panel_id:
            self.panel_id = "settings"

        self._refresh_source_id = None
        self._hovered_card_idx = None
        self._wb_layout_cache = None
        self._niri_geom_cache = None
        self._last_allocated_w = 0
        self._allocated_cc_h = None
        self._cc_refit_pending = False
        self._ignore_leave_until = 0.0
        self._mouse_inside = False
        self._leave_close_source_id = None
        self._stable_cols_key = None
        self._stable_cols_val = 0
        self._css_provider = None
        self._nav_cell_height_px = 48.0
        self._last_nav_height_px = -1
        self._last_nav_state = None
        self._ink_insets = None
        self.header_listbox = None
        self.right_vbox = None
        self.root_overlay = None
        self.cc_search_holder = None
        self.cc_search_listbox = None
        self.help_holder = None
        self.help_listbox = None
        self._help_show_timer_id = None
        self._help_hide_timer_id = None
        self._help_pending_uri = None
        self._help_pending_rect = None
        self._help_pending_prefer_right = False
        self._help_visible_uri = None
        self._help_visible_rect = None
        self._help_visible_prefer_right = False
        self._help_visible_view_key = None
        self._help_expanded = False
        self._help_hovered = False
        self.toast_holder = None
        self.toast_listbox = None
        self._toast_timer_id = None
        self._toast_hovered = False
        self._toast_level = "ok"
        self._toast_duration_ms = 0
        self._action_seq = {}
        self._last_fb_by_view = {}
        self._last_view_key = None
        self._inline_entry = None
        self._suppress_inline_sync = False
        self.click_x = anchor_x if (anchor_x is not None and anchor_x > 0) else -1

        # Stato del motore di navigazione da tastiera 2D
        self._focus_targets = []
        self._focused_target_idx = -1
        self._focused_uri_memory = None
        self._last_render_sig = None
        self._nav_sized = False
        self._last_win_size = None
        self._last_sw_size = None

        self.wb_look = get_waybar_look()
        self.geom = THEME["geometry"]
        self.colors = THEME["colors"]
        self.typo = THEME["typography"]

        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual and screen.is_composited():
            self.set_visual(visual)

        self.nav_canvas = Canvas(total_cols=SIDEBAR_ICON_COLS)

        if self.mode == "settings":
            cc_cols = self._compute_cc_content_cols()
            self.content_canvas = Canvas(total_cols=cc_cols)
        else:
            self.content_canvas = Canvas(total_cols=self.panel.get_cols())

        self._wb_pango_layout = self._create_waybar_pango_layout()

        self._init_layer_shell()
        self._apply_css()
        self._build_layout()

        self.add_events(
            Gdk.EventMask.ENTER_NOTIFY_MASK
            | Gdk.EventMask.LEAVE_NOTIFY_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
            | Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.KEY_PRESS_MASK
            | Gdk.EventMask.KEY_RELEASE_MASK
            | Gdk.EventMask.FOCUS_CHANGE_MASK
            | Gdk.EventMask.SCROLL_MASK
            | Gdk.EventMask.SMOOTH_SCROLL_MASK
        )
        self.connect("enter-notify-event", self._on_window_enter)
        self.connect("motion-notify-event", self._on_window_motion)
        self.connect("leave-notify-event", self._on_leave)
        self.connect("button-press-event", self._on_window_button_press)
        self.connect("focus-out-event", self._on_window_focus_out)
        self.connect("notify::is-active", self._on_window_active_notify)
        self.connect("key-press-event", self._on_key_press)
        self.connect("key-release-event", self._on_key_release)
        self.connect("scroll-event", self._on_scrolled_window_scroll)
        self.connect("realize", self._on_realize)
        self.connect("size-allocate", self._on_size_allocate)
        self._geom_sig_ids = []
        if screen is not None and os.environ.get("NC_UI_OFFSCREEN") != "1":
            self._geom_sig_ids.append((screen, screen.connect("monitors-changed", self._on_screen_geometry_signal)))
            self._geom_sig_ids.append((screen, screen.connect("size-changed", self._on_screen_geometry_signal)))
            self.connect("destroy", self._on_shell_destroy)

        self.refresh_view()
        if self.mode == "settings":
            if "NC_TEST_GLOBAL_SEARCH" in os.environ:
                self.open_cc_global_search(os.environ.get("NC_TEST_GLOBAL_SEARCH", ""))
            elif STATE.get("control_center", "open_global_search", False):
                STATE.set("control_center", "open_global_search", False)
                GLib.idle_add(self.open_cc_global_search)
        self._setup_timer()

    def _get_box_ink_insets(self) -> tuple[int, int, int, int]:
        if self._ink_insets is not None:
            return self._ink_insets
        try:
            import cairo as _cairo
            from gi.repository import PangoCairo as _PC
            c = Canvas(total_cols=36)
            hdr_sample = Box("TITLE", icon="\uf53f", actions=[ActionBtn("\uf002", "x")], highlighted=True)
            hdr_sample.add_row(" ")
            pango_only = _LINK_TAG_RE.sub("", Canvas.sanitize_markup(hdr_sample.render(c)))
            surf_w = max(64, int(round(c.total_px)) + 8)
            surf_h = max(48, int(c.char_h * 5))
            surf = _cairo.ImageSurface(_cairo.FORMAT_ARGB32, surf_w, surf_h)
            cr = _cairo.Context(surf)
            lay = _PC.create_layout(cr)
            lay.set_font_description(c.layout.get_font_description())
            lay.set_markup(f"<span font_features='liga=0, clig=0, dlig=0, hlig=0'>{pango_only}</span>" if pango_only else "", -1)
            _PC.show_layout(cr, lay)
            box_w, box_h = lay.get_pixel_size()
            st = surf.get_stride()
            buf = surf.get_data()

            def _lum(px: int, py: int) -> int:
                i = py * st + px * 4
                return max(buf[i], buf[i + 1], buf[i + 2])

            mid_y = max(1, min(box_h - 2, box_h // 2))
            mid_x = max(1, min(box_w - 2, int(round(box_w * 0.72))))
            xs = [px for px in range(min(box_w, surf_w)) if _lum(px, mid_y) > 100]
            top_ys = [py for py in range(min(box_h // 2, surf_h)) if _lum(mid_x, py) > 100]
            bot_ys = [py for py in range(max(0, box_h - int(c.char_h) - 2), min(box_h, surf_h)) if _lum(mid_x, py) > 100]

            s_left = xs[0] if xs else 4
            s_right = (box_w - 1 - xs[-1]) if xs else 4
            s_top_hdr = top_ys[0] if top_ys else 9
            s_bot = (box_h - 1 - bot_ys[-1]) if bot_ys else 9
            self._ink_insets = (s_left, s_right, s_top_hdr, s_bot)
        except Exception:
            self._ink_insets = (4, 4, 9, 9)
        return self._ink_insets

    def _get_inner_box_margins(self) -> tuple[int, int, int, int, int]:
        inner_pad, _ = get_layout_margins()
        bw = int(self.geom.get("window_border_width", 1))
        s_left, s_right, s_top_hdr, s_bot = self._get_box_ink_insets()
        m_left = max(bw, bw + inner_pad - s_left)
        m_right = max(bw, bw + inner_pad - s_right)
        m_top = max(bw, bw + inner_pad - s_top_hdr)
        m_bot = max(bw, bw + inner_pad - s_bot)
        hbox_gap = max(0, inner_pad - s_right - s_left)
        return m_left, m_right, m_top, m_bot, hbox_gap

    def _all_card_widgets(self) -> list:
        cards = []
        if self.mode == "settings" and self.header_listbox is not None:
            cards.extend(self.header_listbox.get_children())
        cards.extend(self.listbox.get_children())
        return cards

    def _get_cc_target_width_px(self) -> int:
        m_left, m_right, _m_top, _m_bot, hbox_gap = self._get_inner_box_margins()
        nav_w_px = int(round(self.nav_canvas.total_px))
        if hasattr(self, "content_canvas"):
            right_w_px = int(round(self.content_canvas.total_px))
        else:
            right_w_px = int(round(self._compute_cc_content_cols() * self.nav_canvas.char_w))
        return m_left + nav_w_px + hbox_gap + right_w_px + m_right

    def _compute_cc_content_cols(self) -> int:
        cfg_w = int(CONFIG.get("control_center", {}).get("width_px", 960))
        _mon, mon_w, _mon_h = self._resolve_target_monitor()
        _, outer_mar = get_layout_margins()
        eff_w = min(cfg_w, max(640, mon_w - (2 * outer_mar)))
        m_left, m_right, _m_top, _m_bot, hbox_gap = self._get_inner_box_margins()
        nav_w_px = int(round(self.nav_canvas.total_px))
        avail_px = max(320.0, float(eff_w - m_left - m_right - hbox_gap - nav_w_px))
        return max(44, int(avail_px // self.nav_canvas.char_w))

    def _uses_layer_shell_cc_height(self) -> bool:
        return self.mode == "settings" and HAS_LAYER_SHELL and os.environ.get("NC_UI_OFFSCREEN") != "1"

    def _get_cc_target_height_px(self) -> int:
        if self._uses_layer_shell_cc_height() and self._allocated_cc_h is not None and self._allocated_cc_h >= 240:
            return int(self._allocated_cc_h)
        _mon, _mon_w, mon_h = self._resolve_target_monitor()
        if os.environ.get("NC_UI_OFFSCREEN") == "1" and not os.environ.get("NC_SHOT_MON_H") and mon_h < 1200:
            mon_h = 1440
        _, outer_mar = get_layout_margins()
        wb_top = int(self.wb_look.get("top", 30 + outer_mar))
        return max(320, mon_h - wb_top - outer_mar - outer_mar)

    def _get_popup_bounds_px(self) -> tuple[int, int]:
        _mon, mon_w, mon_h = self._resolve_target_monitor()
        if os.environ.get("NC_UI_OFFSCREEN") == "1" and not os.environ.get("NC_SHOT_MON_H") and mon_h < 1200:
            mon_h = 1440
        _, outer_mar = get_layout_margins()
        wb_top = int(self.wb_look.get("top", 30 + outer_mar))
        pad = int(self.geom.get("panel_padding", 10)) * 2
        bw = int(self.geom.get("window_border_width", 1))
        max_win_w = max(380, mon_w - (2 * outer_mar))
        max_win_h = max(240, mon_h - wb_top - outer_mar - pad - (2 * bw))
        return max_win_w, max_win_h

    def _measure_cc_box_px(self, item) -> int:
        bm = item.box_model if hasattr(item, "box_model") else item
        if not isinstance(bm, (Box, Divider)):
            return 0
        c = self.content_canvas
        rendered = bm.render(c, forced_cols=c.total_cols)
        s = c.sanitize_markup(rendered)
        pango_only = _LINK_TAG_RE.sub("", s)
        c.layout.set_width(-1)
        try:
            c.layout.set_markup(f"<span font_features='liga=0, clig=0, dlig=0, hlig=0'>{pango_only}</span>", -1)
            h_px = int(c.layout.get_pixel_size()[1])
            if h_px > 0:
                return h_px
        except Exception:
            pass
        std_row_h = getattr(c, "_std_row_h_px", 0)
        if std_row_h <= 0:
            sample_r = c.row(" ")
            std_row_h = max(12, int(round(c.measure_line_height(sample_r) / Pango.SCALE)))
            c._std_row_h_px = std_row_h
        base_h = int(c.base_row_h_px)
        return sum(std_row_h if "\u200b" in ln else base_h for ln in rendered.splitlines())

    def get_cc_avail_body_height_px(self, extra_top_cards: list = None) -> int:
        _ml, _mr, m_top, m_bot, _gap = self._get_inner_box_margins()
        bw = int(self.geom.get("window_border_width", 1))
        avail_list_h = max(240, self._get_cc_target_height_px() - m_top - m_bot - (2 * bw))
        c = self.content_canvas
        std_row_h = getattr(c, "_std_row_h_px", 0)
        if std_row_h <= 0:
            sample_r = c.row(" ")
            std_row_h = max(12, int(round(c.measure_line_height(sample_r) / Pango.SCALE)))
            c._std_row_h_px = std_row_h
        base_h = int(c.base_row_h_px)
        spacing = int(self.geom.get("card_spacing", 4))
        hdr_h = (2 * base_h) + (3 * std_row_h)
        if self.header_listbox is not None and self.header_listbox.get_children():
            hdr_ch = self.header_listbox.get_children()[0]
            if getattr(hdr_ch, "_last_markup", ""):
                hdr_h = sum(std_row_h if "\u200b" in ln else base_h for ln in hdr_ch._last_markup.splitlines())
        used_h = hdr_h + spacing
        for ec in (extra_top_cards or ()):
            if getattr(ec, "_toast_meta", None):
                continue
            used_h += self._measure_cc_box_px(ec) + spacing
        return max(240, avail_list_h - used_h)

    def bump_leave_guard(self, duration_sec: float = 0.45) -> None:
        self._ignore_leave_until = max(self._ignore_leave_until, time.monotonic() + duration_sec)
        self._cancel_leave_close()

    def _cancel_leave_close(self) -> None:
        if self._leave_close_source_id is not None:
            GLib.source_remove(self._leave_close_source_id)
            self._leave_close_source_id = None

    def _check_help_missed_leave(self) -> None:
        if self._help_hovered and not self._help_expanded and not self._pointer_over_help_holder():
            self._help_hovered = False
            self._schedule_hide_hover_help(35)

    def _on_card_pointer_activity(self, kind: str) -> None:
        self._mouse_inside = True
        self._cancel_leave_close()
        if kind == "motion":
            self._check_help_missed_leave()
        if kind in ("press", "release", "click", "scroll"):
            self.bump_leave_guard(0.50)

    def _on_window_enter(self, _widget, event) -> bool:
        if event.mode == Gdk.CrossingMode.NORMAL:
            self._mouse_inside = True
            self._cancel_leave_close()
        return False

    def _on_window_motion(self, _widget, _event) -> bool:
        self._mouse_inside = True
        self._cancel_leave_close()
        self._check_help_missed_leave()
        return False

    def _on_window_button_press(self, _widget, _event) -> bool:
        if _get_active_tui_id():
            from panels.cc_engine_resources import set_active_tui
            set_active_tui("")
        return False

    def _on_window_focus_out(self, _widget, _event) -> bool:
        if _get_active_tui_id():
            from panels.cc_engine_resources import set_active_tui
            set_active_tui("")
        return False

    def _on_window_active_notify(self, _widget, _pspec) -> None:
        if not self.is_active() and _get_active_tui_id():
            from panels.cc_engine_resources import set_active_tui
            set_active_tui("")

    def is_pointer_busy(self) -> bool:
        for c in self._all_card_widgets():
            if isinstance(c, AsciiCard) and getattr(c, "_button_down", False):
                return True
        return False

    @property
    def prompt_entry(self) -> Gtk.Entry:
        return self.prompt_bar.entry

    @property
    def prompt_action_id(self) -> str:
        return self.prompt_bar.action_id

    def _create_waybar_pango_layout(self) -> Pango.Layout:
        wb_font_px = 13.0
        wb_css = os.path.join(get_waybar_config_dir(), "style.css")
        if os.path.exists(wb_css):
            try:
                with open(wb_css, "r", encoding="utf-8") as f:
                    m = re.search(r"font-size\s*:\s*(\d+(?:\.\d+)?)px", f.read())
                    if m:
                        wb_font_px = float(m.group(1))
            except Exception:
                pass
        screen = Gdk.Screen.get_default()
        pctx = Gdk.pango_context_get_for_screen(screen)
        layout = Pango.Layout.new(pctx)
        desc = Pango.FontDescription.from_string(
            f"{self.typo['font_family']}, Font Awesome 6 Free, Symbols Nerd Font, monospace bold"
        )
        desc.set_absolute_size(int(round(wb_font_px * Pango.SCALE)))
        layout.set_font_description(desc)
        return layout

    def _measure_waybar_markup_px(self, markup_str: str) -> float:
        if not markup_str:
            return 0.0
        s = Canvas.sanitize_markup(markup_str)
        pango_only = _LINK_TAG_RE.sub("", s)
        try:
            self._wb_pango_layout.set_markup(f"<span font_features='liga=0, clig=0, dlig=0, hlig=0'>{pango_only}</span>" if pango_only else "", -1)
            return self._wb_pango_layout.get_size()[0] / Pango.SCALE
        except Exception:
            self._wb_pango_layout.set_attributes(None)
            self._wb_pango_layout.set_text(Canvas.strip_tags(s), -1)
            return self._wb_pango_layout.get_size()[0] / Pango.SCALE

    def _total_window_px(self) -> int:
        if self.mode == "settings":
            return self._get_cc_target_width_px()
        pad = int(self.geom.get("panel_padding", 10)) * 2
        return int(round(self.content_canvas.total_px + pad))

    def _query_niri_context(self) -> dict:
        if self._niri_geom_cache is not None:
            return self._niri_geom_cache
        ctx = {
            "output_name": None,
            "logical": {},
            "model": None,
            "ws_count": 3,
            "win_title": "",
            "_left_loaded": False,
        }
        if probe.has_bin("niri"):
            ok, out = probe.run_cmd(["niri", "msg", "-j", "focused-output"], timeout=0.4)
            if ok and out:
                try:
                    data = json.loads(out)
                    ctx["output_name"] = data.get("name")
                    ctx["logical"] = data.get("logical") or {}
                    ctx["model"] = data.get("model")
                except Exception:
                    pass
        self._niri_geom_cache = ctx
        return ctx

    def _ensure_niri_left_metrics(self) -> dict:
        ctx = self._query_niri_context()
        if ctx.get("_left_loaded") or not probe.has_bin("niri"):
            return ctx
        ctx["_left_loaded"] = True
        ok_w, out_w = probe.run_cmd(["niri", "msg", "-j", "workspaces"], timeout=0.4)
        if ok_w and out_w:
            try:
                ws_list = json.loads(out_w)
                if isinstance(ws_list, list):
                    if ctx["output_name"]:
                        cnt = sum(1 for w in ws_list if w.get("output") == ctx["output_name"])
                    else:
                        cnt = len(ws_list)
                    if cnt > 0:
                        ctx["ws_count"] = cnt
            except Exception:
                pass
        ok_fw, out_fw = probe.run_cmd(["niri", "msg", "-j", "focused-window"], timeout=0.4)
        if ok_fw and out_fw:
            try:
                fw = json.loads(out_fw)
                if isinstance(fw, dict) and fw.get("title"):
                    ctx["win_title"] = str(fw.get("title", ""))
            except Exception:
                pass
        return ctx

    def _resolve_target_monitor(self) -> tuple:
        if os.environ.get("NC_UI_OFFSCREEN") == "1":
            ov_w = os.environ.get("NC_SHOT_MON_W", "").strip()
            ov_h = os.environ.get("NC_SHOT_MON_H", "").strip()
            if ov_w.isdigit() and ov_h.isdigit():
                display = Gdk.Display.get_default()
                mon = display.get_primary_monitor() if display else None
                return mon, max(320, int(ov_w)), max(240, int(ov_h))
        niri_ctx = self._query_niri_context()
        logical = niri_ctx.get("logical") or {}
        niri_w = int(logical.get("width", 0)) if logical.get("width") else 0
        niri_h = int(logical.get("height", 0)) if logical.get("height") else 0
        niri_x = logical.get("x")
        niri_y = logical.get("y")
        niri_model = niri_ctx.get("model")

        display = Gdk.Display.get_default()
        if not display:
            return None, (niri_w or 1920), (niri_h or 1080)

        n_mons = display.get_n_monitors()
        if niri_x is not None and niri_y is not None:
            for i in range(n_mons):
                mon = display.get_monitor(i)
                if not mon:
                    continue
                g = mon.get_geometry()
                if g.x == int(niri_x) and g.y == int(niri_y):
                    return mon, (niri_w or g.width), (niri_h or g.height)

        if niri_model:
            for i in range(n_mons):
                mon = display.get_monitor(i)
                if mon and mon.get_model() == niri_model:
                    g = mon.get_geometry()
                    return mon, (niri_w or g.width), (niri_h or g.height)

        gdk_win = self.get_window()
        if gdk_win:
            mon = display.get_monitor_at_window(gdk_win)
            if mon:
                g = mon.get_geometry()
                return mon, (niri_w or g.width), (niri_h or g.height)

        mon = display.get_primary_monitor() or (display.get_monitor(0) if n_mons > 0 else None)
        mon_w = niri_w or (mon.get_geometry().width if mon else 1920)
        mon_h = niri_h or (mon.get_geometry().height if mon else 1080)
        return mon, mon_w, mon_h

    def _read_waybar_layout(self) -> dict:
        if self._wb_layout_cache is not None:
            return self._wb_layout_cache

        panel_to_mod = dict(DEFAULT_PANEL_TO_WB_MODULE)
        mod_to_panel = {v: k for k, v in panel_to_mod.items()}

        info = {
            "position": "top",
            "left": [],
            "center": [],
            "right": [],
            "spacing": 6,
            "margin_left": 6,
            "margin_right": 6,
            "css_box_extra": 40.0,
            "defs": {},
            "panel_to_mod": panel_to_mod,
            "mod_to_panel": mod_to_panel,
        }

        wb_dir = get_waybar_config_dir()
        wb_css = os.path.join(wb_dir, "style.css")
        if os.path.exists(wb_css):
            try:
                with open(wb_css, "r", encoding="utf-8") as f:
                    css_raw = f.read()
                m_pad = re.search(r"\.module-nc[^{]*\{[^}]*padding\s*:\s*\d+(?:px)?\s+(\d+)px", css_raw, re.DOTALL)
                m_mar = re.search(r"\.module-nc[^{]*\{[^}]*margin\s*:\s*\d+(?:px)?\s+(\d+)px", css_raw, re.DOTALL)
                pad_x = (int(m_pad.group(1)) * 2) if m_pad else 32
                mar_x = (int(m_mar.group(1)) * 2) if m_mar else 8
                info["css_box_extra"] = float(pad_x + mar_x)
            except Exception:
                pass

        wb_conf = os.path.join(wb_dir, "config.jsonc")
        if os.path.exists(wb_conf):
            try:
                with open(wb_conf, "r", encoding="utf-8") as f:
                    loaded = json.loads(strip_jsonc_comments(f.read()))

                data = loaded
                if isinstance(loaded, list) and loaded:
                    out_name = self._query_niri_context().get("output_name")
                    data = loaded[0]
                    if out_name:
                        for bar_cfg in loaded:
                            if isinstance(bar_cfg, dict):
                                bar_out = bar_cfg.get("output")
                                if bar_out == out_name or (isinstance(bar_out, list) and out_name in bar_out):
                                    data = bar_cfg
                                    break

                if isinstance(data, dict):
                    info["position"] = str(data.get("position", "top")).lower()
                    info["left"] = data.get("modules-left", []) or []
                    info["center"] = data.get("modules-center", []) or []
                    info["right"] = data.get("modules-right", []) or []
                    info["spacing"] = int(data.get("spacing", 6))
                    info["margin_left"] = int(data.get("margin-left", 6))
                    info["margin_right"] = int(data.get("margin-right", 6))
                    defs = {k: v for k, v in data.items() if isinstance(v, dict)}
                    info["defs"] = defs

                    for mod_name, mod_cfg in defs.items():
                        detected_pid = None
                        for field in ("exec", "on-click", "on-click-right", "on-click-middle"):
                            cmd_str = str(mod_cfg.get(field, ""))
                            if not cmd_str:
                                continue
                            m_ag = _AGENT_CMD_RE.search(cmd_str)
                            if m_ag:
                                detected_pid = m_ag.group(1).strip()
                                break
                            m_ctl = _CTL_CMD_RE.search(cmd_str)
                            if m_ctl:
                                detected_pid = m_ctl.group(1).strip()
                                break
                            if "nc-ctl settings" in cmd_str and not detected_pid and mod_name == "custom/settings":
                                detected_pid = "settings"

                        if detected_pid:
                            panel_to_mod[detected_pid] = mod_name
                            mod_to_panel[mod_name] = detected_pid
            except Exception as e:
                log.debug(f"Waybar layout parse fallback: {e}")

        self._wb_layout_cache = info
        return info

    def _get_ordered_waybar_panels(self) -> list[tuple[str, str]]:
        """
        Restituisce la sequenza ordinata (da sinistra a destra sulla Waybar) dei pannelli
        interattivi `[(panel_id, mode), ...]` per il "Bar Surfing" da tastiera.
        """
        wb = self._read_waybar_layout()
        mod_to_panel = wb.get("mod_to_panel", {})
        all_mods = list(wb.get("left", [])) + list(wb.get("center", [])) + list(wb.get("right", []))
        ordered = []
        seen = set()
        for m in all_mods:
            pid = mod_to_panel.get(m)
            if not pid or pid == "media" or pid in seen:
                continue
            seen.add(pid)
            p_mode = "settings" if pid == "settings" else "popup"
            ordered.append((pid, p_mode))

        if not ordered:
            for pid in ("settings", "calendar", "display", "clipboard", "capture", "audio", "network", "cpu", "ram", "gpu", "notifications", "session"):
                ordered.append((pid, "settings" if pid == "settings" else "popup"))
        return ordered

    def _surf_waybar_panel(self, delta: int) -> None:
        ordered = self._get_ordered_waybar_panels()
        if not ordered:
            return
        cur_idx = 0
        for idx, (pid, pmode) in enumerate(ordered):
            if (pmode == "settings" and self.mode == "settings") or (pid == self.panel_id and pmode == self.mode):
                cur_idx = idx
                break
        next_pid, next_mode = ordered[(cur_idx + delta) % len(ordered)]
        if next_mode == "popup" and self.mode == "popup":
            # Cambio rapido di pannello dentro la stessa finestra popup con ricalcolo dell'ancoraggio
            self.click_x = -1
            self.switch_panel(next_pid)
            return
        nc_ctl_bin = find_bin("nc-ctl") or os.path.join(CORE_ROOT, "bin", "nc-ctl")
        if next_mode == "settings":
            probe.spawn_bg([sys.executable, nc_ctl_bin, "settings"])
        else:
            probe.spawn_bg([sys.executable, nc_ctl_bin, "popup", next_pid])
        Gtk.main_quit()

    def _read_cached_pills(self) -> dict:
        if os.path.exists(PILL_CACHE_FILE):
            try:
                with open(PILL_CACHE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        return data
            except Exception:
                pass
        return {}

    def _estimate_wb_module_px(self, mod_name: str, wb_info: dict, cached_pills: dict) -> float:
        css_box_extra = float(wb_info.get("css_box_extra", 40.0))
        wb_defs = wb_info.get("defs", {})
        mod_to_panel = wb_info.get("mod_to_panel", {})
        mod_cfg = wb_defs.get(mod_name, {})

        if "exec" not in mod_cfg and "format" in mod_cfg:
            fmt_str = str(mod_cfg.get("format", ""))
            if "{" not in fmt_str:
                return max(44.0, self._measure_waybar_markup_px(fmt_str) + css_box_extra)

        pid = mod_to_panel.get(mod_name)
        if pid:
            if pid in cached_pills:
                txt = cached_pills[pid]
                if not txt:
                    return 0.0
                return self._measure_waybar_markup_px(txt) + css_box_extra
            if pid == self.panel_id:
                pill = self.panel.get_bar_pill()
                txt = pill.get("text", "")
                return (self._measure_waybar_markup_px(txt) + css_box_extra) if txt else 0.0
            try:
                p_obj = load_panel(pid)
                pill = p_obj.get_bar_pill()
                txt = pill.get("text", "")
                return (self._measure_waybar_markup_px(txt) + css_box_extra) if txt else 0.0
            except Exception:
                pass

        if mod_name == "niri/workspaces":
            ws_cnt = max(1, int(self._ensure_niri_left_metrics().get("ws_count", 3)))
            return float(ws_cnt * 38 + 20)

        if mod_name == "niri/window":
            title = self._ensure_niri_left_metrics().get("win_title", "")
            if not title:
                return 0.0
            max_len = int(mod_cfg.get("max-length", 60))
            shown = title[:max_len]
            return self._measure_waybar_markup_px(Canvas.escape(shown)) + css_box_extra

        return 46.0

    def _is_centered_overlay(self) -> bool:
        return self.mode == "popup" and bool(getattr(self.panel, "centered_overlay", False))

    def _default_keyboard_mode(self):
        return GtkLayerShell.KeyboardMode.EXCLUSIVE if self._is_centered_overlay() else GtkLayerShell.KeyboardMode.ON_DEMAND

    def _init_layer_shell(self) -> None:
        if not HAS_LAYER_SHELL or os.environ.get("NC_UI_OFFSCREEN") == "1":
            return
        is_centered = self._is_centered_overlay()
        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_layer(
            self,
            GtkLayerShell.Layer.OVERLAY if is_centered else GtkLayerShell.Layer.TOP,
        )
        GtkLayerShell.set_keyboard_mode(self, self._default_keyboard_mode())

        target_mon, _, _ = self._resolve_target_monitor()
        if target_mon is not None:
            GtkLayerShell.set_monitor(self, target_mon)

        _, outer_mar = get_layout_margins()
        gap_top = outer_mar if self.mode == "settings" else int(self.geom.get("popup_gap_top", outer_mar))
        scr_margin = outer_mar

        if self.mode == "settings":
            side = str(CONFIG.get("control_center", {}).get("side", "right")).strip().lower()
            is_left = (side == "left")
            GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.TOP, True)
            GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.BOTTOM, True)
            GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.LEFT, is_left)
            GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.RIGHT, not is_left)
            GtkLayerShell.set_margin(self, GtkLayerShell.Edge.TOP, gap_top)
            GtkLayerShell.set_margin(self, GtkLayerShell.Edge.BOTTOM, scr_margin)
            GtkLayerShell.set_margin(
                self,
                GtkLayerShell.Edge.LEFT if is_left else GtkLayerShell.Edge.RIGHT,
                scr_margin,
            )
            return

        if is_centered:
            for edge in (
                GtkLayerShell.Edge.TOP,
                GtkLayerShell.Edge.BOTTOM,
                GtkLayerShell.Edge.LEFT,
                GtkLayerShell.Edge.RIGHT,
            ):
                GtkLayerShell.set_anchor(self, edge, False)
            return

        wb = self._read_waybar_layout()
        is_bottom = (wb.get("position") == "bottom")
        v_edge = GtkLayerShell.Edge.BOTTOM if is_bottom else GtkLayerShell.Edge.TOP
        GtkLayerShell.set_anchor(self, v_edge, True)
        GtkLayerShell.set_margin(self, v_edge, gap_top)
        self._apply_dynamic_anchor()

    def _apply_dynamic_anchor(self, actual_win_w: int = None) -> None:
        if not HAS_LAYER_SHELL or self.mode == "settings" or self._is_centered_overlay() or os.environ.get("NC_UI_OFFSCREEN") == "1":
            return

        target_mon, mon_w, _mon_h = self._resolve_target_monitor()
        if target_mon is not None:
            GtkLayerShell.set_monitor(self, target_mon)

        win_w = actual_win_w if (actual_win_w and actual_win_w > 40) else self._total_window_px()
        _, edge_margin = get_layout_margins()
        max_margin = max(edge_margin, mon_w - win_w - edge_margin)

        if self.click_x > 0:
            GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.LEFT, True)
            GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.RIGHT, False)
            clamped_left = max(edge_margin, min(int(round(self.click_x - (win_w / 2.0))), max_margin))
            GtkLayerShell.set_margin(self, GtkLayerShell.Edge.LEFT, clamped_left)
            return

        wb = self._read_waybar_layout()
        panel_to_mod = wb["panel_to_mod"]
        target_mod = panel_to_mod.get(self.panel_id)
        box_spacing = float(wb.get("spacing", 6))
        cached_pills = self._read_cached_pills()

        if target_mod and target_mod in wb["right"]:
            cursor_from_right = float(wb["margin_right"])
            for mod in reversed(wb["right"]):
                w = self._estimate_wb_module_px(mod, wb, cached_pills)
                if mod == target_mod:
                    eff_w = w if w > 0 else 44.0
                    center_from_right = cursor_from_right + (eff_w / 2.0)
                    target_right = int(round(center_from_right - (win_w / 2.0)))
                    clamped_right = max(edge_margin, min(target_right, max_margin))
                    GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.RIGHT, True)
                    GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.LEFT, False)
                    GtkLayerShell.set_margin(self, GtkLayerShell.Edge.RIGHT, clamped_right)
                    return
                if w > 0:
                    cursor_from_right += w + box_spacing

        if target_mod and target_mod in wb["left"]:
            cursor_from_left = float(wb["margin_left"])
            for mod in wb["left"]:
                w = self._estimate_wb_module_px(mod, wb, cached_pills)
                if mod == target_mod:
                    eff_w = w if w > 0 else 44.0
                    center_from_left = cursor_from_left + (eff_w / 2.0)
                    target_left = int(round(center_from_left - (win_w / 2.0)))
                    clamped_left = max(edge_margin, min(target_left, max_margin))
                    GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.LEFT, True)
                    GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.RIGHT, False)
                    GtkLayerShell.set_margin(self, GtkLayerShell.Edge.LEFT, clamped_left)
                    return
                if w > 0:
                    cursor_from_left += w + box_spacing

        if target_mod and target_mod in wb["center"]:
            c_mods = wb["center"]
            if len(c_mods) == 1:
                GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.LEFT, False)
                GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.RIGHT, False)
                return
            widths = [self._estimate_wb_module_px(m, wb, cached_pills) for m in c_mods]
            active_count = sum(1 for w in widths if w > 0)
            total_c_w = sum(widths) + max(0, active_count - 1) * box_spacing
            start_x = (mon_w - total_c_w) / 2.0
            cursor_x = start_x
            for mod, w in zip(c_mods, widths):
                if mod == target_mod:
                    eff_w = w if w > 0 else 44.0
                    center_x = cursor_x + (eff_w / 2.0)
                    target_left = int(round(center_x - (win_w / 2.0)))
                    clamped_left = max(edge_margin, min(target_left, max_margin))
                    GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.LEFT, True)
                    GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.RIGHT, False)
                    GtkLayerShell.set_margin(self, GtkLayerShell.Edge.LEFT, clamped_left)
                    return
                if w > 0:
                    cursor_x += w + box_spacing

        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.LEFT, False)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.RIGHT, False)

    def _on_cc_height_allocated(self) -> bool:
        self._cc_refit_pending = False
        if self.mode != "settings":
            return False
        has_fill = any(
            getattr(c.box_model if hasattr(c, "box_model") else c, "fill_window", False)
            for c in self.listbox.get_children()
        )
        if has_fill or getattr(self.panel, "section", "") in ("updates", "telemetry"):
            self.refresh_view()
        else:
            self._adjust_scroll_height()
        return False

    def _on_size_allocate(self, _widget, allocation) -> None:
        self._cancel_leave_close()
        if self._uses_layer_shell_cc_height() and allocation.height >= 240:
            h_px = int(allocation.height)
            if self._allocated_cc_h is None or abs(h_px - self._allocated_cc_h) >= 1:
                self._allocated_cc_h = h_px
                if not self._cc_refit_pending:
                    self._cc_refit_pending = True
                    GLib.idle_add(self._on_cc_height_allocated)
        if allocation.width > 40 and abs(allocation.width - self._last_allocated_w) > 2:
            self._last_allocated_w = allocation.width
            self._apply_dynamic_anchor(actual_win_w=allocation.width)

    def set_key_capture_grab(self, active: bool) -> None:
        if HAS_LAYER_SHELL and os.environ.get("NC_UI_OFFSCREEN") != "1":
            if active:
                mode = GtkLayerShell.KeyboardMode.EXCLUSIVE
            else:
                still_busy = (
                    self.prompt_bar.get_visible()
                    or bool(get_active_inline_entry())
                    or bool(STATE.get("keyboard", "capture_mode", ""))
                )
                mode = GtkLayerShell.KeyboardMode.EXCLUSIVE if still_busy else self._default_keyboard_mode()
            GtkLayerShell.set_keyboard_mode(self, mode)

    def _apply_css(self) -> None:
        self.geom = THEME["geometry"]
        self.colors = THEME["colors"]
        self.typo = THEME["typography"]
        p = get_semantic_palette()
        if hasattr(self, "content_canvas") and self.content_canvas is not None:
            self.content_canvas.sync_theme()
        if hasattr(self, "nav_canvas") and self.nav_canvas is not None:
            self.nav_canvas.sync_theme()

        bg = get_effective_window_bg(self.wb_look.get("bg"))
        rad = self.wb_look["rad"] if self.wb_look["rad"] is not None else self.geom["window_border_radius"]
        bw = self.geom["window_border_width"]
        bc = self.colors["window_border"]
        c_pri = p["focus"]
        c_acc = p["intent"]

        css = f"""
        window {{
            background-color: transparent;
            font-family: '{self.typo["font_family"]}', 'Font Awesome 6 Free', 'Font Awesome 6 Brands', 'Symbols Nerd Font', monospace;
            font-size: {self.typo["font_size"]}pt;
            font-weight: {self.typo["font_weight"]};
            font-feature-settings: "liga=0, clig=0, dlig=0, hlig=0";
        }}
        #main-container {{
            background-color: {bg};
            border-radius: {rad}px;
            border: {bw}px solid {bc};
        }}
        list, row, scrolledwindow, scrollbar, #nc-card-eb {{
            background: transparent;
            background-color: transparent;
            border: none;
            padding: 0px;
            margin: 0px;
            outline: none;
            box-shadow: none;
        }}
        row:hover {{
            background-color: transparent;
        }}
        link {{ color: inherit; text-decoration: none; }}
        link:hover {{ color: {c_acc}; text-shadow: 0 0 4px {c_acc}; }}
        a {{ text-decoration: none; }}
        scrollbar, scrollbar contents, scrollbar trough {{ min-width: 4px; min-height: 0px; margin: 0px; padding: 0px; border: none; }}
        scrollbar slider, scrollbar.overlay-indicator slider, scrollbar.overlay-indicator.hovering slider, scrollbar.overlay-indicator.dragging slider {{ background-color: rgba(255, 255, 255, 0.2); border-radius: 4px; min-width: 4px; min-height: 24px; margin: 0px; padding: 0px; border: none; }}
        scrollbar slider:hover, scrollbar slider:active {{ background-color: {c_acc}; }}
        """
        if self._css_provider is None:
            self._css_provider = Gtk.CssProvider()
            Gtk.StyleContext.add_provider_for_screen(
                Gdk.Screen.get_default(),
                self._css_provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
            )
        self._css_provider.load_from_data(css.encode("utf-8"))

    def _build_layout(self) -> None:
        self.set_app_paintable(True)
        self.main_box = Gtk.EventBox()
        self.main_box.set_name("main-container")
        self.root_box = self.main_box  # Alias per compatibilità con tool esterni / nc-shot
        self.main_box.add_events(
            Gdk.EventMask.ENTER_NOTIFY_MASK
            | Gdk.EventMask.LEAVE_NOTIFY_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
            | Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.SCROLL_MASK
            | Gdk.EventMask.SMOOTH_SCROLL_MASK
        )
        self.main_box.connect("enter-notify-event", self._on_window_enter)
        self.main_box.connect("motion-notify-event", self._on_window_motion)
        self.main_box.connect("button-press-event", self._on_window_button_press)
        self.main_box.connect("scroll-event", self._on_scrolled_window_scroll)
        self.add(self.main_box)

        if self.mode == "settings":
            m_left, m_right, m_top, m_bot, hbox_gap = self._get_inner_box_margins()
        else:
            pad = int(self.geom.get("panel_padding", 10))
            m_left = m_right = m_top = m_bot = pad
            hbox_gap = 8
        outer_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        outer_vbox.set_margin_top(m_top)
        outer_vbox.set_margin_bottom(m_bot)
        outer_vbox.set_margin_start(m_left)
        outer_vbox.set_margin_end(m_right)
        self.outer_vbox = outer_vbox

        self.root_overlay = Gtk.Overlay()
        self.root_overlay.add(outer_vbox)
        self.main_box.add(self.root_overlay)

        self._inline_entry = Gtk.Entry()
        self._inline_entry.set_opacity(0.0)
        self._inline_entry.set_size_request(1, 1)
        self._inline_entry.set_can_focus(False)
        self._inline_entry.set_halign(Gtk.Align.START)
        self._inline_entry.set_valign(Gtk.Align.START)
        self._inline_entry.connect("changed", self._on_inline_entry_changed)
        self._inline_entry.connect("notify::cursor-position", self._on_inline_cursor_notify)
        self._inline_entry.connect("activate", self._on_inline_entry_activate)
        self._inline_entry.connect("key-press-event", self._on_inline_entry_key_press)
        self.root_overlay.add_overlay(self._inline_entry)
        self.root_overlay.set_overlay_pass_through(self._inline_entry, True)

        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=hbox_gap)
        self.main_hbox = hbox
        outer_vbox.pack_start(hbox, True, True, 0)

        if self.mode == "settings":
            self.nav_scrolled = Gtk.ScrolledWindow()
            self.nav_scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            self.nav_scrolled.set_overlay_scrolling(True)
            self.nav_scrolled.set_propagate_natural_width(True)
            self.nav_scrolled.set_propagate_natural_height(False)
            self.nav_scrolled.set_can_focus(False)
            self.nav_scrolled.connect("scroll-event", self._on_scrolled_window_scroll)

            self.nav_listbox = Gtk.ListBox()
            self.nav_listbox.set_selection_mode(Gtk.SelectionMode.NONE)
            self.nav_listbox.set_can_focus(False)
            self.nav_scrolled.add(self.nav_listbox)
            hbox.pack_start(self.nav_scrolled, False, False, 0)

        self.scrolled_window = Gtk.ScrolledWindow()
        self.scrolled_window.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.NEVER)
        self.scrolled_window.set_overlay_scrolling(True)
        # Blocco orizzontale UI-wide: nel Control Center non propaga mai la larghezza naturale dei figli
        self.scrolled_window.set_propagate_natural_width(self.mode != "settings")
        self.scrolled_window.set_propagate_natural_height(self.mode != "settings")
        p_max = self.panel.get_max_height() if hasattr(self.panel, "get_max_height") else None
        raw_max_h = self._get_cc_target_height_px() if self.mode == "settings" else int(p_max or self.geom["popup_max_height"])
        if self.mode == "settings":
            max_h = raw_max_h
        else:
            _mw, max_popup_h = self._get_popup_bounds_px()
            max_h = min(raw_max_h, max_popup_h)
        self.scrolled_window.set_max_content_height(max_h)
        self.scrolled_window.set_can_focus(False)
        self.scrolled_window.connect("scroll-event", self._on_scrolled_window_scroll)

        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self.listbox.set_can_focus(False)
        self.scrolled_window.add(self.listbox)

        self.prompt_bar = NcPromptBar(
            self.content_canvas,
            self._on_prompt_submit,
            self.hide_prompt,
            on_visibility_change=self._on_prompt_visibility_change,
            on_change=self._on_prompt_change,
        )
        self.prompt_bar.set_no_show_all(True)
        self.prompt_bar.hide()

        if self.mode == "settings":
            self.right_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            self.header_listbox = Gtk.ListBox()
            self.header_listbox.set_selection_mode(Gtk.SelectionMode.NONE)
            self.header_listbox.set_can_focus(False)
            self.right_vbox.pack_start(self.header_listbox, False, False, 0)
            self.right_vbox.pack_start(self.scrolled_window, True, True, 0)
            self.right_vbox.pack_start(self.prompt_bar, False, False, 0)
            hbox.pack_start(self.right_vbox, True, True, 0)

            self.cc_search_listbox = Gtk.ListBox()
            self.cc_search_listbox.set_selection_mode(Gtk.SelectionMode.NONE)
            self.cc_search_listbox.set_can_focus(False)
            self.cc_search_holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            self.cc_search_holder.set_halign(Gtk.Align.END)
            self.cc_search_holder.set_valign(Gtk.Align.START)
            self.cc_search_holder.pack_start(self.cc_search_listbox, False, False, 0)
            self.cc_search_holder.set_no_show_all(True)
            self.cc_search_holder.hide()
            self.root_overlay.add_overlay(self.cc_search_holder)
        else:
            hbox.pack_start(self.scrolled_window, True, True, 0)
            outer_vbox.pack_start(self.prompt_bar, False, False, 0)

        self.help_listbox = Gtk.ListBox()
        self.help_listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self.help_listbox.set_can_focus(False)
        self.help_listbox.set_halign(Gtk.Align.START)
        self.help_listbox.set_valign(Gtk.Align.START)
        self.help_holder = Gtk.EventBox()
        self.help_holder.set_visible_window(False)
        self.help_holder.set_halign(Gtk.Align.START)
        self.help_holder.set_valign(Gtk.Align.START)
        self.help_holder.add_events(
            Gdk.EventMask.ENTER_NOTIFY_MASK
            | Gdk.EventMask.LEAVE_NOTIFY_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
        )
        self.help_holder.connect("enter-notify-event", self._on_help_enter)
        self.help_holder.connect("leave-notify-event", self._on_help_leave)
        self.help_holder.connect("motion-notify-event", self._on_help_enter)
        self.help_holder.add(self.help_listbox)
        self.help_holder.set_no_show_all(True)
        self.help_holder.hide()
        self.root_overlay.add_overlay(self.help_holder)

        self.toast_listbox = Gtk.ListBox()
        self.toast_listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self.toast_listbox.set_can_focus(False)
        self.toast_holder = Gtk.EventBox()
        self.toast_holder.set_visible_window(False)
        self.toast_holder.set_halign(Gtk.Align.END)
        self.toast_holder.set_valign(Gtk.Align.END)
        self.toast_holder.add_events(
            Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK
        )
        self.toast_holder.connect("enter-notify-event", self._on_toast_enter)
        self.toast_holder.connect("leave-notify-event", self._on_toast_leave)
        self.toast_holder.add(self.toast_listbox)
        self.toast_holder.set_no_show_all(True)
        self.toast_holder.hide()
        self.root_overlay.add_overlay(self.toast_holder)
        self.connect("destroy", lambda *_a: (self._cancel_toast_timer(), self._cancel_help_timers()))

        if self.mode == "settings":
            target_h = -1 if self._uses_layer_shell_cc_height() else self._get_cc_target_height_px()
            self.set_size_request(self._get_cc_target_width_px(), target_h)
        else:
            self.set_size_request(self._total_window_px(), -1)

    def apply_layout_margins(self) -> None:
        self._ink_insets = None
        if self.mode == "settings":
            m_left, m_right, m_top, m_bot, hbox_gap = self._get_inner_box_margins()
            if getattr(self, "outer_vbox", None) is not None:
                self.outer_vbox.set_margin_top(m_top)
                self.outer_vbox.set_margin_bottom(m_bot)
                self.outer_vbox.set_margin_start(m_left)
                self.outer_vbox.set_margin_end(m_right)
            if getattr(self, "main_hbox", None) is not None:
                self.main_hbox.set_spacing(hbox_gap)
            self.wb_look = get_waybar_look()
            if HAS_LAYER_SHELL and os.environ.get("NC_UI_OFFSCREEN") != "1":
                _, outer_mar = get_layout_margins()
                side = str(CONFIG.get("control_center", {}).get("side", "right")).strip().lower()
                is_left = (side == "left")
                GtkLayerShell.set_margin(self, GtkLayerShell.Edge.TOP, outer_mar)
                GtkLayerShell.set_margin(self, GtkLayerShell.Edge.BOTTOM, outer_mar)
                GtkLayerShell.set_margin(
                    self,
                    GtkLayerShell.Edge.LEFT if is_left else GtkLayerShell.Edge.RIGHT,
                    outer_mar,
                )
            cc_cols = self._compute_cc_content_cols()
            self.content_canvas.set_cols(cc_cols)
            self._last_nav_height_px = -1
            self._last_nav_state = None
            self._nav_sized = False
            self._last_win_size = None
            self._last_sw_size = None
        self._adjust_scroll_height()

    def _on_shell_destroy(self, _widget) -> None:
        try:
            from core.privileges import is_sudo_session_unlocked, lock_sudo_session
            if is_sudo_session_unlocked():
                lock_sudo_session(drop_ticket=True)
        except Exception:
            pass
        if self._refresh_source_id is not None:
            try:
                GLib.source_remove(self._refresh_source_id)
            except Exception:
                pass
            self._refresh_source_id = None
        for obj, sid in getattr(self, "_geom_sig_ids", ()):
            try:
                obj.disconnect(sid)
            except Exception:
                pass
        self._geom_sig_ids = []

    def _on_screen_geometry_signal(self, *_args) -> None:
        GLib.idle_add(self.on_display_geometry_changed)

    def on_display_geometry_changed(self) -> bool:
        self._niri_geom_cache = None
        self._wb_layout_cache = None
        self._allocated_cc_h = None
        self._ink_insets = None
        self._last_nav_height_px = -1
        self._last_nav_state = None
        self._nav_sized = False
        self._last_win_size = None
        self._last_sw_size = None
        self._last_render_sig = None
        self._stable_cols_key = None
        self._stable_cols_val = 0
        if self.mode == "settings":
            cc_cols = self._compute_cc_content_cols()
            self.content_canvas.set_cols(cc_cols)
        if HAS_LAYER_SHELL and os.environ.get("NC_UI_OFFSCREEN") != "1":
            target_mon, _, _ = self._resolve_target_monitor()
            if target_mon is not None:
                GtkLayerShell.set_monitor(self, target_mon)
        self.refresh_view()
        if os.environ.get("NC_UI_OFFSCREEN") != "1":
            GLib.timeout_add(180, lambda: self._adjust_scroll_height() and False)
        return False

    def _check_step_rate_limit(self, continuous: bool = False) -> bool:
        return allow_step_input(continuous=continuous)

    def _resolve_hovered_focus_target(self) -> tuple[int, dict | None]:
        if not self._focus_targets:
            self._rebuild_focus_targets()
        for idx, t in enumerate(self._focus_targets):
            card = t["card"]
            if card.is_hovered and card._hovered_anchor_idx == t["anchor_idx"]:
                return idx, t
        return -1, None

    def _on_card_anchor_hover(self, card: AsciiCard, anchor_idx: int | None, from_motion: bool) -> None:
        if card is not None and (
            (self.help_listbox is not None and card.get_parent() is self.help_listbox)
            or (self.toast_listbox is not None and card.get_parent() is self.toast_listbox)
        ):
            return
        if anchor_idx is None:
            self._schedule_hide_hover_help()
            return
        if not from_motion or not pointer_moved_recently():
            return
        if 0 <= self._focused_target_idx < len(self._focus_targets):
            cur = self._focus_targets[self._focused_target_idx]
            if cur["card"] is not card or cur["anchor_idx"] != anchor_idx:
                self._focused_target_idx = -1
                self._focused_uri_memory = None
                for ch in self._all_card_widgets():
                    if isinstance(ch, AsciiCard):
                        ch.set_focused_anchor(None)
        links = getattr(card, "_focusable_links", None) or []
        if 0 <= anchor_idx < len(links):
            item = links[anchor_idx]
            target_uri = item.get("action_prefix") or item.get("uri") or ""
            rect = card.get_anchor_rect_in(self.root_overlay, anchor_idx)
            self._schedule_hover_help(target_uri, rect)

    def _dispatch_slider_step(self, target: dict, direction: str, continuous: bool = False, scroll_card=None) -> bool:
        prefix = target.get("action_prefix", "")
        if prefix:
            step_uri = advance_slider_uri(prefix, direction)
            if not step_uri:
                return True
        else:
            step_uri = target.get("inc_uri") if direction == "up" else target.get("dec_uri")
        if not step_uri:
            return False
        if self._check_step_rate_limit(continuous=continuous):
            if continuous and scroll_card is not None:
                scroll_card._scroll_accum = 0.0
            self.dispatch_uri(step_uri)
        return True

    def _dispatch_hover_scroll_target(self, target: dict | None, card: AsciiCard, event) -> bool | None:
        if target is None:
            return None
        ev_time = int(getattr(event, "time", 0) or (event.get_time() if hasattr(event, "get_time") else 0))
        if ev_time > 0 and ev_time == getattr(card, "_passthrough_scroll_ev_time", 0):
            return None
        if target.get("is_slider"):
            direction = card._resolve_scroll_direction(event)
            if direction == "":
                return True
            if direction in ("up", "down"):
                is_cont = _is_continuous_scroll_event(event)
                res = self._dispatch_slider_step(target, direction, continuous=is_cont, scroll_card=card)
                if not is_cont:
                    card._scroll_accum = 0.0
                return res
            return False
        sz_meta = get_scroll_zone_meta(target.get("uri", ""))
        if sz_meta and card.on_link:
            sz_prefix, can_up, can_down = sz_meta
            direction = card._resolve_scroll_direction(event)
            if direction == "":
                return True
            if direction in ("up", "down"):
                if (direction == "up" and not can_up) or (direction == "down" and not can_down):
                    if ev_time > 0:
                        card._passthrough_scroll_ev_time = ev_time
                    card._scroll_accum = 0.0
                    return False
                is_cont = _is_continuous_scroll_event(event)
                if self._check_step_rate_limit(continuous=is_cont):
                    card._scroll_accum = 0.0
                    card.on_link(f"{sz_prefix}:{direction}")
                elif not is_cont:
                    card._scroll_accum = 0.0
                return True
        return None

    def _intercept_card_scroll(self, card: AsciiCard, event) -> bool:
        h_idx = card._hovered_anchor_idx
        if h_idx is None:
            return False
        if not self._focus_targets:
            self._rebuild_focus_targets()
        h_target = next(
            (t for t in self._focus_targets if t["card"] is card and t["anchor_idx"] == h_idx),
            None,
        )
        res = self._dispatch_hover_scroll_target(h_target, card, event)
        return bool(res) if res is not None else False

    def _resolve_active_scroll_card(self) -> AsciiCard | None:
        cards = [
            c for c in self.listbox.get_children()
            if isinstance(c, AsciiCard) and not getattr(c.box_model, "is_divider", False)
        ]
        return next((c for c in cards if c.is_hovered and getattr(c.box_model, "scroll_action", None)), None)

    def _can_card_consume_scroll(self, card: AsciiCard, direction: str) -> bool:
        vadj = self.scrolled_window.get_vadjustment() if hasattr(self, "scrolled_window") else None
        if vadj is None:
            return True
        upper = float(vadj.get_upper())
        page = float(vadj.get_page_size())
        if upper <= page + 2.0:
            return True
        alloc = card.get_allocation()
        if alloc.height <= 0:
            return True
        vp_top = float(vadj.get_value())
        vp_bot = vp_top + page
        card_top = float(alloc.y)
        card_bot = card_top + float(alloc.height)
        if direction == "down" and card_bot > (vp_bot + 4.0) and card_top > (vp_top + 4.0):
            return False
        if direction == "up" and card_top < (vp_top - 4.0) and card_bot < (vp_bot - 4.0):
            return False
        return True

    def _on_scrolled_window_scroll(self, sw_widget, event) -> bool:
        self._mouse_inside = True
        self._cancel_leave_close()
        self.dismiss_hover_help()
        _hov_i, hov_target = self._resolve_hovered_focus_target()
        if hov_target is not None:
            res = self._dispatch_hover_scroll_target(hov_target, hov_target["card"], event)
            if res is not None:
                return res
        target = self._resolve_active_scroll_card()
        if target is not None:
            ev_time = int(getattr(event, "time", 0) or (event.get_time() if hasattr(event, "get_time") else 0))
            if ev_time > 0 and ev_time == getattr(target, "_passthrough_scroll_ev_time", 0):
                return False
            return target._on_scroll(sw_widget, event)
        return False

    def _setup_timer(self) -> None:
        if os.environ.get("NC_UI_OFFSCREEN") == "1":
            return
        if self._refresh_source_id is not None:
            GLib.source_remove(self._refresh_source_id)
            self._refresh_source_id = None
        interval = self.panel.get_refresh_ms()
        if interval and interval > 0:
            self._refresh_source_id = GLib.timeout_add(interval, self._on_timer_tick)

    def _on_timer_tick(self) -> bool:
        if (
            self.get_visible()
            and not self.is_cc_search_open()
            and not self.panel.is_busy()
            and not self.prompt_bar.get_visible()
            and not get_active_inline_entry()
            and not self.is_pointer_busy()
            and self.panel.should_refresh_on_tick()
        ):
            self.refresh_view()
        return True

    def _on_realize(self, _widget) -> None:
        self._apply_dynamic_anchor()
        self._adjust_scroll_height()

    def _on_prompt_visibility_change(self) -> None:
        self.bump_leave_guard(0.45)
        self._adjust_scroll_height()
        GLib.idle_add(self._adjust_scroll_height)

    def _adjust_scroll_height(self) -> bool:
        if os.environ.get("NC_UI_OFFSCREEN") == "1":
            return False
        for child in self._all_card_widgets():
            if isinstance(child, AsciiCard):
                child.lbl.queue_resize()
        if self.header_listbox is not None:
            self.header_listbox.queue_resize()
        self.listbox.queue_resize()
        _min_h, nat_h = self.listbox.get_preferred_height()
        prompt_h = 0
        if self.prompt_bar.get_visible():
            _pm, p_nat = self.prompt_bar.get_preferred_height()
            prompt_h = max(p_nat, int(self.content_canvas.char_h * 3 + 12)) + 6

        if self.mode == "settings":
            _ml, _mr, m_top, m_bot, _gap = self._get_inner_box_margins()
            bw = int(self.geom.get("window_border_width", 1))
            tot_w = self._get_cc_target_width_px()
            tot_h = self._get_cc_target_height_px()
            avail_list_h = max(120, tot_h - m_top - m_bot - (2 * bw))
            ls_cc_h = self._uses_layer_shell_cc_height()

            if avail_list_h != self._last_nav_height_px or not getattr(self, "_nav_sized", False):
                if avail_list_h != self._last_nav_height_px:
                    self._populate_settings_nav(target_h_px=avail_list_h)
                for nav_ch in self.nav_listbox.get_children():
                    nav_ch.queue_resize()
                self.nav_listbox.queue_resize()
                _nmin, nav_nat_h = self.nav_listbox.get_preferred_height()
                if nav_nat_h > avail_list_h + 2:
                    self.nav_scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
                else:
                    self.nav_scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.NEVER)
                nav_w_px = int(round(self.nav_canvas.total_px))
                self.nav_scrolled.set_size_request(nav_w_px, -1 if ls_cc_h else avail_list_h)
                self._nav_sized = True

            hdr_nat_h = 0
            if self.header_listbox is not None:
                _hmin, hdr_nat_h = self.header_listbox.get_preferred_height()
            avail_body_h = max(80, avail_list_h - hdr_nat_h - prompt_h)

            if nat_h > avail_body_h:
                self.scrolled_window.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            else:
                self.scrolled_window.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.NEVER)
            self.scrolled_window.set_max_content_height(avail_body_h)
            right_w_px = int(round(self.content_canvas.total_px))
            if self.header_listbox is not None:
                self.header_listbox.set_size_request(right_w_px, -1)
            sw_sz = (right_w_px, -1 if ls_cc_h else avail_body_h)
            if getattr(self, "_last_sw_size", None) != sw_sz:
                self._last_sw_size = sw_sz
                self.scrolled_window.set_size_request(*sw_sz)
            win_sz = (tot_w, -1 if ls_cc_h else tot_h)
            if getattr(self, "_last_win_size", None) != win_sz:
                self._last_win_size = win_sz
                self.set_size_request(*win_sz)
                if not ls_cc_h:
                    self.resize(tot_w, tot_h)
            return False

        pad = int(self.geom.get("panel_padding", 10)) * 2
        bw = int(self.geom.get("window_border_width", 1))
        p_max = self.panel.get_max_height() if hasattr(self.panel, "get_max_height") else None
        _mw, max_popup_h = self._get_popup_bounds_px()
        max_h = min(int(p_max or self.geom["popup_max_height"]), max_popup_h)
        self.scrolled_window.set_max_content_height(max_h)
        avail_list_h = max(60, max_h - prompt_h)
        if nat_h > avail_list_h:
            self.scrolled_window.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            list_h = avail_list_h
        else:
            self.scrolled_window.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.NEVER)
            list_h = max(nat_h, 24)

        sw_sz = (-1, list_h)
        if getattr(self, "_last_sw_size", None) != sw_sz:
            self._last_sw_size = sw_sz
            self.scrolled_window.set_size_request(-1, list_h)
        tot_w = self._total_window_px()
        tot_h = max(24, list_h + prompt_h + pad + (2 * bw))
        win_sz = (tot_w, tot_h)
        if getattr(self, "_last_win_size", None) != win_sz:
            self._last_win_size = win_sz
            self.set_size_request(tot_w, tot_h)
            self.resize(tot_w, tot_h)
        return False

    def _on_card_hover_change(self, card: AsciiCard, is_entering: bool) -> None:
        self._mouse_inside = True
        self._cancel_leave_close()
        children = [c for c in self.listbox.get_children() if isinstance(c, AsciiCard) and c.hover_enable]
        if is_entering:
            if self._focused_target_idx >= 0:
                cur_t = self._focus_targets[self._focused_target_idx] if self._focused_target_idx < len(self._focus_targets) else {}
                card_has_slider = bool(getattr(card.box_model, "scroll_action", None)) or any(
                    t["card"] is card and t.get("is_slider") for t in self._focus_targets
                )
                if pointer_moved_recently() and (not cur_t.get("is_slider") or card_has_slider):
                    self._focused_target_idx = -1
                    self._focused_uri_memory = None
                    for ch in self._all_card_widgets():
                        if isinstance(ch, AsciiCard):
                            ch.set_focused_anchor(None)
            try:
                self._hovered_card_idx = children.index(card)
            except ValueError:
                self._hovered_card_idx = None
            for c in children:
                c.set_hovered_visual(c is card)
        else:
            if any(c.is_hovered for c in children):
                return
            self._hovered_card_idx = None
            for c in children:
                c.set_hovered_visual(False)

    def sync_inline_gtk_entry(self, text: str, cursor_pos: int = -1, grab_focus: bool = True) -> None:
        if self._inline_entry is None:
            return
        self.bump_leave_guard(0.50)
        if HAS_LAYER_SHELL and os.environ.get("NC_UI_OFFSCREEN") != "1":
            GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.EXCLUSIVE)
        self._inline_entry.set_can_focus(True)
        self._suppress_inline_sync = True
        try:
            self._inline_entry.set_text(str(text or ""))
            self._inline_entry.set_position(int(cursor_pos))
        finally:
            self._suppress_inline_sync = False
        if grab_focus:
            self._inline_entry.grab_focus()
            self._inline_entry.set_position(int(cursor_pos))
            GLib.idle_add(self._focus_inline_entry_idle)

    def _focus_inline_entry_idle(self) -> bool:
        if get_active_inline_entry() and self._inline_entry is not None and self._inline_entry.get_can_focus():
            if not self._inline_entry.has_focus():
                self._inline_entry.grab_focus()
                self._inline_entry.set_position(len(self._inline_entry.get_text()))
        return False

    def release_inline_gtk_entry(self) -> None:
        if self._inline_entry is not None:
            self._suppress_inline_sync = True
            try:
                self._inline_entry.set_text("")
            finally:
                self._suppress_inline_sync = False
            self._inline_entry.set_can_focus(False)
        if (
            HAS_LAYER_SHELL
            and os.environ.get("NC_UI_OFFSCREEN") != "1"
            and not self.prompt_bar.get_visible()
            and not STATE.get("keyboard", "capture_mode", "")
        ):
            GtkLayerShell.set_keyboard_mode(self, self._default_keyboard_mode())

    def _on_inline_entry_changed(self, entry) -> None:
        if self._suppress_inline_sync or not get_active_inline_entry():
            return
        sync_inline_from_gtk_entry(entry.get_text(), entry.get_position(), shell=self)

    def _on_inline_cursor_notify(self, entry, _pspec) -> None:
        if self._suppress_inline_sync or not get_active_inline_entry():
            return
        sync_inline_from_gtk_entry(entry.get_text(), entry.get_position(), shell=self)

    def _on_inline_entry_activate(self, _entry) -> None:
        if get_active_inline_entry():
            self._mark_action_dispatched()
            commit_active_inline_entry(shell=self)

    def _on_inline_entry_key_press(self, _widget, event) -> bool:
        if not get_active_inline_entry():
            return False
        is_ctrl = bool(event.state & Gdk.ModifierType.CONTROL_MASK)
        is_shift = bool(event.state & Gdk.ModifierType.SHIFT_MASK)
        kv = event.keyval
        if is_ctrl and kv in (Gdk.KEY_f, Gdk.KEY_F):
            if self.mode == "settings":
                self.open_cc_global_search()
                return True
        if kv == Gdk.KEY_Escape:
            cancel_inline_entry(refresh=True)
            return True
        if kv in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self._mark_action_dispatched()
            return commit_active_inline_entry(shell=self)
        if kv in (Gdk.KEY_Tab, Gdk.KEY_KP_Tab, Gdk.KEY_ISO_Left_Tab):
            rev = is_shift or (kv == Gdk.KEY_ISO_Left_Tab)
            return step_inline_completion(reverse=rev, shell=self)
        if kv in (Gdk.KEY_Up, Gdk.KEY_KP_Up, Gdk.KEY_Down, Gdk.KEY_KP_Down):
            step_inline_completion(delta_arrow=(-1 if kv in (Gdk.KEY_Up, Gdk.KEY_KP_Up) else 1), shell=self)
            return True
        return False

    def is_cc_search_open(self) -> bool:
        return self.mode == "settings" and get_active_inline_entry() == "__cc_global_search__"

    def open_cc_global_search(self, initial_query: str = "") -> None:
        if self.mode != "settings":
            return
        self.dismiss_hover_help()
        start_inline_input(
            "__cc_global_search__",
            shell=self,
            trigger_uri="cc:global_search_open",
            initial_text=initial_query,
            max_len=48,
            is_picker=True,
            on_cancel=self._hide_cc_search_overlay_only,
        )

    def _hide_cc_search_overlay_only(self) -> None:
        if self.cc_search_holder is not None:
            self.cc_search_holder.hide()

    def close_cc_global_search(self) -> None:
        if self.is_cc_search_open():
            cancel_inline_entry(refresh=True)
        else:
            self._hide_cc_search_overlay_only()

    def _render_cc_global_search_overlay(self) -> None:
        if self.mode != "settings" or self.cc_search_holder is None or self.cc_search_listbox is None:
            return
        if not self.is_cc_search_open():
            for ch in self.cc_search_listbox.get_children():
                self.cc_search_listbox.remove(ch)
            self.cc_search_holder.hide()
            return

        query = get_inline_entry_buffer()
        results = search_control_center(query, limit=10)
        set_inline_candidates(
            "__cc_global_search__",
            [(r["label"], f"cc:search_jump:{r['section']}:{r['subtab']}") for r in results],
        )

        popup_cols = min(74, max(54, self.content_canvas.total_cols - 6))
        popup_canvas = Canvas(total_cols=popup_cols)
        ov_box = Box(
            t("control_center.global_search_results", p0=len(results)),
            icon="\uf002",
            actions=[
                ActionBtn("\uf00d", "cc:global_search_close", tone="danger_soft"),
            ],
            highlighted=True,
            fixed_width=True,
            min_cols=popup_cols,
            max_cols=popup_cols,
        )
        ov_box.is_overlay = True
        ov_box.add_row(" ")
        if not results:
            ov_box.add_row(f"  {Txt(t('ui.search_no_results'), role='inactive')}")
        else:
            cand_idx = get_inline_candidate_index("__cc_global_search__")
            eff_idx = cand_idx if (0 <= cand_idx < len(results)) else 0
            rows = []
            for idx, r in enumerate(results):
                is_sel = (idx == eff_idx)
                jump_uri = f"cc:search_jump:{r['section']}:{r['subtab']}"
                loc_txt = popup_canvas.truncate_plain(f"{r['sec_title']} \u203a {r['sub_title']}", 35)
                lbl_txt = popup_canvas.truncate_plain(r["label"], 30)
                rows.append([
                    InspectorCell(loc_txt, jump_uri, selected=is_sel, icon=r["icon"]),
                    ActionBtn(lbl_txt, jump_uri, tone="focus" if is_sel else "neutral", brackets=False),
                ])
            ov_box.add_table(
                headers=[t("control_center.search_col_location"), t("control_center.search_col_setting")],
                widths=[38, "flex"],
                rows=rows,
                aligns=["left", "left"],
            )
        ov_box.add_section()
        ov_box.add_row(f"  {Txt(t('ui.global_search_keys_hint'), role='inactive')}")

        for ch in self.cc_search_listbox.get_children():
            self.cc_search_listbox.remove(ch)
        ov_card = AsciiCard(ov_box, popup_canvas, on_link=self.dispatch_uri, hover_enable=False)
        self.cc_search_listbox.add(ov_card)

        _ml, m_right, m_top, _mb, _gap = self._get_inner_box_margins()
        self.cc_search_holder.set_margin_end(m_right)
        self.cc_search_holder.set_margin_top(m_top + int(round(self.content_canvas.char_h * 1.15)))
        self.cc_search_holder.set_no_show_all(False)
        self.cc_search_holder.show_all()
        self.cc_search_holder.set_no_show_all(True)

    def _current_view_key(self) -> tuple[str, str, str]:
        sec = str(getattr(self.panel, "section", getattr(self.panel, "active_section", "")) or "")
        if hasattr(self.panel, "get_subtab"):
            sub = str(self.panel.get_subtab() or "")
        else:
            sub = str(getattr(self.panel, "active_tab", getattr(self.panel, "active_subtab", "")) or "")
        return (str(self.panel_id or ""), sec, sub)

    def _cancel_toast_timer(self) -> None:
        if self._toast_timer_id is not None:
            try:
                GLib.source_remove(self._toast_timer_id)
            except Exception:
                pass
            self._toast_timer_id = None

    def _mark_action_dispatched(self) -> None:
        vk = self._current_view_key()
        self._action_seq[vk] = int(self._action_seq.get(vk, 0)) + 1
        if self._toast_level == "err" and self.toast_holder is not None and self.toast_holder.get_visible():
            self.dismiss_toast()

    @staticmethod
    def _is_nav_only_uri(uri: str) -> bool:
        u = str(uri or "")
        if u.startswith((
            "cc:section:",
            "cc:subtab:",
            "cc:lang:",
            "cc:global_search_",
            "cc:search_jump:",
            "ui:toggle_expand",
            "ui:refresh",
            "ui:tab:",
            "ui:state:",
            "ui:panel:",
            "ui:open_settings",
            "ui:close",
            "ui:prompt:",
            "http://",
            "https://",
            "ncui:toast_",
            "ncui:help_",
        )):
            return True
        return ":select_out:" in u or ":select_iface:" in u or ":inspect:" in u

    def dismiss_toast(self) -> None:
        self._cancel_toast_timer()
        self._toast_hovered = False
        if self.toast_listbox is not None:
            for ch in self.toast_listbox.get_children():
                self.toast_listbox.remove(ch)
        if self.toast_holder is not None:
            self.toast_holder.hide()

    def _on_toast_timeout(self) -> bool:
        self._toast_timer_id = None
        if not self._toast_hovered:
            self.dismiss_toast()
        return False

    def _on_toast_enter(self, *_args) -> bool:
        self._toast_hovered = True
        self._cancel_toast_timer()
        return False

    def _on_toast_leave(self, _w=None, event=None) -> bool:
        if event is not None and getattr(event, "detail", None) == Gdk.NotifyType.INFERIOR:
            return False
        self._toast_hovered = False
        if (
            self.toast_holder is not None
            and self.toast_holder.get_visible()
            and self._toast_duration_ms > 0
            and os.environ.get("NC_UI_OFFSCREEN") != "1"
        ):
            self._cancel_toast_timer()
            self._toast_timer_id = GLib.timeout_add(1200, self._on_toast_timeout)
        return False

    def _cancel_help_timers(self) -> None:
        for attr in ("_help_show_timer_id", "_help_hide_timer_id"):
            tid = getattr(self, attr, None)
            if tid is not None:
                try:
                    GLib.source_remove(tid)
                except Exception:
                    pass
                setattr(self, attr, None)

    def dismiss_hover_help(self) -> None:
        self._cancel_help_timers()
        self._help_hovered = False
        self._help_expanded = False
        self._help_pending_uri = None
        self._help_pending_rect = None
        self._help_visible_uri = None
        self._help_visible_rect = None
        self._help_visible_view_key = None
        if self.help_listbox is not None:
            for ch in self.help_listbox.get_children():
                self.help_listbox.remove(ch)
        if self.help_holder is not None:
            self.help_holder.hide()

    def _pointer_over_help_holder(self) -> bool:
        if self.help_holder is None or not self.help_holder.get_visible():
            return False
        try:
            win = self.get_window()
            if win is None:
                return False
            seat = self.get_display().get_default_seat()
            if seat is None:
                return False
            ptr = seat.get_pointer()
            if ptr is None:
                return False
            _w, px, py, _mask = win.get_device_position(ptr)
            coords = _unpack_translated_coords(self.help_holder.translate_coordinates(self, 0, 0))
            if coords is None:
                return False
            hx, hy = coords
            alloc = self.help_holder.get_allocation()
            return (hx <= px < (hx + alloc.width)) and (hy <= py < (hy + alloc.height))
        except Exception:
            return False

    def _on_help_enter(self, *_args) -> bool:
        self._help_hovered = True
        self._cancel_help_timers()
        self._help_pending_uri = None
        return False

    def _on_help_leave(self, widget=None, event=None) -> bool:
        if event is not None:
            if getattr(event, "detail", None) == Gdk.NotifyType.INFERIOR:
                return False
            if getattr(event, "mode", None) != Gdk.CrossingMode.NORMAL:
                return False
            if widget is not None and self.help_holder is not None:
                coords = _unpack_translated_coords(
                    widget.translate_coordinates(self.help_holder, int(event.x), int(event.y))
                )
                if coords is not None:
                    rx, ry = coords
                    alloc = self.help_holder.get_allocation()
                    if 1 <= rx < (alloc.width - 1) and 1 <= ry < (alloc.height - 1):
                        return False
        if self._pointer_over_help_holder():
            self._help_hovered = True
            return False
        self._help_hovered = False
        if not self._help_expanded:
            self._schedule_hide_hover_help(35)
        return False

    def _on_help_show_timeout(self) -> bool:
        self._help_show_timer_id = None
        uri = self._help_pending_uri
        rect = self._help_pending_rect
        pref_r = self._help_pending_prefer_right
        if not uri or not get_hover_tooltips_enabled():
            return False
        if self.is_cc_search_open() or self.prompt_bar.get_visible() or get_active_inline_entry():
            return False
        self.show_hover_help(uri, rect, expanded=False, prefer_right=pref_r)
        return False

    def _on_help_hide_timeout(self) -> bool:
        self._help_hide_timer_id = None
        if self._pointer_over_help_holder():
            self._help_hovered = True
            return False
        if self._help_hovered or self._help_expanded:
            return False
        self.dismiss_hover_help()
        return False

    def _schedule_hover_help(
        self,
        uri: str,
        anchor_rect: tuple[int, int, int, int] | None,
        *,
        prefer_right: bool = False,
    ) -> None:
        if not get_hover_tooltips_enabled() or os.environ.get("NC_UI_OFFSCREEN") == "1":
            return
        if self.is_cc_search_open() or self.prompt_bar.get_visible() or get_active_inline_entry() or self._help_expanded or self._help_hovered:
            return
        u = str(uri or "").strip()
        if not u or u.startswith("ncui:"):
            self._schedule_hide_hover_help()
            return
        if u == self._help_visible_uri:
            if self._help_hide_timer_id is not None:
                try:
                    GLib.source_remove(self._help_hide_timer_id)
                except Exception:
                    pass
                self._help_hide_timer_id = None
            return
        if lookup_action_help(u, mode=self.mode) is None:
            self._schedule_hide_hover_help()
            return
        self._cancel_help_timers()
        self._help_pending_uri = u
        self._help_pending_rect = anchor_rect
        self._help_pending_prefer_right = bool(prefer_right)
        delay_ms = max(200, int(round(get_hover_tooltip_delay() * 1000.0)))
        self._help_show_timer_id = GLib.timeout_add(delay_ms, self._on_help_show_timeout)

    def _schedule_hide_hover_help(self, grace_ms: int = 240) -> None:
        if self._help_show_timer_id is not None:
            try:
                GLib.source_remove(self._help_show_timer_id)
            except Exception:
                pass
            self._help_show_timer_id = None
        self._help_pending_uri = None
        if self._help_visible_uri is None or self._help_expanded or self._help_hovered or self._pointer_over_help_holder():
            return
        if os.environ.get("NC_UI_OFFSCREEN") == "1":
            return
        if self._help_hide_timer_id is not None:
            try:
                GLib.source_remove(self._help_hide_timer_id)
            except Exception:
                pass
        self._help_hide_timer_id = GLib.timeout_add(max(20, int(grace_ms)), self._on_help_hide_timeout)

    def show_hover_help(
        self,
        uri: str,
        anchor_rect: tuple[int, int, int, int] | None = None,
        *,
        expanded: bool = False,
        prefer_right: bool = False,
    ) -> bool:
        if self.help_holder is None or self.help_listbox is None:
            return False
        entry = lookup_action_help(uri, mode=self.mode)
        if entry is None:
            return False
        self._cancel_help_timers()
        eff_exp = bool(expanded) and (self.mode == "settings") and bool(entry["verbose"] or entry["diagram"])
        style = get_hover_help_style()

        if eff_exp:
            max_w = min(580, max(460, int(self.content_canvas.total_px * 0.68)))
        elif self.mode == "settings":
            max_w = min(440, max(340, int(self.content_canvas.total_px * 0.52)))
        else:
            max_w = min(380, max(280, int(self.content_canvas.total_px - 20)))

        diag_w = None
        if eff_exp and entry["diagram"]:
            diag_w = create_help_diagram(
                entry["diagram"],
                focus=entry.get("diagram_focus", ""),
                inset_x=10.0,
                height_px=110,
            )

        ov_card = HoverHelpCard(
            entry,
            style,
            mode=self.mode,
            expanded=eff_exp,
            on_link=self.dispatch_uri,
            diagram_widget=diag_w,
            max_width_px=max_w,
        )

        for ch in self.help_listbox.get_children():
            self.help_listbox.remove(ch)
        row = Gtk.ListBoxRow()
        row.set_selectable(False)
        row.set_activatable(False)
        row.set_can_focus(False)
        row.add(ov_card)
        self.help_listbox.add(row)

        for hw in ov_card.get_hover_widgets():
            hw.add_events(
                Gdk.EventMask.ENTER_NOTIFY_MASK
                | Gdk.EventMask.LEAVE_NOTIFY_MASK
                | Gdk.EventMask.POINTER_MOTION_MASK
            )
            hw.connect("enter-notify-event", self._on_help_enter)
            hw.connect("leave-notify-event", self._on_help_leave)
            hw.connect("motion-notify-event", self._on_help_enter)

        self.help_holder.set_no_show_all(False)
        self.help_holder.show_all()
        self.help_holder.set_no_show_all(True)

        pref_w = max(180, int(ov_card.card_width_px))
        pref_h = max(44, int(ov_card.card_height_px))
        pad = int(ov_card._SH_PAD)
        win_alloc = self.root_overlay.get_allocation() if self.root_overlay is not None else self.get_allocation()
        win_w = int(win_alloc.width) if win_alloc.width > 40 else int(self.get_preferred_width()[1] or 960)
        win_h = int(win_alloc.height) if win_alloc.height > 40 else int(max(400, self.get_preferred_height()[1] or 640))

        tip_side = "top"
        tip_off = 28.0
        if prefer_right and anchor_rect is not None:
            ax, ay, aw, ah = anchor_rect
            px = ax + aw + 4 - pad
            py = ay - 4 - pad
            tip_side = "left"
        elif anchor_rect is not None:
            ax, ay, aw, ah = anchor_rect
            px = ax - pad
            py = ay + ah + 4 - pad
            if py + pref_h + 6 > win_h and (ay - pref_h - 2) >= 4:
                py = ay - pref_h + pad - 4
                tip_side = "bottom"
        else:
            px = max(8, (win_w - pref_w) // 2)
            py = max(8, int(win_h * 0.22))

        px = max(4, min(int(px), max(4, win_w - pref_w - 4)))
        py = max(4, min(int(py), max(4, win_h - pref_h - 4)))
        if anchor_rect is not None:
            ax, ay, aw, ah = anchor_rect
            if tip_side == "left":
                tip_off = (ay + ah * 0.5) - (py + pad)
            else:
                tip_off = (ax + min(aw * 0.5, 48.0)) - (px + pad)
        ov_card.set_tip(tip_side, tip_off)

        self.help_holder.set_margin_start(px)
        self.help_holder.set_margin_top(py)

        self._help_visible_uri = entry["uri"]
        self._help_visible_rect = anchor_rect
        self._help_visible_prefer_right = bool(prefer_right)
        self._help_visible_view_key = self._current_view_key()
        self._help_expanded = eff_exp
        self._help_hovered = self._help_hovered or self._pointer_over_help_holder()
        return True

    def show_toast(self, msg: str, level: str = "ok", title: str = "", duration_ms: int | None = None) -> None:
        if self.toast_holder is None or self.toast_listbox is None:
            return
        msg_str = str(msg or "").strip()
        if not msg_str:
            return
        self._cancel_toast_timer()
        lvl = level if level in ("ok", "warn", "err") else "ok"
        if duration_ms is None:
            eff_dur = 2600 if lvl == "ok" else (4500 if lvl == "warn" else 0)
        else:
            eff_dur = max(0, int(duration_ms))

        self._toast_level = lvl
        self._toast_duration_ms = eff_dur
        self._toast_hovered = False

        pal = get_semantic_palette()
        if lvl == "ok":
            icon = "\uf00c"
            accent = pal["vitality"]
            b_txt = t("common.ok")
            b_bg = pal["vitality_bg"]
            hdr_pref = "OK"
        elif lvl == "warn":
            icon = "\uf071"
            accent = pal["attention"]
            b_txt = t("common.warn")
            b_bg = pal["attention_bg"]
            hdr_pref = "WARN"
        else:
            icon = "\uf071"
            accent = pal["fault"]
            b_txt = t("common.err")
            b_bg = pal["fault_bg"]
            hdr_pref = "ERR"

        eff_title = str(title or t("common.status")).strip()
        if self.mode == "settings":
            max_w = min(560, max(360, int(self.content_canvas.total_px * 0.62)))
        else:
            max_w = min(380, max(250, int(self.content_canvas.total_px - 16)))

        ov_card = StyledNotifCard(
            style=get_toast_style(),
            title=eff_title,
            body=msg_str,
            icon=icon,
            accent_hex=accent,
            badge_text=b_txt,
            badge_fg=accent,
            badge_bg=b_bg,
            close_uri="ncui:toast_close",
            header_prefix=hdr_pref,
            opacity=get_toast_opacity(),
            max_width_px=max_w,
            on_link=self.dispatch_uri,
        )

        for ch in self.toast_listbox.get_children():
            self.toast_listbox.remove(ch)
        row = Gtk.ListBoxRow()
        row.set_selectable(False)
        row.set_activatable(False)
        row.set_can_focus(False)
        row.add(ov_card)
        for tw in ov_card.get_hover_widgets():
            tw.connect("enter-notify-event", self._on_toast_enter)
            tw.connect("leave-notify-event", self._on_toast_leave)
        self.toast_listbox.add(row)

        if self.mode == "settings":
            _ml, m_right, _mt, m_bot, _gap = self._get_inner_box_margins()
        else:
            pad = int(self.geom.get("panel_padding", 10))
            m_right = pad
            m_bot = pad
        prompt_off = (max(0, self.prompt_bar.get_allocated_height()) + 8) if (self.prompt_bar and self.prompt_bar.get_visible()) else 0
        sh_pad = int(ov_card._SH_PAD)
        self.toast_holder.set_margin_end(max(2, m_right + int(round(self.content_canvas.char_w)) - sh_pad))
        self.toast_holder.set_margin_bottom(max(2, m_bot + int(round(self.content_canvas.char_h * 0.6)) + prompt_off - sh_pad))
        self.toast_holder.set_no_show_all(False)
        self.toast_holder.show_all()
        self.toast_holder.set_no_show_all(True)

        if eff_dur > 0 and os.environ.get("NC_UI_OFFSCREEN") != "1":
            self._toast_timer_id = GLib.timeout_add(eff_dur, self._on_toast_timeout)

    def activate_prompt(
        self,
        action_id: str,
        placeholder: str,
        default_txt: str = "",
        secret: bool = None,
        title: str = None,
        required: bool = False,
        completion=None,
    ) -> None:
        self.dismiss_hover_help()
        self.bump_leave_guard(0.50)
        cancel_inline_entry(refresh=False)
        if secret is None:
            secret = ("pass" in action_id.lower() or "secret" in action_id.lower())
        if HAS_LAYER_SHELL and os.environ.get("NC_UI_OFFSCREEN") != "1":
            GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.EXCLUSIVE)
        self.prompt_bar.activate(
            action_id,
            placeholder,
            default_txt,
            secret=secret,
            title=title,
            required=required,
            completion=completion,
        )

    def hide_prompt(self) -> None:
        self.bump_leave_guard(0.40)
        if HAS_LAYER_SHELL and self.mode != "settings" and os.environ.get("NC_UI_OFFSCREEN") != "1":
            GtkLayerShell.set_keyboard_mode(self, self._default_keyboard_mode())
        self.prompt_bar.deactivate()

    def _on_prompt_change(self, act_id: str, text: str) -> None:
        if act_id and hasattr(self.panel, "on_prompt_change") and self.panel.on_prompt_change(act_id, text):
            self.refresh_view()
            if self.prompt_bar.get_visible() and not self.prompt_bar.entry.has_focus():
                self.prompt_bar.entry.grab_focus()
                self.prompt_bar.entry.set_position(-1)

    def _on_prompt_submit(self, act_id: str, text: str) -> None:
        if not act_id:
            self.hide_prompt()
            return
        self._mark_action_dispatched()
        if act_id.startswith("cmd:"):
            self.hide_prompt()
            raw_cmd = act_id[4:].replace("{input}", text)
            probe.spawn_bg(["bash", "-c", raw_cmd])
            GLib.timeout_add(350, lambda: not self.refresh_view())
        else:
            self.panel.on_prompt_submit(act_id, text)
            if not self.prompt_bar.get_visible() and HAS_LAYER_SHELL and self.mode != "settings" and os.environ.get("NC_UI_OFFSCREEN") != "1":
                GtkLayerShell.set_keyboard_mode(self, self._default_keyboard_mode())
            self.refresh_view()

    def dispatch_uri(self, uri: str) -> None:
        log.debug(f"dispatch_uri: {uri}")
        self.bump_leave_guard(0.50)
        if get_active_inline_entry() and uri == get_active_inline_uri():
            if self._inline_entry is not None and self._inline_entry.get_can_focus():
                self._inline_entry.grab_focus()
                self._inline_entry.set_position(len(self._inline_entry.get_text()))
            return
        if uri == "cc:global_search_open":
            self.open_cc_global_search()
            return
        if uri == "cc:global_search_close":
            self.close_cc_global_search()
            return
        if uri == "ncui:toast_close":
            self.dismiss_toast()
            return
        if uri == "ncui:help_close":
            self.dismiss_hover_help()
            return
        if uri == "ncui:help_expand":
            if self._help_visible_uri:
                self.show_hover_help(
                    self._help_visible_uri,
                    self._help_visible_rect,
                    expanded=True,
                    prefer_right=self._help_visible_prefer_right,
                )
            return
        if uri == "ncui:help_collapse":
            if self._help_visible_uri:
                self.show_hover_help(
                    self._help_visible_uri,
                    self._help_visible_rect,
                    expanded=False,
                    prefer_right=self._help_visible_prefer_right,
                )
            return
        self.dismiss_hover_help()
        cancel_inline_entry(refresh=False)
        if _get_active_tui_id():
            from panels.cc_engine_resources import set_active_tui
            set_active_tui("", shell=self)
        if not self._is_nav_only_uri(uri):
            self._mark_action_dispatched()
        if uri.startswith("cc:search_jump:"):
            parts = uri.split(":", 3)
            sec_id = parts[2] if len(parts) > 2 else ""
            sub_id = parts[3] if len(parts) > 3 else ""
            if self.mode == "settings" and hasattr(self.panel, "set_section") and sec_id:
                self._focused_target_idx = -1
                self._focused_uri_memory = None
                self.panel.set_section(sec_id)
                if sub_id and hasattr(self.panel, "set_subtab"):
                    self.panel.set_subtab(sub_id)
                self.refresh_view()
            return
        if uri.startswith(("http://", "https://")):
            probe.spawn_bg(["xdg-open", uri])
            return
        if uri.startswith("ui:open_settings"):
            rest = uri.split(":", 2)[2].strip() if ":" in uri[len("ui:open_settings"):] else ""
            if ":" in rest:
                sec_id, sub_id = [p.strip() for p in rest.split(":", 1)]
            else:
                sec_id, sub_id = rest, ""
            if sec_id and sub_id:
                STATE.set("control_center", f"subtab_{sec_id}", sub_id)
            if self.mode == "settings" and hasattr(self.panel, "set_section"):
                if sec_id:
                    self._focused_target_idx = -1
                    self._focused_uri_memory = None
                    self.panel.set_section(sec_id)
                    if sub_id and hasattr(self.panel, "set_subtab"):
                        self.panel.set_subtab(sub_id)
                    self.refresh_view()
            else:
                nc_ctl_bin = find_bin("nc-ctl") or os.path.join(CORE_ROOT, "bin", "nc-ctl")
                cmd_args = [sys.executable, nc_ctl_bin, "settings"]
                if sec_id:
                    cmd_args.append(sec_id)
                    if sub_id:
                        cmd_args.append(sub_id)
                probe.spawn_bg(cmd_args)
                Gtk.main_quit()
            return
        if uri == "ui:toggle_expand":
            self.panel.is_expanded = not self.panel.is_expanded
            self.refresh_view()
            self._setup_timer()
            return
        if uri == "ui:refresh":
            self.refresh_view()
            self._setup_timer()
            return
        if uri == "ui:close":
            Gtk.main_quit()
            return
        if uri.startswith("ui:tab:"):
            self._focused_target_idx = -1
            self._focused_uri_memory = None
            self.panel.active_tab = uri.split(":", 2)[2]
            self.hide_prompt()
            self.refresh_view()
            self._setup_timer()
            return
        if uri.startswith("ui:state:"):
            parts = uri.split(":", 3)
            if len(parts) == 4:
                key, raw_val = parts[2], parts[3]
                val = True if raw_val.lower() == "true" else (False if raw_val.lower() == "false" else raw_val)
                self.panel.set_state(key, val)
                self.refresh_view()
                self._setup_timer()
            return
        if uri.startswith("ui:panel:"):
            sec_id = uri.split(":", 2)[2]
            if self.mode == "settings" and hasattr(self.panel, "set_section"):
                self._focused_target_idx = -1
                self._focused_uri_memory = None
                self.panel.set_section(sec_id)
                self.refresh_view()
            else:
                self.switch_panel(sec_id)
            return
        if uri.startswith("ui:copy:"):
            probe.copy_clipboard(uri.split(":", 2)[2])
            return
        if uri.startswith("ui:cmd:"):
            probe.spawn_bg(["bash", "-c", uri.split(":", 2)[2]])
            GLib.timeout_add(350, lambda: not self.refresh_view())
            return
        if uri.startswith("ui:prompt:"):
            rest = uri[len("ui:prompt:"):]
            if rest.startswith("cmd:"):
                cmd_body = rest[4:]
                if ":" in cmd_body:
                    cmd_tpl, placeholder = cmd_body.split(":", 1)
                else:
                    cmd_tpl, placeholder = cmd_body, t("common.input_args_placeholder")
                self.activate_prompt(f"cmd:{cmd_tpl}", placeholder, "")
            else:
                parts = rest.split(":", 2)
                act_id = parts[0] if len(parts) > 0 else "default"
                placeholder = parts[1] if len(parts) > 1 else t("common.input_placeholder")
                default_txt = parts[2] if len(parts) > 2 else ""
                self.activate_prompt(act_id, placeholder, default_txt)
            return

        self.panel.on_link(uri)

    def switch_panel(self, new_panel_id: str) -> None:
        self.bump_leave_guard(0.55)
        self.hide_prompt()
        self._hovered_card_idx = None
        self._focused_target_idx = -1
        self._focused_uri_memory = None
        self._stable_cols_key = None
        self._stable_cols_val = 0
        if self.header_listbox is not None:
            for child in self.header_listbox.get_children():
                self.header_listbox.remove(child)
        for child in self.listbox.get_children():
            self.listbox.remove(child)
        self.panel_id = str(new_panel_id or "network").strip()
        self.panel = load_panel(self.panel_id, shell=self, mode=self.mode)
        self._setup_timer()
        self.refresh_view()

    def _cycle_subtab(self, delta: int) -> bool:
        """Cambia Subtab nel Control Center o Tab interna nei popup espansi."""
        self.bump_leave_guard(0.45)
        self._focused_target_idx = -1
        self._focused_uri_memory = None

        if self.mode == "settings" and hasattr(self.panel, "get_subtab"):
            from panels import control_center as cc_mod
            sec = getattr(self.panel, "section", "system")
            tabs = []
            if hasattr(cc_mod, "get_section_blueprints"):
                tabs = cc_mod.get_section_blueprints().get(sec, {}).get("tabs", [])
            elif hasattr(cc_mod, "SECTION_BLUEPRINTS"):
                tabs = cc_mod.SECTION_BLUEPRINTS.get(sec, {}).get("tabs", [])
            elif hasattr(cc_mod, "SECTION_TABS"):
                tabs = cc_mod.SECTION_TABS.get(sec, [])
            if len(tabs) <= 1:
                return False
            valid_ids = [item[0] if isinstance(item, tuple) else item for item in tabs]
            cur = self.panel.get_subtab()
            idx = valid_ids.index(cur) if cur in valid_ids else 0
            self.panel.set_subtab(valid_ids[(idx + delta) % len(valid_ids)])
            self.refresh_view()
            return True

        if self.panel.is_expanded and self.panel_id == "network":
            net_tabs = ["ifaces", "wifi", "vpn", "watchdog"]
            cur = self.panel.active_tab
            idx = net_tabs.index(cur) if cur in net_tabs else 0
            self.panel.active_tab = net_tabs[(idx + delta) % len(net_tabs)]
            self.refresh_view()
            return True

        return False

    def _scroll_nav_to_active(self, active_idx: int, total_items: int) -> bool:
        if not hasattr(self, "nav_scrolled") or total_items <= 0:
            return False
        vadj = self.nav_scrolled.get_vadjustment()
        if not vadj:
            return False
        upper = vadj.get_upper()
        page = vadj.get_page_size()
        if upper <= page:
            return False
        cell_h = self._nav_cell_height_px
        item_top = active_idx * cell_h
        item_bot = item_top + cell_h
        cur = vadj.get_value()
        if item_top < cur:
            vadj.set_value(max(0.0, item_top - 8.0))
        elif item_bot > cur + page:
            vadj.set_value(min(upper - page, item_bot - page + 8.0))
        return False

    def _populate_settings_nav(self, target_h_px: int = None) -> None:
        p = get_semantic_palette()
        c_sec = p["schema"]
        c_acc = p["intent"]
        c_brd = p["structure"]

        self.nav_canvas.set_cols(SIDEBAR_ICON_COLS)
        nav_w_px = int(round(self.nav_canvas.total_px))
        s_left, s_right, _s_top, _s_bot = self._get_box_ink_insets()

        if target_h_px is not None:
            avail_h_px = max(240, int(target_h_px))
        else:
            _ml, _mr, m_top, m_bot, _gap = self._get_inner_box_margins()
            bw = int(self.geom.get("window_border_width", 1))
            prompt_h = 0
            if hasattr(self, "prompt_bar") and self.prompt_bar.get_visible():
                _pm, p_nat = self.prompt_bar.get_preferred_height()
                prompt_h = max(p_nat, int(self.content_canvas.char_h * 3 + 12)) + 6
            avail_h_px = max(360, self._get_cc_target_height_px() - m_top - m_bot - (2 * bw) - prompt_h)

        active_sec = getattr(self.panel, "section", self.panel_id)
        is_rounded = (THEME.get("ascii", {}).get("box_style", "rounded") in ("rounded", "dashed"))
        nav_state = (active_sec, avail_h_px, c_acc, c_brd, c_sec, is_rounded, round(get_box_opacity(), 3), get_surface_bg_rgb())
        if getattr(self, "_last_nav_state", None) == nav_state and self.nav_listbox.get_children():
            self._last_nav_height_px = avail_h_px
            return
        self._last_nav_state = nav_state

        for child in self.nav_listbox.get_children():
            self.nav_listbox.remove(child)

        self._last_nav_height_px = avail_h_px
        nav_items = _get_cc_sections()
        n_items = len(nav_items)

        grid_h_px = max(avail_h_px, n_items * 38)
        self._nav_cell_height_px = float(grid_h_px) / float(max(1, n_items))

        _ml, _mr, m_top, m_bot, _gap = self._get_inner_box_margins()
        inner_pad, _ = get_layout_margins()
        bw = int(self.geom.get("window_border_width", 1))
        x0 = float(s_left) + 0.5
        x1 = float(nav_w_px - 1 - s_right) + 0.5
        y0 = float(max(0, bw + inner_pad - m_top)) + 0.5
        y1 = float(grid_h_px - 1 - max(0, bw + inner_pad - m_bot)) + 0.5
        br_r, br_g, br_b, _ = _parse_rgba_components(c_brd)

        bg_da = Gtk.DrawingArea()
        bg_da.set_size_request(nav_w_px, grid_h_px)
        bg_da.set_can_focus(False)

        def _on_draw_nav(_da, cr):
            box_op = get_box_opacity()
            sr, sg, sb = get_surface_bg_rgb()
            cr.set_line_width(1.0)
            rad = min(4.5, (x1 - x0) / 4.0, (y1 - y0) / 4.0) if is_rounded else 0.0
            if rad > 0.5:
                cr.new_sub_path()
                cr.arc(x1 - rad, y0 + rad, rad, -math.pi / 2.0, 0.0)
                cr.arc(x1 - rad, y1 - rad, rad, 0.0, math.pi / 2.0)
                cr.arc(x0 + rad, y1 - rad, rad, math.pi / 2.0, math.pi)
                cr.arc(x0 + rad, y0 + rad, rad, math.pi, 3.0 * math.pi / 2.0)
                cr.close_path()
            else:
                cr.rectangle(x0, y0, x1 - x0, y1 - y0)
            if box_op > 0.001:
                cr.set_source_rgba(sr / 255.0, sg / 255.0, sb / 255.0, box_op)
                cr.fill_preserve()
            cr.set_source_rgba(br_r / 255.0, br_g / 255.0, br_b / 255.0, 1.0)
            cr.stroke()

            for div_i in range(1, n_items):
                y_div = math.floor(y0 + div_i * (y1 - y0) / float(n_items)) + 0.5
                cr.move_to(x0, y_div)
                cr.line_to(x1, y_div)
                cr.stroke()
            return False

        bg_da.connect("draw", _on_draw_nav)

        overlay = Gtk.Overlay()
        overlay.add(bg_da)

        fixed = Gtk.Fixed()
        fixed.set_size_request(nav_w_px, grid_h_px)

        active_idx = 0
        span_h = y1 - y0
        ic_pt = 18500 if self._nav_cell_height_px >= 44.0 else max(12500, int(round(self._nav_cell_height_px * 400)))
        for idx, (sec_id, ic) in enumerate(nav_items):
            cell_top = y0 + idx * span_h / float(n_items)
            cell_bot = y0 + (idx + 1) * span_h / float(n_items)
            cell_h_px = max(16, int(round(cell_bot - cell_top)))

            is_cur = (sec_id == active_sec)
            if is_cur:
                active_idx = idx
            col = c_acc if is_cur else c_sec

            eb = Gtk.EventBox()
            eb.set_visible_window(False)
            eb.set_size_request(nav_w_px, cell_h_px)
            eb.add_events(
                Gdk.EventMask.BUTTON_PRESS_MASK
                | Gdk.EventMask.BUTTON_RELEASE_MASK
                | Gdk.EventMask.ENTER_NOTIFY_MASK
                | Gdk.EventMask.LEAVE_NOTIFY_MASK
            )

            ic_lbl = Gtk.Label()
            ic_lbl.set_halign(Gtk.Align.CENTER)
            ic_lbl.set_valign(Gtk.Align.CENTER)
            ic_lbl.set_markup(f"<span color='{col}' size='{ic_pt}'>{ic}</span>")
            eb.add(ic_lbl)

            def _on_cell_press(_w, ev, target_sec=sec_id):
                if ev.button == 1:
                    self._on_card_pointer_activity("click")
                    self._focused_target_idx = -1
                    self._focused_uri_memory = None
                    GLib.idle_add(lambda: self.dispatch_uri(f"cc:section:{target_sec}") or False)
                    return True
                return False

            def _on_cell_enter(_w, _ev, lbl=ic_lbl, glyph=ic, active=is_cur, pt_sz=ic_pt, target_sec=sec_id, box_w=eb):
                self._on_card_pointer_activity("enter")
                if not active:
                    lbl.set_markup(f"<span color='{p['focus']}' size='{pt_sz}'>{glyph}</span>")
                rect = None
                if self.root_overlay is not None:
                    res = box_w.translate_coordinates(self.root_overlay, 0, 0)
                    alloc = box_w.get_allocation()
                    if res is not None:
                        tx, ty = (res[1], res[2]) if len(res) >= 3 else (res[0], res[1])
                        rect = (int(tx), int(ty), max(16, int(alloc.width)), max(16, int(alloc.height)))
                self._schedule_hover_help(f"cc:section:{target_sec}", rect, prefer_right=True)
                return False

            def _on_cell_leave(_w, _ev, lbl=ic_lbl, glyph=ic, base_col=col, pt_sz=ic_pt):
                lbl.set_markup(f"<span color='{base_col}' size='{pt_sz}'>{glyph}</span>")
                self._schedule_hide_hover_help()
                return False

            eb.connect("button-press-event", _on_cell_press)
            eb.connect("enter-notify-event", _on_cell_enter)
            eb.connect("leave-notify-event", _on_cell_leave)
            fixed.put(eb, 0, int(round(cell_top)))

        overlay.add_overlay(fixed)

        row = Gtk.ListBoxRow()
        row.set_selectable(False)
        row.set_can_focus(False)
        row.add(overlay)

        self.nav_listbox.add(row)
        self.nav_listbox.show_all()
        GLib.idle_add(self._scroll_nav_to_active, active_idx, n_items)

    # =====================================================================
    # MOTORE DI NAVIGAZIONE TASTIERA 2D (PUNTO 0)
    # =====================================================================

    def _rebuild_focus_targets(self) -> None:
        targets = []
        v_line_offset = 0
        for c_idx, child in enumerate(self._all_card_widgets()):
            if not isinstance(child, AsciiCard):
                continue
            links = child.get_focusable_links()
            for lk in links:
                targets.append({
                    "card": child,
                    "card_idx": c_idx,
                    "anchor_idx": lk["anchor_idx"],
                    "uri": lk["uri"],
                    "v_line": v_line_offset + lk["line"],
                    "col": lk["col"],
                    "is_slider": lk.get("is_slider", False),
                    "dec_uri": lk.get("dec_uri", ""),
                    "inc_uri": lk.get("inc_uri", ""),
                })
            n_card_lines = max(1, len((child._last_markup or "").splitlines()))
            v_line_offset += n_card_lines + 1

        self._focus_targets = targets
        if not targets:
            self._focused_target_idx = -1
            return

        if self._focused_target_idx >= 0 or self._focused_uri_memory:
            if self._focused_uri_memory:
                match_i = next((i for i, t in enumerate(targets) if t["uri"] == self._focused_uri_memory), -1)
                if match_i >= 0:
                    self._apply_focus_target(match_i, scroll=False)
                    return
                if 0 <= self._focused_target_idx < len(targets):
                    mem_prefix = self._focused_uri_memory.rsplit(":", 1)[0]
                    cur_prefix = targets[self._focused_target_idx]["uri"].rsplit(":", 1)[0]
                    if mem_prefix == cur_prefix:
                        self._apply_focus_target(self._focused_target_idx, scroll=False)
                        return
            if 0 <= self._focused_target_idx < len(targets):
                self._apply_focus_target(self._focused_target_idx, scroll=False)
            else:
                self._focused_target_idx = -1

    def focus_link_by_index(self, idx: int) -> None:
        if not self._focus_targets:
            self._rebuild_focus_targets()
        if self._focus_targets:
            clamped = max(0, min(len(self._focus_targets) - 1, int(idx)))
            self._apply_focus_target(clamped, scroll=True)

    def _apply_focus_target(self, target_idx: int, scroll: bool = True) -> None:
        if not self._focus_targets:
            self._focused_target_idx = -1
            return
        clamped = target_idx % len(self._focus_targets)
        self._focused_target_idx = clamped
        target = self._focus_targets[clamped]
        self._focused_uri_memory = target["uri"]

        for child in self._all_card_widgets():
            if isinstance(child, AsciiCard):
                if child is target["card"]:
                    child.set_focused_anchor(target["anchor_idx"])
                else:
                    child.set_focused_anchor(None)

        if scroll:
            self._scroll_card_into_view(target["card"])

    def _scroll_card_into_view(self, card_widget: Gtk.Widget) -> None:
        if card_widget.get_parent() is not self.listbox:
            return
        vadj = self.scrolled_window.get_vadjustment()
        if not vadj:
            return
        alloc = card_widget.get_allocation()
        if alloc.height <= 1:
            return
        cur_v = vadj.get_value()
        page_h = vadj.get_page_size()
        if page_h <= 10:
            return
        top_y = float(alloc.y)
        bot_y = float(alloc.y + alloc.height)
        margin = 24.0
        if top_y < cur_v + margin:
            vadj.set_value(max(vadj.get_lower(), top_y - margin))
        elif bot_y > (cur_v + page_h - margin):
            vadj.set_value(min(vadj.get_upper() - page_h, bot_y - page_h + margin))

    def _move_focus_1d(self, delta: int) -> bool:
        if not self._focus_targets:
            self._rebuild_focus_targets()
        if not self._focus_targets:
            return False
        if self._focused_target_idx < 0:
            next_i = 0 if delta >= 0 else (len(self._focus_targets) - 1)
        else:
            next_i = (self._focused_target_idx + delta) % len(self._focus_targets)
        self._apply_focus_target(next_i, scroll=True)
        return True

    def _move_focus_2d_vertical(self, direction: int) -> bool:
        if not self._focus_targets:
            self._rebuild_focus_targets()
        if not self._focus_targets:
            return False
        if self._focused_target_idx < 0:
            self._apply_focus_target(0 if direction >= 0 else len(self._focus_targets) - 1, scroll=True)
            return True

        cur = self._focus_targets[self._focused_target_idx]
        cur_line = cur["v_line"]
        cur_col = cur["col"]

        if direction > 0:
            candidates = [(i, t) for i, t in enumerate(self._focus_targets) if t["v_line"] > cur_line]
            if not candidates:
                candidates = [(i, t) for i, t in enumerate(self._focus_targets)]
            target_line = min(t["v_line"] for _, t in candidates)
        else:
            candidates = [(i, t) for i, t in enumerate(self._focus_targets) if t["v_line"] < cur_line]
            if not candidates:
                candidates = [(i, t) for i, t in enumerate(self._focus_targets)]
            target_line = max(t["v_line"] for _, t in candidates)

        same_line = [(i, t) for i, t in candidates if t["v_line"] == target_line]
        best_i, _ = min(same_line, key=lambda item: abs(item[1]["col"] - cur_col))
        self._apply_focus_target(best_i, scroll=True)
        return True

    def _activate_focused_target(self) -> bool:
        if not self._focus_targets or not (0 <= self._focused_target_idx < len(self._focus_targets)):
            return False
        target = self._focus_targets[self._focused_target_idx]
        if target.get("is_slider"):
            return True
        uri = target["uri"]
        if uri:
            self.dispatch_uri(uri)
            return True
        return False

    def _sync_listbox_specs(
        self,
        target_lb: Gtk.ListBox,
        target_specs: list,
        can_select_boxes: bool,
        spacing: int,
        trailing_spacing: int = 0,
    ) -> tuple[list, bool]:
        existing_rows = target_lb.get_children()

        def _embedded_reusable(w, sp) -> bool:
            w_emb = getattr(w, "embedded_widget", None)
            sp_emb = getattr(sp[2], "embedded_widget", None) if sp[2] is not None else None
            if w_emb is None and sp_emb is None:
                return True
            if w_emb is None or sp_emb is None or type(w_emb) is not type(sp_emb):
                return False
            if not hasattr(w_emb, "sync_from"):
                return False
            sp_has_footer = sp[2].footer_box_model is not None
            return (w.footer_lbl is not None) == sp_has_footer

        def _row_reusable(w, sp) -> bool:
            return (
                sp[0] == "ascii"
                and isinstance(w, AsciiCard)
                and getattr(w.box_model, "is_divider", False) == sp[3]
                and getattr(w, "pixbuf", None) is None
                and (sp[2] is None or getattr(sp[2], "pixbuf", None) is None)
                and _embedded_reusable(w, sp)
            )

        def _update_reused_card(card_w, sp, margin_b: int) -> bool:
            _kind, bm, card_obj, is_div = sp
            card_w.set_margin_bottom(margin_b)
            card_w.canvas = self.content_canvas
            has_card_action = bool((card_obj is not None and card_obj.on_row_click) or getattr(bm, "scroll_action", None))
            card_w.hover_enable = bool(can_select_boxes and not is_div and has_card_action)
            card_w.on_hover_change = self._on_card_hover_change
            card_w.on_anchor_hover_change = self._on_card_anchor_hover
            card_w.on_pointer_activity = self._on_card_pointer_activity
            card_w.on_scroll_intercept = self._intercept_card_scroll
            card_w.on_scroll_can_consume = self._can_card_consume_scroll
            if card_obj is not None:
                card_w.on_row_click = card_obj.on_row_click
                card_w.on_link = card_obj.on_link or self.dispatch_uri
                if card_w.embedded_widget is not None and card_obj.embedded_widget is not None:
                    card_w.embedded_widget.sync_from(card_obj.embedded_widget)
                if card_obj.footer_box_model is not None and card_w.footer_lbl is not None:
                    card_w.footer_box_model = card_obj.footer_box_model
            else:
                card_w.on_row_click = None
                card_w.on_link = self.dispatch_uri
            bm.selected = bool(card_w.is_hovered and card_w.hover_enable)
            card_w.box_model = bm
            card_w.refresh_markup()
            return card_w.hover_enable

        def _build_spec_widget(sp):
            _kind, bm, card_obj, is_div = sp
            if _kind == "ascii":
                has_card_action = bool((card_obj is not None and card_obj.on_row_click) or getattr(bm, "scroll_action", None))
                c_hover = bool(can_select_boxes and not is_div and has_card_action)
                if card_obj is not None:
                    c = card_obj
                    c.canvas = self.content_canvas
                    c.on_link = c.on_link or self.dispatch_uri
                    c.hover_enable = c_hover
                    c.on_hover_change = self._on_card_hover_change
                    c.on_anchor_hover_change = self._on_card_anchor_hover
                    c.on_pointer_activity = self._on_card_pointer_activity
                    c.on_scroll_intercept = self._intercept_card_scroll
                    c.on_scroll_can_consume = self._can_card_consume_scroll
                    c.box_model.selected = False
                    c.refresh_markup()
                else:
                    bm.selected = False
                    c = AsciiCard(
                        bm,
                        self.content_canvas,
                        on_link=self.dispatch_uri,
                        hover_enable=c_hover,
                        on_hover_change=self._on_card_hover_change,
                        on_pointer_activity=self._on_card_pointer_activity,
                    )
                    c.on_anchor_hover_change = self._on_card_anchor_hover
                    c.on_scroll_intercept = self._intercept_card_scroll
                    c.on_scroll_can_consume = self._can_card_consume_scroll
                return c, c.hover_enable
            return card_obj, False

        if len(existing_rows) == len(target_specs) and len(target_specs) > 0:
            reusable_flags = [_row_reusable(w, sp) for w, sp in zip(existing_rows, target_specs)]
            if all(reusable_flags):
                ascii_cards = []
                for idx, (card_w, sp) in enumerate(zip(existing_rows, target_specs)):
                    margin_b = spacing if idx < len(target_specs) - 1 else trailing_spacing
                    if _update_reused_card(card_w, sp, margin_b):
                        ascii_cards.append(card_w)
                return ascii_cards, True
            if any(reusable_flags):
                ascii_cards = []
                for idx, (old_w, sp, ok_reuse) in enumerate(zip(existing_rows, target_specs, reusable_flags)):
                    margin_b = spacing if idx < len(target_specs) - 1 else trailing_spacing
                    if ok_reuse:
                        if _update_reused_card(old_w, sp, margin_b):
                            ascii_cards.append(old_w)
                    else:
                        target_lb.remove(old_w)
                        new_w, track_hover = _build_spec_widget(sp)
                        new_w.set_margin_bottom(margin_b)
                        target_lb.insert(new_w, idx)
                        new_w.show_all()
                        if track_hover:
                            ascii_cards.append(new_w)
                return ascii_cards, False

        for child in existing_rows:
            target_lb.remove(child)

        ascii_cards = []
        row_widgets = []
        for sp in target_specs:
            rw, track_hover = _build_spec_widget(sp)
            row_widgets.append(rw)
            if track_hover:
                ascii_cards.append(rw)

        for idx, rw in enumerate(row_widgets):
            rw.set_margin_bottom(spacing if idx < len(row_widgets) - 1 else trailing_spacing)
            target_lb.add(rw)

        target_lb.show_all()
        return ascii_cards, False

    @staticmethod
    def _extract_feedback_toasts(raw_cards: list) -> tuple[list, list]:
        filtered = []
        toasts = []
        for item in (raw_cards or []):
            bm = getattr(item, "box_model", None) or item
            meta = getattr(bm, "_toast_meta", None)
            if isinstance(meta, dict) and meta.get("msg"):
                toasts.append(meta)
            else:
                filtered.append(item)
        return filtered, toasts

    def _sync_feedback_toasts(self, toast_metas: list) -> None:
        vk = self._current_view_key()
        if self._last_view_key is not None and vk != self._last_view_key and self._toast_level == "err":
            self.dismiss_toast()
        self._last_view_key = vk
        cur_seq = int(self._action_seq.get(vk, 0))
        prev_present, prev_payload, prev_seq = self._last_fb_by_view.get(vk, (False, None, -1))
        if toast_metas:
            chosen = next((m for m in reversed(toast_metas) if not m.get("ok")), toast_metas[-1])
            payload = (chosen.get("title", ""), bool(chosen.get("ok")), str(chosen.get("msg", "")))
            if (not prev_present) or (payload != prev_payload) or (cur_seq != prev_seq):
                self.show_toast(
                    chosen.get("msg", ""),
                    level=("ok" if chosen.get("ok") else "err"),
                    title=chosen.get("title", ""),
                )
            self._last_fb_by_view[vk] = (True, payload, cur_seq)
        else:
            self._last_fb_by_view[vk] = (False, None, cur_seq)

    def refresh_view(self) -> bool:
        if self._help_visible_uri and self._current_view_key() != self._help_visible_view_key:
            self.dismiss_hover_help()
        nav_vadj = self.nav_scrolled.get_vadjustment() if self.mode == "settings" and hasattr(self, "nav_scrolled") else None
        nav_scroll_val = nav_vadj.get_value() if nav_vadj else 0.0

        vadj = self.scrolled_window.get_vadjustment()
        scroll_val = vadj.get_value() if vadj else 0.0

        toast_metas = []
        if self.mode == "settings":
            self._populate_settings_nav()
            cc_cols = self._compute_cc_content_cols()
            self.content_canvas.set_cols(cc_cols)
            raw_cards, toast_metas = self._extract_feedback_toasts(
                self.panel.build_cards(self.content_canvas, force_expanded=True)
            )
            for c_item in raw_cards:
                if isinstance(c_item, (Box, Divider)):
                    c_item._forced_cols = cc_cols
                    c_item.min_cols = cc_cols
                    c_item.max_cols = cc_cols
                    if isinstance(c_item, Box):
                        c_item.width_mode = "fixed"
                elif hasattr(c_item, "box_model") and isinstance(c_item.box_model, (Box, Divider)):
                    c_item.box_model._forced_cols = cc_cols
                    c_item.box_model.min_cols = cc_cols
                    c_item.box_model.max_cols = cc_cols
                    if isinstance(c_item.box_model, Box):
                        c_item.box_model.width_mode = "fixed"
            fill_items = [
                (c_item.box_model if hasattr(c_item, "box_model") else c_item)
                for c_item in raw_cards[1:]
                if getattr((c_item.box_model if hasattr(c_item, "box_model") else c_item), "fill_window", False)
            ]
            if fill_items and raw_cards:
                _ml, _mr, m_top, m_bot, _gap = self._get_inner_box_margins()
                bw = int(self.geom.get("window_border_width", 1))
                avail_list_h = max(240, self._get_cc_target_height_px() - m_top - m_bot - (2 * bw))
                spacing = int(self.geom.get("card_spacing", 4))
                used_px = self._measure_cc_box_px(raw_cards[0]) + (spacing if len(raw_cards) > 1 else 0)
                for idx_b, b_item in enumerate(raw_cards[1:]):
                    bm_b = b_item.box_model if hasattr(b_item, "box_model") else b_item
                    if getattr(bm_b, "fill_window", False):
                        continue
                    used_px += self._measure_cc_box_px(bm_b) + (spacing if idx_b < len(raw_cards[1:]) - 1 else 0)
                target_fill_px = max(240, avail_list_h - used_px)
                for bm_f in fill_items:
                    bm_f.min_height_px = target_fill_px
        else:
            is_exp = self.panel.is_expanded
            pad = int(self.geom.get("panel_padding", 10)) * 2
            bw = int(self.geom.get("window_border_width", 1))
            max_popup_w, _ = self._get_popup_bounds_px()
            max_popup_cols = max(44, int((max_popup_w - pad - (2 * bw)) // self.content_canvas.char_w))
            base_cols = min(self.panel.get_cols(), max_popup_cols)
            state_key = (self.panel_id, is_exp, max_popup_cols)
            if self._stable_cols_key != state_key:
                self._stable_cols_key = state_key
                self._stable_cols_val = base_cols

            self.content_canvas.set_cols(base_cols)
            raw_cards, toast_metas = self._extract_feedback_toasts(
                self.panel.build_cards(self.content_canvas, force_expanded=False)
            )
            shared_cols = sync_boxes_width(raw_cards, self.content_canvas, max(base_cols, self._stable_cols_val))
            self._stable_cols_val = min(max(self._stable_cols_val, shared_cols), max_popup_cols)
            self.content_canvas.set_cols(self._stable_cols_val)

        specs = []
        selectable_count = 0
        for item in raw_cards:
            if isinstance(item, (Box, Divider)):
                is_div = getattr(item, "is_divider", False)
                specs.append(("ascii", item, None, is_div))
                if not is_div:
                    selectable_count += 1
            elif isinstance(item, AsciiCard):
                bm = item.box_model
                is_div = getattr(bm, "is_divider", False)
                specs.append(("ascii", bm, item, is_div))
                if not is_div:
                    selectable_count += 1
            elif isinstance(item, (CustomWidgetCard, Gtk.ListBoxRow)):
                specs.append(("custom", None, item, False))

        panel_hover = bool(getattr(self.panel, "hover_highlight", True))
        can_select_boxes = panel_hover and (selectable_count > 1)
        spacing = int(self.geom.get("card_spacing", 4))

        if self.mode == "settings" and self.header_listbox is not None and specs:
            hdr_specs = specs[:1]
            body_specs = specs[1:]
            _, hdr_reused = self._sync_listbox_specs(
                self.header_listbox,
                hdr_specs,
                can_select_boxes=False,
                spacing=spacing,
                trailing_spacing=(spacing if body_specs else 0),
            )
            ascii_cards, body_reused = self._sync_listbox_specs(
                self.listbox,
                body_specs,
                can_select_boxes=can_select_boxes,
                spacing=spacing,
                trailing_spacing=0,
            )
        else:
            hdr_reused = True
            if self.header_listbox is not None:
                hdr_children = self.header_listbox.get_children()
                if hdr_children:
                    hdr_reused = False
                    for ch in hdr_children:
                        self.header_listbox.remove(ch)
            ascii_cards, body_reused = self._sync_listbox_specs(
                self.listbox,
                specs,
                can_select_boxes=can_select_boxes,
                spacing=spacing,
                trailing_spacing=0,
            )

        def _lb_line_sig(lb) -> tuple:
            return tuple(
                (
                    len((w._last_markup or "").splitlines()),
                    len((w.footer_lbl.get_text() or "").splitlines()) if getattr(w, "footer_lbl", None) is not None else 0,
                )
                if isinstance(w, AsciiCard) else (-1, id(w))
                for w in lb.get_children()
            )

        render_sig = (
            self.mode,
            int(self.content_canvas.total_cols),
            bool(self.prompt_bar.get_visible()),
            _lb_line_sig(self.header_listbox) if self.header_listbox is not None else (),
            _lb_line_sig(self.listbox),
        )
        same_layout = bool(hdr_reused and body_reused and self._last_render_sig == render_sig)
        self._last_render_sig = render_sig

        if can_select_boxes and self._hovered_card_idx is not None and 0 <= self._hovered_card_idx < len(ascii_cards):
            for idx, c in enumerate(ascii_cards):
                c.set_hovered_visual(idx == self._hovered_card_idx)

        self._rebuild_focus_targets()
        self._apply_dynamic_anchor()
        self._render_cc_global_search_overlay()
        self._sync_feedback_toasts(toast_metas)
        if same_layout:
            return True
        self._adjust_scroll_height()

        def _restore():
            self._adjust_scroll_height()
            if nav_vadj:
                nav_vadj.set_value(min(nav_scroll_val, max(0.0, nav_vadj.get_upper() - nav_vadj.get_page_size())))
            if vadj and self._focused_target_idx < 0:
                vadj.set_value(min(scroll_val, max(0.0, vadj.get_upper() - vadj.get_page_size())))
            if self.prompt_bar.get_visible():
                self.prompt_bar._render()
            return False

        GLib.idle_add(_restore)
        return True

    def _confirm_leave_close(self) -> bool:
        self._leave_close_source_id = None
        if self.mode == "settings" or self._is_centered_overlay() or self.panel.is_busy() or self.prompt_bar.get_visible() or get_active_inline_entry():
            return False
        if time.monotonic() < self._ignore_leave_until or self.is_pointer_busy():
            return False
        try:
            with open(LEAVE_STAMP_FILE, "w", encoding="utf-8") as f:
                json.dump({"ts": time.time(), "panel_id": self.panel_id}, f)
        except Exception:
            pass
        Gtk.main_quit()
        return False

    def _on_leave(self, _widget, event):
        if os.environ.get("NC_UI_OFFSCREEN") == "1":
            return False
        if event.detail == Gdk.NotifyType.INFERIOR:
            return False
        if event.mode != Gdk.CrossingMode.NORMAL:
            return False
        if not self._mouse_inside:
            return False
        if time.monotonic() < self._ignore_leave_until or self.is_pointer_busy():
            return False

        self._hovered_card_idx = None
        for child in self._all_card_widgets():
            if isinstance(child, AsciiCard):
                child.set_hovered_visual(False)

        if self.mode == "settings" or self._is_centered_overlay() or self.panel.is_busy() or self.prompt_bar.get_visible() or get_active_inline_entry():
            return False

        self._cancel_leave_close()
        self._leave_close_source_id = GLib.timeout_add(80, self._confirm_leave_close)
        return False

    def _on_key_release(self, _widget, event) -> bool:
        if not self.prompt_bar.get_visible() and hasattr(self.panel, "on_key_release"):
            return bool(self.panel.on_key_release(event))
        return False

    def _on_key_press(self, _widget, event) -> bool:
        state = event.state
        is_ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        if is_ctrl and event.keyval in (Gdk.KEY_f, Gdk.KEY_F) and not _get_active_tui_id():
            if self.mode == "settings":
                self.open_cc_global_search()
                return True
            STATE.set("control_center", "open_global_search", True)
            nc_ctl_bin = find_bin("nc-ctl") or os.path.join(CORE_ROOT, "bin", "nc-ctl")
            probe.spawn_bg([sys.executable, nc_ctl_bin, "settings"])
            Gtk.main_quit()
            return True

        if self.prompt_bar.get_visible():
            if event.keyval == Gdk.KEY_Escape:
                self.hide_prompt()
                return True
            if event.keyval in (Gdk.KEY_Tab, Gdk.KEY_KP_Tab, Gdk.KEY_ISO_Left_Tab):
                rev = bool(event.state & Gdk.ModifierType.SHIFT_MASK) or (event.keyval == Gdk.KEY_ISO_Left_Tab)
                if self.prompt_bar.handle_tab_complete(reverse=rev):
                    return True
            if not self.prompt_bar.entry.has_focus():
                self.prompt_bar.entry.grab_focus()
                self.prompt_bar.entry.set_position(-1)
            return False

        if get_active_inline_entry():
            if handle_inline_entry_key_press(event, shell=self):
                return True
            if self._inline_entry is not None and self._inline_entry.get_can_focus() and not self._inline_entry.has_focus():
                self._inline_entry.grab_focus()
                self._inline_entry.set_position(len(self._inline_entry.get_text()))
            return False

        if hasattr(self.panel, "on_key_press") and self.panel.on_key_press(event):
            return True

        if event.keyval == Gdk.KEY_F1:
            if self._help_visible_uri and self.mode == "settings" and not self._help_expanded:
                return self.show_hover_help(
                    self._help_visible_uri,
                    self._help_visible_rect,
                    expanded=True,
                    prefer_right=self._help_visible_prefer_right,
                )
            if self._help_pending_uri:
                self._on_help_show_timeout()
                return True

        if event.keyval == Gdk.KEY_Escape:
            if self._help_visible_uri:
                self.dismiss_hover_help()
                return True
            Gtk.main_quit()
            return True

        state = event.state
        is_shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
        is_alt = bool(state & Gdk.ModifierType.MOD1_MASK)

        # ── 1. SHIFT + FRECCE / PAGE_UP / PAGE_DOWN (Sezioni, Subtab & Bar Surfing) ──
        if is_shift and event.keyval in (Gdk.KEY_Left, Gdk.KEY_KP_Left):
            if self.mode == "settings":
                return self._cycle_subtab(-1)
            if self._is_centered_overlay():
                return True
            self._surf_waybar_panel(-1)
            return True

        if is_shift and event.keyval in (Gdk.KEY_Right, Gdk.KEY_KP_Right):
            if self.mode == "settings":
                return self._cycle_subtab(1)
            if self._is_centered_overlay():
                return True
            self._surf_waybar_panel(1)
            return True

        if (is_shift and event.keyval in (Gdk.KEY_Up, Gdk.KEY_KP_Up)) or event.keyval == Gdk.KEY_Page_Up:
            if self.mode == "settings":
                sec_ids = [s[0] for s in _get_cc_sections()]
                if sec_ids and hasattr(self.panel, "section"):
                    cur_sec = self.panel.section
                    cur_idx = sec_ids.index(cur_sec) if cur_sec in sec_ids else 0
                    new_idx = (cur_idx - 1) % len(sec_ids)
                    self._focused_target_idx = -1
                    self._focused_uri_memory = None
                    self.panel.set_section(sec_ids[new_idx])
                    self.refresh_view()
                    GLib.idle_add(self._scroll_nav_to_active, new_idx, len(sec_ids))
                    return True
            elif self.panel.supports_expand:
                self.dispatch_uri("ui:toggle_expand")
                return True

        if (is_shift and event.keyval in (Gdk.KEY_Down, Gdk.KEY_KP_Down)) or event.keyval == Gdk.KEY_Page_Down:
            if self.mode == "settings":
                sec_ids = [s[0] for s in _get_cc_sections()]
                if sec_ids and hasattr(self.panel, "section"):
                    cur_sec = self.panel.section
                    cur_idx = sec_ids.index(cur_sec) if cur_sec in sec_ids else 0
                    new_idx = (cur_idx + 1) % len(sec_ids)
                    self._focused_target_idx = -1
                    self._focused_uri_memory = None
                    self.panel.set_section(sec_ids[new_idx])
                    self.refresh_view()
                    GLib.idle_add(self._scroll_nav_to_active, new_idx, len(sec_ids))
                    return True
            elif self.panel.supports_expand:
                self.dispatch_uri("ui:toggle_expand")
                return True

        if is_alt and event.keyval in (Gdk.KEY_Left, Gdk.KEY_Right):
            return self._cycle_subtab(-1 if event.keyval == Gdk.KEY_Left else 1)

        # ── 2. NAVIGAZIONE PULSANTI 1D / 2D (Tab / Frecce) ──
        if event.keyval in (Gdk.KEY_Tab, Gdk.KEY_KP_Tab) and not is_shift:
            clear_pointer_move_flag()
            if self._focused_target_idx < 0:
                hov_i, _ = self._resolve_hovered_focus_target()
                if hov_i >= 0:
                    self._focused_target_idx = hov_i
            return self._move_focus_1d(1)

        if event.keyval == Gdk.KEY_ISO_Left_Tab or (event.keyval in (Gdk.KEY_Tab, Gdk.KEY_KP_Tab) and is_shift):
            clear_pointer_move_flag()
            if self._focused_target_idx < 0:
                hov_i, _ = self._resolve_hovered_focus_target()
                if hov_i >= 0:
                    self._focused_target_idx = hov_i
            return self._move_focus_1d(-1)

        if event.keyval in (Gdk.KEY_Right, Gdk.KEY_KP_Right, Gdk.KEY_Left, Gdk.KEY_KP_Left):
            is_right = event.keyval in (Gdk.KEY_Right, Gdk.KEY_KP_Right)
            direction = "up" if is_right else "down"
            if 0 <= self._focused_target_idx < len(self._focus_targets):
                f_target = self._focus_targets[self._focused_target_idx]
                if f_target.get("is_slider"):
                    if self._dispatch_slider_step(f_target, direction):
                        return True
            else:
                hov_i, hov_target = self._resolve_hovered_focus_target()
                if hov_target is not None:
                    if hov_target.get("is_slider"):
                        self._apply_focus_target(hov_i, scroll=False)
                        if self._dispatch_slider_step(hov_target, direction):
                            return True
                    self._focused_target_idx = hov_i
            if self._move_focus_1d(1 if is_right else -1):
                clear_pointer_move_flag()
                return True

        if event.keyval in (Gdk.KEY_Down, Gdk.KEY_KP_Down):
            if self._focused_target_idx < 0:
                hov_i, _ = self._resolve_hovered_focus_target()
                if hov_i >= 0:
                    self._focused_target_idx = hov_i
            if self._move_focus_2d_vertical(1):
                clear_pointer_move_flag()
                return True

        if event.keyval in (Gdk.KEY_Up, Gdk.KEY_KP_Up):
            if self._focused_target_idx < 0:
                hov_i, _ = self._resolve_hovered_focus_target()
                if hov_i >= 0:
                    self._focused_target_idx = hov_i
            if self._move_focus_2d_vertical(-1):
                clear_pointer_move_flag()
                return True

        # ── 3. ATTIVAZIONE PULSANTE A FUOCO (Invio / Spazio) ──
        if event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_space):
            if self._focused_target_idx < 0:
                hov_i, _ = self._resolve_hovered_focus_target()
                if hov_i >= 0:
                    self._apply_focus_target(hov_i, scroll=False)
            if self._activate_focused_target():
                return True

        # ── 4. REGOLAZIONE RAPIDA SLIDER / VIEWPORT CON SCROLL_ACTION (+ / -) ──
        if event.keyval in (Gdk.KEY_plus, Gdk.KEY_equal, Gdk.KEY_KP_Add, Gdk.KEY_minus, Gdk.KEY_KP_Subtract):
            direction = "up" if event.keyval in (Gdk.KEY_plus, Gdk.KEY_equal, Gdk.KEY_KP_Add) else "down"
            if 0 <= self._focused_target_idx < len(self._focus_targets):
                f_target = self._focus_targets[self._focused_target_idx]
                if f_target.get("is_slider"):
                    if self._dispatch_slider_step(f_target, direction):
                        return True
            else:
                hov_i, hov_target = self._resolve_hovered_focus_target()
                if hov_target is not None and hov_target.get("is_slider"):
                    self._apply_focus_target(hov_i, scroll=False)
                    if self._dispatch_slider_step(hov_target, direction):
                        return True
            target_card = self._resolve_active_scroll_card()
            if target_card is not None and target_card.on_link:
                if self._check_step_rate_limit():
                    target_card.on_link(f"{target_card.box_model.scroll_action}:{direction}")
                return True

        return self.panel.on_key_press(event)
