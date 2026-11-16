#!/usr/bin/env python3
import json
import re
from core.config import CONFIG
from core.i18n import t
from core import probe, log


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
    muted = (ok_m and "yes" in out_m.lower())
    vol = 50
    ok_v, out_v = probe.run_cmd(["pactl", "get-sink-volume", "@DEFAULT_SINK@"], timeout=1.0)
    if ok_v and out_v:
        m = re.search(r"(\d+)%", out_v)
        if m:
            vol = int(m.group(1))
    return vol, muted


def get_audio_devices(is_input: bool) -> list[dict]:
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
                "is_default": (name == def_name),
                "state": s.get("state", "IDLE"),
                "spec": s.get("sample_specification", t("common.nd")),
            })
    except Exception as e:
        log.error(f"get_audio_devices parse error: {e}")
    devices.sort(key=lambda d: not d["is_default"])
    return devices


def get_sink_inputs() -> list[dict]:
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
            if media_name and media_name.lower() not in app_name.lower() and media_name != "AudioStream":
                label = f"{app_name} - {media_name}"
            else:
                label = app_name
            apps.append({
                "index": int(idx),
                "label": label,
                "mute": bool(item.get("mute", False)),
                "vol": parse_volume_pct(item.get("volume", {})),
                "corked": bool(item.get("corked", False)),
            })
    except Exception as e:
        log.error(f"get_sink_inputs parse error: {e}")
    return apps


def set_default_device(is_input: bool, dev_name: str) -> None:
    cmd = "set-default-source" if is_input else "set-default-sink"
    probe.run_cmd(["pactl", cmd, dev_name], timeout=1.5)


def toggle_device_mute(is_input: bool, dev_name: str) -> None:
    cmd = "set-source-mute" if is_input else "set-sink-mute"
    probe.run_cmd(["pactl", cmd, dev_name, "toggle"], timeout=1.5)


def set_device_volume(is_input: bool, dev_name: str, val: int) -> None:
    cmd = "set-source-volume" if is_input else "set-sink-volume"
    clamped = max(0, min(get_max_volume(), int(val)))
    probe.run_cmd(["pactl", cmd, dev_name, f"{clamped}%"], timeout=1.5)


def step_device_volume(is_input: bool, dev_name: str, delta: int) -> None:
    devs = get_audio_devices(is_input)
    cur = next((d["vol"] for d in devs if d["name"] == dev_name), 50)
    set_device_volume(is_input, dev_name, cur + delta)


def toggle_app_mute(stream_idx: int) -> None:
    probe.run_cmd(["pactl", "set-sink-input-mute", str(stream_idx), "toggle"], timeout=1.5)


def set_app_volume(stream_idx: int, val: int) -> None:
    clamped = max(0, min(get_max_volume(), int(val)))
    probe.run_cmd(["pactl", "set-sink-input-volume", str(stream_idx), f"{clamped}%"], timeout=1.5)


def step_app_volume(stream_idx: int, delta: int) -> None:
    apps = get_sink_inputs()
    cur = next((a["vol"] for a in apps if a["index"] == int(stream_idx)), 50)
    set_app_volume(int(stream_idx), cur + delta)
