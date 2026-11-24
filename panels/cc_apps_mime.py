#!/usr/bin/env python3
import os
from collections import Counter
from core import probe
from core.completion import catalog_desktop_apps, rank_candidates
from core.config import CONFIG
from core.i18n import t
from core.state import STATE
from ui.canvas import Canvas
from ui.kit import ActionBtn, Box, CyclePill, InlineField, InlineFilter, InspectorCell, ProgressBar, SelectPill, Txt, get_active_inline_entry
from panels.cc_engine_apps import (
    CUSTOM_EXT_MIME_PREFIX,
    DEFAULT_CATEGORIES,
    USER_MIMEAPPS,
    _feedback_cards,
    app_display_name,
    load_mime_globs,
    mime_display_ext,
    parse_extension_spec,
    read_xdg_mime_state,
)


def _unified_kv_width() -> int:
    return max(22, len(t("cc_apps.kv_pkg_manager")) + 1)


def _render_other_picker(box: Box, target: str, mime_hint: str, pick_q: str, canvas: Canvas, kv_w: int) -> None:
    is_cat = target.startswith("cat|")
    tgt_id = target.split("|", 1)[1] if "|" in target else target
    uri_prefix = f"cc_apps:cat_apply:{tgt_id}:" if is_cat else f"cc_apps:mime_set:{tgt_id}:"
    ranked = rank_candidates(catalog_desktop_apps(mime_hint=mime_hint), pick_q, limit=6)
    res_pills = [(canvas.truncate_plain(lb, 22), f"{uri_prefix}{val}") for val, lb in ranked]
    box.add_inline_picker(
        t("cc_apps.choose_app_kv"),
        query=pick_q,
        placeholder=t("cc_apps.choose_app_ph"),
        trigger_uri=f"cc_apps:pick_other:{target}",
        results=res_pills,
        clear_uri="cc_apps:pick_other_clear" if pick_q else "",
        active=(get_active_inline_entry() == "apps_pick_other"),
        max_pills_per_row=2,
        width=28,
        entry_id="apps_pick_other",
        right_markup=Txt(mime_hint if is_cat else tgt_id, role="schema"),
        show_empty=True,
    )


def _build_by_category(cc_panel, canvas: Canvas, st: dict, mime_to_exts: dict, kv_w: int) -> list[Box]:
    resolve = st["resolve"]
    apps_by_id = st["apps_by_id"]
    handlers_by_mime = st["handlers_by_mime"]

    sel_cid = str(STATE.get("apps", "mime_cat_sel", "") or "")
    cat_ids = [cid for cid, _ic, _m in DEFAULT_CATEGORIES]
    if sel_cid not in cat_ids:
        sel_cid = cat_ids[0]

    other_open = str(STATE.get("apps", "mime_other_open", "") or "")
    other_q = str(STATE.get("apps", "mime_other_query", "") or "").strip()

    cat_box = Box(t("cc_apps.categories_card_hdr", p0=len(DEFAULT_CATEGORIES)), icon="\uf03a", highlighted=False)
    cat_box.add_row(" ")
    cat_rows = []
    sel_info = None
    for cid, ic, mimes in DEFAULT_CATEGORIES:
        cat_lbl = t(f"cc_apps.cat_{cid}")
        resolved = [(m, *resolve(m)) for m in mimes]
        assigned = [(m, did, src) for m, did, src in resolved if did]
        counts = Counter(did for _, did, _ in assigned)
        if counts:
            top_did, top_cnt = counts.most_common(1)[0]
            top_name = app_display_name(top_did, apps_by_id)
            uniform = (top_cnt == len(mimes))
            cov_txt = Txt(f"{top_cnt}/{len(mimes)}", role="ok" if uniform else "warn")
            h_cell = (
                Txt(canvas.truncate_plain(top_name, 22), role="val")
                if uniform
                else f"{Txt(canvas.truncate_plain(top_name, 14), role='val')} {Txt(t('cc_apps.mixed_short'), role='warn')}"
            )
        else:
            cov_txt = Txt(f"0/{len(mimes)}", role="inactive")
            h_cell = Txt(t("common.unset"), role="inactive")

        ex_preview = "  ".join(mime_display_ext(m, mime_to_exts).split(",")[0].strip() for m in mimes[:3])
        is_sel = (cid == sel_cid)
        if is_sel:
            sel_info = (cid, cat_lbl, ic, mimes, resolved, counts)
        cat_rows.append([
            InspectorCell(canvas.truncate_plain(cat_lbl, 18), f"cc_apps:cat_sel:{cid}", selected=is_sel),
            h_cell,
            cov_txt,
            Txt(canvas.truncate_plain(ex_preview, max(30, canvas.inner_cols - 60)), role="schema"),
        ])
    cat_box.add_table(
        headers=[t("cc_apps.col_category"), t("cc_apps.col_handler"), t("cc_apps.col_coverage"), t("cc_apps.col_examples")],
        widths=[21, 22, 9, "flex"],
        rows=cat_rows,
        aligns=["left", "left", "left", "left"],
    )
    cat_box.add_row(" ")

    # Category Inspector Card
    cid, cat_lbl, ic, mimes, resolved, counts = sel_info
    has_override = any(src == "user" for _, _, src in resolved)
    insp_acts = [ActionBtn(t("cc_apps.reset_cat_btn"), f"cc_apps:cat_reset:{cid}", tone="warn")] if has_override else []
    insp_box = Box(t("cc_apps.cat_inspector_hdr", p0=cat_lbl, p1=len(mimes)), icon=ic, actions=insp_acts, highlighted=False)
    insp_box.add_row(" ")

    cand_counts = Counter()
    for m in mimes:
        for did in handlers_by_mime.get(m, []):
            cand_counts[did] += 1
    top_cands = [did for did, _cnt in cand_counts.most_common(4)]
    uniform_did = counts.most_common(1)[0][0] if (counts and counts.most_common(1)[0][1] == len(mimes)) else ""

    pills = [
        SelectPill(
            canvas.truncate_plain(app_display_name(did, apps_by_id), 14),
            f"cc_apps:cat_apply:{cid}:{did}",
            selected=(did == uniform_did),
        )
        for did in top_cands
    ]
    pills_str = " ".join(pills)
    insp_box.add_kv(
        t("cc_apps.apply_all_kv"),
        pills_str or Txt(t("common.unset"), role="inactive"),
        key_width=kv_w,
        right_markup=ActionBtn(t("cc_apps.other_app_btn"), f"cc_apps:pick_other:cat|{cid}", tone="neutral"),
    )
    if other_open == f"cat|{cid}":
        _render_other_picker(insp_box, other_open, mimes[0], other_q, canvas, kv_w)

    insp_box.add_row(" ")
    mime_rows = []
    open_mime_for_cat = ""
    for m, did, src in resolved:
        ext_s = mime_display_ext(m, mime_to_exts)
        if did:
            h_disp = app_display_name(did, apps_by_id)
            h_cell = Txt(canvas.truncate_plain(h_disp, 24), role="ok" if src == "user" else "val")
        else:
            h_cell = Txt(t("common.unset"), role="inactive")
        rst_b = ActionBtn(t("cc_apps.reset_short_btn"), f"cc_apps:mime_reset:{m}", tone="warn") if src == "user" else ""
        chg_b = ActionBtn(t("cc_apps.change_btn"), f"cc_apps:pick_other:mime|{m}", tone="neutral")
        acts_s = f"{rst_b} {chg_b}".strip()
        if other_open == f"mime|{m}":
            open_mime_for_cat = m
        mime_rows.append([
            Txt(canvas.truncate_plain(ext_s, 13), role="focus"),
            Txt(canvas.truncate_plain(m, 28), role="schema"),
            h_cell,
            acts_s,
        ])
    insp_box.add_table(
        headers=[t("cc_apps.col_ext"), t("cc_apps.col_mime"), t("cc_apps.col_handler"), t("cc_apps.actions")],
        widths=[13, 28, "flex", 19],
        rows=mime_rows,
        aligns=["left", "left", "left", "right"],
    )
    if open_mime_for_cat:
        insp_box.add_row(" ")
        _render_other_picker(insp_box, other_open, open_mime_for_cat, other_q, canvas, kv_w)
    insp_box.add_row(" ")

    # Dedicated NC-UI Tools Card (strictly installed binaries only)
    tools_box = Box(t("cc_apps.ncui_tools_title"), icon="\uf120", highlighted=False)
    tools_box.add_row(" ")
    cur_term = str(CONFIG.get("general", {}).get("terminal", "auto"))
    avail_terms = probe.list_installed_terminals()
    if cur_term not in ("", "auto") and cur_term not in avail_terms and probe.has_bin(cur_term):
        avail_terms.insert(0, cur_term)
    term_pills = (
        " ".join(SelectPill(c, f"cc_apps:set_term:{c}", selected=(cur_term == c)) for c in avail_terms)
        if avail_terms
        else Txt(t("common.none"), role="inactive")
    )
    tools_box.add_kv(t("cc_apps.kv_terminal"), term_pills, key_width=kv_w)

    cur_pm = probe.get_preferred_pkg_manager() or "auto"
    avail_pms = probe.list_installed_pkg_managers()
    pm_pills = (
        " ".join(SelectPill(c, f"cc_apps:set_pkg_mgr:{c}", selected=(cur_pm == c)) for c in avail_pms)
        if avail_pms
        else Txt(t("common.none"), role="inactive")
    )
    tools_box.add_kv(t("cc_apps.kv_pkg_manager"), pm_pills, key_width=kv_w)
    tools_box.add_row(" ")

    return [cat_box, insp_box, tools_box]


def _build_by_app(cc_panel, canvas: Canvas, st: dict, mime_to_exts: dict, kv_w: int) -> list[Box]:
    resolve = st["resolve"]
    apps_by_id = st["apps_by_id"]
    user_defaults = st["user_defaults"]

    candidate_ids = {ent["id"] for ent in st["apps_with_mimes"]} | {did for did in user_defaults.values() if did in apps_by_id}
    app_flt = str(STATE.get("apps", "mime_app_flt", "") or "").strip()
    ql = app_flt.lower()

    enriched = []
    for did in candidate_ids:
        ent = apps_by_id[did]
        extra_user = [m for m, d in user_defaults.items() if d == did]
        merged_mimes = list(dict.fromkeys(list(ent.get("mimes_list") or []) + extra_user))
        if not merged_mimes:
            continue
        if ql:
            blob = f"{ent['name']} {did} {' '.join(merged_mimes)}".lower()
            ext_hits = any(ql in mime_display_ext(m, mime_to_exts).lower() for m in merged_mimes[:30])
            if ql not in blob and not ext_hits:
                continue
        owned = 0
        user_owned = 0
        for m in merged_mimes:
            cur_did, src = resolve(m)
            if cur_did == did:
                owned += 1
                if src == "user":
                    user_owned += 1
        enriched.append((ent, merged_mimes, owned, len(merged_mimes), user_owned))

    enriched.sort(key=lambda x: (x[0]["name"].lower(), x[0]["id"].lower()))
    sel_id = str(STATE.get("apps", "mime_app_sel", "") or "")
    sel_item = next((item for item in enriched if item[0]["id"] == sel_id), enriched[0] if enriched else None)

    app_box = Box(t("cc_apps.apps_browser_hdr", p0=len(enriched)), icon="\uf00a", highlighted=False)
    app_box.add_row(" ")
    app_box.add_kv(
        t("cc_apps.search_app_kv"),
        InlineFilter(
            "apps_app_flt",
            app_flt,
            t("cc_apps.search_app_ph"),
            "cc_apps:app_flt_open",
            clear_uri="cc_apps:app_flt_clear" if app_flt else "",
            width=34,
        ),
        key_width=kv_w,
    )
    app_box.add_row(" ")

    if not enriched:
        app_box.add_row(f"  {Txt(t('cc_apps.no_matching_apps'), role='inactive')}")
        app_box.add_row(" ")
        return [app_box]

    app_rows = []
    for ent, _ml, owned, total, _uo in enriched[:16]:
        did = ent["id"]
        pct = (owned * 100.0 / total) if total else 0.0
        is_sel = (did == sel_item[0]["id"])
        app_rows.append([
            InspectorCell(canvas.truncate_plain(ent["name"], 23), f"cc_apps:app_sel:{did}", selected=is_sel),
            ProgressBar(pct, width=12, color_by_threshold=False),
            Txt(f"{owned}/{total}", role="ok" if owned == total else ("val" if owned > 0 else "inactive")),
            Txt(canvas.truncate_plain(did, max(32, canvas.inner_cols - 60)), role="schema"),
        ])
    app_box.add_table(
        headers=[t("cc_apps.col_application"), t("cc_apps.col_defaults_bar"), t("cc_apps.col_count"), t("cc_apps.col_desktop_id")],
        widths=[26, 15, 9, "flex"],
        rows=app_rows,
        aligns=["left", "left", "right", "left"],
    )
    app_box.add_row(" ")

    # App Associations Inspector
    ent, merged_mimes, owned, total, user_owned = sel_item
    did = ent["id"]
    insp_acts = [ActionBtn(t("cc_apps.add_ext_btn"), f"cc_apps:add_ext_open:app|{did}", tone="apply")]
    if owned < total:
        insp_acts.append(ActionBtn(t("cc_apps.set_all_app_btn", p0=total), f"cc_apps:app_set_all:{did}", tone="apply"))
    if user_owned > 0:
        insp_acts.append(ActionBtn(t("cc_apps.clear_app_btn"), f"cc_apps:app_clear:{did}", tone="warn"))

    insp_box = Box(t("cc_apps.app_inspector_hdr", p0=ent["name"], p1=owned, p2=total), icon="\uf02c", actions=insp_acts, highlighted=False)
    insp_box.add_row(" ")
    add_ext_tgt = str(STATE.get("apps", "mime_add_ext_target", "") or "")
    if add_ext_tgt == f"app|{did}":
        insp_box.add_kv(
            t("cc_apps.add_ext_kv"),
            InlineField(
                "apps_add_ext",
                f"cc_apps:add_ext_open:app|{did}",
                value="",
                empty_label=t("cc_apps.add_ext_ph"),
                placeholder=t("cc_apps.add_ext_ph"),
                width=34,
                tone="neutral",
            ),
            key_width=kv_w,
        )

    subflt = str(STATE.get("apps", "mime_app_subflt", "all") or "all")
    other_cnt = total - owned
    insp_box.add_kv(
        t("cc_apps.app_filter_kv"),
        f"{SelectPill(t('cc_apps.flt_all', p0=total), 'cc_apps:app_subflt:all', selected=(subflt == 'all'))} "
        f"{SelectPill(t('cc_apps.flt_owned', p0=owned), 'cc_apps:app_subflt:owned', selected=(subflt == 'owned'))} "
        f"{SelectPill(t('cc_apps.flt_other', p0=other_cnt), 'cc_apps:app_subflt:other', selected=(subflt == 'other'))}",
        key_width=kv_w,
    )
    insp_box.add_row(" ")

    other_open = str(STATE.get("apps", "mime_other_open", "") or "")
    other_q = str(STATE.get("apps", "mime_other_query", "") or "").strip()
    rows = []
    open_mime = ""
    for m in merged_mimes:
        cur_did, _src = resolve(m)
        is_owned = (cur_did == did)
        if subflt == "owned" and not is_owned:
            continue
        if subflt == "other" and is_owned:
            continue
        if len(rows) >= 20:
            break
        ext_s = mime_display_ext(m, mime_to_exts)
        if cur_did:
            h_disp = app_display_name(cur_did, apps_by_id)
            h_cell = Txt(canvas.truncate_plain(h_disp, 22), role="ok" if is_owned else "schema")
        else:
            h_cell = Txt(t("common.unset"), role="inactive")

        if m.startswith(CUSTOM_EXT_MIME_PREFIX):
            left_act = ActionBtn("x", f"cc_apps:mime_reset:{m}", tone="danger_soft")
        elif is_owned and m in user_defaults:
            left_act = ActionBtn(t("cc_apps.reset_short_btn"), f"cc_apps:mime_reset:{m}", tone="warn")
        elif not is_owned:
            left_act = ActionBtn(t("cc_apps.set_btn"), f"cc_apps:mime_set:{m}:{did}", tone="apply")
        else:
            left_act = ""
        chg_b = ActionBtn(t("cc_apps.change_btn"), f"cc_apps:pick_other:mime|{m}", tone="neutral")
        if other_open == f"mime|{m}":
            open_mime = m
        rows.append([
            Txt(canvas.truncate_plain(ext_s, 13), role="focus"),
            Txt(canvas.truncate_plain(m, 27), role="schema"),
            h_cell,
            f"{left_act} {chg_b}".strip(),
        ])

    if rows:
        insp_box.add_table(
            headers=[t("cc_apps.col_ext"), t("cc_apps.col_mime"), t("cc_apps.col_handler"), t("cc_apps.actions")],
            widths=[13, 27, "flex", 20],
            rows=rows,
            aligns=["left", "left", "left", "right"],
        )
    else:
        insp_box.add_row(f"  {Txt(t('cc_apps.no_matching_types'), role='inactive')}")
    if open_mime:
        insp_box.add_row(" ")
        _render_other_picker(insp_box, other_open, open_mime, other_q, canvas, kv_w)
    insp_box.add_row(" ")
    return [app_box, insp_box]


def _build_by_type(cc_panel, canvas: Canvas, st: dict, mime_to_exts: dict, kv_w: int) -> list[Box]:
    resolve = st["resolve"]
    apps_by_id = st["apps_by_id"]
    handlers_by_mime = st["handlers_by_mime"]
    user_defaults = st["user_defaults"]

    qfind = str(STATE.get("apps", "mime_qfind", "") or "").strip()
    kind = str(STATE.get("apps", "mime_type_kind", "ALL") or "ALL").upper()
    ql = qfind.lower()

    mime_set: set[str] = set(user_defaults.keys()) | set((st.get("custom_mimes") or {}).keys())
    for _cid, _ic, mimes in DEFAULT_CATEGORIES:
        mime_set.update(mimes)
    mime_set.update(handlers_by_mime.keys())
    if ql:
        for m, exts in mime_to_exts.items():
            if ql in m or any(ql in ex for ex in exts):
                mime_set.add(m)

    filtered = []
    for m in sorted(mime_set):
        is_link = m.startswith("x-scheme-handler/")
        cur_did, src = resolve(m)
        if kind == "FILES" and is_link:
            continue
        if kind == "LINKS" and not is_link:
            continue
        if kind == "OVERRIDES" and src != "user":
            continue
        ext_s = mime_display_ext(m, mime_to_exts)
        h_name = app_display_name(cur_did, apps_by_id) if cur_did else ""
        if ql:
            all_exts = " ".join(mime_to_exts.get(m, []))
            blob = f"{m} {ext_s} {all_exts} {cur_did} {h_name}".lower()
            if not all(tok in blob for tok in ql.split()):
                continue
        filtered.append((m, ext_s, cur_did, h_name, src))

    kind_lbl = t(f"cc_apps.kind_{kind.lower()}" if kind != "OVERRIDES" else "cc_apps.kind_overrides")
    tbox = Box(
        t("cc_apps.types_browser_hdr", p0=len(filtered), p1=len(mime_set)),
        icon="\uf15b",
        actions=[CyclePill(kind_lbl, "cc_apps:type_kind_cycle", active=(kind != "ALL"))],
        highlighted=False,
    )
    tbox.add_row(" ")

    spec_kind, spec_disp, spec_mime = parse_extension_spec(qfind) if ql else ("", "", "")
    exact_spec = bool(
        spec_kind
        and any(
            spec_mime == m or spec_disp == mime_display_ext(m, mime_to_exts) or spec_disp in mime_to_exts.get(m, [])
            for m, *_ in filtered
        )
    )
    if spec_kind and not exact_spec:
        tbox.add_row(
            f"  {Txt(t('cc_apps.no_ext_yet', p0=spec_disp), role='schema')}  "
            f"{ActionBtn(t('cc_apps.register_ext_btn', p0=spec_disp), f'cc_apps:register_ext:{qfind}', tone='apply')}"
        )
        if filtered:
            tbox.add_row(" ")

    if not filtered:
        if not spec_kind:
            tbox.add_row(f"  {Txt(t('cc_apps.no_matching_types'), role='inactive')}")
        tbox.add_row(" ")
        return [tbox]

    sel_mime = str(STATE.get("apps", "mime_type_sel", "") or "")
    sel_item = next((it for it in filtered if it[0] == sel_mime), filtered[0])

    src_labels = {
        "user": t("cc_apps.src_user"),
        "system": t("cc_apps.src_system"),
        "unset": t("cc_apps.src_unset"),
        "custom": t("cc_apps.src_custom"),
    }
    trows = []
    for m, ext_s, cur_did, h_name, src in filtered[:18]:
        is_sel = (m == sel_item[0])
        is_custom = m.startswith(CUSTOM_EXT_MIME_PREFIX)
        src_key = "custom" if is_custom else src
        src_role = ("ok" if cur_did else "warn") if is_custom else ("ok" if src == "user" else ("schema" if src == "system" else "inactive"))
        trows.append([
            InspectorCell(canvas.truncate_plain(ext_s, 11), f"cc_apps:type_sel:{m}", selected=is_sel),
            Txt(canvas.truncate_plain(m, 30), role="schema"),
            Txt(canvas.truncate_plain(h_name or t("common.unset"), 24), role="val" if cur_did else "inactive"),
            Txt(src_labels.get(src_key, src_key), role=src_role),
        ])

    tbox.add_table(
        headers=[t("cc_apps.col_ext"), t("cc_apps.col_mime"), t("cc_apps.col_handler"), t("cc_apps.col_source")],
        widths=[16, 30, "flex", 11],
        rows=trows,
        aligns=["left", "left", "left", "right"],
    )
    tbox.add_row(" ")

    # Handler Chooser Card
    sm, sext, sd_id, sd_name, ssrc = sel_item
    c_acts = []
    if sm.startswith(CUSTOM_EXT_MIME_PREFIX):
        c_acts.append(ActionBtn(t("cc_apps.remove_ext_btn"), f"cc_apps:mime_reset:{sm}", tone="danger_soft"))
    elif ssrc == "user":
        c_acts.append(ActionBtn(t("cc_apps.reset_system_btn"), f"cc_apps:mime_reset:{sm}", tone="warn"))
    cbox = Box(t("cc_apps.type_inspector_hdr", p0=sext), icon="\uf085", actions=c_acts, highlighted=False)
    cbox.add_row(" ")

    exts_full = ", ".join(mime_to_exts.get(sm, [])[:6]) or sext
    cbox.add_kv(t("cc_apps.kv_mime_exts"), f"{Txt(sm, role='val')}  {Txt(exts_full, role='schema')}", key_width=kv_w)
    cur_val_m = (
        f"{Txt(sd_name, role='focus')}  {Txt(f'({sd_id}) \u00b7 {src_labels.get(ssrc, ssrc)}', role='schema')}"
        if sd_id
        else Txt(t("common.unset"), role="inactive")
    )
    cbox.add_kv(t("cc_apps.kv_cur_handler"), cur_val_m, key_width=kv_w)

    rec_ids = list(handlers_by_mime.get(sm, []))[:4]
    if sd_id and sd_id not in rec_ids:
        rec_ids.insert(0, sd_id)
        rec_ids = rec_ids[:4]
    rec_pills = " ".join(
        SelectPill(canvas.truncate_plain(app_display_name(did, apps_by_id), 14), f"cc_apps:mime_set:{sm}:{did}", selected=(did == sd_id))
        for did in rec_ids
    )
    cbox.add_kv(
        t("cc_apps.kv_recommended"),
        rec_pills or Txt(t("common.none"), role="inactive"),
        key_width=kv_w,
        right_markup=ActionBtn(t("cc_apps.other_app_btn"), f"cc_apps:pick_other:mime|{sm}", tone="neutral"),
    )
    other_open = str(STATE.get("apps", "mime_other_open", "") or "")
    other_q = str(STATE.get("apps", "mime_other_query", "") or "").strip()
    if other_open == f"mime|{sm}":
        cbox.add_row(" ")
        _render_other_picker(cbox, other_open, sm, other_q, canvas, kv_w)
    cbox.add_row(" ")
    return [tbox, cbox]


def _build_mime_standard(cc_panel, canvas: Canvas) -> list:
    st = read_xdg_mime_state()
    mime_to_exts, _ext_to_mime = load_mime_globs()
    cards = _feedback_cards()
    kv_w = _unified_kv_width()

    view = str(STATE.get("apps", "mime_view", "category") or "category")
    if view not in ("category", "app", "type"):
        view = "category"
    qfind = str(STATE.get("apps", "mime_qfind", "") or "").strip()
    if qfind and view != "type":
        view = "type"
    override_cnt = st["override_count"]

    title = t("cc_apps.default_apps_title_cnt", p0=override_cnt) if override_cnt > 0 else t("cc_apps.default_apps_title")
    cmd_acts = []
    if override_cnt > 0:
        cmd_acts.append(ActionBtn(t("cc_apps.reset_all_btn"), "cc_apps:reset_all", tone="warn"))
    cmd_acts.append(ActionBtn(t("cc_apps.refresh"), "cc_apps:refresh", tone="neutral"))

    cmd_box = Box(title, icon="\uf1b2", actions=cmd_acts, highlighted=False)
    cmd_box.add_row(" ")

    view_pills = (
        f"{SelectPill(t('cc_apps.view_category'), 'cc_apps:view:category', selected=(view == 'category'))} "
        f"{SelectPill(t('cc_apps.view_app'), 'cc_apps:view:app', selected=(view == 'app'))} "
        f"{SelectPill(t('cc_apps.view_type'), 'cc_apps:view:type', selected=(view == 'type'))}"
    )
    qfind_w = max(36, len(Canvas.strip_tags(view_pills)) - 2)
    cmd_box.add_kv(t("cc_apps.view_mode"), view_pills, key_width=kv_w)
    cmd_box.add_kv(
        t("cc_apps.quick_find"),
        InlineFilter(
            "apps_qfind",
            qfind,
            t("cc_apps.quick_find_ph"),
            "cc_apps:qfind_open",
            clear_uri="cc_apps:qfind_clear" if qfind else "",
            width=qfind_w,
        ),
        key_width=kv_w,
        right_markup=ActionBtn(t("cc_apps.add_ext_btn"), "cc_apps:add_ext_open:type|", tone="apply"),
    )
    if str(STATE.get("apps", "mime_add_ext_target", "") or "") == "type|":
        cmd_box.add_kv(
            t("cc_apps.add_ext_kv"),
            InlineField(
                "apps_add_ext",
                "cc_apps:add_ext_open:type|",
                value="",
                empty_label=t("cc_apps.add_ext_ph"),
                placeholder=t("cc_apps.add_ext_ph"),
                width=qfind_w,
                tone="neutral",
            ),
            key_width=kv_w,
        )
    cmd_box.add_row(" ")
    cards.append(cmd_box)

    if view == "category":
        cards.extend(_build_by_category(cc_panel, canvas, st, mime_to_exts, kv_w))
    elif view == "app":
        cards.extend(_build_by_app(cc_panel, canvas, st, mime_to_exts, kv_w))
    else:
        cards.extend(_build_by_type(cc_panel, canvas, st, mime_to_exts, kv_w))

    cfg_box = Box(t("cc_apps.mimeapps_card_title"), icon="\uf15c", highlighted=False)
    cfg_box.add_row(" ")
    custom_mimes = st.get("custom_mimes") or {}
    if custom_mimes:
        rm_btns = " ".join(
            ActionBtn(f"\u2715 {ext}", f"cc_apps:mime_reset:{m}", tone="danger_soft")
            for m, ext in sorted(custom_mimes.items(), key=lambda kv: kv[1])
        )
        cfg_box.add_kv(t("cc_apps.custom_exts_kv"), rm_btns, key_width=kv_w)

    def _build_mimeapps_preview(bx: Box) -> None:
        if not os.path.exists(USER_MIMEAPPS):
            bx.add_row(f"  {Txt(t('cc_apps.mimeapps_missing'), role='inactive')}")
            return
        try:
            lines = open(USER_MIMEAPPS, "r", encoding="utf-8", errors="ignore").read().splitlines()
        except OSError:
            lines = []
        for ln in lines[:40]:
            bx.add_row(f"  {Txt(canvas.truncate_plain(ln, canvas.inner_cols - 6), role='schema')}")

    cfg_box.add_collapsible(
        cc_panel,
        "apps_mimeapps_preview",
        t("cc_apps.mimeapps_preview_title"),
        _build_mimeapps_preview,
        summary=USER_MIMEAPPS,
    )
    cfg_box.add_row(" ")
    cards.append(cfg_box)
    return cards


def build_cards(cc_panel, canvas: Canvas) -> list:
    return _build_mime_standard(cc_panel, canvas)
