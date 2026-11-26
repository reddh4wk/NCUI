#!/usr/bin/env python3
import os

from core.i18n import t
from core.privileges import build_privilege_modal_card
from ui.canvas import Canvas
from ui.kit import (
    ActionBtn,
    Badge,
    Box,
    InlineField,
    SelectPill,
    StateBadge,
    Txt,
    feedback_cards,
)
from panels.cc_engine_boot import detect_bootloader
from panels.cc_engine_telemetry import _build_guide_box, _collect_backup_data


def _compact_mtime(raw: str) -> str:
    s = str(raw or "").strip()
    if len(s) >= 16 and s[4] == "-":
        return s[5:16]
    return s or "-"


def _add_rollback_kv(box: Box, data: dict, rec_env: dict) -> None:
    sq_old = rec_env.get("SQUASHFS_OLD_PRESENT") == "1"
    sq_old_sz = rec_env.get("SQUASHFS_OLD_SIZE", "-")
    uki_old = rec_env.get("UKI_OLD_PRESENT") == "1"
    uki_old_sz = rec_env.get("UKI_OLD_SIZE", "-")
    old_ok = sq_old or uki_old

    old_parts = []
    if sq_old:
        old_parts.append(f"squashfs ({sq_old_sz})")
    if uki_old:
        old_parts.append(f"uki ({uki_old_sz})")
    old_desc = " · ".join(old_parts) if old_parts else "-"

    if data["rescue_confirm_rb"]:
        rb_acts = " ".join([
            ActionBtn(t("cc_system.rescue_rb_confirm"), "cc_sys:rescue_rb_do", tone="danger_soft"),
            ActionBtn(t("common.cancel"), "cc_sys:rescue_rb_cancel", tone="neutral"),
        ])
    else:
        rb_acts = ActionBtn(t("cc_system.rescue_rb_btn"), "cc_sys:rescue_rb_ask", tone="warn" if old_ok else "muted")

    box.add_kv(
        t("cc_system.rescue_old_lbl"),
        f"{StateBadge(old_ok, t('cc_system.rescue_old_ready'), t('cc_system.rescue_old_none'))} {Txt(old_desc, role='val')}",
        right_markup=rb_acts,
    )


def _header_probe_actions(data: dict, rec_env: dict, live_badge: bool = False) -> list:
    acts = []
    if data["rescue_busy"]:
        acts.append(Badge(t("cc_system.rescue_busy_badge", p0=data["rescue_busy"]), style="warn"))
    elif live_badge:
        acts.append(Badge(t("cc_system.rescue_live_mode"), style="warn"))
    else:
        upd_ts = _compact_mtime(rec_env.get("UPDATED_AT", ""))
        if upd_ts != "-":
            acts.append(Txt(upd_ts, role="schema"))
    acts.append(ActionBtn(t("cc_system.rescue_manage_btn"), "cc_sys:rescue_manage_term", tone="neutral"))
    acts.append(ActionBtn(t("cc_system.rescue_probe_btn"), "cc_sys:rescue_refresh", tone="neutral"))
    return acts


def _build_host_view(cc_panel, data: dict, rec_env: dict, rec_cfg: dict) -> list:
    """Host OS view: airy 2-card layout for SquashFS image maintenance and UKI/Limine/Rollback."""
    esp_mnt = rec_env.get("ESP_MOUNT", "/boot")
    esp_dev = rec_env.get("ESP_DEV", "")
    esp_free = rec_env.get("ESP_FREE_HUMAN", "-")
    sq_ok = rec_env.get("SQUASHFS_PRESENT") == "1"
    sq_sz = rec_env.get("SQUASHFS_SIZE", "-")
    sq_mtime = _compact_mtime(rec_env.get("SQUASHFS_MTIME", ""))

    # ── Card 1: Live Recovery SquashFS Image ─────────────────────────────────
    box1 = Box(
        t("cc_system.rescue_box_title"),
        icon="\uf0e2",
        actions=_header_probe_actions(data, rec_env),
        highlighted=False,
    )
    box1.add_row(" ")

    esp_loc = f"{esp_mnt} ({esp_dev})" if esp_dev else esp_mnt
    box1.add_kv(
        t("cc_system.rescue_esp_lbl"),
        Txt(f"{esp_loc} · {t('cc_system.rescue_esp_free', p0=esp_free)}", role="val"),
    )

    sq_meta = f"{sq_sz} · {sq_mtime}" if sq_ok else t("cc_system.rescue_sq_missing")
    box1.add_kv(
        t("cc_system.rescue_sq_lbl"),
        f"{StateBadge(sq_ok, t('cc_system.rescue_uki_ok'), t('cc_system.rescue_uki_missing'))} {Txt(sq_meta, role='val')}",
        right_markup=ActionBtn(t("cc_system.rescue_build_sq_btn"), "cc_sys:rescue_build_full_term", tone="apply"),
    )

    if sq_ok:
        maint_btns = " ".join([
            ActionBtn(t("cc_system.rescue_edit_sq_btn"), "cc_sys:rescue_edit_term", tone="neutral"),
            ActionBtn(t("cc_system.rescue_sync_ncui_btn"), "cc_sys:rescue_sync_ncui_term", tone="neutral"),
        ])
        box1.add_kv(
            t("cc_system.rescue_sq_maint_lbl"),
            Txt(t("cc_system.rescue_sq_maint_desc"), role="schema"),
            right_markup=maint_btns,
        )

    box1.add_row(" ")

    # ── Card 2: Signed Recovery UKI, Limine Entry, ESP Rollback & Settings ──
    box2 = Box(
        t("cc_system.rescue_uki_box_title"),
        icon="\uf084",
        highlighted=False,
    )
    box2.add_row(" ")

    uki_ok = data["rescue_uki_ok"] or rec_env.get("UKI_PRESENT") == "1"
    uki_name = rec_env.get("UKI_NAME") or rec_cfg.get("uki_name", "nc-rescue.efi")
    uki_sz = rec_env.get("UKI_SIZE", "-")
    sb_signed = rec_env.get("UKI_SIGNED") == "1"
    if not uki_ok:
        sb_markup = ""
    elif sb_signed:
        sb_markup = f" · {Txt(t('cc_system.rescue_sb_signed'), role='vitality')}"
    else:
        sb_markup = f"  {Badge(t('cc_system.rescue_sb_unsigned'), style='warn')}"
    if data["rescue_confirm_build"]:
        uki_acts = " ".join([
            ActionBtn(t("cc_system.rescue_build_confirm"), "cc_sys:rescue_build_do", tone="warn"),
            ActionBtn(t("common.cancel"), "cc_sys:rescue_build_cancel", tone="neutral"),
        ])
    else:
        uki_acts = ActionBtn(t("cc_system.rescue_build_btn"), "cc_sys:rescue_build_ask", tone="apply")
    box2.add_kv(
        t("cc_system.rescue_uki_lbl"),
        f"{StateBadge(uki_ok, t('cc_system.rescue_uki_ok'), t('cc_system.rescue_uki_missing'))} {Txt(f'{uki_name} · {uki_sz}', role='val')}{sb_markup}",
        right_markup=uki_acts,
    )

    if detect_bootloader().get("kind") == "limine":
        lim_ok = rec_env.get("LIMINE_HAS_RECOVERY") == "1"
        lim_path = rec_env.get("LIMINE_CONF_PATH") or "/boot/limine.conf"
        box2.add_kv(
            t("cc_system.rescue_limine_lbl"),
            f"{StateBadge(lim_ok, t('cc_system.rescue_limine_reg'), t('cc_system.rescue_limine_miss'))} {Txt(lim_path, role='schema')}",
            right_markup=ActionBtn(t("cc_system.rescue_reg_limine_btn"), "cc_sys:rescue_reg_limine", tone="apply" if not lim_ok else "neutral"),
        )

    _add_rollback_kv(box2, data, rec_env)

    def _build_rec_cfg(target_box):
        uki_fld = InlineField(
            "rescue_uki",
            "cc_sys:rescue_uki_open",
            value=str(rec_cfg.get("uki_name", "nc-rescue.efi")),
            placeholder=t("cc_system.rescue_ph_uki"),
            width=22,
        )
        sb_on = bool(rec_cfg.get("sign_sbctl", True))
        sb_pills = " ".join([
            SelectPill(t("common.on"), "cc_sys:rescue_cfg:sign_sbctl:true", selected=sb_on),
            SelectPill(t("common.off"), "cc_sys:rescue_cfg:sign_sbctl:false", selected=not sb_on),
        ])
        target_box.add_kv(t("cc_system.rescue_cfg_uki"), uki_fld)
        target_box.add_kv(t("cc_system.rescue_cfg_sb"), sb_pills)

    box2.add_collapsible(cc_panel, "rec_cfg_open", t("cc_system.rescue_cfg_sec"), _build_rec_cfg)
    box2.add_row(" ")
    return [box1, box2]


def _build_recovery_chroot_card(canvas: Canvas, data: dict, rec_env: dict, origin_host: str, origin_uuid: str) -> Box:
    cands = data["chroot_cands"]
    ch_mounted = bool(data["chroot_active"])
    ext_n = sum(1 for c in (cands or []) if c.get("scope") != "HOST")
    host_n = sum(1 for c in (cands or []) if c.get("scope") == "HOST")

    hdr_acts = [
        StateBadge(ch_mounted, t("cc_system.rescue_chroot_mounted"), t("cc_system.rescue_chroot_idle")),
        ActionBtn(t("cc_system.rescue_scan_disks_btn"), "cc_sys:chroot_scan", tone="neutral"),
        ActionBtn(t("cc_system.rescue_chroot_term_btn"), "cc_sys:dc_chroot", tone="apply"),
    ]
    if ch_mounted:
        hdr_acts.append(ActionBtn(t("cc_system.rescue_chroot_umount_btn"), "cc_sys:chroot_umount", tone="danger_soft"))

    box = Box(t("cc_system.rescue_chroot_box"), icon="\uf084", actions=hdr_acts, highlighted=False)
    box.add_row(" ")

    if origin_host:
        orig_txt = f"{origin_host} ({origin_uuid[:8]})" if origin_uuid else origin_host
        box.add_kv(t("cc_system.rescue_origin_lbl"), Txt(orig_txt, role="val"))
    else:
        box.add_kv(t("cc_system.rescue_origin_lbl"), Txt(t("cc_system.rescue_origin_unknown"), role="inactive"))

    if ch_mounted:
        ch_mnt = rec_env.get("CHROOT_MNT") or "/run/ncui-chroot"
        ch_tgt = Txt(f"{rec_env.get('CHROOT_DEV') or '/dev/mapper'} ({rec_env.get('CHROOT_VG') or '-'}) -> {ch_mnt}", role="val")
    elif cands is not None:
        ch_tgt = Txt(t("cc_system.rescue_chroot_cands", p0=ext_n, p1=host_n), role="val")
    else:
        ch_tgt = Txt(t("cc_system.rescue_chroot_idle_hint"), role="inactive")
    box.add_kv(t("cc_system.rescue_chroot_target_lbl"), ch_tgt)

    if cands:
        box.add_row(" ")
        cand_rows = [
            [
                Txt(c["dev"], role="focus"),
                Txt(c["size"], role="val"),
                Txt(canvas.truncate_plain(c["model"], 20), role="schema"),
                Txt(c.get("esp") or "-", role="schema"),
                Txt(c["scope"], role="vitality" if c["scope"] != "HOST" else "inactive"),
            ]
            for c in cands[:6]
        ]
        box.add_table(
            headers=[
                t("cc_system.disks_col_disk"),
                t("cc_system.disks_col_cap"),
                t("cc_system.disks_col_model"),
                t("cc_system.rescue_col_esp"),
                t("cc_system.rescue_col_scope"),
            ],
            widths=[20, 8, 22, 16, 10],
            rows=cand_rows,
            aligns=["left", "right", "left", "left", "left"],
        )

    box.add_row(" ")
    return box


def _build_recovery_golden_card(data: dict, rec_cfg: dict) -> Box:
    nas = data["nas_info"]
    nas_mounted = data["nas_mounted"]
    golden_list = data["golden_images"]
    vmap = data["golden_verified"]

    hdr_acts = [
        ActionBtn(t("cc_system.btn_dc_save_img"), "cc_sys:dc_golden_save", tone="apply"),
        ActionBtn(t("cc_system.btn_dc_restore_img"), "cc_sys:dc_golden_restore", tone="warn"),
    ]
    box = Box(t("cc_system.rescue_golden_box", p0=len(golden_list)), icon="\uf0c7", actions=hdr_acts, highlighted=False)
    box.add_row(" ")

    if nas_mounted:
        v_acts = ActionBtn(t("cc_system.umount_nas"), "cc_sys:nas_umount", tone="danger_soft")
    else:
        v_acts = ActionBtn(t("cc_system.mount_nas"), "cc_sys:mount_nas", tone="apply")
    box.add_kv(
        t("cc_system.vault_target_lbl"),
        f"{StateBadge(nas_mounted, t('cc_system.vault_mounted'), t('cc_system.vault_offline'))} {Txt(nas['base'], role='val')}",
        right_markup=v_acts,
    )

    gdir_fld = InlineField(
        "rescue_gdir",
        "cc_sys:rescue_gdir_open",
        value=str(rec_cfg.get("golden_subdir", "images")),
        placeholder=t("cc_system.rescue_ph_gdir"),
        width=24,
    )
    box.add_kv(
        t("cc_system.rescue_cfg_gdir"),
        gdir_fld,
        right_markup=Txt(os.path.join(nas["base"], str(rec_cfg.get("golden_subdir", "images"))), role="schema"),
    )

    box.add_section(t("cc_system.golden_catalog_sec", p0=len(golden_list)))
    if golden_list:
        g_rows = []
        for gi in golden_list:
            nm = gi["dir_name"]
            sz_h = str(gi.get("size") or "-")
            meta_s = f"{sz_h} · {gi['vg']}" if gi.get("vg") and gi["vg"] != "-" else sz_h
            ver = vmap.get(nm)
            if ver is True:
                st_cell = StateBadge(True, t("cc_system.golden_verified_ok"))
            elif ver is False:
                st_cell = Badge(t("cc_system.golden_verify_err"), style="crit")
            else:
                st_cell = Txt(t("cc_system.golden_unverified"), role="schema")
            act_cell = ActionBtn(t("cc_system.golden_verify_btn"), f"cc_sys:golden_verify:{nm}", tone="neutral", min_chars=15)
            g_rows.append([
                Txt(gi.get("title") or nm, role="focus"),
                Txt(gi["date"], role="schema"),
                Txt(meta_s, role="val"),
                st_cell,
                act_cell,
            ])
        box.add_table(
            headers=[t("cc_system.golden_col_img"), t("cc_system.snap_col_date"), t("cc_system.golden_col_meta"), t("cc_system.col_state"), t("cc_system.col_actions")],
            widths=[22, 14, 14, 14, 18],
            rows=g_rows,
            aligns=["left", "left", "left", "left", "right"],
        )
    else:
        empty_k = "cc_system.rescue_golden_empty" if nas_mounted else "cc_system.rescue_golden_offline"
        box.add_row(f"  {Txt(t(empty_k, p0=nas['base']), role='inactive')}")

    box.add_row(" ")
    return box


def _build_recovery_live_session_card(data: dict, rec_env: dict, origin_host: str) -> Box:
    esp_mnt = rec_env.get("ESP_MOUNT", "/boot")
    esp_free = rec_env.get("ESP_FREE_HUMAN", "-")

    box = Box(
        t("cc_system.rescue_live_box"),
        icon="\uf2db",
        actions=_header_probe_actions(data, rec_env, live_badge=True),
        highlighted=False,
    )
    box.add_row(" ")

    boot_img = rec_env.get("BOOTED_IMAGE") or "recovery.squashfs"
    boot_lbl = f"{boot_img} ({origin_host})" if origin_host else boot_img
    box.add_kv(
        t("cc_system.rescue_runtime_lbl"),
        f"{Badge(t('cc_system.rescue_live_mode'), style='warn')} {Txt(f'{boot_lbl} · {esp_mnt} ({esp_free})', role='val')}",
    )

    commit_acts = " ".join([
        ActionBtn(t("cc_system.btn_dc_commit"), "cc_sys:dc_commit", tone="apply"),
        ActionBtn(t("cc_system.btn_dc_commit_rebuild"), "cc_sys:dc_commit_rebuild", tone="neutral"),
    ])
    box.add_kv(
        t("cc_system.rescue_commit_lbl"),
        Txt(t("cc_system.rescue_commit_desc"), role="schema"),
        right_markup=commit_acts,
    )

    _add_rollback_kv(box, data, rec_env)
    box.add_row(" ")
    return box


def build_cards(cc_panel, canvas: Canvas) -> list:
    data = _collect_backup_data()
    rec_env = data["rescue_env"]
    rec_cfg = data["rec_cfg"]
    dc_env = data["rescue_env_info"]
    is_live = bool(dc_env.get("is_live"))
    origin_host = str(dc_env.get("origin_host") or "").strip()
    origin_uuid = str(dc_env.get("origin_luks_uuid") or "").strip()

    cards = feedback_cards("system", "cc_system.system_control_status") + build_privilege_modal_card(cc_panel, canvas)
    rec_stat = f"{rec_env.get('ESP_MOUNT', '/boot')} · {'LIVE RAM' if is_live else 'HOST ROOT'}"
    guide_tpfx = "guide_rec_live" if is_live else "guide_rec_host"
    cards.extend(_build_guide_box(cc_panel, canvas, "guide_recovery_open", "guide_rec", "recovery", rec_stat, text_prefix=guide_tpfx))

    if not is_live:
        cards.extend(_build_host_view(cc_panel, data, rec_env, rec_cfg))
    else:
        cards.append(_build_recovery_chroot_card(canvas, data, rec_env, origin_host, origin_uuid))
        cards.append(_build_recovery_golden_card(data, rec_cfg))
        cards.append(_build_recovery_live_session_card(data, rec_env, origin_host))

    return cards
