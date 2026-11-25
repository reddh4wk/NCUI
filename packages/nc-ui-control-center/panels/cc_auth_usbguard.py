#!/usr/bin/env python3
from core.i18n import t
from core.privileges import build_privilege_modal_card
from core.state import STATE
from ui.canvas import Canvas
from ui.kit import (
    ActionBtn,
    Box,
    CyclePill,
    InlineFilter,
    SelectPill,
    StateBadge,
    Txt,
)
from panels.cc_engine_auth import _feedback_cards, probe_usbguard_devices


def build_cards(cc_panel, canvas: Canvas) -> list:
    d = probe_usbguard_devices()
    cards = _feedback_cards() + build_privilege_modal_card(cc_panel, canvas)

    show_hubs = bool(STATE.get("auth", "usb_show_hubs", False))
    persist = str(STATE.get("auth", "usb_persist", "perm") or "perm")
    st_flt = str(STATE.get("auth", "usb_status_filter", "all") or "all")
    q_txt = str(STATE.get("auth", "usb_query", "") or "")
    q_low = q_txt.strip().lower()

    pol_acts = [StateBadge(d["active"], t("cc_auth.running"), t("cc_auth.inactive"), on_tone="ok")]
    if not d["priv_ok"]:
        pol_acts.append(ActionBtn(t("cc_auth.authorize"), "cc_auth:usb_auth", tone="apply"))
    pol_acts.append(ActionBtn(t("cc_auth.usb_refresh"), "cc_auth:usb_refresh", tone="neutral"))

    pol_box = Box(t("cc_auth.usb_policy_title"), icon="\uf287", actions=pol_acts, highlighted=False)
    pol_box.add_row(" ")

    st_lbl = {
        "all": t("cc_auth.usb_flt_all"),
        "allow": t("cc_auth.usb_flt_allow"),
        "block": t("cc_auth.usb_flt_block"),
    }.get(st_flt, t("cc_auth.usb_flt_all"))
    flt_widget = InlineFilter(
        "cc_auth_usb_q",
        q_txt,
        t("cc_auth.usb_filter_ph"),
        "cc_auth:usb_q_open",
        clear_uri="cc_auth:usb_q_clear" if q_txt else "",
        width=28,
        compact=True,
    )
    pol_box.add_kv(
        t("cc_auth.usb_filter_lbl"),
        flt_widget,
        right_markup=CyclePill(st_lbl, "cc_auth:usb_status_cycle", active=(st_flt != "all")),
    )
    pol_box.add_kv(
        t("cc_auth.usb_scope_lbl"),
        f"{SelectPill(t('cc_auth.usb_scope_periph'), 'cc_auth:usb_scope:periph', selected=(not show_hubs))} "
        f"{SelectPill(t('cc_auth.usb_scope_all'), 'cc_auth:usb_scope:all', selected=show_hubs)}",
    )
    pol_box.add_kv(
        t("cc_auth.usb_persist_lbl"),
        f"{SelectPill(t('cc_auth.usb_persist_perm'), 'cc_auth:usb_persist:perm', selected=(persist == 'perm'))} "
        f"{SelectPill(t('cc_auth.usb_persist_temp'), 'cc_auth:usb_persist:temp', selected=(persist == 'temp'))}",
    )
    pol_box.add_row(" ")
    cards.append(pol_box)

    filtered = []
    for dev in d["devices"]:
        if not show_hubs and dev["is_hub"]:
            continue
        if st_flt == "allow" and dev["status"] != "allow":
            continue
        if st_flt == "block" and dev["status"] == "allow":
            continue
        if q_low:
            hay = f"{dev['id']} {dev['name']} {dev['vid_pid']} {dev['cls']} {dev['port']}".lower()
            if q_low not in hay:
                continue
        filtered.append(dev)

    dev_box = Box(t("cc_auth.usb_devices_n", p0=len(filtered)), icon="\uf1e6", highlighted=False)
    dev_box.add_row(" ")

    if not d["installed"]:
        dev_box.add_row(f"  {Txt(t('cc_auth.usb_missing_bin'), role='attention')}")
    elif not d["priv_ok"]:
        dev_box.add_row(
            f"  {Txt(t('cc_auth.usb_priv_hint'), role='attention')}",
            right_markup=ActionBtn(t("cc_auth.authorize"), "cc_auth:usb_auth", tone="apply"),
        )
    elif d["error"]:
        dev_box.add_row(f"  {Txt(d['error'], role='fault')}")
    elif not filtered:
        dev_box.add_row(f"  {Txt(t('cc_auth.usb_empty'), role='schema')}")
    else:
        act_w = max(len(t("cc_auth.usb_allow_btn")), len(t("cc_auth.usb_block_btn")))
        rows = []
        for dev in filtered:
            st = dev["status"]
            st_badge = StateBadge(
                st == "allow",
                t("cc_auth.usb_allowed"),
                t("cc_auth.usb_blocked") if st == "block" else t("cc_auth.usb_rejected"),
                on_tone="ok",
            )
            if dev["is_root_hub"]:
                act_cell = Txt(t("cc_auth.usb_root_hub"), role="schema")
            elif st == "allow":
                act_cell = ActionBtn(
                    t("cc_auth.usb_block_btn"),
                    f"cc_auth:usb_act:block:{dev['id']}",
                    tone="danger_soft",
                    min_chars=act_w,
                )
            else:
                act_cell = ActionBtn(
                    t("cc_auth.usb_allow_btn"),
                    f"cc_auth:usb_act:allow:{dev['id']}",
                    tone="apply",
                    min_chars=act_w,
                )
            rows.append([
                Txt(dev["id"], role="schema"),
                Txt(canvas.truncate_plain(dev["name"], 32), role="focus"),
                Txt(dev["cls"], role="schema"),
                Txt(dev["vid_pid"] or "-", role="val"),
                st_badge,
                act_cell,
            ])

        dev_box.add_table(
            headers=[
                t("cc_auth.usb_col_id"),
                t("cc_auth.usb_col_device"),
                t("cc_auth.usb_col_class"),
                t("cc_auth.usb_col_vidpid"),
                t("cc_auth.usb_col_state"),
                t("cc_auth.actions"),
            ],
            widths=[4, "flex", 9, 10, 10, max(10, act_w + 3)],
            rows=rows,
            aligns=["left", "left", "left", "left", "left", "right"],
        )

    dev_box.add_row(" ")
    cards.append(dev_box)
    return cards
