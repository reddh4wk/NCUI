#!/usr/bin/env python3
import copy
import os

from core.i18n import t
from core import probe
from ui.canvas import Canvas
from ui.kit import (
    ActionBtn,
    Badge,
    Box,
    Divider,
    InlineField,
    ProgressBar,
    StateBadge,
    Txt,
    make_feedback_cards,
)
from panels.cc_engine_network import (
    _NET_LOCK,
    _NET_STATE,
    _read_sysfs_str,
    _spaced_rows,
    get_watchdog_targets,
    is_resolved_wan_ip,
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
    if not probe.has_bin("ss"):
        return []
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


def build_wan_tab_cards(panel, canvas: Canvas, ifaces: list[dict]) -> list:
    with _NET_LOCK:
        wan = copy.deepcopy(_NET_STATE["wan"])
        pings = copy.deepcopy(_NET_STATE["pings"])
        routes = list(_NET_STATE["routes"])

    cards = []

    # =========================================================================
    # CARD 1: STATO USCITA WAN & DUAL-WAN FAILOVER
    # =========================================================================
    wan_actions = []
    if wan.get("badge"):
        if wan.get("badge_style") == "crit":
            wan_actions.append(Badge(wan["badge"], style="crit"))
        else:
            is_primary_line = (wan.get("badge_style") == "accent")
            wan_actions.append(StateBadge(True, wan["badge"], "", on_tone="ok" if is_primary_line else "warn"))
    wan_actions.append(ActionBtn(t("cc_net_wan.check_wan"), "net:refresh_wan", tone="neutral"))

    wan_box = Box(
        t("cc_net_wan.wan_exit_status_failover_detection"),
        icon="\uf0ac",
        actions=wan_actions,
    )

    active_gws = [(i["name"], i["gw"], i["ip"]) for i in ifaces if i["up"] and i["gw"]]
    if active_gws:
        ifname_gw, gw_ip, local_ip = active_gws[0]
        gw_markup = f"{Txt(gw_ip, role='focus')}   {Txt(t('cc_net_wan.via'), role='schema')} {Txt(f'{ifname_gw} ({local_ip})', role='val')}"
    else:
        gw_markup = Badge(t("cc_net_wan.no_active_default_gateway"), style="crit")

    nameservers, search_dom = _read_resolv_conf()
    ns_str = ", ".join(nameservers) if nameservers else t("cc_net_wan.no_nameserver")
    ip_fwd_raw = _read_sysfs_str("/proc/sys/net/ipv4/ip_forward", "0")
    ip_fwd = t("cc_net_wan.fwd_router") if ip_fwd_raw == "1" else t("cc_net_wan.fwd_host")
    tcp_cc = _read_sysfs_str("/proc/sys/net/ipv4/tcp_congestion_control", "cubic") or "cubic"

    wan_resolved = is_resolved_wan_ip(wan.get("ip", ""))
    wan_copy_btn = ActionBtn(t("cc_net_wan.copy_ip"), "ui:copy:" + wan["ip"], tone="neutral") if wan_resolved else ""
    dns_cell = Txt(ns_str, role="val") + (f" {Txt(f'({search_dom})', role='inactive')}" if search_dom else "")
    wan_rows = [
        [
            Txt(t("cc_net_wan.public_exit_ipv4"), role="schema"),
            Txt(wan["ip"], role="focus" if wan_resolved else "inactive"),
            wan_copy_btn,
        ],
        [
            Txt(t("cc_net_wan.default_gateway"), role="schema"),
            gw_markup,
            "",
        ],
        [
            Txt(t("cc_net_wan.active_system_dns"), role="schema"),
            dns_cell,
            ActionBtn(t("cc_net_wan.flush_dns"), "net:flush_dns", tone="warn"),
        ],
        [
            Txt(t("cc_net_wan.kernel_net_stack"), role="schema"),
            f"{Txt(t('cc_net_wan.forwarding'), role='inactive')} {Txt(ip_fwd, role='val')}   {Txt(t('cc_net_wan.tcp_lbl'), role='inactive')} {Txt(tcp_cc.upper(), role='val')}",
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
    cards.append(wan_box)

    # =========================================================================
    # CARD 2: INFRASTRUCTURE ICMP WATCHDOG
    # =========================================================================
    has_ping = probe.has_bin("ping")
    targets = get_watchdog_targets(ifaces)
    wd_actions = [
        InlineField(
            "net_add_ping",
            "net:inline_add_ping",
            value="",
            empty_label=t("cc_net_wan.host"),
            placeholder=t("cc_net_wan.ping_placeholder"),
            width=18,
            icon="",
            tone="neutral",
        ),
        ActionBtn(t("cc_net_wan.ping_test"), "net:refresh_ping", tone="neutral") if has_ping else Badge(t("ui.missing", p0="ping"), style="warn"),
    ]
    wd_box = Box(
        t("cc_net_wan.infrastructure_icmp_watchdog_ping"),
        icon="\uf201",
        actions=wd_actions,
    )

    wd_rows = []
    for tgt in targets:
        host = tgt["host"]
        name = canvas.truncate_plain(tgt["name"], 22)
        p_info = pings.get(host)
        if not has_ping or (p_info and p_info.get("missing")):
            bar_m = ProgressBar(0, width=16)
            status_m = Badge(t("ui.missing", p0="ping"), style="warn")
        elif p_info is None:
            bar_m = ProgressBar(0, width=16)
            status_m = Txt("\u22ef " + t("cc_net_wan.checking"), role="inactive")
        elif p_info.get("ok"):
            ms = float(p_info.get("ms", 0.0))
            role_ms = "ok_soft" if ms < 15.0 else ("attention" if ms < 60.0 else "fault")
            pct_lat = min(100.0, max(6.0, (ms / 100.0) * 100.0))
            bar_m = ProgressBar(pct_lat, width=16, color_by_threshold=True)
            status_m = Txt(f"\u25cf\u00a0{ms:5.1f}\u00a0ms", role=role_ms)
        else:
            bar_m = ProgressBar(100, width=16, color_by_threshold=True)
            status_m = Badge(t("cc_net_wan.timeout"), style="crit")

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
            t("cc_net_wan.target"),
            t("cc_net_wan.host_ip"),
            t("cc_net_wan.rtt_bar"),
            t("cc_net_wan.latency"),
            t("cc_net_wan.actions"),
        ],
        widths=[22, 16, 18, 14, "flex"],
        rows=_spaced_rows(wd_rows),
        aligns=["left", "left", "left", "left", "right"],
    )
    cards.append(wd_box)

    # =========================================================================
    # CARD 3: TABELLA DI ROUTING DEL KERNEL (A Comparsa / Scomparsa)
    # =========================================================================
    cards.append(Divider(t("cc_net_wan.kernel_diagnostics_sockets_arp_exp"), icon="\uf0e8"))

    has_ip = probe.has_bin("ip")
    rt_open, rt_toggle = panel._fold("wan_routes", default=False)
    rt_box = Box(
        t("cc_net_wan.kernel_ip_routing_table_ip_route", p0=len(routes)),
        icon="\uf0e8",
        actions=[rt_toggle] if has_ip else [Badge(t("ui.missing", p0="ip"), style="warn")],
    )
    if not has_ip:
        rt_box.add_row(f"  {Txt(t('cc_net_wan.ip_missing'), role='inactive')}")
    elif not rt_open:
        def_rt = next((r for r in routes if r.startswith("default")), routes[0] if routes else t("common.none"))
        rt_box.add_row(f"  {Txt(t('cc_net_wan.default'), role='inactive')} {Txt(def_rt[:58], role='val')}")
    else:
        if not routes:
            rt_box.add_row(f"  {Txt(t('cc_net_wan.no_active_routes_in_kernel'), role='inactive')}")
        else:
            for r_line in routes[:10]:
                r_role = "focus" if r_line.startswith("default") else "val"
                rt_box.add_row(f"  {Txt(canvas.truncate_plain(r_line, canvas.inner_cols - 4), role=r_role)}")
    cards.append(rt_box)

    # =========================================================================
    # CARD 4: PORTE E SERVIZI IN ASCOLTO (A Comparsa / Scomparsa)
    # =========================================================================
    has_ss = probe.has_bin("ss")
    socks = _read_listening_sockets(limit=10)
    sk_open, sk_toggle = panel._fold("wan_sockets", default=False)
    sk_box = Box(
        t("cc_net_wan.listening_local_ports_services_ss", p0=len(socks)),
        icon="\uf132",
        actions=[sk_toggle] if has_ss else [Badge(t("ui.missing", p0="ss"), style="warn")],
    )
    if not has_ss:
        sk_box.add_row(f"  {Txt(t('cc_net_wan.ss_missing'), role='inactive')}")
    elif not sk_open:
        procs_sum = ", ".join(sorted(set(pr for _, _, pr in socks if pr != "-"))[:5]) or t("common.none")
        sk_box.add_row(f"  {Txt(t('cc_net_wan.active_listeners'), role='inactive')} {Txt(procs_sum, role='val')}")
    else:
        if not socks:
            sk_box.add_row(f"  {Txt(t('cc_net_wan.no_listening_sockets_detected'), role='inactive')}")
        else:
            sk_rows = [
                [
                    Txt(proto, role="val"),
                    Txt(laddr, role="val"),
                    Txt(proc, role="val"),
                ]
                for proto, laddr, proc in socks
            ]
            sk_box.add_table(
                headers=[
                    t("cc_net_wan.proto"),
                    t("cc_net_wan.local_address_port"),
                    t("cc_net_wan.process_service"),
                ],
                widths=[8, 34, "flex"],
                rows=_spaced_rows(sk_rows),
            )
    cards.append(sk_box)

    # =========================================================================
    # CARD 5: TABELLA ARP / NEIGHBOR LAN (A Comparsa / Scomparsa)
    # =========================================================================
    ok_arp, out_arp = probe.run_cmd(["ip", "neigh", "show"], timeout=1.0) if has_ip else (False, "")
    arp_lines = [l.strip() for l in out_arp.splitlines() if l.strip() and "FAILED" not in l] if (ok_arp and out_arp) else []
    arp_open, arp_toggle = panel._fold("wan_arp", default=False)
    arp_actions = [ActionBtn(t("cc_net_wan.flush_stale_arp"), "net:flush_arp", tone="warn"), arp_toggle] if has_ip else [Badge(t("ui.missing", p0="ip"), style="warn")]
    arp_box = Box(
        t("cc_net_wan.lan_arp_neighbor_table_ip_neigh", p0=len(arp_lines)),
        icon="\uf0c0",
        actions=arp_actions,
    )
    if not has_ip:
        arp_box.add_row(f"  {Txt(t('cc_net_wan.ip_missing'), role='inactive')}")
    elif not arp_open:
        arp_box.add_row(f"  {Txt(t('cc_net_wan.neighbors_in_cache'), role='inactive')} {Txt(t('cc_net_wan.hosts', p0=len(arp_lines)), role='val')}")
    else:
        if not arp_lines:
            arp_box.add_row(f"  {Txt(t('cc_net_wan.no_active_arp_neighbors_in_cache'), role='inactive')}")
        else:
            arp_rows = []
            for al in arp_lines[:10]:
                parts = al.split()
                ip_n = parts[0] if parts else ""
                dev_n = parts[2] if len(parts) > 2 else ""
                mac_n = parts[4] if len(parts) > 4 else "-"
                st_n = parts[-1] if parts else ""
                st_role = "ok_soft" if st_n == "REACHABLE" else ("attention" if st_n in ("STALE", "DELAY") else "inactive")
                cp_btn = ActionBtn(t("cc_net_wan.copy_mac"), f"ui:copy:{mac_n}", tone="neutral") if ":" in mac_n else ""
                arp_rows.append([
                    Txt(ip_n, role="val"),
                    Txt(mac_n, role="schema"),
                    Txt(dev_n, role="inactive"),
                    Txt(st_n, role=st_role),
                    cp_btn,
                ])
            arp_box.add_table(
                headers=[
                    t("cc_net_wan.ipv4_host"),
                    t("cc_net_wan.mac_address"),
                    t("cc_net_wan.dev"),
                    t("cc_net_wan.arp_state"),
                    t("cc_net_wan.actions"),
                ],
                widths=[18, 19, 10, 12, "flex"],
                rows=_spaced_rows(arp_rows),
                aligns=["left", "left", "left", "left", "right"],
            )
    cards.append(arp_box)
    cards.extend(make_feedback_cards("network", title_key="cc_net_wired.networkmanager_operation_status", key="fb_wan_routing"))

    return cards


def build_cards(cc_panel, canvas: Canvas) -> list:
    from panels.cc_engine_network import build_cc_network_cards
    return build_cc_network_cards(cc_panel, canvas, "wan_routing")
