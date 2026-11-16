#!/usr/bin/env python3
import calendar
import datetime
import html
from core.config import THEME
from core.i18n import t, get_active_language
from ui.canvas import Canvas
from ui.kit import Box, ToggleExpandBtn, Txt, ActionBtn, StateBadge
from ui.markup import get_months, get_days_long, format_date_it, wrap_pango_markup
from panels.base import BasePanel
from panels.cc_calendar import (
    is_calendar_initialized,
    get_calendar_mode,
    fetch_calendar_events,
    parse_time_input,
    add_local_event,
    delete_event_by_id,
    check_and_trigger_reminders,
)


class Panel(BasePanel):
    id = "calendar"
    icon = "\uf073"
    supports_expand = True

    def __init__(self, shell=None):
        self.title = t("calendar.title", "Calendar")
        super().__init__(shell)
        today = datetime.date.today()
        self.year = int(self.get_state("year", today.year))
        self.month = int(self.get_state("month", today.month))
        self.selected_date = str(self.get_state("selected_date", today.isoformat()))
        self.show_all = bool(self.get_state("show_all", False))
        self.selected_event_id = self.get_state("selected_event_id", None)
        self._pending_event = {}

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

    def _nav_cluster(self, expanded: bool) -> list[str]:
        nav_group = (
            f"{Txt('[', role='structure')}"
            f"{ActionBtn('<', 'cal:prev', tone='select', brackets=False)} "
            f"{ActionBtn('\u25cf', 'cal:today', tone='neutral', brackets=False)} "
            f"{ActionBtn('>', 'cal:next', tone='select', brackets=False)}"
            f"{Txt(']', role='structure')}"
        )
        return [nav_group, ToggleExpandBtn(expanded)]

    def build_cards(self, canvas: Canvas, force_expanded: bool = False) -> list:
        is_it = (get_active_language() == "it")
        months = get_months()
        days_long = get_days_long()
        expanded = force_expanded or self.is_expanded
        initialized = is_calendar_initialized()
        mode = get_calendar_mode()

        if initialized:
            events, is_logged_in = fetch_calendar_events(
                self.year, self.month,
                on_complete=lambda: self.shell.refresh_view() if self.shell else None
            )
        else:
            events, is_logged_in = [], False

        events_by_date = {}
        for ev in events:
            events_by_date.setdefault(ev["date"], []).append(ev)

        cal_matrix = calendar.monthcalendar(self.year, self.month)
        today_iso = datetime.date.today().isoformat()
        cards = []

        if not expanded:
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
                            cell, role, use_bg = f"[{d:2d}{dot}]", "intent", True
                        elif iso_d == today_iso:
                            cell, role, use_bg = f" {d:2d}{dot} ", "attention", False
                        elif has_ev:
                            cell, role, use_bg = f" {d:2d}\u00b7 ", "vitality", False
                        else:
                            cell, role, use_bg = f" {d:2d}  ", "focus", False
                        w_str += f"<a href='cal:day:{iso_d}'><span underline='none'>{Txt(cell, role=role, bg=use_bg)}</span></a>"
                w_str += "   "
                box.add_row(w_str)

            # Disposto su due righe distinte per mantenere il frame esattamente a 44 colonne
            if not initialized:
                box.add_section("Setup" if not is_it else "Configurazione")
                msg_uninit = "Calendar not initialized." if not is_it else "Calendario non inizializzato."
                btn_init = ActionBtn(
                    "\uf013 Initialize Calendar" if not is_it else "\uf013 Inizializza Calendario",
                    "ui:open_settings:calendar",
                    tone="apply",
                    active=True,
                )
                box.add_row(canvas.pad_cell(Txt(msg_uninit, role="inactive"), canvas.inner_cols, align="center"))
                box.add_row(canvas.pad_cell(btn_init, canvas.inner_cols, align="center", truncate=False))
                cards.append(box)
                return cards

            add_btn = ActionBtn("+", "cal:add_start", tone="apply", active=True)
            if not self.show_all:
                sel_events = events_by_date.get(self.selected_date, [])
                box.add_section(
                    t("calendar.events_on", date=format_date_it(self.selected_date)),
                    actions=[add_btn, ActionBtn(t("calendar.show_all"), "cal:toggle_all", tone="neutral")],
                )
                if not sel_events:
                    box.add_row(Txt(t("calendar.no_events_day"), role="inactive"))
                else:
                    for ev in sel_events:
                        tm = ev["start_time"] or "AllDay"
                        ev_id = ev["id"]
                        t_clean = canvas.truncate_plain(ev["title"], 25)
                        del_btn = ActionBtn("x", f"cal:del:{ev_id}", tone="danger_soft")
                        box.add_row(
                            f" {Txt(f'{tm:<5}', role='intent')}  <a href='cal:event:{ev_id}'><span underline='none'>{Txt(t_clean, role='focus')}</span></a>",
                            right_markup=del_btn,
                        )
            else:
                box.add_section(
                    t("calendar.events_month", month=months[self.month]),
                    actions=[add_btn, ActionBtn(t("calendar.show_less"), "cal:toggle_all", tone="neutral")],
                )
                fut_events = [ev for ev in events if ev["date"] >= today_iso]
                if not fut_events:
                    box.add_row(Txt(t("calendar.no_events_month"), role="inactive"))
                else:
                    fut_by_date = {}
                    for ev in fut_events:
                        fut_by_date.setdefault(ev["date"], []).append(ev)
                    for dt_str in sorted(fut_by_date.keys()):
                        box.add_row(f" {Txt(f'\u25b8 {format_date_it(dt_str)}', role='intent')}")
                        for ev in fut_by_date[dt_str]:
                            tm = ev["start_time"] or "AllDay"
                            ev_id = ev["id"]
                            t_clean = canvas.truncate_plain(ev["title"], 24)
                            del_btn = ActionBtn("x", f"cal:del:{ev_id}", tone="danger_soft")
                            box.add_row(
                                f"   {Txt(f'{tm:<5}', role='schema')} <a href='cal:event:{ev_id}'><span underline='none'>{Txt(t_clean, role='focus')}</span></a>",
                                right_markup=del_btn,
                            )

            box.add_section(t("calendar.account_section"))
            if mode == "local":
                st_left = f" {Txt('\uf1c0  Local SQLite', role='vitality')}"
                st_right = ActionBtn("\uf013", "ui:open_settings:calendar", tone="select")
            else:
                st_left = (
                    f" {StateBadge(True, t('calendar.google_connected'), on_tone='ok')}"
                    if is_logged_in
                    else f" {StateBadge(True, t('calendar.not_connected'), on_tone='warn')}"
                )
                st_right = f"{ActionBtn(t('calendar.sync'), 'cal:sync', tone='select', active=True)}  {ActionBtn('\uf013', 'ui:open_settings:calendar', tone='neutral')}"
            box.add_row(st_left, right_markup=st_right)
            cards.append(box)
        else:
            box = Box(
                f"{t('calendar.google_title')} \u2014 {months[self.month].upper()} {self.year}",
                icon="\uf073",
                actions=self._nav_cluster(True),
                highlighted=False,
            )
            cw = 12
            hdr = " " + " ".join(Txt(g.center(cw), role="schema") for g in days_long) + " "
            box.add_row(hdr)
            box.add_row(f" {Txt('\u2500' * (cw * 7 + 6), role='structure')} ")
            for w_idx, week in enumerate(cal_matrix):
                r_num = []
                for d in week:
                    if d == 0:
                        r_num.append(" " * cw)
                    else:
                        iso_d = f"{self.year}-{self.month:02d}-{d:02d}"
                        evs = events_by_date.get(iso_d, [])
                        cnt = f" ({len(evs)})" if evs else ""
                        if iso_d == self.selected_date:
                            txt, role, use_bg = f"[{d:02d}]{cnt}".ljust(cw)[:cw], "intent", True
                        elif iso_d == today_iso:
                            txt, role, use_bg = f" {d:02d}*{cnt}".ljust(cw)[:cw], "attention", False
                        elif evs:
                            txt, role, use_bg = f" {d:02d}\u00b7{cnt}".ljust(cw)[:cw], "vitality", False
                        else:
                            txt, role, use_bg = f" {d:02d} ".ljust(cw)[:cw], "focus", False
                        r_num.append(f"<a href='cal:day:{iso_d}'><span underline='none'>{Txt(txt, role=role, bg=use_bg)}</span></a>")
                box.add_row(" " + " ".join(r_num) + " ")
                for slot in range(2):
                    r_ev = []
                    for d in week:
                        if d == 0:
                            r_ev.append(" " * cw)
                        else:
                            iso_d = f"{self.year}-{self.month:02d}-{d:02d}"
                            evs = events_by_date.get(iso_d, [])
                            if slot < len(evs):
                                if slot == 1 and len(evs) > 2:
                                    mt = f" +{len(evs) - 1}..".ljust(cw)[:cw]
                                    r_ev.append(f"<a href='cal:day:{iso_d}'><span underline='none'>{Txt(mt, role='intent')}</span></a>")
                                else:
                                    ev = evs[slot]
                                    ev_id = ev["id"]
                                    tm = ev["start_time"][:5] if ev["start_time"] else "\u00b7\u00b7"
                                    t_str = f"{tm} {ev['title']}"[:cw].ljust(cw)
                                    r_ev.append(f"<a href='cal:event:{ev_id}'><span underline='none'>{Txt(t_str, role='focus')}</span></a>")
                            else:
                                r_ev.append(" " * cw)
                    box.add_row(" " + Txt("\u2502", role="structure").join(r_ev) + " ")
                if w_idx < len(cal_matrix) - 1:
                    box.add_row(f" {Txt('\u2500' * (cw * 7 + 6), role='structure')} ")

            if not initialized:
                box.add_section("Setup" if not is_it else "Configurazione Iniziale")
                btn_init = ActionBtn(
                    "\uf013 Initialize Calendar" if not is_it else "\uf013 Inizializza Calendario",
                    "ui:open_settings:calendar",
                    tone="apply",
                    active=True,
                )
                msg = "Calendar not initialized yet." if not is_it else "Calendario non ancora inizializzato."
                box.add_row(f" {Txt(msg, role='inactive')}", right_markup=btn_init)
                cards.append(box)
                return cards

            sel_events = events_by_date.get(self.selected_date, [])
            add_btn = ActionBtn("+", "cal:add_start", tone="apply", active=True)
            sec_acts = [add_btn]
            if mode != "local":
                sec_acts.append(ActionBtn(t("calendar.sync"), "cal:sync", tone="select", active=True))
            sec_acts.append(ActionBtn("\uf013", "ui:open_settings:calendar", tone="neutral"))

            box.add_section(
                f"{t('calendar.events_on', date=format_date_it(self.selected_date))} ({len(sel_events)})",
                actions=sec_acts,
            )
            if not sel_events:
                box.add_row(Txt(t("calendar.no_events_grid"), role="inactive"))
            else:
                for ev in sel_events:
                    tm_range = f"{ev['start_time']} - {ev['end_time']}" if ev.get("end_time") else (ev["start_time"] or t("calendar.all_day"))
                    loc_str = f"  @ {ev['location'][:24]}" if ev.get("location") else ""
                    ev_id = ev["id"]
                    del_btn = ActionBtn("x", f"cal:del:{ev_id}", tone="danger_soft")
                    box.add_row(
                        f" {Txt(f'\u2022 {tm_range:<13}', role='intent')} "
                        f"<a href='cal:event:{ev_id}'><span underline='none'>{Txt(ev['title'][:45], role='focus')}</span></a>"
                        f"{Txt(loc_str, role='schema')}",
                        right_markup=del_btn,
                    )
            cards.append(box)

        if self.selected_event_id is not None:
            ev = next((e for e in events if str(e["id"]) == str(self.selected_event_id)), None)
            if ev:
                wrap_w = 88 if expanded else 36
                det = Box(
                    t("calendar.event_detail"),
                    icon="\uf073",
                    actions=[
                        ActionBtn("x", f"cal:del:{ev['id']}", tone="danger_soft"),
                        ActionBtn(t("common.close"), "cal:close_event", tone="neutral"),
                    ],
                    elected=True,
                )
                for w_l in wrap_pango_markup(Canvas.escape(ev["title"]), max_chars=wrap_w):
                    det.add_row(Txt(f" {html.unescape(w_l)}", role="focus"))
                tm_full = f"{ev['start_time']} - {ev['end_time']}" if ev.get("end_time") else (ev["start_time"] or t("calendar.all_day"))
                det.add_row(Txt(f" \uf017  {format_date_it(ev['date'])}   {tm_full}", role="val"))
                if ev.get("location"):
                    for w_l in wrap_pango_markup(Canvas.escape(f"@  {ev['location']}"), max_chars=wrap_w):
                        det.add_row(Txt(f" {html.unescape(w_l)}", role="intent"))
                if ev.get("description"):
                    det.add_section(t("calendar.notes_desc"))
                    count = 0
                    for raw_l in ev["description"].splitlines():
                        if not raw_l.strip():
                            continue
                        for w_l in wrap_pango_markup(Canvas.escape(raw_l), max_chars=wrap_w):
                            det.add_row(Txt(f" {html.unescape(w_l)}", role="schema"))
                            count += 1
                            if count >= 10:
                                break
                        if count >= 10:
                            break
                cards.append(det)
        return cards

    def on_prompt_submit(self, action_id: str, text: str) -> None:
        is_it = (get_active_language() == "it")
        if action_id == "cal_add_title":
            self._pending_event = {
                "date": self.selected_date,
                "title": text.strip(),
            }
            ph_time = "Ora (es. 14:30 o 14:30-15:30, Invio per Tutto il giorno)" if is_it else "Time (e.g. 14:30 or 14:30-15:30, Enter for All-Day)"
            self.request_prompt(
                "cal_add_time",
                ph_time,
                "",
                title=f"{format_date_it(self.selected_date)} \u203a Time",
                required=False,
            )
        elif action_id == "cal_add_time":
            st, et = parse_time_input(text)
            self._pending_event["start_time"] = st
            self._pending_event["end_time"] = et
            ph_desc = "Descrizione (opzionale, Invio per salvare)" if is_it else "Description (optional, Enter to save)"
            self.request_prompt(
                "cal_add_desc",
                ph_desc,
                "",
                title=f"{self._pending_event.get('title', '')[:20]} \u203a Description",
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
            self._pending_event = {}
            self.request_prompt(
                "cal_add_title",
                "Title",
                "",
                title=f"+ {format_date_it(self.selected_date)}",
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
        if uri == "cal:prev":
            self.month, self.year = (12, self.year - 1) if self.month == 1 else (self.month - 1, self.year)
            self.selected_event_id = None
        elif uri == "cal:next":
            self.month, self.year = (1, self.year + 1) if self.month == 12 else (self.month + 1, self.year)
            self.selected_event_id = None
        elif uri == "cal:today":
            today = datetime.date.today()
            self.year, self.month, self.selected_date = today.year, today.month, today.isoformat()
            self.selected_event_id = None
        elif uri.startswith("cal:day:"):
            self.selected_date = uri.split(":", 2)[2]
            self.show_all = False
            self.selected_event_id = None
        elif uri == "cal:toggle_all":
            self.show_all = not self.show_all
            self.selected_event_id = None
        elif uri.startswith("cal:event:"):
            self.selected_event_id = uri.split(":", 2)[2]
        elif uri == "cal:close_event":
            self.selected_event_id = None
        elif uri == "cal:sync":
            fetch_calendar_events(
                self.year, self.month, force_sync=True,
                on_complete=lambda: self.shell.refresh_view() if self.shell else None
            )
            return
        self.set_state("year", self.year)
        self.set_state("month", self.month)
        self.set_state("selected_date", self.selected_date)
        self.set_state("show_all", self.show_all)
        self.set_state("selected_event_id", self.selected_event_id)
        if self.shell:
            self.shell.refresh_view()
