#!/usr/bin/env python3
import json
import os
import re
import time
from core import probe
from core.config import get_cc_panel_style
from core.i18n import get_active_language
from core.state import STATE
from panels.display import NiriOutputMap, resolve_script_path, run_desktop_cli
from ui.canvas import Canvas
from ui.kit import ActionBtn, Badge, Box, Divider, SelectPill, Slider, StateBadge, Txt
from ui.widgets import CustomWidgetCard

NIRI_CONFIG_FILE = os.path.expanduser("~/.config/niri/config.kdl")


def _set_disp_fb(ok: bool, msg: str) -> None:
    STATE.set("display", "cc_fb", {"ok": bool(ok), "msg": str(msg), "ts": time.time()})


def _get_disp_fb() -> dict:
    fb = STATE.get("display", "cc_fb", {})
    if isinstance(fb, dict) and fb.get("msg") and (time.time() - float(fb.get("ts", 0))) < 15.0:
        return fb
    return {}


def _feedback_cards() -> list:
    fb = _get_disp_fb()
    if not fb:
        return []
    b = Box("Display Status", icon="\uf00c" if fb["ok"] else "\uf071")
    b.add_row(" ")
    b.add_row(f"  {Txt(' ' + fb['msg'] + ' ', role='vitality' if fb['ok'] else 'fault', bg=True)}")
    b.add_row(" ")
    return [b]


def get_niri_outputs_data() -> dict:
    ok, out = probe.run_cmd(["niri", "msg", "-j", "outputs"], timeout=1.5)
    if ok and out:
        try:
            return json.loads(out)
        except Exception:
            pass
    # Fallback parsing from config.kdl when niri IPC socket isn't present in headless env
    outs = {}
    if os.path.exists(NIRI_CONFIG_FILE):
        try:
            txt = open(NIRI_CONFIG_FILE, "r", encoding="utf-8").read()
            for m in re.finditer(r'output\s+"([^"]+)"\s*\{([^}]*)\}', txt, re.DOTALL):
                name, blk = m.group(1), m.group(2)
                is_off = bool(re.search(r"(?m)^\s*off\b", blk))
                m_mode = re.search(r'mode\s+"(\d+)x(\d+)(?:@([0-9.]+))?"', blk)
                m_pos = re.search(r"position\s+x=(-?\d+)\s+y=(-?\d+)", blk)
                m_sc = re.search(r"scale\s+([0-9.]+)", blk)
                w = int(m_mode.group(1)) if m_mode else 1920
                h = int(m_mode.group(2)) if m_mode else 1080
                hz = int(round(float(m_mode.group(3)) * 1000)) if (m_mode and m_mode.group(3)) else 60000
                outs[name] = {
                    "name": name,
                    "model": "Configured Output",
                    "vrr_enabled": bool(re.search(r"(?m)^\s*variable-refresh-rate\b", blk)),
                    "logical": None if is_off else {
                        "width": w,
                        "height": h,
                        "x": int(m_pos.group(1)) if m_pos else 0,
                        "y": int(m_pos.group(2)) if m_pos else 0,
                        "scale": float(m_sc.group(1)) if m_sc else 1.0,
                    },
                    "modes": [{"width": w, "height": h, "refresh_rate": hz}],
                    "current_mode": 0,
                }
        except Exception:
            pass
    return outs


def _get_night_light_state() -> dict:
    ok, _ = probe.run_cmd(["pgrep", "-x", "wlsunset"], timeout=0.8)
    temp = int(STATE.get("display", "night_temp", 4200) or 4200)
    return {"active": ok, "temp": temp}


def _toggle_niri_vrr(out_name: str, enable: bool) -> tuple[bool, str]:
    ok, _out, err = probe.run_cmd_full(["niri", "msg", "output", out_name, "vrr", "on" if enable else "off"], timeout=1.5)
    if os.path.exists(NIRI_CONFIG_FILE):
        try:
            txt = open(NIRI_CONFIG_FILE, "r", encoding="utf-8").read()
            pat = re.compile(rf'(output\s+"{re.escape(out_name)}"\s*\{{)([^}}]*)(\}})', re.DOTALL)
            m = pat.search(txt)
            if m:
                blk = re.sub(r"(?m)^[ \t]*variable-refresh-rate\b.*\n?", "", m.group(2))
                if enable:
                    blk = blk.rstrip() + "\n    variable-refresh-rate\n"
                txt = txt[:m.start(2)] + blk + txt[m.end(2):]
                open(NIRI_CONFIG_FILE, "w", encoding="utf-8").write(txt)
                ok = True
        except Exception as e:
            err = str(e)
    return ok, (f"VRR {'ON' if enable else 'OFF'} on {out_name}" if ok else (err or "VRR toggle failed"))


# =====================================================================
# SUBTAB 1: LAYOUT (Standard + Cyberpunk)
# =====================================================================

def _build_layout_standard(cc_panel, canvas: Canvas, outputs_data: dict, is_it: bool) -> list:
    cards = _feedback_cards()
    hdr = Box(
        "Topologia Spaziale Multi-Monitor (Niri)" if is_it else "Multi-Monitor Spatial Topology (Niri)",
        icon="\uf108",
        actions=[
            ActionBtn("Auto Pos All", "cc_desk:pos_auto_all", tone="neutral"),
            ActionBtn("TUI", "cc_desk:open_tui", tone="select"),
        ],
        highlighted=True,
    )
    hdr.add_row(" ")
    hdr.add_row(f"  {Txt('Trascina i monitor nella mappa o configura coordinate e stato output.' if is_it else 'Drag monitors below to snap positions or configure output geometry.', role='schema')}")
    hdr.add_row(" ")
    cards.append(hdr)

    if any(v.get("logical") for v in outputs_data.values()):
        niri_map = NiriOutputMap(
            outputs_data,
            refresh_cb=(lambda: cc_panel.shell.refresh_view() if cc_panel.shell else None),
            map_width=int(canvas.total_px - 28),
        )
        cards.append(CustomWidgetCard(niri_map))

    for out_name, info in sorted(outputs_data.items()):
        logical = info.get("logical")
        is_on = logical is not None
        modes = info.get("modes") or []
        cur_idx = info.get("current_mode")
        cur_str = "DISABLED" if not is_on else f"{logical.get('width', 0)}x{logical.get('height', 0)}"
        if is_on and cur_idx is not None and 0 <= cur_idx < len(modes):
            cm = modes[cur_idx]
            rr = cm.get("refresh_rate", 0)
            hz = rr / 1000.0 if rr > 1000 else float(rr)
            cur_str = f"{cm.get('width')}x{cm.get('height')}@{hz:.0f}Hz"

        o_box = Box(
            f"{out_name} — {info.get('model', 'Display')}",
            icon="\uf26c",
            actions=[
                StateBadge(is_on, "ENABLED", "OFF"),
                ActionBtn("Disable" if is_on else "Enable", f"cc_desk:power:{out_name}:{'off' if is_on else 'on'}", tone="danger_soft" if is_on else "apply"),
            ],
            elected=is_on,
        )
        o_box.add_row(" ")
        if is_on:
            o_box.add_kv("Active Mode" if not is_it else "Modalita", Txt(cur_str, role="focus"), right_markup=ActionBtn("Native Max", f"cc_desk:res_max:{out_name}", tone="apply"))
            o_box.add_kv("Logical Pos" if not is_it else "Posizione", Txt(f"({logical.get('x', 0)}, {logical.get('y', 0)})  Scale: {logical.get('scale', 1.0)}x", role="schema"),
                         right_markup=f"{ActionBtn('Set Pos..', f'cc_desk:pos_prompt:{out_name}', tone='neutral')} {ActionBtn('Auto', f'cc_desk:pos_auto:{out_name}', tone='select')}")
        else:
            o_box.add_kv("Status", Txt("Output powered off in Niri", role="inactive"))
        o_box.add_row(" ")
        cards.append(o_box)
    return cards


def _build_layout_cyberpunk(cc_panel, canvas: Canvas, outputs_data: dict, is_it: bool) -> list:
    cards = _feedback_cards()
    b = Box(
        "GPU CRTC TOPOLOGY // MULTI-HEAD FRAMEBUFFER BUS",
        icon="\uf108",
        actions=[ActionBtn("AUTO-ALIGN ALL", "cc_desk:pos_auto_all", tone="select")],
        elected=True,
    )
    b.add_row(" ")
    b.add_row(f"  {Txt('[NVIDIA RTX 3090 CRTC]', role='vitality')} ═╦═ {Txt('[DISPLAYPORT / HDMI BUS]', role='schema')}")
    for out_name, info in sorted(outputs_data.items()):
        logical = info.get("logical")
        is_on = logical is not None
        st_b = StateBadge(is_on, "ACTIVE", "OFF")
        geom = f"{logical['width']}x{logical['height']} @({logical['x']},{logical['y']})" if is_on else "POWERED DOWN"
        acts = f"{ActionBtn('MAX', f'cc_desk:res_max:{out_name}', tone='apply')} {ActionBtn('POS..', f'cc_desk:pos_prompt:{out_name}', tone='neutral')} " if is_on else ""
        acts += ActionBtn("OFF" if is_on else "ON", f"cc_desk:power:{out_name}:{'off' if is_on else 'on'}", tone="danger_soft" if is_on else "apply")
        b.add_row(f"     {Txt('╠═►', role='structure')} {Txt(f'{out_name:<8}', role='intent')} {st_b} {Txt(geom, role='focus' if is_on else 'inactive')}", right_markup=acts)
    b.add_row(" ")
    cards.append(b)
    return cards


# =====================================================================
# SUBTAB 2: SCALING, VRR, NIGHT LIGHT & DPMS (Standard + Cyberpunk)
# =====================================================================

def _build_scaling_standard(cc_panel, canvas: Canvas, outputs_data: dict, is_it: bool) -> list:
    cards = _feedback_cards()
    nl = _get_night_light_state()
    dpms_min = int(STATE.get("display", "dpms_timeout", 10) or 10)

    nl_box = Box(
        "Night Light, VRR & Risparmio Schermo (DPMS)" if is_it else "Night Light, VRR & Screen DPMS",
        icon="\uf186",
        actions=[StateBadge(nl["active"], f"NIGHT {nl['temp']}K", "DAYLIGHT", on_tone="warn")],
    )
    nl_box.add_row(" ")
    nl_box.add_kv(
        "Night Light (wlsunset)",
        f"{SelectPill('ON', 'cc_desk:nl:on', selected=nl['active'])} {SelectPill('OFF', 'cc_desk:nl:off', selected=not nl['active'])}  "
        + Slider(nl["temp"], "cc_desk:nl_temp", min_val=2500, max_val=6500, width=18, show_pct=False, step_buttons=True, step=500),
        requires=["wlsunset"],
    )
    nl_box.add_kv(
        "DPMS Screen Off",
        " ".join(SelectPill(f"{m}m" if m else "Never", f"cc_desk:dpms:{m}", selected=(dpms_min == m)) for m in (5, 10, 15, 30, 0)),
        right_markup=ActionBtn("Sleep Now", "cc_desk:dpms_now", tone="warn"),
    )
    nl_box.add_row(" ")
    cards.append(nl_box)

    for out_name, info in sorted(outputs_data.items()):
        logical = info.get("logical") or {}
        if not logical:
            continue
        cur_scale = float(logical.get("scale", 1.0))
        modes = info.get("modes") or []
        cur_idx = info.get("current_mode")
        vrr_on = bool(info.get("vrr_enabled", False))

        s_box = Box(
            f"{out_name} ({info.get('model', 'Monitor')})",
            icon="\uf108",
            actions=[Badge(f"Scale {cur_scale:g}x", style="info"), ActionBtn("Custom..", f"cc_desk:scale_prompt:{out_name}", tone="neutral")],
            elected=True,
        )
        s_box.add_row(" ")
        scale_btns = " ".join(SelectPill(f"{sv:g}x", f"cc_desk:scale:{out_name}:{sv:g}", selected=(abs(cur_scale - sv) < 0.03)) for sv in (1.0, 1.25, 1.5, 1.75, 2.0))
        s_box.add_kv("Scaling Factor" if not is_it else "Fattore Scala", scale_btns)
        s_box.add_kv("Variable Refresh (VRR)", f"{SelectPill('VRR ON', f'cc_desk:vrr:{out_name}:on', selected=vrr_on)} {SelectPill('VRR OFF', f'cc_desk:vrr:{out_name}:off', selected=not vrr_on)}")

        seen, mode_btns = set(), []
        for idx, m in enumerate(modes):
            w, h = m.get("width", 0), m.get("height", 0)
            rr = m.get("refresh_rate", 0)
            hz = rr / 1000.0 if rr > 1000 else float(rr)
            m_spec = f"{w}x{h}@{hz:.0f}"
            if m_spec in seen:
                continue
            seen.add(m_spec)
            mode_btns.append(SelectPill(f"{m_spec}Hz", f"cc_desk:res:{out_name}:{m_spec}", selected=(idx == cur_idx)))
            if len(mode_btns) >= 8:
                break

        def _build_modes(b: Box, btns=mode_btns):
            for i in range(0, len(btns), 3):
                b.add_row("  " + "  ".join(btns[i:i + 3]))

        s_box.add_collapsible(cc_panel, f"disp_modes_{out_name}", f"Modalita Hardware ({len(mode_btns)})" if is_it else f"Hardware Modes ({len(mode_btns)})", _build_modes)
        s_box.add_row(" ")
        cards.append(s_box)
    return cards


def _build_scaling_cyberpunk(cc_panel, canvas: Canvas, outputs_data: dict, is_it: bool) -> list:
    cards = _feedback_cards()
    nl = _get_night_light_state()
    dpms_min = int(STATE.get("display", "dpms_timeout", 10) or 10)

    b = Box(
        "CRTC TIMING SYNTHESIZER // SCALING, VRR & KELVIN FILTER",
        icon="\uf186",
        actions=[StateBadge(nl["active"], f"{nl['temp']}K", "6500K", on_tone="warn")],
        elected=True,
    )
    b.add_row(" ")
    b.add_row(
        f"  {Txt('[KELVIN FILTER]─►', role='attention')} {SelectPill('WLSUNSET', 'cc_desk:nl:' + ('off' if nl['active'] else 'on'), selected=nl['active'])} "
        f"{Slider(nl['temp'], 'cc_desk:nl_temp', min_val=2500, max_val=6500, width=14, show_pct=False, step_buttons=True, step=500)}  "
        f"{Txt('[DPMS]', role='schema')} {SelectPill('10m', 'cc_desk:dpms:10', selected=(dpms_min==10))} {ActionBtn('PWR OFF', 'cc_desk:dpms_now', tone='warn')}"
    )
    b.add_section("OUTPUT CLOCK & FRACTIONAL SCALING MATRIX")
    for out_name, info in sorted(outputs_data.items()):
        logical = info.get("logical")
        if not logical:
            continue
        cur_scale = float(logical.get("scale", 1.0))
        vrr_on = bool(info.get("vrr_enabled", False))
        sc_btns = " ".join(SelectPill(f"{sv:g}x", f"cc_desk:scale:{out_name}:{sv:g}", selected=(abs(cur_scale - sv) < 0.03)) for sv in (1.0, 1.25, 1.5, 2.0))
        b.add_row(f"  {Txt(f'[{out_name}]', role='intent')} {sc_btns}  {SelectPill('VRR', f'cc_desk:vrr:{out_name}:' + ('off' if vrr_on else 'on'), selected=vrr_on)}  {ActionBtn('MAX HZ', f'cc_desk:res_max:{out_name}', tone='apply')}")
    b.add_row(" ")
    cards.append(b)
    return cards


# =====================================================================
# PUBLIC ENTRY POINTS (Single Backend, Dual View)
# =====================================================================

def build_cc_display_cards(cc_panel, canvas: Canvas, active_sub: str = "layout") -> list:
    is_it = (get_active_language() == "it")
    style = get_cc_panel_style()
    outputs_data = get_niri_outputs_data()

    if active_sub == "scaling":
        return _build_scaling_cyberpunk(cc_panel, canvas, outputs_data, is_it) if style == "cyberpunk" else _build_scaling_standard(cc_panel, canvas, outputs_data, is_it)
    return _build_layout_cyberpunk(cc_panel, canvas, outputs_data, is_it) if style == "cyberpunk" else _build_layout_standard(cc_panel, canvas, outputs_data, is_it)


def handle_cc_display_link(cc_panel, uri: str) -> bool:
    if not uri.startswith("cc_desk:"):
        return False
    act = uri[len("cc_desk:"):]

    if act == "open_tui":
        probe.open_terminal(["bash", resolve_script_path("nc_util_desktop.sh", fallback_alias="nc-desktop.sh"), "-I"])
        return True
    if act == "pos_auto_all":
        run_desktop_cli(["-o", "all", "--pos", "auto"])
        _set_disp_fb(True, "Auto-aligned all outputs")
    elif act.startswith("pos_auto:"):
        out_n = act.split(":", 1)[1]
        run_desktop_cli(["-o", out_n, "--pos", "auto"])
        _set_disp_fb(True, f"Auto-positioned {out_n}")
    elif act.startswith("pos_prompt:"):
        out_n = act.split(":", 1)[1]
        cc_panel.request_prompt(f"cc_disp_pos|{out_n}", f"Position X,Y for {out_n} (e.g. 0,0):", "0,0", title=f"Position {out_n}", required=True)
        return True
    elif act.startswith("power:"):
        _, out_n, st = act.split(":", 2)
        ok, _o, err = probe.run_cmd_full(["niri", "msg", "output", out_n, st], timeout=2.0)
        _set_disp_fb(ok, f"Output {out_n} -> {st.upper()}" if ok else (err or "Failed"))
    elif act.startswith("res_max:"):
        out_n = act.split(":", 1)[1]
        run_desktop_cli(["-o", out_n, "-r", "max"])
        _set_disp_fb(True, f"Set native max mode on {out_n}")
    elif act.startswith("res:"):
        _, out_n, m_spec = act.split(":", 2)
        run_desktop_cli(["-o", out_n, "-r", m_spec])
        _set_disp_fb(True, f"Applied mode {m_spec} on {out_n}")
    elif act.startswith("scale:"):
        _, out_n, sc_val = act.split(":", 2)
        run_desktop_cli(["-o", out_n, "--scale", sc_val])
        _set_disp_fb(True, f"Set scale {sc_val}x on {out_n}")
    elif act.startswith("scale_prompt:"):
        out_n = act.split(":", 1)[1]
        cc_panel.request_prompt(f"cc_desk_scale|{out_n}", f"Scale factor for {out_n} (e.g. 1.25):", "1.0", title=f"Scale {out_n}", required=True)
        return True
    elif act.startswith("vrr:"):
        _, out_n, state = act.split(":", 2)
        ok, msg = _toggle_niri_vrr(out_n, state == "on")
        _set_disp_fb(ok, msg)
    elif act.startswith("nl:"):
        on = act.split(":", 1)[1] == "on"
        temp = int(STATE.get("display", "night_temp", 4200) or 4200)
        probe.run_cmd(["pkill", "-x", "wlsunset"], timeout=1.0)
        if on:
            probe.spawn_bg(["wlsunset", "-T", str(temp), "-t", str(max(2000, temp - 300))])
        _set_disp_fb(True, f"Night Light {'ON' if on else 'OFF'}")
    elif act.startswith("nl_temp:"):
        temp = max(2500, min(6500, int(round(float(act.split(":", 1)[1])))))
        STATE.set("display", "night_temp", temp)
        if _get_night_light_state()["active"]:
            probe.run_cmd(["pkill", "-x", "wlsunset"], timeout=1.0)
            probe.spawn_bg(["wlsunset", "-T", str(temp), "-t", str(max(2000, temp - 300))])
    elif act.startswith("dpms:"):
        mins = int(act.split(":", 1)[1])
        STATE.set("display", "dpms_timeout", mins)
        _set_disp_fb(True, f"DPMS timeout set to {mins} min")
    elif act == "dpms_now":
        probe.spawn_bg(["niri", "msg", "action", "power-off-monitors"])
        return True
    else:
        return False

    if cc_panel.shell:
        cc_panel.shell.refresh_view()
    return True


def handle_cc_display_prompt(cc_panel, action_id: str, text: str) -> bool:
    val = str(text or "").strip()
    if not val:
        return False
    if action_id.startswith("cc_desk_scale|"):
        out_n = action_id.split("|", 1)[1]
        run_desktop_cli(["-o", out_n, "--scale", val])
        _set_disp_fb(True, f"Scale {val}x on {out_n}")
        return True
    if action_id.startswith("cc_disp_pos|"):
        out_n = action_id.split("|", 1)[1]
        run_desktop_cli(["-o", out_n, "--pos", val])
        _set_disp_fb(True, f"Position {val} on {out_n}")
        return True
    return False
