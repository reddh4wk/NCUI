#!/usr/bin/env python3
import datetime
import html
import re
import textwrap
from core.i18n import t_list

DEFAULT_MONTHS = ["", "January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
DEFAULT_MONTHS_SHORT = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
DEFAULT_DAYS_SHORT = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
DEFAULT_DAYS_LONG = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def get_months() -> list[str]:
    m = t_list("calendar.months", DEFAULT_MONTHS)
    return m if len(m) >= 13 else DEFAULT_MONTHS


def get_months_short() -> list[str]:
    m = t_list("calendar.months_short", DEFAULT_MONTHS_SHORT)
    return m if len(m) >= 13 else DEFAULT_MONTHS_SHORT


def get_days_short() -> list[str]:
    d = t_list("calendar.days_short", DEFAULT_DAYS_SHORT)
    return d if len(d) >= 7 else DEFAULT_DAYS_SHORT


def get_days_long() -> list[str]:
    d = t_list("calendar.days_long", DEFAULT_DAYS_LONG)
    return d if len(d) >= 7 else DEFAULT_DAYS_LONG


def format_date_it(iso_str: str) -> str:
    try:
        dt = datetime.date.fromisoformat(iso_str)
        days_s = get_days_short()
        months_s = get_months_short()
        return f"{days_s[dt.weekday()]} {dt.day:02d} {months_s[dt.month]}"
    except Exception:
        return iso_str


def format_speed(bytes_per_sec: float, unit_mode: str = "bytes") -> str:
    if unit_mode == "bits":
        val, div, units = bytes_per_sec * 8.0, 1000.0, ["b/s", "kb/s", "Mb/s", "Gb/s"]
    else:
        val, div, units = float(bytes_per_sec), 1024.0, ["B/s", "kB/s", "MB/s", "GB/s"]
    u_idx = 0
    while val >= div and u_idx < len(units) - 1:
        val /= div
        u_idx += 1
    return f"{val:6.1f} {units[u_idx]:<4}"


def format_bytes_short(num_bytes: float) -> str:
    val = float(num_bytes)
    for unit in ["B", "K", "M", "G", "T"]:
        if val < 1024.0 or unit == "T":
            if unit in ("G", "T"):
                return f"{val:.1f}{unit}"
            return f"{int(round(val))}{unit}"
        val /= 1024.0
    return f"{val:.1f}T"


def wrap_pango_markup(markup: str, max_chars: int = 38) -> list[str]:
    plain = html.unescape(re.sub(r"<[^>]+>", "", str(markup or "")))
    return [html.escape(line, quote=True) for line in textwrap.wrap(plain, width=max(1, max_chars))]
