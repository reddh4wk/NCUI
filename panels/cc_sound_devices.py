#!/usr/bin/env python3
from urllib.parse import quote
from core.i18n import t
from ui.canvas import Canvas
from ui.kit import Box, SelectPill, Txt
from panels.cc_engine_sound import (
    _feedback_cards,
    _get_pipewire_cards,
)


def _pack_profile_rows(profs: list[dict], card_name: str, active_prof: str, canvas: Canvas) -> list[str]:
    q_card = quote(card_name, safe="")
    rows: list[str] = []
    cur_pills: list[str] = []
    cur_len = 0
    for p in profs:
        lbl = canvas.truncate_plain(p["desc"], 24 if len(profs) > 1 else 40)
        pill = SelectPill(lbl, f"cc_aud:card_prof:{q_card}:{quote(p['id'], safe='')}", selected=(p["id"] == active_prof))
        pill_w = len(lbl) + 5
        if cur_pills and (cur_len + 1 + pill_w > 50 or len(cur_pills) >= 2):
            rows.append(" ".join(cur_pills))
            cur_pills = [pill]
            cur_len = pill_w
        else:
            cur_pills.append(pill)
            cur_len += (1 if cur_pills else 0) + pill_w
    if cur_pills:
        rows.append(" ".join(cur_pills))
    return rows


def _build_devices_tab_standard(cc_panel, canvas: Canvas) -> list:
    cards_hw = _get_pipewire_cards()
    out_cards = _feedback_cards()
    if not cards_hw:
        b = Box(t("cc_audio.audio_hardware_profiles_pipewire"), icon="\uf2db", highlighted=False)
        b.add_row(f"  {Txt(t('cc_audio.no_audio_cards_detected_via_pactl'), role='inactive')}")
        return out_cards + [b]

    for c in cards_hw:
        avail_profs = [p for p in c["profiles"] if p["avail"] or p["id"] == c["active_profile"]]
        active_desc = next((p["desc"] for p in avail_profs if p["id"] == c["active_profile"]), c["active_profile"] or t("common.off"))
        cb = Box(
            canvas.truncate_plain(c["desc"], 48),
            icon="\uf028",
            highlighted=False,
        )
        cb.add_kv(t("cc_audio.card_node"), Txt(c["name"], role="val"))
        cb.add_kv(t("cc_audio.active_profile"), Txt(canvas.truncate_plain(active_desc, 44), role="val"))

        packed_rows = _pack_profile_rows(avail_profs, c["name"], c["active_profile"], canvas)
        visible_rows = packed_rows[:3]
        for idx, row_markup in enumerate(visible_rows):
            cb.add_kv(t("cc_audio.profiles") if idx == 0 else "", row_markup, requires=["pactl"])

        if len(packed_rows) > 3:
            rem_profs = avail_profs[sum(row.count("[●") + row.count("[○") for row in visible_rows):]
            rem_rows = packed_rows[3:]

            def _more(bx: Box, rows_rem=rem_rows):
                for rm in rows_rem:
                    bx.add_kv("", rm, requires=["pactl"])

            cb.add_collapsible(
                cc_panel,
                f"aud_profs_{c['name']}",
                t("cc_audio.other_profiles", p0=len(rem_profs)),
                _more,
            )
        out_cards.append(cb)
    return out_cards


def build_cards(cc_panel, canvas: Canvas) -> list:
    return _build_devices_tab_standard(cc_panel, canvas)
