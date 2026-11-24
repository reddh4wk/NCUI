#!/usr/bin/env python3
import calendar
import datetime
import fcntl
import hashlib
import json
import os
import re
import shlex
import shutil
import sqlite3
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zoneinfo
from core.config import CONFIG_DIR
from core.completion import catalog_timezones, rank_candidates
from core.state import STATE
from core.i18n import t
from core import probe, log
from ui.canvas import Canvas
from ui.kit import FeedbackBox, start_inline_input

DB_PATH = os.path.join(CONFIG_DIR, "calendar.db")
ICAL_FEEDS_FILE = os.path.join(CONFIG_DIR, "ical_feeds.json")
CLOUD_CACHE_DIR = os.path.expanduser("~/.cache/nc-ui")
REMINDER_STATE_FILE = os.path.join(CLOUD_CACHE_DIR, "calendar_reminded.json")
NOTIF_HIST_FILE = os.path.expanduser("~/.cache/nc_notifications.json")
_ICAL_SYNC_LOCK = threading.Lock()
_ICAL_LOCK_FILE = "/tmp/nc_ical_sync.lock"

_MSG = ""
_ERR = ""
_ALL_TZS: list[str] | None = None
_NTP_PRESETS = ["0.arch.pool.ntp.org 1.arch.pool.ntp.org", "time.cloudflare.com", "pool.ntp.org"]
_CLOCK_FMTS = [
    ("24h Compact", "{:%H:%M}"),
    ("24h + Date", "{:%a %d %b  %H:%M}"),
    ("ISO Full", "{:%Y-%m-%d %H:%M:%S}"),
    ("12h AM/PM", "{:%I:%M %p}"),
]


def _load_all_timezones() -> list[str]:
    global _ALL_TZS
    if _ALL_TZS is None:
        tzs = [
            z for z in zoneinfo.available_timezones()
            if ("/" in z and not z.startswith(("posix/", "right/", "SystemV/", "Etc/"))) or z == "UTC"
        ]
        _ALL_TZS = sorted(tzs)
    return _ALL_TZS


def _feedback_cards() -> list:
    if not _MSG and not _ERR:
        return []
    return [FeedbackBox(t("cc_datetime.status_title"), not bool(_ERR), _ERR if _ERR else _MSG)]

def _probe_timedate() -> dict:
    info = {
        "tz": time.tzname[0] if time.tzname else "UTC",
        "ntp": False,
        "ntp_sync": False,
        "rtc_local": False,
        "ntp_srv": "default (arch pool)",
        "lang": os.environ.get("LANG", "en_US.UTF-8"),
        "lc_time": os.environ.get("LC_TIME", "C.UTF-8"),
        "bar_fmt": "{:%H:%M}",
    }
    if os.path.islink("/etc/localtime"):
        target = os.path.realpath("/etc/localtime")
        if "/zoneinfo/" in target:
            info["tz"] = target.split("/zoneinfo/", 1)[1]

    ok, out, _ = probe.run_cmd_full(["timedatectl", "show"], timeout=1.5)
    if ok and out:
        for ln in out.splitlines():
            if "=" in ln:
                k, v = ln.split("=", 1)
                if k == "Timezone" and v:
                    info["tz"] = v
                elif k == "NTP":
                    info["ntp"] = (v == "yes")
                elif k == "NTPSynchronized":
                    info["ntp_sync"] = (v == "yes")
                elif k == "LocalRTC":
                    info["rtc_local"] = (v == "yes")

    if os.path.exists("/etc/systemd/timesyncd.conf"):
        try:
            for ln in open("/etc/systemd/timesyncd.conf", "r", encoding="utf-8", errors="ignore"):
                s = ln.strip()
                if s.startswith("NTP="):
                    info["ntp_srv"] = s.split("=", 1)[1].strip() or info["ntp_srv"]
        except Exception:
            pass

    if os.path.exists("/etc/locale.conf"):
        try:
            for ln in open("/etc/locale.conf", "r", encoding="utf-8", errors="ignore"):
                s = ln.strip()
                if s.startswith("LANG="):
                    info["lang"] = s.split("=", 1)[1].strip().strip('"')
                elif s.startswith("LC_TIME="):
                    info["lc_time"] = s.split("=", 1)[1].strip().strip('"')
        except Exception:
            pass

    wb_cfg = os.path.expanduser("~/.config/waybar/config.jsonc")
    if os.path.exists(wb_cfg):
        try:
            raw = open(wb_cfg, "r", encoding="utf-8", errors="ignore").read()
            m = re.search(r'"clock"\s*:\s*\{[^}]*?"format"\s*:\s*"([^"]+)"', raw, re.DOTALL)
            if m:
                info["bar_fmt"] = m.group(1)
        except Exception:
            pass
    return info


def init_db(db_path: str = None) -> bool:
    target_db = db_path or DB_PATH
    try:
        os.makedirs(os.path.dirname(target_db), exist_ok=True)
        with sqlite3.connect(target_db, timeout=5.0) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    uid TEXT UNIQUE,
                    date_iso TEXT NOT NULL,
                    start_time TEXT DEFAULT '',
                    end_time TEXT DEFAULT '',
                    title TEXT NOT NULL,
                    location TEXT DEFAULT '',
                    description TEXT DEFAULT '',
                    source TEXT DEFAULT 'local',
                    updated_at REAL DEFAULT 0
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_date ON events(date_iso)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_source ON events(source)")
            conn.commit()
        return True
    except Exception as e:
        log.error(f"Calendar SQLite init_db error: {e}")
        return False


def is_calendar_initialized() -> bool:
    return bool(STATE.get("calendar", "initialized", False))


def set_calendar_initialized(val: bool = True) -> None:
    if val:
        init_db()
    STATE.set("calendar", "initialized", bool(val))


def get_calendar_mode() -> str:
    m = str(STATE.get("calendar", "mode", "ical")).strip()
    if m in ("google_local", "google_cloud"):
        return "ical"
    return "local" if m == "local" else "ical"


def set_calendar_mode(mode: str) -> None:
    norm = "ical" if mode in ("ical", "google_local", "google_cloud") else ("local" if mode == "local" else "")
    if not norm:
        return
    init_db()
    STATE.set("calendar", "mode", norm)
    STATE.set("calendar", "initialized", True)
    try:
        with sqlite3.connect(DB_PATH, timeout=5.0) as conn:
            conn.execute("DELETE FROM events WHERE source = 'google'")
            conn.commit()
    except Exception:
        pass


def get_reminder_minutes() -> int:
    try:
        return max(0, min(1440, int(STATE.get("calendar", "reminder_minutes", 15))))
    except Exception:
        return 15


def set_reminder_minutes(mins: int) -> int:
    clamped = max(0, min(1440, int(mins)))
    STATE.set("calendar", "reminder_minutes", clamped)
    return clamped


def _resolve_notify_script() -> str:
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidates = [
        os.path.join(root_dir, "nc_util_notify.sh"),
        os.path.join(root_dir, "bin", "nc_util_notify.sh"),
        os.path.expanduser("~/Projects/ncui/nc_util_notify.sh"),
        os.path.expanduser("~/Projects/nc-ui/nc_util_notify.sh"),
        "/usr/local/lib/nc-ui/nc_util_notify.sh",
        "/usr/local/bin/nc_util_notify.sh",
        "/usr/local/sbin/nc_util_notify.sh",
        shutil.which("nc_util_notify.sh") or "",
    ]
    for c in candidates:
        if c and os.path.exists(c):
            return c
    return ""


def dispatch_nc_notification(title: str, body: str) -> None:
    script = _resolve_notify_script()
    if script:
        probe.spawn_bg(["bash", script, "--title", str(title), "--body", str(body)])
        return
    try:
        notifs = []
        if os.path.exists(NOTIF_HIST_FILE):
            with open(NOTIF_HIST_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, list):
                    notifs = loaded
        notifs.insert(0, {
            "id": int(time.time() * 1000),
            "title": str(title),
            "body": str(body),
            "deleted": False,
        })
        os.makedirs(os.path.dirname(NOTIF_HIST_FILE), exist_ok=True)
        with open(NOTIF_HIST_FILE, "w", encoding="utf-8") as f:
            json.dump(notifs[:50], f)
    except Exception as e:
        log.error(f"Fallback notification write error: {e}")


def check_and_trigger_reminders() -> None:
    if not is_calendar_initialized():
        return
    lead_mins = get_reminder_minutes()
    if lead_mins <= 0:
        return

    now_ts = time.time()
    os.makedirs(CLOUD_CACHE_DIR, exist_ok=True)
    rem_state = {"last_check": 0.0, "date": "", "notified": []}
    if os.path.exists(REMINDER_STATE_FILE):
        try:
            with open(REMINDER_STATE_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, dict):
                    rem_state.update(loaded)
        except Exception:
            pass

    if now_ts - float(rem_state.get("last_check", 0.0)) < 20.0:
        return

    now_dt = datetime.datetime.now()
    today_iso = now_dt.date().isoformat()
    if rem_state.get("date") != today_iso:
        rem_state["date"] = today_iso
        rem_state["notified"] = []

    notified_set = set(rem_state.get("notified", []))
    events, _ = fetch_calendar_events(now_dt.year, now_dt.month, force_sync=False)
    today_events = [e for e in events if e.get("date") == today_iso and e.get("start_time")]

    for ev in today_events:
        st = str(ev.get("start_time", "")).strip()
        m = re.match(r"^(\d{1,2}):(\d{2})$", st)
        if not m:
            continue
        ev_key = f"{today_iso}|{st}|{ev.get('title', '')}"
        if ev_key in notified_set:
            continue
        ev_dt = now_dt.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0)
        diff_sec = (ev_dt - now_dt).total_seconds()
        if 0.0 <= diff_sec <= (lead_mins * 60.0):
            mins_left = max(1, int(round(diff_sec / 60.0)))
            hdr = t("cc_calendar.calendar", p0=st)
            body = t("cc_calendar.in_min", p0=ev['title'], p1=mins_left)
            if ev.get("location"):
                body += f" @ {ev['location']}"
            dispatch_nc_notification(hdr, body)
            notified_set.add(ev_key)

    rem_state["last_check"] = now_ts
    rem_state["notified"] = list(notified_set)
    try:
        with open(REMINDER_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(rem_state, f)
    except Exception:
        pass


def get_db_stats() -> dict:
    stats = {"total": 0, "local": 0, "ical": 0, "size_kb": 0.0, "path": DB_PATH}
    if not is_calendar_initialized():
        if os.path.exists(DB_PATH):
            try:
                with sqlite3.connect(DB_PATH, timeout=2.0) as conn:
                    cnt = int(conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] or 0)
                if cnt == 0:
                    os.remove(DB_PATH)
                    for ext in ("-wal", "-shm"):
                        if os.path.exists(DB_PATH + ext):
                            try:
                                os.remove(DB_PATH + ext)
                            except Exception:
                                pass
                    return stats
            except Exception:
                pass
        return stats

    if not os.path.exists(DB_PATH):
        init_db()
    if os.path.exists(DB_PATH):
        try:
            stats["size_kb"] = os.path.getsize(DB_PATH) / 1024.0
            with sqlite3.connect(DB_PATH, timeout=5.0) as conn:
                stats["total"] = int(conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] or 0)
                stats["local"] = int(conn.execute("SELECT COUNT(*) FROM events WHERE source = 'local'").fetchone()[0] or 0)
                stats["ical"] = int(conn.execute("SELECT COUNT(*) FROM events WHERE source != 'local'").fetchone()[0] or 0)
        except Exception:
            pass
    return stats


def _hhmm_to_mins(s: str) -> int | None:
    m = re.match(r"^(\d{1,2}):(\d{2})", str(s or "").strip())
    if not m:
        return None
    hh, mm = int(m.group(1)), int(m.group(2))
    return hh * 60 + mm if 0 <= hh <= 23 and 0 <= mm <= 59 else None


def get_calendar_horizon_telemetry(days: int = 7) -> dict:
    now_dt = datetime.datetime.now()
    today = now_dt.date()
    today_iso = today.isoformat()
    now_hm = now_dt.strftime("%H:%M")
    end_30_iso = (today + datetime.timedelta(days=30)).isoformat()

    events = _query_db_range(today_iso, end_30_iso, include_ical=(get_calendar_mode() == "ical"))
    by_date: dict[str, list[dict]] = {}
    for ev in events:
        by_date.setdefault(ev["date"], []).append(ev)

    horizon_days = []
    for off in range(days):
        cur_iso = (today + datetime.timedelta(days=off)).isoformat()
        d_evs = by_date.get(cur_iso, [])
        booked = 0
        for ev in d_evs:
            st_m = _hhmm_to_mins(ev.get("start_time", ""))
            if st_m is None:
                booked += 60
            else:
                et_m = _hhmm_to_mins(ev.get("end_time", ""))
                booked += (et_m - st_m) if (et_m is not None and et_m > st_m) else 60
        load_pct = min(100, int(round((booked / 480.0) * 100.0))) if booked > 0 else 0
        horizon_days.append({
            "date_iso": cur_iso,
            "is_today": (off == 0),
            "count": len(d_evs),
            "booked_mins": booked,
            "load_pct": load_pct,
        })

    upcoming = [
        ev for ev in events
        if ev["date"] > today_iso or not ev.get("start_time") or (ev.get("end_time") or ev["start_time"]) >= now_hm
    ]
    if not upcoming and events:
        upcoming = list(events)

    return {
        "today_count": len(by_date.get(today_iso, [])),
        "week_count": sum(h["count"] for h in horizon_days),
        "month_count": len(events),
        "next_event": upcoming[0] if upcoming else None,
        "horizon_days": horizon_days,
        "upcoming_list": upcoming[:7],
    }


def parse_time_input(raw_time: str) -> tuple[str, str]:
    s = str(raw_time or "").strip()
    if not s:
        return "", ""
    m_range = re.match(r"^(\d{1,2}:\d{2})\s*[-–]\s*(\d{1,2}:\d{2})$", s)
    if m_range:
        t1, t2 = m_range.group(1), m_range.group(2)
        return f"{int(t1.split(':')[0]):02d}:{t1.split(':')[1]}", f"{int(t2.split(':')[0]):02d}:{t2.split(':')[1]}"
    m_single = re.match(r"^(\d{1,2}):(\d{2})$", s)
    if m_single:
        return f"{int(m_single.group(1)):02d}:{m_single.group(2)}", ""
    return s[:11], ""


def add_local_event(
    date_iso: str,
    title: str,
    start_time: str = "",
    end_time: str = "",
    description: str = "",
    location: str = "",
    source: str = "local",
    uid: str = None,
) -> int:
    init_db()
    ev_uid = uid or f"nc-{uuid.uuid4().hex}@nc-ui"
    now = time.time()
    try:
        with sqlite3.connect(DB_PATH, timeout=5.0) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO events (uid, date_iso, start_time, end_time, title, location, description, source, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(uid) DO UPDATE SET
                    date_iso=excluded.date_iso,
                    start_time=excluded.start_time,
                    end_time=excluded.end_time,
                    title=excluded.title,
                    location=excluded.location,
                    description=excluded.description,
                    source=excluded.source,
                    updated_at=excluded.updated_at
                """,
                (
                    ev_uid,
                    str(date_iso).strip(),
                    str(start_time or "").strip(),
                    str(end_time or "").strip(),
                    str(title).strip(),
                    str(location or "").strip(),
                    str(description or "").strip(),
                    str(source or "local").strip(),
                    now,
                ),
            )
            conn.commit()
            return int(cur.lastrowid or 0)
    except Exception as e:
        log.error(f"add_local_event error: {e}")
        return 0


def delete_event_by_id(event_id) -> bool:
    init_db()
    s_id = str(event_id)
    try:
        with sqlite3.connect(DB_PATH, timeout=5.0) as conn:
            if s_id.isdigit():
                conn.execute("DELETE FROM events WHERE id = ?", (int(s_id),))
            else:
                conn.execute("DELETE FROM events WHERE uid = ?", (s_id,))
            conn.commit()
        return True
    except Exception as e:
        log.error(f"delete_event_by_id error: {e}")
        return False


def clear_local_events() -> None:
    init_db()
    try:
        with sqlite3.connect(DB_PATH, timeout=5.0) as conn:
            conn.execute("DELETE FROM events WHERE source = 'local'")
            conn.commit()
    except Exception:
        pass


def _query_db_range(start_iso: str, end_iso: str, include_ical: bool = True) -> list[dict]:
    if not is_calendar_initialized() or not os.path.exists(DB_PATH):
        return []
    out = []
    try:
        with sqlite3.connect(DB_PATH, timeout=5.0) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            sql = (
                "SELECT * FROM events WHERE date_iso >= ? AND date_iso <= ? "
                + ("" if include_ical else "AND source = 'local' ")
                + "ORDER BY date_iso ASC, CASE WHEN start_time = '' THEN 0 ELSE 1 END ASC, start_time ASC, id ASC"
            )
            cur.execute(sql, (start_iso, end_iso))
            for r in cur.fetchall():
                out.append({
                    "id": r["id"],
                    "uid": r["uid"],
                    "date": r["date_iso"],
                    "start_time": r["start_time"] or "",
                    "end_time": r["end_time"] or "",
                    "title": r["title"] or "",
                    "location": r["location"] or "",
                    "description": r["description"] or "",
                    "source": r["source"] or "local",
                })
    except Exception as e:
        log.error(f"_query_db_range error: {e}")
    return out


# ── Pure-Python iCal (.ics) Feed Registry & RFC 5545 Sync Engine ──

_WIN_TZ_MAP = {
    "UTC": "UTC",
    "W. Europe Standard Time": "Europe/Berlin",
    "Romance Standard Time": "Europe/Paris",
    "Central European Standard Time": "Europe/Warsaw",
    "GMT Standard Time": "Europe/London",
    "Eastern Standard Time": "America/New_York",
    "Central Standard Time": "America/Chicago",
    "Mountain Standard Time": "America/Denver",
    "Pacific Standard Time": "America/Los_Angeles",
}
_ICS_WD = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}


def get_ical_feeds() -> list[dict]:
    if not os.path.exists(ICAL_FEEDS_FILE):
        return []
    try:
        with open(ICAL_FEEDS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return [d for d in data if isinstance(d, dict) and d.get("url")]
    except Exception:
        pass
    return []


def save_ical_feeds(feeds: list[dict]) -> None:
    os.makedirs(os.path.dirname(ICAL_FEEDS_FILE), exist_ok=True)
    tmp = f"{ICAL_FEEDS_FILE}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(feeds, f, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, ICAL_FEEDS_FILE)


def get_ical_feed_names() -> dict[str, str]:
    return {f"ical:{f['id']}": str(f.get("name") or "iCal") for f in get_ical_feeds() if f.get("id")}


def mask_ical_url(url: str) -> str:
    s = str(url or "").strip()
    s = re.sub(r"(/private-)[^/]+", r"\1••••••", s)
    if len(s) > 46:
        return s[:26] + "\u2026" + s[-17:]
    return s


def get_ical_sync_interval_min() -> int:
    try:
        v = int(STATE.get("calendar", "ical_sync_mins", 15))
        return v if v in (0, 15, 30, 60) else 15
    except Exception:
        return 15


def set_ical_sync_interval_min(mins: int) -> int:
    v = int(mins) if int(mins) in (0, 15, 30, 60) else 15
    STATE.set("calendar", "ical_sync_mins", v)
    return v


def suggest_feed_name(url: str) -> str:
    parsed = urllib.parse.urlparse(str(url or "").strip())
    host = (parsed.netloc or "").lower()
    if "google.com" in host:
        return "Google Calendar"
    if "proton" in host:
        return "Proton Calendar"
    if "live.com" in host or "office365" in host or "outlook" in host:
        return "Outlook"
    if host:
        return host.split(":")[0][:22]
    return "iCal Feed"


def add_ical_feed(url: str, name: str = "") -> tuple[bool, str]:
    raw = str(url or "").strip()
    if raw.lower().startswith("webcal://"):
        raw = "https://" + raw[9:]
    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
        return False, t("cc_calendar.ical_err_invalid_url")
    feeds = get_ical_feeds()
    if any(f.get("url") == raw for f in feeds):
        return False, t("cc_calendar.ical_err_duplicate")
    fid = "f_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]
    feeds.append({
        "id": fid,
        "name": str(name or "").strip() or suggest_feed_name(raw),
        "url": raw,
        "enabled": True,
        "etag": "",
        "last_modified": "",
        "last_sync": 0.0,
        "last_err": "",
        "count": 0,
    })
    save_ical_feeds(feeds)
    set_calendar_mode("ical")
    return True, fid


def remove_ical_feed(feed_id: str) -> None:
    feeds = [f for f in get_ical_feeds() if f.get("id") != feed_id]
    save_ical_feeds(feeds)
    init_db()
    try:
        with sqlite3.connect(DB_PATH, timeout=5.0) as conn:
            conn.execute("DELETE FROM events WHERE source = ?", (f"ical:{feed_id}",))
            conn.commit()
    except Exception:
        pass


def toggle_ical_feed(feed_id: str) -> bool:
    feeds = get_ical_feeds()
    new_en = False
    for f in feeds:
        if f.get("id") == feed_id:
            new_en = not bool(f.get("enabled", True))
            f["enabled"] = new_en
            break
    save_ical_feeds(feeds)
    if not new_en and os.path.exists(DB_PATH):
        try:
            with sqlite3.connect(DB_PATH, timeout=5.0) as conn:
                conn.execute("DELETE FROM events WHERE source = ?", (f"ical:{feed_id}",))
                conn.commit()
        except Exception:
            pass
    return new_en


def _local_zoneinfo():
    tz_name = ""
    if os.path.islink("/etc/localtime"):
        target = os.path.realpath("/etc/localtime")
        if "/zoneinfo/" in target:
            tz_name = target.split("/zoneinfo/", 1)[1]
    if tz_name:
        try:
            return zoneinfo.ZoneInfo(tz_name)
        except Exception:
            pass
    return datetime.datetime.now().astimezone().tzinfo or datetime.timezone.utc


def _resolve_zoneinfo(tzid: str, fallback_tz):
    clean = _WIN_TZ_MAP.get(tzid.strip().strip('"'), tzid.strip().strip('"'))
    try:
        return zoneinfo.ZoneInfo(clean)
    except Exception:
        return fallback_tz


def _parse_ics_prop_dt(prop_part: str, raw_val: str, local_tz):
    val = str(raw_val or "").strip()
    m = re.match(r"^(\d{4})(\d{2})(\d{2})(?:T(\d{2})(\d{2})(\d{2})?(Z)?)?", val)
    if not m:
        return None
    d_orig = datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    p_upper = prop_part.upper()
    is_date = ("VALUE=DATE" in p_upper and "VALUE=DATE-TIME" not in p_upper) or (m.group(4) is None)
    if is_date:
        return d_orig, None, None, True
    hh, mm, ss = int(m.group(4)), int(m.group(5)), int(m.group(6) or 0)
    if m.group(7) == "Z":
        src_tz = datetime.timezone.utc
    else:
        m_tz = re.search(r"TZID=([^;:]+)", prop_part, re.I)
        src_tz = _resolve_zoneinfo(m_tz.group(1), local_tz) if m_tz else local_tz
    return d_orig, datetime.time(hh, mm, ss), src_tz, False


def _parse_ics_duration(raw_dur: str) -> datetime.timedelta:
    s = str(raw_dur or "").strip().upper()
    m = re.match(r"^[+-]?P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$", s)
    if not m:
        return datetime.timedelta(0)
    return datetime.timedelta(
        weeks=int(m.group(1) or 0),
        days=int(m.group(2) or 0),
        hours=int(m.group(3) or 0),
        minutes=int(m.group(4) or 0),
        seconds=int(m.group(5) or 0),
    )


def _expand_rrule_dates(
    rrule_str: str,
    start_d: datetime.date,
    win_start: datetime.date,
    win_end: datetime.date,
    local_tz,
) -> list[datetime.date]:
    parts = {}
    for kv in rrule_str.split(";"):
        if "=" in kv:
            k, v = kv.split("=", 1)
            parts[k.strip().upper()] = v.strip().upper()
    freq = parts.get("FREQ", "")
    if freq not in ("DAILY", "WEEKLY", "MONTHLY", "YEARLY"):
        return [start_d] if win_start <= start_d <= win_end else []

    interval = max(1, int(parts.get("INTERVAL", "1") or 1))
    count_lim = int(parts["COUNT"]) if parts.get("COUNT", "").isdigit() else None
    until_d = win_end
    if "UNTIL" in parts:
        p_u = _parse_ics_prop_dt("", parts["UNTIL"], local_tz)
        if p_u:
            if p_u[1] is not None and p_u[2] is not None:
                until_d = datetime.datetime.combine(p_u[0], p_u[1], tzinfo=p_u[2]).astimezone(local_tz).date()
            else:
                until_d = p_u[0]
    eff_end = min(win_end, until_d)
    if start_d > eff_end:
        return []

    byday = []
    for tok in parts.get("BYDAY", "").split(","):
        tok = tok.strip()
        m_bd = re.match(r"^([+-]?\d+)?([A-Z]{2})$", tok)
        if m_bd and m_bd.group(2) in _ICS_WD:
            byday.append((int(m_bd.group(1)) if m_bd.group(1) else None, _ICS_WD[m_bd.group(2)]))
    bymonthday = [int(x) for x in parts.get("BYMONTHDAY", "").split(",") if re.match(r"^[+-]?\d+$", x.strip())]
    bymonth = [int(x) for x in parts.get("BYMONTH", "").split(",") if x.strip().isdigit() and 1 <= int(x.strip()) <= 12]

    hits = []
    max_total = min(count_lim or 500, 500)

    def _push(cand: datetime.date) -> bool:
        if cand < start_d:
            return True
        if cand > eff_end:
            return False
        hits.append(cand)
        return len(hits) < max_total

    def _month_candidates(y: int, mo: int) -> list[datetime.date]:
        n_days = calendar.monthrange(y, mo)[1]
        cands = []
        if byday:
            for ord_v, wd in byday:
                wds = [datetime.date(y, mo, day) for day in range(1, n_days + 1) if datetime.date(y, mo, day).weekday() == wd]
                if ord_v is None:
                    cands.extend(wds)
                elif ord_v > 0 and ord_v <= len(wds):
                    cands.append(wds[ord_v - 1])
                elif ord_v < 0 and abs(ord_v) <= len(wds):
                    cands.append(wds[ord_v])
        else:
            for md in (bymonthday or [start_d.day]):
                day = md if md > 0 else (n_days + 1 + md)
                if 1 <= day <= n_days:
                    cands.append(datetime.date(y, mo, day))
        return sorted(set(cands))

    if freq == "DAILY":
        k0 = ((win_start - start_d).days // interval) if (count_lim is None and start_d < win_start) else 0
        cur = start_d + datetime.timedelta(days=max(0, k0) * interval)
        for _ in range(10000):
            if cur > eff_end or len(hits) >= max_total:
                break
            ok_m = (not bymonth or cur.month in bymonth)
            ok_d = (not bymonthday or cur.day in bymonthday)
            ok_w = (not byday or any(cur.weekday() == wd for _, wd in byday))
            if ok_m and ok_d and ok_w and not _push(cur):
                break
            cur += datetime.timedelta(days=interval)
    elif freq == "WEEKLY":
        wds = sorted({wd for _, wd in byday}) if byday else [start_d.weekday()]
        w_mon = start_d - datetime.timedelta(days=start_d.weekday())
        w_cutoff = win_start - datetime.timedelta(days=7)
        k0 = ((w_cutoff - w_mon).days // (interval * 7)) if (count_lim is None and w_mon < w_cutoff) else 0
        for step in range(max(0, k0), max(0, k0) + 4000):
            base = w_mon + datetime.timedelta(days=step * interval * 7)
            if base > eff_end or len(hits) >= max_total:
                break
            for wd in wds:
                if not _push(base + datetime.timedelta(days=wd)):
                    break
            else:
                continue
            break
    elif freq == "MONTHLY":
        m_idx = start_d.year * 12 + (start_d.month - 1)
        w_idx = win_start.year * 12 + (win_start.month - 1)
        k0 = ((w_idx - m_idx) // interval) if (count_lim is None and m_idx < w_idx) else 0
        for step in range(max(0, k0), max(0, k0) + 1200):
            cur_m = m_idx + step * interval
            y, mo = cur_m // 12, (cur_m % 12) + 1
            if datetime.date(y, mo, 1) > eff_end or len(hits) >= max_total:
                break
            if bymonth and mo not in bymonth:
                continue
            for cand in _month_candidates(y, mo):
                if not _push(cand):
                    break
            else:
                continue
            break
    elif freq == "YEARLY":
        k0 = ((win_start.year - start_d.year) // interval) if (count_lim is None and start_d.year < win_start.year) else 0
        for step in range(max(0, k0), max(0, k0) + 250):
            y = start_d.year + step * interval
            if datetime.date(y, 1, 1) > eff_end or len(hits) >= max_total:
                break
            stopped = False
            for mo in sorted(set(bymonth or [start_d.month])):
                for cand in _month_candidates(y, mo):
                    if not _push(cand):
                        stopped = True
                        break
                if stopped:
                    break
            if stopped:
                break

    return [d for d in hits if win_start <= d <= win_end]


def _emit_ics_span(
    out: list[dict],
    source_tag: str,
    raw_uid: str,
    title: str,
    loc: str,
    desc: str,
    start_d: datetime.date,
    start_t,
    src_tz,
    is_all_day: bool,
    span_days: int,
    dur: datetime.timedelta,
    local_tz,
    win_start: datetime.date,
    win_end: datetime.date,
) -> None:
    if is_all_day:
        for off in range(max(1, min(60, span_days))):
            d = start_d + datetime.timedelta(days=off)
            if win_start <= d <= win_end:
                d_iso = d.isoformat()
                row_uid = hashlib.sha1(f"{source_tag}|{raw_uid}|{d_iso}|all".encode("utf-8")).hexdigest()[:28]
                out.append({
                    "uid": row_uid,
                    "date": d_iso,
                    "start_time": "",
                    "end_time": "",
                    "title": title,
                    "location": loc,
                    "description": desc,
                    "source": source_tag,
                })
        return

    dt_start = datetime.datetime.combine(start_d, start_t, tzinfo=src_tz).astimezone(local_tz)
    dt_end = dt_start + max(datetime.timedelta(0), dur)
    d_s, d_e = dt_start.date(), dt_end.date()
    total_days = max(0, min(60, (d_e - d_s).days))
    for off in range(total_days + 1):
        cur_d = d_s + datetime.timedelta(days=off)
        st = dt_start.strftime("%H:%M") if off == 0 else "00:00"
        if off == total_days:
            et = dt_end.strftime("%H:%M") if total_days == 0 or dt_end.time() > datetime.time(0, 0) else ""
            if off > 0 and not et:
                continue
        else:
            et = "23:59"
        if win_start <= cur_d <= win_end:
            d_iso = cur_d.isoformat()
            row_uid = hashlib.sha1(f"{source_tag}|{raw_uid}|{d_iso}|{st}".encode("utf-8")).hexdigest()[:28]
            out.append({
                "uid": row_uid,
                "date": d_iso,
                "start_time": st,
                "end_time": et,
                "title": title,
                "location": loc,
                "description": desc,
                "source": source_tag,
            })


def parse_ics_content(
    raw_text: str,
    source_tag: str = "local",
    window_start: datetime.date = None,
    window_end: datetime.date = None,
) -> list[dict]:
    today = datetime.date.today()
    if window_start is None:
        window_start = today - datetime.timedelta(days=3650 if source_tag == "local" else 90)
    if window_end is None:
        window_end = today + datetime.timedelta(days=3650 if source_tag == "local" else 365)

    local_tz = _local_zoneinfo()
    unfolded = []
    for line in str(raw_text or "").splitlines():
        if line.startswith((" ", "\t")) and unfolded:
            unfolded[-1] += line[1:]
        else:
            unfolded.append(line)

    raw_events = []
    in_ev, in_alarm, cur = False, False, {}
    for line in unfolded:
        su = line.strip().upper()
        if su == "BEGIN:VEVENT":
            in_ev, in_alarm, cur = True, False, {"exdates": []}
            continue
        if su == "END:VEVENT":
            if in_ev and cur.get("dtstart"):
                raw_events.append(cur)
            in_ev, in_alarm, cur = False, False, {}
            continue
        if not in_ev:
            continue
        if su == "BEGIN:VALARM":
            in_alarm = True
            continue
        if su == "END:VALARM":
            in_alarm = False
            continue
        if in_alarm or ":" not in line:
            continue

        prop_part, val_part = line.split(":", 1)
        pname = prop_part.split(";", 1)[0].strip().upper()
        if pname == "UID":
            cur["uid"] = val_part.strip()
        elif pname == "SUMMARY":
            cur["title"] = _ics_unescape(val_part).strip()
        elif pname == "DESCRIPTION":
            cur["description"] = _ics_unescape(val_part).strip()
        elif pname == "LOCATION":
            cur["location"] = _ics_unescape(val_part).strip()
        elif pname == "STATUS":
            cur["status"] = val_part.strip().upper()
        elif pname == "RRULE":
            cur["rrule"] = val_part.strip()
        elif pname == "DURATION":
            cur["duration"] = val_part.strip()
        elif pname == "DTSTART":
            parsed = _parse_ics_prop_dt(prop_part, val_part, local_tz)
            if parsed:
                cur["dtstart"] = parsed
        elif pname == "DTEND":
            parsed = _parse_ics_prop_dt(prop_part, val_part, local_tz)
            if parsed:
                cur["dtend"] = parsed
        elif pname == "RECURRENCE-ID":
            parsed = _parse_ics_prop_dt(prop_part, val_part, local_tz)
            if parsed:
                cur["rec_id"] = parsed
        elif pname == "EXDATE":
            for tok in val_part.split(","):
                parsed = _parse_ics_prop_dt(prop_part, tok, local_tz)
                if parsed:
                    cur["exdates"].append(parsed)

    def _occ_key(dt_tuple) -> str:
        d0, t0, tz0, is_ad = dt_tuple
        if is_ad or t0 is None:
            return f"D:{d0.isoformat()}"
        return f"T:{int(datetime.datetime.combine(d0, t0, tzinfo=tz0).timestamp())}"

    overrides_by_uid = {}
    masters = []
    for ev in raw_events:
        if "rec_id" in ev:
            uid_s = ev.get("uid") or ""
            overrides_by_uid.setdefault(uid_s, set()).add(_occ_key(ev["rec_id"]))
            overrides_by_uid[uid_s].add(f"D:{ev['rec_id'][0].isoformat()}")
            if ev.get("status") != "CANCELLED":
                masters.append(ev)
        elif ev.get("status") != "CANCELLED":
            masters.append(ev)

    out = []
    for ev in masters:
        title = ev.get("title") or ""
        if not title:
            continue
        start_d, start_t, src_tz, is_ad = ev["dtstart"]
        raw_uid = ev.get("uid") or f"{start_d.isoformat()}|{title}"
        loc = ev.get("location", "")
        desc = ev.get("description", "")

        span_days, dur = 1, datetime.timedelta(0)
        if "dtend" in ev:
            ed, et, etz, e_ad = ev["dtend"]
            if is_ad:
                span_days = max(1, (ed - start_d).days)
            elif not e_ad and et is not None:
                dt_s = datetime.datetime.combine(start_d, start_t, tzinfo=src_tz)
                dt_e = datetime.datetime.combine(ed, et, tzinfo=etz)
                dur = max(datetime.timedelta(0), dt_e - dt_s)
        elif "duration" in ev:
            td = _parse_ics_duration(ev["duration"])
            if is_ad:
                span_days = max(1, td.days or 1)
            else:
                dur = max(datetime.timedelta(0), td)

        if "rrule" in ev and "rec_id" not in ev:
            ex_keys = set(overrides_by_uid.get(raw_uid, set()))
            for ex in ev.get("exdates", []):
                ex_keys.add(_occ_key(ex))
                if not ex[3] and ex[1] is not None:
                    ex_local = datetime.datetime.combine(ex[0], ex[1], tzinfo=ex[2]).astimezone(local_tz).date()
                    ex_keys.add(f"L:{ex_local.isoformat()}")
            occ_dates = _expand_rrule_dates(ev["rrule"], start_d, window_start, window_end, local_tz)
            for occ_d in occ_dates:
                k_d = f"D:{occ_d.isoformat()}"
                k_t = _occ_key((occ_d, start_t, src_tz, is_ad))
                if k_d in ex_keys or k_t in ex_keys:
                    continue
                if not is_ad and start_t is not None:
                    loc_d = datetime.datetime.combine(occ_d, start_t, tzinfo=src_tz).astimezone(local_tz).date()
                    if f"L:{loc_d.isoformat()}" in ex_keys:
                        continue
                _emit_ics_span(out, source_tag, raw_uid, title, loc, desc, occ_d, start_t, src_tz, is_ad, span_days, dur, local_tz, window_start, window_end)
        else:
            _emit_ics_span(out, source_tag, raw_uid, title, loc, desc, start_d, start_t, src_tz, is_ad, span_days, dur, local_tz, window_start, window_end)

    return out


def sync_all_ical_feeds_blocking(force: bool = False) -> bool:
    feeds = get_ical_feeds()
    enabled_feeds = [f for f in feeds if f.get("enabled", True)]
    if not enabled_feeds:
        return False
    interval_min = get_ical_sync_interval_min()
    now = time.time()
    if not force:
        if interval_min <= 0:
            return False
        last_sync = float(STATE.get("calendar", "last_sync_ts", 0.0) or 0.0)
        if now - last_sync < (interval_min * 60):
            return False

    if not _ICAL_SYNC_LOCK.acquire(blocking=False):
        return False
    lock_fh = None
    try:
        try:
            lock_fh = open(_ICAL_LOCK_FILE, "w")
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False

        init_db()
        ssl_ctx = ssl.create_default_context()
        changed = False
        today = datetime.date.today()
        w_start = today - datetime.timedelta(days=90)
        w_end = today + datetime.timedelta(days=365)

        for feed in feeds:
            if not feed.get("enabled", True):
                continue
            headers = {"User-Agent": "NC-UI-Calendar/2.0"}
            if not force:
                if feed.get("etag"):
                    headers["If-None-Match"] = str(feed["etag"])
                if feed.get("last_modified"):
                    headers["If-Modified-Since"] = str(feed["last_modified"])
            req = urllib.request.Request(feed["url"], headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=10.0, context=ssl_ctx) as resp:
                    raw_bytes = resp.read(8 * 1024 * 1024 + 1)
                    if len(raw_bytes) > 8 * 1024 * 1024:
                        feed["last_err"] = "Feed > 8 MiB"
                        continue
                    text = raw_bytes.decode("utf-8", errors="replace")
                    if "BEGIN:VCALENDAR" not in text:
                        feed["last_err"] = "Not a valid .ics calendar"
                        continue
                    src_tag = f"ical:{feed['id']}"
                    parsed = parse_ics_content(text, source_tag=src_tag, window_start=w_start, window_end=w_end)
                    ts_now = time.time()
                    with sqlite3.connect(DB_PATH, timeout=5.0) as conn:
                        conn.execute("DELETE FROM events WHERE source = ?", (src_tag,))
                        conn.executemany(
                            """
                            INSERT OR REPLACE INTO events
                            (uid, date_iso, start_time, end_time, title, location, description, source, updated_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            [
                                (
                                    ev["uid"], ev["date"], ev["start_time"], ev["end_time"],
                                    ev["title"], ev["location"], ev["description"], src_tag, ts_now,
                                )
                                for ev in parsed
                            ],
                        )
                        conn.commit()
                    feed["count"] = len(parsed)
                    feed["etag"] = resp.headers.get("ETag") or ""
                    feed["last_modified"] = resp.headers.get("Last-Modified") or ""
                    feed["last_sync"] = ts_now
                    feed["last_err"] = ""
                    changed = True
            except urllib.error.HTTPError as he:
                if he.code == 304:
                    feed["last_sync"] = time.time()
                    feed["last_err"] = ""
                else:
                    feed["last_err"] = f"HTTP {he.code}"
            except Exception as e:
                feed["last_err"] = str(e)[:60]

        save_ical_feeds(feeds)
        STATE.set("calendar", "last_sync_ts", time.time())
        return changed
    finally:
        if lock_fh is not None:
            try:
                fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)
                lock_fh.close()
            except Exception:
                pass
        _ICAL_SYNC_LOCK.release()


def fetch_calendar_range(start_iso: str, end_iso: str, force_sync: bool = False, on_complete=None) -> tuple[list, bool]:
    mode = get_calendar_mode()
    feeds = get_ical_feeds()
    has_feeds = any(f.get("enabled", True) for f in feeds)

    events = _query_db_range(start_iso, end_iso, include_ical=(mode == "ical"))
    if mode == "ical" and has_feeds:
        interval_min = get_ical_sync_interval_min()
        last_sync = float(STATE.get("calendar", "last_sync_ts", 0.0) or 0.0)
        due = force_sync or (interval_min > 0 and (time.time() - last_sync >= interval_min * 60))
        if due:
            probe.run_async(
                lambda: sync_all_ical_feeds_blocking(force=force_sync),
                on_done=lambda changed: on_complete() if (on_complete and (changed or force_sync)) else None,
            )
    return events, has_feeds


def fetch_calendar_events(year: int, month: int, force_sync: bool = False, on_complete=None) -> tuple[list, bool]:
    last_day = calendar.monthrange(year, month)[1]
    return fetch_calendar_range(
        f"{year:04d}-{month:02d}-01",
        f"{year:04d}-{month:02d}-{last_day:02d}",
        force_sync=force_sync,
        on_complete=on_complete,
    )


def export_db_file(dest_path: str) -> tuple[bool, str]:
    init_db()
    target = os.path.expanduser(str(dest_path or "").strip())
    if not target:
        return False, "Invalid destination path"
    try:
        os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
        with sqlite3.connect(DB_PATH, timeout=5.0) as src_conn:
            with sqlite3.connect(target, timeout=5.0) as dst_conn:
                src_conn.backup(dst_conn)
        return True, f"Exported SQLite DB to {target}"
    except Exception as e:
        return False, f"DB export failed: {e}"


def import_db_file(src_path: str) -> tuple[bool, str]:
    init_db()
    source = os.path.expanduser(str(src_path or "").strip())
    if not source or not os.path.exists(source):
        return False, f"File not found: {source}"
    try:
        count = 0
        with sqlite3.connect(source, timeout=5.0) as src_conn:
            src_conn.row_factory = sqlite3.Row
            cur = src_conn.cursor()
            cur.execute("SELECT * FROM events")
            rows = cur.fetchall()
        for r in rows:
            keys = r.keys()
            add_local_event(
                date_iso=r["date_iso"],
                title=r["title"],
                start_time=r["start_time"] if "start_time" in keys else "",
                end_time=r["end_time"] if "end_time" in keys else "",
                description=r["description"] if "description" in keys else "",
                location=r["location"] if "location" in keys else "",
                source=r["source"] if "source" in keys else "local",
                uid=r["uid"] if ("uid" in keys and r["uid"]) else None,
            )
            count += 1
        set_calendar_initialized(True)
        return True, f"Imported {count} events from {os.path.basename(source)}"
    except Exception as e:
        return False, f"DB import failed: {e}"


def _ics_escape(text: str) -> str:
    return (
        str(text or "")
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
    )


def _ics_unescape(text: str) -> str:
    return (
        str(text or "")
        .replace("\\n", "\n")
        .replace("\\N", "\n")
        .replace("\\,", ",")
        .replace("\\;", ";")
        .replace("\\\\", "\\")
    )


def export_ics_file(dest_path: str) -> tuple[bool, str]:
    init_db()
    target = os.path.expanduser(str(dest_path or "").strip())
    if not target:
        return False, "Invalid destination path"
    try:
        os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
        with sqlite3.connect(DB_PATH, timeout=5.0) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("SELECT * FROM events ORDER BY date_iso ASC, start_time ASC")
            rows = cur.fetchall()

        now_utc = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        lines = [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//NC-UI//Niri Wayland Calendar//EN",
            "CALSCALE:GREGORIAN",
        ]
        for r in rows:
            uid = r["uid"] or f"nc-{r['id']}@nc-ui"
            d_clean = str(r["date_iso"]).replace("-", "")
            st = str(r["start_time"] or "").strip()
            et = str(r["end_time"] or "").strip()
            lines.append("BEGIN:VEVENT")
            lines.append(f"UID:{uid}")
            lines.append(f"DTSTAMP:{now_utc}")
            if st and re.match(r"^\d{2}:\d{2}$", st):
                st_clean = st.replace(":", "") + "00"
                lines.append(f"DTSTART:{d_clean}T{st_clean}")
                if et and re.match(r"^\d{2}:\d{2}$", et):
                    et_clean = et.replace(":", "") + "00"
                    lines.append(f"DTEND:{d_clean}T{et_clean}")
            else:
                lines.append(f"DTSTART;VALUE=DATE:{d_clean}")
            lines.append(f"SUMMARY:{_ics_escape(r['title'])}")
            if r["location"]:
                lines.append(f"LOCATION:{_ics_escape(r['location'])}")
            if r["description"]:
                lines.append(f"DESCRIPTION:{_ics_escape(r['description'])}")
            lines.append("END:VEVENT")
        lines.append("END:VCALENDAR")

        with open(target, "w", encoding="utf-8", newline="\r\n") as f:
            f.write("\r\n".join(lines) + "\r\n")
        return True, f"Exported {len(rows)} events to {target}"
    except Exception as e:
        return False, f"ICS export failed: {e}"


def import_ics_file(src_path: str) -> tuple[bool, str]:
    init_db()
    source = os.path.expanduser(str(src_path or "").strip())
    if not source or not os.path.exists(source):
        return False, f"File not found: {source}"
    try:
        with open(source, "r", encoding="utf-8", errors="replace") as f:
            raw_text = f.read()
        parsed = parse_ics_content(raw_text, source_tag="local")
        now = time.time()
        with sqlite3.connect(DB_PATH, timeout=5.0) as conn:
            conn.executemany(
                """
                INSERT INTO events (uid, date_iso, start_time, end_time, title, location, description, source, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'local', ?)
                ON CONFLICT(uid) DO UPDATE SET
                    date_iso=excluded.date_iso,
                    start_time=excluded.start_time,
                    end_time=excluded.end_time,
                    title=excluded.title,
                    location=excluded.location,
                    description=excluded.description,
                    source='local',
                    updated_at=excluded.updated_at
                """,
                [
                    (
                        ev["uid"], ev["date"], ev["start_time"], ev["end_time"],
                        ev["title"], ev["location"], ev["description"], now,
                    )
                    for ev in parsed
                ],
            )
            conn.commit()
        set_calendar_initialized(True)
        return True, f"Imported {len(parsed)} events from {os.path.basename(source)}"
    except Exception as e:
        return False, f"ICS import failed: {e}"


def handle_cc_calendar_link(cc_panel, uri: str) -> bool:
    if not uri.startswith("cc_cal:"):
        return False
    act = uri[len("cc_cal:"):]

    if act.startswith("mode:"):
        m_id = act.split(":", 1)[1]
        set_calendar_mode(m_id)
        STATE.set("calendar", "cc_status_msg", f"Calendar initialized in '{m_id}' mode.")
        if cc_panel.shell:
            cc_panel.shell.refresh_view()
        return True

    if act.startswith("rem_set:"):
        try:
            set_reminder_minutes(int(act.split(":", 1)[1]))
        except Exception:
            pass
        if cc_panel.shell:
            cc_panel.shell.refresh_view()
        return True

    if act == "rem_inline":
        cur = str(get_reminder_minutes())
        start_inline_input(
            entry_id="cc_cal_rem_inline",
            shell=cc_panel.shell,
            trigger_uri=uri,
            initial_text=cur,
            max_len=5,
            validator=lambda s: s.strip().isdigit() and 0 <= int(s.strip()) <= 10080,
            on_commit=lambda s: set_reminder_minutes(int(s.strip())),
        )
        return True

    if act == "rem_test":
        mins = get_reminder_minutes()
        hdr = t("cc_calendar.calendar_reminder")
        body = t("cc_calendar.nc_ui_reminder_test_min_lead_time", p0=mins)
        dispatch_nc_notification(hdr, body)
        return True

    if act == "clear_local":
        clear_local_events()
        if cc_panel.shell:
            cc_panel.shell.refresh_view()
        return True

    if act == "ical_add":
        cc_panel.request_prompt(
            "cc_cal_ical_url",
            t("cc_calendar.ical_url_placeholder"),
            "",
            title=t("cc_calendar.add_ical_title"),
            required=True,
        )
        return True

    if act.startswith("ical_toggle:"):
        fid = act.split(":", 1)[1]
        now_en = toggle_ical_feed(fid)
        if now_en and get_calendar_mode() == "ical":
            probe.run_async(
                lambda: sync_all_ical_feeds_blocking(force=True),
                on_done=lambda _: cc_panel.shell.refresh_view() if cc_panel.shell else None,
            )
        if cc_panel.shell:
            cc_panel.shell.refresh_view()
        return True

    if act.startswith("ical_del:"):
        fid = act.split(":", 1)[1]
        remove_ical_feed(fid)
        if cc_panel.shell:
            cc_panel.shell.refresh_view()
        return True

    if act.startswith("ical_sync_interval:"):
        try:
            set_ical_sync_interval_min(int(act.split(":", 1)[1]))
        except Exception:
            pass
        if cc_panel.shell:
            cc_panel.shell.refresh_view()
        return True

    if act == "sync_now":
        def _do_sync():
            sync_all_ical_feeds_blocking(force=True)
            STATE.set("calendar", "cc_status_msg", t("cc_calendar.ical_sync_done"))
        probe.run_async(
            _do_sync,
            on_done=lambda _: cc_panel.shell.refresh_view() if cc_panel.shell else None,
        )
        return True

    if act == "export_ics":
        cc_panel.request_prompt(
            "cc_cal_exp_ics",
            t("cc_calendar.destination_ics_path"),
            "~/nc-calendar-export.ics",
            title=t("cc_calendar.export_ics"),
            required=True,
            completion=("path_ext", [".ics"]),
        )
        return True

    if act == "export_db":
        cc_panel.request_prompt(
            "cc_cal_exp_db",
            t("cc_calendar.destination_db_path"),
            "~/nc-calendar-backup.db",
            title=t("cc_calendar.export_db"),
            required=True,
            completion=("path_ext", [".db", ".sqlite", ".sqlite3"]),
        )
        return True

    if act == "import_ics":
        cc_panel.request_prompt(
            "cc_cal_imp_ics",
            t("cc_calendar.source_ics_file_path"),
            "~/nc-calendar-export.ics",
            title=t("cc_calendar.import_ics"),
            required=True,
            completion=("path_ext", [".ics"]),
        )
        return True

    if act == "import_db":
        cc_panel.request_prompt(
            "cc_cal_imp_db",
            t("cc_calendar.source_db_file_path"),
            "~/nc-calendar-backup.db",
            title=t("cc_calendar.import_db"),
            required=True,
            completion=("path_ext", [".db", ".sqlite", ".sqlite3"]),
        )
        return True

    return False


def handle_cc_calendar_prompt(cc_panel, action_id: str, text: str) -> bool:
    if not action_id.startswith("cc_cal_"):
        return False
    val = str(text or "").strip()
    if action_id == "cc_cal_rem_mins":
        try:
            set_reminder_minutes(int(val))
        except ValueError:
            pass
    elif action_id == "cc_cal_ical_url":
        norm_url = ("https://" + val[9:]) if val.lower().startswith("webcal://") else val
        parsed = urllib.parse.urlparse(norm_url)
        if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
            STATE.set("calendar", "cc_status_msg", t("cc_calendar.ical_err_invalid_url"))
            return True
        STATE.set("calendar", "_pending_ical_url", norm_url)
        cc_panel.request_prompt(
            "cc_cal_ical_name",
            t("cc_calendar.ical_name_placeholder"),
            suggest_feed_name(norm_url),
            title=t("cc_calendar.ical_name_title"),
            required=False,
        )
    elif action_id == "cc_cal_ical_name":
        pending_url = str(STATE.get("calendar", "_pending_ical_url", "") or "").strip()
        STATE.set("calendar", "_pending_ical_url", "")
        if pending_url:
            ok, msg = add_ical_feed(pending_url, name=val)
            if not ok:
                STATE.set("calendar", "cc_status_msg", msg)
            else:
                STATE.set("calendar", "cc_status_msg", t("cc_calendar.ical_added_syncing"))
                probe.run_async(
                    lambda: sync_all_ical_feeds_blocking(force=True),
                    on_done=lambda _: cc_panel.shell.refresh_view() if cc_panel.shell else None,
                )
    elif action_id == "cc_cal_exp_ics":
        _ok, msg = export_ics_file(val)
        STATE.set("calendar", "cc_status_msg", msg)
    elif action_id == "cc_cal_exp_db":
        _ok, msg = export_db_file(val)
        STATE.set("calendar", "cc_status_msg", msg)
    elif action_id == "cc_cal_imp_ics":
        _ok, msg = import_ics_file(val)
        STATE.set("calendar", "cc_status_msg", msg)
    elif action_id == "cc_cal_imp_db":
        _ok, msg = import_db_file(val)
        STATE.set("calendar", "cc_status_msg", msg)
    return True


def handle_cc_datetime_link(cc_panel, uri: str) -> bool:
    global _MSG, _ERR
    if uri.startswith("cc_cal:"):
        return handle_cc_calendar_link(cc_panel, uri)
    if not uri.startswith("cc_dt:"):
        return False
    _MSG = ""
    _ERR = ""
    parts = uri.split(":", 2)
    cmd = parts[1]
    arg = parts[2] if len(parts) > 2 else ""

    if cmd == "prompt_tz" and cc_panel:
        cc_panel.request_prompt("cc_dt_tz", t("cc_datetime.enter_timezone_e_g_europe_rome"), _probe_timedate()["tz"])
        return True
    if cmd == "tz_search_open" and cc_panel:
        cur_q = str(STATE.get("datetime", "tz_query", "") or "")
        def _commit_raw_tz(txt: str) -> None:
            global _MSG, _ERR
            val = str(txt or "").strip()
            STATE.set("datetime", "tz_query", "")
            if not val:
                return
            ranked = rank_candidates(catalog_timezones(), val, limit=1)
            tz = ranked[0][0] if ranked else val
            ok, _, err = probe.run_cmd_full(["timedatectl", "set-timezone", tz], timeout=3.0)
            _MSG = f"Timezone set to {tz}" if ok else ""
            _ERR = "" if ok else (err or f"Invalid or denied timezone: {tz}")
        start_inline_input(
            entry_id="cc_dt_tz_picker",
            shell=cc_panel.shell,
            trigger_uri=uri,
            initial_text=cur_q,
            max_len=40,
            is_picker=True,
            on_change=lambda txt: STATE.set("datetime", "tz_query", str(txt or "")),
            on_cancel=lambda: STATE.set("datetime", "tz_query", ""),
            on_commit=_commit_raw_tz,
        )
        return True
    if cmd == "tz_search_clear":
        STATE.set("datetime", "tz_query", "")
    elif cmd == "sync_now":
        probe.open_terminal(["bash", "-c", "sudo systemctl restart systemd-timesyncd && timedatectl status; echo; read -p 'Press Enter to close...'"])
        _MSG = t("cc_datetime.triggered_systemd_timesyncd_resync")
    elif cmd == "ntp" and arg in ("true", "false"):
        ok, _, err = probe.run_cmd_full(["timedatectl", "set-ntp", arg], timeout=3.0)
        _MSG = f"timedatectl set-ntp {arg}" if ok else ""
        _ERR = "" if ok else (err or "timedatectl set-ntp failed (try sudo in terminal)")
    elif cmd == "rtc" and arg in ("0", "1"):
        ok, _, err = probe.run_cmd_full(["timedatectl", "set-local-rtc", arg], timeout=3.0)
        _MSG = f"Set LocalRTC={arg}" if ok else ""
        _ERR = "" if ok else (err or "timedatectl set-local-rtc failed")
    elif cmd == "ntp_term":
        probe.open_terminal(["bash", "-c", "${EDITOR:-nano} /etc/systemd/timesyncd.conf"])
        _MSG = t("cc_datetime.opened_timesyncd_conf_in_editor")
    elif cmd == "set_tz" and arg:
        ok, _, err = probe.run_cmd_full(["timedatectl", "set-timezone", arg], timeout=3.0)
        _MSG = f"Timezone set to {arg}" if ok else ""
        _ERR = "" if ok else (err or f"Failed to set timezone {arg}")
        STATE.set("datetime", "tz_query", "")
    elif cmd == "lang" and arg:
        ok, _, err = probe.run_cmd_full(["localectl", "set-locale", f"LANG={arg}"], timeout=3.0)
        _MSG = f"Set LANG={arg}" if ok else ""
        _ERR = "" if ok else (err or "localectl set-locale failed")
    elif cmd == "lctime" and arg:
        ok, _, err = probe.run_cmd_full(["localectl", "set-locale", f"LC_TIME={arg}"], timeout=3.0)
        _MSG = f"Set LC_TIME={arg}" if ok else ""
        _ERR = "" if ok else (err or "localectl set-locale failed")
    elif cmd == "barfmt" and arg.isdigit():
        idx = int(arg)
        if 0 <= idx < len(_CLOCK_FMTS):
            new_fmt = _CLOCK_FMTS[idx][1]
            wb_cfg = os.path.expanduser("~/.config/waybar/config.jsonc")
            if os.path.exists(wb_cfg):
                raw = open(wb_cfg, "r", encoding="utf-8", errors="ignore").read()
                new_raw, n = re.subn(r'("clock"\s*:\s*\{[^}]*?"format"\s*:\s*")([^"]+)(")', rf"\g<1>{new_fmt}\g<3>", raw, count=1, flags=re.DOTALL)
                if n:
                    open(wb_cfg, "w", encoding="utf-8").write(new_raw)
                    probe.spawn_bg(["pkill", "-SIGUSR2", "waybar"])
                    _MSG = t("cc_datetime.waybar_clock_format_set_to", p0=new_fmt)
                else:
                    _ERR = t("cc_datetime.could_not_locate_clock_format_in_w")
            else:
                _ERR = t("cc_datetime.waybar_config_jsonc_not_found")
    else:
        return handle_cc_calendar_link(cc_panel, uri)

    if cc_panel and cc_panel.shell:
        cc_panel.shell.refresh_view()
    return True


def handle_cc_datetime_prompt_change(cc_panel, action_id: str, text: str) -> bool:
    if action_id == "cc_dt_tz_search":
        STATE.set("datetime", "tz_query", str(text or ""))
        return True
    return False


def handle_cc_datetime_prompt(cc_panel, action_id: str, text: str) -> bool:
    global _MSG, _ERR
    if action_id == "cc_dt_tz_search":
        val = text.strip()
        if val:
            ql = val.lower()
            all_tzs = _load_all_timezones()
            exact = next((z for z in all_tzs if z.lower() == ql), None)
            if exact:
                tz = exact
            else:
                matches = [z for z in all_tzs if ql in z.lower()]
                matches.sort(key=lambda z: (0 if z.split("/")[-1].lower().startswith(ql) else 1, len(z), z))
                tz = matches[0] if matches else val
            ok, _, err = probe.run_cmd_full(["timedatectl", "set-timezone", tz], timeout=3.0)
            _MSG = f"Timezone set to {tz}" if ok else ""
            _ERR = "" if ok else (err or f"Invalid or denied timezone: {tz}")
        STATE.set("datetime", "tz_query", "")
        if cc_panel and cc_panel.shell:
            cc_panel.shell.refresh_view()
        return True
    if action_id != "cc_dt_tz" or not text.strip():
        return handle_cc_calendar_prompt(cc_panel, action_id, text)
    tz = text.strip()
    ok, _, err = probe.run_cmd_full(["timedatectl", "set-timezone", tz], timeout=3.0)
    _MSG = f"Timezone set to {tz}" if ok else ""
    _ERR = "" if ok else (err or f"Invalid or denied timezone: {tz}")
    if cc_panel and cc_panel.shell:
        cc_panel.shell.refresh_view()
    return True
