#!/usr/bin/env python3
import copy

from core.config import get_cc_panel_style
from core.i18n import get_active_language
from core import probe
from ui.canvas import Canvas
from ui.kit import (
    Box,
    Divider,
    Txt,
    SelectPill,
    ActionBtn,
    StateBadge,
    FoldToggle,
)
from panels.cc_network import (
    PROXY_ENV_FILE,
    _NET_LOCK,
    _NET_STATE,
    _spaced_rows,
)


def _read_wireguard_live_info() -> list[str]:
    """Legge lo stato dei tunnel attivi (ip -br addr show type wireguard / tun)."""
    out_lines = []
    ok, out = probe.run_cmd(["ip", "-br", "addr", "show"], timeout=1.0)
    if ok and out:
        for line in out.splitlines():
            parts = line.split()
            if parts and parts[0].startswith(("wg", "tun", "tap", "ipsec", "ppp", "tailscale")):
                out_lines.append(line.strip())
    return out_lines


def _build_vpn_cyberpunk(panel, canvas: Canvas, vpns: list, proxy: dict, is_it: bool) -> list:
    cards = []
    any_up = any(v["active"] for v in vpns)
    b = Box(
        "VPN TUNNELS // WIREGUARD & OVPN",
        icon="\uf132",
        actions=[
            StateBadge(any_up, "TUNNEL UP", "STANDBY", on_tone="ok"),
            ActionBtn("+ WG", "vpn:prompt_import:wireguard", tone="select"),
            ActionBtn("+ OVPN", "vpn:prompt_import:openvpn", tone="neutral"),
        ],
        elected=True,
    )
    b.add_row(" ")
    if not vpns:
        b.add_row(f"  {Txt('[TUNNELS]──► NONE CONFIGURED IN NETWORKMANAGER', role='inactive')}")
    else:
        rows = []
        for v in vpns:
            vu = v["uuid"]
            isl = bool(v["active"])
            is_ac = (str(v.get("autoconnect", "no")).lower() == "yes")
            ac_v = "no" if is_ac else "yes"
            sp_v = "no" if v["split_tunnel"] else "yes"
            updn = ActionBtn("DOWN", f"vpn:down:{vu}", tone="danger_soft") if isl else ActionBtn("UP", f"vpn:up:{vu}", tone="apply")
            rows.append([
                f"{Txt('▶━' if isl else '┣━', role='vitality' if isl else 'structure')} {Txt(canvas.truncate_plain(v['name'], 20), role='focus')}",
                StateBadge(isl, "ACTIVE", "IDLE", on_tone="ok"),
                SelectPill("SPLIT", f"vpn:toggle_split:{vu}:{sp_v}", selected=bool(v["split_tunnel"])),
                f"{SelectPill('AUTO', f'vpn:toggle_auto:{vu}:{ac_v}', selected=is_ac)} {ActionBtn('DNS', f'vpn:dns_prompt:{vu}', tone='neutral')} {updn} {ActionBtn('x', f'vpn:del:{vu}', tone='danger_soft')}",
            ])
        b.add_table(headers=[], widths=[24, 12, 12, "flex"], rows=rows, aligns=["left", "left", "left", "right"])

    wg_live = _read_wireguard_live_info()
    if wg_live:
        def _fill_wg(bx: Box):
            for ln in wg_live[:6]:
                bx.add_row(f"  {Txt('├─', role='structure')} {Txt(ln, role='vitality')}")
        b.add_collapsible(panel, "vpn_wg_live", f"KERNEL WG / TUN INTERFACES ({len(wg_live)})", _fill_wg)

    p_mode = str(proxy.get("mode", "none"))
    b.add_section("EGRESS PROXY MATRIX (ENV / SYSTEM)")
    pm_pills = " ".join(SelectPill(lbl, f"net:proxy_mode:{k}", selected=(p_mode == k)) for k, lbl in (("none", "DIRECT"), ("manual", "MANUAL"), ("auto", "PAC AUTO")))
    b.add_row(f"  {Txt('[MODE]  ──►', role='intent')} {pm_pills}")
    if p_mode == "manual":
        b.add_row(f"  {Txt('[HTTP]  ──►', role='schema')} {Txt(proxy.get('http') or 'unset', role='focus')}", right_markup=ActionBtn("EDIT HTTP..", "net:prompt_proxy_http", tone="select"))
        b.add_row(f"  {Txt('[SOCKS] ──►', role='schema')} {Txt(proxy.get('socks') or 'unset', role='val')}", right_markup=ActionBtn("EDIT SOCKS..", "net:prompt_proxy_socks", tone="neutral"))
    elif p_mode == "auto":
        b.add_row(f"  {Txt('[PAC]   ──►', role='schema')} {Txt(proxy.get('pac_url') or 'unset', role='focus')}", right_markup=ActionBtn("EDIT PAC..", "net:prompt_proxy_pac", tone="select"))
    b.add_row(" ")
    cards.append(b)
    return cards


def build_vpn_tab_cards(panel, canvas: Canvas) -> list:
    is_it = (get_active_language() == "it")

    with _NET_LOCK:
        vpns = copy.deepcopy(_NET_STATE["vpns"])
        proxy = copy.deepcopy(_NET_STATE["proxy"])

    if get_cc_panel_style() == "cyberpunk":
        return _build_vpn_cyberpunk(panel, canvas, vpns, proxy, is_it)

    cards = []

    # =========================================================================
    # CARD 1: GESTORE PROFILI VPN & WIREGUARD
    # =========================================================================
    any_vpn_up = any(v["active"] for v in vpns)
    vpn_st_badge = StateBadge(any_vpn_up, "TUNNEL UP", "STANDBY", on_tone="ok")
    vpn_box = Box(
        "Tunnel VPN & WireGuard (NetworkManager)" if is_it else "VPN & WireGuard Tunnels (NetworkManager)",
        icon="\uf132",
        actions=[
            vpn_st_badge,
            ActionBtn("+ Import WireGuard..", "vpn:prompt_import:wireguard", tone="select", active=True),
            ActionBtn("+ Import OpenVPN..", "vpn:prompt_import:openvpn", tone="neutral"),
        ],
    )
    vpn_box.add_row(" ")

    if not vpns:
        msg = "Nessun profilo VPN o WireGuard presente in NetworkManager." if is_it else "No VPN or WireGuard profiles configured in NetworkManager."
        vpn_box.add_row(f"  {Txt(msg, role='inactive')}")
    else:
        v_rows = []
        for v in vpns:
            is_act = bool(v["active"])
            st_dot = StateBadge(is_act, "ATTIVA" if is_it else "ACTIVE", "STANDBY", on_tone="ok")
            is_ac = (str(v.get("autoconnect", "no")).lower() == "yes")
            ac_b = SelectPill("Auto", f"vpn:toggle_auto:{v['uuid']}:{'no' if is_ac else 'yes'}", selected=is_ac)

            split_lbl = "Split-Tunnel" if v["split_tunnel"] else "Full-Tunnel"
            split_uri = f"vpn:toggle_split:{v['uuid']}:{'no' if v['split_tunnel'] else 'yes'}"
            split_b = SelectPill(split_lbl, split_uri, selected=bool(v["split_tunnel"]))

            dns_b = ActionBtn("DNS..", f"vpn:dns_prompt:{v['uuid']}", tone="neutral")
            act_b = (
                ActionBtn("Disattiva" if is_it else "Down", f"vpn:down:{v['uuid']}", tone="danger_soft")
                if is_act
                else ActionBtn("Attiva" if is_it else "Activate", f"vpn:up:{v['uuid']}", tone="apply", active=True)
            )
            del_b = ActionBtn("x", f"vpn:del:{v['uuid']}", tone="danger_soft")
            dev_tag = f" [{v['device']}]" if v.get("device") else ""
            meta_s = f"({v['type']}{dev_tag})"

            v_rows.append([
                f"{Txt(v['name'][:20], role='focus' if is_act else 'val')} {Txt(meta_s, role='inactive')}",
                st_dot,
                f"{ac_b} {split_b} {dns_b} {act_b} {del_b}",
            ])
        vpn_box.add_table(
            headers=[
                "PROFILO TUNNEL" if is_it else "TUNNEL PROFILE",
                "STATO" if is_it else "STATE",
                "OPZIONI & AZIONI" if is_it else "OPTIONS & ACTIONS",
            ],
            widths=[28, 12, "flex"],
            rows=_spaced_rows(v_rows),
            aligns=["left", "left", "right"],
        )
    vpn_box.add_row(" ")
    cards.append(vpn_box)

    # =========================================================================
    # CARD 2: TELEMETRIA INTERFACCE TUNNEL NEL KERNEL (A Comparsa / Scomparsa)
    # =========================================================================
    wg_lines = _read_wireguard_live_info()
    tun_open, tun_toggle = FoldToggle(panel, "vpn_kern_tunnels", default=bool(wg_lines), is_it=is_it)
    tun_box = Box(
        f"Interfacce Tunnel nel Kernel ({len(wg_lines)})" if is_it else f"Active Kernel Tunnel Interfaces ({len(wg_lines)})",
        icon="\uf084",
        actions=[tun_toggle],
    )
    if not tun_open:
        sum_msg = "Nessun tunnel attivo nel kernel." if not wg_lines else f"{len(wg_lines)} tunnel attivi."
        tun_box.add_row(f"  {Txt(sum_msg, role='inactive')}")
    else:
        tun_box.add_row(" ")
        if not wg_lines:
            msg = "Nessuna interfaccia tunnel (wg / tun / ipsec) attiva nel kernel." if is_it else "No active tunnel interfaces (wg / tun / ipsec) detected in kernel."
            tun_box.add_row(f"  {Txt(msg, role='inactive')}")
        else:
            for wl in wg_lines:
                tun_box.add_row(f"  {Txt('\u25b8', role='vitality')}  {Txt(wl, role='val')}")
        tun_box.add_row(" ")
    cards.append(tun_box)

    # =========================================================================
    # CARD 3: PROXY DI SISTEMA (VARIABILI D'AMBIENTE SHELL / SISTEMA)
    # =========================================================================
    cards.append(Divider("Configurazione Proxy di Rete" if is_it else "System Proxy Configuration", icon="\uf0ac"))

    p_mode = proxy.get("mode", "none")
    prx_badge = StateBadge(p_mode != "none", f"PROXY: {p_mode.upper()}", "DIRECT", on_tone="warn")
    prx_box = Box(
        "Proxy di Rete Globale (Environment / Shell)" if is_it else "Global Network Proxy (Environment / Shell)",
        icon="\uf0ac",
        actions=[prx_badge],
    )
    prx_box.add_row(" ")

    lbl_direct = "Diretta (No Proxy)" if is_it else "Direct (No Proxy)"
    lbl_manual = "Manuale (HTTP / SOCKS5)" if is_it else "Manual (HTTP / SOCKS5)"
    lbl_auto = "Automatico (PAC URL)" if is_it else "Automatic (PAC URL)"
    m_btns = "  ".join([
        SelectPill(lbl_direct, "net:proxy_mode:none", selected=(p_mode == "none")),
        SelectPill(lbl_manual, "net:proxy_mode:manual", selected=(p_mode == "manual")),
        SelectPill(lbl_auto, "net:proxy_mode:auto", selected=(p_mode == "auto")),
    ])
    prx_box.add_table(
        headers=[],
        widths=[20, "flex"],
        rows=_spaced_rows([
            [Txt("Modalit\u00e0 Proxy" if is_it else "Proxy Mode", role="schema"), m_btns],
            [Txt("File Ambiente" if is_it else "Environment File", role="schema"), Txt(PROXY_ENV_FILE, role="inactive")],
        ]),
        aligns=["left", "left"],
        show_headers=False,
    )

    unset_lbl = "Non impostato" if is_it else "Not set"
    if p_mode == "manual":
        prx_box.add_row(" ")
        http_s = f"{proxy['http_host']}:{proxy['http_port']}" if proxy["http_host"] else unset_lbl
        socks_s = f"{proxy['socks_host']}:{proxy['socks_port']}" if proxy["socks_host"] else unset_lbl
        prx_box.add_table(
            headers=[],
            widths=[20, 34, "flex"],
            rows=_spaced_rows([
                [
                    Txt("HTTP / HTTPS Proxy", role="schema"),
                    Txt(http_s, role="focus" if proxy["http_host"] else "inactive"),
                    ActionBtn("Modifica HTTP.." if is_it else "Edit HTTP..", "net:prompt_proxy_http", tone="select", active=True),
                ],
                [
                    Txt("SOCKS5 Proxy Host", role="schema"),
                    Txt(socks_s, role="focus" if proxy["socks_host"] else "inactive"),
                    ActionBtn("Modifica SOCKS5.." if is_it else "Edit SOCKS5..", "net:prompt_proxy_socks", tone="neutral"),
                ],
            ]),
            aligns=["left", "left", "right"],
            show_headers=False,
        )
    elif p_mode == "auto":
        prx_box.add_row(" ")
        pac_url = proxy["autoconfig_url"] or unset_lbl
        prx_box.add_table(
            headers=[],
            widths=[20, 34, "flex"],
            rows=[[
                Txt("PAC Autoconfig URL", role="schema"),
                Txt(pac_url, role="focus" if proxy["autoconfig_url"] else "inactive"),
                ActionBtn("Modifica URL PAC.." if is_it else "Edit PAC URL..", "net:prompt_proxy_pac", tone="select", active=True),
            ]],
            aligns=["left", "left", "right"],
            show_headers=False,
        )

    prx_box.add_row(" ")
    cards.append(prx_box)

    return cards
