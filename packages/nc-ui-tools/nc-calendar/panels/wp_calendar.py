#!/usr/bin/env python3
import calendar
import datetime
import html
import re
from core.config import THEME
from core.i18n import t
from ui.canvas import Canvas
from ui.kit import (
    ActionBtn,
    Box,
    InspectorCell,
    SelectPill,
    StateBadge,
    Txt,
    register_scroll_zone,
)
from ui.markup import (
    format_date_it,
    get_days_long,
    get_days_short,
    get_months,
    get_months_short,
    wrap_pango_markup,
)
from panels.base import BasePanel
from panels.cc_engine_calendar import (
    add_local_event,
    check_and_trigger_reminders,
    delete_event_by_id,
    fetch_calendar_events,
    fetch_calendar_range,
    get_calendar_mode,
    get_ical_feed_names,
    is_calendar_initialized,
    parse_time_input,
)

_EXP_COLS = 175
_LEFT_W = 125
_RIGHT_W = 43
_DAY_W = 16
_GUTTER_W = 6
_VIS_HOURS = 13  # 13 hours (08:00-20:00) * 3 rows = 38 grid lines
_URL_RE = re.compile(r"https?://[^\s<>'\"]+")


def _parse_hm(t_str: str) -> int | None:
    m = re.match(r"^(\d{1,2}):(\d{2})", str(t_str or "").strip())
    if not m:
        return None
    hh, mm = int(m.group(1)), int(m.group(2))
    return max(0, min(1440, hh * 60 + mm)) if 0 <= hh <= 23 and 0 <= mm <= 59 else None


def _event_span_mins(ev: dict) -> tuple[int, int] | None:
    st = _parse_hm(ev.get("start_time", ""))
    if st is None:
        return None
    et = _parse_hm(ev.get("end_time", ""))
    if et is None or et <= st:
        et = min(1440, st + 60)
    return st, min(1440, et)


def _trunc_line_with_urls(text: str, max_chars: int) -> list[str]:
    lines = []
    for raw_l in str(text or "").splitlines():
        s = raw_l.strip()
        if not s:
            continue
        m = _URL_RE.search(s)
        if m and m.start() == 0:
            url = m.group(0)
            disp = url if len(url) <= max_chars else (url[: max_chars - 1] + "\u2026")
            lines.append(
                f"<a href='{Canvas.escape(url)}'><span underline='single'>{Txt(disp, role='intent')}</span></a>"
            )
            continue
        for wl in wrap_pango_markup(Canvas.escape(s), max_chars=max_chars):
            plain = html.unescape(wl)
            if _URL_RE.search(plain):
                def _repl(mt):
                    u = mt.group(0)
                    d = u if len(u) <= max_chars else (u[: max_chars - 1] + "\u2026")
                    return f"<a href='{Canvas.escape(u)}'><span underline='single'>{Txt(d, role='intent')}</span></a>"
                parts = []
                last = 0
                for mt in _URL_RE.finditer(plain):
                    if mt.start() > last:
                        parts.append(Txt(plain[last:mt.start()], role="schema"))
                    parts.append(_repl(mt))
                    last = mt.end()
                if last < len(plain):
                    parts.append(Txt(plain[last:], role="schema"))
                lines.append("".join(parts))
            else:
                lines.append(Txt(plain, role="schema"))
    return lines


class Panel(BasePanel):
    id = "calendar"
    icon = "\uf073"
    supports_expand = True

    def __init__(self, shell=None):
        self.title = t("calendar.title") if shell is not None else self.id
        super().__init__(shell)
        today = datetime.date.today()
        self.year = int(self.get_state("year", today.year))
        self.month = int(self.get_state("month", today.month))
        self.selected_date = str(self.get_state("selected_date", today.isoformat()))
        self.show_all = bool(self.get_state("show_all", False))
        self.selected_event_id = self.get_state("selected_event_id", None)
        vm = str(self.get_state("view_mode", "week"))
        self.view_mode = vm if vm in ("week", "month") else "week"
        ho = self.get_state("hour_offset", None)
        self.hour_offset = int(ho) if isinstance(ho, (int, float)) and ho is not None else None
        self._pending_event = {}

    def get_cols(self, force_expanded: bool = False) -> int:
        if force_expanded or self.is_expanded:
            return _EXP_COLS
        return int(THEME["geometry"]["compact_cols"])

    def get_max_height(self, force_expanded: bool = False) -> int:
        if force_expanded or self.is_expanded:
            return 1020
        return int(THEME["geometry"]["popup_max_height"])

    def get_bar_pill(self) -> dict:
        check_and_trigger_reminders()
        now = datetime.datetime.now()
        font = THEME["typography"]["font_family"]
        date_s = format_date_it(now.date().isoformat())
        text = (
            f"{Txt('\uf017', role='focus')}  <span font_family='{font}'>{Txt(now.strftime('%H:%M:%S'), role='focus')}</span>   "
            f"{Txt('\uf073', role='focus')}  <span font_family='{font}'>{Txt(date_s, role='focus')}</span>"
        )
        return {"text": text, "tooltip": "", "class": "module-nc"}

    def _nav_cluster(self, expanded: bool, extra_actions: list[str] = None) -> list[str]:
        nav_group = (
            f"{Txt('[', role='structure')}"
            f"{ActionBtn('<', 'cal:prev', tone='neutral', brackets=False)} "
            f"{ActionBtn('\u2022', 'cal:today', tone='neutral', brackets=False)} "
            f"{ActionBtn('>', 'cal:next', tone='neutral', brackets=False)}"
            f"{Txt(']', role='structure')}"
        )
        out = list(extra_actions or [])
        out.append(nav_group)
        out.append(ActionBtn("-" if expanded else "+", "ui:toggle_expand", tone="neutral"))
        return out

    def _build_compact(self, canvas: Canvas, events: list[dict], has_feeds: bool, initialized: bool, mode: str) -> list:
        months = get_months()
        cal_matrix = calendar.monthcalendar(self.year, self.month)
        today_iso = datetime.date.today().isoformat()
        events_by_date = {}
        for ev in events:
            events_by_date.setdefault(ev["date"], []).append(ev)

        box = Box(
            f"{months[self.month]} {self.year}",
            icon="\uf073",
            actions=self._nav_cluster(False),
            highlighted=False,
            fixed_width=True,
            min_cols=44,
            max_cols=44,
        )
        box.add_row(Txt(t("calendar.compact_weekdays_hdr"), role="schema"))
        box.add_row(f"  {Txt('\u2500' * 35, role='structure')}   ")
        for week in cal_matrix:
            w_str = "  "
            for d in week:
                if d == 0:
                    w_str += "     "
                else:
                    iso_d = f"{self.year}-{self.month:02d}-{d:02d}"
                    has_ev = iso_d in events_by_date
                    dot = "\u00b7" if has_ev else " "
                    if iso_d == self.selected_date:
                        cell_s = f"[{d:2d}{dot}]" if has_ev else f"[{d:2d}]"
                        trail = "" if has_ev else " "
                        w_str += f"<a href='cal:day:{iso_d}'><span underline='none'>{Txt(cell_s, role='intent', bg=True)}</span></a>{trail}"
                        continue
                    elif iso_d == today_iso:
                        cell, role = f" {d:2d}{dot} ", "attention"
                    elif has_ev:
                        cell, role = f" {d:2d}\u00b7 ", "vitality"
                    else:
                        cell, role = f" {d:2d}  ", "focus"
                    w_str += f"<a href='cal:day:{iso_d}'><span underline='none'>{Txt(cell, role=role)}</span></a>"
            w_str += "   "
            box.add_row(w_str)

        if not initialized:
            box.add_section(t("calendar.setup"))
            box.add_row(canvas.pad_cell(Txt(t("calendar.calendar_not_initialized"), role="inactive"), canvas.inner_cols, align="center"))
            box.add_row(
                canvas.pad_cell(
                    ActionBtn(t("calendar.initialize_calendar"), "ui:open_settings:datetime:calendar", tone="apply"),
                    canvas.inner_cols,
                    align="center",
                    truncate=False,
                )
            )
            return [box]

        add_btn = ActionBtn("+..", "cal:add_start", tone="apply")
        if not self.show_all:
            sel_events = events_by_date.get(self.selected_date, [])
            box.add_section(
                t("calendar.events_on", date=format_date_it(self.selected_date)),
                actions=[add_btn, ActionBtn(t("calendar.show_all"), "cal:toggle_all", tone="neutral")],
            )
            if not sel_events:
                box.add_row(f"  {Txt(t('calendar.no_events_day').strip(), role='inactive')}")
            else:
                for ev in sel_events:
                    tm = ev["start_time"] or t("calendar.all_day")
                    ev_id = ev["id"]
                    t_clean = canvas.truncate_plain(ev["title"], 25 if ev.get("source") == "local" else 28)
                    del_btn = ActionBtn("x", f"cal:del:{ev_id}", tone="danger_soft") if ev.get("source") == "local" else ""
                    box.add_row(
                        f"  {Txt(f'{tm:<5}', role='val')} <a href='cal:event:{ev_id}'><span underline='none'>{Txt(t_clean, role='focus')}</span></a>",
                        right_markup=del_btn,
                    )
        else:
            box.add_section(
                t("calendar.events_month", month=months[self.month]),
                actions=[add_btn, ActionBtn(t("calendar.show_less"), "cal:toggle_all", tone="neutral")],
            )
            fut_events = [ev for ev in events if ev["date"] >= today_iso]
            if not fut_events:
                box.add_row(f"  {Txt(t('calendar.no_events_month').strip(), role='inactive')}")
            else:
                fut_by_date = {}
                for ev in fut_events:
                    fut_by_date.setdefault(ev["date"], []).append(ev)
                for dt_str in sorted(fut_by_date.keys()):
                    box.add_row(f"  {Txt(format_date_it(dt_str), role='schema')}")
                    for ev in fut_by_date[dt_str]:
                        tm = ev["start_time"] or t("calendar.all_day")
                        ev_id = ev["id"]
                        t_clean = canvas.truncate_plain(ev["title"], 23 if ev.get("source") == "local" else 27)
                        del_btn = ActionBtn("x", f"cal:del:{ev_id}", tone="danger_soft") if ev.get("source") == "local" else ""
                        box.add_row(
                            f"    {Txt(f'{tm:<5}', role='val')} <a href='cal:event:{ev_id}'><span underline='none'>{Txt(t_clean, role='focus')}</span></a>",
                            right_markup=del_btn,
                        )

        box.add_section(t("calendar.account_section"))
        if mode == "local":
            st_left = f"  {Txt(t('calendar.local_sqlite'), role='val')}"
            st_right = ActionBtn("\uf013", "ui:open_settings:datetime:calendar", tone="neutral")
        else:
            st_left = (
                f"  {StateBadge(True, t('calendar.ical_active'), on_tone='ok')}"
                if has_feeds
                else f"  {StateBadge(False, '', t('calendar.no_ical_feeds'))}"
            )
            sync_b = f"{ActionBtn(t('calendar.sync'), 'cal:sync', tone='neutral')}  " if has_feeds else ""
            st_right = f"{sync_b}{ActionBtn('\uf013', 'ui:open_settings:datetime:calendar', tone='neutral')}"
        box.add_row(st_left, right_markup=st_right)

        cards = [box]
        if self.selected_event_id is not None:
            ev = next((e for e in events if str(e["id"]) == str(self.selected_event_id)), None)
            if ev:
                det_acts = [ActionBtn(t("common.close"), "cal:close_event", tone="neutral")]
                if ev.get("source") == "local":
                    det_acts.insert(0, ActionBtn("x", f"cal:del:{ev['id']}", tone="danger_soft"))
                det = Box(t("calendar.event_detail"), icon="\uf073", actions=det_acts, highlighted=False)
                det.add_row(" ")
                for wl in wrap_pango_markup(Canvas.escape(ev["title"]), max_chars=36):
                    det.add_row(Txt(f"  {html.unescape(wl)}", role="focus"))
                tm_full = f"{ev['start_time']} - {ev['end_time']}" if ev.get("end_time") else (ev["start_time"] or t("calendar.all_day"))
                det.add_row(Txt(f"  \uf017  {format_date_it(ev['date'])}   {tm_full}", role="val"))
                if ev.get("location"):
                    for wl in wrap_pango_markup(Canvas.escape(f"@  {ev['location']}"), max_chars=36):
                        det.add_row(Txt(f"  {html.unescape(wl)}", role="schema"))
                if ev.get("description"):
                    det.add_section(t("calendar.notes_desc"))
                    for dl in _trunc_line_with_urls(ev["description"], 36)[:8]:
                        det.add_row(f"  {dl}")
                cards.append(det)
        return cards

    def _compute_week_h_start(self, week_days: list[datetime.date], events_by_date: dict) -> int:
        if self.hour_offset is not None:
            return max(0, min(24 - _VIS_HOURS, int(self.hour_offset)))
        min_hr = None
        for d in week_days:
            for ev in events_by_date.get(d.isoformat(), []):
                sp = _event_span_mins(ev)
                if sp is not None:
                    hr = sp[0] // 60
                    min_hr = hr if min_hr is None else min(min_hr, hr)
        if min_hr is not None and min_hr < 8:
            return max(0, min_hr)
        return 8

    def _compute_expanded_cols(self, avail_cols: int) -> tuple[int, int, int, int]:
        eff = avail_cols if avail_cols >= 96 else _EXP_COLS
        if eff >= _EXP_COLS:
            return _EXP_COLS, _LEFT_W, _RIGHT_W, _DAY_W
        day_w = max(10, min(_DAY_W, (eff - 7 - 32 - _GUTTER_W - 7) // 7))
        left_w = _GUTTER_W + 7 * day_w + 7
        right_w = max(30, min(_RIGHT_W, eff - 7 - left_w))
        return left_w + right_w + 7, left_w, right_w, day_w

    def _build_week_left(
        self,
        exp_canvas: Canvas,
        week_days: list[datetime.date],
        events_by_date: dict,
        today_iso: str,
        insp_id,
        h_start: int,
        day_w: int = _DAY_W,
    ) -> list[str]:
        days_s = get_days_short()
        now_hm = datetime.datetime.now()
        v_sep = Txt("\u2502", role="structure")
        cross_s = Txt("\u253c", role="structure")
        dash_day = Txt("\u2500" * day_w, role="structure")
        rule_row = Txt("\u2500" * _GUTTER_W, role="structure") + cross_s + cross_s.join([dash_day] * 7)
        dot_day = Txt("\u2508" * day_w, role="structure")

        out = []

        # Row 0: Weekday Headers
        hdr_cells = []
        for dt in week_days:
            iso_d = dt.isoformat()
            d_evs = events_by_date.get(iso_d, [])
            ds = days_s[dt.weekday()]
            cnt_s = f" ({len(d_evs)})" if d_evs else ""
            if iso_d == self.selected_date:
                inner_mk = (
                    f"<a href='cal:day:{iso_d}'><span underline='none'>"
                    f"{Txt(ds + ' ', role='intent')}{Txt(f'[{dt.day:02d}]', role='intent', bg=True)}"
                    f"{Txt(cnt_s, role='schema') if cnt_s else ''}"
                    f"</span></a>"
                )
            elif iso_d == today_iso:
                inner_mk = (
                    f"<a href='cal:day:{iso_d}'><span underline='none'>"
                    f"{Txt(f'{ds} {dt.day:02d}*', role='attention')}{Txt(cnt_s, role='schema') if cnt_s else ''}"
                    f"</span></a>"
                )
            else:
                r_col = "focus" if d_evs else "schema"
                inner_mk = (
                    f"<a href='cal:day:{iso_d}'><span underline='none'>"
                    f"{Txt(f'{ds} {dt.day:02d}', role=r_col)}{Txt(cnt_s, role='schema') if cnt_s else ''}"
                    f"</span></a>"
                )
            hdr_cells.append(exp_canvas.pad_cell(inner_mk, day_w, align="center", truncate=False))
        g_hdr = exp_canvas.pad_cell(Txt(t("calendar.time_hdr"), role="schema"), _GUTTER_W, align="center")
        out.append(g_hdr + v_sep + v_sep.join(hdr_cells))
        out.append(rule_row)

        # Row 2: ALL-DAY Ribbon
        all_cells = []
        for dt in week_days:
            iso_d = dt.isoformat()
            ad_evs = [e for e in events_by_date.get(iso_d, []) if not e.get("start_time")]
            if not ad_evs:
                all_cells.append(exp_canvas.pad_cell(Txt("\u00b7", role="inactive"), day_w, align="center"))
            else:
                e0 = ad_evs[0]
                is_insp = insp_id is not None and str(e0["id"]) == str(insp_id)
                b_role = "intent" if is_insp else "vitality"
                extra = f"+{len(ad_evs) - 1}" if len(ad_evs) > 1 else ""
                max_t = max(3, day_w - 2 - (len(extra) + 1 if extra else 0))
                t_p = exp_canvas.truncate_plain(e0["title"], max_t)
                uri_ev = f"cal:event:{e0['id']}"
                register_scroll_zone(uri_ev, "cal:wscroll")
                mk = (
                    f"<a href='{uri_ev}'><span underline='none'>"
                    f"{Txt('\u2022 ', role=b_role)}{Txt(t_p, role='focus')}"
                    f"{Txt(' ' + extra, role='attention') if extra else ''}"
                    f"</span></a>"
                )
                all_cells.append(exp_canvas.pad_cell(mk, day_w, align="left", truncate=False))
        g_all = exp_canvas.pad_cell(Txt(t("calendar.all_day_short"), role="schema"), _GUTTER_W, align="center")
        out.append(g_all + v_sep + v_sep.join(all_cells))
        out.append(rule_row)

        # Precompute timed spans per day
        day_timed = []
        before_cnt, after_cnt = 0, 0
        h_end = h_start + _VIS_HOURS
        for dt in week_days:
            iso_d = dt.isoformat()
            spans = []
            for ev in events_by_date.get(iso_d, []):
                sp = _event_span_mins(ev)
                if sp is not None:
                    spans.append((sp[0], sp[1], ev))
                    if sp[1] <= h_start * 60:
                        before_cnt += 1
                    elif sp[0] >= h_end * 60:
                        after_cnt += 1
            day_timed.append((iso_d, spans))

        # Hourly rows (3 rows per hour: Header/Time, Title/Close, Separator/Bridge)
        today_in_week = any(d.isoformat() == today_iso for d in week_days)
        for h_idx in range(_VIS_HOURS):
            hr = h_start + h_idx
            h0, h1 = hr * 60, (hr + 1) * 60
            is_cur_hr = today_in_week and (hr == now_hm.hour)

            g_uri = f"cal:add_hour:{hr:02d}"
            register_scroll_zone(g_uri, "cal:wscroll")
            g_lbl = f"\u25b8{hr:02d}:00" if is_cur_hr else f" {hr:02d}:00"
            g_top = (
                f"<a href='{g_uri}'><span underline='none'>"
                f"{Txt(g_lbl.ljust(_GUTTER_W)[:_GUTTER_W], role='attention' if is_cur_hr else 'schema')}"
                f"</span></a>"
            )
            if h_idx == 0 and before_cnt > 0:
                g_mid = exp_canvas.pad_cell(Txt(f"\u2191{before_cnt}", role="attention"), _GUTTER_W, align="center")
            elif h_idx == _VIS_HOURS - 1 and after_cnt > 0:
                g_mid = exp_canvas.pad_cell(Txt(f"\u2193{after_cnt}", role="attention"), _GUTTER_W, align="center")
            else:
                g_mid = " " * _GUTTER_W

            row1_cells, row2_cells, row3_cells = [], [], []
            for iso_d, spans in day_timed:
                starting = [(st, et, e) for (st, et, e) in spans if h0 <= st < h1]
                continuing = [(st, et, e) for (st, et, e) in spans if st < h0 and et > h0]

                if continuing and not starting:
                    st, et, ev = continuing[0]
                    is_insp = insp_id is not None and str(ev["id"]) == str(insp_id)
                    r_col = "intent" if is_insp else "vitality"
                    cont_past = et > h1
                    uri_ev = f"cal:event:{ev['id']}"
                    register_scroll_zone(uri_ev, "cal:wscroll")

                    # Row 1: show @ location only on the hour immediately after start
                    loc = str(ev.get("location") or "").strip()
                    first_cont_hr = (hr == (st // 60) + 1)
                    if loc and first_cont_hr:
                        sub = exp_canvas.truncate_plain(f"@ {loc}", max(3, day_w - 2))
                        r1_inner = Txt("\u2502 ", role=r_col) + Txt(sub, role="schema")
                    elif len(ev["title"]) > day_w - 2 and first_cont_hr:
                        rem = ev["title"][max(0, day_w - 3) :].strip()
                        sub = exp_canvas.truncate_plain(rem, max(3, day_w - 2))
                        r1_inner = Txt("\u2502 ", role=r_col) + Txt(sub, role="schema")
                    else:
                        r1_inner = Txt("\u2502", role=r_col)
                    row1_cells.append(exp_canvas.pad_cell(f"<a href='{uri_ev}'><span underline='none'>{r1_inner}</span></a>", day_w, truncate=False))

                    # Row 2: close block at end hour or keep vertical rail open
                    if cont_past:
                        r2_mk = f"<a href='{uri_ev}'><span underline='none'>{Txt('\u2502', role=r_col)}</span></a>"
                    else:
                        end_hm = ev.get("end_time", "")
                        if end_hm and not end_hm.endswith(":00") and et < h1:
                            close_s = f"\u2570\u2500 {end_hm[:5]} " + "\u2500" * max(0, day_w - 9)
                        else:
                            close_s = "\u2570" + ("\u2500" * max(0, day_w - 1))
                        r2_mk = f"<a href='{uri_ev}'><span underline='none'>{Txt(close_s[:day_w], role=r_col)}</span></a>"
                    row2_cells.append(exp_canvas.pad_cell(r2_mk, day_w, truncate=False))

                    # Row 3: bridge vertically if continues past h1, else dashed separator
                    if cont_past:
                        row3_cells.append(exp_canvas.pad_cell(Txt("\u2502", role=r_col), day_w, truncate=False))
                    else:
                        row3_cells.append(dot_day)

                elif len(starting) >= 2:
                    st1, et1, e1 = starting[0]
                    st2, et2, e2 = starting[1]
                    r1_col = "intent" if (insp_id is not None and str(e1["id"]) == str(insp_id)) else "vitality"
                    r2_col = "intent" if (insp_id is not None and str(e2["id"]) == str(insp_id)) else "vitality"
                    u1, u2 = f"cal:event:{e1['id']}", f"cal:event:{e2['id']}"
                    register_scroll_zone(u1, "cal:wscroll")
                    register_scroll_zone(u2, "cal:wscroll")
                    tm1 = e1["start_time"][:5]
                    tm2 = e2["start_time"][:5]
                    t1_s = exp_canvas.truncate_plain(e1["title"], max(2, day_w - 8))
                    mk1 = f"<a href='{u1}'><span underline='none'>{Txt('\u2022 ', role=r1_col)}{Txt(tm1 + ' ', role='val')}{Txt(t1_s, role='focus')}</span></a>"
                    extra = f"+{len(starting) - 2}" if len(starting) > 2 else ""
                    t2_s = exp_canvas.truncate_plain(e2["title"], max(2, day_w - 8 - (len(extra) + 1 if extra else 0)))
                    mk2 = (
                        f"<a href='{u2}'><span underline='none'>"
                        f"{Txt('\u2022 ', role=r2_col)}{Txt(tm2 + ' ', role='val')}{Txt(t2_s, role='focus')}"
                        f"{Txt(' ' + extra, role='attention') if extra else ''}"
                        f"</span></a>"
                    )
                    row1_cells.append(exp_canvas.pad_cell(mk1, day_w, truncate=False))
                    row2_cells.append(exp_canvas.pad_cell(mk2, day_w, truncate=False))
                    cont_ev = next(((st, et, e) for (st, et, e) in (continuing + starting) if et > h1), None)
                    if cont_ev is not None:
                        rc = "intent" if (insp_id is not None and str(cont_ev[2]["id"]) == str(insp_id)) else "vitality"
                        row3_cells.append(exp_canvas.pad_cell(Txt("\u2502", role=rc), day_w, truncate=False))
                    else:
                        row3_cells.append(dot_day)

                elif len(starting) == 1:
                    st, et, ev = starting[0]
                    is_insp = insp_id is not None and str(ev["id"]) == str(insp_id)
                    r_col = "intent" if is_insp else "vitality"
                    cont_past = et > h1
                    uri_ev = f"cal:event:{ev['id']}"
                    register_scroll_zone(uri_ev, "cal:wscroll")
                    if ev.get("end_time"):
                        tm_rng = f"{ev['start_time'][:5]}\u2013{ev['end_time'][:5]}"
                    else:
                        tm_rng = ev["start_time"][:5]
                    if day_w < 14 and len(tm_rng) > max(5, day_w - 3):
                        tm_rng = ev["start_time"][:5]
                    ov_s = f"+{len(continuing)}" if continuing else ""
                    r1_mk = (
                        f"<a href='{uri_ev}'><span underline='none'>"
                        f"{Txt('\u256d ', role=r_col)}{Txt(tm_rng, role='val')}"
                        f"{Txt(' ' + ov_s, role='attention') if ov_s else ''}"
                        f"</span></a>"
                    )
                    end_ch = "\u2502 " if cont_past else "\u2570 "
                    t_s = exp_canvas.truncate_plain(ev["title"], max(3, day_w - 2))
                    r2_mk = (
                        f"<a href='{uri_ev}'><span underline='none'>"
                        f"{Txt(end_ch, role=r_col)}{Txt(t_s, role='focus')}"
                        f"</span></a>"
                    )
                    row1_cells.append(exp_canvas.pad_cell(r1_mk, day_w, truncate=False))
                    row2_cells.append(exp_canvas.pad_cell(r2_mk, day_w, truncate=False))
                    if cont_past:
                        row3_cells.append(exp_canvas.pad_cell(Txt("\u2502", role=r_col), day_w, truncate=False))
                    else:
                        row3_cells.append(dot_day)

                else:
                    slot_uri = f"cal:slot:{iso_d}:{hr:02d}"
                    register_scroll_zone(slot_uri, "cal:wscroll")
                    blank_click = f"<a href='{slot_uri}'><span underline='none'>{' ' * day_w}</span></a>"
                    row1_cells.append(blank_click)
                    row2_cells.append(blank_click)
                    row3_cells.append(dot_day)

            out.append(g_top + v_sep + v_sep.join(row1_cells))
            out.append(g_mid + v_sep + v_sep.join(row2_cells))
            if h_idx < _VIS_HOURS - 1:
                g_sep = Txt("\u2508" * _GUTTER_W, role="structure")
                out.append(g_sep + cross_s + cross_s.join(row3_cells))

        return out

    def _build_month_left(self, exp_canvas: Canvas, events_by_date: dict, today_iso: str, insp_id, left_w: int = _LEFT_W) -> list[str]:
        cal_matrix = calendar.monthcalendar(self.year, self.month)
        days_long = get_days_long()
        cw = max(10, (left_w - 6) // 7)  # 7*17 + 6 = 125 cols exact at 1x
        v_sep = Txt("\u2502", role="structure")
        rule = Txt("\u2500" * (cw * 7 + 6), role="structure")

        out = [
            v_sep.join(exp_canvas.pad_cell(Txt(exp_canvas.truncate_plain(g, cw), role="schema"), cw, align="center") for g in days_long),
            rule,
        ]
        n_weeks = len(cal_matrix)
        slots_per_week = 3 if n_weeks <= 5 else 2
        for w_idx, week in enumerate(cal_matrix):
            num_cells = []
            for d in week:
                if d == 0:
                    num_cells.append(" " * cw)
                    continue
                iso_d = f"{self.year}-{self.month:02d}-{d:02d}"
                evs = events_by_date.get(iso_d, [])
                cnt = f" ({len(evs)})" if evs else ""
                if iso_d == self.selected_date:
                    mk = f"<a href='cal:day:{iso_d}'><span underline='none'>{Txt(f'[{d:02d}]', role='intent', bg=True)}{Txt(cnt, role='schema') if cnt else ''}</span></a>"
                elif iso_d == today_iso:
                    mk = f"<a href='cal:day:{iso_d}'><span underline='none'>{Txt(f' {d:02d}*', role='attention')}{Txt(cnt, role='schema') if cnt else ''}</span></a>"
                elif evs:
                    mk = f"<a href='cal:day:{iso_d}'><span underline='none'>{Txt(f' {d:02d}\u00b7', role='vitality')}{Txt(cnt, role='schema')}</span></a>"
                else:
                    mk = f"<a href='cal:day:{iso_d}'><span underline='none'>{Txt(f' {d:02d}', role='focus')}</span></a>"
                num_cells.append(exp_canvas.pad_cell(mk, cw, align="left", truncate=False))
            out.append(v_sep.join(num_cells))

            for sl in range(slots_per_week):
                ev_cells = []
                for d in week:
                    if d == 0:
                        ev_cells.append(" " * cw)
                        continue
                    iso_d = f"{self.year}-{self.month:02d}-{d:02d}"
                    evs = events_by_date.get(iso_d, [])
                    if sl < len(evs):
                        if sl == slots_per_week - 1 and len(evs) > slots_per_week:
                            rem_s = f" +{len(evs) - slots_per_week + 1}.."
                            mk = f"<a href='cal:day:{iso_d}'><span underline='none'>{Txt(rem_s, role='schema')}</span></a>"
                        else:
                            ev = evs[sl]
                            tm = ev["start_time"][:5] if ev.get("start_time") else "\u2022"
                            txt_t = exp_canvas.truncate_plain(f"{tm} {ev['title']}", cw - 1)
                            is_insp = insp_id is not None and str(ev["id"]) == str(insp_id)
                            mk = f"<a href='cal:event:{ev['id']}'><span underline='none'>{Txt(txt_t, role='intent' if is_insp else 'focus')}</span></a>"
                        ev_cells.append(exp_canvas.pad_cell(mk, cw, align="left", truncate=False))
                    else:
                        ev_cells.append(" " * cw)
                out.append(v_sep.join(ev_cells))
            if w_idx < n_weeks - 1:
                out.append(rule)
        return out

    def _build_day_inspector_right(
        self,
        exp_canvas: Canvas,
        sel_events: list[dict],
        insp_ev: dict | None,
        initialized: bool,
        mode: str,
        has_feeds: bool,
        feed_names: dict[str, str],
        target_rows: int,
        right_w: int = _RIGHT_W,
    ) -> list[str]:
        inner_w = max(24, right_w - 2)  # 1 space margin left and right
        out = []

        if not initialized:
            out.append(" " + exp_canvas.pad_cell(Txt(t("calendar.setup"), role="focus"), inner_w))
            out.append(" " + Txt("\u2500" * inner_w, role="structure"))
            out.append(" ")
            for wl in wrap_pango_markup(Canvas.escape(t("calendar.calendar_not_initialized_yet")), max_chars=inner_w):
                out.append(" " + exp_canvas.pad_cell(Txt(html.unescape(wl), role="inactive"), inner_w))
            out.append(" ")
            btn = ActionBtn(t("calendar.initialize_calendar"), "ui:open_settings:datetime:calendar", tone="apply")
            out.append(" " + exp_canvas.pad_cell(btn, inner_w, align="left", truncate=False))
            return out

        # Header row: Selected date + quick actions
        hdr_title = f"{format_date_it(self.selected_date)} ({len(sel_events)})"
        add_btn = ActionBtn(t("calendar.add_btn"), "cal:add_start", tone="apply")
        sync_btn = f" {ActionBtn('\uf021', 'cal:sync', tone='neutral')}" if (mode == "ical" and has_feeds) else ""
        cfg_btn = ActionBtn("\uf013", "ui:open_settings:datetime:calendar", tone="neutral")
        r_acts = f"{add_btn}{sync_btn} {cfg_btn}"
        r_acts_w = exp_canvas.measure_cols(r_acts)
        hdr_l = exp_canvas.pad_cell(Txt(exp_canvas.truncate_plain(hdr_title, max(10, inner_w - r_acts_w - 1)), role="focus"), max(10, inner_w - r_acts_w))
        out.append(" " + hdr_l + r_acts + " ")
        out.append(" " + Txt("\u2500" * inner_w, role="structure"))

        # Event list for selected day (cap at 10 visible rows to keep inspector generous)
        insp_id = insp_ev["id"] if insp_ev else None
        if not sel_events:
            out.append(" " + exp_canvas.pad_cell(Txt(t("calendar.no_events_day").strip(), role="inactive"), inner_w))
        else:
            max_list = 10 if insp_ev else min(18, len(sel_events))
            shown = sel_events[:max_list]
            for ev in shown:
                is_sel = insp_id is not None and str(ev["id"]) == str(insp_id)
                is_local = (ev.get("source") == "local")
                tm = ev["start_time"][:5] if ev.get("start_time") else t("calendar.all_day_short")
                cell_budget = inner_w - (8 if is_local else 4)
                lbl_t = exp_canvas.truncate_plain(f"{tm:<5} {ev['title']}", max(8, cell_budget))
                insp_cell = InspectorCell(lbl_t, f"cal:event:{ev['id']}", selected=is_sel)
                if is_local:
                    del_b = ActionBtn("x", f"cal:del:{ev['id']}", tone="danger_soft")
                    out.append(
                        exp_canvas.format_table_row(
                            [insp_cell, del_b],
                            [inner_w - 4, 3],
                            ["left", "right"],
                            truncate=False,
                            left_margin=" ",
                            sep_cols=1,
                        ) + " "
                    )
                else:
                    out.append(" " + exp_canvas.pad_cell(insp_cell, inner_w, truncate=False))
            if len(sel_events) > max_list:
                out.append(" " + exp_canvas.pad_cell(Txt(t("calendar.more_events_day", p0=len(sel_events) - max_list), role="schema"), inner_w))

        # Lower half: Inspected Event Detail
        if insp_ev is not None:
            out.append(" ")
            det_lbl = f"\u2500\u2500 {t('calendar.event_detail')} "
            det_rule = det_lbl + "\u2500" * max(2, inner_w - len(det_lbl))
            out.append(" " + Txt(det_rule[:inner_w], role="schema") + " ")
            for wl in wrap_pango_markup(Canvas.escape(insp_ev["title"]), max_chars=inner_w)[:3]:
                out.append(" " + exp_canvas.pad_cell(Txt(html.unescape(wl), role="focus"), inner_w))

            tm_full = (
                f"{insp_ev['start_time']} \u2013 {insp_ev['end_time']}"
                if insp_ev.get("end_time")
                else (insp_ev["start_time"] or t("calendar.all_day"))
            )
            dt_line = f"\uf017  {format_date_it(insp_ev['date'])} \u00b7 {tm_full}"
            out.append(" " + exp_canvas.pad_cell(Txt(exp_canvas.truncate_plain(dt_line, inner_w), role="val"), inner_w))

            src_tag = str(insp_ev.get("source") or "local")
            src_lbl = t("calendar.source_local") if src_tag == "local" else feed_names.get(src_tag, "iCal")
            out.append(" " + exp_canvas.pad_cell(Txt(f"{t('calendar.source_lbl')}: {src_lbl}", role="schema"), inner_w))

            if insp_ev.get("location"):
                loc_lines = _trunc_line_with_urls(f"@ {insp_ev['location']}", inner_w)
                for ll in loc_lines[:2]:
                    out.append(" " + exp_canvas.pad_cell(ll, inner_w, truncate=False))

            if insp_ev.get("description"):
                out.append(" " + Txt("\u2508" * inner_w, role="structure"))
                desc_lines = _trunc_line_with_urls(insp_ev["description"], inner_w)
                avail_desc = max(2, target_rows - len(out) - 3)
                for dl in desc_lines[:avail_desc]:
                    out.append(" " + exp_canvas.pad_cell(dl, inner_w, truncate=False))

        while len(out) < max(len(out), target_rows - 2):
            out.append(" ")
        out.append(" " + Txt("\u2500" * inner_w, role="structure"))
        if mode == "local":
            foot_mk = Txt(t("calendar.local_sqlite"), role="schema")
        elif has_feeds:
            foot_mk = StateBadge(True, t("calendar.ical_active"), on_tone="ok")
        else:
            foot_mk = StateBadge(False, "", t("calendar.no_ical_feeds"))
        out.append(" " + exp_canvas.pad_cell(foot_mk, inner_w, truncate=False))
        return out

    def build_cards(self, canvas: Canvas, force_expanded: bool = False) -> list:
        months = get_months()
        months_s = get_months_short()
        expanded = force_expanded or self.is_expanded
        initialized = is_calendar_initialized()
        mode = get_calendar_mode()

        if not expanded:
            if initialized:
                events, has_feeds = fetch_calendar_events(
                    self.year,
                    self.month,
                    on_complete=lambda: self.shell.refresh_view() if self.shell else None,
                )
            else:
                events, has_feeds = [], False
            return self._build_compact(canvas, events, has_feeds, initialized, mode)

        exp_cols, left_w, right_w, day_w = self._compute_expanded_cols(canvas.total_cols)
        exp_canvas = canvas if canvas.total_cols == exp_cols else Canvas(total_cols=exp_cols)

        try:
            sel_dt = datetime.date.fromisoformat(self.selected_date)
        except Exception:
            sel_dt = datetime.date.today()
            self.selected_date = sel_dt.isoformat()

        week_mon = sel_dt - datetime.timedelta(days=sel_dt.weekday())
        week_days = [week_mon + datetime.timedelta(days=i) for i in range(7)]
        week_sun = week_days[-1]

        m_first = datetime.date(self.year, self.month, 1)
        m_last = datetime.date(self.year, self.month, calendar.monthrange(self.year, self.month)[1])
        q_start = min(m_first, week_mon)
        q_end = max(m_last, week_sun)

        if initialized:
            events, has_feeds = fetch_calendar_range(
                q_start.isoformat(),
                q_end.isoformat(),
                on_complete=lambda: self.shell.refresh_view() if self.shell else None,
            )
        else:
            events, has_feeds = [], False

        events_by_date = {}
        for ev in events:
            events_by_date.setdefault(ev["date"], []).append(ev)

        today_iso = datetime.date.today().isoformat()
        sel_events = events_by_date.get(self.selected_date, [])

        insp_ev = None
        if self.selected_event_id is not None:
            cand = next((e for e in events if str(e["id"]) == str(self.selected_event_id)), None)
            if cand is not None:
                insp_ev = cand
        if insp_ev is None and sel_events:
            insp_ev = sel_events[0]
        insp_id = insp_ev["id"] if insp_ev else None

        h_start = self._compute_week_h_start(week_days, events_by_date)
        h_last = h_start + _VIS_HOURS - 1

        if self.view_mode == "week":
            left_lines = self._build_week_left(exp_canvas, week_days, events_by_date, today_iso, insp_id, h_start, day_w=day_w)
            if week_mon.month == week_sun.month:
                title_s = f"{week_mon.day:02d} \u2013 {week_sun.day:02d} {months_s[week_sun.month].upper()} {week_sun.year} (W{week_mon.isocalendar()[1]:02d})"
            else:
                title_s = f"{week_mon.day:02d} {months_s[week_mon.month].upper()} \u2013 {week_sun.day:02d} {months_s[week_sun.month].upper()} {week_sun.year} (W{week_mon.isocalendar()[1]:02d})"
            extra_acts = [
                SelectPill(t("calendar.view_week"), "cal:view:week", selected=True),
                SelectPill(t("calendar.view_month"), "cal:view:month", selected=False),
                f"{Txt(f'{h_start:02d}:00\u2013{h_last:02d}:00', role='schema')} {ActionBtn('\uf077', 'cal:wscroll:up', tone='neutral')} {ActionBtn('\uf078', 'cal:wscroll:down', tone='neutral')}",
            ]
        else:
            left_lines = self._build_month_left(exp_canvas, events_by_date, today_iso, insp_id, left_w=left_w)
            title_s = f"{months[self.month].upper()} {self.year}"
            extra_acts = [
                SelectPill(t("calendar.view_week"), "cal:view:week", selected=False),
                SelectPill(t("calendar.view_month"), "cal:view:month", selected=True),
            ]

        feed_names = get_ical_feed_names()
        right_lines = self._build_day_inspector_right(
            exp_canvas,
            sel_events,
            insp_ev,
            initialized,
            mode,
            has_feeds,
            feed_names,
            target_rows=len(left_lines),
            right_w=right_w,
        )

        box = Box(
            title_s,
            icon="\uf073",
            actions=self._nav_cluster(True, extra_actions=extra_acts),
            highlighted=False,
            fixed_width=True,
            min_cols=exp_cols,
            max_cols=exp_cols,
        )
        box.add_split(left_lines, right_lines, ratios=[left_w, right_w], junctions=True)
        return [box]

    def on_prompt_submit(self, action_id: str, text: str) -> None:
        if action_id == "cal_add_title":
            target_date = self._pending_event.get("date", self.selected_date)
            prefill = self._pending_event.get("prefill_time", "")
            self._pending_event = {
                "date": target_date,
                "title": text.strip(),
            }
            ph_time = t("calendar.time_e_g_14_30_or_14_30_15_30_ente")
            self.request_prompt(
                "cal_add_time",
                ph_time,
                prefill,
                title=t("calendar.time", p0=format_date_it(target_date)),
                required=False,
            )
        elif action_id == "cal_add_time":
            st, et = parse_time_input(text)
            self._pending_event["start_time"] = st
            self._pending_event["end_time"] = et
            ph_desc = t("calendar.description_optional_enter_to_save")
            self.request_prompt(
                "cal_add_desc",
                ph_desc,
                "",
                title=t("calendar.description", p0=self._pending_event.get("title", "")[:20]),
                required=False,
            )
        elif action_id == "cal_add_desc":
            if self._pending_event.get("title"):
                add_local_event(
                    date_iso=self._pending_event.get("date", self.selected_date),
                    title=self._pending_event["title"],
                    start_time=self._pending_event.get("start_time", ""),
                    end_time=self._pending_event.get("end_time", ""),
                    description=text.strip(),
                    source="local",
                )
            self._pending_event = {}

    def on_link(self, uri: str) -> None:
        if uri == "cal:add_start":
            self._pending_event = {"date": self.selected_date}
            self.request_prompt(
                "cal_add_title",
                t("calendar.title_2"),
                "",
                title=f"+ {format_date_it(self.selected_date)}",
                required=True,
            )
            return
        if uri.startswith("cal:add_hour:") or uri.startswith("cal:slot:"):
            parts = uri.split(":")
            if uri.startswith("cal:slot:") and len(parts) >= 4:
                self.selected_date = parts[2]
                dt = datetime.date.fromisoformat(self.selected_date)
                self.year, self.month = dt.year, dt.month
                hh = int(parts[3])
            else:
                hh = int(parts[2])
            h2 = min(23, hh + 1)
            prefill = f"{hh:02d}:00-{h2:02d}:00" if h2 > hh else f"{hh:02d}:00-23:59"
            self._pending_event = {"date": self.selected_date, "prefill_time": prefill}
            self.request_prompt(
                "cal_add_title",
                t("calendar.title_2"),
                "",
                title=f"+ {format_date_it(self.selected_date)} ({hh:02d}:00)",
                required=True,
            )
            return
        if uri.startswith("cal:del:"):
            ev_id = uri.split(":", 2)[2]
            delete_event_by_id(ev_id)
            if str(self.selected_event_id) == str(ev_id):
                self.selected_event_id = None
            if self.shell:
                self.shell.refresh_view()
            return
        if uri.startswith("cal:view:"):
            vm = uri.split(":", 2)[2]
            if vm in ("week", "month"):
                self.view_mode = vm
                self.set_state("view_mode", self.view_mode)
        elif uri.startswith("cal:wscroll:"):
            d = uri.rsplit(":", 1)[1]
            cur = self.hour_offset
            if cur is None:
                try:
                    sel_dt = datetime.date.fromisoformat(self.selected_date)
                except Exception:
                    sel_dt = datetime.date.today()
                w_mon = sel_dt - datetime.timedelta(days=sel_dt.weekday())
                evs, _ = fetch_calendar_range(w_mon.isoformat(), (w_mon + datetime.timedelta(days=6)).isoformat())
                by_d = {}
                for e in evs:
                    by_d.setdefault(e["date"], []).append(e)
                cur = self._compute_week_h_start([w_mon + datetime.timedelta(days=i) for i in range(7)], by_d)
            step = -1 if d == "up" else 1
            self.hour_offset = max(0, min(24 - _VIS_HOURS, cur + step))
            self.set_state("hour_offset", self.hour_offset)
        elif uri == "cal:prev":
            if self.is_expanded and self.view_mode == "week":
                dt = datetime.date.fromisoformat(self.selected_date) - datetime.timedelta(days=7)
                self.selected_date, self.year, self.month = dt.isoformat(), dt.year, dt.month
                self.hour_offset = None
                self.set_state("hour_offset", None)
            else:
                self.month, self.year = (12, self.year - 1) if self.month == 1 else (self.month - 1, self.year)
            self.selected_event_id = None
        elif uri == "cal:next":
            if self.is_expanded and self.view_mode == "week":
                dt = datetime.date.fromisoformat(self.selected_date) + datetime.timedelta(days=7)
                self.selected_date, self.year, self.month = dt.isoformat(), dt.year, dt.month
                self.hour_offset = None
                self.set_state("hour_offset", None)
            else:
                self.month, self.year = (1, self.year + 1) if self.month == 12 else (self.month + 1, self.year)
            self.selected_event_id = None
        elif uri == "cal:today":
            today = datetime.date.today()
            self.year, self.month, self.selected_date = today.year, today.month, today.isoformat()
            self.hour_offset = None
            self.set_state("hour_offset", None)
            self.selected_event_id = None
        elif uri.startswith("cal:day:"):
            self.selected_date = uri.split(":", 2)[2]
            try:
                dt = datetime.date.fromisoformat(self.selected_date)
                self.year, self.month = dt.year, dt.month
            except Exception:
                pass
            self.show_all = False
            self.selected_event_id = None
        elif uri == "cal:toggle_all":
            self.show_all = not self.show_all
            self.selected_event_id = None
        elif uri.startswith("cal:event:"):
            ev_id = uri.split(":", 2)[2]
            self.selected_event_id = ev_id
            # Sync selected_date if user clicked an event on a different day of the week
            try:
                sel_dt = datetime.date.fromisoformat(self.selected_date)
                w_mon = sel_dt - datetime.timedelta(days=sel_dt.weekday())
                evs, _ = fetch_calendar_range(
                    (w_mon - datetime.timedelta(days=31)).isoformat(),
                    (w_mon + datetime.timedelta(days=38)).isoformat(),
                )
                ev = next((e for e in evs if str(e["id"]) == str(ev_id)), None)
                if ev and ev.get("date"):
                    self.selected_date = ev["date"]
                    dt = datetime.date.fromisoformat(ev["date"])
                    self.year, self.month = dt.year, dt.month
            except Exception:
                pass
        elif uri == "cal:close_event":
            self.selected_event_id = None
        elif uri == "cal:sync":
            m_last = calendar.monthrange(self.year, self.month)[1]
            fetch_calendar_range(
                f"{self.year:04d}-{self.month:02d}-01",
                f"{self.year:04d}-{self.month:02d}-{m_last:02d}",
                force_sync=True,
                on_complete=lambda: self.shell.refresh_view() if self.shell else None,
            )
            return

        self.set_state("year", self.year)
        self.set_state("month", self.month)
        self.set_state("selected_date", self.selected_date)
        self.set_state("show_all", self.show_all)
        self.set_state("selected_event_id", self.selected_event_id)
        if self.shell:
            self.shell.refresh_view()
