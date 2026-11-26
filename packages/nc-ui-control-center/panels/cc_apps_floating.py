#!/usr/bin/env python3
import json
import os
import re
import time
from core import probe
from core.completion import catalog_window_app_ids, rank_candidates
from core.config import CONFIG, _update_toml_key_in_section
from core.i18n import t
from core.state import STATE
from ui.canvas import Canvas
from ui.kit import ActionBtn, Badge, Box, Divider, SelectPill, StateBadge, Txt, get_active_inline_entry
from panels.cc_engine_apps import (
    _feedback_cards,
    get_niri_config_file,
    get_niri_floating_rules,
    get_niri_active_windows,
)


def _build_floating_standard(cc_panel, canvas: Canvas) -> list:
    floating_ids = get_niri_floating_rules()
    active_wins = get_niri_active_windows()
    cards = _feedback_cards()

    hdr = Box(
        t("cc_apps.niri_floating_window_rules_nc_util"),
        icon="\uf2d2",
        highlighted=False,
    )
    hdr.add_row(" ")
    hdr.add_kv(t("cc_apps.config_file"), Txt(get_niri_config_file(), role="val"), right_markup=Txt(t("cc_apps.rules", p0=len(floating_ids)), role="schema"))
    hdr.add_row(" ")
    cards.append(hdr)

    act_box = Box(t("cc_apps.running_applications_window_picker"), icon="\uf2d0", highlighted=False)
    act_box.add_row(" ")
    if not active_wins:
        act_box.add_row(f"  {Txt(t('cc_apps.no_active_windows_detected_via_nir'), role='inactive')}")
    else:
        rows = []
        for w in active_wins[:10]:
            aid = w["app_id"]
            is_flt = aid in floating_ids
            st = StateBadge(True, t("cc_apps.floating"), on_tone="warn") if is_flt else Txt(t("cc_apps.tiled"), role="schema")
            btn = ActionBtn(t("cc_apps.unfloat"), f"cc_flt:toggle:{aid}", tone="danger_soft") if is_flt else ActionBtn(t("cc_apps.set_float"), f"cc_flt:toggle:{aid}", tone="apply")
            rows.append([Txt(aid[:24], role="focus"), st, Txt(canvas.truncate_plain(w["title"], 26), role="schema"), btn])
        act_box.add_table(headers=[t("cc_apps.app_id"), t("cc_apps.mode"), t("cc_apps.title"), t("cc_apps.action")], widths=[24, 12, 26, "flex"], rows=rows, aligns=["left", "left", "left", "right"])
    act_box.add_row(" ")
    cards.append(act_box)

    r_box = Box(t("cc_apps.configured_floating_rules", p0=len(floating_ids)), icon="\uf0c9", highlighted=False)
    r_box.add_row(" ")
    flt_q = str(STATE.get("apps", "flt_query", "") or "").strip()
    flt_active = (get_active_inline_entry() == "cc_flt_picker")
    if flt_q or flt_active:
        avail_ids = [it for it in catalog_window_app_ids(extra_apps=floating_ids) if it[0] not in set(floating_ids)]
        ranked_ids = rank_candidates(avail_ids, flt_q, limit=6)
        res_pills = [(canvas.truncate_plain(lb, 24), f"cc_flt:add_app:{val}") for val, lb in ranked_ids]
    else:
        res_pills = []
    r_box.add_inline_picker(
        t("cc_apps.add_floating_app"),
        query=flt_q,
        placeholder=t("cc_apps.search_app_id_ph"),
        trigger_uri="cc_flt:pick_open",
        results=res_pills,
        clear_uri="cc_flt:pick_clear" if flt_q else "",
        active=flt_active,
        max_pills_per_row=2,
        width=28,
        entry_id="cc_flt_picker",
        show_empty=bool(flt_q),
    )
    r_box.add_row(" ")
    if not floating_ids:
        r_box.add_row(f"  {Txt(t('cc_apps.no_floating_rules_configured'), role='inactive')}")
    else:
        f_rows = [
            [Txt(fid, role="focus"), ActionBtn(t("cc_apps.remove"), f"cc_flt:toggle:{fid}", tone="danger_soft")]
            for fid in floating_ids
        ]
        r_box.add_table(headers=["", ""], widths=["flex", 14], rows=f_rows, aligns=["left", "right"], show_headers=False)
    r_box.add_row(" ")
    cards.append(r_box)
    return cards


def build_cards(cc_panel, canvas: Canvas) -> list:
    return _build_floating_standard(cc_panel, canvas)
