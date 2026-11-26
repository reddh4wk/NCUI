#!/usr/bin/env python3
from core import probe
from core.i18n import t
from ui.canvas import Canvas
from ui.kit import ActionBtn, Box, StateBadge, Txt
from panels.cc_engine_auth import _feedback_cards, _probe_tpm_luks


def _build_tpm_standard(cc_panel, canvas: Canvas) -> list:
    d = _probe_tpm_luks()
    cards = _feedback_cards()
    b = Box(
        t("cc_auth.uefi_secure_boot_uki_tpm2_luks2"),
        icon="\uf084",
        actions=[StateBadge(d["tpm"], t("cc_auth.tpm2_present"), t("cc_auth.no_tpm2")), StateBadge(d["sb_enabled"], t("cc_auth.secure_boot"), t("cc_auth.sb_off"))],
        highlighted=False,
    )
    b.add_row(" ")
    b.add_kv(t("cc_auth.chip_tpm_2_0"), StateBadge(d["tpm"], t("cc_auth.detected_dev_tpmrm0"), t("cc_auth.not_detected")))
    b.add_kv(t("cc_auth.sbctl_verify_uki"), ActionBtn(t("cc_auth.verify_uki_signatures"), "cc_auth:sbctl_verify", tone="apply"), requires=["sbctl"])
    b.add_kv(t("cc_auth.efi_uki_images"), Txt(t("cc_auth.uki_found", p0=len(d["ukis"])) if d["ukis"] else t("cc_auth.needs_elevated_efi"), role="val" if d["ukis"] else "inactive"))

    if d["sb_lines"]:
        def _fill_sb(bx: Box):
            for ln in d["sb_lines"]:
                bx.add_row(f"  {Txt(ln, role='schema')}")
        b.add_collapsible(cc_panel, "auth_sbctl", t("cc_auth.sbctl_status_details"), _fill_sb)

    luks_label = ", ".join(f"{dev} ({sz})" for dev, sz in d["luks_devs"]) or t("cc_auth.none_detected_or_hidden")
    luks_acts = []
    if d["luks_devs"]:
        luks_acts.append(ActionBtn(t("cc_auth.inspect_slots_term"), "cc_auth:luks_term", tone="neutral"))
        if d["tpm"] and probe.has_bin("systemd-cryptenroll"):
            luks_acts.append(ActionBtn(t("cc_auth.tpm2_enroll_term"), "cc_auth:tpm_enroll", tone="warn"))
    b.add_kv(
        t("cc_auth.luks2_containers"),
        Txt(luks_label, role="val" if d["luks_devs"] else "inactive"),
        right_markup=" ".join(luks_acts),
        requires=["cryptsetup"],
    )
    b.add_row(" ")
    cards.append(b)
    return cards


def build_cards(cc_panel, canvas: Canvas) -> list:
    return _build_tpm_standard(cc_panel, canvas)
