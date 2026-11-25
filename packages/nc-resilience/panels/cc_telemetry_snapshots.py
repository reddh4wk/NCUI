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
    InlineField,
    InspectorCell,
    ProgressBar,
    SelectPill,
    StateBadge,
    Txt,
)
from panels.cc_engine_telemetry import _build_guide_box, _collect_backup_data, _feedback_cards, _read_env_kv


def _compact_created_ts(raw_ts: str) -> str:
    s = str(raw_ts or "").strip()
    if len(s) >= 16 and s[4] == "-" and s[7] == "-":
        return s[5:16]
    return s[:16] if s and s != "-" else "-"


def build_cards(cc_panel, canvas: Canvas) -> list:
    bk = _collect_backup_data()
    dr = bk["dr_env"]
    cfg = bk["snap_cfg"]
    backend = cfg.get("backend") or dr.get("BACKEND", "lvm_uki") or "lvm_uki"
    booted_slot = dr.get("BOOTED_SLOT", "main") or "main"
    sel_slot = str(STATE.get("system", "snap_selected_slot", "auto") or "auto")
    if sel_slot not in ("auto", "stable"):
        sel_slot = "auto"

    cards = _feedback_cards() + build_privilege_modal_card(cc_panel, canvas)
    snap_stat = f"{'Btrfs' if backend == 'btrfs' else 'LVM + UKI'} · {booted_slot.upper()}"
    cards.extend(_build_guide_box(cc_panel, canvas, "guide_snapshots_open", "guide_snap", "snapshots", snap_stat))

    # 1. Boot Environments & Snapshot Slots (nc-snap)
    backend_lbl = t("cc_system.snap_backend_btrfs") if backend == "btrfs" else t("cc_system.snap_backend_lvm")
    snap_actions = [
        Badge(t("cc_system.snap_busy_badge", p0=bk["busy_slot"].upper()), style="warn")
        if bk["busy_slot"]
        else Txt(backend_lbl, role="schema"),
    ]
    if not bk["priv_ok"]:
        snap_actions.append(ActionBtn(t("cc_system.btn_authorize"), "cc_sys:snap_auth", tone="apply"))
    snap_actions.append(ActionBtn(t("cc_system.btn_health"), "cc_sys:snap_health", tone="neutral"))
    if booted_slot != "main":
        if bk["confirm_promote"]:
            snap_actions.append(ActionBtn(t("cc_system.snap_confirm_promote"), "cc_sys:snap_promote_do", tone="warn"))
            snap_actions.append(ActionBtn(t("common.cancel"), "cc_sys:snap_promote_cancel", tone="neutral"))
        else:
            snap_actions.append(ActionBtn(t("cc_system.btn_promote"), "cc_sys:snap_promote_ask", tone="warn"))

    snap_box = Box(
        t("cc_system.snap_box_title"),
        icon="\uf1c0",
        actions=snap_actions,
        highlighted=False,
    )
    snap_box.add_row(" ")

    boot_env_widget = (
        StateBadge(True, t("cc_system.env_main"))
        if booted_slot == "main"
        else Badge(t("cc_system.env_snapshot_booted", p0=booted_slot.upper()), style="warn")
    )
    snap_box.add_kv(
        t("cc_system.boot_environment"),
        boot_env_widget,
        right_markup=StateBadge(bk["duskcity_exists"], t("cc_system.duskcity_ready"), t("cc_system.not_detected_on_esp")),
    )

    pool_free = dr.get("VG_FREE", "-")
    if backend == "btrfs":
        pool_detail = f"{cfg.get('btrfs_subvol', '@')} · /.snapshots"
    else:
        vols = [v for v in (cfg.get("root_lv", "root"), cfg.get("var_lv", ""), cfg.get("home_lv", "")) if v]
        pool_detail = f"{cfg.get('vg_name', 'vg0')} ({', '.join(vols)})"
    snap_box.add_kv(
        t("cc_system.pool_free_space"),
        Txt(pool_free, role="val"),
        right_markup=Txt(pool_detail, role="schema"),
    )

    hook_on = bool(bk["pacman_hook"])
    snap_box.add_kv(
        t("cc_system.snap_hook_lbl"),
        f"{SelectPill(t('cc_system.snap_hook_on'), 'cc_sys:snap_hook:on', selected=hook_on)} "
        f"{SelectPill(t('cc_system.snap_hook_off'), 'cc_sys:snap_hook:off', selected=(not hook_on))}",
    )

    note_field = InlineField(
        "snap_note_draft",
        "cc_sys:snap_note_open",
        bk["note_draft"],
        placeholder=t("cc_system.snap_note_ph"),
        empty_label=t("cc_system.snap_note_set"),
        width=28,
        icon="\u270e" if bk["note_draft"] else "",
    )
    note_row_right = ActionBtn("x", "cc_sys:snap_note_clear", tone="danger_soft") if bk["note_draft"] else ""
    snap_box.add_kv(t("cc_system.snap_note_lbl"), note_field, right_markup=note_row_right)

    if bk["broken_lvs"]:
        b0 = bk["broken_lvs"][0]
        b0_lv = b0["lv"]
        b0_sz = b0["size"]
        extra = f" (+{len(bk['broken_lvs']) - 1})" if len(bk["broken_lvs"]) > 1 else ""
        if bk["confirm_purge"] == b0_lv:
            p_right = (
                f"{ActionBtn(t('cc_system.snap_confirm_purge'), f'cc_sys:snap_purge_do:{b0_lv}', tone='danger_soft')} "
                f"{ActionBtn(t('common.cancel'), 'cc_sys:snap_purge_cancel', tone='neutral')}"
            )
        else:
            p_right = ActionBtn(t("cc_system.snap_purge_btn"), f"cc_sys:snap_purge_ask:{b0_lv}", tone="danger_soft")
        snap_box.add_kv(
            t("cc_system.snap_broken_lbl"),
            Txt(f"{b0_lv} ({b0_sz}){extra}", role="attention"),
            right_markup=p_right,
        )

    slot_rows = []
    sel_slot_obj = bk["snap_slots"][0]
    for sl in bk["snap_slots"]:
        slot = sl["slot"]
        if slot == sel_slot:
            sel_slot_obj = sl
        st_badge = StateBadge(sl["present"], t("cc_system.slot_ready"), t("cc_system.slot_absent"))
        if sl["present"]:
            ts_short = _compact_created_ts(sl["created"])
            meta_txt = f"{ts_short} · {sl['kernel']}"
            if sl["note"] and sl["note"] not in ("-", "N/A"):
                meta_txt = f"{meta_txt} ({sl['note']})"
            info_cell = Txt(canvas.truncate_plain(meta_txt, 30), role="val")

            uki_tag = t("cc_system.snap_uki_ok") if sl["uki_present"] else t("cc_system.snap_uki_missing")
            if backend == "btrfs":
                storage_cell = Txt(f"subvol · {uki_tag}", role="vitality" if sl["uki_present"] else "attention")
            elif sl["lv_health"] != "ACTIVE":
                storage_cell = Txt(t("cc_system.snap_cow_crit"), role="fault")
            else:
                pct_f = sl["max_cow_pct"]
                cow_role = "fault" if pct_f >= 85.0 else ("attention" if pct_f >= 75.0 or not sl["uki_present"] else "vitality")
                storage_cell = f"{ProgressBar(pct_f, width=7)} {Txt(f'{pct_f:3.0f}% · {uki_tag}', role=cow_role)}"
        else:
            info_cell = Txt(t("cc_system.snap_slot_empty"), role="inactive")
            storage_cell = Txt("-", role="inactive")

        if bk["confirm_del"] == slot:
            act_cell = (
                f"{ActionBtn(t('cc_system.snap_confirm_del'), f'cc_sys:snap_del_do:{slot}', tone='danger_soft')} "
                f"{ActionBtn(t('common.cancel'), 'cc_sys:snap_del_cancel', tone='neutral')}"
            )
        else:
            btns = [
                ActionBtn(
                    t("cc_system.snap_update_btn") if sl["present"] else t("cc_system.snap_capture_btn"),
                    f"cc_sys:snap_capture:{slot}",
                    tone="apply",
                )
            ]
            if sl["present"]:
                if backend == "lvm_uki":
                    btns.append(ActionBtn(t("cc_system.snap_extend_btn"), f"cc_sys:snap_extend:{slot}", tone="warn"))
                if booted_slot != slot:
                    btns.append(ActionBtn(t("cc_system.btn_delete"), f"cc_sys:snap_del_ask:{slot}", tone="danger_soft"))
            act_cell = " ".join(btns)

        slot_rows.append([
            InspectorCell(slot.upper(), f"cc_sys:snap_select:{slot}", selected=(slot == sel_slot)),
            st_badge,
            info_cell,
            storage_cell,
            act_cell,
        ])

    snap_box.add_table(
        headers=[
            t("cc_system.col_slot"),
            t("cc_system.col_state"),
            t("cc_system.col_created"),
            t("cc_system.snap_col_storage"),
            t("cc_system.actions"),
        ],
        widths=[10, 10, "flex", 22, 26],
        rows=slot_rows,
        aligns=["left", "left", "left", "left", "right"],
    )

    # Selected Slot Detail Inspector
    snap_box.add_section(t("cc_system.snap_inspector_sec", p0=sel_slot_obj["slot"].upper()))
    if sel_slot_obj["present"]:
        esp_m = cfg.get("esp_mount", "/boot")
        uki_full = os.path.join(esp_m, "EFI", "Linux", sel_slot_obj["uki_file"])
        pkgs_path = uki_full[:-4] + ".pkgs" if uki_full.endswith(".efi") else ""
        snap_box.add_kv(
            t("cc_system.snap_insp_uki"),
            Txt(uki_full, role="val"),
            right_markup=StateBadge(sel_slot_obj["uki_present"], t("cc_system.snap_uki_ok"), t("cc_system.snap_uki_missing")),
        )
        snap_box.add_kv(
            t("cc_system.snap_insp_vols"),
            Txt(sel_slot_obj.get("cow_summary") or sel_slot_obj.get("volumes") or "-", role="val"),
        )
        snap_box.add_kv(
            t("cc_system.snap_insp_meta"),
            Txt(f"{sel_slot_obj['created']} · {sel_slot_obj['kernel']}", role="val"),
            right_markup=Txt(sel_slot_obj.get("note") or "-", role="schema"),
        )
        if pkgs_path:
            snap_box.add_kv(t("cc_system.snap_insp_pkgs"), Txt(pkgs_path, role="schema"))
    else:
        snap_box.add_row(f"  {Txt(t('cc_system.snap_slot_empty'), role='inactive')}")

    snap_box.add_row(" ")
    cards.append(snap_box)

    # 2. Snapshot Engine Configuration (/etc/ncui/resilience.toml)
    cfg_box = Box(t("cc_system.snap_cfg_sec"), icon="\uf013", highlighted=False)
    cfg_box.add_row(" ")
    be = cfg.get("backend", "lvm_uki")
    esp = cfg.get("esp_mount", "/boot")
    pref = cfg.get("uki_prefix", "linux")
    cfg_box.add_kv(
        t("cc_system.snap_cfg_backend"),
        f"{SelectPill(t('cc_system.snap_backend_lvm'), 'cc_sys:snap_cfg:backend:lvm_uki', selected=(be == 'lvm_uki'))} "
        f"{SelectPill(t('cc_system.snap_backend_btrfs'), 'cc_sys:snap_cfg:backend:btrfs', selected=(be == 'btrfs'))}",
    )
    pref_field = InlineField("snap_prefix", "cc_sys:snap_prefix_open", pref, placeholder=t("cc_system.snap_prefix_ph"), width=18)
    cfg_box.add_kv(
        t("cc_system.snap_cfg_esp_prefix"),
        f"{SelectPill('/boot', 'cc_sys:snap_cfg:esp_mount:/boot', selected=(esp == '/boot'))} "
        f"{SelectPill('/efi', 'cc_sys:snap_cfg:esp_mount:/efi', selected=(esp == '/efi'))}  {pref_field}",
        right_markup=Txt(f"{pref}-auto.efi / {pref}-stable.efi", role="schema"),
    )
    if be == "lvm_uki":
        vg_field = InlineField("snap_vg", "cc_sys:snap_vg_open", cfg.get("vg_name", "vg0"), placeholder=t("cc_system.snap_vg_ph"), width=14)
        has_var = bool(cfg.get("var_lv"))
        has_home = bool(cfg.get("home_lv"))
        cfg_box.add_kv(
            t("cc_system.snap_cfg_vg_vols"),
            f"{vg_field}  "
            f"{SelectPill('/var LV', 'cc_sys:snap_cfg:var_lv:' + ('' if has_var else 'var'), selected=has_var)} "
            f"{SelectPill('/home LV', 'cc_sys:snap_cfg:home_lv:' + ('' if has_home else 'home'), selected=has_home)}",
        )
        r_sz = str(cfg.get("snap_root_size", "15G")).upper()
        v_sz = str(cfg.get("snap_var_size", "5G")).upper()
        root_pills = " ".join(SelectPill(s, f"cc_sys:snap_cfg:snap_root_size:{s}", selected=(r_sz == s)) for s in ("10G", "15G", "20G", "30G"))
        var_pills = " ".join(SelectPill(s, f"cc_sys:snap_cfg:snap_var_size:{s}", selected=(v_sz == s)) for s in ("5G", "10G"))
        cfg_box.add_kv(t("cc_system.snap_cfg_cow_sizes"), f"{Txt('root', role='schema')} {root_pills}  {Txt('var', role='schema')} {var_pills}")
    else:
        subv = str(cfg.get("btrfs_subvol", "@"))
        subv_field = InlineField("snap_subvol", "cc_sys:snap_subvol_open", subv, placeholder=t("cc_system.snap_subvol_ph"), width=14, selected=(subv not in ("@", "root")))
        cfg_box.add_kv(
            t("cc_system.snap_cfg_btrfs_sub"),
            f"{SelectPill('@', 'cc_sys:snap_cfg:btrfs_subvol:@', selected=(subv == '@'))} "
            f"{SelectPill('root', 'cc_sys:snap_cfg:btrfs_subvol:root', selected=(subv == 'root'))} {subv_field}",
        )
    cfg_box.add_row(" ")
    cards.append(cfg_box)
    return cards
