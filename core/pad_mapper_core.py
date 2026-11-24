#!/usr/bin/env python3
"""Zero-GTK shared core for NC-UI Gaming Controllers, Calibration, and KBM Compatibility Mapper."""

import json
import math
import os
import re
import signal
import time
from core.config import CONFIG_DIR

PAD_PROFILES_FILE = os.path.join(CONFIG_DIR, "pad_profiles.json")
PAD_MAPPER_PID_FILE = "/tmp/nc_pad_mapper.pid"
PAD_MAPPER_STATUS_FILE = "/tmp/nc_pad_mapper_status.json"
PAD_MAPPER_BIN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin", "nc-pad-mapper")
MACROS_FILE = os.path.join(CONFIG_DIR, "macros.json")
CONTROLLERS_FILE = os.path.join(CONFIG_DIR, "controllers.json")

# Verified against <linux/input.h> and <linux/input-event-codes.h> on x86_64
_EV_KEY = 0x01
_EV_REL = 0x02
_EV_ABS = 0x03
_EV_FF = 0x15
_FF_RUMBLE = 0x50
_EVIOCSFF = 0x40304580
_EVIOCRMFF = 0x40044581
_EVIOCGRAB = 0x40044590

_REL_X = 0x00
_REL_Y = 0x01
_REL_HWHEEL = 0x06
_REL_WHEEL = 0x08

_BTN_THUMB = 0x121
_BTN_THUMB2 = 0x122
_BTN_SOUTH = 0x130
_BTN_EAST = 0x131
_BTN_NORTH = 0x133
_BTN_WEST = 0x134
_BTN_TL = 0x136
_BTN_TR = 0x137
_BTN_TL2 = 0x138
_BTN_TR2 = 0x139
_BTN_SELECT = 0x13A
_BTN_START = 0x13B
_BTN_MODE = 0x13C
_BTN_THUMBL = 0x13D
_BTN_THUMBR = 0x13E
_BTN_GEAR_DOWN = 0x150
_BTN_GEAR_UP = 0x151
_BTN_DPAD_UP = 0x220
_BTN_DPAD_DOWN = 0x221
_BTN_DPAD_LEFT = 0x222
_BTN_DPAD_RIGHT = 0x223
_BTN_GRIPL = 0x224
_BTN_GRIPR = 0x225
_BTN_GRIPL2 = 0x226
_BTN_GRIPR2 = 0x227
_BTN_TRIGGER_HAPPY1 = 0x2C0
_BTN_TRIGGER_HAPPY2 = 0x2C1
_BTN_TRIGGER_HAPPY3 = 0x2C2
_BTN_TRIGGER_HAPPY4 = 0x2C3
_BTN_TRIGGER_HAPPY5 = 0x2C4
_BTN_TRIGGER_HAPPY6 = 0x2C5
_BTN_TRIGGER_HAPPY7 = 0x2C6
_BTN_TRIGGER_HAPPY8 = 0x2C7
_PADDLE_EVDEV_CODES = (
    _BTN_GRIPL,
    _BTN_GRIPR,
    _BTN_GRIPL2,
    _BTN_GRIPR2,
    _BTN_TRIGGER_HAPPY1,
    _BTN_TRIGGER_HAPPY2,
    _BTN_TRIGGER_HAPPY3,
    _BTN_TRIGGER_HAPPY4,
    _BTN_TRIGGER_HAPPY5,
    _BTN_TRIGGER_HAPPY6,
    _BTN_TRIGGER_HAPPY7,
    _BTN_TRIGGER_HAPPY8,
)
_KEY_BACK = 0x9E
_KEY_RECORD = 0xA7
_KEY_HOMEPAGE = 0xAC

_ABS_X = 0x00
_ABS_Y = 0x01
_ABS_Z = 0x02
_ABS_RX = 0x03
_ABS_RY = 0x04
_ABS_RZ = 0x05
_ABS_GAS = 0x09
_ABS_BRAKE = 0x0A
_ABS_HAT0X = 0x10
_ABS_HAT0Y = 0x11
_ABS_HAT2X = 0x14
_ABS_HAT2Y = 0x15
_ABS_PROFILE = 0x21
_PROBE_AXES = (_ABS_X, _ABS_Y, _ABS_Z, _ABS_RX, _ABS_RY, _ABS_RZ, _ABS_GAS, _ABS_BRAKE, _ABS_HAT0X, _ABS_HAT0Y, _ABS_HAT2X, _ABS_HAT2Y, _ABS_PROFILE)

_XBOX_ELITE_PIDS = ("02e3", "0b00", "0b05", "0b22")
_CURVES = ("linear", "aggressive", "relaxed", "instant", "smooth")
_LED_MODES = ("theme", "off", "low", "high")
_GYRO_MODES = ("off", "aim", "always")
_ELITE_SLOTS = (0, 1, 2, 3)
_PADDLE_KEYS = ("p1", "p2", "p3", "p4")
_PADDLE_ACTIONS = ("none", "a", "b", "x", "y", "lb", "rb", "l3", "r3", "view", "menu", "shift")
_TRIGGER_LOCKS = (("full", 100), ("mid", 55), ("short", 25))
_TRIGGER_LOCK_IDS = tuple(k for k, _ in _TRIGGER_LOCKS)
_TRIGGER_LOCK_CUT = dict(_TRIGGER_LOCKS)
_STICK_CALCS = ("radial", "axis", "true_diag")

MOUSE_TARGET_TO_EVDEV: dict[str, int] = {
    "LMB": 0x110,
    "RMB": 0x111,
    "MMB": 0x112,
    "Mouse4": 0x113,
    "Mouse5": 0x114,
}
WHEEL_TARGETS = ("WheelUp", "WheelDown")

KEY_NAME_TO_EVDEV: dict[str, int] = {
    "Escape": 1, "1": 2, "2": 3, "3": 4, "4": 5, "5": 6, "6": 7, "7": 8, "8": 9, "9": 10, "0": 11,
    "Minus": 12, "Equal": 13, "BackSpace": 14, "Tab": 15,
    "Q": 16, "W": 17, "E": 18, "R": 19, "T": 20, "Y": 21, "U": 22, "I": 23, "O": 24, "P": 25,
    "BracketLeft": 26, "BracketRight": 27, "Return": 28,
    "Ctrl": 29, "Ctrl_L": 29, "Control_L": 29,
    "A": 30, "S": 31, "D": 32, "F": 33, "G": 34, "H": 35, "J": 36, "K": 37, "L": 38,
    "Semicolon": 39, "Apostrophe": 40, "Grave": 41,
    "Shift": 42, "Shift_L": 42, "BackSlash": 43,
    "Z": 44, "X": 45, "C": 46, "V": 47, "B": 48, "N": 49, "M": 50,
    "Comma": 51, "Period": 52, "Slash": 53, "Shift_R": 54,
    "Alt": 56, "Alt_L": 56, "Space": 57, "Caps_Lock": 58,
    "F1": 59, "F2": 60, "F3": 61, "F4": 62, "F5": 63, "F6": 64, "F7": 65, "F8": 66, "F9": 67, "F10": 68,
    "F11": 87, "F12": 88,
    "Control_R": 97, "Ctrl_R": 97, "Alt_R": 100,
    "Home": 102, "Up": 103, "Page_Up": 104, "Left": 105, "Right": 106,
    "End": 107, "Down": 108, "Page_Down": 109, "Insert": 110, "Delete": 111,
    "Pause": 119, "Mod": 125, "Super": 125, "Super_L": 125, "Super_R": 126,
}

ALL_PAD_CONTROLS: list[tuple[str, str]] = [
    ("l_stick", "sticks_triggers"),
    ("r_stick", "sticks_triggers"),
    ("lt", "sticks_triggers"),
    ("rt", "sticks_triggers"),
    ("south", "face_dpad"),
    ("east", "face_dpad"),
    ("west", "face_dpad"),
    ("north", "face_dpad"),
    ("dup", "face_dpad"),
    ("ddown", "face_dpad"),
    ("dleft", "face_dpad"),
    ("dright", "face_dpad"),
    ("view", "face_dpad"),
    ("menu", "face_dpad"),
    ("share", "face_dpad"),
    ("lb", "shoulders_paddles"),
    ("rb", "shoulders_paddles"),
    ("l3", "shoulders_paddles"),
    ("r3", "shoulders_paddles"),
    ("p1", "shoulders_paddles"),
    ("p2", "shoulders_paddles"),
    ("p3", "shoulders_paddles"),
    ("p4", "shoulders_paddles"),
    ("l4", "shoulders_paddles"),
    ("r4", "shoulders_paddles"),
]
PAD_CONTROL_IDS = {cid for cid, _ in ALL_PAD_CONTROLS}
STICK_MODES = ("off", "wasd", "arrows", "mouse", "scroll")
BIND_MODES = ("hold", "toggle", "turbo")
MACRO_PAD_MODES = ("once", "while_held", "toggle_loop")


def eval_response_curve(raw_mag: float, inner_dz: float, outer_th: float, curve: str) -> float:
    """Evaluate calibrated stick output magnitude in [0, 1] from raw deflection in [0, 1]."""
    m = max(0.0, min(1.0, float(raw_mag)))
    lo = max(0.0, min(0.45, float(inner_dz)))
    hi = max(lo + 0.05, min(1.0, float(outer_th)))
    if m <= lo:
        return 0.0
    if m >= hi:
        return 1.0
    u = (m - lo) / (hi - lo)
    if curve == "aggressive":
        return u ** 0.55
    if curve == "relaxed":
        return u ** 1.85
    if curve == "instant":
        return min(1.0, 0.22 + 0.78 * (u ** 0.45))
    if curve == "smooth":
        return u * u * (3.0 - 2.0 * u)
    return u


def eval_trigger_curve(raw_pull: float, trigger_cut: float) -> float:
    """Evaluate hair-trigger output in [0, 1] with 4% resting deadzone and configurable cutoff."""
    p = max(0.0, min(1.0, float(raw_pull)))
    lo = 0.04
    hi = max(lo + 0.06, min(1.0, float(trigger_cut)))
    if p <= lo:
        return 0.0
    if p >= hi:
        return 1.0
    return (p - lo) / (hi - lo)


def apply_stick_calibration(rx: float, ry: float, prof: dict, is_right: bool = False) -> tuple[float, float]:
    """Apply center zeroing, axis inversion, deadzone, outer threshold, stick calc mode, and response curve."""
    ox = float(prof.get("cal_rx" if is_right else "cal_lx", 0.0) or 0.0)
    oy = float(prof.get("cal_ry" if is_right else "cal_ly", 0.0) or 0.0)
    x = max(-1.0, min(1.0, float(rx) - ox))
    y = max(-1.0, min(1.0, float(ry) - oy))
    if (is_right and prof.get("inv_ry")) or (not is_right and prof.get("inv_ly")):
        y = -y
    inner_dz = float(prof.get("inner_dz", 8)) / 100.0
    outer_th = float(prof.get("outer_th", 95)) / 100.0
    curve = str(prof.get("curve", "linear"))
    calc = str(prof.get("stick_calc", "radial")) if prof.get("layout") == "xbox_elite" else "radial"

    if calc == "axis":
        ax = eval_response_curve(abs(x), inner_dz, outer_th, curve) * (-1.0 if x < 0.0 else 1.0)
        ay = eval_response_curve(abs(y), inner_dz, outer_th, curve) * (-1.0 if y < 0.0 else 1.0)
        m_out = math.hypot(ax, ay)
        if m_out > 1.0:
            ax, ay = ax / m_out, ay / m_out
        return ax, ay

    mag = math.hypot(x, y)
    if mag < 1e-6:
        return 0.0, 0.0
    eff_in = max(abs(x), abs(y)) if calc == "true_diag" else min(1.0, mag)
    out_mag = eval_response_curve(eff_in, inner_dz, outer_th, curve)
    if out_mag <= 0.0:
        return 0.0, 0.0
    return (x / mag) * out_mag, (y / mag) * out_mag


def _detect_family(name: str, vendor: str, product: str = "") -> str:
    vl = (vendor or "").lower()
    pl = (product or "").lower()
    nl = f"{name} {vl} {pl}".lower()
    if vl == "28de" or any(k in nl for k in ("steam controller", "steam deck", "valve", "neptune")):
        return "steam"
    if vl == "054c" or any(k in nl for k in ("dualsense", "dualshock", "sony", "playstation", "ps4", "ps5")):
        return "playstation"
    if vl == "057e" or any(k in nl for k in ("nintendo", "switch", "joy-con", "pro controller")):
        return "nintendo"
    if (vl == "045e" and pl in _XBOX_ELITE_PIDS) or ("elite" in nl and any(k in nl for k in ("xbox", "microsoft", "045e"))):
        return "xbox_elite"
    return "xbox"


def resolve_evdev_axis_map(abs_ranges: dict[int, tuple[int, int]]) -> dict[str, int | None]:
    """Resolve logical gamepad axes from actual kernel EVIOCGABS capabilities across all Linux drivers."""
    has = set(abs_ranges.keys())
    amap: dict[str, int | None] = {
        "lx": _ABS_X if _ABS_X in has else None,
        "ly": _ABS_Y if _ABS_Y in has else None,
        "rx": None,
        "ry": None,
        "lt": None,
        "rt": None,
        "hat_x": None,
        "hat_y": None,
        "lpad_x": None,
        "lpad_y": None,
        "profile": _ABS_PROFILE if _ABS_PROFILE in has else None,
    }
    if _ABS_GAS in has or _ABS_BRAKE in has:
        amap["lt"] = _ABS_BRAKE if _ABS_BRAKE in has else None
        amap["rt"] = _ABS_GAS if _ABS_GAS in has else None
        if _ABS_RX in has and _ABS_RY in has:
            amap["rx"], amap["ry"] = _ABS_RX, _ABS_RY
        else:
            amap["rx"] = _ABS_Z if _ABS_Z in has else None
            amap["ry"] = _ABS_RZ if _ABS_RZ in has else None
    elif _ABS_HAT2X in has or _ABS_HAT2Y in has:
        amap["lt"] = _ABS_HAT2Y if _ABS_HAT2Y in has else None
        amap["rt"] = _ABS_HAT2X if _ABS_HAT2X in has else None
        amap["rx"] = _ABS_RX if _ABS_RX in has else None
        amap["ry"] = _ABS_RY if _ABS_RY in has else None
    elif _ABS_RX in has and _ABS_RY in has:
        rx_mn, rx_mx = abs_ranges[_ABS_RX]
        z_mx = abs_ranges[_ABS_Z][1] if _ABS_Z in has else 0
        if _ABS_Z in has and _ABS_RZ in has and rx_mn == 0 and rx_mx <= 255 and z_mx > 255:
            amap["rx"], amap["ry"] = _ABS_Z, _ABS_RZ
            amap["lt"], amap["rt"] = _ABS_RX, _ABS_RY
        else:
            amap["rx"], amap["ry"] = _ABS_RX, _ABS_RY
            amap["lt"] = _ABS_Z if _ABS_Z in has else None
            amap["rt"] = _ABS_RZ if _ABS_RZ in has else None
    elif _ABS_Z in has or _ABS_RZ in has:
        amap["rx"] = _ABS_Z if _ABS_Z in has else None
        amap["ry"] = _ABS_RZ if _ABS_RZ in has else None

    if _ABS_HAT0X in has:
        if abs_ranges[_ABS_HAT0X][1] <= 2:
            amap["hat_x"] = _ABS_HAT0X
        else:
            amap["lpad_x"] = _ABS_HAT0X
    if _ABS_HAT0Y in has:
        if abs_ranges[_ABS_HAT0Y][1] <= 2:
            amap["hat_y"] = _ABS_HAT0Y
        else:
            amap["lpad_y"] = _ABS_HAT0Y
    return amap


def resolve_evdev_btn_map(family: str) -> dict[int, str]:
    """Map Linux evdev key/button codes to logical blueprint targets across hardware families."""
    swap_xy = family in ("xbox", "xbox_elite", "steam")
    return {
        _BTN_SOUTH: "south",
        _BTN_EAST: "east",
        _BTN_NORTH: "west" if swap_xy else "north",
        _BTN_WEST: "north" if swap_xy else "west",
        _BTN_TL: "lb",
        _BTN_TR: "rb",
        _BTN_SELECT: "view",
        _KEY_BACK: "view",
        _BTN_START: "menu",
        _BTN_MODE: "guide",
        _KEY_HOMEPAGE: "guide",
        _KEY_RECORD: "share",
        _BTN_THUMBL: "l3",
        _BTN_THUMBR: "r3",
        _BTN_THUMB: "lpad_click",
        _BTN_THUMB2: "r3",
        _BTN_GEAR_DOWN: "l4",
        _BTN_GEAR_UP: "r4",
        _BTN_GRIPR: "p1",
        _BTN_GRIPR2: "p2",
        _BTN_GRIPL: "p3",
        _BTN_GRIPL2: "p4",
        _BTN_TRIGGER_HAPPY5: "p1",
        _BTN_TRIGGER_HAPPY6: "p2",
        _BTN_TRIGGER_HAPPY7: "p3",
        _BTN_TRIGGER_HAPPY8: "p4",
        _BTN_TRIGGER_HAPPY1: "p1",
        _BTN_TRIGGER_HAPPY2: "p2",
        _BTN_TRIGGER_HAPPY3: "p3",
        _BTN_TRIGGER_HAPPY4: "p4",
        _BTN_DPAD_UP: "dup",
        _BTN_DPAD_DOWN: "ddown",
        _BTN_DPAD_LEFT: "dleft",
        _BTN_DPAD_RIGHT: "dright",
    }


def resolve_combo_codes(combo: str, extra_code: int = 0) -> list[int]:
    """Resolve a human combo like 'Ctrl+Space' into ordered Linux evdev keycodes."""
    codes: list[int] = []
    for tok in str(combo or "").split("+"):
        t_clean = tok.strip()
        if not t_clean:
            continue
        kc = KEY_NAME_TO_EVDEV.get(t_clean) or KEY_NAME_TO_EVDEV.get(t_clean.upper())
        if kc and kc not in codes:
            codes.append(kc)
    if not codes and 0 < int(extra_code or 0) < 256:
        codes.append(int(extra_code))
    elif 0 < int(extra_code or 0) < 256 and int(extra_code) not in codes:
        codes.append(int(extra_code))
    return codes


def norm_action_spec(raw: dict | None) -> dict | None:
    if not isinstance(raw, dict):
        return None
    kind = str(raw.get("kind", "") or "").lower()
    if kind == "shift":
        return {"kind": "shift"}
    if kind == "key":
        combo = str(raw.get("combo", "") or "").strip()
        codes = [int(c) for c in (raw.get("codes") or []) if 0 < int(c) < 256]
        if not codes and combo:
            codes = resolve_combo_codes(combo)
        if not combo and not codes:
            return None
        mode = str(raw.get("mode", "hold"))
        return {"kind": "key", "combo": combo or f"Key#{codes[0]}", "codes": codes, "mode": mode if mode in BIND_MODES else "hold"}
    if kind in ("mouse_btn", "mouse_wheel"):
        tgt = str(raw.get("target", "") or "")
        if tgt not in MOUSE_TARGET_TO_EVDEV and tgt not in WHEEL_TARGETS:
            return None
        k_eff = "mouse_wheel" if tgt in WHEEL_TARGETS else "mouse_btn"
        mode = str(raw.get("mode", "hold"))
        return {"kind": k_eff, "target": tgt, "mode": mode if mode in BIND_MODES else "hold"}
    if kind == "macro":
        mid = str(raw.get("macro_id", "") or "").strip()
        if not mid:
            return None
        mode = str(raw.get("mode", "once"))
        return {"kind": "macro", "macro_id": mid, "mode": mode if mode in MACRO_PAD_MODES else "once"}
    return None


def norm_pad_profile(raw: dict | None, fallback_id: str = "prof_default", fallback_name: str = "Game Profile") -> dict:
    d = raw if isinstance(raw, dict) else {}
    pid = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(d.get("id") or fallback_id)) or fallback_id
    ls = d.get("l_stick") if isinstance(d.get("l_stick"), dict) else {}
    rs = d.get("r_stick") if isinstance(d.get("r_stick"), dict) else {}
    ls_mode = str(ls.get("mode", "off"))
    rs_mode = str(rs.get("mode", "off"))
    raw_binds = d.get("binds") if isinstance(d.get("binds"), dict) else {}
    binds: dict[str, dict] = {}
    for cid, grp in ALL_PAD_CONTROLS:
        if grp == "sticks_triggers" and cid in ("l_stick", "r_stick"):
            continue
        entry = raw_binds.get(cid) if isinstance(raw_binds.get(cid), dict) else {}
        base_a = norm_action_spec(entry.get("base"))
        shift_a = norm_action_spec(entry.get("shift"))
        if base_a is not None or shift_a is not None:
            binds[cid] = {"base": base_a, "shift": shift_a}
    return {
        "id": pid,
        "name": str(d.get("name") or fallback_name).strip()[:36] or fallback_name,
        "match_app_id": str(d.get("match_app_id", "") or "").strip()[:80],
        "match_title": str(d.get("match_title", "") or "").strip()[:80],
        "is_fallback": bool(d.get("is_fallback", False)),
        "exclusive_grab": bool(d.get("exclusive_grab", True)),
        "mouse_sens": max(2, min(60, int(round(float(d.get("mouse_sens", 18) or 18))))),
        "scroll_sens": max(1, min(20, int(round(float(d.get("scroll_sens", 4) or 4))))),
        "turbo_hz": max(2, min(30, int(round(float(d.get("turbo_hz", 12) or 12))))),
        "l_stick": {
            "mode": ls_mode if ls_mode in STICK_MODES else "off",
            "threshold": max(15, min(80, int(round(float(ls.get("threshold", 35) or 35))))),
        },
        "r_stick": {
            "mode": rs_mode if rs_mode in STICK_MODES else "off",
            "invert_y": bool(rs.get("invert_y", False)),
        },
        "binds": binds,
    }


def _key_spec(combo: str, mode: str = "hold") -> dict:
    return {"kind": "key", "combo": combo, "codes": resolve_combo_codes(combo), "mode": mode}


def _mouse_spec(target: str, mode: str = "hold") -> dict:
    return {"kind": "mouse_wheel" if target in WHEEL_TARGETS else "mouse_btn", "target": target, "mode": mode}


def build_template_profile(tpl_id: str, prof_id: str = "prof_fps", name: str = "") -> dict:
    tpl = str(tpl_id or "blank").lower()
    if tpl == "fps":
        raw = {
            "id": prof_id,
            "name": name or "FPS (WASD + Mouse)",
            "exclusive_grab": True,
            "mouse_sens": 18,
            "l_stick": {"mode": "wasd", "threshold": 35},
            "r_stick": {"mode": "mouse", "invert_y": False},
            "binds": {
                "rt": {"base": _mouse_spec("LMB"), "shift": None},
                "lt": {"base": _mouse_spec("RMB"), "shift": None},
                "south": {"base": _key_spec("Space"), "shift": None},
                "east": {"base": _key_spec("Ctrl_L"), "shift": None},
                "west": {"base": _key_spec("R"), "shift": None},
                "north": {"base": _key_spec("E"), "shift": None},
                "lb": {"base": _key_spec("Q"), "shift": None},
                "rb": {"base": _key_spec("G"), "shift": None},
                "l3": {"base": _key_spec("Shift_L"), "shift": None},
                "r3": {"base": _key_spec("V"), "shift": None},
                "dup": {"base": _key_spec("1"), "shift": None},
                "dright": {"base": _key_spec("2"), "shift": None},
                "ddown": {"base": _key_spec("3"), "shift": None},
                "dleft": {"base": _key_spec("4"), "shift": None},
                "view": {"base": _key_spec("Tab"), "shift": None},
                "menu": {"base": _key_spec("Escape"), "shift": None},
                "p1": {"base": _key_spec("Space"), "shift": None},
                "p2": {"base": _key_spec("Ctrl_L"), "shift": None},
                "p3": {"base": _key_spec("R"), "shift": None},
                "p4": {"base": {"kind": "shift"}, "shift": None},
            },
        }
    elif tpl == "platformer":
        raw = {
            "id": prof_id,
            "name": name or "2D Platformer",
            "exclusive_grab": True,
            "l_stick": {"mode": "arrows", "threshold": 32},
            "r_stick": {"mode": "off", "invert_y": False},
            "binds": {
                "dup": {"base": _key_spec("Up"), "shift": None},
                "ddown": {"base": _key_spec("Down"), "shift": None},
                "dleft": {"base": _key_spec("Left"), "shift": None},
                "dright": {"base": _key_spec("Right"), "shift": None},
                "south": {"base": _key_spec("Z"), "shift": None},
                "west": {"base": _key_spec("X"), "shift": None},
                "east": {"base": _key_spec("C"), "shift": None},
                "north": {"base": _key_spec("A"), "shift": None},
                "lb": {"base": _key_spec("S"), "shift": None},
                "rb": {"base": _key_spec("D"), "shift": None},
                "rt": {"base": _key_spec("Shift_L"), "shift": None},
                "view": {"base": _key_spec("Tab"), "shift": None},
                "menu": {"base": _key_spec("Escape"), "shift": None},
            },
        }
    elif tpl == "twinstick":
        raw = {
            "id": prof_id,
            "name": name or "Twin-Stick Action",
            "exclusive_grab": True,
            "l_stick": {"mode": "wasd", "threshold": 30},
            "r_stick": {"mode": "arrows", "invert_y": False},
            "binds": {
                "rt": {"base": _key_spec("Space"), "shift": None},
                "lt": {"base": _key_spec("Shift_L"), "shift": None},
                "rb": {"base": _key_spec("E"), "shift": None},
                "lb": {"base": _key_spec("Q"), "shift": None},
                "south": {"base": _key_spec("Return"), "shift": None},
                "east": {"base": _key_spec("Escape"), "shift": None},
                "p4": {"base": {"kind": "shift"}, "shift": None},
            },
        }
    elif tpl == "desktop":
        raw = {
            "id": prof_id,
            "name": name or "Desktop / Couch",
            "is_fallback": True,
            "exclusive_grab": False,
            "mouse_sens": 16,
            "scroll_sens": 4,
            "l_stick": {"mode": "scroll", "threshold": 30},
            "r_stick": {"mode": "mouse", "invert_y": False},
            "binds": {
                "rt": {"base": _mouse_spec("LMB"), "shift": None},
                "lt": {"base": _mouse_spec("RMB"), "shift": None},
                "r3": {"base": _mouse_spec("MMB"), "shift": None},
                "south": {"base": _key_spec("Return"), "shift": None},
                "east": {"base": _key_spec("Escape"), "shift": None},
                "west": {"base": _key_spec("BackSpace"), "shift": None},
                "north": {"base": _key_spec("Space"), "shift": None},
                "lb": {"base": _key_spec("Alt+Left"), "shift": None},
                "rb": {"base": _key_spec("Alt+Right"), "shift": None},
                "dup": {"base": _key_spec("Up"), "shift": None},
                "ddown": {"base": _key_spec("Down"), "shift": None},
                "dleft": {"base": _key_spec("Left"), "shift": None},
                "dright": {"base": _key_spec("Right"), "shift": None},
                "menu": {"base": _key_spec("Super_L"), "shift": None},
            },
        }
    else:
        raw = {
            "id": prof_id,
            "name": name or "Custom Profile",
            "exclusive_grab": True,
            "l_stick": {"mode": "off", "threshold": 35},
            "r_stick": {"mode": "off", "invert_y": False},
            "binds": {},
        }
    return norm_pad_profile(raw, prof_id, raw["name"])


def default_pad_store() -> dict:
    p1 = build_template_profile("fps", "prof_fps", "FPS (WASD + Mouse)")
    return {"active_profile_id": p1["id"], "auto_switch": True, "profiles": [p1]}


def norm_pad_store(raw: dict | None) -> dict:
    d = raw if isinstance(raw, dict) else {}
    raw_profs = d.get("profiles") if isinstance(d.get("profiles"), list) else []
    profs = [norm_pad_profile(p, f"prof_{i+1}", f"Profile {i+1}") for i, p in enumerate(raw_profs)]
    if not profs:
        return default_pad_store()
    seen: set[str] = set()
    uniq: list[dict] = []
    for p in profs:
        if p["id"] in seen:
            continue
        seen.add(p["id"])
        uniq.append(p)
    act_id = str(d.get("active_profile_id") or "")
    if not any(p["id"] == act_id for p in uniq):
        act_id = uniq[0]["id"]
    return {"active_profile_id": act_id, "auto_switch": bool(d.get("auto_switch", True)), "profiles": uniq}


def load_pad_store() -> dict:
    if not os.path.exists(PAD_PROFILES_FILE):
        return default_pad_store()
    try:
        data = json.load(open(PAD_PROFILES_FILE, "r", encoding="utf-8"))
        return norm_pad_store(data)
    except Exception:
        return default_pad_store()


def save_pad_store(store: dict) -> bool:
    norm = norm_pad_store(store)
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        payload = json.dumps(norm, indent=2)
        tmp = f"{PAD_PROFILES_FILE}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(payload)
        os.replace(tmp, PAD_PROFILES_FILE)
        return os.path.exists(PAD_PROFILES_FILE)
    except OSError:
        return False


def _rule_matches(rule: str, val: str, exact_plain: bool = True) -> bool:
    r = str(rule or "").strip()
    v = str(val or "").strip()
    if not r or not v:
        return False
    if r.startswith("~") and len(r) > 1:
        try:
            return bool(re.search(r[1:], v, re.IGNORECASE))
        except re.error:
            return False
    if any(ch in r for ch in ("*", "?", "|")):
        try:
            return bool(re.search(r, v, re.IGNORECASE))
        except re.error:
            pass
    if exact_plain:
        return r.lower() == v.lower() or v.lower().startswith(r.lower())
    return r.lower() in v.lower()


def match_window_to_profile(
    profiles: list[dict],
    app_id: str,
    title: str,
    active_id: str = "",
    auto_switch: bool = True,
) -> dict | None:
    if not profiles:
        return None
    if not auto_switch:
        return next((p for p in profiles if p["id"] == active_id), profiles[0])
    # Priority 1: match_app_id
    for p in profiles:
        if p.get("match_app_id") and _rule_matches(p["match_app_id"], app_id, exact_plain=True):
            return p
    # Priority 2: match_title
    for p in profiles:
        if p.get("match_title") and _rule_matches(p["match_title"], title, exact_plain=False):
            return p
    # Priority 3: fallback profile (Desktop / Couch)
    fb = next((p for p in profiles if p.get("is_fallback")), None)
    return fb


def step_mouse_look(
    cal_x: float,
    cal_y: float,
    sens: float,
    rem_x: float = 0.0,
    rem_y: float = 0.0,
    invert_y: bool = False,
    dt_sec: float = 0.008,
) -> tuple[int, int, float, float]:
    """Convert calibrated [-1..1] stick vector into integer REL_X/REL_Y pixels using sub-pixel carry."""
    scale = float(sens) * (dt_sec / 0.008) * 1.15
    sy = -cal_y if invert_y else cal_y
    fx = float(cal_x) * scale + float(rem_x)
    fy = float(sy) * scale + float(rem_y)
    dx = int(math.trunc(fx))
    dy = int(math.trunc(fy))
    return dx, dy, fx - dx, fy - dy


def eval_8way_directions(
    cal_x: float,
    cal_y: float,
    active_dirs: set[str],
    thresh: float = 0.35,
    hyst: float = 0.08,
) -> set[str]:
    """Evaluate 4 cardinal / 8-way directions from calibrated stick with anti-jitter hysteresis."""
    hi = max(0.15, min(0.85, float(thresh)))
    lo = max(0.05, hi - float(hyst))
    out: set[str] = set()
    if (-cal_y) >= (lo if "up" in active_dirs else hi):
        out.add("up")
    if cal_y >= (lo if "down" in active_dirs else hi):
        out.add("down")
    if (-cal_x) >= (lo if "left" in active_dirs else hi):
        out.add("left")
    if cal_x >= (lo if "right" in active_dirs else hi):
        out.add("right")
    return out


def compile_macro_events(macro: dict) -> list[tuple[float, int, int]]:
    """Compile a Macro Studio dict into sorted (time_ms, evdev_keycode, value) tuples."""
    steps = macro.get("steps") or []
    t_mode = str(macro.get("timing_mode", "recorded"))
    fixed_ms = max(5, int(macro.get("fixed_delay_ms", 25) or 25))
    speed = max(0.1, float(macro.get("speed_mult", 1.0) or 1.0))
    events: list[tuple[float, int, int]] = []
    cur_ms = 0.0
    for st in steps:
        kc = int(st.get("keycode", 0) or 0)
        if kc <= 0 or kc >= 256:
            continue
        if t_mode == "zero":
            events.append((cur_ms, kc, 1))
            events.append((cur_ms + 4.0, kc, 0))
            cur_ms += 6.0
        elif t_mode == "fixed":
            wait = fixed_ms / speed
            hold = max(8.0, fixed_ms / speed)
            cur_ms += wait
            events.append((cur_ms, kc, 1))
            events.append((cur_ms + hold, kc, 0))
        else:
            wait = max(0.0, float(st.get("delay_ms", 0))) / speed
            hold = max(10.0, float(st.get("hold_ms", 50))) / speed
            cur_ms += wait
            events.append((cur_ms, kc, 1))
            events.append((cur_ms + hold, kc, 0))
    events.sort(key=lambda e: (e[0], 0 if e[2] == 1 else 1))
    return events


def get_macro_pad_usage(store: dict | None = None) -> dict[str, list[tuple[str, str, str]]]:
    """Return {macro_id: [(profile_name, ctrl_id, layer), ...]} for Macro Studio cross-reference."""
    st = store if isinstance(store, dict) else load_pad_store()
    usage: dict[str, list[tuple[str, str, str]]] = {}
    for prof in st.get("profiles") or []:
        pname = str(prof.get("name") or prof.get("id") or "Profile")
        for cid, layers in (prof.get("binds") or {}).items():
            if not isinstance(layers, dict):
                continue
            for lyr in ("base", "shift"):
                act = layers.get(lyr)
                if isinstance(act, dict) and act.get("kind") == "macro":
                    mid = str(act.get("macro_id") or "")
                    if mid:
                        usage.setdefault(mid, []).append((pname, cid, lyr))
    return usage


def format_action_short(act: dict | None, macros_by_id: dict[str, str] | None = None) -> str:
    if not isinstance(act, dict):
        return "-"
    k = act.get("kind", "")
    if k == "shift":
        return "Shift"
    if k == "key":
        return str(act.get("combo") or "-")
    if k in ("mouse_btn", "mouse_wheel"):
        return str(act.get("target") or "-")
    if k == "macro":
        mid = str(act.get("macro_id") or "")
        mname = (macros_by_id or {}).get(mid, mid)
        return f"\u26a1 {mname}"
    return "-"


def is_pad_mapper_running() -> tuple[bool, int]:
    if not os.path.exists(PAD_MAPPER_PID_FILE):
        return False, 0
    try:
        pid = int(open(PAD_MAPPER_PID_FILE, "r", encoding="utf-8").read().strip())
        if pid > 0:
            os.kill(pid, 0)
            return True, pid
    except Exception:
        pass
    return False, 0


def get_mapper_runtime_status() -> dict:
    running, pid = is_pad_mapper_running()
    if not running:
        return {"running": False, "pid": 0, "active_profile_id": "", "active_profile_name": "", "app_id": "", "title": "", "grabbed": False, "device_name": "", "paused": False}
    if os.path.exists(PAD_MAPPER_STATUS_FILE):
        try:
            data = json.load(open(PAD_MAPPER_STATUS_FILE, "r", encoding="utf-8"))
            if isinstance(data, dict):
                data["running"] = True
                data["pid"] = pid
                return data
        except Exception:
            pass
    return {"running": True, "pid": pid, "active_profile_id": "", "active_profile_name": "", "app_id": "", "title": "", "grabbed": False, "device_name": "", "paused": False}


def get_mapper_runtime_sig() -> tuple:
    st = get_mapper_runtime_status()
    return (
        bool(st.get("running")),
        int(st.get("pid") or 0),
        str(st.get("active_profile_id") or ""),
        str(st.get("app_id") or ""),
        bool(st.get("grabbed")),
        bool(st.get("paused")),
    )


def reload_pad_mapper_if_running() -> bool:
    running, pid = is_pad_mapper_running()
    if not running or pid <= 0:
        return False
    try:
        os.kill(pid, signal.SIGHUP)
        return True
    except OSError:
        return False


def stop_pad_mapper() -> bool:
    running, pid = is_pad_mapper_running()
    stopped = False
    if running and pid > 0:
        try:
            os.kill(pid, signal.SIGTERM)
            stopped = True
        except OSError:
            pass
    for p in (PAD_MAPPER_PID_FILE, PAD_MAPPER_STATUS_FILE):
        try:
            if os.path.exists(p):
                os.unlink(p)
        except OSError:
            pass
    return stopped
