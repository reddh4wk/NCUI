#!/usr/bin/env python3
import json
import re
import time
from core import log, probe
from core.config import CONFIG, _update_toml_key_in_section, get_cc_panel_style
from core.i18n import get_active_language, t
from core.state import STATE
from ui.canvas import Canvas
from ui.kit import ActionBtn, Badge, Box, ProgressBar, SelectPill, Slider, StateBadge, Txt


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


# =====================================================================
# CONTROL CENTER AUDIO BACKEND & DUAL VIEW
# =====================================================================

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
    b = Box("PipeWire Audio Status", icon="\uf00c" if fb["ok"] else "\uf071")
    b.add_row(" ")
    b.add_row(f"  {Txt(' ' + fb['msg'] + ' ', role='vitality' if fb['ok'] else 'fault', bg=True)}")
    b.add_row(" ")
    return [b]


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
    return quantum, rate


def _get_loopback_active() -> bool:
    ok, out = probe.run_cmd(["pactl", "list", "short", "modules"], timeout=1.0)
    return bool(ok and "module-loopback" in out)


def _build_devices_tab_standard(cc_panel, canvas: Canvas, is_it: bool) -> list:
    cards_hw = _get_pipewire_cards()
    out_cards = _feedback_cards()
    if not cards_hw:
        b = Box("Schede Audio Hardware (PipeWire / Pulse)" if is_it else "Audio Hardware Profiles (PipeWire)", icon="\uf2db")
        b.add_row(" ")
        b.add_row(f"  {Txt('No audio cards detected via pactl.' if not is_it else 'Nessuna scheda audio rilevata.', role='inactive')}")
        b.add_row(" ")
        return out_cards + [b]

    for c in cards_hw:
        cb = Box(c["desc"][:48], icon="\uf028", actions=[Badge(c["active_profile"][:22] or "OFF", style="info")], elected=True)
        cb.add_row(" ")
        cb.add_kv("Card Node", Txt(c["name"], role="schema"))
        avail_profs = [p for p in c["profiles"] if p["avail"] or p["id"] == c["active_profile"]]
        for p in avail_profs[:5]:
            is_act = p["id"] == c["active_profile"]
            p_id = p["id"]
            c_name = c["name"]
            cb.add_row(
                f"  {SelectPill(p['desc'][:44], f'cc_aud:card_prof:{c_name}:{p_id}', selected=is_act)}",
            )
        if len(avail_profs) > 5:
            def _more(bx: Box, rem=avail_profs[5:], cn=c["name"], ap=c["active_profile"]):
                for rp in rem:
                    rp_id = rp["id"]
                    bx.add_row(f"  {SelectPill(rp['desc'][:44], f'cc_aud:card_prof:{cn}:{rp_id}', selected=(rp_id == ap))}")
            cb.add_collapsible(cc_panel, f"aud_profs_{c['name']}", f"Altri Profili ({len(avail_profs) - 5})", _more)
        cb.add_row(" ")
        out_cards.append(cb)
    return out_cards


def _build_devices_tab_cyberpunk(cc_panel, canvas: Canvas, is_it: bool) -> list:
    cards_hw = _get_pipewire_cards()
    out_cards = _feedback_cards()
    b = Box("ALSA / BLUEZ HARDWARE CARD MATRIX", icon="\uf2db", elected=True)
    b.add_row(" ")
    for c in cards_hw:
        b.add_row(f"  {Txt('[CARD]──►', role='intent')} {Txt(c['desc'][:42], role='focus')}  {Badge(c['active_profile'][:20], style='accent')}")
        avail = [p for p in c["profiles"] if p["avail"] or p["id"] == c["active_profile"]]
        for p in avail[:4]:
            p_id = p["id"]
            c_name = c["name"]
            b.add_row(f"     {Txt('┣━', role='structure')} {SelectPill(p['desc'][:46], f'cc_aud:card_prof:{c_name}:{p_id}', selected=(p_id == c['active_profile']))}")
    if not cards_hw:
        b.add_row(f"  {Txt('[ NO HARDWARE CARDS DETECTED ]', role='inactive')}")
    b.add_row(" ")
    out_cards.append(b)
    return out_cards


def _build_mixer_standard(cc_panel, canvas: Canvas, is_it: bool) -> list:
    apps = get_sink_inputs()
    max_vol = get_max_volume()
    quantum, rate = _get_pw_clock_meta()
    cards = _feedback_cards()

    pw_box = Box(
        "Parametri Engine PipeWire (Quantum / Sample Rate)" if is_it else "PipeWire DSP Clock & Quantum (Low-Latency)",
        icon="\uf1de",
        actions=[Badge(f"Q:{quantum} R:{rate}", style="info")],
        elected=True,
    )
    pw_box.add_row(" ")
    q_btns = " ".join(SelectPill("Auto" if q == "0" else q, f"cc_aud:pw_q:{q}", selected=(quantum == ("auto" if q == "0" else q))) for q in ("0", "64", "128", "256", "512", "1024"))
    r_btns = " ".join(SelectPill("Auto" if r == "0" else f"{int(r)//1000}k", f"cc_aud:pw_r:{r}", selected=(rate == ("auto" if r == "0" else r))) for r in ("0", "44100", "48000", "96000", "192000"))
    pw_box.add_kv("Clock Quantum", q_btns, requires=["pw-metadata"])
    pw_box.add_kv("Sample Rate", r_btns, requires=["pw-metadata"])
    pw_box.add_row(" ")
    cards.append(pw_box)

    app_box = Box(f"Application Stream Mixer ({len(apps)})", icon="\uf001")
    app_box.add_row(" ")
    if not apps:
        app_box.add_row(f"  {Txt(t('audio.no_active_apps'), role='inactive')}")
    else:
        for a in apps:
            sl = Slider(a["vol"], f"cc_aud:app:vol:{a['index']}", min_val=0, max_val=max_vol, width=20, show_pct=True, step_buttons=True, step=5)
            mb = ActionBtn("Unmute" if a["mute"] else "Mute", f"cc_aud:app:mute:{a['index']}", tone="warn" if a["mute"] else "neutral", active=a["mute"])
            app_box.add_kv(canvas.truncate_plain(a["label"], 18), sl, right_markup=mb)
    app_box.add_row(" ")
    cards.append(app_box)
    return cards


def _build_mixer_cyberpunk(cc_panel, canvas: Canvas, is_it: bool) -> list:
    apps = get_sink_inputs()
    max_vol = get_max_volume()
    quantum, rate = _get_pw_clock_meta()
    cards = _feedback_cards()

    b = Box("PIPEWIRE DSP PATCHBAY // QUANTUM CLOCK & STREAM FADER", icon="\uf1de", elected=True)
    b.add_row(" ")
    q_btns = " ".join(SelectPill("AUTO" if q == "0" else q, f"cc_aud:pw_q:{q}", selected=(quantum == ("auto" if q == "0" else q))) for q in ("0", "64", "128", "256", "512"))
    r_btns = " ".join(SelectPill("AUTO" if r == "0" else f"{int(r)//1000}k", f"cc_aud:pw_r:{r}", selected=(rate == ("auto" if r == "0" else r))) for r in ("0", "48000", "96000", "192000"))
    b.add_row(f"  {Txt('[CLOCK QUANTUM]─►', role='intent')} {q_btns}")
    b.add_row(f"  {Txt('[SAMPLE RATE]  ─►', role='schema')} {r_btns}")
    b.add_section(f"ACTIVE STREAM BUS ({len(apps)})")
    if not apps:
        b.add_row(f"  {Txt('[ NO ACTIVE AUDIO STREAMS ]', role='inactive')}")
    for a in apps:
        sl = Slider(a["vol"], f"cc_aud:app:vol:{a['index']}", min_val=0, max_val=max_vol, width=18, show_pct=True)
        b.add_row(f"  {Txt('┣━', role='structure')} {Txt(canvas.truncate_plain(a['label'], 16), role='focus')} {sl}",
                  right_markup=ActionBtn("MUTE", f"cc_aud:app:mute:{a['index']}", tone="warn" if a["mute"] else "neutral", active=a["mute"]))
    b.add_row(" ")
    cards.append(b)
    return cards


def _build_io_standard(cc_panel, canvas: Canvas, is_input: bool, is_it: bool) -> list:
    devs = get_audio_devices(is_input=is_input)
    max_vol = get_max_volume()
    scope = "in" if is_input else "out"
    cards = _feedback_cards()

    top_box = Box(
        ("Opzioni Microfono & Loopback" if is_it else "Input Loopback & Noise Filter") if is_input else ("Opzioni Uscita & Test Canali" if is_it else "Output Amplification & Channel Test"),
        icon="\uf130" if is_input else "\uf028",
        elected=True,
    )
    top_box.add_row(" ")
    if not is_input:
        over_on = max_vol > 100
        top_box.add_kv("Over-Amplification", f"{SelectPill('100% (Safe)', 'cc_aud:max_vol:100', selected=not over_on)} {SelectPill('150% (Boost)', 'cc_aud:max_vol:150', selected=over_on)}")
        top_box.add_kv("Speaker Test", ActionBtn("Test L/R Channels", "cc_aud:test_ch", tone="select"), requires=["speaker-test"])
    else:
        lb_on = _get_loopback_active()
        has_ee = probe.has_bin("easyeffects")
        top_box.add_kv("Mic Loopback Test", SelectPill("LOOPBACK ON" if lb_on else "LOOPBACK OFF", "cc_aud:toggle_loopback", selected=lb_on))
        top_box.add_kv("Noise Filter", StateBadge(has_ee, "EasyEffects Ready", "Standard PipeWire") if has_ee else Txt("PipeWire Built-in", role="schema"))
    top_box.add_row(" ")
    cards.append(top_box)

    for d in devs:
        sl = Slider(d["vol"], f"cc_aud:{scope}:vol:{d['name']}", min_val=0, max_vol=max_vol if False else max_vol, width=22, show_pct=True, step_buttons=True, step=5) if False else Slider(d["vol"], f"cc_aud:{scope}:vol:{d['name']}", min_val=0, max_val=max_vol, width=22, show_pct=True, step_buttons=True, step=5)
        def_b = SelectPill("Default", f"cc_aud:{scope}:def:{d['name']}", selected=d["is_default"])
        mut_b = ActionBtn("Unmute" if d["mute"] else "Mute", f"cc_aud:{scope}:mute:{d['name']}", tone="warn" if d["mute"] else "neutral", active=d["mute"])
        box = Box(canvas.truncate_plain(d["desc"], 44), icon="\uf130" if is_input else speaker_icon(d["vol"], d["mute"]), actions=[def_b, mut_b], elected=d["is_default"])
        box.add_row(" ")
        box.add_kv("Volume", sl)
        box.add_kv("State & Spec", f"{StateBadge(not d['mute'], d['state'], 'MUTE')}  {Txt(d['spec'], role='schema')}")
        box.add_row(" ")
        cards.append(box)
    if not devs:
        eb = Box("No Device", icon="\uf6a9")
        eb.add_row(" ")
        eb.add_row(f"  {Txt(t('audio.no_input_detected') if is_input else t('audio.no_output_detected'), role='inactive')}")
        eb.add_row(" ")
        cards.append(eb)
    return cards


def _build_io_cyberpunk(cc_panel, canvas: Canvas, is_input: bool, is_it: bool) -> list:
    devs = get_audio_devices(is_input=is_input)
    max_vol = get_max_volume()
    scope = "in" if is_input else "out"
    cards = _feedback_cards()

    b = Box(
        "MIC ADC PREAMP & LOOPBACK ROUTER" if is_input else "DAC OUTPUT BUS & AMPLIFIER STAGE",
        icon="\uf130" if is_input else "\uf028",
        actions=[ActionBtn("TEST L/R", "cc_aud:test_ch", tone="select")] if not is_input else [SelectPill("LOOPBACK", "cc_aud:toggle_loopback", selected=_get_loopback_active())],
        elected=True,
    )
    b.add_row(" ")
    if not is_input:
        over_on = max_vol > 100
        b.add_row(f"  {Txt('[GAIN LIMITER]─►', role='schema')} {SelectPill('100%', 'cc_aud:max_vol:100', selected=not over_on)} {SelectPill('150% OVERDRIVE', 'cc_aud:max_vol:150', selected=over_on)}")
    for d in devs:
        sl = Slider(d["vol"], f"cc_aud:{scope}:vol:{d['name']}", min_val=0, max_val=max_vol, width=18, show_pct=True)
        b.add_section(canvas.truncate_plain(d["desc"], 44), actions=[SelectPill("ELECTED" if d["is_default"] else "SELECT", f"cc_aud:{scope}:def:{d['name']}", selected=d["is_default"])])
        b.add_row(
            f"  {Txt('[SIGNAL]─►', role='vitality' if not d['mute'] else 'fault')} {sl}  {Txt(d['spec'], role='schema')}",
            right_markup=ActionBtn("UNMUTE" if d["mute"] else "MUTE", f"cc_aud:{scope}:mute:{d['name']}", tone="warn" if d["mute"] else "neutral", active=d["mute"]),
        )
    if not devs:
        b.add_row(f"  {Txt('[ NO PIPEWIRE NODES FOUND ]', role='inactive')}")
    b.add_row(" ")
    cards.append(b)
    return cards


def build_cc_audio_cards(cc_panel, canvas: Canvas, active_sub: str = "output") -> list:
    is_it = (get_active_language() == "it")
    cyber = get_cc_panel_style() == "cyberpunk"
    if active_sub == "input":
        return _build_io_cyberpunk(cc_panel, canvas, True, is_it) if cyber else _build_io_standard(cc_panel, canvas, True, is_it)
    if active_sub == "mixer":
        return _build_mixer_cyberpunk(cc_panel, canvas, is_it) if cyber else _build_mixer_standard(cc_panel, canvas, is_it)
    if active_sub == "devices":
        return _build_devices_tab_cyberpunk(cc_panel, canvas, is_it) if cyber else _build_devices_tab_standard(cc_panel, canvas, is_it)
    return _build_io_cyberpunk(cc_panel, canvas, False, is_it) if cyber else _build_io_standard(cc_panel, canvas, False, is_it)


def handle_cc_audio_link(cc_panel, uri: str) -> bool:
    if not uri.startswith("cc_aud:"):
        return False
    act = uri[len("cc_aud:"):]

    if act.startswith("max_vol:"):
        mv = int(act.split(":", 1)[1])
        CONFIG.setdefault("audio", {})["max_volume"] = mv
        _update_toml_key_in_section("audio", "max_volume", str(mv))
        _set_aud_fb(True, f"Max volume limit set to {mv}%")
    elif act == "test_ch":
        probe.spawn_bg(["speaker-test", "-t", "wav", "-c", "2", "-l", "1"])
        _set_aud_fb(True, "Playing L/R test signal")
    elif act == "toggle_loopback":
        if _get_loopback_active():
            probe.run_cmd_full(["pactl", "unload-module", "module-loopback"], timeout=1.5)
            _set_aud_fb(True, "Mic loopback disabled")
        else:
            ok, _o, err = probe.run_cmd_full(["pactl", "load-module", "module-loopback", "latency_msec=1"], timeout=1.5)
            _set_aud_fb(ok, "Mic loopback active" if ok else (err or "Loopback failed"))
    elif act.startswith("pw_q:"):
        val = act.split(":", 1)[1]
        ok, _o, err = probe.run_cmd_full(["pw-metadata", "-n", "settings", "0", "clock.force-quantum", val], timeout=1.5)
        _set_aud_fb(ok, f"PipeWire quantum -> {val}" if ok else (err or "pw-metadata failed"))
    elif act.startswith("pw_r:"):
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
        else:
            if op == "mute":
                toggle_app_mute(int(rest))
            elif op == "vol":
                idx, val = rest.rsplit(":", 1)
                set_app_volume(int(idx), int(val))

    if cc_panel.shell:
        cc_panel.shell.refresh_view()
    return True


def handle_cc_audio_prompt(cc_panel, action_id: str, text: str) -> bool:
    return False
