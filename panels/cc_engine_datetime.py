#!/usr/bin/env python3
import datetime
import hashlib
import json
import os
import re
import shutil
import sqlite3
import time
import uuid
from core.config import CONFIG_DIR
from core.state import STATE
from core.i18n import get_active_language, t
from core import probe, log
from ui.canvas import Canvas
from ui.kit import Box, Badge, Txt, SelectPill, ActionBtn, StateBadge

DB_PATH = os.path.join(CONFIG_DIR, "calendar.db")
CLOUD_CACHE_DIR = os.path.expanduser("~/.cache/nc-ui")
REMINDER_STATE_FILE = os.path.join(CLOUD_CACHE_DIR, "calendar_reminded.json")
NOTIF_HIST_FILE = os.path.expanduser("~/.cache/nc_notifications.json")


# -*- coding: utf-8 -*-
"""NC-UI Control Center — Date, Time & Region (`panels/cc_datetime.py`) [Single Backend / Dual View]"""

import os
import re
import shlex
import time
import zoneinfo
from core import probe
from core.completion import catalog_timezones, rank_candidates
from core.i18n import get_active_language, t
from ui.canvas import Canvas
from ui.kit import ActionBtn, Badge, Box, FeedbackBox, SelectPill, StateBadge, Txt, start_inline_input

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


def init_db(db_path: str = DB_PATH) -> bool:
    try:
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        with sqlite3.connect(db_path, timeout=5.0) as conn:
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
    m = str(STATE.get("calendar", "mode", "local")).strip()
    return m if m in ("local", "google_local", "google_cloud") else "local"


def set_calendar_mode(mode: str) -> None:
    if mode not in ("local", "google_local", "google_cloud"):
        return
    init_db()
    STATE.set("calendar", "mode", mode)
    STATE.set("calendar", "initialized", True)
    if mode == "google_cloud":
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
    stats = {"total": 0, "local": 0, "google": 0, "size_kb": 0.0, "path": DB_PATH}
    if not is_calendar_initialized():
        # Se esiste un file DB vuoto creato prima dell'inizializzazione, lo rimuove
        if os.path.exists(DB_PATH):
            try:
                with sqlite3.connect(DB_PATH, timeout=2.0) as conn:
                    cur = conn.cursor()
                    cur.execute("SELECT COUNT(*) FROM events")
                    cnt = int(cur.fetchone()[0] or 0)
                if cnt == 0:
                    os.remove(DB_PATH)
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
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) FROM events")
                stats["total"] = int(cur.fetchone()[0] or 0)
                cur.execute("SELECT COUNT(*) FROM events WHERE source != 'google'")
                stats["local"] = int(cur.fetchone()[0] or 0)
                cur.execute("SELECT COUNT(*) FROM events WHERE source = 'google'")
                stats["google"] = int(cur.fetchone()[0] or 0)
        except Exception:
            pass
    return stats


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
            conn.execute("DELETE FROM events WHERE source != 'google'")
            conn.commit()
    except Exception:
        pass


def _query_db_month(year: int, month: int, include_google: bool = True) -> list[dict]:
    if not is_calendar_initialized() or not os.path.exists(DB_PATH):
        return []
    prefix = f"{year:04d}-{month:02d}-%"
    out = []
    try:
        with sqlite3.connect(DB_PATH, timeout=5.0) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            if include_google:
                cur.execute(
                    "SELECT * FROM events WHERE date_iso LIKE ? ORDER BY date_iso ASC, start_time ASC, id ASC",
                    (prefix,),
                )
            else:
                cur.execute(
                    "SELECT * FROM events WHERE date_iso LIKE ? AND source != 'google' ORDER BY date_iso ASC, start_time ASC, id ASC",
                    (prefix,),
                )
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
        log.error(f"_query_db_month error: {e}")
    return out


def get_google_oauth_paths() -> list[str]:
    return [
        os.path.expanduser(p)
        for p in ("~/.gcalcli_oauth", "~/.config/gcalcli", "~/.local/share/gcalcli")
    ]


def is_google_authenticated() -> bool:
    return any(os.path.exists(p) for p in get_google_oauth_paths())


def reset_google_oauth() -> None:
    for p in get_google_oauth_paths():
        if os.path.exists(p):
            try:
                if os.path.isfile(p):
                    os.remove(p)
            except Exception:
                pass


def launch_google_oauth_init() -> None:
    cid = str(STATE.get("calendar", "gcal_client_id", "") or "").strip()
    csec = str(STATE.get("calendar", "gcal_client_secret", "") or "").strip()
    cmd = ["gcalcli"]
    if cid:
        cmd += ["--client-id", cid]
    if csec:
        cmd += ["--client-secret", csec]
    cmd.append("init")
    probe.open_terminal(cmd)


def _sync_gcalcli_month_into_db(year: int, month: int) -> list[dict]:
    next_y, next_m = (year + 1, 1) if month == 12 else (year, month + 1)
    start_d = f"{year:04d}-{month:02d}-01"
    end_d = f"{next_y:04d}-{next_m:02d}-01"
    cmd = ["gcalcli", "--nocolor", "agenda", "--tsv", "--details", "location", "--details", "description", start_d, end_d]
    ok, out = probe.run_cmd(cmd, timeout=12.0)
    if not ok:
        return None

    fetched = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 5:
            continue
        s_date, s_time, _e_date, e_time, title = parts[:5]
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", s_date.strip()):
            continue
        loc = parts[5] if len(parts) > 5 else ""
        desc = parts[6] if len(parts) > 6 else ""
        raw_key = f"{s_date}|{s_time}|{e_time}|{title}"
        uid = "gcal-" + hashlib.sha1(raw_key.encode("utf-8")).hexdigest()[:20]
        fetched.append({
            "id": uid,
            "uid": uid,
            "date": s_date.strip(),
            "start_time": s_time.strip(),
            "end_time": e_time.strip(),
            "title": title.strip(),
            "location": loc.strip(),
            "description": desc.strip(),
            "source": "google",
        })

    STATE.set("calendar", "last_sync_ts", time.time())
    mode = get_calendar_mode()
    if mode == "google_local":
        init_db()
        prefix = f"{year:04d}-{month:02d}-%"
        try:
            with sqlite3.connect(DB_PATH, timeout=5.0) as conn:
                conn.execute("DELETE FROM events WHERE source = 'google' AND date_iso LIKE ?", (prefix,))
                now = time.time()
                for ev in fetched:
                    conn.execute(
                        """
                        INSERT INTO events (uid, date_iso, start_time, end_time, title, location, description, source, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, 'google', ?)
                        ON CONFLICT(uid) DO UPDATE SET
                            date_iso=excluded.date_iso,
                            start_time=excluded.start_time,
                            end_time=excluded.end_time,
                            title=excluded.title,
                            location=excluded.location,
                            description=excluded.description,
                            source='google',
                            updated_at=excluded.updated_at
                        """,
                        (ev["uid"], ev["date"], ev["start_time"], ev["end_time"], ev["title"], ev["location"], ev["description"], now),
                    )
                conn.commit()
        except Exception as e:
            log.error(f"Google SQLite sync error: {e}")
    else:
        os.makedirs(CLOUD_CACHE_DIR, exist_ok=True)
        cache_file = os.path.join(CLOUD_CACHE_DIR, f"calendar_cloud_{year}_{month:02d}.json")
        try:
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(fetched, f)
        except Exception:
            pass
    return fetched


def fetch_calendar_events(year: int, month: int, force_sync: bool = False, on_complete=None) -> tuple[list, bool]:
    mode = get_calendar_mode()
    oauth_ok = is_google_authenticated() and probe.has_bin("gcalcli")

    if mode == "local":
        return _query_db_month(year, month, include_google=False), oauth_ok

    if mode == "google_local":
        db_events = _query_db_month(year, month, include_google=True)
        has_gcal_in_month = any(e["source"] == "google" for e in db_events)
        if oauth_ok and (force_sync or not has_gcal_in_month):
            probe.run_async(
                lambda: _sync_gcalcli_month_into_db(year, month),
                on_done=lambda res: on_complete() if (res is not None and on_complete) else None,
            )
        return db_events, oauth_ok

    local_evs = _query_db_month(year, month, include_google=False)
    cache_file = os.path.join(CLOUD_CACHE_DIR, f"calendar_cloud_{year}_{month:02d}.json")
    cloud_evs = []
    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                cloud_evs = json.load(f)
        except Exception:
            pass
    if oauth_ok and (force_sync or not cloud_evs):
        probe.run_async(
            lambda: _sync_gcalcli_month_into_db(year, month),
            on_done=lambda res: on_complete() if (res is not None and on_complete) else None,
        )
    merged = local_evs + cloud_evs
    merged.sort(key=lambda x: (x.get("date", ""), x.get("start_time", "")))
    return merged, oauth_ok


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


def _parse_ics_dt(raw_val: str) -> tuple[str, str]:
    val = str(raw_val or "").strip()
    m = re.search(r"(\d{4})(\d{2})(\d{2})(?:T(\d{2})(\d{2}))?", val)
    if not m:
        return "", ""
    d_iso = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    t_str = f"{m.group(4)}:{m.group(5)}" if (m.group(4) and m.group(5)) else ""
    return d_iso, t_str


def import_ics_file(src_path: str) -> tuple[bool, str]:
    init_db()
    source = os.path.expanduser(str(src_path or "").strip())
    if not source or not os.path.exists(source):
        return False, f"File not found: {source}"
    try:
        with open(source, "r", encoding="utf-8", errors="replace") as f:
            raw_lines = f.read().splitlines()

        unfolded = []
        for line in raw_lines:
            if line.startswith((" ", "\t")) and unfolded:
                unfolded[-1] += line[1:]
            else:
                unfolded.append(line)

        in_event = False
        cur_ev = {}
        imported = 0

        for line in unfolded:
            s_line = line.strip()
            if s_line.upper() == "BEGIN:VEVENT":
                in_event = True
                cur_ev = {}
                continue
            if s_line.upper() == "END:VEVENT":
                in_event = False
                if cur_ev.get("date") and cur_ev.get("title"):
                    add_local_event(
                        date_iso=cur_ev["date"],
                        title=cur_ev["title"],
                        start_time=cur_ev.get("start_time", ""),
                        end_time=cur_ev.get("end_time", ""),
                        description=cur_ev.get("description", ""),
                        location=cur_ev.get("location", ""),
                        source="local",
                        uid=cur_ev.get("uid"),
                    )
                    imported += 1
                cur_ev = {}
                continue
            if not in_event or ":" not in line:
                continue

            prop_part, val_part = line.split(":", 1)
            prop_name = prop_part.split(";", 1)[0].strip().upper()
            if prop_name == "UID":
                cur_ev["uid"] = val_part.strip()
            elif prop_name == "SUMMARY":
                cur_ev["title"] = _ics_unescape(val_part)
            elif prop_name == "DESCRIPTION":
                cur_ev["description"] = _ics_unescape(val_part)
            elif prop_name == "LOCATION":
                cur_ev["location"] = _ics_unescape(val_part)
            elif prop_name == "DTSTART":
                d_iso, t_s = _parse_ics_dt(val_part)
                if d_iso:
                    cur_ev["date"] = d_iso
                    cur_ev["start_time"] = t_s
            elif prop_name == "DTEND":
                _, t_e = _parse_ics_dt(val_part)
                if t_e:
                    cur_ev["end_time"] = t_e

        set_calendar_initialized(True)
        return True, f"Imported {imported} events from {os.path.basename(source)}"
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

    if act == "oauth_login":
        set_calendar_initialized(True)
        if get_calendar_mode() == "local":
            set_calendar_mode("google_local")
        launch_google_oauth_init()
        return True

    if act == "oauth_reset":
        reset_google_oauth()
        if cc_panel.shell:
            cc_panel.shell.refresh_view()
        return True

    if act == "sync_now":
        today = datetime.date.today()
        probe.run_async(
            lambda: _sync_gcalcli_month_into_db(today.year, today.month),
            on_done=lambda _: cc_panel.shell.refresh_view() if cc_panel.shell else None,
        )
        return True

    if act == "prompt_user":
        cur = str(STATE.get("calendar", "gcal_user", "") or "")
        cc_panel.request_prompt("cc_cal_user", t("cc_calendar.google_email_e_g_user_gmail_com"), cur, title=t("cc_calendar.google_account"))
        return True

    if act == "prompt_cid":
        cur = str(STATE.get("calendar", "gcal_client_id", "") or "")
        cc_panel.request_prompt("cc_cal_cid", t("cc_calendar.oauth2_client_id_apps_googleuserco"), cur, title=t("cc_calendar.oauth2_client_id"))
        return True

    if act == "prompt_csec":
        cur = str(STATE.get("calendar", "gcal_client_secret", "") or "")
        cc_panel.request_prompt("cc_cal_csec", t("cc_calendar.oauth2_client_secret"), cur, secret=True, title=t("cc_calendar.oauth2_client_secret_2"))
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
    elif action_id == "cc_cal_user":
        STATE.set("calendar", "gcal_user", val)
    elif action_id == "cc_cal_cid":
        STATE.set("calendar", "gcal_client_id", val)
    elif action_id == "cc_cal_csec":
        STATE.set("calendar", "gcal_client_secret", val)
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
