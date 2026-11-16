#!/usr/bin/env python3
import copy

from core.config import get_network_show_bar_speed
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
    InspectorCell,
)
from ui.markup import format_speed, format_bytes_short
from panels.cc_network import (
    _NET_LOCK,
    _NET_STATE,
    _get_feedback,
    _spaced_rows,
)


def build_wired_tab_cards(
    panel,
    canvas: Canvas,
    ifaces: list[dict],
    mon_iface: str,
    sel_iface: str,
    unit_mode: str,
) -> list:
    is_it = (get_active_language() == "it")
    show_bar_spd = get_network_show_bar_speed()

    cur_filter = str(panel.get_state("iface_filter", "all") or "all")
    if cur_filter == "wired":
        filtered_ifaces = [i for i in ifaces if i["kind"] != "wifi"]
    elif cur_filter == "wifi":
        filtered_ifaces = [i for i in ifaces if i["kind"] == "wifi"]
    else:
        filtered_ifaces = list(ifaces)

    if not filtered_ifaces and ifaces:
        filtered_ifaces = list(ifaces)
        cur_filter = "all"

    if filtered_ifaces and sel_iface not in [i["name"] for i in filtered_ifaces]:
        sel_iface = filtered_ifaces[0]["name"]

    cards = []

    # =========================================================================
    # CARD 0: ELENCO INTERFACCE & TELEMETRIA LIVE (Allineata e legata a show_bar_speed)
    # =========================================================================
    flt_all = SelectPill("Tutte" if is_it else "All", "net:if_filter:all", selected=(cur_filter == "all"))
    flt_eth = SelectPill("Cablate/Virt" if is_it else "Wired/Virt", "net:if_filter:wired", selected=(cur_filter == "wired"))
    flt_wifi = SelectPill("Wi-Fi", "net:if_filter:wifi", selected=(cur_filter == "wifi"))
    virt_open, virt_btn = FoldToggle(panel, "wired_virt", default=False, is_it=is_it)

    if_box = Box(
        "Interfacce di Rete & Telemetria Live" if is_it else "Network Interfaces & Live Telemetry",
        icon="\uf6ff",
        actions=[
            flt_all,
            flt_eth,
            flt_wifi,
            ActionBtn(f"Unit: {unit_mode.upper()}", "net:toggle_unit", tone="neutral"),
            virt_btn,
        ],
    )
    if_box.add_row(" ")

    if not filtered_ifaces:
        if_box.add_row(f"  {Txt('Nessuna interfaccia di rete rilevata.' if is_it else 'No network interfaces detected.', role='inactive')}")
        if_box.add_row(" ")
        return [if_box]

    if_rows = []
    for iface in filtered_ifaces:
        name = iface["name"]
        is_sel = (name == sel_iface)
        is_mon = (name == mon_iface)
        is_up = bool(iface["up"])
        ic = "\uf1eb" if iface["kind"] == "wifi" else ("\uf132" if iface["kind"] == "vpn" else ("\uf0e8" if iface["kind"] == "bridge" else "\uf6ff"))

        # Colonna 1: Selezione UI (Ciano su Blu Cobalto se in ispezione, altrimenti Bianco/Grafite)
        dev_str = InspectorCell(name[:11], f"net:sel_iface:{name}", selected=is_sel, is_alive=is_up, icon=ic)

        # Colonna 2: Stato Fisico Link (Verde Matrix se UP, Grigio Spento se DOWN)
        spd_s = iface.get("link_speed", "N/D")
        spd_tag = f" {spd_s[:6]}" if (is_up and spd_s and spd_s != "N/D") else ""
        st_link = StateBadge(is_up, f"UP{spd_tag}", "DOWN", on_tone="ok")

        # Colonna 3: Indirizzo IPv4 (Bianco Puro se assegnato, Grigio Spento se N/D)
        has_ip = (iface["ip"] != "N/D")
        ip_col = Txt(iface["ip"][:18], role="focus" if has_ip else "inactive")

        # Colonna 4: Traffico (Verde Fosforo per RX attivo, Ciano attenuato per TX attivo, Grafite se 0)
        rx_val, tx_val = iface["rx_rate"], iface["tx_rate"]
        rx_s = format_speed(rx_val, unit_mode).strip()
        tx_s = format_speed(tx_val, unit_mode).strip()
        rx_role = "ok_soft" if rx_val > 64.0 else "inactive"
        tx_role = "intent_soft" if tx_val > 64.0 else "inactive"
        traffic_col = (
            f"{Txt(f'\u2193\u00a0{rx_s:<8}', role=rx_role)} "
            f"{Txt(f'\u2191\u00a0{tx_s:<8}', role=tx_role)}"
        )

        # Colonna 5: Azioni ([Bar] mostrato SOLO se show_bar_speed è attivo)
        updn_b = (
            ActionBtn("Down", f"net:ifdown:{name}", tone="danger_soft")
            if is_up
            else ActionBtn("Up", f"net:ifup:{name}", tone="apply")
        )
        if show_bar_spd:
            mon_b = SelectPill("Bar", f"net:mon:{name}", selected=is_mon)
            act_cell = f"{mon_b} {updn_b}"
        else:
            act_cell = updn_b

        if_rows.append([dev_str, st_link, ip_col, traffic_col, act_cell])

    if_box.add_table(
        headers=[
            "INTERFACCIA" if is_it else "INTERFACE",
            "STATO / LINK" if is_it else "STATE / LINK",
            "IPv4 / CIDR",
            "TRAFFICO" if is_it else "TRAFFIC",
            "AZIONI" if is_it else "ACTIONS",
        ],
        widths=[16, 13, 18, 22, "flex"],
        rows=_spaced_rows(if_rows),
        aligns=["left", "left", "left", "left", "right"],
    )

    if virt_open:
        if_box.add_section("Creazione Interfacce Virtuali (Bridge / VLAN 802.1Q)" if is_it else "Virtual Interfaces (Bridge / 802.1Q VLAN)")
        if_box.add_row(
            f"  {Txt('Crea interfaccia virtuale NM:' if is_it else 'Create virtual NM interface:', role='schema')}",
            right_markup=(
                f"{ActionBtn('+ Bridge (br0)..', 'net:prompt_virtual', tone='select', active=True)}  "
                f"{ActionBtn('+ VLAN 802.1Q..', 'net:prompt_vlan', tone='neutral')}"
            ),
        )

    if_box.add_row(" ")
    cards.append(if_box)

    sel_obj = next((i for i in ifaces if i["name"] == sel_iface), ifaces[0])
    is_wifi_iface = (sel_obj["kind"] == "wifi")

    with _NET_LOCK:
        nm_cur = copy.deepcopy(_NET_STATE["nm_profiles"].get(sel_iface, {}))

    buf_all = panel.get_state("edit_buffer", {})
    buf = copy.deepcopy(buf_all.get(sel_iface, {}) if isinstance(buf_all, dict) else {})
    fb = _get_feedback(sel_iface)

    def _eff(key: str, default=""):
        return buf[key] if key in buf else nm_cur.get(key, default)

    def _is_dirty(key: str) -> bool:
        return key in buf and buf[key] != nm_cur.get(key)

    # Banner di feedback operazione (Verde Matrix per OK, Rosso Allarme per Errore)
    if fb.get("state") in ("ok", "err", "err_banner") and fb.get("msg"):
        is_ok = (fb["state"] == "ok")
        fb_role = "vitality" if is_ok else "fault"
        fb_ic = "\uf00c" if is_ok else "\uf071"
        fb_box = Box(
            "Esito Operazione NetworkManager" if is_it else "NetworkManager Operation Status",
            icon=fb_ic,
        )
        fb_box.add_row(f"  {Txt(' ' + fb['msg'] + ' ', role=fb_role, bg=True)}")
        cards.append(fb_box)

    # =========================================================================
    # CARD 1: PROFILI NETWORKMANAGER ASSOCIATI ALL'INTERFACCIA SELEZIONATA
    # =========================================================================
    cards.append(
        Divider(
            f"Ispezione Interfaccia: {sel_iface} ({sel_obj['kind'].upper()})"
            if is_it
            else f"Inspecting Interface: {sel_iface} ({sel_obj['kind'].upper()})",
            icon="\uf1eb" if is_wifi_iface else "\uf085",
        )
    )

    avail_profs = nm_cur.get("available_profiles", [])
    sel_uuid = nm_cur.get("uuid", "")

    prof_actions = [
        ActionBtn("+ Nuovo Profilo.." if is_it else "+ New Profile..", f"net:nm_prompt_new:{sel_iface}", tone="apply"),
    ]
    if is_wifi_iface:
        prof_actions.insert(0, ActionBtn("\uf1eb Scanner Wi-Fi ->" if is_it else "\uf1eb Wi-Fi Scanner ->", "net:jump_wifi_tab", tone="select"))

    prof_box = Box(
        f"1. Profili NetworkManager — {sel_iface} ({len(avail_profs)})"
        if is_it
        else f"1. Associated NetworkManager Profiles — {sel_iface} ({len(avail_profs)})",
        icon="\uf0c7",
        actions=prof_actions,
    )
    prof_box.add_row(" ")

    if not nm_cur.get("nm_running", True):
        prof_box.add_row(f"  {Txt('Servizio NetworkManager non in esecuzione.' if is_it else 'NetworkManager service is not running.', role='fault')}")
        prof_box.add_row(" ")
        cards.append(prof_box)
        return cards

    if not avail_profs:
        prof_box.add_row(
            f"  {Txt('Nessun profilo persistente trovato per questa interfaccia.' if is_it else 'No persistent NetworkManager profile found for this interface.', role='attention')}",
            right_markup=ActionBtn("+ Crea Profilo Default" if is_it else "+ Create Default Profile", f"net:nm_create:{sel_iface}", tone="apply", active=True),
        )
        prof_box.add_row(" ")
        cards.append(prof_box)
        return cards

    p_rows = []
    for pr in avail_profs:
        p_uuid = pr["uuid"]
        is_editing = (p_uuid == sel_uuid)
        is_live = bool(pr.get("active"))
        is_ac = (str(pr.get("autoconnect", "yes")).lower() == "yes")
        prio_s = str(pr.get("priority", 0))

        name_cell = InspectorCell(
            canvas.truncate_plain(pr["name"], 20),
            f"net:nm_sel_prof:{sel_iface}:{p_uuid}",
            selected=is_editing,
            is_alive=is_live,
        )
        live_cell = StateBadge(is_live, "ATTIVO" if is_it else "ACTIVE", "STANDBY", on_tone="ok")
        ac_lbl = f"Auto ({prio_s})" if is_ac else "Manual"
        ac_btn = ActionBtn(
            f"\u25cf {ac_lbl}" if is_ac else f"\u25cb {ac_lbl}",
            f"net:nm_auto_prof:{sel_iface}:{p_uuid}:{'no' if is_ac else 'yes'}",
            tone="apply" if is_ac else "neutral",
        )

        act_updn = (
            ActionBtn("Down", f"net:nm_down_prof:{sel_iface}:{p_uuid}", tone="danger_soft")
            if is_live
            else ActionBtn("Attiva" if is_it else "Up", f"net:nm_up_prof:{sel_iface}:{p_uuid}", tone="apply")
        )
        clone_b = ActionBtn("Clona" if is_it else "Clone", f"net:nm_clone_prof:{sel_iface}:{p_uuid}", tone="neutral")
        ren_b = ActionBtn("Rinomina" if is_it else "Rename", f"net:nm_rename_prof:{sel_iface}:{p_uuid}", tone="neutral")
        del_b = ActionBtn("x", f"net:nm_del_prof:{sel_iface}:{p_uuid}", tone="danger_soft")

        p_rows.append([
            name_cell,
            live_cell,
            ac_btn,
            Txt(p_uuid[:8], role="inactive"),
            f"{act_updn} {clone_b} {ren_b} {del_b}",
        ])

    prof_box.add_table(
        headers=[
            "PROFILO (EDIT)" if is_it else "PROFILE (EDIT)",
            "STATO" if is_it else "STATE",
            "AUTOCONN",
            "UUID",
            "AZIONI PROFILO" if is_it else "PROFILE ACTIONS",
        ],
        widths=[22, 11, 14, 9, "flex"],
        rows=_spaced_rows(p_rows),
        aligns=["left", "left", "left", "left", "right"],
    )
    prof_box.add_row(" ")
    cards.append(prof_box)

    # =========================================================================
    # CARD 2: CONFIGURAZIONE IPv4 & PARAMETRI PRINCIPALI (+ Avanzate Collapsible)
    # =========================================================================
    has_dirty = bool(buf)
    con_name = str(_eff("con_name", sel_iface))
    method = str(_eff("method", "auto"))
    ip_cidr = str(_eff("ip_cidr", ""))
    gw = str(_eff("gateway", ""))
    dns = str(_eff("dns", ""))
    ignore_auto_dns = str(_eff("ignore_auto_dns", "no"))
    dns_search = str(_eff("dns_search", ""))
    route_metric = _eff("route_metric", -1)
    never_default = str(_eff("never_default", "no"))
    zone = str(_eff("zone", ""))
    metered = str(_eff("metered", "unknown"))
    autoconn = str(_eff("autoconnect", "yes"))
    prio = _eff("autoconnect_priority", 0)
    static_routes = list(_eff("static_routes", []))

    v4_actions = []
    if has_dirty:
        v4_actions.append(StateBadge(True, "MODIFICHE NON SALVATE" if is_it else "UNSAVED CHANGES", on_tone="warn"))
        v4_actions.append(ActionBtn("Reset", f"net:nm_reset:{sel_iface}", tone="warn", active=True))
    v4_actions.append(
        ActionBtn(
            "\uf00c Applica & Verifica" if is_it else "\uf00c Apply & Verify",
            f"net:nm_apply:{sel_iface}:{sel_uuid}",
            tone="apply",
            active=has_dirty,
        )
    )

    v4_box = Box(
        f"2. Indirizzamento IPv4 & DNS — {con_name}" if is_it else f"2. IPv4 Addressing & DNS — {con_name}",
        icon="\uf124",
        actions=v4_actions,
    )
    v4_box.add_row(" ")

    m_opts = [
        ("auto", "DHCP Auto"),
        ("manual", "Statico / Manuale" if is_it else "Static / Manual"),
        ("shared", "Shared (NAT)"),
        ("disabled", "Off"),
    ]
    m_btns = "  ".join(
        SelectPill(lbl, f"net:buf:{sel_iface}:method:{k}", selected=(method == k), dirty=(method == k and _is_dirty("method")))
        for k, lbl in m_opts
    )

    ac_yes = (autoconn.lower() == "yes")
    ac_btns = "  ".join([
        SelectPill("ON", f"net:buf:{sel_iface}:autoconnect:yes", selected=ac_yes, dirty=(ac_yes and _is_dirty("autoconnect"))),
        SelectPill("OFF", f"net:buf:{sel_iface}:autoconnect:no", selected=not ac_yes, dirty=(not ac_yes and _is_dirty("autoconnect"))),
        ActionBtn(
            f"Prio: {prio}..",
            f"ui:prompt:net_prio|{sel_iface}:Autoconnect priority (-999..999):{prio}",
            tone="warn" if _is_dirty("autoconnect_priority") else "neutral",
            active=_is_dirty("autoconnect_priority"),
        ),
    ])

    v4_box.add_table(
        headers=[],
        widths=[18, "flex"],
        rows=_spaced_rows([
            [Txt("Metodo IPv4" if is_it else "IPv4 Method", role="schema"), m_btns],
            [Txt("Autoconnect", role="schema"), ac_btns],
        ]),
        aligns=["left", "left"],
        uniform_row_height=True,
        show_headers=False,
    )
    v4_box.add_row(" ")

    def _fmt_val(text_str: str, dirty_flag: bool, is_auto_fallback: bool = False) -> str:
        s_trunc = str(text_str)[:32]
        if dirty_flag:
            return Txt(f" {s_trunc} ", role="attention", bg=True)
        if is_auto_fallback:
            return Txt(s_trunc, role="schema")
        return Txt(s_trunc, role="focus")

    live_ip_hint = f"DHCP ({sel_obj['ip']})" if sel_obj["ip"] != "N/D" else "DHCP Auto"
    ip_disp = ip_cidr if ip_cidr else (live_ip_hint if method == "auto" else ("Non impostato" if is_it else "Not set"))
    gw_disp = gw if gw else (f"DHCP ({sel_obj['gw']})" if (method == "auto" and sel_obj["gw"]) else (sel_obj["gw"] or ("Automatico" if is_it else "Auto")))
    dns_disp = dns if dns else ("Automatico (DHCP)" if is_it else "Automatic (DHCP)")

    ign_yes = (ignore_auto_dns.lower() == "yes")
    auto_dns_pill = SelectPill(
        "Solo DNS Manuali" if is_it else "Ignore Auto DNS",
        f"net:buf:{sel_iface}:ignore_auto_dns:{'no' if ign_yes else 'yes'}",
        selected=ign_yes,
        dirty=_is_dirty("ignore_auto_dns"),
    )

    clean_live_ip = sel_obj["ip"].split("/")[0]
    param_rows = [
        [
            Txt("IPv4 / CIDR", role="schema"),
            _fmt_val(ip_disp, _is_dirty("ip_cidr"), is_auto_fallback=(not ip_cidr)),
            f"{ActionBtn('Modifica..' if is_it else 'Edit..', f'ui:prompt:net_ip|{sel_iface}:IPv4/CIDR (e.g. 10.10.0.10/24):{ip_cidr}', tone='select' if method == 'manual' else 'neutral', active=(method == 'manual'))} "
            f"{ActionBtn('Copy', 'ui:copy:' + clean_live_ip, tone='neutral')}",
        ],
        [
            Txt("Gateway IPv4", role="schema"),
            _fmt_val(gw_disp, _is_dirty("gateway"), is_auto_fallback=(not gw)),
            f"{ActionBtn('Modifica..' if is_it else 'Edit..', f'ui:prompt:net_gw|{sel_iface}:IPv4 Gateway (e.g. 10.10.0.1):{gw}', tone='neutral')} "
            f"{ActionBtn('Clear', f'net:buf:{sel_iface}:gateway:', tone='danger_soft') if gw else ''}".strip(),
        ],
        [
            Txt("DNS Servers", role="schema"),
            _fmt_val(dns_disp, _is_dirty("dns"), is_auto_fallback=(not dns)),
            f"{auto_dns_pill} {ActionBtn('Modifica..' if is_it else 'Edit..', f'ui:prompt:net_dns|{sel_iface}:Comma-separated IPv4 DNS:{dns}', tone='neutral')}",
        ],
    ]
    v4_box.add_table(
        headers=[
            "PARAMETRO" if is_it else "PARAMETER",
            "VALORE ATTUALE" if is_it else "CURRENT VALUE",
            "AZIONI" if is_it else "ACTIONS",
        ],
        widths=[18, 32, "flex"],
        rows=_spaced_rows(param_rows),
        aligns=["left", "left", "right"],
    )

    adv_dirty = any(_is_dirty(k) for k in ("dns_search", "route_metric", "never_default", "zone", "metered", "static_routes"))
    adv_open, adv_toggle = FoldToggle(panel, f"v4_adv_{sel_iface}", default=False, is_it=is_it, dirty=adv_dirty)

    v4_box.add_section(
        "Opzioni Avanzate Routing, Firewall & Rotte Statiche" if is_it else "Advanced Routing, Firewall & Static Routes",
        actions=[adv_toggle],
    )
    if not adv_open:
        z_sum = zone or "Default"
        m_sum = f"{route_metric}" if int(route_metric) >= 0 else "Auto"
        r_sum = str(len(static_routes))
        v4_box.add_row(
            f"  {Txt('Firewall Zone:', role='inactive')} {Txt(z_sum, role='val')}  \u00b7  "
            f"{Txt('Metric:', role='inactive')} {Txt(m_sum, role='val')}  \u00b7  "
            f"{Txt('Static Routes:', role='inactive')} {Txt(r_sum, role='val')}"
        )
    else:
        v4_box.add_row(" ")
        nd_yes = (never_default.lower() == "yes")
        nd_btns = "  ".join([
            SelectPill("Default Route: OK", f"net:buf:{sel_iface}:never_default:no", selected=not nd_yes, dirty=(not nd_yes and _is_dirty("never_default"))),
            SelectPill("No Default Route (Split)", f"net:buf:{sel_iface}:never_default:yes", selected=nd_yes, dirty=(nd_yes and _is_dirty("never_default"))),
        ])

        z_opts = [("", "Default"), ("trusted", "Trusted"), ("home", "Home"), ("work", "Work"), ("public", "Public"), ("drop", "Drop")]
        z_btns = " ".join(
            SelectPill(lbl, f"net:buf:{sel_iface}:zone:{zk}", selected=(zone == zk), dirty=(zone == zk and _is_dirty("zone")))
            for zk, lbl in z_opts
        )

        met_opts = [("unknown", "Auto"), ("yes", "Metered (Limit)"), ("no", "Unmetered")]
        met_btns = "  ".join(
            SelectPill(lbl, f"net:buf:{sel_iface}:metered:{mk}", selected=(metered == mk), dirty=(metered == mk and _is_dirty("metered")))
            for mk, lbl in met_opts
        )

        v4_box.add_table(
            headers=[],
            widths=[18, "flex"],
            rows=_spaced_rows([
                [Txt("Default Route", role="schema"), nd_btns],
                [Txt("Firewalld Zone", role="schema"), z_btns],
                [Txt("Rete a Consumo" if is_it else "Metered Link", role="schema"), met_btns],
            ]),
            aligns=["left", "left"],
            uniform_row_height=True,
            show_headers=False,
        )
        v4_box.add_row(" ")

        search_disp = dns_search if dns_search else ("Nessuno" if is_it else "None")
        metric_disp = f"{route_metric}" if int(route_metric) >= 0 else "Auto (-1)"
        adv_param_rows = [
            [
                Txt("Domini Ricerca" if is_it else "DNS Search", role="schema"),
                _fmt_val(search_disp, _is_dirty("dns_search"), is_auto_fallback=(not dns_search)),
                ActionBtn("Modifica.." if is_it else "Edit..", f"ui:prompt:net_search|{sel_iface}:DNS search domains:{dns_search}", tone="neutral"),
            ],
            [
                Txt("Metrica Rotta" if is_it else "Route Metric", role="schema"),
                _fmt_val(metric_disp, _is_dirty("route_metric"), is_auto_fallback=(int(route_metric) < 0)),
                f"{ActionBtn('Modifica..' if is_it else 'Edit..', f'ui:prompt:net_metric|{sel_iface}:Route metric (-1 for auto, 10..1000):{route_metric}', tone='neutral')} "
                f"{ActionBtn('Auto', f'net:buf:{sel_iface}:route_metric:-1', tone='neutral')}",
            ],
            [
                Txt("Rotte Statiche" if is_it else "Static Routes", role="schema"),
                Txt(f"{len(static_routes)} configured", role="val"),
                ActionBtn("+ Aggiungi Rotta.." if is_it else "+ Add Route..", f"ui:prompt:net_add_route|{sel_iface}:Subnet/CIDR Gateway [Metric] (es. 10.20.0.0/24 10.10.0.1):", tone="apply"),
            ],
        ]
        v4_box.add_table(
            headers=[],
            widths=[18, 32, "flex"],
            rows=_spaced_rows(adv_param_rows),
            aligns=["left", "left", "right"],
            show_headers=False,
        )
        for idx, rt_str in enumerate(static_routes):
            v4_box.add_row(
                f"    {Txt('\u25b8', role='intent')}  {Txt(rt_str, role='focus')}",
                right_markup=ActionBtn("x", f"net:del_route:{sel_iface}:{idx}", tone="danger_soft"),
            )

    v4_box.add_row(" ")
    cards.append(v4_box)

    # =========================================================================
    # CARD 3: STACK IPv6 & PRIVACY (A Comparsa / Scomparsa)
    # =========================================================================
    ip6_method = str(_eff("ip6_method", "auto"))
    ip6_privacy = str(_eff("ip6_privacy", "default"))
    ip6_dns = str(_eff("ip6_dns", ""))
    v6_dirty = any(_is_dirty(k) for k in ("ip6_method", "ip6_privacy", "ip6_dns"))
    v6_open, v6_toggle = FoldToggle(panel, f"v6_sec_{sel_iface}", default=False, is_it=is_it, dirty=v6_dirty)

    v6_acts = [v6_toggle]
    if v6_open and v6_dirty:
        v6_acts.insert(0, ActionBtn("\uf00c Applica" if is_it else "\uf00c Apply", f"net:nm_apply:{sel_iface}:{sel_uuid}", tone="apply", active=True))

    v6_box = Box(
        f"3. Stack IPv6 & Privacy — {con_name}" if is_it else f"3. IPv6 Stack & Privacy — {con_name}",
        icon="\uf0ac",
        actions=v6_acts,
    )
    live_ip6 = sel_obj.get("ip6") or ("Disattivato / Link-Local" if is_it else "None / Link-Local")
    if not v6_open:
        v6_box.add_row(
            f"  {Txt('Method:', role='inactive')} {Txt(ip6_method.upper(), role='val')}  \u00b7  "
            f"{Txt('IPv6:', role='inactive')} {Txt(live_ip6[:32], role='val')}  \u00b7  "
            f"{Txt('Privacy:', role='inactive')} {Txt(ip6_privacy, role='val')}"
        )
    else:
        v6_box.add_row(" ")
        m6_opts = [("auto", "SLAAC / Auto"), ("dhcp", "DHCPv6"), ("link-local", "Link-Local"), ("ignore", "Ignore"), ("disabled", "Off")]
        m6_btns = " ".join(
            SelectPill(lbl, f"net:buf:{sel_iface}:ip6_method:{k}", selected=(ip6_method == k), dirty=(ip6_method == k and _is_dirty("ip6_method")))
            for k, lbl in m6_opts
        )

        p6_opts = [("default", "Default"), ("0", "Off (EUI-64)"), ("1", "Prefer Public"), ("2", "Prefer Temp (RFC4941)")]
        p6_btns = " ".join(
            SelectPill(lbl, f"net:buf:{sel_iface}:ip6_privacy:{k}", selected=(ip6_privacy == k), dirty=(ip6_privacy == k and _is_dirty("ip6_privacy")))
            for k, lbl in p6_opts
        )

        v6_box.add_table(
            headers=[],
            widths=[18, "flex"],
            rows=_spaced_rows([
                [Txt("Metodo IPv6" if is_it else "IPv6 Method", role="schema"), m6_btns],
                [Txt("IPv6 Privacy", role="schema"), p6_btns],
            ]),
            aligns=["left", "left"],
            uniform_row_height=True,
            show_headers=False,
        )
        v6_box.add_row(" ")
        v6_param_rows = [
            [
                Txt("Live Global IPv6", role="schema"),
                Txt(live_ip6[:34], role="val"),
                ActionBtn("Copy", "ui:copy:" + live_ip6.split("/")[0], tone="neutral") if sel_obj.get("ip6") else "",
            ],
            [
                Txt("DNSv6 Servers", role="schema"),
                _fmt_val(ip6_dns or "Auto", _is_dirty("ip6_dns"), is_auto_fallback=(not ip6_dns)),
                ActionBtn("Modifica.." if is_it else "Edit..", f"ui:prompt:net_ip6_dns|{sel_iface}:Comma-separated IPv6 DNS:{ip6_dns}", tone="neutral"),
            ],
        ]
        v6_box.add_table(
            headers=[],
            widths=[18, 36, "flex"],
            rows=_spaced_rows(v6_param_rows),
            aligns=["left", "left", "right"],
            show_headers=False,
        )
        v6_box.add_row(" ")
    cards.append(v6_box)

    # =========================================================================
    # CARD 4: LIVELLO FISICO L1/L2, MTU, MAC SPOOF & WOL (A Comparsa / Scomparsa)
    # =========================================================================
    mtu = str(_eff("mtu", "auto"))
    cloned_mac = str(_eff("cloned_mac", ""))
    wol = str(_eff("wol", "default"))
    lldp = str(_eff("lldp", "default"))
    wifi_band = str(_eff("wifi_band", "auto"))

    hw_dirty = any(_is_dirty(k) for k in ("mtu", "cloned_mac", "wol", "lldp", "wifi_band"))
    hw_open, hw_toggle = FoldToggle(panel, f"hw_sec_{sel_iface}", default=False, is_it=is_it, dirty=hw_dirty)

    hw_acts = [hw_toggle]
    if hw_open and hw_dirty:
        hw_acts.insert(0, ActionBtn("\uf00c Applica" if is_it else "\uf00c Apply", f"net:nm_apply:{sel_iface}:{sel_uuid}", tone="apply", active=True))

    hw_title = (
        f"4. Livello Fisico 802.11, MTU & MAC Spoofing — {sel_iface}"
        if is_wifi_iface
        else f"4. Livello Fisico 802.3, MTU, MAC & Wake-on-LAN — {sel_iface}"
    )
    hw_box = Box(
        hw_title,
        icon="\uf1eb" if is_wifi_iface else "\uf2db",
        actions=hw_acts,
    )

    if not hw_open:
        extra_sum = f"{Txt('Band:', role='inactive')} {Txt(wifi_band, role='val')}" if is_wifi_iface else f"{Txt('WoL:', role='inactive')} {Txt(wol, role='val')}"
        mtu_s = f"{sel_obj['mtu']} ({mtu})"
        hw_box.add_row(
            f"  {Txt('MAC:', role='inactive')} {Txt(sel_obj['mac'], role='val')}  \u00b7  "
            f"{Txt('MTU:', role='inactive')} {Txt(mtu_s, role='val')}  \u00b7  "
            f"{extra_sum}"
        )
    else:
        hw_box.add_row(" ")
        mtu_presets = "  ".join([
            SelectPill("Auto", f"net:buf:{sel_iface}:mtu:auto", selected=(mtu == "auto"), dirty=(mtu == "auto" and _is_dirty("mtu"))),
            SelectPill("1500 (Std)", f"net:buf:{sel_iface}:mtu:1500", selected=(mtu == "1500"), dirty=(mtu == "1500" and _is_dirty("mtu"))),
            SelectPill("1420 (WG)", f"net:buf:{sel_iface}:mtu:1420", selected=(mtu == "1420"), dirty=(mtu == "1420" and _is_dirty("mtu"))),
            SelectPill("9000 (Jumbo)", f"net:buf:{sel_iface}:mtu:9000", selected=(mtu == "9000"), dirty=(mtu == "9000" and _is_dirty("mtu"))),
            ActionBtn("Custom..", f"ui:prompt:net_mtu|{sel_iface}:MTU (68..9216 or auto):{mtu}", tone="neutral"),
        ])

        mac_presets = "  ".join([
            SelectPill("Permanent", f"net:buf:{sel_iface}:cloned_mac:permanent", selected=(cloned_mac == "permanent"), dirty=(cloned_mac == "permanent" and _is_dirty("cloned_mac"))),
            SelectPill("Preserve", f"net:buf:{sel_iface}:cloned_mac:preserve", selected=(cloned_mac == "preserve"), dirty=(cloned_mac == "preserve" and _is_dirty("cloned_mac"))),
            SelectPill("Stable", f"net:buf:{sel_iface}:cloned_mac:stable", selected=(cloned_mac == "stable"), dirty=(cloned_mac == "stable" and _is_dirty("cloned_mac"))),
            SelectPill("Random", f"net:buf:{sel_iface}:cloned_mac:random", selected=(cloned_mac == "random"), dirty=(cloned_mac == "random" and _is_dirty("cloned_mac"))),
            ActionBtn("Clear", f"net:buf:{sel_iface}:cloned_mac:", tone="danger_soft") if cloned_mac else "",
        ]).strip()

        hw_sel_rows = [
            [Txt(f"MTU (Live {sel_obj['mtu']})", role="schema"), mtu_presets],
            [Txt("MAC Policy", role="schema"), mac_presets],
        ]

        if is_wifi_iface:
            b_opts = [("auto", "Auto (2.4/5 GHz)"), ("a", "5 GHz Only"), ("bg", "2.4 GHz Only")]
            b_btns = "  ".join(
                SelectPill(lbl, f"net:buf:{sel_iface}:wifi_band:{k}", selected=(wifi_band == k), dirty=(wifi_band == k and _is_dirty("wifi_band")))
                for k, lbl in b_opts
            )
            hw_sel_rows.append([Txt("Banda Wi-Fi" if is_it else "Wi-Fi Band", role="schema"), b_btns])
        else:
            wol_opts = [("default", "Default"), ("magic", "MagicPacket (ON)"), ("ignore", "Disabled")]
            wol_btns = "  ".join(
                SelectPill(lbl, f"net:buf:{sel_iface}:wol:{k}", selected=(wol == k), dirty=(wol == k and _is_dirty("wol")))
                for k, lbl in wol_opts
            )
            lldp_opts = [("default", "Default"), ("rx", "Listen RX (ON)"), ("disable", "Disabled")]
            lldp_btns = "  ".join(
                SelectPill(lbl, f"net:buf:{sel_iface}:lldp:{k}", selected=(lldp == k), dirty=(lldp == k and _is_dirty("lldp")))
                for k, lbl in lldp_opts
            )
            hw_sel_rows.append([Txt("Wake-on-LAN", role="schema"), wol_btns])
            hw_sel_rows.append([Txt("LLDP Listener", role="schema"), lldp_btns])

        hw_box.add_table(
            headers=[],
            widths=[18, "flex"],
            rows=_spaced_rows(hw_sel_rows),
            aligns=["left", "left"],
            uniform_row_height=True,
            show_headers=False,
        )
        hw_box.add_row(" ")

        rx_tot = format_bytes_short(sel_obj.get("rx_total", 0))
        tx_tot = format_bytes_short(sel_obj.get("tx_total", 0))
        clone_role = "attention" if _is_dirty("cloned_mac") else "schema"
        clone_s = f"(Clone: {cloned_mac or 'Off'})"
        hw_param_rows = [
            [
                Txt("Hardware MAC", role="schema"),
                f"{Txt(sel_obj['mac'], role='focus')}   {Txt(clone_s, role=clone_role)}",
                f"{ActionBtn('Spoof MAC..', f'ui:prompt:net_cmac|{sel_iface}:Cloned MAC (XX:XX:XX:XX:XX:XX, random, permanent):{cloned_mac}', tone='neutral')} "
                f"{ActionBtn('Copy', 'ui:copy:' + sel_obj['mac'], tone='neutral')}",
            ],
            [
                Txt("Contatori Kernel" if is_it else "Kernel Totals", role="schema"),
                f"{Txt(f'RX: {rx_tot}', role='ok_soft')}   {Txt(f'TX: {tx_tot}', role='intent_soft')}",
                Txt((nm_cur.get("filename") or "unmanaged")[-26:], role="inactive"),
            ],
        ]
        hw_box.add_table(
            headers=[],
            widths=[18, 36, "flex"],
            rows=_spaced_rows(hw_param_rows),
            aligns=["left", "left", "right"],
            show_headers=False,
        )
        hw_box.add_row(" ")

    cards.append(hw_box)
    return cards
