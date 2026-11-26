#!/usr/bin/env python3
import copy

from core import probe
from core.config import get_network_show_bar_speed
from core.i18n import t
from ui.canvas import Canvas
from ui.kit import (
    ActionBtn,
    Badge,
    Box,
    Divider,
    InlineField,
    InspectorCell,
    SelectPill,
    StateBadge,
    Txt,
    make_feedback_cards,
)
from ui.markup import format_speed, format_bytes_short
from panels.cc_engine_network import (
    _NET_LOCK,
    _NET_STATE,
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
    flt_all = SelectPill(t("cc_net_wired.all"), "net:if_filter:all", selected=(cur_filter == "all"))
    flt_eth = SelectPill(t("cc_net_wired.wired_virt"), "net:if_filter:wired", selected=(cur_filter == "wired"))
    flt_wifi = SelectPill(t("cc_net_wired.wifi"), "net:if_filter:wifi", selected=(cur_filter == "wifi"))
    virt_open, virt_btn = panel._fold("wired_virt", default=False)

    if_box = Box(
        t("cc_net_wired.network_interfaces_live_telemetry"),
        icon="\uf6ff",
        actions=[
            flt_all,
            flt_eth,
            flt_wifi,
            ActionBtn(t("cc_net_wired.unit", p0=unit_mode.upper()), "net:toggle_unit", tone="neutral"),
        ],
    )

    if not filtered_ifaces:
        if_box.add_row(f"  {Txt(t('cc_net_wired.no_network_interfaces_detected'), role='inactive')}")
        cards.append(if_box)
        cards.extend(make_feedback_cards("network", title_key="cc_net_wired.networkmanager_operation_status", key="fb_interfaces"))
        return cards

    if_up_lbl = t("cc_net_wired.up_2")
    if_dn_lbl = t("cc_net_wired.down")
    if_updn_mc = max(len(if_up_lbl), len(if_dn_lbl))
    if_rows = []
    for iface in filtered_ifaces:
        name = iface["name"]
        is_sel = (name == sel_iface)
        is_mon = (name == mon_iface)
        is_up = bool(iface["up"])
        ic = "\uf1eb" if iface["kind"] == "wifi" else ("\uf132" if iface["kind"] == "vpn" else ("\uf0e8" if iface["kind"] == "bridge" else "\uf6ff"))

        dev_str = InspectorCell(name[:11], f"net:sel_iface:{name}", selected=is_sel, is_alive=is_up, icon=ic)

        spd_s = iface.get("link_speed", "N/D")
        spd_tag = f" {spd_s[:6]}" if (is_up and spd_s and spd_s != "N/D") else ""
        st_link = StateBadge(is_up, f"{t('common.up')}{spd_tag}", t("common.down"), on_tone="ok")

        has_ip = (iface["ip"] != "N/D")
        ip_col = Txt(iface["ip"][:18] if has_ip else t("common.nd"), role="val" if has_ip else "inactive")

        rx_val, tx_val = iface["rx_rate"], iface["tx_rate"]
        rx_s = format_speed(rx_val, unit_mode).strip()
        tx_s = format_speed(tx_val, unit_mode).strip()
        rx_role = "ok_soft" if rx_val > 64.0 else "inactive"
        tx_role = "val" if tx_val > 64.0 else "inactive"
        traffic_col = (
            f"{Txt(f'\u2193\u00a0{rx_s:<8}', role=rx_role)} "
            f"{Txt(f'\u2191\u00a0{tx_s:<8}', role=tx_role)}"
        )

        updn_b = (
            ActionBtn(if_dn_lbl, f"net:ifdown:{name}", tone="danger_soft", min_chars=if_updn_mc)
            if is_up
            else ActionBtn(if_up_lbl, f"net:ifup:{name}", tone="apply", min_chars=if_updn_mc)
        )
        if show_bar_spd:
            mon_b = SelectPill(t("cc_net_wired.bar"), f"net:mon:{name}", selected=is_mon)
            act_cell = f"{mon_b} {updn_b}"
        else:
            act_cell = updn_b

        if_rows.append([dev_str, st_link, ip_col, traffic_col, act_cell])

    if_box.add_table(
        headers=[
            t("cc_net_wired.interface"),
            t("cc_net_wired.state_link"),
            t("cc_net_wired.ipv4_cidr"),
            t("cc_net_wired.traffic"),
            t("cc_net_wired.actions"),
        ],
        widths=[16, 13, 18, 22, "flex"],
        rows=_spaced_rows(if_rows),
        aligns=["left", "left", "left", "left", "right"],
    )

    has_nmcli = probe.has_bin("nmcli")
    if_box.add_section(t("cc_net_wired.virtual_interfaces_bridge_802_1q_v"), actions=[virt_btn] if has_nmcli else [], requires=["nmcli"])
    if virt_open and has_nmcli:
        if_box.add_row(
            f"  {Txt(t('cc_net_wired.create_virtual_nm_interface'), role='schema')}",
            right_markup=(
                f"{InlineField('net_virtual_br', 'net:inline_virtual', value='', empty_label=t('cc_net_wired.bridge_br0'), placeholder='br0', width=12, icon='', tone='neutral')}  "
                f"{InlineField('net_vlan_create', 'net:inline_vlan', value='', empty_label=t('cc_net_wired.vlan_802_1q'), placeholder=f'{sel_iface}|10', width=14, icon='', tone='neutral')}"
            ),
        )

    cards.append(if_box)

    sel_obj = next((i for i in ifaces if i["name"] == sel_iface), ifaces[0])
    is_wifi_iface = (sel_obj["kind"] == "wifi")

    with _NET_LOCK:
        nm_cur = copy.deepcopy(_NET_STATE["nm_profiles"].get(sel_iface, {}))
        net_init = bool(_NET_STATE.get("initialized"))
        nm_global_run = bool(_NET_STATE.get("nm_running", False))

    buf_all = panel.get_state("edit_buffer", {})
    buf = copy.deepcopy(buf_all.get(sel_iface, {}) if isinstance(buf_all, dict) else {})

    def _eff(key: str, default=""):
        return buf[key] if key in buf else nm_cur.get(key, default)

    def _is_dirty(key: str) -> bool:
        if key not in buf:
            return False
        b_val, n_val = buf[key], nm_cur.get(key)
        if key in ("route_metric", "autoconnect_priority"):
            return str(b_val) != str(n_val)
        return b_val != n_val

    def _pills(field: str, cur_val: str, opts: list[tuple[str, str]], sep: str = "  ") -> str:
        d = _is_dirty(field)
        return sep.join(
            SelectPill(lbl, f"net:buf:{sel_iface}:{field}:{k}", selected=(cur_val == k), dirty=(cur_val == k and d))
            for k, lbl in opts
        )

    cards.extend(make_feedback_cards("network", title_key="cc_net_wired.networkmanager_operation_status", key="fb_interfaces"))

    # =========================================================================
    # CARD 1: PROFILI NETWORKMANAGER ASSOCIATI ALL'INTERFACCIA SELEZIONATA
    # =========================================================================
    cards.append(
        Divider(
            t("cc_net_wired.inspecting_interface", p0=sel_iface, p1=sel_obj['kind'].upper()),
            icon="\uf1eb" if is_wifi_iface else "\uf085",
        )
    )

    avail_profs = nm_cur.get("available_profiles", [])
    sel_uuid = nm_cur.get("uuid", "")
    nm_active = has_nmcli and net_init and nm_cur.get("nm_running", nm_global_run)

    prof_actions = []
    if not has_nmcli:
        prof_actions.append(Badge(t("ui.missing", p0="nmcli"), style="warn"))
    elif nm_active:
        if is_wifi_iface:
            prof_actions.append(ActionBtn(t("cc_net_wired.wi_fi_scanner"), "net:jump_wifi_tab", tone="neutral"))
        prof_actions.append(
            InlineField(f"net_new_prof_{sel_iface}", f"net:inline_new_prof:{sel_iface}", value="", empty_label=t("cc_net_wired.new_profile"), placeholder=f"{sel_iface}-static", width=16, icon="", tone="apply")
        )

    prof_box = Box(
        t("cc_net_wired.1_associated_networkmanager_profil", p0=sel_iface, p1=len(avail_profs)),
        icon="\uf0c7",
        actions=prof_actions,
    )

    if not has_nmcli:
        prof_box.add_row(f"  {Txt(t('cc_net_wired.nmcli_missing'), role='inactive')}")
        cards.append(prof_box)
        return cards

    if not net_init:
        prof_box.add_row(f"  {Txt(t('network.detecting'), role='inactive')}")
        cards.append(prof_box)
        return cards

    if not nm_cur.get("nm_running", nm_global_run):
        prof_box.add_row(f"  {Txt(t('cc_net_wired.networkmanager_service_is_not_runn'), role='fault')}")
        cards.append(prof_box)
        return cards

    if not avail_profs:
        prof_box.add_row(
            f"  {Txt(t('cc_net_wired.no_persistent_networkmanager_profi'), role='attention')}",
            right_markup=ActionBtn(t("cc_net_wired.create_default_profile"), f"net:nm_create:{sel_iface}", tone="apply"),
        )
        cards.append(prof_box)
        return cards

    p_up_lbl = t("cc_net_wired.up")
    p_dn_lbl = t("cc_net_wired.down")
    p_updn_mc = max(len(p_up_lbl), len(p_dn_lbl))
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
        live_cell = StateBadge(is_live, t("cc_net_wired.active"), t("cc_net_wired.standby"), on_tone="ok")
        ac_lbl = t("cc_net_wired.auto_prio", p0=prio_s) if is_ac else t("cc_net_wired.manual_lbl")
        ac_btn = SelectPill(
            ac_lbl,
            f"net:nm_auto_prof:{sel_iface}:{p_uuid}:{'no' if is_ac else 'yes'}",
            selected=is_ac,
        )

        act_updn = (
            ActionBtn(p_dn_lbl, f"net:nm_down_prof:{sel_iface}:{p_uuid}", tone="danger_soft", min_chars=p_updn_mc)
            if is_live
            else ActionBtn(p_up_lbl, f"net:nm_up_prof:{sel_iface}:{p_uuid}", tone="apply", min_chars=p_updn_mc)
        )
        clone_b = ActionBtn(t("cc_net_wired.clone"), f"net:nm_clone_prof:{sel_iface}:{p_uuid}", tone="neutral")
        ren_b = InlineField(f"net_ren_{p_uuid}", f"net:inline_rename_prof:{sel_iface}:{p_uuid}", value="", empty_label=t("cc_net_wired.rename"), placeholder="name", width=12, icon="\u270e", tone="neutral")
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
            t("cc_net_wired.profile_edit"),
            t("cc_net_wired.state"),
            t("cc_net_wired.autoconn"),
            "UUID",
            t("cc_net_wired.profile_actions"),
        ],
        widths=[22, 11, 14, 9, "flex"],
        rows=_spaced_rows(p_rows),
        aligns=["left", "left", "left", "left", "right"],
    )
    cards.append(prof_box)

    # =========================================================================
    # CARD 2: CONFIGURAZIONE IPv4 & PARAMETRI PRINCIPALI (+ Avanzate Collapsible)
    # =========================================================================
    has_dirty = any(_is_dirty(k) for k in buf)
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
        v4_actions.append(StateBadge(True, t("cc_net_wired.unsaved_changes"), on_tone="warn"))
        v4_actions.append(ActionBtn(t("cc_net_wired.reset"), f"net:nm_reset:{sel_iface}", tone="warn"))
    v4_actions.append(
        ActionBtn(
            t("cc_net_wired.apply_verify"),
            f"net:nm_apply:{sel_iface}:{sel_uuid}",
            tone="apply" if has_dirty else "inactive",
        )
    )

    v4_box = Box(
        t("cc_net_wired.2_ipv4_addressing_dns", p0=con_name),
        icon="\uf124",
        actions=v4_actions,
    )

    m_btns = _pills("method", method, [
        ("auto", t("cc_net_wired.dhcp_auto")),
        ("manual", t("cc_net_wired.static_manual")),
        ("shared", t("cc_net_wired.shared_nat")),
        ("disabled", t("cc_net_wired.off_lbl")),
    ])

    ac_yes = (autoconn.lower() == "yes")
    ac_btns = "  ".join([
        SelectPill(t("common.on"), f"net:buf:{sel_iface}:autoconnect:yes", selected=ac_yes, dirty=(ac_yes and _is_dirty("autoconnect"))),
        SelectPill(t("common.off"), f"net:buf:{sel_iface}:autoconnect:no", selected=not ac_yes, dirty=(not ac_yes and _is_dirty("autoconnect"))),
        InlineField(
            f"net_prio_{sel_iface}",
            f"net:inline_field:net_prio:{sel_iface}",
            value="",
            empty_label=t("cc_net_wired.prio", p0=prio),
            placeholder="-999..999",
            width=10,
            icon="",
            tone="neutral",
            dirty=_is_dirty("autoconnect_priority"),
        ),
    ])

    v4_box.add_table(
        headers=[],
        widths=[18, "flex"],
        rows=_spaced_rows([
            [Txt(t("cc_net_wired.ipv4_method"), role="schema"), m_btns],
            [Txt(t("cc_net_wired.autoconnect"), role="schema"), ac_btns],
        ]),
        aligns=["left", "left"],
        uniform_row_height=True,
        show_headers=False,
    )
    v4_box.add_row(" ")

    def _fmt_val(text_str: str, dirty_flag: bool, is_auto_fallback: bool = False) -> str:
        s_trunc = str(text_str)[:32]
        if dirty_flag:
            return Badge(s_trunc, style="warn")
        if is_auto_fallback:
            return Txt(s_trunc, role="schema")
        return Txt(s_trunc, role="val")

    has_live_ip = (sel_obj["ip"] != "N/D")
    live_ip_hint = t("cc_net_wired.dhcp_val", p0=sel_obj["ip"]) if has_live_ip else t("cc_net_wired.dhcp_auto")
    ip_disp = ip_cidr if ip_cidr else (live_ip_hint if method == "auto" else t("cc_net_wired.not_set"))
    gw_disp = gw if gw else (t("cc_net_wired.dhcp_val", p0=sel_obj["gw"]) if (method == "auto" and sel_obj["gw"]) else (sel_obj["gw"] or t("cc_net_wired.auto")))
    dns_disp = dns if dns else t("cc_net_wired.automatic_dhcp")

    ign_yes = (ignore_auto_dns.lower() == "yes")
    auto_dns_pill = SelectPill(
        t("cc_net_wired.ignore_auto_dns"),
        f"net:buf:{sel_iface}:ignore_auto_dns:{'no' if ign_yes else 'yes'}",
        selected=ign_yes,
        dirty=_is_dirty("ignore_auto_dns"),
    )

    clean_live_ip = sel_obj["ip"].split("/")[0]
    ip_copy_btn = ActionBtn(t("cc_net_wired.copy"), "ui:copy:" + clean_live_ip, tone="neutral") if has_live_ip else ""
    param_rows = [
        [
            Txt(t("cc_net_wired.ipv4_cidr"), role="schema"),
            _fmt_val(ip_disp, _is_dirty("ip_cidr"), is_auto_fallback=(not ip_cidr)),
            f"{InlineField(f'net_ip_{sel_iface}', f'net:inline_field:net_ip:{sel_iface}', value='', empty_label=t('cc_net_wired.edit'), placeholder='10.0.0.10/24', width=15, icon='\u270e', tone='neutral')} {ip_copy_btn}".strip(),
        ],
        [
            Txt(t("cc_net_wired.gateway_ipv4"), role="schema"),
            _fmt_val(gw_disp, _is_dirty("gateway"), is_auto_fallback=(not gw)),
            f"{InlineField(f'net_gw_{sel_iface}', f'net:inline_field:net_gw:{sel_iface}', value='', empty_label=t('cc_net_wired.edit'), placeholder='10.0.0.1', width=14, icon='\u270e', tone='neutral')} "
            f"{ActionBtn(t('cc_net_wired.clear'), f'net:buf:{sel_iface}:gateway:', tone='danger_soft') if gw else ''}".strip(),
        ],
        [
            Txt(t("cc_net_wired.dns_servers"), role="schema"),
            _fmt_val(dns_disp, _is_dirty("dns"), is_auto_fallback=(not dns)),
            f"{auto_dns_pill} {InlineField(f'net_dns_{sel_iface}', f'net:inline_field:net_dns:{sel_iface}', value='', empty_label=t('cc_net_wired.edit'), placeholder='1.1.1.1', width=14, icon='\u270e', tone='neutral')}",
        ],
    ]
    v4_box.add_table(
        headers=[
            t("cc_net_wired.parameter"),
            t("cc_net_wired.current_value"),
            t("cc_net_wired.actions"),
        ],
        widths=[18, 32, "flex"],
        rows=_spaced_rows(param_rows),
        aligns=["left", "left", "right"],
    )

    adv_dirty = any(_is_dirty(k) for k in ("dns_search", "route_metric", "never_default", "zone", "metered", "static_routes"))
    adv_open, adv_toggle = panel._fold(f"v4_adv_{sel_iface}", default=False, dirty=adv_dirty)

    v4_box.add_section(
        t("cc_net_wired.advanced_routing_firewall_static_r"),
        actions=[adv_toggle],
    )
    if not adv_open:
        z_sum = zone or t("cc_net_wired.zone_default")
        m_sum = f"{route_metric}" if int(route_metric) >= 0 else t("cc_net_wired.auto_2")
        r_sum = str(len(static_routes))
        v4_box.add_row(
            f"  {Txt(t('cc_net_wired.firewall_zone'), role='inactive')} {Txt(z_sum, role='val')}  \u00b7  "
            f"{Txt(t('cc_net_wired.metric'), role='inactive')} {Txt(m_sum, role='val')}  \u00b7  "
            f"{Txt(t('cc_net_wired.static_routes_2'), role='inactive')} {Txt(r_sum, role='val')}"
        )
    else:
        nd_btns = _pills("never_default", never_default.lower(), [
            ("no", t("cc_net_wired.default_route_ok")),
            ("yes", t("cc_net_wired.no_default_route_split")),
        ])
        z_btns = _pills("zone", zone, [
            ("", t("cc_net_wired.zone_default")),
            ("trusted", t("cc_net_wired.zone_trusted")),
            ("home", t("cc_net_wired.zone_home")),
            ("work", t("cc_net_wired.zone_work")),
            ("public", t("cc_net_wired.zone_public")),
            ("drop", t("cc_net_wired.zone_drop")),
        ], sep=" ")
        met_btns = _pills("metered", metered, [
            ("unknown", t("cc_net_wired.auto_2")),
            ("yes", t("cc_net_wired.metered_yes")),
            ("no", t("cc_net_wired.metered_no")),
        ])

        v4_box.add_table(
            headers=[],
            widths=[18, "flex"],
            rows=_spaced_rows([
                [Txt(t("cc_net_wired.default_route"), role="schema"), nd_btns],
                [Txt(t("cc_net_wired.firewalld_zone"), role="schema"), z_btns],
                [Txt(t("cc_net_wired.metered_link"), role="schema"), met_btns],
            ]),
            aligns=["left", "left"],
            uniform_row_height=True,
            show_headers=False,
        )
        v4_box.add_row(" ")

        search_disp = dns_search if dns_search else t("cc_net_wired.none")
        metric_disp = f"{route_metric}" if int(route_metric) >= 0 else t("cc_net_wired.auto_neg1")
        adv_param_rows = [
            [
                Txt(t("cc_net_wired.dns_search"), role="schema"),
                _fmt_val(search_disp, _is_dirty("dns_search"), is_auto_fallback=(not dns_search)),
                InlineField(f"net_search_{sel_iface}", f"net:inline_field:net_search:{sel_iface}", value="", empty_label=t("cc_net_wired.edit"), placeholder="corp.lan", width=14, icon="\u270e", tone="neutral"),
            ],
            [
                Txt(t("cc_net_wired.route_metric"), role="schema"),
                _fmt_val(metric_disp, _is_dirty("route_metric"), is_auto_fallback=(int(route_metric) < 0)),
                f"{InlineField(f'net_metric_{sel_iface}', f'net:inline_field:net_metric:{sel_iface}', value='', empty_label=t('cc_net_wired.edit'), placeholder='100', width=9, icon='\u270e', tone='neutral')} "
                f"{ActionBtn(t('cc_net_wired.auto_2'), f'net:buf:{sel_iface}:route_metric:-1', tone='neutral')}",
            ],
            [
                Txt(t("cc_net_wired.static_routes"), role="schema"),
                Txt(t("cc_net_wired.configured", p0=len(static_routes)), role="val"),
                ActionBtn(t("cc_net_wired.add_route"), f"net:prompt_add_route:{sel_iface}", tone="apply"),
            ],
        ]
        v4_box.add_table(
            headers=[],
            widths=[18, 32, "flex"],
            rows=_spaced_rows(adv_param_rows),
            aligns=["left", "left", "right"],
            show_headers=False,
        )
        if static_routes:
            rt_rows = [
                [Txt(rt_str, role="val"), ActionBtn("x", f"net:del_route:{sel_iface}:{idx}", tone="danger_soft")]
                for idx, rt_str in enumerate(static_routes)
            ]
            v4_box.add_table(headers=["", ""], widths=["flex", 6], rows=rt_rows, aligns=["left", "right"], show_headers=False)

    cards.append(v4_box)

    # =========================================================================
    # CARD 3: STACK IPv6 & PRIVACY (A Comparsa / Scomparsa)
    # =========================================================================
    ip6_method = str(_eff("ip6_method", "auto"))
    ip6_privacy = str(_eff("ip6_privacy", "default"))
    ip6_dns = str(_eff("ip6_dns", ""))
    v6_dirty = any(_is_dirty(k) for k in ("ip6_method", "ip6_privacy", "ip6_dns"))
    v6_open, v6_toggle = panel._fold(f"v6_sec_{sel_iface}", default=False, dirty=v6_dirty)

    v6_acts = [v6_toggle]
    if v6_open and v6_dirty:
        v6_acts.insert(0, ActionBtn(t("cc_net_wired.apply"), f"net:nm_apply:{sel_iface}:{sel_uuid}", tone="apply"))

    v6_box = Box(
        t("cc_net_wired.3_ipv6_stack_privacy", p0=con_name),
        icon="\uf0ac",
        actions=v6_acts,
    )
    live_ip6 = sel_obj.get("ip6") or t("cc_net_wired.none_link_local")
    if not v6_open:
        v6_box.add_row(
            f"  {Txt(t('cc_net_wired.method'), role='inactive')} {Txt(ip6_method.upper(), role='val')}  \u00b7  "
            f"{Txt(t('cc_net_wired.ipv6'), role='inactive')} {Txt(live_ip6[:32], role='val')}  \u00b7  "
            f"{Txt(t('cc_net_wired.privacy'), role='inactive')} {Txt(ip6_privacy, role='val')}"
        )
    else:
        m6_btns = _pills("ip6_method", ip6_method, [
            ("auto", t("cc_net_wired.ip6_slaac")),
            ("dhcp", t("cc_net_wired.ip6_dhcp")),
            ("link-local", t("cc_net_wired.ip6_link_local")),
            ("ignore", t("cc_net_wired.ip6_ignore")),
            ("disabled", t("cc_net_wired.off_lbl")),
        ], sep=" ")
        p6_btns = _pills("ip6_privacy", ip6_privacy, [
            ("default", t("cc_net_wired.zone_default")),
            ("0", t("cc_net_wired.ip6_priv_off")),
            ("1", t("cc_net_wired.ip6_priv_public")),
            ("2", t("cc_net_wired.ip6_priv_temp")),
        ], sep=" ")

        v6_box.add_table(
            headers=[],
            widths=[18, "flex"],
            rows=_spaced_rows([
                [Txt(t("cc_net_wired.ipv6_method"), role="schema"), m6_btns],
                [Txt(t("cc_net_wired.ipv6_privacy"), role="schema"), p6_btns],
            ]),
            aligns=["left", "left"],
            uniform_row_height=True,
            show_headers=False,
        )
        v6_box.add_row(" ")
        v6_param_rows = [
            [
                Txt(t("cc_net_wired.live_global_ipv6"), role="schema"),
                Txt(live_ip6[:34], role="val"),
                ActionBtn(t("cc_net_wired.copy"), "ui:copy:" + live_ip6.split("/")[0], tone="neutral") if sel_obj.get("ip6") else "",
            ],
            [
                Txt(t("cc_net_wired.dnsv6_servers"), role="schema"),
                _fmt_val(ip6_dns or t("cc_net_wired.auto_2"), _is_dirty("ip6_dns"), is_auto_fallback=(not ip6_dns)),
                InlineField(f"net_ip6_dns_{sel_iface}", f"net:inline_field:net_ip6_dns:{sel_iface}", value="", empty_label=t("cc_net_wired.edit"), placeholder="2606:4700::1111", width=16, icon="\u270e", tone="neutral"),
            ],
        ]
        v6_box.add_table(
            headers=[],
            widths=[18, 36, "flex"],
            rows=_spaced_rows(v6_param_rows),
            aligns=["left", "left", "right"],
            show_headers=False,
        )

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
    hw_open, hw_toggle = panel._fold(f"hw_sec_{sel_iface}", default=False, dirty=hw_dirty)

    hw_acts = [hw_toggle]
    if hw_open and hw_dirty:
        hw_acts.insert(0, ActionBtn(t("cc_net_wired.apply"), f"net:nm_apply:{sel_iface}:{sel_uuid}", tone="apply"))

    hw_title = (
        t("cc_net_wired.4_physical_802_11", p0=sel_iface)
        if is_wifi_iface
        else t("cc_net_wired.4_physical_802_3", p0=sel_iface)
    )
    hw_box = Box(
        hw_title,
        icon="\uf1eb" if is_wifi_iface else "\uf2db",
        actions=hw_acts,
    )

    mac_disp = sel_obj["mac"] if sel_obj["mac"] != "N/D" else t("common.nd")
    if not hw_open:
        extra_sum = f"{Txt(t('cc_net_wired.band'), role='inactive')} {Txt(wifi_band, role='val')}" if is_wifi_iface else f"{Txt(t('cc_net_wired.wol_short'), role='inactive')} {Txt(wol, role='val')}"
        mtu_s = f"{sel_obj['mtu']} ({mtu})"
        hw_box.add_row(
            f"  {Txt(t('cc_net_wired.mac'), role='inactive')} {Txt(mac_disp, role='val')}  \u00b7  "
            f"{Txt(t('cc_net_wired.mtu'), role='inactive')} {Txt(mtu_s, role='val')}  \u00b7  "
            f"{extra_sum}"
        )
    else:
        is_custom_mtu = mtu not in ("auto", "1500", "1420", "9000")
        mtu_presets = "  ".join([
            _pills("mtu", mtu, [
                ("auto", t("cc_net_wired.auto_2")),
                ("1500", t("cc_net_wired.1500_std")),
                ("1420", t("cc_net_wired.1420_wg")),
                ("9000", t("cc_net_wired.9000_jumbo")),
            ]),
            InlineField(
                f"net_mtu_{sel_iface}",
                f"net:inline_field:net_mtu:{sel_iface}",
                value=mtu if is_custom_mtu else "",
                empty_label=t("cc_net_wired.custom"),
                placeholder="1500",
                width=8,
                icon="\u270e",
                tone="neutral",
                selected=is_custom_mtu,
                dirty=(is_custom_mtu and _is_dirty("mtu")),
            ),
        ])

        is_custom_mac = bool(cloned_mac and cloned_mac not in ("permanent", "preserve", "stable", "random"))
        mac_presets = "  ".join([
            _pills("cloned_mac", cloned_mac, [
                ("permanent", t("cc_net_wired.permanent")),
                ("preserve", t("cc_net_wired.preserve")),
                ("stable", t("cc_net_wired.stable")),
                ("random", t("cc_net_wired.random")),
            ]),
            ActionBtn(t("cc_net_wired.clear"), f"net:buf:{sel_iface}:cloned_mac:", tone="danger_soft") if cloned_mac else "",
        ]).strip()

        hw_sel_rows = [
            [Txt(t("cc_net_wired.mtu_live", p0=sel_obj['mtu']), role="schema"), mtu_presets],
            [Txt(t("cc_net_wired.mac_policy"), role="schema"), mac_presets],
        ]

        if is_wifi_iface:
            b_btns = _pills("wifi_band", wifi_band, [
                ("auto", t("cc_net_wired.band_auto")),
                ("a", t("cc_net_wired.band_5")),
                ("bg", t("cc_net_wired.band_24")),
            ])
            hw_sel_rows.append([Txt(t("cc_net_wired.wi_fi_band"), role="schema"), b_btns])
        else:
            wol_btns = _pills("wol", wol, [
                ("default", t("cc_net_wired.zone_default")),
                ("magic", t("cc_net_wired.wol_magic")),
                ("ignore", t("cc_net_wired.wol_disabled")),
            ])
            lldp_btns = _pills("lldp", lldp, [
                ("default", t("cc_net_wired.zone_default")),
                ("rx", t("cc_net_wired.lldp_rx")),
                ("disable", t("cc_net_wired.wol_disabled")),
            ])
            hw_sel_rows.append([Txt(t("cc_net_wired.wake_on_lan"), role="schema"), wol_btns])
            hw_sel_rows.append([Txt(t("cc_net_wired.lldp_listener"), role="schema"), lldp_btns])

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
        mac_copy_btn = ActionBtn(t("cc_net_wired.copy"), "ui:copy:" + sel_obj["mac"], tone="neutral") if sel_obj["mac"] != "N/D" else ""
        hw_param_rows = [
            [
                Txt(t("cc_net_wired.hardware_mac"), role="schema"),
                Txt(mac_disp, role="focus" if sel_obj["mac"] != "N/D" else "inactive"),
                f"{InlineField(f'net_cmac_{sel_iface}', f'net:inline_field:net_cmac:{sel_iface}', value=cloned_mac if is_custom_mac else '', empty_label=t('cc_net_wired.spoof_mac'), placeholder='random / MAC', width=14, icon='\u270e', tone='neutral', selected=is_custom_mac, dirty=(is_custom_mac and _is_dirty('cloned_mac')))} {mac_copy_btn}".strip(),
            ],
            [
                Txt(t("cc_net_wired.kernel_totals"), role="schema"),
                f"{Txt(f'RX: {rx_tot}', role='ok_soft')}   {Txt(f'TX: {tx_tot}', role='val')}",
                Txt((nm_cur.get("filename") or t("cc_net_wired.unmanaged"))[-26:], role="inactive"),
            ],
        ]
        hw_box.add_table(
            headers=[],
            widths=[18, 36, "flex"],
            rows=_spaced_rows(hw_param_rows),
            aligns=["left", "left", "right"],
            show_headers=False,
        )

    cards.append(hw_box)
    return cards


def build_cards(cc_panel, canvas: Canvas) -> list:
    from panels.cc_engine_network import build_cc_network_cards
    return build_cc_network_cards(cc_panel, canvas, "interfaces")
