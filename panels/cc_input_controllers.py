#!/usr/bin/env python3
"""NC-UI Control Center — Gaming Controllers Hub (Xbox Accessories + Steam Input inspired)."""

import fcntl
import glob
import json
import math
import os
import re
import struct
import time
from core import probe
from core.config import CONFIG_DIR, THEME, _parse_rgba_components
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
    CONTROLLERS_FILE,
    MACRO_PAD_MODES,
    MOUSE_TARGET_TO_EVDEV,
    PAD_CONTROL_IDS,
    PAD_MAPPER_BIN,
    STICK_MODES,
    WHEEL_TARGETS,
    _detect_family,
    apply_stick_calibration,
    build_template_profile,
    compile_macro_events,
    eval_response_curve,
    eval_trigger_curve,
    format_action_short,
    get_mapper_runtime_status,
    is_pad_mapper_running,
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
    FeedbackBox,
    InlineField,
    InspectorCell,
    SelectPill,
    Slider,
    StateBadge,
    Txt,
    get_semantic_palette,
)
from ui.widgets import AsciiCard

CONTROLLERS_EXPORT_FILE = os.path.join(CONFIG_DIR, "controllers_export.json")
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


def _bitmask_has(hex_line: str, bit_num: int) -> bool:
    if not hex_line:
        return False
    words = hex_line.strip().split()
    word_bits = 64
    word_idx = bit_num // word_bits
    bit_pos = bit_num % word_bits
    if word_idx >= len(words):
        return False
    try:
        val = int(words[len(words) - 1 - word_idx], 16)
        return bool((val >> bit_pos) & 1)
    except ValueError:
        return False


def _read_sysfs_str(path: str) -> str:
    try:
        return open(path, "r", encoding="utf-8", errors="ignore").read().strip()
    except OSError:
        return ""


def _find_battery_for_device(uniq: str, phys: str, name: str) -> tuple[int | None, str]:
    uniq_l = (uniq or "").strip().lower()
    phys_l = (phys or "").strip().lower()
    name_l = (name or "").strip().lower()
    for ps in sorted(glob.glob("/sys/class/power_supply/*")):
        bn = os.path.basename(ps).lower()
        scope = _read_sysfs_str(os.path.join(ps, "scope")).lower()
        if scope and scope != "device":
            continue
        is_match = False
        if uniq_l and uniq_l in bn:
            is_match = True
        elif phys_l and any(tok in bn for tok in phys_l.split("/") if len(tok) >= 6):
            is_match = True
        elif any(k in bn for k in ("ps-controller", "dualsense", "dualshock", "xbox", "nintendo", "hidpp", "gamepad", "joystick")):
            if not name_l or any(w in bn for w in name_l.split() if len(w) >= 4) or "ps-controller" in bn or "xbox" in bn:
                is_match = True
        if is_match:
            cap_s = _read_sysfs_str(os.path.join(ps, "capacity"))
            st_s = _read_sysfs_str(os.path.join(ps, "status"))
            if cap_s.isdigit():
                return max(0, min(100, int(cap_s))), st_s
    return None, ""


def _find_led_paths(uniq: str, family: str) -> list[str]:
    found = []
    uniq_l = (uniq or "").strip().lower()
    for p in sorted(glob.glob("/sys/class/leds/*")):
        bn = os.path.basename(p).lower()
        if any(k in bn for k in ("::capslock", "::numlock", "::scrolllock", "::lan", "phy0", "kbd_backlight")):
            continue
        if (uniq_l and uniq_l in bn) or any(
            k in bn for k in ("rgb:indicator", "playstation", "dualsense", "dualshock", "xpad", "xbox", "nintendo", "player")
        ):
            found.append(p)
        elif family == "playstation" and ("red" in bn or "green" in bn or "blue" in bn) and "0005:" in bn:
            found.append(p)
    return found


def _scan_paired_bt_controllers(connected_uniqs: set[str]) -> list[dict]:
    if not probe.has_bin("bluetoothctl") or os.environ.get("NC_UI_OFFSCREEN") == "1":
        return []
    ok, out = probe.run_cmd(["bluetoothctl", "devices"], timeout=0.9)
    if not ok or not out:
        return []
    out_list = []
    for ln in out.splitlines():
        m = re.match(r"^Device\s+([0-9A-Fa-f:]{17})\s+(.+)$", ln.strip())
        if not m:
            continue
        mac, name = m.group(1).upper(), m.group(2).strip()
        if mac.lower() in connected_uniqs:
            continue
        nl = name.lower()
        is_gp_name = any(k in nl for k in ("controller", "gamepad", "xbox", "dualsense", "dualshock", "wireless controller", "pro controller", "8bitdo", "joy-con", "steam"))
        ok_i, info = probe.run_cmd(["bluetoothctl", "info", mac], timeout=0.6)
        is_gp_icon = bool(ok_i and ("Icon: input-gaming" in info or "UUID: Human Interface Device" in info and is_gp_name))
        if not (is_gp_name or is_gp_icon):
            continue
        is_conn = bool(ok_i and "Connected: yes" in info)
        bat_m = re.search(r"Battery Percentage:\s*0x[0-9a-fA-F]+\s*\((\d+)\)", info or "")
        bat_pct = int(bat_m.group(1)) if bat_m else None
        mod_m = re.search(r"Modalias:\s*\w+:v([0-9A-Fa-f]{4})p([0-9A-Fa-f]{4})", info or "")
        bt_vid, bt_pid = (mod_m.group(1).lower(), mod_m.group(2).lower()) if mod_m else ("", "")
        bt_fam = _detect_family(name, bt_vid, bt_pid)
        out_list.append({
            "id": f"bt_{mac.replace(':', '').lower()}",
            "name": name,
            "vendor": bt_vid,
            "product": bt_pid,
            "family": bt_fam,
            "bus": "bt",
            "evdev": "",
            "jsdev": "",
            "uniq": mac,
            "bt_mac": mac,
            "connected": is_conn,
            "readable": False,
            "writable": False,
            "has_rumble": False,
            "has_gyro": False,
            "has_paddles": bt_fam in ("xbox_elite", "steam"),
            "leds": [],
            "battery": bat_pct,
            "battery_status": "",
        })
    return out_list


def discover_controllers(force: bool = False) -> list[dict]:
    """Discover real USB/BT gamepads from /proc/bus/input/devices, sysfs, and bluetoothctl."""
    now = time.monotonic()
    if not force and (now - _DISCOVERY_CACHE["ts"]) < 2.0 and _DISCOVERY_CACHE["devices"] is not None:
        return list(_DISCOVERY_CACHE["devices"])

    raw_blocks: list[dict] = []
    if os.path.exists("/proc/bus/input/devices"):
        try:
            txt = open("/proc/bus/input/devices", "r", encoding="utf-8", errors="ignore").read()
            for blk in txt.strip().split("\n\n"):
                info = {"bus": "", "vendor": "", "product": "", "name": "", "phys": "", "uniq": "", "handlers": [], "key": "", "abs": "", "ff": "", "prop": ""}
                for ln in blk.splitlines():
                    if ln.startswith("I:"):
                        m = re.search(r"Bus=([0-9a-fA-F]+)\s+Vendor=([0-9a-fA-F]+)\s+Product=([0-9a-fA-F]+)", ln)
                        if m:
                            info["bus"], info["vendor"], info["product"] = m.group(1).lower(), m.group(2).lower(), m.group(3).lower()
                    elif ln.startswith("N: Name="):
                        info["name"] = ln.split("=", 1)[1].strip().strip('"')
                    elif ln.startswith("P: Phys="):
                        info["phys"] = ln.split("=", 1)[1].strip()
                    elif ln.startswith("U: Uniq="):
                        info["uniq"] = ln.split("=", 1)[1].strip()
                    elif ln.startswith("H: Handlers="):
                        info["handlers"] = ln.split("=", 1)[1].strip().split()
                    elif ln.startswith("B: KEY="):
                        info["key"] = ln.split("=", 1)[1].strip()
                    elif ln.startswith("B: ABS="):
                        info["abs"] = ln.split("=", 1)[1].strip()
                    elif ln.startswith("B: FF="):
                        info["ff"] = ln.split("=", 1)[1].strip()
                    elif ln.startswith("B: PROP="):
                        info["prop"] = ln.split("=", 1)[1].strip()
                if info["name"]:
                    raw_blocks.append(info)
        except OSError:
            pass

    motion_uniqs = {
        b["uniq"].lower()
        for b in raw_blocks
        if b["uniq"] and ("motion" in b["name"].lower() or "accel" in b["name"].lower() or _bitmask_has(b["prop"], 6))
    }

    devices: list[dict] = []
    connected_uniqs: set[str] = set()
    for b in raw_blocks:
        nl = b["name"].lower()
        if any(skip in nl for skip in ("touchpad", "motion sensors", "consumer control", "keyboard", "mouse")):
            continue
        has_js = any(h.startswith("js") for h in b["handlers"])
        has_gp_btn = _bitmask_has(b["key"], _BTN_SOUTH) or _bitmask_has(b["key"], 0x120)
        if not (has_js or has_gp_btn):
            continue
        ev_h = next((h for h in b["handlers"] if h.startswith("event")), "")
        js_h = next((h for h in b["handlers"] if h.startswith("js")), "")
        ev_path = f"/dev/input/{ev_h}" if ev_h else ""
        js_path = f"/dev/input/{js_h}" if js_h else ""
        bus_kind = {"0003": "usb", "0005": "bt", "0006": "virtual"}.get(b["bus"], "usb")
        fam = _detect_family(b["name"], b["vendor"], b["product"])
        bat_pct, bat_st = _find_battery_for_device(b["uniq"], b["phys"], b["name"])
        leds = _find_led_paths(b["uniq"], fam)
        uniq_l = b["uniq"].lower()
        if uniq_l:
            connected_uniqs.add(uniq_l)
        dev_id = f"{b['vendor']}_{b['product']}_{uniq_l.replace(':', '') or ev_h or 'pad'}"
        bt_mac = b["uniq"].upper() if (bus_kind == "bt" and re.match(r"^[0-9a-fA-F:]{17}$", b["uniq"])) else ""
        # Enrich connected BT controllers (e.g. Xbox BLE uhid) with BlueZ GATT battery & real VID/PID
        if bt_mac and (bat_pct is None or b["vendor"] == "0000") and probe.has_bin("bluetoothctl") and os.environ.get("NC_UI_OFFSCREEN") != "1":
            ok_bt, bt_info = probe.run_cmd(["bluetoothctl", "info", bt_mac], timeout=0.6)
            if ok_bt and bt_info:
                if bat_pct is None:
                    bm = re.search(r"Battery Percentage:\s*0x[0-9a-fA-F]+\s*\((\d+)\)", bt_info)
                    if bm:
                        bat_pct = max(0, min(100, int(bm.group(1))))
                if b["vendor"] == "0000":
                    mm = re.search(r"Modalias:\s*\w+:v([0-9A-Fa-f]{4})p([0-9A-Fa-f]{4})", bt_info)
                    if mm:
                        b["vendor"], b["product"] = mm.group(1).lower(), mm.group(2).lower()
                        fam = _detect_family(b["name"], b["vendor"], b["product"])
        has_paddles = fam in ("xbox_elite", "steam") or any(_bitmask_has(b["key"], pc) for pc in _PADDLE_EVDEV_CODES)
        devices.append({
            "id": dev_id,
            "name": b["name"],
            "vendor": b["vendor"],
            "product": b["product"],
            "family": fam,
            "bus": bus_kind,
            "evdev": ev_path,
            "jsdev": js_path,
            "uniq": b["uniq"],
            "bt_mac": bt_mac,
            "connected": True,
            "readable": bool(ev_path and os.access(ev_path, os.R_OK)),
            "writable": bool(ev_path and os.access(ev_path, os.W_OK)),
            "has_rumble": _bitmask_has(b["ff"], _FF_RUMBLE),
            "has_gyro": bool(uniq_l and uniq_l in motion_uniqs),
            "has_paddles": has_paddles,
            "leds": leds,
            "battery": bat_pct,
            "battery_status": bat_st,
        })

    devices.extend(_scan_paired_bt_controllers(connected_uniqs))
    _DISCOVERY_CACHE["ts"] = now
    _DISCOVERY_CACHE["devices"] = devices
    return list(devices)


def _norm_profile(raw: dict | None, default_layout: str = "xbox") -> dict:
    p = dict(_DEFAULT_PROFILE)
    p["layout"] = default_layout if default_layout in _LAYOUTS else "xbox"
    if isinstance(raw, dict):
        p.update(raw)
    if p["layout"] not in _LAYOUTS:
        p["layout"] = "xbox"
    if p["curve"] not in _CURVES:
        p["curve"] = "linear"
    if p["led_mode"] not in _LED_MODES:
        p["led_mode"] = "theme"
    if p["gyro_mode"] not in _GYRO_MODES:
        p["gyro_mode"] = "off"
    p["inner_dz"] = max(0, min(40, int(round(float(p.get("inner_dz", 8))))))
    p["outer_th"] = max(60, min(100, int(round(float(p.get("outer_th", 95))))))
    p["trigger_cut"] = max(10, min(100, int(round(float(p.get("trigger_cut", 85))))))
    p["rumble_pct"] = max(0, min(100, int(round(float(p.get("rumble_pct", 80))))))
    for k in ("inv_ly", "inv_ry", "swap_sticks"):
        p[k] = bool(p.get(k, False))
    for k in ("cal_lx", "cal_ly", "cal_rx", "cal_ry"):
        p[k] = max(-0.5, min(0.5, round(float(p.get(k, 0.0) or 0.0), 3)))
    try:
        slot = int(p.get("elite_slot", 1))
    except (TypeError, ValueError):
        slot = 1
    p["elite_slot"] = slot if slot in _ELITE_SLOTS else 1
    for pk in _PADDLE_KEYS:
        act = str(p.get(pk, _DEFAULT_PROFILE[pk]) or "none").lower()
        p[pk] = act if act in _PADDLE_ACTIONS else _DEFAULT_PROFILE[pk]
    if p.get("trigger_lock") not in _TRIGGER_LOCK_IDS:
        p["trigger_lock"] = "full"
    if p.get("stick_calc") not in _STICK_CALCS:
        p["stick_calc"] = "radial"
    return p


def _load_saved_profiles() -> dict[str, dict]:
    if not os.path.exists(CONTROLLERS_FILE):
        return {}
    try:
        data = json.load(open(CONTROLLERS_FILE, "r", encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_profiles(profiles: dict[str, dict]) -> bool:
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        payload = json.dumps(profiles, indent=2)
        with open(CONTROLLERS_FILE, "w", encoding="utf-8") as f:
            f.write(payload)
        return open(CONTROLLERS_FILE, "r", encoding="utf-8").read() == payload
    except OSError:
        return False


def _active_key_and_device(devices: list[dict]) -> tuple[str, dict | None]:
    sel_id = str(STATE.get("controllers", "selected_id", "") or "")
    dev = next((d for d in devices if d["id"] == sel_id), None)
    if dev is None and devices:
        dev = next((d for d in devices if d["connected"]), devices[0])
    return (dev["id"] if dev else "default"), dev


def _get_profile_draft(key: str, default_layout: str = "xbox") -> dict:
    drafts = STATE.get("controllers", "drafts", {})
    if isinstance(drafts, dict) and key in drafts:
        return _norm_profile(drafts[key], default_layout)
    saved = _load_saved_profiles().get(key)
    return _norm_profile(saved, default_layout)


def _set_profile_draft(key: str, default_layout: str = "xbox", **kw) -> dict:
    cur = _get_profile_draft(key, default_layout)
    cur.update(kw)
    norm = _norm_profile(cur, default_layout)
    drafts = dict(STATE.get("controllers", "drafts", {}) or {})
    drafts[key] = norm
    STATE.set("controllers", "drafts", drafts)
    return norm


def _is_profile_dirty(key: str, draft: dict, default_layout: str = "xbox") -> bool:
    saved = _load_saved_profiles().get(key)
    base = _norm_profile(saved, default_layout)
    return _norm_profile(draft, default_layout) != base


def _set_ctrl_feedback(ok: bool, msg: str) -> None:
    STATE.set("controllers", "fb", {"ok": bool(ok), "msg": str(msg), "ts": time.time()})


def _feedback_cards() -> list:
    fb = STATE.get("controllers", "fb", {})
    if isinstance(fb, dict) and fb.get("msg") and (time.time() - float(fb.get("ts", 0))) < 15.0:
        return [FeedbackBox(t("cc_input_controllers.status_title"), bool(fb["ok"]), fb["msg"])]
    return []


def _send_kernel_rumble(dev: dict | None, side: str, pct: int) -> tuple[bool, str]:
    _LIVE_INPUT["rumble_until"] = time.monotonic() + 0.50
    _LIVE_INPUT["rumble_side"] = side
    if not dev or not dev.get("evdev") or not dev.get("connected"):
        return False, t("cc_input_controllers.fb_rumble_no_hw")
    if not dev.get("has_rumble"):
        return False, t("cc_input_controllers.fb_rumble_unsupported", p0=dev.get("name", "Gamepad"))
    ev_path = dev["evdev"]
    if not os.access(ev_path, os.W_OK):
        return False, t("cc_input_controllers.fb_rumble_no_perm", p0=ev_path)
    scale = max(0.0, min(1.0, float(pct) / 100.0))
    strong = int(round(0xFFFF * scale)) if side in ("left", "dual") else 0
    weak = int(round(0xFFFF * scale)) if side in ("right", "dual") else 0
    duration_ms = 420 if side != "dual" else 520
    try:
        fd = os.open(ev_path, os.O_RDWR | os.O_NONBLOCK)
        eff = bytearray(struct.pack("@HhHHHHH2xHH28x", _FF_RUMBLE, -1, 0, 0, 0, duration_ms, 0, strong, weak))
        fcntl.ioctl(fd, _EVIOCSFF, eff)
        eff_id = struct.unpack_from("@h", eff, 2)[0]
        now = time.time()
        os.write(fd, _EV_STRUCT.pack(int(now), int((now % 1.0) * 1_000_000), _EV_FF, eff_id, 1))
        from gi.repository import GLib

        def _cleanup(fdesc=fd, eid=eff_id):
            try:
                fcntl.ioctl(fdesc, _EVIOCRMFF, struct.pack("@i", eid))
            except OSError:
                pass
            try:
                os.close(fdesc)
            except OSError:
                pass
            return False

        GLib.timeout_add(duration_ms + 120, _cleanup)
        return True, t("cc_input_controllers.fb_rumble_ok", p0=side, p1=pct)
    except OSError as e:
        return False, str(e)


def _apply_led_mode(dev: dict | None, mode: str) -> tuple[bool, str]:
    leds = (dev or {}).get("leds") or []
    if not leds:
        return True, t("cc_input_controllers.fb_led_saved", p0=mode)
    pal = get_semantic_palette()
    r, g, b, _ = _parse_rgba_components(pal["intent"])
    applied = False
    for lp in leds:
        max_b = int(_read_sysfs_str(os.path.join(lp, "max_brightness")) or "255")
        b_path = os.path.join(lp, "brightness")
        mi_path = os.path.join(lp, "multi_intensity")
        try:
            if mode == "off":
                if os.access(b_path, os.W_OK):
                    open(b_path, "w", encoding="utf-8").write("0")
                    applied = True
            else:
                tgt_b = max(1, int(round(max_b * (0.30 if mode == "low" else 1.0))))
                if os.access(mi_path, os.W_OK):
                    if mode == "theme":
                        open(mi_path, "w", encoding="utf-8").write(f"{r} {g} {b}")
                    else:
                        open(mi_path, "w", encoding="utf-8").write(f"{tgt_b} {tgt_b} {tgt_b}")
                    applied = True
                if os.access(b_path, os.W_OK):
                    open(b_path, "w", encoding="utf-8").write(str(tgt_b))
                    applied = True
        except OSError:
            continue
    if applied:
        return True, t("cc_input_controllers.fb_led_applied", p0=mode)
    return True, t("cc_input_controllers.fb_led_saved", p0=mode)


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
        self.footer_h = 24
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
        cr.set_source_rgba(*_hex_rgba(pal["schema"], 0.90))
        cr.move_to(cx - te.width * 0.5, cy - r - te.height - 7.0)
        PangoCairo.show_layout(cr, tl)

        # Outer circular scope face
        sc_ctrl = "l_stick" if scope_id == "scope_ls" else "r_stick"
        s_col, s_lw = self._bind_col_lw(sc_ctrl, clicked, pal["focus"] if is_hov else pal["structure"], 1.2 if is_hov else 1.0, pal)
        cr.save()
        cr.new_sub_path()
        cr.arc(cx, cy, r, 0.0, 2.0 * math.pi)
        cr.set_source_rgba(*_hex_rgba(surf_hex, 0.88))
        cr.fill_preserve()
        cr.set_source_rgba(*_hex_rgba(s_col, 0.95))
        cr.set_line_width(s_lw)
        cr.stroke()

        # Crosshair axes
        cr.set_source_rgba(*_hex_rgba(pal["structure"], 0.55))
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
        cr.set_source_rgba(*_hex_rgba(pal["vitality"], 0.52))
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
        cr.set_source_rgba(*_hex_rgba(pal["attention_bg"], 0.72))
        cr.fill_preserve()
        cr.save()
        cr.set_dash([2.5, 2.5])
        cr.set_source_rgba(*_hex_rgba(pal["attention"], 0.85))
        cr.set_line_width(1.0)
        cr.stroke()
        cr.restore()

        # Raw dot if shifted from calibrated
        if math.hypot(raw_x, raw_y) > 0.02:
            cr.new_sub_path()
            cr.arc(cx + raw_x * r, cy + raw_y * r, 2.8, 0.0, 2.0 * math.pi)
            cr.set_source_rgba(*_hex_rgba(pal["schema"], 0.72))
            cr.fill()

        # Calibrated stick vector + glowing cyan tip
        vx, vy = cx + cal_x * r, cy + cal_y * r
        if math.hypot(cal_x, cal_y) > 0.01:
            cr.set_source_rgba(*_hex_rgba(pal["intent"], 0.90))
            cr.set_line_width(1.8)
            cr.move_to(cx, cy)
            cr.line_to(vx, vy)
            cr.stroke()
        cr.new_sub_path()
        cr.arc(vx, vy, 4.2 if clicked else 3.4, 0.0, 2.0 * math.pi)
        cr.set_source_rgba(*_hex_rgba(pal["intent"], 1.0))
        cr.fill()
        cr.restore()

        # Numeric readout below scope
        rd = PangoCairo.create_layout(cr)
        rd.set_font_description(f_micro)
        rd.set_text(f"{cal_x:+.2f}, {cal_y:+.2f}", -1)
        _, re_e = rd.get_pixel_extents()
        cr.set_source_rgba(*_hex_rgba(pal["focus_val"] if math.hypot(cal_x, cal_y) > 0.01 else pal["schema"], 0.88))
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
        cr.set_source_rgba(*_hex_rgba(pal["intent"] if eff_val > 0.02 else pal["schema"], 0.92))
        cr.move_to(x + (w - te.width) * 0.5, y - te.height - 6.0)
        PangoCairo.show_layout(cr, tl)

        # Recessed vertical bar
        tr_ctrl = "lt" if bar_id == "bar_lt" else "rt"
        t_col, t_lw = self._bind_col_lw(tr_ctrl, eff_val > 0.02, pal["focus"] if is_hov else pal["structure"], 1.1, pal)
        _rounded_rect(cr, x + 0.5, y + 0.5, w - 1.0, h - 1.0, 3.0)
        cr.set_source_rgba(*_hex_rgba(surf_hex, 0.90))
        cr.fill_preserve()
        cr.set_source_rgba(*_hex_rgba(t_col, 0.94))
        cr.set_line_width(t_lw)
        cr.stroke()

        # Fill proportional to calibrated trigger output
        if eff_val > 0.005:
            fh = max(2.0, (h - 2.0) * eff_val)
            _rounded_rect(cr, x + 1.5, y + h - 1.0 - fh, w - 3.0, fh, 2.0)
            cr.set_source_rgba(*_hex_rgba(pal["intent"], 0.90))
            cr.fill()

        # Hair-trigger cutoff marker (attention yellow) + Xbox Elite 3-stop lock notches
        if self.profile.get("layout") == "xbox_elite":
            cr.set_source_rgba(*_hex_rgba(pal["schema"], 0.65))
            cr.set_line_width(1.0)
            for stop_pct in (25, 55, 100):
                sy_t = y + h - (h * (stop_pct / 100.0))
                cr.move_to(x + w + 1.0, sy_t)
                cr.line_to(x + w + 4.0, sy_t)
            cr.stroke()
        cut_frac = float(self.profile["trigger_cut"]) / 100.0
        cut_y = y + h - (h * cut_frac)
        cr.set_source_rgba(*_hex_rgba(pal["attention"], 0.95))
        cr.set_line_width(1.6)
        cr.move_to(x - 2.5, cut_y)
        cr.line_to(x + w + 2.5, cut_y)
        cr.stroke()

        # Percent below bar
        pl = PangoCairo.create_layout(cr)
        pl.set_font_description(f_micro)
        pl.set_text(f"{int(round(eff_val * 100))}%", -1)
        _, pe = pl.get_pixel_extents()
        cr.set_source_rgba(*_hex_rgba(pal["focus_val"] if eff_val > 0.02 else pal["schema"], 0.86))
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
            _rounded_rect(cr, bx0, by0, bw0, bh0, 6.0 * scale)
            cr.set_source_rgba(*_hex_rgba(pal["intent_bg"] if pressed else surf_hex, 0.92))
            cr.fill_preserve()
            b_col, b_lw = self._bind_col_lw(b_id, pressed, pal["structure"], 1.0, pal)
            cr.set_source_rgba(*_hex_rgba(b_col, 0.95))
            cr.set_line_width(b_lw)
            cr.stroke()
            bl = PangoCairo.create_layout(cr)
            bl.set_font_description(f_micro)
            bl.set_text(b_lbl, -1)
            _, be = bl.get_pixel_extents()
            cr.set_source_rgba(*_hex_rgba(pal["focus"] if pressed else pal["schema"], 0.95))
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

        cr.set_source_rgba(*_hex_rgba(surf_hex, 0.86))
        cr.fill_preserve()
        cr.set_source_rgba(*_hex_rgba(pal["attention"] if rumbling else pal["structure"], 0.96))
        cr.set_line_width(1.6 if rumbling else 1.35)
        cr.stroke()

        # Subtle rumble pulse arcs on grips when testing motors
        if rumbling:
            for side_tag, sgn in (("left", -1.0), ("right", 1.0)):
                if r_side in (side_tag, "dual"):
                    gx, gy = _p(sgn * 138.0, 26.0)
                    cr.set_source_rgba(*_hex_rgba(pal["attention"], 0.85))
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
            _rounded_rect(cr, tpx, tpy, tpw, tph, 5.0 * scale)
            cr.set_source_rgba(*_hex_rgba(pal["structure_bg"], 0.85))
            cr.fill_preserve()
            cr.set_source_rgba(*_hex_rgba(pal["structure"], 0.95))
            cr.set_line_width(1.0)
            cr.stroke()
            if led_alpha > 0.0:
                cr.set_source_rgba(*_hex_rgba(led_col, led_alpha))
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
        cr.set_source_rgba(*_hex_rgba(pal["intent_bg"] if g_press else pal["structure_bg"], 0.92))
        cr.fill_preserve()
        cr.set_source_rgba(*_hex_rgba(led_col if (g_press or led_alpha > 0.0) else pal["structure"], 0.95 if g_press else max(0.45, led_alpha)))
        cr.set_line_width(1.6 if g_press else 1.2)
        cr.stroke()
        cr.new_sub_path()
        cr.arc(gx, gy, 3.2 * scale, 0.0, 2.0 * math.pi)
        cr.set_source_rgba(*_hex_rgba(led_col if led_alpha > 0.0 else pal["schema"], 0.95 if g_press else max(0.35, led_alpha)))
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
            cr.set_source_rgba(*_hex_rgba(pal["intent_bg"] if bp else pal["structure_bg"], 0.92))
            cr.fill_preserve()
            vc_col, vc_lw = self._bind_col_lw(b_id, bp, pal["structure"], 1.0, pal)
            cr.set_source_rgba(*_hex_rgba(vc_col, 0.95))
            cr.set_line_width(vc_lw)
            cr.stroke()

        if layout == "xbox":
            # Series X|S Share pill tucked neatly under Guide button
            sh_x, sh_y = _p(-7.0, -7.0)
            sh_w, sh_h = 14.0 * scale, 6.0 * scale
            sh_p = "share" in btns
            self._hit_targets.append({"id": "btn_share", "shape": "rect", "x": sh_x - 2.0, "y": sh_y - 2.0, "w": sh_w + 4.0, "h": sh_h + 4.0})
            _rounded_rect(cr, sh_x, sh_y, sh_w, sh_h, 2.5 * scale)
            cr.set_source_rgba(*_hex_rgba(pal["intent_bg"] if sh_p else pal["structure_bg"], 0.92))
            cr.fill_preserve()
            cr.set_source_rgba(*_hex_rgba(pal["intent"] if sh_p else pal["structure"], 0.90))
            cr.set_line_width(1.2 if sh_p else 0.9)
            cr.stroke()
        elif layout == "xbox_elite":
            # Xbox Elite Series 2 Profile switch pill + 3 Onboard Slot LEDs beneath Guide button
            pr_x, pr_y = _p(-8.0, -7.5)
            pr_w, pr_h = 16.0 * scale, 6.5 * scale
            pr_p = "profile" in btns
            self._hit_targets.append({"id": "btn_profile", "shape": "rect", "x": pr_x - 2.0, "y": pr_y - 2.0, "w": pr_w + 4.0, "h": pr_h + 4.0})
            _rounded_rect(cr, pr_x, pr_y, pr_w, pr_h, 2.5 * scale)
            cr.set_source_rgba(*_hex_rgba(pal["intent_bg"] if pr_p else pal["structure_bg"], 0.92))
            cr.fill_preserve()
            cr.set_source_rgba(*_hex_rgba(pal["intent"] if pr_p else pal["schema"], 0.92))
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
                cr.set_source_rgba(*_hex_rgba(pal["intent"] if lit else pal["structure"], 0.98 if lit else 0.50))
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
                _rounded_rect(cr, px0, py0, pw0, ph0, 4.0 * scale)
                cr.set_source_rgba(*_hex_rgba(pal["intent_bg"] if pp else pal["structure_bg"], 0.92))
                cr.fill_preserve()
                b_col_base = pal["schema"] if is_mapped else pal["structure"]
                b_col, b_lw = self._bind_col_lw(pid, pp, b_col_base, 1.0, pal)
                cr.set_source_rgba(*_hex_rgba(b_col, 0.95 if (pp or pid in self.mapped_ctrls or pid == self.selected_ctrl) else (0.85 if is_mapped else 0.60)))
                cr.set_line_width(b_lw)
                cr.stroke()
                pl_t = PangoCairo.create_layout(cr)
                pl_t.set_font_description(f_micro)
                pl_t.set_text(plbl, -1)
                _, pe_t = pl_t.get_pixel_extents()
                cr.set_source_rgba(*_hex_rgba(pal["focus"] if pp else (pal["focus_val"] if is_mapped else pal["inactive"]), 0.92))
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
                _rounded_rect(cr, px0, py0, pw0, ph0, 4.0 * scale)
                cr.set_source_rgba(*_hex_rgba(pal["intent_bg"] if pp else pal["structure_bg"], 0.90))
                cr.fill_preserve()
                cr.set_source_rgba(*_hex_rgba(pal["intent"] if pp else pal["structure"], 0.85))
                cr.set_line_width(1.3 if pp else 0.9)
                cr.stroke()
                pl_t = PangoCairo.create_layout(cr)
                pl_t.set_font_description(f_micro)
                pl_t.set_text(plbl, -1)
                _, pe_t = pl_t.get_pixel_extents()
                cr.set_source_rgba(*_hex_rgba(pal["focus"] if pp else pal["schema"], 0.88))
                cr.move_to(px0 + (pw0 - pe_t.width) * 0.5, py0 + (ph0 - pe_t.height) * 0.5)
                PangoCairo.show_layout(cr, pl_t)

            # Left Circular Haptic Trackpad (engraved cross D-Pad dish)
            lpx, lpy = _p(-76.0, -24.0)
            pr = 24.5 * scale
            lpad_p = ("lpad_click" in btns) or d_up or d_dn or d_lt or d_rt
            self._hit_targets.append({"id": "btn_lpad_click", "shape": "circle", "cx": lpx, "cy": lpy, "r": pr})
            cr.new_sub_path()
            cr.arc(lpx, lpy, pr, 0.0, 2.0 * math.pi)
            cr.set_source_rgba(*_hex_rgba(pal["intent_bg"] if lpad_p else pal["structure_bg"], 0.92))
            cr.fill_preserve()
            cr.set_source_rgba(*_hex_rgba(pal["intent"] if lpad_p else pal["structure"], 0.95))
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
                _rounded_rect(cr, qx, qy, qw, qh, 2.0 * scale)
                cr.set_source_rgba(*_hex_rgba(pal["intent"] if qact else pal["structure"], 0.92 if qact else 0.45))
                cr.fill()

            # Right Circular Haptic Trackpad (concave dish with concentric ring + live touch cursor)
            rpx, rpy = _p(76.0, -24.0)
            rpad_p = ("r3" in btns) or math.hypot(rs_xy[0], rs_xy[1]) > 0.04
            self._hit_targets.append({"id": "btn_r3", "shape": "circle", "cx": rpx, "cy": rpy, "r": pr})
            cr.new_sub_path()
            cr.arc(rpx, rpy, pr, 0.0, 2.0 * math.pi)
            cr.set_source_rgba(*_hex_rgba(pal["intent_bg"] if ("r3" in btns) else pal["structure_bg"], 0.92))
            cr.fill_preserve()
            cr.set_source_rgba(*_hex_rgba(pal["intent"] if rpad_p else pal["structure"], 0.95))
            cr.set_line_width(1.4 if rpad_p else 1.1)
            cr.stroke()
            cr.new_sub_path()
            cr.arc(rpx, rpy, pr * 0.62, 0.0, 2.0 * math.pi)
            cr.set_source_rgba(*_hex_rgba(pal["structure"], 0.40))
            cr.set_line_width(0.9)
            cr.stroke()
            # Live Right Trackpad finger cursor dot
            tx_p, ty_p = rpx + rs_xy[0] * (pr * 0.72), rpy + rs_xy[1] * (pr * 0.72)
            cr.new_sub_path()
            cr.arc(tx_p, ty_p, 4.2 * scale, 0.0, 2.0 * math.pi)
            cr.set_source_rgba(*_hex_rgba(pal["intent"] if rpad_p else pal["schema"], 0.95))
            cr.fill()

            # Lower-Left Analog Stick
            sx, sy = _p(-36.0, 11.0)
            sr = 15.5 * scale
            clicked = "l3" in btns
            self._hit_targets.append({"id": "btn_l3", "shape": "circle", "cx": sx, "cy": sy, "r": sr})
            cr.new_sub_path()
            cr.arc(sx, sy, sr, 0.0, 2.0 * math.pi)
            cr.set_source_rgba(*_hex_rgba(pal["structure_bg"], 0.90))
            cr.fill_preserve()
            cr.set_source_rgba(*_hex_rgba(pal["intent"] if clicked else pal["structure"], 0.92))
            cr.set_line_width(1.4 if clicked else 1.0)
            cr.stroke()
            nx, ny = sx + ls_xy[0] * (sr * 0.48), sy + ls_xy[1] * (sr * 0.48)
            active_s = clicked or math.hypot(ls_xy[0], ls_xy[1]) > 0.04
            cr.new_sub_path()
            cr.arc(nx, ny, 8.8 * scale, 0.0, 2.0 * math.pi)
            cr.set_source_rgba(*_hex_rgba(pal["intent_bg"] if active_s else surf_hex, 0.95))
            cr.fill_preserve()
            cr.set_source_rgba(*_hex_rgba(pal["intent"] if active_s else pal["schema"], 0.95))
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
                cr.set_source_rgba(*_hex_rgba(pal["intent_bg"] if pressed else pal["structure_bg"], 0.95))
                cr.fill_preserve()
                cr.set_source_rgba(*_hex_rgba(pal["intent"] if pressed else pal["structure"], 0.95))
                cr.set_line_width(1.4 if pressed else 1.0)
                cr.stroke()
                fl = PangoCairo.create_layout(cr)
                fl.set_font_description(f_micro)
                fl.set_text(glyphs[bid], -1)
                _, fe = fl.get_pixel_extents()
                cr.set_source_rgba(*_hex_rgba(pal["focus"] if pressed else pal["focus_val"], 0.95))
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
            cr.set_source_rgba(*_hex_rgba(pal["structure_bg"], 0.90))
            cr.fill_preserve()
            sc_c, sc_w = self._bind_col_lw(click_id, clicked, pal["structure"], 1.0, pal)
            cr.set_source_rgba(*_hex_rgba(sc_c, 0.92))
            cr.set_line_width(sc_w)
            cr.stroke()
            # Moving thumbstick cap
            nx, ny = sx + vx * (sr * 0.48), sy + vy * (sr * 0.48)
            nr = 10.0 * scale
            active_s = clicked or math.hypot(vx, vy) > 0.04
            cr.new_sub_path()
            cr.arc(nx, ny, nr, 0.0, 2.0 * math.pi)
            cr.set_source_rgba(*_hex_rgba(pal["intent_bg"] if active_s else surf_hex, 0.95))
            cr.fill_preserve()
            cr.set_source_rgba(*_hex_rgba(pal["intent"] if active_s else pal["schema"], 0.95))
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
            cr.set_source_rgba(*_hex_rgba(pal["structure_bg"], 0.94))
            cr.fill_preserve()
            cr.set_source_rgba(*_hex_rgba(pal["intent"] if any_d else pal["schema"], 0.92))
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
                _rounded_rect(cr, rx0, ry0, rw0, rh0, 2.0 * scale)
                dc_c, _dc_w = self._bind_col_lw(did, active_d, pal["structure"], 1.0, pal)
                cr.set_source_rgba(*_hex_rgba(dc_c, 0.95 if (active_d or did in self.mapped_ctrls or did == self.selected_ctrl) else 0.55))
                cr.fill()
            # Center concave dimple
            cr.new_sub_path()
            cr.arc(dx0, dy0, 3.2 * scale, 0.0, 2.0 * math.pi)
            cr.set_source_rgba(*_hex_rgba(pal["structure"], 0.72))
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
            cr.set_source_rgba(*_hex_rgba(pal["structure_bg"], 0.92))
            cr.fill()
            for did, rx0, ry0, rw0, rh0, active_d in dpad_parts:
                self._hit_targets.append({"id": f"btn_{did}", "shape": "rect", "x": rx0, "y": ry0, "w": rw0, "h": rh0})
                _rounded_rect(cr, rx0, ry0, rw0, rh0, 2.2 * scale)
                cr.set_source_rgba(*_hex_rgba(pal["intent_bg"] if active_d else pal["structure_bg"], 0.95))
                cr.fill_preserve()
                dc_c, dc_w = self._bind_col_lw(did, active_d, pal["structure"], 0.95, pal)
                cr.set_source_rgba(*_hex_rgba(dc_c, 0.95))
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
            cr.set_source_rgba(*_hex_rgba(pal["intent_bg"] if pressed else pal["structure_bg"], 0.95))
            cr.fill_preserve()
            fc_c, fc_w = self._bind_col_lw(bid, pressed, pal["structure"], 1.05, pal)
            cr.set_source_rgba(*_hex_rgba(fc_c, 0.95))
            cr.set_line_width(fc_w)
            cr.stroke()
            fl = PangoCairo.create_layout(cr)
            fl.set_font_description(f_btn)
            fl.set_text(glyphs[bid], -1)
            _, fe = fl.get_pixel_extents()
            cr.set_source_rgba(*_hex_rgba(pal["focus"] if pressed else pal["focus_val"], 0.95))
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
        _rounded_rect(cr, bx + 0.5, 0.5, bw - 1.0, h - 1.0, 6.0)
        cr.set_source_rgba(*_hex_rgba(pal["structure_bg"], 0.62))
        cr.fill_preserve()
        cr.set_source_rgba(*_hex_rgba(pal["structure"], 0.90))
        cr.set_line_width(1.0)
        cr.stroke()

        cr.save()
        _rounded_rect(cr, bx + 1.0, 1.0, bw - 2.0, h - 2.0, 5.5)
        cr.clip()

        micro_sz = int(round(max(8.0, self.font_px * 0.78) * Pango.SCALE))
        btn_sz = int(round(max(8.5, self.font_px * 0.86) * Pango.SCALE))
        f_micro = Pango.FontDescription()
        f_micro.set_family(font_family)
        f_micro.set_absolute_size(micro_sz)
        f_btn = Pango.FontDescription()
        f_btn.set_family(font_family)
        f_btn.set_weight(Pango.Weight.BOLD)
        f_btn.set_absolute_size(btn_sz)

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
            cr.set_source_rgba(*_hex_rgba(pal["structure"], 0.35))
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
        fy = stage_h
        cr.set_source_rgba(*_hex_rgba(surf_hex, 0.78))
        cr.rectangle(bx + 1.0, fy, bw - 2.0, footer_h)
        cr.fill()
        cr.set_source_rgba(*_hex_rgba(pal["structure"], 0.72))
        cr.set_line_width(1.0)
        cr.move_to(bx + 1.0, fy + 0.5)
        cr.line_to(bx + bw - 1.0, fy + 0.5)
        cr.stroke()

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
        cr.set_source_rgba(*_hex_rgba(pal["focus_val"], 0.92))
        cr.move_to(bx + fpad, fty)
        PangoCairo.show_layout(cr, fl)

        cr.set_source_rgba(*_hex_rgba(pal["schema"], 0.82))
        cr.move_to(bx + bw - fpad - fre.width, fty)
        PangoCairo.show_layout(cr, fr)

        cr.restore()
        return False


class _ResponseCurveMixin:
    """Pure-Cairo Dual Transfer Graph: Stick Response Curve (left) + Trigger Actuation Ramp (right)."""

    def __init__(
        self,
        profile: dict,
        widget_width: int = 760,
        fixed_height: int = 118,
        inset_x: int = 14,
        font_px: float = 11.0,
    ):
        super().__init__()
        self.profile = dict(profile)
        self.widget_width = max(300, int(widget_width))
        self.widget_height = max(106, int(fixed_height))
        self.inset_x = max(4, int(inset_x))
        self.font_px = max(8.5, float(font_px))
        self.footer_h = 22
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

        _rounded_rect(cr, bx + 0.5, 0.5, bw - 1.0, h - 1.0, 6.0)
        cr.set_source_rgba(*_hex_rgba(pal["structure_bg"], 0.62))
        cr.fill_preserve()
        cr.set_source_rgba(*_hex_rgba(pal["structure"], 0.90))
        cr.set_line_width(1.0)
        cr.stroke()

        cr.save()
        _rounded_rect(cr, bx + 1.0, 1.0, bw - 2.0, h - 2.0, 5.5)
        cr.clip()

        micro_sz = int(round(max(7.8, self.font_px * 0.76) * Pango.SCALE))
        f_micro = Pango.FontDescription()
        f_micro.set_family(font_family)
        f_micro.set_absolute_size(micro_sz)

        # Split into 2 crisp equal plot areas
        pad_x = 18.0
        gap_mid = 26.0
        plot_w = max(80.0, (bw - pad_x * 2.0 - gap_mid) * 0.5)
        plot_y = 22.0
        plot_h = max(42.0, stage_h - plot_y - 10.0)
        left_x = bx + pad_x
        right_x = left_x + plot_w + gap_mid

        def _draw_grid(px0: float, title: str, tag: str, tag_col: str) -> None:
            tl = PangoCairo.create_layout(cr)
            tl.set_font_description(f_micro)
            tl.set_text(title, -1)
            cr.set_source_rgba(*_hex_rgba(pal["schema"], 0.92))
            cr.move_to(px0, 5.0)
            PangoCairo.show_layout(cr, tl)

            gl = PangoCairo.create_layout(cr)
            gl.set_font_description(f_micro)
            gl.set_text(tag, -1)
            _, ge = gl.get_pixel_extents()
            cr.set_source_rgba(*_hex_rgba(tag_col, 0.92))
            cr.move_to(px0 + plot_w - ge.width, 5.0)
            PangoCairo.show_layout(cr, gl)

            cr.set_source_rgba(*_hex_rgba(surf_hex, 0.85))
            cr.rectangle(px0, plot_y, plot_w, plot_h)
            cr.fill()
            cr.set_source_rgba(*_hex_rgba(pal["structure"], 0.72))
            cr.set_line_width(1.0)
            cr.rectangle(px0 + 0.5, plot_y + 0.5, plot_w - 1.0, plot_h - 1.0)
            cr.stroke()

            # 4x4 dashed grid + 1:1 linear diagonal reference
            cr.save()
            cr.set_source_rgba(*_hex_rgba(pal["structure"], 0.35))
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
            cr.set_source_rgba(*_hex_rgba(pal["schema"], 0.38))
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
        cr.set_source_rgba(*_hex_rgba(pal["attention_bg"], 0.65))
        cr.rectangle(left_x + 1.0, plot_y + 1.0, max(0.0, dz_w - 1.0), plot_h - 2.0)
        cr.fill()
        if outer_th < 0.999:
            sat_x = left_x + plot_w * outer_th
            cr.set_source_rgba(*_hex_rgba(pal["vitality_bg"], 0.55))
            cr.rectangle(sat_x, plot_y + 1.0, max(0.0, left_x + plot_w - sat_x - 1.0), plot_h - 2.0)
            cr.fill()
            cr.save()
            cr.set_dash([2.5, 2.5])
            cr.set_source_rgba(*_hex_rgba(pal["vitality"], 0.80))
            cr.set_line_width(1.0)
            cr.move_to(sat_x, plot_y)
            cr.line_to(sat_x, plot_y + plot_h)
            cr.stroke()
            cr.restore()
        cr.save()
        cr.set_dash([2.5, 2.5])
        cr.set_source_rgba(*_hex_rgba(pal["attention"], 0.85))
        cr.set_line_width(1.0)
        cr.move_to(left_x + dz_w, plot_y)
        cr.line_to(left_x + dz_w, plot_y + plot_h)
        cr.stroke()
        cr.restore()

        # Sampled active stick response curve (intent cyan)
        cr.save()
        cr.set_source_rgba(*_hex_rgba(pal["intent"], 0.98))
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
            cr.set_source_rgba(*_hex_rgba(pal["attention_bg"], 0.52))
            cr.rectangle(cut_px, plot_y + 1.0, max(0.0, right_x + plot_w - cut_px - 1.0), plot_h - 2.0)
            cr.fill()
        cr.save()
        cr.set_dash([2.5, 2.5])
        cr.set_source_rgba(*_hex_rgba(pal["attention"], 0.90))
        cr.set_line_width(1.1)
        cr.move_to(cut_px, plot_y)
        cr.line_to(cut_px, plot_y + plot_h)
        cr.stroke()
        cr.restore()

        cr.save()
        cr.set_source_rgba(*_hex_rgba(pal["intent"], 0.98))
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
        fy = stage_h
        cr.set_source_rgba(*_hex_rgba(surf_hex, 0.78))
        cr.rectangle(bx + 1.0, fy, bw - 2.0, footer_h)
        cr.fill()
        cr.set_source_rgba(*_hex_rgba(pal["structure"], 0.72))
        cr.set_line_width(1.0)
        cr.move_to(bx + 1.0, fy + 0.5)
        cr.line_to(bx + bw - 1.0, fy + 0.5)
        cr.stroke()

        c_lbl = t(f"cc_input_controllers.curve_{curve}")
        foot_l = t(
            "cc_input_controllers.curve_footer_left",
            p0=c_lbl,
            p1=self.profile["inner_dz"],
            p2=self.profile["outer_th"],
            p3=self.profile["trigger_cut"],
        )
        foot_r = t("cc_input_controllers.curve_footer_right")

        fr = PangoCairo.create_layout(cr)
        fr.set_font_description(f_micro)
        fr.set_text(foot_r, -1)
        _, fre = fr.get_pixel_extents()

        fpad = 12.0
        fl = PangoCairo.create_layout(cr)
        fl.set_font_description(f_micro)
        fl.set_ellipsize(Pango.EllipsizeMode.END)
        fl.set_width(int(max(40.0, bw - fpad * 3.0 - fre.width) * Pango.SCALE))
        fl.set_text(foot_l, -1)
        _, fle = fl.get_pixel_extents()

        fty = fy + max(2.0, (footer_h - max(fle.height, fre.height)) * 0.5)
        cr.set_source_rgba(*_hex_rgba(pal["focus_val"], 0.90))
        cr.move_to(bx + fpad, fty)
        PangoCairo.show_layout(cr, fl)

        cr.set_source_rgba(*_hex_rgba(pal["schema"], 0.78))
        cr.move_to(bx + bw - fpad - fre.width, fty)
        PangoCairo.show_layout(cr, fr)

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


def _get_map_store_draft() -> dict:
    d = STATE.get("controllers", "map_store_draft", None)
    if isinstance(d, dict) and d.get("profiles"):
        return norm_pad_store(d)
    st = load_pad_store()
    STATE.set("controllers", "map_store_draft", st)
    return st


def _set_map_store_draft(store: dict) -> dict:
    norm = norm_pad_store(store)
    STATE.set("controllers", "map_store_draft", norm)
    return norm


def _is_map_store_dirty(draft: dict) -> bool:
    return norm_pad_store(draft) != load_pad_store()


def _active_map_profile(store: dict) -> tuple[int, dict]:
    profs = store["profiles"]
    aid = store.get("active_profile_id", "")
    for i, p in enumerate(profs):
        if p["id"] == aid:
            return i, p
    return 0, profs[0]


def _load_macros_brief() -> list[dict]:
    from panels.cc_engine_input import _load_macros
    return _load_macros()


def apply_captured_pad_key(combo: str, codes: list[int]) -> None:
    st = _get_map_store_draft()
    idx, prof = _active_map_profile(st)
    sel = str(STATE.get("controllers", "mp_selected_ctrl", "south") or "south")
    if sel in ("l_stick", "r_stick"):
        sel = "south"
    layer = str(STATE.get("controllers", "mp_layer", "base") or "base")
    if layer not in ("base", "shift"):
        layer = "base"
    binds = dict(prof.get("binds") or {})
    cur_entry = dict(binds.get(sel) or {"base": None, "shift": None})
    old_spec = cur_entry.get(layer) if isinstance(cur_entry.get(layer), dict) else {}
    mode = old_spec.get("mode", "hold") if old_spec.get("kind") in ("key", "mouse_btn", "mouse_wheel") else "hold"
    cur_entry[layer] = {"kind": "key", "combo": combo, "codes": list(codes), "mode": mode}
    binds[sel] = cur_entry
    prof = dict(prof)
    prof["binds"] = binds
    profs = list(st["profiles"])
    profs[idx] = prof
    st["profiles"] = profs
    _set_map_store_draft(st)
    STATE.set("controllers", "mp_act_kind", "")
    _set_ctrl_feedback(
        True,
        t(
            "cc_input_controllers.fb_mapper_bound",
            p0=t(f"cc_input_controllers.ctrl_{sel}"),
            p1=t(f"cc_input_controllers.mp_layer_{layer}"),
            p2=combo,
        ),
    )


def handle_cc_controllers_prompt(cc_panel, action_id: str, text: str) -> bool:
    if not action_id.startswith("cc_ctrl_"):
        return False
    val = str(text or "").strip()
    st = _get_map_store_draft()
    idx, prof = _active_map_profile(st)
    profs = list(st["profiles"])
    if action_id == "cc_ctrl_pick_window":
        prof = dict(prof)
        prof["match_app_id"] = val[:80]
        if val:
            prof["is_fallback"] = False
        profs[idx] = prof
        st["profiles"] = profs
        _set_map_store_draft(st)
        _set_ctrl_feedback(True, t("cc_input_controllers.fb_mapper_win_set", p0=val or "-"))
        return True
    if action_id == "cc_ctrl_new_prof" and val:
        nid = f"prof_{int(time.time())}"
        new_p = build_template_profile("fps", nid, val[:36])
        profs.append(new_p)
        st["profiles"] = profs
        st["active_profile_id"] = nid
        _set_map_store_draft(st)
        _set_ctrl_feedback(True, t("cc_input_controllers.fb_mapper_prof_created", p0=new_p["name"]))
        return True
    if action_id == "cc_ctrl_ren_prof" and val:
        prof = dict(prof)
        prof["name"] = val[:36]
        profs[idx] = prof
        st["profiles"] = profs
        _set_map_store_draft(st)
        return True
    return False


def _set_ctrl_bind_action(spec: dict | None, layer_override: str | None = None) -> None:
    st = _get_map_store_draft()
    idx, prof = _active_map_profile(st)
    sel = str(STATE.get("controllers", "mp_selected_ctrl", "south") or "south")
    if sel in ("l_stick", "r_stick"):
        return
    layer = layer_override or str(STATE.get("controllers", "mp_layer", "base") or "base")
    if layer not in ("base", "shift"):
        layer = "base"
    binds = dict(prof.get("binds") or {})
    entry = dict(binds.get(sel) or {"base": None, "shift": None})
    entry[layer] = spec
    if entry.get("base") is None and entry.get("shift") is None:
        binds.pop(sel, None)
    else:
        binds[sel] = entry
    prof = dict(prof)
    prof["binds"] = binds
    profs = list(st["profiles"])
    profs[idx] = prof
    st["profiles"] = profs
    _set_map_store_draft(st)


def _handle_mp_link(cc_panel, act: str, dev: dict | None) -> bool:
    st = _get_map_store_draft()
    idx, prof = _active_map_profile(st)
    profs = list(st["profiles"])
    sel = str(STATE.get("controllers", "mp_selected_ctrl", "south") or "south")
    layer = str(STATE.get("controllers", "mp_layer", "base") or "base")

    if act.startswith("mp_view:"):
        vm = act.split(":", 1)[1]
        if vm in ("hardware", "mapping"):
            STATE.set("controllers", "view_mode", vm)
    elif act == "open_from_macro":
        STATE.set("controllers", "view_mode", "mapping")
        if cc_panel:
            cc_panel.set_section("input")
            cc_panel.set_subtab("controllers")
    elif act == "open_macros":
        if cc_panel:
            cc_panel.set_section("input")
            cc_panel.set_subtab("macros")
    elif act == "unlock_uinput":
        from panels.cc_engine_input import _ensure_uinput_or_request
        _ensure_uinput_or_request(cc_panel)
    elif act == "mp_start":
        from panels.cc_engine_input import _ensure_uinput_or_request
        if _ensure_uinput_or_request(cc_panel):
            save_pad_store(st)
            cmd = [PAD_MAPPER_BIN]
            if dev and dev.get("evdev"):
                cmd.extend(["--device", dev["evdev"]])
            probe.spawn_bg(cmd)
            _set_ctrl_feedback(True, t("cc_input_controllers.fb_mapper_started"))
    elif act == "mp_stop":
        stop_pad_mapper()
        _set_ctrl_feedback(True, t("cc_input_controllers.fb_mapper_stopped"))
    elif act == "mp_next":
        if profs:
            nxt = profs[(idx + 1) % len(profs)]
            st["active_profile_id"] = nxt["id"]
            _set_map_store_draft(st)
    elif act == "mp_new":
        if cc_panel:
            cc_panel.request_prompt(
                "cc_ctrl_new_prof",
                t("cc_input_controllers.mp_prompt_new_ph"),
                "",
                title=t("cc_input_controllers.mp_prompt_new_title"),
                required=True,
            )
        return True
    elif act == "mp_dup":
        nid = f"prof_{int(time.time())}"
        cp = norm_pad_profile(prof, nid, f"{prof['name']} Copy")
        cp["id"] = nid
        cp["name"] = f"{prof['name'][:28]} (2)"
        profs.append(cp)
        st["profiles"] = profs
        st["active_profile_id"] = nid
        _set_map_store_draft(st)
    elif act == "mp_ren":
        if cc_panel:
            cc_panel.request_prompt(
                "cc_ctrl_ren_prof",
                t("cc_input_controllers.mp_prompt_new_ph"),
                prof["name"],
                title=t("cc_input_controllers.mp_prompt_ren_title"),
                required=True,
            )
        return True
    elif act == "mp_del":
        if len(profs) > 1:
            removed_name = prof["name"]
            profs.pop(idx)
            st["profiles"] = profs
            st["active_profile_id"] = profs[max(0, idx - 1)]["id"]
            _set_map_store_draft(st)
            _set_ctrl_feedback(True, t("cc_input_controllers.fb_mapper_prof_deleted", p0=removed_name))
    elif act.startswith("mp_tpl:"):
        tpl = act.split(":", 1)[1]
        new_p = build_template_profile(tpl, prof["id"], prof["name"])
        new_p["match_app_id"] = prof.get("match_app_id", "")
        new_p["match_title"] = prof.get("match_title", "")
        profs[idx] = new_p
        st["profiles"] = profs
        _set_map_store_draft(st)
        _set_ctrl_feedback(True, t("cc_input_controllers.fb_mapper_tpl", p0=tpl.upper()))
    elif act == "mp_toggle_auto":
        st["auto_switch"] = not bool(st.get("auto_switch", True))
        _set_map_store_draft(st)
    elif act == "mp_toggle_fallback":
        p = dict(prof)
        p["is_fallback"] = not bool(p.get("is_fallback", False))
        profs[idx] = p
        st["profiles"] = profs
        _set_map_store_draft(st)
    elif act == "mp_toggle_grab":
        p = dict(prof)
        p["exclusive_grab"] = not bool(p.get("exclusive_grab", True))
        profs[idx] = p
        st["profiles"] = profs
        _set_map_store_draft(st)
    elif act == "mp_inline_app":
        from ui.kit import start_inline_input
        cur_rule = prof.get("match_app_id") or prof.get("match_title") or ""
        def _commit_rule(txt: str) -> None:
            s2 = _get_map_store_draft()
            i2, p2 = _active_map_profile(s2)
            p2 = dict(p2)
            p2["match_app_id"] = str(txt or "").strip()[:80]
            s2["profiles"][i2] = p2
            _set_map_store_draft(s2)
        if cc_panel and cc_panel.shell:
            start_inline_input(
                entry_id="cc_ctrl_app",
                shell=cc_panel.shell,
                trigger_uri="cc_ctrl:mp_inline_app",
                initial_text=cur_rule,
                max_len=60,
                on_commit=_commit_rule,
            )
        return True
    elif act == "mp_pick_win":
        if cc_panel:
            cc_panel.request_prompt(
                "cc_ctrl_pick_window",
                t("cc_input_controllers.mp_prompt_win_ph"),
                prof.get("match_app_id", ""),
                title=t("cc_input_controllers.mp_prompt_win_title"),
                completion="niri_windows",
            )
        return True
    elif act.startswith("mp_sens:"):
        p = dict(prof)
        p["mouse_sens"] = max(2, min(60, int(round(float(act.split(":", 1)[1])))))
        profs[idx] = p
        st["profiles"] = profs
        _set_map_store_draft(st)
        if cc_panel:
            cc_panel.request_throttled_refresh()
        return True
    elif act.startswith("mp_sel:"):
        cid = act.split(":", 1)[1]
        if cid in PAD_CONTROL_IDS:
            STATE.set("controllers", "mp_selected_ctrl", cid)
            STATE.set("controllers", "mp_act_kind", "")
    elif act.startswith("mp_layer:"):
        ly = act.split(":", 1)[1]
        if ly in ("base", "shift"):
            STATE.set("controllers", "mp_layer", ly)
            STATE.set("controllers", "mp_act_kind", "")
    elif act.startswith("mp_select_prof:"):
        pid = act.split(":", 1)[1]
        if any(p["id"] == pid for p in profs):
            st["active_profile_id"] = pid
            _set_map_store_draft(st)
    elif act.startswith("mp_set_actmode:"):
        amode = act.split(":", 1)[1]
        p = dict(prof)
        if amode == "auto":
            st["auto_switch"] = True
            p["is_fallback"] = False
        elif amode == "always":
            st["auto_switch"] = False
            p["is_fallback"] = False
        elif amode == "fallback":
            st["auto_switch"] = True
            p["is_fallback"] = True
        profs[idx] = p
        st["profiles"] = profs
        _set_map_store_draft(st)
    elif act.startswith("mp_set_grab:"):
        want_excl = act.split(":", 1)[1] == "1"
        p = dict(prof)
        p["exclusive_grab"] = want_excl
        profs[idx] = p
        st["profiles"] = profs
        _set_map_store_draft(st)
    elif act == "mp_turbo_next":
        hz_opts = (6, 10, 12, 16, 20, 25)
        cur_hz = int(prof.get("turbo_hz", 12))
        n_idx = (hz_opts.index(cur_hz) + 1) % len(hz_opts) if cur_hz in hz_opts else 2
        p = dict(prof)
        p["turbo_hz"] = hz_opts[n_idx]
        profs[idx] = p
        st["profiles"] = profs
        _set_map_store_draft(st)
    elif act.startswith("mp_kind:"):
        knd = act.split(":", 1)[1]
        if knd in ("key", "mouse", "macro", "shift"):
            STATE.set("controllers", "mp_act_kind", knd)
            cur_a = (prof.get("binds") or {}).get(sel, {}).get(layer)
            if knd == "shift":
                STATE.set("controllers", "mp_layer", "base")
                _set_ctrl_bind_action({"kind": "shift"}, layer_override="base")
            elif knd == "mouse" and not (isinstance(cur_a, dict) and cur_a.get("kind") in ("mouse_btn", "mouse_wheel")):
                _set_ctrl_bind_action({"kind": "mouse_btn", "target": "LMB", "mode": "hold"})
            elif knd == "macro":
                macros = _load_macros_brief()
                if macros and not (isinstance(cur_a, dict) and cur_a.get("kind") == "macro"):
                    _set_ctrl_bind_action({"kind": "macro", "macro_id": macros[0]["id"], "mode": "once"})
    elif act.startswith("mp_quick_key:"):
        kname = act.split(":", 1)[1]
        codes = resolve_combo_codes(kname)
        if codes:
            cur_a = (prof.get("binds") or {}).get(sel, {}).get(layer)
            bmode = cur_a.get("mode", "hold") if isinstance(cur_a, dict) and cur_a.get("mode") in BIND_MODES else "hold"
            _set_ctrl_bind_action({"kind": "key", "combo": kname, "codes": codes, "mode": bmode})
            STATE.set("controllers", "mp_act_kind", "key")
    elif act.startswith("mp_stick_thresh:"):
        if sel in ("l_stick", "r_stick"):
            p = dict(prof)
            scfg = dict(p[sel])
            scfg["threshold"] = max(15, min(80, int(round(float(act.split(":", 1)[1])))))
            p[sel] = scfg
            profs[idx] = p
            st["profiles"] = profs
            _set_map_store_draft(st)
            if cc_panel:
                cc_panel.request_throttled_refresh()
        return True
    elif act.startswith("mp_scroll_sens:"):
        p = dict(prof)
        p["scroll_sens"] = max(1, min(20, int(round(float(act.split(":", 1)[1])))))
        profs[idx] = p
        st["profiles"] = profs
        _set_map_store_draft(st)
        if cc_panel:
            cc_panel.request_throttled_refresh()
        return True
    elif act == "mp_cap_key":
        STATE.set("keyboard", "capture_mode", "ctrl_bind")
        STATE.set("keyboard", "capture_preview", "")
        if cc_panel and cc_panel.shell and hasattr(cc_panel.shell, "set_key_capture_grab"):
            cc_panel.shell.set_key_capture_grab(True)
    elif act == "mp_cap_stop":
        STATE.set("keyboard", "capture_mode", "")
        STATE.set("keyboard", "capture_preview", "")
        if cc_panel and cc_panel.shell and hasattr(cc_panel.shell, "set_key_capture_grab"):
            cc_panel.shell.set_key_capture_grab(False)
    elif act.startswith("mp_bind_mouse:"):
        tgt = act.split(":", 1)[1]
        if tgt in MOUSE_TARGET_TO_EVDEV or tgt in WHEEL_TARGETS:
            _set_ctrl_bind_action({"kind": "mouse_wheel" if tgt in WHEEL_TARGETS else "mouse_btn", "target": tgt, "mode": "hold"})
    elif act == "mp_bind_shift":
        STATE.set("controllers", "mp_layer", "base")
        _set_ctrl_bind_action({"kind": "shift"}, layer_override="base")
    elif act == "mp_open_macro_pick":
        STATE.set("controllers", "mp_macro_open", True)
        macros = _load_macros_brief()
        cur_a = (prof.get("binds") or {}).get(sel, {}).get(layer)
        if macros and not (isinstance(cur_a, dict) and cur_a.get("kind") == "macro"):
            _set_ctrl_bind_action({"kind": "macro", "macro_id": macros[0]["id"], "mode": "once"})
    elif act == "mp_cycle_macro":
        macros = _load_macros_brief()
        if macros:
            cur_a = (prof.get("binds") or {}).get(sel, {}).get(layer)
            cur_mid = cur_a.get("macro_id", "") if isinstance(cur_a, dict) else ""
            mids = [m["id"] for m in macros]
            n_idx = (mids.index(cur_mid) + 1) % len(mids) if cur_mid in mids else 0
            pmode = cur_a.get("mode", "once") if isinstance(cur_a, dict) and cur_a.get("mode") in MACRO_PAD_MODES else "once"
            _set_ctrl_bind_action({"kind": "macro", "macro_id": mids[n_idx], "mode": pmode})
    elif act.startswith("mp_bind_macro:"):
        mid = act.split(":", 1)[1]
        _set_ctrl_bind_action({"kind": "macro", "macro_id": mid, "mode": "once"})
    elif act.startswith("mp_macro_mode:"):
        pmode = act.split(":", 1)[1]
        cur_a = (prof.get("binds") or {}).get(sel, {}).get(layer)
        if pmode in MACRO_PAD_MODES and isinstance(cur_a, dict) and cur_a.get("kind") == "macro":
            upd = dict(cur_a)
            upd["mode"] = pmode
            _set_ctrl_bind_action(upd)
    elif act.startswith("mp_mode:"):
        bm = act.split(":", 1)[1]
        cur_a = (prof.get("binds") or {}).get(sel, {}).get(layer)
        if bm in BIND_MODES and isinstance(cur_a, dict) and cur_a.get("kind") in ("key", "mouse_btn", "mouse_wheel"):
            upd = dict(cur_a)
            upd["mode"] = bm
            _set_ctrl_bind_action(upd)
    elif act == "mp_unbind" or act.startswith("mp_unbind:"):
        tgt_cid = act.split(":", 1)[1] if ":" in act else sel
        STATE.set("controllers", "mp_selected_ctrl", tgt_cid)
        STATE.set("controllers", "mp_act_kind", "")
        if tgt_cid in ("l_stick", "r_stick"):
            p = dict(prof)
            scfg = dict(p[tgt_cid])
            scfg["mode"] = "off"
            p[tgt_cid] = scfg
            profs[idx] = p
            st["profiles"] = profs
            _set_map_store_draft(st)
        else:
            _set_ctrl_bind_action(None)
    elif act.startswith("mp_stick_mode:"):
        sm = act.split(":", 1)[1]
        if sel in ("l_stick", "r_stick") and sm in STICK_MODES:
            p = dict(prof)
            scfg = dict(p[sel])
            scfg["mode"] = sm
            p[sel] = scfg
            profs[idx] = p
            st["profiles"] = profs
            _set_map_store_draft(st)
    elif act == "mp_stick_invy":
        p = dict(prof)
        rs = dict(p["r_stick"])
        rs["invert_y"] = not bool(rs.get("invert_y", False))
        p["r_stick"] = rs
        profs[idx] = p
        st["profiles"] = profs
        _set_map_store_draft(st)
    elif act.startswith("mp_flt:"):
        STATE.set("controllers", "mp_filter", act.split(":", 1)[1])
        STATE.set("controllers", "mp_page", 0)
    elif act.startswith("mp_page:"):
        try:
            STATE.set("controllers", "mp_page", max(0, int(act.split(":", 1)[1])))
        except ValueError:
            pass
    elif act.startswith("mp_row_bind:"):
        cid = act.split(":", 1)[1]
        if cid in PAD_CONTROL_IDS:
            STATE.set("controllers", "mp_selected_ctrl", cid)
            if cid not in ("l_stick", "r_stick"):
                STATE.set("keyboard", "capture_mode", "ctrl_bind")
                STATE.set("keyboard", "capture_preview", "")
                if cc_panel and cc_panel.shell and hasattr(cc_panel.shell, "set_key_capture_grab"):
                    cc_panel.shell.set_key_capture_grab(True)
    elif act == "mp_save":
        ok = save_pad_store(st)
        if ok:
            reload_pad_mapper_if_running()
        _set_ctrl_feedback(ok, t("cc_input_controllers.fb_mapper_saved"))
    elif act == "mp_reset":
        fresh = load_pad_store()
        _set_map_store_draft(fresh)
        _set_ctrl_feedback(True, t("cc_input_controllers.fb_mapper_reset"))
    else:
        return False

    if cc_panel and cc_panel.shell:
        cc_panel.shell.refresh_view()
    return True


def handle_cc_controllers_link(cc_panel, uri: str) -> bool:
    if not uri.startswith("cc_ctrl:"):
        return False
    act = uri.split(":", 1)[1]
    devices = discover_controllers(force=(act == "rescan"))
    key, dev = _active_key_and_device(devices)
    def_fam = dev["family"] if dev else "xbox"

    if act.startswith(("mp_", "open_from_macro", "open_macros", "unlock_uinput")):
        return _handle_mp_link(cc_panel, act, dev)

    if act == "rescan":
        n_conn = sum(1 for d in devices if d["connected"])
        n_bt = sum(1 for d in devices if not d["connected"])
        _set_ctrl_feedback(True, t("cc_input_controllers.fb_rescanned", p0=n_conn, p1=n_bt))
    elif act == "open_bt":
        if cc_panel:
            cc_panel.set_section("network")
            cc_panel.set_subtab("wifi_bt")
    elif act.startswith("select:"):
        STATE.set("controllers", "selected_id", act.split(":", 1)[1])
    elif act.startswith("bt_conn:"):
        mac = act.split(":", 1)[1]
        ok, out, err = probe.run_cmd_full(["bluetoothctl", "connect", mac], timeout=6.0)
        discover_controllers(force=True)
        _set_ctrl_feedback(ok, t("cc_input_controllers.fb_bt_connected", p0=mac) if ok else t("cc_input_controllers.fb_bt_failed", p0=mac, p1=(err or out)[:48]))
    elif act.startswith("bt_disc:"):
        mac = act.split(":", 1)[1]
        ok, out, err = probe.run_cmd_full(["bluetoothctl", "disconnect", mac], timeout=4.0)
        discover_controllers(force=True)
        _set_ctrl_feedback(ok, t("cc_input_controllers.fb_bt_disconnected", p0=mac) if ok else t("cc_input_controllers.fb_bt_failed", p0=mac, p1=(err or out)[:48]))
    elif act == "layout_next":
        cur_lay = _get_profile_draft(key, def_fam)["layout"]
        idx = _LAYOUTS.index(cur_lay) if cur_lay in _LAYOUTS else 0
        _set_profile_draft(key, def_fam, layout=_LAYOUTS[(idx + 1) % len(_LAYOUTS)])
    elif act.startswith("layout:"):
        lay = act.split(":", 1)[1]
        if lay in _LAYOUTS:
            _set_profile_draft(key, def_fam, layout=lay)
    elif act.startswith("elite_slot:"):
        try:
            sl_i = int(act.split(":", 1)[1])
            if sl_i in _ELITE_SLOTS:
                _set_profile_draft(key, def_fam, elite_slot=sl_i)
        except ValueError:
            pass
    elif act.startswith("paddle:"):
        pk = act.split(":", 1)[1]
        if pk in _PADDLE_KEYS:
            cur_act = _get_profile_draft(key, def_fam).get(pk, "none")
            idx = _PADDLE_ACTIONS.index(cur_act) if cur_act in _PADDLE_ACTIONS else 0
            _set_profile_draft(key, def_fam, **{pk: _PADDLE_ACTIONS[(idx + 1) % len(_PADDLE_ACTIONS)]})
    elif act.startswith("trigger_lock:"):
        tl = act.split(":", 1)[1]
        if tl in _TRIGGER_LOCK_CUT:
            _set_profile_draft(key, def_fam, trigger_lock=tl, trigger_cut=_TRIGGER_LOCK_CUT[tl])
    elif act.startswith("stick_calc:"):
        sc = act.split(":", 1)[1]
        if sc in _STICK_CALCS:
            _set_profile_draft(key, def_fam, stick_calc=sc)
    elif act == "reset_elite":
        _set_profile_draft(
            key,
            def_fam,
            elite_slot=_DEFAULT_PROFILE["elite_slot"],
            p1=_DEFAULT_PROFILE["p1"],
            p2=_DEFAULT_PROFILE["p2"],
            p3=_DEFAULT_PROFILE["p3"],
            p4=_DEFAULT_PROFILE["p4"],
            trigger_lock=_DEFAULT_PROFILE["trigger_lock"],
            trigger_cut=_DEFAULT_PROFILE["trigger_cut"],
            stick_calc=_DEFAULT_PROFILE["stick_calc"],
        )
    elif act.startswith("curve:"):
        crv = act.split(":", 1)[1]
        if crv in _CURVES:
            _set_profile_draft(key, def_fam, curve=crv)
    elif act.startswith("inner_dz:"):
        _set_profile_draft(key, def_fam, inner_dz=int(round(float(act.split(":", 1)[1]))))
        if cc_panel:
            cc_panel.request_throttled_refresh()
        return True
    elif act.startswith("outer_th:"):
        _set_profile_draft(key, def_fam, outer_th=int(round(float(act.split(":", 1)[1]))))
        if cc_panel:
            cc_panel.request_throttled_refresh()
        return True
    elif act.startswith("trigger_cut:"):
        tcut = int(round(float(act.split(":", 1)[1])))
        tlock = "short" if tcut <= 35 else ("mid" if tcut <= 70 else "full")
        _set_profile_draft(key, def_fam, trigger_cut=tcut, trigger_lock=tlock)
        if cc_panel:
            cc_panel.request_throttled_refresh()
        return True
    elif act.startswith("rumble_pct:"):
        _set_profile_draft(key, def_fam, rumble_pct=int(round(float(act.split(":", 1)[1]))))
        if cc_panel:
            cc_panel.request_throttled_refresh()
        return True
    elif act.startswith("rumble_test:"):
        side = act.split(":", 1)[1]
        prof = _get_profile_draft(key, def_fam)
        ok, msg = _send_kernel_rumble(dev, side, prof["rumble_pct"])
        _set_ctrl_feedback(ok, msg)
    elif act.startswith("led:"):
        lm = act.split(":", 1)[1]
        if lm in _LED_MODES:
            _set_profile_draft(key, def_fam, led_mode=lm)
            ok, msg = _apply_led_mode(dev, lm)
            _set_ctrl_feedback(ok, msg)
    elif act.startswith("toggle:"):
        fld = act.split(":", 1)[1]
        if fld in ("inv_ly", "inv_ry", "swap_sticks"):
            cur = _get_profile_draft(key, def_fam)
            _set_profile_draft(key, def_fam, **{fld: not bool(cur.get(fld, False))})
    elif act.startswith("gyro:"):
        gm = act.split(":", 1)[1]
        if gm in _GYRO_MODES:
            _set_profile_draft(key, def_fam, gyro_mode=gm)
    elif act == "zero_center":
        lx, ly = round(float(_LIVE_INPUT["lx"]), 3), round(float(_LIVE_INPUT["ly"]), 3)
        rx, ry = round(float(_LIVE_INPUT["rx"]), 3), round(float(_LIVE_INPUT["ry"]), 3)
        _set_profile_draft(key, def_fam, cal_lx=lx, cal_ly=ly, cal_rx=rx, cal_ry=ry)
        _set_ctrl_feedback(True, t("cc_input_controllers.fb_zeroed", p0=f"{lx:+.2f},{ly:+.2f}", p1=f"{rx:+.2f},{ry:+.2f}"))
    elif act == "save_profile":
        prof = _get_profile_draft(key, def_fam)
        profs = _load_saved_profiles()
        profs[key] = prof
        ok = _save_profiles(profs)
        dname = dev["name"] if dev else t("cc_input_controllers.preview_device_name")
        _set_ctrl_feedback(ok, t("cc_input_controllers.fb_profile_saved", p0=dname))
    elif act == "reset_profile":
        fresh = _norm_profile(None, def_fam)
        drafts = dict(STATE.get("controllers", "drafts", {}) or {})
        drafts[key] = fresh
        STATE.set("controllers", "drafts", drafts)
        profs = _load_saved_profiles()
        if key in profs:
            profs[key] = fresh
            _save_profiles(profs)
        dname = dev["name"] if dev else t("cc_input_controllers.preview_device_name")
        _set_ctrl_feedback(True, t("cc_input_controllers.fb_profile_reset", p0=dname))
    elif act == "export_json":
        profs = _load_saved_profiles()
        profs[key] = _get_profile_draft(key, def_fam)
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            payload = json.dumps(profs, indent=2)
            with open(CONTROLLERS_EXPORT_FILE, "w", encoding="utf-8") as f:
                f.write(payload)
            ok = open(CONTROLLERS_EXPORT_FILE, "r", encoding="utf-8").read() == payload
            _set_ctrl_feedback(ok, t("cc_input_controllers.fb_exported", p0=CONTROLLERS_EXPORT_FILE))
        except OSError as e:
            _set_ctrl_feedback(False, str(e))
    else:
        return False

    if cc_panel and cc_panel.shell:
        cc_panel.shell.refresh_view()
    return True


def _build_mapping_cards(cc_panel, canvas: Canvas, dev: dict | None, prof_hw: dict) -> list:
    store = _get_map_store_draft()
    _idx, mp = _active_map_profile(store)
    is_dirty = _is_map_store_dirty(store)
    uinput_ok = os.access("/dev/uinput", os.W_OK)
    rt = get_mapper_runtime_status()
    m_run = bool(rt.get("running"))
    macros = _load_macros_brief()
    macros_by_id = {m["id"]: m["name"] for m in macros}

    # --- CARD 1: Compatibility Layer, Game Profiles & Window Auto-Switch ---
    hdr_acts = [
        ActionBtn(t("cc_input_controllers.mapper_stop_btn"), "cc_ctrl:mp_stop", tone="danger_soft")
        if m_run
        else ActionBtn(t("cc_input_controllers.mapper_start_btn"), "cc_ctrl:mp_start", tone="apply"),
    ]
    comp_box = Box(
        t("cc_input_controllers.mapper_box_title"),
        icon="\uf11c",
        actions=hdr_acts,
        highlighted=False,
    )
    if not uinput_ok:
        comp_box.add_kv(
            t("cc_input_controllers.mapper_uinput_locked"),
            Badge(t("cc_keyboard.kernel_uinput_permission_required"), style="warn"),
            right_markup=ActionBtn(t("cc_input_controllers.mapper_unlock_btn"), "cc_ctrl:unlock_uinput", tone="apply"),
        )
    prof_acts = (
        f"{ActionBtn(t('cc_input_controllers.mp_new_btn'), 'cc_ctrl:mp_new', tone='neutral')} "
        f"{ActionBtn(t('cc_input_controllers.mp_dup_btn'), 'cc_ctrl:mp_dup', tone='neutral')} "
        f"{ActionBtn(t('cc_input_controllers.mp_ren_btn'), 'cc_ctrl:mp_ren', tone='neutral')} "
        f"{ActionBtn('x', 'cc_ctrl:mp_del', tone='danger_soft' if len(store['profiles']) > 1 else 'inactive')}"
    )
    comp_box.add_kv(
        t("cc_input_controllers.mp_active_prof_lbl"),
        CyclePill(mp["name"], "cc_ctrl:mp_next", active=True),
        right_markup=prof_acts,
    )
    tpl_btns = " ".join(
        ActionBtn(t(f"cc_input_controllers.mp_tpl_{tid}"), f"cc_ctrl:mp_tpl:{tid}", tone="neutral")
        for tid in ("fps", "platformer", "twinstick", "desktop")
    )
    comp_box.add_kv(
        t("cc_input_controllers.mp_tpl_lbl"),
        tpl_btns,
        right_markup=ActionBtn(t("cc_input_controllers.mp_tpl_blank"), "cc_ctrl:mp_tpl:blank", tone="danger_soft"),
    )

    def _build_prof_catalog(b: Box) -> None:
        p_rows = []
        for p_item in store["profiles"]:
            is_act_p = p_item["id"] == mp["id"]
            rule_s = p_item.get("match_app_id") or p_item.get("match_title") or (
                t("cc_input_controllers.mp_mode_fallback") if p_item.get("is_fallback") else "-"
            )
            ls_s = t(f"cc_input_controllers.mp_stick_{p_item['l_stick']['mode']}")
            rs_s = t(f"cc_input_controllers.mp_stick_{p_item['r_stick']['mode']}")
            grab_s = t("cc_input_controllers.mp_grab_short") if p_item.get("exclusive_grab", True) else t("cc_input_controllers.mp_pass_short")
            p_rows.append([
                InspectorCell(canvas.truncate_plain(p_item["name"], 22), f"cc_ctrl:mp_select_prof:{p_item['id']}", selected=is_act_p, is_alive=True),
                Txt(canvas.truncate_plain(rule_s, 20), role="val" if rule_s != "-" else "inactive"),
                Txt(canvas.truncate_plain(f"{ls_s} / {rs_s}", 18), role="schema"),
                Txt(f"{grab_s} · {p_item['mouse_sens']}", role="schema"),
            ])
        b.add_table(
            headers=[
                t("cc_input_controllers.mp_col_profile"),
                t("cc_input_controllers.mp_col_window"),
                t("cc_input_controllers.mp_col_sticks"),
                t("cc_input_controllers.mp_col_runtime"),
            ],
            widths=[24, 22, 18, "flex"],
            rows=p_rows,
            aligns=["left", "left", "left", "right"],
        )

    prof_summary = " · ".join(canvas.truncate_plain(p["name"], 18) for p in store["profiles"][:4])
    comp_box.add_collapsible(
        cc_panel,
        "ctrl_mp_profiles_list",
        t("cc_input_controllers.mp_profiles_list_title", p0=len(store["profiles"])),
        _build_prof_catalog,
        summary=prof_summary,
    )

    comp_box.add_section(t("cc_input_controllers.mp_sec_autoswitch"))
    auto_on = bool(store.get("auto_switch", True))
    fb_on = bool(mp.get("is_fallback", False))
    rule_txt = mp.get("match_app_id") or mp.get("match_title") or ""
    act_pills = " ".join([
        SelectPill(t("cc_input_controllers.mp_mode_auto_niri"), "cc_ctrl:mp_set_actmode:auto", selected=(auto_on and not fb_on)),
        SelectPill(t("cc_input_controllers.mp_mode_always"), "cc_ctrl:mp_set_actmode:always", selected=(not auto_on)),
        SelectPill(t("cc_input_controllers.mp_mode_fallback"), "cc_ctrl:mp_set_actmode:fallback", selected=(auto_on and fb_on)),
    ])
    comp_box.add_kv(t("cc_input_controllers.mp_actmode_lbl"), act_pills)

    if auto_on and not fb_on:
        rule_disp = (
            Txt(canvas.truncate_plain(rule_txt, 30), role="val")
            if rule_txt
            else Txt(t("cc_input_controllers.mp_no_rule_hint"), role="inactive")
        )
        rule_acts = (
            f"{InlineField('cc_ctrl_app', 'cc_ctrl:mp_inline_app', value=rule_txt, empty_label=t('cc_input_controllers.mp_set_rule_btn'), placeholder='steam_app_..', width=18, icon='\u270e', tone='neutral', selected=bool(rule_txt))} "
            f"{ActionBtn(t('cc_input_controllers.mp_pick_win_btn'), 'cc_ctrl:mp_pick_win', tone='neutral')}"
        )
        comp_box.add_kv(t("cc_input_controllers.mp_target_win_lbl"), rule_disp, right_markup=rule_acts)

    grab_excl = bool(mp.get("exclusive_grab", True))
    grab_pills = " ".join([
        SelectPill(t("cc_input_controllers.mp_grab_excl_full"), "cc_ctrl:mp_set_grab:1", selected=grab_excl),
        SelectPill(t("cc_input_controllers.mp_grab_pass_full"), "cc_ctrl:mp_set_grab:0", selected=not grab_excl),
    ])
    comp_box.add_kv(t("cc_input_controllers.mp_dev_access_lbl"), grab_pills)

    msens = int(mp.get("mouse_sens", 18))
    sl_w = 12 if canvas.inner_cols < 86 else 14
    sens_sl = Slider(msens, "cc_ctrl:mp_sens", min_val=2, max_val=60, width=sl_w, show_pct=False, step=2)
    turbo_hz = int(mp.get("turbo_hz", 12))
    comp_box.add_kv(
        t("cc_input_controllers.mp_mouse_look_lbl"),
        f"{sens_sl} {Txt(f'{msens}  (125 Hz)', role='val')}",
        right_markup=CyclePill(t("cc_input_controllers.mp_turbo_pill", p0=turbo_hz), "cc_ctrl:mp_turbo_next", active=True),
    )
    if m_run:
        win_lbl = rt.get("app_id") or rt.get("title") or "-"
        if rt.get("paused"):
            live_val = Txt(t("cc_input_controllers.mp_live_paused", p0=win_lbl), role="warn")
        elif rt.get("active_profile_name"):
            live_val = Txt(t("cc_input_controllers.mp_live_matched", p0=win_lbl, p1=rt["active_profile_name"]), role="ok")
        else:
            live_val = Txt(t("cc_input_controllers.mp_live_standby", p0=win_lbl), role="schema")
        comp_box.add_kv(t("cc_input_controllers.mp_live_lbl"), live_val)

    # --- CARD 2: Interactive Binding Blueprint ---
    sel = str(STATE.get("controllers", "mp_selected_ctrl", "south") or "south")
    if sel not in PAD_CONTROL_IDS:
        sel = "south"
    layer = str(STATE.get("controllers", "mp_layer", "base") or "base")
    if layer not in ("base", "shift"):
        layer = "base"
    is_cap = str(STATE.get("keyboard", "capture_mode", "") or "") == "ctrl_bind"

    mapped_ctrls: set[str] = set()
    if mp["l_stick"]["mode"] != "off":
        mapped_ctrls.add("l_stick")
    if mp["r_stick"]["mode"] != "off":
        mapped_ctrls.add("r_stick")
    for cid, entry in (mp.get("binds") or {}).items():
        if isinstance(entry, dict) and (entry.get("base") or entry.get("shift")):
            mapped_ctrls.add(cid)

    sel_lbl = t(f"cc_input_controllers.ctrl_{sel}")
    if sel in ("l_stick", "r_stick"):
        sm_cur = mp[sel]["mode"]
        cur_txt = t(f"cc_input_controllers.mp_stick_{sm_cur}")
        bp_foot_txt = f"{sel_lbl}: {cur_txt}"
    else:
        entry = (mp.get("binds") or {}).get(sel) or {}
        base_s = format_action_short(entry.get("base"), macros_by_id)
        shift_s = format_action_short(entry.get("shift"), macros_by_id)
        cur_act = entry.get(layer)
        cur_txt = format_action_short(cur_act, macros_by_id)
        bp_foot_txt = t("cc_input_controllers.mp_bp_footer", p0=sel_lbl, p1=base_s, p2=shift_s)

    bp_box = Box(
        t("cc_input_controllers.mp_bp_box_title", p0=canvas.truncate_plain(mp["name"], 24)),
        icon="\uf11b",
        actions=[Txt(f"{len(mapped_ctrls)}/{len(ALL_PAD_CONTROLS)}", role="schema")],
        highlighted=False,
    )
    if sel in ("l_stick", "r_stick"):
        bp_box.add_kv(
            t("cc_input_controllers.mp_sel_ctrl_lbl"),
            f"{Txt(sel_lbl, role='focus')}  {Txt('·', role='schema')}  {Txt(cur_txt, role='val' if sm_cur != 'off' else 'inactive')}",
            right_markup=ActionBtn(t("cc_input_controllers.mp_unbind_btn"), f"cc_ctrl:mp_unbind:{sel}", tone="danger_soft" if sm_cur != "off" else "inactive"),
        )
        stick_pills = " ".join(
            SelectPill(t(f"cc_input_controllers.mp_stick_long_{sm}"), f"cc_ctrl:mp_stick_mode:{sm}", selected=(sm_cur == sm))
            for sm in STICK_MODES
        )
        bp_box.add_kv(t("cc_input_controllers.mp_stick_mode_lbl"), stick_pills)
        if sm_cur in ("wasd", "arrows"):
            th_val = int(mp[sel].get("threshold", 35))
            th_sl = Slider(th_val, "cc_ctrl:mp_stick_thresh", min_val=15, max_val=80, width=14, show_pct=False, step=5)
            bp_box.add_kv(t("cc_input_controllers.mp_stick_thresh_lbl"), f"{th_sl} {Txt(f'{th_val}%', role='val')}")
        elif sm_cur == "mouse":
            inv_y = bool(mp["r_stick"].get("invert_y", False)) if sel == "r_stick" else False
            if sel == "r_stick":
                inv_pills = " ".join([
                    SelectPill(t("cc_input_controllers.mp_stick_norm_y"), "cc_ctrl:mp_stick_invy", selected=not inv_y),
                    SelectPill(t("cc_input_controllers.mp_stick_inv_y"), "cc_ctrl:mp_stick_invy", selected=inv_y),
                ])
                bp_box.add_kv(t("cc_input_controllers.mp_stick_axis_lbl"), inv_pills)
        elif sm_cur == "scroll":
            sc_val = int(mp.get("scroll_sens", 4))
            sc_sl = Slider(sc_val, "cc_ctrl:mp_scroll_sens", min_val=1, max_val=20, width=14, show_pct=False, step=1)
            bp_box.add_kv(t("cc_input_controllers.mp_scroll_speed_lbl"), f"{sc_sl} {Txt(str(sc_val), role='val')}")
    else:
        layer_summary = Txt(t("cc_input_controllers.mp_bp_summary", p0=base_s, p1=shift_s), role="schema")
        bp_box.add_kv(
            t("cc_input_controllers.mp_sel_ctrl_lbl"),
            f"{Txt(sel_lbl, role='focus')}   {layer_summary}",
            right_markup=ActionBtn(t("cc_input_controllers.mp_unbind_btn"), f"cc_ctrl:mp_unbind:{sel}", tone="danger_soft" if cur_act else "inactive"),
        )
        layer_pills = " ".join([
            SelectPill(t("cc_input_controllers.mp_layer_base_full"), "cc_ctrl:mp_layer:base", selected=(layer == "base")),
            SelectPill(t("cc_input_controllers.mp_layer_shift_full"), "cc_ctrl:mp_layer:shift", selected=(layer == "shift")),
        ])
        bp_box.add_kv(t("cc_input_controllers.mp_edit_layer_lbl"), layer_pills)

        bp_box.add_section(
            t("cc_input_controllers.mp_sec_composer", p0=sel_lbl, p1=t(f"cc_input_controllers.mp_layer_{layer}")),
            actions=[ActionBtn(t("cc_input_controllers.mp_macro_studio_btn"), "cc_ctrl:open_macros", tone="neutral")],
        )

        act_kind = str(STATE.get("controllers", "mp_act_kind", "") or "")
        if act_kind not in ("key", "mouse", "macro", "shift"):
            ck = cur_act.get("kind", "") if isinstance(cur_act, dict) else ""
            if ck in ("mouse_btn", "mouse_wheel"):
                act_kind = "mouse"
            elif ck == "macro":
                act_kind = "macro"
            elif ck == "shift":
                act_kind = "shift"
            else:
                act_kind = "key"

        kind_pills = " ".join([
            SelectPill(t("cc_input_controllers.mp_kind_key"), "cc_ctrl:mp_kind:key", selected=(act_kind == "key")),
            SelectPill(t("cc_input_controllers.mp_kind_mouse"), "cc_ctrl:mp_kind:mouse", selected=(act_kind == "mouse")),
            SelectPill(t("cc_input_controllers.mp_kind_macro"), "cc_ctrl:mp_kind:macro", selected=(act_kind == "macro")),
            SelectPill(t("cc_input_controllers.mp_kind_shift"), "cc_ctrl:mp_kind:shift", selected=(act_kind == "shift")),
        ])
        bp_box.add_kv(t("cc_input_controllers.mp_kind_lbl"), kind_pills)

        if act_kind == "key":
            cap_btn = (
                ActionBtn(t("cc_input_controllers.mp_cap_key_active"), "cc_ctrl:mp_cap_stop", tone="warn", modal_active=True)
                if is_cap
                else ActionBtn(t("cc_input_controllers.mp_cap_key_btn"), "cc_ctrl:mp_cap_key", tone="apply")
            )
            quick_keys = " ".join(
                ActionBtn(lbl, f"cc_ctrl:mp_quick_key:{kcode}", tone="neutral")
                for lbl, kcode in (("Space", "Space"), ("Enter", "Return"), ("Esc", "Escape"), ("Tab", "Tab"), ("Shift", "Shift_L"), ("Ctrl", "Ctrl_L"))
            )
            bp_box.add_kv(t("cc_input_controllers.mp_key_row_lbl"), cap_btn, right_markup=quick_keys)
            bm_cur = cur_act.get("mode", "hold") if isinstance(cur_act, dict) and cur_act.get("kind") == "key" else "hold"
            b_pills = " ".join(
                SelectPill(t(f"cc_input_controllers.mp_mode_long_{bm}"), f"cc_ctrl:mp_mode:{bm}", selected=(bm_cur == bm), style="secondary")
                for bm in BIND_MODES
            )
            bp_box.add_kv(t("cc_input_controllers.mp_behavior_lbl"), b_pills)
        elif act_kind == "mouse":
            cur_tgt = cur_act.get("target", "") if isinstance(cur_act, dict) else ""
            m_pills = " ".join(
                SelectPill(lbl, f"cc_ctrl:mp_bind_mouse:{tgt}", selected=(cur_tgt == tgt))
                for tgt, lbl in (
                    ("LMB", "LMB"),
                    ("RMB", "RMB"),
                    ("MMB", "MMB"),
                    ("Mouse4", "M4"),
                    ("Mouse5", "M5"),
                    ("WheelUp", "Whl\u2191"),
                    ("WheelDown", "Whl\u2193"),
                )
            )
            bp_box.add_kv(t("cc_input_controllers.mp_mouse_row_lbl"), m_pills)
            bm_cur = cur_act.get("mode", "hold") if isinstance(cur_act, dict) and cur_act.get("kind") in ("mouse_btn", "mouse_wheel") else "hold"
            b_pills = " ".join(
                SelectPill(t(f"cc_input_controllers.mp_mode_long_{bm}"), f"cc_ctrl:mp_mode:{bm}", selected=(bm_cur == bm), style="secondary")
                for bm in BIND_MODES
            )
            bp_box.add_kv(t("cc_input_controllers.mp_behavior_lbl"), b_pills)
        elif act_kind == "macro":
            cur_mid = cur_act.get("macro_id", "") if isinstance(cur_act, dict) and cur_act.get("kind") == "macro" else ""
            if macros:
                m_pills_list = [
                    SelectPill(canvas.truncate_plain(m["name"], 15), f"cc_ctrl:mp_bind_macro:{m['id']}", selected=(m["id"] == cur_mid))
                    for m in macros[:3]
                ]
                m_right = CyclePill(t("cc_input_controllers.mp_macro_more"), "cc_ctrl:mp_cycle_macro", active=(len(macros) > 3)) if len(macros) > 3 else ""
                bp_box.add_kv(t("cc_input_controllers.mp_macro_lbl"), " ".join(m_pills_list), right_markup=m_right)
            else:
                bp_box.add_kv(t("cc_input_controllers.mp_macro_lbl"), Txt(t("cc_input_controllers.mp_macro_none"), role="inactive"))
            pmode = cur_act.get("mode", "once") if isinstance(cur_act, dict) and cur_act.get("kind") == "macro" else "once"
            m_modes = " ".join(
                SelectPill(t(f"cc_input_controllers.mp_macro_mode_{pm}"), f"cc_ctrl:mp_macro_mode:{pm}", selected=(pmode == pm), style="secondary")
                for pm in MACRO_PAD_MODES
            )
            bp_box.add_kv(t("cc_input_controllers.mp_macro_play_lbl"), m_modes)
        else:
            is_shift_now = isinstance(cur_act, dict) and cur_act.get("kind") == "shift"
            bp_box.add_kv(
                t("cc_input_controllers.mp_kind_shift"),
                Txt(t("cc_input_controllers.mp_shift_desc"), role="val" if is_shift_now else "schema"),
                right_markup=ActionBtn(t("cc_input_controllers.mp_apply_shift_btn"), "cc_ctrl:mp_bind_shift", tone="apply" if not is_shift_now else "inactive"),
            )

    bp_box.add_row(" ")
    bp_box.open_bottom = True
    bp_widget = ControllerBlueprintWidget(
        dev=dev,
        profile=prof_hw,
        widget_width=int(round(canvas.total_px)),
        fixed_height=max(222, int(round(canvas.char_h * 11.8))),
        inset_x=max(14, int(round(canvas.char_w * 2.0))),
        font_px=max(10.0, float(canvas.char_w) * 1.38),
        bind_mode=True,
        selected_ctrl=sel,
        mapped_ctrls=mapped_ctrls,
        on_select_ctrl=lambda cid: cc_panel.on_link(f"cc_ctrl:mp_sel:{cid}") if cc_panel else None,
        bind_footer_text=bp_foot_txt,
    )
    bp_foot = Box("", highlighted=False)
    bp_foot.open_top = True
    bp_foot.add_row(" ")

    # --- CARD 3: Profile Bindings Matrix (N) ---
    flt = str(STATE.get("controllers", "mp_filter", "all") or "all")
    if flt not in ("all", "sticks_triggers", "face_dpad", "shoulders_paddles"):
        flt = "all"
    filtered = [(cid, grp) for cid, grp in ALL_PAD_CONTROLS if flt == "all" or grp == flt]
    per_page = 9
    n_pages = max(1, (len(filtered) + per_page - 1) // per_page)
    page = max(0, min(n_pages - 1, int(STATE.get("controllers", "mp_page", 0) or 0)))
    slice_rows = filtered[page * per_page:(page + 1) * per_page]

    mat_box = Box(
        t("cc_input_controllers.mp_matrix_title", p0=len(mapped_ctrls)),
        icon="\uf03a",
        actions=[
            ActionBtn(t("cc_input_controllers.reset_btn"), "cc_ctrl:mp_reset", tone="warn" if is_dirty else "inactive"),
            ActionBtn(t("cc_input_controllers.mp_save_reload_btn"), "cc_ctrl:mp_save", tone="apply" if is_dirty else "inactive"),
        ],
        highlighted=False,
    )
    flt_pills = " ".join([
        SelectPill(t("cc_input_controllers.mp_flt_all"), "cc_ctrl:mp_flt:all", selected=(flt == "all")),
        SelectPill(t("cc_input_controllers.mp_flt_sticks"), "cc_ctrl:mp_flt:sticks_triggers", selected=(flt == "sticks_triggers")),
        SelectPill(t("cc_input_controllers.mp_flt_face"), "cc_ctrl:mp_flt:face_dpad", selected=(flt == "face_dpad")),
        SelectPill(t("cc_input_controllers.mp_flt_shoulders"), "cc_ctrl:mp_flt:shoulders_paddles", selected=(flt == "shoulders_paddles")),
    ])
    page_ctrl = ""
    if n_pages > 1:
        p0, p1 = page * per_page + 1, min(len(filtered), (page + 1) * per_page)
        prev_b = ActionBtn("\u2190", f"cc_ctrl:mp_page:{max(0, page - 1)}", tone="neutral" if page > 0 else "inactive")
        next_b = ActionBtn("\u2192", f"cc_ctrl:mp_page:{min(n_pages - 1, page + 1)}", tone="neutral" if page < n_pages - 1 else "inactive")
        page_ctrl = f"{prev_b} {Txt(t('cc_input_controllers.mp_page_range', p0=p0, p1=p1, p2=len(filtered)), role='schema')} {next_b}"
    mat_box.add_kv(t("cc_input_controllers.mp_filter_lbl"), flt_pills, right_markup=page_ctrl)

    t_rows = []
    for cid, _grp in slice_rows:
        c_lbl = t(f"cc_input_controllers.ctrl_{cid}")
        is_s = cid == sel
        if cid in ("l_stick", "r_stick"):
            sm = mp[cid]["mode"]
            b_str = t(f"cc_input_controllers.mp_stick_{sm}") if sm != "off" else "-"
            s_str = "-"
            m_str = "125Hz" if sm == "mouse" else ("8-Way" if sm in ("wasd", "arrows") else "-")
            is_bound = sm != "off"
            act_cell = f"{ActionBtn(t('cc_input_controllers.mp_unbind_btn'), f'cc_ctrl:mp_unbind:{cid}', tone='danger_soft' if is_bound else 'inactive')}"
        else:
            entry = (mp.get("binds") or {}).get(cid) or {}
            b_a = entry.get("base")
            s_a = entry.get("shift")
            b_str = format_action_short(b_a, macros_by_id)
            s_str = format_action_short(s_a, macros_by_id)
            raw_mode = (b_a or s_a or {}).get("mode", "") if isinstance(b_a or s_a, dict) else ""
            m_str = t(f"cc_input_controllers.mp_mode_{raw_mode}") if raw_mode in BIND_MODES else (
                t(f"cc_input_controllers.mp_macro_mode_{raw_mode}") if raw_mode in MACRO_PAD_MODES else "-"
            )
            is_bound = bool(b_a or s_a)
            act_cell = f"{ActionBtn(t('cc_input_controllers.mp_bind_short'), f'cc_ctrl:mp_row_bind:{cid}', tone='neutral')} {ActionBtn('x', f'cc_ctrl:mp_unbind:{cid}', tone='danger_soft' if is_bound else 'inactive')}"
        t_rows.append([
            InspectorCell(c_lbl, f"cc_ctrl:mp_sel:{cid}", selected=is_s, is_alive=is_bound),
            Txt(canvas.truncate_plain(b_str, 18), role="val" if b_str != "-" else "inactive"),
            Txt(canvas.truncate_plain(s_str, 15), role="val" if s_str != "-" else "inactive"),
            Txt(canvas.truncate_plain(m_str, 10), role="schema"),
            act_cell,
        ])
    mat_box.add_table(
        headers=[
            t("cc_input_controllers.mp_col_control"),
            t("cc_input_controllers.mp_col_base"),
            t("cc_input_controllers.mp_col_shift"),
            t("cc_input_controllers.mp_col_mode"),
            t("cc_input_controllers.mp_col_actions"),
        ],
        widths=[18, 19, 16, 11, "flex"],
        rows=t_rows,
        aligns=["left", "left", "left", "left", "right"],
    )

    return [comp_box, AsciiCard(bp_box, canvas, embedded_widget=bp_widget, footer_box_model=bp_foot), mat_box]


def build_cards(cc_panel, canvas: Canvas) -> list:
    cards = _feedback_cards() + build_privilege_modal_card(cc_panel, canvas)
    devices = discover_controllers()
    key, dev = _active_key_and_device(devices)
    def_fam = dev["family"] if dev else "xbox"
    prof = _get_profile_draft(key, def_fam)
    saved_base = _norm_profile(_load_saved_profiles().get(key), def_fam)
    is_dirty = _is_profile_dirty(key, prof, def_fam)
    active_name = dev["name"] if dev else t("cc_input_controllers.preview_device_name")
    view_mode = str(STATE.get("controllers", "view_mode", "hardware") or "hardware")
    if view_mode not in ("hardware", "mapping"):
        view_mode = "hardware"
    mapper_running, _ = is_pad_mapper_running()

    # =====================================================================
    # BOX 1 — Connected & Paired Controllers + Workbench Switch
    # =====================================================================
    dev_box = Box(
        t("cc_input_controllers.box_devices_title", p0=len(devices)),
        icon="\uf11b",
        actions=[
            ActionBtn(t("cc_input_controllers.rescan_btn"), "cc_ctrl:rescan", tone="neutral"),
            ActionBtn(t("cc_input_controllers.bt_pair_btn"), "cc_ctrl:open_bt", tone="neutral"),
        ],
        highlighted=False,
    )
    if not devices:
        dev_box.add_row(f"  {Txt(t('cc_input_controllers.no_ctrl_detected'), role='val')}")
        dev_box.add_row(f"  {Txt(t('cc_input_controllers.no_ctrl_preview_hint'), role='schema')}")
    else:
        d_rows = []
        for d in devices[:6]:
            bus_lbl = t(f"cc_input_controllers.bus_{d['bus']}")
            if d["battery"] is not None:
                bat_role = "fault" if d["battery"] < 15 else ("warn" if d["battery"] < 35 else "ok")
                bat_cell = Txt(f"{d['battery']}%", role=bat_role)
            elif d["bus"] == "usb":
                bat_cell = Txt(t("cc_input_controllers.bat_wired"), role="schema")
            else:
                bat_cell = Txt(t("cc_input_controllers.bat_na"), role="inactive")

            caps = []
            if d["has_rumble"]:
                caps.append(t("cc_input_controllers.cap_rumble"))
            if d.get("has_paddles"):
                caps.append(t("cc_input_controllers.cap_paddles"))
            if d["has_gyro"]:
                caps.append(t("cc_input_controllers.cap_gyro"))
            if d["leds"]:
                caps.append(t("cc_input_controllers.cap_led"))
            caps_txt = " · ".join(caps) if caps else t("cc_input_controllers.cap_basic")

            if d["bt_mac"]:
                act_cell = (
                    ActionBtn(t("cc_input_controllers.disconnect_btn"), f"cc_ctrl:bt_disc:{d['bt_mac']}", tone="warn")
                    if d["connected"]
                    else ActionBtn(t("cc_input_controllers.connect_btn"), f"cc_ctrl:bt_conn:{d['bt_mac']}", tone="apply")
                )
            else:
                act_cell = StateBadge(d["connected"], t("cc_input_controllers.connected_lbl"), t("cc_input_controllers.paired_lbl"))

            d_rows.append([
                InspectorCell(canvas.truncate_plain(d["name"], 24), f"cc_ctrl:select:{d['id']}", selected=(d["id"] == key), is_alive=d["connected"]),
                Txt(bus_lbl, role="schema"),
                bat_cell,
                Txt(canvas.truncate_plain(caps_txt, 20), role="schema"),
                act_cell,
            ])
        dev_box.add_table(
            headers=[
                t("cc_input_controllers.col_controller"),
                t("cc_input_controllers.col_bus"),
                t("cc_input_controllers.col_battery"),
                t("cc_input_controllers.col_caps"),
                t("cc_input_controllers.col_actions"),
            ],
            widths=[26, 8, 9, 20, "flex"],
            rows=d_rows,
            aligns=["left", "left", "left", "left", "right"],
        )
    cards.append(dev_box)

    # =====================================================================
    # BOX 1.5 — Cardinal Controller Workbench Switcher (1.5x TabBar)
    # =====================================================================
    wb_box = Box(
        t("cc_input_controllers.workbench_sec"),
        icon="\uf0e8",
        actions=[
            StateBadge(
                mapper_running,
                t("cc_input_controllers.mapper_active"),
                t("cc_input_controllers.mapper_standby"),
            ),
        ],
        highlighted=False,
    )
    wb_box.add_tabs(
        view_mode,
        [
            ("hardware", f"\uf11b  {t('cc_input_controllers.wb_hardware')}"),
            ("mapping", f"\uf11c  {t('cc_input_controllers.wb_mapping')}"),
        ],
        uri_prefix="cc_ctrl:mp_view",
        min_tab_cols=40,
    )
    wb_hint = t(
        "cc_input_controllers.wb_desc_mapping"
        if view_mode == "mapping"
        else "cc_input_controllers.wb_desc_hardware"
    )
    wb_box.add_row(f"  {Txt(wb_hint, role='schema')}")
    cards.append(wb_box)

    if view_mode == "mapping":
        cards.extend(_build_mapping_cards(cc_panel, canvas, dev, prof))
        return cards

    # =====================================================================
    # BOX 2 — Interactive Controller Test Deck (Pure-Cairo 3-Zone Blueprint)
    # =====================================================================
    hdr_badges = []
    if dev and dev["connected"] and dev["readable"]:
        hdr_badges.append(StateBadge(True, t("cc_input_controllers.badge_live")))
    elif dev and dev["connected"] and not dev["readable"]:
        hdr_badges.append(Badge(t("cc_input_controllers.badge_no_perm"), style="warn"))
    else:
        hdr_badges.append(StateBadge(False, "", t("cc_input_controllers.badge_preview")))
    if dev and dev.get("battery") is not None:
        hdr_badges.append(Txt(f"{dev['battery']}%", role="ok" if dev["battery"] >= 35 else "warn"))

    deck_box = Box(
        t("cc_input_controllers.test_deck_title", p0=canvas.truncate_plain(active_name, 26)),
        icon="\uf11b",
        actions=hdr_badges,
        highlighted=False,
    )
    d_lay = prof["layout"] != saved_base["layout"]
    cur_meta = _MODEL_BY_ID.get(prof["layout"], _CONTROLLER_MODELS[0])
    model_cycle = CyclePill(
        t(f"cc_input_controllers.layout_{prof['layout']}"),
        "cc_ctrl:layout_next",
        active=(prof["layout"] != def_fam),
        dirty=d_lay,
    )
    model_feats = Txt(t(f"cc_input_controllers.{cur_meta['feat_key']}"), role="schema")
    deck_box.add_kv(
        t("cc_input_controllers.layout_lbl"),
        f"{model_cycle}  {model_feats}",
        right_markup=ActionBtn(t("cc_input_controllers.zero_center_btn"), "cc_ctrl:zero_center", tone="neutral"),
    )

    sl_w = 14 if canvas.inner_cols < 86 else 16
    rumble_pct = prof["rumble_pct"]
    rumble_sl = Slider(rumble_pct, "cc_ctrl:rumble_pct", min_val=0, max_val=100, width=sl_w, show_pct=False, step=10)
    rumble_btns = (
        f"{ActionBtn(t('cc_input_controllers.rumble_left_btn'), 'cc_ctrl:rumble_test:left', tone='neutral')} "
        f"{ActionBtn(t('cc_input_controllers.rumble_right_btn'), 'cc_ctrl:rumble_test:right', tone='neutral')} "
        f"{ActionBtn(t('cc_input_controllers.rumble_dual_btn'), 'cc_ctrl:rumble_test:dual', tone='apply')}"
    )
    deck_box.add_kv(
        t("cc_input_controllers.haptics_lbl"),
        f"{rumble_sl} {Txt(f'{rumble_pct}%', role='val')}",
        right_markup=rumble_btns,
    )

    def _build_model_catalog(b: Box) -> None:
        m_rows = []
        for m in _CONTROLLER_MODELS:
            mid = m["id"]
            is_sel = prof["layout"] == mid
            m_rows.append([
                InspectorCell(
                    t(f"cc_input_controllers.model_full_{mid}"),
                    f"cc_ctrl:layout:{mid}",
                    selected=is_sel,
                    is_alive=True,
                ),
                Txt(m["brand"], role="schema"),
                Txt(t(f"cc_input_controllers.{m['arch_key']}"), role="schema"),
                Txt(t(f"cc_input_controllers.{m['feat_key']}"), role="val" if is_sel else "schema"),
            ])
        b.add_table(
            headers=[
                t("cc_input_controllers.col_model"),
                t("cc_input_controllers.col_brand"),
                t("cc_input_controllers.col_arch"),
                t("cc_input_controllers.col_features"),
            ],
            widths=[26, 11, 13, "flex"],
            rows=m_rows,
            aligns=["left", "left", "left", "left"],
        )

    catalog_summary = " · ".join(t(f"cc_input_controllers.layout_{m['id']}") for m in _CONTROLLER_MODELS)
    deck_box.add_collapsible(
        cc_panel,
        "ctrl_model_catalog",
        t("cc_input_controllers.model_catalog_title", p0=len(_CONTROLLER_MODELS)),
        _build_model_catalog,
        summary=catalog_summary,
    )
    deck_box.add_row(" ")
    deck_box.open_bottom = True

    bp_widget = ControllerBlueprintWidget(
        dev=dev,
        profile=prof,
        widget_width=int(round(canvas.total_px)),
        fixed_height=max(226, int(round(canvas.char_h * 12.2))),
        inset_x=max(14, int(round(canvas.char_w * 2.0))),
        font_px=max(10.0, float(canvas.char_w) * 1.38),
    )
    deck_foot = Box("", highlighted=False)
    deck_foot.open_top = True
    deck_foot.add_row(" ")
    cards.append(AsciiCard(deck_box, canvas, embedded_widget=bp_widget, footer_box_model=deck_foot))

    # =====================================================================
    # BOX 2.5 — Conditional Xbox Elite Hardware & Paddles Deck
    # =====================================================================
    if prof["layout"] == "xbox_elite":
        hw_slot = _LIVE_INPUT.get("hw_profile")
        elite_dirty = any(
            prof[k] != saved_base[k]
            for k in ("elite_slot", "p1", "p2", "p3", "p4", "trigger_lock", "stick_calc")
        )
        slot_tag = (
            Txt(t("cc_input_controllers.elite_hw_slot", p0=hw_slot), role="ok")
            if hw_slot is not None
            else Txt(t(f"cc_input_controllers.elite_slot_{prof['elite_slot']}"), role="schema")
        )
        elite_box = Box(
            t("cc_input_controllers.elite_box_title"),
            icon="\uf0e7",
            actions=[
                slot_tag,
                ActionBtn(t("cc_input_controllers.elite_reset_btn"), "cc_ctrl:reset_elite", tone="warn" if elite_dirty else "inactive"),
            ],
            highlighted=False,
        )
        d_slot = prof["elite_slot"] != saved_base["elite_slot"]
        slot_pills = " ".join(
            SelectPill(
                t(f"cc_input_controllers.elite_slot_{sl_i}"),
                f"cc_ctrl:elite_slot:{sl_i}",
                selected=(prof["elite_slot"] == sl_i),
                dirty=d_slot,
            )
            for sl_i in _ELITE_SLOTS
        )
        elite_box.add_kv(t("cc_input_controllers.elite_slot_lbl"), slot_pills)

        paddle_pills = []
        for pk in _PADDLE_KEYS:
            p_act = prof[pk]
            act_lbl = t(f"cc_input_controllers.paddle_act_{p_act}")
            paddle_pills.append(
                CyclePill(
                    f"{pk.upper()}: {act_lbl}",
                    f"cc_ctrl:paddle:{pk}",
                    active=(p_act != "none"),
                    dirty=(p_act != saved_base[pk]),
                )
            )
        elite_box.add_kv(t("cc_input_controllers.elite_paddles_lbl"), " ".join(paddle_pills))

        d_tl = prof["trigger_lock"] != saved_base["trigger_lock"]
        lock_pills = " ".join(
            SelectPill(
                t(f"cc_input_controllers.tlock_{tl_id}"),
                f"cc_ctrl:trigger_lock:{tl_id}",
                selected=(prof["trigger_lock"] == tl_id),
                dirty=d_tl,
            )
            for tl_id, _ in _TRIGGER_LOCKS
        )
        elite_box.add_kv(t("cc_input_controllers.elite_tlock_lbl"), lock_pills)

        d_sc = prof["stick_calc"] != saved_base["stick_calc"]
        calc_pills = " ".join(
            SelectPill(
                t(f"cc_input_controllers.scalc_{sc_id}"),
                f"cc_ctrl:stick_calc:{sc_id}",
                selected=(prof["stick_calc"] == sc_id),
                dirty=d_sc,
            )
            for sc_id in _STICK_CALCS
        )
        elite_box.add_kv(t("cc_input_controllers.elite_scalc_lbl"), calc_pills)
        cards.append(elite_box)

    # =====================================================================
    # BOX 3 — Calibration, Response Curve & Hardware (Pure-Cairo Graphs)
    # =====================================================================
    cal_box = Box(
        t("cc_input_controllers.cal_box_title"),
        icon="\uf1de",
        actions=[
            ActionBtn(t("cc_input_controllers.reset_btn"), "cc_ctrl:reset_profile", tone="warn" if is_dirty else "inactive"),
            ActionBtn(t("cc_input_controllers.save_profile_btn"), "cc_ctrl:save_profile", tone="apply" if is_dirty else "inactive"),
        ],
        highlighted=False,
    )
    d_crv = prof["curve"] != saved_base["curve"]
    cal_box.add_kv(
        t("cc_input_controllers.curve_lbl"),
        " ".join(
            SelectPill(t(f"cc_input_controllers.curve_{cid}"), f"cc_ctrl:curve:{cid}", selected=(prof["curve"] == cid), dirty=d_crv)
            for cid in _CURVES
        ),
    )

    dz_val = prof["inner_dz"]
    ot_val = prof["outer_th"]
    tc_val = prof["trigger_cut"]
    cal_box.add_kv(
        t("cc_input_controllers.inner_dz_lbl"),
        f"{Slider(dz_val, 'cc_ctrl:inner_dz', min_val=0, max_val=40, width=sl_w, show_pct=False, step=2)} {Txt(f'{dz_val}%', role='val')}",
    )
    cal_box.add_kv(
        t("cc_input_controllers.outer_th_lbl"),
        f"{Slider(ot_val, 'cc_ctrl:outer_th', min_val=60, max_val=100, width=sl_w, show_pct=False, step=5)} {Txt(f'{ot_val}%', role='val')}",
    )
    cal_box.add_kv(
        t("cc_input_controllers.trigger_cut_lbl"),
        f"{Slider(tc_val, 'cc_ctrl:trigger_cut', min_val=10, max_val=100, width=sl_w, show_pct=False, step=5)} {Txt(f'{tc_val}%', role='val')}",
    )

    d_led = prof["led_mode"] != saved_base["led_mode"]
    led_pills = " ".join(
        SelectPill(t(f"cc_input_controllers.led_{lm}"), f"cc_ctrl:led:{lm}", selected=(prof["led_mode"] == lm), dirty=d_led, style="secondary")
        for lm in _LED_MODES
    )
    led_hint = Txt(
        t("cc_input_controllers.led_hw_ready") if (dev and dev.get("leds")) else t("cc_input_controllers.led_profile_only"),
        role="ok" if (dev and dev.get("leds")) else "schema",
    )
    cal_box.add_kv(t("cc_input_controllers.led_lbl"), led_pills, right_markup=led_hint)

    def _build_adv(b: Box) -> None:
        ax_pills = " ".join([
            SelectPill(t("cc_input_controllers.inv_ly"), "cc_ctrl:toggle:inv_ly", selected=bool(prof["inv_ly"]), dirty=(prof["inv_ly"] != saved_base["inv_ly"]), style="secondary"),
            SelectPill(t("cc_input_controllers.inv_ry"), "cc_ctrl:toggle:inv_ry", selected=bool(prof["inv_ry"]), dirty=(prof["inv_ry"] != saved_base["inv_ry"]), style="secondary"),
            SelectPill(t("cc_input_controllers.swap_sticks"), "cc_ctrl:toggle:swap_sticks", selected=bool(prof["swap_sticks"]), dirty=(prof["swap_sticks"] != saved_base["swap_sticks"]), style="secondary"),
        ])
        b.add_kv(t("cc_input_controllers.axes_lbl"), ax_pills)
        g_pills = " ".join(
            SelectPill(t(f"cc_input_controllers.gyro_{gm}"), f"cc_ctrl:gyro:{gm}", selected=(prof["gyro_mode"] == gm), dirty=(prof["gyro_mode"] != saved_base["gyro_mode"]), style="secondary")
            for gm in _GYRO_MODES
        )
        b.add_kv(t("cc_input_controllers.gyro_lbl"), g_pills)
        has_joydev = os.path.isdir("/sys/module/joydev")
        has_uinput = os.access("/dev/uinput", os.W_OK)
        stack_txt = f"joydev: {'OK' if has_joydev else 'OFF'} · uinput: {'OK' if has_uinput else 'LOCKED'} · evdev: {len(glob.glob('/dev/input/event*'))}"
        b.add_kv(
            t("cc_input_controllers.stack_lbl"),
            Txt(stack_txt, role="schema"),
            right_markup=ActionBtn(t("cc_input_controllers.export_btn"), "cc_ctrl:export_json", tone="neutral"),
        )

    cal_box.add_collapsible(
        cc_panel,
        "ctrl_adv_stack",
        t("cc_input_controllers.adv_sec_title"),
        _build_adv,
        summary=t("cc_input_controllers.adv_summary"),
    )
    cal_box.add_row(" ")
    cal_box.open_bottom = True

    curve_widget = ResponseCurveWidget(
        profile=prof,
        widget_width=int(round(canvas.total_px)),
        fixed_height=max(116, int(round(canvas.char_h * 6.3))),
        inset_x=max(14, int(round(canvas.char_w * 2.0))),
        font_px=max(10.0, float(canvas.char_w) * 1.38),
    )
    cal_foot = Box("", highlighted=False)
    cal_foot.open_top = True
    cal_foot.add_row(" ")
    cards.append(AsciiCard(cal_box, canvas, embedded_widget=curve_widget, footer_box_model=cal_foot))
    return cards
