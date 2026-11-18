#!/usr/bin/env python3
import json
import re
import time
from core import log, probe
from core.config import CONFIG, _update_toml_key_in_section
from core.i18n import t
from core.state import STATE
from ui.canvas import Canvas
from ui.kit import ActionBtn, Badge, Box, Divider, ProgressBar, SelectPill, Slider, StateBadge, Txt
from panels.cc_engine_sound import (
    get_max_volume,
    get_sink_inputs,
    _feedback_cards,
    _get_pw_clock_meta,
)


def _build_mixer_standard(cc_panel, canvas: Canvas) -> list:
    apps = get_sink_inputs()
    max_vol = get_max_volume()
    quantum, rate = _get_pw_clock_meta()
    cards = _feedback_cards()

    pw_box = Box(
        t("cc_audio.pipewire_dsp_clock_quantum_low_lat"),
        icon="\uf1de",
        actions=[Txt(f"Q:{quantum}  R:{rate}", role="schema")],
        highlighted=False,
    )
    pw_box.add_row(" ")
    auto_lbl = t("cc_audio.auto")
    q_btns = " ".join(SelectPill(auto_lbl if q == "0" else q, f"cc_aud:pw_q:{q}", selected=(quantum == ("auto" if q == "0" else q))) for q in ("0", "64", "128", "256", "512", "1024"))
    r_btns = " ".join(SelectPill(auto_lbl if r == "0" else f"{int(r)//1000}k", f"cc_aud:pw_r:{r}", selected=(rate == ("auto" if r == "0" else r))) for r in ("0", "44100", "48000", "96000", "192000"))
    pw_box.add_kv(t("cc_audio.clock_quantum"), q_btns, requires=["pw-metadata"])
    pw_box.add_kv(t("cc_audio.sample_rate"), r_btns, requires=["pw-metadata"])
    pw_box.add_row(" ")
    cards.append(pw_box)

    if not apps:
        app_box = Box(t("cc_audio.application_stream_mixer", p0=0), icon="\uf001", highlighted=False)
        app_box.add_row(" ")
        app_box.add_row(f"  {Txt(t('audio.no_active_apps'), role='inactive')}")
        app_box.add_row(" ")
        cards.append(app_box)
    else:
        if len(apps) > 1:
            cards.append(Divider(t("cc_audio.application_stream_mixer", p0=len(apps)), icon="\uf001"))
        for a in apps:
            sl = Slider(a["vol"], f"cc_aud:app:vol:{a['index']}", min_val=0, max_val=max_vol, width=20, show_pct=True, step_buttons=False, scroll_prefix=f"cc_aud:app:scroll:{a['index']}", enabled=not a["mute"])
            mb = ActionBtn(t("audio.unmute") if a["mute"] else t("audio.mute"), f"cc_aud:app:mute:{a['index']}", tone="apply" if a["mute"] else "danger_soft")
            ab = Box(
                canvas.truncate_plain(a["label"], 44),
                icon="\uf6a9" if a["mute"] else "\uf001",
                actions=[mb],
                highlighted=False,
            )
            ab.add_row(" ")
            ab.add_kv(t("cc_audio.volume"), sl)
            ab.add_row(" ")
            cards.append(ab)
    return cards


def build_cards(cc_panel, canvas: Canvas) -> list:
    return _build_mixer_standard(cc_panel, canvas)
