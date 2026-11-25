#!/usr/bin/env python3
from core.i18n import t
from ui.canvas import Canvas
from ui.kit import ActionBtn, Box, SelectPill, Txt
from panels.cc_engine_waybar import (
    KNOWN_WAYBAR_PLUGINS,
    _discover_user_waybar_plugins,
    _read_wb_config,
    _wb_plugin_meta,
    get_wb_feedback_cards,
)


def build_cards(cc_panel, canvas: Canvas) -> list:
    data = _read_wb_config()
    left_mods = list(data.get("modules-left", []) or [])
    center_mods = list(data.get("modules-center", []) or [])
    right_mods = list(data.get("modules-right", []) or [])
    active_all = set(left_mods + center_mods + right_mods)

    cards = []

    hdr_box = Box(
        t("cc_waybar.waybar_layout_plugins_manager"),
        icon="\uf0db",
        actions=[ActionBtn(t("cc_waybar.apply_reload"), "cc_wb:apply", tone="apply")],
        highlighted=False,
    )
    hdr_box.add_row(f"  {Txt(t('cc_waybar.order_and_move_modules_across_left'), role='schema')}")
    cards.append(hdr_box)

    sections_spec = [
        ("left",   t("cc_waybar.left_section_modules_left"),   left_mods),
        ("center", t("cc_waybar.center_section_modules_center"), center_mods),
        ("right",  t("cc_waybar.right_section_modules_right"),  right_mods),
    ]

    for sec_key, sec_title, mod_list in sections_spec:
        sec_box = Box(f"{sec_title} ({len(mod_list)})", icon="\uf0c9", highlighted=False)
        if not mod_list:
            sec_box.add_row(f"  {Txt(t('cc_waybar.empty_section'), role='inactive')}")
        else:
            sec_rows = []
            for idx, m_id in enumerate(mod_list):
                p_icon, p_lbl, _p_desc = _wb_plugin_meta(m_id)
                up_b = ActionBtn("\u2191", f"cc_wb:up:{sec_key}:{idx}", tone="neutral") if idx > 0 else "   "
                dn_b = ActionBtn("\u2193", f"cc_wb:down:{sec_key}:{idx}", tone="neutral") if idx < len(mod_list) - 1 else "   "
                to_l = SelectPill("L", f"cc_wb:move:{m_id}:left", selected=(sec_key == "left"))
                to_c = SelectPill("C", f"cc_wb:move:{m_id}:center", selected=(sec_key == "center"))
                to_r = SelectPill("R", f"cc_wb:move:{m_id}:right", selected=(sec_key == "right"))
                rm_b = ActionBtn("x", f"cc_wb:remove:{m_id}", tone="danger_soft")
                sec_rows.append([
                    Txt(f"{idx + 1:02d}.", role="schema"),
                    f"{Txt(f'{p_icon}  {p_lbl}', role='focus')}  {Txt(f'({m_id})', role='schema')}",
                    f"{up_b} {dn_b}  {to_l} {to_c} {to_r}  {rm_b}",
                ])
            sec_box.add_table(
                headers=["", "", ""],
                widths=[4, "flex", 32],
                rows=sec_rows,
                aligns=["left", "left", "right"],
                show_headers=False,
            )
        cards.append(sec_box)

    all_known_keys = list(KNOWN_WAYBAR_PLUGINS.keys())
    for k in _discover_user_waybar_plugins():
        if k not in all_known_keys:
            all_known_keys.append(k)
    for k, v in data.items():
        if isinstance(v, dict) and (k.startswith("custom/") or k.startswith("niri/")) and k not in all_known_keys:
            all_known_keys.append(k)

    disabled_mods = [m for m in all_known_keys if m not in active_all]
    pool_box = Box(
        t("cc_waybar.available_disabled_plugins_pool_n", p0=len(disabled_mods)),
        icon="\uf067",
        highlighted=False,
    )
    if not disabled_mods:
        pool_box.add_row(f"  {Txt(t('cc_waybar.all_available_plugins_are_currentl'), role='inactive')}")
    else:
        pool_rows = []
        for m_id in disabled_mods:
            p_icon, p_lbl, p_desc = _wb_plugin_meta(m_id)
            add_l = ActionBtn(t("cc_waybar.left"), f"cc_wb:add:{m_id}:left", tone="apply")
            add_c = ActionBtn(t("cc_waybar.center"), f"cc_wb:add:{m_id}:center", tone="apply")
            add_r = ActionBtn(t("cc_waybar.right"), f"cc_wb:add:{m_id}:right", tone="apply")
            pool_rows.append([
                f"{Txt(f'{p_icon}  {p_lbl}', role='focus')}  {Txt(p_desc, role='schema')}",
                f"{add_l} {add_c} {add_r}",
            ])
        pool_box.add_table(
            headers=["", ""],
            widths=["flex", 30],
            rows=pool_rows,
            aligns=["left", "right"],
            show_headers=False,
        )
    cards.append(pool_box)
    cards.extend(get_wb_feedback_cards())

    return cards
