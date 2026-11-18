#!/usr/bin/env python3
from core.i18n import t
from ui.canvas import Canvas
from ui.kit import ActionBtn, Box, SelectPill, StateBadge, Txt
from panels.cc_engine_telemetry import _feedback_cards, _probe_limine

def _build_boot_standard(cc_panel, canvas: Canvas) -> list:
    bm = _probe_limine()
    cards = _feedback_cards()
    b = Box(
        t("cc_settings.bootloader_limine_kernel_uki_entri"),
        icon="\uf2db",
        actions=[StateBadge(bm["readable"], t("cc_settings.config_readable"), t("cc_settings.elevated_hidden")), ActionBtn(t("cc_settings.edit_limine_term"), "cc_set:limine_edit", tone="neutral")],
        highlighted=False,
    )
    b.add_row(" ")
    b.add_kv(t("cc_settings.config_file"), Txt(bm["conf_path"], role="val"))
    to_pills = " ".join(SelectPill(f"{v}s", f"cc_set:limine_to:{v}", selected=(bm["timeout"] == v)) for v in ("0", "3", "5", "10"))
    to_val = to_pills if bm["timeout"] in ("0", "3", "5", "10") else f"{Txt(t('cc_settings.seconds', p0=bm['timeout']), role='val')}  {to_pills}"
    b.add_kv(t("cc_settings.boot_timeout"), to_val, requires=["limine"])
    b.add_kv(t("cc_settings.uki_snapshots"), Txt(t("cc_settings.uki_efi_lvm_snapshots", p0=len(bm['ukis']), p1=bm['snaps']), role="val"))
    b.add_section(t("cc_settings.default_boot_entry_limine"))
    ent_rows = []
    for idx, entry in enumerate(bm["entries"], start=1):
        is_def = (str(idx) == bm["default_entry"])
        ent_rows.append([
            Txt(f"{idx:02d}.", role="schema"),
            Txt(entry, role="focus"),
            SelectPill(t("cc_settings.boot_default") if is_def else t("cc_settings.boot_set_default"), f"cc_set:limine_def:{idx}", selected=is_def),
        ])
    if ent_rows:
        b.add_table(
            headers=["#", t("cc_settings.boot_entry"), t("cc_notifications.action")],
            widths=[4, "flex", 18],
            rows=ent_rows,
            aligns=["left", "left", "right"],
            show_headers=False,
            requires=["limine"],
        )
    b.add_row(" ")
    cards.append(b)
    return cards


def build_cards(cc_panel, canvas: Canvas) -> list:
    return _build_boot_standard(cc_panel, canvas)
