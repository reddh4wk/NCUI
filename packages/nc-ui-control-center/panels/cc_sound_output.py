#!/usr/bin/env python3
from urllib.parse import quote
from core import probe
from core.config import get_hide_unavailable_settings
from core.i18n import t
from ui.canvas import Canvas
from ui.kit import ActionBtn, Badge, Box, SelectPill, Slider, StateBadge, Txt, feedback_cards
from panels.cc_engine_sound import (
    ALERT_SOUNDS,
    speaker_icon,
    get_max_volume,
    get_alert_sound,
    get_audio_devices,
    is_easyeffects_running,
    is_pipewire_running,
    channel_short_label,
    _get_loopback_active,
)


def _format_bal_label(bal: int) -> str:
    if bal == 0:
        return t("cc_audio.bal_center")
    if bal < 0:
        return t("cc_audio.bal_left", p0=abs(bal))
    return t("cc_audio.bal_right", p0=bal)


def _format_fade_label(fade: int) -> str:
    if fade == 0:
        return t("cc_audio.fade_center")
    if fade > 0:
        return t("cc_audio.fade_front", p0=fade)
    return t("cc_audio.fade_rear", p0=abs(fade))


def _alert_label(sid: str) -> str:
    key_map = {
        "none": "cc_audio.alert_none",
        "bell": "cc_audio.alert_bell",
        "complete": "cc_audio.alert_complete",
        "message": "cc_audio.alert_message",
        "dialog-warning": "cc_audio.alert_warning",
    }
    return t(key_map.get(sid, "cc_audio.alert_none"))


def _build_io_standard(cc_panel, canvas: Canvas, is_input: bool) -> list:
    devs = get_audio_devices(is_input=is_input)
    max_vol = get_max_volume()
    vol_w = 24 if max_vol > 100 else 20
    scope = "in" if is_input else "out"
    cards = feedback_cards("sound", "cc_audio.pipewire_audio_status")

    top_box = Box(
        t("cc_audio.input_loopback_noise_filter") if is_input else t("cc_audio.output_amplification_channel_test"),
        icon="\uf130" if is_input else "\uf028",
        highlighted=False,
    )
    if not is_input:
        top_box.add_kv(
            t("cc_audio.over_amplification"),
            " ".join([
                SelectPill(t("cc_audio.100_safe"), "cc_aud:max_vol:100", selected=(max_vol == 100)),
                SelectPill(t("cc_audio.150_boost"), "cc_aud:max_vol:150", selected=(max_vol == 150)),
                SelectPill(t("cc_audio.200_overdrive"), "cc_aud:max_vol:200", selected=(max_vol == 200)),
            ]),
        )
        def_sink = next((d for d in devs if d.get("is_default")), devs[0] if devs else None)
        if def_sink:
            chs = def_sink.get("channels") or ["front-left", "front-right"]
            n_ch = max(1, min(8, len(chs)))
            sweep_btn = ActionBtn(t("cc_audio.test_channels_n", p0=n_ch), "cc_aud:test_ch", tone="neutral")
            pos_btns = " ".join(
                ActionBtn(channel_short_label(ch), f"cc_aud:test_pos:{idx + 1}", tone="neutral")
                for idx, ch in enumerate(chs[:6])
            )
            top_box.add_kv(t("cc_audio.speaker_test"), f"{sweep_btn}  {pos_btns}", requires=["speaker-test"])
        else:
            top_box.add_kv(t("cc_audio.speaker_test"), Txt(t("audio.no_output_detected"), role="inactive"), requires=["speaker-test"])
        cur_alert = get_alert_sound()
        alert_pills = " ".join(
            SelectPill(_alert_label(sid), f"cc_aud:alert:{sid}", selected=(cur_alert == sid))
            for sid in ALERT_SOUNDS
        )
        top_box.add_kv(t("cc_audio.alert_sound"), alert_pills)
    else:
        lb_on = _get_loopback_active()
        has_ee = probe.has_bin("easyeffects")
        top_box.add_kv(
            t("cc_audio.mic_loopback_test"),
            SelectPill(t("cc_audio.loopback_on") if lb_on else t("cc_audio.loopback_off"), "cc_aud:toggle_loopback", selected=lb_on),
            requires=["pactl"],
        )
        if has_ee:
            ee_on = is_easyeffects_running()
            ee_val = f"{StateBadge(ee_on, t('common.active'), t('common.off'))}  {ActionBtn(t('cc_audio.launch_easyeffects'), 'cc_aud:spawn_ee', tone='neutral')}"
            top_box.add_kv(t("cc_audio.noise_filter"), ee_val)
        else:
            top_box.add_kv(t("cc_audio.noise_filter"), Txt(t("cc_audio.pipewire_built_in"), role="val"))
    cards.append(top_box)

    for d in devs:
        q_name = quote(d["name"], safe="")
        sl = Slider(
            d["vol"],
            f"cc_aud:{scope}:vol:{q_name}",
            min_val=0,
            max_val=max_vol,
            width=vol_w,
            show_pct=True,
            step_buttons=True,
            scroll_prefix=f"cc_aud:{scope}:scroll:{q_name}",
            enabled=not d["mute"],
            overdrive_threshold=100,
        )
        def_b = SelectPill(t("cc_audio.default"), f"cc_aud:{scope}:def:{q_name}", selected=d["is_default"])
        mut_b = ActionBtn(t("audio.unmute") if d["mute"] else t("audio.mute"), f"cc_aud:{scope}:mute:{q_name}", tone="apply" if d["mute"] else "danger_soft")
        box = Box(
            d["desc"],
            icon="\uf130" if is_input else speaker_icon(d["vol"], d["mute"]),
            actions=[def_b, mut_b],
            highlighted=False,
        )
        box.add_kv(t("cc_audio.volume"), sl, requires=["pactl"])
        if not is_input:
            chs = d.get("channels", [])
            if "front-left" in chs and "front-right" in chs:
                bal_sl = Slider(
                    d["balance"],
                    f"cc_aud:out:bal:{q_name}",
                    min_val=-100,
                    max_val=100,
                    step=10,
                    width=20,
                    show_pct=False,
                    step_buttons=True,
                    scroll_prefix=f"cc_aud:out:bal_scroll:{q_name}",
                    enabled=not d["mute"],
                )
                box.add_kv(t("cc_audio.balance"), f"{bal_sl}  {Txt(_format_bal_label(d['balance']), role='val')}", requires=["pactl"])
            has_front = any(c in chs for c in ("front-left", "front-right", "front-center"))
            has_rear = any(c in chs for c in ("rear-left", "rear-right", "side-left", "side-right"))
            if has_front and has_rear:
                fade_sl = Slider(
                    d["fade"],
                    f"cc_aud:out:fade:{q_name}",
                    min_val=-100,
                    max_val=100,
                    step=10,
                    width=20,
                    show_pct=False,
                    step_buttons=True,
                    scroll_prefix=f"cc_aud:out:fade_scroll:{q_name}",
                    enabled=not d["mute"],
                )
                box.add_kv(t("cc_audio.fade"), f"{fade_sl}  {Txt(_format_fade_label(d['fade']), role='val')}", requires=["pactl"])
            if "lfe" in chs:
                lfe_sl = Slider(
                    d["lfe"],
                    f"cc_aud:out:lfe:{q_name}",
                    min_val=0,
                    max_val=max_vol,
                    width=vol_w,
                    show_pct=True,
                    step_buttons=True,
                    scroll_prefix=f"cc_aud:out:lfe_scroll:{q_name}",
                    enabled=not d["mute"],
                    overdrive_threshold=100,
                )
                box.add_kv(t("cc_audio.subwoofer_lfe"), lfe_sl, requires=["pactl"])
        st_badge = StateBadge(d["state"] == "RUNNING", d["state"], d["state"])
        mute_badge = f"  {Badge(t('cc_audio.mute'), style='warn')}" if d["mute"] else ""
        box.add_kv(t("cc_audio.state_spec"), f"{st_badge}{mute_badge}  {Txt(d['spec'], role='val')}")
        cards.append(box)
    if not devs:
        eb_actions = []
        if not probe.has_bin("pactl"):
            if not get_hide_unavailable_settings():
                eb_actions.append(Badge(t("ui.missing", p0="pactl"), style="warn"))
        elif not is_pipewire_running():
            eb_actions.append(StateBadge(False, "", t("cc_audio.pipewire_offline")))
        eb = Box(t("cc_audio.no_device"), icon="\uf131" if is_input else "\uf6a9", actions=eb_actions, highlighted=False)
        eb.add_row(f"  {Txt(t('audio.no_input_detected') if is_input else t('audio.no_output_detected'), role='inactive')}")
        cards.append(eb)
    return cards


def build_cards(cc_panel, canvas: Canvas) -> list:
    return _build_io_standard(cc_panel, canvas, is_input=False)
