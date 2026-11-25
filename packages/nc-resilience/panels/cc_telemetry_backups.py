#!/usr/bin/env python3
import os

from core.i18n import t
from core.privileges import build_privilege_modal_card
from core.state import STATE
from ui.canvas import Canvas
from ui.kit import (
    ActionBtn,
    Badge,
    Box,
    FoldToggle,
    InlineField,
    InlineFilter,
    InspectorCell,
    SelectPill,
    StateBadge,
    Txt,
    get_active_inline_entry,
    get_inline_entry_buffer,
)
from panels.cc_engine_telemetry import (
    _build_guide_box,
    _collect_backup_data,
    _feedback_cards,
    ensure_backups_loaded,
)


def _state_role(st: str) -> str:
    s = str(st or "").upper()
    if s == "IN_SYNC":
        return "vitality"
    if s in ("CONFLICT", "MISSING", "ERROR"):
        return "fault"
    return "attention"


def _diff_role(diff_s: str) -> str:
    s = str(diff_s or "").upper()
    if s == "IDENTICAL":
        return "vitality"
    if s in ("ABSENT", "ERROR"):
        return "fault"
    if s.startswith("+") or s == "ENCRYPTED":
        return "attention"
    return "schema"


def build_cards(cc_panel, canvas: Canvas) -> list:
    ensure_backups_loaded(cc_panel)
    data = _collect_backup_data()
    nas = data["nas_info"]
    nas_mounted = data["nas_mounted"]
    vcfg = data["vault_cfg"]
    icfg = data["ident_cfg"]
    h_sum = data["hanoi_summary"]
    cfg_open, cfg_fold_btn = FoldToggle(cc_panel, "backups_cfg_open", uri_prefix="ui:state")

    cards = _feedback_cards() + build_privilege_modal_card(cc_panel, canvas)
    bak_stat = f"{icfg.get('sys_alias', 'nc')} · {str(vcfg.get('protocol', 'smb')).upper()}"
    cards.extend(_build_guide_box(cc_panel, canvas, "guide_backups_open", "guide_bak", "backups", bak_stat))

    # ── Card 1 (TOP): Vault & Identity Configuration + Mount Status ───────────
    proto = str(vcfg.get("protocol", "smb")).upper()
    tgt = vcfg.get("host") or nas.get("base") or "-"
    hdr1_acts = [StateBadge(nas_mounted, t("cc_system.vault_mounted"), t("cc_system.vault_offline"))]
    if nas_mounted:
        hdr1_acts.extend([
            ActionBtn(t("cc_system.remount_nas"), "cc_sys:nas_remount", tone="neutral"),
            ActionBtn(t("cc_system.umount_nas"), "cc_sys:nas_umount", tone="danger_soft"),
        ])
    else:
        hdr1_acts.append(ActionBtn(t("cc_system.mount_nas"), "cc_sys:mount_nas", tone="apply"))
    hdr1_acts.append(cfg_fold_btn)

    box1 = Box(t("cc_system.vault_cfg_sec"), icon="\uf013", actions=hdr1_acts, highlighted=False)
    box1.add_row(" ")
    if cfg_open:
        cur_p = str(vcfg.get("protocol", "smb")).lower()
        p_pills = " ".join(
            SelectPill(p.upper(), f"cc_sys:vault_cfg:protocol:{p}", selected=(cur_p == p))
            for p in ("smb", "nfs", "ssh", "local")
        )
        box1.add_kv(t("cc_system.vault_cfg_proto"), p_pills)

        host_fld = InlineField("vault_host", "cc_sys:vault_host_open", value=str(vcfg.get("host", "")), placeholder=t("cc_system.vault_ph_host"), width=20)
        mp_fld = InlineField("vault_mp", "cc_sys:vault_mp_open", value=str(vcfg.get("mountpoint", "/nas")), placeholder="/nas..", width=20)
        box1.add_kv(t("cc_system.vault_cfg_host_mp"), f"{host_fld}   {Txt('->', role='schema')}   {mp_fld}")

        if cur_p == "smb":
            sh_val = ",".join(str(s) for s in (vcfg.get("shares") or []))
            sh_fld = InlineField("vault_shares", "cc_sys:vault_shares_open", value=sh_val, placeholder=t("cc_system.vault_ph_shares"), width=34)
            box1.add_kv(t("cc_system.vault_cfg_shares"), sh_fld)
        else:
            rp_fld = InlineField("vault_rpath", "cc_sys:vault_rpath_open", value=str(vcfg.get("remote_path", "")), placeholder="/export/..", width=34)
            box1.add_kv(t("cc_system.vault_cfg_rpath"), rp_fld)

        if cur_p == "ssh":
            u_fld = InlineField("vault_user", "cc_sys:vault_user_open", value=str(vcfg.get("ssh_user", "")), placeholder="user..", width=20)
            box1.add_kv(t("cc_system.vault_cfg_ssh_user"), u_fld)

        sub_fld = InlineField("vault_subdir", "cc_sys:vault_subdir_open", value=str(vcfg.get("backup_root_subdir", "")), width=20)
        pkg_fld = InlineField("vault_pkgdir", "cc_sys:vault_pkgdir_open", value=str(vcfg.get("pkglist_subdir", "")), width=20)
        box1.add_kv(t("cc_system.vault_cfg_subdirs"), f"{sub_fld}   {Txt('/', role='schema')}   {pkg_fld}")

        al_fld = InlineField("vault_alias", "cc_sys:vault_alias_open", value=str(icfg.get("sys_alias", "")), width=14)
        fo_fld = InlineField("vault_folder", "cc_sys:vault_folder_open", value=str(icfg.get("sys_folder", "")), width=22)
        box1.add_kv(t("cc_system.vault_cfg_identity"), f"{al_fld}   {Txt('/', role='schema')}   {fo_fld}")
    else:
        box1.add_kv(
            t("cc_system.vault_cfg_summary"),
            Txt(f"{proto} · {tgt} -> {nas['base']} · {icfg.get('sys_alias', '-')}/{icfg.get('sys_folder', '-')}", role="val"),
        )
    box1.add_row(" ")
    cards.append(box1)

    # ── Card 2: Hanoi Sync Operations (nc-hanoi) ──────────────────────────────
    lc = str(h_sum["last_check"] or "")
    if len(lc) >= 16 and lc[4] == "-":
        lc = lc[5:16]
    hdr2_acts = []
    if data["vault_busy"]:
        hdr2_acts.append(Badge(t("cc_system.vault_busy_badge", p0=data["vault_busy"]), style="warn"))
    elif lc and lc != "-":
        hdr2_acts.append(Txt(lc, role="schema"))
    hdr2_acts.append(ActionBtn(t("cc_system.hanoi_scan_btn"), "cc_sys:hanoi_check", tone="apply" if nas_mounted else "muted"))

    box2 = Box(t("cc_system.backups_box_title"), icon="\uf1c0", actions=hdr2_acts, highlighted=False)
    box2.add_row(" ")

    push_n = h_sum["push_total"]
    pull_n = h_sum["pull_total"]
    h_meta = f"{h_sum['sys_alias']} · ↑{push_n} ↓{pull_n}"
    h_acts = " ".join([
        ActionBtn(t("cc_system.hanoi_push_btn"), "cc_sys:hanoi_push", tone="apply" if nas_mounted else "muted"),
        ActionBtn(t("cc_system.hanoi_pull_btn"), "cc_sys:hanoi_pull", tone="warn" if nas_mounted else "muted"),
        ActionBtn(t("cc_system.hanoi_add_btn"), "cc_sys:hanoi_add_prompt", tone="apply"),
    ])
    box2.add_kv(t("cc_system.hanoi_drift_lbl"), Txt(h_meta, role="attention" if (push_n or pull_n) else "val"), right_markup=h_acts)

    t_on = bool(data["hanoi_timer_on"])
    t_pills = " ".join([
        SelectPill(t("common.on"), "cc_sys:hanoi_timer:on", selected=t_on),
        SelectPill(t("common.off"), "cc_sys:hanoi_timer:off", selected=not t_on),
    ])
    if data["confirm_prune"]:
        p_acts = " ".join([
            ActionBtn(t("cc_system.hanoi_prune_confirm"), "cc_sys:hanoi_prune_do", tone="danger_soft"),
            ActionBtn(t("common.cancel"), "cc_sys:hanoi_prune_cancel", tone="neutral"),
        ])
    else:
        p_acts = ActionBtn(t("cc_system.hanoi_prune_btn"), "cc_sys:hanoi_prune_ask", tone="warn" if nas_mounted else "muted")
    box2.add_kv(t("cc_system.hanoi_timer_lbl"), t_pills, right_markup=p_acts)

    pkgs = data["pkglist_items"] or []
    box2.add_kv(
        t("cc_system.vault_pkglists_lbl"),
        Txt(t("cc_system.backups_pkg_cnt", p0=len(pkgs)), role="val" if pkgs else "schema"),
        right_markup=ActionBtn(t("cc_system.vault_pkg_export_btn"), "cc_sys:pkglist_prompt", tone="apply" if nas_mounted else "muted"),
    )
    box2.add_row(" ")
    cards.append(box2)

    # ── Card 3: Master-Detail Vault Catalog ───────────────────────────────────
    view = str(STATE.get("system", "hanoi_view_mode", "tracked") or "tracked")
    if view not in ("tracked", "drift", "history", "pkglists"):
        view = "tracked"

    eid = "cc_sys_hanoi_filter"
    raw_q = (get_inline_entry_buffer() if get_active_inline_entry() == eid else str(STATE.get("system", "hanoi_search_q", "") or "")).strip()
    q = raw_q.lower()

    v_pills = " ".join(
        SelectPill(t(lbl_k), f"cc_sys:hanoi_view:{vid}", selected=(view == vid))
        for vid, lbl_k in (
            ("tracked", "cc_system.hanoi_tab_tracked"),
            ("drift", "cc_system.hanoi_tab_drift"),
            ("history", "cc_system.hanoi_tab_history"),
            ("pkglists", "cc_system.hanoi_tab_pkglists"),
        )
    )
    filt = InlineFilter(
        eid,
        query=raw_q,
        placeholder=t("cc_system.backups_filter_ph"),
        trigger_uri="cc_sys:hanoi_filter_open",
        clear_uri="cc_sys:hanoi_filter_clear",
        width=22,
        active=bool(raw_q),
    )

    items = data["hanoi_list_items"] or []
    hist = data["hanoi_hist_items"] or []

    # Resolve currently inspected path against the visible rows of the active catalog slice
    sel_path = str(data.get("hanoi_selected_path") or "").strip()
    show_all = bool(STATE.get("system", "hanoi_show_all", False))
    page_cap = 12
    filtered_paths = []
    if view in ("tracked", "drift"):
        filtered_items = [
            it for it in items
            if (view != "drift" or it.get("state") != "IN_SYNC")
            and (not q or q in f"{it.get('state', '')} {it.get('role', '')} {it.get('path', '')}".lower())
        ]
        filtered_paths = [it["path"] for it in filtered_items]
    elif view == "history":
        filtered_hist = [
            h for h in hist
            if not q or q in f"{h.get('owner', '')} {h.get('path', '')}".lower()
        ]
        filtered_paths = [h["path"] for h in filtered_hist]

    visible_paths = filtered_paths if show_all else filtered_paths[:page_cap]
    prev_sel = sel_path
    if view == "pkglists" or not visible_paths:
        sel_path = ""
    elif sel_path not in visible_paths:
        sel_path = visible_paths[0]
    if sel_path != prev_sel:
        STATE.set("system", "hanoi_selected_path", sel_path)
        STATE.set("system", "hanoi_confirm_restore", "")
        STATE.set("system", "hanoi_confirm_ver_restore", "")

    t_headers, t_widths, t_aligns = [], [], []
    rows_all = []
    if view in ("tracked", "drift"):
        t_headers = [t("cc_system.hanoi_col_path"), t("cc_system.col_state"), t("cc_system.hanoi_col_role"), t("cc_system.hanoi_col_shared")]
        t_widths = [44, 12, 14, 12]
        t_aligns = ["left", "left", "left", "left"]
        for it in filtered_items:
            p_str = it["path"]
            rows_all.append([
                InspectorCell(canvas.truncate_plain(p_str, 40), f"cc_sys:hanoi_select:{p_str}", selected=(p_str == sel_path)),
                Txt(it["state"], role=_state_role(it["state"])),
                Txt(it["role"], role="schema"),
                Txt(it["shared"], role="schema"),
            ])
    elif view == "history":
        t_headers = [t("cc_system.hanoi_col_path"), t("cc_system.hanoi_col_owner"), t("cc_system.hanoi_col_gens"), t("cc_system.snap_col_date"), t("cc_system.col_state")]
        t_widths = [36, 10, 6, 16, 12]
        t_aligns = ["left", "left", "right", "left", "left"]
        for h_it in filtered_hist:
            p_str = h_it["path"]
            st_item = StateBadge(bool(h_it.get("active")), t("common.active")) if h_it.get("active") else Txt("-", role="inactive")
            rows_all.append([
                InspectorCell(canvas.truncate_plain(p_str, 32), f"cc_sys:hanoi_select:{p_str}", selected=(p_str == sel_path)),
                Txt(h_it["owner"], role="val"),
                Txt(str(h_it["count"]), role="schema"),
                Txt(h_it["latest"], role="schema"),
                st_item,
            ])
    else:
        t_headers = ["#", t("cc_system.hanoi_col_label"), t("cc_system.backups_col_pkgs"), t("cc_system.snap_col_date"), t("cc_system.hanoi_col_file")]
        t_widths = [5, 18, 10, 16, 35]
        t_aligns = ["right", "left", "right", "left", "left"]
        for pk in pkgs:
            if q and q not in f"{pk.get('label', '')} {pk.get('file', '')}".lower():
                continue
            rows_all.append([
                Txt(str(pk["num"]), role="schema"),
                Txt(pk["label"], role="focus"),
                Txt(str(pk["count"]), role="val"),
                Txt(pk["date"], role="schema"),
                Txt(pk["file"], role="schema"),
            ])

    shown_rows = rows_all if show_all else rows_all[:page_cap]

    box3 = Box(f"{t('cc_system.backups_list_sec')} ({len(shown_rows)}/{len(rows_all)})", icon="\uf03a", highlighted=False)
    box3.add_row(" ")
    box3.add_row(f"  {v_pills}   {filt}")
    box3.add_row(" ")

    if shown_rows:
        box3.add_table(headers=t_headers, widths=t_widths, rows=shown_rows, aligns=t_aligns)
        if len(rows_all) > page_cap:
            tog_lbl = t("cc_system.backups_collapse_list") if show_all else t("cc_system.backups_show_all", p0=len(rows_all))
            box3.add_row(f"  {ActionBtn(tog_lbl, 'cc_sys:hanoi_toggle_all', tone='neutral')}")
    else:
        empty_k = "cc_system.backups_no_rows" if nas_mounted else "cc_system.backups_need_mount"
        box3.add_row(f"  {Txt(t(empty_k), role='inactive')}")

    box3.add_row(" ")
    cards.append(box3)

    # ── Card 4: File & Hanoi Version History Inspector (Native NC-Box) ────────
    if view != "pkglists" and sel_path and sel_path in visible_paths:
        sel_item = next((it for it in items if it.get("path") == sel_path), None)
        sel_base = os.path.basename(sel_path) or sel_path
        if data["hanoi_confirm_restore"] == sel_path:
            rest_btn = (
                f"{ActionBtn(t('cc_system.hanoi_confirm_restore'), 'cc_sys:hanoi_restore_do', tone='danger_soft')} "
                f"{ActionBtn(t('common.cancel'), 'cc_sys:hanoi_restore_cancel', tone='neutral')}"
            )
        else:
            rest_btn = ActionBtn(t("cc_system.hanoi_restore_file_btn"), "cc_sys:hanoi_restore_ask", tone="warn" if nas_mounted else "muted")

        hdr4_acts = [
            ActionBtn(t("cc_system.hanoi_push_one_btn"), "cc_sys:hanoi_push_one", tone="apply" if nas_mounted else "muted"),
            rest_btn,
            ActionBtn(t("cc_system.hanoi_diff_term_btn"), "cc_sys:hanoi_diff_term", tone="neutral"),
        ]
        box4 = Box(t("cc_system.hanoi_insp_title", p0=sel_base), icon="\uf15c", actions=hdr4_acts, highlighted=False)
        box4.add_row(" ")

        st_lbl = sel_item["state"] if sel_item else "-"
        role_lbl = sel_item["role"] if sel_item else "-"
        box4.add_kv(
            t("cc_system.hanoi_insp_path"),
            Txt(sel_path, role="focus"),
            right_markup=f"{Txt(st_lbl, role=_state_role(st_lbl))}  {Txt(role_lbl, role='schema')}",
        )

        sh_val = "" if (not sel_item or sel_item.get("shared") == "-") else str(sel_item.get("shared") or "")
        sh_fld = InlineField(
            "cc_sys_hanoi_share",
            "cc_sys:hanoi_share_open",
            value=sh_val,
            placeholder=t("cc_system.hanoi_ph_share"),
            empty_label=t("cc_system.hanoi_share_set"),
            width=22,
        )
        box4.add_kv(
            t("cc_system.hanoi_insp_share"),
            sh_fld,
            right_markup=ActionBtn(t("cc_system.hanoi_load_vers_btn"), "cc_sys:hanoi_load_versions", tone="neutral"),
        )

        vmap = data.get("hanoi_versions_map") or {}
        vlist = vmap.get(sel_path)
        box4.add_section(t("cc_system.hanoi_vers_sec", p0=len(vlist) if isinstance(vlist, list) else 0))
        if isinstance(vlist, list) and vlist:
            v_rows = []
            c_ver = str(data.get("hanoi_confirm_ver_restore") or "")
            for v in vlist:
                idx_s = str(v["idx"])
                if c_ver == idx_s:
                    v_acts = (
                        f"{ActionBtn(t('cc_system.hanoi_confirm_ver_btn'), f'cc_sys:hanoi_ver_restore_do:{idx_s}', tone='danger_soft')} "
                        f"{ActionBtn(t('common.cancel'), 'cc_sys:hanoi_ver_restore_cancel', tone='neutral')}"
                    )
                else:
                    v_acts = (
                        f"{ActionBtn(t('cc_system.hanoi_ver_diff_btn'), f'cc_sys:hanoi_ver_diff:{idx_s}', tone='neutral')} "
                        f"{ActionBtn(t('cc_system.hanoi_ver_rest_btn'), f'cc_sys:hanoi_ver_restore_ask:{idx_s}', tone='warn')}"
                    )
                ts_short = str(v["ts"] or "-")[:16]
                v_rows.append([
                    Txt(f"#{idx_s}", role="schema"),
                    Txt(ts_short, role="schema"),
                    Txt(canvas.truncate_plain(v["slot"], 17), role="val"),
                    Txt(v["event"], role="schema"),
                    Txt(v["diff"], role=_diff_role(v["diff"])),
                    v_acts,
                ])
            box4.add_table(
                headers=[
                    "#",
                    t("cc_system.snap_col_date"),
                    t("cc_system.hanoi_col_slot"),
                    t("cc_system.hanoi_col_event"),
                    t("cc_system.hanoi_col_vsloc"),
                    t("cc_system.col_actions"),
                ],
                widths=[3, 16, 17, 6, 9, 21],
                rows=v_rows,
                aligns=["right", "left", "left", "left", "left", "right"],
            )
        elif isinstance(vlist, list):
            box4.add_row(f"  {Txt(t('cc_system.hanoi_vers_empty'), role='inactive')}")
        else:
            box4.add_row(f"  {Txt(t('cc_system.hanoi_vers_unloaded'), role='schema')}")

        box4.add_row(" ")
        cards.append(box4)

    return cards
