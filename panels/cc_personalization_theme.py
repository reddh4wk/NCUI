#!/usr/bin/env python3
from core.completion import catalog_mono_fonts, rank_candidates
from core.config import (
    BOX_STYLES,
    THEME,
    get_box_opacity_pct,
    get_layout_margins,
    get_row_gap,
    get_sys_notif_opacity_pct,
    get_toast_opacity_pct,
    get_ui_transparency_pct,
    get_waybar_sync_color,
    get_waybar_sync_opacity,
)
from core.i18n import t
from core.state import STATE
from ui.canvas import Canvas
from ui.kit import (
    ActionBtn,
    Badge,
    Box,
    CyclePill,
    FoldToggle,
    InlineField,
    InlineFilter,
    InspectorCell,
    ProgressBar,
    SelectPill,
    Slider,
    Sparkline,
    StateBadge,
    SwatchTxt,
    Txt,
    get_active_inline_entry,
)
from panels.cc_engine_personalization import (
    _COLLAPSE_PRESETS,
    _KV_SEP_PRESETS,
    _PILL_DOT_PRESETS,
    _SEARCH_BOX_PRESETS,
    _SLIDER_BRK_PRESETS,
    _SLIDER_GLYPH_PRESETS,
    _TAB_CAP_PRESETS,
    _TABLE_RULE_PRESETS,
    _TITLE_BRK_PRESETS,
    THEME_PRESETS,
    _feedback_cards,
    _list_saved_themes,
    get_active_theme_preset_id,
)


def build_cards(cc_panel, canvas: Canvas) -> list:
    cards = _feedback_cards()
    trans_pct = get_ui_transparency_pct()
    asc = THEME["ascii"]
    geom = THEME["geometry"]
    colors = THEME["colors"]
    typo = THEME["typography"]

    cur_bs = str(asc.get("box_style", "rounded"))
    tb = asc.get("title_brackets", ["[ ", " ]"])
    sb = asc.get("slider_brackets", ["[", "]"])
    pd = asc.get("pill_dots", ["\u25cf", "\u25cb"])
    tc = asc.get("tab_caps", ["\u258f", "\u2595"])
    ci = asc.get("collapsible_icons", ["\u25bc", "\u25b6"])
    sf = str(asc.get("slider_fill", "#"))
    se = str(asc.get("slider_empty", "-"))
    trule = str(asc.get("table_rule", "\u2500"))
    kvsep = str(asc.get("kv_separator", ":"))
    kw_cur = int(geom.get("default_key_width", 20))
    sp_cur = int(geom.get("card_spacing", 6))
    rad_cur = int(geom.get("window_border_radius", 16))
    in_pad, out_mar = get_layout_margins()
    saved = _list_saved_themes()
    active_preset = get_active_theme_preset_id()

    # =====================================================================
    # BOX 1 — Live Theme Sandbox, 1-Click Architectural Presets & Profiles
    # =====================================================================
    sb_box = Box(
        t("cc_personalization.live_theme_sandbox_preview"),
        icon="\uf108",
        actions=[
            InlineField(
                "cc_pers_save_theme",
                "cc_pers:inline_save_theme",
                value="",
                empty_label=t("cc_personalization.save"),
                placeholder="nightcity-custom",
                width=16,
                icon="\uf0c7",
                tone="apply",
            ),
            ActionBtn(t("cc_personalization.export"), "cc_pers:export_theme_prompt", tone="neutral"),
            ActionBtn(t("cc_personalization.import_toml"), "cc_pers:import_theme_prompt", tone="neutral"),
            ActionBtn(t("cc_personalization.reset"), "cc_pers:reset_theme", tone="warn"),
        ],
        highlighted=False,
    )
    sb_box.add_kv(
        t("cc_personalization.sample_telemetry"),
        f"{StateBadge(True, t('cc_personalization.online'))}  "
        f"{ProgressBar(36, width=10)}  {ProgressBar(72, width=10)}  "
        f"{Sparkline([18, 35, 58, 82, 44, 92, 65, 28])}  "
        f"{Badge(t('cc_personalization.wp_badge_preview'), style='warn')}",
    )
    sb_box.add_kv(
        t("cc_personalization.sample_controls"),
        f"{SelectPill(t('cc_personalization.active'), 'cc_pers:noop', selected=True)} "
        f"{SelectPill(t('cc_personalization.dirty'), 'cc_pers:noop', selected=True, dirty=True)} "
        f"{SelectPill(t('cc_personalization.idle'), 'cc_pers:noop', selected=False)} "
        f"{CyclePill('TCP', 'cc_pers:noop', active=True)} "
        f"{ActionBtn(t('cc_personalization.apply'), 'cc_pers:noop', tone='apply')} "
        f"{ActionBtn(t('cc_personalization.stop'), 'cc_pers:noop', tone='danger_soft')}",
    )
    sb_box.add_kv(
        t("cc_personalization.sample_searchbox"),
        InlineFilter(
            "cc_pers_demo_q",
            "nightcity",
            t("cc_personalization.filter_items"),
            "cc_pers:noop",
            clear_uri="cc_pers:noop",
            width=28,
        ),
    )

    sb_box.add_section(t("cc_personalization.sample_multi_column_component_prev"))
    sb_box.add_table(
        headers=[t("cc_personalization.component"), t("cc_personalization.status"), t("cc_personalization.live_metric")],
        widths=[28, 22, "flex"],
        rows=[
            [
                InspectorCell(t("cc_personalization.compositor_bridge"), "cc_pers:noop", selected=True),
                StateBadge(True, t("cc_personalization.synced")),
                Txt("60 FPS \u00b7 16.6 ms", role="val"),
            ],
            [
                InspectorCell(t("cc_personalization.rule_evaluator"), "cc_pers:noop", selected=False, is_alive=False),
                StateBadge(False, "ON", t("cc_personalization.standby")),
                Txt(t("cc_personalization.0_warnings"), role="inactive"),
            ],
        ],
    )

    sb_box.add_section(
        t("cc_personalization.sec_1click_presets"),
        actions=[ActionBtn(t("cc_personalization.apply_waybar"), "cc_pers:wb_apply_preset_palette", tone="apply")],
    )
    row_cyber = " ".join(
        SelectPill(THEME_PRESETS[pid]["label"], f"cc_pers:preset:{pid}", selected=(active_preset == pid))
        for pid in ("nightcity", "arasaka", "ghost_net")
    )
    row_retro = " ".join(
        SelectPill(THEME_PRESETS[pid]["label"], f"cc_pers:preset:{pid}", selected=(active_preset == pid))
        for pid in ("robco", "amber_deck", "arctic_studio")
    )
    sb_box.add_kv(t("cc_personalization.cyber_tactical"), row_cyber)
    sb_box.add_kv(t("cc_personalization.retro_studio"), row_retro)

    sb_box.add_section(t("cc_personalization.saved_theme_profiles_config_nc_ui", p0=len(saved)))
    if not saved:
        sb_box.add_row(f"  {Txt(t('cc_personalization.no_custom_themes_saved_yet_click_s'), role='inactive')}")
    else:
        prof_rows = [
            [
                Txt(f"{nm}.toml", role="focus"),
                f"{ActionBtn(t('cc_personalization.load'), f'cc_pers:load_theme:{nm}', tone='apply')} "
                f"{ActionBtn(t('cc_personalization.cur_del_x'), f'cc_pers:del_theme:{nm}', tone='danger_soft')}",
            ]
            for nm in saved[:10]
        ]
        sb_box.add_table(
            headers=["", ""],
            widths=["flex", 18],
            rows=prof_rows,
            aligns=["left", "right"],
            show_headers=False,
        )
    cards.append(sb_box)

    # =====================================================================
    # BOX 2 — Opacity Layers & Waybar Sync
    # =====================================================================
    box_op_pct = get_box_opacity_pct()
    toast_op_pct = get_toast_opacity_pct()
    sys_notif_op_pct = get_sys_notif_opacity_pct()
    wb_sync_op = get_waybar_sync_opacity()
    wb_sync_col = get_waybar_sync_color()

    surf_open, surf_fold = FoldToggle(cc_panel, "pers_sec_opacity", default=False, uri_prefix="ui:state")
    surf_acts = [ActionBtn(t("cc_personalization.apply_waybar"), "cc:trans_apply", tone="apply"), surf_fold] if surf_open else [surf_fold]
    surf_box = Box(
        t("cc_personalization.box_opacity_surface"),
        icon="\uf042",
        actions=surf_acts,
        highlighted=False,
    )
    if not surf_open:
        surf_box.add_row(f"  {Txt(t('cc_personalization.desc_sec_opacity'), role='schema')}")
    else:
        tr_sl = Slider(trans_pct, "cc:trans", min_val=5, max_val=100, width=26, show_pct=True, step=5)
        surf_box.add_kv(t("cc_personalization.window_transparency"), tr_sl)

        box_sl = Slider(box_op_pct, "cc_pers:box_op", min_val=0, max_val=100, width=26, show_pct=True, step=5)
        surf_box.add_kv(t("cc_personalization.box_layer_opacity"), box_sl)

        toast_sl = Slider(toast_op_pct, "cc_pers:toast_op", min_val=0, max_val=100, width=26, show_pct=True, step=5)
        surf_box.add_kv(t("cc_personalization.toast_layer_opacity"), toast_sl)

        sys_notif_sl = Slider(sys_notif_op_pct, "cc_pers:sys_notif_op", min_val=0, max_val=100, width=26, show_pct=True, step=5)
        surf_box.add_kv(t("cc_personalization.sys_notif_layer_opacity"), sys_notif_sl)

        wb_sync_btns = (
            f"{SelectPill(t('cc_personalization.wb_sync_opacity'), 'cc_pers:wb_sync_op', selected=wb_sync_op)} "
            f"{SelectPill(t('cc_personalization.wb_sync_color'), 'cc_pers:wb_sync_col', selected=wb_sync_col)}"
        )
        surf_box.add_kv(t("cc_personalization.waybar_sync_row"), wb_sync_btns)
    cards.append(surf_box)

    # =====================================================================
    # BOX 3 — Monospace Typography, Window Radius & Layout Geometry
    # =====================================================================
    cur_font = str(typo.get("font_family", "Courier") or "Courier")
    cur_fs = int(typo.get("font_size", 11))
    typo_open, typo_fold = FoldToggle(cc_panel, "pers_sec_typo", default=False, uri_prefix="ui:state")
    typo_box = Box(
        t("cc_personalization.box_typo_geometry"),
        icon="\uf031",
        actions=[typo_fold],
        highlighted=False,
    )
    if not typo_open:
        typo_box.add_row(f"  {Txt(t('cc_personalization.desc_sec_typo'), role='schema')}")
    else:
        fonts = ["Courier", "JetBrainsMono Nerd Font", "FiraCode Nerd Font", "Iosevka"]
        is_custom_font = cur_font not in fonts
        f_btns = " ".join(SelectPill(f.split()[0], f"cc_pers:font:{f}", selected=(cur_font == f)) for f in fonts)
        if is_custom_font:
            f_btns = f"{SelectPill(canvas.truncate_plain(cur_font, 18), f'cc_pers:font:{cur_font}', selected=True)} {f_btns}"
        is_font_open = bool(
            STATE.get("personalization", "font_pick_open", False)
            or get_active_inline_entry() == "cc_pers_font_picker"
        )
        typo_box.add_kv(
            t("cc_personalization.monospace_font"),
            f_btns,
            right_markup=ActionBtn(f"\u270e {t('cc_personalization.custom')}", "cc_pers:font_pick_open", tone="neutral"),
        )
        if is_font_open:
            fq = str(STATE.get("personalization", "font_query", "") or "").strip()
            f_ranked = rank_candidates(catalog_mono_fonts(), fq, limit=8)
            f_pills = [(canvas.truncate_plain(lb, 24), f"cc_pers:font:{val}") for val, lb in f_ranked]
            typo_box.add_inline_picker(
                "",
                query=fq,
                placeholder=t("cc_personalization.monospace_font_family"),
                trigger_uri="cc_pers:font_pick_open",
                results=f_pills,
                clear_uri="cc_pers:font_pick_clear" if fq else "",
                active=(get_active_inline_entry() == "cc_pers_font_picker"),
                max_pills_per_row=2,
                width=28,
                entry_id="cc_pers_font_picker",
                show_empty=bool(fq),
            )
        fs_btns = " ".join(
            SelectPill(f"{sz}pt", f"cc_pers:font_size:{sz}", selected=(cur_fs == sz))
            for sz in (9, 10, 11, 12, 13, 14)
        )
        typo_box.add_kv(t("cc_personalization.font_size"), fs_btns)
        typo_box.add_kv(
            t("cc_personalization.window_border_rad"),
            " ".join(SelectPill(f"{rd}px", f"cc_pers:winrad:{rd}", selected=(rad_cur == rd)) for rd in (0, 8, 12, 16, 24)),
        )

        def _build_adv_geom(b: Box) -> None:
            rg_cur = get_row_gap()
            rg_opts = [
                (0, t("cc_personalization.row_gap_0")),
                (2, t("cc_personalization.row_gap_2")),
                (4, t("cc_personalization.row_gap_4")),
            ]
            b.add_kv(
                t("cc_personalization.row_vertical_gap"),
                " ".join(SelectPill(lbl, f"cc_pers:row_gap:{v}", selected=(rg_cur == v)) for v, lbl in rg_opts),
            )
            b.add_kv(
                t("cc_personalization.kv_key_width"),
                " ".join(SelectPill(f"{w}c", f"cc_pers:kwidth:{w}", selected=(kw_cur == w)) for w in (16, 20, 24, 28)),
            )
            b.add_kv(
                t("cc_personalization.card_spacing"),
                " ".join(SelectPill(f"{sp}px", f"cc_pers:spacing:{sp}", selected=(sp_cur == sp)) for sp in (2, 4, 6, 10)),
            )
            b.add_kv(
                t("cc_personalization.inner_pad_lbl"),
                " ".join(SelectPill(f"{p}px", f"cc_pers:inner_pad:{p}", selected=(in_pad == p)) for p in (6, 10, 14, 18)),
                right_markup=InlineField(
                    "cc_pers_inpad",
                    "cc_pers:inline_inner_pad",
                    value=f"{in_pad}px" if in_pad not in (6, 10, 14, 18) else "",
                    empty_label=t("cc_personalization.custom"),
                    placeholder="10",
                    width=7,
                    icon="\u270e",
                    tone="neutral",
                    selected=(in_pad not in (6, 10, 14, 18)),
                ),
            )
            b.add_kv(
                t("cc_personalization.outer_mar_lbl"),
                " ".join(SelectPill(f"{m}px", f"cc_pers:outer_mar:{m}", selected=(out_mar == m)) for m in (4, 8, 12, 16)),
                right_markup=InlineField(
                    "cc_pers_outmar",
                    "cc_pers:inline_outer_mar",
                    value=f"{out_mar}px" if out_mar not in (4, 8, 12, 16) else "",
                    empty_label=t("cc_personalization.custom"),
                    placeholder="8",
                    width=7,
                    icon="\u270e",
                    tone="neutral",
                    selected=(out_mar not in (4, 8, 12, 16)),
                ),
            )

        typo_box.add_collapsible(
            cc_panel,
            "pers_geom",
            t("cc_personalization.adv_layout_geom"),
            _build_adv_geom,
            summary=t("cc_personalization.desc_sec_geom"),
        )
    cards.append(typo_box)

    # =====================================================================
    # BOX 4 — 7-Axis Semantic Color Studio & Surface Base (theme.toml)
    # =====================================================================
    n_sw = 3 if canvas.inner_cols >= 96 else (2 if canvas.inner_cols >= 80 else 1)

    def _add_color_studio_table(target_box: Box, specs: list, sel_pref: str, fld_pref: str, uri_pref: str) -> None:
        c_rows = []
        for c_key, c_desc, swatches in specs:
            cur_hx = str(colors.get(c_key, "#ffffff")).lower()
            vis_sw = list(swatches[:n_sw])
            if cur_hx in swatches and cur_hx not in vis_sw:
                vis_sw[-1] = cur_hx
            is_custom_sw = cur_hx not in vis_sw
            sw_btns = " ".join(
                SelectPill(hx.upper(), f"{sel_pref}:{c_key}:{hx}", selected=(cur_hx == hx.lower()))
                for hx in vis_sw
            )
            c_rows.append([
                Txt(c_key, role="focus"),
                Txt(c_desc, role="schema"),
                SwatchTxt(cur_hx),
                f"{sw_btns} "
                f"{InlineField(f'{fld_pref}_{c_key}', f'{uri_pref}:{c_key}', value=cur_hx.upper() if is_custom_sw else '', empty_label=t('cc_personalization.hex_2'), placeholder=cur_hx.upper(), width=9, icon='', tone='neutral', is_hex_color=True, selected=is_custom_sw)}",
            ])
        target_box.add_table(
            headers=[
                t("cc_personalization.axis"),
                t("cc_personalization.role"),
                t("cc_personalization.current"),
                t("cc_personalization.swatches_custom_hex"),
            ],
            widths=[10, 21, 10, "flex"],
            rows=c_rows,
            aligns=["left", "left", "left", "right"],
        )

    col_open, col_fold = FoldToggle(cc_panel, "pers_sec_colors", default=False, uri_prefix="ui:state")
    col_acts = [ActionBtn(t("cc_personalization.reset_theme"), "cc_pers:reset_theme", tone="warn"), col_fold] if col_open else [col_fold]
    col_box = Box(
        t("cc_personalization.7_axis_semantic_color_studio_profi"),
        icon="\uf53f",
        actions=col_acts,
        highlighted=False,
    )
    if not col_open:
        col_box.add_row(f"  {Txt(t('cc_personalization.desc_sec_colors'), role='schema')}")
    else:
        axis_specs = [
            ("surface_bg", t("cc_personalization.axis_surface_bg"), ["#080a0f", "#000000", "#070d17", "#041107"]),
            ("structure",  t("cc_personalization.axis_structure"),  ["#3e465c", "#47424f", "#2d4263", "#19542c"]),
            ("inactive",   t("cc_personalization.axis_inactive"),   ["#596278", "#665f73", "#4a6285", "#2c7542"]),
            ("schema",     t("cc_personalization.axis_schema"),     ["#8b9bb8", "#9a94ab", "#7fa0c9", "#55ba73"]),
            ("focus",      t("cc_personalization.axis_focus"),      ["#ffffff", "#f7f5fa", "#d6ffe0", "#eceff4"]),
            ("intent",     t("cc_personalization.axis_intent"),     ["#00f0ff", "#ffb800", "#b57aff", "#1aff80"]),
            ("vitality",   t("cc_personalization.axis_vitality"),   ["#00f57a", "#2ee68a", "#00e699", "#7dff3b"]),
            ("attention",  t("cc_personalization.axis_attention"),  ["#ffe619", "#ff7b1c", "#ffd93d", "#ffe135"]),
            ("interrupt",  t("cc_personalization.axis_interrupt"),  ["#ff2a9d", "#ff3377", "#ff4da6", "#ff8c2b"]),
            ("fault",      t("cc_personalization.axis_fault"),      ["#ff2442", "#ff1e38", "#ff3855", "#ff3636"]),
        ]
        _add_color_studio_table(col_box, axis_specs, "cc_pers:set_color", "cc_col", "cc_pers:inline_col")
    cards.append(col_box)

    # =====================================================================
    # BOX 5 — Waybar Color Palette (style.css)
    # =====================================================================
    wb_open, wb_fold = FoldToggle(cc_panel, "pers_sec_waybar_colors", default=False, uri_prefix="ui:state")
    wb_acts = (
        [
            ActionBtn(t("cc_personalization.wb_sync_theme_btn"), "cc_pers:wb_sync_from_theme", tone="warn"),
            ActionBtn(t("cc_personalization.apply_waybar"), "cc_pers:wb_apply_palette", tone="apply"),
            wb_fold,
        ]
        if wb_open
        else [wb_fold]
    )
    wb_box = Box(
        t("cc_personalization.box_waybar_colors"),
        icon="\uf1fc",
        actions=wb_acts,
        highlighted=False,
    )
    if not wb_open:
        wb_box.add_row(f"  {Txt(t('cc_personalization.desc_sec_wb_colors'), role='schema')}")
    else:
        wb_specs = [
            ("wb_bg",      t("cc_personalization.axis_wb_bg"),      ["#080a0f", "#0b0a0e", "#070d17", "#041107"]),
            ("wb_pill_bg", t("cc_personalization.axis_wb_pill_bg"), ["#141824", "#17141d", "#0f1a2c", "#082110"]),
            ("wb_fg",      t("cc_personalization.axis_wb_fg"),      ["#dce6f7", "#e0dce8", "#cfe0fa", "#9ef7b4"]),
            ("wb_accent",  t("cc_personalization.axis_wb_accent"),  ["#00f0ff", "#ffb800", "#b57aff", "#1aff80"]),
            ("wb_active",  t("cc_personalization.axis_wb_active"),  ["#00f57a", "#2ee68a", "#00e699", "#7dff3b"]),
            ("wb_warn",    t("cc_personalization.axis_wb_warn"),    ["#ffe619", "#ff7b1c", "#ffd93d", "#ffe135"]),
            ("wb_crit",    t("cc_personalization.axis_wb_crit"),    ["#ff2442", "#ff1e38", "#ff3855", "#ff3636"]),
        ]
        _add_color_studio_table(wb_box, wb_specs, "cc_pers:wb_color", "cc_wb_col", "cc_pers:inline_wb_col")
    cards.append(wb_box)

    # =====================================================================
    # BOX 6 — Structural Canvas Frames & Orthogonal Widget Glyphs
    # =====================================================================
    st_open, st_fold = FoldToggle(cc_panel, "pers_sec_ascii", default=False, uri_prefix="ui:state")
    st_box = Box(
        t("cc_personalization.structural_canvas_frames_ascii_pri"),
        icon="\uf031",
        actions=[st_fold],
        highlighted=False,
    )
    if not st_open:
        st_box.add_row(f"  {Txt(t('cc_personalization.desc_sec_ascii'), role='schema')}")
    else:
        st_box.add_kv(
            t("cc_personalization.box_border_style"),
            " ".join(
                SelectPill("ASCII" if s == "ascii" else s.title(), f"cc_pers:box_style:{s}", selected=(cur_bs == s))
                for s in BOX_STYLES
            ),
        )
        st_box.add_kv(
            t("cc_personalization.title_brackets"),
            " ".join(SelectPill(lbl, f"cc_pers:tbrk:{k}", selected=(list(tb) == [l, r])) for k, (l, r, lbl) in _TITLE_BRK_PRESETS.items()),
        )
        st_box.add_kv(
            t("cc_personalization.pill_status_dots"),
            " ".join(SelectPill(lbl, f"cc_pers:pdots:{k}", selected=(list(pd) == [on_d, off_d])) for k, (on_d, off_d, lbl) in _PILL_DOT_PRESETS.items()),
        )
        st_box.add_kv(
            t("cc_personalization.tab_bar_caps"),
            " ".join(SelectPill(lbl, f"cc_pers:tcaps:{k}", selected=(list(tc) == [l, r])) for k, (l, r, lbl) in _TAB_CAP_PRESETS.items()),
        )

        def _build_adv_glyphs(b: Box) -> None:
            b.add_kv(
                t("cc_personalization.collapsible_icons"),
                " ".join(SelectPill(lbl, f"cc_pers:cicons:{k}", selected=(list(ci) == [o, c])) for k, (o, c, lbl) in _COLLAPSE_PRESETS.items()),
            )
            b.add_kv(
                t("cc_personalization.slider_fill_empty"),
                " ".join(SelectPill(lbl, f"cc_pers:slglyph:{k}", selected=(sf == fg_g and se == em_g)) for k, (fg_g, em_g, lbl) in _SLIDER_GLYPH_PRESETS.items()),
                right_markup=InlineField(
                    "cc_pers_spark",
                    "cc_pers:inline_spark",
                    value="",
                    empty_label=t("cc_personalization.sparkline"),
                    placeholder="\u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588",
                    width=12,
                    icon="\u270e",
                    tone="neutral",
                ),
            )
            b.add_kv(
                t("cc_personalization.slider_brackets"),
                " ".join(SelectPill(lbl, f"cc_pers:sbrk:{k}", selected=(list(sb) == [l, r])) for k, (l, r, lbl) in _SLIDER_BRK_PRESETS.items()),
            )
            b.add_kv(
                t("cc_personalization.table_rule_char"),
                " ".join(SelectPill(f"'{r}'", f"cc_pers:trule:{i}", selected=(trule == r)) for i, r in enumerate(_TABLE_RULE_PRESETS)),
            )
            b.add_kv(
                t("cc_personalization.kv_separator_char"),
                " ".join(SelectPill(f"'{s}'", f"cc_pers:kvsep:{i}", selected=(kvsep == s)) for i, s in enumerate(_KV_SEP_PRESETS)),
            )
            cur_spref = str(asc.get("search_prefix") or "\uf002 >").strip()
            cur_scur = str(asc.get("search_cursor") or "\u2502")[0]
            b.add_kv(
                t("cc_personalization.search_box_style"),
                " ".join(SelectPill(lbl, f"cc_pers:sbox:{k}", selected=(cur_spref == sp_v and cur_scur == sc_v)) for k, (sp_v, sc_v, lbl) in _SEARCH_BOX_PRESETS.items()),
            )

        st_box.add_collapsible(
            cc_panel,
            "pers_glyphs",
            t("cc_personalization.adv_widget_glyphs"),
            _build_adv_glyphs,
            summary=t("cc_personalization.desc_sec_glyphs"),
        )
    cards.append(st_box)

    return cards
