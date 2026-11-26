#!/usr/bin/env python3
import copy

from core.i18n import t
from core import probe
from ui.canvas import Canvas
from ui.kit import (
    ActionBtn,
    Badge,
    Box,
    InlineField,
    SelectPill,
    StateBadge,
    Txt,
    make_feedback_cards,
)
from panels.cc_engine_network import (
    _NET_LOCK,
    _NET_STATE,
    _spaced_rows,
    get_proxy_env_path,
)


def _read_wireguard_live_info() -> list[str]:
    """Legge lo stato dei tunnel attivi (ip -br addr show type wireguard / tun)."""
    if not probe.has_bin("ip"):
        return []
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
        net_init = bool(_NET_STATE.get("initialized"))
        nm_running = bool(_NET_STATE.get("nm_running", True))

    cards = []

    # =========================================================================
    # CARD 1: GESTORE PROFILI VPN & WIREGUARD
    # =========================================================================
    has_nmcli = probe.has_bin("nmcli")
    nm_ready = has_nmcli and (not net_init or nm_running)
    any_vpn_up = any(v["active"] for v in vpns)
    vpn_actions = []
    if not has_nmcli:
        vpn_actions.append(Badge(t("ui.missing", p0="nmcli"), style="warn"))
    elif not nm_ready:
        vpn_actions.append(StateBadge(True, t("cc_net_vpn.nm_offline"), on_tone="crit"))
    else:
        vpn_actions = [
            StateBadge(any_vpn_up, t("cc_net_vpn.tunnel_up"), t("cc_net_vpn.standby"), on_tone="ok"),
            ActionBtn(t("cc_net_vpn.import_wireguard"), "vpn:prompt_import:wireguard", tone="neutral"),
            ActionBtn(t("cc_net_vpn.import_openvpn"), "vpn:prompt_import:openvpn", tone="neutral"),
        ]

    vpn_box = Box(
        t("cc_net_vpn.vpn_wireguard_tunnels_networkmanag"),
        icon="\uf132",
        actions=vpn_actions,
    )

    if not has_nmcli:
        vpn_box.add_row(f"  {Txt(t('cc_net_wifi.networkmanager_nmcli_is_not_instal'), role='inactive')}")
    elif not nm_ready:
        vpn_box.add_row(f"  {Txt(t('cc_net_wired.networkmanager_service_is_not_runn'), role='fault')}")
    elif not vpns:
        vpn_box.add_row(f"  {Txt(t('cc_net_vpn.no_vpn_or_wireguard_profiles_confi'), role='inactive')}")
    else:
        split_t = t("cc_net_vpn.split_tunnel")
        full_t = t("cc_net_vpn.full_tunnel")
        split_mc = max(len(split_t), len(full_t))
        down_t = t("cc_net_vpn.down")
        act_t = t("cc_net_vpn.activate")
        act_mc = max(len(down_t), len(act_t))

        v_rows = []
        for v in vpns:
            is_act = bool(v["active"])
            st_dot = StateBadge(is_act, t("cc_net_vpn.active"), t("cc_net_vpn.standby"), on_tone="ok")
            is_ac = (str(v.get("autoconnect", "no")).lower() == "yes")
            ac_b = SelectPill(t("cc_net_vpn.auto"), f"vpn:toggle_auto:{v['uuid']}:{'no' if is_ac else 'yes'}", selected=is_ac)

            split_lbl = split_t if v["split_tunnel"] else full_t
            split_uri = f"vpn:toggle_split:{v['uuid']}:{'no' if v['split_tunnel'] else 'yes'}"
            split_b = SelectPill(split_lbl, split_uri, selected=bool(v["split_tunnel"]), min_chars=split_mc)

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
                ActionBtn(down_t, f"vpn:down:{v['uuid']}", tone="danger_soft", min_chars=act_mc)
                if is_act
                else ActionBtn(act_t, f"vpn:up:{v['uuid']}", tone="apply", min_chars=act_mc)
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
    cards.append(vpn_box)

    # =========================================================================
    # CARD 2: TELEMETRIA INTERFACCE TUNNEL NEL KERNEL (A Comparsa / Scomparsa)
    # =========================================================================
    has_ip = probe.has_bin("ip")
    wg_lines = _read_wireguard_live_info()
    tun_open, tun_toggle = panel._fold("vpn_kern_tunnels", default=bool(wg_lines))
    tun_box = Box(
        t("cc_net_vpn.active_kernel_tunnel_interfaces", p0=len(wg_lines)),
        icon="\uf084",
        actions=[tun_toggle] if has_ip else [Badge(t("ui.missing", p0="ip"), style="warn")],
    )
    if not has_ip:
        tun_box.add_row(f"  {Txt(t('cc_net_wan.ip_missing'), role='inactive')}")
    elif not tun_open:
        sum_msg = t("cc_net_vpn.no_active_tunnel_interfaces_wg_tun") if not wg_lines else t("cc_net_vpn.active_tunnels_summary", p0=len(wg_lines))
        tun_box.add_row(f"  {Txt(sum_msg, role='inactive')}")
    else:
        if not wg_lines:
            tun_box.add_row(f"  {Txt(t('cc_net_vpn.no_active_tunnel_interfaces_wg_tun'), role='inactive')}")
        else:
            for wl in wg_lines:
                tun_box.add_row(f"  {Txt('\u2022', role='vitality')}  {Txt(wl, role='val')}")
    cards.append(tun_box)

    # =========================================================================
    # CARD 3: PROXY DI SISTEMA (VARIABILI D'AMBIENTE SHELL / SISTEMA)
    # =========================================================================
    p_mode = proxy.get("mode", "none")
    prx_badge = StateBadge(p_mode != "none", t("cc_net_vpn.proxy_badge", p0=p_mode.upper()), t("cc_net_vpn.direct"), on_tone="warn")
    prx_box = Box(
        t("cc_net_vpn.global_network_proxy_environment_s"),
        icon="\uf0ac",
        actions=[prx_badge],
    )

    m_btns = "  ".join([
        SelectPill(t("cc_net_vpn.direct_no_proxy"), "net:proxy_mode:none", selected=(p_mode == "none")),
        SelectPill(t("cc_net_vpn.manual_http_socks5"), "net:proxy_mode:manual", selected=(p_mode == "manual")),
        SelectPill(t("cc_net_vpn.automatic_pac_url"), "net:proxy_mode:auto", selected=(p_mode == "auto")),
    ])
    prx_box.add_table(
        headers=[],
        widths=[20, "flex"],
        rows=_spaced_rows([
            [Txt(t("cc_net_vpn.proxy_mode"), role="schema"), m_btns],
            [Txt(t("cc_net_vpn.environment_file"), role="schema"), Txt(get_proxy_env_path(), role="val")],
        ]),
        aligns=["left", "left"],
        show_headers=False,
    )

    unset_lbl = t("cc_net_vpn.not_set")
    if p_mode == "manual":
        prx_box.add_row(" ")
        http_s = f"{proxy['http_host']}:{proxy['http_port']}" if proxy["http_host"] else unset_lbl
        socks_s = f"{proxy['socks_host']}:{proxy['socks_port']}" if proxy["socks_host"] else unset_lbl
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

    cards.append(prx_box)
    cards.extend(make_feedback_cards("network", title_key="cc_net_wired.networkmanager_operation_status", key="fb_vpn_proxy"))

    return cards


def build_cards(cc_panel, canvas: Canvas) -> list:
    from panels.cc_engine_network import build_cc_network_cards
    return build_cc_network_cards(cc_panel, canvas, "vpn_proxy")
