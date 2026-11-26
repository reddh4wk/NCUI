#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""NC-UI Control Center — nftables Firewall & Packet Filter (`panels/cc_firewall.py`)."""

import ipaddress
import json
import os
import re
import shlex
import tempfile
import time
from core import probe
from core.completion import catalog_net_ifaces
from core.config import get_config_dir
from core.i18n import t
from core.state import STATE
from ui.kit import set_feedback, start_inline_input

SYS_NFT_CONF = "/etc/nftables.conf"

_CHAINS = ("input", "output", "forward")
_PROTOS = ("tcp", "udp", "icmp", "any")
_VERDICTS = ("accept", "drop", "reject", "log")

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

_DEFAULT_ENABLED = {"core_lo", "core_inv", "core_est", "core_icmp"}
_DEFAULT_DISABLED = {"svc_ssh_lan", "svc_web", "svc_sync_tcp", "svc_sync_udp"}
_DEFAULT_RULES = [
    {"id": pid, "dst": "", **spec, "enabled": pid in _DEFAULT_ENABLED}
    for pid, spec in _PRESET_SPECS
    if pid in _DEFAULT_ENABLED or pid in _DEFAULT_DISABLED
]

_DICT_DEFAULTS = {
    "filters": {"dir": "ALL", "proto": "ALL", "src": "", "port": "", "verdict": "ALL", "text": ""},
    "draft": {"chain": "input", "proto": "tcp", "src": "", "dport": "", "iface": "", "ct": "", "limit": "", "verdict": "accept", "comment": "", "edit_idx": None},
    "conn_flt": {"state": "ALL", "proto": "ALL", "port": "", "peer": "", "proc": ""},
}


def _fw_store_path() -> str:
    return os.path.join(get_config_dir(), "firewall_rules.json")


def _parse_sys_nft_meta() -> dict:
    meta = {
        "lan_net": "",
        "policies": {"input": "drop", "forward": "drop", "output": "accept"},
    }
    if not os.path.exists(SYS_NFT_CONF):
        return meta
    try:
        with open(SYS_NFT_CONF, "r", encoding="utf-8", errors="ignore") as f:
            txt = f.read()
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
    path = _fw_store_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
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
    path = _fw_store_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
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


def _rule_to_nft_line(r: dict, lan_net: str = "") -> str:
    parts = []
    lan_s = str(lan_net or "").strip()
    iface = str(r.get("iface", "")).strip()
    if iface.lower() in ("any", "*"):
        iface = ""
    elif iface.lower().startswith("if:"):
        iface = iface.split(":", 1)[1].strip()
    src = str(r.get("src", "")).strip()
    if src.lower() in ("any", "*"):
        src = ""
    elif src.lower().startswith("if:"):
        if not iface:
            iface = src.split(":", 1)[1].strip()
        src = ""
    chain = str(r.get("chain", "input")).lower()
    if iface:
        parts.append(f'{"oifname" if chain == "output" else "iifname"} "{iface}"')
    ct = str(r.get("ct", "")).strip()
    if ct:
        if "," in ct and not ct.startswith("{"):
            parts.append(f"ct state {{ {ct} }}")
        else:
            parts.append(f"ct state {ct}")
    if src:
        key = "ip6 saddr" if ":" in (lan_s if src == "$LAN_NETWORK" else src) else "ip saddr"
        parts.append(f"{key} {src}")
    dst = str(r.get("dst", "")).strip()
    if dst:
        key = "ip6 daddr" if ":" in (lan_s if dst == "$LAN_NETWORK" else dst) else "ip daddr"
        parts.append(f"{key} {dst}")
    proto = str(r.get("proto", "any")).lower()
    dport = str(r.get("dport", "")).strip()
    if proto in ("tcp", "udp"):
        if dport:
            parts.append(f"{proto} dport {_fmt_port_set(dport)}")
        else:
            parts.append(f"meta l4proto {proto}")
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


def _has_missing_lan_var(store: dict) -> bool:
    if str(store.get("lan_net") or "").strip():
        return False
    return any(
        r.get("enabled", True) and "$LAN_NETWORK" in f"{r.get('src', '')} {r.get('dst', '')}"
        for r in store.get("rules", [])
    )


def _generate_nft_config(store: dict) -> str:
    lan = str(store.get("lan_net") or "").strip()
    pols = store.get("policies", {})
    rules = store.get("rules", [])
    lines = [
        "#!/usr/bin/nft -f",
        "# Generated by NC-UI Firewall Manager",
        "",
        "flush ruleset",
        "",
    ]
    if lan:
        lines.extend([f"define LAN_NETWORK = {lan}", ""])
    lines.append("table inet filter {")
    for ch in ("input", "forward", "output"):
        pol = pols.get(ch, "accept" if ch == "output" else "drop")
        lines.append(f"    chain {ch} {{")
        lines.append(f"        type filter hook {ch} priority filter; policy {pol};")
        for r in rules:
            if not r.get("enabled", True) or r.get("chain") != ch:
                continue
            nft_ln = _rule_to_nft_line(r, lan)
            if nft_ln:
                lines.append(f"        {nft_ln}")
        lines.append("    }")
        lines.append("")
    lines.append("}")
    return "\n".join(lines) + "\n"


def _fw_dict(key: str) -> dict:
    raw = STATE.get("firewall", key, {})
    base = dict(_DICT_DEFAULTS[key])
    if isinstance(raw, dict):
        for k in base:
            if k in raw and raw[k] is not None:
                base[k] = raw[k]
    for k in ("dir", "proto", "verdict", "state"):
        if k in base:
            base[k] = str(base[k]).lower() if key == "draft" else str(base[k]).upper()
    if key == "draft":
        base["chain"] = str(base["chain"]).lower()
    return base


def _upd_fw_dict(key: str, **kw) -> None:
    cur = _fw_dict(key)
    cur.update(kw)
    STATE.set("firewall", key, cur)


def _cycle_next(seq: tuple, cur):
    return seq[(seq.index(cur) + 1) % len(seq)] if cur in seq else seq[0]


def _parse_idx(act: str) -> int | None:
    try:
        return int(act.split(":", 1)[1])
    except (ValueError, IndexError):
        return None


def _dir_short(chain: str) -> str:
    return {"input": "IN", "output": "OUT", "forward": "FWD"}.get(chain, chain.upper()[:3])


def _verdict_style(v: str) -> str:
    return {"accept": "ok", "drop": "crit", "reject": "crit", "log": "info"}.get(v.lower(), "secondary")


def _split_laddr_iface(laddr: str) -> tuple[str, str]:
    m_v6 = re.match(r"^(\[[^%\]]+)%([^\]]+)(\]:.+)$", laddr)
    if m_v6:
        return f"{m_v6.group(1)}{m_v6.group(3)}", m_v6.group(2)
    m_v4 = re.match(r"^([^%:]+)%([^:]+)(:.+)$", laddr)
    if m_v4:
        return f"{m_v4.group(1)}{m_v4.group(3)}", m_v4.group(2)
    return laddr, ""


def _read_active_flows() -> list[dict]:
    if not probe.has_bin("ss"):
        return []
    ok, out = probe.run_cmd(["ss", "-tunap"], timeout=1.5)
    if not ok or not out:
        ok, out = probe.run_cmd(["ss", "-tuna"], timeout=1.5)
    if not ok or not out:
        return []
    flows = []
    seen_keys: dict[str, int] = {}
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
        laddr_clean, iface = _split_laddr_iface(laddr)
        l_port = laddr.rsplit(":", 1)[-1] if ":" in laddr else ""
        r_ip = raddr.rsplit(":", 1)[0].strip("[]").split("%", 1)[0] if ":" in raddr else raddr
        if r_ip in ("0.0.0.0", "*", "::", "[::]"):
            r_ip = "*"
        raddr_clean = "*" if raddr in ("0.0.0.0:*", "[::]:*", "*:*") else raddr
        base_key = f"{proto}|{laddr}|{raddr}"
        cnt = seen_keys.get(base_key, 0) + 1
        seen_keys[base_key] = cnt
        flow_key = base_key if cnt == 1 else f"{base_key}|{cnt}"
        flows.append({
            "key": flow_key,
            "proto": proto,
            "state": state,
            "laddr": laddr,
            "laddr_clean": laddr_clean,
            "iface": iface,
            "lport": l_port,
            "raddr": raddr,
            "raddr_clean": raddr_clean,
            "rip": r_ip,
            "proc": proc,
        })
    return flows


def _dry_run_nft(store: dict) -> tuple[bool, str]:
    if not probe.has_bin("nft"):
        return False, t("ui.missing", p0="nft")
    if _has_missing_lan_var(store):
        return False, t("cc_firewall.lan_network_required")
    with tempfile.NamedTemporaryFile("w", suffix=".nft", delete=False, encoding="utf-8") as tf:
        tf.write(_generate_nft_config(store))
        tp = tf.name
    try:
        if os.geteuid() == 0:
            ok, out, err = probe.run_cmd_full(["nft", "-c", "-f", tp], timeout=3.0)
        elif probe.has_bin("unshare"):
            ok, out, err = probe.run_cmd_full(["unshare", "-rn", "nft", "-c", "-f", tp], timeout=3.0)
            low = (err + " " + out).lower()
            if not ok and any(m in low for m in ("unshare:", "operation not permitted", "permission denied")) and probe.has_bin("pkexec"):
                ok, out, err = probe.run_cmd_full(["pkexec", "nft", "-c", "-f", tp], timeout=10.0)
        elif probe.has_bin("pkexec"):
            ok, out, err = probe.run_cmd_full(["pkexec", "nft", "-c", "-f", tp], timeout=10.0)
        else:
            return False, t("cc_firewall.pkexec_missing")
    finally:
        try:
            os.unlink(tp)
        except OSError:
            pass
    if ok:
        return True, t("cc_firewall.dry_run_ok")
    return False, err or out or t("cc_firewall.dry_run_failed")


def _apply_ruleset(store: dict, persist_etc: bool = False) -> None:
    if not probe.has_bin("nft"):
        set_feedback("firewall", False, t("ui.missing", p0="nft"))
        return
    if _has_missing_lan_var(store):
        set_feedback("firewall", False, t("cc_firewall.lan_network_required"))
        return
    is_root = os.geteuid() == 0
    if not is_root and not probe.has_bin("pkexec"):
        set_feedback("firewall", False, t("cc_firewall.pkexec_missing"))
        return
    with tempfile.NamedTemporaryFile("w", suffix=".nft", delete=False, encoding="utf-8") as tf:
        tf.write(_generate_nft_config(store))
        tmp_path = tf.name
    try:
        q_tmp = shlex.quote(tmp_path)
        if persist_etc:
            q_etc = shlex.quote(SYS_NFT_CONF)
            cmd = f"nft -c -f {q_tmp} && cp {q_tmp} {q_etc} && chmod 644 {q_etc} && nft -f {q_etc} && nft list table inet filter >/dev/null"
        else:
            cmd = f"nft -c -f {q_tmp} && nft -f {q_tmp} && nft list table inet filter >/dev/null"
        runner = ["bash", "-c", cmd] if is_root else ["pkexec", "bash", "-c", cmd]
        ok_exec, _o, err = probe.run_cmd_full(runner, timeout=12.0)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
    if not ok_exec:
        set_feedback("firewall", False, err or t("cc_firewall.pkexec_failed"))
        return
    if persist_etc and not os.path.isfile(SYS_NFT_CONF):
        set_feedback("firewall", False, t("cc_firewall.sys_nft_not_found", p0=SYS_NFT_CONF))
        return
    set_feedback("firewall", True, t("cc_firewall.saved_applied_etc") if persist_etc else t("cc_firewall.applied_live_nft"))


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
        cycles = {
            "dir": ("ALL", "IN", "OUT", "FWD"),
            "proto": ("ALL", "TCP", "UDP", "ICMP", "ANY"),
            "verdict": ("ALL", "ACCEPT", "DROP", "REJECT", "LOG"),
        }
        _upd_fw_dict("filters", **{col: _cycle_next(cycles.get(col, ("ALL",)), _fw_dict("filters").get(col, "ALL"))})
    elif act.startswith("flt_inline:"):
        col = act.split(":", 1)[1]
        start_inline_input(
            entry_id=f"fw_flt_{col}",
            shell=cc_panel.shell,
            trigger_uri=uri,
            initial_text=str(_fw_dict("filters").get(col, "") or ""),
            max_len=32,
            on_change=lambda txt, c=col: _upd_fw_dict("filters", **{c: str(txt or "")}),
            on_commit=lambda txt, c=col: _upd_fw_dict("filters", **{c: str(txt or "").strip()}),
        )
        return True
    elif act == "flt_clear":
        STATE.set("firewall", "filters", {})
    elif act.startswith("draft_cycle:"):
        col = act.split(":", 1)[1]
        cycles = {
            "chain": _CHAINS,
            "proto": _PROTOS,
            "verdict": _VERDICTS,
            "ct": ("", "new", "established,related", "invalid"),
        }
        seq = cycles.get(col, ())
        if seq:
            _upd_fw_dict("draft", **{col: _cycle_next(seq, _fw_dict("draft").get(col, seq[0]))})
    elif act.startswith("draft_inline:"):
        col = act.split(":", 1)[1]
        comp = (lambda _q, c=col: catalog_net_ifaces(include_fw_tokens=(c == "src"))) if col in ("src", "iface") else None
        start_inline_input(
            entry_id=f"fw_draft_{col}",
            shell=cc_panel.shell,
            trigger_uri=uri,
            initial_text=str(_fw_dict("draft").get(col, "") or ""),
            max_len=48 if col == "comment" else 32,
            completion=comp,
            on_commit=lambda txt, c=col: _upd_fw_dict("draft", **{c: str(txt or "").strip()}),
        )
        return True
    elif act == "composer_new":
        STATE.set("firewall", "draft", {})
        STATE.set("firewall", "composer_open", True)
    elif act == "draft_cancel":
        STATE.set("firewall", "draft", {})
        STATE.set("firewall", "composer_open", False)
    elif act.startswith("sel_rule:"):
        idx = _parse_idx(act)
        if idx is not None and 0 <= idx < len(rules):
            STATE.set("firewall", "sel_rule_idx", idx)
    elif act.startswith("sel_flow:"):
        STATE.set("firewall", "sel_flow_key", act.split(":", 1)[1])
    elif act.startswith("edit:"):
        idx = _parse_idx(act)
        if idx is not None and 0 <= idx < len(rules):
            r = rules[idx]
            STATE.set("firewall", "sel_rule_idx", idx)
            STATE.set("firewall", "composer_open", True)
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
        draft = _fw_dict("draft")
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
            STATE.set("firewall", "composer_open", False)
            STATE.set("firewall", "sel_rule_idx", edit_idx)
            _save_fw_store(store)
            set_feedback("firewall", True, t("cc_firewall.updated_rule", p0=edit_idx + 1, p1=payload["verdict"].upper(), p2=payload["chain"].upper()))
        else:
            rules.append({"id": f"usr_{int(time.time() * 1000)}", **payload, "enabled": True})
            STATE.set("firewall", "draft", {})
            STATE.set("firewall", "composer_open", False)
            STATE.set("firewall", "sel_rule_idx", len(rules) - 1)
            _save_fw_store(store)
            set_feedback(
                "firewall",
                True,
                t("cc_firewall.added_rule_on", p0=draft["verdict"].upper(), p1=draft["chain"].upper(), p2=draft["proto"].upper(), p3=draft["dport"] or t("cc_firewall.any_lbl")),
            )
    elif act.startswith("toggle:"):
        idx = _parse_idx(act)
        if idx is not None and 0 <= idx < len(rules):
            rules[idx]["enabled"] = not bool(rules[idx].get("enabled", True))
            _save_fw_store(store)
            if rules[idx]["enabled"] and _has_missing_lan_var(store):
                set_feedback("firewall", False, t("cc_firewall.lan_network_required"))
    elif act.startswith("move:"):
        try:
            _, s_idx, s_delta = act.split(":", 2)
            idx, delta = int(s_idx), int(s_delta)
        except (ValueError, IndexError):
            idx, delta = -1, 0
        nxt = idx + delta
        if 0 <= idx < len(rules) and 0 <= nxt < len(rules):
            rules[idx], rules[nxt] = rules[nxt], rules[idx]
            cur_sel = STATE.get("firewall", "sel_rule_idx", None)
            if cur_sel == idx:
                STATE.set("firewall", "sel_rule_idx", nxt)
            elif cur_sel == nxt:
                STATE.set("firewall", "sel_rule_idx", idx)
            edit_idx = _fw_dict("draft").get("edit_idx")
            if isinstance(edit_idx, int) and edit_idx >= 0:
                if edit_idx == idx:
                    _upd_fw_dict("draft", edit_idx=nxt)
                elif edit_idx == nxt:
                    _upd_fw_dict("draft", edit_idx=idx)
            _save_fw_store(store)
    elif act.startswith("del:"):
        idx = _parse_idx(act)
        if idx is not None and 0 <= idx < len(rules):
            rules.pop(idx)
            cur_sel = STATE.get("firewall", "sel_rule_idx", None)
            if isinstance(cur_sel, int):
                if not rules:
                    STATE.set("firewall", "sel_rule_idx", None)
                elif cur_sel >= len(rules):
                    STATE.set("firewall", "sel_rule_idx", len(rules) - 1)
                elif cur_sel > idx:
                    STATE.set("firewall", "sel_rule_idx", cur_sel - 1)
            edit_idx = _fw_dict("draft").get("edit_idx")
            if isinstance(edit_idx, int):
                if edit_idx == idx:
                    STATE.set("firewall", "draft", {})
                    STATE.set("firewall", "composer_open", False)
                elif edit_idx > idx:
                    _upd_fw_dict("draft", edit_idx=edit_idx - 1)
            _save_fw_store(store)
    elif act.startswith("pol:"):
        parts = act.split(":", 2)
        if len(parts) == 3 and parts[1] in _CHAINS and parts[2] in ("accept", "drop"):
            ch, val = parts[1], parts[2]
            store.setdefault("policies", {})[ch] = val
            _save_fw_store(store)
            set_feedback("firewall", True, t("cc_firewall.default_policy_for_set_to_click_ap", p0=ch.upper(), p1=val.upper()))
    elif act == "inline_lan":
        def _validate_lan(txt: str) -> tuple[bool, str]:
            s = str(txt or "").strip()
            if not s:
                return True, ""
            try:
                ipaddress.ip_network(s, strict=False)
                return True, ""
            except ValueError:
                return False, t("cc_firewall.invalid_cidr")

        def _commit_lan(txt: str) -> None:
            raw = str(txt or "").strip()
            val = str(ipaddress.ip_network(raw, strict=False)) if raw else ""
            st = _load_fw_store()
            st["lan_net"] = val
            _save_fw_store(st)
            set_feedback("firewall", True, t("cc_firewall.lan_network_updated_to", p0=val or t("cc_firewall.lan_unset")))

        start_inline_input(
            entry_id="fw_lan_net",
            shell=cc_panel.shell,
            trigger_uri=uri,
            initial_text=str(store.get("lan_net") or ""),
            max_len=39,
            validator=_validate_lan,
            on_invalid=lambda _txt: set_feedback("firewall", False, t("cc_firewall.invalid_cidr")),
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
                en_now = bool(found["enabled"])
            else:
                rules.append({"id": pid, "dst": "", **spec, "enabled": True})
                en_now = True
            _save_fw_store(store)
            if en_now and "$LAN_NETWORK" in f"{spec.get('src', '')} {spec.get('dst', '')}" and not str(store.get("lan_net") or "").strip():
                set_feedback("firewall", False, t("cc_firewall.lan_network_required"))
    elif act == "dry_run":
        ok, msg = _dry_run_nft(store)
        set_feedback("firewall", ok, msg)
    elif act == "apply_live":
        _apply_ruleset(store, persist_etc=False)
    elif act == "save_etc":
        _apply_ruleset(store, persist_etc=True)
    elif act == "sync_etc":
        if not os.path.exists(SYS_NFT_CONF):
            set_feedback("firewall", False, t("cc_firewall.sys_nft_not_found", p0=SYS_NFT_CONF))
        else:
            meta = _parse_sys_nft_meta()
            store["lan_net"] = meta["lan_net"]
            store["policies"] = meta["policies"]
            _save_fw_store(store)
            set_feedback(
                "firewall",
                True,
                t("cc_firewall.synced_chain_policies_lan_network", p0=meta["lan_net"] or t("cc_firewall.lan_unset"), p1=SYS_NFT_CONF),
            )
    elif act == "svc_restart":
        if not probe.has_bin("nft"):
            set_feedback("firewall", False, t("ui.missing", p0="nft"))
        elif not probe.has_bin("systemctl"):
            set_feedback("firewall", False, t("ui.missing", p0="systemctl"))
        elif os.geteuid() != 0 and not probe.has_bin("pkexec"):
            set_feedback("firewall", False, t("cc_firewall.pkexec_missing"))
        else:
            cmd = ["systemctl", "restart", "nftables"]
            if os.geteuid() != 0:
                cmd = ["pkexec"] + cmd
            ok, _o, err = probe.run_cmd_full(cmd, timeout=10.0)
            active_now = ok and _service_active()
            set_feedback("firewall", active_now, t("cc_firewall.svc_restarted") if active_now else (err or t("cc_firewall.svc_restart_failed")))
    elif act.startswith("cflt_cycle:"):
        col = act.split(":", 1)[1]
        cycles = {"proto": ("ALL", "TCP", "UDP"), "state": ("ALL", "ESTAB", "LISTEN")}
        _upd_fw_dict("conn_flt", **{col: _cycle_next(cycles.get(col, ("ALL",)), _fw_dict("conn_flt").get(col, "ALL"))})
    elif act.startswith("cflt_inline:"):
        col = act.split(":", 1)[1]
        start_inline_input(
            entry_id=f"fw_cflt_{col}",
            shell=cc_panel.shell,
            trigger_uri=uri,
            initial_text=str(_fw_dict("conn_flt").get(col, "") or ""),
            max_len=32,
            on_change=lambda txt, c=col: _upd_fw_dict("conn_flt", **{c: str(txt or "")}),
            on_commit=lambda txt, c=col: _upd_fw_dict("conn_flt", **{c: str(txt or "").strip()}),
        )
        return True
    elif act == "cflt_clear":
        STATE.set("firewall", "conn_flt", {})
    elif act.startswith("quick_drop:"):
        parts = act.split(":", 3)
        if len(parts) != 4:
            return True
        _, kind, proto_raw, target = parts
        target = target.strip()
        if kind == "port":
            if not (target.isascii() and target.isdigit() and 1 <= int(target) <= 65535):
                return True
        elif kind == "ip":
            try:
                target = str(ipaddress.ip_address(target.strip("[]").split("%", 1)[0]))
            except ValueError:
                return True
        else:
            return True
        proto = "udp" if "UDP" in proto_raw.upper() else "tcp"
        rules.insert(3, {
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
        })
        _save_fw_store(store)
        set_feedback("firewall", True, t("cc_firewall.added_drop_rule_for_click_apply_li", p0=target))

    if cc_panel.shell:
        cc_panel.shell.refresh_view()
    return True
