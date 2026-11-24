#!/usr/bin/env python3
from core.i18n import t
from ui.canvas import Canvas
from ui.kit import ActionBtn, Badge, Box, StateBadge, Txt
from panels.cc_engine_telemetry import (
    _feedback_cards,
    _collect_backup_data,
)


def _build_backup_standard(cc_panel, canvas: Canvas) -> list:
    bk = _collect_backup_data()
    dr = bk["dr_env"]
    booted_slot = dr.get("BOOTED_SLOT", "main") or "main"
    cards = _feedback_cards()

    # 1. LVM Boot Environments (nc-snapshot.sh) & Golden Images Catalog
    snap_actions = [
        ActionBtn(t("cc_system.create_snapshot"), "cc_sys:snap_create", tone="apply"),
        ActionBtn(t("cc_system.btn_status"), "cc_sys:snap_status", tone="neutral"),
        ActionBtn(t("cc_system.btn_health"), "cc_sys:snap_health", tone="neutral"),
        ActionBtn(t("cc_system.btn_delete"), "cc_sys:snap_delete", tone="danger_soft"),
    ]
    if booted_slot != "main":
        snap_actions.append(ActionBtn(t("cc_system.btn_promote"), "cc_sys:snap_promote", tone="warn"))

    snap_box = Box(
        t("cc_system.snapshot_lvm_nc_snapshot_sh_golden"),
        icon="\uf1c0",
        actions=snap_actions,
        highlighted=False,
    )
    snap_box.add_row(" ")
    snap_box.add_kv(
        t("cc_system.golden_recovery"),
        StateBadge(bk["duskcity_exists"], t("cc_system.duskcity_ready"), t("cc_system.not_detected_on_esp")),
    )
    boot_env_widget = (
        StateBadge(True, t("cc_system.env_main"))
        if booted_slot == "main"
        else Badge(t("cc_system.env_snapshot_booted", p0=booted_slot.upper()), style="warn")
    )
    snap_box.add_kv(t("cc_system.boot_environment"), boot_env_widget)
    snap_box.add_kv(t("cc_system.vg_free_space"), Txt(dr.get("VG_FREE", "-"), role="val"))

    slot_rows = []
    for sl in bk["snap_slots"]:
        st_badge = StateBadge(sl["present"], t("cc_system.slot_ready"), t("cc_system.slot_absent"))
        slot_rows.append([
            Txt(sl["slot"].upper(), role="focus"),
            st_badge,
            Txt(sl["created"][:16] if sl["created"] != "-" else "-", role="val" if sl["present"] else "inactive"),
            Txt(canvas.truncate_plain(sl["kernel"], 22), role="val" if sl["present"] else "inactive"),
        ])
    snap_box.add_table(
        headers=[t("cc_system.col_slot"), t("cc_system.col_state"), t("cc_system.col_created"), t("cc_system.col_kernel")],
        widths=[10, 14, 20, "flex"],
        rows=slot_rows,
    )

    if bk["nas_mounted"]:
        imgs = bk["golden_images"]

        def _build_golden_cat(bx: Box):
            if not imgs:
                bx.add_row(f"  {Txt(t('cc_system.no_golden_images'), role='schema')}")
                return
            img_rows = []
            for im in imgs:
                img_rows.append([
                    Txt(canvas.truncate_plain(im["title"], 22), role="focus"),
                    Txt(im["date"], role="val"),
                    Txt(im["vg"], role="schema"),
                    Txt(canvas.truncate_plain(im["desc"], 32), role="val"),
                ])
            bx.add_table(
                headers=[t("cc_system.col_title"), t("cc_system.col_date"), t("cc_system.col_vg"), t("cc_system.col_img_desc")],
                widths=[22, 12, 8, "flex"],
                rows=img_rows,
            )

        snap_box.add_collapsible(
            cc_panel,
            "bk_golden_images",
            t("cc_system.golden_images_catalog", p0=len(imgs)),
            _build_golden_cat,
        )

    snap_box.add_row(" ")
    cards.append(snap_box)

    # 2. Tower of Hanoi Config Backup (nc-nas-conf.sh)
    han_actions = [
        ActionBtn(t("cc_system.hanoi_sync_btn"), "cc_sys:hanoi_backup", tone="apply" if bk["nas_mounted"] else "inactive"),
        ActionBtn(t("cc_system.hanoi_check_btn"), "cc_sys:hanoi_check", tone="neutral"),
        ActionBtn(t("cc_system.hanoi_history_btn"), "cc_sys:hanoi_history", tone="neutral"),
        ActionBtn(t("cc_system.hanoi_list_btn"), "cc_sys:hanoi_list", tone="neutral"),
    ]
    han_box = Box(
        t("cc_system.tower_of_hanoi_config_backup_nc_na"),
        icon="\uf0c7",
        actions=han_actions,
        highlighted=False,
    )
    han_box.add_row(" ")
    nas_badge = StateBadge(True, t("cc_system.nas_mounted"), on_tone="ok") if bk["nas_mounted"] else Badge(t("cc_system.nas_required"), style="warn")
    han_right = "" if bk["nas_mounted"] else ActionBtn(t("cc_system.mount_nas"), "cc_sys:mount_nas", tone="apply")
    han_box.add_kv(t("cc_system.nas_mount_gate"), nas_badge, right_markup=han_right)

    hs = bk["hanoi_summary"]
    if hs["has_data"]:
        han_box.add_kv(t("cc_system.hanoi_last_check"), Txt(f"{hs['last_check']} ({hs['sys_alias']})", role="val"))
        in_sync = (hs["push_total"] == 0 and hs["pull_total"] == 0)
        drift_badge = (
            StateBadge(True, t("cc_system.hanoi_in_sync"))
            if in_sync
            else Badge(t("cc_system.hanoi_pending", p0=hs["push_total"], p1=hs["pull_total"]), style="warn")
        )
        han_box.add_kv(t("cc_system.hanoi_drift_lbl"), drift_badge)
    else:
        han_box.add_kv(t("cc_system.hanoi_last_check"), Txt(t("cc_system.hanoi_no_status"), role="inactive"))

    han_box.add_row(" ")
    cards.append(han_box)
    return cards


def build_cards(cc_panel, canvas: Canvas) -> list:
    return _build_backup_standard(cc_panel, canvas)
