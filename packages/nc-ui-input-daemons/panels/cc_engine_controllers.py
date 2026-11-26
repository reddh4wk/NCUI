#!/usr/bin/env python3
"""NC-UI Gaming Controllers — Discovery, Hardware & Action Handlers."""

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


from panels.cc_engine_controllers_widgets import (
    _CONTROLLER_MODELS,
    _DEFAULT_PROFILE,
    _EV_STRUCT,
    _LAYOUTS,
    _LIVE_INPUT,
    _MODEL_BY_ID,
)

_DISCOVERY_CACHE: dict = {"ts": 0.0, "devices": []}

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

    raw_blocks = iter_proc_input_blocks()

    motion_uniqs = {
        b["uniq"].lower()
        for b in raw_blocks
        if b["uniq"] and ("motion" in b["name"].lower() or "accel" in b["name"].lower() or bitmask_has(b["prop"], 6))
    }

    devices: list[dict] = []
    connected_uniqs: set[str] = set()
    for b in raw_blocks:
        nl = b["name"].lower()
        if any(skip in nl for skip in ("touchpad", "motion sensors", "consumer control", "keyboard", "mouse")):
            continue
        has_js = any(h.startswith("js") for h in b["handlers"])
        has_gp_btn = bitmask_has(b["key"], _BTN_SOUTH) or bitmask_has(b["key"], 0x120)
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
        has_paddles = fam in ("xbox_elite", "steam") or any(bitmask_has(b["key"], pc) for pc in _PADDLE_EVDEV_CODES)
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
            "has_rumble": bitmask_has(b["ff"], _FF_RUMBLE),
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
    ctrl_file = _controllers_path()
    if not os.path.exists(ctrl_file):
        return {}
    try:
        with open(ctrl_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_profiles(profiles: dict[str, dict]) -> bool:
    try:
        ctrl_file = _controllers_path()
        os.makedirs(os.path.dirname(ctrl_file), exist_ok=True)
        payload = json.dumps(profiles, indent=2)
        with open(ctrl_file, "w", encoding="utf-8") as f:
            f.write(payload)
        with open(ctrl_file, "r", encoding="utf-8") as f:
            return f.read() == payload
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
    set_feedback("controllers", 
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
        set_feedback("controllers", True, t("cc_input_controllers.fb_mapper_win_set", p0=val or "-"))
        return True
    if action_id == "cc_ctrl_new_prof" and val:
        nid = f"prof_{int(time.time())}"
        new_p = build_template_profile("fps", nid, val[:36])
        profs.append(new_p)
        st["profiles"] = profs
        st["active_profile_id"] = nid
        _set_map_store_draft(st)
        set_feedback("controllers", True, t("cc_input_controllers.fb_mapper_prof_created", p0=new_p["name"]))
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

    def _commit_prof(p_new: dict) -> None:
        profs[idx] = p_new
        st["profiles"] = profs
        _set_map_store_draft(st)

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
        _ensure_uinput_or_request(cc_panel, fb_ns="controllers")
    elif act == "mp_start":
        from panels.cc_engine_input import _ensure_uinput_or_request
        if not PAD_MAPPER_BIN or not os.path.isfile(PAD_MAPPER_BIN):
            set_feedback("controllers", False, t("cc_input_controllers.fb_mapper_bin_missing"))
        elif _ensure_uinput_or_request(cc_panel, fb_ns="controllers"):
            save_pad_store(st)
            cmd = [PAD_MAPPER_BIN]
            if dev and dev.get("evdev"):
                cmd.extend(["--device", dev["evdev"]])
            probe.spawn_bg(cmd)
            set_feedback("controllers", True, t("cc_input_controllers.fb_mapper_started"))
    elif act == "mp_stop":
        stop_pad_mapper()
        set_feedback("controllers", True, t("cc_input_controllers.fb_mapper_stopped"))
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
            set_feedback("controllers", True, t("cc_input_controllers.fb_mapper_prof_deleted", p0=removed_name))
    elif act.startswith("mp_tpl:"):
        tpl = act.split(":", 1)[1]
        if not tpl:
            return False
        new_p = build_template_profile(tpl, prof["id"], prof["name"])
        new_p["match_app_id"] = prof.get("match_app_id", "")
        new_p["match_title"] = prof.get("match_title", "")
        _commit_prof(new_p)
        set_feedback("controllers", True, t("cc_input_controllers.fb_mapper_tpl", p0=tpl.upper()))
    elif act == "mp_toggle_auto":
        st["auto_switch"] = not bool(st.get("auto_switch", True))
        _set_map_store_draft(st)
    elif act == "mp_toggle_fallback":
        p = dict(prof)
        p["is_fallback"] = not bool(p.get("is_fallback", False))
        _commit_prof(p)
    elif act == "mp_toggle_grab":
        p = dict(prof)
        p["exclusive_grab"] = not bool(p.get("exclusive_grab", True))
        _commit_prof(p)
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
        try:
            sens = max(2, min(60, int(round(float(act.split(":", 1)[1])))))
        except (ValueError, TypeError, OverflowError):
            return False
        p = dict(prof)
        p["mouse_sens"] = sens
        _commit_prof(p)
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
        _commit_prof(p)
    elif act.startswith("mp_set_grab:"):
        p = dict(prof)
        p["exclusive_grab"] = (act.split(":", 1)[1] == "1")
        _commit_prof(p)
    elif act == "mp_turbo_next":
        hz_opts = (6, 10, 12, 16, 20, 25)
        cur_hz = int(prof.get("turbo_hz", 12))
        n_idx = (hz_opts.index(cur_hz) + 1) % len(hz_opts) if cur_hz in hz_opts else 2
        p = dict(prof)
        p["turbo_hz"] = hz_opts[n_idx]
        _commit_prof(p)
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
        try:
            thresh = max(15, min(80, int(round(float(act.split(":", 1)[1])))))
        except (ValueError, TypeError, OverflowError):
            return False
        if sel in ("l_stick", "r_stick"):
            p = dict(prof)
            scfg = dict(p[sel])
            scfg["threshold"] = thresh
            p[sel] = scfg
            _commit_prof(p)
            if cc_panel:
                cc_panel.request_throttled_refresh()
        return True
    elif act.startswith("mp_scroll_sens:"):
        try:
            ssens = max(1, min(20, int(round(float(act.split(":", 1)[1])))))
        except (ValueError, TypeError, OverflowError):
            return False
        p = dict(prof)
        p["scroll_sens"] = ssens
        _commit_prof(p)
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
            _commit_prof(p)
        else:
            _set_ctrl_bind_action(None)
    elif act.startswith("mp_stick_mode:"):
        sm = act.split(":", 1)[1]
        if sel in ("l_stick", "r_stick") and sm in STICK_MODES:
            p = dict(prof)
            scfg = dict(p[sel])
            scfg["mode"] = sm
            p[sel] = scfg
            _commit_prof(p)
    elif act == "mp_stick_invy":
        p = dict(prof)
        rs = dict(p["r_stick"])
        rs["invert_y"] = not bool(rs.get("invert_y", False))
        p["r_stick"] = rs
        _commit_prof(p)
    elif act.startswith("mp_flt:"):
        STATE.set("controllers", "mp_filter", act.split(":", 1)[1])
        STATE.set("controllers", "mp_page", 0)
    elif act.startswith("mp_page:"):
        try:
            STATE.set("controllers", "mp_page", max(0, int(act.split(":", 1)[1])))
        except (ValueError, TypeError):
            return False
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
        set_feedback("controllers", ok, t("cc_input_controllers.fb_mapper_saved"))
    elif act == "mp_reset":
        fresh = load_pad_store()
        _set_map_store_draft(fresh)
        set_feedback("controllers", True, t("cc_input_controllers.fb_mapper_reset"))
    else:
        return False

    if cc_panel and cc_panel.shell:
        cc_panel.shell.refresh_view()
    return True


def handle_cc_controllers_link(cc_panel, uri: str) -> bool:
    if not isinstance(uri, str) or not uri.startswith("cc_ctrl:"):
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
        set_feedback("controllers", True, t("cc_input_controllers.fb_rescanned", p0=n_conn, p1=n_bt))
    elif act == "open_bt":
        if cc_panel:
            cc_panel.set_section("network")
            cc_panel.set_subtab("wifi_bt")
    elif act.startswith("select:"):
        STATE.set("controllers", "selected_id", act.split(":", 1)[1])
    elif act.startswith("bt_conn:"):
        mac = act.split(":", 1)[1]
        if not mac:
            return False
        if not probe.has_bin("bluetoothctl"):
            set_feedback("controllers", False, t("cc_keyboard.probe_missing", p0="bluetoothctl"))
        else:
            ok, out, err = probe.run_cmd_full(["bluetoothctl", "connect", mac], timeout=6.0)
            discover_controllers(force=True)
            set_feedback("controllers", ok, t("cc_input_controllers.fb_bt_connected", p0=mac) if ok else t("cc_input_controllers.fb_bt_failed", p0=mac, p1=(err or out)[:48]))
    elif act.startswith("bt_disc:"):
        mac = act.split(":", 1)[1]
        if not mac:
            return False
        if not probe.has_bin("bluetoothctl"):
            set_feedback("controllers", False, t("cc_keyboard.probe_missing", p0="bluetoothctl"))
        else:
            ok, out, err = probe.run_cmd_full(["bluetoothctl", "disconnect", mac], timeout=4.0)
            discover_controllers(force=True)
            set_feedback("controllers", ok, t("cc_input_controllers.fb_bt_disconnected", p0=mac) if ok else t("cc_input_controllers.fb_bt_failed", p0=mac, p1=(err or out)[:48]))
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
        except (ValueError, TypeError):
            return False
        if sl_i in _ELITE_SLOTS:
            _set_profile_draft(key, def_fam, elite_slot=sl_i)
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
        try:
            dz = int(round(float(act.split(":", 1)[1])))
        except (ValueError, TypeError, OverflowError):
            return False
        _set_profile_draft(key, def_fam, inner_dz=dz)
        if cc_panel:
            cc_panel.request_throttled_refresh()
        return True
    elif act.startswith("outer_th:"):
        try:
            oth = int(round(float(act.split(":", 1)[1])))
        except (ValueError, TypeError, OverflowError):
            return False
        _set_profile_draft(key, def_fam, outer_th=oth)
        if cc_panel:
            cc_panel.request_throttled_refresh()
        return True
    elif act.startswith("trigger_cut:"):
        try:
            tcut = int(round(float(act.split(":", 1)[1])))
        except (ValueError, TypeError, OverflowError):
            return False
        tlock = "short" if tcut <= 35 else ("mid" if tcut <= 70 else "full")
        _set_profile_draft(key, def_fam, trigger_cut=tcut, trigger_lock=tlock)
        if cc_panel:
            cc_panel.request_throttled_refresh()
        return True
    elif act.startswith("rumble_pct:"):
        try:
            rpct = int(round(float(act.split(":", 1)[1])))
        except (ValueError, TypeError, OverflowError):
            return False
        _set_profile_draft(key, def_fam, rumble_pct=rpct)
        if cc_panel:
            cc_panel.request_throttled_refresh()
        return True
    elif act.startswith("rumble_test:"):
        side = act.split(":", 1)[1]
        prof = _get_profile_draft(key, def_fam)
        ok, msg = _send_kernel_rumble(dev, side, prof["rumble_pct"])
        set_feedback("controllers", ok, msg)
    elif act.startswith("led:"):
        lm = act.split(":", 1)[1]
        if lm in _LED_MODES:
            _set_profile_draft(key, def_fam, led_mode=lm)
            ok, msg = _apply_led_mode(dev, lm)
            set_feedback("controllers", ok, msg)
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
        set_feedback("controllers", True, t("cc_input_controllers.fb_zeroed", p0=f"{lx:+.2f},{ly:+.2f}", p1=f"{rx:+.2f},{ry:+.2f}"))
    elif act == "save_profile":
        prof = _get_profile_draft(key, def_fam)
        profs = _load_saved_profiles()
        profs[key] = prof
        ok = _save_profiles(profs)
        dname = dev["name"] if dev else t("cc_input_controllers.preview_device_name")
        set_feedback("controllers", ok, t("cc_input_controllers.fb_profile_saved", p0=dname))
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
        set_feedback("controllers", True, t("cc_input_controllers.fb_profile_reset", p0=dname))
    elif act == "export_json":
        profs = _load_saved_profiles()
        profs[key] = _get_profile_draft(key, def_fam)
        cfg_d = get_config_dir()
        exp_f = os.path.join(cfg_d, "controllers_export.json")
        try:
            os.makedirs(cfg_d, exist_ok=True)
            payload = json.dumps(profs, indent=2)
            with open(exp_f, "w", encoding="utf-8") as f:
                f.write(payload)
            ok = open(exp_f, "r", encoding="utf-8").read() == payload
            set_feedback("controllers", ok, t("cc_input_controllers.fb_exported", p0=exp_f))
        except OSError as e:
            set_feedback("controllers", False, str(e))
    else:
        return False

    if cc_panel and cc_panel.shell:
        cc_panel.shell.refresh_view()
    return True

