#!/usr/bin/env python3
"""NC-UI Control Center — Printers & Preferences (`panels/cc_printers_devices.py`)."""

import os
from urllib.parse import urlparse

from core.i18n import t
from core.privileges import build_privilege_modal_card
from ui.canvas import Canvas
from ui.kit import (
    ActionBtn,
    Badge,
    Box,
    CyclePill,
    InlineField,
    InlineFilter,
    InspectorCell,
    ProgressBar,
    SelectPill,
    Slider,
    StateBadge,
    Txt,
    get_active_inline_entry,
)
from panels.cc_engine_printers import (
    build_offline_spooler_card,
    feedback_cards,
    get_cups_daemon_state,
    get_drv_edit_state,
    get_pending_confirm,
    get_printer_options,
    get_printer_supplies,
    get_printers_snapshot,
    get_quick_print_draft,
    humanize_ppd_choice,
    search_cups_drivers,
    validate_ppd_file,
)


def _pill_row(prn_name: str, key: str, current: str, choices: list[tuple[str, str]]) -> str:
    return " ".join(
        SelectPill(lbl, f"cc_prn:set_opt:{prn_name}:{key}={val}", selected=(current == val))
        for val, lbl in choices
    )


def _editable_kv_row(
    bx: Box,
    canvas: Canvas,
    key_label: str,
    entry_id: str,
    trigger_uri: str,
    current_val: str,
    placeholder: str,
    extra_right: str = "",
) -> None:
    if get_active_inline_entry() == entry_id:
        fld = InlineField(entry_id, trigger_uri, value=current_val, placeholder=placeholder, width=40)
        bx.add_kv(key_label, fld, right_markup=extra_right)
    else:
        val_m = Txt(canvas.truncate_plain(current_val, 44 if extra_right else 52), role="val") if current_val else Txt(t("common.unset"), role="inactive")
        edit_b = ActionBtn(t("cc_printers.edit_dots_btn"), trigger_uri, tone="neutral")
        r_m = f"{edit_b} {extra_right}".strip() if extra_right else edit_b
        bx.add_kv(key_label, val_m, right_markup=r_m)


def _build_devices_standard(cc_panel, canvas: Canvas) -> list:
    cards = feedback_cards() + build_privilege_modal_card(cc_panel, canvas)
    daemon = get_cups_daemon_state()

    if not daemon["scheduler_up"]:
        cards.append(build_offline_spooler_card(daemon))
        return cards

    snap = get_printers_snapshot()
    printers = snap["printers"]
    dev_filter = str(cc_panel.get_state("prn_dev_filter", "") or "")
    df_low = dev_filter.strip().lower()
    filtered_printers = [
        p for p in printers
        if not df_low
        or df_low in p["name"].lower()
        or df_low in p["uri"].lower()
        or df_low in p["proto"].lower()
        or df_low in p["description"].lower()
        or df_low in p["location"].lower()
        or df_low in p["make_model"].lower()
    ]
    sel_name = str(cc_panel.get_state("prn_selected", "") or "")
    valid_names = [p["name"] for p in (filtered_printers or printers)]
    if sel_name not in valid_names:
        sel_name = snap["default"] if snap["default"] in valid_names else (valid_names[0] if valid_names else "")

    list_box = Box(
        t("cc_printers.installed_printers_n", p0=len(filtered_printers)),
        icon="\uf02f",
        actions=[
            ActionBtn(t("cc_printers.add_printer_btn"), "cc_prn:goto_subtab:discovery", tone="apply"),
            ActionBtn(t("common.refresh"), "cc_prn:refresh", tone="neutral"),
        ],
        highlighted=False,
    )
    if len(printers) > 3 or dev_filter:
        list_box.add_kv(
            t("cc_printers.kv_filter_printers"),
            InlineFilter(
                "prn_dev_filter_in",
                dev_filter,
                t("cc_printers.ph_filter_printers"),
                "cc_prn:dev_filter_open",
                clear_uri="cc_prn:dev_filter_clear",
                width=34,
            ),
        )
    if not printers:
        list_box.add_row(f"  {Txt(t('cc_printers.no_printers_hint'), role='inactive')}")
    elif not filtered_printers:
        list_box.add_row(f"  {Txt(t('cc_printers.no_printers_match'), role='inactive')}")
    else:
        toggle_w = max(len(t("cc_printers.pause")), len(t("cc_printers.resume")))
        def_w = len(t("cc_printers.set_default"))
        rows = []
        for p in filtered_printers:
            pname = p["name"]
            is_sel = (pname == sel_name)
            if not p["enabled"]:
                st_m = StateBadge(False, t("common.active"), t("cc_printers.paused"))
            elif p["state"] == "printing":
                st_m = StateBadge(True, t("cc_printers.printing"))
            elif not p["accepting"]:
                st_m = Badge(t("cc_printers.rejecting"), style="warn")
            else:
                st_m = StateBadge(True, t("cc_printers.ready"))

            q_txt = Txt(str(p["jobs_count"]), role="attention" if p["jobs_count"] > 0 else "val")
            conn_s = f"{p['proto']} · {canvas.truncate_plain(p['uri'], 28)}" if p["uri"] else p["proto"]
            def_slot = (
                StateBadge(True, t("cc_printers.default_badge"))
                if p["is_default"]
                else ActionBtn(t("cc_printers.set_default"), f"cc_prn:set_default:{pname}", tone="apply", min_chars=def_w)
            )
            pause_btn = (
                ActionBtn(t("cc_printers.pause"), f"cc_prn:pause_prn:{pname}", tone="danger_soft", min_chars=toggle_w)
                if p["enabled"]
                else ActionBtn(t("cc_printers.resume"), f"cc_prn:enable_prn:{pname}", tone="apply", min_chars=toggle_w)
            )
            rows.append([
                InspectorCell(canvas.truncate_plain(pname, 20), f"cc_prn:select_prn:{pname}", selected=is_sel, icon="\uf02f"),
                st_m,
                q_txt,
                Txt(conn_s, role="schema"),
                f"{def_slot} {pause_btn}",
            ])

        list_box.add_table(
            headers=[
                t("cc_printers.col_printer"),
                t("cc_printers.col_state"),
                t("cc_printers.col_jobs"),
                t("cc_printers.col_connection"),
                t("cc_printers.col_actions"),
            ],
            widths=[22, 11, 5, "flex", 26],
            rows=rows,
            aligns=["left", "left", "right", "left", "right"],
        )
    cards.append(list_box)

    sel = next((p for p in printers if p["name"] == sel_name), None)
    if not sel:
        return cards

    pname = sel["name"]
    opts = get_printer_options(pname)
    supplies = get_printer_supplies(pname)
    pend_conf = get_pending_confirm(cc_panel)

    insp_acts = [
        ActionBtn(t("cc_printers.print_file_btn"), f"cc_prn:print_file:{pname}", tone="apply"),
        ActionBtn(t("cc_printers.test_page"), f"cc_prn:test_page:{pname}", tone="neutral"),
        ActionBtn(t("cc_printers.open_queue_btn"), "cc_prn:goto_subtab:queue", tone="neutral"),
    ]
    if pend_conf == f"delete_prn:{pname}":
        insp_acts.append(ActionBtn(t("cc_printers.confirm_remove_btn"), f"cc_prn:delete_prn:{pname}", tone="danger_soft"))
        insp_acts.append(ActionBtn(t("common.cancel"), "cc_prn:cancel_confirm", tone="neutral"))
    else:
        insp_acts.append(ActionBtn(t("cc_printers.remove_btn"), f"cc_prn:ask_confirm:delete_prn:{pname}", tone="danger_soft"))

    insp = Box(
        t("cc_printers.inspector_title", p0=pname),
        icon="\uf085",
        actions=insp_acts,
        highlighted=False,
    )

    _editable_kv_row(
        insp,
        canvas,
        t("cc_printers.kv_prn_name"),
        f"prn_edit_name_{pname}",
        f"cc_prn:rename_prn:{pname}",
        pname,
        t("cc_printers.ph_queue_name"),
        extra_right=ActionBtn(t("cc_printers.clone_btn"), f"cc_prn:clone_prompt:{pname}", tone="neutral"),
    )

    is_net = sel["proto"] in ("IPP", "IPPS", "TCP:9100", "RAW:9100", "LPD", "SMB", "MDNS")
    net_host = (urlparse(sel["uri"]).hostname or "") if (is_net and "://" in sel["uri"]) else ""
    uri_extra = []
    if is_net and sel["uri"]:
        uri_extra.append(ActionBtn(t("cc_printers.probe_port_btn"), f"cc_prn:probe_uri:{sel['uri']}", tone="neutral"))
    if net_host:
        uri_extra.append(ActionBtn(t("cc_printers.open_addr_btn"), f"cc_prn:open_addr:{pname}", tone="neutral"))
    _editable_kv_row(
        insp,
        canvas,
        t("cc_printers.kv_device_uri"),
        f"prn_edit_uri_{pname}",
        f"cc_prn:edit_uri:{pname}",
        sel["uri"],
        "ipp://10.10.0.45:631/ipp/print",
        extra_right=" ".join(uri_extra),
    )

    _editable_kv_row(
        insp,
        canvas,
        t("cc_printers.kv_description"),
        f"prn_edit_desc_{pname}",
        f"cc_prn:edit_desc:{pname}",
        sel["description"],
        t("cc_printers.ph_description"),
    )
    _editable_kv_row(
        insp,
        canvas,
        t("cc_printers.kv_location"),
        f"prn_edit_loc_{pname}",
        f"cc_prn:edit_loc:{pname}",
        sel["location"],
        t("cc_printers.ph_location"),
    )

    acc_pills = (
        f"{SelectPill(t('cc_printers.accepting_on'), f'cc_prn:accept_prn:{pname}:1', selected=sel['accepting'])} "
        f"{SelectPill(t('cc_printers.accepting_off'), f'cc_prn:accept_prn:{pname}:0', selected=not sel['accepting'])}"
    )
    shr_pills = (
        f"{SelectPill(t('cc_printers.shared_on'), f'cc_prn:share_prn:{pname}:1', selected=sel['shared'])} "
        f"{SelectPill(t('cc_printers.shared_off'), f'cc_prn:share_prn:{pname}:0', selected=not sel['shared'])}"
    )
    insp.add_kv(t("cc_printers.kv_queue_sharing"), f"{acc_pills}  {shr_pills}")

    if sel["alerts"]:
        rec_b = ActionBtn(t("cc_printers.restart_prn_btn"), f"cc_prn:restart_prn:{pname}", tone="apply")
        insp.add_kv(t("cc_printers.kv_alerts"), Txt(sel["alerts"], role="attention"), right_markup=rec_b)

    # --- Collapsible 0: Driver / PPD Configuration ---
    drv_str = sel["make_model"] or t("cc_printers.driverless_ipp")
    if sel["classes"]:
        drv_str = f"{drv_str}  ·  {t('cc_printers.pool_lbl')}: {', '.join(sel['classes'])}"

    def _build_driver_switch(bx: Box) -> None:
        de_raw = get_drv_edit_state()
        de = de_raw if de_raw["printer"] == pname else {"printer": pname, "mode": "everywhere", "drv_id": "everywhere", "query": "", "ppd_path": ""}
        bx.add_kv(t("cc_printers.kv_driver_model"), Txt(canvas.truncate_plain(drv_str, 54), role="val"))
        drv_modes = [
            ("everywhere", t("cc_printers.drv_everywhere")),
            ("db", t("cc_printers.drv_database")),
            ("raw", t("cc_printers.drv_raw")),
            ("ppd", t("cc_printers.drv_custom_ppd")),
        ]
        m_pills = " ".join(
            SelectPill(lbl, f"cc_prn:drv_edit_mode:{mid}", selected=(de["mode"] == mid))
            for mid, lbl in drv_modes
        )
        bx.add_kv(t("cc_printers.kv_driver_mode"), m_pills)
        if de["mode"] == "db":
            flt = InlineFilter(
                "prn_drv_edit_in",
                de["query"],
                t("cc_printers.ph_search_drv"),
                "cc_prn:drv_edit_search",
                clear_uri="cc_prn:drv_edit_clear",
                width=32,
            )
            bx.add_kv(t("cc_printers.kv_search_drv"), flt)
            for drv_id, lbl in search_cups_drivers(de["query"], limit=4):
                bx.add_kv(
                    "",
                    SelectPill(canvas.truncate_plain(lbl, 56), f"cc_prn:drv_edit_pick:{drv_id}", selected=(de["drv_id"] == drv_id)),
                )
        elif de["mode"] == "ppd":
            ppd_b = ActionBtn(t("cc_printers.browse_ppd_btn"), "cc_prn:drv_edit_ppd", tone="neutral")
            if de["ppd_path"]:
                v_ok, _ = validate_ppd_file(de["ppd_path"])
                st_b = StateBadge(True, t("cc_printers.ppd_valid_badge")) if v_ok else Badge(t("cc_printers.ppd_invalid_badge"), style="crit")
                bx.add_kv(t("cc_printers.kv_ppd_file"), f"{Txt(os.path.basename(de['ppd_path']), role='val')}  {st_b}", right_markup=ppd_b)
            else:
                bx.add_kv(t("cc_printers.kv_ppd_file"), Txt(t("common.unset"), role="inactive"), right_markup=ppd_b)
        bx.add_kv("", ActionBtn(t("cc_printers.apply_drv_btn"), f"cc_prn:drv_edit_apply:{pname}", tone="apply"))

    insp.add_collapsible(
        cc_panel,
        "prn_sec_driver",
        t("cc_printers.sec_driver_switch"),
        _build_driver_switch,
        summary=canvas.truncate_plain(drv_str, 64),
    )

    # --- Collapsible 1: Printing Preferences & Media Defaults ---
    ppd = opts["ppd"]
    job = opts["job"]
    cur_media = ppd["PageSize"]["current"] if "PageSize" in ppd else job.get("media", "A4")
    cur_dup_raw = ppd["Duplex"]["current"] if "Duplex" in ppd else job.get("sides", "one-sided")
    cur_col_raw = ppd["ColorModel"]["current"] if "ColorModel" in ppd else job.get("print-color-mode", "color")
    cur_nup = job.get("number-up", "1")
    prefs_summary = t(
        "cc_printers.prefs_summary",
        p0=cur_media,
        p1=humanize_ppd_choice("Duplex", cur_dup_raw),
        p2=humanize_ppd_choice("ColorModel", cur_col_raw),
        p3=f"{cur_nup}-up",
    )

    def _build_prefs(bx: Box) -> None:
        if "PageSize" in ppd:
            ch = [(c, c) for c in ppd["PageSize"]["choices"][:7]]
            bx.add_kv(t("cc_printers.opt_media_size"), _pill_row(pname, "PageSize", ppd["PageSize"]["current"], ch))
        else:
            ch = [("A4", "A4"), ("Letter", "Letter"), ("A3", "A3"), ("A5", "A5"), ("Legal", "Legal")]
            bx.add_kv(t("cc_printers.opt_media_size"), _pill_row(pname, "media", job.get("media", "A4"), ch))

        if "InputSlot" in ppd:
            ch = [(c, humanize_ppd_choice("InputSlot", c)) for c in ppd["InputSlot"]["choices"][:6]]
            bx.add_kv(t("cc_printers.opt_input_tray"), _pill_row(pname, "InputSlot", ppd["InputSlot"]["current"], ch))
        else:
            ch = [
                ("auto", t("cc_printers.tray_auto")),
                ("tray-1", f"{t('cc_printers.tray_n')} 1"),
                ("tray-2", f"{t('cc_printers.tray_n')} 2"),
                ("manual", t("cc_printers.tray_manual")),
            ]
            bx.add_kv(t("cc_printers.opt_input_tray"), _pill_row(pname, "media-source", job.get("media-source", "auto"), ch))

        if "MediaType" in ppd:
            ch = [(c, humanize_ppd_choice("MediaType", c)) for c in ppd["MediaType"]["choices"][:5]]
            bx.add_kv(t("cc_printers.opt_media_type"), _pill_row(pname, "MediaType", ppd["MediaType"]["current"], ch))

        if "Duplex" in ppd:
            ch = [(c, humanize_ppd_choice("Duplex", c)) for c in ppd["Duplex"]["choices"][:4]]
            bx.add_kv(t("cc_printers.opt_duplex"), _pill_row(pname, "Duplex", ppd["Duplex"]["current"], ch))
        else:
            ch = [
                ("one-sided", t("cc_printers.duplex_off")),
                ("two-sided-long-edge", t("cc_printers.duplex_long")),
                ("two-sided-short-edge", t("cc_printers.duplex_short")),
            ]
            bx.add_kv(t("cc_printers.opt_duplex"), _pill_row(pname, "sides", job.get("sides", "one-sided"), ch))

        if "ColorModel" in ppd:
            ch = [(c, humanize_ppd_choice("ColorModel", c)) for c in ppd["ColorModel"]["choices"][:4]]
            bx.add_kv(t("cc_printers.opt_color_mode"), _pill_row(pname, "ColorModel", ppd["ColorModel"]["current"], ch))
        else:
            ch = [("color", t("cc_printers.color_rgb")), ("monochrome", t("cc_printers.color_mono"))]
            bx.add_kv(t("cc_printers.opt_color_mode"), _pill_row(pname, "print-color-mode", job.get("print-color-mode", "color"), ch))

        q_key = "cupsPrintQuality" if "cupsPrintQuality" in ppd else ("Resolution" if "Resolution" in ppd else "")
        if q_key:
            ch = [(c, humanize_ppd_choice(q_key, c)) for c in ppd[q_key]["choices"][:5]]
            bx.add_kv(t("cc_printers.opt_quality"), _pill_row(pname, q_key, ppd[q_key]["current"], ch))
        else:
            ch = [("3", t("cc_printers.qual_draft")), ("4", t("cc_printers.qual_normal")), ("5", t("cc_printers.qual_high"))]
            bx.add_kv(t("cc_printers.opt_quality"), _pill_row(pname, "print-quality", job.get("print-quality", "4"), ch))

        ori_p = _pill_row(
            pname,
            "orientation-requested",
            job.get("orientation-requested", "3"),
            [("3", t("cc_printers.orient_portrait")), ("4", t("cc_printers.orient_landscape"))],
        )
        bx.add_kv(t("cc_printers.opt_orientation"), ori_p)

        nup_p = _pill_row(pname, "number-up", cur_nup, [("1", "1-up"), ("2", "2-up"), ("4", "4-up"), ("6", "6-up")])
        bx.add_kv(t("cc_printers.opt_nup"), nup_p)

        scale_p = _pill_row(
            pname,
            "print-scaling",
            job.get("print-scaling", "auto"),
            [
                ("auto", "Auto"),
                ("fit", t("cc_printers.scale_fit")),
                ("fill", t("cc_printers.scale_fill")),
                ("none", t("cc_printers.scale_none")),
            ],
        )
        bx.add_kv(t("cc_printers.opt_scaling"), scale_p)

        err_p = " ".join(
            SelectPill(lbl, f"cc_prn:err_policy:{pname}:{pol}", selected=(sel["error_policy"] == pol))
            for pol, lbl in (
                ("retry-job", t("cc_printers.epol_retry")),
                ("abort-job", t("cc_printers.epol_abort")),
                ("stop-printer", t("cc_printers.epol_stop")),
            )
        )
        bx.add_kv(t("cc_printers.opt_error_policy"), err_p)

        ban_p = _pill_row(
            pname,
            "job-sheets",
            job.get("job-sheets", "none"),
            [("none", t("cc_printers.banner_none")), ("standard", t("cc_printers.banner_std"))],
        )
        rst_b = ActionBtn(t("cc_printers.reset_user_opts_btn"), f"cc_prn:reset_user_opts:{pname}", tone="warn")
        bx.add_kv(t("cc_printers.opt_banner"), ban_p, right_markup=rst_b)

        extra_opts = opts["extra"]
        extra_groups = opts.get("extra_groups", [])
        if extra_opts:
            bx.add_section(t("cc_printers.sec_ppd_extra_opts"))
            cur_grp = str(cc_panel.get_state("prn_ppd_group", "") or "")
            valid_gids = [g[0] for g in extra_groups]
            if cur_grp not in valid_gids and valid_gids:
                cur_grp = valid_gids[0]
            if len(extra_groups) > 1:
                grp_pills = " ".join(
                    SelectPill(canvas.truncate_plain(glbl, 18), f"cc_prn:ppd_grp:{gid}", selected=(gid == cur_grp))
                    for gid, glbl in extra_groups
                )
                bx.add_kv("", grp_pills)
            shown_extras = [e for e in extra_opts if not cur_grp or e.get("group") == cur_grp]
            for ext in shown_extras:
                if len(ext["choices"]) <= 6:
                    ch = [(c, humanize_ppd_choice(ext["key"], c)) for c in ext["choices"]]
                    bx.add_kv(canvas.truncate_plain(ext["label"], 20), _pill_row(pname, ext["key"], ext["current"], ch))
                else:
                    cur_lbl = humanize_ppd_choice(ext["key"], ext["current"])
                    bx.add_kv(
                        canvas.truncate_plain(ext["label"], 20),
                        CyclePill(canvas.truncate_plain(cur_lbl, 28), f"cc_prn:cycle_opt:{pname}:{ext['key']}", active=True),
                    )

    insp.add_collapsible(
        cc_panel,
        "prn_sec_prefs",
        t("cc_printers.sec_printing_prefs"),
        _build_prefs,
        summary=prefs_summary,
    )

    # --- Collapsible 2: Quick Print Job Composer ---
    qp = get_quick_print_draft()
    qp_summary = (
        t("cc_printers.qp_summary_ready", p0=os.path.basename(qp["file"]), p1=qp["copies"])
        if qp["file"]
        else t("cc_printers.qp_summary_empty")
    )

    def _build_qprint(bx: Box) -> None:
        f_txt = (
            Txt(canvas.truncate_plain(qp["file"], 48), role="val")
            if qp["file"]
            else Txt(t("cc_printers.qp_no_file"), role="inactive")
        )
        pick_b = ActionBtn(t("cc_printers.qp_choose_file_btn"), "cc_prn:qp_pick_file", tone="neutral")
        bx.add_kv(t("cc_printers.kv_qp_document"), f_txt, right_markup=pick_b)

        cop_slider = Slider(
            min(20, qp["copies"]),
            "cc_prn:qp_copies",
            min_val=1,
            max_val=20,
            width=19,
            show_pct=False,
            step=1,
        )
        cop_custom = InlineField(
            "prn_qp_copies_in",
            "cc_prn:qp_copies_edit",
            value=str(qp["copies"]) if qp["copies"] > 20 else "",
            placeholder="1..99",
            empty_label=t("cc_printers.custom_dots"),
            width=6,
            selected=(qp["copies"] > 20),
        )
        cop_n = qp["copies"]
        bx.add_kv(
            t("cc_printers.kv_qp_copies"),
            f"{cop_slider}  {Txt(f'{cop_n}x', role='val')}  {cop_custom}",
        )

        pg_all = SelectPill(t("cc_printers.pages_all"), "cc_prn:qp_pages_all", selected=(not qp["pages"]))
        pg_rng = InlineField(
            "prn_qp_pages_in",
            "cc_prn:qp_pages_edit",
            value=qp["pages"],
            placeholder="1-5,8",
            empty_label=t("cc_printers.pages_range_btn"),
            width=16,
            selected=bool(qp["pages"]),
        )
        bx.add_kv(t("cc_printers.kv_qp_pages"), f"{pg_all} {pg_rng}")

        fit_pills = (
            f"{SelectPill(t('cc_printers.scale_none'), 'cc_prn:qp_fit:0', selected=not qp['fit'])} "
            f"{SelectPill(t('cc_printers.scale_fit'), 'cc_prn:qp_fit:1', selected=qp['fit'])}"
        )
        bx.add_kv(t("cc_printers.kv_qp_fit"), fit_pills)

        sd_pills = " ".join(
            SelectPill(lbl, f"cc_prn:qp_sides:{sv}", selected=(qp["sides"] == sv))
            for sv, lbl in (
                ("default", t("cc_printers.use_printer_def")),
                ("one-sided", t("cc_printers.duplex_off")),
                ("two-sided-long-edge", t("cc_printers.duplex_long")),
                ("two-sided-short-edge", t("cc_printers.duplex_short")),
            )
        )
        bx.add_kv(t("cc_printers.opt_duplex"), sd_pills)

        cl_pills = " ".join(
            SelectPill(lbl, f"cc_prn:qp_color:{cv}", selected=(qp["color"] == cv))
            for cv, lbl in (
                ("default", t("cc_printers.use_printer_def")),
                ("color", t("cc_printers.color_rgb")),
                ("monochrome", t("cc_printers.color_mono")),
            )
        )
        run_b = ActionBtn(
            t("cc_printers.qp_print_now"),
            f"cc_prn:qp_submit:{pname}",
            tone="apply" if qp["file"] else "muted",
        )
        bx.add_kv(t("cc_printers.opt_color_mode"), cl_pills, right_markup=run_b)

    insp.add_collapsible(
        cc_panel,
        "prn_sec_qprint",
        t("cc_printers.sec_quick_print"),
        _build_qprint,
        summary=qp_summary,
    )

    # --- Collapsible 3: Supply Levels (Ink / Toner) & Maintenance ---
    markers = supplies["markers"]
    sup_summary = (
        "  ·  ".join(f"{m['name']}: {m['level']}%" if m["level"] >= 0 else f"{m['name']}: {t('common.nd')}" for m in markers[:4])
        if markers
        else t("cc_printers.supplies_summary_empty")
    )

    def _build_supplies(bx: Box) -> None:
        if markers:
            for m in markers:
                lvl = int(m["level"])
                if 0 <= lvl <= 100:
                    role = "fault" if lvl <= 10 else ("attention" if lvl <= 25 else "vitality")
                    bar = f"{ProgressBar(lvl, width=18, invert_threshold=True)}  {Txt(f'{lvl:3d}%', role=role)}  {Txt(m['type'], role='schema')}"
                else:
                    bar = Txt(t("cc_printers.level_unknown"), role="inactive")
                bx.add_kv(canvas.truncate_plain(m["name"], 20), bar)
        else:
            bx.add_row(f"  {Txt(t('cc_printers.no_ipp_markers_hint'), role='inactive')}")

        if supplies["reasons"]:
            bx.add_kv(t("cc_printers.kv_ipp_reasons"), Txt(", ".join(supplies["reasons"][:4]), role="attention"))

        maint_btns = [
            ActionBtn(t("cc_printers.maint_levels"), f"cc_prn:maint:{pname}:levels", tone="neutral"),
            ActionBtn(t("cc_printers.identify_btn"), f"cc_prn:identify_prn:{pname}", tone="neutral"),
            ActionBtn(t("cc_printers.maint_selftest"), f"cc_prn:maint:{pname}:selftest", tone="neutral"),
            ActionBtn(t("cc_printers.maint_clean"), f"cc_prn:maint:{pname}:clean", tone="warn"),
        ]
        bx.add_kv(t("cc_printers.kv_maintenance"), " ".join(maint_btns))

    insp.add_collapsible(
        cc_panel,
        "prn_sec_supplies",
        t("cc_printers.sec_supplies_maint"),
        _build_supplies,
        requires=["ipptool"],
        summary=sup_summary,
    )

    cards.append(insp)
    return cards


def build_cards(cc_panel, canvas: Canvas) -> list:
    return _build_devices_standard(cc_panel, canvas)
