#!/usr/bin/env python3
"""NC-UI Gaming Controllers — Cairo Blueprint & Response Curve Widgets."""

import fcntl
import glob
import json
import math
import os
import re
import struct
import time
from core import probe
from core.config import THEME, _parse_rgba_components, get_config_dir
from core.i18n import t
from core.privileges import build_privilege_modal_card
from core.pad_mapper_core import (
    _ABS_BRAKE,
    _ABS_GAS,
    _ABS_HAT0X,
    _ABS_HAT0Y,
    _ABS_HAT2X,
    _ABS_HAT2Y,
    _ABS_PROFILE,
    _ABS_RX,
    _ABS_RY,
    _ABS_RZ,
    _ABS_X,
    _ABS_Y,
    _ABS_Z,
    _BTN_DPAD_DOWN,
    _BTN_DPAD_LEFT,
    _BTN_DPAD_RIGHT,
    _BTN_DPAD_UP,
    _BTN_EAST,
    _BTN_GEAR_DOWN,
    _BTN_GEAR_UP,
    _BTN_GRIPL,
    _BTN_GRIPL2,
    _BTN_GRIPR,
    _BTN_GRIPR2,
    _BTN_MODE,
    _BTN_NORTH,
    _BTN_SELECT,
    _BTN_SOUTH,
    _BTN_START,
    _BTN_THUMB,
    _BTN_THUMB2,
    _BTN_THUMBL,
    _BTN_THUMBR,
    _BTN_TL,
    _BTN_TL2,
    _BTN_TR,
    _BTN_TR2,
    _BTN_TRIGGER_HAPPY1,
    _BTN_TRIGGER_HAPPY2,
    _BTN_TRIGGER_HAPPY3,
    _BTN_TRIGGER_HAPPY4,
    _BTN_TRIGGER_HAPPY5,
    _BTN_TRIGGER_HAPPY6,
    _BTN_TRIGGER_HAPPY7,
    _BTN_TRIGGER_HAPPY8,
    _BTN_WEST,
    _CURVES,
    _ELITE_SLOTS,
    _EVIOCRMFF,
    _EVIOCSFF,
    _EV_ABS,
    _EV_FF,
    _EV_KEY,
    _FF_RUMBLE,
    _GYRO_MODES,
    _KEY_BACK,
    _KEY_HOMEPAGE,
    _KEY_RECORD,
    _LED_MODES,
    _PADDLE_ACTIONS,
    _PADDLE_EVDEV_CODES,
    _PADDLE_KEYS,
    _PROBE_AXES,
    _STICK_CALCS,
    _TRIGGER_LOCKS,
    _TRIGGER_LOCK_CUT,
    _TRIGGER_LOCK_IDS,
    _XBOX_ELITE_PIDS,
    ALL_PAD_CONTROLS,
    BIND_MODES,
    MACRO_PAD_MODES,
    MOUSE_TARGET_TO_EVDEV,
    PAD_CONTROL_IDS,
    PAD_MAPPER_BIN,
    STICK_MODES,
    WHEEL_TARGETS,
    _controllers_path,
    _detect_family,
    apply_stick_calibration,
    bitmask_has,
    build_template_profile,
    compile_macro_events,
    eval_response_curve,
    eval_trigger_curve,
    format_action_short,
    get_mapper_runtime_status,
    is_pad_mapper_running,
    iter_proc_input_blocks,
    load_pad_store,
    norm_pad_profile,
    norm_pad_store,
    reload_pad_mapper_if_running,
    resolve_combo_codes,
    resolve_evdev_axis_map,
    resolve_evdev_btn_map,
    save_pad_store,
    stop_pad_mapper,
)
from core.state import STATE
from ui.canvas import Canvas
from ui.kit import (
    ActionBtn,
    Badge,
    Box,
    CyclePill,
    InlineField,
    InspectorCell,
    SelectPill,
    Slider,
    StateBadge,
    Txt,
    feedback_cards,
    get_semantic_palette,
    hex_to_rgba_f,
    rounded_rect_path,
    set_feedback,
)
from ui.widgets import AsciiCard

_EV_STRUCT = struct.Struct("@qqHHi")

_CONTROLLER_MODELS: tuple[dict, ...] = (
    {"id": "xbox", "brand": "Microsoft", "arch_key": "arch_asym", "feat_key": "feat_xbox"},
    {"id": "xbox_elite", "brand": "Microsoft", "arch_key": "arch_asym", "feat_key": "feat_xbox_elite"},
    {"id": "playstation", "brand": "Sony", "arch_key": "arch_sym", "feat_key": "feat_playstation"},
    {"id": "nintendo", "brand": "Nintendo", "arch_key": "arch_asym", "feat_key": "feat_nintendo"},
    {"id": "steam", "brand": "Valve", "arch_key": "arch_dual_pads", "feat_key": "feat_steam"},
)
_MODEL_BY_ID = {m["id"]: m for m in _CONTROLLER_MODELS}
_LAYOUTS = tuple(m["id"] for m in _CONTROLLER_MODELS)

_DEFAULT_PROFILE = {
    "layout": "xbox",
    "curve": "linear",
    "inner_dz": 8,
    "outer_th": 95,
    "trigger_cut": 85,
    "rumble_pct": 80,
    "led_mode": "theme",
    "inv_ly": False,
    "inv_ry": False,
    "swap_sticks": False,
    "gyro_mode": "off",
    "cal_lx": 0.0,
    "cal_ly": 0.0,
    "cal_rx": 0.0,
    "cal_ry": 0.0,
    "elite_slot": 1,
    "p1": "a",
    "p2": "b",
    "p3": "x",
    "p4": "y",
    "trigger_lock": "full",
    "stick_calc": "radial",
}

# Shared live input state (updated by active evdev poller or preview interaction)
_LIVE_INPUT: dict = {
    "dev_id": "",
    "lx": 0.0,
    "ly": 0.0,
    "rx": 0.0,
    "ry": 0.0,
    "lt": 0.0,
    "rt": 0.0,
    "hat_x": 0,
    "hat_y": 0,
    "hw_profile": None,
    "buttons": set(),
    "rumble_until": 0.0,
    "rumble_side": "",
}
_DISCOVERY_CACHE: dict = {"ts": 0.0, "devices": []}

def _draw_cairo_footer(cr, bx: float, bw: float, fy: float, footer_h: float, left_txt: str, left_col: str, right_txt: str, pal: dict, surf_hex: str, f_micro) -> None:
    from gi.repository import Pango, PangoCairo

    cr.set_source_rgba(*hex_to_rgba_f(surf_hex, 0.78))
    cr.rectangle(bx + 1.0, fy, bw - 2.0, footer_h)
    cr.fill()
    cr.set_source_rgba(*hex_to_rgba_f(pal["structure"], 0.72))
    cr.set_line_width(1.0)
    cr.move_to(bx + 1.0, fy + 0.5)
    cr.line_to(bx + bw - 1.0, fy + 0.5)
    cr.stroke()

    fr = PangoCairo.create_layout(cr)
    fr.set_font_description(f_micro)
    fr.set_text(right_txt, -1)
    _, fre = fr.get_pixel_extents()

    fpad = 12.0
    fl = PangoCairo.create_layout(cr)
    fl.set_font_description(f_micro)
    fl.set_ellipsize(Pango.EllipsizeMode.END)
    fl.set_width(int(max(40.0, bw - fpad * 3.0 - fre.width) * Pango.SCALE))
    fl.set_text(left_txt, -1)
    _, fle = fl.get_pixel_extents()

    fty = fy + max(2.0, (footer_h - max(fle.height, fre.height)) * 0.5)
    cr.set_source_rgba(*hex_to_rgba_f(left_col, 0.92))
    cr.move_to(bx + fpad, fty)
    PangoCairo.show_layout(cr, fl)

    cr.set_source_rgba(*hex_to_rgba_f(pal["schema"], 0.80))
    cr.move_to(bx + bw - fpad - fre.width, fty)
    PangoCairo.show_layout(cr, fr)


class _ControllerBlueprintMixin:
    """Pure-Cairo 3-Zone Tactical Gamepad Test Deck (Xbox Accessories + Steam Input style)."""

    def __init__(
        self,
        dev: dict | None,
        profile: dict,
        widget_width: int = 760,
        fixed_height: int = 232,
        inset_x: int = 14,
        font_px: float = 11.0,
        bind_mode: bool = False,
        selected_ctrl: str = "",
        mapped_ctrls: set[str] | None = None,
        on_select_ctrl=None,
        bind_footer_text: str = "",
    ):
        super().__init__()
        self.dev = dev
        self.profile = dict(profile)
        self.widget_width = max(320, int(widget_width))
        self.widget_height = max(210, int(fixed_height))
        self.inset_x = max(4, int(inset_x))
        self.font_px = max(8.5, float(font_px))
        self.bind_mode = bool(bind_mode)
        self.selected_ctrl = str(selected_ctrl or "")
        self.mapped_ctrls = set(mapped_ctrls or ())
        self.on_select_ctrl = on_select_ctrl
        self.bind_footer_text = str(bind_footer_text or "")
        self.footer_h = 26
        self._ev_fd: int | None = None
        self._abs_ranges: dict[int, tuple[int, int]] = {}
        self._poll_id: int = 0
        self._hit_targets: list[dict] = []
        self._drag_kind: str = ""
        self._hovered_id: str = ""
        self._init_widget()

    def _hit_to_ctrl(self, hid: str) -> str:
        if hid == "scope_ls":
            return "l_stick"
        if hid == "scope_rs":
            return "r_stick"
        if hid == "bar_lt":
            return "lt"
        if hid == "bar_rt":
            return "rt"
        if hid.startswith("btn_"):
            raw = hid[4:]
            return "l3" if raw == "lpad_click" else raw
        return ""

    def _bind_col_lw(self, cid: str, pressed: bool, base_col: str, base_lw: float, pal: dict) -> tuple[str, float]:
        if pressed:
            return pal["intent"], max(1.5, base_lw)
        if self.bind_mode:
            if cid == self.selected_ctrl:
                return pal["attention"], max(2.0, base_lw + 0.7)
            if cid in self.mapped_ctrls:
                return pal["vitality"], max(1.3, base_lw + 0.25)
        return base_col, base_lw

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
        self.connect("destroy", self._on_destroy)
        if os.environ.get("NC_UI_OFFSCREEN") != "1":
            self._open_live_evdev()

    def _open_live_evdev(self) -> None:
        self._axis_map = resolve_evdev_axis_map({})
        fam = self.dev["family"] if self.dev else self.profile.get("layout", "xbox")
        self._btn_map = resolve_evdev_btn_map(fam)
        if not self.dev or not self.dev.get("readable") or not self.dev.get("evdev"):
            return
        try:
            fd = os.open(self.dev["evdev"], os.O_RDONLY | os.O_NONBLOCK)
            self._ev_fd = fd
            if _LIVE_INPUT.get("dev_id") != self.dev["id"]:
                _LIVE_INPUT["dev_id"] = self.dev["id"]
                _LIVE_INPUT.update({"lx": 0.0, "ly": 0.0, "rx": 0.0, "ry": 0.0, "lt": 0.0, "rt": 0.0, "hat_x": 0, "hat_y": 0, "hw_profile": None})
                _LIVE_INPUT["buttons"].clear()
            init_vals: dict[int, int] = {}
            for axis in _PROBE_AXES:
                try:
                    req = (2 << 30) | (24 << 16) | (ord("E") << 8) | (0x40 + axis)
                    buf = bytearray(24)
                    fcntl.ioctl(fd, req, buf)
                    val, mn, mx, _fz, _fl, _rs = struct.unpack("@6i", buf)
                    if mx > mn:
                        self._abs_ranges[axis] = (mn, mx)
                        init_vals[axis] = val
                except OSError:
                    pass
            self._axis_map = resolve_evdev_axis_map(self._abs_ranges)
            for axis, val in init_vals.items():
                self._apply_abs_value(axis, val)
            from gi.repository import GLib

            self._poll_id = GLib.timeout_add(33, self._poll_evdev)
        except OSError:
            self._ev_fd = None

    def _on_destroy(self, _widget) -> None:
        if self._poll_id:
            try:
                from gi.repository import GLib
                GLib.source_remove(self._poll_id)
            except Exception:
                pass
            self._poll_id = 0
        if self._ev_fd is not None:
            try:
                os.close(self._ev_fd)
            except OSError:
                pass
            self._ev_fd = None

    def _norm_axis(self, axis: int, val: int, signed: bool = True) -> float:
        mn, mx = self._abs_ranges.get(axis, (-32768, 32767) if signed else (0, 255))
        span = max(1, mx - mn)
        u = (float(val) - float(mn)) / float(span)
        return max(-1.0, min(1.0, u * 2.0 - 1.0)) if signed else max(0.0, min(1.0, u))

    def _apply_abs_value(self, code: int, val: int) -> None:
        am = self._axis_map
        if code == am.get("lx"):
            _LIVE_INPUT["lx"] = self._norm_axis(code, val, True)
        elif code == am.get("ly"):
            _LIVE_INPUT["ly"] = self._norm_axis(code, val, True)
        elif code == am.get("rx"):
            _LIVE_INPUT["rx"] = self._norm_axis(code, val, True)
        elif code == am.get("ry"):
            _LIVE_INPUT["ry"] = self._norm_axis(code, val, True)
        elif code == am.get("lt"):
            _LIVE_INPUT["lt"] = self._norm_axis(code, val, False)
        elif code == am.get("rt"):
            _LIVE_INPUT["rt"] = self._norm_axis(code, val, False)
        elif code == am.get("hat_x"):
            _LIVE_INPUT["hat_x"] = -1 if val < 0 else (1 if val > 0 else 0)
        elif code == am.get("hat_y"):
            _LIVE_INPUT["hat_y"] = -1 if val < 0 else (1 if val > 0 else 0)
        elif code == am.get("lpad_x"):
            nv = self._norm_axis(code, val, True)
            _LIVE_INPUT["hat_x"] = -1 if nv < -0.35 else (1 if nv > 0.35 else 0)
        elif code == am.get("lpad_y"):
            nv = self._norm_axis(code, val, True)
            _LIVE_INPUT["hat_y"] = -1 if nv < -0.35 else (1 if nv > 0.35 else 0)
        elif code == am.get("profile"):
            _LIVE_INPUT["hw_profile"] = max(0, min(3, int(val)))

    def _poll_evdev(self) -> bool:
        if self._ev_fd is None:
            return False
        dirty = time.monotonic() < float(_LIVE_INPUT.get("rumble_until", 0.0))
        btn_map = self._btn_map
        has_analog_lt = self._axis_map.get("lt") is not None
        has_analog_rt = self._axis_map.get("rt") is not None
        picked_ctrl = ""
        can_auto_sel = self.bind_mode and self.on_select_ctrl and not STATE.get("keyboard", "capture_mode", "")
        try:
            while True:
                data = os.read(self._ev_fd, _EV_STRUCT.size * 32)
                if not data:
                    break
                off = 0
                while off + _EV_STRUCT.size <= len(data):
                    _s, _us, ev_t, code, val = _EV_STRUCT.unpack_from(data, off)
                    off += _EV_STRUCT.size
                    if ev_t == _EV_ABS:
                        prev_lt, prev_rt = float(_LIVE_INPUT["lt"]), float(_LIVE_INPUT["rt"])
                        self._apply_abs_value(code, val)
                        if can_auto_sel:
                            if prev_lt < 0.55 <= float(_LIVE_INPUT["lt"]):
                                picked_ctrl = "lt"
                            elif prev_rt < 0.55 <= float(_LIVE_INPUT["rt"]):
                                picked_ctrl = "rt"
                        dirty = True
                    elif ev_t == _EV_KEY:
                        if code == _BTN_TL2 and not has_analog_lt:
                            _LIVE_INPUT["lt"] = 1.0 if val else 0.0
                            if val and can_auto_sel:
                                picked_ctrl = "lt"
                            dirty = True
                        elif code == _BTN_TR2 and not has_analog_rt:
                            _LIVE_INPUT["rt"] = 1.0 if val else 0.0
                            if val and can_auto_sel:
                                picked_ctrl = "rt"
                            dirty = True
                        elif code in btn_map:
                            bname = btn_map[code]
                            if val:
                                _LIVE_INPUT["buttons"].add(bname)
                                if can_auto_sel:
                                    picked_ctrl = "l3" if bname == "lpad_click" else bname
                            else:
                                _LIVE_INPUT["buttons"].discard(bname)
                            dirty = True
        except BlockingIOError:
            pass
        except OSError:
            self._on_destroy(self)
            return False
        if picked_ctrl and picked_ctrl != self.selected_ctrl and picked_ctrl in PAD_CONTROL_IDS:
            self.selected_ctrl = picked_ctrl
            self.on_select_ctrl(picked_ctrl)
            return True
        if dirty:
            self.queue_draw()
        return True

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

    def _find_hit(self, px: float, py: float) -> dict | None:
        for t_obj in reversed(self._hit_targets):
            if t_obj["shape"] == "circle":
                if math.hypot(px - t_obj["cx"], py - t_obj["cy"]) <= t_obj["r"] + 3.0:
                    return t_obj
            else:
                if t_obj["x"] <= px <= t_obj["x"] + t_obj["w"] and t_obj["y"] <= py <= t_obj["y"] + t_obj["h"]:
                    return t_obj
        return None

    def _update_drag_target(self, kind: str, px: float, py: float) -> None:
        tgt = next((t_obj for t_obj in self._hit_targets if t_obj["id"] == kind), None)
        if not tgt:
            return
        if kind in ("scope_ls", "scope_rs"):
            dx = (px - tgt["cx"]) / max(1.0, tgt["r"])
            dy = (py - tgt["cy"]) / max(1.0, tgt["r"])
            mag = math.hypot(dx, dy)
            if mag > 1.0:
                dx, dy = dx / mag, dy / mag
            if kind == "scope_ls":
                _LIVE_INPUT["lx"], _LIVE_INPUT["ly"] = dx, dy
            else:
                _LIVE_INPUT["rx"], _LIVE_INPUT["ry"] = dx, dy
        elif kind in ("bar_lt", "bar_rt"):
            frac = max(0.0, min(1.0, (tgt["y"] + tgt["h"] - py) / max(1.0, tgt["h"])))
            if kind == "bar_lt":
                _LIVE_INPUT["lt"] = frac
            else:
                _LIVE_INPUT["rt"] = frac
        self.queue_draw()

    def _on_press(self, _widget, event) -> bool:
        if event.button != 1:
            return False
        hit = self._find_hit(event.x, event.y)
        if not hit:
            return False
        hid = hit["id"]
        if self.bind_mode and self.on_select_ctrl:
            cid = self._hit_to_ctrl(hid)
            if cid and cid in PAD_CONTROL_IDS:
                self.selected_ctrl = cid
                self.on_select_ctrl(cid)
                return True
        if hid in ("scope_ls", "scope_rs", "bar_lt", "bar_rt"):
            self._drag_kind = hid
            self._set_cursor("grabbing")
            self._update_drag_target(hid, event.x, event.y)
            return True
        if hid.startswith("btn_"):
            bname = hid[4:]
            self._drag_kind = hid
            _LIVE_INPUT["buttons"].add(bname)
            self.queue_draw()
            return True
        return False

    def _on_motion(self, _widget, event) -> bool:
        if self._drag_kind in ("scope_ls", "scope_rs", "bar_lt", "bar_rt"):
            self._update_drag_target(self._drag_kind, event.x, event.y)
            return True
        hit = self._find_hit(event.x, event.y)
        hid = hit["id"] if hit else ""
        if hid != self._hovered_id:
            self._hovered_id = hid
            if hid in ("scope_ls", "scope_rs", "bar_lt", "bar_rt"):
                self._set_cursor("grab")
            elif hid:
                self._set_cursor("pointer")
            else:
                self._set_cursor(None)
            self.queue_draw()
        return False

    def _on_release(self, _widget, event) -> bool:
        if not self._drag_kind or event.button != 1:
            return False
        dk = self._drag_kind
        self._drag_kind = ""
        self._set_cursor(None)
        if dk == "scope_ls":
            _LIVE_INPUT["lx"], _LIVE_INPUT["ly"] = 0.0, 0.0
        elif dk == "scope_rs":
            _LIVE_INPUT["rx"], _LIVE_INPUT["ry"] = 0.0, 0.0
        elif dk == "bar_lt":
            _LIVE_INPUT["lt"] = 0.0
        elif dk == "bar_rt":
            _LIVE_INPUT["rt"] = 0.0
        elif dk.startswith("btn_"):
            _LIVE_INPUT["buttons"].discard(dk[4:])
        self.queue_draw()
        return True

    def _on_leave(self, _widget, _event) -> bool:
        if self._hovered_id and not self._drag_kind:
            self._hovered_id = ""
            self._set_cursor(None)
            self.queue_draw()
        return False

    def _draw_polar_scope(
        self,
        cr,
        cx: float,
        cy: float,
        r: float,
        title: str,
        raw_x: float,
        raw_y: float,
        cal_x: float,
        cal_y: float,
        clicked: bool,
        scope_id: str,
        pal: dict,
        surf_hex: str,
        f_micro,
    ) -> None:
        from gi.repository import PangoCairo

        self._hit_targets.append({"id": scope_id, "shape": "circle", "cx": cx, "cy": cy, "r": r})
        is_hov = self._hovered_id == scope_id or self._drag_kind == scope_id

        # Title above scope
        tl = PangoCairo.create_layout(cr)
        tl.set_font_description(f_micro)
        tl.set_text(title, -1)
        _, te = tl.get_pixel_extents()
        cr.set_source_rgba(*hex_to_rgba_f(pal["schema"], 0.90))
        cr.move_to(cx - te.width * 0.5, cy - r - te.height - 7.0)
        PangoCairo.show_layout(cr, tl)

        # Outer circular scope face
        sc_ctrl = "l_stick" if scope_id == "scope_ls" else "r_stick"
        s_col, s_lw = self._bind_col_lw(sc_ctrl, clicked, pal["focus"] if is_hov else pal["structure"], 1.2 if is_hov else 1.0, pal)
        cr.save()
        cr.new_sub_path()
        cr.arc(cx, cy, r, 0.0, 2.0 * math.pi)
        cr.set_source_rgba(*hex_to_rgba_f(surf_hex, 0.88))
        cr.fill_preserve()
        cr.set_source_rgba(*hex_to_rgba_f(s_col, 0.95))
        cr.set_line_width(s_lw)
        cr.stroke()

        # Crosshair axes
        cr.set_source_rgba(*hex_to_rgba_f(pal["structure"], 0.55))
        cr.set_line_width(0.9)
        cr.move_to(cx - r + 2.0, cy)
        cr.line_to(cx + r - 2.0, cy)
        cr.move_to(cx, cy - r + 2.0)
        cr.line_to(cx, cy + r - 2.0)
        cr.stroke()

        # Outer threshold ring (vitality dashed)
        r_out = r * (float(self.profile["outer_th"]) / 100.0)
        cr.save()
        cr.set_dash([3.0, 3.0])
        cr.new_sub_path()
        cr.arc(cx, cy, r_out, 0.0, 2.0 * math.pi)
        cr.set_source_rgba(*hex_to_rgba_f(pal["vitality"], 0.52))
        cr.set_line_width(1.0)
        cr.stroke()
        cr.restore()

        # Inner deadzone reticle (square for Xbox Elite Axis-Independent, circle for Radial / True Diag)
        r_dz = max(3.5, r * (float(self.profile["inner_dz"]) / 100.0))
        is_axial = self.profile.get("layout") == "xbox_elite" and self.profile.get("stick_calc") == "axis"
        cr.new_sub_path()
        if is_axial:
            cr.rectangle(cx - r_dz, cy - r_dz, r_dz * 2.0, r_dz * 2.0)
        else:
            cr.arc(cx, cy, r_dz, 0.0, 2.0 * math.pi)
        cr.set_source_rgba(*hex_to_rgba_f(pal["attention_bg"], 0.72))
        cr.fill_preserve()
        cr.save()
        cr.set_dash([2.5, 2.5])
        cr.set_source_rgba(*hex_to_rgba_f(pal["attention"], 0.85))
        cr.set_line_width(1.0)
        cr.stroke()
        cr.restore()

        # Raw dot if shifted from calibrated
        if math.hypot(raw_x, raw_y) > 0.02:
            cr.new_sub_path()
            cr.arc(cx + raw_x * r, cy + raw_y * r, 2.8, 0.0, 2.0 * math.pi)
            cr.set_source_rgba(*hex_to_rgba_f(pal["schema"], 0.72))
            cr.fill()

        # Calibrated stick vector + glowing cyan tip
        vx, vy = cx + cal_x * r, cy + cal_y * r
        if math.hypot(cal_x, cal_y) > 0.01:
            cr.set_source_rgba(*hex_to_rgba_f(pal["intent"], 0.90))
            cr.set_line_width(1.8)
            cr.move_to(cx, cy)
            cr.line_to(vx, vy)
            cr.stroke()
        cr.new_sub_path()
        cr.arc(vx, vy, 4.2 if clicked else 3.4, 0.0, 2.0 * math.pi)
        cr.set_source_rgba(*hex_to_rgba_f(pal["intent"], 1.0))
        cr.fill()
        cr.restore()

        # Numeric readout below scope
        rd = PangoCairo.create_layout(cr)
        rd.set_font_description(f_micro)
        rd.set_text(f"{cal_x:+.2f}, {cal_y:+.2f}", -1)
        _, re_e = rd.get_pixel_extents()
        cr.set_source_rgba(*hex_to_rgba_f(pal["focus_val"] if math.hypot(cal_x, cal_y) > 0.01 else pal["schema"], 0.88))
        cr.move_to(cx - re_e.width * 0.5, cy + r + 5.0)
        PangoCairo.show_layout(cr, rd)

    def _draw_trigger_bar(
        self,
        cr,
        x: float,
        y: float,
        w: float,
        h: float,
        lbl: str,
        raw_val: float,
        eff_val: float,
        bar_id: str,
        pal: dict,
        surf_hex: str,
        f_micro,
    ) -> None:
        from gi.repository import PangoCairo

        self._hit_targets.append({"id": bar_id, "shape": "rect", "x": x - 4.0, "y": y, "w": w + 8.0, "h": h})
        is_hov = self._hovered_id == bar_id or self._drag_kind == bar_id

        # Label above bar
        tl = PangoCairo.create_layout(cr)
        tl.set_font_description(f_micro)
        tl.set_text(lbl, -1)
        _, te = tl.get_pixel_extents()
        cr.set_source_rgba(*hex_to_rgba_f(pal["intent"] if eff_val > 0.02 else pal["schema"], 0.92))
        cr.move_to(x + (w - te.width) * 0.5, y - te.height - 6.0)
        PangoCairo.show_layout(cr, tl)

        # Recessed vertical bar
        tr_ctrl = "lt" if bar_id == "bar_lt" else "rt"
        t_col, t_lw = self._bind_col_lw(tr_ctrl, eff_val > 0.02, pal["focus"] if is_hov else pal["structure"], 1.1, pal)
        rounded_rect_path(cr, x + 0.5, y + 0.5, w - 1.0, h - 1.0, 3.0)
        cr.set_source_rgba(*hex_to_rgba_f(surf_hex, 0.90))
        cr.fill_preserve()
        cr.set_source_rgba(*hex_to_rgba_f(t_col, 0.94))
        cr.set_line_width(t_lw)
        cr.stroke()

        # Fill proportional to calibrated trigger output
        if eff_val > 0.005:
            fh = max(2.0, (h - 2.0) * eff_val)
            rounded_rect_path(cr, x + 1.5, y + h - 1.0 - fh, w - 3.0, fh, 2.0)
            cr.set_source_rgba(*hex_to_rgba_f(pal["intent"], 0.90))
            cr.fill()

        # Hair-trigger cutoff marker (attention yellow) + Xbox Elite 3-stop lock notches
        if self.profile.get("layout") == "xbox_elite":
            cr.set_source_rgba(*hex_to_rgba_f(pal["schema"], 0.65))
            cr.set_line_width(1.0)
            for stop_pct in (25, 55, 100):
                sy_t = y + h - (h * (stop_pct / 100.0))
                cr.move_to(x + w + 1.0, sy_t)
                cr.line_to(x + w + 4.0, sy_t)
            cr.stroke()
        cut_frac = float(self.profile["trigger_cut"]) / 100.0
        cut_y = y + h - (h * cut_frac)
        cr.set_source_rgba(*hex_to_rgba_f(pal["attention"], 0.95))
        cr.set_line_width(1.6)
        cr.move_to(x - 2.5, cut_y)
        cr.line_to(x + w + 2.5, cut_y)
        cr.stroke()

        # Percent below bar
        pl = PangoCairo.create_layout(cr)
        pl.set_font_description(f_micro)
        pl.set_text(f"{int(round(eff_val * 100))}%", -1)
        _, pe = pl.get_pixel_extents()
        cr.set_source_rgba(*hex_to_rgba_f(pal["focus_val"] if eff_val > 0.02 else pal["schema"], 0.86))
        cr.move_to(x + (w - pe.width) * 0.5, y + h + 5.0)
        PangoCairo.show_layout(cr, pl)

    def _draw_gamepad_silhouette(
        self,
        cr,
        cx: float,
        cy: float,
        scale: float,
        ls_xy: tuple[float, float],
        rs_xy: tuple[float, float],
        lt_eff: float,
        rt_eff: float,
        pal: dict,
        surf_hex: str,
        f_btn,
        f_micro,
    ) -> None:
        from gi.repository import PangoCairo

        layout = self.profile.get("layout", "xbox")
        btns = _LIVE_INPUT["buttons"]
        hx, hy = int(_LIVE_INPUT.get("hat_x", 0)), int(_LIVE_INPUT.get("hat_y", 0))
        d_up = ("dup" in btns) or (hy < 0)
        d_dn = ("ddown" in btns) or (hy > 0)
        d_lt = ("dleft" in btns) or (hx < 0)
        d_rt = ("dright" in btns) or (hx > 0)
        rumbling = time.monotonic() < float(_LIVE_INPUT.get("rumble_until", 0.0))
        r_side = _LIVE_INPUT.get("rumble_side", "")

        def _p(dx: float, dy: float) -> tuple[float, float]:
            return cx + dx * scale, cy + dy * scale

        # 1. Shoulder Bumpers (LB / RB)
        for side_i, (b_id, b_lbl) in enumerate([
            ("lb", "L1" if layout == "playstation" else ("L" if layout == "nintendo" else "LB")),
            ("rb", "R1" if layout == "playstation" else ("R" if layout == "nintendo" else "RB")),
        ]):
            sgn = -1.0 if side_i == 0 else 1.0
            bx0, by0 = _p(sgn * 74.0 - 24.0, -76.0)
            bw0, bh0 = 48.0 * scale, 16.0 * scale
            pressed = b_id in btns
            self._hit_targets.append({"id": f"btn_{b_id}", "shape": "rect", "x": bx0, "y": by0, "w": bw0, "h": bh0})
            rounded_rect_path(cr, bx0, by0, bw0, bh0, 6.0 * scale)
            cr.set_source_rgba(*hex_to_rgba_f(pal["intent_bg"] if pressed else surf_hex, 0.92))
            cr.fill_preserve()
            b_col, b_lw = self._bind_col_lw(b_id, pressed, pal["structure"], 1.0, pal)
            cr.set_source_rgba(*hex_to_rgba_f(b_col, 0.95))
            cr.set_line_width(b_lw)
            cr.stroke()
            bl = PangoCairo.create_layout(cr)
            bl.set_font_description(f_micro)
            bl.set_text(b_lbl, -1)
            _, be = bl.get_pixel_extents()
            cr.set_source_rgba(*hex_to_rgba_f(pal["focus"] if pressed else pal["schema"], 0.95))
            cr.move_to(bx0 + (bw0 - be.width) * 0.5, by0 + (bh0 - be.height) * 0.5)
            PangoCairo.show_layout(cr, bl)

        # 2. Main Stylized Ergonomic Gamepad Wireframe Silhouette
        cr.save()
        x0, y0 = _p(0.0, -56.0)
        cr.move_to(x0, y0)
        # Right upper shoulder → right grip → inner arch → left grip → left upper shoulder
        cr.curve_to(*_p(58.0, -56.0), *_p(106.0, -54.0), *_p(122.0, -24.0))
        cr.curve_to(*_p(136.0, 4.0), *_p(144.0, 52.0), *_p(126.0, 72.0))
        cr.curve_to(*_p(112.0, 86.0), *_p(92.0, 80.0), *_p(74.0, 54.0))
        cr.curve_to(*_p(58.0, 32.0), *_p(44.0, 26.0), *_p(0.0, 26.0))
        cr.curve_to(*_p(-44.0, 26.0), *_p(-58.0, 32.0), *_p(-74.0, 54.0))
        cr.curve_to(*_p(-92.0, 80.0), *_p(-112.0, 86.0), *_p(-126.0, 72.0))
        cr.curve_to(*_p(-144.0, 52.0), *_p(-136.0, 4.0), *_p(-122.0, -24.0))
        cr.curve_to(*_p(-106.0, -54.0), *_p(-58.0, -56.0), *_p(0.0, -56.0))
        cr.close_path()

        cr.set_source_rgba(*hex_to_rgba_f(surf_hex, 0.86))
        cr.fill_preserve()
        cr.set_source_rgba(*hex_to_rgba_f(pal["attention"] if rumbling else pal["structure"], 0.96))
        cr.set_line_width(1.6 if rumbling else 1.35)
        cr.stroke()

        # Subtle rumble pulse arcs on grips when testing motors
        if rumbling:
            for side_tag, sgn in (("left", -1.0), ("right", 1.0)):
                if r_side in (side_tag, "dual"):
                    gx, gy = _p(sgn * 138.0, 26.0)
                    cr.set_source_rgba(*hex_to_rgba_f(pal["attention"], 0.85))
                    cr.set_line_width(1.5)
                    for rad_i in (10.0, 16.0):
                        a0 = (-0.55 if sgn > 0 else math.pi - 0.55)
                        a1 = (0.55 if sgn > 0 else math.pi + 0.55)
                        cr.new_sub_path()
                        cr.arc(gx, gy, rad_i * scale, a0, a1)
                        cr.stroke()
        cr.restore()

        # 3. Center Nexus / Touchpad + View & Menu buttons
        led_mode = self.profile.get("led_mode", "theme")
        led_alpha = {"theme": 0.90, "high": 0.95, "low": 0.38, "off": 0.0}.get(led_mode, 0.85)
        led_col = pal["intent"] if led_mode == "theme" else pal["focus"]

        if layout == "playstation":
            # DualSense Touchpad with glowing side lightbar strips
            tpx, tpy = _p(-27.0, -48.0)
            tpw, tph = 54.0 * scale, 30.0 * scale
            rounded_rect_path(cr, tpx, tpy, tpw, tph, 5.0 * scale)
            cr.set_source_rgba(*hex_to_rgba_f(pal["structure_bg"], 0.85))
            cr.fill_preserve()
            cr.set_source_rgba(*hex_to_rgba_f(pal["structure"], 0.95))
            cr.set_line_width(1.0)
            cr.stroke()
            if led_alpha > 0.0:
                cr.set_source_rgba(*hex_to_rgba_f(led_col, led_alpha))
                cr.set_line_width(2.2)
                cr.move_to(tpx + 1.5, tpy + 4.0)
                cr.line_to(tpx + 1.5, tpy + tph - 4.0)
                cr.move_to(tpx + tpw - 1.5, tpy + 4.0)
                cr.line_to(tpx + tpw - 1.5, tpy + tph - 4.0)
                cr.stroke()

        # Center Guide / Home button
        gx, gy = _p(0.0, 4.0 if layout == "playstation" else -24.0)
        gr = 10.5 * scale
        g_press = "guide" in btns
        self._hit_targets.append({"id": "btn_guide", "shape": "circle", "cx": gx, "cy": gy, "r": gr})
        cr.new_sub_path()
        cr.arc(gx, gy, gr, 0.0, 2.0 * math.pi)
        cr.set_source_rgba(*hex_to_rgba_f(pal["intent_bg"] if g_press else pal["structure_bg"], 0.92))
        cr.fill_preserve()
        cr.set_source_rgba(*hex_to_rgba_f(led_col if (g_press or led_alpha > 0.0) else pal["structure"], 0.95 if g_press else max(0.45, led_alpha)))
        cr.set_line_width(1.6 if g_press else 1.2)
        cr.stroke()
        cr.new_sub_path()
        cr.arc(gx, gy, 3.2 * scale, 0.0, 2.0 * math.pi)
        cr.set_source_rgba(*hex_to_rgba_f(led_col if led_alpha > 0.0 else pal["schema"], 0.95 if g_press else max(0.35, led_alpha)))
        cr.fill()

        # View & Menu small pill buttons (+ Xbox Share button)
        for b_id, dx_c, dy_c in (
            ("view", -39.0 if layout == "playstation" else (-24.0 if layout == "steam" else -26.0), -35.0 if layout == "playstation" else (-20.0 if layout == "steam" else -16.0)),
            ("menu", 39.0 if layout == "playstation" else (24.0 if layout == "steam" else 26.0), -35.0 if layout == "playstation" else (-20.0 if layout == "steam" else -16.0)),
        ):
            bx_c, by_c = _p(dx_c, dy_c)
            br_c = 5.6 * scale
            bp = b_id in btns
            self._hit_targets.append({"id": f"btn_{b_id}", "shape": "circle", "cx": bx_c, "cy": by_c, "r": br_c + 2.0})
            cr.new_sub_path()
            cr.arc(bx_c, by_c, br_c, 0.0, 2.0 * math.pi)
            cr.set_source_rgba(*hex_to_rgba_f(pal["intent_bg"] if bp else pal["structure_bg"], 0.92))
            cr.fill_preserve()
            vc_col, vc_lw = self._bind_col_lw(b_id, bp, pal["structure"], 1.0, pal)
            cr.set_source_rgba(*hex_to_rgba_f(vc_col, 0.95))
            cr.set_line_width(vc_lw)
            cr.stroke()

        if layout == "xbox":
            # Series X|S Share pill tucked neatly under Guide button
            sh_x, sh_y = _p(-7.0, -7.0)
            sh_w, sh_h = 14.0 * scale, 6.0 * scale
            sh_p = "share" in btns
            self._hit_targets.append({"id": "btn_share", "shape": "rect", "x": sh_x - 2.0, "y": sh_y - 2.0, "w": sh_w + 4.0, "h": sh_h + 4.0})
            rounded_rect_path(cr, sh_x, sh_y, sh_w, sh_h, 2.5 * scale)
            cr.set_source_rgba(*hex_to_rgba_f(pal["intent_bg"] if sh_p else pal["structure_bg"], 0.92))
            cr.fill_preserve()
            cr.set_source_rgba(*hex_to_rgba_f(pal["intent"] if sh_p else pal["structure"], 0.90))
            cr.set_line_width(1.2 if sh_p else 0.9)
            cr.stroke()
        elif layout == "xbox_elite":
            # Xbox Elite Series 2 Profile switch pill + 3 Onboard Slot LEDs beneath Guide button
            pr_x, pr_y = _p(-8.0, -7.5)
            pr_w, pr_h = 16.0 * scale, 6.5 * scale
            pr_p = "profile" in btns
            self._hit_targets.append({"id": "btn_profile", "shape": "rect", "x": pr_x - 2.0, "y": pr_y - 2.0, "w": pr_w + 4.0, "h": pr_h + 4.0})
            rounded_rect_path(cr, pr_x, pr_y, pr_w, pr_h, 2.5 * scale)
            cr.set_source_rgba(*hex_to_rgba_f(pal["intent_bg"] if pr_p else pal["structure_bg"], 0.92))
            cr.fill_preserve()
            cr.set_source_rgba(*hex_to_rgba_f(pal["intent"] if pr_p else pal["schema"], 0.92))
            cr.set_line_width(1.2 if pr_p else 0.95)
            cr.stroke()
            # 3 Onboard Profile Indicator LEDs (Slot 1 / 2 / 3; Slot 0 = all dim)
            hw_slot = _LIVE_INPUT.get("hw_profile")
            active_slot = int(hw_slot) if hw_slot is not None else int(self.profile.get("elite_slot", 1))
            for slot_i, lx_off in ((1, -7.0), (2, 0.0), (3, 7.0)):
                ldx, ldy = _p(lx_off, 5.5)
                lit = active_slot == slot_i
                cr.new_sub_path()
                cr.arc(ldx, ldy, 2.3 * scale, 0.0, 2.0 * math.pi)
                cr.set_source_rgba(*hex_to_rgba_f(pal["intent"] if lit else pal["structure"], 0.98 if lit else 0.50))
                cr.fill()
            # 4 Physical Rear Paddles (P3/P4 left lower arch, P1/P2 right lower arch)
            for pid, plbl, dx_p, dy_p in (
                ("p3", "P3", -118.0, 35.0),
                ("p4", "P4", -104.0, 52.0),
                ("p1", "P1", 92.0, 35.0),
                ("p2", "P2", 78.0, 52.0),
            ):
                px0, py0 = _p(dx_p, dy_p)
                pw0, ph0 = 26.0 * scale, 13.0 * scale
                pp = pid in btns
                is_mapped = self.profile.get(pid, "none") != "none"
                self._hit_targets.append({"id": f"btn_{pid}", "shape": "rect", "x": px0, "y": py0, "w": pw0, "h": ph0})
                rounded_rect_path(cr, px0, py0, pw0, ph0, 4.0 * scale)
                cr.set_source_rgba(*hex_to_rgba_f(pal["intent_bg"] if pp else pal["structure_bg"], 0.92))
                cr.fill_preserve()
                b_col_base = pal["schema"] if is_mapped else pal["structure"]
                b_col, b_lw = self._bind_col_lw(pid, pp, b_col_base, 1.0, pal)
                cr.set_source_rgba(*hex_to_rgba_f(b_col, 0.95 if (pp or pid in self.mapped_ctrls or pid == self.selected_ctrl) else (0.85 if is_mapped else 0.60)))
                cr.set_line_width(b_lw)
                cr.stroke()
                pl_t = PangoCairo.create_layout(cr)
                pl_t.set_font_description(f_micro)
                pl_t.set_text(plbl, -1)
                _, pe_t = pl_t.get_pixel_extents()
                cr.set_source_rgba(*hex_to_rgba_f(pal["focus"] if pp else (pal["focus_val"] if is_mapped else pal["inactive"]), 0.92))
                cr.move_to(px0 + (pw0 - pe_t.width) * 0.5, py0 + (ph0 - pe_t.height) * 0.5)
                PangoCairo.show_layout(cr, pl_t)

        if layout == "steam":
            # Valve Steam Controller: Dual Large Circular Haptic Trackpads, Lower-Left Stick, Lower-Right ABXY, Rear Paddles
            # Rear Grip Paddle indicators (LG / RG) along lower inner arches
            for pid, plbl, dx_p in (("l4", "LG", -114.0), ("r4", "RG", 88.0)):
                px0, py0 = _p(dx_p, 46.0)
                pw0, ph0 = 26.0 * scale, 13.0 * scale
                pp = pid in btns
                self._hit_targets.append({"id": f"btn_{pid}", "shape": "rect", "x": px0, "y": py0, "w": pw0, "h": ph0})
                rounded_rect_path(cr, px0, py0, pw0, ph0, 4.0 * scale)
                cr.set_source_rgba(*hex_to_rgba_f(pal["intent_bg"] if pp else pal["structure_bg"], 0.90))
                cr.fill_preserve()
                cr.set_source_rgba(*hex_to_rgba_f(pal["intent"] if pp else pal["structure"], 0.85))
                cr.set_line_width(1.3 if pp else 0.9)
                cr.stroke()
                pl_t = PangoCairo.create_layout(cr)
                pl_t.set_font_description(f_micro)
                pl_t.set_text(plbl, -1)
                _, pe_t = pl_t.get_pixel_extents()
                cr.set_source_rgba(*hex_to_rgba_f(pal["focus"] if pp else pal["schema"], 0.88))
                cr.move_to(px0 + (pw0 - pe_t.width) * 0.5, py0 + (ph0 - pe_t.height) * 0.5)
                PangoCairo.show_layout(cr, pl_t)

            # Left Circular Haptic Trackpad (engraved cross D-Pad dish)
            lpx, lpy = _p(-76.0, -24.0)
            pr = 24.5 * scale
            lpad_p = ("lpad_click" in btns) or d_up or d_dn or d_lt or d_rt
            self._hit_targets.append({"id": "btn_lpad_click", "shape": "circle", "cx": lpx, "cy": lpy, "r": pr})
            cr.new_sub_path()
            cr.arc(lpx, lpy, pr, 0.0, 2.0 * math.pi)
            cr.set_source_rgba(*hex_to_rgba_f(pal["intent_bg"] if lpad_p else pal["structure_bg"], 0.92))
            cr.fill_preserve()
            cr.set_source_rgba(*hex_to_rgba_f(pal["intent"] if lpad_p else pal["structure"], 0.95))
            cr.set_line_width(1.4 if lpad_p else 1.1)
            cr.stroke()
            # Engraved cross indentations (clickable 4-way quadrants)
            c_arm = 16.0 * scale
            c_thk = 5.5 * scale
            for did, qx, qy, qw, qh, qact in (
                ("dup", lpx - c_thk * 0.5, lpy - c_arm, c_thk, c_arm - 2.0, d_up),
                ("ddown", lpx - c_thk * 0.5, lpy + 2.0, c_thk, c_arm - 2.0, d_dn),
                ("dleft", lpx - c_arm, lpy - c_thk * 0.5, c_arm - 2.0, c_thk, d_lt),
                ("dright", lpx + 2.0, lpy - c_thk * 0.5, c_arm - 2.0, c_thk, d_rt),
            ):
                self._hit_targets.append({"id": f"btn_{did}", "shape": "rect", "x": qx - 3.0, "y": qy - 3.0, "w": qw + 6.0, "h": qh + 6.0})
                rounded_rect_path(cr, qx, qy, qw, qh, 2.0 * scale)
                cr.set_source_rgba(*hex_to_rgba_f(pal["intent"] if qact else pal["structure"], 0.92 if qact else 0.45))
                cr.fill()

            # Right Circular Haptic Trackpad (concave dish with concentric ring + live touch cursor)
            rpx, rpy = _p(76.0, -24.0)
            rpad_p = ("r3" in btns) or math.hypot(rs_xy[0], rs_xy[1]) > 0.04
            self._hit_targets.append({"id": "btn_r3", "shape": "circle", "cx": rpx, "cy": rpy, "r": pr})
            cr.new_sub_path()
            cr.arc(rpx, rpy, pr, 0.0, 2.0 * math.pi)
            cr.set_source_rgba(*hex_to_rgba_f(pal["intent_bg"] if ("r3" in btns) else pal["structure_bg"], 0.92))
            cr.fill_preserve()
            cr.set_source_rgba(*hex_to_rgba_f(pal["intent"] if rpad_p else pal["structure"], 0.95))
            cr.set_line_width(1.4 if rpad_p else 1.1)
            cr.stroke()
            cr.new_sub_path()
            cr.arc(rpx, rpy, pr * 0.62, 0.0, 2.0 * math.pi)
            cr.set_source_rgba(*hex_to_rgba_f(pal["structure"], 0.40))
            cr.set_line_width(0.9)
            cr.stroke()
            # Live Right Trackpad finger cursor dot
            tx_p, ty_p = rpx + rs_xy[0] * (pr * 0.72), rpy + rs_xy[1] * (pr * 0.72)
            cr.new_sub_path()
            cr.arc(tx_p, ty_p, 4.2 * scale, 0.0, 2.0 * math.pi)
            cr.set_source_rgba(*hex_to_rgba_f(pal["intent"] if rpad_p else pal["schema"], 0.95))
            cr.fill()

            # Lower-Left Analog Stick
            sx, sy = _p(-36.0, 11.0)
            sr = 15.5 * scale
            clicked = "l3" in btns
            self._hit_targets.append({"id": "btn_l3", "shape": "circle", "cx": sx, "cy": sy, "r": sr})
            cr.new_sub_path()
            cr.arc(sx, sy, sr, 0.0, 2.0 * math.pi)
            cr.set_source_rgba(*hex_to_rgba_f(pal["structure_bg"], 0.90))
            cr.fill_preserve()
            cr.set_source_rgba(*hex_to_rgba_f(pal["intent"] if clicked else pal["structure"], 0.92))
            cr.set_line_width(1.4 if clicked else 1.0)
            cr.stroke()
            nx, ny = sx + ls_xy[0] * (sr * 0.48), sy + ls_xy[1] * (sr * 0.48)
            active_s = clicked or math.hypot(ls_xy[0], ls_xy[1]) > 0.04
            cr.new_sub_path()
            cr.arc(nx, ny, 8.8 * scale, 0.0, 2.0 * math.pi)
            cr.set_source_rgba(*hex_to_rgba_f(pal["intent_bg"] if active_s else surf_hex, 0.95))
            cr.fill_preserve()
            cr.set_source_rgba(*hex_to_rgba_f(pal["intent"] if active_s else pal["schema"], 0.95))
            cr.set_line_width(1.3 if active_s else 1.0)
            cr.stroke()

            # Lower-Right Compact Face Buttons Diamond (Y/X/B/A)
            fcx, fcy = _p(36.0, 11.0)
            f_dist = 13.5 * scale
            f_rad = 6.8 * scale
            glyphs = {"north": "Y", "west": "X", "east": "B", "south": "A"}
            for bid, ox, oy in (("north", 0.0, -f_dist), ("west", -f_dist, 0.0), ("east", f_dist, 0.0), ("south", 0.0, f_dist)):
                bx_f, by_f = fcx + ox, fcy + oy
                pressed = bid in btns
                self._hit_targets.append({"id": f"btn_{bid}", "shape": "circle", "cx": bx_f, "cy": by_f, "r": f_rad + 1.5})
                cr.new_sub_path()
                cr.arc(bx_f, by_f, f_rad, 0.0, 2.0 * math.pi)
                cr.set_source_rgba(*hex_to_rgba_f(pal["intent_bg"] if pressed else pal["structure_bg"], 0.95))
                cr.fill_preserve()
                cr.set_source_rgba(*hex_to_rgba_f(pal["intent"] if pressed else pal["structure"], 0.95))
                cr.set_line_width(1.4 if pressed else 1.0)
                cr.stroke()
                fl = PangoCairo.create_layout(cr)
                fl.set_font_description(f_micro)
                fl.set_text(glyphs[bid], -1)
                _, fe = fl.get_pixel_extents()
                cr.set_source_rgba(*hex_to_rgba_f(pal["focus"] if pressed else pal["focus_val"], 0.95))
                cr.move_to(bx_f - fe.width * 0.5, by_f - fe.height * 0.5)
                PangoCairo.show_layout(cr, fl)
            return

        # 4. Adaptive Stick & D-Pad Positions (Xbox/Nintendo asymmetric vs PlayStation symmetric)
        if layout == "playstation":
            ls_pos = _p(-42.0, 12.0)
            rs_pos = _p(42.0, 12.0)
            dp_pos = _p(-82.0, -22.0)
        else:
            ls_pos = _p(-78.0, -22.0)
            dp_pos = _p(-42.0, 12.0)
            rs_pos = _p(42.0, 12.0)

        # Draw Left & Right Thumbstick Sockets on Gamepad
        for (sx, sy), (vx, vy), click_id in ((ls_pos, ls_xy, "l3"), (rs_pos, rs_xy, "r3")):
            sr = 18.0 * scale
            clicked = click_id in btns
            self._hit_targets.append({"id": f"btn_{click_id}", "shape": "circle", "cx": sx, "cy": sy, "r": sr})
            cr.new_sub_path()
            cr.arc(sx, sy, sr, 0.0, 2.0 * math.pi)
            cr.set_source_rgba(*hex_to_rgba_f(pal["structure_bg"], 0.90))
            cr.fill_preserve()
            sc_c, sc_w = self._bind_col_lw(click_id, clicked, pal["structure"], 1.0, pal)
            cr.set_source_rgba(*hex_to_rgba_f(sc_c, 0.92))
            cr.set_line_width(sc_w)
            cr.stroke()
            # Moving thumbstick cap
            nx, ny = sx + vx * (sr * 0.48), sy + vy * (sr * 0.48)
            nr = 10.0 * scale
            active_s = clicked or math.hypot(vx, vy) > 0.04
            cr.new_sub_path()
            cr.arc(nx, ny, nr, 0.0, 2.0 * math.pi)
            cr.set_source_rgba(*hex_to_rgba_f(pal["intent_bg"] if active_s else surf_hex, 0.95))
            cr.fill_preserve()
            cr.set_source_rgba(*hex_to_rgba_f(pal["intent"] if active_s else pal["schema"], 0.95))
            cr.set_line_width(1.3 if active_s else 1.0)
            cr.stroke()

        # Draw D-Pad (Faceted Metallic Dish on Xbox Elite, 4-Way Cross on standard controllers)
        dx0, dy0 = dp_pos
        if layout == "xbox_elite":
            dish_r = 17.5 * scale
            # Outer octagonal faceted metallic dish
            cr.new_sub_path()
            for oct_i in range(8):
                ang = (math.pi / 8.0) + oct_i * (math.pi / 4.0)
                ox_p, oy_p = dx0 + math.cos(ang) * dish_r, dy0 + math.sin(ang) * dish_r
                if oct_i == 0:
                    cr.move_to(ox_p, oy_p)
                else:
                    cr.line_to(ox_p, oy_p)
            cr.close_path()
            any_d = d_up or d_dn or d_lt or d_rt
            cr.set_source_rgba(*hex_to_rgba_f(pal["structure_bg"], 0.94))
            cr.fill_preserve()
            cr.set_source_rgba(*hex_to_rgba_f(pal["intent"] if any_d else pal["schema"], 0.92))
            cr.set_line_width(1.35 if any_d else 1.05)
            cr.stroke()
            # 4 faceted directional wedge petals
            p_arm = 11.5 * scale
            p_thk = 7.6 * scale
            for did, rx0, ry0, rw0, rh0, active_d in (
                ("dup", dx0 - p_thk * 0.5, dy0 - p_arm - 2.0 * scale, p_thk, p_arm - 1.5 * scale, d_up),
                ("ddown", dx0 - p_thk * 0.5, dy0 + 3.5 * scale, p_thk, p_arm - 1.5 * scale, d_dn),
                ("dleft", dx0 - p_arm - 2.0 * scale, dy0 - p_thk * 0.5, p_arm - 1.5 * scale, p_thk, d_lt),
                ("dright", dx0 + 3.5 * scale, dy0 - p_thk * 0.5, p_arm - 1.5 * scale, p_thk, d_rt),
            ):
                self._hit_targets.append({"id": f"btn_{did}", "shape": "rect", "x": rx0 - 2.0, "y": ry0 - 2.0, "w": rw0 + 4.0, "h": rh0 + 4.0})
                rounded_rect_path(cr, rx0, ry0, rw0, rh0, 2.0 * scale)
                dc_c, _dc_w = self._bind_col_lw(did, active_d, pal["structure"], 1.0, pal)
                cr.set_source_rgba(*hex_to_rgba_f(dc_c, 0.95 if (active_d or did in self.mapped_ctrls or did == self.selected_ctrl) else 0.55))
                cr.fill()
            # Center concave dimple
            cr.new_sub_path()
            cr.arc(dx0, dy0, 3.2 * scale, 0.0, 2.0 * math.pi)
            cr.set_source_rgba(*hex_to_rgba_f(pal["structure"], 0.72))
            cr.fill()
        else:
            arm = 11.0 * scale
            thk = 10.5 * scale
            dpad_parts = [
                ("dup", dx0 - thk * 0.5, dy0 - thk * 0.5 - arm, thk, arm, d_up),
                ("ddown", dx0 - thk * 0.5, dy0 + thk * 0.5, thk, arm, d_dn),
                ("dleft", dx0 - thk * 0.5 - arm, dy0 - thk * 0.5, arm, thk, d_lt),
                ("dright", dx0 + thk * 0.5, dy0 - thk * 0.5, arm, thk, d_rt),
            ]
            # Center square of D-Pad
            cr.rectangle(dx0 - thk * 0.5, dy0 - thk * 0.5, thk, thk)
            cr.set_source_rgba(*hex_to_rgba_f(pal["structure_bg"], 0.92))
            cr.fill()
            for did, rx0, ry0, rw0, rh0, active_d in dpad_parts:
                self._hit_targets.append({"id": f"btn_{did}", "shape": "rect", "x": rx0, "y": ry0, "w": rw0, "h": rh0})
                rounded_rect_path(cr, rx0, ry0, rw0, rh0, 2.2 * scale)
                cr.set_source_rgba(*hex_to_rgba_f(pal["intent_bg"] if active_d else pal["structure_bg"], 0.95))
                cr.fill_preserve()
                dc_c, dc_w = self._bind_col_lw(did, active_d, pal["structure"], 0.95, pal)
                cr.set_source_rgba(*hex_to_rgba_f(dc_c, 0.95))
                cr.set_line_width(dc_w)
                cr.stroke()

        # 5. 4 Face Buttons Diamond (Adaptive Glyphs: Xbox / Xbox Elite / PlayStation / Nintendo)
        fcx, fcy = _p(82.0 if layout == "playstation" else 78.0, -22.0)
        f_dist = 18.5 * scale
        f_rad = 9.2 * scale
        glyphs = {
            "xbox": {"north": "Y", "west": "X", "east": "B", "south": "A"},
            "xbox_elite": {"north": "Y", "west": "X", "east": "B", "south": "A"},
            "playstation": {"north": "\u25b3", "west": "\u25a1", "east": "\u25cb", "south": "\u2715"},
            "nintendo": {"north": "X", "west": "Y", "east": "A", "south": "B"},
        }[layout]
        face_offsets = [
            ("north", 0.0, -f_dist),
            ("west", -f_dist, 0.0),
            ("east", f_dist, 0.0),
            ("south", 0.0, f_dist),
        ]
        for bid, ox, oy in face_offsets:
            bx_f, by_f = fcx + ox, fcy + oy
            pressed = bid in btns
            self._hit_targets.append({"id": f"btn_{bid}", "shape": "circle", "cx": bx_f, "cy": by_f, "r": f_rad + 1.5})
            cr.new_sub_path()
            cr.arc(bx_f, by_f, f_rad, 0.0, 2.0 * math.pi)
            cr.set_source_rgba(*hex_to_rgba_f(pal["intent_bg"] if pressed else pal["structure_bg"], 0.95))
            cr.fill_preserve()
            fc_c, fc_w = self._bind_col_lw(bid, pressed, pal["structure"], 1.05, pal)
            cr.set_source_rgba(*hex_to_rgba_f(fc_c, 0.95))
            cr.set_line_width(fc_w)
            cr.stroke()
            fl = PangoCairo.create_layout(cr)
            fl.set_font_description(f_btn)
            fl.set_text(glyphs[bid], -1)
            _, fe = fl.get_pixel_extents()
            cr.set_source_rgba(*hex_to_rgba_f(pal["focus"] if pressed else pal["focus_val"], 0.95))
            cr.move_to(bx_f - fe.width * 0.5, by_f - fe.height * 0.5)
            PangoCairo.show_layout(cr, fl)

    def _on_draw(self, _widget, cr) -> bool:
        from gi.repository import Pango, PangoCairo

        alloc = self.get_allocation()
        w = max(320.0, float(alloc.width or self.widget_width))
        h = max(210.0, float(alloc.height or self.widget_height))
        bx = float(self.inset_x)
        bw = max(200.0, w - 2.0 * bx)
        footer_h = float(self.footer_h)
        stage_h = h - footer_h

        pal = get_semantic_palette()
        surf_hex = str((THEME.get("colors") or {}).get("bg_panel") or pal["structure_bg"])
        font_family = str((THEME.get("typography") or {}).get("font_family", "monospace") or "monospace")

        self._hit_targets = []

        # 1. Outer recessed Tactical Blueprint container
        rounded_rect_path(cr, bx + 0.5, 0.5, bw - 1.0, h - 1.0, 6.0)
        cr.set_source_rgba(*hex_to_rgba_f(pal["structure_bg"], 0.62))
        cr.fill_preserve()
        cr.set_source_rgba(*hex_to_rgba_f(pal["structure"], 0.90))
        cr.set_line_width(1.0)
        cr.stroke()

        cr.save()
        rounded_rect_path(cr, bx + 1.0, 1.0, bw - 2.0, h - 2.0, 5.5)
        cr.clip()

        base_pt = float((THEME.get("typography") or {}).get("font_size", 11) or 11)
        f_micro = Pango.FontDescription()
        f_micro.set_family(font_family)
        f_micro.set_size(int(round(base_pt * 0.85 * Pango.SCALE)))
        f_btn = Pango.FontDescription()
        f_btn.set_family(font_family)
        f_btn.set_weight(Pango.Weight.BOLD)
        f_btn.set_size(int(round(base_pt * 0.88 * Pango.SCALE)))

        # Compute calibrated live values
        raw_lx, raw_ly = float(_LIVE_INPUT["lx"]), float(_LIVE_INPUT["ly"])
        raw_rx, raw_ry = float(_LIVE_INPUT["rx"]), float(_LIVE_INPUT["ry"])
        if self.profile.get("swap_sticks"):
            raw_lx, raw_ly, raw_rx, raw_ry = raw_rx, raw_ry, raw_lx, raw_ly
        cal_lx, cal_ly = apply_stick_calibration(raw_lx, raw_ly, self.profile, is_right=False)
        cal_rx, cal_ry = apply_stick_calibration(raw_rx, raw_ry, self.profile, is_right=True)
        lt_raw, rt_raw = float(_LIVE_INPUT["lt"]), float(_LIVE_INPUT["rt"])
        cut_f = float(self.profile["trigger_cut"]) / 100.0
        lt_eff = eval_trigger_curve(lt_raw, cut_f)
        rt_eff = eval_trigger_curve(rt_raw, cut_f)
        layout = self.profile.get("layout", "xbox")

        # 2. Symmetric 3-Zone Geometry
        scope_r = min(46.0, max(32.0, stage_h * 0.23, bw * 0.062))
        scope_cy = stage_h * 0.50
        bar_w = 12.0
        bar_h = scope_r * 2.0
        bar_y = scope_cy - scope_r

        # Left Scope & LT Bar
        ls_cx = bx + max(scope_r + 22.0, bw * 0.12)
        lt_x = ls_cx + scope_r + 20.0
        self._draw_polar_scope(
            cr, ls_cx, scope_cy, scope_r, t("cc_input_controllers.bp_l_stick"),
            raw_lx, raw_ly, cal_lx, cal_ly, ("l3" in _LIVE_INPUT["buttons"]), "scope_ls", pal, surf_hex, f_micro,
        )
        self._draw_trigger_bar(
            cr, lt_x, bar_y, bar_w, bar_h,
            "L2" if layout == "playstation" else ("ZL" if layout == "nintendo" else "LT"),
            lt_raw, lt_eff, "bar_lt", pal, surf_hex, f_micro,
        )

        # Right Scope & RT Bar
        rs_cx = bx + bw - max(scope_r + 22.0, bw * 0.12)
        rt_x = rs_cx - scope_r - 20.0 - bar_w
        self._draw_polar_scope(
            cr, rs_cx, scope_cy, scope_r,
            t("cc_input_controllers.bp_r_pad") if layout == "steam" else t("cc_input_controllers.bp_r_stick"),
            raw_rx, raw_ry, cal_rx, cal_ry, ("r3" in _LIVE_INPUT["buttons"]), "scope_rs", pal, surf_hex, f_micro,
        )
        self._draw_trigger_bar(
            cr, rt_x, bar_y, bar_w, bar_h,
            "R2" if layout == "playstation" else ("ZR" if layout == "nintendo" else "RT"),
            rt_raw, rt_eff, "bar_rt", pal, surf_hex, f_micro,
        )

        # Subtle vertical zone dividers separating scopes from center gamepad
        div_l = lt_x + bar_w + 18.0
        div_r = rt_x - 18.0
        if div_r - div_l > 160.0:
            cr.save()
            cr.set_source_rgba(*hex_to_rgba_f(pal["structure"], 0.35))
            cr.set_line_width(1.0)
            cr.set_dash([2.5, 4.0])
            for dx_line in (div_l, div_r):
                cr.move_to(math.floor(dx_line) + 0.5, 14.0)
                cr.line_to(math.floor(dx_line) + 0.5, stage_h - 14.0)
            cr.stroke()
            cr.restore()

            # Center Stylized Gamepad Wireframe
            avail_cw = div_r - div_l - 20.0
            avail_ch = stage_h - 24.0
            gp_scale = min(1.02, avail_cw / 292.0, avail_ch / 172.0)
            self._draw_gamepad_silhouette(
                cr, (div_l + div_r) * 0.5, stage_h * 0.51, gp_scale,
                (cal_lx, cal_ly), (cal_rx, cal_ry), lt_eff, rt_eff, pal, surf_hex, f_btn, f_micro,
            )

        # 3. Integrated Live Telemetry Footer Strip
        drift_pct = min(100.0, max(math.hypot(raw_lx, raw_ly), math.hypot(raw_rx, raw_ry)) * 100.0)
        if self.bind_mode:
            left_txt = self.bind_footer_text or t("cc_input_controllers.mp_bp_footer_empty")
            right_txt = t("cc_input_controllers.bp_footer_hint_bind")
        else:
            left_txt = t(
                "cc_input_controllers.bp_footer_live",
                p0=f"{cal_lx:+.2f}", p1=f"{cal_ly:+.2f}",
                p2=f"{cal_rx:+.2f}", p3=f"{cal_ry:+.2f}",
                p4=int(round(lt_eff * 100)), p5=int(round(rt_eff * 100)),
                p6=f"{drift_pct:.1f}",
            )
            right_txt = (
                t("cc_input_controllers.bp_footer_hint_live")
                if (self.dev and self.dev.get("readable"))
                else t("cc_input_controllers.bp_footer_hint_prev")
            )
        _draw_cairo_footer(cr, bx, bw, stage_h, footer_h, left_txt, pal["focus_val"], right_txt, pal, surf_hex, f_micro)

        cr.restore()
        return False


class _ResponseCurveMixin:
    """Pure-Cairo Dual Transfer Graph: Stick Response Curve (left) + Trigger Actuation Ramp (right)."""

    def __init__(
        self,
        profile: dict,
        widget_width: int = 760,
        fixed_height: int = 128,
        inset_x: int = 14,
        font_px: float = 11.0,
    ):
        super().__init__()
        self.profile = dict(profile)
        self.widget_width = max(300, int(widget_width))
        self.widget_height = max(106, int(fixed_height))
        self.inset_x = max(4, int(inset_x))
        self.font_px = max(8.5, float(font_px))
        self.footer_h = 26
        self.set_size_request(self.widget_width, self.widget_height)
        self.connect("size-allocate", self._on_size_allocate)
        self.connect("draw", self._on_draw)

    def _on_size_allocate(self, _widget, alloc) -> None:
        if alloc.width > (2 * self.inset_x + 40) and abs(alloc.width - self.widget_width) > 1:
            self.widget_width = alloc.width
            self.queue_draw()

    def _on_draw(self, _widget, cr) -> bool:
        from gi.repository import Pango, PangoCairo

        alloc = self.get_allocation()
        w = max(300.0, float(alloc.width or self.widget_width))
        h = max(106.0, float(alloc.height or self.widget_height))
        bx = float(self.inset_x)
        bw = max(200.0, w - 2.0 * bx)
        footer_h = float(self.footer_h)
        stage_h = h - footer_h

        pal = get_semantic_palette()
        surf_hex = str((THEME.get("colors") or {}).get("bg_panel") or pal["structure_bg"])
        font_family = str((THEME.get("typography") or {}).get("font_family", "monospace") or "monospace")

        rounded_rect_path(cr, bx + 0.5, 0.5, bw - 1.0, h - 1.0, 6.0)
        cr.set_source_rgba(*hex_to_rgba_f(pal["structure_bg"], 0.62))
        cr.fill_preserve()
        cr.set_source_rgba(*hex_to_rgba_f(pal["structure"], 0.90))
        cr.set_line_width(1.0)
        cr.stroke()

        cr.save()
        rounded_rect_path(cr, bx + 1.0, 1.0, bw - 2.0, h - 2.0, 5.5)
        cr.clip()

        base_pt = float((THEME.get("typography") or {}).get("font_size", 11) or 11)
        f_micro = Pango.FontDescription()
        f_micro.set_family(font_family)
        f_micro.set_size(int(round(base_pt * 0.84 * Pango.SCALE)))

        # Split into 2 crisp equal plot areas
        pad_x = 18.0
        gap_mid = 26.0
        plot_w = max(80.0, (bw - pad_x * 2.0 - gap_mid) * 0.5)
        plot_y = 25.0
        plot_h = max(42.0, stage_h - plot_y - 10.0)
        left_x = bx + pad_x
        right_x = left_x + plot_w + gap_mid

        def _draw_grid(px0: float, title: str, tag: str, tag_col: str) -> None:
            tl = PangoCairo.create_layout(cr)
            tl.set_font_description(f_micro)
            tl.set_text(title, -1)
            cr.set_source_rgba(*hex_to_rgba_f(pal["schema"], 0.92))
            cr.move_to(px0, 5.0)
            PangoCairo.show_layout(cr, tl)

            gl = PangoCairo.create_layout(cr)
            gl.set_font_description(f_micro)
            gl.set_text(tag, -1)
            _, ge = gl.get_pixel_extents()
            cr.set_source_rgba(*hex_to_rgba_f(tag_col, 0.92))
            cr.move_to(px0 + plot_w - ge.width, 5.0)
            PangoCairo.show_layout(cr, gl)

            cr.set_source_rgba(*hex_to_rgba_f(surf_hex, 0.85))
            cr.rectangle(px0, plot_y, plot_w, plot_h)
            cr.fill()
            cr.set_source_rgba(*hex_to_rgba_f(pal["structure"], 0.72))
            cr.set_line_width(1.0)
            cr.rectangle(px0 + 0.5, plot_y + 0.5, plot_w - 1.0, plot_h - 1.0)
            cr.stroke()

            # 4x4 dashed grid + 1:1 linear diagonal reference
            cr.save()
            cr.set_source_rgba(*hex_to_rgba_f(pal["structure"], 0.35))
            cr.set_dash([2.0, 3.0])
            cr.set_line_width(0.8)
            for frac in (0.25, 0.50, 0.75):
                gx = px0 + plot_w * frac
                gy = plot_y + plot_h * frac
                cr.move_to(gx, plot_y)
                cr.line_to(gx, plot_y + plot_h)
                cr.move_to(px0, gy)
                cr.line_to(px0 + plot_w, gy)
            cr.stroke()
            cr.set_source_rgba(*hex_to_rgba_f(pal["schema"], 0.38))
            cr.move_to(px0, plot_y + plot_h)
            cr.line_to(px0 + plot_w, plot_y)
            cr.stroke()
            cr.restore()

        inner_dz = float(self.profile["inner_dz"]) / 100.0
        outer_th = float(self.profile["outer_th"]) / 100.0
        trig_cut = float(self.profile["trigger_cut"]) / 100.0
        curve = str(self.profile["curve"])

        # LEFT PLOT: Stick Response Curve
        _draw_grid(
            left_x,
            t("cc_input_controllers.curve_graph_title"),
            t("cc_input_controllers.bp_deadzone", p0=self.profile["inner_dz"]),
            pal["attention"],
        )
        # Shaded inner deadzone & outer saturation bands
        dz_w = plot_w * inner_dz
        cr.set_source_rgba(*hex_to_rgba_f(pal["attention_bg"], 0.65))
        cr.rectangle(left_x + 1.0, plot_y + 1.0, max(0.0, dz_w - 1.0), plot_h - 2.0)
        cr.fill()
        if outer_th < 0.999:
            sat_x = left_x + plot_w * outer_th
            cr.set_source_rgba(*hex_to_rgba_f(pal["vitality_bg"], 0.55))
            cr.rectangle(sat_x, plot_y + 1.0, max(0.0, left_x + plot_w - sat_x - 1.0), plot_h - 2.0)
            cr.fill()
            cr.save()
            cr.set_dash([2.5, 2.5])
            cr.set_source_rgba(*hex_to_rgba_f(pal["vitality"], 0.80))
            cr.set_line_width(1.0)
            cr.move_to(sat_x, plot_y)
            cr.line_to(sat_x, plot_y + plot_h)
            cr.stroke()
            cr.restore()
        cr.save()
        cr.set_dash([2.5, 2.5])
        cr.set_source_rgba(*hex_to_rgba_f(pal["attention"], 0.85))
        cr.set_line_width(1.0)
        cr.move_to(left_x + dz_w, plot_y)
        cr.line_to(left_x + dz_w, plot_y + plot_h)
        cr.stroke()
        cr.restore()

        # Sampled active stick response curve (intent cyan)
        cr.save()
        cr.set_source_rgba(*hex_to_rgba_f(pal["intent"], 0.98))
        cr.set_line_width(2.0)
        for i in range(61):
            u = i / 60.0
            y_val = eval_response_curve(u, inner_dz, outer_th, curve)
            px = left_x + u * plot_w
            py = plot_y + plot_h - y_val * (plot_h - 2.0) - 1.0
            if i == 0:
                cr.move_to(px, py)
            else:
                cr.line_to(px, py)
        cr.stroke()
        cr.restore()

        # RIGHT PLOT: Trigger Actuation Ramp
        _draw_grid(
            right_x,
            t("cc_input_controllers.trigger_graph_title"),
            t("cc_input_controllers.bp_haircut", p0=self.profile["trigger_cut"]),
            pal["attention"],
        )
        cut_px = right_x + plot_w * trig_cut
        if trig_cut < 0.999:
            cr.set_source_rgba(*hex_to_rgba_f(pal["attention_bg"], 0.52))
            cr.rectangle(cut_px, plot_y + 1.0, max(0.0, right_x + plot_w - cut_px - 1.0), plot_h - 2.0)
            cr.fill()
        cr.save()
        cr.set_dash([2.5, 2.5])
        cr.set_source_rgba(*hex_to_rgba_f(pal["attention"], 0.90))
        cr.set_line_width(1.1)
        cr.move_to(cut_px, plot_y)
        cr.line_to(cut_px, plot_y + plot_h)
        cr.stroke()
        cr.restore()

        cr.save()
        cr.set_source_rgba(*hex_to_rgba_f(pal["intent"], 0.98))
        cr.set_line_width(2.0)
        for i in range(51):
            u = i / 50.0
            y_val = eval_trigger_curve(u, trig_cut)
            px = right_x + u * plot_w
            py = plot_y + plot_h - y_val * (plot_h - 2.0) - 1.0
            if i == 0:
                cr.move_to(px, py)
            else:
                cr.line_to(px, py)
        cr.stroke()
        cr.restore()

        # Integrated Profile Footer Strip
        c_lbl = t(f"cc_input_controllers.curve_{curve}")
        foot_l = t(
            "cc_input_controllers.curve_footer_left",
            p0=c_lbl,
            p1=self.profile["inner_dz"],
            p2=self.profile["outer_th"],
            p3=self.profile["trigger_cut"],
        )
        foot_r = t("cc_input_controllers.curve_footer_right")
        _draw_cairo_footer(cr, bx, bw, stage_h, footer_h, foot_l, pal["focus_val"], foot_r, pal, surf_hex, f_micro)

        cr.restore()
        return False


_BLUEPRINT_CLS = None
_CURVE_CLS = None


def ControllerBlueprintWidget(*args, **kwargs):
    global _BLUEPRINT_CLS
    if _BLUEPRINT_CLS is None:
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk

        class _ControllerBlueprint(_ControllerBlueprintMixin, Gtk.DrawingArea):
            pass

        _BLUEPRINT_CLS = _ControllerBlueprint
    return _BLUEPRINT_CLS(*args, **kwargs)


def ResponseCurveWidget(*args, **kwargs):
    global _CURVE_CLS
    if _CURVE_CLS is None:
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk

        class _ResponseCurve(_ResponseCurveMixin, Gtk.DrawingArea):
            pass

        _CURVE_CLS = _ResponseCurve
    return _CURVE_CLS(*args, **kwargs)

