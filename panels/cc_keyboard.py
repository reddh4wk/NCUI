#!/usr/bin/env python3
import json
import os
import re
import time
from core import probe
from core.config import CONFIG_DIR, THEME, get_cc_panel_style
from core.i18n import get_active_language, set_language
from core.state import STATE
from ui.canvas import Canvas
from ui.kit import ActionBtn, Badge, Box, Divider, SelectPill, Slider, StateBadge, Txt

NIRI_CONFIG_FILE = os.path.expanduser("~/.config/niri/config.kdl")
MACROS_FILE = os.path.join(CONFIG_DIR, "macros.json")


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


def _write_niri_kdl(new_text: str) -> tuple[bool, str]:
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
        return True, "Applied & verified in config.kdl"
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
                action = re.sub(r"\s+", " ", m_b.group(2).strip())
                cat = "system"
                if "nc-ctl" in action or "hotkey" in action or "quit" in action:
                    cat = "nc_sys"
                elif "spawn" in action:
                    cat = "launch"
                elif any(w in action for w in ("focus", "move", "maximize", "fullscreen", "center", "close", "column", "workspace")):
                    cat = "window"
                state["binds"].append({"combo": combo, "action": action, "cat": cat})
    return state


def _update_kbd_kdl(key: str, value) -> tuple[bool, str]:
    content = _read_niri_kdl()
    if not content:
        content = "input {\n    keyboard {\n        xkb {\n            layout \"it\"\n        }\n    }\n}\n"

    inp_span = _find_block_span(content, "input")
    if not inp_span:
        content = "input {\n    keyboard {\n        xkb {\n            layout \"it\"\n        }\n    }\n}\n\n" + content
        inp_span = _find_block_span(content, "input")

    kbd_span = _find_block_span(content, "keyboard", inp_span[0], inp_span[1])
    if not kbd_span:
        pos = inp_span[0] + content[inp_span[0]:inp_span[1]].find("{") + 1
        content = content[:pos] + "\n    keyboard {\n        xkb {\n            layout \"it\"\n        }\n    }" + content[pos:]
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

    new_content = content[:kbd_span[0]] + kbd_blk + content[kbd_span[1]:]
    return _write_niri_kdl(new_content)


def _update_niri_bind(combo: str, action: str = None, remove: bool = False) -> tuple[bool, str]:
    clean_c = str(combo or "").strip()
    if not clean_c or any(ch in clean_c for ch in ("{", "}", "\n")):
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
    return _write_niri_kdl(content[:binds_span[0]] + b_blk + content[binds_span[1]:])


def _load_macros() -> list[dict]:
    if not os.path.exists(MACROS_FILE):
        return [
            {"name": "Reload Waybar", "combo": "Mod+Shift+W", "cmd": "pkill -SIGUSR2 waybar"},
            {"name": "Quick Screenshot", "combo": "Print", "cmd": "nc-ctl popup capture"},
        ]
    try:
        with open(MACROS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_macros(macros: list[dict]) -> None:
    try:
        os.makedirs(os.path.dirname(MACROS_FILE), exist_ok=True)
        with open(MACROS_FILE, "w", encoding="utf-8") as f:
            json.dump(macros, f, indent=2)
    except Exception:
        pass


def _feedback_box(is_it: bool) -> list:
    fb = _get_kbd_feedback()
    if not fb:
        return []
    b = Box("Stato Configurazione Tastiera" if is_it else "Keyboard Configuration Status", icon="\uf00c" if fb["ok"] else "\uf071")
    b.add_row(" ")
    b.add_row(f"  {Txt(' ' + fb['msg'] + ' ', role='vitality' if fb['ok'] else 'fault', bg=True)}")
    b.add_row(" ")
    return [b]


# =====================================================================
# SUBTAB 1: KEYBOARD (Standard + Cyberpunk)
# =====================================================================

def _build_keyboard_standard(cc_panel, canvas: Canvas, st: dict, is_it: bool) -> list:
    cards = _feedback_box(is_it)
    macros = _load_macros()
    test_str = str(STATE.get("keyboard", "test_typing", "") or "")

    box = Box(
        "Ripetizione Tasti & Comportamento (Niri)" if is_it else "Keyboard Repeat & Behavior (Niri)",
        icon="\uf11c",
        actions=[ActionBtn("Test Area.." if not is_it else "Area Test..", "cc_kbd:prompt_test", tone="select", active=True)],
        elected=True,
    )
    box.add_row(" ")
    rate_sl = Slider(st["repeat_rate"], "cc_kbd:rate", min_val=10, max_val=100, width=24, show_pct=False, step_buttons=True, step=5)
    del_sl = Slider(st["repeat_delay"], "cc_kbd:delay", min_val=150, max_val=1000, width=24, show_pct=False, step_buttons=True, step=50)
    box.add_kv("Repeat Rate" if not is_it else "Velocita Ripetizione", f"{rate_sl}  {Txt(str(st['repeat_rate']) + ' char/s', role='focus')}")
    box.add_kv("Repeat Delay" if not is_it else "Ritardo Ripetizione", f"{del_sl}  {Txt(str(st['repeat_delay']) + ' ms', role='focus')}")

    nl_btns = f"{SelectPill('ON', 'cc_kbd:numlock:on', selected=st['numlock'])}  {SelectPill('OFF', 'cc_kbd:numlock:off', selected=not st['numlock'])}"
    box.add_kv("NumLock on Boot" if not is_it else "NumLock all'Avvio", nl_btns)

    trk_btns = f"{SelectPill('Global', 'cc_kbd:track:global', selected=(st['track_layout'] == 'global'))}  {SelectPill('Per-Window', 'cc_kbd:track:window', selected=(st['track_layout'] == 'window'))}"
    box.add_kv("Track Layout", trk_btns)

    if test_str:
        box.add_kv("Typing Test" if not is_it else "Test Scrittura", Txt(canvas.truncate_plain(test_str, 46), role="vitality"))

    def _build_adv(b: Box):
        b.add_row(f"  {Txt('Preset Gaming (Fast 50/200ms) vs Standard (25/600ms):' if is_it else 'Presets: Gaming Fast (50/200ms) vs Standard (25/600ms):', role='schema')}",
                  right_markup=f"{ActionBtn('Gaming Fast', 'cc_kbd:preset:fast', tone='apply')} {ActionBtn('Default', 'cc_kbd:preset:std', tone='neutral')}")
        b.add_row(f"  {Txt('Config Path:', role='schema')} {Txt(NIRI_CONFIG_FILE, role='val')}",
                  right_markup=ActionBtn("Validate KDL", "cc_kbd:validate", tone="select"), requires=["niri"])

    box.add_collapsible(cc_panel, "kbd_adv", "Impostazioni Avanzate & Preset" if is_it else "Advanced Parameters & Presets", _build_adv)
    box.add_row(" ")
    cards.append(box)

    cards.append(Divider("Macro Manager & Key Capture Inspector", icon="\uf120"))
    m_box = Box(
        f"Macro Manager ({len(macros)})",
        icon="\uf0e7",
        actions=[ActionBtn("+ Add Macro..", "cc_kbd:prompt_macro", tone="apply", active=True)],
    )
    m_box.add_row(" ")
    if not macros:
        m_box.add_row(f"  {Txt('No custom macros defined.' if not is_it else 'Nessuna macro definita.', role='inactive')}")
    else:
        rows = []
        for idx, m in enumerate(macros[:8]):
            rows.append([
                Txt(m.get("name", "Macro")[:18], role="focus"),
                Badge(m.get("combo", ""), style="info"),
                Txt(canvas.truncate_plain(m.get("cmd", ""), 26), role="schema"),
                f"{ActionBtn('Run', f'cc_kbd:run_macro:{idx}', tone='apply')} {ActionBtn('Bind', f'cc_kbd:bind_macro:{idx}', tone='select')} {ActionBtn('x', f'cc_kbd:del_macro:{idx}', tone='danger_soft')}",
            ])
        m_box.add_table(
            headers=["MACRO", "TRIGGER", "COMMAND", "ACTIONS" if not is_it else "AZIONI"],
            widths=[18, 15, 26, "flex"],
            rows=rows,
            aligns=["left", "left", "left", "right"],
        )
    m_box.add_row(" ")
    cards.append(m_box)

    cap_box = Box(
        "Key Capture & Event Inspector (wev / libinput)",
        icon="\uf002",
        actions=[
            ActionBtn("Launch wev", "cc_kbd:launch_wev", tone="select", active=True),
            ActionBtn("libinput debug", "cc_kbd:launch_libinput", tone="neutral"),
        ],
    )
    cap_box.add_row(" ")
    cap_box.add_kv("Wayland Event Probe", ActionBtn("Open wev in Terminal", "cc_kbd:launch_wev", tone="apply"), requires=["wev"])
    cap_box.add_kv("Kernel Evdev Probe", ActionBtn("Open libinput debug-events", "cc_kbd:launch_libinput", tone="neutral"), requires=["libinput"])
    cap_box.add_kv("XKB Inspector", ActionBtn("xkbcli interactive-wayland", "cc_kbd:launch_xkbcli", tone="select"), requires=["xkbcli"])

    def _build_keysym_ref(b: Box):
        b.add_table(
            headers=["SPECIAL KEY", "XKB KEYSYM", "SUGGESTED NIRI ACTION"],
            widths=[18, 24, "flex"],
            rows=[
                [Txt("Vol Up/Down", role="focus"), Txt("XF86AudioRaise/LowerVolume", role="intent"), Txt("spawn wpctl/pactl", role="schema")],
                [Txt("Mic Mute", role="focus"), Txt("XF86AudioMicMute", role="intent"), Txt("pactl set-source-mute", role="schema")],
                [Txt("Screenshot", role="focus"), Txt("Print", role="intent"), Txt("nc-ctl popup capture", role="schema")],
                [Txt("Calc / Fn", role="focus"), Txt("XF86Calculator / Menu", role="intent"), Txt("spawn custom app", role="schema")],
            ],
        )

    cap_box.add_collapsible(cc_panel, "kbd_keysyms", "Tabella Keysym Multimediali Niri" if is_it else "Special/Media Keysym Reference", _build_keysym_ref)
    cap_box.add_row(" ")
    cards.append(cap_box)
    return cards


def _build_keyboard_cyberpunk(cc_panel, canvas: Canvas, st: dict, is_it: bool) -> list:
    cards = _feedback_box(is_it)
    macros = _load_macros()
    rate = st["repeat_rate"]
    delay = st["repeat_delay"]
    nl_on = st["numlock"]

    mcu = Box(
        "CYBERDECK HID // KBD-MCU & POLLING SYNTHESIZER",
        icon="\uf11c",
        actions=[ActionBtn("Typing Test..", "cc_kbd:prompt_test", tone="select", active=True)],
        elected=True,
    )
    mcu.add_row(" ")
    rate_sl = Slider(rate, "cc_kbd:rate", min_val=10, max_val=100, width=18, show_pct=False, step_buttons=True, step=5)
    del_sl = Slider(delay, "cc_kbd:delay", min_val=150, max_val=1000, width=18, show_pct=False, step_buttons=True, step=50)
    nl_p = SelectPill("NUMLOCK:ON" if nl_on else "NUMLOCK:OFF", f"cc_kbd:numlock:{'off' if nl_on else 'on'}", selected=nl_on)
    tr_g = SelectPill("TRK:GLOBAL", "cc_kbd:track:global", selected=(st["track_layout"] == "global"))
    tr_w = SelectPill("TRK:WINDOW", "cc_kbd:track:window", selected=(st["track_layout"] == "window"))
    pr_f = ActionBtn("OVERCLOCK 50/200", "cc_kbd:preset:fast", tone="apply")
    mcu.add_row(f"  {Txt('╭─[ NIRI-HID MCU ]──► RATE PLL:', role='structure')} {rate_sl} {Txt(f'{rate:3d} Hz', role='vitality')}")
    mcu.add_row(f"  {Txt('│', role='structure')}                   {Txt('DEBOUNCE:', role='intent')} {del_sl} {Txt(f'{delay:4d}ms', role='attention')}")
    mcu.add_row(f"  {Txt('╰─[ BUS MODES ]─────►', role='structure')} {nl_p}  {tr_g} {tr_w}  {pr_f}")
    mcu.add_row(f"       {Txt('│ [EEPROM: MACROS]', role='schema')}          {Txt('▼ [RAW EVENT PROBE]', role='schema')}")
    mcu.add_row(" ")
    cards.append(mcu)

    probe_box = Box(
        "MACRO EEPROM & HID EVENT SNIFFER",
        icon="\uf0e7",
        actions=[ActionBtn("+ INJECT MACRO", "cc_kbd:prompt_macro", tone="apply")],
    )
    probe_box.add_row(" ")
    for idx, m in enumerate(macros[:5]):
        probe_box.add_row(
            f"  {Txt('┣━', role='structure')} {Badge(m.get('combo', ''), style='info')} {Txt('──►', role='intent')} {Txt(m.get('name', '')[:18], role='focus')}",
            right_markup=f"{ActionBtn('EXEC', f'cc_kbd:run_macro:{idx}', tone='apply')} {ActionBtn('BIND', f'cc_kbd:bind_macro:{idx}', tone='select')} {ActionBtn('DEL', f'cc_kbd:del_macro:{idx}', tone='danger_soft')}",
        )
    probe_box.add_section("LIVE WAYLAND / EVDEV SIGNAL CAPTURE")
    probe_box.add_row(
        f"  {Txt('[RX]──►', role='vitality')} {ActionBtn('wev probe', 'cc_kbd:launch_wev', tone='select')}  "
        f"{ActionBtn('libinput sniffer', 'cc_kbd:launch_libinput', tone='neutral')}  "
        f"{ActionBtn('xkbcli', 'cc_kbd:launch_xkbcli', tone='neutral')}"
    )
    probe_box.add_row(" ")
    cards.append(probe_box)
    return cards


# =====================================================================
# SUBTAB 2: LAYOUT & LANGUAGE (Standard + Cyberpunk)
# =====================================================================

def _build_layout_standard(cc_panel, canvas: Canvas, st: dict, is_it: bool) -> list:
    cards = _feedback_box(is_it)
    cur_layouts = [x.strip() for x in st["layout"].split(",") if x.strip()] or ["it"]
    cur_var = st["variant"]
    cur_opts = [x.strip() for x in st["options"].split(",") if x.strip()]
    cur_lang = get_active_language()

    box = Box(
        "Layout Tastiera XKB & Lingua NC-UI" if is_it else "XKB Keyboard Layout & NC-UI Language",
        icon="\uf0ac",
        actions=[Badge(f"ACTIVE: {','.join(cur_layouts).upper()}", style="accent")],
        elected=True,
    )
    box.add_row(" ")
    common_layouts = [("it", "Italiano (it)"), ("us", "English US (us)"), ("gb", "English UK (gb)"), ("de", "Deutsch (de)"), ("fr", "Francais (fr)"), ("es", "Espanol (es)"), ("jp", "Japanese (jp)")]
    lay_btns = " ".join(SelectPill(lbl, f"cc_kbd:toggle_layout:{code}", selected=(code in cur_layouts)) for code, lbl in common_layouts[:4])
    lay_btns2 = " ".join(SelectPill(lbl, f"cc_kbd:toggle_layout:{code}", selected=(code in cur_layouts)) for code, lbl in common_layouts[4:])
    box.add_kv("Layout Attivi" if is_it else "Active Layouts", lay_btns)
    box.add_kv("Altri Layout" if is_it else "More Layouts", f"{lay_btns2}  {ActionBtn('Custom..', 'cc_kbd:prompt_layout', tone='neutral')}")

    vars_list = [("", "Default"), ("nodeadkeys", "NoDeadKeys"), ("intl", "Intl"), ("dvorak", "Dvorak"), ("colemak", "Colemak")]
    var_btns = " ".join(SelectPill(lbl, f"cc_kbd:variant:{v}", selected=(cur_var == v)) for v, lbl in vars_list)
    box.add_kv("Variante" if is_it else "Variant", var_btns)

    lang_btns = f"{SelectPill('English (en)', 'cc_kbd:ui_lang:en', selected=(cur_lang == 'en'))}  {SelectPill('Italiano (it)', 'cc_kbd:ui_lang:it', selected=(cur_lang == 'it'))}"
    box.add_kv("Lingua NC-UI" if is_it else "NC-UI Language", lang_btns)

    def _build_xkb_opts(b: Box):
        opts = [
            ("grp:alt_shift_toggle", "Alt+Shift Switch"),
            ("grp:win_space_toggle", "Super+Space Switch"),
            ("caps:escape", "CapsLock -> Esc"),
            ("caps:swapescape", "Swap Caps/Esc"),
            ("compose:ralt", "Compose RightAlt"),
        ]
        for ok_code, ok_lbl in opts:
            is_on = ok_code in cur_opts
            b.add_kv(ok_lbl, SelectPill("ACTIVE" if is_on else "OFF", f"cc_kbd:toggle_opt:{ok_code}", selected=is_on))
        b.add_kv("Custom XKB Dir", Txt("~/.config/xkb/symbols/", role="schema"), right_markup=ActionBtn("Inspect localectl", "cc_kbd:localectl", tone="neutral"), requires=["localectl"])

    box.add_collapsible(cc_panel, "kbd_xkb_opts", "Opzioni XKB & Modificatori (Caps/Switch)" if is_it else "XKB Switch & Modifier Options", _build_xkb_opts)
    box.add_row(" ")
    cards.append(box)
    return cards


def _build_layout_cyberpunk(cc_panel, canvas: Canvas, st: dict, is_it: bool) -> list:
    cards = _feedback_box(is_it)
    cur_layouts = [x.strip() for x in st["layout"].split(",") if x.strip()] or ["it"]
    cur_var = st["variant"]
    cur_opts = [x.strip() for x in st["options"].split(",") if x.strip()]
    cur_lang = get_active_language()

    b = Box("XKB SCANCODE MULTIPLEXER // KEYMAP & LOCALE BUS", icon="\uf0ac", elected=True)
    b.add_row(" ")
    r1 = " ".join(SelectPill(c.upper(), f"cc_kbd:toggle_layout:{c}", selected=(c in cur_layouts)) for c in ("it", "us", "gb", "de"))
    r2 = " ".join(SelectPill(c.upper(), f"cc_kbd:toggle_layout:{c}", selected=(c in cur_layouts)) for c in ("fr", "es", "jp"))
    v1 = " ".join(SelectPill(v or "STD", f"cc_kbd:variant:{v}", selected=(cur_var == v)) for v in ("", "nodeadkeys", "intl"))
    b.add_row(f"  {Txt('╭─[ KEYMAP BANK ]──►', role='structure')} {r1}")
    b.add_row(f"  {Txt('├─[ EXTENDED    ]──►', role='structure')} {r2}  {ActionBtn('CUSTOM', 'cc_kbd:prompt_layout', tone='neutral')}")
    b.add_row(f"  {Txt('╰─[ VARIANT MUX ]──►', role='structure')} {v1}  {SelectPill('CAPS->ESC', 'cc_kbd:toggle_opt:caps:escape', selected=('caps:escape' in cur_opts))}")
    b.add_row(f"       {Txt('▼ [ACTIVE KEYMAP: ' + ','.join(cur_layouts).upper() + ']', role='vitality')}")
    b.add_section("NC-UI INTERFACE LOCALE PIPELINE")
    b.add_row(
        f"  {Txt('[LOCALE.TOML] ──►', role='schema')}  "
        f"{SelectPill('EN', 'cc_kbd:ui_lang:en', selected=(cur_lang == 'en'))}  "
        f"{SelectPill('IT', 'cc_kbd:ui_lang:it', selected=(cur_lang == 'it'))}  "
        f"{SelectPill('ALT+SHIFT', 'cc_kbd:toggle_opt:grp:alt_shift_toggle', selected=('grp:alt_shift_toggle' in cur_opts))}"
    )
    b.add_row(" ")
    cards.append(b)
    return cards


# =====================================================================
# SUBTAB 3: SHORTCUTS (Standard + Cyberpunk)
# =====================================================================

def _build_shortcuts_standard(cc_panel, canvas: Canvas, st: dict, is_it: bool) -> list:
    cards = _feedback_box(is_it)
    binds = st["binds"]

    hdr = Box(
        f"Scorciatoie Compositor Niri ({len(binds)})" if is_it else f"Niri Compositor Keybindings ({len(binds)})",
        icon="\uf084",
        actions=[ActionBtn("+ New Bind..", "cc_kbd:prompt_bind", tone="apply", active=True)],
        elected=True,
    )
    hdr.add_row(" ")
    hdr.add_kv("Niri Config", Txt(NIRI_CONFIG_FILE, role="val"), right_markup=ActionBtn("Reload Niri", "cc_kbd:reload_niri", tone="select"), requires=["niri"])
    hdr.add_kv(
        "NC-UI Quick Inject",
        f"{ActionBtn('+ Mod+I (CC)', 'cc_kbd:inject_nc:cc', tone='apply')}  "
        f"{ActionBtn('+ Mod+N (Net)', 'cc_kbd:inject_nc:net', tone='neutral')}  "
        f"{ActionBtn('+ Mod+V (Clip)', 'cc_kbd:inject_nc:clip', tone='neutral')}",
    )
    hdr.add_row(" ")
    cards.append(hdr)

    b_box = Box("Active Keybindings in config.kdl", icon="\uf0c9")
    b_box.add_row(" ")
    if not binds:
        b_box.add_row(f"  {Txt('No bindings found in config.kdl.', role='inactive')}")
    else:
        rows = []
        for b in binds[:18]:
            rows.append([
                Badge(b["combo"], style="info" if b["cat"] == "nc_sys" else "secondary"),
                Txt(canvas.truncate_plain(b["action"], 44), role="focus"),
                ActionBtn("x", f"cc_kbd:del_bind:{b['combo']}", tone="danger_soft"),
            ])
        b_box.add_table(
            headers=["SHORTCUT", "NIRI ACTION / SPAWN", "DEL"],
            widths=[22, "flex", 8],
            rows=rows,
            aligns=["left", "left", "right"],
        )

    def _build_nc_nav(b: Box):
        b.add_table(
            headers=["KEYS", "NC-UI NAVIGATION ACTION"],
            widths=[22, "flex"],
            rows=[
                [Txt("Arrows / Tab", role="intent"), Txt("2D Spatial focus across buttons & sliders", role="val")],
                [Txt("Shift + Left/Right", role="intent"), Txt("Cycle Subtabs / Waybar Bar Surfing", role="val")],
                [Txt("Shift + Up/Down", role="intent"), Txt("Switch Control Center section / Expand popup", role="val")],
                [Txt("Enter / Space", role="intent"), Txt("Trigger focused control or prompt", role="val")],
            ],
        )

    b_box.add_collapsible(cc_panel, "kbd_nc_nav", "Scorciatoie Interne Navigazione NC-UI" if is_it else "NC-UI Internal 2D Navigation Keys", _build_nc_nav)
    b_box.add_row(" ")
    cards.append(b_box)
    return cards


def _build_shortcuts_cyberpunk(cc_panel, canvas: Canvas, st: dict, is_it: bool) -> list:
    cards = _feedback_box(is_it)
    binds = st["binds"]

    b = Box(
        "HOTKEY ROUTING PATCHBAY // NIRI & NC-CTL MATRIX",
        icon="\uf084",
        actions=[ActionBtn("+ PATCH BIND", "cc_kbd:prompt_bind", tone="apply", active=True)],
        elected=True,
    )
    b.add_row(" ")
    b.add_row(f"  {Txt('[QUICK NC-CTL PATCH]', role='schema')}  {ActionBtn('MOD+I -> CC', 'cc_kbd:inject_nc:cc', tone='apply')}  {ActionBtn('MOD+N -> NET', 'cc_kbd:inject_nc:net', tone='select')}  {ActionBtn('MOD+V -> CLIP', 'cc_kbd:inject_nc:clip', tone='neutral')}")
    b.add_section(f"ACTIVE SIGNAL ROUTES ({len(binds)})")
    for item in binds[:14]:
        c = item["combo"]
        a = canvas.truncate_plain(item["action"], 32)
        b.add_row(
            f"  {Badge(c, style='info')} {Txt('══►', role='structure')} {Txt(a, role='focus')}",
            right_markup=ActionBtn("UNPATCH", f"cc_kbd:del_bind:{c}", tone="danger_soft"),
        )
    b.add_row(" ")
    cards.append(b)
    return cards


# =====================================================================
# PUBLIC ENTRY POINTS (Single Backend, Dual View)
# =====================================================================

def build_cc_keyboard_cards(cc_panel, canvas: Canvas, active_sub: str = "keyboard") -> list:
    is_it = (get_active_language() == "it")
    style = get_cc_panel_style()
    st = _collect_keyboard_state()

    if active_sub == "layout":
        return _build_layout_cyberpunk(cc_panel, canvas, st, is_it) if style == "cyberpunk" else _build_layout_standard(cc_panel, canvas, st, is_it)
    if active_sub == "shortcuts":
        return _build_shortcuts_cyberpunk(cc_panel, canvas, st, is_it) if style == "cyberpunk" else _build_shortcuts_standard(cc_panel, canvas, st, is_it)
    return _build_keyboard_cyberpunk(cc_panel, canvas, st, is_it) if style == "cyberpunk" else _build_keyboard_standard(cc_panel, canvas, st, is_it)


def handle_cc_keyboard_link(cc_panel, uri: str) -> bool:
    if not uri.startswith("cc_kbd:"):
        return False
    act = uri[len("cc_kbd:"):]
    st = _collect_keyboard_state()

    if act.startswith("rate:"):
        ok, msg = _update_kbd_kdl("repeat-rate", max(10, min(100, int(round(float(act.split(":", 1)[1]))))))
        _set_kbd_feedback(ok, msg)
    elif act.startswith("delay:"):
        ok, msg = _update_kbd_kdl("repeat-delay", max(150, min(1000, int(round(float(act.split(":", 1)[1]))))))
        _set_kbd_feedback(ok, msg)
    elif act.startswith("numlock:"):
        ok, msg = _update_kbd_kdl("numlock", act.split(":", 1)[1] == "on")
        _set_kbd_feedback(ok, msg)
    elif act.startswith("track:"):
        ok, msg = _update_kbd_kdl("track-layout", act.split(":", 1)[1])
        _set_kbd_feedback(ok, msg)
    elif act.startswith("preset:"):
        if act.endswith(":fast"):
            ok, msg = _update_kbd_kdl("repeat-rate", 50)
            if ok:
                ok, msg = _update_kbd_kdl("repeat-delay", 200)
        else:
            ok, msg = _update_kbd_kdl("repeat-rate", 25)
            if ok:
                ok, msg = _update_kbd_kdl("repeat-delay", 600)
        _set_kbd_feedback(ok, msg)
    elif act == "validate":
        ok, out, err = probe.run_cmd_full(["niri", "validate", "-c", NIRI_CONFIG_FILE], timeout=2.5)
        _set_kbd_feedback(ok, out or err or ("KDL syntax valid" if ok else "Validation failed"))
    elif act == "reload_niri":
        ok, out, err = probe.run_cmd_full(["niri", "msg", "action", "load-config-file"], timeout=2.5)
        _set_kbd_feedback(ok, "Niri config reloaded" if ok else (err or out or "Reload failed"))
    elif act == "prompt_test":
        cc_panel.request_prompt("cc_kbd_test", "Type here to test key repeat rate & layout...", "", title="Keyboard Typing Test")
        return True
    elif act == "prompt_macro":
        cc_panel.request_prompt("cc_kbd_macro", "Name | Combo | Shell Command (e.g. Term | Mod+T | ghostty):", "", title="+ New Macro", required=True)
        return True
    elif act.startswith("run_macro:"):
        idx = int(act.split(":", 1)[1])
        macros = _load_macros()
        if 0 <= idx < len(macros):
            probe.spawn_bg(["bash", "-c", macros[idx].get("cmd", "")])
            _set_kbd_feedback(True, f"Executed macro: {macros[idx].get('name')}")
    elif act.startswith("bind_macro:"):
        idx = int(act.split(":", 1)[1])
        macros = _load_macros()
        if 0 <= idx < len(macros):
            m = macros[idx]
            ok, msg = _update_niri_bind(m["combo"], f'spawn "bash" "-c" "{m["cmd"]}"')
            _set_kbd_feedback(ok, msg)
    elif act.startswith("del_macro:"):
        idx = int(act.split(":", 1)[1])
        macros = _load_macros()
        if 0 <= idx < len(macros):
            macros.pop(idx)
            _save_macros(macros)
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
    elif act.startswith("toggle_layout:"):
        code = act.split(":", 1)[1]
        cur = [x.strip() for x in st["layout"].split(",") if x.strip()] or ["it"]
        if code in cur:
            if len(cur) > 1:
                cur.remove(code)
        else:
            cur.append(code)
        ok, msg = _update_kbd_kdl("layout", ",".join(cur))
        _set_kbd_feedback(ok, msg)
    elif act == "prompt_layout":
        cc_panel.request_prompt("cc_kbd_layout", "Comma-separated XKB layouts (e.g. it,us):", st["layout"], title="XKB Layouts", required=True)
        return True
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
        _set_kbd_feedback(True, f"Language switched to {get_active_language().upper()}")
    elif act == "prompt_bind":
        cc_panel.request_prompt("cc_kbd_bind", "Combo | Niri Action (e.g. Mod+G | spawn \"nc-ctl popup network\"):", "", title="+ New Niri Keybind", required=True)
        return True
    elif act.startswith("del_bind:"):
        ok, msg = _update_niri_bind(act.split(":", 1)[1], remove=True)
        _set_kbd_feedback(ok, msg)
    elif act.startswith("inject_nc:"):
        which = act.split(":", 1)[1]
        presets = {
            "cc": ("Mod+I", 'spawn "/usr/local/lib/nc-ui/bin/nc-ctl" "settings" "system"'),
            "net": ("Mod+N", 'spawn "/usr/local/lib/nc-ui/bin/nc-ctl" "popup" "network"'),
            "clip": ("Mod+V", 'spawn "/usr/local/lib/nc-ui/bin/nc-ctl" "popup" "clipboard"'),
        }
        combo, cmd = presets.get(which, presets["cc"])
        ok, msg = _update_niri_bind(combo, cmd)
        _set_kbd_feedback(ok, msg)

    if cc_panel.shell:
        cc_panel.shell.refresh_view()
    return True


def handle_cc_keyboard_prompt(cc_panel, action_id: str, text: str) -> bool:
    if not action_id.startswith("cc_kbd_"):
        return False
    val = str(text or "").strip()
    if action_id == "cc_kbd_test":
        STATE.set("keyboard", "test_typing", val)
        return True
    if action_id == "cc_kbd_layout" and val:
        ok, msg = _update_kbd_kdl("layout", val)
        _set_kbd_feedback(ok, msg)
        return True
    if action_id == "cc_kbd_macro" and "|" in val:
        parts = [p.strip() for p in val.split("|", 2)]
        if len(parts) == 3 and parts[1] and parts[2]:
            macros = _load_macros()
            macros.append({"name": parts[0], "combo": parts[1], "cmd": parts[2]})
            _save_macros(macros)
            _set_kbd_feedback(True, f"Added macro '{parts[0]}'")
        return True
    if action_id == "cc_kbd_bind" and "|" in val:
        combo, action = [p.strip() for p in val.split("|", 1)]
        ok, msg = _update_niri_bind(combo, action)
        _set_kbd_feedback(ok, msg)
        return True
    return False
