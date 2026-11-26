#!/usr/bin/env python3
"""NC-UI Control Center — Personalization -> Niri Compositor & Ghostty Terminal."""

from core import probe
from core.i18n import t
from ui.canvas import Canvas
from ui.kit import ActionBtn, Badge, Box, SelectPill, StateBadge, feedback_cards
from panels.cc_engine_ecosystem import (
    GHOSTTY_CURSOR_STYLES,
    GHOSTTY_KEYBIND_PROFILES,
    GHOSTTY_OPACITY_OPTIONS,
    NIRI_CORNER_OPTIONS,
    NIRI_RING_STYLES,
    NIRI_RING_WIDTHS,
    get_editor_ecosystem_config,
    is_ghostty_applied,
    is_ghostty_dirty,
    is_niri_applied,
    is_niri_dirty,
)


def build_cards(cc_panel, canvas: Canvas) -> list:
    cfg = get_editor_ecosystem_config()
    n_cfg = cfg["niri"]
    g_cfg = cfg["ghostty"]

    n_dirty = is_niri_dirty()
    n_applied = is_niri_applied()
    has_niri = probe.has_bin("niri")

    g_dirty = is_ghostty_dirty()
    g_applied = is_ghostty_applied()
    has_ghostty = probe.has_bin("ghostty")

    cards = feedback_cards("ecosystem", "cc_ecosystem.status_title")

    # =====================================================================
    # BOX 1 — Niri Compositor Theme Sync & Geometry
    # =====================================================================
    n_acts = [
        Badge(t("cc_personalization.wp_badge_preview"), style="warn")
        if n_dirty
        else StateBadge(n_applied, t("cc_ecosystem.state_applied"), t("cc_ecosystem.state_not_applied")),
        ActionBtn(t("cc_personalization.reset"), "cc_eco:niri:reset", tone="warn" if n_dirty else "muted"),
        ActionBtn(t("cc_personalization.apply"), "cc_eco:niri:apply", tone="apply" if (n_dirty or not n_applied) else "muted"),
    ]
    if not has_niri:
        n_acts.insert(0, Badge(t("ui.missing", p0="niri"), style="warn"))

    n_box = Box(t("cc_ecosystem.box_niri"), icon="\uf2d2", actions=n_acts, highlighted=False)
    n_box.add_row(" ")

    n_box.add_kv(
        t("cc_ecosystem.lbl_auto_sync"),
        f"{SelectPill(t('common.on'), 'cc_eco:niri:auto_sync:1', selected=n_cfg['auto_sync'], dirty=(n_dirty and n_cfg['auto_sync']))} "
        f"{SelectPill(t('common.off'), 'cc_eco:niri:auto_sync:0', selected=not n_cfg['auto_sync'], dirty=(n_dirty and not n_cfg['auto_sync']))}",
    )

    ring_pills = " ".join(
        SelectPill(
            t(f"cc_ecosystem.ring_{rs}"),
            f"cc_eco:niri:ring_style:{rs}",
            selected=(n_cfg["focus_ring_style"] == rs),
            dirty=(n_dirty and n_cfg["focus_ring_style"] == rs),
        )
        for rs in NIRI_RING_STYLES
    )
    n_box.add_kv(t("cc_ecosystem.lbl_ring_style"), ring_pills)

    ring_off = n_cfg["focus_ring_style"] == "off"
    width_pills = " ".join(
        SelectPill(
            f"{rw}px",
            f"cc_eco:niri:ring_width:{rw}",
            selected=(not ring_off and n_cfg["focus_ring_width"] == rw),
            dirty=(n_dirty and not ring_off and n_cfg["focus_ring_width"] == rw),
            disabled=ring_off,
        )
        for rw in NIRI_RING_WIDTHS
    )
    n_box.add_kv(t("cc_ecosystem.lbl_ring_width"), width_pills)

    corner_pills = " ".join(
        SelectPill(
            t("cc_ecosystem.sync_ncui") if cr == "sync" else f"{cr}px",
            f"cc_eco:niri:corner:{cr}",
            selected=(n_cfg["corner_radius"] == cr),
            dirty=(n_dirty and n_cfg["corner_radius"] == cr),
        )
        for cr in NIRI_CORNER_OPTIONS
    )
    n_box.add_kv(t("cc_ecosystem.lbl_corner_radius"), corner_pills)

    n_extras = " ".join([
        SelectPill(
            t("cc_ecosystem.opt_tab_indicator"),
            "cc_eco:niri:toggle:sync_tab_indicator",
            selected=n_cfg["sync_tab_indicator"],
            dirty=(n_dirty and n_cfg["sync_tab_indicator"]),
        ),
        SelectPill(
            t("cc_ecosystem.opt_insert_hint"),
            "cc_eco:niri:toggle:sync_insert_hint",
            selected=n_cfg["sync_insert_hint"],
            dirty=(n_dirty and n_cfg["sync_insert_hint"]),
        ),
        SelectPill(
            t("cc_ecosystem.opt_backdrop"),
            "cc_eco:niri:toggle:sync_backdrop",
            selected=n_cfg["sync_backdrop"],
            dirty=(n_dirty and n_cfg["sync_backdrop"]),
        ),
    ])
    n_box.add_kv(t("cc_ecosystem.lbl_niri_extras"), n_extras)
    n_box.add_row(" ")
    cards.append(n_box)

    # =====================================================================
    # BOX 2 — Ghostty Terminal Theme Sync & PowerShell Ergonomics
    # =====================================================================
    g_acts = [
        Badge(t("cc_personalization.wp_badge_preview"), style="warn")
        if g_dirty
        else StateBadge(g_applied, t("cc_ecosystem.state_applied"), t("cc_ecosystem.state_not_applied")),
        ActionBtn(t("cc_personalization.reset"), "cc_eco:ghostty:reset", tone="warn" if g_dirty else "muted"),
        ActionBtn(t("cc_personalization.apply"), "cc_eco:ghostty:apply", tone="apply" if (g_dirty or not g_applied) else "muted"),
    ]
    if not has_ghostty:
        g_acts.insert(0, Badge(t("ui.missing", p0="ghostty"), style="warn"))

    g_box = Box(t("cc_ecosystem.box_ghostty"), icon="\uf120", actions=g_acts, highlighted=False)
    g_box.add_row(" ")

    g_box.add_kv(
        t("cc_ecosystem.lbl_auto_sync"),
        f"{SelectPill(t('common.on'), 'cc_eco:ghostty:auto_sync:1', selected=g_cfg['auto_sync'], dirty=(g_dirty and g_cfg['auto_sync']))} "
        f"{SelectPill(t('common.off'), 'cc_eco:ghostty:auto_sync:0', selected=not g_cfg['auto_sync'], dirty=(g_dirty and not g_cfg['auto_sync']))}",
    )

    kb_pills = " ".join(
        SelectPill(
            t(f"cc_ecosystem.kb_{kp}"),
            f"cc_eco:ghostty:keybind:{kp}",
            selected=(g_cfg["keybind_profile"] == kp),
            dirty=(g_dirty and g_cfg["keybind_profile"] == kp),
        )
        for kp in GHOSTTY_KEYBIND_PROFILES
    )
    g_box.add_kv(t("cc_ecosystem.lbl_keybind_profile"), kb_pills)

    op_pills = " ".join(
        SelectPill(
            t("cc_ecosystem.sync_ncui") if om == "sync" else f"{om}%",
            f"cc_eco:ghostty:opacity:{om}",
            selected=(g_cfg["opacity_mode"] == om),
            dirty=(g_dirty and g_cfg["opacity_mode"] == om),
        )
        for om in GHOSTTY_OPACITY_OPTIONS
    )
    g_box.add_kv(t("cc_ecosystem.lbl_opacity"), op_pills)

    cur_pills = " ".join(
        SelectPill(
            t(f"cc_ecosystem.cur_{cs}"),
            f"cc_eco:ghostty:cursor:{cs}",
            selected=(g_cfg["cursor_style"] == cs),
            dirty=(g_dirty and g_cfg["cursor_style"] == cs),
        )
        for cs in GHOSTTY_CURSOR_STYLES
    )
    blink_pill = SelectPill(
        t("cc_ecosystem.opt_blink"),
        "cc_eco:ghostty:toggle:cursor_blink",
        selected=g_cfg["cursor_blink"],
        dirty=(g_dirty and g_cfg["cursor_blink"]),
    )
    g_box.add_kv(t("cc_ecosystem.lbl_cursor"), f"{cur_pills}  {blink_pill}")

    g_extras = " ".join([
        SelectPill(
            t("cc_ecosystem.opt_copy_select"),
            "cc_eco:ghostty:toggle:copy_on_select",
            selected=g_cfg["copy_on_select"],
            dirty=(g_dirty and g_cfg["copy_on_select"]),
        ),
        SelectPill(
            t("cc_ecosystem.opt_borderless"),
            "cc_eco:ghostty:toggle:borderless",
            selected=g_cfg["borderless"],
            dirty=(g_dirty and g_cfg["borderless"]),
        ),
        SelectPill(
            t("cc_ecosystem.opt_hide_mouse"),
            "cc_eco:ghostty:toggle:hide_mouse",
            selected=g_cfg["hide_mouse"],
            dirty=(g_dirty and g_cfg["hide_mouse"]),
        ),
    ])
    g_box.add_kv(t("cc_ecosystem.lbl_ghostty_extras"), g_extras)
    g_box.add_row(" ")
    cards.append(g_box)

    return cards
