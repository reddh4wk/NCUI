#!/usr/bin/env python3
import json
import re
import threading
import time
from core import log, probe
from core.config import CONFIG, _update_toml_key_in_section
from core.i18n import get_active_language, t
from core.state import STATE
from ui.canvas import Canvas
from ui.kit import ActionBtn, Badge, Box, FeedbackBox, ProgressBar, SelectPill, Slider, StateBadge, Txt



_CACHE_TTL = 1.5
_DEV_CACHE: dict[bool, tuple[float, list[dict]]] = {}
_APP_CACHE: tuple[float, list[dict]] | None = None
_META_CACHE: tuple[float, tuple[str, str]] | None = None
_LOOPBACK_CACHE: tuple[float, bool] | None = None

_VOL_LOCK = threading.Lock()
_VOL_EVENT = threading.Event()
_PENDING_VOL: dict[tuple[str, str], int] = {}
_VOL_THREAD: threading.Thread | None = None


def _pactl_vol_worker() -> None:
    while True:
        _VOL_EVENT.wait()
        while True:
            with _VOL_LOCK:
                if not _PENDING_VOL:
                    _VOL_EVENT.clear()
                    break
                key, val = next(iter(_PENDING_VOL.items()))
                del _PENDING_VOL[key]
            cmd, target = key
            probe.run_cmd(["pactl", cmd, target, f"{val}%"], timeout=1.5)


def _queue_pactl_vol(cmd: str, target: str, val: int) -> None:
    global _VOL_THREAD
    with _VOL_LOCK:
        _PENDING_VOL[(cmd, str(target))] = int(val)
        if _VOL_THREAD is None or not _VOL_THREAD.is_alive():
            _VOL_THREAD = threading.Thread(target=_pactl_vol_worker, name="nc-pactl-vol", daemon=True)
            _VOL_THREAD.start()
        _VOL_EVENT.set()


def parse_volume_pct(vol_obj: dict) -> int:
    if not vol_obj or not isinstance(vol_obj, dict):
        return 0
    try:
        first_ch = next(iter(vol_obj.values()))
        raw = str(first_ch.get("value_percent", "0%")).strip().rstrip("%")
        return max(0, int(round(float(raw))))
    except Exception:
        return 0


def speaker_icon(vol: int, muted: bool) -> str:
    if muted:
        return "\uf6a9"
    if vol <= 0:
        return "\uf026"
    if vol <= 50:
        return "\uf027"
    return "\uf028"


def get_max_volume() -> int:
    return int(CONFIG.get("audio", {}).get("max_volume", 100))


def get_default_sink_status() -> tuple[int, bool]:
    ok_m, out_m = probe.run_cmd(["pactl", "get-sink-mute", "@DEFAULT_SINK@"], timeout=1.0)
    muted = ok_m and "yes" in out_m.lower()
    vol = 50
    ok_v, out_v = probe.run_cmd(["pactl", "get-sink-volume", "@DEFAULT_SINK@"], timeout=1.0)
    if ok_v and out_v:
        m = re.search(r"(\d+)%", out_v)
        if m:
            vol = int(m.group(1))
    return vol, muted


def get_audio_devices(is_input: bool) -> list[dict]:
    now = time.monotonic()
    cached = _DEV_CACHE.get(bool(is_input))
    if cached is not None and (now - cached[0]) < _CACHE_TTL:
        return [dict(d) for d in cached[1]]
    devices = []
    if not probe.has_bin("pactl"):
        return devices
    def_cmd = "get-default-source" if is_input else "get-default-sink"
    list_kind = "sources" if is_input else "sinks"
    _, def_name = probe.run_cmd(["pactl", def_cmd], timeout=1.5)
    def_name = def_name.strip()
    ok, out = probe.run_cmd(["pactl", "-f", "json", "list", list_kind], timeout=2.0)
    if not ok or not out:
        return devices
    try:
        for s in json.loads(out):
            name = s.get("name", "")
            if is_input and name.endswith(".monitor"):
                continue
            devices.append({
                "name": name,
                "desc": s.get("description", name),
                "is_in": is_input,
                "mute": bool(s.get("mute", False)),
                "vol": parse_volume_pct(s.get("volume", {})),
                "is_default": name == def_name,
                "state": s.get("state", "IDLE"),
                "spec": s.get("sample_specification", t("common.nd")),
            })
    except Exception as e:
        log.error(f"get_audio_devices parse error: {e}")
    devices.sort(key=lambda d: not d["is_default"])
    _DEV_CACHE[bool(is_input)] = (now, [dict(d) for d in devices])
    return devices


def get_sink_inputs() -> list[dict]:
    global _APP_CACHE
    now = time.monotonic()
    if _APP_CACHE is not None and (now - _APP_CACHE[0]) < _CACHE_TTL:
        return [dict(a) for a in _APP_CACHE[1]]
    apps = []
    if not probe.has_bin("pactl"):
        return apps
    ok, out = probe.run_cmd(["pactl", "-f", "json", "list", "sink-inputs"], timeout=2.0)
    if not ok or not out:
        return apps
    try:
        for item in json.loads(out):
            idx = item.get("index")
            if idx is None:
                continue
            props = item.get("properties", {}) or {}
            app_name = (
                props.get("application.name")
                or props.get("application.process.binary")
                or props.get("node.name")
                or f"Stream #{idx}"
            )
            media_name = props.get("media.name", "")
            label = f"{app_name} - {media_name}" if (media_name and media_name.lower() not in app_name.lower() and media_name != "AudioStream") else app_name
            apps.append({
                "index": int(idx),
                "label": label,
                "mute": bool(item.get("mute", False)),
                "vol": parse_volume_pct(item.get("volume", {})),
                "corked": bool(item.get("corked", False)),
            })
    except Exception as e:
        log.error(f"get_sink_inputs parse error: {e}")
    _APP_CACHE = (now, [dict(a) for a in apps])
    return apps


def set_default_device(is_input: bool, dev_name: str) -> None:
    cmd = "set-default-source" if is_input else "set-default-sink"
    _DEV_CACHE.pop(bool(is_input), None)
    probe.run_cmd(["pactl", cmd, dev_name], timeout=1.5)


def toggle_device_mute(is_input: bool, dev_name: str) -> None:
    cmd = "set-source-mute" if is_input else "set-sink-mute"
    _DEV_CACHE.pop(bool(is_input), None)
    probe.run_cmd(["pactl", cmd, dev_name, "toggle"], timeout=1.5)


def set_device_volume(is_input: bool, dev_name: str, val: int) -> None:
    cmd = "set-source-volume" if is_input else "set-sink-volume"
    clamped = max(0, min(get_max_volume(), int(val)))
    cached = _DEV_CACHE.get(bool(is_input))
    if cached is not None:
        for d in cached[1]:
            if d["name"] == dev_name:
                d["vol"] = clamped
        _DEV_CACHE[bool(is_input)] = (time.monotonic(), cached[1])
    _queue_pactl_vol(cmd, dev_name, clamped)


def step_device_volume(is_input: bool, dev_name: str, delta: int) -> None:
    cached = _DEV_CACHE.get(bool(is_input))
    devs = cached[1] if cached is not None else None
    cur = next((d["vol"] for d in devs if d["name"] == dev_name), None) if devs is not None else None
    if cur is None:
        devs = get_audio_devices(is_input)
        cur = next((d["vol"] for d in devs if d["name"] == dev_name), 50)
    set_device_volume(is_input, dev_name, cur + delta)


def toggle_app_mute(stream_idx: int) -> None:
    global _APP_CACHE
    _APP_CACHE = None
    probe.run_cmd(["pactl", "set-sink-input-mute", str(stream_idx), "toggle"], timeout=1.5)


def set_app_volume(stream_idx: int, val: int) -> None:
    global _APP_CACHE
    clamped = max(0, min(get_max_volume(), int(val)))
    if _APP_CACHE is not None:
        for a in _APP_CACHE[1]:
            if a["index"] == int(stream_idx):
                a["vol"] = clamped
        _APP_CACHE = (time.monotonic(), _APP_CACHE[1])
    _queue_pactl_vol("set-sink-input-volume", str(stream_idx), clamped)


def step_app_volume(stream_idx: int, delta: int) -> None:
    sid = int(stream_idx)
    apps = _APP_CACHE[1] if _APP_CACHE is not None else None
    cur = next((a["vol"] for a in apps if a["index"] == sid), None) if apps is not None else None
    if cur is None:
        apps = get_sink_inputs()
        cur = next((a["vol"] for a in apps if a["index"] == sid), 50)
    set_app_volume(sid, cur + delta)


def _set_aud_fb(ok: bool, msg: str) -> None:
    STATE.set("sound", "cc_fb", {"ok": bool(ok), "msg": str(msg), "ts": time.time()})


def _get_aud_fb() -> dict:
    fb = STATE.get("sound", "cc_fb", {})
    if isinstance(fb, dict) and fb.get("msg") and (time.time() - float(fb.get("ts", 0))) < 15.0:
        return fb
    return {}


def _feedback_cards() -> list:
    fb = _get_aud_fb()
    if not fb:
        return []
    return [FeedbackBox(t("cc_audio.pipewire_audio_status"), bool(fb["ok"]), fb["msg"])]


def _get_pipewire_cards() -> list[dict]:
    if not probe.has_bin("pactl"):
        return []
    ok, out = probe.run_cmd(["pactl", "-f", "json", "list", "cards"], timeout=2.0)
    if not ok or not out:
        return []
    try:
        res = []
        for c in json.loads(out):
            props = c.get("properties", {}) or {}
            profs = c.get("profiles", {}) or {}
            res.append({
                "name": c.get("name", ""),
                "desc": props.get("device.description") or props.get("device.nick") or c.get("name", ""),
                "active_profile": c.get("active_profile", ""),
                "profiles": [
                    {"id": k, "desc": v.get("description", k), "avail": v.get("available", "yes") != "no"}
                    for k, v in profs.items()
                ],
            })
        return res
    except Exception:
        return []


def _get_pw_clock_meta() -> tuple[str, str]:
    global _META_CACHE
    now = time.monotonic()
    if _META_CACHE is not None and (now - _META_CACHE[0]) < _CACHE_TTL:
        return _META_CACHE[1]
    quantum, rate = "auto", "auto"
    if probe.has_bin("pw-metadata"):
        ok, out = probe.run_cmd(["pw-metadata", "-n", "settings"], timeout=1.0)
        if ok and out:
            mq = re.search(r"key:'clock\.force-quantum'\s+value:'([^']+)'", out)
            mr = re.search(r"key:'clock\.force-rate'\s+value:'([^']+)'", out)
            if mq and mq.group(1) != "0":
                quantum = mq.group(1)
            if mr and mr.group(1) != "0":
                rate = mr.group(1)
    _META_CACHE = (now, (quantum, rate))
    return quantum, rate


def _get_loopback_active() -> bool:
    global _LOOPBACK_CACHE
    now = time.monotonic()
    if _LOOPBACK_CACHE is not None and (now - _LOOPBACK_CACHE[0]) < _CACHE_TTL:
        return _LOOPBACK_CACHE[1]
    ok, out = probe.run_cmd(["pactl", "list", "short", "modules"], timeout=1.0)
    active = bool(ok and "module-loopback" in out)
    _LOOPBACK_CACHE = (now, active)
    return active


def handle_cc_audio_link(cc_panel, uri: str) -> bool:
    global _META_CACHE, _LOOPBACK_CACHE
    if uri.startswith("cc_aud:"):
        act = uri[len("cc_aud:"):]
    elif uri.startswith("aud:"):
        act = uri[len("aud:"):]
    else:
        return False

    if act.startswith("max_vol:"):
        mv = int(act.split(":", 1)[1])
        CONFIG.setdefault("audio", {})["max_volume"] = mv
        _update_toml_key_in_section("audio", "max_volume", str(mv))
        _set_aud_fb(True, t("cc_audio.max_volume_limit_set_to", p0=mv))
    elif act == "test_ch":
        probe.spawn_bg(["speaker-test", "-t", "wav", "-c", "2", "-l", "1"])
        _set_aud_fb(True, t("cc_audio.playing_l_r_test_signal"))
    elif act == "toggle_loopback":
        _LOOPBACK_CACHE = None
        if _get_loopback_active():
            probe.run_cmd_full(["pactl", "unload-module", "module-loopback"], timeout=1.5)
            _set_aud_fb(True, t("cc_audio.mic_loopback_disabled"))
        else:
            ok, _o, err = probe.run_cmd_full(["pactl", "load-module", "module-loopback", "latency_msec=1"], timeout=1.5)
            _set_aud_fb(ok, "Mic loopback active" if ok else (err or "Loopback failed"))
        _LOOPBACK_CACHE = None
    elif act.startswith("pw_q:"):
        _META_CACHE = None
        val = act.split(":", 1)[1]
        ok, _o, err = probe.run_cmd_full(["pw-metadata", "-n", "settings", "0", "clock.force-quantum", val], timeout=1.5)
        _set_aud_fb(ok, f"PipeWire quantum -> {val}" if ok else (err or "pw-metadata failed"))
    elif act.startswith("pw_r:"):
        _META_CACHE = None
        val = act.split(":", 1)[1]
        ok, _o, err = probe.run_cmd_full(["pw-metadata", "-n", "settings", "0", "clock.force-rate", val], timeout=1.5)
        _set_aud_fb(ok, f"PipeWire rate -> {val} Hz" if ok else (err or "pw-metadata failed"))
    elif act.startswith("card_prof:"):
        _, card, prof = act.split(":", 2)
        ok, _o, err = probe.run_cmd_full(["pactl", "set-card-profile", card, prof], timeout=2.0)
        _set_aud_fb(ok, f"Profile '{prof}' active" if ok else (err or "Profile switch failed"))
    elif act.startswith(("out:", "in:", "app:")):
        scope, op, rest = act.split(":", 2)
        if scope in ("out", "in"):
            is_in = scope == "in"
            if op == "def":
                set_default_device(is_in, rest)
            elif op == "mute":
                toggle_device_mute(is_in, rest)
            elif op == "vol":
                nm, val = rest.rsplit(":", 1)
                set_device_volume(is_in, nm, int(val))
            elif op == "scroll":
                nm, direction = rest.rsplit(":", 1)
                step_device_volume(is_in, nm, 5 if direction == "up" else -5)
        else:
            if op == "mute":
                toggle_app_mute(int(rest))
            elif op == "vol":
                idx, val = rest.rsplit(":", 1)
                set_app_volume(int(idx), int(val))
            elif op == "scroll":
                idx, direction = rest.rsplit(":", 1)
                step_app_volume(int(idx), 5 if direction == "up" else -5)

    if any(k in act for k in (":vol:", ":scroll:")):
        cc_panel.request_throttled_refresh()
    elif getattr(cc_panel, "shell", None):
        cc_panel.shell.refresh_view()
    else:
        cc_panel.request_throttled_refresh()
    return True


def handle_cc_audio_prompt(cc_panel, action_id: str, text: str) -> bool:
    return False
