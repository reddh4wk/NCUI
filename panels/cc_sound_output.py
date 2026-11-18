#!/usr/bin/env python3
import json
import re
import time
from core import log, probe
from core.config import CONFIG, _update_toml_key_in_section
from core.i18n import t
from core.state import STATE
from ui.canvas import Canvas
from ui.kit import ActionBtn, Badge, Box, ProgressBar, SelectPill, Slider, StateBadge, Txt
from panels.cc_engine_sound import (
    speaker_icon,
    get_max_volume,
    get_audio_devices,
    _feedback_cards,
    _get_loopback_active,
)


def _build_io_standard(cc_panel, canvas: Canvas, is_input: bool) -> list:
    devs = get_audio_devices(is_input=is_input)
    max_vol = get_max_volume()
    scope = "in" if is_input else "out"
    cards = _feedback_cards()

    top_box = Box(
        (t("cc_audio.input_loopback_noise_filter")) if is_input else (t("cc_audio.output_amplification_channel_test")),
        icon="\uf130" if is_input else "\uf028",
        highlighted=False,
    )
    top_box.add_row(" ")
    if not is_input:
        over_on = max_vol > 100
        top_box.add_kv(t("cc_audio.over_amplification"), f"{SelectPill(t('cc_audio.100_safe'), 'cc_aud:max_vol:100', selected=not over_on)} {SelectPill(t('cc_audio.150_boost'), 'cc_aud:max_vol:150', selected=over_on)}")
        top_box.add_kv(t("cc_audio.speaker_test"), ActionBtn(t("cc_audio.test_l_r_channels"), "cc_aud:test_ch", tone="neutral"), requires=["speaker-test"])
    else:
        lb_on = _get_loopback_active()
        has_ee = probe.has_bin("easyeffects")
        top_box.add_kv(t("cc_audio.mic_loopback_test"), SelectPill(t("cc_audio.loopback_on") if lb_on else t("cc_audio.loopback_off"), "cc_aud:toggle_loopback", selected=lb_on))
        top_box.add_kv(t("cc_audio.noise_filter"), StateBadge(has_ee, t("cc_audio.easyeffects_ready"), t("cc_audio.standard_pipewire")) if has_ee else Txt(t("cc_audio.pipewire_built_in"), role="schema"))
    top_box.add_row(" ")
    cards.append(top_box)

    for d in devs:
        sl = Slider(d["vol"], f"cc_aud:{scope}:vol:{d['name']}", min_val=0, max_val=max_vol, width=20, show_pct=True, step_buttons=False, scroll_prefix=f"cc_aud:{scope}:scroll:{d['name']}", enabled=not d["mute"])
        def_b = SelectPill(t("cc_audio.default"), f"cc_aud:{scope}:def:{d['name']}", selected=d["is_default"])
        mut_b = ActionBtn(t("audio.unmute") if d["mute"] else t("audio.mute"), f"cc_aud:{scope}:mute:{d['name']}", tone="apply" if d["mute"] else "danger_soft")
        box = Box(
            canvas.truncate_plain(d["desc"], 44),
            icon="\uf130" if is_input else speaker_icon(d["vol"], d["mute"]),
            actions=[def_b, mut_b],
            highlighted=False,
        )
        box.add_row(" ")
        box.add_kv(t("cc_audio.volume"), sl)
        box.add_kv(t("cc_audio.state_spec"), f"{StateBadge(not d['mute'], d['state'], t('cc_audio.mute'))}  {Txt(d['spec'], role='schema')}")
        box.add_row(" ")
        cards.append(box)
    if not devs:
        eb = Box(t("cc_audio.no_device"), icon="\uf6a9")
        eb.add_row(" ")
        eb.add_row(f"  {Txt(t('audio.no_input_detected') if is_input else t('audio.no_output_detected'), role='inactive')}")
        eb.add_row(" ")
        cards.append(eb)
    return cards


def build_cards(cc_panel, canvas: Canvas) -> list:
    return _build_io_standard(cc_panel, canvas, is_input=False)
