#!/usr/bin/env python3
from core.config import (
    CONFIG,
    get_network_show_bar_speed,
    get_screen_lock_cmd,
)
from core.i18n import t
from core.state import STATE
from ui.canvas import Canvas
from ui.kit import ActionBtn, Box, ProgressBar, SelectPill, Slider, StateBadge, Txt
from panels.cc_engine_network import get_interfaces_fast
from panels.cc_engine_waybar import estimate_waybar_free_chars_budget, get_wb_feedback_cards


def _build_waybar_options_tab(cc_panel, canvas: Canvas) -> list:
    cards = []

    # ── CARD 1: SCREEN LOCK & SESSION PLUGIN OPTIONS (custom/session) ──
    lock_cmd = get_screen_lock_cmd()
    lock_badge = StateBadge(True, t("cc_waybar.configured"), on_tone="ok") if lock_cmd else StateBadge(True, t("cc_waybar.not_set"), on_tone="warn")
    lock_box = Box(
        t("cc_waybar.screen_lock_session_plugin_custom"),
        icon="\uf023",
        actions=[
            lock_badge,
            ActionBtn(t("cc_waybar.set_lock_command"), "cc_wb:prompt_lock_cmd", tone="neutral"),
        ],
        highlighted=False,
    )
    lock_box.add_row(" ")
    lock_disp = (
        Txt(lock_cmd, role="val")
        if lock_cmd
        else Txt(t("cc_waybar.not_configured_select_a_preset_or"), role="attention")
    )
    lock_box.add_kv(
        t("cc_waybar.lock_command"),
        lock_disp,
        right_markup=ActionBtn(t("cc_waybar.clear"), "cc_wb:set_lock:", tone="danger_soft") if lock_cmd else "",
    )
    presets = [
        ("swaylock", "swaylock -f -c 000000"),
        ("hyprlock", "hyprlock"),
        ("gtklock", "gtklock"),
        ("waylock", "waylock"),
        ("loginctl", "loginctl lock-session"),
    ]
    preset_btns = "  ".join(
        SelectPill(lbl, f"cc_wb:set_lock:{cmd_v}", selected=(lock_cmd == cmd_v))
        for lbl, cmd_v in presets
    )
    lock_box.add_kv(t("cc_waybar.quick_presets"), preset_btns)
    lock_box.add_row(" ")
    cards.append(lock_box)

    # ── CARD 2: NETWORK PLUGIN OPTIONS (custom/netcycle) ──
    show_spd = get_network_show_bar_speed()
    unit_mode = str(STATE.get("network", "speed_unit", CONFIG.get("network", {}).get("default_speed_unit", "bytes")))
    mon_iface = str(STATE.get("network", "monitored_iface", CONFIG.get("network", {}).get("monitored_interfaces", "auto")))
    ifaces = [i["name"] for i in get_interfaces_fast()]

    net_badge = StateBadge(show_spd, t("cc_waybar.speed_on"), t("cc_waybar.icons_only"), on_tone="ok")
    net_box = Box(
        t("cc_waybar.network_pill_plugin_options_custom"),
        icon="\uf6ff",
        actions=[net_badge],
        highlighted=False,
    )
    net_box.add_row(" ")
    spd_on_btn = SelectPill(t("cc_waybar.on_show_speed"), "cc_wb:net_spd:on", selected=show_spd)
    spd_off_btn = SelectPill(t("cc_waybar.off_icons_only"), "cc_wb:net_spd:off", selected=not show_spd)
    net_box.add_kv(
        t("cc_waybar.traffic_speed"),
        f"{spd_on_btn}   {spd_off_btn}",
    )

    u_bytes_btn = SelectPill(t("cc_waybar.bytes_kb_s_mb_s"), "cc_wb:net_unit:bytes", selected=(unit_mode == "bytes"))
    u_bits_btn = SelectPill(t("cc_waybar.bits_kb_s_mb_s"), "cc_wb:net_unit:bits", selected=(unit_mode == "bits"))
    net_box.add_kv(
        t("cc_waybar.speed_unit"),
        f"{u_bytes_btn}   {u_bits_btn}",
    )

    if_candidates = ["auto", "ALL"] + ifaces[:6]
    if_btns = "  ".join(
        SelectPill(ic_name, f"cc_wb:net_if:{ic_name}", selected=(mon_iface == ic_name))
        for ic_name in if_candidates
    )
    net_box.add_kv(t("cc_waybar.monitored_dev"), if_btns)
    net_box.add_row(" ")
    cards.append(net_box)

    # ── CARD 3: LUNGHEZZA PERCENTUALE BARRA DEL TITOLO E SPOTIFY/MUSICA ──
    budget = estimate_waybar_free_chars_budget()
    win_pct = budget["window_pct"]
    med_pct = budget["media_pct"]
    win_chars = budget["window_chars"]
    med_chars = budget["media_chars"]
    tot_chars = budget["total_free_chars"]
    mon_w = budget["mon_w"]
    has_win = budget["has_window"]
    has_med = budget["has_media"]

    apply_btn = ActionBtn(t("cc_waybar.apply_reload"), "cc_wb:apply", tone="apply")
    len_box = Box(
        t("cc_waybar.elastic_text_bars_width_free_bar_s"),
        icon="\uf07e",
        actions=[Txt(t("cc_waybar.px_chars", p0=mon_w, p1=tot_chars), role="schema"), apply_btn],
        highlighted=False,
    )

    win_status = t("cc_waybar.on_bar") if has_win else t("cc_waybar.disabled")
    len_box.add_section(
        t("cc_waybar.window_title_bar_niri_window", p0=win_status),
        actions=[Txt(t("cc_waybar.chars", p0=win_pct, p1=win_chars), role="schema")],
    )
    win_slider = Slider(
        win_pct,
        "cc_wb:win_pct",
        min_val=5,
        max_val=100,
        width=28,
        show_pct=True,
        step_buttons=True,
        step=5,
        enabled=bool(has_win),
    )
    len_box.add_kv(t("cc_waybar.title_width"), win_slider)
    win_presets = "  ".join(
        SelectPill(f"{p}%", f"cc_wb:win_pct:{p}", selected=(abs(win_pct - p) <= 2))
        for p in (15, 25, 35, 45, 60, 75, 90)
    )
    len_box.add_kv(t("cc_waybar.quick_presets"), win_presets)

    med_status = t("cc_waybar.on_bar") if has_med else t("cc_waybar.disabled")
    len_box.add_section(
        t("cc_waybar.spotify_music_bar_custom_media", p0=med_status),
        actions=[Txt(t("cc_waybar.chars", p0=med_pct, p1=med_chars), role="schema")],
    )
    med_slider = Slider(
        med_pct,
        "cc_wb:med_pct",
        min_val=5,
        max_val=100,
        width=28,
        show_pct=True,
        step_buttons=True,
        step=5,
        enabled=bool(has_med),
    )
    len_box.add_kv(t("cc_waybar.music_width"), med_slider)
    med_presets = "  ".join(
        SelectPill(f"{p}%", f"cc_wb:med_pct:{p}", selected=(abs(med_pct - p) <= 2))
        for p in (15, 25, 35, 45, 60, 75, 90)
    )
    len_box.add_kv(t("cc_waybar.quick_presets"), med_presets)

    used_pct = min(100, (win_pct if has_win else 0) + (med_pct if has_med else 0))
    free_margin_pct = max(0, 100 - used_pct)
    len_box.add_section(t("cc_waybar.available_space_allocation"))
    len_box.add_kv(
        t("cc_waybar.total_allocated"),
        f"{ProgressBar(used_pct, width=24, color_by_threshold=False)}   "
        f"{Txt(t('cc_waybar.used', p0=used_pct), role='val')} {Txt(t('cc_waybar.margin', p0=free_margin_pct), role='schema')}",
    )
    len_box.add_row(Txt(t("cc_waybar.music_bar_updates_live_immediately"), role="schema"))
    cards.append(len_box)

    # ── CARD 4: CPU & RAM CRITICAL GLOW (custom/cpu, custom/ram) ──
    sys_glow = bool(STATE.get("waybar", "sys_glow_enabled", True))
    sys_thresh = max(50, min(95, int(STATE.get("waybar", "sys_glow_threshold", 80) or 80)))
    sys_badge = StateBadge(sys_glow, t("cc_waybar.sys_glow_on", p0=sys_thresh), t("cc_waybar.sys_glow_off"), on_tone="crit")
    sys_box = Box(
        t("cc_waybar.sys_glow_card_title"),
        icon="\uf2db",
        actions=[sys_badge],
        highlighted=False,
    )
    sys_box.add_row(" ")
    sys_box.add_kv(
        t("cc_waybar.sys_glow_enable_lbl"),
        f"{SelectPill(t('common.on'), 'cc_wb:sys_glow:1', selected=sys_glow)} "
        f"{SelectPill(t('common.off'), 'cc_wb:sys_glow:0', selected=not sys_glow)}",
    )
    sys_box.add_kv(
        t("cc_waybar.sys_glow_thresh_lbl"),
        Slider(
            sys_thresh,
            "cc_wb:sys_glow_thresh",
            min_val=50,
            max_val=95,
            width=18,
            show_pct=True,
            step_buttons=True,
            step=5,
            enabled=sys_glow,
        ),
    )
    sys_box.add_row(" ")
    cards.append(sys_box)

    # ── CARD 5: PRINT SPOOLER INDICATOR PLUGIN (custom/printers) ──
    prn_vis = str(STATE.get("waybar", "printers_bar_mode", "auto") or "auto").lower()
    if prn_vis not in ("auto", "always"):
        prn_vis = "auto"
    prn_badge = (
        StateBadge(True, t("cc_waybar.prn_auto_badge"), on_tone="ok")
        if prn_vis == "auto"
        else StateBadge(True, t("cc_waybar.prn_always_badge"), on_tone="ok")
    )
    prn_box = Box(
        t("cc_waybar.printers_plugin_custom"),
        icon="\uf02f",
        actions=[
            prn_badge,
            ActionBtn(t("cc_waybar.open_printers_sec"), "cc:section:printers", tone="neutral"),
        ],
        highlighted=False,
    )
    prn_box.add_row(" ")
    vis_pills = (
        f"{SelectPill(t('cc_waybar.prn_vis_auto'), 'cc_wb:prn_vis:auto', selected=(prn_vis == 'auto'))} "
        f"{SelectPill(t('cc_waybar.prn_vis_always'), 'cc_wb:prn_vis:always', selected=(prn_vis == 'always'))}"
    )
    prn_box.add_kv(t("cc_waybar.prn_vis_lbl"), vis_pills)
    prn_box.add_row(" ")
    cards.append(prn_box)
    cards.extend(get_wb_feedback_cards())

    return cards



def build_cards(cc_panel, canvas: Canvas) -> list:
    return _build_waybar_options_tab(cc_panel, canvas)
