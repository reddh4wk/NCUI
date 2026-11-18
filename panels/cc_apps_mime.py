#!/usr/bin/env python3
import json
import os
import re
import time
from core import probe
from core.completion import catalog_desktop_apps, rank_candidates
from core.config import CONFIG, _update_toml_key_in_section
from core.i18n import t
from core.state import STATE
from panels.wp_display import resolve_script_path
from ui.canvas import Canvas
from ui.kit import ActionBtn, Badge, Box, Divider, SelectPill, StateBadge, Txt, get_active_inline_entry
from panels.cc_engine_apps import (
    _feedback_cards,
    _query_mime_defaults,
    _available_desktop_files,
    MIME_SLOTS,
)


def _build_mime_standard(cc_panel, canvas: Canvas) -> list:
    defs = _query_mime_defaults()
    avail_desk = _available_desktop_files()
    cards = _feedback_cards()

    box = Box(
        t("cc_apps.default_applications_xdg_mime_conf"),
        icon="\uf1b2",
        actions=[ActionBtn(t("cc_apps.refresh"), "cc_apps:refresh", tone="neutral")],
        highlighted=False,
    )
    box.add_row(" ")
    pick_slot = str(STATE.get("apps", "mime_pick_slot", "") or "")
    pick_q = str(STATE.get("apps", "mime_pick_query", "") or "").strip()
    for key, lbl, mime, cands in MIME_SLOTS:
        cur = defs.get(key, "unset")
        if mime == "nc_terminal":
            opts = " ".join(SelectPill(c, f"cc_apps:set_term:{c}", selected=(cur == c)) for c in cands)
            box.add_kv(lbl, opts)
        elif mime == "nc_pkg_manager":
            avail_pms = probe.list_installed_pkg_managers() or cands[:3]
            opts = " ".join(SelectPill(c, f"cc_apps:set_pkg_mgr:{c}", selected=(cur == c)) for c in avail_pms)
            box.add_kv(t("cc_apps.kv_pkg_manager"), opts)
        else:
            installed_cands = [c for c in cands if c in avail_desk][:3]
            btns = " ".join(SelectPill(c.replace(".desktop", "")[:14], f"cc_apps:set_mime:{mime}:{c}", selected=(cur == c)) for c in installed_cands)
            cur_short = cur.replace(".desktop", "")
            in_pills = cur in installed_cands
            if cur == "unset":
                val_prefix = f"{Txt(t('common.unset'), role='inactive')}  " if btns else Txt(t("common.unset"), role="inactive")
            elif not in_pills:
                val_prefix = f"{Txt(cur_short, role='val')}  " if btns else Txt(cur_short, role="val")
            else:
                val_prefix = ""
            eid = f"mime_pick_{key}"
            is_pick_open = (pick_slot == mime or get_active_inline_entry() == eid)
            box.add_kv(
                lbl,
                f"{val_prefix}{btns}".strip(),
                right_markup=ActionBtn(t("cc_apps.custom"), f"cc_apps:pick_mime:{mime}", tone="neutral"),
                requires=["xdg-mime"],
            )
            if is_pick_open:
                ranked = rank_candidates(catalog_desktop_apps(mime_hint=mime), pick_q, limit=6)
                res_pills = [(canvas.truncate_plain(lb, 22), f"cc_apps:set_mime:{mime}:{val}") for val, lb in ranked]
                box.add_inline_picker(
                    "",
                    query=pick_q,
                    placeholder=t("cc_apps.desktop_handler_for", p0=mime),
                    trigger_uri=f"cc_apps:pick_mime:{mime}",
                    results=res_pills,
                    clear_uri=f"cc_apps:pick_mime_clear:{mime}" if pick_q else "",
                    active=(get_active_inline_entry() == eid),
                    max_pills_per_row=2,
                    width=28,
                    entry_id=eid,
                    show_empty=bool(pick_q),
                )
    box.add_row(" ")
    cards.append(box)
    return cards


def build_cards(cc_panel, canvas: Canvas) -> list:
    return _build_mime_standard(cc_panel, canvas)
