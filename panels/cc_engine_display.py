#!/usr/bin/env python3
import json
import os
import re
import time
from core import probe
from core.i18n import get_active_language, t
from core.state import STATE
from panels.wp_display import NiriOutputMap, resolve_script_path, run_desktop_cli
from ui.canvas import Canvas
from ui.kit import ActionBtn, Badge, Box, Divider, FeedbackBox, SelectPill, Slider, StateBadge, Txt, start_inline_input
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
    return [FeedbackBox(t("cc_display.display_status"), bool(fb["ok"]), fb["msg"])]


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


def handle_cc_display_link(cc_panel, uri: str) -> bool:
    if not uri.startswith("cc_desk:"):
        return False
    act = uri[len("cc_desk:"):]

    if act == "open_tui":
        probe.open_terminal(["bash", resolve_script_path("nc_util_desktop.sh", fallback_alias="nc-desktop.sh"), "-I"])
        return True
    if act == "pos_auto_all":
        run_desktop_cli(["-o", "all", "--pos", "auto"])
        _set_disp_fb(True, t("cc_display.auto_aligned_all_outputs"))
    elif act.startswith("pos_auto:"):
        out_n = act.split(":", 1)[1]
        run_desktop_cli(["-o", out_n, "--pos", "auto"])
        _set_disp_fb(True, t("cc_display.auto_positioned", p0=out_n))
    elif act.startswith("pos_inline:"):
        out_n = act.split(":", 1)[1]
        start_inline_input(
            entry_id=f"cc_disp_pos_{out_n}",
            shell=cc_panel.shell,
            trigger_uri=uri,
            initial_text="0,0",
            max_len=16,
            on_commit=lambda s, o=out_n: handle_cc_display_prompt(cc_panel, f"cc_disp_pos|{o}", s),
        )
        return True
    elif act.startswith("power:"):
        _, out_n, st = act.split(":", 2)
        ok, _o, err = probe.run_cmd_full(["niri", "msg", "output", out_n, st], timeout=2.0)
        _set_disp_fb(ok, f"Output {out_n} -> {st.upper()}" if ok else (err or "Failed"))
    elif act.startswith("res_max:"):
        out_n = act.split(":", 1)[1]
        run_desktop_cli(["-o", out_n, "-r", "max"])
        _set_disp_fb(True, t("cc_display.set_native_max_mode_on", p0=out_n))
    elif act.startswith("res:"):
        _, out_n, m_spec = act.split(":", 2)
        run_desktop_cli(["-o", out_n, "-r", m_spec])
        _set_disp_fb(True, t("cc_display.applied_mode_on", p0=m_spec, p1=out_n))
    elif act.startswith("scale:"):
        _, out_n, sc_val = act.split(":", 2)
        run_desktop_cli(["-o", out_n, "--scale", sc_val])
        _set_disp_fb(True, t("cc_display.set_scale_x_on", p0=sc_val, p1=out_n))
    elif act.startswith("scale_inline:"):
        out_n = act.split(":", 1)[1]
        start_inline_input(
            entry_id=f"cc_disp_scale_{out_n}",
            shell=cc_panel.shell,
            trigger_uri=uri,
            initial_text="1.0",
            max_len=8,
            on_commit=lambda s, o=out_n: handle_cc_display_prompt(cc_panel, f"cc_desk_scale|{o}", s),
        )
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
        _set_disp_fb(True, t("cc_display.night_light", p0='ON' if on else 'OFF'))
    elif act.startswith("nl_temp:"):
        temp = max(2500, min(6500, int(round(float(act.split(":", 1)[1])))))
        STATE.set("display", "night_temp", temp)
        if _get_night_light_state()["active"]:
            probe.run_cmd(["pkill", "-x", "wlsunset"], timeout=1.0)
            probe.spawn_bg(["wlsunset", "-T", str(temp), "-t", str(max(2000, temp - 300))])
    elif act.startswith("dpms:"):
        mins = int(act.split(":", 1)[1])
        STATE.set("display", "dpms_timeout", mins)
        _set_disp_fb(True, t("cc_display.dpms_timeout_set_to_min", p0=mins))
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
        _set_disp_fb(True, t("cc_display.scale_x_on", p0=val, p1=out_n))
        return True
    if action_id.startswith("cc_disp_pos|"):
        out_n = action_id.split("|", 1)[1]
        run_desktop_cli(["-o", out_n, "--pos", val])
        _set_disp_fb(True, t("cc_display.position_on", p0=val, p1=out_n))
        return True
    return False
