#!/usr/bin/env python3
import os
from core.i18n import t
from ui.canvas import Canvas
from ui.kit import ActionBtn, Badge, Box, StateBadge, Txt
from panels.cc_engine_telemetry import (
    _feedback_cards,
    _collect_backup_data,
)

def _build_backup_standard(cc_panel, canvas: Canvas) -> list:
    bk = _collect_backup_data()
    cards = _feedback_cards()

    snap_box = Box(
        t("cc_system.snapshot_lvm_nc_snapshot_sh_golden"),
        icon="\uf1c0",
        actions=[ActionBtn(t("cc_system.create_snapshot"), "cc_sys:snap_create", tone="apply")],
        highlighted=False,
    )
    snap_box.add_row(" ")
    nas_badge = StateBadge(True, t("cc_system.nas_mounted"), on_tone="ok") if bk["nas_mounted"] else Badge(t("cc_system.nas_required"), style="warn")
    snap_box.add_kv(t("cc_system.nas_mount_gate"), nas_badge)
    snap_box.add_kv(t("cc_system.golden_recovery"), StateBadge(bk["duskcity_exists"], t("cc_system.duskcity_ready"), t("cc_system.not_detected_on_esp")))
    snap_box.add_kv(t("cc_system.lvm_snapshots"), Txt(t("cc_system.recorded_in_var_lib_nightcity_snap", p0=len(bk['snaps'])), role="val"))
    snap_box.add_row(" ")
    cards.append(snap_box)

    han_box = Box(
        t("cc_system.tower_of_hanoi_config_backup_nc_na"),
        icon="\uf0c7",
        actions=[ActionBtn(t("cc_system.run_hanoi_backup"), "cc_sys:hanoi_backup", tone="apply" if bk["nas_mounted"] else "inactive")],
        highlighted=False,
    )
    han_box.add_row(" ")
    if not bk["nas_mounted"]:
        han_box.add_row(f"  {Txt(t('cc_system.nas_10_10_0_20_not_mounted_mount_n'), role='attention')}",
                        right_markup=ActionBtn(t("cc_system.mount_nas"), "cc_sys:mount_nas", tone="apply"))
    else:
        han_box.add_row(f"  {Txt(t('cc_system.nas_mounted_and_ready_for_tower_of'), role='vitality')}")
    if bk["hanoi_env"]:
        for k, v in list(bk["hanoi_env"].items())[:4]:
            han_box.add_kv(k, Txt(v, role="val"))
    han_box.add_row(" ")
    cards.append(han_box)
    return cards


def build_cards(cc_panel, canvas: Canvas) -> list:
    return _build_backup_standard(cc_panel, canvas)
