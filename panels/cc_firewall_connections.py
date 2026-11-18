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
from ui.kit import ActionBtn, Badge, Box, CyclePill, InlineFilter, SelectPill, StateBadge, Txt
from panels.cc_engine_firewall import (
    _feedback_cards,
    _read_active_flows,
)


def _build_connections_tab(cc_panel, canvas: Canvas) -> list:
    flows = _read_active_flows()
    cf = STATE.get("firewall", "conn_flt", {})
    if not isinstance(cf, dict):
        cf = {}
    f_state = str(cf.get("state", "ALL")).upper()
    f_proto = str(cf.get("proto", "ALL")).upper()
    f_port = str(cf.get("port", ""))
    f_peer = str(cf.get("peer", ""))
    f_proc = str(cf.get("proc", ""))
    cards = _feedback_cards()

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

    box = Box(
        t("cc_firewall.active_flows_sockets", p0=len(shown), p1=len(flows)),
        icon="\uf0ec",
        actions=[
            Txt(f"LISTEN:{n_listen}  ESTAB:{n_estab}", role="schema"),
            ActionBtn(t("cc_firewall.refresh"), "cc_fw:refresh", tone="neutral"),
        ],
        highlighted=False,
    )
    box.add_row(" ")
    rows = [[
        CyclePill(f_proto, "cc_fw:cflt_cycle:proto", active=(f_proto != "ALL")),
        CyclePill(f_state, "cc_fw:cflt_cycle:state", active=(f_state != "ALL")),
        InlineFilter("fw_cflt_port", f_port, "port:*", "cc_fw:cflt_inline:port", width=11, compact=True),
        InlineFilter("fw_cflt_peer", f_peer, "peer:*", "cc_fw:cflt_inline:peer", width=13, compact=True),
        InlineFilter("fw_cflt_proc", f_proc, "proc:*", "cc_fw:cflt_inline:proc", width=11, compact=True),
        ActionBtn(t("cc_firewall.reset_flt"), "cc_fw:cflt_clear", tone="warn") if has_cflt else Txt(t("cc_firewall.filter"), role="inactive"),
    ]]
    ip_lbl = t("cc_firewall.blk_ip")
    ip_btn_w = max(9, len(ip_lbl))
    blank_ip_slot = "\u00a0" * (ip_btn_w + 2)
    blank_drop_slot = "\u00a0" * 12
    for f in shown[:20]:
        has_drop = f["lport"].isdigit()
        has_ip = f["rip"] not in ("*", "127.0.0.1", "::1")
        if has_drop or has_ip:
            drop_slot = ActionBtn(t("cc_firewall.drop", p0=f["lport"]), f"cc_fw:quick_drop:port:{f['proto']}:{f['lport']}", tone="warn", min_chars=10) if has_drop else blank_drop_slot
            ip_slot = ActionBtn(ip_lbl, f"cc_fw:quick_drop:ip:{f['proto']}:{f['rip']}", tone="danger_soft", min_chars=ip_btn_w) if has_ip else blank_ip_slot
            act_cell = f"{drop_slot} {ip_slot}"
        else:
            act_cell = Txt("-", role="inactive")
        st_s = f["state"][:9]
        st_cell = StateBadge(True, st_s) if f["state"] == "ESTAB" else Txt(st_s, role="schema" if f["state"] == "LISTEN" else "inactive")
        rows.append([
            Txt(f["proto"], role="val"),
            st_cell,
            Txt(canvas.truncate_plain(f["laddr"], 18), role="focus"),
            Txt(canvas.truncate_plain(f["raddr"], 18), role="val"),
            Txt(canvas.truncate_plain(f["proc"], 12), role="schema"),
            act_cell,
        ])
    box.add_table(
        headers=[t("cc_firewall.proto"), t("cc_firewall.state"), t("cc_firewall.local_address"), t("cc_firewall.peer_address"), t("cc_firewall.process"), t("cc_firewall.quick_block")],
        widths=[7, 11, 17, 17, 13, "flex"],
        rows=rows,
        aligns=["left", "left", "left", "left", "left", "right"],
    )
    box.add_row(" ")
    cards.append(box)
    return cards


def build_cards(cc_panel, canvas: Canvas) -> list:
    return _build_connections_tab(cc_panel, canvas)
