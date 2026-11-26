#!/usr/bin/env python3
"""NC-UI Control Center — Add Printer, Discovery & Drivers (`panels/cc_printers_discovery.py`)."""

import time
from urllib.parse import urlparse

from core.i18n import t
from core.privileges import build_privilege_modal_card
from ui.canvas import Canvas
from ui.kit import (
    ActionBtn,
    Badge,
    Box,
    FoldIconBtn,
    InlineField,
    InlineFilter,
    InspectorCell,
    RelevanceGlyph,
    SelectPill,
    StateBadge,
    Txt,
    register_scroll_zone,
)
from panels.cc_engine_printers import (
    add_ppd_picker_kv,
    build_tcp_uri,
    feedback_cards,
    get_add_draft,
    get_auto_found_drivers,
    get_cups_daemon_state,
    get_cups_drivers,
    get_discovered_devices,
    get_pending_confirm,
    get_printer_classes,
    get_printers_snapshot,
    get_proto_presets,
    get_smart_probe,
    get_tcp_scan_state,
    is_discovery_running,
    is_drivers_loading,
    is_install_running,
    is_tcp_scan_running,
    render_scalar_kv,
    resolve_effective_driver,
    search_cups_drivers,
    trigger_smart_probe_async,
)


def _find_installed_printer(printers: list[dict], uri: str, ip: str = "") -> dict | None:
    if uri:
        for p in printers:
            if p.get("uri") == uri:
                return p
    if ip:
        for p in printers:
            u = p.get("uri", "")
            if "://" in u and (urlparse(u).hostname or "") == ip:
                return p
    return None


def _cmp_arrow(is_up: bool) -> str:
    return Txt("\u21e1", role="vitality") if is_up else Txt("\u21e3", role="attention")


def _render_auto_found_table(bx: Box, canvas: Canvas, d: dict, auto_info: dict, nav_acts: list[str]) -> None:
    win_rows = auto_info["window"]
    win_size = auto_info["win_size"]
    total = auto_info["total"]
    off = auto_info["offset"]
    pm = auto_info["pkg_mgr"]
    scroll_act = "cc_prn:drv_box_scroll"

    if total > 0:
        hi = min(total, off + win_size)
        hdr_txt = t("cc_printers.auto_box_hdr_n", p0=pm, p1=total, p2=f"{off + 1}-{hi}/{total}")
    elif auto_info["searching"]:
        hdr_txt = t("cc_printers.auto_box_searching_hdr", p0=pm)
    else:
        hdr_txt = t("cc_printers.auto_box_hdr_empty", p0=pm)

    bx.add_section(hdr_txt, actions=nav_acts)
    if not win_rows:
        msg_k = "cc_printers.auto_box_searching" if auto_info["searching"] else "cc_printers.auto_box_no_filter_match"
        bx.add_row(f"  {Txt(t(msg_k, p0=pm), role='attention' if auto_info['searching'] else 'inactive')}")
        return

    t_rows = []
    for it in win_rows:
        rel_cell = RelevanceGlyph(int(it["score"]), show_pct=True)
        cmp_cell = _cmp_arrow(bool(it.get("better_than_local", True)))
        if it["kind"] == "cups":
            item_uri = f"cc_prn:pick_drv:{it['id']}"
            register_scroll_zone(item_uri, scroll_act)
            is_sel = (d["drv_id"] == it["id"]) or (d["drv_mode"] == "ppd" and it["id"] == f"ppd:{d['ppd_path']}")
            main_cell = SelectPill(canvas.truncate_plain(str(it["label"]), 36), item_uri, selected=is_sel)
            tag_role = "vitality" if it.get("tag") == "AUTO" else "schema"
            act_cell = Txt(canvas.truncate_plain(str(it.get("tag", "CUPS")), 10), role=tag_role)
        elif it["installed"]:
            item_uri = f"cc_prn:pick_pkg_drv:{it['pkg']}"
            register_scroll_zone(item_uri, scroll_act)
            is_pkg_sel = (d["drv_mode"] == "db" and d.get("selected_pkg") == it["pkg"])
            main_cell = SelectPill(canvas.truncate_plain(f"{it['label']} ({it['repo']})", 36), item_uri, selected=is_pkg_sel)
            act_cell = StateBadge(True, t("cc_printers.pkg_installed_tag"))
        else:
            pk = it["pkg"]
            item_uri = f"cc_prn:install_drv_pkg:{pk}"
            register_scroll_zone(item_uri, scroll_act)
            main_cell = Txt(canvas.truncate_plain(f"{it['label']} ({it['repo']})", 36), role="focus")
            act_cell = ActionBtn(t("cc_printers.install_pkg_btn"), item_uri, tone="apply")

        t_rows.append([rel_cell, main_cell, cmp_cell, act_cell])

    bx.add_table(
        headers=[
            t("cc_printers.col_match"),
            t("cc_printers.col_driver_pkg"),
            t("cc_printers.col_delta"),
            t("cc_printers.col_actions"),
        ],
        widths=[8, "flex", 8, 14],
        rows=t_rows,
        aligns=["left", "left", "center", "right"],
    )


def _render_driver_selector(bx: Box, cc_panel, canvas: Canvas, d: dict, smart: dict) -> None:
    drv_modes = [
        ("auto", t("cc_printers.drv_smart_auto")),
        ("db", t("cc_printers.drv_db_short")),
        ("ppd", t("cc_printers.drv_ppd_short")),
        ("raw", t("cc_printers.drv_raw_short")),
    ]
    drv_pills = " ".join(
        SelectPill(lbl, f"cc_prn:draft_drv_mode:{mid}", selected=(d["drv_mode"] == mid))
        for mid, lbl in drv_modes
    )
    bx.add_kv(t("cc_printers.kv_driver_mode"), drv_pills)

    if d["drv_mode"] in ("auto", "db"):
        auto_info = get_auto_found_drivers(cc_panel, d, smart)
        loc_drv = auto_info["local_driver"]
        loading_b = Badge(t("cc_printers.loading_drivers_badge"), style="warn") if is_drivers_loading() else ""
        loc_dot = RelevanceGlyph(int(loc_drv["score"]), show_pct=False)
        loc_val = f"{loc_dot} {Txt(canvas.truncate_plain(loc_drv['label'], 54), role='val')}"
        bx.add_kv(t("cc_printers.kv_local_drv"), loc_val, right_markup=loading_b)

        if d["drv_mode"] == "db":
            for drv_id, lbl in search_cups_drivers(d["drv_query"], limit=4, cc_panel=cc_panel):
                bx.add_kv(
                    "",
                    SelectPill(
                        canvas.truncate_plain(lbl, 58),
                        f"cc_prn:pick_drv:{drv_id}",
                        selected=(d["drv_id"] == drv_id),
                    ),
                )

        has_auto_box = auto_info["total_unfiltered"] > 0 or auto_info["searching"]
        flt = InlineFilter(
            "prn_drv_picker",
            d["drv_query"],
            t("cc_printers.ph_search_drv"),
            "cc_prn:drv_search_open",
            clear_uri="cc_prn:drv_search_clear",
            width=34,
        )
        rescan_lbl = f"\u21bb {auto_info['pkg_mgr']}" if auto_info["pkg_mgr"] else t("cc_printers.rescan_btn")
        rescan_b = ActionBtn(rescan_lbl, "cc_prn:drv_pkg_rescan", tone="neutral")
        nav_acts: list[str] = []
        if has_auto_box:
            off = auto_info["offset"]
            win_size = auto_info["win_size"]
            total = auto_info["total"]
            scroll_act = "cc_prn:drv_box_scroll"
            up_uri = f"{scroll_act}:up"
            dn_uri = f"{scroll_act}:down"
            register_scroll_zone(up_uri, scroll_act)
            register_scroll_zone(dn_uri, scroll_act)
            up_btn = ActionBtn("\uf077", up_uri, tone="neutral" if off > 0 else "muted")
            dn_btn = ActionBtn("\uf078", dn_uri, tone="neutral" if (off + win_size) < total else "muted")
            nav_acts = [up_btn, dn_btn]
        bx.add_kv(t("cc_printers.kv_search_drv"), flt, right_markup=rescan_b)

        if has_auto_box:
            _render_auto_found_table(bx, canvas, d, auto_info, nav_acts)
            pend_pkg = str(cc_panel.get_state("prn_pkg_confirm", "") or "")
            pend_it = next((c for c in auto_info["items"] if c.get("pkg") == pend_pkg and not c.get("installed")), None)
            if pend_it:
                pk_c = pend_it["pkg"]
                rp = pend_it.get("reason_params", {})
                reason_txt = t(
                    f"cc_printers.pkg_warn_reason_{pend_it.get('reason_kind', 'pdl')}",
                    p0=pk_c,
                    p1=rp.get("model", ""),
                    p2=rp.get("brand", ""),
                )
                warn_w = max(44, canvas.inner_cols - 28)
                bx.add_kv("", Txt(canvas.truncate_plain(t("cc_printers.pkg_warn_title", p0=pk_c), warn_w), role="attention"))
                for r_ln in canvas.wrap_markup(Txt(reason_txt, role="schema"), warn_w * canvas.char_w)[:2]:
                    bx.add_kv("", r_ln)
                conf_btns = (
                    f"{ActionBtn(t('cc_printers.pkg_confirm_install_btn', p0=pk_c), f'cc_prn:install_drv_pkg_force:{pk_c}', tone='apply')} "
                    f"{ActionBtn(t('common.cancel'), 'cc_prn:cancel_pkg_confirm', tone='neutral')}"
                )
                bx.add_kv("", conf_btns)

            d_hi = RelevanceGlyph(95, show_pct=False)
            d_md = RelevanceGlyph(65, show_pct=False)
            d_lo = RelevanceGlyph(45, show_pct=False)
            leg1 = (
                f"{d_hi} {Txt(t('cc_printers.leg_high'), role='val')}  "
                f"{d_md} {Txt(t('cc_printers.leg_med'), role='val')}  "
                f"{d_lo} {Txt(t('cc_printers.leg_low'), role='val')}  "
                f"{Txt('— ' + t('cc_printers.leg_prob_desc'), role='schema')}"
            )
            leg2 = (
                f"{_cmp_arrow(True)} {Txt(t('cc_printers.leg_up_desc'), role='val')}   "
                f"{_cmp_arrow(False)} {Txt(t('cc_printers.leg_dn_desc'), role='schema')}"
            )
            bx.add_row(f"  {leg1}")
            bx.add_row(f"  {leg2}")
            gap_adv = auto_info.get("gap_advice", "")
            if gap_adv == "test_local_first":
                bx.add_row(f"  {Txt('\u25b8', role='attention')} {Txt(t('cc_printers.leg_gap_test_local'), role='schema')} {_cmp_arrow(True)}")
            elif gap_adv == "install_pkg_first":
                bx.add_row(
                    f"  {Txt('\u25b8', role='vitality')} {Txt(t('cc_printers.leg_gap_install_first_pre'), role='schema')} "
                    f"{d_hi} {_cmp_arrow(True)} {Txt(t('cc_printers.leg_gap_install_first_post'), role='schema')}"
                )
            elif gap_adv == "local_optimal" and auto_info["total"] > 0:
                bx.add_row(f"  {Txt('\u25b8', role='vitality')} {Txt(t('cc_printers.leg_gap_local_optimal'), role='schema')}")
    elif d["drv_mode"] == "ppd":
        add_ppd_picker_kv(bx, d["ppd_path"], "cc_prn:browse_ppd")


def _render_install_actions(bx: Box, daemon: dict, d: dict, include_cancel: bool = True) -> None:
    opt_pills = (
        f"{SelectPill(t('cc_printers.opt_make_default'), 'cc_prn:draft_toggle:make_default', selected=d['make_default'])} "
        f"{SelectPill(t('cc_printers.shared_on'), 'cc_prn:draft_toggle:shared', selected=d['shared'])}"
    )
    bx.add_kv(t("cc_printers.kv_install_flags"), opt_pills)

    inst_active = bool(is_install_running())
    can_install = bool(daemon["cups_installed"] and not inst_active and d["name"] and "://" in d["uri"] and d["uri"] != "usb://")
    inst_lbl = t("cc_printers.install_printer_btn") if daemon["scheduler_up"] else t("cc_printers.start_and_install_btn")
    btns = [
        ActionBtn(inst_lbl, "cc_prn:install_printer", tone="apply" if can_install else "muted"),
        ActionBtn(t("cc_printers.install_and_test_btn"), "cc_prn:install_printer_test", tone="apply" if can_install else "muted"),
    ]
    if include_cancel:
        btns.append(ActionBtn(t("common.cancel"), "cc_prn:wiz_close", tone="neutral"))
    bx.add_kv("", " ".join(btns))
    if not daemon["cups_installed"]:
        bx.add_kv("", Txt(t("ui.missing", p0="cups"), role="attention"))
    elif not daemon["scheduler_up"]:
        bx.add_kv("", Txt(t("cc_printers.wiz_autostart_cups_hint"), role="attention"))


def _render_attached_wizard(
    bx: Box,
    cc_panel,
    canvas: Canvas,
    d: dict,
    daemon: dict,
    snap: dict,
    is_tcp: bool,
    open_ports: list[int] | None = None,
    is_manual: bool = False,
) -> None:
    inst_name = is_install_running()
    smart = get_smart_probe(d["host"] or d["uri"])
    if not is_manual and (d["host"] or d["uri"]) and (
        not smart or (smart.get("probing") and (time.monotonic() - float(smart.get("ts", 0.0))) >= 8.0)
    ):
        trigger_smart_probe_async(cc_panel, d["host"], d["uri"], d["desc"] or d["name"], open_ports)
        smart = get_smart_probe(d["host"] or d["uri"])
    if is_manual:
        sec_title = t("cc_printers.manual_section_title")
        sec_acts = [
            ActionBtn(t("common.reset"), "cc_prn:draft_reset", tone="warn"),
            ActionBtn(t("cc_printers.close_setup_btn"), "cc_prn:wiz_close", tone="neutral"),
        ]
    else:
        if inst_name:
            wiz_badge = Badge(t("cc_printers.installing_badge"), style="warn")
        elif smart.get("probing"):
            wiz_badge = Badge(t("cc_printers.probing_badge"), style="warn")
        else:
            wiz_badge = StateBadge(True, t("cc_printers.wiz_ready_badge"))
        sec_title = t("cc_printers.wiz_section_title", p0=canvas.truncate_plain(d["desc"] or d["host"] or d["name"], 38))
        sec_acts = [wiz_badge]

    bx.add_section(sec_title, actions=sec_acts)

    if smart.get("probing") and not is_manual:
        bx.add_kv(t("cc_printers.kv_auto_diag"), Txt(t("cc_printers.wiz_probing_hint"), role="attention"))
    else:
        diag_parts: list[str] = []
        p_ms = smart.get("ports_ms") or {}
        if p_ms:
            lo, hi = min(p_ms.values()), max(p_ms.values())
            ms_s = f"{lo}ms" if lo == hi else f"{lo}-{hi}ms"
            diag_parts.append(f"{' '.join(f':{p}' for p in p_ms)} ({ms_s})")
        elif open_ports:
            diag_parts.append(" ".join(f":{p}" for p in open_ports))
        fmt_map = {"PWG-Raster": "PWG", "Apple-URF": "URF", "PostScript": "PS"}
        caps = [fmt_map.get(f, f) for f in smart.get("formats", ())]
        if "PWG" in caps and "URF" in caps:
            caps.remove("URF")
        if smart.get("duplex"):
            caps.append(t("cc_printers.cap_duplex"))
        if smart.get("color"):
            caps.append(t("cc_printers.cap_color"))
        if caps:
            diag_parts.append(" \u00b7 ".join(caps))
        if diag_parts and (not is_manual or p_ms or caps):
            bx.add_kv(t("cc_printers.kv_auto_diag"), Txt(canvas.truncate_plain(" \u00b7 ".join(diag_parts), 64), role="val"))

    render_scalar_kv(
        bx,
        canvas,
        t("cc_printers.kv_queue_name"),
        "prn_draft_name",
        "cc_prn:draft_field:name",
        d["name"],
        t("cc_printers.ph_queue_name"),
        empty_btn_lbl=t("cc_printers.set_name_btn"),
        width=44,
    )

    _ph_map = {
        "socket": build_tcp_uri("192.168.1.50", d["tcp_port"]),
        "ipp": "ipp://192.168.1.50:631/ipp/print",
        "ipps": "ipps://192.168.1.50:631/ipp/print",
        "lpd": "lpd://192.168.1.50/BINARY_P1",
        "smb": "smb://WORKGROUP/server/printer",
        "usb": "usb://Vendor/Model?serial=...",
    }
    uri_ph = _ph_map.get(d["proto"], "socket://192.168.1.50:9100")
    probe_b = ActionBtn(t("cc_printers.probe_port_btn"), "cc_prn:probe_uri", tone="neutral") if (d["proto"] != "usb" and (d["host"] or d["uri"])) else ""
    render_scalar_kv(
        bx,
        canvas,
        t("cc_printers.kv_device_uri"),
        "prn_draft_uri",
        "cc_prn:draft_field:uri",
        d["uri"] if d["uri"] != "usb://" else "",
        uri_ph,
        empty_btn_lbl=t("cc_printers.set_uri_btn"),
        width=44,
        extra_right=probe_b,
    )

    _render_install_actions(bx, daemon, d, include_cancel=True)

    adv_summary_parts = [d["proto"].upper()]
    if d["host"]:
        adv_summary_parts.append(d["host"])
    if d["desc"]:
        adv_summary_parts.append(canvas.truncate_plain(d["desc"], 24))
    if d["loc"]:
        adv_summary_parts.append(canvas.truncate_plain(d["loc"], 18))

    def _build_wiz_adv(sub_bx: Box) -> None:
        render_scalar_kv(
            sub_bx,
            canvas,
            t("cc_printers.kv_description"),
            "prn_draft_desc",
            "cc_prn:draft_field:desc",
            d["desc"],
            t("cc_printers.ph_description"),
            empty_btn_lbl=t("cc_printers.set_desc_btn"),
            width=44,
        )
        render_scalar_kv(
            sub_bx,
            canvas,
            t("cc_printers.kv_location"),
            "prn_draft_loc",
            "cc_prn:draft_field:loc",
            d["loc"],
            t("cc_printers.ph_location"),
            empty_btn_lbl=t("cc_printers.set_loc_btn"),
            width=44,
        )
        proto_pills = " ".join(
            SelectPill(lbl, f"cc_prn:draft_proto:{pid}", selected=(d["proto"] == pid))
            for pid, lbl, _tpl in get_proto_presets()
        )
        sub_bx.add_kv(t("cc_printers.kv_protocol"), proto_pills)
        if is_tcp or d["proto"] == "socket":
            tcp_mode_pills = " ".join(
                SelectPill(lbl, f"cc_prn:draft_tcp_port:{port}", selected=(d["tcp_port"] == port))
                for port, lbl in (
                    (9100, t("cc_printers.tcp_mode_9100")),
                    (631, t("cc_printers.tcp_mode_631")),
                    (515, t("cc_printers.tcp_mode_515")),
                )
            )
            sub_bx.add_kv(t("cc_printers.kv_tcp_port_mode"), tcp_mode_pills)
        if d["proto"] != "usb":
            smb_auth = ActionBtn(t("cc_printers.smb_auth_btn"), "cc_prn:smb_auth_prompt", tone="apply") if d["proto"] == "smb" else ""
            render_scalar_kv(
                sub_bx,
                canvas,
                t("cc_printers.kv_host_ip"),
                "prn_draft_host",
                "cc_prn:draft_field:host",
                d["host"],
                t("cc_printers.ph_host_ip"),
                empty_btn_lbl=t("cc_printers.set_host_btn"),
                width=44,
                extra_right=smb_auth,
            )
            if d["proto"] == "smb":
                sub_bx.add_kv("", Txt(d["uri"] or uri_ph, role="schema"), requires=["smbclient"])

    bx.add_collapsible(
        cc_panel,
        "prn_wiz_adv",
        t("cc_printers.sec_wiz_adv_uri"),
        _build_wiz_adv,
        summary="  ·  ".join(adv_summary_parts),
    )

    _strat, _args, res_drv_lbl = resolve_effective_driver(d, smart)
    bx.add_collapsible(
        cc_panel,
        "prn_wiz_drv",
        t("cc_printers.sec_wiz_driver"),
        lambda sub_bx: _render_driver_selector(sub_bx, cc_panel, canvas, d, smart),
        summary=canvas.truncate_plain(res_drv_lbl, 60),
    )


def _render_split_table_with_wizard(
    bx: Box,
    cc_panel,
    canvas: Canvas,
    headers: list,
    widths: list,
    aligns: list,
    rows: list,
    open_idx: int | None,
    d: dict,
    daemon: dict,
    snap: dict,
    is_tcp: bool,
    open_ports: list[int] | None = None,
) -> None:
    if open_idx is None or not (0 <= open_idx < len(rows)):
        bx.add_table(headers=headers, widths=widths, rows=rows, aligns=aligns)
        return

    bx.add_table(headers=headers, widths=widths, rows=rows[: open_idx + 1], aligns=aligns)
    _render_attached_wizard(bx, cc_panel, canvas, d, daemon, snap, is_tcp=is_tcp, open_ports=open_ports)
    if open_idx + 1 < len(rows):
        bx.add_section("")
        bx.add_table(headers=headers, widths=widths, rows=rows[open_idx + 1 :], aligns=aligns, show_headers=False)


def build_cards(cc_panel, canvas: Canvas) -> list:
    cards = feedback_cards() + build_privilege_modal_card(cc_panel, canvas)
    daemon = get_cups_daemon_state()
    snap = get_printers_snapshot()
    disc_devs = get_discovered_devices()
    tcp_st = get_tcp_scan_state()
    tcp_devs = tcp_st["devices"]
    d = get_add_draft()
    wiz_target = str(cc_panel.get_state("prn_wiz_target", "") or "")
    scanning = is_discovery_running() or is_tcp_scan_running() or tcp_st["scanning"]

    # --- Unified Card 1: Discovery (TCP/IP LAN + USB/CUPS) + Attached / Manual Provisioning Drawer ---
    unified_entries: list[dict] = []
    tcp_ips: set[str] = set()
    for idx, dv in enumerate(tcp_devs[:24]):
        tcp_ips.add(dv["ip"])
        unified_entries.append({
            "target_id": f"tcp:{idx}",
            "open_uri": f"cc_prn:wiz_open_tcp:{idx}",
            "label": dv["label"],
            "endpoint": dv["ip"],
            "proto_ports": " ".join(f":{p}" for p in dv["open_ports"]),
            "uri": dv["uri"],
            "ip": dv["ip"],
            "icon": "\uf6ff",
            "is_tcp": True,
            "open_ports": dv["open_ports"],
        })
    for idx, dv in enumerate(disc_devs[:16]):
        u_host = urlparse(dv["uri"]).hostname or ""
        if u_host and u_host in tcp_ips:
            continue
        unified_entries.append({
            "target_id": f"disc:{idx}",
            "open_uri": f"cc_prn:wiz_open_disc:{idx}",
            "label": dv["label"],
            "endpoint": u_host or dv["source"].upper(),
            "proto_ports": dv["proto"],
            "uri": dv["uri"],
            "ip": u_host,
            "icon": "\uf02f",
            "is_tcp": False,
            "open_ports": None,
        })

    box_acts = []
    if scanning:
        box_acts.append(Badge(t("cc_printers.scanning_badge"), style="warn"))
    else:
        box_acts.append(ActionBtn(t("cc_printers.scan_all_btn"), "cc_prn:scan_all", tone="apply"))
    if wiz_target == "manual":
        box_acts.append(ActionBtn(t("cc_printers.close_manual_btn"), "cc_prn:wiz_close", tone="neutral"))
    else:
        box_acts.append(ActionBtn(t("cc_printers.open_manual_btn"), "cc_prn:wiz_open_manual", tone="neutral"))
    if daemon["scheduler_up"] and daemon["has_cups_pdf"] and not snap["has_pdf_queue"]:
        box_acts.append(ActionBtn(t("cc_printers.create_pdf_btn"), "cc_prn:create_pdf_queue", tone="apply"))
    if not daemon["cups_installed"]:
        box_acts.append(Badge(t("ui.missing", p0="cups"), style="warn"))
    elif not daemon["scheduler_up"]:
        if daemon.get("has_systemctl", True):
            box_acts.append(ActionBtn(t("cc_printers.start_cups"), "cc_prn:svc:start:cups.service", tone="apply"))
        else:
            box_acts.append(Badge(t("ui.missing", p0="systemctl"), style="warn"))

    disc_box = Box(
        t("cc_printers.unified_discovery_box_title", p0=len(unified_entries)),
        icon="\uf02f",
        actions=box_acts,
        highlighted=False,
    )

    sub_items = [
        SelectPill(sub, f"cc_prn:tcp_subnet_pick:{sub}", selected=(tcp_st["active_subnet"] == sub))
        for sub in tcp_st["subnets"][:3]
    ]
    sub_items.append(
        InlineField(
            "prn_tcp_subnet_in",
            "cc_prn:tcp_subnet_custom",
            value=tcp_st["active_subnet"] if tcp_st["is_custom"] else "",
            empty_label=t("cc_printers.custom_subnet_btn"),
            placeholder=t("cc_printers.ph_tcp_subnet"),
            width=18,
            selected=tcp_st["is_custom"],
        )
    )
    disc_box.add_kv(t("cc_printers.kv_tcp_subnet"), " ".join(sub_items))

    if not unified_entries:
        hint = (
            t("cc_printers.tcp_scanning_wait_hint")
            if scanning
            else t("cc_printers.unified_empty_hint", p0=tcp_st["active_subnet"])
        )
        disc_box.add_row(f"  {Txt(hint, role='attention' if scanning else 'inactive')}")
        if wiz_target == "manual":
            _render_attached_wizard(
                disc_box,
                cc_panel,
                canvas,
                d,
                daemon,
                snap,
                is_tcp=(d["proto"] == "socket"),
                is_manual=True,
            )
    else:
        open_idx = next((i for i, ent in enumerate(unified_entries) if ent["target_id"] == wiz_target), None)
        t_rows = []
        for idx, ent in enumerate(unified_entries):
            is_open = (idx == open_idx)
            inst_p = _find_installed_printer(snap["printers"], ent["uri"], ent["ip"])
            if is_open:
                act_cell = ActionBtn(t("cc_printers.close_setup_btn"), "cc_prn:wiz_close", tone="neutral")
            elif inst_p:
                iname = inst_p["name"]
                act_cell = f"{StateBadge(True, t('cc_printers.already_installed'))} {ActionBtn(t('cc_printers.open_printer_btn'), f'cc_prn:open_installed:{iname}', tone='neutral')}"
            else:
                act_cell = ActionBtn(t("cc_printers.use_device_btn"), ent["open_uri"], tone="apply")
            t_rows.append([
                InspectorCell(canvas.truncate_plain(ent["label"], 28), ent["open_uri"], selected=is_open, icon=ent["icon"]),
                Txt(canvas.truncate_plain(ent["endpoint"], 16), role="val"),
                Txt(canvas.truncate_plain(ent["proto_ports"], 16), role="schema"),
                act_cell,
            ])

        sel_ent = unified_entries[open_idx] if open_idx is not None else None
        _render_split_table_with_wizard(
            disc_box,
            cc_panel,
            canvas,
            headers=[t("cc_printers.col_device"), t("cc_printers.col_ip_addr"), t("cc_printers.col_open_ports"), t("cc_printers.col_actions")],
            widths=[30, 16, 16, "flex"],
            aligns=["left", "left", "left", "right"],
            rows=t_rows,
            open_idx=open_idx,
            d=d,
            daemon=daemon,
            snap=snap,
            is_tcp=bool(sel_ent["is_tcp"]) if sel_ent else False,
            open_ports=sel_ent["open_ports"] if sel_ent else None,
        )
        if wiz_target == "manual":
            _render_attached_wizard(
                disc_box,
                cc_panel,
                canvas,
                d,
                daemon,
                snap,
                is_tcp=(d["proto"] == "socket"),
                is_manual=True,
            )

    cards.append(disc_box)

    # --- Card 4: CUPS Printer Classes & Failover Pools ---
    classes = get_printer_classes()
    pend_conf = get_pending_confirm(cc_panel)
    is_cls_open = bool(cc_panel.get_state("prn_sec_classes", False))
    cls_summary = (
        t("cc_printers.classes_summary_n", p0=len(classes))
        if classes
        else t("cc_printers.classes_summary_empty")
    )
    cls_box = Box(
        t("cc_printers.sec_classes_pools"),
        icon="\uf1b2",
        actions=[FoldIconBtn(is_cls_open, f"ui:state:prn_sec_classes:{'false' if is_cls_open else 'true'}")],
        highlighted=False,
    )
    if not is_cls_open:
        cls_box.add_row(f"  {Txt(cls_summary, role='schema')}")
    else:
        new_pool_b = ActionBtn(
            t("cc_printers.new_pool_btn"),
            "cc_prn:class_new_prompt",
            tone="apply" if snap["printers"] else "muted",
        )
        if not classes:
            cls_box.add_kv(t("cc_printers.kv_pool_manage"), Txt(t("cc_printers.no_classes_hint"), role="inactive"), right_markup=new_pool_b)
        else:
            valid_cls = [c["name"] for c in classes]
            sel_cls_name = str(cc_panel.get_state("prn_selected_class", "") or "")
            if sel_cls_name not in valid_cls:
                sel_cls_name = valid_cls[0]

            c_rows = []
            for cl in classes:
                cname = cl["name"]
                is_csel = (cname == sel_cls_name)
                mem_s = ", ".join(cl["members"]) if cl["members"] else t("common.none")
                if pend_conf == f"class_delete:{cname}":
                    act_c = (
                        f"{ActionBtn(t('cc_printers.confirm_remove_btn'), f'cc_prn:class_delete:{cname}', tone='danger_soft')} "
                        f"{ActionBtn(t('common.cancel'), 'cc_prn:cancel_confirm', tone='neutral')}"
                    )
                else:
                    act_c = ActionBtn(t("cc_printers.remove_btn"), f"cc_prn:ask_confirm:class_delete:{cname}", tone="danger_soft")
                c_rows.append([
                    InspectorCell(canvas.truncate_plain(cname, 18), f"cc_prn:select_class:{cname}", selected=is_csel, icon="\uf1b2"),
                    Txt(canvas.truncate_plain(mem_s, 44), role="val"),
                    act_c,
                ])

            cls_box.add_table(
                headers=[t("cc_printers.col_class"), t("cc_printers.col_members"), t("cc_printers.col_actions")],
                widths=[22, "flex", 24],
                rows=c_rows,
                aligns=["left", "left", "right"],
            )

            sel_cl = next((c for c in classes if c["name"] == sel_cls_name), None)
            if sel_cl and snap["printers"]:
                mem_pills = " ".join(
                    SelectPill(
                        p["name"],
                        f"cc_prn:class_toggle_member:{sel_cls_name}:{p['name']}",
                        selected=(p["name"] in sel_cl["members"]),
                    )
                    for p in snap["printers"][:8]
                )
                cls_box.add_kv(t("cc_printers.kv_pool_members_of", p0=sel_cls_name), mem_pills, right_markup=new_pool_b)
    cards.append(cls_box)
    return cards
