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


def _build_aur_card(cc_panel, canvas: Canvas) -> Box:
    aur = _STATE["aur"]
    foreign = _STATE["foreign"]
    aur_f = _STATE.get("aur_filter", "all")
    if aur_f not in ("all", "pending", "installed"):
        aur_f = "all"
    raw_q, q = get_tab_query("aur")
    show_all = bool(_STATE.get("show_all_aur"))
    aur_tool = probe.get_preferred_pkg_manager(aur_only=True) or "paru"

    b = Box(
        t("cc_updates.aur_card_title"),
        icon="\uf1b3",
        actions=build_sync_actions(),
        highlighted=False,
    )
    b.fill_window = True
    b.add_row(" ")
    b.add_kv(
        t("cc_updates.aur_paru", p0=aur_tool),
        Txt(t("cc_updates.aur_summary", p0=len(aur), p1=len(foreign)), role="attention" if aur else "vitality"),
        right_markup=ActionBtn(t("cc_updates.upgrade_aur"), "cc_upd:upgrade_aur", tone="apply") if aur else "",
    )

    hdr_kv_count = len(b._ops)
    max_fit = compute_table_fit_rows(cc_panel, canvas, header_kv_count=hdr_kv_count, has_section=True)
    b.add_section(t("cc_updates.aur_list_sec"))

    aur_by_name = {item["name"]: item for item in aur}
    all_entries = []
    for p in aur:
        all_entries.append({
            "name": p["name"],
            "cur_ver": p["old_ver"],
            "new_ver": p["new_ver"],
            "size": p.get("dl_size", "-"),
            "pending": True,
            "_search": p.get("_search", f"{p['name']} aur {p['new_ver']}".lower()),
        })
    for f in foreign:
        if f["name"] in aur_by_name:
            continue
        all_entries.append({
            "name": f["name"],
            "cur_ver": f["ver"],
            "new_ver": "",
            "size": f["size"],
            "pending": False,
            "_search": f.get("_search", f"{f['name']} {f['ver']} {f.get('desc', '')}".lower()),
        })

    if aur_f == "pending":
        base_pool = [e for e in all_entries if e["pending"]]
    elif aur_f == "installed":
        base_pool = [
            {
                "name": f["name"],
                "cur_ver": f["ver"],
                "new_ver": aur_by_name[f["name"]]["new_ver"] if f["name"] in aur_by_name else "",
                "size": f["size"],
                "pending": f["name"] in aur_by_name,
                "_search": f.get("_search", f"{f['name']} {f['ver']} {f.get('desc', '')}".lower()),
            }
            for f in foreign
        ]
    else:
        base_pool = all_entries

    pills = (
        f"{SelectPill(t('cc_updates.filter_all', p0=len(all_entries)), 'cc_upd:aur_filter:all', selected=(aur_f == 'all'))} "
        f"{SelectPill(t('cc_updates.filter_pending', p0=len(aur)), 'cc_upd:aur_filter:pending', selected=(aur_f == 'pending'))} "
        f"{SelectPill(t('cc_updates.filter_installed', p0=len(foreign)), 'cc_upd:aur_filter:installed', selected=(aur_f == 'installed'))}"
    )
    add_tab_filter_row(b, canvas, "aur", raw_q, right_markup=pills)

    pool = [e for e in base_pool if q in e["_search"]] if q else base_pool

    if pool:
        shown = pool if show_all else pool[:max_fit]
        btn_w = max(len(t("cc_updates.update_one")), len(t("cc_updates.remove_btn")))
        rows = []
        for e in shown:
            nm = e["name"]
            if e["pending"]:
                st_cell = Txt(e["new_ver"], role="vitality")
                act_btn = ActionBtn(t("cc_updates.update_one"), f"cc_upd:pkg_up:{nm}", tone="apply", min_chars=btn_w)
            else:
                st_cell = Txt(t("cc_updates.installed_foreign_badge"), role="schema")
                act_btn = ActionBtn(t("cc_updates.remove_btn"), f"cc_upd:rm_foreign:{nm}", tone="danger_soft", min_chars=btn_w)
            rows.append([
                Txt(nm, role="focus"),
                Txt(e["cur_ver"], role="schema"),
                st_cell,
                Txt(e["size"], role="val"),
                act_btn,
            ])
        b.add_table(
            [
                t("cc_updates.col_pkg"),
                t("cc_updates.col_cur"),
                t("cc_updates.col_new"),
                t("cc_updates.col_size"),
                t("cc_updates.col_act"),
            ],
            ["flex", 16, 18, 10, max(10, btn_w + 4)],
            rows,
            aligns=["left", "left", "left", "right", "right"],
        )
        add_tab_footer_row(b, "aur", len(shown), len(pool), max_fit, show_all)
    else:
        if q:
            b.add_row(f"  {Txt(t('cc_updates.no_filter_matches', p0=raw_q), role='inactive')}")
        elif aur_f == "pending":
            b.add_row(f"  {Txt(t('cc_updates.no_pending_aur_updates'), role='vitality')}")
        else:
            b.add_row(f"  {Txt(t('cc_updates.no_foreign_installed'), role='inactive')}")
    b.add_row(" ")
    return b


def build_cards(cc_panel, canvas: Canvas) -> list:
    ensure_updates_loaded(cc_panel)
    return _feedback_cards() + [_build_aur_card(cc_panel, canvas)]
