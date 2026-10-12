"""Screen time by duration (spec §6, F8, P7). A stretch's elapsed seconds are split across the local hours and days
it spans. The split steps through UTC with each instant's own fixed offset, so time across a daylight-saving change
is counted exactly once."""
import re
import sqlite3
from datetime import timedelta, timezone

from . import screen


def split(started, ended, tz=None):
    tz = tz if tz is not None else screen.LOCAL_TZ
    a, b, out = screen.parse(started), screen.parse(ended), []
    while a < b:
        la = a.astimezone(tz) if tz is not None else a.astimezone()
        fixed = la.astimezone(timezone(la.utcoffset()))
        boundary = (fixed.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)).astimezone(timezone.utc)
        nxt = min(b, boundary)
        out.append((la.date().isoformat(), la.hour, (nxt - a).total_seconds()))
        a = nxt
    return out


def has_column(db, table, column):
    try:
        return any(r[1] == column for r in db.execute(f"PRAGMA table_info({table})"))
    except sqlite3.Error:
        return False


def pieces_events_sql(db):
    return " AND source='pieces'" if has_column(db, "events", "source") else ""


def stretches(db):
    try:
        return db.execute("SELECT started, ended, app, title FROM screen_stretches").fetchall()
    except sqlite3.OperationalError:
        return []


def app_name(exe):
    return re.sub(r"\.exe$", "", exe, flags=re.I)


def fmt(seconds):
    s = int(seconds)
    return f"{s // 3600}h {s % 3600 // 60}m" if s >= 3600 else f"{s // 60}m"
