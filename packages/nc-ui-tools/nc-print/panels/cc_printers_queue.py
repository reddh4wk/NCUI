#!/usr/bin/env python3
"""NC-UI Control Center — Print Queue & Spooler Jobs (`panels/cc_printers_queue.py`)."""

from core.i18n import t
from core.privileges import build_privilege_modal_card
from ui.canvas import Canvas
from ui.kit import (
    ActionBtn,
    Badge,
    Box,
    CyclePill,
    InlineFilter,
    InspectorCell,
    SelectPill,
    StateBadge,
    Txt,
)
from ui.markup import format_bytes_short
from panels.cc_engine_printers import (
    build_offline_spooler_card,
    feedback_cards,
    get_cups_daemon_state,
    get_pending_confirm,
    get_print_jobs,
    get_printers_snapshot,
)


def _prio_bucket(prio_val: int) -> str:
    if prio_val <= 35:
        return "25"
    if prio_val <= 62:
        return "50"
    if prio_val <= 85:
        return "75"
    return "95"


def _build_queue_standard(cc_panel, canvas: Canvas) -> list:
    cards = feedback_cards() + build_privilege_modal_card(cc_panel, canvas)
    daemon = get_cups_daemon_state()

    if not daemon["scheduler_up"]:
        cards.append(build_offline_spooler_card(daemon))
        return cards

    snap = get_printers_snapshot()
    printers = snap["printers"]
    valid_prns = [p["name"] for p in printers]

    q_mode = str(cc_panel.get_state("prn_q_mode", "not-completed") or "not-completed")
    if q_mode not in ("not-completed", "completed"):
        q_mode = "not-completed"

    q_prn = str(cc_panel.get_state("prn_q_printer", "") or "")
    if q_prn and q_prn not in valid_prns:
        q_prn = ""

    q_filter = str(cc_panel.get_state("prn_q_filter", "") or "")
    all_jobs = get_print_jobs(which=q_mode, printer=q_prn)
    ql = q_filter.strip().lower()
    jobs = [
        j
        for j in all_jobs
        if not ql
        or ql in j["id"].lower()
        or ql in j["title"].lower()
        or ql in j["user"].lower()
        or ql in j["printer"].lower()
        or ql in j["submitted"].lower()
    ]

    target_prn_for_print = q_prn or snap["default"] or (valid_prns[0] if valid_prns else "")
    pend_conf = get_pending_confirm(cc_panel)
    purge_target = q_prn or "ALL"

    hdr_acts = []
    if target_prn_for_print:
        hdr_acts.append(ActionBtn(t("cc_printers.print_file_btn"), f"cc_prn:print_file:{target_prn_for_print}", tone="apply"))
    hdr_acts.append(ActionBtn(t("common.refresh"), "cc_prn:refresh", tone="neutral"))
    if q_mode == "not-completed" and all_jobs:
        if pend_conf == f"purge_queue:{purge_target}":
            hdr_acts.append(ActionBtn(t("cc_printers.confirm_purge_btn"), f"cc_prn:purge_queue:{purge_target}", tone="danger_soft"))
            hdr_acts.append(ActionBtn(t("common.cancel"), "cc_prn:cancel_confirm", tone="neutral"))
        else:
            hdr_acts.append(ActionBtn(t("cc_printers.purge_queue_btn"), f"cc_prn:ask_confirm:purge_queue:{purge_target}", tone="danger_soft"))

    q_box = Box(
        t("cc_printers.queue_box_title_n", p0=len(jobs)),
        icon="\uf0cb",
        actions=hdr_acts,
        highlighted=False,
    )

    mode_pills = (
        f"{SelectPill(t('cc_printers.q_active'), 'cc_prn:q_mode:not-completed', selected=(q_mode == 'not-completed'))} "
        f"{SelectPill(t('cc_printers.q_completed'), 'cc_prn:q_mode:completed', selected=(q_mode == 'completed'))}"
    )
    cyc_lbl = q_prn if q_prn else "ALL"
    prn_cyc = CyclePill(cyc_lbl, "cc_prn:q_cycle_prn", active=bool(q_prn))
    q_box.add_kv(t("cc_printers.kv_queue_view"), f"{mode_pills}   {prn_cyc}")

    q_box.add_kv(
        t("cc_printers.kv_queue_search"),
        InlineFilter(
            "prn_q_filter_in",
            q_filter,
            t("cc_printers.ph_filter_jobs"),
            "cc_prn:q_filter_open",
            clear_uri="cc_prn:q_filter_clear",
            width=36,
        ),
    )

    q_box.add_section(t("cc_printers.sec_spooler_jobs"))
    sel_job_id = str(cc_panel.get_state("prn_selected_job", "") or "")
    valid_jids = [j["id"] for j in jobs]
    if sel_job_id not in valid_jids:
        sel_job_id = valid_jids[0] if valid_jids else ""

    if not jobs:
        empty_k = "cc_printers.no_completed_jobs" if q_mode == "completed" else "cc_printers.no_active_jobs"
        q_box.add_row(f"  {Txt(t(empty_k), role='inactive')}")
    else:
        hold_w = max(len(t("cc_printers.hold_btn")), len(t("cc_printers.release_btn")))
        rows = []
        for j in jobs[:35]:
            jid = j["id"]
            is_sel = (jid == sel_job_id)
            if j["state"] == "printing":
                st_m = StateBadge(True, t("cc_printers.printing"))
            elif j["state"] == "held":
                st_m = Badge(t("cc_printers.held_badge"), style="warn")
            elif j["state"] in ("aborted", "stopped"):
                st_m = Badge(t("cc_printers.aborted_badge"), style="crit")
            elif j["state"] == "canceled":
                st_m = Txt(t("cc_printers.canceled_lbl"), role="inactive")
            elif j["state"] == "completed":
                st_m = Txt(t("cc_printers.done_lbl"), role="schema")
            else:
                st_m = Txt(t("cc_printers.pending_lbl"), role="val")

            if q_mode == "not-completed":
                prio_val = int(j.get("priority", 50))
                top_btn = ActionBtn(
                    t("cc_printers.top_btn"),
                    f"cc_prn:job_prio:{jid}:95",
                    tone="neutral" if prio_val < 95 else "muted",
                )
                h_btn = (
                    ActionBtn(t("cc_printers.release_btn"), f"cc_prn:job_hold:{jid}:0", tone="apply", min_chars=hold_w)
                    if j["state"] == "held"
                    else ActionBtn(t("cc_printers.hold_btn"), f"cc_prn:job_hold:{jid}:1", tone="warn", min_chars=hold_w)
                )
                acts_cell = f"{top_btn} {h_btn} {ActionBtn(t('common.cancel'), f'cc_prn:job_cancel:{jid}', tone='danger_soft')}"
            else:
                acts_cell = ActionBtn(t("cc_printers.reprint_btn"), f"cc_prn:job_reprint:{jid}", tone="neutral")

            rows.append([
                InspectorCell(f"#{j['num']}", f"cc_prn:select_job:{jid}", selected=is_sel),
                Txt(canvas.truncate_plain(j["title"], 26), role="focus"),
                Txt(canvas.truncate_plain(j["printer"], 17), role="schema"),
                Txt(format_bytes_short(j["size"]), role="val"),
                st_m,
                acts_cell,
            ])

        q_box.add_table(
            headers=[
                t("cc_printers.col_job_id"),
                t("cc_printers.col_document"),
                t("cc_printers.col_printer"),
                t("cc_printers.col_size"),
                t("cc_printers.col_state"),
                t("cc_printers.col_actions"),
            ],
            widths=[8, "flex", 17, 7, 12, 27],
            rows=rows,
            aligns=["left", "left", "left", "right", "left", "right"],
        )

    cards.append(q_box)

    sel_job = next((j for j in jobs if j["id"] == sel_job_id), None)
    if sel_job:
        jid = sel_job["id"]
        is_active_job = (q_mode == "not-completed")
        auth_needed = is_active_job and (
            "auth-info-required" in sel_job["reasons"].lower()
            or "authentication" in sel_job["reasons"].lower()
        )
        jb_acts = []
        if auth_needed:
            jb_acts.append(ActionBtn(t("cc_printers.auth_job_btn"), f"cc_prn:job_auth:{jid}", tone="apply"))
        if is_active_job:
            jb_acts.append(ActionBtn(t("common.cancel"), f"cc_prn:job_cancel:{jid}", tone="danger_soft"))
        else:
            jb_acts.append(ActionBtn(t("cc_printers.reprint_btn"), f"cc_prn:job_reprint:{jid}", tone="apply"))

        jb_box = Box(
            t("cc_printers.job_inspector_title", p0=jid),
            icon="\uf15c",
            actions=jb_acts,
            highlighted=False,
        )
        jb_box.add_kv(t("cc_printers.kv_job_document"), Txt(canvas.truncate_plain(sel_job["title"], 64), role="val"))

        meta_parts = [
            Txt(sel_job["user"], role="val"),
            Txt(format_bytes_short(sel_job["size"]), role="val"),
            Txt(f"{sel_job['copies']}x", role="val"),
        ]
        if sel_job["pages"] > 0:
            meta_parts.append(Txt(t("cc_printers.pages_done_n", p0=sel_job["pages"]), role="val"))
        meta_parts.append(Txt(sel_job["submitted"], role="schema"))
        jb_box.add_kv(t("cc_printers.kv_job_meta"), "  ·  ".join(meta_parts))

        if sel_job["reasons"]:
            auth_b = ActionBtn(t("cc_printers.auth_job_btn"), f"cc_prn:job_auth:{jid}", tone="apply") if auth_needed else ""
            jb_box.add_kv(t("cc_printers.kv_ipp_reasons"), Txt(sel_job["reasons"], role="attention"), right_markup=auth_b)

        if is_active_job:
            cur_pb = _prio_bucket(int(sel_job.get("priority", 50)))
            prio_pills = " ".join(
                SelectPill(lbl, f"cc_prn:job_prio:{jid}:{pv}", selected=(cur_pb == pv))
                for pv, lbl in (
                    ("25", t("cc_printers.prio_low")),
                    ("50", t("cc_printers.prio_normal")),
                    ("75", t("cc_printers.prio_high")),
                    ("95", t("cc_printers.prio_urgent")),
                )
            )
            jb_box.add_kv(t("cc_printers.kv_job_priority"), prio_pills)

            other_prns = [p["name"] for p in printers if p["name"] != sel_job["printer"]]
            if other_prns:
                mv_btns = " ".join(
                    ActionBtn(f"\u2192 {pn}", f"cc_prn:job_move:{jid}:{pn}", tone="neutral")
                    for pn in other_prns[:5]
                )
                jb_box.add_kv(t("cc_printers.kv_move_printer"), mv_btns)

        cards.append(jb_box)

    return cards


def build_cards(cc_panel, canvas: Canvas) -> list:
    return _build_queue_standard(cc_panel, canvas)
