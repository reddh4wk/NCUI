#!/usr/bin/env python3
import json
import os
import re
import threading
import time
from urllib.parse import quote, unquote
from core import log, probe
from core.config import CONFIG, _update_toml_key_in_section
from core.i18n import t
from core.state import STATE
from ui.kit import set_feedback

_CACHE_TTL = 0.9
_DEV_CACHE: dict[bool, tuple[float, list[dict]]] = {}
_STREAM_CACHE: dict[str, tuple[float, list[dict]]] = {}
_META_CACHE: tuple[float, tuple[str, str]] | None = None
_LOOPBACK_CACHE: tuple[float, bool] | None = None
_EE_CACHE: tuple[float, bool] | None = None
_PW_UP_CACHE: tuple[float, bool] | None = None

_VALID_QUANTA = {"0", "32", "64", "128", "256", "512", "1024", "2048", "4096", "8192"}
_VALID_RATES = {"0", "44100", "48000", "88200", "96000", "176400", "192000"}
ALERT_SOUNDS = ("none", "bell", "complete", "message", "dialog-warning")

_VOL_LOCK = threading.Lock()
_VOL_EVENT = threading.Event()
_PENDING_VOL: dict[tuple[str, str], list[str]] = {}
_VOL_THREAD: threading.Thread | None = None
_VOL_IN_FLIGHT: bool = False
_LAST_VOL_TS: float = 0.0


def _pactl_vol_worker() -> None:
    global _VOL_IN_FLIGHT, _LAST_VOL_TS
    while True:
        _VOL_EVENT.wait()
        while True:
            with _VOL_LOCK:
                if not _PENDING_VOL:
                    _VOL_IN_FLIGHT = False
                    _VOL_EVENT.clear()
                    break
                key, vals = next(iter(_PENDING_VOL.items()))
                del _PENDING_VOL[key]
                _VOL_IN_FLIGHT = True
            cmd, target = key
            try:
                if probe.has_bin("pactl"):
                    probe.run_cmd(["pactl", cmd, target, *vals], timeout=1.5)
            finally:
                with _VOL_LOCK:
                    _LAST_VOL_TS = time.monotonic()
                    if not _PENDING_VOL:
                        _VOL_IN_FLIGHT = False


def _queue_pactl_vol(cmd: str, target: str, val: int | list[int]) -> None:
    global _VOL_THREAD, _LAST_VOL_TS
    if not probe.has_bin("pactl"):
        return
    vals = [f"{int(v)}%" for v in val] if isinstance(val, list) else [f"{int(val)}%"]
    with _VOL_LOCK:
        _PENDING_VOL[(cmd, str(target))] = vals
        _LAST_VOL_TS = time.monotonic()
        if _VOL_THREAD is None or not _VOL_THREAD.is_alive():
            _VOL_THREAD = threading.Thread(target=_pactl_vol_worker, name="nc-pactl-vol", daemon=True)
            _VOL_THREAD.start()
        _VOL_EVENT.set()


def is_sound_busy() -> bool:
    with _VOL_LOCK:
        return bool(_PENDING_VOL or _VOL_IN_FLIGHT or (time.monotonic() - _LAST_VOL_TS) < 0.45)


def parse_channel_volumes(vol_obj: dict) -> dict[str, int]:
    res: dict[str, int] = {}
    if not vol_obj or not isinstance(vol_obj, dict):
        return res
    for ch, info in vol_obj.items():
        if not isinstance(info, dict):
            continue
        try:
            raw = str(info.get("value_percent", "0%")).strip().rstrip("%")
            res[str(ch)] = max(0, int(round(float(raw))))
        except Exception:
            continue
    return res


def speaker_icon(vol: int, muted: bool) -> str:
    if muted:
        return "\uf6a9"
    if vol <= 0:
        return "\uf026"
    if vol <= 50:
        return "\uf027"
    return "\uf028"


def get_max_volume() -> int:
    try:
        mv = int(CONFIG.get("audio", {}).get("max_volume", 100))
        return mv if mv in (100, 150) else max(100, min(150, mv))
    except Exception:
        return 100


def get_alert_sound() -> str:
    val = str(CONFIG.get("audio", {}).get("alert_sound", "none")).strip()
    return val if val in ALERT_SOUNDS else "none"


def is_easyeffects_running() -> bool:
    global _EE_CACHE
    now = time.monotonic()
    if _EE_CACHE is not None and (now - _EE_CACHE[0]) < _CACHE_TTL:
        return _EE_CACHE[1]
    if not probe.has_bin("pgrep"):
        return False
    ok, _ = probe.run_cmd(["pgrep", "-x", "easyeffects"], timeout=0.8)
    _EE_CACHE = (now, bool(ok))
    return bool(ok)


def is_pipewire_running() -> bool:
    global _PW_UP_CACHE
    now = time.monotonic()
    if _PW_UP_CACHE is not None and (now - _PW_UP_CACHE[0]) < _CACHE_TTL:
        return _PW_UP_CACHE[1]
    up = probe.has_bin("pactl") and probe.run_cmd(["pactl", "info"], timeout=1.0)[0]
    _PW_UP_CACHE = (now, bool(up))
    return bool(up)


def _pactl_json(kind: str) -> list:
    if not probe.has_bin("pactl"):
        return []
    ok, out = probe.run_cmd(["pactl", "-f", "json", "list", kind], timeout=2.0)
    if not ok or not out:
        return []
    try:
        data = json.loads(out)
        return data if isinstance(data, list) else []
    except Exception as e:
        log.error(f"_pactl_json({kind}) parse error: {e}")
        return []


def get_default_sink_status() -> tuple[int, bool]:
    if not probe.has_bin("pactl"):
        return 50, False
    ok_m, out_m = probe.run_cmd(["pactl", "get-sink-mute", "@DEFAULT_SINK@"], timeout=1.0)
    muted = ok_m and "yes" in out_m.lower()
    vol = 50
    ok_v, out_v = probe.run_cmd(["pactl", "get-sink-volume", "@DEFAULT_SINK@"], timeout=1.0)
    if ok_v and out_v:
        pcts = [int(m) for m in re.findall(r"(\d+)%", out_v)]
        if pcts:
            vol = max(pcts)
    return vol, muted


def _compute_spatial(ch_vols: dict[str, int]) -> tuple[int, int]:
    bal = 0
    if "front-left" in ch_vols and "front-right" in ch_vols:
        lv, rv = ch_vols["front-left"], ch_vols["front-right"]
        mx = max(lv, rv)
        if mx > 0:
            bal = max(-100, min(100, int(round(100.0 * (rv - lv) / mx))))
    fade = 0
    front_chs = [ch_vols[c] for c in ("front-left", "front-right", "front-center") if c in ch_vols]
    rear_chs = [ch_vols[c] for c in ("rear-left", "rear-right", "side-left", "side-right") if c in ch_vols]
    if front_chs and rear_chs:
        fv, rrv = max(front_chs), max(rear_chs)
        mx_fr = max(fv, rrv)
        if mx_fr > 0:
            fade = max(-100, min(100, int(round(100.0 * (fv - rrv) / mx_fr))))
    return bal, fade


def get_audio_devices(is_input: bool) -> list[dict]:
    now = time.monotonic()
    cached = _DEV_CACHE.get(bool(is_input))
    if cached is not None and (now - cached[0]) < _CACHE_TTL:
        return [dict(d, channels=list(d["channels"]), ch_vols=dict(d["ch_vols"])) for d in cached[1]]
    raw = _pactl_json("sources" if is_input else "sinks")
    if not raw:
        return []
    def_cmd = "get-default-source" if is_input else "get-default-sink"
    _, def_name = probe.run_cmd(["pactl", def_cmd], timeout=1.5)
    def_name = def_name.strip()
    devices = []
    for s in raw:
        if not isinstance(s, dict):
            continue
        name = str(s.get("name") or "")
        if not name or (is_input and name.endswith(".monitor")):
            continue
        ch_vols = parse_channel_volumes(s.get("volume", {}))
        cmap = [c.strip() for c in str(s.get("channel_map") or "").split(",") if c.strip()]
        if not cmap:
            cmap = list(ch_vols.keys())
        bal, fade = _compute_spatial(ch_vols)
        devices.append({
            "name": name,
            "desc": str(s.get("description") or name),
            "is_in": is_input,
            "mute": bool(s.get("mute", False)),
            "vol": max(ch_vols.values()) if ch_vols else 0,
            "channels": cmap,
            "ch_vols": ch_vols,
            "balance": bal,
            "fade": fade,
            "lfe": ch_vols.get("lfe", 0),
            "is_default": name == def_name,
            "state": str(s.get("state") or "IDLE"),
            "spec": str(s.get("sample_specification") or t("common.nd")),
        })
    devices.sort(key=lambda d: not d["is_default"])
    _DEV_CACHE[bool(is_input)] = (now, [dict(d, channels=list(d["channels"]), ch_vols=dict(d["ch_vols"])) for d in devices])
    return devices


def get_audio_streams(kind: str = "sink-inputs") -> list[dict]:
    now = time.monotonic()
    cached = _STREAM_CACHE.get(kind)
    if cached is not None and (now - cached[0]) < _CACHE_TTL:
        return [dict(a) for a in cached[1]]
    raw = _pactl_json(kind)
    if not raw:
        return []
    apps = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        raw_idx = item.get("index")
        if isinstance(raw_idx, bool) or raw_idx is None:
            continue
        try:
            idx = int(raw_idx)
        except (TypeError, ValueError):
            continue
        raw_props = item.get("properties")
        props = raw_props if isinstance(raw_props, dict) else {}
        media_name = str(props.get("media.name") or "")
        if kind == "source-outputs" and media_name == "Peak detect":
            continue
        app_name = str(
            props.get("application.name")
            or props.get("application.process.binary")
            or props.get("node.name")
            or t("cc_audio.stream_fallback", p0=idx)
        )
        label = f"{app_name} - {media_name}" if (media_name and media_name.lower() not in app_name.lower() and media_name != "AudioStream") else app_name
        apps.append({
            "index": idx,
            "label": label,
            "mute": bool(item.get("mute", False)),
            "vol": max(parse_channel_volumes(item.get("volume", {})).values(), default=0),
            "corked": bool(item.get("corked", False)),
        })
    _STREAM_CACHE[kind] = (now, [dict(a) for a in apps])
    return apps


def get_sink_inputs() -> list[dict]:
    return get_audio_streams("sink-inputs")


def get_source_outputs() -> list[dict]:
    return get_audio_streams("source-outputs")


def set_default_device(is_input: bool, dev_name: str) -> bool:
    if not probe.has_bin("pactl"):
        return False
    cmd = "set-default-source" if is_input else "set-default-sink"
    get_cmd = "get-default-source" if is_input else "get-default-sink"
    _DEV_CACHE.pop(bool(is_input), None)
    ok, _o, err = probe.run_cmd_full(["pactl", cmd, dev_name], timeout=1.5)
    if not ok:
        log.error(f"set_default_device({dev_name}) failed: {err}")
        return False
    ok_rb, out_rb = probe.run_cmd(["pactl", get_cmd], timeout=1.0)
    return bool(ok_rb and out_rb.strip() == dev_name)


def toggle_device_mute(is_input: bool, dev_name: str) -> bool:
    if not probe.has_bin("pactl"):
        return False
    cmd = "set-source-mute" if is_input else "set-sink-mute"
    _DEV_CACHE.pop(bool(is_input), None)
    ok, _o, err = probe.run_cmd_full(["pactl", cmd, dev_name, "toggle"], timeout=1.5)
    if not ok:
        log.error(f"toggle_device_mute({dev_name}) failed: {err}")
    return bool(ok)


def _find_cached_device(is_input: bool, dev_name: str) -> dict | None:
    cached = _DEV_CACHE.get(bool(is_input))
    devs = cached[1] if cached is not None else get_audio_devices(is_input)
    return next((d for d in devs if d["name"] == dev_name), None)


def _commit_device_channels(is_input: bool, d: dict) -> None:
    cmd = "set-source-volume" if is_input else "set-sink-volume"
    channels = d.get("channels") or []
    ch_vols = d.get("ch_vols") or {}
    d["vol"] = max(ch_vols.values()) if ch_vols else d["vol"]
    d["balance"], d["fade"] = _compute_spatial(ch_vols)
    d["lfe"] = ch_vols.get("lfe", d.get("lfe", 0))
    cached = _DEV_CACHE.get(bool(is_input))
    if cached is not None:
        _DEV_CACHE[bool(is_input)] = (time.monotonic(), cached[1])
    if channels and ch_vols:
        _queue_pactl_vol(cmd, d["name"], [int(ch_vols.get(ch, d["vol"])) for ch in channels])
    else:
        _queue_pactl_vol(cmd, d["name"], int(d["vol"]))


def set_device_volume(is_input: bool, dev_name: str, val: int) -> None:
    max_vol = get_max_volume()
    clamped = max(0, min(max_vol, int(val)))
    d = _find_cached_device(is_input, dev_name)
    if d is None:
        _queue_pactl_vol("set-source-volume" if is_input else "set-sink-volume", dev_name, clamped)
        return
    ch_vols = d.get("ch_vols") or {}
    old_max = max(ch_vols.values()) if ch_vols else 0
    # Preserve relative L/R balance, fade, and LFE offset across master volume adjustments
    if old_max > 0 and len(set(ch_vols.values())) > 1:
        scale = clamped / float(old_max)
        for ch in list(ch_vols.keys()):
            ch_vols[ch] = max(0, min(max_vol, int(round(ch_vols[ch] * scale))))
    else:
        for ch in (d.get("channels") or list(ch_vols.keys())):
            ch_vols[ch] = clamped
        d["vol"] = clamped
    _commit_device_channels(is_input, d)


def step_device_volume(is_input: bool, dev_name: str, delta: int) -> None:
    d = _find_cached_device(is_input, dev_name)
    cur = d["vol"] if d is not None else 50
    set_device_volume(is_input, dev_name, cur + delta)


def set_sink_balance(dev_name: str, bal: int) -> None:
    bal = max(-100, min(100, int(bal)))
    d = _find_cached_device(False, dev_name)
    if d is None or not d.get("ch_vols"):
        return
    ch_vols = d["ch_vols"]
    l_fac = 1.0 if bal <= 0 else (100.0 - bal) / 100.0
    r_fac = 1.0 if bal >= 0 else (100.0 + bal) / 100.0
    for l_ch, r_ch in (("front-left", "front-right"), ("rear-left", "rear-right"), ("side-left", "side-right")):
        if l_ch in ch_vols and r_ch in ch_vols:
            base = max(ch_vols[l_ch], ch_vols[r_ch])
            ch_vols[l_ch] = int(round(base * l_fac))
            ch_vols[r_ch] = int(round(base * r_fac))
    _commit_device_channels(False, d)


def set_sink_fade(dev_name: str, fade: int) -> None:
    fade = max(-100, min(100, int(fade)))
    d = _find_cached_device(False, dev_name)
    if d is None or not d.get("ch_vols"):
        return
    ch_vols = d["ch_vols"]
    front = [c for c in ("front-left", "front-right", "front-center") if c in ch_vols]
    rear = [c for c in ("rear-left", "rear-right", "side-left", "side-right") if c in ch_vols]
    if not front or not rear:
        return
    f_cur, r_cur = max(ch_vols[c] for c in front), max(ch_vols[c] for c in rear)
    base = max(f_cur, r_cur)
    f_tgt = base * (1.0 if fade >= 0 else (100.0 + fade) / 100.0)
    r_tgt = base * (1.0 if fade <= 0 else (100.0 - fade) / 100.0)
    for c in front:
        ch_vols[c] = int(round(ch_vols[c] * (f_tgt / f_cur))) if f_cur > 0 else int(round(f_tgt))
    for c in rear:
        ch_vols[c] = int(round(ch_vols[c] * (r_tgt / r_cur))) if r_cur > 0 else int(round(r_tgt))
    _commit_device_channels(False, d)


def set_sink_lfe_volume(dev_name: str, val: int) -> None:
    clamped = max(0, min(get_max_volume(), int(val)))
    d = _find_cached_device(False, dev_name)
    if d is None or "lfe" not in d.get("ch_vols", {}):
        return
    d["ch_vols"]["lfe"] = clamped
    _commit_device_channels(False, d)


def _toggle_stream_mute(kind: str, stream_idx: int) -> bool:
    if not probe.has_bin("pactl"):
        return False
    _STREAM_CACHE.pop(kind, None)
    cmd = "set-source-output-mute" if kind == "source-outputs" else "set-sink-input-mute"
    ok, _o, err = probe.run_cmd_full(["pactl", cmd, str(int(stream_idx)), "toggle"], timeout=1.5)
    if not ok:
        log.error(f"_toggle_stream_mute({kind}, {stream_idx}) failed: {err}")
    return bool(ok)


def _set_stream_volume(kind: str, stream_idx: int, val: int) -> None:
    sid = int(stream_idx)
    clamped = max(0, min(get_max_volume(), int(val)))
    cached = _STREAM_CACHE.get(kind)
    if cached is not None:
        for a in cached[1]:
            if a["index"] == sid:
                a["vol"] = clamped
        _STREAM_CACHE[kind] = (time.monotonic(), cached[1])
    cmd = "set-source-output-volume" if kind == "source-outputs" else "set-sink-input-volume"
    _queue_pactl_vol(cmd, str(sid), clamped)


def _step_stream_volume(kind: str, stream_idx: int, delta: int) -> None:
    sid = int(stream_idx)
    cached = _STREAM_CACHE.get(kind)
    apps = cached[1] if cached is not None else get_audio_streams(kind)
    cur = next((a["vol"] for a in apps if a["index"] == sid), 50)
    _set_stream_volume(kind, sid, cur + delta)


def _get_pipewire_cards() -> list[dict]:
    res = []
    for c in _pactl_json("cards"):
        if not isinstance(c, dict):
            continue
        name = str(c.get("name") or "")
        if not name:
            continue
        raw_props = c.get("properties")
        props = raw_props if isinstance(raw_props, dict) else {}
        raw_profs = c.get("profiles")
        profs = raw_profs if isinstance(raw_profs, dict) else {}
        res.append({
            "name": name,
            "desc": str(props.get("device.description") or props.get("device.nick") or name),
            "active_profile": str(c.get("active_profile") or ""),
            "profiles": [
                {"id": str(k), "desc": str(v.get("description") or k), "avail": v.get("available", "yes") != "no"}
                for k, v in profs.items()
                if isinstance(v, dict)
            ],
        })
    return res


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


def _list_loopback_ids() -> list[str]:
    if not probe.has_bin("pactl"):
        return []
    ok, out = probe.run_cmd(["pactl", "list", "short", "modules"], timeout=1.0)
    if not ok or not out:
        return []
    ids = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and parts[1].strip() == "module-loopback":
            ids.append(parts[0].strip())
    return ids


def _get_loopback_active() -> bool:
    global _LOOPBACK_CACHE
    now = time.monotonic()
    if _LOOPBACK_CACHE is not None and (now - _LOOPBACK_CACHE[0]) < _CACHE_TTL:
        return _LOOPBACK_CACHE[1]
    tracked = str(STATE.get("sound", "loopback_mod_id", "") or "").strip()
    ids = _list_loopback_ids()
    active = (tracked in ids) if tracked else bool(ids)
    _LOOPBACK_CACHE = (now, active)
    return active


def channel_short_label(ch: str) -> str:
    key_map = {
        "front-left": "cc_audio.ch_front_left",
        "front-right": "cc_audio.ch_front_right",
        "front-center": "cc_audio.ch_front_center",
        "lfe": "cc_audio.ch_lfe",
        "rear-left": "cc_audio.ch_rear_left",
        "rear-right": "cc_audio.ch_rear_right",
        "side-left": "cc_audio.ch_side_left",
        "side-right": "cc_audio.ch_side_right",
        "mono": "cc_audio.ch_mono",
    }
    return t(key_map[ch]) if ch in key_map else ch


def _stop_speaker_test() -> None:
    if probe.has_bin("pkill"):
        probe.run_cmd(["pkill", "-x", "speaker-test"], timeout=0.5)


_PW_META_MAP = {
    "pw_q": ("clock.force-quantum", _VALID_QUANTA, "cc_audio.pw_quantum_set", "cc_audio.invalid_quantum", False),
    "pw_r": ("clock.force-rate", _VALID_RATES, "cc_audio.pw_rate_set", "cc_audio.invalid_rate", True),
}


def handle_cc_audio_link(cc_panel, uri: str) -> bool:
    global _META_CACHE, _LOOPBACK_CACHE, _EE_CACHE
    is_cc = uri.startswith("cc_aud:")
    if is_cc:
        act = uri[len("cc_aud:"):]
    elif uri.startswith("aud:"):
        act = uri[len("aud:"):]
    else:
        return False

    def _fb(ok: bool, msg: str) -> None:
        if is_cc:
            set_feedback("sound", ok, msg)

    throttled = False
    if act.startswith("max_vol:"):
        raw_mv = act.split(":", 1)[1]
        try:
            mv = int(raw_mv)
            if mv not in (100, 150):
                raise ValueError
        except ValueError:
            _fb(False, t("cc_audio.invalid_max_volume", p0=raw_mv))
        else:
            CONFIG.setdefault("audio", {})["max_volume"] = mv
            _update_toml_key_in_section("audio", "max_volume", str(mv))
            _fb(True, t("cc_audio.max_volume_limit_set_to", p0=mv))
    elif act == "test_ch" or act.startswith("test_pos:"):
        if not probe.has_bin("speaker-test"):
            _fb(False, t("cc_audio.speaker_test_missing"))
        else:
            devs = get_audio_devices(False)
            def_sink = next((d for d in devs if d.get("is_default")), devs[0] if devs else None)
            if not def_sink:
                _fb(False, t("audio.no_output_detected"))
            else:
                chs = def_sink.get("channels") or []
                n_ch = max(1, min(8, len(chs))) if chs else 2
                _stop_speaker_test()
                if act == "test_ch":
                    probe.spawn_bg(["speaker-test", "-t", "wav", "-c", str(n_ch), "-l", "1"])
                    _fb(True, t("cc_audio.playing_ch_test_signal", p0=n_ch))
                else:
                    raw_idx = act.split(":", 1)[1]
                    try:
                        ch_idx = max(1, min(n_ch, int(raw_idx)))
                    except ValueError:
                        ch_idx = 1
                    ch_lbl = channel_short_label(chs[ch_idx - 1]) if ch_idx <= len(chs) else str(ch_idx)
                    probe.spawn_bg(["speaker-test", "-t", "wav", "-c", str(n_ch), "-s", str(ch_idx), "-l", "1"])
                    _fb(True, t("cc_audio.playing_pos_test_signal", p0=ch_lbl))
    elif act.startswith("alert:"):
        sid = act.split(":", 1)[1]
        if sid not in ALERT_SOUNDS:
            _fb(False, t("cc_audio.invalid_alert_sound", p0=sid))
        else:
            CONFIG.setdefault("audio", {})["alert_sound"] = sid
            _update_toml_key_in_section("audio", "alert_sound", json.dumps(sid))
            snd_path = f"/usr/share/sounds/freedesktop/stereo/{sid}.oga"
            if sid != "none" and probe.has_bin("pw-play") and os.path.exists(snd_path):
                probe.spawn_bg(["pw-play", snd_path])
                _fb(True, t("cc_audio.alert_sound_set_to", p0=sid))
            elif sid != "none":
                _fb(True, t("cc_audio.alert_preview_missing", p0=sid))
            else:
                _fb(True, t("cc_audio.alert_sound_set_to", p0=sid))
    elif act == "spawn_ee":
        _EE_CACHE = None
        if not probe.has_bin("easyeffects"):
            _fb(False, t("cc_audio.easyeffects_missing"))
        else:
            probe.spawn_bg(["easyeffects"])
            _fb(True, t("cc_audio.easyeffects_launched"))
    elif act == "toggle_loopback":
        _LOOPBACK_CACHE = None
        if not probe.has_bin("pactl"):
            _fb(False, t("cc_audio.pactl_missing"))
        elif _get_loopback_active():
            tracked = str(STATE.get("sound", "loopback_mod_id", "") or "").strip()
            ids = _list_loopback_ids()
            targets = [tracked] if (tracked and tracked in ids) else ids
            err_msg = ""
            for mid in targets:
                ok_u, _o, err_u = probe.run_cmd_full(["pactl", "unload-module", mid], timeout=1.5)
                if not ok_u and not err_msg:
                    err_msg = err_u or t("common.err")
            _LOOPBACK_CACHE = None
            rem_ids = _list_loopback_ids()
            still_active = (tracked in rem_ids) if (tracked and tracked in targets) else bool(rem_ids)
            if not err_msg and not still_active:
                STATE.set("sound", "loopback_mod_id", "")
                _fb(True, t("cc_audio.mic_loopback_disabled"))
            else:
                _fb(False, t("cc_audio.loopback_failed", p0=err_msg or t("common.err")))
        else:
            args = ["pactl", "load-module", "module-loopback", "latency_msec=20"]
            in_devs = get_audio_devices(True)
            out_devs = get_audio_devices(False)
            def_in = next((d["name"] for d in in_devs if d.get("is_default")), "")
            def_out = next((d["name"] for d in out_devs if d.get("is_default")), "")
            if def_in:
                args.append(f"source={def_in}")
            if def_out:
                args.append(f"sink={def_out}")
            ok, out, err = probe.run_cmd_full(args, timeout=1.5)
            _LOOPBACK_CACHE = None
            mod_id = out.strip()
            if ok and (mod_id or _list_loopback_ids()):
                STATE.set("sound", "loopback_mod_id", mod_id)
                _fb(True, t("cc_audio.mic_loopback_active"))
            else:
                _fb(False, t("cc_audio.loopback_failed", p0=err or t("common.err")))
        _LOOPBACK_CACHE = None
    elif act.startswith(("pw_q:", "pw_r:")):
        _META_CACHE = None
        prefix, val = act.split(":", 1)
        meta_key, valid_set, ok_key, err_key, is_rate = _PW_META_MAP[prefix]
        if val not in valid_set:
            _fb(False, t(err_key, p0=val))
        elif not probe.has_bin("pw-metadata"):
            _fb(False, t("cc_audio.pw_metadata_missing"))
        else:
            ok, _o, err = probe.run_cmd_full(["pw-metadata", "-n", "settings", "0", meta_key, val], timeout=1.5)
            if ok:
                _META_CACHE = None
                q_cur, r_cur = _get_pw_clock_meta()
                actual = r_cur if is_rate else q_cur
                expected = "auto" if val == "0" else val
                if actual == expected:
                    disp = t("cc_audio.auto") if val == "0" else (f"{val} Hz" if is_rate else val)
                    _fb(True, t(ok_key, p0=disp))
                else:
                    _fb(False, t("cc_audio.pw_metadata_failed", p0=f"{meta_key}={actual} != {expected}"))
            else:
                _fb(False, t("cc_audio.pw_metadata_failed", p0=err or t("common.err")))
    elif act.startswith("card_prof:"):
        parts = act.split(":", 2)
        if len(parts) == 3:
            card, prof = unquote(parts[1]), unquote(parts[2])
            if not probe.has_bin("pactl"):
                _fb(False, t("cc_audio.pactl_missing"))
            else:
                ok, _o, err = probe.run_cmd_full(["pactl", "set-card-profile", card, prof], timeout=2.0)
                if ok:
                    _DEV_CACHE.clear()
                    _STREAM_CACHE.clear()
                    matched = any(c["name"] == card and c["active_profile"] == prof for c in _get_pipewire_cards())
                    _fb(matched, t("cc_audio.profile_active", p0=prof) if matched else t("cc_audio.profile_switch_failed", p0=prof))
                else:
                    _fb(False, t("cc_audio.profile_switch_failed", p0=err or t("common.err")))
    elif act.startswith(("out:", "in:", "app:", "rec:")):
        parts = act.split(":", 2)
        if len(parts) != 3:
            return True
        scope, op, rest = parts
        if scope in ("out", "in"):
            is_in = scope == "in"
            if op == "def":
                dev = unquote(rest)
                ok_def = set_default_device(is_in, dev)
                if ok_def:
                    _fb(True, t("cc_audio.default_input_set" if is_in else "cc_audio.default_output_set", p0=dev))
                else:
                    err_p = t("cc_audio.pactl_missing") if not probe.has_bin("pactl") else dev
                    _fb(False, t("cc_audio.default_switch_failed", p0=err_p))
            elif op == "mute":
                dev = unquote(rest)
                if not toggle_device_mute(is_in, dev):
                    err_p = t("cc_audio.pactl_missing") if not probe.has_bin("pactl") else dev
                    _fb(False, t("cc_audio.mute_toggle_failed", p0=err_p))
            elif op in ("vol", "scroll", "bal", "bal_scroll", "fade", "fade_scroll", "lfe", "lfe_scroll"):
                if ":" not in rest:
                    return True
                q_nm, arg = rest.rsplit(":", 1)
                nm = unquote(q_nm)
                throttled = True
                try:
                    if op == "vol":
                        set_device_volume(is_in, nm, int(arg))
                    elif op == "scroll":
                        step_device_volume(is_in, nm, 5 if arg == "up" else -5)
                    elif op == "bal":
                        set_sink_balance(nm, int(arg))
                    elif op == "bal_scroll":
                        d = _find_cached_device(False, nm)
                        cur_b = d["balance"] if d else 0
                        set_sink_balance(nm, cur_b + (10 if arg == "up" else -10))
                    elif op == "fade":
                        set_sink_fade(nm, int(arg))
                    elif op == "fade_scroll":
                        d = _find_cached_device(False, nm)
                        cur_f = d["fade"] if d else 0
                        set_sink_fade(nm, cur_f + (10 if arg == "up" else -10))
                    elif op == "lfe":
                        set_sink_lfe_volume(nm, int(arg))
                    elif op == "lfe_scroll":
                        d = _find_cached_device(False, nm)
                        cur_lfe = d["lfe"] if d else 0
                        set_sink_lfe_volume(nm, cur_lfe + (5 if arg == "up" else -5))
                except ValueError:
                    return True
        else:
            kind = "source-outputs" if scope == "rec" else "sink-inputs"
            try:
                if op == "mute":
                    if not _toggle_stream_mute(kind, int(rest)):
                        err_p = t("cc_audio.pactl_missing") if not probe.has_bin("pactl") else rest
                        _fb(False, t("cc_audio.mute_toggle_failed", p0=err_p))
                elif op == "vol" and ":" in rest:
                    idx, val = rest.rsplit(":", 1)
                    _set_stream_volume(kind, int(idx), int(val))
                    throttled = True
                elif op == "scroll" and ":" in rest:
                    idx, direction = rest.rsplit(":", 1)
                    _step_stream_volume(kind, int(idx), 5 if direction == "up" else -5)
                    throttled = True
            except ValueError:
                return True

    if throttled or not getattr(cc_panel, "shell", None):
        cc_panel.request_throttled_refresh()
    else:
        cc_panel.shell.refresh_view()
    return True


def handle_cc_audio_prompt(cc_panel, action_id: str, text: str) -> bool:
    return False
