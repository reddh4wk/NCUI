#!/usr/bin/env python3
import copy

from core.i18n import t
from ui.canvas import Canvas
from ui.kit import (
    ActionBtn,
    Badge,
    Box,
    Divider,
    SelectPill,
    StateBadge,
    Txt,
    make_feedback_cards,
)
from panels.cc_engine_network import (
    SIGNAL_BARS,
    _NET_LOCK,
    _NET_STATE,
    _spaced_rows,
    get_interfaces_fast,
)


def build_wifi_tab_cards(panel, canvas: Canvas) -> list:
    with _NET_LOCK:
        wifi = copy.deepcopy(_NET_STATE["wifi"])

    cards = []
    if not wifi["available"]:
        b = Box(
            t("cc_net_wifi.wireless_network_802_11"),
            icon="\uf1eb",
            actions=[Badge(t("ui.missing", p0="nmcli"), style="warn")],
        )
        b.add_row(f"  {Txt(t('cc_net_wifi.networkmanager_nmcli_is_not_instal'), role='inactive')}")
        cards.append(b)
        cards.append(Divider(t("cc_net_wifi.bluetooth_bluez_controller_devices"), icon="\uf294"))
        cards.extend(panel._build_bt_cards(canvas))
        cards.extend(make_feedback_cards("network", title_key="cc_net_wired.networkmanager_operation_status", key="fb_wifi_bt"))
        return cards

    wifi_devs = [i["name"] for i in get_interfaces_fast() if i["kind"] == "wifi"]
    primary_wdev = wifi_devs[0] if wifi_devs else ""

    if not wifi_devs:
        b = Box(
            t("cc_net_wifi.wireless_network_802_11"),
            icon="\uf1eb",
            actions=[StateBadge(False, "", t("cc_net_wifi.no_adapter_badge"))],
        )
        b.add_row(f"  {Txt(t('cc_net_wifi.no_wifi_adapter'), role='inactive')}")
        cards.append(b)
        cards.append(Divider(t("cc_net_wifi.bluetooth_bluez_controller_devices"), icon="\uf294"))
        cards.extend(panel._build_bt_cards(canvas))
        cards.extend(make_feedback_cards("network", title_key="cc_net_wired.networkmanager_operation_status", key="fb_wifi_bt"))
        return cards

    # =========================================================================
    # CARD 1: CONTROLLO RADIO 802.11 (+ Opzioni Radio Avanzate Collapsible)
    # =========================================================================
    st_badge = StateBadge(wifi["enabled"], t("cc_net_wifi.radio_on"), t("cc_net_wifi.off"), on_tone="ok")
    radio_btn = (
        ActionBtn(t("cc_net_wifi.turn_off"), "wifi:radio:off", tone="danger_soft")
        if wifi["enabled"]
        else ActionBtn(t("cc_net_wifi.turn_on"), "wifi:radio:on", tone="apply")
    )
    jump_if_btn = ActionBtn(t("cc_net_wifi.ip_profiles", p0=primary_wdev), f"net:jump_iface:{primary_wdev}", tone="neutral")
    adv_w_open, adv_w_btn = panel._fold("wifi_radio_adv", default=False)

    ctrl_box = Box(
        t("cc_net_wifi.802_11_wireless_radio"),
        icon="\uf1eb",
        actions=[a for a in [st_badge, jump_if_btn, radio_btn] if a],
    )

    if not wifi["enabled"]:
        ctrl_box.add_row(f"  {Txt(t('cc_net_wifi.enable_the_wi_fi_radio_above_to_sc'), role='inactive')}")
        cards.append(ctrl_box)
        cards.append(Divider(t("cc_net_wifi.bluetooth_bluez_controller_devices"), icon="\uf294"))
        cards.extend(panel._build_bt_cards(canvas))
        cards.extend(make_feedback_cards("network", title_key="cc_net_wired.networkmanager_operation_status", key="fb_wifi_bt"))
        return cards

    active_net = next((n for n in wifi.get("networks", []) if n.get("in_use")), None)
    if active_net:
        ap_meta = f"CH {active_net['chan']}  \u00b7  {active_net.get('rate', 'N/D')}  \u00b7  {active_net['signal']}%"
        conn_val = f"{StateBadge(True, active_net['ssid'], on_tone='ok')}   {Txt(ap_meta, role='schema')}"
    else:
        conn_val = StateBadge(False, "", t("cc_net_wifi.not_connected_to_any_access_point"))

    hidden_btn = ActionBtn(t("cc_net_wifi.hidden_ssid"), "wifi:prompt_hidden", tone="neutral")
    hotspot_btn = ActionBtn(t("cc_net_wifi.start_hotspot_ap"), "wifi:prompt_hotspot", tone="apply")

    ctrl_rows = [
        [Txt(t("cc_net_wifi.connected_ssid"), role="schema"), conn_val],
        [Txt(t("cc_net_wifi.quick_actions"), role="schema"), f"{hidden_btn}   {hotspot_btn}"],
    ]

    ctrl_box.add_table(
        headers=[],
        widths=[20, "flex"],
        rows=_spaced_rows(ctrl_rows),
        aligns=["left", "left"],
        uniform_row_height=True,
        show_headers=False,
    )
    ctrl_box.add_section(t("cc_net_wifi.advanced_radio_options"), actions=[adv_w_btn])
    if adv_w_open:
        ps_cur = str(wifi.get("powersave", "default"))
        ps_btns = "  ".join([
            SelectPill(t("cc_net_wifi.low_latency_off"), "wifi:powersave:2", selected=(ps_cur == "off")),
            SelectPill(t("cc_net_wifi.power_save_on"), "wifi:powersave:3", selected=(ps_cur == "on")),
        ])
        ctrl_box.add_kv(t("cc_net_wifi.802_11_power_save"), ps_btns, key_width=20, requires=["iw"])

        active_saved = next((s for s in wifi.get("saved", []) if s.get("active")), None)
        if active_saved:
            au_uuid = active_saved["uuid"]
            cur_band = str(wifi.get("active_band", "auto") or "auto")
            cur_macp = str(wifi.get("active_mac_priv", "") or "")
            band_btns = "  ".join([
                SelectPill(t("cc_net_wifi.auto_2_4_5ghz"), f"wifi:band:{au_uuid}:auto", selected=(cur_band == "auto")),
                SelectPill(t("cc_net_wifi.5_ghz_only"), f"wifi:band:{au_uuid}:a", selected=(cur_band == "a")),
                SelectPill(t("cc_net_wifi.2_4_ghz_only"), f"wifi:band:{au_uuid}:bg", selected=(cur_band == "bg")),
            ])
            mac_p_btns = "  ".join([
                SelectPill(t("cc_net_wifi.permanent_mac"), f"wifi:mac_priv:{au_uuid}:permanent", selected=(cur_macp == "permanent")),
                SelectPill(t("cc_net_wifi.stable_ssid"), f"wifi:mac_priv:{au_uuid}:stable", selected=(cur_macp == "stable")),
                SelectPill(t("cc_net_wifi.random_mac"), f"wifi:mac_priv:{au_uuid}:random", selected=(cur_macp == "random")),
            ])
            ctrl_box.add_kv(t("cc_net_wifi.frequency_band"), band_btns, key_width=20)
            ctrl_box.add_kv(t("cc_net_wifi.mac_privacy"), mac_p_btns, key_width=20)
    cards.append(ctrl_box)

    # =========================================================================
    # CARD 2: SCANNER ACCESS POINT VISIBILI (Semantica Segnale & Sicurezza)
    # =========================================================================
    nets = wifi.get("networks", [])
    scan_box = Box(
        t("cc_net_wifi.visible_wireless_access_points", p0=len(nets)),
        icon="\uf012",
        actions=[ActionBtn(t("cc_net_wifi.rescan_networks"), "wifi:rescan", tone="neutral")],
    )

    if not nets:
        scan_box.add_row(f"  {Txt(t('cc_net_wifi.scanning_networks_click_rescan_net'), role='inactive')}")
    else:
        rows = []
        for n in nets[:16]:
            in_use = bool(n["in_use"])
            ssid_trunc = canvas.truncate_plain(n["ssid"], 19)
            if in_use:
                ssid_col = StateBadge(True, ssid_trunc, on_tone="ok")
            else:
                ssid_col = f"{Txt('\u00a0\u25cb\u00a0', role='inactive')}{Txt(ssid_trunc, role='focus')}"

            rate_short = n.get("rate", "N/D").replace(" Mbit/s", "M").replace("MBit/s", "M")[:8]
            ch_rate = f"CH {n['chan']} \u00b7 {rate_short}"
            sig = int(n["signal"])
            bar_idx = min(3, max(0, int(sig // 26)))
            sig_role = "ok_soft" if sig >= 65 else ("attention" if sig >= 40 else "fault")
            sig_str = Txt(f"{SIGNAL_BARS[bar_idx]} {sig:3d}%", role=sig_role)

            sec_raw = n["security"][:10]
            is_open_ap = sec_raw in ("OPEN", "--", "")
            sec_col = StateBadge(True, t("cc_net_wifi.open"), on_tone="warn") if is_open_ap else Txt(sec_raw, role="schema")

            if in_use:
                disc_uri = "wifi:disc:" + n["ssid"]
                forget_uri = "wifi:forget:" + n["ssid"]
                act = f"{ActionBtn(t('cc_net_wifi.disc'), disc_uri, tone='danger_soft')} {ActionBtn(t('cc_net_wifi.forget'), forget_uri, tone='danger_soft')}"
            elif n.get("hidden"):
                act = ActionBtn(t("cc_net_wifi.conn"), "wifi:prompt_hidden", tone="apply")
            else:
                conn_uri = "wifi:conn:" + n["ssid"] + ":" + n["security"]
                pass_uri = f"wifi:prompt_pass:{n['ssid']}"
                act = f"{ActionBtn(t('cc_net_wifi.conn'), conn_uri, tone='apply')} {ActionBtn(t('cc_net_wifi.pass'), pass_uri, tone='neutral')}"

            rows.append([
                ssid_col,
                Txt(ch_rate, role="schema"),
                sig_str,
                sec_col,
                act,
            ])

        scan_box.add_table(
            headers=[
                "SSID",
                t("cc_net_wifi.ch_rate"),
                t("cc_net_wifi.signal"),
                t("cc_net_wifi.security"),
                t("cc_net_wifi.actions"),
            ],
            widths=[22, 14, 10, 10, "flex"],
            rows=_spaced_rows(rows),
            aligns=["left", "left", "left", "left", "right"],
        )
    cards.append(scan_box)

    # =========================================================================
    # CARD 3: RETI WI-FI MEMORIZZATE (A Comparsa / Scomparsa)
    # =========================================================================
    saved_nets = wifi.get("saved", [])
    if saved_nets:
        sv_open, sv_toggle = panel._fold("wifi_saved_list", default=False)
        s_box = Box(
            t("cc_net_wifi.saved_wi_fi_networks", p0=len(saved_nets)),
            icon="\uf0c7",
            actions=[sv_toggle],
        )
        if not sv_open:
            names_preview = ", ".join(sn["name"] for sn in saved_nets[:4])
            if len(saved_nets) > 4:
                names_preview += f" (+{len(saved_nets) - 4})"
            s_box.add_row(f"  {Txt(t('cc_net_wifi.ssid'), role='inactive')} {Txt(names_preview, role='val')}")
        else:
            s_rows = []
            for sn in saved_nets[:12]:
                is_act = bool(sn["active"])
                st_dot = StateBadge(is_act, t("cc_net_wifi.active"), t("cc_net_wired.standby"), on_tone="ok")
                is_ac = str(sn["autoconnect"]).lower() == "yes"
                ac_lbl = t("cc_net_wifi.auto_prio", p0=sn["priority"]) if is_ac else t("cc_net_wifi.manual_lbl")
                ac_toggle = f"wifi:toggle_auto:{sn['uuid']}:{'no' if is_ac else 'yes'}"
                edit_prof_btn = ActionBtn(t("cc_net_wifi.ip_prof"), f"net:jump_wifi_prof:{primary_wdev}:{sn['uuid']}", tone="neutral")
                conn_btn = ActionBtn(t("cc_net_wifi.up"), "wifi:up_uuid:" + sn["uuid"], tone="apply")
                del_btn = ActionBtn("x", "wifi:del_uuid:" + sn["uuid"], tone="danger_soft")

                s_rows.append([
                    Txt(sn["name"][:22], role="focus" if is_act else "val"),
                    st_dot,
                    SelectPill(ac_lbl, ac_toggle, selected=is_ac),
                    f"{edit_prof_btn} {conn_btn} {del_btn}".strip(),
                ])
            s_box.add_table(
                headers=[
                    t("cc_net_wifi.ssid_profile"),
                    t("cc_net_wifi.state"),
                    t("cc_net_wifi.autoconnect"),
                    t("cc_net_wifi.actions"),
                ],
                widths=[24, 11, 14, "flex"],
                rows=_spaced_rows(s_rows),
                aligns=["left", "left", "left", "right"],
            )
        cards.append(s_box)

    # =========================================================================
    # CARD 4+: SEZIONE BLUETOOTH INTEGRATA NELLA STESSA TAB
    # =========================================================================
    cards.append(Divider(t("cc_net_wifi.bluetooth_bluez_controller_devices"), icon="\uf294"))
    cards.extend(panel._build_bt_cards(canvas))
    cards.extend(make_feedback_cards("network", title_key="cc_net_wired.networkmanager_operation_status", key="fb_wifi_bt"))

    return cards


def build_cards(cc_panel, canvas: Canvas) -> list:
    from panels.cc_engine_network import build_cc_network_cards
    return build_cc_network_cards(cc_panel, canvas, "wifi_bt")
