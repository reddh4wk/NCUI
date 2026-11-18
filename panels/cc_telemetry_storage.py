#!/usr/bin/env python3
import os
from core.i18n import t
from ui.canvas import Canvas
from ui.kit import ActionBtn, Box, StateBadge, Txt
from panels.cc_engine_telemetry import (
    _feedback_cards,
    _collect_storage_data,
)


def _use_role(use_s: str) -> str:
    s = (use_s or "").strip().rstrip("%")
    try:
        v = float(s)
    except Exception:
        return "inactive"
    if v >= 90.0:
        return "fault"
    if v >= 75.0:
        return "interrupt"
    if v >= 50.0:
        return "attention"
    return "val"


def _build_storage_standard(cc_panel, canvas: Canvas) -> list:
    st = _collect_storage_data()
    cards = _feedback_cards()

    nas_box = Box(
        t("cc_system.nasus_storage", p0=st['nas_host']),
        icon="\uf233",
        actions=[StateBadge(st["nas_mounted"], t("cc_system.mounted"), t("cc_system.unmounted")), ActionBtn(t("cc_system.mount_nas"), "cc_sys:mount_nas", tone="apply")],
        highlighted=False,
    )
    nas_box.add_row(" ")
    nas_box.add_kv(t("cc_system.mount_target"), Txt(t("cc_system.nas_smb_cifs"), role="val"))
    nas_box.add_kv(t("cc_system.smart_health"), ActionBtn(t("cc_system.run_nvme_smart_check"), "cc_sys:smart_check", tone="neutral"), requires=["smartctl"])
    nas_box.add_row(" ")
    cards.append(nas_box)

    b_box = Box(t("cc_system.block_devices_lvm", p0=len(st['blk'])), icon="\uf0a0", highlighted=False)
    b_box.add_row(" ")
    rows = []
    for d in st["blk"][:12]:
        use_s = d.get("fsuse%") or "-"
        mp = d.get("mountpoint") or "-"
        rows.append([
            Txt(d.get("name", "")[:16], role="focus"),
            Txt(d.get("size", ""), role="val"),
            Txt(d.get("fstype") or d.get("type", ""), role="schema"),
            Txt(canvas.truncate_plain(mp, 24), role="val" if d.get("mountpoint") else "inactive"),
            Txt(use_s, role=_use_role(use_s)),
        ])
    b_box.add_table(headers=[t("cc_system.device"), t("cc_system.size"), t("cc_system.fs"), t("cc_system.mountpoint"), t("cc_system.use")], widths=[16, 8, 12, 24, "flex"], rows=rows)

    if st["lvs"]:
        def _build_lvm(bx: Box):
            lv_rows = []
            for r in st["lvs"][:10]:
                if len(r) < 3:
                    continue
                d_pct = r[3] if len(r) > 3 and r[3] else "-"
                lv_rows.append([Txt(r[0], role="schema"), Txt(r[1], role="focus"), Txt(r[2], role="val"), Txt(d_pct, role="inactive" if d_pct in ("-", "N/A") else "val")])
            bx.add_table(headers=["VG", t("cc_system.lv"), t("cc_system.size"), t("cc_system.data")], widths=[14, 22, 10, "flex"], rows=lv_rows)
        b_box.add_collapsible(cc_panel, "sys_lvm", t("cc_system.lvm_logical_volumes", p0=len(st['lvs'])), _build_lvm)

    b_box.add_row(" ")
    cards.append(b_box)
    return cards


def build_cards(cc_panel, canvas: Canvas) -> list:
    return _build_storage_standard(cc_panel, canvas)
