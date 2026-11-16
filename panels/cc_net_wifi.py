#!/usr/bin/env python3
import copy

from core.config import get_cc_panel_style
from core.i18n import get_active_language
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
    SIGNAL_BARS,
    _NET_LOCK,
    _NET_STATE,
    _spaced_rows,
    get_interfaces_fast,
)


def _build_wifi_cyberpunk(panel, canvas: Canvas, wifi: dict, is_it: bool) -> list:
    cards = []
    if not wifi["available"]:
        b = Box("802.11 RF SPECTRUM // RADIO & SSID SCANNER", icon="\uf1eb", elected=True)
        b.add_row(" ")
        b.add_row(f"  {Txt('[NMCLI]──► UNAVAILABLE (NetworkManager not installed)', role='inactive')}")
        b.add_row(" ")
        cards.append(b)
        cards.append(Divider("SHORT-RANGE RF // BLUEZ CONTROLLER", icon="\uf294"))
        cards.extend(panel._build_bt_cards(canvas))
        return cards

    wifi_devs = [i["name"] for i in get_interfaces_fast() if i["kind"] == "wifi"]
    primary_wdev = wifi_devs[0] if wifi_devs else ""
    pwr_btn = ActionBtn("RF OFF", "wifi:radio:off", tone="danger_soft") if wifi["enabled"] else ActionBtn("RF ON", "wifi:radio:on", tone="apply", active=True)
    acts = [StateBadge(wifi["enabled"], "TX ON", "TX OFF", on_tone="ok"), pwr_btn]
    if primary_wdev:
        acts.append(ActionBtn(f"IFACE {primary_wdev}", f"net:jump_iface:{primary_wdev}", tone="select"))

    b = Box("802.11 RF // WIFI SPECTRUM", icon="\uf1eb", actions=acts, elected=True)
    b.add_row(" ")
    if not wifi["enabled"]:
        b.add_row(f"  {Txt('[RADIO]──► SOFTWARE RF KILL ACTIVE', role='inactive')}")
    else:
        nets = wifi.get("networks", [])
        active_net = next((n for n in nets if n.get("in_use") or n.get("active")), None)
        b.add_row(
            f"  {Txt('[LINK]  ──►', role='vitality')} {Txt(active_net['ssid'] if active_net else 'DISCONNECTED', role='focus')}",
            right_markup=f"{ActionBtn('+ HIDDEN SSID', 'wifi:prompt_hidden', tone='neutral')} {ActionBtn('HOTSPOT AP', 'wifi:prompt_hotspot', tone='select')} {ActionBtn('RESCAN', 'wifi:rescan', tone='apply')}",
        )
        ps_cur = str(wifi.get("powersave", "unknown"))
        b.add_row(
            f"  {Txt('[PWR]   ──►', role='schema')} {SelectPill('LOW-LATENCY', 'wifi:powersave:2', selected=(ps_cur == 'off'))} {SelectPill('POWERSAVE', 'wifi:powersave:3', selected=(ps_cur == 'on'))}"
        )
        b.add_section(f"DETECTED ACCESS POINTS ({len(nets)})")
        rows = []
        max_bar = max(0, len(SIGNAL_BARS) - 1)
        for n in nets[:8]:
            sig_pct = int(n.get("signal", 0))
            bars = SIGNAL_BARS[min(max_bar, max(0, sig_pct // 25))] if SIGNAL_BARS else f"{sig_pct}%"
            ssid = n["ssid"]
            sec = n.get("security") or "open"
            is_a = bool(n.get("in_use") or n.get("active"))
            act = f"{ActionBtn('DISC', 'wifi:disc:' + ssid, tone='danger_soft')} {ActionBtn('FORGET', 'wifi:forget:' + ssid, tone='neutral')}" if is_a else ActionBtn("LINK", f"wifi:conn:{ssid}:{sec}", tone="apply")
            rows.append([
                f"{Txt('▶━' if is_a else '┣━', role='vitality' if is_a else 'structure')} {Txt(canvas.truncate_plain(ssid, 22), role='focus')}",
                Txt(bars, role="vitality" if n["signal"] > 55 else "attention"),
                Txt(sec[:10], role="schema"),
                act,
            ])
        if rows:
            b.add_table(headers=[], widths=[26, 8, 14, "flex"], rows=rows, aligns=["left", "left", "left", "right"])
        else:
            b.add_row(f"  {Txt('No SSIDs cached — click [RESCAN].', role='inactive')}")

        with _NET_LOCK:
            saved_p = copy.deepcopy(_NET_STATE.get("saved_wifi_profiles", []))
        if saved_p:
            def _fill_saved(bx: Box):
                for sn in saved_p[:8]:
                    suuid = sn["uuid"]
                    ac = (str(sn.get("autoconnect", "yes")).lower() == "yes")
                    ac_v = "no" if ac else "yes"
                    bx.add_row(
                        f"  {Txt('├─', role='structure')} {Txt(sn['name'][:22], role='focus')}",
                        right_markup=f"{SelectPill('AUTO', f'wifi:toggle_auto:{suuid}:{ac_v}', selected=ac)} {ActionBtn('UP', 'wifi:up_uuid:' + suuid, tone='apply')} {ActionBtn('x', 'wifi:del_uuid:' + suuid, tone='danger_soft')}",
                    )
            b.add_collapsible(panel, "wifi_saved_cyber", f"STORED WIFI CREDENTIALS ({len(saved_p)})", _fill_saved)
    b.add_row(" ")
    cards.append(b)
    cards.append(Divider("SHORT-RANGE RF // BLUEZ CONTROLLER", icon="\uf294"))
    cards.extend(panel._build_bt_cards(canvas))
    return cards


def build_wifi_tab_cards(panel, canvas: Canvas) -> list:
    is_it = (get_active_language() == "it")

    with _NET_LOCK:
        wifi = copy.deepcopy(_NET_STATE["wifi"])

    if get_cc_panel_style() == "cyberpunk":
        return _build_wifi_cyberpunk(panel, canvas, wifi, is_it)

    cards = []
    if not wifi["available"]:
        b = Box("Wireless Network (802.11)", icon="\uf1eb")
        b.add_row(" ")
        msg = "NetworkManager (nmcli) non installato sul sistema." if is_it else "NetworkManager (nmcli) is not installed."
        b.add_row(f"  {Txt(msg, role='inactive')}")
        b.add_row(" ")
        cards.append(b)
        cards.append(Divider("Bluetooth (BlueZ)", icon="\uf294"))
        cards.extend(panel._build_bt_cards(canvas))
        return cards

    wifi_devs = [i["name"] for i in get_interfaces_fast() if i["kind"] == "wifi"]
    primary_wdev = wifi_devs[0] if wifi_devs else ""

    # =========================================================================
    # CARD 1: CONTROLLO RADIO 802.11 (+ Opzioni Radio Avanzate Collapsible)
    # =========================================================================
    st_badge = StateBadge(wifi["enabled"], "RADIO ON" if not is_it else "ATTIVA", "SPENTA" if is_it else "OFF", on_tone="ok")
    radio_btn = (
        ActionBtn("Spegni Wi-Fi" if is_it else "Turn Off", "wifi:radio:off", tone="danger_soft")
        if wifi["enabled"]
        else ActionBtn("Accendi Wi-Fi" if is_it else "Turn On", "wifi:radio:on", tone="apply", active=True)
    )
    jump_if_btn = (
        ActionBtn(f"\uf013 IP & Profili ({primary_wdev})" if is_it else f"\uf013 IP & Profiles ({primary_wdev})", f"net:jump_iface:{primary_wdev}", tone="select")
        if primary_wdev
        else ""
    )
    adv_w_open, adv_w_btn = FoldToggle(panel, "wifi_radio_adv", default=False, is_it=is_it)

    ctrl_box = Box(
        "Radio Wireless 802.11" if is_it else "802.11 Wireless Radio",
        icon="\uf1eb",
        actions=[a for a in [st_badge, jump_if_btn, adv_w_btn, radio_btn] if a],
    )
    ctrl_box.add_row(" ")

    if not wifi["enabled"]:
        msg = "Attiva la radio Wi-Fi in alto a destra per scansionare e connetterti alle reti." if is_it else "Enable the Wi-Fi radio above to scan and connect to wireless networks."
        ctrl_box.add_row(f"  {Txt(msg, role='inactive')}")
        ctrl_box.add_row(" ")
        cards.append(ctrl_box)
        cards.append(Divider("Bluetooth (Controller BlueZ & Periferiche)" if is_it else "Bluetooth (BlueZ Controller & Devices)", icon="\uf294"))
        cards.extend(panel._build_bt_cards(canvas))
        return cards

    active_net = next((n for n in wifi.get("networks", []) if n.get("in_use")), None)
    if active_net:
        ap_meta = f"CH {active_net['chan']}  \u00b7  {active_net.get('rate', 'N/D')}  \u00b7  {active_net['signal']}%"
        conn_val = f"{StateBadge(True, active_net['ssid'], on_tone='ok')}   {Txt(ap_meta, role='schema')}"
    else:
        conn_val = StateBadge(False, "", "Non connesso ad alcun Access Point" if is_it else "Not connected to any Access Point")

    hidden_btn = ActionBtn("+ Rete Nascosta.." if is_it else "+ Hidden SSID..", "wifi:prompt_hidden", tone="neutral")
    hotspot_btn = ActionBtn("\uf519 Avvia Hotspot AP.." if is_it else "\uf519 Start Hotspot AP..", "wifi:prompt_hotspot", tone="select")

    ctrl_rows = [
        [Txt("Access Point Attivo" if is_it else "Connected SSID", role="schema"), conn_val],
        [Txt("Azioni Rapide" if is_it else "Quick Actions", role="schema"), f"{hidden_btn}   {hotspot_btn}"],
    ]

    if adv_w_open:
        ps_cur = str(wifi.get("powersave", "default"))
        ps_btns = "  ".join([
            SelectPill("Low-Latency (OFF)", "wifi:powersave:2", selected=(ps_cur == "off")),
            SelectPill("Power-Save (ON)", "wifi:powersave:3", selected=(ps_cur == "on")),
        ])
        ctrl_rows.append([Txt("802.11 Power Save", role="schema"), ps_btns])

        active_saved = next((s for s in wifi.get("saved", []) if s.get("active")), None)
        if active_saved:
            au_uuid = active_saved["uuid"]
            band_btns = "  ".join([
                ActionBtn("Auto (2.4/5GHz)", f"wifi:band:{au_uuid}:auto", tone="select", active=True),
                ActionBtn("5 GHz Only", f"wifi:band:{au_uuid}:a", tone="neutral"),
                ActionBtn("2.4 GHz Only", f"wifi:band:{au_uuid}:bg", tone="neutral"),
            ])
            mac_p_btns = "  ".join([
                ActionBtn("Permanent MAC", f"wifi:mac_priv:{au_uuid}:permanent", tone="neutral"),
                ActionBtn("Stable SSID", f"wifi:mac_priv:{au_uuid}:stable", tone="select", active=True),
                ActionBtn("Random MAC", f"wifi:mac_priv:{au_uuid}:random", tone="neutral"),
            ])
            ctrl_rows.append([Txt("Banda Frequenza" if is_it else "Frequency Band", role="schema"), band_btns])
            ctrl_rows.append([Txt("MAC Privacy", role="schema"), mac_p_btns])

    ctrl_box.add_table(
        headers=[],
        widths=[20, "flex"],
        rows=_spaced_rows(ctrl_rows),
        aligns=["left", "left"],
        uniform_row_height=True,
        show_headers=False,
    )
    ctrl_box.add_row(" ")
    cards.append(ctrl_box)

    # =========================================================================
    # CARD 2: SCANNER ACCESS POINT VISIBILI (Semantica Segnale & Sicurezza)
    # =========================================================================
    nets = wifi.get("networks", [])
    scan_box = Box(
        f"Reti Wi-Fi Rilevate ({len(nets)})" if is_it else f"Visible Wireless Access Points ({len(nets)})",
        icon="\uf012",
        actions=[ActionBtn("\uf021 Scansiona Reti" if is_it else "\uf021 Rescan Networks", "wifi:rescan", tone="select", active=True)],
    )
    scan_box.add_row(" ")

    if not nets:
        msg = "Scansione in corso... Clicca [Scansiona Reti] per aggiornare." if is_it else "Scanning networks... Click [Rescan Networks] to refresh."
        scan_box.add_row(f"  {Txt(msg, role='inactive')}")
    else:
        rows = []
        for n in nets[:16]:
            in_use = bool(n["in_use"])
            ssid_trunc = canvas.truncate_plain(n["ssid"], 19)
            if in_use:
                ssid_col = StateBadge(True, ssid_trunc, on_tone="ok")
            else:
                ssid_col = f"{Txt('\u00a0\u25cb\u00a0', role='inactive')}{Txt(ssid_trunc, role='focus')}"

            rate_short = n.get('rate', 'N/D').replace(' Mbit/s', 'M').replace('MBit/s', 'M')[:8]
            ch_rate = f"CH {n['chan']} \u00b7 {rate_short}"
            sig = int(n["signal"])
            bar_idx = min(3, max(0, int(sig // 26)))
            sig_role = "ok_soft" if sig >= 65 else ("attention" if sig >= 40 else "fault")
            sig_str = Txt(f"{SIGNAL_BARS[bar_idx]} {sig:3d}%", role=sig_role)

            sec_raw = n["security"][:10]
            is_open_ap = sec_raw in ("OPEN", "--", "")
            sec_col = StateBadge(True, "OPEN", on_tone="warn") if is_open_ap else Txt(sec_raw, role="schema")

            if in_use:
                disc_uri = "wifi:disc:" + n["ssid"]
                forget_uri = "wifi:forget:" + n["ssid"]
                act = f"{ActionBtn('Disc', disc_uri, tone='danger_soft')} {ActionBtn('Forget' if not is_it else 'Dimentica', forget_uri, tone='danger_soft')}"
            else:
                conn_uri = "wifi:conn:" + n["ssid"] + ":" + n["security"]
                pass_uri = "ui:prompt:wifi_pass|" + n["ssid"] + ":WPA Password for " + n["ssid"] + ":"
                act = f"{ActionBtn('Conn', conn_uri, tone='apply', active=True)} {ActionBtn('Pass..', pass_uri, tone='neutral')}"

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
                "CH / RATE",
                "SEGNALE" if is_it else "SIGNAL",
                "SECURITY",
                "AZIONI" if is_it else "ACTIONS",
            ],
            widths=[22, 14, 10, 10, "flex"],
            rows=_spaced_rows(rows),
            aligns=["left", "left", "left", "left", "right"],
        )
    scan_box.add_row(" ")
    cards.append(scan_box)

    # =========================================================================
    # CARD 3: RETI WI-FI MEMORIZZATE (A Comparsa / Scomparsa)
    # =========================================================================
    saved_nets = wifi.get("saved", [])
    if saved_nets:
        sv_open, sv_toggle = FoldToggle(panel, "wifi_saved_list", default=False, is_it=is_it)
        s_box = Box(
            f"Reti Wi-Fi Memorizzate ({len(saved_nets)})" if is_it else f"Saved Wi-Fi Networks ({len(saved_nets)})",
            icon="\uf0c7",
            actions=[sv_toggle],
        )
        if not sv_open:
            names_preview = ", ".join(sn["name"] for sn in saved_nets[:4])
            if len(saved_nets) > 4:
                names_preview += f" (+{len(saved_nets) - 4})"
            s_box.add_row(f"  {Txt('SSID:', role='inactive')} {Txt(names_preview, role='val')}")
        else:
            s_box.add_row(" ")
            s_rows = []
            for sn in saved_nets[:12]:
                is_act = bool(sn["active"])
                st_dot = StateBadge(is_act, "ATTIVA" if is_it else "ACTIVE", "SALVATA" if is_it else "SAVED", on_tone="ok")
                is_ac = str(sn["autoconnect"]).lower() == "yes"
                ac_lbl = f"Auto ({sn['priority']})" if is_ac else "Manual"
                ac_toggle = f"wifi:toggle_auto:{sn['uuid']}:{'no' if is_ac else 'yes'}"
                edit_prof_btn = (
                    ActionBtn("\uf013 IP/Prof", f"net:jump_wifi_prof:{primary_wdev}:{sn['uuid']}", tone="select")
                    if primary_wdev
                    else ""
                )
                conn_btn = ActionBtn("Up", "wifi:up_uuid:" + sn["uuid"], tone="apply")
                del_btn = ActionBtn("x", "wifi:del_uuid:" + sn["uuid"], tone="danger_soft")

                s_rows.append([
                    Txt(sn["name"][:22], role="focus" if is_act else "val"),
                    st_dot,
                    ActionBtn(f"\u25cf {ac_lbl}" if is_ac else f"\u25cb {ac_lbl}", ac_toggle, tone="apply" if is_ac else "neutral"),
                    f"{edit_prof_btn} {conn_btn} {del_btn}".strip(),
                ])
            s_box.add_table(
                headers=[
                    "PROFILO SSID" if is_it else "SSID PROFILE",
                    "STATO" if is_it else "STATE",
                    "AUTOCONNECT",
                    "AZIONI" if is_it else "ACTIONS",
                ],
                widths=[24, 11, 14, "flex"],
                rows=_spaced_rows(s_rows),
                aligns=["left", "left", "left", "right"],
            )
            s_box.add_row(" ")
        cards.append(s_box)

    # =========================================================================
    # CARD 4+: SEZIONE BLUETOOTH INTEGRATA NELLA STESSA TAB
    # =========================================================================
    cards.append(Divider("Bluetooth (Controller BlueZ & Periferiche)" if is_it else "Bluetooth (BlueZ Controller & Devices)", icon="\uf294"))
    cards.extend(panel._build_bt_cards(canvas))

    return cards
