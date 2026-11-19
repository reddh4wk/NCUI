#!/usr/bin/env python3
"""NC-UI Control Center — CUPS Server, Sharing & Diagnostics (`panels/cc_printers_server.py`)."""

from core.i18n import t
from core.privileges import build_privilege_modal_card, get_privileged_action
from ui.canvas import Canvas
from ui.kit import ActionBtn, Box, SelectPill, StateBadge, Txt
from ui.markup import format_bytes_short
from panels.cc_engine_printers import (
    feedback_cards,
    get_cups_daemon_state,
    get_cups_logs,
    get_cups_server_settings,
)


def _policy_row_val(flag_name: str, is_on: bool, is_up: bool) -> str:
    if not is_up:
        lbl = f"\u25cf {t('common.on')}" if is_on else f"\u25cb {t('common.off')}"
        return Txt(lbl, role="inactive")
    return (
        f"{SelectPill(t('common.on'), f'cc_prn:cupsctl:{flag_name}:1', selected=is_on)} "
        f"{SelectPill(t('common.off'), f'cc_prn:cupsctl:{flag_name}:0', selected=not is_on)}"
    )


def _build_server_standard(cc_panel, canvas: Canvas) -> list:
    cards = feedback_cards() + build_privilege_modal_card(cc_panel, canvas)
    daemon = get_cups_daemon_state()
    srv = get_cups_server_settings()
    is_up = daemon["scheduler_up"]

    svc_acts = [
        ActionBtn(t("cc_printers.webui_btn"), "cc_prn:webui", tone="neutral" if is_up else "muted"),
        ActionBtn(t("common.refresh"), "cc_prn:refresh", tone="neutral"),
    ]
    svc_box = Box(
        t("cc_printers.server_box_title"),
        icon="\uf233",
        actions=svc_acts,
        highlighted=False,
    )

    cups_badge = StateBadge(is_up, t("common.active"), t("cc_printers.stopped"))
    if is_up:
        cups_btns = (
            f"{ActionBtn(t('cc_printers.restart_btn'), 'cc_prn:svc:restart:cups.service', tone='warn')} "
            f"{ActionBtn(t('cc_printers.stop_btn'), 'cc_prn:svc:stop:cups.service', tone='danger_soft')}"
        )
    else:
        cups_btns = ActionBtn(t("cc_printers.start_cups"), "cc_prn:svc:start:cups.service", tone="apply")
    svc_box.add_kv(t("cc_printers.kv_cups_scheduler"), cups_badge, right_markup=cups_btns)

    boot_pills = (
        f"{SelectPill(t('common.enabled'), 'cc_prn:svc:enable:cups.service', selected=daemon['service_enabled'])} "
        f"{SelectPill(t('common.disabled'), 'cc_prn:svc:disable:cups.service', selected=not daemon['service_enabled'])}"
    )
    svc_box.add_kv(t("cc_printers.boot_autostart"), boot_pills)

    avahi_badge = StateBadge(daemon["avahi_active"], t("common.active"), t("cc_printers.stopped"))
    avahi_btn = (
        ActionBtn(t("cc_printers.restart_btn"), "cc_prn:svc:restart:avahi-daemon.service", tone="neutral")
        if daemon["avahi_active"]
        else ActionBtn(t("cc_printers.start_btn"), "cc_prn:svc:start:avahi-daemon.service", tone="apply")
    )
    svc_box.add_kv(t("cc_printers.kv_avahi_mdns"), avahi_badge, right_markup=avahi_btn, requires=["avahi-browse"])

    priv_svc = get_privileged_action("cups_service")
    priv_grp = get_privileged_action("cups_sysgroup")
    svc_inst = bool(priv_svc and priv_svc.check_installed())
    grp_rule_inst = bool(priv_grp and priv_grp.check_installed())

    svc_priv_badge = StateBadge(svc_inst, t("cc_printers.priv_nopasswd"), t("cc_printers.priv_sudo_pwd"))
    svc_priv_btn = (
        ActionBtn(t("cc_auth.revoke"), "priv:revoke:cups_service", tone="danger_soft")
        if svc_inst
        else ActionBtn(t("cc_auth.authorize"), "priv:request:cups_service", tone="apply")
    )
    svc_box.add_kv(t("cc_printers.kv_systemd_priv"), svc_priv_badge, right_markup=svc_priv_btn)

    grp_list_s = ", ".join(daemon["groups"])
    grp_on_lbl = f"{t('cc_printers.sysgroup_member')} ({grp_list_s})" if grp_list_s else t("cc_printers.sysgroup_member")
    grp_badge = StateBadge(daemon["in_sysgroup"], grp_on_lbl, t("cc_printers.std_user_badge"))
    if not daemon["in_sysgroup"]:
        grp_right = ActionBtn(t("cc_printers.grant_sysgroup_btn"), "priv:request:cups_sysgroup", tone="apply")
    elif grp_rule_inst:
        grp_right = ActionBtn(t("cc_auth.revoke"), "priv:revoke:cups_sysgroup", tone="danger_soft")
    else:
        grp_right = ""
    svc_box.add_kv(t("cc_printers.kv_admin_access"), grp_badge, right_markup=grp_right)

    cards.append(svc_box)

    # --- Global Server Policies (`cupsctl`) ---
    port_ind = StateBadge(is_up, f":{srv['port']}", f":{srv['port']} {t('cc_printers.stopped')}")
    pol_box = Box(
        t("cc_printers.policies_box_title"),
        icon="\uf1e0",
        actions=[port_ind],
        highlighted=False,
    )
    if not is_up:
        pol_box.add_row(f"  {Txt(t('cc_printers.policies_offline_hint'), role='attention')}")
    pol_box.add_kv(t("cc_printers.pol_share_printers"), _policy_row_val("share-printers", srv["_share_printers"], is_up))
    pol_box.add_kv(t("cc_printers.pol_remote_any"), _policy_row_val("remote-any", srv["_remote_any"], is_up))
    pol_box.add_kv(t("cc_printers.pol_remote_admin"), _policy_row_val("remote-admin", srv["_remote_admin"], is_up))
    pol_box.add_kv(t("cc_printers.pol_user_cancel"), _policy_row_val("user-cancel-any", srv["_user_cancel_any"], is_up))
    pol_box.add_kv(t("cc_printers.pol_debug_log"), _policy_row_val("debug-logging", srv["_debug_logging"], is_up))

    # --- Collapsible Diagnostics, Filter Stack, Arch Driver Packs & Error Log ---
    tools = daemon["tools"]
    drv_packs = daemon["driver_packs"]
    ok_tools = sum(1 for v in tools.values() if v)
    ok_packs = sum(1 for v in drv_packs.values() if v)
    diag_summary = t(
        "cc_printers.diag_summary_v2",
        p0=daemon["spool_files"],
        p1=format_bytes_short(daemon["spool_bytes"]),
        p2=f"{ok_tools}/{len(tools)}",
        p3=f"{ok_packs}/{len(drv_packs)}",
    )

    def _build_diag(bx: Box) -> None:
        logs = get_cups_logs(max_lines=8)
        clr_b = ActionBtn(t("cc_printers.clear_logs_btn"), "cc_prn:clear_logs", tone="warn") if logs else ""
        bx.add_kv(
            t("cc_printers.kv_spool_dir"),
            Txt(f"/var/spool/cups  ·  {daemon['spool_files']} files ({format_bytes_short(daemon['spool_bytes'])})", role="val"),
            right_markup=clr_b,
        )
        stack_items = [
            f"{nm}: {Txt(t('common.ok') if ok_b else t('cc_printers.missing_bin'), role='vitality' if ok_b else 'inactive')}"
            for nm, ok_b in tools.items()
        ]
        bx.add_kv(t("cc_printers.kv_filter_stack"), "  ·  ".join(stack_items[:3]))
        bx.add_kv("", "  ·  ".join(stack_items[3:]))

        pack_items = [
            f"{nm}: {Txt(t('common.ok') if ok_p else t('cc_printers.opt_missing'), role='vitality' if ok_p else 'inactive')}"
            for nm, ok_p in drv_packs.items()
        ]
        bx.add_kv(t("cc_printers.kv_driver_packs"), " · ".join(pack_items[:3]))
        bx.add_kv("", " · ".join(pack_items[3:]))

        dismiss_log_b = ActionBtn(t("cc_printers.dismiss_log_view_btn"), "cc_prn:dismiss_logs_view", tone="danger_soft") if logs else ""
        bx.add_section(t("cc_printers.sec_error_log"), actions=[dismiss_log_b] if dismiss_log_b else [])
        if not logs:
            bx.add_row(f"  {Txt(t('cc_printers.no_logs_hint'), role='inactive')}")
        else:
            if not daemon["avahi_active"] and any("ippfind" in ln and "status 2" in ln for ln in logs):
                bx.add_row(f"  {Txt(t('cc_printers.ippfind_avahi_hint'), role='attention')}")
            for ln in logs:
                low = ln.lower()
                r = "fault" if ln.startswith("E ") or "error" in low else ("attention" if ln.startswith("W ") or "warn" in low else "schema")
                bx.add_row(f"  {Txt(canvas.truncate_plain(ln, canvas.inner_cols - 6), role=r)}")

    pol_box.add_collapsible(
        cc_panel,
        "prn_sec_diag",
        t("cc_printers.sec_diag_logs"),
        _build_diag,
        summary=diag_summary,
    )

    cards.append(pol_box)
    return cards


def build_cards(cc_panel, canvas: Canvas) -> list:
    return _build_server_standard(cc_panel, canvas)
