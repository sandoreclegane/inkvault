"""The watcher's vault writes (spec §5). Each write is one short BEGIN IMMEDIATE transaction that first checks the
removal generation it was observed under, and for an extend, close or text, that its stretch still exists.
Otherwise it raises Stale and writes nothing: a stretch a removal deleted is never recreated (F1)."""
import sqlite3
from dataclasses import dataclass

from . import export, screen

BUSY_MS = 250


class Stale(Exception):
    """The removal generation moved, or the stretch is gone."""


class Busy(Exception):
    """Another writer holds the vault."""


class StoreError(Exception):
    """Any other database error (disk full, I/O): the write stays queued and the error is reported."""


@dataclass
class Write:
    kind: str
    stretch_id: str
    gen: int
    settings_gen: int = 0
    started: str = ""
    ended: str = ""
    app: str = ""
    title: object = None
    text_id: str = ""
    created: str = ""
    text: str = ""
    text_hash: str = ""

    def size(self):
        return len(self.text.encode("utf-8")) if self.kind == "text" else 0


def classify(e):
    msg = str(e)
    if isinstance(e, sqlite3.OperationalError) and ("locked" in msg or "busy" in msg):
        return Busy(msg)
    return StoreError(type(e).__name__ + ": " + msg)


def connect():
    try:
        conn = export.open_vault(timeout=BUSY_MS / 1000)
        conn.commit()
    except sqlite3.Error as e:
        raise classify(e) from None
    conn.isolation_level = None
    return conn


def apply(conn, w):
    try:
        conn.execute("BEGIN IMMEDIATE")
    except sqlite3.Error as e:
        raise classify(e) from None
    try:
        if screen.removal_gen(conn) != w.gen:
            raise Stale("removal generation moved")
        if w.kind == "open":
            conn.execute("INSERT OR IGNORE INTO screen_stretches VALUES (?,?,?,?,?)",
                         (w.stretch_id, w.started, w.ended, w.app, w.title))
        elif w.kind in ("extend", "close"):
            if conn.execute("UPDATE screen_stretches SET ended=? WHERE id=?", (w.ended, w.stretch_id)).rowcount == 0:
                raise Stale("stretch gone")
        elif w.kind == "text":
            if not conn.execute("SELECT 1 FROM screen_stretches WHERE id=?", (w.stretch_id,)).fetchone():
                raise Stale("stretch gone")
            conn.execute("INSERT OR IGNORE INTO screen_text VALUES (?,?,?,?,?)",
                         (w.text_id, w.stretch_id, w.created, w.text, w.text_hash))
        else:
            raise ValueError(f"unknown write {w.kind!r}")
        conn.execute("COMMIT")
    except BaseException as e:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        if isinstance(e, sqlite3.Error):
            raise classify(e) from None
        raise
