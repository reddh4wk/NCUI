#!/usr/bin/env python3
from urllib.parse import quote
from core import probe
from core.config import get_hide_unavailable_settings
from core.i18n import t
from ui.canvas import Canvas
from ui.kit import Badge, Box, SelectPill, StateBadge, Txt, feedback_cards
from panels.cc_engine_sound import (
    _get_pipewire_cards,
    is_pipewire_running,
)


def _pack_profile_rows(profs: list[dict], card_name: str, active_prof: str, canvas: Canvas) -> list[tuple[str, int]]:
    q_card = quote(card_name, safe="")
    rows: list[tuple[str, int]] = []
    cur_pills: list[str] = []
    cur_len = 0
    for p in profs:
        lbl = canvas.truncate_plain(p["desc"], 24 if len(profs) > 1 else 40)
        pill = SelectPill(lbl, f"cc_aud:card_prof:{q_card}:{quote(p['id'], safe='')}", selected=(p["id"] == active_prof))
        pill_w = len(lbl) + 5
        if cur_pills and (cur_len + 1 + pill_w > 50 or len(cur_pills) >= 2):
            rows.append((" ".join(cur_pills), len(cur_pills)))
            cur_pills = [pill]
            cur_len = pill_w
        else:
            cur_len += (1 if cur_pills else 0) + pill_w
            cur_pills.append(pill)
    if cur_pills:
        rows.append((" ".join(cur_pills), len(cur_pills)))
    return rows


def build_cards(cc_panel, canvas: Canvas) -> list:
    cards_hw = _get_pipewire_cards()
    out_cards = feedback_cards("sound", "cc_audio.pipewire_audio_status")
    if not cards_hw:
        b_actions = []
        if not probe.has_bin("pactl"):
            if not get_hide_unavailable_settings():
                b_actions.append(Badge(t("ui.missing", p0="pactl"), style="warn"))
        elif not is_pipewire_running():
            b_actions.append(StateBadge(False, "", t("cc_audio.pipewire_offline")))
        b = Box(t("cc_audio.audio_hardware_profiles_pipewire"), icon="\uf2db", actions=b_actions, highlighted=False)
        b.add_row(f"  {Txt(t('cc_audio.no_audio_cards_detected_via_pactl'), role='inactive')}")
        return out_cards + [b]

    for c in cards_hw:
        avail_profs = sorted(
            (p for p in c["profiles"] if p["avail"] or p["id"] == c["active_profile"]),
            key=lambda p: p["id"] != c["active_profile"],
        )
        active_desc = next((p["desc"] for p in avail_profs if p["id"] == c["active_profile"]), c["active_profile"] or t("common.off"))
        cb = Box(
            c["desc"],
            icon="\uf028",
            highlighted=False,
        )
        cb.add_kv(t("cc_audio.card_node"), Txt(c["name"], role="val"))
        cb.add_kv(t("cc_audio.active_profile"), Txt(active_desc, role="val"))

        packed_rows = _pack_profile_rows(avail_profs, c["name"], c["active_profile"], canvas)
        visible_rows = packed_rows[:3]
        for idx, (row_markup, _cnt) in enumerate(visible_rows):
            cb.add_kv(t("cc_audio.profiles") if idx == 0 else "", row_markup, requires=["pactl"])

        if len(packed_rows) > 3:
            shown_n = sum(cnt for _, cnt in visible_rows)
            rem_rows = [rm for rm, _ in packed_rows[3:]]

            def _more(bx: Box, rows_rem=rem_rows):
                for rm in rows_rem:
                    bx.add_kv("", rm, requires=["pactl"])

            cb.add_collapsible(
                cc_panel,
                f"aud_profs_{c['name']}",
                t("cc_audio.other_profiles", p0=len(avail_profs) - shown_n),
                _more,
            )
        out_cards.append(cb)
    return out_cards
