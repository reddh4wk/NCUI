#!/usr/bin/env python3
import time
from core import probe
from core.completion import catalog_timezones, rank_candidates
from core.i18n import t
from core.state import STATE
from ui.canvas import Canvas
from ui.kit import ActionBtn, Badge, Box, SelectPill, StateBadge, Txt, get_active_inline_entry
from panels.cc_engine_datetime import (
    _CLOCK_FMTS,
    _feedback_cards,
    _probe_timedate,
)


def _tz_earth_icon(tz: str) -> str:
    if not probe.has_nerd_font():
        return ""
    region = (tz or "").split("/", 1)[0].lower()
    if region in ("europe", "atlantic", "arctic", "cet", "eet", "wet", "met"):
        return "\uef4b"  # nf-fa-earth_europe
    if region in ("america", "us", "canada", "brazil", "chile", "mexico"):
        return "\uee46"  # nf-fa-earth_americas
    if region in ("asia", "australia", "pacific", "indian", "japan", "singapore", "hongkong", "prc", "roc", "rok"):
        return "\uee47"  # nf-fa-earth_asia
    if region in ("africa", "egypt"):
        return "\uee45"  # nf-fa-earth_africa
    return "\uf0ac"      # nf-fa-globe


def _build_time_standard(cc_panel, canvas: Canvas) -> list:
    d = _probe_timedate()
    cards = _feedback_cards()
    now_s = time.strftime("%Y-%m-%d  %H:%M:%S %Z")

    if d["ntp_sync"]:
        ntp_hdr = StateBadge(True, t("cc_datetime.ntp_synced"), on_tone="ok")
    elif d["ntp"]:
        ntp_hdr = Badge(t("cc_datetime.unsynced"), style="warn")
    else:
        ntp_hdr = StateBadge(False, "", t("cc_datetime.manual_mode"))

    b_time = Box(
        t("cc_datetime.time_synchronization_ntp_systemd_t"),
        icon="\uf017",
        actions=[ntp_hdr, ActionBtn(t("cc_datetime.sync_now"), "cc_dt:sync_now", tone="apply")],
        highlighted=False,
    )
    b_time.add_row(" ")
    b_time.add_kv(t("cc_datetime.system_clock"), Txt(now_s, role="val"))
    b_time.add_kv(t("cc_datetime.automatic_ntp"), f"{SelectPill(t('common.on'), 'cc_dt:ntp:true', selected=d['ntp'])} {SelectPill(t('common.off'), 'cc_dt:ntp:false', selected=not d['ntp'])}", requires=["timedatectl"])
    b_time.add_kv(t("cc_datetime.hardware_rtc"), f"{SelectPill('UTC', 'cc_dt:rtc:0', selected=not d['rtc_local'])} {SelectPill(t('cc_datetime.local'), 'cc_dt:rtc:1', selected=d['rtc_local'])}")
    b_time.add_kv(t("cc_datetime.ntp_pool_server"), Txt(d["ntp_srv"], role="val"), right_markup=ActionBtn(t("cc_datetime.configure_term"), "cc_dt:ntp_term", tone="neutral"))
    b_time.add_row(" ")
    cards.append(b_time)

    b_tz = Box(
        t("cc_datetime.active_timezone_geographic_presets"),
        icon="\uf0ac",
        highlighted=False,
    )
    b_tz.add_row(" ")
    b_tz.add_kv(t("cc_datetime.current_timezone"), Txt(d["tz"], role="val"))

    tz_q = str(STATE.get("datetime", "tz_query", "") or "").strip()
    is_searching = (get_active_inline_entry() == "cc_dt_tz_picker")
    if tz_q or is_searching:
        ranked = rank_candidates(catalog_timezones(exclude=d["tz"]), tz_q, limit=8)
        res_pills = [(canvas.truncate_plain(val, 22), f"cc_dt:set_tz:{val}") for val, _lbl in ranked]
    else:
        res_pills = []
    b_tz.add_search_box(
        t("cc_datetime.search_timezone"),
        query=tz_q,
        placeholder=t("cc_datetime.search_tz_placeholder"),
        trigger_uri="cc_dt:tz_search_open",
        results=res_pills,
        clear_uri="cc_dt:tz_search_clear",
        active=is_searching,
        max_pills_per_row=2,
        width=32,
        entry_id="cc_dt_tz_picker",
        show_empty=bool(tz_q),
    )
    b_tz.add_row(" ")
    tz_ic = _tz_earth_icon(d["tz"])
    if tz_ic:
        b_tz.set_side_emblem(tz_ic, label=t("cc_datetime.emblem_tz"), role="schema", max_rows=3.6)
    cards.append(b_tz)

    b_reg = Box(t("cc_datetime.region_settings_bar_clock_format"), icon="\uf1ab", highlighted=False)
    b_reg.add_row(" ")
    lang_pills = f"{SelectPill('en_US.UTF-8', 'cc_dt:lang:en_US.UTF-8', selected=('en_US' in d['lang']))} {SelectPill('it_IT.UTF-8', 'cc_dt:lang:it_IT.UTF-8', selected=('it_IT' in d['lang']))}"
    lang_match = ("en_US" in d["lang"]) or ("it_IT" in d["lang"])
    b_reg.add_kv(t("cc_datetime.lang_system"), lang_pills if lang_match else f"{Txt(d['lang'], role='val')}  {lang_pills}", requires=["localectl"])

    lctime_pills = f"{SelectPill(t('cc_datetime.24h_it_it'), 'cc_dt:lctime:it_IT.UTF-8', selected=('it_IT' in d['lc_time']))} {SelectPill('POSIX/C', 'cc_dt:lctime:C.UTF-8', selected=d['lc_time'].startswith('C'))}"
    lctime_match = ("it_IT" in d["lc_time"]) or d["lc_time"].startswith("C")
    b_reg.add_kv(t("cc_datetime.lc_time_24h_12h"), lctime_pills if lctime_match else f"{Txt(d['lc_time'], role='val')}  {lctime_pills}", requires=["localectl"])

    fmt_match = any(d["bar_fmt"] == fmt for _lbl_key, fmt in _CLOCK_FMTS)
    if not fmt_match:
        b_reg.add_kv(t("cc_datetime.waybar_clock_format"), Txt(d["bar_fmt"], role="val"))
    fmt_pills = " ".join(SelectPill(t(lbl_key), f"cc_dt:barfmt:{i}", selected=(d["bar_fmt"] == fmt)) for i, (lbl_key, fmt) in enumerate(_CLOCK_FMTS))
    b_reg.add_kv(t("cc_datetime.bar_format_presets") if fmt_match else t("cc_datetime.waybar_clock_format"), fmt_pills)
    b_reg.add_row(" ")
    cards.append(b_reg)

    return cards


def build_cards(cc_panel, canvas: Canvas) -> list:
    return _build_time_standard(cc_panel, canvas)
