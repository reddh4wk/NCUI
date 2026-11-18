#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""NC-UI Control Center — nftables Firewall & Packet Filter (`panels/cc_firewall.py`)."""

import json
import os
import re
import shlex
import tempfile
import time
from core import probe
from core.completion import catalog_net_ifaces
from core.config import CONFIG_DIR
from core.i18n import get_active_language, t
from core.state import STATE
from ui.canvas import Canvas
from ui.kit import ActionBtn, Badge, Box, FeedbackBox, SelectPill, StateBadge, Txt, start_inline_input

FW_STORE = os.path.join(CONFIG_DIR, "firewall_rules.json")
SYS_NFT_CONF = "/etc/nftables.conf"

_CHAINS = ("input", "output", "forward")
_PROTOS = ("tcp", "udp", "icmp", "any")
_VERDICTS = ("accept", "drop", "reject", "log")

_DEFAULT_RULES = [
    {"id": "core_lo", "chain": "input", "proto": "any", "src": "", "dst": "", "dport": "", "iface": "lo", "ct": "", "limit": "", "verdict": "accept", "comment": "Allow loopback", "enabled": True},
    {"id": "core_inv", "chain": "input", "proto": "any", "src": "", "dst": "", "dport": "", "iface": "", "ct": "invalid", "limit": "", "verdict": "drop", "comment": "Drop invalid packets", "enabled": True},
    {"id": "core_est", "chain": "input", "proto": "any", "src": "", "dst": "", "dport": "", "iface": "", "ct": "established,related", "limit": "", "verdict": "accept", "comment": "Allow tracked connections", "enabled": True},
    {"id": "core_icmp", "chain": "input", "proto": "icmp", "src": "", "dst": "", "dport": "", "iface": "", "ct": "", "limit": "", "verdict": "accept", "comment": "Allow IPv4/IPv6 ICMP", "enabled": True},
    {"id": "svc_ssh_lan", "chain": "input", "proto": "tcp", "src": "$LAN_NETWORK", "dst": "", "dport": "22", "iface": "", "ct": "new", "limit": "3/minute", "verdict": "accept", "comment": "SSH LAN rate limit", "enabled": False},
    {"id": "svc_web", "chain": "input", "proto": "tcp", "src": "", "dst": "", "dport": "80,443", "iface": "", "ct": "", "limit": "", "verdict": "accept", "comment": "HTTP/HTTPS web ports", "enabled": False},
    {"id": "svc_sync_tcp", "chain": "input", "proto": "tcp", "src": "", "dst": "", "dport": "22000", "iface": "", "ct": "", "limit": "", "verdict": "accept", "comment": "Syncthing sync TCP", "enabled": False},
    {"id": "svc_sync_udp", "chain": "input", "proto": "udp", "src": "", "dst": "", "dport": "22000,21027", "iface": "", "ct": "", "limit": "", "verdict": "accept", "comment": "Syncthing QUIC/Discovery", "enabled": False},
]

_PRESET_SPECS = [
    ("core_lo", {"chain": "input", "proto": "any", "src": "", "dport": "", "iface": "lo", "ct": "", "limit": "", "verdict": "accept", "comment": "Allow loopback"}),
    ("core_inv", {"chain": "input", "proto": "any", "src": "", "dport": "", "iface": "", "ct": "invalid", "limit": "", "verdict": "drop", "comment": "Drop invalid packets"}),
    ("core_est", {"chain": "input", "proto": "any", "src": "", "dport": "", "iface": "", "ct": "established,related", "limit": "", "verdict": "accept", "comment": "Allow tracked connections"}),
    ("core_icmp", {"chain": "input", "proto": "icmp", "src": "", "dport": "", "iface": "", "ct": "", "limit": "", "verdict": "accept", "comment": "Allow IPv4/IPv6 ICMP"}),
    ("svc_ssh_lan", {"chain": "input", "proto": "tcp", "src": "$LAN_NETWORK", "dport": "22", "iface": "", "ct": "new", "limit": "3/minute", "verdict": "accept", "comment": "SSH LAN rate limit"}),
    ("svc_ssh_all", {"chain": "input", "proto": "tcp", "src": "", "dport": "22", "iface": "", "ct": "", "limit": "", "verdict": "accept", "comment": "SSH global"}),
    ("svc_web", {"chain": "input", "proto": "tcp", "src": "", "dport": "80,443", "iface": "", "ct": "", "limit": "", "verdict": "accept", "comment": "HTTP/HTTPS web ports"}),
    ("svc_dns_out", {"chain": "output", "proto": "udp", "src": "", "dport": "53", "iface": "", "ct": "", "limit": "", "verdict": "accept", "comment": "Outbound DNS"}),
    ("svc_sync_tcp", {"chain": "input", "proto": "tcp", "src": "", "dport": "22000", "iface": "", "ct": "", "limit": "", "verdict": "accept", "comment": "Syncthing sync TCP"}),
    ("svc_sync_udp", {"chain": "input", "proto": "udp", "src": "", "dport": "22000,21027", "iface": "", "ct": "", "limit": "", "verdict": "accept", "comment": "Syncthing QUIC/Discovery"}),
    ("svc_localsend", {"chain": "input", "proto": "tcp", "src": "$LAN_NETWORK", "dport": "53317", "iface": "", "ct": "", "limit": "", "verdict": "accept", "comment": "LocalSend LAN"}),
    ("core_log", {"chain": "input", "proto": "any", "src": "", "dport": "", "iface": "", "ct": "", "limit": "5/second", "verdict": "log", "comment": "[NFT-DROP-INPUT] "}),
]



def _set_fw_fb(ok: bool, msg: str) -> None:
    STATE.set("firewall", "cc_fb", {"ok": bool(ok), "msg": str(msg), "ts": time.time()})


def _get_fw_fb() -> dict:
    fb = STATE.get("firewall", "cc_fb", {})
    if isinstance(fb, dict) and fb.get("msg") and (time.time() - float(fb.get("ts", 0))) < 15.0:
        return fb
    return {}


def _feedback_cards() -> list:
    fb = _get_fw_fb()
    if not fb:
        return []
    return [FeedbackBox(t("cc_firewall.status_title"), bool(fb["ok"]), fb["msg"])]


def _parse_sys_nft_meta() -> dict:
    meta = {
        "lan_net": "10.10.0.0/24",
        "policies": {"input": "drop", "forward": "drop", "output": "accept"},
    }
    if not os.path.exists(SYS_NFT_CONF):
        return meta
    try:
        txt = open(SYS_NFT_CONF, "r", encoding="utf-8", errors="ignore").read()
        m_lan = re.search(r"(?m)^\s*define\s+LAN_NETWORK\s*=\s*(\S+)", txt)
        if m_lan:
            meta["lan_net"] = m_lan.group(1)
        for ch in _CHAINS:
            m_pol = re.search(rf"chain\s+{ch}\s*\{{[^}}]*?policy\s+(accept|drop)\s*;", txt, re.DOTALL)
            if m_pol:
                meta["policies"][ch] = m_pol.group(1)
    except Exception:
        pass
    return meta


def _load_fw_store() -> dict:
    if os.path.exists(FW_STORE):
        try:
            data = json.load(open(FW_STORE, "r", encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("rules"), list):
                return data
        except Exception:
            pass
    meta = _parse_sys_nft_meta()
    data = {
        "lan_net": meta["lan_net"],
        "policies": meta["policies"],
        "rules": [dict(r) for r in _DEFAULT_RULES],
    }
    _save_fw_store(data)
    return data


def _save_fw_store(data: dict) -> None:
    try:
        os.makedirs(os.path.dirname(FW_STORE), exist_ok=True)
        with open(FW_STORE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


def _service_active() -> bool:
    ok, out = probe.run_cmd(["systemctl", "is-active", "nftables"], timeout=1.0)
    return ok and out.strip() == "active"


def _fmt_port_set(p_str: str) -> str:
    clean = str(p_str or "").strip()
    if "," in clean and not clean.startswith("{"):
        return "{ " + clean + " }"
    return clean


def _rule_to_nft_line(r: dict) -> str:
    parts = []
    iface = str(r.get("iface", "")).strip()
    chain = str(r.get("chain", "input")).lower()
    if iface:
        parts.append(f'{"oifname" if chain == "output" else "iifname"} "{iface}"')
    ct = str(r.get("ct", "")).strip()
    if ct:
        if "," in ct and not ct.startswith("{"):
            parts.append(f"ct state {{ {ct} }}")
        else:
            parts.append(f"ct state {ct}")
    src = str(r.get("src", "")).strip()
    if src:
        key = "ip6 saddr" if ":" in src else "ip saddr"
        parts.append(f"{key} {src}")
    dst = str(r.get("dst", "")).strip()
    if dst:
        key = "ip6 daddr" if ":" in dst else "ip daddr"
        parts.append(f"{key} {dst}")
    proto = str(r.get("proto", "any")).lower()
    dport = str(r.get("dport", "")).strip()
    if proto in ("tcp", "udp"):
        if dport:
            parts.append(f"{proto} dport {_fmt_port_set(dport)}")
        else:
            parts.append(f"ip protocol {proto}")
    elif proto == "icmp":
        parts.append("meta l4proto { icmp, ipv6-icmp }")
    limit = str(r.get("limit", "")).strip()
    if limit:
        parts.append(f"limit rate {limit}")
    verdict = str(r.get("verdict", "accept")).lower()
    comment = str(r.get("comment", "")).strip().replace('"', "'")
    if verdict == "log":
        prefix = comment or "[NFT-DROP] "
        parts.append(f'log prefix "{prefix}" counter')
    else:
        parts.append(verdict)
        if comment:
            parts.append(f'comment "{comment}"')
    return " ".join(parts)


def _generate_nft_config(store: dict) -> str:
    lan = str(store.get("lan_net", "10.10.0.0/24") or "10.10.0.0/24")
    pols = store.get("policies", {})
    rules = store.get("rules", [])
    lines = [
        "#!/usr/bin/nft -f",
        "# Generated by NC-UI Firewall Manager",
        "",
        "flush ruleset",
        "",
        f"define LAN_NETWORK = {lan}",
        "",
        "table inet filter {",
    ]
    for ch in ("input", "forward", "output"):
        pol = pols.get(ch, "accept" if ch == "output" else "drop")
        lines.append(f"    chain {ch} {{")
        lines.append(f"        type filter hook {ch} priority filter; policy {pol};")
        for r in rules:
            if not r.get("enabled", True) or r.get("chain") != ch:
                continue
            nft_ln = _rule_to_nft_line(r)
            if nft_ln:
                lines.append(f"        {nft_ln}")
        lines.append("    }")
        lines.append("")
    lines.append("}")
    return "\n".join(lines) + "\n"


def _get_filters() -> dict:
    flt = STATE.get("firewall", "filters", {})
    if not isinstance(flt, dict):
        flt = {}
    return {
        "dir": str(flt.get("dir", "ALL")).upper(),
        "proto": str(flt.get("proto", "ALL")).upper(),
        "src": str(flt.get("src", "")),
        "port": str(flt.get("port", "")),
        "verdict": str(flt.get("verdict", "ALL")).upper(),
        "text": str(flt.get("text", "")),
    }


def _set_filters(**kw) -> None:
    cur = _get_filters()
    cur.update(kw)
    STATE.set("firewall", "filters", cur)


def _get_draft() -> dict:
    d = STATE.get("firewall", "draft", {})
    if not isinstance(d, dict):
        d = {}
    return {
        "chain": str(d.get("chain", "input")).lower(),
        "proto": str(d.get("proto", "tcp")).lower(),
        "src": str(d.get("src", "")),
        "dport": str(d.get("dport", "")),
        "iface": str(d.get("iface", "")),
        "ct": str(d.get("ct", "")),
        "limit": str(d.get("limit", "")),
        "verdict": str(d.get("verdict", "accept")).lower(),
        "comment": str(d.get("comment", "")),
        "edit_idx": d.get("edit_idx", None),
    }


def _set_draft(**kw) -> None:
    cur = _get_draft()
    cur.update(kw)
    STATE.set("firewall", "draft", cur)


def _dir_short(chain: str) -> str:
    return {"input": "IN", "output": "OUT", "forward": "FWD"}.get(chain, chain.upper()[:3])


def _verdict_style(v: str) -> str:
    return {"accept": "ok", "drop": "crit", "reject": "warn", "log": "info"}.get(v.lower(), "secondary")


def _read_active_flows() -> list[dict]:
    ok, out = probe.run_cmd(["ss", "-tunap"], timeout=1.5)
    if not ok or not out:
        ok, out = probe.run_cmd(["ss", "-tuna"], timeout=1.5)
    if not ok or not out:
        return []
    flows = []
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 6:
            continue
        proto = parts[0].upper()
        state = parts[1].upper()
        laddr = parts[4]
        raddr = parts[5]
        proc = parts[6] if len(parts) > 6 else "-"
        if 'users:(("' in proc:
            proc = proc.split('"', 2)[1]
        l_port = laddr.rsplit(":", 1)[-1] if ":" in laddr else ""
        r_ip = raddr.rsplit(":", 1)[0].strip("[]") if ":" in raddr else raddr
        if r_ip in ("0.0.0.0", "*", "::", "[::]"):
            r_ip = "*"
        flows.append({"proto": proto, "state": state, "laddr": laddr, "lport": l_port, "raddr": raddr, "rip": r_ip, "proc": proc[:14]})
    return flows


def _apply_ruleset(store: dict, persist_etc: bool = False) -> None:
    nft_txt = _generate_nft_config(store)
    with tempfile.NamedTemporaryFile("w", suffix=".nft", delete=False, encoding="utf-8") as tf:
        tf.write(nft_txt)
        tmp_path = tf.name
    ok, _o, err = probe.run_cmd_full(["nft", "-c", "-f", tmp_path], timeout=3.0)
    if not ok:
        os.unlink(tmp_path)
        _set_fw_fb(False, t("cc_firewall.nft_syntax_check_failed", p0=err or 'error'))
        return
    if persist_etc:
        cmd = f"cp {shlex.quote(tmp_path)} {SYS_NFT_CONF} && chmod 644 {SYS_NFT_CONF} && nft -f {SYS_NFT_CONF}"
    else:
        cmd = f"nft -f {shlex.quote(tmp_path)}"
    ok_exec, _o2, err2 = probe.run_cmd_full(["pkexec", "bash", "-c", cmd], timeout=12.0)
    try:
        os.unlink(tmp_path)
    except Exception:
        pass
    if ok_exec:
        _set_fw_fb(True, "Saved & applied to /etc/nftables.conf" if persist_etc else "Ruleset applied live to nft kernel")
    else:
        _set_fw_fb(False, err2 or "Privilege elevation (pkexec) cancelled or failed")


def handle_cc_firewall_link(cc_panel, uri: str) -> bool:
    if not uri.startswith("cc_fw:"):
        return False
    act = uri[len("cc_fw:"):]
    store = _load_fw_store()
    rules = store["rules"]

    if act == "refresh":
        pass
    elif act.startswith("flt_cycle:"):
        col = act.split(":", 1)[1]
        flt = _get_filters()
        cycles = {
            "dir": ("ALL", "IN", "OUT", "FWD"),
            "proto": ("ALL", "TCP", "UDP", "ICMP", "ANY"),
            "verdict": ("ALL", "ACCEPT", "DROP", "REJECT", "LOG"),
        }
        seq = cycles.get(col, ("ALL",))
        cur = flt.get(col, "ALL")
        nxt = seq[(seq.index(cur) + 1) % len(seq)] if cur in seq else seq[0]
        _set_filters(**{col: nxt})
    elif act.startswith("flt_inline:"):
        col = act.split(":", 1)[1]
        flt = _get_filters()
        start_inline_input(
            entry_id=f"fw_flt_{col}",
            shell=cc_panel.shell,
            trigger_uri=uri,
            initial_text=str(flt.get(col, "") or ""),
            max_len=32,
            on_change=lambda txt, c=col: _set_filters(**{c: str(txt or "")}),
            on_commit=lambda txt, c=col: _set_filters(**{c: str(txt or "").strip()}),
        )
        return True
    elif act == "flt_clear":
        STATE.set("firewall", "filters", {})
    elif act.startswith("draft_cycle:"):
        col = act.split(":", 1)[1]
        draft = _get_draft()
        cycles = {
            "chain": _CHAINS,
            "proto": _PROTOS,
            "verdict": _VERDICTS,
            "ct": ("", "new", "established,related", "invalid"),
        }
        seq = cycles.get(col, ())
        if seq:
            cur = draft.get(col, seq[0])
            nxt = seq[(seq.index(cur) + 1) % len(seq)] if cur in seq else seq[0]
            _set_draft(**{col: nxt})
    elif act.startswith("draft_inline:"):
        col = act.split(":", 1)[1]
        draft = _get_draft()
        comp = (lambda _q, c=col: catalog_net_ifaces(include_fw_tokens=(c == "src"))) if col in ("src", "iface") else None
        start_inline_input(
            entry_id=f"fw_draft_{col}",
            shell=cc_panel.shell,
            trigger_uri=uri,
            initial_text=str(draft.get(col, "") or ""),
            max_len=48 if col == "comment" else 32,
            completion=comp,
            on_commit=lambda txt, c=col: _set_draft(**{c: str(txt or "").strip()}),
        )
        return True
    elif act == "draft_cancel":
        STATE.set("firewall", "draft", {})
    elif act.startswith("edit:"):
        idx = int(act.split(":", 1)[1])
        if 0 <= idx < len(rules):
            r = rules[idx]
            STATE.set("firewall", "draft", {
                "chain": r.get("chain", "input"),
                "proto": r.get("proto", "tcp"),
                "src": r.get("src", ""),
                "dport": r.get("dport", ""),
                "iface": r.get("iface", ""),
                "ct": r.get("ct", ""),
                "limit": r.get("limit", ""),
                "verdict": r.get("verdict", "accept"),
                "comment": r.get("comment", ""),
                "edit_idx": idx,
            })
    elif act == "add_rule":
        draft = _get_draft()
        edit_idx = draft.get("edit_idx")
        payload = {
            "chain": draft["chain"],
            "proto": draft["proto"],
            "src": draft["src"],
            "dst": "",
            "dport": draft["dport"],
            "iface": draft["iface"],
            "ct": draft["ct"],
            "limit": draft["limit"],
            "verdict": draft["verdict"],
            "comment": draft["comment"] or f"{draft['chain']} {draft['proto']} {draft['dport'] or draft['iface'] or 'any'}",
        }
        if isinstance(edit_idx, int) and 0 <= edit_idx < len(rules):
            rules[edit_idx].update(payload)
            STATE.set("firewall", "draft", {})
            _save_fw_store(store)
            _set_fw_fb(True, t("cc_firewall.updated_rule", p0=edit_idx + 1, p1=payload['verdict'].upper(), p2=payload['chain'].upper()))
        else:
            new_r = {"id": f"usr_{int(time.time() * 1000)}", **payload, "enabled": True}
            rules.append(new_r)
            STATE.set("firewall", "draft", {})
            _save_fw_store(store)
            _set_fw_fb(True, t("cc_firewall.added_rule_on", p0=draft['verdict'].upper(), p1=draft['chain'].upper(), p2=draft['proto'].upper(), p3=draft['dport'] or 'any'))
    elif act.startswith("toggle:"):
        idx = int(act.split(":", 1)[1])
        if 0 <= idx < len(rules):
            rules[idx]["enabled"] = not bool(rules[idx].get("enabled", True))
            _save_fw_store(store)
    elif act.startswith("move:"):
        _, s_idx, s_delta = act.split(":", 2)
        idx, delta = int(s_idx), int(s_delta)
        nxt = idx + delta
        if 0 <= idx < len(rules) and 0 <= nxt < len(rules):
            rules[idx], rules[nxt] = rules[nxt], rules[idx]
            _save_fw_store(store)
    elif act.startswith("del:"):
        idx = int(act.split(":", 1)[1])
        if 0 <= idx < len(rules):
            rules.pop(idx)
            _save_fw_store(store)
    elif act.startswith("pol:"):
        _, ch, val = act.split(":", 2)
        if ch in _CHAINS and val in ("accept", "drop"):
            store.setdefault("policies", {})[ch] = val
            _save_fw_store(store)
            _set_fw_fb(True, t("cc_firewall.default_policy_for_set_to_click_ap", p0=ch.upper(), p1=val.upper()))
    elif act == "inline_lan":
        def _commit_lan(txt: str) -> None:
            val = str(txt or "").strip()
            if not val:
                return
            st = _load_fw_store()
            st["lan_net"] = val
            _save_fw_store(st)
            _set_fw_fb(True, t("cc_firewall.lan_network_updated_to", p0=val))
        start_inline_input(
            entry_id="fw_lan_net",
            shell=cc_panel.shell,
            trigger_uri=uri,
            initial_text=str(store.get("lan_net", "10.10.0.0/24") or ""),
            max_len=32,
            on_commit=_commit_lan,
        )
        return True
    elif act.startswith("preset:"):
        pid = act.split(":", 1)[1]
        spec = next((s for p, s in _PRESET_SPECS if p == pid), None)
        if spec:
            found = next((r for r in rules if r.get("id") == pid), None)
            if found:
                found["enabled"] = not bool(found.get("enabled", True))
            else:
                rules.append({"id": pid, "dst": "", **spec, "enabled": True})
            _save_fw_store(store)
    elif act == "dry_run":
        nft_txt = _generate_nft_config(store)
        with tempfile.NamedTemporaryFile("w", suffix=".nft", delete=False, encoding="utf-8") as tf:
            tf.write(nft_txt)
            tp = tf.name
        ok, out, err = probe.run_cmd_full(["nft", "-c", "-f", tp], timeout=2.5)
        os.unlink(tp)
        _set_fw_fb(ok, "Dry-run nft -c passed (0 syntax errors)" if ok else (err or out or "Syntax check failed"))
    elif act == "apply_live":
        _apply_ruleset(store, persist_etc=False)
    elif act == "save_etc":
        _apply_ruleset(store, persist_etc=True)
    elif act == "sync_etc":
        meta = _parse_sys_nft_meta()
        store["lan_net"] = meta["lan_net"]
        store["policies"] = meta["policies"]
        _save_fw_store(store)
        _set_fw_fb(True, t("cc_firewall.synced_chain_policies_lan_network", p0=meta['lan_net'], p1=SYS_NFT_CONF))
    elif act == "svc_restart":
        ok, _o, err = probe.run_cmd_full(["pkexec", "systemctl", "restart", "nftables"], timeout=10.0)
        _set_fw_fb(ok, "nftables.service restarted" if ok else (err or "Restart failed"))
    elif act.startswith("cflt_cycle:"):
        col = act.split(":", 1)[1]
        cf = dict(STATE.get("firewall", "conn_flt", {}) or {})
        cycles = {"proto": ("ALL", "TCP", "UDP"), "state": ("ALL", "ESTAB", "LISTEN")}
        seq = cycles.get(col, ("ALL",))
        cur = str(cf.get(col, "ALL")).upper()
        cf[col] = seq[(seq.index(cur) + 1) % len(seq)] if cur in seq else seq[0]
        STATE.set("firewall", "conn_flt", cf)
    elif act.startswith("cflt_inline:"):
        col = act.split(":", 1)[1]
        cf = dict(STATE.get("firewall", "conn_flt", {}) or {})
        start_inline_input(
            entry_id=f"fw_cflt_{col}",
            shell=cc_panel.shell,
            trigger_uri=uri,
            initial_text=str(cf.get(col, "") or ""),
            max_len=32,
            on_change=lambda txt, c=col: STATE.set("firewall", "conn_flt", {**dict(STATE.get("firewall", "conn_flt", {}) or {}), c: str(txt or "")}),
            on_commit=lambda txt, c=col: STATE.set("firewall", "conn_flt", {**dict(STATE.get("firewall", "conn_flt", {}) or {}), c: str(txt or "").strip()}),
        )
        return True
    elif act == "cflt_clear":
        STATE.set("firewall", "conn_flt", {})
    elif act.startswith("quick_drop:"):
        _, kind, proto_raw, target = act.split(":", 3)
        proto = "udp" if "UDP" in proto_raw.upper() else "tcp"
        new_r = {
            "id": f"blk_{int(time.time() * 1000)}",
            "chain": "input",
            "proto": proto if kind == "port" else "any",
            "src": target if kind == "ip" else "",
            "dst": "",
            "dport": target if kind == "port" else "",
            "iface": "",
            "ct": "",
            "limit": "",
            "verdict": "drop",
            "comment": f"Quick block {target}",
            "enabled": True,
        }
        rules.insert(3, new_r)
        _save_fw_store(store)
        _set_fw_fb(True, t("cc_firewall.added_drop_rule_for_click_apply_li", p0=target))

    if cc_panel.shell:
        cc_panel.shell.refresh_view()
    return True


def handle_cc_firewall_prompt(cc_panel, action_id: str, text: str) -> bool:
    if not action_id.startswith("cc_fw"):
        return False
    val = str(text or "").strip()
    if action_id.startswith("cc_fw_flt|"):
        col = action_id.split("|", 1)[1]
        _set_filters(**{col: val})
        return True
    if action_id.startswith("cc_fw_draft|"):
        col = action_id.split("|", 1)[1]
        _set_draft(**{col: val})
        return True
    if action_id.startswith("cc_fw_cflt|"):
        col = action_id.split("|", 1)[1]
        cf = dict(STATE.get("firewall", "conn_flt", {}) or {})
        cf[col] = val
        STATE.set("firewall", "conn_flt", cf)
        return True
    if action_id == "cc_fw_lan" and val:
        store = _load_fw_store()
        store["lan_net"] = val
        _save_fw_store(store)
        _set_fw_fb(True, t("cc_firewall.lan_network_updated_to", p0=val))
        return True
    return False
