#!/usr/bin/env python3
import re
from core.i18n import t, t_list
from core.state import STATE
from ui.canvas import Canvas
from ui.kit import (
    ActionBtn,
    Box,
    FoldToggle,
    SelectPill,
    Txt,
    compute_fit_scroll_rows,
    set_scroll_viewport_max_off,
)
from panels.cc_engine_waybar import (
    INTERNAL_SPACER_MODS,
    KNOWN_WAYBAR_PLUGINS,
    _discover_user_waybar_plugins,
    _read_wb_config,
    _wb_plugin_meta,
    get_wb_feedback_cards,
)

_GUIDE_ROLE_PREFIXES = {
    "H:": "focus",
    "C:": "focus_val",
    "T:": "schema",
    "W:": "attention",
    "G:": "vitality",
    "D:": "structure",
    "I:": "inactive",
    "E:": "interrupt_rest",
}
_GUIDE_INLINE_ROLES = {
    "f": "focus",
    "v": "focus_val",
    "s": "schema",
    "g": "vitality",
    "w": "attention",
    "e": "interrupt_rest",
    "d": "structure",
    "i": "inactive",
}
_GUIDE_SPAN_RE = re.compile(r"«([fvsgwedi]):([^»]*)»")


def _format_guide_line(raw: str, canvas: Canvas, max_cols: int) -> str:
    if not raw or not raw.strip():
        return " "
    base_role = "schema"
    txt = raw
    for pfx, r_name in _GUIDE_ROLE_PREFIXES.items():
        if raw.startswith(pfx):
            base_role = r_name
            txt = raw[len(pfx):]
            break

    if "«" not in txt:
        return f"  {Txt(canvas.truncate_plain(txt, max_cols), role=base_role)}"

    parts: list[str] = []
    rem = max_cols
    pos = 0
    for m in _GUIDE_SPAN_RE.finditer(txt):
        if m.start() > pos and rem > 0:
            plain = txt[pos:m.start()]
            seg = canvas.truncate_plain(plain, rem) if canvas.measure_cols(Txt(plain, role=base_role)) > rem else plain
            if seg:
                parts.append(Txt(seg, role=base_role))
                rem = max(0, rem - canvas.measure_cols(parts[-1]))
        if rem <= 0:
            pos = m.end()
            break
        span_role = _GUIDE_INLINE_ROLES[m.group(1)]
        span_txt = m.group(2)
        seg = canvas.truncate_plain(span_txt, rem) if canvas.measure_cols(Txt(span_txt, role=span_role)) > rem else span_txt
        if seg:
            parts.append(Txt(seg, role=span_role))
            rem = max(0, rem - canvas.measure_cols(parts[-1]))
        pos = m.end()
    if pos < len(txt) and rem > 0:
        plain = txt[pos:]
        seg = canvas.truncate_plain(plain, rem) if canvas.measure_cols(Txt(plain, role=base_role)) > rem else plain
        if seg:
            parts.append(Txt(seg, role=base_role))
    return f"  {''.join(parts)}" if parts else " "


def _build_plugin_guide_box(cc_panel, canvas: Canvas, prior_cards: list) -> Box:
    is_open, fold_btn = FoldToggle(cc_panel, "wb_plugin_guide_open", default=True, uri_prefix="ui:state")
    if not is_open:
        set_scroll_viewport_max_off("cc_wb:guide_scroll", 0)
        guide_box = Box(
            t("cc_waybar.plugin_guide_title"),
            icon="\uf02d",
            actions=[fold_btn],
            highlighted=False,
        )
        guide_box.add_row(" ")
        guide_box.add_row(f"  {Txt(t('cc_waybar.plugin_guide_summary'), role='schema')}")
        guide_box.add_row(" ")
        return guide_box

    raw_lines = [str(ln) for ln in t_list("cc_waybar.plugin_guide_lines", default=[])]
    tot_lines = len(raw_lines)
    win_rows = compute_fit_scroll_rows(cc_panel, canvas, prior_cards, tot_lines, min_rows=10)
    max_off = max(0, tot_lines - win_rows)
    raw_off = int(STATE.get("waybar", "plugin_guide_scroll", 0) or 0)
    cur_off = max(0, min(max_off, raw_off))

    max_txt_cols = max(40, canvas.inner_cols - 6)
    fmt_rows = [
        _format_guide_line(ln, canvas, max_txt_cols)
        for ln in raw_lines[cur_off:min(tot_lines, cur_off + win_rows)]
    ]

    guide_box = Box(t("cc_waybar.plugin_guide_title"), icon="\uf02d", highlighted=False)
    guide_box.fill_window = True
    guide_box.add_scroll_viewport(
        fmt_rows,
        total_items=tot_lines,
        win_rows=win_rows,
        offset=raw_off,
        action_prefix="cc_wb:guide_scroll",
        state_ns="waybar",
        state_key="plugin_guide_scroll",
        anchor="top",
        trailing_actions=[fold_btn],
    )
    return guide_box


def _display_mod_id(m_id: str) -> str:
    if m_id in KNOWN_WAYBAR_PLUGINS and m_id.startswith("custom/"):
        return "ncui/" + m_id[len("custom/"):]
    return m_id


def _is_removable_custom_mod(m_id: str, user_plugins: dict) -> bool:
    return (m_id in user_plugins) or (m_id.startswith("custom/") and m_id not in KNOWN_WAYBAR_PLUGINS)


def build_cards(cc_panel, canvas: Canvas) -> list:
    data = _read_wb_config()
    user_plugins = _discover_user_waybar_plugins()
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

    all_known_keys = list(KNOWN_WAYBAR_PLUGINS.keys())
    for k in user_plugins:
        if k not in all_known_keys:
            all_known_keys.append(k)
    for k, v in data.items():
        if k in INTERNAL_SPACER_MODS:
            continue
        if isinstance(v, dict) and (k.startswith("custom/") or k.startswith("niri/")) and k not in all_known_keys:
            all_known_keys.append(k)

    disabled_mods = [m for m in all_known_keys if m not in active_all]
    meta_cache = {m: _wb_plugin_meta(m) for m in (left_mods + center_mods + right_mods + disabled_mods)}
    icon_col_w = 2 if any(canvas.measure_cols(Txt(mc[0], role="focus")) > 1 for mc in meta_cache.values()) else 1

    flex_cols = max(16, canvas.inner_cols - 3 - icon_col_w - 32 - 6)

    for sec_key, sec_title, mod_list in sections_spec:
        sec_box = Box(f"{sec_title} ({len(mod_list)})", icon="\uf0c9", highlighted=False)
        if not mod_list:
            sec_box.add_row(f"  {Txt(t('cc_waybar.empty_section'), role='inactive')}")
        else:
            sec_rows = []
            for idx, m_id in enumerate(mod_list):
                p_icon, p_lbl, _p_desc = meta_cache[m_id]
                up_b = ActionBtn("\u2191", f"cc_wb:up:{sec_key}:{idx}", tone="neutral") if idx > 0 else "   "
                dn_b = ActionBtn("\u2193", f"cc_wb:down:{sec_key}:{idx}", tone="neutral") if idx < len(mod_list) - 1 else "   "
                to_l = SelectPill("L", f"cc_wb:move:{m_id}:left", selected=(sec_key == "left"))
                to_c = SelectPill("C", f"cc_wb:move:{m_id}:center", selected=(sec_key == "center"))
                to_r = SelectPill("R", f"cc_wb:move:{m_id}:right", selected=(sec_key == "right"))
                rm_b = ActionBtn("x", f"cc_wb:remove:{m_id}", tone="danger_soft")

                id_plain = f"({_display_mod_id(m_id)})"
                suffix_len = 2 + len(id_plain)
                lbl_shown = canvas.truncate_plain(p_lbl, max(6, flex_cols - suffix_len))
                sec_rows.append([
                    Txt(f"{idx + 1:02d}.", role="schema"),
                    Txt(p_icon, role="focus"),
                    f"{Txt(lbl_shown, role='focus')}  {Txt(id_plain, role='schema')}",
                    f"{up_b} {dn_b}  {to_l} {to_c} {to_r}  {rm_b}",
                ])
            sec_box.add_table(
                headers=["", "", "", ""],
                widths=[3, icon_col_w, "flex", 32],
                rows=sec_rows,
                aligns=["left", "left", "left", "right"],
                show_headers=False,
            )
        cards.append(sec_box)

    has_custom_in_pool = any(_is_removable_custom_mod(m, user_plugins) for m in disabled_mods)
    pool_acts_w = 40 if has_custom_in_pool else 30
    pool_flex_cols = max(16, canvas.inner_cols - icon_col_w - pool_acts_w - 4)
    pool_box = Box(
        t("cc_waybar.available_disabled_plugins_pool_n", p0=len(disabled_mods)),
        icon="\uf067",
        actions=[ActionBtn(t("cc_waybar.link_plugin_btn"), "cc_wb:prompt_link_plugin", tone="apply")],
        highlighted=False,
    )
    if not disabled_mods:
        pool_box.add_row(f"  {Txt(t('cc_waybar.all_available_plugins_are_currentl'), role='inactive')}")
    else:
        pool_rows = []
        for m_id in disabled_mods:
            p_icon, p_lbl, p_desc = meta_cache[m_id]
            add_l = ActionBtn(t("cc_waybar.left"), f"cc_wb:add:{m_id}:left", tone="apply")
            add_c = ActionBtn(t("cc_waybar.center"), f"cc_wb:add:{m_id}:center", tone="apply")
            add_r = ActionBtn(t("cc_waybar.right"), f"cc_wb:add:{m_id}:right", tone="apply")
            acts_m = f"{add_l} {add_c} {add_r}"
            is_custom = _is_removable_custom_mod(m_id, user_plugins)
            id_plain = f"({_display_mod_id(m_id)})"
            lbl_max = max(8, pool_flex_cols - len(id_plain) - 10)
            lbl_shown = canvas.truncate_plain(p_lbl, lbl_max)
            desc_budget = max(0, pool_flex_cols - len(lbl_shown) - len(id_plain) - 3)
            if is_custom:
                acts_m += f" {ActionBtn(t('cc_waybar.unlink_btn'), f'cc_wb:unlink:{m_id}', tone='danger_soft')}"
            desc_shown = canvas.truncate_plain(p_desc, desc_budget) if desc_budget >= 6 else ""
            mid_cell = f"{Txt(lbl_shown, role='focus')} {Txt(id_plain, role='schema')}"
            if desc_shown:
                mid_cell += f"  {Txt(desc_shown, role='schema')}"
            pool_rows.append([
                Txt(p_icon, role="focus"),
                mid_cell,
                acts_m,
            ])
        pool_box.add_table(
            headers=["", "", ""],
            widths=[icon_col_w, "flex", pool_acts_w],
            rows=pool_rows,
            aligns=["left", "left", "right"],
            show_headers=False,
        )
    cards.append(pool_box)
    cards.append(_build_plugin_guide_box(cc_panel, canvas, cards))
    cards.extend(get_wb_feedback_cards())

    return cards
