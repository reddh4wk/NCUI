#!/usr/bin/env python3
from core.i18n import t
from ui.canvas import Canvas
from ui.kit import ActionBtn, Box, StateBadge, Txt
from panels.cc_engine_auth import _feedback_cards, _probe_polkit


def _build_polkit_standard(cc_panel, canvas: Canvas) -> list:
    d = _probe_polkit()
    cards = _feedback_cards()
    b = Box(
        t("cc_auth.polkit_agent_sudoers_pam_fido2"),
        icon="\uf132",
        actions=[ActionBtn(t("cc_auth.check_sudo_term"), "cc_auth:sudo_term", tone="neutral")],
        highlighted=False,
    )
    b.add_row(" ")
    b.add_kv(t("cc_auth.polkit_agent"), StateBadge(bool(d["agents"]), t("cc_auth.running"), t("cc_auth.none_detected")), right_markup=Txt(canvas.truncate_plain(d["agents"][0], 34), role="schema") if d["agents"] else "")
    grp_lbl = (
        t("cc_auth.sudo_group_active")
        if d.get("priv_group") == "sudo"
        else (t("cc_auth.wheel_active") if d["in_wheel"] else t("cc_auth.no_priv_group"))
    )
    b.add_kv(t("cc_auth.privilege_group"), StateBadge(d["in_wheel"], grp_lbl, grp_lbl))
    b.add_kv(t("cc_auth.pam_fido2_u2f"), StateBadge(d["fido_mod"], t("cc_auth.pam_u2f_installed"), t("cc_auth.pam_u2f_missing")), right_markup=StateBadge(bool(d["fido_keys"]), t("cc_auth.u2f_keys_ok"), t("cc_auth.no_u2f_keys"), on_tone="ok"))
    b.add_kv(
        t("cc_auth.usbguard_daemon"),
        StateBadge(d["usb_active"], t("cc_auth.active"), t("cc_auth.inactive")),
        right_markup=ActionBtn(t("cc_auth.usbguard_helper_term"), "cc_auth:usbguard_term", tone="neutral"),
        requires=["usbguard"],
    )
    b.add_row(" ")
    cards.append(b)
    return cards


def build_cards(cc_panel, canvas: Canvas) -> list:
    return _build_polkit_standard(cc_panel, canvas)
