#!/usr/bin/env python3
import json
import os
import re
import sys
import time
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

from core.config import CONFIG, THEME, get_waybar_look, get_effective_window_bg
from core.i18n import t
from core import probe, log
from ui.canvas import Canvas
from ui.kit import PALETTE, Box, Divider, sync_boxes_width
from ui.widgets import AsciiCard, CustomWidgetCard, NcPromptBar
from panels.base import load_panel, CONTROL_CENTER_SECTIONS

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
    "notifications": "custom/notifications",
    "session": "custom/session",
    "power": "custom/power",
    "ups": "custom/ups",
    "storage": "custom/storage",
}

_LINK_TAG_RE = re.compile(r"</?a\b[^>]*>", re.IGNORECASE)
_AGENT_CMD_RE = re.compile(r"nc-bar-agent\s+([a-zA-Z0-9_-]+)")
_CTL_CMD_RE = re.compile(r"nc-ctl\s+(?:popup|settings|action)\s+([a-zA-Z0-9_-]+)")

SIDEBAR_ICON_COLS = 8


class ShellWindow(Gtk.Window):
    def __init__(self, panel_id: str, mode: str = "popup", anchor_x: int = None):
        self.mode = mode
        self.panel_id = str(panel_id or ("system" if mode == "settings" else "network")).strip()
        super().__init__(title=f"NC-UI: {self.panel_id}")
        self.panel = load_panel(self.panel_id, shell=self, mode=self.mode)

        self._refresh_source_id = None
        self._hovered_card_idx = None
        self._wb_layout_cache = None
        self._niri_geom_cache = None
        self._last_allocated_w = 0
        self._ignore_leave_until = 0.0
        self._mouse_inside = False
        self._leave_close_source_id = None
        self._stable_cols_key = None
        self._stable_cols_val = 0
        self._css_provider = None
        self._nav_cell_height_px = 48.0
        self.click_x = anchor_x if (anchor_x is not None and anchor_x > 0) else -1

        # Stato del motore di navigazione da tastiera 2D
        self._focus_targets = []
        self._focused_target_idx = -1
        self._focused_uri_memory = None

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
            | Gdk.EventMask.SCROLL_MASK
            | Gdk.EventMask.SMOOTH_SCROLL_MASK
        )
        self.connect("enter-notify-event", self._on_window_enter)
        self.connect("motion-notify-event", self._on_window_motion)
        self.connect("leave-notify-event", self._on_leave)
        self.connect("key-press-event", self._on_key_press)
        self.connect("realize", self._on_realize)
        self.connect("size-allocate", self._on_size_allocate)

        self.refresh_view()
        self._setup_timer()

    def _get_cc_target_width_px(self) -> int:
        return int(CONFIG.get("control_center", {}).get("width_px", 960))

    def _compute_cc_content_cols(self) -> int:
        target_w = self._get_cc_target_width_px()
        pad = int(self.geom.get("panel_padding", 10))
        avail_px = max(320.0, float(target_w - (pad * 2) - 8) - self.nav_canvas.total_px)
        return max(44, int(avail_px // self.nav_canvas.char_w))

    def _get_cc_target_height_px(self) -> int:
        _mon, _mon_w, mon_h = self._resolve_target_monitor()
        wb_top = int(self.wb_look.get("top", 34))
        gap_top = int(self.geom.get("popup_gap_top", 4))
        scr_margin = int(self.geom.get("screen_margin", 12))
        return max(540, mon_h - wb_top - gap_top - scr_margin)

    def _measure_nav_markup_size_px(self, markup_str: str) -> tuple[int, int]:
        s = Canvas.sanitize_markup(markup_str)
        pango_only = _LINK_TAG_RE.sub("", s)
        self.nav_canvas.layout.set_width(-1)
        try:
            self.nav_canvas.layout.set_markup(pango_only, -1)
            w_px, h_px = self.nav_canvas.layout.get_pixel_size()
            return int(w_px), int(h_px)
        except Exception:
            return int(self.nav_canvas.total_px), int(len(markup_str.splitlines()) * self.nav_canvas.char_h)

    def bump_leave_guard(self, duration_sec: float = 0.45) -> None:
        self._ignore_leave_until = max(self._ignore_leave_until, time.monotonic() + duration_sec)
        self._cancel_leave_close()

    def _cancel_leave_close(self) -> None:
        if self._leave_close_source_id is not None:
            GLib.source_remove(self._leave_close_source_id)
            self._leave_close_source_id = None

    def _on_card_pointer_activity(self, kind: str) -> None:
        self._mouse_inside = True
        self._cancel_leave_close()
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
        return False

    def is_pointer_busy(self) -> bool:
        for c in self.listbox.get_children():
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
        wb_css = os.path.expanduser("~/.config/waybar/style.css")
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
            self._wb_pango_layout.set_markup(pango_only, -1)
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
        self._niri_geom_cache = ctx
        return ctx

    def _resolve_target_monitor(self) -> tuple:
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

        wb_css = os.path.expanduser("~/.config/waybar/style.css")
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

        wb_conf = os.path.expanduser("~/.config/waybar/config.jsonc")
        if os.path.exists(wb_conf):
            try:
                with open(wb_conf, "r", encoding="utf-8") as f:
                    raw = f.read()
                cleaned = re.sub(r'("(?:\\.|[^"\\])*")|//[^\n]*', lambda m: m.group(1) if m.group(1) else "", raw)
                cleaned = re.sub(r",(\s*[}\]])", r"\1", cleaned)
                loaded = json.loads(cleaned)

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
                            if "nc-ctl settings" in cmd_str and not detected_pid:
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
            for pid in ("settings", "calendar", "display", "clipboard", "capture", "audio", "network", "cpu", "ram", "notifications", "session"):
                ordered.append((pid, "settings" if pid == "settings" else "popup"))
        return ordered

    def _surf_waybar_panel(self, delta: int) -> None:
        ordered = self._get_ordered_waybar_panels()
        if not ordered:
            return
        cur_idx = 0
        for idx, (pid, pmode) in enumerate(ordered):
            if pid == self.panel_id and pmode == self.mode:
                cur_idx = idx
                break
        next_pid, next_mode = ordered[(cur_idx + delta) % len(ordered)]
        if next_mode == "popup" and self.mode == "popup":
            # Cambio rapido di pannello dentro la stessa finestra popup con ricalcolo dell'ancoraggio
            self.click_x = -1
            self.switch_panel(next_pid)
            return
        nc_ctl_bin = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin", "nc-ctl")
        if next_mode == "settings":
            probe.spawn_bg([sys.executable, nc_ctl_bin, "settings", "system"])
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
            ws_cnt = max(1, int(self._query_niri_context().get("ws_count", 3)))
            return float(ws_cnt * 38 + 20)

        if mod_name == "niri/window":
            title = self._query_niri_context().get("win_title", "")
            if not title:
                return 0.0
            max_len = int(mod_cfg.get("max-length", 60))
            shown = title[:max_len]
            return self._measure_waybar_markup_px(Canvas.escape(shown)) + css_box_extra

        return 46.0

    def _init_layer_shell(self) -> None:
        if not HAS_LAYER_SHELL or os.environ.get("NC_UI_OFFSCREEN") == "1":
            return
        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.TOP)
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.ON_DEMAND)

        target_mon, _, _ = self._resolve_target_monitor()
        if target_mon is not None:
            GtkLayerShell.set_monitor(self, target_mon)

        gap_top = int(self.geom.get("popup_gap_top", 4))
        scr_margin = int(self.geom.get("screen_margin", 12))

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

        wb = self._read_waybar_layout()
        is_bottom = (wb.get("position") == "bottom")
        v_edge = GtkLayerShell.Edge.BOTTOM if is_bottom else GtkLayerShell.Edge.TOP
        GtkLayerShell.set_anchor(self, v_edge, True)
        GtkLayerShell.set_margin(self, v_edge, gap_top)
        self._apply_dynamic_anchor()

    def _apply_dynamic_anchor(self, actual_win_w: int = None) -> None:
        if not HAS_LAYER_SHELL or self.mode == "settings" or os.environ.get("NC_UI_OFFSCREEN") == "1":
            return

        target_mon, mon_w, _mon_h = self._resolve_target_monitor()
        if target_mon is not None:
            GtkLayerShell.set_monitor(self, target_mon)

        win_w = actual_win_w if (actual_win_w and actual_win_w > 40) else self._total_window_px()
        edge_margin = int(self.geom.get("screen_margin", 12))
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

    def _on_size_allocate(self, _widget, allocation) -> None:
        self._cancel_leave_close()
        if allocation.width > 40 and abs(allocation.width - self._last_allocated_w) > 2:
            self._last_allocated_w = allocation.width
            self._apply_dynamic_anchor(actual_win_w=allocation.width)

    def _apply_css(self) -> None:
        bg = get_effective_window_bg(self.wb_look.get("bg"))
        rad = self.wb_look["rad"] if self.wb_look["rad"] is not None else self.geom["window_border_radius"]
        bw = self.geom["window_border_width"]
        bc = self.colors["window_border"]
        c_pri = self.colors["primary"]
        c_acc = self.colors["accent"]

        css = f"""
        window {{
            background-color: transparent;
            font-family: '{self.typo["font_family"]}', 'Font Awesome 6 Free', 'Font Awesome 6 Brands', 'Symbols Nerd Font', monospace;
            font-size: {self.typo["font_size"]}pt;
            font-weight: {self.typo["font_weight"]};
        }}
        #main-container {{
            background-color: {bg};
            border-radius: {rad}px;
            border: {bw}px solid {bc};
        }}
        #niri-map-out {{
            background: rgba(166, 227, 161, 0.15);
            background-color: rgba(166, 227, 161, 0.15);
            border: 2px solid {c_acc};
            border-radius: 8px;
            color: {c_pri};
        }}
        #niri-map-out:hover {{
            background: rgba(166, 227, 161, 0.30);
            background-color: rgba(166, 227, 161, 0.30);
        }}
        #niri-map-out label {{
            color: {c_pri};
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
        scrollbar {{ min-width: 0px; min-height: 0px; }}
        scrollbar slider {{ background-color: rgba(255, 255, 255, 0.2); border-radius: 4px; min-width: 4px; }}
        scrollbar slider:hover {{ background-color: {c_acc}; }}
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
        )
        self.main_box.connect("enter-notify-event", self._on_window_enter)
        self.main_box.connect("motion-notify-event", self._on_window_motion)
        self.add(self.main_box)

        pad = int(self.geom.get("panel_padding", 10))
        outer_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        outer_vbox.set_margin_top(pad)
        outer_vbox.set_margin_bottom(pad)
        outer_vbox.set_margin_start(pad)
        outer_vbox.set_margin_end(pad)
        self.main_box.add(outer_vbox)

        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        outer_vbox.pack_start(hbox, True, True, 0)

        if self.mode == "settings":
            self.nav_scrolled = Gtk.ScrolledWindow()
            self.nav_scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            self.nav_scrolled.set_overlay_scrolling(True)
            self.nav_scrolled.set_propagate_natural_width(True)
            self.nav_scrolled.set_propagate_natural_height(False)
            self.nav_scrolled.set_can_focus(False)

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
        max_h = self._get_cc_target_height_px() if self.mode == "settings" else int(self.geom["popup_max_height"])
        self.scrolled_window.set_max_content_height(max_h)
        self.scrolled_window.set_can_focus(False)
        self.scrolled_window.connect("scroll-event", self._on_scrolled_window_scroll)

        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self.listbox.set_can_focus(False)
        self.scrolled_window.add(self.listbox)
        hbox.pack_start(self.scrolled_window, True, True, 0)

        self.prompt_bar = NcPromptBar(
            self.content_canvas,
            self._on_prompt_submit,
            self.hide_prompt,
            on_visibility_change=self._on_prompt_visibility_change,
        )
        self.prompt_bar.set_no_show_all(True)
        self.prompt_bar.hide()
        outer_vbox.pack_start(self.prompt_bar, False, False, 0)

        if self.mode == "settings":
            self.set_size_request(self._get_cc_target_width_px(), self._get_cc_target_height_px())
        else:
            self.set_size_request(self._total_window_px(), -1)

    def _on_scrolled_window_scroll(self, _widget, event) -> bool:
        self._mouse_inside = True
        self._cancel_leave_close()
        hover_cards = [
            c for c in self.listbox.get_children()
            if isinstance(c, AsciiCard) and not getattr(c.box_model, "is_divider", False)
        ]
        target = next((c for c in hover_cards if c.is_hovered and getattr(c.box_model, "scroll_action", None)), None)
        if target is None and self._hovered_card_idx is not None and 0 <= self._hovered_card_idx < len(hover_cards):
            cand = hover_cards[self._hovered_card_idx]
            if getattr(cand.box_model, "scroll_action", None):
                target = cand
        if target is not None:
            return target._on_scroll(target, event)
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
        if not self.panel.is_busy() and not self.prompt_bar.get_visible() and not self.is_pointer_busy():
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
        for child in self.listbox.get_children():
            if isinstance(child, AsciiCard):
                child.lbl.queue_resize()
        self.listbox.queue_resize()
        _min_h, nat_h = self.listbox.get_preferred_height()
        pad = int(self.geom.get("panel_padding", 10)) * 2
        prompt_h = 0
        if self.prompt_bar.get_visible():
            _pm, p_nat = self.prompt_bar.get_preferred_height()
            prompt_h = max(p_nat, int(self.content_canvas.char_h * 3 + 12)) + 6

        if self.mode == "settings":
            tot_w = self._get_cc_target_width_px()
            tot_h = self._get_cc_target_height_px()
            avail_list_h = max(120, tot_h - prompt_h - pad)

            for nav_ch in self.nav_listbox.get_children():
                nav_ch.queue_resize()
            self.nav_listbox.queue_resize()
            _nmin, nav_nat_h = self.nav_listbox.get_preferred_height()
            if nav_nat_h > avail_list_h + 2:
                self.nav_scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            else:
                self.nav_scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.NEVER)
            self.nav_scrolled.set_size_request(int(self.nav_canvas.total_px) + 4, avail_list_h)

            if nat_h > avail_list_h:
                self.scrolled_window.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            else:
                self.scrolled_window.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.NEVER)
            right_w_px = int(round(self.content_canvas.total_px))
            self.scrolled_window.set_size_request(right_w_px, avail_list_h)
            self.set_size_request(tot_w, tot_h)
            self.resize(tot_w, tot_h)
            return False

        max_h = int(self.geom["popup_max_height"])
        avail_list_h = max(60, max_h - prompt_h)
        if nat_h > avail_list_h:
            self.scrolled_window.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            list_h = avail_list_h
        else:
            self.scrolled_window.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.NEVER)
            list_h = max(nat_h, 24)

        self.scrolled_window.set_size_request(-1, list_h)
        tot_w = self._total_window_px()
        tot_h = max(24, list_h + prompt_h + pad)
        self.set_size_request(tot_w, tot_h)
        self.resize(tot_w, tot_h)
        return False

    def _on_card_hover_change(self, card: AsciiCard, is_entering: bool) -> None:
        self._mouse_inside = True
        self._cancel_leave_close()
        children = [c for c in self.listbox.get_children() if isinstance(c, AsciiCard) and c.hover_enable]
        if is_entering:
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

    def activate_prompt(
        self,
        action_id: str,
        placeholder: str,
        default_txt: str = "",
        secret: bool = None,
        title: str = None,
        required: bool = False,
    ) -> None:
        self.bump_leave_guard(0.50)
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
        )

    def hide_prompt(self) -> None:
        self.bump_leave_guard(0.40)
        if HAS_LAYER_SHELL and self.mode != "settings" and os.environ.get("NC_UI_OFFSCREEN") != "1":
            GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.ON_DEMAND)
        self.prompt_bar.deactivate()

    def _on_prompt_submit(self, act_id: str, text: str) -> None:
        if not act_id:
            self.hide_prompt()
            return
        if act_id.startswith("cmd:"):
            self.hide_prompt()
            raw_cmd = act_id[4:].replace("{input}", text)
            probe.spawn_bg(["bash", "-c", raw_cmd])
            GLib.timeout_add(350, lambda: not self.refresh_view())
        else:
            self.panel.on_prompt_submit(act_id, text)
            if not self.prompt_bar.get_visible() and HAS_LAYER_SHELL and self.mode != "settings" and os.environ.get("NC_UI_OFFSCREEN") != "1":
                GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.ON_DEMAND)
            self.refresh_view()

    def dispatch_uri(self, uri: str) -> None:
        log.debug(f"dispatch_uri: {uri}")
        self.bump_leave_guard(0.50)
        if uri.startswith(("http://", "https://")):
            probe.spawn_bg(["xdg-open", uri])
            return
        if uri.startswith("ui:open_settings:"):
            sec_id = uri.split(":", 2)[2]
            if self.mode == "settings" and hasattr(self.panel, "set_section"):
                self._focused_target_idx = -1
                self._focused_uri_memory = None
                self.panel.set_section(sec_id)
                self.refresh_view()
            else:
                nc_ctl_bin = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin", "nc-ctl")
                probe.spawn_bg([sys.executable, nc_ctl_bin, "settings", sec_id])
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
            if hasattr(cc_mod, "SECTION_BLUEPRINTS"):
                tabs = cc_mod.SECTION_BLUEPRINTS.get(sec, {}).get("tabs", [])
            elif hasattr(cc_mod, "SECTION_TABS"):
                tabs = cc_mod.SECTION_TABS.get(sec, [])
            if len(tabs) <= 1:
                return False
            valid_ids = [tid for tid, _ in tabs]
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

    def _build_pure_ascii_nav_grid(self, n_items: int, inner_rows_per_item: list[int]) -> str:
        c_brd = PALETTE["structure"]
        self.nav_canvas.set_cols(SIDEBAR_ICON_COLS)
        empty_inner = " " * self.nav_canvas.inner_cols

        lines = [self.nav_canvas.top("", c_box=c_brd)]
        for idx in range(n_items):
            if idx > 0:
                lines.append(self.nav_canvas.mid("", c_box=c_brd))
            for _ in range(inner_rows_per_item[idx]):
                lines.append(self.nav_canvas.row(empty_inner, c_box=c_brd))
        lines.append(self.nav_canvas.bot(c_box=c_brd))
        return "\n".join(lines)

    def _populate_settings_nav(self) -> None:
        for child in self.nav_listbox.get_children():
            self.nav_listbox.remove(child)

        c_sec = PALETTE["schema"]
        c_acc = PALETTE["intent"]

        self.nav_canvas.set_cols(SIDEBAR_ICON_COLS)
        pad = int(self.geom.get("panel_padding", 10)) * 2
        prompt_h = 0
        if hasattr(self, "prompt_bar") and self.prompt_bar.get_visible():
            _pm, p_nat = self.prompt_bar.get_preferred_height()
            prompt_h = max(p_nat, int(self.content_canvas.char_h * 3 + 12)) + 6
        avail_h_px = max(360, self._get_cc_target_height_px() - pad - prompt_h)

        # Solo le sezioni reali del Control Center (rimosso il riquadro 'x' finale)
        nav_items = list(CONTROL_CENTER_SECTIONS)
        n_items = len(nav_items)
        active_sec = getattr(self.panel, "section", self.panel_id)

        # Minimo 2 righe interne ASCII per cella (cella quadrata con bordi │ continui a 11pt)
        inner_rows = [2] * n_items
        grid_markup = self._build_pure_ascii_nav_grid(n_items, inner_rows)
        grid_w_px, grid_h_px = self._measure_nav_markup_size_px(grid_markup)

        # Se lo schermo è più alto, distribuisce righe interne extra in modo identico su tutte le celle
        total_lines = 1 + sum(r + 1 for r in inner_rows)
        line_px = max(14.0, float(grid_h_px) / float(max(1, total_lines)))
        if grid_h_px < avail_h_px:
            extra_per_item = int((avail_h_px - grid_h_px) // (line_px * n_items))
            while extra_per_item > 0:
                cand_rows = [2 + extra_per_item] * n_items
                cand_markup = self._build_pure_ascii_nav_grid(n_items, cand_rows)
                cand_w, cand_h = self._measure_nav_markup_size_px(cand_markup)
                if cand_h <= avail_h_px:
                    inner_rows = cand_rows
                    grid_markup = cand_markup
                    grid_w_px, grid_h_px = cand_w, cand_h
                    total_lines = 1 + sum(r + 1 for r in inner_rows)
                    line_px = float(grid_h_px) / float(max(1, total_lines))
                    break
                extra_per_item -= 1

        self._nav_cell_height_px = float(grid_h_px) / float(max(1, n_items))

        # 1. Background: Griglia ASCII pura a 11pt (contorno │ continuo, zero deformazioni di baseline)
        bg_lbl = Gtk.Label()
        bg_lbl.set_xalign(0.0)
        bg_lbl.set_yalign(0.0)
        bg_lbl.set_line_wrap(False)
        bg_lbl.set_can_focus(False)
        bg_lbl.set_markup(grid_markup)

        overlay = Gtk.Overlay()
        overlay.add(bg_lbl)

        # 2. Foreground: Icone grandi (size='18500') centrate al pixel in ogni cella tramite Gtk.Fixed
        fixed = Gtk.Fixed()
        fixed.set_size_request(grid_w_px, grid_h_px)

        active_idx = 0
        cur_line_offset = 1  # salta la riga superiore ╭──────╮

        for idx, (sec_id, ic) in enumerate(nav_items):
            n_in = inner_rows[idx]
            y_top_px = int(round(cur_line_offset * line_px))
            cell_h_px = int(round(n_in * line_px))

            is_cur = (sec_id == active_sec)
            if is_cur:
                active_idx = idx
            col = c_acc if is_cur else c_sec

            eb = Gtk.EventBox()
            eb.set_visible_window(False)
            eb.set_size_request(grid_w_px, cell_h_px)
            eb.add_events(
                Gdk.EventMask.BUTTON_PRESS_MASK
                | Gdk.EventMask.BUTTON_RELEASE_MASK
                | Gdk.EventMask.ENTER_NOTIFY_MASK
            )

            ic_lbl = Gtk.Label()
            ic_lbl.set_halign(Gtk.Align.CENTER)
            ic_lbl.set_valign(Gtk.Align.CENTER)
            ic_lbl.set_markup(f"<span color='{col}' size='18500'>{ic}</span>")
            eb.add(ic_lbl)

            def _on_cell_press(_w, ev, target_sec=sec_id):
                if ev.button == 1:
                    self._on_card_pointer_activity("click")
                    self._focused_target_idx = -1
                    self._focused_uri_memory = None
                    GLib.idle_add(lambda: self.dispatch_uri(f"cc:section:{target_sec}") or False)
                    return True
                return False

            eb.connect("button-press-event", _on_cell_press)
            eb.connect("enter-notify-event", lambda *_: self._on_card_pointer_activity("enter") or False)
            fixed.put(eb, 0, y_top_px)

            cur_line_offset += n_in + 1  # avanza oltre le righe interne + la riga ├──────┤

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
        for c_idx, child in enumerate(self.listbox.get_children()):
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

        for child in self.listbox.get_children():
            if isinstance(child, AsciiCard):
                if child is target["card"]:
                    child.set_focused_anchor(target["anchor_idx"])
                else:
                    child.set_focused_anchor(None)

        if scroll:
            self._scroll_card_into_view(target["card"])

    def _scroll_card_into_view(self, card_widget: Gtk.Widget) -> None:
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
        uri = self._focus_targets[self._focused_target_idx]["uri"]
        if uri:
            self.dispatch_uri(uri)
            return True
        return False

    def refresh_view(self) -> bool:
        nav_vadj = self.nav_scrolled.get_vadjustment() if self.mode == "settings" and hasattr(self, "nav_scrolled") else None
        nav_scroll_val = nav_vadj.get_value() if nav_vadj else 0.0

        vadj = self.scrolled_window.get_vadjustment()
        scroll_val = vadj.get_value() if vadj else 0.0

        if self.mode == "settings":
            self._populate_settings_nav()
            cc_cols = self._compute_cc_content_cols()
            self.content_canvas.set_cols(cc_cols)
            raw_cards = self.panel.build_cards(self.content_canvas, force_expanded=True)
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
        else:
            is_exp = self.panel.is_expanded
            base_cols = self.panel.get_cols()
            state_key = (self.panel_id, is_exp)
            if self._stable_cols_key != state_key:
                self._stable_cols_key = state_key
                self._stable_cols_val = base_cols

            self.content_canvas.set_cols(base_cols)
            raw_cards = self.panel.build_cards(self.content_canvas, force_expanded=False)
            shared_cols = sync_boxes_width(raw_cards, self.content_canvas, max(base_cols, self._stable_cols_val))
            self._stable_cols_val = max(self._stable_cols_val, shared_cols)
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

        existing_rows = self.listbox.get_children()
        can_reuse = (
            len(existing_rows) == len(specs)
            and all(
                sp[0] == "ascii"
                and isinstance(w, AsciiCard)
                and getattr(w.box_model, "is_divider", False) == sp[3]
                and getattr(w, "pixbuf", None) is None
                and (sp[2] is None or getattr(sp[2], "pixbuf", None) is None)
                for w, sp in zip(existing_rows, specs)
            )
        )

        if can_reuse:
            for idx, (card_w, (_kind, bm, card_obj, is_div)) in enumerate(zip(existing_rows, specs)):
                card_w.set_margin_bottom(spacing if idx < len(specs) - 1 else 0)
                card_w.canvas = self.content_canvas
                card_w.hover_enable = can_select_boxes and not is_div
                card_w.on_hover_change = self._on_card_hover_change
                card_w.on_pointer_activity = self._on_card_pointer_activity
                if card_obj is not None:
                    card_w.on_row_click = card_obj.on_row_click
                    card_w.on_link = card_obj.on_link or self.dispatch_uri
                else:
                    card_w.on_row_click = None
                    card_w.on_link = self.dispatch_uri
                bm.selected = bool(card_w.is_hovered and card_w.hover_enable)
                card_w.box_model = bm
                card_w.refresh_markup()
        else:
            for child in existing_rows:
                self.listbox.remove(child)

            ascii_cards = []
            row_widgets = []
            for _kind, bm, card_obj, is_div in specs:
                if _kind == "ascii":
                    if card_obj is not None:
                        c = card_obj
                        c.canvas = self.content_canvas
                        c.on_link = c.on_link or self.dispatch_uri
                        c.hover_enable = can_select_boxes and not is_div
                        c.on_hover_change = self._on_card_hover_change
                        c.on_pointer_activity = self._on_card_pointer_activity
                        c.box_model.selected = False
                        c.refresh_markup()
                    else:
                        bm.selected = False
                        c = AsciiCard(
                            bm,
                            self.content_canvas,
                            on_link=self.dispatch_uri,
                            hover_enable=(can_select_boxes and not is_div),
                            on_hover_change=self._on_card_hover_change,
                            on_pointer_activity=self._on_card_pointer_activity,
                        )
                    row_widgets.append(c)
                    if c.hover_enable:
                        ascii_cards.append(c)
                else:
                    row_widgets.append(card_obj)

            for idx, rw in enumerate(row_widgets):
                rw.set_margin_bottom(spacing if idx < len(row_widgets) - 1 else 0)
                self.listbox.add(rw)

            if can_select_boxes and self._hovered_card_idx is not None and 0 <= self._hovered_card_idx < len(ascii_cards):
                for idx, c in enumerate(ascii_cards):
                    c.set_hovered_visual(idx == self._hovered_card_idx)

            self.listbox.show_all()

        self._rebuild_focus_targets()
        self._apply_dynamic_anchor()
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
        if self.mode == "settings" or self.panel.is_busy() or self.prompt_bar.get_visible():
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
        for child in self.listbox.get_children():
            if isinstance(child, AsciiCard) and child.hover_enable:
                child.set_hovered_visual(False)

        if self.mode == "settings" or self.panel.is_busy() or self.prompt_bar.get_visible():
            return False

        self._cancel_leave_close()
        self._leave_close_source_id = GLib.timeout_add(80, self._confirm_leave_close)
        return False

    def _on_key_press(self, _widget, event) -> bool:
        if event.keyval == Gdk.KEY_Escape:
            if self.prompt_bar.get_visible():
                self.hide_prompt()
                return True
            Gtk.main_quit()
            return True

        if self.prompt_bar.get_visible():
            if not self.prompt_bar.entry.has_focus():
                self.prompt_bar.entry.grab_focus()
                self.prompt_bar.entry.set_position(-1)
            return False

        state = event.state
        is_shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
        is_alt = bool(state & Gdk.ModifierType.MOD1_MASK)

        # ── 1. SHIFT + FRECCE / PAGE_UP / PAGE_DOWN (Sezioni, Subtab & Bar Surfing) ──
        if is_shift and event.keyval in (Gdk.KEY_Left, Gdk.KEY_KP_Left):
            if self.mode == "settings":
                return self._cycle_subtab(-1)
            self._surf_waybar_panel(-1)
            return True

        if is_shift and event.keyval in (Gdk.KEY_Right, Gdk.KEY_KP_Right):
            if self.mode == "settings":
                return self._cycle_subtab(1)
            self._surf_waybar_panel(1)
            return True

        if (is_shift and event.keyval in (Gdk.KEY_Up, Gdk.KEY_KP_Up)) or event.keyval == Gdk.KEY_Page_Up:
            if self.mode == "settings":
                sec_ids = [s[0] for s in CONTROL_CENTER_SECTIONS]
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
                sec_ids = [s[0] for s in CONTROL_CENTER_SECTIONS]
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
            return self._move_focus_1d(1)

        if event.keyval == Gdk.KEY_ISO_Left_Tab or (event.keyval in (Gdk.KEY_Tab, Gdk.KEY_KP_Tab) and is_shift):
            return self._move_focus_1d(-1)

        if event.keyval in (Gdk.KEY_Right, Gdk.KEY_KP_Right, Gdk.KEY_Left, Gdk.KEY_KP_Left):
            is_right = event.keyval in (Gdk.KEY_Right, Gdk.KEY_KP_Right)
            # Se una card con scroll_action è esplicitamente sotto il puntatore mouse e non stiamo usando il focus tastiera
            if self._focused_target_idx < 0:
                hover_cards = [
                    c for c in self.listbox.get_children()
                    if isinstance(c, AsciiCard) and not getattr(c.box_model, "is_divider", False)
                ]
                target_card = next((c for c in hover_cards if c.is_hovered and getattr(c.box_model, "scroll_action", None)), None)
                if target_card is not None and target_card.on_link:
                    target_card.on_link(f"{target_card.box_model.scroll_action}:{'up' if is_right else 'down'}")
                    return True
            if self._move_focus_1d(1 if is_right else -1):
                return True

        if event.keyval in (Gdk.KEY_Down, Gdk.KEY_KP_Down):
            if self._move_focus_2d_vertical(1):
                return True

        if event.keyval in (Gdk.KEY_Up, Gdk.KEY_KP_Up):
            if self._move_focus_2d_vertical(-1):
                return True

        # ── 3. ATTIVAZIONE PULSANTE A FUOCO (Invio / Spazio) ──
        if event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_space):
            if self._activate_focused_target():
                return True

        # ── 4. REGOLAZIONE RAPIDA CARD CON SCROLL_ACTION (+ / -) ──
        if event.keyval in (Gdk.KEY_plus, Gdk.KEY_equal, Gdk.KEY_KP_Add, Gdk.KEY_minus, Gdk.KEY_KP_Subtract):
            direction = "up" if event.keyval in (Gdk.KEY_plus, Gdk.KEY_equal, Gdk.KEY_KP_Add) else "down"
            if 0 <= self._focused_target_idx < len(self._focus_targets):
                f_card = self._focus_targets[self._focused_target_idx]["card"]
                scr_act = getattr(f_card.box_model, "scroll_action", None)
                if scr_act:
                    self.dispatch_uri(f"{scr_act}:{direction}")
                    return True
            hover_cards = [
                c for c in self.listbox.get_children()
                if isinstance(c, AsciiCard) and not getattr(c.box_model, "is_divider", False)
            ]
            target_card = next(
                (c for c in hover_cards if (c.is_hovered or getattr(c.box_model, "elected", False)) and getattr(c.box_model, "scroll_action", None)),
                None,
            )
            if target_card is not None and target_card.on_link:
                target_card.on_link(f"{target_card.box_model.scroll_action}:{direction}")
                return True

        return self.panel.on_key_press(event)
