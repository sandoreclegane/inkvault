"""The watcher's vault writes (spec §5). Each write is one short BEGIN IMMEDIATE transaction that first checks the
removal generation it was observed under, and for an extend, close or text, that its stretch still exists.
Otherwise it raises Stale and writes nothing: a stretch a removal deleted is never recreated (F1)."""
import sqlite3
import time
from collections import OrderedDict
from dataclasses import dataclass, field

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


@dataclass
class Flush:
    written: int = 0
    stale: bool = False
    busy: bool = False
    error: object = None
    texts: list = field(default_factory=list)


class Entry:
    def __init__(self):
        self.open, self.state, self.texts = None, None, []

    def empty(self):
        return self.open is None and self.state is None and not self.texts


class Queue:
    """Pending writes by stretch (spec §5, R4, P11). An unwritten open absorbs later extends and its close; text
    follows its open in order. One flush per poll, stopping at the first failure, after max_writes, or budget_s."""

    def __init__(self, max_stretches=50, max_text_bytes=2_000_000):
        self.max_stretches, self.max_text_bytes = max_stretches, max_text_bytes
        self.entries, self.text_bytes, self.dropped_text = OrderedDict(), 0, 0

    def __len__(self):
        return len(self.entries)

    def blocked(self):
        return len(self.entries) >= self.max_stretches

    def push(self, w):
        if w.kind == "open" and w.stretch_id not in self.entries and self.blocked():
            return False
        e = self.entries.setdefault(w.stretch_id, Entry())
        if w.kind == "open":
            e.open = w
        elif w.kind in ("extend", "close"):
            if e.open is not None:
                e.open.ended = w.ended
            elif not (e.state and e.state.kind == "close" and w.kind == "extend"):
                e.state = w
        else:
            e.texts.append(w)
            self.text_bytes += w.size()
            self._trim()
        return True

    def _trim(self):
        while self.text_bytes > self.max_text_bytes:
            for sid, e in self.entries.items():
                if e.texts:
                    self.text_bytes -= e.texts.pop(0).size()
                    self.dropped_text += 1
                    if e.empty():
                        del self.entries[sid]
                    break
            else:
                return

    def has_text(self, sid, h):
        e = self.entries.get(sid)
        return bool(e) and any(w.text_hash == h for w in e.texts)

    def drop_text(self, pred):
        n = 0
        for sid in list(self.entries):
            e = self.entries[sid]
            keep = []
            for w in e.texts:
                if pred(w):
                    self.text_bytes -= w.size()
                    n += 1
                else:
                    keep.append(w)
            e.texts = keep
            if e.empty():
                del self.entries[sid]
        self.dropped_text += n
        return n

    def clear(self):
        self.entries.clear()
        self.text_bytes = 0

    def flush(self, conn, max_writes=20, budget_s=0.25, clock=time.monotonic):
        r, start = Flush(), clock()

        def one(w):
            if r.written >= max_writes or clock() - start > budget_s:
                return False
            try:
                apply(conn, w)
            except Stale:
                r.stale = True
                return False
            except Busy:
                r.busy = True
                return False
            except StoreError as e:
                r.error = str(e)
                return False
            r.written += 1
            if w.kind == "text":
                r.texts.append(w)
            return True

        for sid in list(self.entries):
            e = self.entries[sid]
            for slot in ("open", "state"):
                w = getattr(e, slot)
                if w is not None:
                    if not one(w):
                        return r
                    setattr(e, slot, None)
            while e.texts:
                if not one(e.texts[0]):
                    return r
                self.text_bytes -= e.texts.pop(0).size()
            del self.entries[sid]
        return r
