#!/usr/bin/env python3
import copy
import time
from core.config import CONFIG, THEME, get_network_show_bar_speed
from core.state import STATE
from core.i18n import t, get_active_language
from ui.canvas import Canvas
from ui.kit import (
    Box,
    Txt,
    ActionBtn,
    StateBadge,
)
from ui.markup import format_speed
from panels.cc_network import (
    Panel as _CCBackendPanel,
    _NET_LOCK,
    _NET_STATE,
    get_interfaces_fast,
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
        self.title = t("network.title", "Network & Comms")
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

        is_it = (get_active_language() == "it")

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
            "Uscita WAN & Routing" if is_it else "WAN & Routing",
            icon="\uf0ac",
            actions=[
                ActionBtn(unit_lbl, "net:toggle_unit", tone="neutral"),
                ActionBtn("\uf013", "ui:open_settings:network", tone="select"),
            ],
            fixed_width=True,
            min_cols=target_cols,
            max_cols=target_cols,
        )
        wan_box.add_row(" ")

        wan_ip = str(wan.get("ip", "N/D"))
        wan_badge_txt = str(wan.get("badge", "")).strip()
        wan_b_style = wan.get("badge_style", "info")
        wan_tone = "ok" if wan_b_style == "accent" else ("warn" if wan_b_style == "warn" else "select")
        wan_dot_role = "vitality" if wan_tone == "ok" else ("attention" if wan_tone == "warn" else "intent")
        wan_dot = f"{Txt('\u25cf', role=wan_dot_role)} " if wan_badge_txt else ""

        active_gws = [(i["gw"], i["name"]) for i in ifaces if i["up"] and i["gw"]]
        if active_gws:
            gw_ip, gw_dev = active_gws[0]
            gw_left = f"{Txt('\uf0e8  Gateway', role='schema')}   {Txt(gw_ip, role='focus')}"
            gw_right = Txt(gw_dev, role="schema")
        else:
            gw_left = f"{Txt('\uf0e8  Gateway', role='schema')}   {Txt('Assente' if is_it else 'None', role='inactive')}"
            gw_right = ""

        wan_rows = [
            [
                f"{Txt('\uf0ac  WAN IP', role='schema')}  {wan_dot}{Txt(wan_ip, role='focus')}",
                ActionBtn("Copy", f"ui:copy:{wan_ip}", tone="neutral"),
            ],
            [" ", " "],
            [gw_left, gw_right],
        ]
        if wan_badge_txt:
            wan_rows.insert(
                1,
                [
                    f"   {Txt('Linea:' if is_it else 'Line:', role='schema')}      {StateBadge(True, wan_badge_txt, on_tone=wan_tone)}",
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
            "Interfacce di Rete" if is_it else "Network Interfaces",
            icon="\uf6ff",
            fixed_width=True,
            min_cols=target_cols,
            max_cols=target_cols,
        )
        if_box.add_row(" ")

        if not up_ifaces:
            if_box.add_row(f"  {Txt('Nessuna interfaccia attiva.' if is_it else 'No active interfaces.', role='inactive')}")
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
                    mon_dot = f"{ActionBtn(dot_ch, mon_uri, tone='accent' if is_mon else 'muted', brackets=False)}  "
                    bar_right = Txt("Bar: ON", role="intent") if is_mon else Txt("Bar: OFF", role="inactive")
                else:
                    mon_dot = f"{Txt('\u25cf', role='vitality')}  "
                    bar_right = ""

                hdr_left = f"{mon_dot}{Txt(ic, role='vitality')}  {Txt(iface['name'], role='focus')}{spd_tag}"
                dn_btn = ActionBtn("Down", dn_uri, tone="danger_soft")

                rx_val, tx_val = iface["rx_rate"], iface["tx_rate"]
                rx_s = format_speed(rx_val, unit_mode).strip()
                tx_s = format_speed(tx_val, unit_mode).strip()
                rx_role = "ok_soft" if rx_val > 64.0 else "inactive"
                tx_role = "intent_soft" if tx_val > 64.0 else "inactive"

                ip_right = ActionBtn("Copy", f"ui:copy:{clean_ip}", tone="neutral") if iface["ip"] != "N/D" else ""

                iface_rows = [
                    [hdr_left, dn_btn],
                    [
                        f"   {Txt('IPv4:', role='schema')}  {Txt(iface['ip'], role='focus' if iface['ip'] != 'N/D' else 'inactive')}",
                        ip_right,
                    ],
                    [
                        f"   {Txt('\u2193', role=rx_role)} {Txt(rx_s, role='focus' if rx_val > 64.0 else 'inactive')}"
                        f"    {Txt('\u2191', role=tx_role)} {Txt(tx_s, role='focus' if tx_val > 64.0 else 'inactive')}",
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
            if_box.add_section("Inattive" if is_it else "Inactive")
            if_box.add_row(" ")
            down_rows = []
            for idx, iface in enumerate(down_ifaces):
                if idx > 0:
                    down_rows.append([" ", " "])
                ic = "\uf1eb" if iface["kind"] == "wifi" else ("\uf132" if iface["kind"] == "vpn" else "\uf6ff")
                up_uri = f"net:ifup:{iface['name']}"
                down_rows.append([
                    f"{Txt(f'\u25cb  {ic}', role='inactive')}  {Txt(iface['name'], role='inactive')}",
                    ActionBtn("Up", up_uri, tone="apply"),
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
                ActionBtn("Off", "wifi:radio:off", tone="danger_soft")
                if w_on
                else ActionBtn("On", "wifi:radio:on", tone="apply")
            )
            wifi_box = Box(
                "Wireless (Wi-Fi)",
                icon="\uf1eb",
                actions=[
                    w_toggle,
                    ActionBtn("\uf013", "net:open_cc_wifi", tone="select"),
                ],
                fixed_width=True,
                min_cols=target_cols,
                max_cols=target_cols,
            )
            wifi_box.add_row(" ")
            if not w_on:
                wifi_box.add_row(f"  {Txt('Radio Wi-Fi spenta.' if is_it else 'Wi-Fi radio is off.', role='inactive')}")
            else:
                active_net = next((n for n in wifi.get("networks", []) if n.get("in_use")), None)
                if active_net:
                    ssid_s = canvas.truncate_plain(active_net["ssid"], 19)
                    sig_s = f"{active_net.get('signal', 0)}%"
                    w_rows = [[
                        f"{Txt('\u25cf', role='vitality')}  {Txt(ssid_s, role='focus')} {Txt('(' + sig_s + ')', role='schema')}",
                        ActionBtn("Disc", f"wifi:disc:{active_net['ssid']}", tone="danger_soft"),
                    ]]
                else:
                    w_rows = [[
                        f"{Txt('\u25cb', role='inactive')}  {Txt('Non connesso' if is_it else 'Not connected', role='inactive')}",
                        ActionBtn("Scan..", "net:open_cc_wifi", tone="select"),
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
        # CARD 4: BLUETOOTH
        # =====================================================================
        bt_on = bool(bt.get("powered"))
        bt_toggle = (
            ActionBtn("Off", "bt:power:off", tone="danger_soft")
            if bt_on
            else ActionBtn("On", "bt:power:on", tone="apply")
        )
        bt_box = Box(
            "Bluetooth",
            icon="\uf294",
            actions=[
                bt_toggle,
                ActionBtn("\uf013", "ui:open_settings:bluetooth", tone="select"),
            ],
            fixed_width=True,
            min_cols=target_cols,
            max_cols=target_cols,
        )
        bt_box.add_row(" ")

        if not bt_on:
            bt_box.add_row(f"  {Txt('Controller spento.' if is_it else 'Controller is off.', role='inactive')}")
        elif not bt.get("paired"):
            bt_box.add_row(f"  {Txt('Nessun dispositivo associato.' if is_it else 'No paired devices.', role='inactive')}")
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
                    ActionBtn("Disc", f"bt:disc:{dev['mac']}", tone="danger_soft")
                    if is_conn
                    else ActionBtn("Conn", f"bt:conn:{dev['mac']}", tone="apply")
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
            STATE.set("control_center", "subtab_network", "wireless")
            if self.shell:
                self.shell.dispatch_uri("ui:open_settings:network")
            return
        super().on_link(uri)


def create_panel(panel_id: str, shell=None):
    return Panel(shell)
