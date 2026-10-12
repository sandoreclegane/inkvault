"""Screen capture's shared pieces (screen spec §3, §6): the time format, the one overlap rule, bounds for the MCP
tools (UTC convention) and for `forget` (local input), the settings in screen.json, and the vault markers the
watcher and forget agree on. Windows-only at run time, importable everywhere."""
from datetime import datetime, timedelta, timezone

FMT = "%Y-%m-%dT%H:%M:%S.%fZ"
LOCAL_TZ = None  # None: this computer's local time. Tests set a ZoneInfo.


def iso(dt):
    return dt.astimezone(timezone.utc).strftime(FMT)


def now():
    return datetime.now(timezone.utc)


def parse(ts):
    t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t.astimezone(timezone.utc)


def overlaps(started, ended, since, until):
    """The one overlap rule (spec §6): ended is the last observation, inclusive. FMT strings compare as times."""
    return started < until and ended >= since


def overlap_sql(started="started", ended="ended"):
    """overlaps() as SQL. Parameters: [until, since]."""
    return f" AND {started} < ? AND {ended} >= ?"


def _checked(s, u, since, until):
    if s >= u:
        raise ValueError(f"the range is empty or reversed ({since} → {until or 'now'})")
    return iso(s), iso(u)


def bounds(since, until, now_=None):
    """MCP tools (spec §6): a date-only bound is that UTC day (an until date includes its whole day), a time
    without a zone is UTC, omitted until is now. Raises ValueError for anything unparseable, empty or reversed."""
    def one(text, end):
        if len(text) == 10:
            day = datetime.fromisoformat(text).replace(tzinfo=timezone.utc)
            return day + timedelta(days=1) if end else day
        return parse(text)
    s = one(since, False)
    u = one(until, True) if until else (now_ or now())
    return _checked(s, u, since, until)


def _to_utc(naive, fold, tz):
    if tz is None:
        return naive.replace(fold=fold).astimezone().astimezone(timezone.utc)
    return naive.replace(fold=fold, tzinfo=tz).astimezone(timezone.utc)


def _wall(utc, tz):
    return (utc.astimezone() if tz is None else utc.astimezone(tz)).replace(tzinfo=None, fold=0)


def from_local(naive, later, tz=None):
    """A local wall time -> UTC. The repeated hour widens (earliest for since, latest for until); a skipped one is
    an error (spec §6, P4)."""
    a, b = _to_utc(naive, 0, tz), _to_utc(naive, 1, tz)
    if _wall(a, tz) != naive and _wall(b, tz) != naive:
        raise ValueError(f"{naive:%Y-%m-%d %H:%M} doesn't exist here (the clocks went forward); "
                         "pick a time outside that hour")
    return max(a, b) if later else min(a, b)


def local_bounds(since, until, tz=None, now_=None):
    """`inkvault forget --screen` (spec §6, P4): no zone or a date means local wall time (a date is local
    midnight; an until date includes its whole day); an explicit offset or Z is respected."""
    tz = tz if tz is not None else LOCAL_TZ

    def one(text, end):
        if len(text) == 10:
            day = datetime.fromisoformat(text)
            return from_local(day + timedelta(days=1) if end else day, end, tz)
        t = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return t.astimezone(timezone.utc) if t.tzinfo else from_local(t, end, tz)
    s = one(since, False)
    u = one(until, True) if until else (now_ or now())
    return _checked(s, u, since, until)
