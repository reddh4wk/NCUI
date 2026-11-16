#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""NC-UI Control Center — Mouse & Touchpad (`panels/cc_mouse.py`) [Single Backend / Dual View]"""

import os
import re
from core import probe
from core.config import get_cc_panel_style
from core.i18n import get_active_language
from ui.canvas import Canvas
from ui.kit import ActionBtn, Badge, Box, ProgressBar, SelectPill, StateBadge, Txt

NIRI_CONFIG_FILE = os.path.expanduser("~/.config/niri/config.kdl")
_MSG = ""
_ERR = ""


def _read_kdl() -> str:
    if not os.path.exists(NIRI_CONFIG_FILE):
        return ""
    try:
        return open(NIRI_CONFIG_FILE, "r", encoding="utf-8").read()
    except Exception:
        return ""


def _write_kdl(new_text: str) -> tuple[bool, str]:
    try:
        os.makedirs(os.path.dirname(NIRI_CONFIG_FILE), exist_ok=True)
        old_text = _read_kdl()
        with open(NIRI_CONFIG_FILE, "w", encoding="utf-8") as f:
            f.write(new_text)
        if probe.has_bin("niri"):
            ok, _out, err = probe.run_cmd_full(["niri", "validate", "-c", NIRI_CONFIG_FILE], timeout=2.5)
            if not ok:
                with open(NIRI_CONFIG_FILE, "w", encoding="utf-8") as f:
                    f.write(old_text)
                return False, f"niri validate failed: {err or 'syntax error'}"
        if _read_kdl() != new_text:
            return False, "Read-back mismatch on config.kdl"
        probe.spawn_bg(["niri", "msg", "action", "load-config-file"])
        return True, "Updated & validated in ~/.config/niri/config.kdl"
    except Exception as e:
        return False, str(e)


def _find_block_span(text: str, block_name: str, start: int = 0, end: int | None = None) -> tuple[int, int] | None:
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


def _has_flag(block: str, flag: str) -> bool:
    return bool(re.search(rf"(?m)^[ \t]*{re.escape(flag)}\b", block))


def _set_flag_in_block(block: str, flag: str, enabled: bool, indent: str = "        ") -> str:
    cleaned = re.sub(rf"(?m)^[ \t]*(?://\s*)?{re.escape(flag)}[ \t]*\n?", "", block)
    if not enabled:
        return cleaned
    idx = cleaned.rfind("}")
    if idx == -1:
        return cleaned
    prefix = "" if cleaned[:idx].endswith("\n") else "\n"
    return cleaned[:idx] + f"{prefix}{indent}{flag}\n" + cleaned[idx:]


def _set_kv_in_block(block: str, key: str, raw_val: str, indent: str = "        ") -> str:
    pat = re.compile(rf"(?m)^([ \t]*)(?://\s*)?{re.escape(key)}\s+[^\n]+")
    if pat.search(block):
        return pat.sub(rf"\g<1>{key} {raw_val}", block, count=1)
    idx = block.rfind("}")
    if idx == -1:
        return block
    prefix = "" if block[:idx].endswith("\n") else "\n"
    return block[:idx] + f"{prefix}{indent}{key} {raw_val}\n" + block[idx:]


def _collect_mouse_state() -> dict:
    txt = _read_kdl()
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
    inp = _find_block_span(txt, "input")
    if not inp:
        return st
    inp_blk = txt[inp[0]:inp[1]]
    st["focus_follows_mouse"] = _has_flag(inp_blk, "focus-follows-mouse")

    ms = _find_block_span(txt, "mouse", inp[0], inp[1])
    if ms:
        mb = txt[ms[0]:ms[1]]
        st["left_handed"] = _has_flag(mb, "left-handed")
        st["natural_scroll"] = _has_flag(mb, "natural-scroll")
        st["middle_emulation"] = _has_flag(mb, "middle-emulation")
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

    tp = _find_block_span(txt, "touchpad", inp[0], inp[1])
    if tp:
        tb = txt[tp[0]:tp[1]]
        st["tp_tap"] = _has_flag(tb, "tap")
        st["tp_dwt"] = _has_flag(tb, "dwt")
        st["tp_natural_scroll"] = _has_flag(tb, "natural-scroll")
    return st


def _mutate_niri_input(subblock: str | None, updater) -> tuple[bool, str]:
    txt = _read_kdl()
    inp = _find_block_span(txt, "input")
    if not inp:
        txt = txt.rstrip() + "\n\ninput {\n}\n"
        inp = _find_block_span(txt, "input")
    if not inp:
        return False, "Could not locate or create input { } block"

    if subblock is None:
        blk = txt[inp[0]:inp[1]]
        new_blk = updater(blk)
        return _write_kdl(txt[:inp[0]] + new_blk + txt[inp[1]:])

    sp = _find_block_span(txt, subblock, inp[0], inp[1])
    if not sp:
        idx = inp[1] - 1
        prefix = "" if txt[:idx].endswith("\n") else "\n"
        txt = txt[:idx] + f"{prefix}    {subblock} {{\n    }}\n" + txt[idx:]
        inp = _find_block_span(txt, "input")
        sp = _find_block_span(txt, subblock, inp[0], inp[1])
    if not sp:
        return False, f"Failed creating {subblock} {{ }} block"

    blk = txt[sp[0]:sp[1]]
    new_blk = updater(blk)
    return _write_kdl(txt[:sp[0]] + new_blk + txt[sp[1]:])


def _feedback_cards() -> list:
    if not _MSG and not _ERR:
        return []
    b = Box("MOUSE // ERROR" if _ERR else "MOUSE // KDL SAVED", icon="\uf071" if _ERR else "\uf058")
    b.add_row(f"  {Txt(_ERR if _ERR else _MSG, role='warn' if _ERR else 'vitality')}")
    return [b]


def _toggle_pair(uri_on: str, uri_off: str, cur: bool) -> str:
    return f"{SelectPill('ON', uri_on, selected=cur)} {SelectPill('OFF', uri_off, selected=not cur)}"


def _build_general_standard(cc_panel, canvas: Canvas, is_it: bool) -> list:
    st = _collect_mouse_state()
    cards = _feedback_cards()
    b = Box("Configurazione Mouse (Niri config.kdl)" if is_it else "General Mouse Config (Niri config.kdl)", icon="\uf245", elected=True)
    b.add_row(" ")
    b.add_kv("Left-Handed Mode", StateBadge(st["left_handed"], "ENABLED", "RIGHT-HAND"), right_markup=_toggle_pair("cc_mouse:flag:left-handed:1", "cc_mouse:flag:left-handed:0", st["left_handed"]), requires=["niri"])
    b.add_kv("Natural Scroll", StateBadge(st["natural_scroll"], "REVERSED", "CLASSIC"), right_markup=_toggle_pair("cc_mouse:flag:natural-scroll:1", "cc_mouse:flag:natural-scroll:0", st["natural_scroll"]), requires=["niri"])
    b.add_kv("Middle-Click Emulation", StateBadge(st["middle_emulation"], "ENABLED", "DISABLED"), right_markup=_toggle_pair("cc_mouse:flag:middle-emulation:1", "cc_mouse:flag:middle-emulation:0", st["middle_emulation"]), requires=["niri"])
    b.add_kv("Focus Follows Mouse", StateBadge(st["focus_follows_mouse"], "AUTO FOCUS", "CLICK FOCUS"), right_markup=_toggle_pair("cc_mouse:inflag:focus-follows-mouse:1", "cc_mouse:inflag:focus-follows-mouse:0", st["focus_follows_mouse"]), requires=["niri"])
    sf_pills = " ".join(SelectPill(f"{v:.1f}x", f"cc_mouse:sf:{v:.1f}", selected=(abs(st["scroll_factor"] - v) < 0.05)) for v in (0.5, 1.0, 1.5, 2.0))
    b.add_kv("Scroll Factor", Txt(f"{st['scroll_factor']:.2f}x", role="focus"), right_markup=sf_pills, requires=["niri"])
    b.add_row(" ")
    cards.append(b)
    return cards


def _build_general_cyberpunk(cc_panel, canvas: Canvas, is_it: bool) -> list:
    st = _collect_mouse_state()
    cards = _feedback_cards()
    b = Box("INPUT MATRIX // MOUSE TOPOLOGY & SCROLL VECTOR", icon="\uf245", elected=True)
    b.add_row(" ")
    b.add_row(f"  {Txt('[LEFT-HANDED] ──►', role='schema')} {StateBadge(st['left_handed'], 'SINISTRORSE', 'STANDARD')}", right_markup=_toggle_pair("cc_mouse:flag:left-handed:1", "cc_mouse:flag:left-handed:0", st["left_handed"]), requires=["niri"])
    b.add_row(f"  {Txt('[NAT-SCROLL]  ──►', role='schema')} {StateBadge(st['natural_scroll'], 'INVERTED', 'CLASSIC')}", right_markup=_toggle_pair("cc_mouse:flag:natural-scroll:1", "cc_mouse:flag:natural-scroll:0", st["natural_scroll"]), requires=["niri"])
    b.add_row(f"  {Txt('[MID-EMUL]    ──►', role='schema')} {StateBadge(st['middle_emulation'], 'L+R CHORD', 'OFF')}", right_markup=_toggle_pair("cc_mouse:flag:middle-emulation:1", "cc_mouse:flag:middle-emulation:0", st["middle_emulation"]), requires=["niri"])
    b.add_row(f"  {Txt('[HOVER FOCUS] ──►', role='intent')} {StateBadge(st['focus_follows_mouse'], 'SLOPPY', 'CLICK')}", right_markup=_toggle_pair("cc_mouse:inflag:focus-follows-mouse:1", "cc_mouse:inflag:focus-follows-mouse:0", st["focus_follows_mouse"]), requires=["niri"])
    sf_pills = " ".join(SelectPill(f"{v:.1f}x", f"cc_mouse:sf:{v:.1f}", selected=(abs(st["scroll_factor"] - v) < 0.05)) for v in (0.5, 1.0, 1.5, 2.0))
    sf_val = st["scroll_factor"]
    b.add_row(f"  {Txt('[SCROLL MULT] ──►', role='vitality')} {Txt(f'{sf_val:.2f}x', role='focus')}", right_markup=sf_pills, requires=["niri"])
    b.add_row(" ")
    cards.append(b)
    return cards


def _build_pointer_standard(cc_panel, canvas: Canvas, is_it: bool) -> list:
    st = _collect_mouse_state()
    cards = _feedback_cards()
    bar_pct = (st["accel_speed"] + 1.0) * 50.0
    as_str = f"{st['accel_speed']:+.2f}"
    b = Box("Puntatore, Accelerazione & Touchpad" if is_it else "Pointer Speed, Acceleration & Touchpad", icon="\uf192", elected=True)
    b.add_row(" ")
    b.add_kv(
        "Accel Speed (-1..+1)",
        f"{ProgressBar(bar_pct, width=18)} {Txt(as_str, role='vitality')}",
        right_markup=f"{ActionBtn('-0.1', 'cc_mouse:accel_step:-0.1', tone='select')} {ActionBtn('+0.1', 'cc_mouse:accel_step:+0.1', tone='select')}",
        requires=["niri"],
    )
    presets = " ".join(SelectPill(f"{v:+.1f}", f"cc_mouse:accel:{v:.2f}", selected=(abs(st["accel_speed"] - v) < 0.06)) for v in (-0.6, -0.3, 0.0, 0.3, 0.6))
    b.add_kv("Speed Presets", presets, requires=["niri"])
    prof_pills = f"{SelectPill('adaptive', 'cc_mouse:prof:adaptive', selected=(st['accel_profile'] == 'adaptive'))} {SelectPill('flat (RAW 1:1)', 'cc_mouse:prof:flat', selected=(st['accel_profile'] == 'flat'))}"
    b.add_kv("Accel Profile", Txt(st["accel_profile"], role="intent"), right_markup=prof_pills, requires=["niri"])

    def _fill_tp(bx: Box):
        bx.add_kv("Tap to Click", StateBadge(st["tp_tap"], "ON", "OFF"), right_markup=_toggle_pair("cc_mouse:tp:tap:1", "cc_mouse:tp:tap:0", st["tp_tap"]), requires=["niri"])
        bx.add_kv("Disable While Typing", StateBadge(st["tp_dwt"], "DWT ON", "OFF"), right_markup=_toggle_pair("cc_mouse:tp:dwt:1", "cc_mouse:tp:dwt:0", st["tp_dwt"]), requires=["niri"])
        bx.add_kv("Touchpad Natural Scroll", StateBadge(st["tp_natural_scroll"], "NATURAL", "CLASSIC"), right_markup=_toggle_pair("cc_mouse:tp:natural-scroll:1", "cc_mouse:tp:natural-scroll:0", st["tp_natural_scroll"]), requires=["niri"])

    b.add_collapsible(cc_panel, "mouse_touchpad", "Touchpad Settings (tap / dwt / natural-scroll)", _fill_tp)
    b.add_row(" ")
    cards.append(b)
    return cards


def _build_pointer_cyberpunk(cc_panel, canvas: Canvas, is_it: bool) -> list:
    st = _collect_mouse_state()
    cards = _feedback_cards()
    bar_pct = (st["accel_speed"] + 1.0) * 50.0
    as_str = f"{st['accel_speed']:+.2f}"
    b = Box("BALLISTICS DECK // LIBINPUT POINTER CURVE & TOUCHPAD", icon="\uf192", elected=True)
    b.add_row(" ")
    b.add_row(
        f"  {Txt('[ACCEL GAIN]──►', role='vitality')} {ProgressBar(bar_pct, width=18)} {Txt(as_str, role='focus')}",
        right_markup=f"{ActionBtn('-0.1', 'cc_mouse:accel_step:-0.1', tone='select')} {ActionBtn('+0.1', 'cc_mouse:accel_step:+0.1', tone='select')}",
        requires=["niri"],
    )
    b.add_row(f"  {Txt('[CURVE MODE]──►', role='intent')} {Txt(st['accel_profile'].upper(), role='focus')}", right_markup=f"{SelectPill('ADAPTIVE', 'cc_mouse:prof:adaptive', selected=(st['accel_profile'] == 'adaptive'))} {SelectPill('FLAT (1:1)', 'cc_mouse:prof:flat', selected=(st['accel_profile'] == 'flat'))}", requires=["niri"])

    def _fill_tp(bx: Box):
        bx.add_row(f"  {Txt('├─ tap:', role='structure')} {StateBadge(st['tp_tap'], 'ENABLED', 'OFF')}", right_markup=_toggle_pair("cc_mouse:tp:tap:1", "cc_mouse:tp:tap:0", st["tp_tap"]), requires=["niri"])
        bx.add_row(f"  {Txt('├─ dwt:', role='structure')} {StateBadge(st['tp_dwt'], 'GUARDED', 'OFF')}", right_markup=_toggle_pair("cc_mouse:tp:dwt:1", "cc_mouse:tp:dwt:0", st["tp_dwt"]), requires=["niri"])
        bx.add_row(f"  {Txt('└─ nat:', role='structure')} {StateBadge(st['tp_natural_scroll'], 'INVERT', 'STD')}", right_markup=_toggle_pair("cc_mouse:tp:natural-scroll:1", "cc_mouse:tp:natural-scroll:0", st["tp_natural_scroll"]), requires=["niri"])

    b.add_collapsible(cc_panel, "mouse_touchpad", "TOUCHPAD MATRIX (TAP / DWT / SCROLL)", _fill_tp)
    b.add_row(" ")
    cards.append(b)
    return cards


def build_cc_mouse_cards(cc_panel, canvas: Canvas, active_sub: str = "general") -> list:
    is_it = (get_active_language() == "it")
    cyber = get_cc_panel_style() == "cyberpunk"
    if active_sub == "pointer":
        return _build_pointer_cyberpunk(cc_panel, canvas, is_it) if cyber else _build_pointer_standard(cc_panel, canvas, is_it)
    return _build_general_cyberpunk(cc_panel, canvas, is_it) if cyber else _build_general_standard(cc_panel, canvas, is_it)


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
        ok, msg = _mutate_niri_input("mouse", lambda b: _set_flag_in_block(b, flag, en))
    elif cmd == "inflag" and len(parts) >= 4:
        flag, en = parts[2], (parts[3] == "1")
        ok, msg = _mutate_niri_input(None, lambda b: _set_flag_in_block(b, flag, en, indent="    "))
    elif cmd == "sf" and len(parts) >= 3:
        val = parts[2]
        ok, msg = _mutate_niri_input("mouse", lambda b: _set_kv_in_block(b, "scroll-factor", val))
    elif cmd == "prof" and len(parts) >= 3 and parts[2] in ("adaptive", "flat"):
        prof = parts[2]
        ok, msg = _mutate_niri_input("mouse", lambda b: _set_kv_in_block(b, "accel-profile", f'"{prof}"'))
    elif cmd == "accel" and len(parts) >= 3:
        val = max(-1.0, min(1.0, float(parts[2])))
        ok, msg = _mutate_niri_input("mouse", lambda b: _set_kv_in_block(b, "accel-speed", f"{val:.2f}"))
    elif cmd == "accel_step" and len(parts) >= 3:
        cur = _collect_mouse_state()["accel_speed"]
        val = max(-1.0, min(1.0, round(cur + float(parts[2]), 2)))
        ok, msg = _mutate_niri_input("mouse", lambda b: _set_kv_in_block(b, "accel-speed", f"{val:.2f}"))
    elif cmd == "tp" and len(parts) >= 4:
        flag, en = parts[2], (parts[3] == "1")
        ok, msg = _mutate_niri_input("touchpad", lambda b: _set_flag_in_block(b, flag, en))
    else:
        return False

    _MSG = msg if ok else ""
    _ERR = "" if ok else msg
    if cc_panel and cc_panel.shell:
        cc_panel.shell.refresh_view()
    return True
