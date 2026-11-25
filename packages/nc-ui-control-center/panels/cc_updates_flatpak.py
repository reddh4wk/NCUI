#!/usr/bin/env python3
from core import probe
from core.i18n import t
from ui.canvas import Canvas
from ui.kit import ActionBtn, Box, SelectPill, Txt
from panels.cc_engine_updates import (
    _STATE,
    _feedback_cards,
    add_tab_filter_row,
    add_tab_footer_row,
    build_sync_actions,
    compute_table_fit_rows,
    ensure_updates_loaded,
    get_tab_query,
)


def _build_flatpak_card(cc_panel, canvas: Canvas) -> Box:
    fp_up = _STATE.get("flatpak_updates", [])
    fp_inst = _STATE.get("flatpak_installed", [])
    fp_f = _STATE.get("fp_filter", "all")
    if fp_f not in ("all", "pending", "installed"):
        fp_f = "all"
    raw_q, q = get_tab_query("flatpak")
    show_all = bool(_STATE.get("show_all_flatpak"))

    b = Box(
        t("cc_updates.flatpak_card_title"),
        icon="\uf1b2",
        actions=build_sync_actions(),
        highlighted=False,
    )
    b.fill_window = True
    b.add_row(" ")

    if not probe.has_bin("flatpak"):
        b.add_row(f"  {Txt(t('cc_updates.flatpak_not_installed'), role='inactive')}")
        b.add_row(" ")
        return b

    b.add_kv(
        t("cc_updates.flatpak_status"),
        Txt(t("cc_updates.flatpak_summary", p0=len(fp_up), p1=len(fp_inst)), role="attention" if fp_up else "vitality"),
        right_markup=ActionBtn(t("cc_updates.upgrade_flatpak"), "cc_upd:upgrade_flatpak", tone="apply") if fp_up else "",
    )
    b.add_kv(
        t("cc_updates.flatpak_unused"),
        Txt(t("cc_updates.flatpak_unused_hint"), role="schema"),
        right_markup=ActionBtn(t("cc_updates.clean_flatpak_unused"), "cc_upd:flatpak_unused", tone="warn"),
    )

    hdr_kv_count = len(b._ops)
    max_fit = compute_table_fit_rows(cc_panel, canvas, header_kv_count=hdr_kv_count, has_section=True)
    b.add_section(t("cc_updates.flatpak_list_sec"))

    up_by_id = {u["app_id"]: u for u in fp_up}
    all_entries = []
    for u in fp_up:
        all_entries.append({
            "name": u["name"],
            "app_id": u["app_id"],
            "branch": u["branch"],
            "ver": u["new_ver"],
            "size": u["dl_size"],
            "pending": True,
            "_search": u.get("_search", f"{u['name']} {u['app_id']} {u['branch']}".lower()),
        })
    for it in fp_inst:
        if it["app_id"] in up_by_id:
            continue
        all_entries.append({
            "name": it["name"],
            "app_id": it["app_id"],
            "branch": it["branch"],
            "ver": it["ver"],
            "size": it["size"],
            "pending": False,
            "_search": it.get("_search", f"{it['name']} {it['app_id']} {it['branch']}".lower()),
        })

    if fp_f == "pending":
        base_pool = [e for e in all_entries if e["pending"]]
    elif fp_f == "installed":
        base_pool = [
            {
                "name": it["name"],
                "app_id": it["app_id"],
                "branch": it["branch"],
                "ver": up_by_id[it["app_id"]]["new_ver"] if it["app_id"] in up_by_id else it["ver"],
                "size": it["size"],
                "pending": it["app_id"] in up_by_id,
                "_search": it.get("_search", f"{it['name']} {it['app_id']} {it['branch']}".lower()),
            }
            for it in fp_inst
        ]
    else:
        base_pool = all_entries

    pills = (
        f"{SelectPill(t('cc_updates.filter_all', p0=len(all_entries)), 'cc_upd:fp_filter:all', selected=(fp_f == 'all'))} "
        f"{SelectPill(t('cc_updates.filter_pending', p0=len(fp_up)), 'cc_upd:fp_filter:pending', selected=(fp_f == 'pending'))} "
        f"{SelectPill(t('cc_updates.filter_installed', p0=len(fp_inst)), 'cc_upd:fp_filter:installed', selected=(fp_f == 'installed'))}"
    )
    add_tab_filter_row(b, canvas, "flatpak", raw_q, right_markup=pills)

    pool = [e for e in base_pool if q in e["_search"]] if q else base_pool

    if pool:
        shown = pool if show_all else pool[:max_fit]
        btn_w = max(len(t("cc_updates.update_one")), len(t("cc_updates.remove_btn")))
        rows = []
        for e in shown:
            aid = e["app_id"]
            ver_role = "vitality" if e["pending"] else "schema"
            act_btn = (
                ActionBtn(t("cc_updates.update_one"), f"cc_upd:fp_up:{aid}", tone="apply", min_chars=btn_w)
                if e["pending"]
                else ActionBtn(t("cc_updates.remove_btn"), f"cc_upd:fp_rm:{aid}", tone="danger_soft", min_chars=btn_w)
            )
            rows.append([
                Txt(e["name"], role="focus"),
                Txt(aid, role="schema"),
                Txt(e["branch"], role="schema"),
                Txt(e["ver"], role=ver_role),
                Txt(e["size"], role="val"),
                act_btn,
            ])
        b.add_table(
            [
                t("cc_updates.col_name"),
                t("cc_updates.col_app_id"),
                t("cc_updates.col_branch"),
                t("cc_updates.col_ver"),
                t("cc_updates.col_size"),
                t("cc_updates.col_act"),
            ],
            ["flex", 28, 12, 16, 10, max(10, btn_w + 4)],
            rows,
            aligns=["left", "left", "left", "left", "right", "right"],
        )
        add_tab_footer_row(b, "flatpak", len(shown), len(pool), max_fit, show_all)
    else:
        if q:
            b.add_row(f"  {Txt(t('cc_updates.no_filter_matches', p0=raw_q), role='inactive')}")
        elif fp_f == "pending":
            b.add_row(f"  {Txt(t('cc_updates.no_pending_flatpak_updates'), role='vitality')}")
        else:
            b.add_row(f"  {Txt(t('cc_updates.no_flatpak_installed'), role='inactive')}")
    b.add_row(" ")
    return b


def build_cards(cc_panel, canvas: Canvas) -> list:
    ensure_updates_loaded(cc_panel)
    return _feedback_cards() + [_build_flatpak_card(cc_panel, canvas)]
