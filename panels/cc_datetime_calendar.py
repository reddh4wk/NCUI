#!/usr/bin/env python3
import time
from core import probe
from core.i18n import t
from core.state import STATE
from ui.canvas import Canvas
from ui.kit import ActionBtn, Badge, Box, InlineField, SelectPill, StateBadge, Txt
from panels.cc_engine_datetime import (
    is_calendar_initialized,
    get_calendar_mode,
    get_reminder_minutes,
    get_db_stats,
    is_google_authenticated,
)

def build_cards(cc_panel, canvas: Canvas) -> list:
    init_ok = is_calendar_initialized()
    mode = get_calendar_mode()
    stats = get_db_stats()
    status_msg = STATE.get("calendar", "cc_status_msg", "")

    cards = []

    oauth_ok = is_google_authenticated()
    has_gcal = probe.has_bin("gcalcli")
    g_user = str(STATE.get("calendar", "gcal_user", "") or "")
    g_cid = str(STATE.get("calendar", "gcal_client_id", "") or "")
    g_csec = str(STATE.get("calendar", "gcal_client_secret", "") or "")
    last_sync = float(STATE.get("calendar", "last_sync_ts", 0.0) or 0.0)
    sync_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(last_sync)) if last_sync > 0 else t("cc_calendar.never")

    st_badge = StateBadge(True, t("cc_calendar.connected"), on_tone="ok") if oauth_ok else Badge(t("cc_calendar.not_authenticated"), style="warn")
    g_box = Box(
        t("cc_calendar.google_calendar_synchronization"),
        icon="\uf1a0",
        actions=[st_badge],
        highlighted=False,
    )
    g_box.add_row(" ")
    gcal_badge = StateBadge(True, t("cc_calendar.installed"), on_tone="ok") if has_gcal else Badge(t("cc_calendar.missing_paru_s_gcalcli"), style="warn")
    g_box.add_kv(t("cc_calendar.gcalcli_bin"), gcal_badge, key_width=18)
    g_box.add_kv(t("cc_calendar.oauth2_token"), StateBadge(oauth_ok, t("cc_calendar.active_gcalcli_oauth"), t("cc_calendar.not_initialized"), on_tone="ok"), key_width=18)
    g_box.add_kv(
        t("cc_calendar.last_sync"),
        Txt(sync_str, role="val" if last_sync > 0 else "inactive"),
        key_width=18,
        right_markup=ActionBtn(t("cc_calendar.force_sync_now"), "cc_cal:sync_now", tone="apply"),
    )

    g_box.add_section(t("cc_calendar.account_oauth2_credentials"))
    g_box.add_kv(
        t("cc_calendar.google_email"),
        Txt(g_user or t("cc_calendar.not_set"), role="val" if g_user else "inactive"),
        key_width=18,
        right_markup=ActionBtn(t("cc_calendar.edit_email"), "cc_cal:prompt_user", tone="neutral"),
    )
    cid_disp = (g_cid[:22] + "\u2026" + g_cid[-14:]) if len(g_cid) > 40 else (g_cid or t("cc_calendar.not_set"))
    g_box.add_kv(
        t("cc_calendar.client_id"),
        Txt(cid_disp, role="val" if g_cid else "inactive"),
        key_width=18,
        right_markup=ActionBtn(t("cc_calendar.set_client_id"), "cc_cal:prompt_cid", tone="neutral"),
    )
    csec_disp = ("\u25cf" * 16) if g_csec else t("cc_calendar.not_set")
    g_box.add_kv(
        t("cc_calendar.client_secret"),
        Txt(csec_disp, role="val" if g_csec else "inactive"),
        key_width=18,
        right_markup=ActionBtn(t("cc_calendar.set_secret"), "cc_cal:prompt_csec", tone="neutral"),
    )

    g_box.add_section(t("cc_calendar.authentication_actions"))
    login_btn = ActionBtn(t("cc_calendar.start_google_oauth2_login"), "cc_cal:oauth_login", tone="apply")
    reset_btn = ActionBtn(t("cc_calendar.reset_token"), "cc_cal:oauth_reset", tone="danger_soft")
    g_box.add_row(f"  {login_btn}    {reset_btn}")

    url_proj = "https://console.cloud.google.com/projectcreate"
    url_api = "https://console.cloud.google.com/apis/library/calendar-json.googleapis.com"
    url_cred = "https://console.cloud.google.com/apis/credentials"

    def _url_link(u: str) -> str:
        return f"<a href='{u}'><span underline='single'>{Txt(u, role='intent')}</span></a>"

    def _build_oauth_guide(b: Box) -> None:
        b.add_row(f"  {Txt(t('cc_calendar.oauth_s1_title'), role='focus')}")
        b.add_row(f"     {t('cc_calendar.oauth_open_prefix')} {_url_link(url_proj)}")
        b.add_row(f"     {t('cc_calendar.oauth_s1_hint', proj=Txt('gcalendarncui', role='schema'))}")
        b.add_section()
        b.add_row(f"  {Txt(t('cc_calendar.oauth_s2_title'), role='focus')}")
        b.add_row(f"     {t('cc_calendar.oauth_open_prefix')} {_url_link(url_api)}")
        b.add_row(f"     {t('cc_calendar.oauth_s2_hint', btn=Txt(t('cc_calendar.oauth_enable_btn'), role='schema'))}")
        b.add_section()
        b.add_row(f"  {Txt(t('cc_calendar.oauth_s3_title'), role='focus')}")
        b.add_row(f"     {t('cc_calendar.oauth_goto_prefix')} {_url_link(url_cred)}:")
        b.add_row(f"     {t('cc_calendar.oauth_s3_b1', ext=Txt(t('cc_calendar.external'), role='schema'), testers=Txt(t('cc_calendar.test_users'), role='schema'))}")
        b.add_row(f"     {t('cc_calendar.oauth_s3_b2', cred=Txt(t('cc_calendar.create_credentials_oauth_client_id'), role='schema'), app=Txt(t('cc_calendar.desktop_app'), role='schema'))}")
        b.add_row(f"     {t('cc_calendar.oauth_s3_b3', cid=Txt(t('cc_calendar.client_id'), role='schema'), sec=Txt(t('cc_calendar.client_secret'), role='schema'), btn=Txt(t('cc_calendar.oauth_login_btn'), role='vitality'))}")

    g_box.add_collapsible(cc_panel, "cal_oauth_guide", t("cc_calendar.google_cloud_oauth2_setup_guide"), _build_oauth_guide)
    g_box.add_row(" ")
    cards.append(g_box)

    io_box = Box(
        t("cc_calendar.database_import_export_ics_db_n", p0=stats["total"]),
        icon="\uf56e",
        highlighted=False,
    )
    io_box.add_row(" ")
    if status_msg:
        io_box.add_row(f"  {Txt(f'\uf05a {status_msg}', role='val')}")
        io_box.add_section()

    io_box.add_section(t("cc_calendar.export_calendar"))
    io_box.add_row(
        f"  {Txt(t('cc_calendar.rfc_5545_standard_ics'), role='focus')}",
        right_markup=ActionBtn(t("cc_calendar.export_ics"), "cc_cal:export_ics", tone="neutral"),
    )
    io_box.add_row(
        f"  {Txt(t('cc_calendar.sqlite_binary_snapshot_db'), role='focus')}",
        right_markup=ActionBtn(t("cc_calendar.export_db"), "cc_cal:export_db", tone="neutral"),
    )

    io_box.add_section(t("cc_calendar.import_calendar"))
    io_box.add_row(
        f"  {Txt(t('cc_calendar.import_events_from_ics_file'), role='focus')}",
        right_markup=ActionBtn(t("cc_calendar.import_ics"), "cc_cal:import_ics", tone="apply"),
    )
    io_box.add_row(
        f"  {Txt(t('cc_calendar.restore_merge_from_sqlite_db_file'), role='focus')}",
        right_markup=ActionBtn(t("cc_calendar.import_db"), "cc_cal:import_db", tone="neutral"),
    )
    io_box.add_row(" ")
    cards.append(io_box)

    # ── TAB PREDEFINITA: MODE, REMINDERS & LOCAL DB ──
    init_badge = StateBadge(True, t("cc_calendar.initialized"), on_tone="ok") if init_ok else Badge(t("cc_calendar.uninitialized"), style="warn")
    m_box = Box(
        t("cc_calendar.calendar_engine_storage_mode"),
        icon="\uf073",
        actions=[init_badge],
        highlighted=False,
    )
    m_box.add_row(" ")
    if not init_ok:
        msg = t("cc_calendar.select_an_operating_mode_below_to")
        m_box.add_row(f"  {Txt(f'\uf071 {msg}', role='attention')}")
        m_box.add_section()

    modes_spec = [
        (
            "local",
            t("cc_calendar.local_sqlite_only_100_offline"),
            t("cc_calendar.all_appointments_are_stored_locall"),
        ),
        (
            "google_local",
            t("cc_calendar.google_calendar_local_sqlite_mirro"),
            t("cc_calendar.syncs_with_google_calendar_and_kee"),
        ),
        (
            "google_cloud",
            t("cc_calendar.google_calendar_cloud_only_no_loca"),
            t("cc_calendar.fetches_google_events_into_volatil"),
        ),
    ]

    for m_id, m_title, m_desc in modes_spec:
        is_sel = init_ok and (mode == m_id)
        btn_lbl = t("cc_calendar.active") if is_sel else t("cc_calendar.select_initialize")
        m_box.add_row(
            f"  {Txt(m_title, role='focus')}",
            right_markup=SelectPill(btn_lbl, f"cc_cal:mode:{m_id}", selected=is_sel),
        )
        m_box.add_row(f"     {Txt(m_desc, role='schema')}")
        m_box.add_section()

    m_box.add_kv(t("cc_calendar.sqlite_path"), Txt(f"{stats['path']} ({stats['size_kb']:.1f} KB)", role="val"), key_width=16)
    stat_desc = f"(Local: {stats['local']} | Google Mirror: {stats['google']})"
    m_box.add_kv(
        t("cc_calendar.stored_events"),
        f"{Txt(str(stats['total']), role='val')} {Txt(stat_desc, role='schema')}",
        key_width=16,
        right_markup=ActionBtn(t("cc_calendar.clear_local_events"), "cc_cal:clear_local", tone="danger_soft"),
    )
    m_box.add_row(" ")
    cards.append(m_box)

    # ── CARD 2: PREAVVISO NOTIFICHE (nc_util_notify.sh) ──
    rem_mins = get_reminder_minutes()
    rem_badge = StateBadge(rem_mins > 0, t("cc_calendar.min_before", p0=rem_mins), t("common.off"), on_tone="ok")
    r_box = Box(
        t("cc_calendar.event_reminder_notifications_nc_ut"),
        icon="\uf0f3",
        actions=[rem_badge],
        highlighted=False,
    )
    r_box.add_row(" ")
    presets = [0, 5, 10, 15, 30, 60]
    p_btns = []
    for p in presets:
        lbl = t("cc_power.off") if p == 0 else f"{p}m"
        is_cur = (rem_mins == p)
        p_btns.append(SelectPill(lbl, f"cc_cal:rem_set:{p}", selected=is_cur))
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
    test_btn = ActionBtn(t("cc_calendar.test_notify"), "cc_cal:rem_test", tone="neutral")

    r_box.add_kv(t("cc_calendar.lead_time").rstrip(":"), "  ".join(p_btns), right_markup=test_btn)
    r_box.add_row(" ")
    cards.append(r_box)
    return cards
