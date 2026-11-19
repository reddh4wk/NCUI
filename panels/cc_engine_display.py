#!/usr/bin/env python3
import json
import math
import os
import re
import shutil
import tempfile
import time
from core import probe
from core.i18n import t
from core.state import STATE
from panels.wp_display import resolve_script_path, run_desktop_cli
from ui.kit import FeedbackBox, start_inline_input

NIRI_CONFIG_FILE = os.path.expanduser("~/.config/niri/config.kdl")
VALID_TRANSFORMS = (
    "normal",
    "90",
    "180",
    "270",
    "flipped",
    "flipped-90",
    "flipped-180",
    "flipped-270",
)



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
                m_tr = re.search(r'transform\s+"([^"]+)"', blk)
                m_vrr = re.search(r"(?m)^\s*variable-refresh-rate\b(.*)$", blk)
                vrr_on_demand = bool(m_vrr and "on-demand=true" in m_vrr.group(1))
                w = int(m_mode.group(1)) if m_mode else 1920
                h = int(m_mode.group(2)) if m_mode else 1080
                hz = int(round(float(m_mode.group(3)) * 1000)) if (m_mode and m_mode.group(3)) else 60000
                outs[name] = {
                    "name": name,
                    "model": "Configured Output",
                    "vrr_enabled": bool(m_vrr),
                    "vrr_on_demand": vrr_on_demand,
                    "logical": None if is_off else {
                        "width": w,
                        "height": h,
                        "x": int(m_pos.group(1)) if m_pos else 0,
                        "y": int(m_pos.group(2)) if m_pos else 0,
                        "scale": float(m_sc.group(1)) if m_sc else 1.0,
                        "transform": m_tr.group(1) if m_tr else "normal",
                    },
                    "modes": [{"width": w, "height": h, "refresh_rate": hz}],
                    "current_mode": 0,
                }
        except Exception:
            pass
    return outs


def _is_valid_scale(raw: str) -> bool:
    try:
        v = float(str(raw).strip())
        return math.isfinite(v) and (0.25 <= v <= 4.0)
    except Exception:
        return False


def _parse_pos_spec(raw: str):
    s = str(raw or "").strip()
    if s.lower() == "auto":
        return "auto"
    m = re.match(r"^(-?\d+)[,x:]\s*(-?\d+)$", s)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None


def _parse_hours_range(raw: str):
    m = re.match(r"^(\d{2}:\d{2})\s*-\s*(\d{2}:\d{2})$", str(raw or "").strip())
    if not m:
        return None
    for part in (m.group(1), m.group(2)):
        hh, mm = map(int, part.split(":"))
        if hh > 23 or mm > 59:
            return None
    return m.group(1), m.group(2)


def _parse_latlon(raw: str):
    m = re.match(r"^(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)$", str(raw or "").strip())
    if not m:
        return None
    lat, lon = float(m.group(1)), float(m.group(2))
    if not (math.isfinite(lat) and math.isfinite(lon)):
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None
    return f"{lat:g}", f"{lon:g}"


def _cli_result(res) -> tuple[bool, str]:
    if isinstance(res, tuple):
        ok = res[0]
        if isinstance(ok, bool):
            ok_b = ok
        elif isinstance(ok, int):
            ok_b = (ok == 0)
        else:
            ok_b = bool(ok)
        msg = " ".join(str(x).strip() for x in res[1:] if x and str(x).strip())
        return ok_b, msg
    if isinstance(res, bool):
        return res, ""
    if isinstance(res, int):
        return (res == 0), ""
    return bool(res), str(res or "")


def _update_niri_output_kdl(out_name: str, **updates) -> tuple[bool, str]:
    if not os.path.exists(NIRI_CONFIG_FILE):
        return True, ""
    try:
        txt = open(NIRI_CONFIG_FILE, "r", encoding="utf-8").read()
        pat = re.compile(rf'(output\s+"{re.escape(out_name)}"\s*\{{)([^}}]*)(\}})', re.DOTALL)
        m = pat.search(txt)
        blk = m.group(2) if m else "\n"

        if "off" in updates:
            blk = re.sub(r"(?m)^[ \t]*off\b.*\n?", "", blk)
            if updates["off"]:
                blk = blk.rstrip() + "\n    off\n"
        if "mode" in updates:
            blk = re.sub(r"(?m)^[ \t]*mode\b.*\n?", "", blk)
            if updates["mode"]:
                blk = blk.rstrip() + f'\n    mode "{updates["mode"]}"\n'
        if "scale" in updates:
            blk = re.sub(r"(?m)^[ \t]*scale\b.*\n?", "", blk)
            if updates["scale"] is not None:
                blk = blk.rstrip() + f"\n    scale {updates['scale']}\n"
        if "position" in updates:
            blk = re.sub(r"(?m)^[ \t]*position\b.*\n?", "", blk)
            pos = updates["position"]
            if isinstance(pos, tuple) and len(pos) == 2:
                blk = blk.rstrip() + f"\n    position x={pos[0]} y={pos[1]}\n"
        if "transform" in updates:
            blk = re.sub(r"(?m)^[ \t]*transform\b.*\n?", "", blk)
            tr = updates["transform"]
            if tr and tr != "normal":
                blk = blk.rstrip() + f'\n    transform "{tr}"\n'
        if "vrr" in updates:
            blk = re.sub(r"(?m)^[ \t]*variable-refresh-rate\b.*\n?", "", blk)
            vrr = updates["vrr"]
            if vrr == "on":
                blk = blk.rstrip() + "\n    variable-refresh-rate\n"
            elif vrr == "on-demand":
                blk = blk.rstrip() + "\n    variable-refresh-rate on-demand=true\n"

        if m:
            new_txt = txt[:m.start(2)] + blk + txt[m.end(2):]
        else:
            new_txt = txt.rstrip() + f'\n\noutput "{out_name}" {{{blk}}}\n'

        parent_dir = os.path.dirname(NIRI_CONFIG_FILE) or "."
        fd, tmp_path = tempfile.mkstemp(dir=parent_dir, prefix=".niri_kdl_", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(new_txt)
            os.replace(tmp_path, NIRI_CONFIG_FILE)
        except Exception:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise
        return True, ""
    except Exception as e:
        return False, str(e)


def _get_max_mode_spec(out_name: str) -> str:
    modes = (get_niri_outputs_data().get(out_name) or {}).get("modes") or []
    if not modes:
        return ""
    best = max(modes, key=lambda m: (m.get("width", 0) * m.get("height", 0), m.get("width", 0), m.get("refresh_rate", 0)))
    rr = best.get("refresh_rate", 0)
    hz = rr / 1000.0 if rr > 1000 else float(rr)
    hz_str = f"{hz:.3f}".rstrip("0").rstrip(".")
    return f"{best.get('width', 0)}x{best.get('height', 0)}@{hz_str}"


def _get_night_light_state() -> dict:
    ok, _ = probe.run_cmd(["pgrep", "-x", "wlsunset"], timeout=0.8)
    temp = max(1700, min(6500, int(STATE.get("display", "night_temp", 4200) or 4200)))
    sched = str(STATE.get("display", "nl_schedule", "manual") or "manual")
    if sched not in ("manual", "sunset"):
        sched = "manual"
    sunset = str(STATE.get("display", "nl_sunset", "20:00") or "20:00")
    sunrise = str(STATE.get("display", "nl_sunrise", "06:30") or "06:30")
    lat = str(STATE.get("display", "nl_lat", "") or "")
    lon = str(STATE.get("display", "nl_lon", "") or "")
    return {
        "active": ok,
        "temp": temp,
        "schedule": sched,
        "sunset": sunset,
        "sunrise": sunrise,
        "lat": lat,
        "lon": lon,
    }


def _apply_night_light(enable: bool) -> tuple[bool, str]:
    probe.run_cmd(["pkill", "-x", "wlsunset"], timeout=1.0)
    if not enable:
        return True, t("cc_display.night_light", p0=t("common.off"))
    nl = _get_night_light_state()
    temp = nl["temp"]
    # wlsunset strictly requires -T (day temp) > -t (night temp)
    day_temp = max(6500, temp + 100)
    cmd = ["wlsunset", "-T", str(day_temp), "-t", str(temp)]
    if nl["schedule"] == "sunset" and nl["lat"] and nl["lon"]:
        cmd.extend(["-l", nl["lat"], "-L", nl["lon"]])
    else:
        cmd.extend(["-S", nl["sunrise"], "-s", nl["sunset"]])
    probe.spawn_bg(cmd)
    time.sleep(0.15)
    alive, _ = probe.run_cmd(["pgrep", "-x", "wlsunset"], timeout=0.8)
    if not alive:
        return False, t("cc_display.wlsunset_failed")
    return True, t("cc_display.night_light", p0=t("common.on"))


def _set_niri_vrr(out_name: str, mode: str) -> tuple[bool, str]:
    if mode not in ("off", "on", "on-demand"):
        return False, t("cc_display.err_cmd_failed", p0=out_name)
    ok, _out, err = probe.run_cmd_full(["niri", "msg", "output", out_name, "vrr", mode], timeout=1.5)
    kdl_ok, kdl_err = _update_niri_output_kdl(out_name, vrr=mode) if (ok or os.path.exists(NIRI_CONFIG_FILE)) else (True, "")
    if ok or (kdl_ok and os.path.exists(NIRI_CONFIG_FILE)):
        lbl = t("cc_display.vrr_on_demand") if mode == "on-demand" else (t("cc_display.vrr_on") if mode == "on" else t("cc_display.vrr_off"))
        return True, f"{lbl} ({out_name})"
    return False, (err or kdl_err or t("cc_display.err_cmd_failed", p0=out_name))


def _apply_dpms_timeout(mins: int) -> tuple[bool, str]:
    STATE.set("display", "dpms_timeout", mins)
    probe.run_cmd(["pkill", "-x", "swayidle"], timeout=1.0)
    if mins <= 0:
        return True, t("cc_display.dpms_timeout_set_to_min", p0=0)
    if not shutil.which("swayidle"):
        return False, t("cc_display.swayidle_missing")
    probe.spawn_bg(["swayidle", "-w", "timeout", str(mins * 60), "niri msg action power-off-monitors"])
    return True, t("cc_display.dpms_timeout_set_to_min", p0=mins)


def handle_cc_display_link(cc_panel, uri: str) -> bool:
    if not uri.startswith("cc_desk:"):
        return False
    act = uri[len("cc_desk:"):]

    if act == "open_tui":
        probe.open_terminal(["bash", resolve_script_path("nc_util_desktop.sh", fallback_alias="nc-desktop.sh"), "-I"])
        return True
    if act == "pos_auto_all":
        ok, msg = _cli_result(run_desktop_cli(["-o", "all", "--pos", "auto"]))
        if ok:
            for o_name in get_niri_outputs_data():
                _update_niri_output_kdl(o_name, position="auto")
            _set_disp_fb(True, t("cc_display.auto_aligned_all_outputs"))
        else:
            _set_disp_fb(False, msg or t("cc_display.err_cmd_failed", p0="all"))
    elif act.startswith("pos_auto:"):
        out_n = act.split(":", 1)[1]
        ok, msg = _cli_result(run_desktop_cli(["-o", out_n, "--pos", "auto"]))
        if ok:
            _update_niri_output_kdl(out_n, position="auto")
            _set_disp_fb(True, t("cc_display.auto_positioned", p0=out_n))
        else:
            _set_disp_fb(False, msg or t("cc_display.err_cmd_failed", p0=out_n))
    elif act.startswith("pos_inline:"):
        out_n = act.split(":", 1)[1]
        logical = (get_niri_outputs_data().get(out_n) or {}).get("logical") or {}
        init_pos = f"{logical.get('x', 0)},{logical.get('y', 0)}"
        start_inline_input(
            entry_id=f"cc_disp_pos_{out_n}",
            shell=cc_panel.shell,
            trigger_uri=uri,
            initial_text=init_pos,
            max_len=16,
            validator=lambda s: _parse_pos_spec(s) is not None,
            on_commit=lambda s, o=out_n: handle_cc_display_prompt(cc_panel, f"cc_disp_pos|{o}", s),
        )
        return True
    elif act.startswith("power:"):
        _, out_n, st = act.split(":", 2)
        if st not in ("on", "off"):
            return False
        ok, _o, err = probe.run_cmd_full(["niri", "msg", "output", out_n, st], timeout=2.0)
        if ok:
            _update_niri_output_kdl(out_n, off=(st == "off"))
        _set_disp_fb(ok, f"{out_n} -> {st.upper()}" if ok else (err or t("cc_display.err_cmd_failed", p0=out_n)))
    elif act.startswith("transform:"):
        _, out_n, tr_val = act.split(":", 2)
        if tr_val not in VALID_TRANSFORMS:
            return False
        ok, _o, err = probe.run_cmd_full(["niri", "msg", "output", out_n, "transform", tr_val], timeout=2.0)
        if ok:
            _update_niri_output_kdl(out_n, transform=tr_val)
        _set_disp_fb(ok, t("cc_display.transform_set", p0=tr_val, p1=out_n) if ok else (err or t("cc_display.err_cmd_failed", p0=out_n)))
    elif act.startswith("res_max:"):
        out_n = act.split(":", 1)[1]
        ok, msg = _cli_result(run_desktop_cli(["-o", out_n, "-r", "max"]))
        if ok:
            max_spec = _get_max_mode_spec(out_n)
            if max_spec:
                _update_niri_output_kdl(out_n, mode=max_spec)
            _set_disp_fb(True, t("cc_display.set_native_max_mode_on", p0=out_n))
        else:
            _set_disp_fb(False, msg or t("cc_display.err_cmd_failed", p0=out_n))
    elif act.startswith("res:"):
        _, out_n, m_spec = act.split(":", 2)
        ok, msg = _cli_result(run_desktop_cli(["-o", out_n, "-r", m_spec]))
        if ok:
            _update_niri_output_kdl(out_n, mode=m_spec)
            _set_disp_fb(True, t("cc_display.applied_mode_on", p0=m_spec, p1=out_n))
        else:
            _set_disp_fb(False, msg or t("cc_display.err_cmd_failed", p0=out_n))
    elif act.startswith("scale:"):
        _, out_n, sc_val = act.split(":", 2)
        if not _is_valid_scale(sc_val):
            _set_disp_fb(False, t("cc_display.invalid_scale", p0=sc_val))
        else:
            ok, msg = _cli_result(run_desktop_cli(["-o", out_n, "--scale", sc_val]))
            if ok:
                _update_niri_output_kdl(out_n, scale=sc_val)
                _set_disp_fb(True, t("cc_display.set_scale_x_on", p0=sc_val, p1=out_n))
            else:
                _set_disp_fb(False, msg or t("cc_display.err_cmd_failed", p0=out_n))
    elif act.startswith("scale_inline:"):
        out_n = act.split(":", 1)[1]
        logical = (get_niri_outputs_data().get(out_n) or {}).get("logical") or {}
        init_scale = f"{float(logical.get('scale', 1.0)):g}"
        start_inline_input(
            entry_id=f"cc_disp_scale_{out_n}",
            shell=cc_panel.shell,
            trigger_uri=uri,
            initial_text=init_scale,
            max_len=8,
            validator=_is_valid_scale,
            on_commit=lambda s, o=out_n: handle_cc_display_prompt(cc_panel, f"cc_desk_scale|{o}", s),
        )
        return True
    elif act.startswith("vrr:"):
        _, out_n, state = act.split(":", 2)
        ok, msg = _set_niri_vrr(out_n, state)
        _set_disp_fb(ok, msg)
    elif act.startswith("nl:"):
        on = act.split(":", 1)[1] == "on"
        ok, msg = _apply_night_light(on)
        _set_disp_fb(ok, msg)
    elif act.startswith("nl_temp:"):
        temp = max(1700, min(6500, int(round(float(act.split(":", 1)[1])))))
        STATE.set("display", "night_temp", temp)
        if _get_night_light_state()["active"]:
            ok, msg = _apply_night_light(True)
            if not ok:
                _set_disp_fb(False, msg)
    elif act.startswith("nl_sched:"):
        sched = act.split(":", 1)[1]
        if sched not in ("manual", "sunset"):
            return False
        STATE.set("display", "nl_schedule", sched)
        if _get_night_light_state()["active"]:
            ok, msg = _apply_night_light(True)
            _set_disp_fb(ok, msg if not ok else t("cc_display.nl_sched_saved", p0=t(f"cc_display.nl_sched_{sched}")))
        else:
            _set_disp_fb(True, t("cc_display.nl_sched_saved", p0=t(f"cc_display.nl_sched_{sched}")))
    elif act == "nl_hours_inline":
        nl = _get_night_light_state()
        start_inline_input(
            entry_id="cc_disp_nl_hours",
            shell=cc_panel.shell,
            trigger_uri=uri,
            initial_text=f"{nl['sunset']}-{nl['sunrise']}",
            max_len=13,
            validator=lambda s: _parse_hours_range(s) is not None,
            on_commit=lambda s: handle_cc_display_prompt(cc_panel, "cc_disp_nl_hours", s),
        )
        return True
    elif act == "nl_coords_inline":
        nl = _get_night_light_state()
        coords_init = f"{nl['lat']},{nl['lon']}" if (nl["lat"] and nl["lon"]) else ""
        start_inline_input(
            entry_id="cc_disp_nl_coords",
            shell=cc_panel.shell,
            trigger_uri=uri,
            initial_text=coords_init,
            max_len=24,
            validator=lambda s: _parse_latlon(s) is not None,
            on_commit=lambda s: handle_cc_display_prompt(cc_panel, "cc_disp_nl_coords", s),
        )
        return True
    elif act.startswith("dpms:"):
        mins = int(act.split(":", 1)[1])
        ok, msg = _apply_dpms_timeout(mins)
        _set_disp_fb(ok, msg)
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
        if not _is_valid_scale(val):
            _set_disp_fb(False, t("cc_display.invalid_scale", p0=val))
            return True
        ok, msg = _cli_result(run_desktop_cli(["-o", out_n, "--scale", val]))
        if ok:
            _update_niri_output_kdl(out_n, scale=val)
            _set_disp_fb(True, t("cc_display.scale_x_on", p0=val, p1=out_n))
        else:
            _set_disp_fb(False, msg or t("cc_display.err_cmd_failed", p0=out_n))
        return True
    if action_id.startswith("cc_disp_pos|"):
        out_n = action_id.split("|", 1)[1]
        parsed_pos = _parse_pos_spec(val)
        if parsed_pos is None:
            _set_disp_fb(False, t("cc_display.invalid_pos", p0=val))
            return True
        ok, msg = _cli_result(run_desktop_cli(["-o", out_n, "--pos", val]))
        if ok:
            _update_niri_output_kdl(out_n, position=parsed_pos)
            _set_disp_fb(True, t("cc_display.position_on", p0=val, p1=out_n))
        else:
            _set_disp_fb(False, msg or t("cc_display.err_cmd_failed", p0=out_n))
        return True
    if action_id == "cc_disp_nl_hours":
        parsed = _parse_hours_range(val)
        if parsed is None:
            _set_disp_fb(False, t("cc_display.invalid_hours", p0=val))
            return True
        sunset, sunrise = parsed
        STATE.set("display", "nl_sunset", sunset)
        STATE.set("display", "nl_sunrise", sunrise)
        if _get_night_light_state()["active"]:
            ok, msg = _apply_night_light(True)
            _set_disp_fb(ok, msg if not ok else t("cc_display.nl_hours_saved", p0=f"{sunset}-{sunrise}"))
        else:
            _set_disp_fb(True, t("cc_display.nl_hours_saved", p0=f"{sunset}-{sunrise}"))
        return True
    if action_id == "cc_disp_nl_coords":
        parsed = _parse_latlon(val)
        if parsed is None:
            _set_disp_fb(False, t("cc_display.invalid_coords", p0=val))
            return True
        lat, lon = parsed
        STATE.set("display", "nl_lat", lat)
        STATE.set("display", "nl_lon", lon)
        if _get_night_light_state()["active"]:
            ok, msg = _apply_night_light(True)
            _set_disp_fb(ok, msg if not ok else t("cc_display.nl_coords_saved", p0=f"{lat},{lon}"))
        else:
            _set_disp_fb(True, t("cc_display.nl_coords_saved", p0=f"{lat},{lon}"))
        return True
    return False
