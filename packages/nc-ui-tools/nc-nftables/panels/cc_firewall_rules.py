#!/usr/bin/env python3
from core import probe
from core.i18n import t
from core.state import STATE
from ui.canvas import Canvas
from ui.kit import ActionBtn, Badge, Box, CyclePill, InlineField, InlineFilter, InspectorCell, SelectPill, StateBadge, Txt, feedback_cards
from panels.cc_engine_firewall import (
    _load_fw_store,
    _service_active,
    _fw_dict,
    _dir_short,
    _verdict_style,
    _rule_to_nft_line,
)


def _filter_rules(rules: list[dict], flt: dict) -> list[int]:
    out = []
    for idx, r in enumerate(rules):
        d_s = _dir_short(r.get("chain", "input"))
        p_s = str(r.get("proto", "any")).upper()
        s_s = str(r.get("src", "") or r.get("iface", "") or "any")
        pt_s = str(r.get("dport", "") or r.get("dst", "") or "any")
        v_s = str(r.get("verdict", "accept")).upper()
        c_s = f"{r.get('ct', '')} {r.get('limit', '')} {r.get('comment', '')}"
        if flt["dir"] != "ALL" and d_s != flt["dir"]:
            continue
        if flt["proto"] != "ALL" and p_s != flt["proto"]:
            continue
        if flt["src"] and flt["src"].lower() not in s_s.lower():
            continue
        if flt["port"] and flt["port"].lower() not in pt_s.lower():
            continue
        if flt["verdict"] != "ALL" and v_s != flt["verdict"]:
            continue
        if flt["text"] and flt["text"].lower() not in c_s.lower():
            continue
        out.append(idx)
    return out


def _build_composer_box(draft: dict, edit_idx: int | None) -> Box:
    is_edit = edit_idx is not None
    save_btn = ActionBtn(
        t("cc_firewall.upd") if is_edit else t("cc_firewall.save_rule_btn"),
        "cc_fw:add_rule",
        tone="apply",
    )
    cancel_btn = ActionBtn(t("common.cancel"), "cc_fw:draft_cancel", tone="neutral")
    title = t("cc_firewall.edit_rule_hdr", p0=edit_idx + 1) if is_edit else t("cc_firewall.new_rule_hdr")
    cbox = Box(title, icon="\uf044", actions=[save_btn, cancel_btn], highlighted=False)
    cbox.add_row(" ")

    cbox.add_kv(
        t("cc_firewall.chain_proto_verdict"),
        f"{CyclePill(_dir_short(draft['chain']), 'cc_fw:draft_cycle:chain', active=True, dirty=is_edit)}  "
        f"{CyclePill(draft['proto'].upper(), 'cc_fw:draft_cycle:proto', active=True, dirty=is_edit)}  "
        f"{CyclePill(draft['verdict'].upper(), 'cc_fw:draft_cycle:verdict', active=True, dirty=is_edit)}",
    )
    cbox.add_kv(
        t("cc_firewall.source_port"),
        f"{InlineField('fw_draft_src', 'cc_fw:draft_inline:src', value=draft['src'], empty_label=t('cc_firewall.src_any'), placeholder=t('cc_firewall.cidr_ph'), width=16, tone='neutral', selected=False, dirty=bool(draft['src'] and is_edit))}  "
        f"{InlineField('fw_draft_dport', 'cc_fw:draft_inline:dport', value=draft['dport'], empty_label=t('cc_firewall.port_any'), placeholder='22,80,443', width=13, tone='neutral', selected=False, dirty=bool(draft['dport'] and is_edit))}  "
        f"{InlineField('fw_draft_iface', 'cc_fw:draft_inline:iface', value=draft['iface'], empty_label=t('cc_firewall.if_any'), placeholder=t('cc_firewall.iface_ph'), width=12, tone='neutral', selected=False, dirty=bool(draft['iface'] and is_edit))}",
    )
    ct_s = t("cc_firewall.ct_val", p0=draft["ct"]) if draft["ct"] else t("cc_firewall.ct_any")
    cbox.add_kv(
        t("cc_firewall.extras_ct_limit"),
        f"{CyclePill(ct_s, 'cc_fw:draft_cycle:ct', active=bool(draft['ct']), dirty=bool(draft['ct'] and is_edit))}  "
        f"{InlineField('fw_draft_limit', 'cc_fw:draft_inline:limit', value=draft['limit'], empty_label=t('cc_firewall.limit_none'), placeholder='10/minute', width=14, tone='neutral', selected=False, dirty=bool(draft['limit'] and is_edit))}",
    )
    cbox.add_kv(
        t("cc_firewall.comment_lbl"),
        InlineField(
            "fw_draft_comment",
            "cc_fw:draft_inline:comment",
            value=draft["comment"],
            empty_label=t("cc_firewall.comment_ph"),
            placeholder=t("cc_firewall.comment_ph"),
            width=36,
            tone="neutral",
            selected=False,
            dirty=bool(draft["comment"] and is_edit),
        ),
    )
    cbox.add_kv("nftables", Txt(_rule_to_nft_line(draft) or "-", role="schema"))
    cbox.add_row(" ")
    return cbox


def _build_rule_inspector(rules: list[dict], sel_idx: int) -> Box:
    r = rules[sel_idx]
    en = bool(r.get("enabled", True))
    verd_s = str(r.get("verdict", "accept")).upper()

    acts = [
        SelectPill(t("common.on") if en else t("common.off"), f"cc_fw:toggle:{sel_idx}", selected=en),
        ActionBtn(t("cc_firewall.edit_btn"), f"cc_fw:edit:{sel_idx}", tone="neutral"),
    ]
    if sel_idx > 0:
        acts.append(ActionBtn("\u2191", f"cc_fw:move:{sel_idx}:-1", tone="neutral"))
    if sel_idx < len(rules) - 1:
        acts.append(ActionBtn("\u2193", f"cc_fw:move:{sel_idx}:1", tone="neutral"))
    acts.append(ActionBtn(t("cc_firewall.delete_btn"), f"cc_fw:del:{sel_idx}", tone="danger_soft"))

    ibox = Box(t("cc_firewall.rule_inspector_hdr", p0=sel_idx + 1, p1=len(rules)), icon="\uf06e", actions=acts, highlighted=False)
    ibox.add_row(" ")
    v_badge = Badge(verd_s, style=_verdict_style(verd_s)) if en else Txt(verd_s, role="inactive")
    ibox.add_kv(
        t("cc_firewall.inspector_rule"),
        f"{Txt(r.get('chain', 'input').upper() + ' · ' + str(r.get('proto', 'any')).upper(), role='val' if en else 'inactive')}  "
        f"{v_badge}  {Txt(r.get('comment') or '-', role='focus' if en else 'inactive')}",
    )
    match_parts = [
        f"src:{r['src']}" if r.get("src") else "",
        f"if:{r['iface']}" if r.get("iface") else "",
        f"dport:{r['dport']}" if r.get("dport") else "",
        f"dst:{r['dst']}" if r.get("dst") else "",
        f"ct:{r['ct']}" if r.get("ct") else "",
        f"limit:{r['limit']}" if r.get("limit") else "",
    ]
    match_txt = "  ·  ".join(p for p in match_parts if p) or "any"
    ibox.add_kv(t("cc_firewall.inspector_match"), Txt(match_txt, role="val" if en else "inactive"))
    ibox.add_kv("nftables", Txt(_rule_to_nft_line(r) or "-", role="schema"))
    ibox.add_row(" ")
    return ibox


def build_cards(cc_panel, canvas: Canvas) -> list:
    store = _load_fw_store()
    pols = store.get("policies", {})
    rules = store.get("rules", [])
    has_nft = probe.has_bin("nft")
    svc_on = _service_active()
    flt = _fw_dict("filters")
    draft = _fw_dict("draft")
    edit_idx = draft.get("edit_idx")
    if not (isinstance(edit_idx, int) and 0 <= edit_idx < len(rules)):
        edit_idx = None
    composer_open = bool(STATE.get("firewall", "composer_open", False)) or (edit_idx is not None)
    cards = feedback_cards("firewall", "cc_firewall.status_title")

    hdr_acts = [StateBadge(svc_on, t("cc_firewall.active"), t("cc_firewall.stopped"), on_tone="ok" if svc_on else "warn")]
    if has_nft:
        hdr_acts.extend([
            ActionBtn(t("cc_firewall.apply"), "cc_fw:apply_live", tone="apply"),
            ActionBtn(t("cc_firewall.save_etc"), "cc_fw:save_etc", tone="neutral"),
        ])
    else:
        hdr_acts.append(Badge(t("ui.missing", p0="nft"), style="warn"))

    hdr = Box(
        t("cc_firewall.firewall_status_nftables"),
        icon="\uf3ed",
        actions=hdr_acts,
        highlighted=False,
    )
    hdr.add_row(" ")
    p_in = pols.get("input", "drop").upper()
    p_out = pols.get("output", "accept").upper()
    p_fwd = pols.get("forward", "drop").upper()
    pol_badges = (
        f"{Badge('IN: ' + p_in, style='ok' if p_in == 'ACCEPT' else 'crit')}  "
        f"{Badge('OUT: ' + p_out, style='ok' if p_out == 'ACCEPT' else 'crit')}  "
        f"{Badge('FWD: ' + p_fwd, style='ok' if p_fwd == 'ACCEPT' else 'crit')}"
    )
    en_cnt = sum(1 for r in rules if r.get("enabled", True))
    sync_btn = ActionBtn(t("cc_firewall.sync_etc"), "cc_fw:sync_etc", tone="neutral")
    right_pol = f"{ActionBtn(t('cc_firewall.dry_run'), 'cc_fw:dry_run', tone='neutral')} {sync_btn}" if has_nft else sync_btn
    hdr.add_kv(t("cc_firewall.chain_policies"), pol_badges, right_markup=right_pol)
    lan_s = str(store.get("lan_net") or "").strip() or t("cc_firewall.lan_unset")
    hdr.add_kv(
        t("cc_firewall.rules_summary"),
        Txt(t("cc_firewall.enabled_rules_lan", p0=en_cnt, p1=len(rules), p2=lan_s), role="val"),
    )
    hdr.add_row(" ")
    cards.append(hdr)

    filtered_indices = _filter_rules(rules, flt)
    has_flt = any([flt["dir"] != "ALL", flt["proto"] != "ALL", flt["src"], flt["port"], flt["verdict"] != "ALL", flt["text"]])

    sel_idx = STATE.get("firewall", "sel_rule_idx", None)
    if not (isinstance(sel_idx, int) and sel_idx in filtered_indices):
        sel_idx = filtered_indices[0] if filtered_indices else None

    tbl_acts = []
    if has_flt:
        tbl_acts.append(ActionBtn(t("cc_firewall.reset_flt"), "cc_fw:flt_clear", tone="warn"))
    if composer_open:
        tbl_acts.append(ActionBtn(t("common.cancel"), "cc_fw:draft_cancel", tone="neutral"))
    else:
        tbl_acts.append(ActionBtn(t("cc_firewall.add_rule"), "cc_fw:composer_new", tone="apply"))

    tbl_box = Box(
        t("cc_firewall.packet_filter_rules_hdr", p0=len(filtered_indices), p1=len(rules)),
        icon="\uf0b0",
        actions=tbl_acts,
        highlighted=False,
    )
    tbl_box.add_row(" ")

    filter_row = [
        CyclePill(flt["dir"], "cc_fw:flt_cycle:dir", active=(flt["dir"] != "ALL")),
        CyclePill(flt["proto"], "cc_fw:flt_cycle:proto", active=(flt["proto"] != "ALL")),
        InlineFilter("fw_flt_src", flt["src"], "*", "cc_fw:flt_inline:src", width=12, compact=True),
        InlineFilter("fw_flt_port", flt["port"], "*", "cc_fw:flt_inline:port", width=9, compact=True),
        CyclePill(flt["verdict"], "cc_fw:flt_cycle:verdict", active=(flt["verdict"] != "ALL")),
        InlineFilter("fw_flt_text", flt["text"], t("cc_firewall.filter_hint"), "cc_fw:flt_inline:text", width=18, compact=True),
    ]
    rows = [filter_row]
    comment_max = max(30, canvas.inner_cols - 62)
    for idx in filtered_indices[:18]:
        r = rules[idx]
        en = bool(r.get("enabled", True))
        chain_s = _dir_short(r.get("chain", "input"))
        proto_s = str(r.get("proto", "any")).upper()
        src_s = r.get("src") or (f"if:{r['iface']}" if r.get("iface") else "any")
        port_s = r.get("dport") or r.get("dst") or "any"
        verd_s = str(r.get("verdict", "accept")).upper()
        meta_txt = r.get("comment") or (f"ct:{r['ct']}" if r.get("ct") else "-")
        if r.get("limit"):
            meta_txt = f"{meta_txt} ({r['limit']})"
        rows.append([
            InspectorCell(chain_s, f"cc_fw:sel_rule:{idx}", selected=(idx == sel_idx), is_alive=en),
            Txt(proto_s, role="val" if en else "inactive"),
            Txt(canvas.truncate_plain(src_s, 14), role="val" if en else "inactive"),
            Txt(canvas.truncate_plain(port_s, 11), role="val" if en else "inactive"),
            Badge(verd_s, style=_verdict_style(verd_s)) if en else f"\u00a0{Txt(verd_s, role='inactive')}\u00a0",
            Txt(canvas.truncate_plain(meta_txt, comment_max), role="schema" if en else "inactive"),
        ])

    tbl_box.add_table(
        headers=[
            t("cc_firewall.dir"),
            t("cc_firewall.proto"),
            t("cc_firewall.source_if"),
            t("cc_firewall.dst_port"),
            t("cc_firewall.verdict"),
            t("cc_firewall.comment_ct"),
        ],
        widths=[7, 8, 14, 11, 10, "flex"],
        rows=rows,
        aligns=["left", "left", "left", "left", "left", "left"],
    )
    if not filtered_indices:
        tbl_box.add_row(f"  {Txt(t('cc_firewall.no_matching_rules'), role='inactive')}")
    tbl_box.add_row(" ")
    cards.append(tbl_box)

    if composer_open:
        cards.append(_build_composer_box(draft, edit_idx))

    if sel_idx is not None:
        cards.append(_build_rule_inspector(rules, sel_idx))

    return cards
