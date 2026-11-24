#!/usr/bin/env python3
import time
from core import probe
from core.i18n import t
from core.state import STATE
from ui.canvas import Canvas
from ui.kit import ActionBtn, Badge, Box, Divider, InlineField, ProgressBar, SelectPill, StateBadge, Txt
from ui.markup import format_date_it
from panels.cc_engine_datetime import (
    is_calendar_initialized,
    get_calendar_mode,
    get_calendar_horizon_telemetry,
    get_reminder_minutes,
    get_db_stats,
    get_ical_feed_names,
    get_ical_feeds,
    get_ical_sync_interval_min,
    mask_ical_url,
)


def build_cards(cc_panel, canvas: Canvas) -> list:
    init_ok = is_calendar_initialized()
    mode = get_calendar_mode()
    stats = get_db_stats()
    hz = get_calendar_horizon_telemetry(days=7)
    status_msg = STATE.get("calendar", "cc_status_msg", "")
    feeds = get_ical_feeds()
    feed_names = get_ical_feed_names()
    enabled_cnt = sum(1 for f in feeds if f.get("enabled", True))
    sync_mins = get_ical_sync_interval_min()
    last_sync = float(STATE.get("calendar", "last_sync_ts", 0.0) or 0.0)
    sync_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(last_sync)) if last_sync > 0 else t("cc_calendar.never")
    rem_mins = get_reminder_minutes()

    cards = []

    # ── CARD 1: Engine, Reminders & 7-Day Horizon Split Telemetry ──
    init_badge = (
        StateBadge(True, t("cc_calendar.initialized"), on_tone="ok")
        if init_ok
        else Badge(t("cc_calendar.uninitialized"), style="warn")
    )
    rem_badge = StateBadge(rem_mins > 0, t("cc_calendar.min_before", p0=rem_mins), t("common.off"), on_tone="ok")
    m_box = Box(
        t("cc_calendar.calendar_engine_storage_mode"),
        icon="\uf073",
        actions=[init_badge, rem_badge],
        highlighted=False,
    )
    m_box.add_row(" ")
    if not init_ok:
        msg = t("cc_calendar.select_an_operating_mode_below_to")
        m_box.add_row(f"  {Txt(f'\uf071 {msg}', role='attention')}")

    m_box.add_kv(
        t("cc_calendar.operating_mode"),
        f"{SelectPill(t('cc_calendar.mode_ical_pill'), 'cc_cal:mode:ical', selected=(init_ok and mode == 'ical'))}"
        f"  {SelectPill(t('cc_calendar.mode_local_pill'), 'cc_cal:mode:local', selected=(init_ok and mode == 'local'))}",
        key_width=18,
    )

    presets = [0, 5, 10, 15, 30, 60]
    p_btns = []
    for p in presets:
        lbl = t("cc_power.off") if p == 0 else f"{p}m"
        p_btns.append(SelectPill(lbl, f"cc_cal:rem_set:{p}", selected=(rem_mins == p)))
    is_custom_rem = rem_mins not in presets
    p_btns.append(
        InlineField(
            "cc_cal_rem_inline",
            "cc_cal:rem_inline",
            value=f"{rem_mins}m" if is_custom_rem else "",
            empty_label=t("cc_calendar.custom"),
            placeholder="min",
            width=7,
            icon="\u270e",
            tone="neutral",
            selected=is_custom_rem,
        )
    )
    m_box.add_kv(t("cc_calendar.lead_time").rstrip(":"), "  ".join(p_btns), key_width=18)

    stat_desc = f"(Local: {stats['local']} \u00b7 iCal: {stats['ical']} \u00b7 {stats['size_kb']:.1f} KB)"
    m_box.add_kv(
        t("cc_calendar.stored_events"),
        f"{Txt(str(stats['total']), role='val')} {Txt(stat_desc, role='schema')}",
        key_width=18,
    )

    nxt = hz.get("next_event")
    if nxt:
        nxt_dt = format_date_it(nxt["date"])
        nxt_tm = nxt["start_time"][:5] if nxt.get("start_time") else t("calendar.all_day_short")
        nxt_mk = (
            f"{Txt(f'{nxt_dt} \u00b7 {nxt_tm}', role='vitality')}"
            f"  {Txt(canvas.truncate_plain(nxt['title'], 28), role='focus')}"
        )
    else:
        nxt_mk = Txt(t("cc_calendar.no_upcoming_events"), role="inactive")
    m_box.add_kv(t("cc_calendar.next_appointment"), nxt_mk, key_width=18)

    m_box.add_kv(
        t("cc_calendar.quick_actions"),
        f"{ActionBtn(t('cc_calendar.test_notify'), 'cc_cal:rem_test', tone='neutral')}"
        f"  {ActionBtn(t('cc_calendar.clear_local_events'), 'cc_cal:clear_local', tone='danger_soft')}",
        key_width=18,
    )

    if probe.has_nerd_font():
        m_box.set_side_emblem("\uf073", label=t("cc_calendar.emblem_engine"), role="schema", max_rows=3.8)

    # Split inside Card 1: adapt cleanly to canvas.inner_cols (e.g. 92 at 96 cols -> split_avail=89)
    split_avail = max(60, canvas.inner_cols - 3)
    left_w = max(37, min(46, int(round(split_avail * 0.44))))
    right_w = max(36, split_avail - left_w)
    l_in, r_in = left_w - 2, right_w - 2

    sum_7d = t("cc_calendar.horizon_summary", p0=hz["week_count"], p1=hz["month_count"])
    left_hdr_l = canvas.pad_cell(Txt(t("cc_calendar.horizon_7d_hdr"), role="schema"), max(10, l_in - len(sum_7d)))
    left_7d = [
        f" {left_hdr_l}{Txt(sum_7d, role='schema')} ",
        f" {Txt('\u2500' * l_in, role='structure')} ",
    ]
    bar_w = 12 if l_in >= 42 else 10
    r_stat_w = l_in - 11 - (bar_w + 2)
    for hd in hz["horizon_days"]:
        d_str = format_date_it(hd["date_iso"])
        d_role = "attention" if hd["is_today"] else ("focus" if hd["count"] > 0 else "schema")
        d_cell = canvas.pad_cell(Txt(d_str, role=d_role), 11)
        bar = ProgressBar(hd["load_pct"], width=bar_w, color_by_threshold=True)
        if hd["count"] > 0:
            hrs = hd["booked_mins"] / 60.0
            r_cell = canvas.pad_cell(Txt(f"{hrs:.1f}h ({hd['count']})", role="val"), r_stat_w, align="right")
        else:
            r_cell = canvas.pad_cell(Txt(t("cc_calendar.day_free"), role="inactive"), r_stat_w, align="right")
        left_7d.append(f" {d_cell}{bar}{r_cell} ")

    up_list = hz["upcoming_list"]
    cnt_up = f"({len(up_list)})"
    r_hdr_l = canvas.pad_cell(Txt(t("cc_calendar.upcoming_hdr"), role="schema"), max(10, r_in - len(cnt_up)))
    right_up = [
        f" {r_hdr_l}{Txt(cnt_up, role='schema')} ",
        f" {Txt('\u2500' * r_in, role='structure')} ",
    ]
    if not up_list:
        right_up.append(f" {canvas.pad_cell(Txt(t('cc_calendar.no_upcoming_events'), role='inactive'), r_in)} ")
    else:
        for ev in up_list[:7]:
            ev_d = format_date_it(ev["date"])
            is_td = bool(hz["horizon_days"] and ev["date"] == hz["horizon_days"][0]["date_iso"])
            d_mk = canvas.pad_cell(Txt(ev_d, role="attention" if is_td else "schema"), 11)
            tm_raw = ev["start_time"][:5] if ev.get("start_time") else t("calendar.all_day_short")
            t_mk = canvas.pad_cell(Txt(tm_raw, role="val"), 6)
            src_tag = str(ev.get("source") or "local")
            src_s = t("cc_calendar.src_local_short") if src_tag == "local" else canvas.truncate_plain(feed_names.get(src_tag, "iCal"), 9)
            src_w = len(src_s) + 1
            tit_budget = max(10, r_in - 17 - src_w)
            tit_mk = canvas.pad_cell(Txt(canvas.truncate_plain(ev["title"], tit_budget), role="focus"), tit_budget)
            src_mk = canvas.pad_cell(Txt(src_s, role="schema"), src_w, align="right")
            right_up.append(f" {d_mk}{t_mk}{tit_mk}{src_mk} ")
    while len(right_up) < len(left_7d):
        right_up.append(" ")

    m_box.add_split(left_7d, right_up, ratios=[left_w, right_w], junctions=True)
    m_box.add_row(" ")
    cards.append(m_box)

    # ── CARD 2: iCal (.ics) Feed Subscriptions ──
    st_badge = (
        StateBadge(True, t("cc_calendar.feeds_active", p0=enabled_cnt), on_tone="ok")
        if enabled_cnt > 0
        else StateBadge(False, "", t("cc_calendar.no_feeds"))
    )
    box_title = t("cc_calendar.ical_subscriptions_n", p0=len(feeds)) if feeds else t("cc_calendar.ical_subscriptions")
    top_acts = [st_badge, ActionBtn(t("cc_calendar.add_ical_btn"), "cc_cal:ical_add", tone="apply")]
    if enabled_cnt > 0:
        top_acts.append(ActionBtn(t("cc_calendar.force_sync_now"), "cc_cal:sync_now", tone="neutral"))

    g_box = Box(
        box_title,
        icon="\uf0ac",
        actions=top_acts,
        highlighted=False,
    )
    g_box.add_row(" ")

    interval_pills = []
    for mins_opt, lbl_key in ((15, "15m"), (30, "30m"), (60, "1h"), (0, None)):
        lbl = t("cc_calendar.manual") if mins_opt == 0 else lbl_key
        interval_pills.append(
            SelectPill(lbl, f"cc_cal:ical_sync_interval:{mins_opt}", selected=(sync_mins == mins_opt))
        )
    g_box.add_kv(t("cc_calendar.auto_sync"), "  ".join(interval_pills), key_width=18)
    g_box.add_kv(
        t("cc_calendar.last_sync"),
        f"{Txt(sync_str, role='val' if last_sync > 0 else 'inactive')}   {Txt(t('cc_calendar.sync_pipeline_meta'), role='schema')}",
        key_width=18,
    )

    if feeds:
        g_box.add_section(t("cc_calendar.configured_feeds"))
        rows = []
        errs = []
        for f in feeds:
            fid = str(f.get("id") or "")
            en = bool(f.get("enabled", True))
            err = str(f.get("last_err") or "").strip()
            if err:
                errs.append((f.get("name") or fid, err))
                f_badge = Badge(t("common.err"), style="crit")
            else:
                f_badge = StateBadge(en, t("common.ok"), t("common.off"), on_tone="ok")
            ev_cnt = int(f.get("count", 0) or 0)
            acts = (
                f"{SelectPill(t('common.on') if en else t('common.off'), f'cc_cal:ical_toggle:{fid}', selected=en)}"
                f"  {ActionBtn('x', f'cc_cal:ical_del:{fid}', tone='danger_soft')}"
            )
            rows.append([
                Txt(str(f.get("name") or "iCal"), role="focus" if en else "inactive"),
                Txt(mask_ical_url(f.get("url", "")), role="val" if en else "inactive"),
                f_badge,
                Txt(str(ev_cnt), role="val" if en else "inactive"),
                acts,
            ])
        g_box.add_table(
            headers=[
                t("cc_calendar.col_feed"),
                t("cc_calendar.col_url"),
                t("cc_calendar.col_status"),
                t("cc_calendar.col_events"),
                t("cc_calendar.col_actions"),
            ],
            widths=[16, "flex", 10, 7, 15],
            rows=rows,
            aligns=["left", "left", "left", "right", "right"],
        )
        for fname, ferr in errs:
            g_box.add_row(f"  {Txt(f'\uf071 {fname}: {ferr}', role='fault')}")
    else:
        g_box.add_section(t("cc_calendar.supported_providers_sec"))
        sec_tag = Txt(t("cc_calendar.prov_sec_meta"), role="schema")
        idle_st = StateBadge(False, "", t("cc_calendar.no_feeds"))
        g_box.add_table(
            headers=[
                t("cc_calendar.col_provider"),
                t("cc_calendar.col_endpoint"),
                t("cc_calendar.col_security"),
                t("cc_calendar.col_status"),
            ],
            widths=[20, "flex", 24, 14],
            rows=[
                [Txt("Google Calendar", role="focus"), Txt(t("cc_calendar.prov_google_fmt"), role="val"), sec_tag, idle_st],
                [Txt("Nextcloud / CalDAV", role="focus"), Txt(t("cc_calendar.prov_nextcloud_fmt"), role="val"), sec_tag, idle_st],
                [Txt("Proton / Outlook", role="focus"), Txt(t("cc_calendar.prov_proton_fmt"), role="val"), sec_tag, idle_st],
            ],
            aligns=["left", "left", "left", "right"],
        )

    url_gcal_settings = "https://calendar.google.com/calendar/r/settings"

    def _build_ical_guide(b: Box) -> None:
        b.add_row(f"  {Txt(t('cc_calendar.ical_s1_title'), role='focus')}")
        b.add_row(f"     {t('cc_calendar.ical_open_prefix')} <a href='{url_gcal_settings}'><span underline='single'>{Txt(url_gcal_settings, role='intent')}</span></a>")
        b.add_row(f"  {Txt(t('cc_calendar.ical_s2_title'), role='focus')}")
        b.add_row(f"     {Txt(t('cc_calendar.ical_s2_hint'), role='schema')}")
        b.add_row(f"  {Txt(t('cc_calendar.ical_s3_title'), role='focus')}")
        b.add_row(f"     {Txt(t('cc_calendar.ical_s3_hint'), role='schema')}")

    g_box.add_collapsible(cc_panel, "cal_oauth_guide", t("cc_calendar.ical_setup_guide"), _build_ical_guide)
    g_box.add_row(" ")
    cards.append(g_box)

    # ── MACRO CHAPTER DIVIDER + CARD 3: Import & Export Table ──
    cards.append(Divider(t("cc_calendar.storage_io_divider"), icon="\uf56e"))

    io_box = Box(
        t("cc_calendar.database_import_export_ics_db_n", p0=stats["total"]),
        icon="\uf56e",
        highlighted=False,
    )
    io_box.add_row(" ")
    if status_msg:
        io_box.add_row(f"  {Txt(f'\uf05a {status_msg}', role='val')}")
        io_box.add_section()

    io_box.add_table(
        headers=[
            t("cc_calendar.col_operation"),
            t("cc_calendar.col_format"),
            t("cc_calendar.col_pipeline"),
            t("cc_calendar.col_actions"),
        ],
        widths=[15, 28, "flex", 16],
        rows=[
            [
                Txt(t("cc_calendar.op_export"), role="focus"),
                Txt(t("cc_calendar.rfc_5545_standard_ics"), role="val"),
                Txt(t("cc_calendar.pipe_ics_exp"), role="schema"),
                ActionBtn(t("cc_calendar.export_ics"), "cc_cal:export_ics", tone="neutral"),
            ],
            [
                Txt(t("cc_calendar.op_import"), role="focus"),
                Txt(t("cc_calendar.import_events_from_ics_file"), role="val"),
                Txt(t("cc_calendar.pipe_ics_imp"), role="schema"),
                ActionBtn(t("cc_calendar.import_ics"), "cc_cal:import_ics", tone="apply"),
            ],
            [
                Txt(t("cc_calendar.op_export"), role="focus"),
                Txt(t("cc_calendar.sqlite_binary_snapshot_db"), role="val"),
                Txt(t("cc_calendar.pipe_db_exp"), role="schema"),
                ActionBtn(t("cc_calendar.export_db"), "cc_cal:export_db", tone="neutral"),
            ],
            [
                Txt(t("cc_calendar.op_import"), role="focus"),
                Txt(t("cc_calendar.restore_merge_from_sqlite_db_file"), role="val"),
                Txt(t("cc_calendar.pipe_db_imp"), role="schema"),
                ActionBtn(t("cc_calendar.import_db"), "cc_cal:import_db", tone="neutral"),
            ],
        ],
        aligns=["left", "left", "left", "right"],
    )
    io_box.add_row(" ")
    cards.append(io_box)
    return cards
