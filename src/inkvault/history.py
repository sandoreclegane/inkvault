"""Browser history: from the chosen profiles into the vault (browser_visits), and from the vault into search
(visits, pages).

Each sync copies a profile's history file and reads the copy; the browser's own file is never opened by SQLite.
"""
import contextlib
import hashlib
import shutil
import sqlite3
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import NamedTuple
from urllib.parse import urlsplit

from . import browsers, export, urlclean
from .times import local

CHROME_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)
UNIX_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
SIDECARS = ("-journal", "-wal")
RETRY_WAIT = 5  # seconds before the second try at copying a profile


class Visit(NamedTuple):  # one row of browser_visits, in column order
    id: str
    profile: str
    visit_id: int
    redirected: int
    created: str
    url: str
    address: str
    title: str | None
    title_observed_at: str | None
    duration_s: float | None
    transition: int
    origin: str | None
    origin_visit_id: int | None


class SyncResult(NamedTuple):
    outcome: str        # "nothing" (no profile chosen), "ok", "partial" or "failed"
    chosen: int         # profiles chosen
    failed: list        # keys of the chosen profiles that couldn't be read
    waiting: int        # profiles waiting for the user to choose


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def row_id(profile, native_id, raw_time, raw_url):
    """Length-prefixed fields, so two different visits never hash alike by running together."""
    h = hashlib.sha1()
    for part in (profile, str(native_id), str(raw_time), raw_url):
        b = part.encode("utf-8", "surrogatepass")
        h.update(len(b).to_bytes(8, "big") + b)
    return h.hexdigest()


def address_hash(raw_url):
    """Groups pages by the browser's own address without storing it (it can hold what cleaning removed)."""
    return hashlib.sha1(raw_url.encode("utf-8", "surrogatepass")).hexdigest()


def visit(profile, native_id, raw_time, when, raw_url, title, observed, duration, transition, redirected=0,
          origin=None, origin_id=None):
    url = urlclean.clean_url(raw_url)
    if url is None:
        return None  # not http(s): never stored
    title = urlclean.clean_title(title) or None
    return Visit(row_id(profile, native_id, raw_time, raw_url), profile, native_id, redirected, iso(when), url,
                 address_hash(raw_url), title, observed if title else None, duration, transition, origin or None,
                 origin_id if origin else None)


@contextlib.contextmanager
def snapshot(history):
    """A fresh copy of a live history file and its sidecars, opened read-write so SQLite can recover the copy from
    its own journal or WAL. Best effort: a copy taken mid-write can miss the newest visits."""
    with tempfile.TemporaryDirectory(prefix="inkvault-history-") as tmp:
        copy = Path(tmp) / history.name
        shutil.copyfile(history, copy)
        for suffix in SIDECARS:
            side = history.with_name(history.name + suffix)
            if side.exists():
                shutil.copyfile(side, copy.with_name(copy.name + suffix))
        db = sqlite3.connect(copy)
        db.text_factory = lambda b: b.decode("utf-8", "replace")
        try:
            if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise sqlite3.DatabaseError("the copy failed SQLite's quick_check")
            yield db
        finally:
            db.close()


def read_chromium(db, profile, observed):
    cols = {r[1] for r in db.execute("PRAGMA table_info(visits)")}
    origin = "v.originator_cache_guid" if "originator_cache_guid" in cols else "NULL"
    origin_id = "v.originator_visit_id" if "originator_visit_id" in cols else "NULL"
    for vid, vtime, url, title, duration, transition, guid, ovid in db.execute(
            f"SELECT v.id, v.visit_time, u.url, u.title, v.visit_duration, v.transition, {origin}, {origin_id} "
            "FROM visits v JOIN urls u ON u.id = v.url"):
        try:
            when = CHROME_EPOCH + timedelta(microseconds=vtime)
        except (TypeError, OverflowError):
            continue
        v = visit(profile, vid, vtime, when, url or "", title, observed, (duration or 0) / 1e6, transition or 0,
                  origin=guid, origin_id=ovid)
        if v:
            yield v


def read_firefox(db, profile, observed):
    # Redirects are resolved inside this copy only: visit ids can be reused, so no stored visit is ever matched.
    redirected = {f for (f,) in db.execute(
        "SELECT from_visit FROM moz_historyvisits WHERE visit_type IN (5, 6) AND from_visit > 0")}
    for vid, vdate, url, title, vtype in db.execute(
            "SELECT v.id, v.visit_date, p.url, p.title, v.visit_type FROM moz_historyvisits v "
            "JOIN moz_places p ON p.id = v.place_id"):
        try:
            when = UNIX_EPOCH + timedelta(microseconds=vdate)
        except (TypeError, OverflowError):
            continue
        v = visit(profile, vid, vdate, when, url or "", title, observed, None, vtype or 0,
                  redirected=int(vid in redirected))
        if v:
            yield v


def read_profile(profile, observed):
    """All of a profile's visits, read completely from a fresh copy. Tries twice; the second failure is raised."""
    reader = read_chromium if profile.kind == "chromium" else read_firefox
    for attempt in (1, 2):
        try:
            with snapshot(profile.history) as db:
                return list(reader(db, profile.key, observed))
        except (OSError, sqlite3.Error):
            if attempt == 2:
                raise
            time.sleep(RETRY_WAIT)


def skipped(url, sites):
    host = (urlsplit(url).hostname or "").lower()
    return any(host == s or host.endswith("." + s) for s in sites)


UPSERT = ("INSERT INTO browser_visits VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
          "redirected = excluded.redirected, duration_s = excluded.duration_s, transition = excluded.transition, "
          "title = coalesce(excluded.title, title), "
          "title_observed_at = CASE WHEN excluded.title IS NULL THEN title_observed_at "
          "ELSE excluded.title_observed_at END")


def store(db, key, visits, sites):
    """Add new visits; update the ones still in the browser's file (it fills in duration and titles, and can change
    a visit's transition later). Visits gone from the file stay as they were. Returns how many were new."""
    count = lambda: db.execute("SELECT COUNT(*) FROM browser_visits WHERE profile=?", (key,)).fetchone()[0]
    before = count()
    db.executemany(UPSERT, (v for v in visits if not skipped(v.url, sites)))
    return count() - before


def record(db, key, **fields):
    db.execute("INSERT INTO browser_profiles (profile) VALUES (?) ON CONFLICT(profile) DO NOTHING", (key,))
    for name, value in fields.items():
        db.execute(f"UPDATE browser_profiles SET {name}=? WHERE profile=?", (value, key))


def waiting_line(n):
    return f"{n} browser profile{'' if n == 1 else 's'} waiting for you to choose: run `inkvault browsers`."


def sync():
    """Copy the chosen profiles' history into the vault. Never asks: choosing happens before (`inkvault browsers`,
    or the prompt `sync` and `rescue` show in a terminal)."""
    from . import forget  # imported here: forget.py imports this module
    forget.apply_pending()  # a removal stopped part-way goes first: its choices are saved before profiles are read
    profiles = browsers.find_profiles()
    choices = browsers.load_choices()
    states = {p.key: browsers.state(p, choices) for p in profiles}
    chosen = [p for p in profiles if states[p.key][0] == "yes"]
    waiting = [p for p in profiles if states[p.key][0] == "new"]
    for p in waiting:
        if why := states[p.key][1]:
            print(f"{p.key}: {why}; not read until you choose again (`inkvault browsers`).")
    if waiting:
        print(waiting_line(len(waiting)))
    if not chosen:
        print("Browsers: no profile chosen" + ("" if profiles else " (no supported browser found)"))
        return SyncResult("nothing", 0, [], len(waiting))
    db = export.open_vault()
    failed = []
    try:
        observed = now()
        for p in chosen:
            label = f"{browsers.NAMES[p.browser]} {p.name} ({p.key})"
            record(db, p.key, last_attempt=observed)
            db.commit()
            try:
                visits = read_profile(p, observed)
                new = store(db, p.key, visits, choices["skip_sites"])
                record(db, p.key, last_success=observed, last_error=None, visits_in_file=len(visits))
                db.commit()
                print(f"{label}: {len(visits):,} visits in the file, {new:,} new", flush=True)
            except (OSError, sqlite3.Error) as e:
                db.rollback()
                record(db, p.key, last_error=f"{type(e).__name__}: {e}")
                db.commit()
                failed.append(p.key)
                print(f"{label}: couldn't be read ({type(e).__name__}: {e})", flush=True)
        db.execute("INSERT OR REPLACE INTO meta VALUES ('last_browser_sync', ?)", (observed,))
        db.execute("INSERT OR REPLACE INTO meta VALUES ('browser_clean_format', ?)", (urlclean.FORMAT,))
        db.commit()
    finally:
        db.close()
    outcome = "ok" if not failed else "failed" if len(failed) == len(chosen) else "partial"
    return SyncResult(outcome, len(chosen), failed, len(waiting))


# --- From the vault into search (used by index.py) ---

FIREFOX_NOT_PAGES = {4, 7, 8}  # embed, download, framed link
CHAIN_START, CHAIN_END = 0x10000000, 0x20000000

INDEX_TABLES = """
    CREATE TABLE visits (id TEXT PRIMARY KEY, created TEXT, profile TEXT, browser TEXT, host TEXT, page_id TEXT,
                         synced INTEGER);
    CREATE TABLE pages (id TEXT PRIMARY KEY, created TEXT, day TEXT, url TEXT, host TEXT, path TEXT, title TEXT,
                        visits INTEGER, profiles TEXT);
"""


def counts(profile, transition, redirected):
    """Whether a stored visit counts: a recorded top-level navigation (spec §4). Decided here, at index time, so a
    visit the browser later re-labels, or a later change to this rule, needs no new sync."""
    if profile.startswith("firefox/"):
        return transition not in FIREFOX_NOT_PAGES and not redirected
    if (transition & 0xFF) in (3, 4):  # sub-frames
        return False
    return bool(transition & CHAIN_END) or not transition & CHAIN_START  # a chain's end, or no chain at all


def index_into(raw, db):
    """Counted visits, and one page per address per local day, from vault.db (raw) into search.db (db)."""
    db.executescript(INDEX_TABLES)
    try:
        rows = raw.execute("SELECT id, profile, visit_id, created, url, address, title, title_observed_at, "
                           "transition, redirected, origin FROM browser_visits ORDER BY created, id").fetchall()
    except sqlite3.OperationalError:  # a vault from before 0.3.0
        rows = []
    pages, visits = {}, []
    for vid, profile, native, created, url, address, title, observed, transition, redirected, origin in rows:
        if not counts(profile, transition, redirected):
            continue
        try:
            day = local(created).date().isoformat()
        except (TypeError, ValueError):
            continue
        page_id = "web:" + hashlib.sha1(f"{address}\n{day}".encode()).hexdigest()[:16]
        parts = urlsplit(url)
        host = (parts.hostname or "").removeprefix("www.")
        p = pages.setdefault(page_id, {"created": created, "day": day, "url": url, "host": host, "path": parts.path,
                                       "title": None, "rank": None, "visits": 0, "profiles": set()})
        p["visits"] += 1
        p["profiles"].add(profile)
        rank = (observed or "", created, -native)  # last observed, then latest visit, then smallest visit id
        if title and (p["rank"] is None or rank > p["rank"]):
            p["title"], p["rank"] = title, rank
        visits.append((vid, created, profile, profile.split("/", 1)[0], host, page_id, int(bool(origin))))
    db.executemany("INSERT INTO visits VALUES (?,?,?,?,?,?,?)", visits)
    db.executemany("INSERT INTO pages VALUES (?,?,?,?,?,?,?,?,?)", (
        (pid, p["created"], p["day"], p["url"], p["host"], p["path"], p["title"], p["visits"],
         ",".join(sorted(p["profiles"]))) for pid, p in pages.items()))
