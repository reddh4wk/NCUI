#!/usr/bin/env python3
import copy

from core.i18n import t
from core import probe
from ui.canvas import Canvas
from ui.kit import (
    Box,
    Txt,
    SelectPill,
    ActionBtn,
    InlineField,
    StateBadge,
    FoldToggle,
)
from panels.cc_engine_network import (
    PROXY_ENV_FILE,
    _NET_LOCK,
    _NET_STATE,
    _spaced_rows,
    get_net_tab_feedback_cards,
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


def build_vpn_tab_cards(panel, canvas: Canvas) -> list:
    with _NET_LOCK:
        vpns = copy.deepcopy(_NET_STATE["vpns"])
        proxy = copy.deepcopy(_NET_STATE["proxy"])

    cards = []

    # =========================================================================
    # CARD 1: GESTORE PROFILI VPN & WIREGUARD
    # =========================================================================
    any_vpn_up = any(v["active"] for v in vpns)
    vpn_st_badge = StateBadge(any_vpn_up, t("cc_net_vpn.tunnel_up"), t("cc_net_vpn.standby"), on_tone="ok")
    vpn_box = Box(
        t("cc_net_vpn.vpn_wireguard_tunnels_networkmanag"),
        icon="\uf132",
        actions=[
            vpn_st_badge,
            ActionBtn(t("cc_net_vpn.import_wireguard"), "vpn:prompt_import:wireguard", tone="neutral"),
            ActionBtn(t("cc_net_vpn.import_openvpn"), "vpn:prompt_import:openvpn", tone="neutral"),
        ],
    )
    vpn_box.add_row(" ")

    if not vpns:
        msg = t("cc_net_vpn.no_vpn_or_wireguard_profiles_confi")
        vpn_box.add_row(f"  {Txt(msg, role='inactive')}")
    else:
        v_rows = []
        for v in vpns:
            is_act = bool(v["active"])
            st_dot = StateBadge(is_act, t("cc_net_vpn.active"), t("cc_net_vpn.standby"), on_tone="ok")
            is_ac = (str(v.get("autoconnect", "no")).lower() == "yes")
            ac_b = SelectPill(t("cc_net_vpn.auto"), f"vpn:toggle_auto:{v['uuid']}:{'no' if is_ac else 'yes'}", selected=is_ac)

            split_lbl = "Split-Tunnel" if v["split_tunnel"] else "Full-Tunnel"
            split_uri = f"vpn:toggle_split:{v['uuid']}:{'no' if v['split_tunnel'] else 'yes'}"
            split_b = SelectPill(split_lbl, split_uri, selected=bool(v["split_tunnel"]))

            dns_b = InlineField(
                f"vpn_dns_{v['uuid']}",
                f"vpn:inline_dns:{v['uuid']}",
                value="",
                empty_label=t("cc_net_vpn.dns"),
                placeholder="1.1.1.1,9.9.9.9",
                width=16,
                icon="",
                tone="neutral",
            )
            act_b = (
                ActionBtn(t("cc_net_vpn.down"), f"vpn:down:{v['uuid']}", tone="danger_soft")
                if is_act
                else ActionBtn(t("cc_net_vpn.activate"), f"vpn:up:{v['uuid']}", tone="apply")
            )
            del_b = ActionBtn("x", f"vpn:del:{v['uuid']}", tone="danger_soft")
            dev_tag = f" ({v['device']})" if v.get("device") else ""
            meta_s = f"({v['type']}{dev_tag})"

            v_rows.append([
                f"{Txt(v['name'][:20], role='focus' if is_act else 'val')} {Txt(meta_s, role='inactive')}",
                st_dot,
                f"{ac_b} {split_b} {dns_b} {act_b} {del_b}",
            ])
        vpn_box.add_table(
            headers=[
                t("cc_net_vpn.tunnel_profile"),
                t("cc_net_vpn.state"),
                t("cc_net_vpn.options_actions"),
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
    tun_open, tun_toggle = FoldToggle(panel, "vpn_kern_tunnels", default=bool(wg_lines))
    tun_box = Box(
        t("cc_net_vpn.active_kernel_tunnel_interfaces", p0=len(wg_lines)),
        icon="\uf084",
        actions=[tun_toggle],
    )
    tun_box.add_row(" ")
    if not tun_open:
        sum_msg = t("cc_net_vpn.no_active_tunnel_interfaces_wg_tun") if not wg_lines else t("cc_net_vpn.active_tunnels_summary", p0=len(wg_lines))
        tun_box.add_row(f"  {Txt(sum_msg, role='inactive')}")
    else:
        if not wg_lines:
            msg = t("cc_net_vpn.no_active_tunnel_interfaces_wg_tun")
            tun_box.add_row(f"  {Txt(msg, role='inactive')}")
        else:
            for wl in wg_lines:
                tun_box.add_row(f"  {Txt('\u2022', role='vitality')}  {Txt(wl, role='val')}")
    tun_box.add_row(" ")
    cards.append(tun_box)

    # =========================================================================
    # CARD 3: PROXY DI SISTEMA (VARIABILI D'AMBIENTE SHELL / SISTEMA)
    # =========================================================================
    p_mode = proxy.get("mode", "none")
    prx_badge = StateBadge(p_mode != "none", f"PROXY: {p_mode.upper()}", t("cc_net_vpn.direct"), on_tone="warn")
    prx_box = Box(
        t("cc_net_vpn.global_network_proxy_environment_s"),
        icon="\uf0ac",
        actions=[prx_badge],
    )
    prx_box.add_row(" ")

    lbl_direct = t("cc_net_vpn.direct_no_proxy")
    lbl_manual = t("cc_net_vpn.manual_http_socks5")
    lbl_auto = t("cc_net_vpn.automatic_pac_url")
    m_btns = "  ".join([
        SelectPill(lbl_direct, "net:proxy_mode:none", selected=(p_mode == "none")),
        SelectPill(lbl_manual, "net:proxy_mode:manual", selected=(p_mode == "manual")),
        SelectPill(lbl_auto, "net:proxy_mode:auto", selected=(p_mode == "auto")),
    ])
    prx_box.add_table(
        headers=[],
        widths=[20, "flex"],
        rows=_spaced_rows([
            [Txt(t("cc_net_vpn.proxy_mode"), role="schema"), m_btns],
            [Txt(t("cc_net_vpn.environment_file"), role="schema"), Txt(PROXY_ENV_FILE, role="inactive")],
        ]),
        aligns=["left", "left"],
        show_headers=False,
    )

    unset_lbl = t("cc_net_vpn.not_set")
    if p_mode == "manual":
        prx_box.add_row(" ")
        http_s = f"{proxy['http_host']}:{proxy['http_port']}" if proxy["http_host"] else unset_lbl
        socks_s = f"{proxy['socks_host']}:{proxy['socks_port']}" if proxy["socks_port"] else unset_lbl
        prx_box.add_table(
            headers=[],
            widths=[20, 34, "flex"],
            rows=_spaced_rows([
                [
                    Txt(t("cc_net_vpn.http_https_proxy"), role="schema"),
                    Txt(http_s, role="focus" if proxy["http_host"] else "inactive"),
                    InlineField("net_proxy_http", "net:inline_proxy_http", value="", empty_label=t("cc_net_vpn.edit_http"), placeholder="127.0.0.1:8080", width=18, icon="\u270e", tone="neutral"),
                ],
                [
                    Txt(t("cc_net_vpn.socks5_proxy_host"), role="schema"),
                    Txt(socks_s, role="focus" if proxy["socks_host"] else "inactive"),
                    InlineField("net_proxy_socks", "net:inline_proxy_socks", value="", empty_label=t("cc_net_vpn.edit_socks5"), placeholder="127.0.0.1:1080", width=18, icon="\u270e", tone="neutral"),
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
                Txt(t("cc_net_vpn.pac_autoconfig_url"), role="schema"),
                Txt(pac_url, role="focus" if proxy["autoconfig_url"] else "inactive"),
                ActionBtn(t("cc_net_vpn.edit_pac_url"), "net:prompt_proxy_pac", tone="neutral"),
            ]],
            aligns=["left", "left", "right"],
            show_headers=False,
        )

    prx_box.add_row(" ")
    cards.append(prx_box)
    cards.extend(get_net_tab_feedback_cards("vpn_proxy"))

    return cards


def build_cards(cc_panel, canvas: Canvas) -> list:
    from panels.cc_engine_network import build_cc_network_cards
    return build_cc_network_cards(cc_panel, canvas, "vpn_proxy")
