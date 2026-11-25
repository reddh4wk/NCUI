#!/usr/bin/env python3
from core.i18n import t
from ui.canvas import Canvas
from ui.kit import ActionBtn, Badge, Box, InlineField, SelectPill, Txt
from panels.cc_engine_firewall import (
    _feedback_cards,
    _load_fw_store,
    _generate_nft_config,
    _dir_short,
    _verdict_style,
    _PRESET_SPECS,
)


def _build_chains_tab(cc_panel, canvas: Canvas) -> list:
    store = _load_fw_store()
    pols = store.get("policies", {})
    rules = store.get("rules", [])
    lan = str(store.get("lan_net", "10.10.0.0/24"))
    by_id = {r.get("id"): r for r in rules if r.get("id")}
    cards = _feedback_cards()

    pol_box = Box(
        t("cc_firewall.chain_default_policies_variables_l"),
        icon="\uf0e8",
        actions=[ActionBtn(t("cc_firewall.start_restart_nftables"), "cc_fw:svc_restart", tone="apply")],
        highlighted=False,
    )
    pol_box.add_row(" ")
    for ch in ("input", "forward", "output"):
        lbl = t(f"cc_firewall.pol_{ch}")
        cur_p = pols.get(ch, "drop")
        pills = (
            f"{SelectPill(t('cc_firewall.drop_secure'), f'cc_fw:pol:{ch}:drop', selected=(cur_p == 'drop'))} "
            f"{SelectPill(t('cc_firewall.accept_open'), f'cc_fw:pol:{ch}:accept', selected=(cur_p == 'accept'))}"
        )
        pol_box.add_kv(lbl, pills)
    pol_box.add_kv(
        "$LAN_NETWORK",
        Txt(lan, role="val"),
        right_markup=InlineField(
            "fw_lan_net",
            "cc_fw:inline_lan",
            value="",
            empty_label=t("cc_firewall.edit_subnet"),
            placeholder="10.10.0.0/24",
            width=18,
            icon="\u270e",
            tone="neutral",
        ),
    )
    pol_box.add_row(" ")
    cards.append(pol_box)

    pre_box = Box(t("cc_firewall.quick_service_security_rule_preset"), icon="\uf0e7", highlighted=False)
    pre_box.add_row(" ")
    col0_w = 24 if canvas.inner_cols < 86 else 28
    match_max = max(12, canvas.inner_cols - (col0_w + 39))
    pre_rows = []
    for pid, p_spec in _PRESET_SPECS:
        p_lbl = t(f"cc_firewall.preset_{pid}")
        existing = by_id.get(pid)
        is_on = bool(existing and existing.get("enabled", True))
        match_s = p_spec.get("dport") or p_spec.get("iface") or p_spec.get("ct") or p_spec.get("limit") or "all"
        verd_s = p_spec["verdict"].upper()
        pre_rows.append([
            Txt(canvas.truncate_plain(p_lbl, col0_w), role="focus" if is_on else "schema"),
            Txt(_dir_short(p_spec["chain"]), role="val" if is_on else "inactive"),
            Txt(p_spec["proto"].upper(), role="val" if is_on else "inactive"),
            Txt(canvas.truncate_plain(match_s, match_max), role="schema"),
            Badge(verd_s, style=_verdict_style(verd_s)) if is_on else f"\u00a0{Txt(verd_s, role='inactive')}\u00a0",
            SelectPill(t("common.on") if is_on else t("common.off"), f"cc_fw:preset:{pid}", selected=is_on),
        ])
    pre_box.add_table(
        headers=[
            t("cc_firewall.preset_rule"),
            t("cc_firewall.dir"),
            t("cc_firewall.proto"),
            t("cc_firewall.port_match"),
            t("cc_firewall.verdict"),
            t("cc_firewall.status"),
        ],
        widths=[col0_w, 5, 6, "flex", 8, 8],
        rows=pre_rows,
        aligns=["left", "left", "left", "left", "left", "right"],
    )

    def _build_preview(bx: Box):
        for ln in _generate_nft_config(store).splitlines():
            bx.add_row(f"  {Txt(canvas.truncate_plain(ln, canvas.inner_cols - 6), role='schema')}")

    pre_box.add_collapsible(cc_panel, "fw_nft_preview", t("cc_firewall.generated_etc_nftables_conf_previe"), _build_preview)
    pre_box.add_row(" ")
    cards.append(pre_box)
    return cards


def build_cards(cc_panel, canvas: Canvas) -> list:
    return _build_chains_tab(cc_panel, canvas)
