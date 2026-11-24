#!/usr/bin/env python3
from core.i18n import t
from core.privileges import build_privilege_modal_card
from ui.canvas import Canvas
from ui.kit import ActionBtn, Badge, Box, SelectPill, StateBadge, Txt
from panels.cc_engine_telemetry import (
    _collect_power_tools_data,
    _feedback_cards,
)


def _build_power_tools_standard(cc_panel, canvas: Canvas) -> list:
    pt = _collect_power_tools_data()
    gm = pt["gaming"]
    nas = pt["nas"]
    dc = pt["duskcity"]

    cards = _feedback_cards()
    cards.extend(build_privilege_modal_card(cc_panel, canvas))

    # 1. Runtime Posture Profile (nc-gaming.sh)
    if gm["available"]:
        gm_acts = []
        if gm["is_gaming"]:
            gm_acts.append(Badge(t("cc_system.gaming_badge"), style="warn"))
        gm_acts.append(ActionBtn(t("cc_system.btn_status"), "cc_sys:gaming_status", tone="neutral"))

        gm_box = Box(
            t("cc_system.runtime_profile_title"),
            icon="\uf11b",
            actions=gm_acts,
            highlighted=False,
        )
        gm_box.add_row(" ")
        mode_pills = " ".join([
            SelectPill(t("cc_system.mode_hardened"), "cc_sys:gaming_mode:off", selected=(not gm["is_gaming"])),
            SelectPill(t("cc_system.mode_gaming"), "cc_sys:gaming_mode:on", selected=gm["is_gaming"]),
        ])
        gm_box.add_kv(t("cc_system.runtime_mode"), mode_pills)
        posture_str = f"ptrace={gm['ptrace']} · max_map={gm['max_map']} · gov={gm['gov']}"
        gm_box.add_kv(t("cc_system.kernel_posture"), Txt(posture_str, role="val"))
        gm_box.add_kv(
            t("cc_system.ntsync_device"),
            StateBadge(gm["ntsync"], t("cc_system.ntsync_active"), t("cc_system.ntsync_absent")),
        )
        gm_box.add_row(" ")
        cards.append(gm_box)

    # 2. System & NAS Quick Tools
    q_box = Box(t("cc_system.quick_utilities_title"), icon="\uf0e7", highlighted=False)
    q_box.add_row(" ")
    if pt["has_pkglist"]:
        q_box.add_kv(
            t("cc_system.pkg_snapshot"),
            ActionBtn(t("cc_system.save_pkglist_btn"), "cc_sys:pkglist_prompt", tone="apply"),
        )
    if pt["has_usbguard"]:
        q_box.add_kv(
            t("cc_system.usbguard_devices"),
            ActionBtn(t("cc_system.list_usb_devices"), "cc_sys:usbguard_list", tone="neutral"),
        )
    nas_lbl = t("cc_system.nas_shares_count", p0=nas["count"], p1=nas["total"], p2=nas["base"])
    nas_badge = StateBadge(nas["mounted"], nas_lbl, nas_lbl)
    nas_btns = " ".join([
        ActionBtn(t("cc_system.mount_nas"), "cc_sys:mount_nas", tone="apply"),
        ActionBtn(t("cc_system.remount_nas"), "cc_sys:nas_remount", tone="warn"),
        ActionBtn(t("cc_system.umount_nas"), "cc_sys:nas_umount", tone="danger_soft"),
    ])
    q_box.add_kv(t("cc_system.nas_quick_mount"), nas_badge, right_markup=nas_btns)
    q_box.add_row(" ")
    cards.append(q_box)

    # 3. Conditional Duskcity Live RAM & Recovery Tools (only when Live RAM or dc-*.sh present)
    if dc["is_live"] or dc["has_commit"] or dc["has_chroot"] or dc["has_golden"]:
        dc_box = Box(t("cc_system.duskcity_live_title"), icon="\uf1c0", highlighted=False)
        dc_box.add_row(" ")
        dc_box.add_kv(
            t("cc_system.duskcity_ram_overlay"),
            StateBadge(dc["is_live"], t("cc_system.duskcity_ram_active"), t("cc_system.duskcity_ram_inactive")),
        )
        dc_btns = []
        if dc["is_live"] and dc["has_commit"]:
            dc_btns.append(ActionBtn(t("cc_system.btn_dc_commit"), "cc_sys:dc_commit", tone="apply"))
            dc_btns.append(ActionBtn(t("cc_system.btn_dc_rebuild"), "cc_sys:dc_commit_rebuild", tone="warn"))
        if dc["has_chroot"]:
            dc_btns.append(ActionBtn(t("cc_system.btn_dc_chroot"), "cc_sys:dc_chroot", tone="neutral"))
        if dc["has_golden"]:
            dc_btns.append(ActionBtn(t("cc_system.btn_dc_save_img"), "cc_sys:dc_golden_save", tone="apply"))
            dc_btns.append(ActionBtn(t("cc_system.btn_dc_restore_img"), "cc_sys:dc_golden_restore", tone="warn"))
        if dc_btns:
            dc_box.add_kv(t("cc_system.duskcity_tools_lbl"), " ".join(dc_btns))
        dc_box.add_row(" ")
        cards.append(dc_box)

    # 4. Discovered Scripts Table
    scripts = pt["scripts"]
    b = Box(t("cc_system.nc_power_tools_usr_local_sbin_nc_u"), icon="\uf0ad", highlighted=False)
    b.add_row(" ")
    rows = []
    for sc in scripts:
        sc_p = sc["path"]
        rows.append([
            Txt(sc["name"], role="focus"),
            Txt(sc["scope"].upper(), role="schema"),
            Txt(canvas.truncate_plain(t(sc["desc_key"]), 40), role="val"),
            f"{ActionBtn(t('cc_system.run_term'), f'cc_sys:run_script:{sc_p}', tone='apply')} {ActionBtn(t('cc_system.args'), f'cc_sys:prompt_script:{sc_p}', tone='neutral')}",
        ])
    b.add_table(
        headers=[t("cc_system.script"), t("cc_system.scope"), t("cc_system.col_desc"), t("cc_system.actions")],
        widths=[22, 6, "flex", 22],
        rows=rows,
        aligns=["left", "left", "left", "right"],
    )
    b.add_row(" ")
    cards.append(b)
    return cards


def build_cards(cc_panel, canvas: Canvas) -> list:
    return _build_power_tools_standard(cc_panel, canvas)
