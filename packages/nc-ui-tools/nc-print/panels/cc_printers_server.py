#!/usr/bin/env python3
"""NC-UI Control Center — CUPS Server, Sharing & Diagnostics (`panels/cc_printers_server.py`)."""

import re
from core.i18n import t
from core.privileges import build_privilege_modal_card, get_privileged_action
from ui.canvas import Canvas
from ui.kit import ActionBtn, Badge, Box, SelectPill, StateBadge, Txt
from ui.markup import format_bytes_short
from panels.cc_engine_printers import (
    feedback_cards,
    get_cups_daemon_state,
    get_cups_logs,
    get_cups_server_settings,
)

_CUPS_LOG_RE = re.compile(r"^([EWID])\s+\[(\d{2})/([A-Za-z]{3})/\d{4}:(\d{2}:\d{2}):\d{2}[^\]]*\]\s*(.*)$")


def _parse_cups_log_line(ln: str) -> tuple[str, str, str, str]:
    s = str(ln or "").strip()
    m = _CUPS_LOG_RE.match(s)
    if m:
        code, day, mon, hm, body = m.groups()
        ts = f"{day} {mon} {hm}"
        if code == "E":
            return t("common.err"), "fault", ts, body.strip()
        if code == "W":
            return t("common.warn"), "attention", ts, body.strip()
        return t("cc_printers.log_lvl_info"), "schema", ts, body.strip()
    low = s.lower()
    if "error" in low or "fail" in low:
        return t("common.err"), "fault", "\u2014", s
    if "warn" in low:
        return t("common.warn"), "attention", "\u2014", s
    return t("cc_printers.log_lvl_info"), "schema", "\u2014", s


def _build_status_grid_rows(items: dict, absent_label: str) -> list[list[str]]:
    cells = []
    for nm, is_ok in items.items():
        if is_ok:
            cells.append(f"{Txt('\u25cf', role='vitality')} {Txt(nm, role='val')}  {Txt(t('common.ok'), role='vitality')}")
        else:
            cells.append(f"{Txt('\u25cb', role='inactive')} {Txt(nm, role='inactive')}  {Txt(absent_label, role='inactive')}")
    rows = []
    for i in range(0, len(cells), 3):
        chunk = cells[i : i + 3]
        while len(chunk) < 3:
            chunk.append("")
        rows.append(chunk)
    return rows


def _policy_row_val(flag_name: str, is_on: bool, is_up: bool) -> str:
    if not is_up:
        lbl = f"\u25cf {t('common.on')}" if is_on else f"\u25cb {t('common.off')}"
        return Txt(lbl, role="inactive")
    return (
        f"{SelectPill(t('common.on'), f'cc_prn:cupsctl:{flag_name}:1', selected=is_on)} "
        f"{SelectPill(t('common.off'), f'cc_prn:cupsctl:{flag_name}:0', selected=not is_on)}"
    )


def build_cards(cc_panel, canvas: Canvas) -> list:
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
    has_sc = daemon.get("has_systemctl", True)
    if not daemon["cups_installed"]:
        cups_btns = Badge(t("ui.missing", p0="cups"), style="warn")
    elif not has_sc:
        cups_btns = Badge(t("ui.missing", p0="systemctl"), style="warn")
    elif is_up:
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
    svc_box.add_kv(t("cc_printers.boot_autostart"), boot_pills, requires=["systemctl"])

    avahi_badge = StateBadge(daemon["avahi_active"], t("common.active"), t("cc_printers.stopped"))
    if not has_sc:
        avahi_btn = Badge(t("ui.missing", p0="systemctl"), style="warn")
    elif daemon["avahi_active"]:
        avahi_btn = ActionBtn(t("cc_printers.restart_btn"), "cc_prn:svc:restart:avahi-daemon.service", tone="warn")
    else:
        avahi_btn = ActionBtn(t("cc_printers.start_btn"), "cc_prn:svc:start:avahi-daemon.service", tone="apply")
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
        logs = get_cups_logs(max_lines=10)
        spool_meta = t("cc_printers.spool_files_fmt", p0=daemon["spool_files"], p1=format_bytes_short(daemon["spool_bytes"]))
        bx.add_kv(
            t("cc_printers.kv_spool_dir"),
            f"{Txt('/var/spool/cups', role='val')}  {Txt('·', role='inactive')}  {Txt(spool_meta, role='schema')}",
        )

        bx.add_section(f"{t('cc_printers.kv_filter_stack')} ({ok_tools}/{len(tools)})")
        bx.add_table(
            headers=["", "", ""],
            widths=[25, 25, "flex"],
            rows=_build_status_grid_rows(tools, t("cc_printers.missing_bin")),
            aligns=["left", "left", "left"],
            show_headers=False,
        )

        bx.add_section(f"{t('cc_printers.kv_driver_packs')} ({ok_packs}/{len(drv_packs)})")
        bx.add_table(
            headers=["", "", ""],
            widths=[25, 25, "flex"],
            rows=_build_status_grid_rows(drv_packs, "\u2014"),
            aligns=["left", "left", "left"],
            show_headers=False,
        )

        log_acts = []
        if logs:
            log_acts.append(ActionBtn(t("cc_printers.clear_logs_btn"), "cc_prn:clear_logs", tone="warn"))
            log_acts.append(ActionBtn(t("cc_printers.dismiss_log_view_btn"), "cc_prn:dismiss_logs_view", tone="danger_soft"))
        bx.add_section(t("cc_printers.sec_error_log"), actions=log_acts)
        if not logs:
            bx.add_row(f"  {Txt(t('cc_printers.no_logs_hint'), role='inactive')}")
        else:
            if not daemon["avahi_active"] and any("ippfind" in ln and "status 2" in ln for ln in logs):
                bx.add_row(f"  {Txt(t('cc_printers.ippfind_avahi_hint'), role='attention')}")
            grouped: list[list] = []
            for ln in logs:
                lvl_s, role, ts_s, body = _parse_cups_log_line(ln)
                if grouped and grouped[-1][0] == lvl_s and grouped[-1][3] == body:
                    grouped[-1][2] = ts_s
                    grouped[-1][4] += 1
                else:
                    grouped.append([lvl_s, role, ts_s, body, 1])
            log_rows = []
            for lvl_s, role, ts_s, body, cnt in grouped:
                msg_s = f"{body} (\u00d7{cnt})" if cnt > 1 else body
                log_rows.append([
                    Txt(lvl_s, role=role),
                    Txt(ts_s, role="schema"),
                    Txt(msg_s, role=role if role == "fault" else "val"),
                ])
            bx.add_table(
                headers=[
                    t("cc_printers.log_col_lvl"),
                    t("cc_printers.log_col_time"),
                    t("cc_printers.log_col_msg"),
                ],
                widths=[7, 13, "flex"],
                rows=log_rows,
                aligns=["left", "left", "left"],
            )

    pol_box.add_collapsible(
        cc_panel,
        "prn_sec_diag",
        t("cc_printers.sec_diag_logs"),
        _build_diag,
        summary=diag_summary,
    )

    cards.append(pol_box)
    return cards
