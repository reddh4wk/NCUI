#!/usr/bin/env python3
"""NC-UI Control Center — Personalization -> Zsh Cyberpunk Theme & Wizard."""

from core import probe
from core.i18n import t
from ui.canvas import Canvas
from ui.kit import ActionBtn, Badge, Box, SelectPill, StateBadge, Txt, feedback_cards
from panels.cc_engine_zsh import (
    ACCENT_MODES,
    HIST_SIZE_OPTIONS,
    ZSH_PRESET_IDS,
    get_editor_zsh_config,
    has_zsh_plugin,
    is_zsh_draft_dirty,
    is_zsh_theme_applied,
    render_zsh_preview_lines,
)


def build_cards(cc_panel, canvas: Canvas) -> list:
    cfg = get_editor_zsh_config()
    dirty = is_zsh_draft_dirty()
    applied = is_zsh_theme_applied()
    has_zsh = probe.has_bin("zsh")
    cards = feedback_cards("zsh", "cc_zsh.status_title")

    # =====================================================================
    # BOX 1 — Cyberpunk Prompt Presets, Live Preview & Palette Sync
    # =====================================================================
    hdr_acts = [
        Badge(t("cc_personalization.wp_badge_preview"), style="warn")
        if dirty
        else StateBadge(applied, t("cc_zsh.state_synced"), t("cc_zsh.state_not_applied")),
        ActionBtn(t("cc_personalization.reset"), "cc_zsh:reset", tone="warn" if dirty else "muted"),
        ActionBtn(t("cc_personalization.apply"), "cc_zsh:apply", tone="apply" if (dirty or not applied) else "muted"),
    ]
    p_box = Box(t("cc_zsh.box_prompt"), icon="\uf120", actions=hdr_acts, highlighted=False)
    p_box.add_row(" ")

    preset_pills = " ".join(
        SelectPill(
            t(f"cc_zsh.preset_{pid}"),
            f"cc_zsh:preset:{pid}",
            selected=(cfg["preset"] == pid),
            dirty=(dirty and cfg["preset"] == pid),
        )
        for pid in ZSH_PRESET_IDS
    )
    p_box.add_kv(t("cc_zsh.lbl_preset"), preset_pills)

    accent_pills = " ".join(
        SelectPill(
            t(f"cc_zsh.accent_{am}"),
            f"cc_zsh:accent:{am}",
            selected=(cfg["accent_mode"] == am),
            dirty=(dirty and cfg["accent_mode"] == am),
        )
        for am in ACCENT_MODES
    )
    p_box.add_kv(t("cc_zsh.lbl_accent_mode"), accent_pills)

    prev_lines = render_zsh_preview_lines(cfg, canvas)
    for idx, pline in enumerate(prev_lines):
        p_box.add_kv(t("cc_zsh.lbl_preview") if idx == 0 else "", pline)

    p_box.add_section(t("cc_zsh.sec_modules"))
    mod_row_1 = " ".join([
        SelectPill(t("cc_zsh.mod_git"), "cc_zsh:toggle:show_git", selected=cfg["show_git"]),
        SelectPill(t("cc_zsh.mod_exit"), "cc_zsh:toggle:show_exit", selected=cfg["show_exit"]),
        SelectPill(t("cc_zsh.mod_duration"), "cc_zsh:toggle:show_duration", selected=cfg["show_duration"]),
        SelectPill(t("cc_zsh.mod_clock"), "cc_zsh:toggle:show_clock", selected=cfg["show_clock"]),
        SelectPill(t("cc_zsh.mod_context"), "cc_zsh:toggle:show_context", selected=cfg["show_context"]),
    ])
    p_box.add_kv(t("cc_zsh.lbl_modules"), mod_row_1)
    p_box.add_kv(
        t("cc_zsh.lbl_palette_sync"),
        f"{SelectPill(t('common.on'), 'cc_zsh:sync:1', selected=cfg['auto_sync_theme'])} "
        f"{SelectPill(t('common.off'), 'cc_zsh:sync:0', selected=not cfg['auto_sync_theme'])}",
    )
    p_box.add_row(" ")
    cards.append(p_box)

    # =====================================================================
    # BOX 2 — Shell Behavior, History & Optional Zsh Plugins
    # =====================================================================
    b_box = Box(t("cc_zsh.box_behavior"), icon="\uf085", highlighted=False)
    b_box.add_row(" ")
    hist_pills = " ".join(
        SelectPill(
            f"{hs // 1000}k",
            f"cc_zsh:hist_size:{hs}",
            selected=(cfg["hist_size"] == hs),
        )
        for hs in HIST_SIZE_OPTIONS
    )
    hist_opts = " ".join([
        SelectPill(t("cc_zsh.opt_share_history"), "cc_zsh:toggle:share_history", selected=cfg["share_history"]),
        SelectPill(t("cc_zsh.opt_ignore_dups"), "cc_zsh:toggle:hist_ignore_dups", selected=cfg["hist_ignore_dups"]),
    ])
    b_box.add_kv(t("cc_zsh.lbl_history"), f"{hist_pills}  {hist_opts}")

    feat_pills = " ".join([
        SelectPill(t("cc_zsh.opt_completion"), "cc_zsh:toggle:completion_menu", selected=cfg["completion_menu"]),
        SelectPill(t("cc_zsh.opt_keybindings"), "cc_zsh:toggle:fix_keybindings", selected=cfg["fix_keybindings"]),
        SelectPill(t("cc_zsh.opt_autocd"), "cc_zsh:toggle:auto_cd", selected=cfg["auto_cd"]),
    ])
    b_box.add_kv(t("cc_zsh.lbl_ergonomics"), feat_pills)

    as_ok = has_zsh_plugin("autosuggestions")
    sh_ok = has_zsh_plugin("syntax_highlighting")
    plug_pills = " ".join([
        SelectPill(
            "zsh-autosuggestions",
            "cc_zsh:toggle:plugin_autosuggestions",
            selected=(as_ok and cfg["plugin_autosuggestions"]),
            disabled=not as_ok,
        ),
        SelectPill(
            "zsh-syntax-highlighting",
            "cc_zsh:toggle:plugin_syntax_highlighting",
            selected=(sh_ok and cfg["plugin_syntax_highlighting"]),
            disabled=not sh_ok,
        ),
    ])
    missing_plugs = []
    if not as_ok:
        missing_plugs.append("zsh-autosuggestions")
    if not sh_ok:
        missing_plugs.append("zsh-syntax-highlighting")
    plug_warn = Badge(t("ui.missing", p0=missing_plugs[0]), style="warn") if missing_plugs else ""
    b_box.add_kv(t("cc_zsh.lbl_plugins"), plug_pills, right_markup=plug_warn)
    b_box.add_row(" ")
    cards.append(b_box)

    # =====================================================================
    # BOX 3 — Interactive Guided Zsh Setup Wizard (Embedded Terminal)
    # =====================================================================
    wiz_acts = [
        ActionBtn(
            t("cc_zsh.btn_launch_wizard"),
            "cc_zsh:launch_wizard",
            tone="apply" if has_zsh else "muted",
        )
    ]
    if not has_zsh:
        wiz_acts.insert(0, Badge(t("ui.missing", p0="zsh"), style="warn"))
    w_box = Box(t("cc_zsh.box_wizard"), icon="\uf0d0", actions=wiz_acts, highlighted=False)
    w_box.add_row(" ")
    w_box.add_row(f"  {Txt(t('cc_zsh.wizard_desc'), role='schema')}")
    w_box.add_row(" ")
    cards.append(w_box)

    return cards
