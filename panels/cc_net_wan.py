#!/usr/bin/env python3
import copy
import os

from core.config import get_cc_panel_style
from core.i18n import get_active_language
from core import probe
from ui.canvas import Canvas
from ui.kit import (
    Box,
    ProgressBar,
    Divider,
    Txt,
    ActionBtn,
    StateBadge,
    FoldToggle,
)
from panels.cc_network import (
    _NET_LOCK,
    _NET_STATE,
    _spaced_rows,
    get_watchdog_targets,
)


def _read_resolv_conf() -> tuple[list[str], str]:
    nameservers = []
    search_dom = ""
    if os.path.exists("/etc/resolv.conf"):
        try:
            with open("/etc/resolv.conf", "r", encoding="utf-8") as f:
                for line in f:
                    s = line.strip()
                    if s.startswith("nameserver "):
                        nameservers.append(s.split(None, 1)[1].strip())
                    elif s.startswith(("search ", "domain ")):
                        search_dom = s.split(None, 1)[1].strip()
        except Exception:
            pass
    return nameservers, search_dom


def _read_listening_sockets(limit: int = 10) -> list[tuple[str, str, str]]:
    """Legge i socket TCP/UDP in ascolto tramite `ss -tulnp` (standard iproute2)."""
    rows = []
    ok, out = probe.run_cmd(["ss", "-tulnp"], timeout=1.2)
    if not ok or not out:
        return rows
    seen = set()
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 5:
            continue
        proto = parts[0].upper()
        local_addr = parts[4]
        proc_info = parts[6] if len(parts) > 6 else "-"
        if "users:((" in proc_info:
            proc_info = proc_info.split('"', 2)[1] if '"' in proc_info else proc_info
        key = (proto, local_addr)
        if key in seen:
            continue
        seen.add(key)
        rows.append((proto, local_addr, proc_info[:24]))
        if len(rows) >= limit:
            break
    return rows


def _build_wan_cyberpunk(panel, canvas: Canvas, ifaces: list[dict], wan: dict, pings: dict, routes: list, is_it: bool) -> list:
    cards = []
    wan_ip = str(wan.get("ip") or "scanning...")
    badge_s = wan.get("badge") or "PROBING"
    b = Box(
        "WAN & WATCHDOG // EGRESS & DNS",
        icon="\uf0ac",
        actions=[
            StateBadge(bool(wan.get("ip")), badge_s, "OFFLINE", on_tone="ok"),
            ActionBtn("PROBE", "net:refresh_wan", tone="select"),
            ActionBtn("FLUSH DNS", "net:flush_dns", tone="neutral"),
        ],
        elected=True,
    )
    b.add_row(" ")
    b.add_row(f"  {Txt('[WAN EGRESS]──►', role='vitality')} {Txt(wan_ip, role='focus')}  {Txt(str(wan.get('isp') or ''), role='schema')}")
    dns_l, dom = _read_resolv_conf()
    b.add_row(f"  {Txt('[RESOLV DNS]──►', role='intent')} {Txt(', '.join(dns_l[:3]) or 'dhcp/unset', role='focus')}  {Txt(dom, role='schema')}")

    b.add_section("ICMP RTT WATCHDOG")
    targets = get_watchdog_targets(ifaces)
    wd_rows = []
    for t in targets:
        host = t["host"]
        name = canvas.truncate_plain(t.get("name", host), 18)
        p_info = pings.get(host)
        if p_info and p_info.get("ok"):
            ms = float(p_info.get("ms", 0.0))
            bar_v = min(100.0, max(6.0, (ms / 100.0) * 100.0))
            rtt_s = f"{ms:5.1f}ms"
            rtt_role = "vitality" if ms < 25 else "attention"
        elif p_info is not None:
            bar_v = 100.0
            rtt_s = "TIMEOUT"
            rtt_role = "fault"
        else:
            bar_v = 0.0
            rtt_s = "idle"
            rtt_role = "inactive"
        rm_b = ActionBtn("x", f"net:del_ping:{host}", tone="danger_soft") if len(targets) > 1 else ""
        wd_rows.append([
            Txt(name, role="focus"),
            Txt(host, role="schema"),
            ProgressBar(bar_v, width=12),
            Txt(rtt_s, role=rtt_role),
            rm_b,
        ])
    b.add_table(headers=[], widths=[18, 16, 16, 10, "flex"], rows=wd_rows, aligns=["left", "left", "left", "left", "right"])
    b.add_row(f"  {Txt('[TARGETS]  ──►', role='schema')}", right_markup=f"{ActionBtn('+ ADD HOST..', 'net:prompt_add_ping', tone='neutral')} {ActionBtn('PING ALL', 'net:refresh_ping', tone='apply')}")

    def _fill_routes(bx: Box):
        for r in routes[:8]:
            bx.add_row(f"  {Txt('├─', role='structure')} {Txt(canvas.truncate_plain(str(r), 64), role='schema')}")
        if not routes:
            bx.add_row(f"  {Txt('No kernel routes cached.', role='inactive')}")

    socks = _read_listening_sockets(limit=8)

    def _fill_socks(bx: Box):
        for proto, addr, proc in socks:
            bx.add_row(f"  {Txt('├─', role='structure')} {Txt(f'{proto:<6}', role='intent')} {Txt(f'{addr:<24}', role='focus')} {Txt(proc, role='schema')}")
        if not socks:
            bx.add_row(f"  {Txt('No listening sockets reported.', role='inactive')}")

    b.add_collapsible(panel, "wan_routes_cyber", f"KERNEL ROUTING TABLE ({len(routes)})", _fill_routes)
    b.add_collapsible(panel, "wan_socks_cyber", f"LISTENING SOCKETS ({len(socks)})", _fill_socks)
    b.add_row(" ")
    cards.append(b)
    return cards


def build_wan_tab_cards(panel, canvas: Canvas, ifaces: list[dict]) -> list:
    is_it = (get_active_language() == "it")

    with _NET_LOCK:
        wan = copy.deepcopy(_NET_STATE["wan"])
        pings = copy.deepcopy(_NET_STATE["pings"])
        routes = list(_NET_STATE["routes"])

    if get_cc_panel_style() == "cyberpunk":
        return _build_wan_cyberpunk(panel, canvas, ifaces, wan, pings, routes, is_it)

    cards = []

    # =========================================================================
    # CARD 1: STATO USCITA WAN & DUAL-WAN FAILOVER
    # =========================================================================
    wan_actions = []
    if wan.get("badge"):
        is_primary_line = (wan.get("badge_style") == "accent")
        wan_actions.append(StateBadge(True, wan["badge"], "", on_tone="ok" if is_primary_line else "warn"))
    wan_actions.append(ActionBtn("\uf021 Verifica WAN" if is_it else "\uf021 Check WAN", "net:refresh_wan", tone="select", active=True))

    wan_box = Box(
        "Stato Uscita WAN & Rilevamento Failover" if is_it else "WAN Exit Status & Failover Detection",
        icon="\uf0ac",
        actions=wan_actions,
    )
    wan_box.add_row(" ")

    active_gws = [(i["name"], i["gw"], i["ip"]) for i in ifaces if i["up"] and i["gw"]]
    if active_gws:
        ifname_gw, gw_ip, local_ip = active_gws[0]
        gw_markup = f"{Txt(gw_ip, role='vitality')}   {Txt('via', role='schema')} {Txt(f'{ifname_gw} ({local_ip})', role='val')}"
    else:
        gw_markup = StateBadge(True, "Nessun gateway predefinito attivo" if is_it else "No active default gateway", on_tone="crit")

    nameservers, search_dom = _read_resolv_conf()
    ns_str = ", ".join(nameservers) if nameservers else ("Nessun nameserver" if is_it else "No nameserver")
    ip_fwd = "ON (Router)" if os.path.exists("/proc/sys/net/ipv4/ip_forward") and open("/proc/sys/net/ipv4/ip_forward").read().strip() == "1" else "OFF (Host)"
    tcp_cc = open("/proc/sys/net/ipv4/tcp_congestion_control").read().strip() if os.path.exists("/proc/sys/net/ipv4/tcp_congestion_control") else "cubic"

    dns_cell = Txt(ns_str, role="val") + (f" {Txt(f'({search_dom})', role='inactive')}" if search_dom else "")
    wan_rows = [
        [
            Txt("IPv4 Pubblico Uscita" if is_it else "Public Exit IPv4", role="schema"),
            Txt(wan["ip"], role="focus"),
            ActionBtn("Copy IP", "ui:copy:" + wan["ip"], tone="neutral"),
        ],
        [
            Txt("Gateway Predefinito" if is_it else "Default Gateway", role="schema"),
            gw_markup,
            "",
        ],
        [
            Txt("DNS Resolver Attivi" if is_it else "Active System DNS", role="schema"),
            dns_cell,
            ActionBtn("Flush DNS", "net:flush_dns", tone="neutral"),
        ],
        [
            Txt("Kernel Net Stack", role="schema"),
            f"{Txt('Forwarding:', role='inactive')} {Txt(ip_fwd, role='val')}   {Txt('TCP:', role='inactive')} {Txt(tcp_cc.upper(), role='intent')}",
            "",
        ],
    ]
    wan_box.add_table(
        headers=[],
        widths=[20, 44, "flex"],
        rows=_spaced_rows(wan_rows),
        aligns=["left", "left", "right"],
        show_headers=False,
    )
    wan_box.add_row(" ")
    cards.append(wan_box)

    # =========================================================================
    # CARD 2: INFRASTRUCTURE ICMP WATCHDOG
    # =========================================================================
    targets = get_watchdog_targets(ifaces)
    wd_box = Box(
        "Infrastructure ICMP Watchdog (Ping & Latenza)" if is_it else "Infrastructure ICMP Watchdog (Ping & Latency)",
        icon="\uf201",
        actions=[
            ActionBtn("+ Host..", "net:prompt_add_ping", tone="neutral"),
            ActionBtn("\uf021 Ping Test", "net:refresh_ping", tone="select", active=True),
        ],
    )
    wd_box.add_row(" ")

    wd_rows = []
    for t in targets:
        host = t["host"]
        name = canvas.truncate_plain(t["name"], 22)
        p_info = pings.get(host)
        if p_info is None:
            bar_m = ProgressBar(0, width=16)
            status_m = Txt("\u22ef " + ("Verifica..." if is_it else "Checking..."), role="inactive")
        elif p_info.get("ok"):
            ms = float(p_info.get("ms", 0.0))
            role_ms = "ok_soft" if ms < 15.0 else ("attention" if ms < 60.0 else "fault")
            pct_lat = min(100.0, max(6.0, (ms / 100.0) * 100.0))
            bar_m = ProgressBar(pct_lat, width=16, color_by_threshold=True)
            status_m = Txt(f"\u25cf\u00a0{ms:5.1f}\u00a0ms", role=role_ms)
        else:
            bar_m = ProgressBar(100, width=16, color_by_threshold=True)
            status_m = StateBadge(True, "TIMEOUT", on_tone="crit")

        rm_btn = ActionBtn("x", f"net:del_ping:{host}", tone="danger_soft") if len(targets) > 1 else ""
        wd_rows.append([
            Txt(name, role="focus"),
            Txt(host, role="schema"),
            bar_m,
            status_m,
            rm_btn,
        ])

    wd_box.add_table(
        headers=[
            "TARGET",
            "HOST / IP",
            "CARICO RTT" if is_it else "RTT BAR",
            "LATENZA" if is_it else "LATENCY",
            "AZIONI" if is_it else "ACTIONS",
        ],
        widths=[22, 16, 18, 12, "flex"],
        rows=_spaced_rows(wd_rows),
        aligns=["left", "left", "left", "left", "right"],
    )
    wd_box.add_row(" ")
    cards.append(wd_box)

    # =========================================================================
    # CARD 3: TABELLA DI ROUTING DEL KERNEL (A Comparsa / Scomparsa)
    # =========================================================================
    cards.append(Divider("Diagnostica Kernel, Socket & ARP (Sezioni Espandibili)" if is_it else "Kernel Diagnostics, Sockets & ARP (Expandable)", icon="\uf0e8"))

    rt_open, rt_toggle = FoldToggle(panel, "wan_routes", default=False, is_it=is_it)
    rt_box = Box(
        f"Tabella di Routing del Kernel (ip route) [{len(routes)}]" if is_it else f"Kernel IP Routing Table (ip route) [{len(routes)}]",
        icon="\uf0e8",
        actions=[rt_toggle],
    )
    if not rt_open:
        def_rt = next((r for r in routes if r.startswith("default")), routes[0] if routes else "none")
        rt_box.add_row(f"  {Txt('Default:', role='inactive')} {Txt(def_rt[:58], role='val')}")
    else:
        rt_box.add_row(" ")
        if not routes:
            msg = "Nessuna rotta attiva nel kernel." if is_it else "No active routes in kernel."
            rt_box.add_row(f"  {Txt(msg, role='inactive')}")
        else:
            for r_line in routes[:10]:
                r_role = "intent" if r_line.startswith("default") else "val"
                rt_box.add_row(f"  {Txt(canvas.truncate_plain(r_line, canvas.inner_cols - 4), role=r_role)}")
        rt_box.add_row(" ")
    cards.append(rt_box)

    # =========================================================================
    # CARD 4: PORTE E SERVIZI IN ASCOLTO (A Comparsa / Scomparsa)
    # =========================================================================
    socks = _read_listening_sockets(limit=10)
    sk_open, sk_toggle = FoldToggle(panel, "wan_sockets", default=False, is_it=is_it)
    sk_box = Box(
        f"Porte & Servizi Locali in Ascolto (ss -tulnp) [{len(socks)}]" if is_it else f"Listening Local Ports & Services (ss -tulnp) [{len(socks)}]",
        icon="\uf132",
        actions=[sk_toggle],
    )
    if not sk_open:
        procs_sum = ", ".join(sorted(set(pr for _, _, pr in socks if pr != "-"))[:5]) or "none"
        sk_box.add_row(f"  {Txt('Active listeners:', role='inactive')} {Txt(procs_sum, role='val')}")
    else:
        sk_box.add_row(" ")
        if not socks:
            msg = "Nessun socket in ascolto rilevato." if is_it else "No listening sockets detected."
            sk_box.add_row(f"  {Txt(msg, role='inactive')}")
        else:
            sk_rows = [
                [
                    Txt(proto, role="intent"),
                    Txt(laddr, role="val"),
                    Txt(proc, role="ok_soft"),
                ]
                for proto, laddr, proc in socks
            ]
            sk_box.add_table(
                headers=[
                    "PROTO",
                    "INDIRIZZO : PORTA LOCALE" if is_it else "LOCAL ADDRESS : PORT",
                    "PROCESSO / SERVIZIO" if is_it else "PROCESS / SERVICE",
                ],
                widths=[8, 34, "flex"],
                rows=_spaced_rows(sk_rows),
            )
        sk_box.add_row(" ")
    cards.append(sk_box)

    # =========================================================================
    # CARD 5: TABELLA ARP / NEIGHBOR LAN (A Comparsa / Scomparsa)
    # =========================================================================
    ok_arp, out_arp = probe.run_cmd(["ip", "neigh", "show"], timeout=1.0)
    arp_lines = [l.strip() for l in out_arp.splitlines() if l.strip() and "FAILED" not in l] if (ok_arp and out_arp) else []
    arp_open, arp_toggle = FoldToggle(panel, "wan_arp", default=False, is_it=is_it)
    arp_box = Box(
        f"Tabella ARP / Neighbor LAN (ip neigh) [{len(arp_lines)}]" if is_it else f"LAN ARP / Neighbor Table (ip neigh) [{len(arp_lines)}]",
        icon="\uf0c0",
        actions=[ActionBtn("Flush Stale ARP", "net:flush_arp", tone="neutral"), arp_toggle],
    )
    if not arp_open:
        arp_box.add_row(f"  {Txt('Neighbors in cache:', role='inactive')} {Txt(f'{len(arp_lines)} hosts', role='val')}")
    else:
        arp_box.add_row(" ")
        if not arp_lines:
            msg = "Nessun vicino ARP attivo in cache." if is_it else "No active ARP neighbors in cache."
            arp_box.add_row(f"  {Txt(msg, role='inactive')}")
        else:
            arp_rows = []
            for al in arp_lines[:10]:
                parts = al.split()
                ip_n = parts[0] if parts else ""
                dev_n = parts[2] if len(parts) > 2 else ""
                mac_n = parts[4] if len(parts) > 4 else "-"
                st_n = parts[-1] if parts else ""
                st_role = "ok_soft" if st_n == "REACHABLE" else ("attention" if st_n in ("STALE", "DELAY") else "inactive")
                cp_btn = ActionBtn("Copy MAC", f"ui:copy:{mac_n}", tone="neutral") if ":" in mac_n else ""
                arp_rows.append([
                    Txt(ip_n, role="val"),
                    Txt(mac_n, role="schema"),
                    Txt(dev_n, role="inactive"),
                    Txt(st_n, role=st_role),
                    cp_btn,
                ])
            arp_box.add_table(
                headers=[
                    "IPv4 HOST",
                    "MAC ADDRESS",
                    "DEV",
                    "STATO ARP" if is_it else "ARP STATE",
                    "AZIONI" if is_it else "ACTIONS",
                ],
                widths=[18, 19, 10, 12, "flex"],
                rows=_spaced_rows(arp_rows),
                aligns=["left", "left", "left", "left", "right"],
            )
        arp_box.add_row(" ")
    cards.append(arp_box)

    return cards
