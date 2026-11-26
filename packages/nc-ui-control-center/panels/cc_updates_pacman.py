#!/usr/bin/env python3
from core.i18n import t
from ui.canvas import Canvas
from ui.kit import ActionBtn, Box, Txt
from panels.cc_engine_updates import (
    _PKGLIST_SCRIPT,
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


def _build_pacman_card(cc_panel, canvas: Canvas) -> Box:
    backend = get_pkg_backend()
    off = _STATE["official"]
    raw_q, q = get_tab_query("pacman")
    show_all = bool(_STATE.get("show_all_pacman"))
    is_arch = backend == "pacman"
    pm_label = backend.upper() if backend != "unknown" else t("cc_updates.system_fallback")
    card_title = t("cc_updates.pacman_card_title") if is_arch else t("cc_updates.pkg_card_title", p0=pm_label)

    b = Box(
        card_title,
        icon="\uf187",
        actions=build_sync_actions(),
        highlighted=False,
    )
    b.fill_window = True
    b.add_row(" ")

    if backend == "unknown":
        b.add_row(f"  {Txt(t('cc_updates.pm_not_detected'), role='attention')}")
        b.add_row(" ")
        return b

    hint_txt = t("cc_updates.on_demand_manual_scan_checkupdates") if is_arch else t("cc_updates.scan_hint_generic")
    up_lbl = t("cc_updates.upgrade_official") if is_arch else t("cc_updates.upgrade_system_btn")
    cache_lbl = t("cc_updates.pacman_cache") if is_arch else t("cc_updates.pkg_cache", p0=pm_label)
    cache_btn = t("cc_updates.clean_cache_paccache_rk2") if is_arch else t("cc_updates.clean_cache_btn")

    b.add_row(f"  {Txt(hint_txt, role='schema')}")
    b.add_kv(
        t("cc_updates.arch_official"),
        Txt(t("cc_updates.pending_summary", p0=len(off), p1=f"{_STATE['total_dl_mib']:.1f}"), role="attention" if off else "vitality"),
        right_markup=ActionBtn(up_lbl, "cc_upd:upgrade_official", tone="apply") if off else "",
    )
    b.add_kv(
        cache_lbl,
        Txt(_STATE["cache_info"], role="val"),
        right_markup=ActionBtn(cache_btn, "cc_upd:paccache", tone="warn"),
        requires=["paccache"] if is_arch else None,
    )
    if _PKGLIST_SCRIPT:
        b.add_kv(
            t("cc_updates.package_snapshot"),
            Txt(_PKGLIST_SCRIPT, role="schema"),
            right_markup=ActionBtn(t("cc_updates.save_pkglist"), "cc_upd:save_pkglist", tone="apply"),
            requires=[_PKGLIST_SCRIPT],
        )

    hdr_kv_count = len(b._ops)
    max_fit = compute_table_fit_rows(cc_panel, canvas, header_kv_count=hdr_kv_count, has_section=True)
    b.add_section(t("cc_updates.pending_updates_card"))
    add_tab_filter_row(b, canvas, "pacman", raw_q)

    if q:
        pool = [
            p for p in off
            if q in p.get("_search", f"{p['name']} {p['repo']} {p['new_ver']}".lower())
        ]
    else:
        pool = off

    if pool:
        shown = pool if show_all else pool[:max_fit]
        rows = [
            [
                Txt(p["name"], role="focus"),
                Txt(p["repo"], role="schema"),
                Txt(p["old_ver"], role="schema"),
                Txt(p["new_ver"], role="vitality"),
                Txt(p["dl_size"], role="val"),
                ActionBtn(t("cc_updates.update_one"), f"cc_upd:pkg_up:{p['name']}", tone="apply"),
            ]
            for p in shown
        ]
        b.add_table(
            [
                t("cc_updates.col_pkg"),
                t("cc_updates.col_repo"),
                t("cc_updates.col_cur"),
                t("cc_updates.col_new"),
                t("cc_updates.col_dl"),
                t("cc_updates.col_act"),
            ],
            ["flex", 17, 13, 15, 9, 10],
            rows,
            aligns=["left", "left", "left", "left", "right", "right"],
        )
        add_tab_footer_row(b, "pacman", len(shown), len(pool), max_fit, show_all)
    else:
        if q:
            b.add_row(f"  {Txt(t('cc_updates.no_filter_matches', p0=raw_q), role='inactive')}")
        else:
            empty_msg = t("cc_updates.no_pending_official_updates") if is_arch else t("cc_updates.no_pending_pkg_updates")
            b.add_row(f"  {Txt(empty_msg, role='vitality')}")
    b.add_row(" ")
    return b


def build_cards(cc_panel, canvas: Canvas) -> list:
    ensure_updates_loaded(cc_panel)
    return _feedback_cards() + [_build_pacman_card(cc_panel, canvas)]
