#!/usr/bin/env python3
"""NC-UI Control Center Tab: NAS & Vault Configuration, Mountpoints & Desktop Integration."""

from core.i18n import t
from core.privileges import build_privilege_modal_card
from ui.canvas import Canvas
from ui.kit import (
    ActionBtn,
    Badge,
    Box,
    InlineField,
    InspectorCell,
    SelectPill,
    StateBadge,
    Txt,
    feedback_cards,
)
from panels.cc_engine_telemetry_vault import collect_vault_page_data


def _trust_role(trust_s: str) -> str:
    return "vitality" if trust_s == "TRUSTED" else "attention"


def _build_overview_card(nas: dict, vcfg: dict, data: dict) -> Box:
    mounted = bool(nas["mounted"])
    proto = str(nas["protocol"]).upper()
    host = nas["host"] or "-"
    hdr_acts = []
    if data["vault_busy"]:
        hdr_acts.append(Badge(t("cc_system.vault_busy_badge", p0=data["vault_busy"]), style="warn"))
    hdr_acts.append(StateBadge(mounted, t("cc_system.vault_mounted"), t("cc_system.vault_offline")))
    if mounted:
        hdr_acts.append(ActionBtn(t("cc_system.remount_nas"), "cc_sys:nas_remount", tone="neutral"))
        hdr_acts.append(ActionBtn(t("cc_system.umount_nas"), "cc_sys:nas_umount", tone="danger_soft"))
        if proto == "SSH":
            hdr_acts.append(ActionBtn(t("cc_system.vault_ssh_push_btn"), "cc_sys:vault_ssh_push", tone="apply"))
    else:
        hdr_acts.append(ActionBtn(t("cc_system.mount_nas"), "cc_sys:mount_nas", tone="apply"))

    box = Box(t("cc_system.vault_overview_sec"), icon="\uf233", actions=hdr_acts, highlighted=False)
    box.add_row(" ")

    tgt_txt = f"{proto} · {host}" if proto != "LOCAL" else proto
    if nas["port"]:
        tgt_txt = f"{tgt_txt}:{nas['port']}"
    cnt_txt = t("cc_system.vault_mount_count_fmt", p0=nas["count"], p1=nas["total"])
    trust_s = nas["trust_state"]
    box.add_kv(
        t("cc_system.vault_target_mounts_lbl"),
        f"{Txt(tgt_txt, role='focus')}   {Txt(cnt_txt, role='vitality' if mounted else 'inactive')}",
        right_markup=Txt(t("cc_system.vault_trust_label", p0=trust_s), role=_trust_role(trust_s)),
    )

    vis = str(vcfg.get("mount_visibility", "symlink")).lower()
    vis_lbl = t(f"cc_system.vault_vis_{vis}")
    open_base = (
        ActionBtn(t("cc_system.vault_open_folder_btn"), "cc_sys:vault_open_base", tone="neutral")
        if (mounted and data["has_xdg_open"])
        else ""
    )
    box.add_kv(
        t("cc_system.vault_base_path_lbl"),
        f"{Txt(nas['base'], role='val')}   {Txt(vis_lbl, role='schema')}",
        right_markup=open_base,
    )

    if proto == "SMB":
        c_mode = nas["creds_mode"]
        is_guest = c_mode == "guest"
        mode_pills = " ".join([
            SelectPill(t("cc_system.vault_creds_file_opt"), "cc_sys:vault_creds_mode:file", selected=not is_guest),
            SelectPill(t("cc_system.vault_creds_guest_opt"), "cc_sys:vault_creds_mode:guest", selected=is_guest),
        ])
        if is_guest:
            st_badge = ""
            auth_btn = ""
        elif c_mode == "saved":
            st_badge = StateBadge(True, t("cc_system.vault_creds_saved"))
            auth_btn = ActionBtn(t("cc_system.vault_creds_auth_btn"), "cc_sys:vault_creds_auth", tone="neutral")
        else:
            st_badge = Badge(t("cc_system.vault_creds_not_set"), style="warn")
            auth_btn = ActionBtn(t("cc_system.vault_creds_auth_btn"), "cc_sys:vault_creds_auth", tone="apply")
        box.add_kv(
            t("cc_system.vault_auth_lbl"),
            f"{mode_pills}  {st_badge}".rstrip(),
            right_markup=auth_btn,
        )

    box.add_row(" ")
    return box


def _build_shares_card(canvas: Canvas, nas: dict, vcfg: dict, data: dict) -> Box:
    rows_data = nas["share_rows"]
    sel_idx = data["selected_share_idx"]
    confirm_idx = data["confirm_rm_idx"]
    box = Box(
        t("cc_system.vault_shares_sec", p0=len(rows_data)),
        icon="\uf07c",
        actions=[ActionBtn(t("cc_system.vault_share_add_btn"), "cc_sys:vault_share_add", tone="apply")],
        highlighted=False,
    )
    box.add_row(" ")

    if rows_data:
        t_rows = []
        rm_lbl = t("cc_system.vault_share_remove_btn")
        rm_w = max(len(rm_lbl), 6)
        for row in rows_data:
            idx = row["idx"]
            is_sel = idx == sel_idx
            st_cell = StateBadge(True, t("common.on")) if row["mounted"] else Txt(f"\u25cb {t('common.off')}", role="inactive")
            if confirm_idx == idx:
                act_cell = (
                    f"{ActionBtn(t('cc_system.vault_remove_confirm'), f'cc_sys:vault_share_rm_do:{idx}', tone='danger_soft')} "
                    f"{ActionBtn('x', 'cc_sys:vault_share_rm_cancel', tone='neutral')}"
                )
            else:
                act_cell = ActionBtn(rm_lbl, f"cc_sys:vault_share_rm_ask:{idx}", tone="danger_soft", min_chars=rm_w)
            t_rows.append([
                InspectorCell(canvas.truncate_plain(row["remote"], 21), f"cc_sys:vault_share_sel:{idx}", selected=is_sel),
                Txt(canvas.truncate_plain(row["target_mp"], 34), role="val"),
                st_cell,
                Txt(row["usage_str"], role="schema"),
                act_cell,
            ])
        box.add_table(
            headers=[
                t("cc_system.vault_col_share"),
                t("cc_system.vault_col_mp"),
                t("cc_system.col_state"),
                t("cc_system.vault_col_usage"),
                t("cc_system.col_actions"),
            ],
            widths=[24, "flex", 9, 13, 16],
            rows=t_rows,
            aligns=["left", "left", "left", "right", "right"],
        )

        if 0 <= sel_idx < len(rows_data):
            sel_row = rows_data[sel_idx]
            box.add_row(" ")
            box.add_section(t("cc_system.vault_insp_sec", p0=sel_row["remote"]))
            proto = nas["protocol"]
            host = nas["host"] or "-"
            if proto == "smb":
                src_s = f"//{host}/{sel_row['remote']}"
            else:
                exp = sel_row["remote"] if sel_row["remote"].startswith("/") else f"/{sel_row['remote']}"
                src_s = f"{host}:{exp}"
            open_btn = (
                ActionBtn(t("cc_system.vault_open_btn"), f"cc_sys:vault_open_fm:{sel_idx}", tone="neutral")
                if (sel_row["mounted"] and data["has_xdg_open"])
                else ""
            )
            box.add_kv(t("cc_system.vault_insp_source"), Txt(src_s, role="val"), right_markup=open_btn)

            mp_fld = InlineField(
                "vault_share_mp",
                f"cc_sys:vault_share_edit_mp:{sel_idx}",
                value=sel_row["target_mp"],
                placeholder="/mount/..",
                width=30,
            )
            reset_btn = (
                ActionBtn(t("cc_system.vault_insp_reset_btn"), f"cc_sys:vault_share_reset_mp:{sel_idx}", tone="warn")
                if sel_row["is_custom_mp"]
                else ""
            )
            box.add_kv(t("cc_system.vault_insp_mp"), mp_fld, right_markup=reset_btn)

            vis = str(vcfg.get("mount_visibility", "symlink")).lower()
            if vis in ("symlink", "both") and sel_row["symlink_path"]:
                acc_val = f"{sel_row['symlink_path']}  ->  {sel_row['target_mp']}"
                if vis == "both":
                    acc_val = f"{acc_val}  ·  x-gvfs-show"
            elif vis == "filemanager":
                acc_val = t("cc_system.vault_access_fm_only")
            else:
                acc_val = t("cc_system.vault_access_hidden")
            box.add_kv(t("cc_system.vault_insp_access"), Txt(acc_val, role="schema"))
    else:
        box.add_row(f"  {Txt(t('cc_system.vault_shares_empty'), role='inactive')}")

    box.add_row(" ")
    return box


def _build_connection_card(vcfg: dict) -> Box:
    cur_p = str(vcfg.get("protocol", "smb")).lower()
    box = Box(t("cc_system.vault_conn_sec"), icon="\uf6ff", highlighted=False)
    box.add_row(" ")

    p_pills = " ".join(
        SelectPill(p.upper(), f"cc_sys:vault_cfg:protocol:{p}", selected=(cur_p == p))
        for p in ("smb", "nfs", "ssh", "local")
    )
    box.add_kv(t("cc_system.vault_cfg_proto"), p_pills)

    if cur_p != "local":
        host_fld = InlineField("vault_host", "cc_sys:vault_host_open", value=str(vcfg.get("host", "")), placeholder=t("cc_system.vault_ph_host"), width=24)
        port_fld = InlineField("vault_port", "cc_sys:vault_port_open", value=str(vcfg.get("port", "")), placeholder=t("cc_system.vault_ph_port"), width=9)
        box.add_kv(t("cc_system.vault_cfg_host_port"), f"{host_fld}   {Txt(':', role='schema')}   {port_fld}")

    mp_fld = InlineField("vault_mp", "cc_sys:vault_mp_open", value=str(vcfg.get("mountpoint", "/nas")), placeholder=t("cc_system.vault_ph_mp"), width=32)
    box.add_kv(t("cc_system.vault_cfg_base_mp"), mp_fld)

    if cur_p == "smb":
        cur_ver = str(vcfg.get("smb_version", "auto")).lower()
        ver_pills = " ".join(
            SelectPill(t("cc_system.vault_smb_auto") if ver == "auto" else ver, f"cc_sys:vault_cfg:smb_version:{ver}", selected=(cur_ver == ver))
            for ver in ("auto", "3.1.1", "3.0", "2.1")
        )
        opts_fld = InlineField("vault_mopts", "cc_sys:vault_mopts_open", value=str(vcfg.get("mount_opts", "")), placeholder=t("cc_system.vault_ph_mopts"), width=20)
        box.add_kv(t("cc_system.vault_cfg_smb_opts"), f"{ver_pills}   {opts_fld}")
    elif cur_p == "nfs":
        opts_fld = InlineField("vault_mopts", "cc_sys:vault_mopts_open", value=str(vcfg.get("mount_opts", "")), placeholder=t("cc_system.vault_ph_mopts"), width=32)
        box.add_kv(t("cc_system.vault_cfg_mount_opts"), opts_fld)
    elif cur_p == "ssh":
        u_fld = InlineField("vault_user", "cc_sys:vault_user_open", value=str(vcfg.get("ssh_user", "")), placeholder=t("cc_system.vault_ph_user"), width=14)
        rp_fld = InlineField("vault_rpath", "cc_sys:vault_rpath_open", value=str(vcfg.get("remote_path", "")), placeholder=t("cc_system.vault_ph_rpath"), width=26)
        box.add_kv(t("cc_system.vault_cfg_ssh"), f"{u_fld}   {Txt('@', role='schema')}   {rp_fld}")
        opts_fld = InlineField("vault_mopts", "cc_sys:vault_mopts_open", value=str(vcfg.get("mount_opts", "")), placeholder=t("cc_system.vault_ph_mopts"), width=32)
        box.add_kv(t("cc_system.vault_cfg_mount_opts"), opts_fld)

    box.add_row(" ")
    return box


def _build_visibility_and_paths_card(vcfg: dict, icfg: dict) -> Box:
    box = Box(t("cc_system.vault_vis_sec"), icon="\uf013", highlighted=False)
    box.add_row(" ")

    cur_vis = str(vcfg.get("mount_visibility", "symlink")).lower()
    vis_pills = " ".join(
        SelectPill(t(lbl_k), f"cc_sys:vault_cfg:mount_visibility:{vid}", selected=(cur_vis == vid))
        for vid, lbl_k in (
            ("symlink", "cc_system.vault_vis_symlink"),
            ("filemanager", "cc_system.vault_vis_filemanager"),
            ("both", "cc_system.vault_vis_both"),
            ("none", "cc_system.vault_vis_none"),
        )
    )
    box.add_kv(t("cc_system.vault_cfg_visibility"), vis_pills)

    if cur_vis in ("symlink", "both"):
        sym_fld = InlineField("vault_symdir", "cc_sys:vault_symdir_open", value=str(vcfg.get("user_symlink_dir", "~/NAS")), placeholder=t("cc_system.vault_ph_symdir"), width=28)
        box.add_kv(t("cc_system.vault_cfg_symdir"), sym_fld)

    sub_fld = InlineField("vault_subdir", "cc_sys:vault_subdir_open", value=str(vcfg.get("backup_root_subdir", "")), placeholder=t("cc_system.vault_ph_subdir"), width=40)
    box.add_kv(t("cc_system.vault_cfg_backup_sub"), sub_fld)

    pkg_fld = InlineField("vault_pkgdir", "cc_sys:vault_pkgdir_open", value=str(vcfg.get("pkglist_subdir", "")), placeholder=t("cc_system.vault_ph_subdir"), width=40)
    box.add_kv(t("cc_system.vault_cfg_pkg_sub"), pkg_fld)

    al_fld = InlineField("vault_alias", "cc_sys:vault_alias_open", value=str(icfg.get("sys_alias", "")), placeholder=t("cc_system.vault_ph_alias"), width=14)
    fo_fld = InlineField("vault_folder", "cc_sys:vault_folder_open", value=str(icfg.get("sys_folder", "")), placeholder=t("cc_system.vault_ph_folder"), width=24)
    box.add_kv(t("cc_system.vault_cfg_identity"), f"{al_fld}   {Txt('/', role='schema')}   {fo_fld}")

    box.add_row(" ")
    return box


def build_cards(cc_panel, canvas: Canvas) -> list:
    data = collect_vault_page_data()
    nas = data["nas"]
    vcfg = data["vault_cfg"]
    icfg = data["ident_cfg"]

    cards = feedback_cards("system", "cc_system.system_control_status") + build_privilege_modal_card(cc_panel, canvas)
    cards.append(_build_overview_card(nas, vcfg, data))
    if nas["protocol"] in ("smb", "nfs"):
        cards.append(_build_shares_card(canvas, nas, vcfg, data))
    cards.append(_build_connection_card(vcfg))
    cards.append(_build_visibility_and_paths_card(vcfg, icfg))
    return cards
