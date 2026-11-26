#!/usr/bin/env python3
from core import probe
from core.i18n import t
from core.state import STATE
from ui.canvas import Canvas
from ui.kit import ActionBtn, Badge, Box, CyclePill, InlineFilter, InspectorCell, StateBadge, Txt, feedback_cards
from panels.cc_engine_firewall import (
    _fw_dict,
    _read_active_flows,
)


def _build_flow_inspector(flow: dict) -> Box:
    q_acts = []
    if flow["lport"].isdigit():
        q_acts.append(ActionBtn(t("cc_firewall.drop_port_btn"), f"cc_fw:quick_drop:port:{flow['proto']}:{flow['lport']}", tone="warn"))
    if flow["rip"] not in ("*", "127.0.0.1", "::1"):
        q_acts.append(ActionBtn(t("cc_firewall.block_peer_btn"), f"cc_fw:quick_drop:ip:{flow['proto']}:{flow['rip']}", tone="danger_soft"))

    ibox = Box(t("cc_firewall.flow_inspector_hdr"), icon="\uf06e", actions=q_acts, highlighted=False)
    ibox.add_row(" ")
    st_cell = StateBadge(True, flow["state"]) if flow["state"] == "ESTAB" else Txt(flow["state"], role="schema" if flow["state"] == "LISTEN" else "inactive")
    iface_s = f"  \u00b7  {Txt('if:' + flow['iface'], role='schema')}" if flow["iface"] else ""
    ibox.add_kv(t("cc_firewall.flow_proto_state"), f"{Txt(flow['proto'], role='val')}  {st_cell}{iface_s}")
    ibox.add_kv(t("cc_firewall.flow_local"), Txt(flow["laddr"], role="focus"))
    ibox.add_kv(t("cc_firewall.flow_remote"), Txt(flow["raddr"], role="val"))
    ibox.add_kv(t("cc_firewall.flow_process"), Txt(flow["proc"], role="val"))
    ibox.add_row(" ")
    return ibox


def build_cards(cc_panel, canvas: Canvas) -> list:
    has_ss = probe.has_bin("ss")
    flows = _read_active_flows() if has_ss else []
    cf = _fw_dict("conn_flt")
    f_state = cf["state"]
    f_proto = cf["proto"]
    f_port = cf["port"]
    f_peer = cf["peer"]
    f_proc = cf["proc"]
    cards = feedback_cards("firewall", "cc_firewall.status_title")

    shown = []
    for f in flows:
        if f_state != "ALL" and f["state"] != f_state:
            continue
        if f_proto != "ALL" and not f["proto"].startswith(f_proto):
            continue
        if f_port and f_port not in f["laddr"]:
            continue
        if f_peer and f_peer.lower() not in f["raddr"].lower():
            continue
        if f_proc and f_proc.lower() not in f["proc"].lower():
            continue
        shown.append(f)

    n_listen = sum(1 for f in flows if f["state"] == "LISTEN")
    n_estab = sum(1 for f in flows if f["state"] == "ESTAB")
    has_cflt = any([f_state != "ALL", f_proto != "ALL", f_port, f_peer, f_proc])

    visible_flows = shown[:20]
    sel_key = str(STATE.get("firewall", "sel_flow_key", "") or "")
    sel_flow = next((f for f in visible_flows if f["key"] == sel_key), visible_flows[0] if visible_flows else None)

    hdr_acts = [Txt(f"LISTEN:{n_listen}  ESTAB:{n_estab}", role="schema")]
    if not has_ss:
        hdr_acts.append(Badge(t("ui.missing", p0="ss"), style="warn"))
    if has_cflt:
        hdr_acts.append(ActionBtn(t("cc_firewall.reset_flt"), "cc_fw:cflt_clear", tone="warn"))
    hdr_acts.append(ActionBtn(t("cc_firewall.refresh"), "cc_fw:refresh", tone="neutral"))

    box = Box(
        t("cc_firewall.active_flows_sockets", p0=len(shown), p1=len(flows)),
        icon="\uf0ec",
        actions=hdr_acts,
        highlighted=False,
    )
    box.add_row(" ")
    rows = [[
        CyclePill(f_proto, "cc_fw:cflt_cycle:proto", active=(f_proto != "ALL")),
        CyclePill(f_state, "cc_fw:cflt_cycle:state", active=(f_state != "ALL")),
        InlineFilter("fw_cflt_port", f_port, t("cc_firewall.cflt_port_ph"), "cc_fw:cflt_inline:port", width=16, compact=True),
        InlineFilter("fw_cflt_peer", f_peer, t("cc_firewall.cflt_peer_ph"), "cc_fw:cflt_inline:peer", width=16, compact=True),
        InlineFilter("fw_cflt_proc", f_proc, t("cc_firewall.cflt_proc_ph"), "cc_fw:cflt_inline:proc", width=14, compact=True),
    ]]
    proc_max = max(20, canvas.inner_cols - 72)
    for f in visible_flows:
        is_sel = bool(sel_flow is not None and f["key"] == sel_flow["key"])
        st_s = f["state"][:9]
        st_cell = Badge(st_s, style="ok") if f["state"] == "ESTAB" else Txt(st_s, role="schema" if f["state"] == "LISTEN" else "inactive")
        rows.append([
            InspectorCell(f["proto"], f"cc_fw:sel_flow:{f['key']}", selected=is_sel),
            st_cell,
            Txt(canvas.truncate_plain(f["laddr_clean"], 22), role="focus"),
            Txt(canvas.truncate_plain(f["raddr_clean"], 22), role="val"),
            Txt(canvas.truncate_plain(f["proc"], proc_max), role="schema"),
        ])

    box.add_table(
        headers=[
            t("cc_firewall.proto"),
            t("cc_firewall.state"),
            t("cc_firewall.local_address"),
            t("cc_firewall.peer_address"),
            t("cc_firewall.process"),
        ],
        widths=[8, 10, 22, 22, "flex"],
        rows=rows,
        aligns=["left", "left", "left", "left", "left"],
    )
    if not has_ss:
        box.add_row(f"  {Txt(t('cc_firewall.ss_not_installed'), role='warn')}")
    elif not visible_flows:
        box.add_row(f"  {Txt(t('cc_firewall.no_matching_flows'), role='inactive')}")
    box.add_row(" ")
    cards.append(box)

    if sel_flow is not None:
        cards.append(_build_flow_inspector(sel_flow))

    return cards
