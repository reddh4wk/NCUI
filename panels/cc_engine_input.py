#!/usr/bin/env python3
import glob
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import time
from core import probe
from core.completion import catalog_xkb_layouts, rank_candidates
from core.config import CONFIG_DIR
from core.i18n import get_active_language, set_language, t
from core.privileges import (
    build_privilege_modal_card,
    execute_privileged,
    get_privileged_action,
    request_privilege_modal,
)
from core.state import STATE
from ui.canvas import Canvas
from ui.kit import ActionBtn, Badge, Box, FeedbackBox, ProgressBar, SelectPill, Slider, StateBadge, Txt, start_inline_input

NIRI_CONFIG_FILE = os.path.expanduser("~/.config/niri/config.kdl")
MACROS_FILE = os.path.join(CONFIG_DIR, "macros.json")
MACROS_EXPORT_FILE = os.path.join(CONFIG_DIR, "macros_export.json")
SHORTCUTS_STORE_FILE = os.path.join(CONFIG_DIR, "shortcuts_store.json")
MACRO_PLAY_BIN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin", "nc-macro-play")
XKB_BASE_LST = "/usr/share/X11/xkb/rules/base.lst"

DEFAULT_NIRI_SHORTCUTS = [
    # Window defaults
    {"id": "win_close", "cat": "window", "combo": "Mod+Q", "action": "close-window"},
    {"id": "win_f_left", "cat": "window", "combo": "Mod+Left", "action": "focus-column-left"},
    {"id": "win_f_right", "cat": "window", "combo": "Mod+Right", "action": "focus-column-right"},
    {"id": "win_f_up", "cat": "window", "combo": "Mod+Up", "action": "focus-window-up"},
    {"id": "win_f_down", "cat": "window", "combo": "Mod+Down", "action": "focus-window-down"},
    {"id": "win_m_left", "cat": "window", "combo": "Mod+Shift+Left", "action": "move-column-left"},
    {"id": "win_m_right", "cat": "window", "combo": "Mod+Shift+Right", "action": "move-column-right"},
    {"id": "win_m_up", "cat": "window", "combo": "Mod+Shift+Up", "action": "move-window-up"},
    {"id": "win_m_down", "cat": "window", "combo": "Mod+Shift+Down", "action": "move-window-down"},
    {"id": "win_max", "cat": "window", "combo": "Mod+F", "action": "maximize-column"},
    {"id": "win_full", "cat": "window", "combo": "Mod+Shift+F", "action": "fullscreen-window"},
    {"id": "win_center", "cat": "window", "combo": "Mod+C", "action": "center-column"},
    {"id": "win_float", "cat": "window", "combo": "Mod+V", "action": "toggle-window-floating"},
    {"id": "win_shot", "cat": "window", "combo": "Print", "action": "screenshot"},
    # System & Layout defaults
    {"id": "sys_kbd_next", "cat": "system", "combo": "Mod+Space", "action": 'switch-layout "next"'},
    {"id": "sys_nc_cc", "cat": "system", "combo": "Mod+I", "action": 'spawn "/usr/local/lib/nc-ui/bin/nc-ctl" "popup" "settings"'},
    {"id": "sys_nc_shot", "cat": "system", "combo": "Mod+Print", "action": 'spawn "/usr/local/lib/nc-ui/bin/nc-ctl" "action" "capture" "repeat"'},
    {"id": "sys_overlay", "cat": "system", "combo": "Mod+Shift+Slash", "action": 'spawn "/usr/local/lib/nc-ui/bin/nc-ctl" "popup" "hotkeys"'},
    {"id": "sys_quit", "cat": "system", "combo": "Mod+Shift+E", "action": "quit"},
    # Apps defaults
    {"id": "app_term", "cat": "apps", "combo": "Mod+Return", "action": 'spawn "ghostty"'},
    {"id": "app_launch", "cat": "apps", "combo": "Mod+D", "action": 'spawn "walker"'},
    {"id": "app_browser", "cat": "apps", "combo": "Mod+B", "action": 'spawn "brave"'},
    {"id": "app_files", "cat": "apps", "combo": "Mod+N", "action": 'spawn "nautilus"'},
]

_MOD_KEYS = {
    "Super_L", "Super_R", "Meta_L", "Meta_R", "Hyper_L", "Hyper_R",
    "Control_L", "Control_R", "Shift_L", "Shift_R", "Alt_L", "Alt_R",
    "ISO_Level3_Shift", "Caps_Lock", "Num_Lock",
}

_NIRI_KEY_ALIASES = {
    "Prior": "Page_Up", "Next": "Page_Down", "KP_Enter": "KP_Enter", "ISO_Left_Tab": "Tab",
    "slash": "Slash", "question": "Slash", "minus": "Minus", "underscore": "Minus",
    "equal": "Equal", "plus": "Equal", "bracketleft": "BracketLeft", "braceleft": "BracketLeft",
    "bracketright": "BracketRight", "braceright": "BracketRight", "backslash": "BackSlash",
    "bar": "BackSlash", "semicolon": "Semicolon", "colon": "Semicolon",
    "apostrophe": "Apostrophe", "quotedbl": "Apostrophe", "comma": "Comma", "less": "Comma",
    "period": "Period", "greater": "Period", "grave": "Grave", "asciitilde": "Grave",
    "space": "Space", "Return": "Return", "Escape": "Escape", "Tab": "Tab",
    "BackSpace": "BackSpace", "Delete": "Delete", "Insert": "Insert", "Home": "Home",
    "End": "End", "Up": "Up", "Down": "Down", "Left": "Left", "Right": "Right", "Print": "Print",
}

_COMMON_KEYCODES = {
    "Escape": 1, "1": 2, "2": 3, "3": 4, "4": 5, "5": 6, "6": 7, "7": 8, "8": 9, "9": 10, "0": 11,
    "Minus": 12, "Equal": 13, "BackSpace": 14, "Tab": 15, "Q": 16, "W": 17, "E": 18, "R": 19, "T": 20,
    "Y": 21, "U": 22, "I": 23, "O": 24, "P": 25, "BracketLeft": 26, "BracketRight": 27, "Return": 28,
    "Ctrl_L": 29, "Control_L": 29, "A": 30, "S": 31, "D": 32, "F": 33, "G": 34, "H": 35, "J": 36,
    "K": 37, "L": 38, "Semicolon": 39, "Apostrophe": 40, "Grave": 41, "Shift_L": 42, "BackSlash": 43,
    "Z": 44, "X": 45, "C": 46, "V": 47, "B": 48, "N": 49, "M": 50, "Comma": 51, "Period": 52,
    "Slash": 53, "Shift_R": 54, "Alt_L": 56, "Space": 57, "F1": 59, "F2": 60, "F3": 61, "F4": 62,
    "F5": 63, "F6": 64, "F7": 65, "F8": 66, "F9": 67, "F10": 68, "F11": 87, "F12": 88,
    "Control_R": 97, "Alt_R": 100, "Up": 103, "Left": 105, "Right": 106, "Down": 108, "Delete": 111,
    "Pause": 119, "Super_L": 125, "Super_R": 126,
}

_TEXT_CHAR_TO_KEY = {
    " ": ("Space", False), "\t": ("Tab", False), "\n": ("Return", False),
    "-": ("Minus", False), "_": ("Minus", True), "=": ("Equal", False), "+": ("Equal", True),
    "[": ("BracketLeft", False), "{": ("BracketLeft", True), "]": ("BracketRight", False), "}": ("BracketRight", True),
    ";": ("Semicolon", False), ":": ("Semicolon", True), "'": ("Apostrophe", False), '"': ("Apostrophe", True),
    "`": ("Grave", False), "~": ("Grave", True), "\\": ("BackSlash", False), "|": ("BackSlash", True),
    ",": ("Comma", False), "<": ("Comma", True), ".": ("Period", False), ">": ("Period", True),
    "/": ("Slash", False), "?": ("Slash", True),
    "!": ("1", True), "@": ("2", True), "#": ("3", True), "$": ("4", True), "%": ("5", True),
    "^": ("6", True), "&": ("7", True), "*": ("8", True), "(": ("9", True), ")": ("0", True),
}

_FALLBACK_XKB_LAYOUTS = [
    ("it", "Italian"), ("us", "English (US)"), ("gb", "English (UK)"), ("de", "German"),
    ("fr", "French"), ("es", "Spanish"), ("pt", "Portuguese"), ("br", "Portuguese (Brazil)"),
    ("jp", "Japanese"), ("kr", "Korean"), ("cn", "Chinese"), ("ru", "Russian"),
    ("ua", "Ukrainian"), ("pl", "Polish"), ("nl", "Dutch"), ("se", "Swedish"),
    ("no", "Norwegian"), ("dk", "Danish"), ("fi", "Finnish"), ("ch", "German (Switzerland)"),
    ("be", "Belgian"), ("at", "German (Austria)"), ("tr", "Turkish"), ("gr", "Greek"),
    ("il", "Hebrew"), ("ara", "Arabic"), ("cz", "Czech"), ("hu", "Hungarian"),
]

_RUNTIME_REC = {"held": {}, "last_ts": 0.0, "steps": []}
_XKB_LAYOUTS_CACHE: list[tuple[str, str]] | None = None
_OPT_KBD: dict[str, tuple[float, int]] = {}
_OPT_MOUSE: dict[str, tuple[float, float]] = {}


from core.config import _update_toml_key_in_section

# -*- coding: utf-8 -*-
"""NC-UI Control Center — Mouse & Touchpad (`panels/cc_mouse.py`) [Single Backend / Dual View]"""


NIRI_CONFIG_FILE = os.path.expanduser("~/.config/niri/config.kdl")
_MSG = ""
_ERR = ""




def _load_all_xkb_layouts() -> list[tuple[str, str]]:
    global _XKB_LAYOUTS_CACHE
    if _XKB_LAYOUTS_CACHE is not None:
        return _XKB_LAYOUTS_CACHE
    out = []
    if os.path.exists(XKB_BASE_LST):
        try:
            in_sec = False
            with open(XKB_BASE_LST, "r", encoding="utf-8", errors="ignore") as f:
                for ln in f:
                    s = ln.rstrip("\n")
                    if s.startswith("! layout"):
                        in_sec = True
                        continue
                    if s.startswith("!") and in_sec:
                        break
                    if in_sec:
                        m = re.match(r"^\s+([a-zA-Z0-9_-]+)\s+(.+)$", s)
                        if m:
                            out.append((m.group(1).strip(), m.group(2).strip()))
        except Exception:
            pass
    _XKB_LAYOUTS_CACHE = out or list(_FALLBACK_XKB_LAYOUTS)
    return _XKB_LAYOUTS_CACHE


def _xkb_layout_names_map() -> dict[str, str]:
    return dict(_load_all_xkb_layouts())


def _set_kbd_feedback(ok: bool, msg: str) -> None:
    STATE.set("keyboard", "cc_fb", {"ok": bool(ok), "msg": str(msg), "ts": time.time()})


def _get_kbd_feedback() -> dict:
    fb = STATE.get("keyboard", "cc_fb", {})
    if isinstance(fb, dict) and fb.get("msg") and (time.time() - float(fb.get("ts", 0))) < 15.0:
        return fb
    return {}


def _read_niri_kdl() -> str:
    if not os.path.exists(NIRI_CONFIG_FILE):
        return ""
    try:
        with open(NIRI_CONFIG_FILE, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


def _write_niri_kdl(new_text: str, auto_reload: bool = False) -> tuple[bool, str]:
    try:
        os.makedirs(os.path.dirname(NIRI_CONFIG_FILE), exist_ok=True)
        old_text = _read_niri_kdl()
        with open(NIRI_CONFIG_FILE, "w", encoding="utf-8") as f:
            f.write(new_text)
        if probe.has_bin("niri"):
            ok, _out, err = probe.run_cmd_full(["niri", "validate", "-c", NIRI_CONFIG_FILE], timeout=2.5)
            if not ok:
                with open(NIRI_CONFIG_FILE, "w", encoding="utf-8") as f:
                    f.write(old_text)
                return False, f"niri validate failed: {err or 'syntax error'}"
        with open(NIRI_CONFIG_FILE, "r", encoding="utf-8") as f:
            if f.read() != new_text:
                return False, "Read-back verification mismatch on config.kdl"
        if auto_reload and probe.has_bin("niri"):
            probe.run_cmd(["niri", "msg", "action", "load-config-file"], timeout=1.5)
        return True, "Applied, validated & loaded in Niri" if auto_reload else "Applied & verified in config.kdl"
    except Exception as e:
        return False, str(e)


def _find_block_span(text: str, block_name: str, start: int = 0, end: int = None) -> tuple[int, int] | None:
    sub = text[start:end]
    m = re.search(rf"(?m)^[ \t]*{re.escape(block_name)}\s*\{{", sub)
    if not m:
        return None
    brace_open = start + m.end() - 1
    depth = 0
    for i in range(brace_open, end if end is not None else len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return start + m.start(), i + 1
    return None


def _norm_action_str(act: str) -> str:
    return re.sub(r"\s+", " ", str(act or "").strip().rstrip(";"))


def _std_action_lookup() -> dict[str, dict]:
    res = {}
    by_id = {}
    for d in DEFAULT_NIRI_SHORTCUTS:
        res[_norm_action_str(d["action"])] = d
        by_id[d["id"]] = d
    # Also recognize single-string nc-ctl command variants & legacy overlay
    res['spawn "/usr/local/lib/nc-ui/bin/nc-ctl popup settings"'] = by_id["sys_nc_cc"]
    res['spawn "/usr/local/lib/nc-ui/bin/nc-ctl action capture repeat"'] = by_id["sys_nc_shot"]
    res['spawn "/usr/local/lib/nc-ui/bin/nc-ctl popup hotkeys"'] = by_id["sys_overlay"]
    res["show-hotkey-overlay"] = by_id["sys_overlay"]
    return res


def _action_cat(action: str) -> str:
    norm = _norm_action_str(action)
    std = _std_action_lookup().get(norm)
    if std:
        return std["cat"]
    if "nc-ctl" in norm:
        return "system"
    return "custom"


def _action_label(action: str, is_it: bool = False) -> str:
    norm = _norm_action_str(action)
    std = _std_action_lookup().get(norm)
    if std:
        return t(f"cc_keyboard.shortcut_{std['id']}")
    return norm


def _collect_keyboard_state() -> dict:
    content = _read_niri_kdl()
    state = {
        "repeat_delay": 600,
        "repeat_rate": 25,
        "numlock": False,
        "track_layout": "global",
        "layout": "it",
        "variant": "",
        "options": "",
        "binds": [],
    }
    inp_span = _find_block_span(content, "input")
    if inp_span:
        kbd_span = _find_block_span(content, "keyboard", inp_span[0], inp_span[1])
        if kbd_span:
            kbd_blk = content[kbd_span[0]:kbd_span[1]]
            m_del = re.search(r"(?m)^\s*repeat-delay\s+(\d+)", kbd_blk)
            if m_del:
                state["repeat_delay"] = int(m_del.group(1))
            m_rate = re.search(r"(?m)^\s*repeat-rate\s+(\d+)", kbd_blk)
            if m_rate:
                state["repeat_rate"] = int(m_rate.group(1))
            state["numlock"] = bool(re.search(r"(?m)^\s*numlock\b", kbd_blk))
            m_trk = re.search(r'(?m)^\s*track-layout\s+"([^"]+)"', kbd_blk)
            if m_trk:
                state["track_layout"] = m_trk.group(1).strip()
            xkb_span = _find_block_span(kbd_blk, "xkb")
            if xkb_span:
                xkb_blk = kbd_blk[xkb_span[0]:xkb_span[1]]
                for k in ("layout", "variant", "options"):
                    mk = re.search(rf'(?m)^\s*{k}\s+"([^"]*)"', xkb_blk)
                    if mk:
                        state[k] = mk.group(1).strip()

    binds_span = _find_block_span(content, "binds")
    if binds_span:
        binds_blk = content[binds_span[0]:binds_span[1]]
        for line in binds_blk.splitlines():
            m_b = re.match(r"^\s*([A-Za-z0-9_+]+)(?:\s+[^\{]*)?\{\s*([^;\}]+?);?\s*\}\s*$", line)
            if m_b:
                combo = m_b.group(1).strip()
                action = _norm_action_str(m_b.group(2))
                state["binds"].append({"combo": combo, "action": action, "cat": _action_cat(action)})
    now = time.monotonic()
    for sk in ("repeat_rate", "repeat_delay"):
        ov = _OPT_KBD.get(sk)
        if ov and (now - ov[0]) < 2.5:
            state[sk] = ov[1]
    return state


def get_live_keyboard_layouts() -> dict:
    st = _collect_keyboard_state()
    codes = [x.strip() for x in st["layout"].split(",") if x.strip()] or ["it"]
    names_map = _xkb_layout_names_map()
    cur_idx = int(STATE.get("keyboard", "active_layout_idx", 0) or 0)
    if probe.has_bin("niri"):
        ok, out = probe.run_cmd(["niri", "msg", "-j", "keyboard-layouts"], timeout=0.4)
        if ok and out:
            try:
                data = json.loads(out)
                if isinstance(data, dict) and "current_idx" in data:
                    cur_idx = int(data.get("current_idx", 0) or 0)
            except Exception:
                pass
    if cur_idx < 0 or cur_idx >= len(codes):
        cur_idx = 0
    items = [{"idx": i, "code": c, "name": names_map.get(c, c.upper())} for i, c in enumerate(codes)]
    cur_item = items[cur_idx] if items else {"idx": 0, "code": "it", "name": "Italian"}
    return {"items": items, "codes": codes, "current_idx": cur_idx, "current": cur_item}


def switch_active_keyboard_layout(idx_or_code) -> tuple[bool, str]:
    info = get_live_keyboard_layouts()
    codes = info["codes"]
    if str(idx_or_code).isdigit():
        idx = int(idx_or_code)
    elif str(idx_or_code) in codes:
        idx = codes.index(str(idx_or_code))
    else:
        return False, f"Unknown layout: {idx_or_code}"
    if idx < 0 or idx >= len(codes):
        return False, "Layout index out of range"
    STATE.set("keyboard", "active_layout_idx", idx)
    if probe.has_bin("niri"):
        probe.run_cmd(["niri", "msg", "action", "switch-layout", str(idx)], timeout=0.6)
    return True, f"Switched layout to {codes[idx].upper()}"


def cycle_active_keyboard_layout() -> tuple[bool, str]:
    info = get_live_keyboard_layouts()
    codes = info["codes"]
    if not codes:
        return False, "No layouts"
    nxt = (info["current_idx"] + 1) % len(codes)
    STATE.set("keyboard", "active_layout_idx", nxt)
    if probe.has_bin("niri"):
        probe.run_cmd(["niri", "msg", "action", "switch-layout", "next"], timeout=0.6)
    return True, f"Cycled layout to {codes[nxt].upper()}"


def _update_kbd_kdl(key: str, value) -> tuple[bool, str]:
    content = _read_niri_kdl() or 'input {\n    keyboard {\n        xkb {\n            layout "it"\n        }\n    }\n}\n'
    inp_span = _find_block_span(content, "input")
    if not inp_span:
        content = 'input {\n    keyboard {\n        xkb {\n            layout "it"\n        }\n    }\n}\n\n' + content
        inp_span = _find_block_span(content, "input")

    kbd_span = _find_block_span(content, "keyboard", inp_span[0], inp_span[1])
    if not kbd_span:
        pos = inp_span[0] + content[inp_span[0]:inp_span[1]].find("{") + 1
        content = content[:pos] + '\n    keyboard {\n        xkb {\n            layout "it"\n        }\n    }' + content[pos:]
        inp_span = _find_block_span(content, "input")
        kbd_span = _find_block_span(content, "keyboard", inp_span[0], inp_span[1])

    kbd_blk = content[kbd_span[0]:kbd_span[1]]
    if key in ("layout", "variant", "options"):
        xkb_span = _find_block_span(kbd_blk, "xkb")
        if not xkb_span:
            pos = kbd_blk.find("{") + 1
            kbd_blk = kbd_blk[:pos] + '\n        xkb {\n            layout "it"\n        }' + kbd_blk[pos:]
            xkb_span = _find_block_span(kbd_blk, "xkb")
        xkb_blk = kbd_blk[xkb_span[0]:xkb_span[1]]
        pat = re.compile(rf'(?m)^([ \t]*{key}\s+)"[^"]*"')
        if str(value).strip() == "":
            xkb_blk = re.sub(rf'(?m)^[ \t]*{key}\s+"[^"]*"\n?', "", xkb_blk)
        elif pat.search(xkb_blk):
            xkb_blk = pat.sub(rf'\g<1>"{value}"', xkb_blk, count=1)
        else:
            ins = xkb_blk.rfind("}")
            xkb_blk = xkb_blk[:ins].rstrip() + f'\n            {key} "{value}"\n        }}'
        kbd_blk = kbd_blk[:xkb_span[0]] + xkb_blk + kbd_blk[xkb_span[1]:]
    elif key == "numlock":
        has_nl = bool(re.search(r"(?m)^\s*numlock\b", kbd_blk))
        if value and not has_nl:
            ins = kbd_blk.rfind("}")
            kbd_blk = kbd_blk[:ins].rstrip() + "\n        numlock\n    }"
        elif not value and has_nl:
            kbd_blk = re.sub(r"(?m)^[ \t]*numlock\b.*\n?", "", kbd_blk)
    elif key in ("repeat-delay", "repeat-rate", "track-layout"):
        formatted = f'"{value}"' if key == "track-layout" else str(int(value))
        pat = re.compile(rf"(?m)^([ \t]*{key}\s+)[^\n]+")
        if pat.search(kbd_blk):
            kbd_blk = pat.sub(rf"\g<1>{formatted}", kbd_blk, count=1)
        else:
            ins = kbd_blk.rfind("}")
            kbd_blk = kbd_blk[:ins].rstrip() + f"\n        {key} {formatted}\n    }}"

    return _write_niri_kdl(content[:kbd_span[0]] + kbd_blk + content[kbd_span[1]:], auto_reload=(key == "layout"))


def _update_niri_bind(combo: str, action: str = None, remove: bool = False, auto_reload: bool = True) -> tuple[bool, str]:
    clean_c = str(combo or "").strip()
    if not clean_c or any(ch in clean_c for ch in ("{", "}", "\n", " ")):
        return False, "Invalid key combination"
    content = _read_niri_kdl()
    binds_span = _find_block_span(content, "binds")
    if not binds_span:
        content = content.rstrip() + "\n\nbinds {\n}\n"
        binds_span = _find_block_span(content, "binds")
    b_blk = content[binds_span[0]:binds_span[1]]
    pat = re.compile(rf"(?m)^[ \t]*{re.escape(clean_c)}(?:\s+[^\{{\n]*)?\{{[^\n\{{}}]*\}}\s*\n?")
    if remove:
        b_blk = pat.sub("", b_blk)
    else:
        clean_a = str(action or "").strip().rstrip(";")
        if not clean_a:
            return False, "Empty bind action"
        new_line = f"    {clean_c} {{ {clean_a}; }}\n"
        if pat.search(b_blk):
            b_blk = pat.sub(new_line, b_blk, count=1)
        else:
            ins = b_blk.rfind("}")
            b_blk = b_blk[:ins].rstrip() + "\n" + new_line + "}"
    return _write_niri_kdl(content[:binds_span[0]] + b_blk + content[binds_span[1]:], auto_reload=auto_reload)


def _load_shortcuts_store() -> dict:
    if os.path.exists(SHORTCUTS_STORE_FILE):
        try:
            d = json.load(open(SHORTCUTS_STORE_FILE, "r", encoding="utf-8"))
            if isinstance(d, dict):
                return {"orphans": list(d.get("orphans", [])), "custom_actions": list(d.get("custom_actions", []))}
        except Exception:
            pass
    return {"orphans": [], "custom_actions": []}


def _save_shortcuts_store(store: dict) -> None:
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(SHORTCUTS_STORE_FILE, "w", encoding="utf-8") as f:
            json.dump(store, f, indent=2)
    except Exception:
        pass


def _add_orphan_action(action: str, prev_combo: str = "", label: str = "") -> None:
    norm = _norm_action_str(action)
    if not norm:
        return
    # Standard default actions don't need orphan entries because they appear as Unbound Standard Actions
    if norm in _std_action_lookup():
        return
    store = _load_shortcuts_store()
    orphans = [o for o in store["orphans"] if _norm_action_str(o.get("action")) != norm]
    orphans.insert(0, {"action": norm, "prev_combo": prev_combo, "label": label or norm, "cat": _action_cat(norm)})
    store["orphans"] = orphans[:30]
    _save_shortcuts_store(store)


def _remove_orphan_action(action: str) -> None:
    norm = _norm_action_str(action)
    store = _load_shortcuts_store()
    new_o = [o for o in store["orphans"] if _norm_action_str(o.get("action")) != norm]
    if len(new_o) != len(store["orphans"]):
        store["orphans"] = new_o
        _save_shortcuts_store(store)


def _record_custom_action(action: str, label: str = "") -> None:
    norm = _norm_action_str(action)
    if not norm or norm in _std_action_lookup():
        return
    store = _load_shortcuts_store()
    if not any(_norm_action_str(c.get("action")) == norm for c in store["custom_actions"]):
        store["custom_actions"].insert(0, {"action": norm, "label": label or norm})
        store["custom_actions"] = store["custom_actions"][:20]
        _save_shortcuts_store(store)


def _start_restore_defaults(cc_panel, scope: str, is_it: bool = False) -> None:
    st = _collect_keyboard_state()
    by_combo = {b["combo"]: b["action"] for b in st["binds"]}
    by_act = {_norm_action_str(b["action"]): b["combo"] for b in st["binds"]}

    items = [d for d in DEFAULT_NIRI_SHORTCUTS if scope == "all" or d["cat"] == scope]
    free_list = []
    conflicts = []
    for d in items:
        d_act = _norm_action_str(d["action"])
        d_cmb = d["combo"]
        if by_act.get(d_act) == d_cmb:
            continue
        cur_on_cmb = by_combo.get(d_cmb)
        if cur_on_cmb and _norm_action_str(cur_on_cmb) != d_act:
            conflicts.append({
                "combo": d_cmb,
                "default_action": d_act,
                "default_label": t(f"cc_keyboard.shortcut_{d['id']}"),
                "current_action": cur_on_cmb,
                "old_default_combo": by_act.get(d_act, ""),
            })
        else:
            free_list.append({"combo": d_cmb, "action": d_act, "old_combo": by_act.get(d_act, "")})

    if not conflicts and not free_list:
        _set_kbd_feedback(True, t("cc_keyboard.all_defaults_are_already_active", p0=scope))
        return

    if not conflicts:
        for item in free_list:
            if item["old_combo"] and item["old_combo"] != item["combo"]:
                _update_niri_bind(item["old_combo"], remove=True, auto_reload=False)
            ok, msg = _update_niri_bind(item["combo"], item["action"], auto_reload=False)
            if not ok:
                _set_kbd_feedback(False, msg)
                return
        probe.run_cmd(["niri", "msg", "action", "load-config-file"], timeout=1.5)
        _set_kbd_feedback(True, t("cc_keyboard.restored_default_keybindings", p0=len(free_list), p1=scope))
        return

    STATE.set("keyboard", "conflict_wizard", {
        "scope": scope,
        "free": free_list,
        "conflicts": conflicts,
        "step": 0,
        "mode": "choice",
    })


def _apply_conflict_overwrite(wizard: dict) -> None:
    for item in wizard.get("free", []):
        if item.get("old_combo"):
            _update_niri_bind(item["old_combo"], remove=True, auto_reload=False)
        _update_niri_bind(item["combo"], item["action"], auto_reload=False)
    for c in wizard.get("conflicts", []):
        _add_orphan_action(c["current_action"], prev_combo=c["combo"])
        if c.get("old_default_combo") and c["old_default_combo"] != c["combo"]:
            _update_niri_bind(c["old_default_combo"], remove=True, auto_reload=False)
        _update_niri_bind(c["combo"], c["default_action"], auto_reload=False)
    probe.run_cmd(["niri", "msg", "action", "load-config-file"], timeout=1.5)
    STATE.set("keyboard", "conflict_wizard", {})
    _set_kbd_feedback(True, t("cc_keyboard.restored_defaults_displaced_binds", p0=len(wizard.get('conflicts', [])) + len(wizard.get('free', []))))


def _apply_wizard_free_items(wizard: dict) -> None:
    for item in wizard.get("free", []):
        if item.get("old_combo"):
            _update_niri_bind(item["old_combo"], remove=True, auto_reload=False)
        _update_niri_bind(item["combo"], item["action"], auto_reload=False)
    wizard["free"] = []


def _advance_conflict_wizard(cc_panel, wizard: dict, mode_msg: str = "") -> None:
    wizard["step"] = int(wizard.get("step", 0)) + 1
    if wizard["step"] >= len(wizard.get("conflicts", [])):
        _apply_wizard_free_items(wizard)
        probe.run_cmd(["niri", "msg", "action", "load-config-file"], timeout=1.5)
        STATE.set("keyboard", "conflict_wizard", {})
        _stop_capture(cc_panel)
        _set_kbd_feedback(True, mode_msg or "Guided default restore completed")
    else:
        STATE.set("keyboard", "conflict_wizard", wizard)


def _norm_macro(m: dict, idx: int = 0) -> dict:
    p_key = str(m.get("panic_key") or "F12")
    return {
        "id": str(m.get("id") or f"mac_{idx}"),
        "name": str(m.get("name") or f"Macro {idx + 1}"),
        "combo": str(m.get("combo") or ""),
        "timing_mode": str(m.get("timing_mode") or "recorded"),
        "fixed_delay_ms": int(m.get("fixed_delay_ms", 25) or 25),
        "speed_mult": float(m.get("speed_mult", 1.0) or 1.0),
        "repeat_mode": str(m.get("repeat_mode") or "once"),
        "repeat_count": int(m.get("repeat_count", 1) or 1),
        "steps": [
            {
                "key": str(s.get("key", "?")),
                "keycode": int(s.get("keycode", 0) or 0),
                "delay_ms": int(s.get("delay_ms", 0) or 0),
                "hold_ms": int(s.get("hold_ms", 50) or 50),
            }
            for s in (m.get("steps") or [])
            if isinstance(s, dict)
        ],
        "cmd": str(m.get("cmd") or ""),
        "panic_key": p_key if p_key in ("F12", "Pause") else "F12",
        "countdown_s": max(0, min(15, int(m.get("countdown_s", 0) or 0))),
    }


def _load_macros() -> list[dict]:
    if not os.path.exists(MACROS_FILE):
        return [
            _norm_macro({
                "id": "mac_gg", "name": "GG Quick Chat", "combo": "",
                "timing_mode": "recorded", "fixed_delay_ms": 25, "speed_mult": 1.0,
                "repeat_mode": "once", "repeat_count": 1,
                "steps": [
                    {"key": "Return", "keycode": 28, "delay_ms": 0, "hold_ms": 40},
                    {"key": "G", "keycode": 34, "delay_ms": 45, "hold_ms": 55},
                    {"key": "G", "keycode": 34, "delay_ms": 65, "hold_ms": 55},
                    {"key": "Return", "keycode": 28, "delay_ms": 45, "hold_ms": 40},
                ],
                "cmd": "",
            }, 0),
        ]
    try:
        with open(MACROS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return [_norm_macro(m, i) for i, m in enumerate(data)] if isinstance(data, list) else []
    except Exception:
        return []


def _save_macros(macros: list[dict]) -> None:
    try:
        os.makedirs(os.path.dirname(MACROS_FILE), exist_ok=True)
        with open(MACROS_FILE, "w", encoding="utf-8") as f:
            json.dump(macros, f, indent=2)
    except Exception:
        pass


def _get_macro_draft() -> dict:
    d = STATE.get("keyboard", "macro_draft", {})
    if not isinstance(d, dict) or not d:
        macros = _load_macros()
        if macros:
            return _norm_macro(macros[0], 0)
        return _norm_macro({"id": f"mac_{int(time.time())}", "name": "New Gaming Macro", "steps": []}, 0)
    return _norm_macro(d, 0)


def _set_macro_draft(**kw) -> dict:
    cur = _get_macro_draft()
    cur.update(kw)
    norm = _norm_macro(cur, 0)
    STATE.set("keyboard", "macro_draft", norm)
    return norm


def _find_saved_macro(macro_id: str, macros: list[dict] = None) -> dict | None:
    lst = macros if macros is not None else _load_macros()
    return next((m for m in lst if m["id"] == macro_id), None)


def is_macro_draft_dirty(draft: dict, macros: list[dict] = None) -> bool:
    saved = _find_saved_macro(draft.get("id", ""), macros)
    if saved is None:
        return bool(draft.get("steps") or draft.get("combo") or draft.get("cmd"))
    return _norm_macro(draft) != _norm_macro(saved)


def _is_macro_engine_running() -> bool:
    for pf in glob.glob("/tmp/nc_macro_play*.pid"):
        try:
            pid = int(open(pf, "r", encoding="utf-8").read().strip())
            os.kill(pid, 0)
            return True
        except Exception:
            pass
    return False


def _stop_active_macros() -> bool:
    stopped = False
    for pf in glob.glob("/tmp/nc_macro_play*.pid"):
        try:
            pid = int(open(pf, "r", encoding="utf-8").read().strip())
            os.kill(pid, signal.SIGTERM)
            stopped = True
        except Exception:
            pass
        try:
            os.unlink(pf)
        except OSError:
            pass
    return stopped


def _simulate_macro_sandbox(draft: dict) -> str:
    steps = draft.get("steps") or []
    t_mode = draft.get("timing_mode", "recorded")
    fixed_ms = max(5, int(draft.get("fixed_delay_ms", 25) or 25))
    spd = max(0.1, float(draft.get("speed_mult", 1.0) or 1.0))
    toks = []
    typed = []
    for i, st in enumerate(steps[:8]):
        k = str(st.get("key", "?"))
        d_ms = 0 if t_mode == "zero" else (fixed_ms if t_mode == "fixed" else int(st.get("delay_ms", 0)))
        h_ms = 4 if t_mode == "zero" else (max(8, fixed_ms) if t_mode == "fixed" else max(10, int(st.get("hold_ms", 50))))
        d_eff = int(round(d_ms / spd))
        h_eff = int(round(h_ms / spd))
        prefix = f"+{d_eff}ms " if i > 0 else ""
        toks.append(f"{prefix}[{k}:{h_eff}ms]")
        if len(k) == 1:
            typed.append(k.lower())
        elif k == "Space":
            typed.append(" ")
        elif k == "Return":
            typed.append("↵")
    if len(steps) > 8:
        toks.append(f"+{len(steps) - 8}")
    out_s = "".join(typed)
    trace = " ".join(toks) if toks else draft.get("cmd", "") or "-"
    return f'{trace}  =>  "{out_s}"' if out_s else trace


def _save_macro_draft_entry(d: dict) -> list[dict]:
    macros = _load_macros()
    for i, m in enumerate(macros):
        if m["id"] == d["id"]:
            macros[i] = d
            _save_macros(macros)
            from core.pad_mapper_core import reload_pad_mapper_if_running
            reload_pad_mapper_if_running()
            return macros
    macros.append(d)
    _save_macros(macros)
    from core.pad_mapper_core import reload_pad_mapper_if_running
    reload_pad_mapper_if_running()
    return macros


def _ensure_uinput_or_request(cc_panel) -> bool:
    if os.access("/dev/uinput", os.W_OK):
        return True
    priv = get_privileged_action("uinput_unlock")
    if priv and priv.check_installed():
        ok, _msg = execute_privileged("uinput_unlock")
        if ok and os.access("/dev/uinput", os.W_OK):
            return True
    request_privilege_modal(cc_panel, "uinput_unlock")
    return False


def _get_bind_draft() -> dict:
    d = STATE.get("keyboard", "bind_draft", {})
    if not isinstance(d, dict):
        d = {}
    return {
        "combo": str(d.get("combo", "")),
        "mode": str(d.get("mode", "standard")),
        "std_cat": str(d.get("std_cat", "window")),
        "std_act": str(d.get("std_act", "close-window")),
        "cmd": str(d.get("cmd", "")),
        "nc_target": str(d.get("nc_target", "popup network")),
        "edit_orig": str(d.get("edit_orig", "")),
    }


def _set_bind_draft(**kw) -> dict:
    cur = _get_bind_draft()
    cur.update(kw)
    STATE.set("keyboard", "bind_draft", cur)
    return cur


def _build_niri_action_from_draft(d: dict) -> str:
    mode = d.get("mode", "standard")
    if mode == "standard":
        return d.get("std_act", "close-window")
    if mode == "ncui":
        parts = d.get("nc_target", "popup network").split()
        args = " ".join(f'"{p}"' for p in parts)
        return f'spawn "/usr/local/lib/nc-ui/bin/nc-ctl" {args}'
    cmd_str = str(d.get("cmd", "") or "").strip()
    if not cmd_str:
        return ""
    try:
        tokens = shlex.split(cmd_str)
    except ValueError:
        tokens = cmd_str.split()
    if not tokens:
        return ""
    return "spawn " + " ".join(f'"{t.replace(chr(34), chr(92) + chr(34))}"' for t in tokens)


def _decode_gdk_event(event) -> tuple[str, list[str], str, int]:
    import gi
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk
    keysym = Gdk.keyval_name(event.keyval) or f"Key_{event.keyval}"
    hw = int(getattr(event, "hardware_keycode", 0) or 0)
    evdev_code = max(0, hw - 8)
    grp = int(getattr(event, "group", 0) or 0)

    base_sym = keysym
    try:
        if Gdk.Display.get_default() is not None and hw > 0:
            km = Gdk.Keymap.get_default()
            if km is not None:
                ok, kv, _eg, _lv, _cs = km.translate_keyboard_state(hw, Gdk.ModifierType(0), grp)
                if ok and kv:
                    base_sym = Gdk.keyval_name(kv) or keysym
    except Exception:
        pass

    st = getattr(event, "state", Gdk.ModifierType(0))
    mods = []
    if st & (Gdk.ModifierType.SUPER_MASK | Gdk.ModifierType.MOD4_MASK):
        mods.append("Mod")
    if st & Gdk.ModifierType.CONTROL_MASK:
        mods.append("Ctrl")
    if st & Gdk.ModifierType.MOD1_MASK:
        mods.append("Alt")
    if st & Gdk.ModifierType.SHIFT_MASK:
        mods.append("Shift")

    if base_sym in _NIRI_KEY_ALIASES:
        niri_k = _NIRI_KEY_ALIASES[base_sym]
    elif len(base_sym) == 1 and base_sym.isalpha():
        niri_k = base_sym.upper()
    else:
        niri_k = base_sym

    return niri_k, mods, keysym, evdev_code


def _stop_capture(cc_panel) -> None:
    STATE.set("keyboard", "capture_mode", "")
    STATE.set("keyboard", "capture_preview", "")
    if cc_panel.shell and hasattr(cc_panel.shell, "set_key_capture_grab"):
        cc_panel.shell.set_key_capture_grab(False)


def handle_cc_keyboard_key_press(cc_panel, event) -> bool:
    cap_mode = str(STATE.get("keyboard", "capture_mode", "") or "")
    niri_k, mods, keysym, evdev_code = _decode_gdk_event(event)
    if not cap_mode:
        if keysym in ("F12", "Pause") and not mods and _is_macro_engine_running():
            _stop_active_macros()
            _set_kbd_feedback(True, t("cc_keyboard.panic_stopped_msg", p0=keysym))
            if cc_panel.shell:
                cc_panel.shell.refresh_view()
            return True
        return False

    is_mod_only = keysym in _MOD_KEYS

    if cap_mode == "inspector":
        if keysym == "Escape":
            now = time.monotonic()
            prev_esc = float(STATE.get("keyboard", "esc_ts", 0.0) or 0.0)
            if now - prev_esc < 1.2:
                _stop_capture(cc_panel)
                _set_kbd_feedback(True, t("cc_keyboard.exited_live_key_inspector"))
                if cc_panel.shell:
                    cc_panel.shell.refresh_view()
                return True
            STATE.set("keyboard", "esc_ts", now)

        combo = "+".join(mods + [niri_k]) if (mods and not is_mod_only) else (niri_k if not is_mod_only else "+".join(mods + [keysym]))
        STATE.set("keyboard", "inspected_key", {
            "combo": combo, "keysym": keysym, "keyval_hex": f"0x{event.keyval:04x}",
            "evdev": evdev_code, "mods": "+".join(mods) or "None",
        })
        if cc_panel.shell:
            cc_panel.shell.refresh_view()
        return True

    if cap_mode in ("shortcut_combo", "macro_trigger", "conflict_rebind", "ctrl_bind"):
        if keysym == "Escape" and not mods:
            _stop_capture(cc_panel)
            _set_kbd_feedback(True, t("cc_keyboard.key_capture_cancelled"))
            if cc_panel.shell:
                cc_panel.shell.refresh_view()
            return True

        if is_mod_only:
            mod_lbl = {"Super": "Mod", "Control": "Ctrl", "Shift": "Shift", "Alt": "Alt"}.get(keysym.split("_")[0], keysym)
            cur_m = list(mods)
            if mod_lbl not in cur_m:
                cur_m.append(mod_lbl)
            STATE.set("keyboard", "capture_preview", "+".join(cur_m) + "+...")
            if cc_panel.shell:
                cc_panel.shell.refresh_view()
            return True

        combo = "+".join(mods + [niri_k])
        if cap_mode == "ctrl_bind":
            _stop_capture(cc_panel)
            from core.pad_mapper_core import resolve_combo_codes
            from panels.cc_input_controllers import apply_captured_pad_key
            codes = resolve_combo_codes(combo, extra_code=evdev_code)
            apply_captured_pad_key(combo, codes)
            if cc_panel.shell:
                cc_panel.shell.refresh_view()
            return True
        if cap_mode == "conflict_rebind":
            wiz = dict(STATE.get("keyboard", "conflict_wizard", {}) or {})
            step_i = int(wiz.get("step", 0))
            confs = wiz.get("conflicts", [])
            if 0 <= step_i < len(confs):
                c = confs[step_i]
                if c.get("old_default_combo") and c["old_default_combo"] != c["combo"]:
                    _update_niri_bind(c["old_default_combo"], remove=True, auto_reload=False)
                _update_niri_bind(c["combo"], c["default_action"], auto_reload=False)
                _update_niri_bind(combo, c["current_action"], auto_reload=False)
                _advance_conflict_wizard(cc_panel, wiz, f"Guided restore finished; rebound user action to {combo}")
                if cc_panel.shell:
                    cc_panel.shell.refresh_view()
                return True

        _stop_capture(cc_panel)
        if cap_mode == "shortcut_combo":
            _set_bind_draft(combo=combo)
        else:
            _set_macro_draft(combo=combo)
        _set_kbd_feedback(True, t("cc_keyboard.captured_shortcut", p0=combo))
        if cc_panel.shell:
            cc_panel.shell.refresh_view()
        return True

    if cap_mode == "macro_rec":
        stop_key = str(_get_macro_draft().get("panic_key") or "F12")
        if keysym == stop_key and not mods:
            _finish_macro_recording(cc_panel)
            return True
        if evdev_code in _RUNTIME_REC["held"]:
            return True
        now = time.monotonic()
        steps = _RUNTIME_REC["steps"]
        delay_ms = 0 if not steps else max(0, min(10000, int(round((now - _RUNTIME_REC["last_ts"]) * 1000))))
        _RUNTIME_REC["last_ts"] = now
        steps.append({"key": niri_k if not is_mod_only else keysym, "keycode": evdev_code, "delay_ms": delay_ms, "hold_ms": 60})
        _RUNTIME_REC["held"][evdev_code] = (len(steps) - 1, now)
        STATE.set("keyboard", "macro_sel_step", len(steps) - 1)
        _set_macro_draft(steps=list(steps))
        if cc_panel.shell:
            cc_panel.shell.refresh_view()
        return True

    return False


def handle_cc_keyboard_key_release(cc_panel, event) -> bool:
    cap_mode = str(STATE.get("keyboard", "capture_mode", "") or "")
    if not cap_mode:
        return False

    hw = int(getattr(event, "hardware_keycode", 0) or 0)
    evdev_code = max(0, hw - 8)

    if cap_mode == "macro_rec" and evdev_code in _RUNTIME_REC["held"]:
        idx, p_ts = _RUNTIME_REC["held"].pop(evdev_code)
        hold_ms = max(10, min(10000, int(round((time.monotonic() - p_ts) * 1000))))
        if 0 <= idx < len(_RUNTIME_REC["steps"]):
            _RUNTIME_REC["steps"][idx]["hold_ms"] = hold_ms
            _set_macro_draft(steps=list(_RUNTIME_REC["steps"]))
            if cc_panel.shell:
                cc_panel.shell.refresh_view()
        return True

    if cap_mode in ("shortcut_combo", "macro_trigger", "conflict_rebind", "ctrl_bind"):
        STATE.set("keyboard", "capture_preview", "")
        if cc_panel.shell:
            cc_panel.shell.refresh_view()
        return True

    return cap_mode == "inspector"


def _finish_macro_recording(cc_panel) -> None:
    now = time.monotonic()
    for _evc, (idx, p_ts) in list(_RUNTIME_REC["held"].items()):
        if 0 <= idx < len(_RUNTIME_REC["steps"]):
            _RUNTIME_REC["steps"][idx]["hold_ms"] = max(10, min(10000, int(round((now - p_ts) * 1000))))
    _RUNTIME_REC["held"].clear()
    _stop_capture(cc_panel)
    _set_macro_draft(steps=list(_RUNTIME_REC["steps"]))
    _set_kbd_feedback(True, t("cc_keyboard.recorded_keystrokes", p0=len(_RUNTIME_REC['steps'])))
    if cc_panel.shell:
        cc_panel.shell.refresh_view()


def _feedback_box(is_it: bool = False) -> list:
    fb = _get_kbd_feedback()
    if not fb:
        return []
    return [FeedbackBox(t("cc_keyboard.keyboard_configuration_status"), bool(fb["ok"]), fb["msg"])]


def compile_macro_timeline(steps: list, timing_mode: str, fixed_ms: int, speed: float) -> tuple[list[dict], int]:
    """Compila la timeline reale degli step (identica a bin/nc-macro-play) con timestamp e corsie di overlap."""
    if not steps:
        return [], 0
    spd = max(0.1, float(speed or 1.0))
    fms = max(5, int(fixed_ms or 25))
    cur_ms = 0.0
    max_end = 0.0
    lane_ends: list[float] = []
    out: list[dict] = []
    for idx, st in enumerate(steps):
        key_s = str(st.get("key", "?"))
        raw_d = max(0, int(st.get("delay_ms", 0) or 0))
        raw_h = max(10, int(st.get("hold_ms", 50) or 50))
        if timing_mode == "zero":
            eff_d = 0.0 if idx == 0 else 6.0
            eff_h = 4.0
            start_ms = float(idx * 6.0)
            end_ms = start_ms + 4.0
        elif timing_mode == "fixed":
            eff_d = fms / spd
            eff_h = max(8.0, fms / spd)
            cur_ms += eff_d
            start_ms = cur_ms
            end_ms = start_ms + eff_h
        else:
            eff_d = float(raw_d) / spd
            eff_h = max(10.0, float(raw_h)) / spd
            cur_ms += eff_d
            start_ms = cur_ms
            end_ms = start_ms + eff_h
        lane = -1
        for li, le in enumerate(lane_ends):
            if start_ms >= le - 0.1:
                lane = li
                lane_ends[li] = end_ms
                break
        if lane < 0:
            lane = len(lane_ends)
            lane_ends.append(end_ms)
        max_end = max(max_end, end_ms)
        is_mod = key_s in _MOD_KEYS or key_s.startswith(("Shift", "Ctrl", "Control", "Alt", "Super", "Mod"))
        out.append({
            "idx": idx,
            "key": key_s,
            "keycode": int(st.get("keycode", 0) or 0),
            "raw_delay": raw_d,
            "raw_hold": raw_h,
            "eff_delay": eff_d,
            "eff_hold": eff_h,
            "start_ms": start_ms,
            "end_ms": end_ms,
            "lane": lane,
            "is_mod": is_mod,
        })
    return out, int(round(max_end))


def _calc_macro_total_ms(steps: list, timing_mode: str, fixed_ms: int, speed: float) -> int:
    _items, tot = compile_macro_timeline(steps, timing_mode, fixed_ms, speed)
    return tot


def _text_to_macro_steps(text: str, existing_count: int = 0) -> list[dict]:
    """Converte una stringa in una sequenza di step evdev (con Shift sovrapposto per maiuscole/simboli)."""
    steps: list[dict] = []
    for ch in text:
        delay = 0 if (existing_count == 0 and not steps) else 45
        if ch in _TEXT_CHAR_TO_KEY:
            k_name, need_shift = _TEXT_CHAR_TO_KEY[ch]
        elif "a" <= ch <= "z":
            k_name, need_shift = ch.upper(), False
        elif "A" <= ch <= "Z":
            k_name, need_shift = ch, True
        elif "0" <= ch <= "9":
            k_name, need_shift = ch, False
        else:
            continue
        kc = _COMMON_KEYCODES.get(k_name, 0)
        if kc <= 0:
            continue
        if need_shift:
            steps.append({"key": "Shift_L", "keycode": 42, "delay_ms": delay, "hold_ms": 85})
            steps.append({"key": k_name, "keycode": kc, "delay_ms": 20, "hold_ms": 45})
        else:
            steps.append({"key": k_name, "keycode": kc, "delay_ms": delay, "hold_ms": 45})
    return steps


def handle_cc_keyboard_link(cc_panel, uri: str) -> bool:
    if not uri.startswith("cc_kbd:"):
        return False
    act = uri[len("cc_kbd:"):]
    st = _collect_keyboard_state()

    if act.startswith("rate:"):
        rate = max(10, min(100, int(round(float(act.split(":", 1)[1])))))
        _OPT_KBD["repeat_rate"] = (time.monotonic(), rate)
        probe.enqueue_latest("kbd_kdl_rate", lambda r=rate: _update_kbd_kdl("repeat-rate", r), debounce_ms=160)
        if cc_panel:
            cc_panel.request_throttled_refresh()
        return True
    elif act.startswith("delay:"):
        delay = max(150, min(1000, int(round(float(act.split(":", 1)[1])))))
        _OPT_KBD["repeat_delay"] = (time.monotonic(), delay)
        probe.enqueue_latest("kbd_kdl_delay", lambda d=delay: _update_kbd_kdl("repeat-delay", d), debounce_ms=160)
        if cc_panel:
            cc_panel.request_throttled_refresh()
        return True
    elif act.startswith("numlock:"):
        ok, msg = _update_kbd_kdl("numlock", act.split(":", 1)[1] == "on")
        _set_kbd_feedback(ok, msg)
    elif act.startswith("track:"):
        ok, msg = _update_kbd_kdl("track-layout", act.split(":", 1)[1])
        _set_kbd_feedback(ok, msg)
    elif act.startswith("preset:"):
        r, d = (50, 200) if act.endswith(":fast") else (25, 600)
        ok, msg = _update_kbd_kdl("repeat-rate", r)
        if ok:
            ok, msg = _update_kbd_kdl("repeat-delay", d)
        _set_kbd_feedback(ok, msg)
    elif act == "validate":
        ok, out, err = probe.run_cmd_full(["niri", "validate", "-c", NIRI_CONFIG_FILE], timeout=2.5)
        _set_kbd_feedback(ok, out or err or ("KDL syntax valid" if ok else "Validation failed"))
    elif act == "reload_niri":
        ok, out, err = probe.run_cmd_full(["niri", "msg", "action", "load-config-file"], timeout=2.5)
        _set_kbd_feedback(ok, "Niri config reloaded" if ok else (err or out or "Reload failed"))
    elif act == "test_inline":
        start_inline_input(
            entry_id="cc_kbd_test_inline",
            shell=cc_panel.shell,
            trigger_uri=uri,
            initial_text=str(STATE.get("keyboard", "test_typing", "") or ""),
            max_len=64,
            on_change=lambda txt: STATE.set("keyboard", "test_typing", str(txt or "")),
            on_commit=lambda txt: STATE.set("keyboard", "test_typing", str(txt or "")),
        )
        return True
    elif act.startswith("cap_start:"):
        mode = act.split(":", 1)[1]
        STATE.set("keyboard", "capture_mode", mode)
        STATE.set("keyboard", "capture_preview", "")
        if cc_panel.shell and hasattr(cc_panel.shell, "set_key_capture_grab"):
            cc_panel.shell.set_key_capture_grab(True)
    elif act == "cap_stop":
        _stop_capture(cc_panel)
    elif act == "use_inspected":
        insp = STATE.get("keyboard", "inspected_key", {}) or {}
        if insp.get("combo"):
            _set_bind_draft(combo=insp["combo"])
            _stop_capture(cc_panel)
            cc_panel.set_subtab("shortcuts")
            _set_kbd_feedback(True, t("cc_keyboard.loaded_into_shortcut_builder", p0=insp['combo']))
    elif act == "launch_wev":
        probe.open_terminal(["bash", "-c", "wev || read -p 'wev not installed. Enter...'"])
        return True
    elif act == "launch_libinput":
        probe.open_terminal(["bash", "-c", "sudo libinput debug-events || read -p 'Enter...'"])
        return True
    elif act == "launch_xkbcli":
        probe.open_terminal(["bash", "-c", "xkbcli interactive-wayland || read -p 'Enter...'"])
        return True
    elif act == "localectl":
        ok, out, err = probe.run_cmd_full(["localectl", "status"], timeout=2.0)
        _set_kbd_feedback(ok, (out.splitlines()[0] if out else err) or "localectl queried")

    # --- Active Layouts & Live XKB Search (Punto B) ---
    elif act == "cycle_layout":
        ok, msg = cycle_active_keyboard_layout()
        _set_kbd_feedback(ok, msg)
    elif act.startswith("switch_layout:"):
        ok, msg = switch_active_keyboard_layout(act.split(":", 1)[1])
        _set_kbd_feedback(ok, msg)
    elif act == "layout_search_open":
        cur_q = str(STATE.get("keyboard", "layout_query", "") or "")
        def _commit_raw_xkb(txt: str) -> None:
            val = str(txt or "").strip()
            STATE.set("keyboard", "layout_query", "")
            if not val:
                return
            cur = [x.strip() for x in _collect_keyboard_state()["layout"].split(",") if x.strip()] or ["it"]
            ranked = rank_candidates(catalog_xkb_layouts(exclude=set(cur)), val, limit=1)
            chosen = ranked[0][0] if ranked else val.lower()
            if chosen and chosen not in cur:
                cur.append(chosen)
                ok, msg = _update_kbd_kdl("layout", ",".join(cur))
                _set_kbd_feedback(ok, t("cc_keyboard.added_to_active_layouts", p0=chosen.upper(), p1=msg))
        start_inline_input(
            entry_id="cc_kbd_xkb_picker",
            shell=cc_panel.shell,
            trigger_uri=uri,
            initial_text=cur_q,
            max_len=32,
            is_picker=True,
            on_change=lambda txt: STATE.set("keyboard", "layout_query", str(txt or "")),
            on_cancel=lambda: STATE.set("keyboard", "layout_query", ""),
            on_commit=_commit_raw_xkb,
        )
        return True
    elif act == "layout_search_clear":
        STATE.set("keyboard", "layout_query", "")
    elif act.startswith("add_layout:"):
        code = act.split(":", 1)[1]
        cur = [x.strip() for x in st["layout"].split(",") if x.strip()] or ["it"]
        if code not in cur:
            cur.append(code)
            ok, msg = _update_kbd_kdl("layout", ",".join(cur))
            _set_kbd_feedback(ok, t("cc_keyboard.added_to_active_layouts", p0=code.upper(), p1=msg))
        STATE.set("keyboard", "layout_query", "")
        if cc_panel.shell and cc_panel.shell.prompt_bar.get_visible():
            cc_panel.shell.hide_prompt()
    elif act.startswith("rem_layout:"):
        code = act.split(":", 1)[1]
        cur = [x.strip() for x in st["layout"].split(",") if x.strip()] or ["it"]
        if code in cur and len(cur) > 1:
            cur.remove(code)
            ok, msg = _update_kbd_kdl("layout", ",".join(cur))
            STATE.set("keyboard", "active_layout_idx", 0)
            _set_kbd_feedback(ok, t("cc_keyboard.removed", p0=code.upper(), p1=msg))
    elif act.startswith("move_layout:"):
        _, s_i, s_d = act.split(":", 2)
        idx, delta = int(s_i), int(s_d)
        cur = [x.strip() for x in st["layout"].split(",") if x.strip()] or ["it"]
        nxt = idx + delta
        if 0 <= idx < len(cur) and 0 <= nxt < len(cur):
            cur[idx], cur[nxt] = cur[nxt], cur[idx]
            ok, msg = _update_kbd_kdl("layout", ",".join(cur))
            _set_kbd_feedback(ok, msg)
    elif act.startswith("variant:"):
        ok, msg = _update_kbd_kdl("variant", act.split(":", 1)[1])
        _set_kbd_feedback(ok, msg)
    elif act.startswith("toggle_opt:"):
        opt = act.split(":", 1)[1]
        cur = [x.strip() for x in st["options"].split(",") if x.strip()]
        if opt in cur:
            cur.remove(opt)
        else:
            cur.append(opt)
        ok, msg = _update_kbd_kdl("options", ",".join(cur))
        _set_kbd_feedback(ok, msg)
    elif act.startswith("ui_lang:"):
        set_language(act.split(":", 1)[1])
        _set_kbd_feedback(True, t("cc_keyboard.language_switched_to", p0=get_active_language().upper()))

    # --- Gaming Macro Studio + Centralized Sudoers Flow (Punto C) ---
    elif act == "unlock_uinput":
        priv = get_privileged_action("uinput_unlock")
        if priv and priv.check_installed():
            ok, _msg = execute_privileged("uinput_unlock")
            if ok:
                _set_kbd_feedback(True, t("cc_keyboard.dev_uinput_unlocked_using_authoriz"))
            else:
                request_privilege_modal(cc_panel, "uinput_unlock")
        else:
            request_privilege_modal(cc_panel, "uinput_unlock")
    elif act == "macro_rec_start":
        _RUNTIME_REC["held"].clear()
        _RUNTIME_REC["steps"].clear()
        _RUNTIME_REC["last_ts"] = time.monotonic()
        STATE.set("keyboard", "capture_mode", "macro_rec")
        STATE.set("keyboard", "macro_sel_step", 0)
        _set_macro_draft(steps=[])
        if cc_panel.shell and hasattr(cc_panel.shell, "set_key_capture_grab"):
            cc_panel.shell.set_key_capture_grab(True)
    elif act == "macro_rec_append":
        d = _get_macro_draft()
        _RUNTIME_REC["held"].clear()
        _RUNTIME_REC["steps"] = [dict(s) for s in d["steps"]]
        _RUNTIME_REC["last_ts"] = time.monotonic()
        STATE.set("keyboard", "capture_mode", "macro_rec")
        if cc_panel.shell and hasattr(cc_panel.shell, "set_key_capture_grab"):
            cc_panel.shell.set_key_capture_grab(True)
    elif act == "macro_rec_stop":
        _finish_macro_recording(cc_panel)
        return True
    elif act == "macro_test_sandbox":
        d = _get_macro_draft()
        STATE.set("keyboard", "sandbox_output", _simulate_macro_sandbox(d))
        _set_kbd_feedback(True, t("cc_keyboard.sandbox_tested_msg", p0=d["name"], p1=len(d["steps"])))
    elif act == "sandbox_clear":
        STATE.set("keyboard", "sandbox_output", "")
    elif act == "sandbox_inline":
        start_inline_input(
            entry_id="cc_mac_sandbox",
            shell=cc_panel.shell,
            trigger_uri=uri,
            initial_text=str(STATE.get("keyboard", "sandbox_output", "") or ""),
            max_len=64,
            on_change=lambda txt: STATE.set("keyboard", "sandbox_output", str(txt or "")),
            on_commit=lambda txt: STATE.set("keyboard", "sandbox_output", str(txt or "")),
        )
        return True
    elif act == "macro_panic_stop":
        _stop_active_macros()
        _stop_capture(cc_panel)
        _set_kbd_feedback(True, t("cc_keyboard.panic_stopped_msg", p0="STOP"))
    elif act.startswith("macro_panic_key:"):
        pk = act.split(":", 1)[1]
        if pk in ("F12", "Pause"):
            _set_macro_draft(panic_key=pk)
    elif act.startswith("macro_countdown:"):
        _set_macro_draft(countdown_s=max(0, min(15, int(act.split(":", 1)[1]))))
    elif act == "macro_inline_name":
        start_inline_input(
            entry_id="cc_mac_name",
            shell=cc_panel.shell,
            trigger_uri=uri,
            initial_text=_get_macro_draft()["name"],
            max_len=36,
            on_commit=lambda txt: _set_macro_draft(name=txt.strip()[:36]) if txt.strip() else None,
        )
        return True
    elif act == "macro_inline_combo":
        start_inline_input(
            entry_id="cc_mac_combo",
            shell=cc_panel.shell,
            trigger_uri=uri,
            initial_text=_get_macro_draft()["combo"],
            max_len=36,
            on_commit=lambda txt: _set_macro_draft(combo=txt.strip()[:36]) if txt.strip() else None,
        )
        return True
    elif act == "macro_new":
        _RUNTIME_REC["steps"].clear()
        STATE.set("keyboard", "macro_draft", _norm_macro({"id": f"mac_{int(time.time())}", "name": "New Gaming Macro", "steps": []}, 0))
    elif act == "macro_dup":
        d = _get_macro_draft()
        d["id"] = f"mac_{int(time.time())}"
        d["name"] = f"{d['name'][:28]} (Copy)"
        d["combo"] = ""
        STATE.set("keyboard", "macro_draft", _norm_macro(d, 0))
    elif act == "macro_reset_draft":
        d = _get_macro_draft()
        _RUNTIME_REC["steps"].clear()
        saved = _find_saved_macro(d["id"])
        if saved is not None:
            STATE.set("keyboard", "macro_draft", _norm_macro(saved, 0))
        else:
            STATE.set("keyboard", "macro_draft", _norm_macro({"id": d["id"], "name": "New Gaming Macro", "steps": []}, 0))
        _set_kbd_feedback(True, t("cc_keyboard.draft_reset_msg"))
    elif act.startswith("macro_tmode:"):
        _set_macro_draft(timing_mode=act.split(":", 1)[1])
    elif act.startswith("macro_fixedms:"):
        _set_macro_draft(fixed_delay_ms=int(act.split(":", 1)[1]))
    elif act.startswith("macro_speed:"):
        _set_macro_draft(speed_mult=float(act.split(":", 1)[1]))
    elif act.startswith("macro_rep:"):
        _, rmode, rcnt = act.split(":", 2)
        _set_macro_draft(repeat_mode=rmode, repeat_count=int(rcnt))
    elif act == "macro_inline_rep":
        start_inline_input(
            entry_id="cc_mac_rep",
            shell=cc_panel.shell,
            trigger_uri=uri,
            initial_text=str(_get_macro_draft()["repeat_count"]),
            max_len=4,
            validator=lambda s: s.strip().isdigit() and 1 <= int(s.strip()) <= 999,
            on_commit=lambda txt: _set_macro_draft(repeat_mode="count", repeat_count=max(1, min(999, int(txt.strip())))),
        )
        return True
    elif act == "macro_norm":
        d = _get_macro_draft()
        for i, s in enumerate(d["steps"]):
            s["delay_ms"] = 0 if i == 0 else 35
            s["hold_ms"] = 45
        _set_macro_draft(steps=d["steps"])
        _set_kbd_feedback(True, t("cc_keyboard.normalized_all_steps_to_35ms_delay"))
    elif act == "macro_clear_steps":
        _RUNTIME_REC["steps"].clear()
        STATE.set("keyboard", "macro_sel_step", 0)
        _set_macro_draft(steps=[])
    elif act == "macro_inline_text":
        def _commit_text_steps(txt: str) -> None:
            val = str(txt or "")
            if not val:
                return
            d_m = _get_macro_draft()
            added = _text_to_macro_steps(val, existing_count=len(d_m["steps"]))
            if not added:
                return
            new_steps = list(d_m["steps"]) + added
            STATE.set("keyboard", "macro_sel_step", len(new_steps) - 1)
            _set_macro_draft(steps=new_steps)
            _set_kbd_feedback(True, t("cc_keyboard.inserted_text_steps", p0=len(added)))
        start_inline_input(
            entry_id="cc_mac_text_add",
            shell=cc_panel.shell,
            trigger_uri=uri,
            initial_text="",
            max_len=48,
            on_commit=_commit_text_steps,
        )
        return True
    elif act.startswith("step_select:"):
        idx = int(act.split(":", 1)[1])
        steps = _get_macro_draft()["steps"]
        if steps:
            STATE.set("keyboard", "macro_sel_step", max(0, min(len(steps) - 1, idx)))
    elif act.startswith("step_nudge:"):
        _, s_i, fld, s_delta = act.split(":", 3)
        idx, delta = int(s_i), int(s_delta)
        steps = [dict(s) for s in _get_macro_draft()["steps"]]
        if 0 <= idx < len(steps) and fld in ("delay_ms", "hold_ms"):
            lo = 0 if fld == "delay_ms" else 10
            steps[idx][fld] = max(lo, min(10000, int(steps[idx].get(fld, lo)) + delta))
            STATE.set("keyboard", "macro_sel_step", idx)
            _set_macro_draft(steps=steps)
    elif act.startswith("step_set_timing:"):
        _, s_i, s_del, s_hold = act.split(":", 3)
        idx = int(s_i)
        steps = [dict(s) for s in _get_macro_draft()["steps"]]
        if 0 <= idx < len(steps):
            steps[idx]["delay_ms"] = max(0, min(10000, int(s_del)))
            steps[idx]["hold_ms"] = max(10, min(10000, int(s_hold)))
            STATE.set("keyboard", "macro_sel_step", idx)
            _set_macro_draft(steps=steps)
    elif act.startswith("step_dup:"):
        idx = int(act.split(":", 1)[1])
        steps = [dict(s) for s in _get_macro_draft()["steps"]]
        if 0 <= idx < len(steps):
            clone = dict(steps[idx])
            if clone["delay_ms"] == 0:
                clone["delay_ms"] = 45
            steps.insert(idx + 1, clone)
            STATE.set("keyboard", "macro_sel_step", idx + 1)
            _set_macro_draft(steps=steps)
    elif act == "step_inline_add":
        def _commit_step_add(txt: str) -> None:
            val = str(txt or "").strip()
            if not val:
                return
            parts = [p.strip() for p in val.split(",")]
            key = parts[0]
            del_ms = max(0, int(parts[1])) if len(parts) > 1 and parts[1].isdigit() else 50
            hold_ms = max(10, int(parts[2])) if len(parts) > 2 and parts[2].isdigit() else 60
            evc = _COMMON_KEYCODES.get(key, _COMMON_KEYCODES.get(key.upper(), 30))
            d_m = _get_macro_draft()
            steps_n = list(d_m["steps"]) + [{"key": key, "keycode": evc, "delay_ms": del_ms, "hold_ms": hold_ms}]
            STATE.set("keyboard", "macro_sel_step", len(steps_n) - 1)
            _set_macro_draft(steps=steps_n)
        start_inline_input(
            entry_id="cc_mac_step_add",
            shell=cc_panel.shell,
            trigger_uri=uri,
            initial_text="W, 50, 80",
            max_len=24,
            on_commit=_commit_step_add,
        )
        return True
    elif act.startswith("step_inline_edit:"):
        idx = int(act.split(":", 1)[1])
        steps = _get_macro_draft()["steps"]
        if 0 <= idx < len(steps):
            s = steps[idx]
            def _commit_step_edit(txt: str, step_idx: int = idx) -> None:
                parts = [p.strip() for p in str(txt or "").split(",")]
                cur_steps = list(_get_macro_draft()["steps"])
                if 0 <= step_idx < len(cur_steps) and parts[0].isdigit():
                    cur_steps[step_idx]["delay_ms"] = max(0, min(10000, int(parts[0])))
                    if len(parts) > 1 and parts[1].isdigit():
                        cur_steps[step_idx]["hold_ms"] = max(10, min(10000, int(parts[1])))
                    _set_macro_draft(steps=cur_steps)
                    _set_kbd_feedback(True, t("cc_keyboard.updated_step_timing", p0=step_idx + 1))
            start_inline_input(
                entry_id=f"cc_mac_step_{idx}",
                shell=cc_panel.shell,
                trigger_uri=uri,
                initial_text=f"{s['delay_ms']}, {s['hold_ms']}",
                max_len=16,
                validator=lambda txt: txt.strip().split(",")[0].strip().isdigit(),
                on_commit=_commit_step_edit,
            )
            return True
    elif act.startswith("step_move:"):
        _, s_i, s_d = act.split(":", 2)
        idx, delta = int(s_i), int(s_d)
        steps = _get_macro_draft()["steps"]
        nxt = idx + delta
        if 0 <= idx < len(steps) and 0 <= nxt < len(steps):
            steps[idx], steps[nxt] = steps[nxt], steps[idx]
            STATE.set("keyboard", "macro_sel_step", nxt)
            _set_macro_draft(steps=steps)
    elif act.startswith("step_del:"):
        idx = int(act.split(":", 1)[1])
        steps = _get_macro_draft()["steps"]
        if 0 <= idx < len(steps):
            steps.pop(idx)
            STATE.set("keyboard", "macro_sel_step", max(0, min(len(steps) - 1, idx)) if steps else 0)
            _set_macro_draft(steps=steps)
    elif act == "macro_save_draft":
        d = _get_macro_draft()
        _save_macro_draft_entry(d)
        _set_kbd_feedback(True, t("cc_keyboard.saved_macro_steps", p0=d['name'], p1=len(d['steps'])))
    elif act in ("macro_play_draft", "macro_run_engine"):
        d = _get_macro_draft()
        if _ensure_uinput_or_request(cc_panel):
            _save_macro_draft_entry(d)
            probe.spawn_bg([MACRO_PLAY_BIN, d["id"]])
            _set_kbd_feedback(True, t("cc_keyboard.replaying_macro_via_nc_macro_play", p0=d['name']))
    elif act == "macro_bind_draft":
        d = _get_macro_draft()
        if d["combo"]:
            _save_macro_draft_entry(d)
            ok, msg = _update_niri_bind(d["combo"], f'spawn "{MACRO_PLAY_BIN}" "{d["id"]}"')
            _set_kbd_feedback(ok, msg)
    elif act == "macro_unbind_draft":
        d = _get_macro_draft()
        if d["combo"]:
            ok, msg = _update_niri_bind(d["combo"], remove=True)
            _set_kbd_feedback(ok, msg)
    elif act.startswith("macro_load:"):
        idx = int(act.split(":", 1)[1])
        macros = _load_macros()
        if 0 <= idx < len(macros):
            _RUNTIME_REC["steps"].clear()
            STATE.set("keyboard", "macro_sel_step", 0)
            STATE.set("keyboard", "macro_draft", macros[idx])
    elif act.startswith("run_macro:"):
        idx = int(act.split(":", 1)[1])
        macros = _load_macros()
        if 0 <= idx < len(macros):
            m = macros[idx]
            if _ensure_uinput_or_request(cc_panel):
                probe.spawn_bg([MACRO_PLAY_BIN, m["id"]])
                _set_kbd_feedback(True, t("cc_keyboard.running_macro", p0=m['name']))
    elif act.startswith("bind_macro:"):
        idx = int(act.split(":", 1)[1])
        macros = _load_macros()
        if 0 <= idx < len(macros):
            m = macros[idx]
            if not m["combo"]:
                _set_kbd_feedback(False, t("cc_keyboard.load_macro_and_capture_a_trigger_s"))
            else:
                ok, msg = _update_niri_bind(m["combo"], f'spawn "{MACRO_PLAY_BIN}" "{m["id"]}"')
                _set_kbd_feedback(ok, msg)
    elif act.startswith("del_macro:"):
        idx = int(act.split(":", 1)[1])
        macros = _load_macros()
        if 0 <= idx < len(macros):
            macros.pop(idx)
            _save_macros(macros)
            from core.pad_mapper_core import reload_pad_mapper_if_running
            reload_pad_mapper_if_running()
    elif act.startswith("lib_flt:"):
        STATE.set("keyboard", "macro_lib_filter", act.split(":", 1)[1])
    elif act == "lib_search_open":
        cur_q = str(STATE.get("keyboard", "macro_lib_query", "") or "")
        start_inline_input(
            entry_id="cc_kbd_lib_flt",
            shell=cc_panel.shell,
            trigger_uri=uri,
            initial_text=cur_q,
            max_len=40,
            on_change=lambda txt: STATE.set("keyboard", "macro_lib_query", str(txt or "")),
            on_commit=lambda txt: STATE.set("keyboard", "macro_lib_query", str(txt or "").strip()),
        )
        return True
    elif act == "lib_search_clear":
        STATE.set("keyboard", "macro_lib_query", "")
    elif act == "macro_export":
        macros = _load_macros()
        payload = json.dumps(macros, indent=2)
        try:
            os.makedirs(os.path.dirname(MACROS_EXPORT_FILE), exist_ok=True)
            with open(MACROS_EXPORT_FILE, "w", encoding="utf-8") as f:
                f.write(payload)
            ok_rb = open(MACROS_EXPORT_FILE, "r", encoding="utf-8").read() == payload
            if ok_rb and probe.has_bin("wl-copy"):
                try:
                    subprocess.run(["wl-copy"], input=payload.encode("utf-8"), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=1.5)
                except Exception:
                    pass
            _set_kbd_feedback(ok_rb, t("cc_keyboard.exported_macros_msg", p0=len(macros), p1=MACROS_EXPORT_FILE))
        except Exception as e:
            _set_kbd_feedback(False, str(e))

    # --- Shortcut Builder, Defaults Restore & Orphans (Punto A) ---
    elif act.startswith("restore_defaults:"):
        _start_restore_defaults(cc_panel, act.split(":", 1)[1])
    elif act == "conflict_overwrite":
        wiz = dict(STATE.get("keyboard", "conflict_wizard", {}) or {})
        if wiz.get("conflicts"):
            _apply_conflict_overwrite(wiz)
    elif act == "conflict_guided":
        wiz = dict(STATE.get("keyboard", "conflict_wizard", {}) or {})
        wiz["mode"] = "guided"
        wiz["step"] = 0
        STATE.set("keyboard", "conflict_wizard", wiz)
        STATE.set("keyboard", "capture_mode", "conflict_rebind")
        STATE.set("keyboard", "capture_preview", "")
        if cc_panel.shell and hasattr(cc_panel.shell, "set_key_capture_grab"):
            cc_panel.shell.set_key_capture_grab(True)
    elif act == "conflict_step_orphan":
        wiz = dict(STATE.get("keyboard", "conflict_wizard", {}) or {})
        step_i = int(wiz.get("step", 0))
        confs = wiz.get("conflicts", [])
        if 0 <= step_i < len(confs):
            c = confs[step_i]
            _add_orphan_action(c["current_action"], prev_combo=c["combo"])
            if c.get("old_default_combo") and c["old_default_combo"] != c["combo"]:
                _update_niri_bind(c["old_default_combo"], remove=True, auto_reload=False)
            _update_niri_bind(c["combo"], c["default_action"], auto_reload=False)
            _advance_conflict_wizard(cc_panel, wiz, "Guided default restore completed; moved user bind to Orphans")
    elif act == "conflict_step_skip":
        wiz = dict(STATE.get("keyboard", "conflict_wizard", {}) or {})
        _advance_conflict_wizard(cc_panel, wiz)
    elif act == "conflict_cancel":
        STATE.set("keyboard", "conflict_wizard", {})
        _stop_capture(cc_panel)
    elif act == "bind_inline_combo":
        start_inline_input(
            entry_id="cc_bind_combo",
            shell=cc_panel.shell,
            trigger_uri=uri,
            initial_text=_get_bind_draft()["combo"],
            max_len=40,
            on_commit=lambda txt: _set_bind_draft(combo=txt.strip()) if txt.strip() else None,
        )
        return True
    elif act == "bind_flt_open":
        start_inline_input(
            entry_id="cc_bind_flt",
            shell=cc_panel.shell,
            trigger_uri=uri,
            initial_text=str(STATE.get("keyboard", "shortcut_query", "") or ""),
            max_len=40,
            on_change=lambda txt: (STATE.set("keyboard", "shortcut_query", str(txt or "")), STATE.set("keyboard", "bind_page", 0)),
            on_commit=lambda txt: (STATE.set("keyboard", "shortcut_query", str(txt or "").strip()), STATE.set("keyboard", "bind_page", 0)),
        )
        return True
    elif act == "bind_flt_clear":
        STATE.set("keyboard", "shortcut_query", "")
        STATE.set("keyboard", "bind_page", 0)
    elif act.startswith("bind_page:"):
        STATE.set("keyboard", "bind_page", max(0, int(act.split(":", 1)[1])))
    elif act == "bind_new":
        STATE.set("keyboard", "bind_draft", {})
        _stop_capture(cc_panel)
    elif act.startswith("bind_mode:"):
        _set_bind_draft(mode=act.split(":", 1)[1])
    elif act.startswith("bind_std_cat:"):
        cat = act.split(":", 1)[1]
        first = next((d["action"] for d in DEFAULT_NIRI_SHORTCUTS if d["cat"] == cat), "close-window")
        _set_bind_draft(mode="standard", std_cat=cat, std_act=first)
    elif act.startswith("bind_std_act:"):
        did = act.split(":", 1)[1]
        d = next((x for x in DEFAULT_NIRI_SHORTCUTS if x["id"] == did), None)
        if d:
            bd = _get_bind_draft()
            updates = {"mode": "standard", "std_cat": d["cat"], "std_act": d["action"]}
            if not bd["combo"]:
                updates["combo"] = d["combo"]
            _set_bind_draft(**updates)
    elif act.startswith("bind_std_default:"):
        did = act.split(":", 1)[1]
        d = next((x for x in DEFAULT_NIRI_SHORTCUTS if x["id"] == did), None)
        if d:
            cur_on_cmb = next((b["action"] for b in st["binds"] if b["combo"] == d["combo"]), "")
            if cur_on_cmb and _norm_action_str(cur_on_cmb) != _norm_action_str(d["action"]):
                _add_orphan_action(cur_on_cmb, prev_combo=d["combo"])
            ok, msg = _update_niri_bind(d["combo"], d["action"], auto_reload=True)
            _set_kbd_feedback(ok, t("cc_keyboard.restored", p0=d['combo'], p1=d['action'], p2=msg))
    elif act.startswith("bind_std_pick:"):
        did = act.split(":", 1)[1]
        d = next((x for x in DEFAULT_NIRI_SHORTCUTS if x["id"] == did), None)
        if d:
            _set_bind_draft(combo="", mode="standard", std_cat=d["cat"], std_act=d["action"])
            STATE.set("keyboard", "capture_mode", "shortcut_combo")
            STATE.set("keyboard", "capture_preview", "")
            if cc_panel.shell and hasattr(cc_panel.shell, "set_key_capture_grab"):
                cc_panel.shell.set_key_capture_grab(True)
    elif act.startswith("bind_orphan:"):
        idx = int(act.split(":", 1)[1])
        by_norm = {_norm_action_str(b["action"]) for b in st["binds"]}
        orphans = [o for o in _load_shortcuts_store()["orphans"] if _norm_action_str(o.get("action")) not in by_norm]
        if 0 <= idx < len(orphans):
            o = orphans[idx]
            _set_bind_draft(combo="", mode="spawn", cmd=o["action"][6:].replace('"', "") if o["action"].startswith("spawn ") else o["action"])
            if not o["action"].startswith("spawn "):
                _set_bind_draft(mode="standard", std_act=o["action"])
            STATE.set("keyboard", "capture_mode", "shortcut_combo")
            STATE.set("keyboard", "capture_preview", "")
            if cc_panel.shell and hasattr(cc_panel.shell, "set_key_capture_grab"):
                cc_panel.shell.set_key_capture_grab(True)
    elif act.startswith("del_orphan:"):
        idx = int(act.split(":", 1)[1])
        by_norm = {_norm_action_str(b["action"]) for b in st["binds"]}
        store = _load_shortcuts_store()
        orphans = [o for o in store["orphans"] if _norm_action_str(o.get("action")) not in by_norm]
        if 0 <= idx < len(orphans):
            _remove_orphan_action(orphans[idx]["action"])
    elif act == "bind_prompt_cmd":
        cc_panel.request_prompt("cc_kbd_bind_cmd", t("cc_keyboard.command_app_to_launch_e_g_ghostty"), _get_bind_draft()["cmd"], title=t("cc_keyboard.custom_launch_command"), required=True, completion="shell_cmd")
        return True
    elif act.startswith("bind_nc:"):
        _set_bind_draft(nc_target=act.split(":", 1)[1])
    elif act == "bind_clear":
        STATE.set("keyboard", "bind_draft", {})
    elif act == "bind_save":
        bd = _get_bind_draft()
        act_str = _build_niri_action_from_draft(bd)
        if bd["combo"] and act_str:
            cur_on_cmb = next((b["action"] for b in st["binds"] if b["combo"] == bd["combo"]), "")
            if cur_on_cmb and _norm_action_str(cur_on_cmb) != _norm_action_str(act_str) and bd["edit_orig"] != bd["combo"]:
                _add_orphan_action(cur_on_cmb, prev_combo=bd["combo"])
            if bd["edit_orig"] and bd["edit_orig"] != bd["combo"]:
                _update_niri_bind(bd["edit_orig"], remove=True, auto_reload=False)
            ok, msg = _update_niri_bind(bd["combo"], act_str, auto_reload=True)
            if ok:
                _remove_orphan_action(act_str)
                if bd["mode"] == "spawn":
                    _record_custom_action(act_str, bd["cmd"])
                STATE.set("keyboard", "bind_draft", {})
            _set_kbd_feedback(ok, msg)
    elif act.startswith("bind_edit:"):
        idx = int(act.split(":", 1)[1])
        if 0 <= idx < len(st["binds"]):
            b = st["binds"][idx]
            a_str = b["action"]
            std = _std_action_lookup().get(_norm_action_str(a_str))
            nc_match = re.search(r'nc-ctl"\s+"(popup|settings)"\s+"([a-z0-9_]+)"', a_str)
            if std:
                _set_bind_draft(combo=b["combo"], mode="standard", std_cat=std["cat"], std_act=std["action"], edit_orig=b["combo"])
            elif nc_match:
                _set_bind_draft(combo=b["combo"], mode="ncui", nc_target=f"{nc_match.group(1)} {nc_match.group(2)}", edit_orig=b["combo"])
            elif a_str.startswith("spawn "):
                try:
                    cmd_s = shlex.join(shlex.split(a_str[6:]))
                except Exception:
                    cmd_s = a_str[6:]
                _set_bind_draft(combo=b["combo"], mode="spawn", cmd=cmd_s, edit_orig=b["combo"])
            else:
                _set_bind_draft(combo=b["combo"], mode="standard", std_cat="window", std_act=a_str, edit_orig=b["combo"])
    elif act.startswith("unbind:"):
        idx = int(act.split(":", 1)[1])
        if 0 <= idx < len(st["binds"]):
            b = st["binds"][idx]
            _add_orphan_action(b["action"], prev_combo=b["combo"])
            ok, msg = _update_niri_bind(b["combo"], remove=True, auto_reload=True)
            _set_kbd_feedback(ok, t("cc_keyboard.unbound_and_moved_action_to_orphan", p0=b['combo']))
    elif act.startswith("bind_flt:"):
        STATE.set("keyboard", "bind_filter", act.split(":", 1)[1])
        STATE.set("keyboard", "bind_page", 0)
    elif act.startswith("del_bind:"):
        ok, msg = _update_niri_bind(act.split(":", 1)[1], remove=True, auto_reload=True)
        _set_kbd_feedback(ok, msg)

    if cc_panel.shell:
        cc_panel.shell.refresh_view()
    return True


def handle_cc_keyboard_prompt_change(cc_panel, action_id: str, text: str) -> bool:
    if action_id == "cc_kbd_layout_search":
        STATE.set("keyboard", "layout_query", str(text or ""))
        return True
    if action_id == "cc_kbd_lib_search":
        STATE.set("keyboard", "macro_lib_query", str(text or ""))
        return True
    return False


def handle_cc_keyboard_prompt(cc_panel, action_id: str, text: str) -> bool:
    if not action_id.startswith("cc_kbd_"):
        return False
    val = str(text or "").strip()
    if action_id == "cc_kbd_layout_search":
        if val:
            ql = val.lower()
            cur = [x.strip() for x in _collect_keyboard_state()["layout"].split(",") if x.strip()]
            matches = [(c, n) for c, n in _load_all_xkb_layouts() if c not in cur and (ql == c.lower() or ql in c.lower() or ql in n.lower())]
            matches.sort(key=lambda pair: (0 if pair[0].lower() == ql else 1, pair[1]))
            if matches:
                chosen = matches[0][0]
                cur.append(chosen)
                ok, msg = _update_kbd_kdl("layout", ",".join(cur))
                _set_kbd_feedback(ok, t("cc_keyboard.added_to_active_layouts", p0=chosen.upper(), p1=msg))
                STATE.set("keyboard", "layout_query", "")
        return True
    if action_id == "cc_kbd_lib_search":
        STATE.set("keyboard", "macro_lib_query", val)
        return True
    if action_id == "cc_kbd_test":
        STATE.set("keyboard", "test_typing", val)
        return True
    if action_id == "cc_kbd_sandbox_pad":
        STATE.set("keyboard", "sandbox_output", val)
        return True
    if action_id == "cc_kbd_bind_combo" and val:
        _set_bind_draft(combo=val)
        return True
    if action_id == "cc_kbd_bind_cmd" and val:
        _set_bind_draft(cmd=val)
        return True
    if action_id == "cc_kbd_mac_name" and val:
        _set_macro_draft(name=val[:36])
        return True
    if action_id == "cc_kbd_mac_combo" and val:
        _set_macro_draft(combo=val[:36])
        return True
    if action_id == "cc_kbd_mac_rep" and val.isdigit():
        _set_macro_draft(repeat_mode="count", repeat_count=max(1, min(999, int(val))))
        return True
    if action_id == "cc_kbd_step_add" and val:
        parts = [p.strip() for p in val.split(",")]
        key = parts[0]
        del_ms = max(0, int(parts[1])) if len(parts) > 1 and parts[1].isdigit() else 50
        hold_ms = max(10, int(parts[2])) if len(parts) > 2 and parts[2].isdigit() else 60
        evc = _COMMON_KEYCODES.get(key, _COMMON_KEYCODES.get(key.upper(), 30))
        d = _get_macro_draft()
        steps = list(d["steps"]) + [{"key": key, "keycode": evc, "delay_ms": del_ms, "hold_ms": hold_ms}]
        _set_macro_draft(steps=steps)
        return True
    if action_id.startswith("cc_kbd_step_edit|") and val:
        idx = int(action_id.split("|", 1)[1])
        parts = [p.strip() for p in val.split(",")]
        steps = list(_get_macro_draft()["steps"])
        if 0 <= idx < len(steps) and parts[0].isdigit():
            steps[idx]["delay_ms"] = max(0, min(10000, int(parts[0])))
            if len(parts) > 1 and parts[1].isdigit():
                steps[idx]["hold_ms"] = max(10, min(10000, int(parts[1])))
            _set_macro_draft(steps=steps)
            _set_kbd_feedback(True, t("cc_keyboard.updated_step_timing", p0=idx + 1))
        return True
    return False


def _mouse_read_kdl() -> str:
    if not os.path.exists(NIRI_CONFIG_FILE):
        return ""
    try:
        return open(NIRI_CONFIG_FILE, "r", encoding="utf-8").read()
    except Exception:
        return ""


def _mouse_write_kdl(new_text: str) -> tuple[bool, str]:
    try:
        os.makedirs(os.path.dirname(NIRI_CONFIG_FILE), exist_ok=True)
        old_text = _mouse_read_kdl()
        with open(NIRI_CONFIG_FILE, "w", encoding="utf-8") as f:
            f.write(new_text)
        if probe.has_bin("niri"):
            ok, _out, err = probe.run_cmd_full(["niri", "validate", "-c", NIRI_CONFIG_FILE], timeout=2.5)
            if not ok:
                with open(NIRI_CONFIG_FILE, "w", encoding="utf-8") as f:
                    f.write(old_text)
                return False, f"niri validate failed: {err or 'syntax error'}"
        if _mouse_read_kdl() != new_text:
            return False, "Read-back mismatch on config.kdl"
        probe.spawn_bg(["niri", "msg", "action", "load-config-file"])
        return True, "Updated & validated in ~/.config/niri/config.kdl"
    except Exception as e:
        return False, str(e)


def _mouse_find_block_span(text: str, block_name: str, start: int = 0, end: int | None = None) -> tuple[int, int] | None:
    sub = text[start:end]
    m = re.search(rf"(?m)^[ \t]*{re.escape(block_name)}\s*\{{", sub)
    if not m:
        return None
    brace_open = start + m.end() - 1
    depth = 0
    limit = end if end is not None else len(text)
    for i in range(brace_open, limit):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return start + m.start(), i + 1
    return None


def _mouse_has_flag(block: str, flag: str) -> bool:
    return bool(re.search(rf"(?m)^[ \t]*{re.escape(flag)}\b", block))


def _mouse_set_flag_in_block(block: str, flag: str, enabled: bool, indent: str = "        ") -> str:
    cleaned = re.sub(rf"(?m)^[ \t]*(?://\s*)?{re.escape(flag)}[ \t]*\n?", "", block)
    if not enabled:
        return cleaned
    idx = cleaned.rfind("}")
    if idx == -1:
        return cleaned
    prefix = "" if cleaned[:idx].endswith("\n") else "\n"
    return cleaned[:idx] + f"{prefix}{indent}{flag}\n" + cleaned[idx:]


def _mouse_set_kv_in_block(block: str, key: str, raw_val: str, indent: str = "        ") -> str:
    pat = re.compile(rf"(?m)^([ \t]*)(?://\s*)?{re.escape(key)}\s+[^\n]+")
    if pat.search(block):
        return pat.sub(rf"\g<1>{key} {raw_val}", block, count=1)
    idx = block.rfind("}")
    if idx == -1:
        return block
    prefix = "" if block[:idx].endswith("\n") else "\n"
    return block[:idx] + f"{prefix}{indent}{key} {raw_val}\n" + block[idx:]


def _collect_mouse_state() -> dict:
    txt = _mouse_read_kdl()
    st = {
        "focus_follows_mouse": False,
        "left_handed": False,
        "natural_scroll": False,
        "middle_emulation": False,
        "scroll_factor": 1.0,
        "accel_speed": 0.0,
        "accel_profile": "adaptive",
        "tp_tap": False,
        "tp_dwt": False,
        "tp_natural_scroll": False,
    }
    inp = _mouse_find_block_span(txt, "input")
    if not inp:
        return st
    inp_blk = txt[inp[0]:inp[1]]
    st["focus_follows_mouse"] = _mouse_has_flag(inp_blk, "focus-follows-mouse")

    ms = _mouse_find_block_span(txt, "mouse", inp[0], inp[1])
    if ms:
        mb = txt[ms[0]:ms[1]]
        st["left_handed"] = _mouse_has_flag(mb, "left-handed")
        st["natural_scroll"] = _mouse_has_flag(mb, "natural-scroll")
        st["middle_emulation"] = _mouse_has_flag(mb, "middle-emulation")
        m_sf = re.search(r"(?m)^\s*scroll-factor\s+([0-9.+-]+)", mb)
        if m_sf:
            try:
                st["scroll_factor"] = float(m_sf.group(1))
            except Exception:
                pass
        m_as = re.search(r"(?m)^\s*accel-speed\s+([0-9.+-]+)", mb)
        if m_as:
            try:
                st["accel_speed"] = max(-1.0, min(1.0, float(m_as.group(1))))
            except Exception:
                pass
        m_ap = re.search(r'(?m)^\s*accel-profile\s+"([^"]+)"', mb)
        if m_ap:
            st["accel_profile"] = m_ap.group(1)

    tp = _mouse_find_block_span(txt, "touchpad", inp[0], inp[1])
    if tp:
        tb = txt[tp[0]:tp[1]]
        st["tp_tap"] = _mouse_has_flag(tb, "tap")
        st["tp_dwt"] = _mouse_has_flag(tb, "dwt")
        st["tp_natural_scroll"] = _mouse_has_flag(tb, "natural-scroll")
    ov_as = _OPT_MOUSE.get("accel_speed")
    if ov_as and (time.monotonic() - ov_as[0]) < 2.5:
        st["accel_speed"] = ov_as[1]
    return st


def _mutate_niri_input(subblock: str | None, updater) -> tuple[bool, str]:
    txt = _mouse_read_kdl()
    inp = _mouse_find_block_span(txt, "input")
    if not inp:
        txt = txt.rstrip() + "\n\ninput {\n}\n"
        inp = _mouse_find_block_span(txt, "input")
    if not inp:
        return False, "Could not locate or create input { } block"

    if subblock is None:
        blk = txt[inp[0]:inp[1]]
        new_blk = updater(blk)
        return _mouse_write_kdl(txt[:inp[0]] + new_blk + txt[inp[1]:])

    sp = _mouse_find_block_span(txt, subblock, inp[0], inp[1])
    if not sp:
        idx = inp[1] - 1
        prefix = "" if txt[:idx].endswith("\n") else "\n"
        txt = txt[:idx] + f"{prefix}    {subblock} {{\n    }}\n" + txt[idx:]
        inp = _mouse_find_block_span(txt, "input")
        sp = _mouse_find_block_span(txt, subblock, inp[0], inp[1])
    if not sp:
        return False, f"Failed creating {subblock} {{ }} block"

    blk = txt[sp[0]:sp[1]]
    new_blk = updater(blk)
    return _mouse_write_kdl(txt[:sp[0]] + new_blk + txt[sp[1]:])


def _mouse_feedback_cards() -> list:
    if not _MSG and not _ERR:
        return []
    return [FeedbackBox(t("cc_mouse.status_title"), not bool(_ERR), _ERR if _ERR else _MSG)]


def _mouse_toggle_pair(uri_on: str, uri_off: str, cur: bool) -> str:
    return f"{SelectPill(t('common.on'), uri_on, selected=cur)} {SelectPill(t('common.off'), uri_off, selected=not cur)}"


def handle_cc_mouse_link(cc_panel, uri: str) -> bool:
    global _MSG, _ERR
    if not uri.startswith("cc_mouse:"):
        return False
    _MSG = ""
    _ERR = ""
    parts = uri.split(":")
    cmd = parts[1]

    ok, msg = False, "Unknown command"
    if cmd == "flag" and len(parts) >= 4:
        flag, en = parts[2], (parts[3] == "1")
        ok, msg = _mutate_niri_input("mouse", lambda b: _mouse_set_flag_in_block(b, flag, en))
    elif cmd == "inflag" and len(parts) >= 4:
        flag, en = parts[2], (parts[3] == "1")
        ok, msg = _mutate_niri_input(None, lambda b: _mouse_set_flag_in_block(b, flag, en, indent="    "))
    elif cmd == "sf" and len(parts) >= 3:
        val = parts[2]
        ok, msg = _mutate_niri_input("mouse", lambda b: _mouse_set_kv_in_block(b, "scroll-factor", val))
    elif cmd == "prof" and len(parts) >= 3 and parts[2] in ("adaptive", "flat"):
        prof = parts[2]
        ok, msg = _mutate_niri_input("mouse", lambda b: _mouse_set_kv_in_block(b, "accel-profile", f'"{prof}"'))
    elif cmd == "accel" and len(parts) >= 3:
        val = max(-1.0, min(1.0, float(parts[2])))
        _OPT_MOUSE["accel_speed"] = (time.monotonic(), val)
        probe.enqueue_latest("mouse_accel_kdl", lambda v=val: _mutate_niri_input("mouse", lambda b: _mouse_set_kv_in_block(b, "accel-speed", f"{v:.2f}")), debounce_ms=160)
        if cc_panel:
            cc_panel.request_throttled_refresh()
        return True
    elif cmd == "accel_step" and len(parts) >= 3:
        cur = _collect_mouse_state()["accel_speed"]
        val = max(-1.0, min(1.0, round(cur + float(parts[2]), 2)))
        _OPT_MOUSE["accel_speed"] = (time.monotonic(), val)
        probe.enqueue_latest("mouse_accel_kdl", lambda v=val: _mutate_niri_input("mouse", lambda b: _mouse_set_kv_in_block(b, "accel-speed", f"{v:.2f}")), debounce_ms=160)
        if cc_panel:
            cc_panel.request_throttled_refresh()
        return True
    elif cmd == "tp" and len(parts) >= 4:
        flag, en = parts[2], (parts[3] == "1")
        ok, msg = _mutate_niri_input("touchpad", lambda b: _mouse_set_flag_in_block(b, flag, en))
    else:
        return False

    _MSG = msg if ok else ""
    _ERR = "" if ok else msg
    if cc_panel and cc_panel.shell:
        cc_panel.shell.refresh_view()
    return True



def handle_cc_input_link(cc_panel, uri: str) -> bool:
    if handle_cc_keyboard_link(cc_panel, uri):
        return True
    if uri.startswith("cc_ctrl:"):
        from panels.cc_input_controllers import handle_cc_controllers_link
        return handle_cc_controllers_link(cc_panel, uri)
    return handle_cc_mouse_link(cc_panel, uri)


def handle_cc_input_prompt(cc_panel, action_id: str, text: str) -> bool:
    if action_id.startswith("cc_ctrl_"):
        from panels.cc_input_controllers import handle_cc_controllers_prompt
        return handle_cc_controllers_prompt(cc_panel, action_id, text)
    return handle_cc_keyboard_prompt(cc_panel, action_id, text)


def handle_cc_input_prompt_change(cc_panel, action_id: str, text: str) -> bool:
    return handle_cc_keyboard_prompt_change(cc_panel, action_id, text)


def handle_cc_input_key_press(cc_panel, event) -> bool:
    return handle_cc_keyboard_key_press(cc_panel, event)


def handle_cc_input_key_release(cc_panel, event) -> bool:
    return handle_cc_keyboard_key_release(cc_panel, event)
