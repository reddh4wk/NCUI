#!/usr/bin/env python3
import copy
import glob
import time
from core.config import CONFIG, THEME, get_network_show_bar_speed
from core.state import STATE
from core.i18n import t
from ui.canvas import Canvas
from ui.kit import (
    Box,
    Txt,
    ActionBtn,
    Badge,
    StateBadge,
)
from ui.markup import format_speed
from panels.cc_engine_network import (
    Panel as _CCBackendPanel,
    _NET_LOCK,
    _NET_STATE,
    get_interfaces_fast,
    is_resolved_wan_ip,
)


class Panel(_CCBackendPanel):
    """
    Pannello Popup Waybar per Network & Comms (44 colonne).
    Usa esclusivamente i componenti semantici centralizzati di `ui/kit.py`
    e mostra i selettori di monitoraggio barra solo se `show_bar_speed` è attivo.
    """
    id = "network"
    icon = "\uf6ff"
    supports_expand = False
    hover_highlight = True

    def __init__(self, shell=None):
        self.title = t("network.title") if shell is not None else self.id
        super().__init__(shell)

    def get_cols(self, force_expanded: bool = False) -> int:
        return int(THEME["geometry"]["compact_cols"])

    def build_cards(self, canvas: Canvas, force_expanded: bool = False) -> list:
        self._update_live_speeds()
        target_cols = self.get_cols()
        canvas.set_cols(target_cols)

        ifaces = get_interfaces_fast(self._speeds)
        unit_mode = self.get_state("speed_unit", CONFIG["network"].get("default_speed_unit", "bytes"))
        mon_iface = self.get_state("monitored_iface", "auto")
        show_bar_spd = get_network_show_bar_speed()

        valid_names = [i["name"] for i in ifaces]
        if mon_iface not in valid_names and mon_iface != "ALL":
            up_ifs = [i["name"] for i in ifaces if i["up"]]
            mon_iface = up_ifs[0] if up_ifs else (valid_names[0] if valid_names else "N/D")

        self._schedule_bg_refresh(mode_hint="compact")
        has_wifi_hw = any(i["kind"] == "wifi" for i in ifaces)
        if has_wifi_hw:
            with _NET_LOCK:
                wifi_age = time.time() - float(_NET_STATE["ts"].get("wifi", 0.0))
            if wifi_age >= 5.0:
                self._schedule_bg_refresh(mode_hint="wifi")

        with _NET_LOCK:
            wan = copy.deepcopy(_NET_STATE["wan"])
            bt = copy.deepcopy(_NET_STATE["bt"])
            wifi = copy.deepcopy(_NET_STATE["wifi"])

        cards = []

        # =====================================================================
        # CARD 1: USCITA WAN & GATEWAY
        # =====================================================================
        unit_lbl = "B/s" if unit_mode == "bytes" else "b/s"
        wan_box = Box(
            t("network.wan_routing"),
            icon="\uf0ac",
            actions=[
                ActionBtn(unit_lbl, "net:toggle_unit", tone="neutral"),
                ActionBtn("\uf013", "ui:open_settings:network", tone="neutral"),
            ],
            fixed_width=True,
            min_cols=target_cols,
            max_cols=target_cols,
        )
        wan_box.add_row(" ")

        wan_ip = str(wan.get("ip") or t("common.nd"))
        wan_badge_txt = str(wan.get("badge", "")).strip()
        wan_b_style = wan.get("badge_style", "info")
        if wan_badge_txt and wan_b_style == "accent":
            wan_dot = f"{Txt('\u25cf', role='vitality')} "
        elif wan_badge_txt and wan_b_style == "warn":
            wan_dot = f"{Txt('\u25cf', role='attention')} "
        elif wan_badge_txt and wan_b_style == "crit":
            wan_dot = f"{Txt('\u25cf', role='fault')} "
        else:
            wan_dot = ""

        active_gws = [(i["gw"], i["name"]) for i in ifaces if i["up"] and i["gw"]]
        if active_gws:
            gw_ip, gw_dev = active_gws[0]
            gw_left = f"{Txt(t('network.gateway_2'), role='schema')}   {Txt(gw_ip, role='val')}"
            gw_right = Txt(gw_dev, role="schema")
        else:
            gw_left = f"{Txt(t('network.gateway_2'), role='schema')}   {Txt(t('network.none'), role='inactive')}"
            gw_right = ""

        wan_resolved = is_resolved_wan_ip(wan_ip)
        wan_copy_btn = ActionBtn(t("network.copy"), f"ui:copy:{wan_ip}", tone="neutral") if wan_resolved else ""
        wan_rows = [
            [
                f"{Txt(t('network.wan_ip_2'), role='schema')}  {wan_dot}{Txt(wan_ip, role='val' if wan_resolved else 'inactive')}",
                wan_copy_btn,
            ],
            [" ", " "],
            [gw_left, gw_right],
        ]
        if wan_badge_txt:
            if wan_b_style == "crit":
                wan_line_val = Badge(wan_badge_txt, style="crit")
            elif wan_b_style in ("accent", "warn"):
                wan_line_val = StateBadge(True, wan_badge_txt, on_tone="ok" if wan_b_style == "accent" else "warn")
            else:
                wan_line_val = Txt(wan_badge_txt, role="schema")
            wan_rows.insert(
                1,
                [
                    f"   {Txt(t('network.line'), role='schema')}      {wan_line_val}",
                    "",
                ],
            )

        wan_box.add_table(
            headers=[],
            widths=["flex", 9],
            rows=wan_rows,
            aligns=["left", "right"],
            uniform_row_height=True,
            show_headers=False,
        )
        wan_box.add_row(" ")
        cards.append(wan_box)

        # =====================================================================
        # CARD 2: INTERFACCE DI RETE (Attive & Inattive)
        # =====================================================================
        visible_ifaces = [
            i for i in ifaces
            if not (i["kind"] == "bridge" and not i["up"] and i["ip"] == "N/D" and i["name"].startswith("veth"))
        ]
        up_ifaces = [i for i in visible_ifaces if i["up"]]
        down_ifaces = [i for i in visible_ifaces if not i["up"]]

        if_box = Box(
            t("network.network_interfaces"),
            icon="\uf6ff",
            fixed_width=True,
            min_cols=target_cols,
            max_cols=target_cols,
        )
        if_box.add_row(" ")

        if not up_ifaces:
            if_box.add_row(f"  {Txt(t("network.no_active_interfaces"), role='inactive')}")
            if_box.add_row(" ")
        else:
            for idx, iface in enumerate(up_ifaces):
                if idx > 0:
                    if_box.add_section()
                    if_box.add_row(" ")

                ic = "\uf1eb" if iface["kind"] == "wifi" else ("\uf132" if iface["kind"] == "vpn" else "\uf6ff")
                is_mon = (iface["name"] == mon_iface)
                mon_uri = f"net:mon:{iface['name']}"
                dn_uri = f"net:ifdown:{iface['name']}"
                clean_ip = iface["ip"].split("/")[0]
                ls_str = iface.get("link_speed", "")

                spd_tag = f" {Txt('(' + ls_str + ')', role='schema')}" if ls_str and ls_str != "N/D" else ""
                if show_bar_spd:
                    dot_ch = "\u25cf" if is_mon else "\u25cb"
                    mon_dot = f"<a href='{mon_uri}'><span underline='none'>{Txt(dot_ch, role='intent' if is_mon else 'inactive')}</span></a>  "
                    bar_right = Txt(t("network.bar_on"), role="intent") if is_mon else Txt(t("network.bar_off"), role="inactive")
                else:
                    mon_dot = f"{Txt('\u25cf', role='vitality')}  "
                    bar_right = ""

                hdr_left = f"{mon_dot}{Txt(ic, role='vitality')}  {Txt(iface['name'], role='focus')}{spd_tag}"
                dn_btn = ActionBtn(t("network.down"), dn_uri, tone="danger_soft")

                rx_val, tx_val = iface["rx_rate"], iface["tx_rate"]
                rx_s = format_speed(rx_val, unit_mode).strip()
                tx_s = format_speed(tx_val, unit_mode).strip()
                rx_role = "ok_soft" if rx_val > 64.0 else "inactive"
                tx_role = "val" if tx_val > 64.0 else "inactive"

                ip_right = ActionBtn(t("network.copy"), f"ui:copy:{clean_ip}", tone="neutral") if iface["ip"] != "N/D" else ""

                iface_rows = [
                    [hdr_left, dn_btn],
                    [
                        f"   {Txt(t('network.ipv4'), role='schema')}  {Txt(iface['ip'], role='val' if iface['ip'] != 'N/D' else 'inactive')}",
                        ip_right,
                    ],
                    [
                        f"   {Txt('\u2193', role=rx_role)} {Txt(rx_s, role='val' if rx_val > 64.0 else 'inactive')}"
                        f"    {Txt('\u2191', role=tx_role)} {Txt(tx_s, role='val' if tx_val > 64.0 else 'inactive')}",
                        bar_right,
                    ],
                ]
                if_box.add_table(
                    headers=[],
                    widths=["flex", 9],
                    rows=iface_rows,
                    aligns=["left", "right"],
                    uniform_row_height=True,
                    show_headers=False,
                )
                if_box.add_row(" ")

        if down_ifaces:
            if_box.add_section(t("network.inactive"))
            if_box.add_row(" ")
            down_rows = []
            for idx, iface in enumerate(down_ifaces):
                if idx > 0:
                    down_rows.append([" ", " "])
                ic = "\uf1eb" if iface["kind"] == "wifi" else ("\uf132" if iface["kind"] == "vpn" else "\uf6ff")
                up_uri = f"net:ifup:{iface['name']}"
                down_rows.append([
                    f"{Txt(f'\u25cb  {ic}', role='inactive')}  {Txt(iface['name'], role='inactive')}",
                    ActionBtn(t("network.up"), up_uri, tone="apply"),
                ])
            if_box.add_table(
                headers=[],
                widths=["flex", 9],
                rows=down_rows,
                aligns=["left", "right"],
                uniform_row_height=True,
                show_headers=False,
            )
            if_box.add_row(" ")

        cards.append(if_box)

        # =====================================================================
        # CARD 3: WIRELESS (Se presente scheda Wi-Fi)
        # =====================================================================
        if has_wifi_hw and wifi.get("available"):
            w_on = bool(wifi.get("enabled"))
            w_toggle = (
                ActionBtn(t("network.off"), "wifi:radio:off", tone="danger_soft")
                if w_on
                else ActionBtn(t("network.on"), "wifi:radio:on", tone="apply")
            )
            wifi_box = Box(
                t("network.wireless_wi_fi"),
                icon="\uf1eb",
                actions=[
                    w_toggle,
                    ActionBtn("\uf013", "net:open_cc_wifi", tone="neutral"),
                ],
                fixed_width=True,
                min_cols=target_cols,
                max_cols=target_cols,
            )
            wifi_box.add_row(" ")
            if not w_on:
                wifi_box.add_row(f"  {Txt(t('network.wi_fi_radio_is_off'), role='inactive')}")
            else:
                active_net = next((n for n in wifi.get("networks", []) if n.get("in_use")), None)
                if active_net:
                    ssid_s = canvas.truncate_plain(active_net["ssid"], 19)
                    sig_s = f"{active_net.get('signal', 0)}%"
                    w_rows = [[
                        f"{Txt('\u25cf', role='vitality')}  {Txt(ssid_s, role='focus')} {Txt('(' + sig_s + ')', role='schema')}",
                        ActionBtn(t("network.disc_btn"), f"wifi:disc:{active_net['ssid']}", tone="danger_soft"),
                    ]]
                else:
                    w_rows = [[
                        f"{Txt('\u25cb', role='inactive')}  {Txt(t('network.not_connected'), role='inactive')}",
                        ActionBtn(t("network.scan"), "net:open_cc_wifi", tone="neutral"),
                    ]]
                wifi_box.add_table(
                    headers=[],
                    widths=["flex", 9],
                    rows=w_rows,
                    aligns=["left", "right"],
                    uniform_row_height=True,
                    show_headers=False,
                )
            wifi_box.add_row(" ")
            cards.append(wifi_box)

        # =====================================================================
        # CARD 4: BLUETOOTH (Se presente controller BT)
        # =====================================================================
        has_bt_hw = bool(bt.get("available")) and (
            bool(glob.glob("/sys/class/bluetooth/hci*"))
            or bool(bt.get("powered"))
            or bool(bt.get("paired"))
        )
        if has_bt_hw:
            bt_on = bool(bt.get("powered"))
            bt_toggle = (
                ActionBtn(t("network.off"), "bt:power:off", tone="danger_soft")
                if bt_on
                else ActionBtn(t("network.on"), "bt:power:on", tone="apply")
            )
            bt_box = Box(
                t("cc_network.bt_bluez_short"),
                icon="\uf294",
                actions=[
                    bt_toggle,
                    ActionBtn("\uf013", "ui:open_settings:network:wifi_bt", tone="neutral"),
                ],
                fixed_width=True,
                min_cols=target_cols,
                max_cols=target_cols,
            )
            bt_box.add_row(" ")

            if not bt_on:
                bt_box.add_row(f"  {Txt(t('network.controller_off'), role='inactive')}")
            elif not bt.get("paired"):
                bt_box.add_row(f"  {Txt(t('network.no_paired_devices'), role='inactive')}")
            else:
                bt_rows = []
                for idx, dev in enumerate(bt["paired"][:5]):
                    if idx > 0:
                        bt_rows.append([" ", " "])
                    is_conn = bool(dev.get("connected"))
                    dot = Txt("\u25cf" if is_conn else "\u25cb", role="vitality" if is_conn else "inactive")
                    name_short = canvas.truncate_plain(dev["name"], 17)
                    bat_v = dev.get("battery")
                    bat = f" {Txt(str(bat_v) + '%', role='ok_soft')}" if bat_v is not None else ""
                    left_cell = (
                        f"{dot}  {Txt(dev['icon'], role='schema')}  "
                        f"{Txt(name_short, role='focus' if is_conn else 'inactive')}{bat}"
                    )
                    act_btn = (
                        ActionBtn(t("network.disc_btn"), f"bt:disc:{dev['mac']}", tone="danger_soft")
                        if is_conn
                        else ActionBtn(t("network.conn_btn"), f"bt:conn:{dev['mac']}", tone="apply")
                    )
                    bt_rows.append([left_cell, act_btn])

                bt_box.add_table(
                    headers=[],
                    widths=["flex", 9],
                    rows=bt_rows,
                    aligns=["left", "right"],
                    uniform_row_height=True,
                    show_headers=False,
                )

            bt_box.add_row(" ")
            cards.append(bt_box)

        return cards

    def on_link(self, uri: str) -> None:
        if uri == "net:open_cc_wifi":
            STATE.set("control_center", "subtab_network", "wifi_bt")
            if self.shell:
                self.shell.dispatch_uri("ui:open_settings:network:wifi_bt")
            return
        super().on_link(uri)


def create_panel(panel_id: str, shell=None):
    return Panel(shell)
