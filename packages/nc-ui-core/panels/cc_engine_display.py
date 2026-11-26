#!/usr/bin/env python3
from datetime import datetime, timezone
import json
import math
import os
import re
import shutil
import socket
import sys
import tempfile
import time
from core import probe
from core.i18n import t
from core.state import STATE
from core.paths import find_bin
from ui.kit import feedback_cards, set_feedback, start_inline_input


def _niri_config_file() -> str:
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip() or "~/.config"
    return os.path.join(os.path.expanduser(xdg), "niri", "config.kdl")


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
    set_feedback("display", ok, msg)


def _feedback_cards() -> list:
    return feedback_cards("display", "cc_display.display_status")


_OUTPUTS_CACHE: tuple[float, dict] | None = None
_NIGHTLIGHT_SOCK = "/tmp/nc_ui_nightlight.sock"


def _nightlight_bin() -> str:
    return find_bin("nc-nightlight")


def _invalidate_outputs_cache() -> None:
    global _OUTPUTS_CACHE
    _OUTPUTS_CACHE = None


def get_niri_outputs_data() -> dict:
    global _OUTPUTS_CACHE
    now = time.monotonic()
    if _OUTPUTS_CACHE is not None and (now - _OUTPUTS_CACHE[0]) < 1.5:
        return _OUTPUTS_CACHE[1]
    ok, out = probe.run_cmd(["niri", "msg", "-j", "outputs"], timeout=1.5)
    if ok and out:
        try:
            parsed = json.loads(out)
            _OUTPUTS_CACHE = (now, parsed)
            return parsed
        except Exception:
            pass
    # Fallback parsing from config.kdl when niri IPC socket isn't present in headless env
    outs = {}
    cfg_file = _niri_config_file()
    if os.path.exists(cfg_file):
        try:
            txt = open(cfg_file, "r", encoding="utf-8").read()
            for m in re.finditer(r'output\s+"([^"]+)"\s*\{([^}]*)\}', txt, re.DOTALL):
                name, blk = m.group(1), m.group(2)
                is_off = bool(re.search(r"(?m)^\s*off\b", blk))
                m_mode = re.search(r'mode\s+"(\d+)x(\d+)(?:@([0-9.]+))?"', blk)
                m_pos = re.search(r"position\s+x=(-?\d+)\s+y=(-?\d+)", blk)
                m_sc = re.search(r"scale\s+([0-9.]+)", blk)
                m_tr = re.search(r'transform\s+"([^"]+)"', blk)
                w = int(m_mode.group(1)) if m_mode else 1920
                h = int(m_mode.group(2)) if m_mode else 1080
                hz = int(round(float(m_mode.group(3)) * 1000)) if (m_mode and m_mode.group(3)) else 60000
                outs[name] = {
                    "name": name,
                    "model": t("cc_display.configured_output"),
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
    _OUTPUTS_CACHE = (now, outs)
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


def _update_niri_output_kdl(out_name: str, **updates) -> tuple[bool, str]:
    cfg_file = _niri_config_file()
    if not os.path.exists(cfg_file):
        return True, ""
    try:
        txt = open(cfg_file, "r", encoding="utf-8").read()
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

        if m:
            new_txt = txt[:m.start(2)] + blk + txt[m.end(2):]
        else:
            new_txt = txt.rstrip() + f'\n\noutput "{out_name}" {{{blk}}}\n'

        parent_dir = os.path.dirname(cfg_file) or "."
        fd, tmp_path = tempfile.mkstemp(dir=parent_dir, prefix=".niri_kdl_", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(new_txt)
            os.replace(tmp_path, cfg_file)
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


def _parse_hhmm_min(hhmm: str, default: int) -> int:
    try:
        hh, mm = map(int, str(hhmm).strip().split(":", 1))
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            return hh * 60 + mm
    except Exception:
        pass
    return default


def _is_night_window_active(nl: dict) -> bool:
    sched = nl.get("schedule", "always")
    if sched == "always":
        return True
    if sched == "sunset" and nl.get("lat") and nl.get("lon"):
        try:
            lat = float(nl["lat"])
            lon = float(nl["lon"])
            now_utc = datetime.now(timezone.utc)
            doy = now_utc.timetuple().tm_yday
            utc_hours = now_utc.hour + now_utc.minute / 60.0 + now_utc.second / 3600.0
            gamma = 2.0 * math.pi / 365.0 * (doy - 1 + (utc_hours - 12.0) / 24.0)
            eqtime = 229.18 * (
                0.000075
                + 0.001868 * math.cos(gamma)
                - 0.032077 * math.sin(gamma)
                - 0.014615 * math.cos(2 * gamma)
                - 0.040849 * math.sin(2 * gamma)
            )
            decl = (
                0.006918
                - 0.399912 * math.cos(gamma)
                + 0.070257 * math.sin(gamma)
                - 0.006758 * math.cos(2 * gamma)
                + 0.000907 * math.sin(2 * gamma)
                - 0.002697 * math.cos(3 * gamma)
                + 0.00148 * math.sin(3 * gamma)
            )
            ha_deg = (utc_hours * 60.0 + eqtime + 4.0 * lon) / 4.0 - 180.0
            lat_r = math.radians(lat)
            cos_zenith = math.sin(lat_r) * math.sin(decl) + math.cos(lat_r) * math.cos(decl) * math.cos(math.radians(ha_deg))
            cos_zenith = max(-1.0, min(1.0, cos_zenith))
            alt_deg = 90.0 - math.degrees(math.acos(cos_zenith))
            return alt_deg <= 0.0
        except Exception:
            pass
    now_local = datetime.now()
    now_min = now_local.hour * 60 + now_local.minute
    s_min = _parse_hhmm_min(nl.get("sunset", "20:00"), 20 * 60)
    r_min = _parse_hhmm_min(nl.get("sunrise", "06:30"), 6 * 60 + 30)
    if s_min > r_min:
        return now_min >= s_min or now_min < r_min
    return s_min <= now_min < r_min


def _is_proc_alive(name: str) -> bool:
    ok, out = probe.run_cmd(["pgrep", "-x", name], timeout=0.6)
    if not ok or not out:
        return False
    for tok in out.split():
        try:
            with open(f"/proc/{int(tok)}/stat", "r", encoding="utf-8") as f:
                if ") Z " not in f.read():
                    return True
        except Exception:
            pass
    return False


def _nightlight_ipc(payload: dict, timeout: float = 0.20) -> bool:
    if not os.path.exists(_NIGHTLIGHT_SOCK):
        return False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect(_NIGHTLIGHT_SOCK)
            s.sendall((json.dumps(payload) + "\n").encode("utf-8"))
            resp = s.recv(64).decode("utf-8", "ignore").strip()
            return resp == "ok"
    except Exception:
        return False


def _is_night_light_alive() -> bool:
    if _nightlight_ipc({"cmd": "ping"}, timeout=0.08):
        return True
    return _is_proc_alive("wlsunset")


def _get_night_light_state() -> dict:
    ok = _is_night_light_alive()
    temp = max(1700, min(6500, int(STATE.get("display", "night_temp", 4200) or 4200)))
    sched = str(STATE.get("display", "nl_schedule", "always") or "always")
    if sched not in ("always", "manual", "sunset"):
        sched = "always"
    sunset = str(STATE.get("display", "nl_sunset", "20:00") or "20:00")
    sunrise = str(STATE.get("display", "nl_sunrise", "06:30") or "06:30")
    lat = str(STATE.get("display", "nl_lat", "") or "")
    lon = str(STATE.get("display", "nl_lon", "") or "")
    res = {
        "active": ok,
        "temp": temp,
        "schedule": sched,
        "sunset": sunset,
        "sunrise": sunrise,
        "lat": lat,
        "lon": lon,
    }
    res["in_night"] = _is_night_window_active(res)
    return res


def _nl_payload(nl: dict) -> dict:
    return {
        "temp": int(nl["temp"]),
        "schedule": str(nl["schedule"]),
        "sunrise": str(nl["sunrise"]),
        "sunset": str(nl["sunset"]),
        "lat": str(nl["lat"]),
        "lon": str(nl["lon"]),
    }


def _ensure_night_light_running(nl: dict) -> bool:
    payload = _nl_payload(nl)
    if _nightlight_ipc(payload):
        return True
    if _is_proc_alive("wlsunset"):
        probe.run_cmd(["pkill", "-x", "wlsunset"], timeout=1.0)
        for _ in range(16):
            if not _is_proc_alive("wlsunset"):
                break
            time.sleep(0.025)
    nl_bin = _nightlight_bin()
    if not nl_bin:
        return False
    probe.spawn_bg([
        sys.executable,
        nl_bin,
        "--temp", str(payload["temp"]),
        "--schedule", payload["schedule"],
        "--sunrise", payload["sunrise"],
        "--sunset", payload["sunset"],
        "--lat", payload["lat"],
        "--lon", payload["lon"],
    ])
    for _ in range(25):
        if _nightlight_ipc({"cmd": "ping"}, timeout=0.08):
            return True
        time.sleep(0.02)
    return False


def _apply_night_light(enable: bool) -> tuple[bool, str]:
    if not enable:
        _nightlight_ipc({"cmd": "stop"}, timeout=0.25)
        nl_bin = _nightlight_bin()
        if nl_bin:
            probe.run_cmd(["pkill", "-f", nl_bin], timeout=0.8)
        probe.run_cmd(["pkill", "-x", "wlsunset"], timeout=0.8)
        return True, t("cc_display.night_light", p0=t("common.off"))
    nl = _get_night_light_state()
    if not _ensure_night_light_running(nl):
        return False, t("cc_display.wlsunset_failed")
    return True, t("cc_display.night_light", p0=t("common.on"))


def _sync_after_display_geometry_change(cc_panel) -> None:
    if os.environ.get("NC_UI_OFFSCREEN") != "1":
        try:
            from panels.cc_engine_waybar import _read_wb_config, _write_wb_config
            _write_wb_config(_read_wb_config(), reload_bar=True)
        except Exception:
            pass
    sh = getattr(cc_panel, "shell", None) if cc_panel else None
    if sh and hasattr(sh, "on_display_geometry_changed"):
        sh.on_display_geometry_changed()


def _apply_dpms_timeout(mins: int) -> tuple[bool, str]:
    STATE.set("display", "dpms_timeout", mins)
    probe.run_cmd(["pkill", "-x", "swayidle"], timeout=1.0)
    if mins <= 0:
        return True, t("cc_display.dpms_timeout_set_to_min", p0=0)
    if not shutil.which("swayidle"):
        return False, t("cc_display.swayidle_missing")
    probe.spawn_bg(["swayidle", "-w", "timeout", str(mins * 60), "niri msg action power-off-monitors"])
    return True, t("cc_display.dpms_timeout_set_to_min", p0=mins)


def get_backlight_info() -> dict | None:
    bl_root = "/sys/class/backlight"
    if not os.path.isdir(bl_root):
        return None
    prio = {"raw": 0, "platform": 1, "firmware": 2}
    best = None
    try:
        entries = sorted(os.listdir(bl_root))
    except OSError:
        return None
    for name in entries:
        dev_dir = os.path.join(bl_root, name)
        try:
            max_b = int(open(os.path.join(dev_dir, "max_brightness"), encoding="utf-8").read().strip())
            if max_b <= 0:
                continue
            cur_p = os.path.join(dev_dir, "actual_brightness")
            if not os.path.isfile(cur_p):
                cur_p = os.path.join(dev_dir, "brightness")
            cur_b = int(open(cur_p, encoding="utf-8").read().strip())
            tp = os.path.join(dev_dir, "type")
            btype = open(tp, encoding="utf-8").read().strip().lower() if os.path.isfile(tp) else ""
        except Exception:
            continue
        rank = (prio.get(btype, 3), -max_b, name)
        if best is None or rank < best[0]:
            best = (rank, {
                "device": name,
                "path": dev_dir,
                "type": btype or "unknown",
                "brightness": cur_b,
                "max_brightness": max_b,
                "pct": max(0, min(100, int(round((cur_b * 100.0) / max_b)))),
            })
    return best[1] if best else None


def set_backlight_pct(pct: int) -> tuple[bool, str]:
    info = get_backlight_info()
    if not info:
        return False, t("cc_display.backlight_err", p0="no backlight device")
    target_pct = max(1, min(100, int(round(pct))))
    bctl = find_bin("brightnessctl") or shutil.which("brightnessctl")
    last_err = ""
    if bctl:
        ok, _o, err = probe.run_cmd_full([bctl, "-d", info["device"], "set", f"{target_pct}%"], timeout=2.0)
        if ok:
            after = get_backlight_info()
            return True, t("cc_display.backlight_set", p0=(after["pct"] if after else target_pct))
        last_err = err
    try:
        raw_val = max(1, min(info["max_brightness"], int(round(info["max_brightness"] * (target_pct / 100.0)))))
        with open(os.path.join(info["path"], "brightness"), "w", encoding="utf-8") as f:
            f.write(f"{raw_val}\n")
        after = get_backlight_info()
        return True, t("cc_display.backlight_set", p0=(after["pct"] if after else target_pct))
    except Exception as e:
        return False, t("cc_display.backlight_err", p0=(last_err or str(e)))


def handle_cc_display_link(cc_panel, uri: str) -> bool:
    if not isinstance(uri, str) or not uri.startswith("cc_desk:"):
        return False
    act = uri[len("cc_desk:"):]

    if act == "pos_auto_all":
        outs = list(get_niri_outputs_data().keys())
        all_ok = bool(outs)
        last_err = ""
        for o_name in outs:
            ok_p, _o, err_p = probe.run_cmd_full(["niri", "msg", "output", o_name, "position", "auto"], timeout=2.0)
            if ok_p:
                _update_niri_output_kdl(o_name, position="auto")
            else:
                all_ok = False
                last_err = err_p or last_err
        _invalidate_outputs_cache()
        if all_ok:
            _set_disp_fb(True, t("cc_display.auto_aligned_all_outputs"))
        else:
            _set_disp_fb(False, last_err or t("cc_display.err_cmd_failed", p0="all"))
    elif act.startswith("pos_auto:"):
        out_n = act.split(":", 1)[1]
        if not out_n:
            return False
        ok, _o, err = probe.run_cmd_full(["niri", "msg", "output", out_n, "position", "auto"], timeout=2.0)
        if ok:
            _invalidate_outputs_cache()
            _update_niri_output_kdl(out_n, position="auto")
            _set_disp_fb(True, t("cc_display.auto_positioned", p0=out_n))
        else:
            _set_disp_fb(False, err or t("cc_display.err_cmd_failed", p0=out_n))
    elif act.startswith("pos_inline:"):
        out_n = act.split(":", 1)[1]
        if not out_n or not cc_panel or not cc_panel.shell:
            return False
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
    elif act.startswith("select_out:"):
        out_n = act.split(":", 1)[1]
        if not out_n:
            return False
        if out_n in get_niri_outputs_data():
            STATE.set("display", "selected_output", out_n)
    elif act.startswith("power:"):
        parts = act.split(":", 2)
        if len(parts) != 3 or not parts[1]:
            return False
        _, out_n, st = parts
        if st not in ("on", "off"):
            return False
        ok, _o, err = probe.run_cmd_full(["niri", "msg", "output", out_n, st], timeout=2.0)
        if ok:
            _invalidate_outputs_cache()
            _update_niri_output_kdl(out_n, off=(st == "off"))
        _set_disp_fb(ok, f"{out_n} -> {st.upper()}" if ok else (err or t("cc_display.err_cmd_failed", p0=out_n)))
        if ok:
            _sync_after_display_geometry_change(cc_panel)
            return True
    elif act.startswith("transform:"):
        parts = act.split(":", 2)
        if len(parts) != 3 or not parts[1]:
            return False
        _, out_n, tr_val = parts
        if tr_val not in VALID_TRANSFORMS:
            return False
        ok, _o, err = probe.run_cmd_full(["niri", "msg", "output", out_n, "transform", tr_val], timeout=2.0)
        if ok:
            _invalidate_outputs_cache()
            _update_niri_output_kdl(out_n, transform=tr_val)
        _set_disp_fb(ok, t("cc_display.transform_set", p0=tr_val, p1=out_n) if ok else (err or t("cc_display.err_cmd_failed", p0=out_n)))
        if ok:
            _sync_after_display_geometry_change(cc_panel)
            return True
    elif act.startswith("res_max:"):
        out_n = act.split(":", 1)[1]
        if not out_n:
            return False
        max_spec = _get_max_mode_spec(out_n)
        if not max_spec:
            _set_disp_fb(False, t("cc_display.err_cmd_failed", p0=out_n))
        else:
            ok, _o, err = probe.run_cmd_full(["niri", "msg", "output", out_n, "mode", max_spec], timeout=2.0)
            if ok:
                _invalidate_outputs_cache()
                _update_niri_output_kdl(out_n, mode=max_spec)
                _set_disp_fb(True, t("cc_display.set_native_max_mode_on", p0=out_n))
                _sync_after_display_geometry_change(cc_panel)
                return True
            _set_disp_fb(False, err or t("cc_display.err_cmd_failed", p0=out_n))
    elif act.startswith("res:"):
        parts = act.split(":", 2)
        if len(parts) != 3 or not parts[1] or not parts[2]:
            return False
        _, out_n, m_spec = parts
        ok, _o, err = probe.run_cmd_full(["niri", "msg", "output", out_n, "mode", m_spec], timeout=2.0)
        if ok:
            _invalidate_outputs_cache()
            _update_niri_output_kdl(out_n, mode=m_spec)
            _set_disp_fb(True, t("cc_display.applied_mode_on", p0=m_spec, p1=out_n))
            _sync_after_display_geometry_change(cc_panel)
            return True
        _set_disp_fb(False, err or t("cc_display.err_cmd_failed", p0=out_n))
    elif act.startswith("scale:"):
        parts = act.split(":", 2)
        if len(parts) != 3 or not parts[1] or not parts[2]:
            return False
        _, out_n, sc_val = parts
        if not _is_valid_scale(sc_val):
            _set_disp_fb(False, t("cc_display.invalid_scale", p0=sc_val))
        else:
            ok, _o, err = probe.run_cmd_full(["niri", "msg", "output", out_n, "scale", sc_val], timeout=2.0)
            if ok:
                _invalidate_outputs_cache()
                _update_niri_output_kdl(out_n, scale=sc_val)
                _set_disp_fb(True, t("cc_display.set_scale_x_on", p0=sc_val, p1=out_n))
                _sync_after_display_geometry_change(cc_panel)
                return True
            _set_disp_fb(False, err or t("cc_display.err_cmd_failed", p0=out_n))
    elif act.startswith("scale_inline:"):
        out_n = act.split(":", 1)[1]
        if not out_n or not cc_panel or not cc_panel.shell:
            return False
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
    elif act.startswith("nl:"):
        nl_arg = act.split(":", 1)[1]
        if nl_arg not in ("on", "off"):
            return False
        ok, msg = _apply_night_light(nl_arg == "on")
        _set_disp_fb(ok, msg)
    elif act.startswith("nl_temp:"):
        try:
            temp = max(1700, min(6500, int(round(float(act.split(":", 1)[1])))))
        except (ValueError, TypeError, OverflowError):
            return False
        STATE.set("display", "night_temp", temp)
        nl = _get_night_light_state()
        if nl["active"]:
            probe.enqueue_latest("night_light_apply", lambda snap=dict(nl): _ensure_night_light_running(snap), debounce_ms=0)
        if cc_panel:
            cc_panel.request_throttled_refresh()
        return True
    elif act.startswith("nl_sched:"):
        sched = act.split(":", 1)[1]
        if sched not in ("always", "manual", "sunset"):
            return False
        STATE.set("display", "nl_schedule", sched)
        nl = _get_night_light_state()
        if nl["active"]:
            probe.enqueue_latest("night_light_apply", lambda snap=dict(nl): _ensure_night_light_running(snap), debounce_ms=0)
        _set_disp_fb(True, t("cc_display.nl_sched_saved", p0=t(f"cc_display.nl_sched_{sched}")))
    elif act == "nl_hours_inline":
        if not cc_panel or not cc_panel.shell:
            return False
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
        if not cc_panel or not cc_panel.shell:
            return False
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
        try:
            mins = int(act.split(":", 1)[1])
        except (ValueError, TypeError):
            return False
        ok, msg = _apply_dpms_timeout(mins)
        _set_disp_fb(ok, msg)
    elif act == "dpms_now":
        probe.spawn_bg(["niri", "msg", "action", "power-off-monitors"])
        return True
    elif act.startswith("backlight:"):
        try:
            pct = int(round(float(act.split(":", 1)[1])))
        except (ValueError, TypeError, OverflowError):
            return False
        ok, msg = set_backlight_pct(pct)
        if not ok:
            _set_disp_fb(False, msg)
        elif cc_panel:
            cc_panel.request_throttled_refresh()
            return True
    elif act.startswith("backlight_preset:"):
        try:
            pct = int(round(float(act.split(":", 1)[1])))
        except (ValueError, TypeError, OverflowError):
            return False
        ok, msg = set_backlight_pct(pct)
        _set_disp_fb(ok, msg)
    else:
        return False

    if cc_panel and cc_panel.shell:
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
        ok, _o, err = probe.run_cmd_full(["niri", "msg", "output", out_n, "scale", val], timeout=2.0)
        if ok:
            _invalidate_outputs_cache()
            _update_niri_output_kdl(out_n, scale=val)
            _set_disp_fb(True, t("cc_display.set_scale_x_on", p0=val, p1=out_n))
            _sync_after_display_geometry_change(cc_panel)
        else:
            _set_disp_fb(False, err or t("cc_display.err_cmd_failed", p0=out_n))
        return True
    if action_id.startswith("cc_disp_pos|"):
        out_n = action_id.split("|", 1)[1]
        parsed_pos = _parse_pos_spec(val)
        if parsed_pos is None:
            _set_disp_fb(False, t("cc_display.invalid_pos", p0=val))
            return True
        cmd = (
            ["niri", "msg", "output", out_n, "position", "auto"]
            if parsed_pos == "auto"
            else ["niri", "msg", "output", out_n, "position", "set", str(parsed_pos[0]), str(parsed_pos[1])]
        )
        ok, _o, err = probe.run_cmd_full(cmd, timeout=2.0)
        if ok:
            _invalidate_outputs_cache()
            _update_niri_output_kdl(out_n, position=parsed_pos)
            _set_disp_fb(True, t("cc_display.position_on", p0=val, p1=out_n))
        else:
            _set_disp_fb(False, err or t("cc_display.err_cmd_failed", p0=out_n))
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
