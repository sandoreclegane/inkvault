"""Tiny browser history files with the real schemas, for tests. Real history is never read."""
import configparser
import contextlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

CHAIN_START, CHAIN_END = 0x10000000, 0x20000000
LINK = CHAIN_START | CHAIN_END  # core type 0 (a link), a one-hop chain: what an ordinary page visit looks like

CHROMIUM_SCHEMA = """
CREATE TABLE IF NOT EXISTS urls (id INTEGER PRIMARY KEY AUTOINCREMENT, url LONGVARCHAR, title LONGVARCHAR,
    visit_count INTEGER DEFAULT 0 NOT NULL, typed_count INTEGER DEFAULT 0 NOT NULL,
    last_visit_time INTEGER DEFAULT 0 NOT NULL, hidden INTEGER DEFAULT 0 NOT NULL);
CREATE TABLE IF NOT EXISTS visits (id INTEGER PRIMARY KEY, url INTEGER NOT NULL, visit_time INTEGER NOT NULL,
    from_visit INTEGER, transition INTEGER DEFAULT 0 NOT NULL, segment_id INTEGER,
    visit_duration INTEGER DEFAULT 0 NOT NULL{extra});
"""
FIREFOX_SCHEMA = """
CREATE TABLE IF NOT EXISTS moz_places (id INTEGER PRIMARY KEY, url LONGVARCHAR, title LONGVARCHAR,
    rev_host LONGVARCHAR, visit_count INTEGER DEFAULT 0, hidden INTEGER DEFAULT 0 NOT NULL,
    typed INTEGER DEFAULT 0 NOT NULL, frecency INTEGER DEFAULT -1 NOT NULL, last_visit_date INTEGER, guid TEXT,
    url_hash INTEGER DEFAULT 0 NOT NULL);
CREATE TABLE IF NOT EXISTS moz_historyvisits (id INTEGER PRIMARY KEY, from_visit INTEGER, place_id INTEGER,
    visit_date INTEGER, visit_type INTEGER, session INTEGER, source INTEGER DEFAULT 0 NOT NULL,
    triggeringPlaceId INTEGER);
"""


def _us(iso, epoch):
    return (datetime.fromisoformat(iso.replace("Z", "+00:00")) - epoch) // timedelta(microseconds=1)


def chrome_time(iso):
    return _us(iso, datetime(1601, 1, 1, tzinfo=timezone.utc))


def unix_time(iso):
    return _us(iso, datetime(1970, 1, 1, tzinfo=timezone.utc))


def install(monkeypatch, *entries):
    """Pretend these (browser, kind, folder) are the browsers on this computer."""
    from inkvault import browsers
    monkeypatch.setattr(browsers, "user_data_dirs", lambda: list(entries))


def execute(path, sql, *params):
    with contextlib.closing(sqlite3.connect(path)) as db:
        db.execute(sql, params)
        db.commit()


def chromium_profile(root, folder, name=None, user_name=None, gaia_id=None, originator=True):
    """A Chromium profile folder with a History file. folder=None is Opera's layout: History in root itself.
    With a name, the profile is listed in root/Local State (replacing any earlier entry for it)."""
    root = Path(root)
    profile = root / folder if folder else root
    profile.mkdir(parents=True, exist_ok=True)
    history = profile / "History"
    extra = (", originator_cache_guid TEXT DEFAULT '' NOT NULL, originator_visit_id INTEGER DEFAULT 0 NOT NULL"
             if originator else "")
    with contextlib.closing(sqlite3.connect(history)) as db:
        db.executescript(CHROMIUM_SCHEMA.format(extra=extra))
    if folder and name is not None:
        state_file = root / "Local State"
        state = json.loads(state_file.read_text(encoding="utf-8")) if state_file.exists() else {}
        entry = {"name": name, **({"user_name": user_name} if user_name else {}), **({"gaia_id": gaia_id} if gaia_id else {})}
        state.setdefault("profile", {}).setdefault("info_cache", {})[folder] = entry
        state_file.write_text(json.dumps(state), encoding="utf-8")
    return history


def chromium_visit(history, vid, url, when, title=None, transition=LINK, duration_s=0.0, guid=""):
    """One visit. A URL seen before keeps its urls row; a title replaces that row's title (the browser keeps only
    the current one)."""
    with contextlib.closing(sqlite3.connect(history)) as db:
        row = db.execute("SELECT id FROM urls WHERE url=?", (url,)).fetchone()
        if row:
            url_id = row[0]
            if title is not None:
                db.execute("UPDATE urls SET title=? WHERE id=?", (title, url_id))
        else:
            url_id = db.execute("INSERT INTO urls (url, title) VALUES (?, ?)", (url, title or "")).lastrowid
        cols = [r[1] for r in db.execute("PRAGMA table_info(visits)")]
        values = [vid, url_id, chrome_time(when), transition, int(duration_s * 1_000_000)]
        if "originator_cache_guid" in cols:
            db.execute("INSERT INTO visits (id, url, visit_time, transition, visit_duration, originator_cache_guid, "
                       "originator_visit_id) VALUES (?,?,?,?,?,?,?)", (*values, guid, vid if guid else 0))
        else:
            db.execute("INSERT INTO visits (id, url, visit_time, transition, visit_duration) VALUES (?,?,?,?,?)",
                       values)
        db.commit()


def firefox_profile(root, folder, name="default-release"):
    """A Firefox profile under root/Profiles, listed in root/profiles.ini, with a WAL-mode places.sqlite."""
    root = Path(root)
    profile = root / "Profiles" / folder
    profile.mkdir(parents=True, exist_ok=True)
    places = profile / "places.sqlite"
    with contextlib.closing(sqlite3.connect(places)) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript(FIREFOX_SCHEMA)
    ini = configparser.ConfigParser(interpolation=None)
    ini.optionxform = str  # Firefox writes Name, Path, IsRelative
    ini.read(root / "profiles.ini", encoding="utf-8")
    n = sum(s.startswith("Profile") for s in ini.sections())
    ini[f"Profile{n}"] = {"Name": name, "IsRelative": "1", "Path": f"Profiles/{folder}"}
    with open(root / "profiles.ini", "w", encoding="utf-8") as f:
        ini.write(f)
    return places


def firefox_visit(places, vid, url, when, title=None, visit_type=1, from_visit=0, db=None):
    """One visit. Pass an open connection (db) to leave the visit in the WAL file, as a running Firefox does."""
    own = db is None
    db = db or sqlite3.connect(places)
    try:
        row = db.execute("SELECT id FROM moz_places WHERE url=?", (url,)).fetchone()
        if row:
            place = row[0]
            if title is not None:
                db.execute("UPDATE moz_places SET title=? WHERE id=?", (title, place))
        else:
            place = db.execute("INSERT INTO moz_places (url, title) VALUES (?, ?)", (url, title)).lastrowid
        db.execute("INSERT INTO moz_historyvisits (id, from_visit, place_id, visit_date, visit_type) "
                   "VALUES (?,?,?,?,?)", (vid, from_visit, place, unix_time(when), visit_type))
        db.commit()
    finally:
        if own:
            db.close()
