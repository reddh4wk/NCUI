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
    _feedback_cards,
    _get_pipewire_cards,
)


def _build_devices_tab_standard(cc_panel, canvas: Canvas) -> list:
    cards_hw = _get_pipewire_cards()
    out_cards = _feedback_cards()
    if not cards_hw:
        b = Box(t("cc_audio.audio_hardware_profiles_pipewire"), icon="\uf2db", highlighted=False)
        b.add_row(" ")
        b.add_row(f"  {Txt(t('cc_audio.no_audio_cards_detected_via_pactl'), role='inactive')}")
        b.add_row(" ")
        return out_cards + [b]

    for c in cards_hw:
        cb = Box(
            c["desc"][:48],
            icon="\uf028",
            actions=[Txt(c["active_profile"][:26] or t("common.off"), role="schema")],
            highlighted=False,
        )
        cb.add_row(" ")
        cb.add_kv(t("cc_audio.card_node"), Txt(c["name"], role="schema"))
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
            cb.add_collapsible(cc_panel, f"aud_profs_{c['name']}", t("cc_audio.other_profiles", p0=len(avail_profs) - 5), _more)
        cb.add_row(" ")
        out_cards.append(cb)
    return out_cards


def build_cards(cc_panel, canvas: Canvas) -> list:
    return _build_devices_tab_standard(cc_panel, canvas)
