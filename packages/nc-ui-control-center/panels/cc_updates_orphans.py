#!/usr/bin/env python3
from core.i18n import t
from ui.canvas import Canvas
from ui.kit import ActionBtn, Box, Txt
from panels.cc_engine_updates import (
    _STATE,
    _feedback_cards,
    add_tab_filter_row,
    add_tab_footer_row,
    build_sync_actions,
    compute_table_fit_rows,
    ensure_updates_loaded,
    get_pkg_backend,
    get_tab_query,
)


def _build_orphans_card(cc_panel, canvas: Canvas) -> Box:
    backend = get_pkg_backend()
    is_arch = backend == "pacman"
    orphans = _STATE["orphans"]
    raw_q, q = get_tab_query("orphans")
    show_all = bool(_STATE.get("show_all_orphans"))

    b = Box(
        t("cc_updates.orphans_card") if is_arch else t("cc_updates.orphans_card_generic"),
        icon="\uf1f8",
        actions=build_sync_actions(),
        highlighted=False,
    )
    b.fill_window = True
    b.add_row(" ")

    if backend == "unknown":
        b.add_row(f"  {Txt(t('cc_updates.pm_not_detected'), role='inactive')}")
        b.add_row(" ")
        return b

    b.add_kv(
        t("cc_updates.orphans_qdtq") if is_arch else t("cc_updates.orphans_lbl_generic"),
        Txt(t("cc_updates.orphan_summary", p0=len(orphans), p1=f"{_STATE['total_orphan_mib']:.1f}"), role="attention" if orphans else "vitality"),
        right_markup=ActionBtn(t("cc_updates.remove_all_orphans", p0=len(orphans)), "cc_upd:rm_orphans", tone="danger_soft") if orphans else "",
    )

    hdr_kv_count = len(b._ops)
    max_fit = compute_table_fit_rows(cc_panel, canvas, header_kv_count=hdr_kv_count, has_section=True)
    b.add_section(t("cc_updates.orphans_list_sec"))
    add_tab_filter_row(b, canvas, "orphans", raw_q)

    if q:
        pool = [
            o for o in orphans
            if q in o.get("_search", f"{o['name']} {o['ver']} {o['desc']}".lower())
        ]
    else:
        pool = orphans

    if pool:
        shown = pool if show_all else pool[:max_fit]
        rows = []
        show_keep = backend != "zypper"
        for o in shown:
            oname = o["name"]
            rm_btn = ActionBtn(t("cc_updates.remove_btn"), f"cc_upd:rm_orphan:{oname}", tone="danger_soft")
            acts = f"{ActionBtn(t('cc_updates.keep_btn'), f'cc_upd:keep_orphan:{oname}', tone='apply')} {rm_btn}" if show_keep else rm_btn
            rows.append([
                Txt(oname, role="focus"),
                Txt(o["ver"], role="val"),
                Txt(o["size"], role="val"),
                Txt(o["desc"], role="schema"),
                acts,
            ])
        act_w = 20 if show_keep else max(12, len(t("cc_updates.remove_btn")) + 4)
        b.add_table(
            [
                t("cc_updates.col_pkg"),
                t("cc_updates.col_ver"),
                t("cc_updates.col_size"),
                t("cc_updates.col_desc"),
                t("cc_updates.col_acts") if show_keep else t("cc_updates.col_act"),
            ],
            [20, 14, 10, "flex", act_w],
            rows,
            aligns=["left", "left", "right", "left", "right"],
        )
        add_tab_footer_row(b, "orphans", len(shown), len(pool), max_fit, show_all)
    else:
        if q:
            b.add_row(f"  {Txt(t('cc_updates.no_filter_matches', p0=raw_q), role='inactive')}")
        else:
            empty_msg = t("cc_updates.no_orphan_packages") if is_arch else t("cc_updates.no_orphan_packages_generic")
            b.add_row(f"  {Txt(empty_msg, role='vitality')}")
    b.add_row(" ")
    return b


def build_cards(cc_panel, canvas: Canvas) -> list:
    ensure_updates_loaded(cc_panel)
    return _feedback_cards() + [_build_orphans_card(cc_panel, canvas)]
