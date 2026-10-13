"""The watcher's pure core (spec §1): 2-second samples in, stretch actions out. No Windows, no SQLite.

A stretch is written from its second poll (one poll is a flick), extended every 30 s, and closed: at the observing
poll for a window change, lock, pause or exclusion; at the last input (never before its start) for idle; at the
earlier poll when two polls are more than 30 s apart. Reads need 30 s of dwell in the stretch AND 120 s since this
window (hwnd, pid, process creation time) was last read."""
import re
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import timedelta

POLL_GAP = 30.0
IDLE_LIMIT = 300.0
DWELL = 30.0
REREAD = 120.0
EXTEND_EVERY = 30.0
MIN_POLLS = 2


@dataclass(frozen=True)
class Identity:
    hwnd: int
    pid: int
    created: int  # process creation time (FILETIME): a reused pid is a different window
    exe: str
    title: str    # raw, memory only

    @property
    def window(self):
        return self.hwnd, self.pid, self.created


def _kept(ch):
    return unicodedata.category(ch)[0] in "LN"


def title_key(title):
    """A title's identity for stretch purposes (I3): the ends lose anything that isn't a letter or digit (spinner
    glyphs, braille patterns, symbols, punctuation, spaces) and runs of digits become one "#", so an animated or
    ticking title ("Build 0:41" -> "Build 0:42") stays one window while a real change ("Inbox" -> "Drafts") doesn't."""
    t = title or ""
    lo, hi = 0, len(t)
    while lo < hi and not _kept(t[lo]):
        lo += 1
    while hi > lo and not _kept(t[hi - 1]):
        hi -= 1
    return re.sub(r"\d+", "#", t[lo:hi])


def same_window(a, b):
    """The same window showing the same thing: hwnd, pid, creation time and exe exactly; title by title_key."""
    return ((a.hwnd, a.pid, a.created, a.exe) == (b.hwnd, b.pid, b.created, b.exe)
            and title_key(a.title) == title_key(b.title))


@dataclass(frozen=True)
class Sample:
    mono: float
    utc: object
    window: object  # Identity | None
    idle_s: float
    locked: bool


@dataclass(frozen=True)
class Opened:
    stretch_id: str
    identity: Identity
    started: object
    ended: object


@dataclass(frozen=True)
class Extended:
    stretch_id: str
    ended: object


@dataclass(frozen=True)
class Closed:
    stretch_id: str
    ended: object


@dataclass(frozen=True)
class ReadDue:
    stretch_id: str
    identity: Identity


@dataclass
class OpenStretch:
    id: str
    identity: Identity
    started: object
    started_mono: float
    last: object
    last_mono: float
    polls: int = 1
    written: bool = False
    extended_mono: float = 0.0


class Core:
    def __init__(self, new_id=lambda: "screen:" + str(uuid.uuid4())):
        self.new_id, self.open, self.last_read, self.last_mono = new_id, None, {}, None

    def step(self, s, settings):
        out = []
        if self.open and self.last_mono is not None and s.mono - self.last_mono > POLL_GAP:
            out += self.close(self.open.last)
        self.last_mono = s.mono
        self.last_read = {k: v for k, v in self.last_read.items() if s.mono - v < REREAD}
        idle = s.idle_s >= IDLE_LIMIT
        if idle and self.open and not s.locked:
            out += self.close(s.utc - timedelta(seconds=s.idle_s))
        w = s.window
        if s.locked or idle or w is None or settings.paused(s.utc) or settings.never(w.exe, w.title):
            if self.open:
                out += self.close(s.utc)
            return out
        o = self.open
        if o and same_window(o.identity, w):
            o.last, o.last_mono, o.polls = max(s.utc, o.last), s.mono, o.polls + 1
            if not o.written and o.polls >= MIN_POLLS:
                o.written, o.extended_mono = True, s.mono
                out.append(Opened(o.id, o.identity, o.started, o.last))
            elif o.written and s.mono - o.extended_mono >= EXTEND_EVERY:
                o.extended_mono = s.mono
                out.append(Extended(o.id, o.last))
        else:
            if o:
                out += self.close(s.utc)
            o = self.open = OpenStretch(self.new_id(), w, s.utc, s.mono, s.utc, s.mono)
        if (o.written and settings.text_app(w.exe) and s.mono - o.started_mono >= DWELL
                and w.window not in self.last_read):
            self.last_read[w.window] = s.mono
            out.append(ReadDue(o.id, w))
        return out

    def close(self, ended):
        o, self.open = self.open, None
        if not o or not o.written:
            return []
        return [Closed(o.id, max(ended, o.started))]

    def drop(self):
        """After a removal (spec §5): forget the open stretch without writing anything."""
        self.open = None
