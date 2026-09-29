"""Copy everything out of a running PiecesOS into vault.db (SQLite).

Read-only against PiecesOS. Only adds, never deletes, and skips what it already has, so it is safe
to stop and re-run at any time (re-running also picks up anything PiecesOS captured since).
Every record is kept as the raw JSON PiecesOS returned, so nothing is lost to our interpretation.
"""
import datetime
import json
import os
import sqlite3
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import paths

# PiecesOS has listened on 39300 (current) and 1000 (older macOS/Linux builds).
PORTS = [int(p) for p in os.environ.get("INKVAULT_PIECES_PORTS", "39300,1000").split(",")]
WORKERS = 16
# Collections fetched as one list. Missing ones are skipped: not every PiecesOS version has all of them.
LISTED = {
    "summary": "/workstream_summaries",
    "conversation": "/conversations",
    "annotation": "/annotations",
    "message": "/messages",
    "tag": "/tags",
    "website": "/websites",
    "person": "/persons",
    "anchor": "/anchors",
    "range": "/ranges",
}


class PiecesOS:
    def __init__(self, base):
        self.base = base

    def get(self, path, timeout=60):
        with urllib.request.urlopen(self.base + path, timeout=timeout) as r:
            return json.loads(r.read())

    @classmethod
    def find(cls):
        for port in PORTS:
            base = f"http://localhost:{port}"
            try:
                with urllib.request.urlopen(base + "/.well-known/version", timeout=5) as r:
                    return cls(base), r.read().decode().strip().strip('"')
            except OSError:
                continue
        return None, None


def open_vault():
    db = sqlite3.connect(paths.vault_db())
    db.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS events (
            id TEXT PRIMARY KEY, created TEXT, app TEXT, window_title TEXT, url TEXT, readable TEXT, raw TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS raw_records (kind TEXT NOT NULL, id TEXT NOT NULL, raw TEXT NOT NULL, PRIMARY KEY (kind, id));
        CREATE TABLE IF NOT EXISTS failures (kind TEXT, id TEXT, error TEXT);
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
    """)
    return db


def event_row(ev):
    ocr = (ev.get("context") or {}).get("native_ocr") or {}
    return (
        ev["id"],
        (ev.get("created") or {}).get("value"),
        ocr.get("appTitle"),
        ev.get("windowTitle") or ocr.get("windowTitle"),
        ev.get("browserUrl") or ocr.get("browserUrl"),
        ev.get("readable"),
        json.dumps(ev),
    )


def fetch_each(pos, db, kind, ids_path, item_path, save, have):
    """Fetch items one by one (in parallel): scales to collections too big for one list response."""
    try:
        ids = [x["id"] for x in pos.get(ids_path, timeout=300).get("iterable", [])]
    except OSError as e:
        print(f"{kind}: skipped ({e})")
        return
    todo = [i for i in ids if i not in have]
    print(f"{kind}: {len(ids)} total, {len(ids) - len(todo)} already saved, {len(todo)} to fetch", flush=True)
    start, n = time.time(), 0
    pool = ThreadPoolExecutor(WORKERS)
    try:
        futures = {pool.submit(pos.get, item_path.format(id=i)): i for i in todo}
        for f in as_completed(futures):
            try:
                save(f.result())
            except Exception as e:  # one bad record must not stop a rescue
                db.execute("INSERT INTO failures VALUES (?, ?, ?)", (kind, futures[f], str(e)))
            n += 1
            if n % 1000 == 0:
                db.commit()
                rate = n / (time.time() - start)
                print(f"  {n}/{len(todo)} ({rate:.0f}/s, ~{(len(todo) - n) / rate / 60:.1f} min left)", flush=True)
    finally:
        # Keep everything fetched so far. On Ctrl+C, drop the queued requests instead of waiting
        # for all of them (a `with` block would wait, which can mean hours).
        db.commit()
        pool.shutdown(wait=False, cancel_futures=True)


def export_listed(pos, db, kind, path):
    try:
        items = pos.get(path, timeout=300).get("iterable", [])
    except OSError as e:
        print(f"{kind}: skipped ({e})")
        return
    db.executemany("INSERT OR REPLACE INTO raw_records VALUES (?,?,?)",
                   [(kind, it["id"], json.dumps(it)) for it in items if it.get("id")])
    db.commit()
    print(f"{kind}: {len(items)}", flush=True)


def run():
    pos, version = PiecesOS.find()
    if not pos:
        print(f"PiecesOS isn't answering on port(s) {', '.join(map(str, PORTS))}.\n"
              "Open the Pieces app (or PiecesOS) and try again. If it uses another port, set INKVAULT_PIECES_PORTS.")
        return False
    print(f"PiecesOS {version} at {pos.base}\nSaving to {paths.vault_db()}\n"
          "PiecesOS hands records over slowly (~8/second), so a big vault can take a few hours.\n"
          "It's safe to stop anytime (Ctrl+C) and run again later: it picks up where it left off.", flush=True)
    db = open_vault()
    db.execute("DELETE FROM failures")  # failed items are retried below and re-recorded if they still fail
    db.executemany("INSERT OR REPLACE INTO meta VALUES (?,?)", [
        ("pieces_version", version), ("pieces_url", pos.base),
        ("last_export", datetime.datetime.now().astimezone().isoformat(timespec="seconds")),
    ])

    for kind, path in LISTED.items():
        export_listed(pos, db, kind, path)

    # Saved snippets: Pieces' original feature, and often what people care about most.
    have = {i for (i,) in db.execute("SELECT id FROM raw_records WHERE kind='asset'")}
    fetch_each(pos, db, "snippets", "/assets/identifiers", "/asset/{id}",
               lambda a: db.execute("INSERT OR REPLACE INTO raw_records VALUES ('asset',?,?)", (a["id"], json.dumps(a))), have)

    have = {i for (i,) in db.execute("SELECT id FROM events")}
    fetch_each(pos, db, "events", "/workstream_events/identifiers", "/workstream_event/{id}",
               lambda ev: db.execute("INSERT OR REPLACE INTO events VALUES (?,?,?,?,?,?,?)", event_row(ev)), have)

    fails = db.execute("SELECT kind, COUNT(*) FROM failures GROUP BY kind").fetchall()
    db.close()
    print("export done. failures:", ", ".join(f"{k} {n}" for k, n in fails) if fails else "none")
    return True
