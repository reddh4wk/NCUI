#!/usr/bin/env python3
import json
import os
import re
import shlex
import tempfile
import time
from core import probe
from core.config import CONFIG_DIR
from core.i18n import t
from core.state import STATE
from ui.canvas import Canvas
from ui.kit import ActionBtn, Badge, Box, CyclePill, InlineField, InlineFilter, SelectPill, StateBadge, Txt
from panels.cc_engine_firewall import (
    _feedback_cards,
    _load_fw_store,
    _service_active,
    _get_filters,
    _get_draft,
    _dir_short,
    _verdict_style,
)


def _build_rules_tab(cc_panel, canvas: Canvas) -> list:
    store = _load_fw_store()
    pols = store.get("policies", {})
    rules = store.get("rules", [])
    svc_on = _service_active()
    flt = _get_filters()
    draft = _get_draft()
    edit_idx = draft.get("edit_idx")
    if not (isinstance(edit_idx, int) and 0 <= edit_idx < len(rules)):
        edit_idx = None
    cards = _feedback_cards()

    hdr = Box(
        t("cc_firewall.firewall_status_nftables"),
        icon="\uf3ed",
        actions=[
            StateBadge(svc_on, t("cc_firewall.active"), t("cc_firewall.stopped"), on_tone="ok" if svc_on else "warn"),
            ActionBtn(t("cc_firewall.apply"), "cc_fw:apply_live", tone="apply"),
            ActionBtn(t("cc_firewall.save_etc"), "cc_fw:save_etc", tone="neutral"),
        ],
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
    hdr.add_kv(
        t("cc_firewall.chain_policies"),
        pol_badges,
        key_width=16,
        right_markup=f"{ActionBtn(t('cc_firewall.dry_run'), 'cc_fw:dry_run', tone='neutral')} {ActionBtn(t('cc_firewall.sync_etc'), 'cc_fw:sync_etc', tone='neutral')}",
        requires=["nft"],
    )
    hdr.add_kv(
        t("cc_firewall.rules_summary"),
        Txt(t("cc_firewall.enabled_rules_lan", p0=en_cnt, p1=len(rules), p2=store.get('lan_net', '10.10.0.0/24')), role="schema"),
        key_width=16,
    )
    hdr.add_row(" ")
    cards.append(hdr)

    # Filter rules matching column-header filters
    filtered_indices = []
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
        filtered_indices.append(idx)

    has_flt = any([flt["dir"] != "ALL", flt["proto"] != "ALL", flt["src"], flt["port"], flt["verdict"] != "ALL", flt["text"]])
    tbl_box = Box(
        t("cc_firewall.multi_column_packet_filter_rule_co", p0=len(filtered_indices), p1=len(rules)),
        icon="\uf0b0",
        actions=[
            Badge(t("cc_firewall.edit", p0=edit_idx + 1), style="warn")
            if edit_idx is not None
            else Txt(t("cc_firewall.filtered_badge") if has_flt else t("cc_firewall.all_rules_badge"), role="schema")
        ],
        highlighted=False,
    )
    tbl_box.add_row(" ")

    # Row A: Top-of-column interactive Filter Inputs
    filter_row = [
        CyclePill(flt["dir"], "cc_fw:flt_cycle:dir", active=(flt["dir"] != "ALL")),
        CyclePill(flt["proto"], "cc_fw:flt_cycle:proto", active=(flt["proto"] != "ALL")),
        InlineFilter("fw_flt_src", flt["src"], "*", "cc_fw:flt_inline:src", width=10, compact=True),
        InlineFilter("fw_flt_port", flt["port"], "*", "cc_fw:flt_inline:port", width=7, compact=True),
        CyclePill(flt["verdict"], "cc_fw:flt_cycle:verdict", active=(flt["verdict"] != "ALL")),
        InlineFilter("fw_flt_text", flt["text"], t("cc_firewall.filter_hint"), "cc_fw:flt_inline:text", width=11, compact=True),
        ActionBtn(t("cc_firewall.reset_flt"), "cc_fw:flt_clear", tone="warn") if has_flt else Txt(t("cc_firewall.filter"), role="inactive"),
    ]

    # Row B: Column-aligned New / Edit Packet Rule Composer Row
    compose_acts = (
        f"{ActionBtn(t('cc_firewall.upd'), 'cc_fw:add_rule', tone='warn')} {ActionBtn(t('cc_firewall.can'), 'cc_fw:draft_cancel', tone='neutral')}"
        if edit_idx is not None
        else ActionBtn(t("cc_firewall.add_rule"), "cc_fw:add_rule", tone="apply")
    )
    compose_row = [
        CyclePill(_dir_short(draft["chain"]), "cc_fw:draft_cycle:chain", active=True, dirty=(edit_idx is not None)),
        CyclePill(draft["proto"].upper(), "cc_fw:draft_cycle:proto", active=True, dirty=(edit_idx is not None)),
        InlineField(
            "fw_draft_src",
            "cc_fw:draft_inline:src",
            value=canvas.truncate_plain(draft["src"], 8) if draft["src"] else "",
            placeholder="any/$LAN",
            empty_label="any",
            width=10,
            tone="neutral",
            selected=bool(draft["src"]),
            dirty=bool(draft["src"] and edit_idx is not None),
        ),
        InlineField(
            "fw_draft_dport",
            "cc_fw:draft_inline:dport",
            value=canvas.truncate_plain(draft["dport"], 5) if draft["dport"] else "",
            placeholder="22,80",
            empty_label="any",
            width=7,
            tone="neutral",
            selected=bool(draft["dport"]),
            dirty=bool(draft["dport"] and edit_idx is not None),
        ),
        CyclePill(draft["verdict"].upper(), "cc_fw:draft_cycle:verdict", active=True, dirty=(edit_idx is not None)),
        InlineField(
            "fw_draft_comment",
            "cc_fw:draft_inline:comment",
            value=canvas.truncate_plain(draft["comment"], 9) if draft["comment"] else "",
            placeholder="comment..",
            empty_label="comment..",
            width=11,
            tone="neutral",
            selected=bool(draft["comment"]),
            dirty=bool(draft["comment"] and edit_idx is not None),
        ),
        compose_acts,
    ]

    rows = [filter_row, compose_row]
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
        up_b = ActionBtn("\u2191", f"cc_fw:move:{idx}:-1", tone="neutral") if idx > 0 else "   "
        dn_b = ActionBtn("\u2193", f"cc_fw:move:{idx}:1", tone="neutral") if idx < len(rules) - 1 else "   "
        en_b = SelectPill(t("common.on") if en else t("common.off"), f"cc_fw:toggle:{idx}", selected=en)
        ed_b = ActionBtn("\u270e", f"cc_fw:edit:{idx}", tone="warn" if edit_idx == idx else "neutral")
        rows.append([
            Txt(chain_s, role="focus" if en else "inactive"),
            Txt(proto_s, role="val" if en else "inactive"),
            Txt(canvas.truncate_plain(src_s, 13), role="focus" if en else "inactive"),
            Txt(canvas.truncate_plain(port_s, 10), role="val" if en else "inactive"),
            Badge(verd_s, style=_verdict_style(verd_s)) if en else f"\u00a0{Txt(verd_s, role='inactive')}\u00a0",
            Txt(canvas.truncate_plain(meta_txt, 14), role="schema" if en else "inactive"),
            f"{en_b} {ed_b} {up_b}{dn_b} {ActionBtn('x', f'cc_fw:del:{idx}', tone='danger_soft')}",
        ])

    tbl_box.add_table(
        headers=[t("cc_firewall.dir"), t("cc_firewall.proto"), t("cc_firewall.source_if"), t("cc_firewall.dst_port"), t("cc_firewall.verdict"), t("cc_firewall.comment_ct"), t("cc_firewall.actions")],
        widths=[7, 7, 12, 9, 10, "flex", 24],
        rows=rows,
        aligns=["left", "left", "left", "left", "left", "left", "right"],
    )
    if_v = t("cc_firewall.if_val", p0=draft["iface"]) if draft["iface"] else ""
    ct_s = t("cc_firewall.ct_val", p0=draft["ct"]) if draft["ct"] else t("cc_firewall.ct_any")
    lim_v = t("cc_firewall.limit_val", p0=draft["limit"]) if draft["limit"] else ""
    tbl_box.add_row(
        f"  {Txt(t('cc_firewall.match_extras'), role='schema')}  "
        f"{InlineField('fw_draft_iface', 'cc_fw:draft_inline:iface', value=if_v, empty_label=t('cc_firewall.if_any'), placeholder=t('cc_firewall.iface_ph'), width=12, tone='neutral', selected=bool(draft['iface']))}  "
        f"{CyclePill(ct_s, 'cc_fw:draft_cycle:ct', active=bool(draft['ct']))}  "
        f"{InlineField('fw_draft_limit', 'cc_fw:draft_inline:limit', value=lim_v, empty_label=t('cc_firewall.limit_none'), placeholder='10/min', width=12, tone='neutral', selected=bool(draft['limit']))}"
    )
    tbl_box.add_row(" ")
    cards.append(tbl_box)
    return cards


def build_cards(cc_panel, canvas: Canvas) -> list:
    return _build_rules_tab(cc_panel, canvas)
