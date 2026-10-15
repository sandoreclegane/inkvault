"""Search over the InkVault index, for MCP (server.py) and the Memory Atlas (atlasserve.py).

Answers are structures, not text (spec §3.1): server.py formats them exactly as it always has, and the Atlas sends
them to the page as JSON. Everything here is read-only. Importing it starts nothing and changes no process setting;
each process that answers questions sets HF_HUB_OFFLINE itself.
"""
import base64
import functools
import hashlib
import heapq
import json
import re
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone

import numpy as np

from . import embed, paths, screen, sources
from .times import local

# kind -> (table, fts table, title expr, snippet column, body column, the Atlas's title expr)
SOURCES = {
    "summaries": ("summaries", "summaries_fts", "t.name", 1, "text", "t.name"),
    "events": ("events", "events_fts", "t.window_title", 3, "readable", "t.window_title"),
    "chats": ("messages", "messages_fts", "t.conversation_name || ' (' || t.role || ')'", 1, "text",
              "t.conversation_name"),
    "snippets": ("snippets", "snippets_fts", "t.name || ' (' || t.language || ')'", 2, "text", "t.name"),
    "screen": ("screen_stretches", "stretches_fts", "t.app || ': ' || coalesce(t.title, '')", 0, "title",
               "coalesce(t.title, t.app)"),
    "web": ("pages", "pages_fts", "coalesce(t.title, t.url) || ' — ' || t.host", -1, "url",
            "coalesce(t.title, t.url)"),
}
# Two facts the Atlas shows in each hit's source label; (table, column) or None.
FACTS = {
    "summaries": (None, None),
    "events": (("events", "source"), ("events", "app")),
    "chats": (("messages", "source"), None),
    "snippets": (("snippets", "language"), None),
    "screen": (("screen_stretches", "app"), None),
    "web": (("pages", "host"), None),
}
MCP_MARKERS = ("[", "]")
ATLAS_MARKERS = ("\ue000", "\ue001")  # the Atlas's markers (private-use characters)...
SECOND_MARKERS = ("\ue002", "\ue003")  # ...and a second pair: each snippet is made with both, so a character that
# differs between the two copies is a marker FTS inserted, and one that's the same is the record's own text, even when
# the record itself contains marker characters (Codex plan review #5)
# The Atlas's source chips (spec §4.2): the kinds each searches, and which events.source it keeps.
CHIPS = {
    "all": (None, None),
    "conversations": (["chats"], None),
    "summaries": (["summaries"], None),
    "captures": (["events"], "pieces"),
    "snippets": (["snippets"], None),
    "screen": (["screen", "events"], "screen"),
    "web": (["web"], None),
}
CANDIDATES = 60  # per ranked list, before fusion
RRF_K = 60
REBUILDING = "Search is being rebuilt after something was removed: run `inkvault index`."
CHANGED = "Search was updated while answering; ask again."
UNAVAILABLE = "The vault can't be read right now; try again in a moment."
NAMES = {"pieces": "Pieces", **sources.NAMES}


def has_table(conn, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE name=?", (name,)).fetchone() is not None


def has_column(conn, table, column):
    return any(r[1] == column for r in conn.execute(f"PRAGMA table_info({table})"))


def removal_gen():
    """0 only when there's genuinely no generation (no vault, or a vault without a meta table); None when the
    vault can't be read, which the guard turns into an explicit refusal (R8)."""
    if not paths.vault_db().exists():
        return 0
    try:
        v = paths.connect_ro(paths.vault_db())
    except sqlite3.Error:
        return None
    try:
        if not has_table(v, "meta"):
            return 0
        return screen.removal_gen(v)
    except sqlite3.Error:
        return None
    finally:
        v.close()


def published():
    """What a read uses: whether a removal is under way, which search files are published, and the screen removal
    generation (a removal that completes during a live read changes it)."""
    def stamp(p):
        try:
            st = p.stat()
        except FileNotFoundError:
            return None
        return st.st_mtime_ns, st.st_size
    return paths.rebuild_marker().exists(), stamp(paths.search_db()), stamp(paths.vectors()), removal_gen()


def generation(stamp):
    """An opaque name for one published state (spec §3.1): the page and every cursor carry it."""
    return hashlib.sha256(repr(stamp).encode()).hexdigest()[:16]


class Refused(Exception):
    """A read the guard refused. status: "rebuilding", "unavailable" or "changed"."""
    def __init__(self, status):
        super().__init__(status)
        self.status = status


def guarded_call(fn, *args, **kwargs):
    """Never answer from files a removal has started to change: check before starting and again before answering.
    Returns (answer, generation); raises Refused. An answer already shown can't be recalled."""
    before = published()
    if before[0]:
        raise Refused("rebuilding")
    if before[3] is None:
        raise Refused("unavailable")
    answer = fn(*args, **kwargs)
    after = published()
    if after[0]:
        raise Refused("rebuilding")
    if after[3] is None:
        raise Refused("unavailable")
    if after != before:
        raise Refused("changed")
    return answer, generation(before)


SENTENCES = {"rebuilding": REBUILDING, "unavailable": UNAVAILABLE, "changed": CHANGED}


def guarded(tool):
    """The guard for MCP tools: the same checks, answered with the same sentences as always."""
    @functools.wraps(tool)
    def run(*args, **kwargs):
        try:
            return guarded_call(tool, *args, **kwargs)[0]
        except Refused as r:
            return SENTENCES[r.status]
    return run


def utc_bound(text):
    """A time bound as stored: UTC, microseconds, "Z". A bound without a zone is UTC, like every stored time."""
    t = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def web_filter(since, until, params):
    """Pages by when they were opened. A date-only bound compares the page's local day; a bound with a time compares
    its visits (UTC, like every other source's times), so a later visit in the range finds the page."""
    sql, visit = "", []
    if since:
        if len(since) == 10:
            sql += " AND t.day >= ?"
            params.append(since)
        else:
            visit.append(("v.created >= ?", utc_bound(since)))
    if until:
        if len(until) == 10:
            sql += " AND t.day <= ?"
            params.append(until)
        else:
            visit.append(("v.created < ?", utc_bound(until)))
    if visit:
        sql += (" AND EXISTS (SELECT 1 FROM visits v WHERE v.page_id = t.id AND "
                + " AND ".join(c for c, _ in visit) + ")")
        params.extend(v for _, v in visit)
    return sql


def range_filter(kind, since, until, params):
    if kind == "web":
        return web_filter(since, until, params)
    if kind == "screen":
        if not (since or until):
            return ""
        s, u = screen.bounds(since or "1970-01-01", until)
        params.extend([u, s])
        return screen.overlap_sql("t.started", "t.ended")
    return date_filter("t.created", since, until, params)


def date_filter(col, since, until, params):
    sql = ""
    if since:
        sql += f" AND {col} >= ?"
        params.append(since)
    if until:
        sql += f" AND {col} < ?"
        params.append(until + "T99" if len(until) == 10 else until)
    return sql


_vec = {"mtime": None}
_model = None


@contextmanager
def db():
    # Close explicitly (sqlite3's `with` only ends the transaction) so a re-index can replace the file on Windows.
    conn = paths.connect_ro(paths.search_db())
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def fts_query(q: str) -> str:
    """Plain words -> AND of quoted terms. Queries already using FTS syntax pass through."""
    if re.search(r'"|\bOR\b|\bAND\b|\bNOT\b|\bNEAR\b|\*', q):
        return q
    return " ".join(f'"{t}"' for t in re.findall(r"[\w'-]+", q))


def vectors():
    """Meaning-search vectors, reloaded automatically after a re-index."""
    path = paths.vectors()
    if not path.exists():
        return None
    mtime = path.stat().st_mtime
    if _vec["mtime"] != mtime:
        with np.load(path) as z:  # close right away: Windows can't replace an open file
            _vec.update(mtime=mtime, kinds=z["kinds"], ids=z["ids"], vecs=z["vecs"].astype(np.float32))
    return _vec


def meaning_ready():
    """Meaning search can run without a download: the vectors and the model folder are both there (spec §3.1)."""
    return paths.vectors().exists() and paths.model_dir().exists()


def keyword_ranked(conn, kind, query, since, until, **opts):
    """Ids best first (bm25 only: no snippets here, they are costly on long records). opts (Atlas only; MCP passes
    none): event_source="pieces"|"screen"."""
    table, fts, _, _, _, _ = SOURCES[kind]
    params = [fts_query(query)]
    sql = (f"SELECT t.id FROM {fts} JOIN {table} t ON t.rowid = {fts}.rowid WHERE {fts} MATCH ?"
           + range_filter(kind, since, until, params))
    if kind == "events" and opts.get("event_source") and has_column(conn, "events", "source"):
        sql += " AND t.source = ?"
        params.append(opts["event_source"])
    sql += f" ORDER BY bm25({fts}) LIMIT ?"
    params.append(CANDIDATES)
    return conn.execute(sql, params).fetchall()


SNIPPET_LIMIT = 100_000  # Atlas only: a record whose snippet column is longer than this gets no highlight


def keyword_snippets(conn, kind, query, id_, markers):
    """(snippet, snippet2) for one hit. MCP (MCP_MARKERS): one snippet, exactly as before. Atlas: the snippet made
    with its markers and with SECOND_MARKERS; for a record too long to snippet quickly, ("", "") (a keyword match
    with no spans, never an invented highlight)."""
    table, fts, _, col, body, _ = SOURCES[kind]
    atlas = markers != MCP_MARKERS
    if atlas:
        n = conn.execute(f"SELECT length(t.{body}) FROM {table} t WHERE t.id = ?", (id_,)).fetchone()
        if n and (n[0] or 0) > SNIPPET_LIMIT:
            return "", ""
    pairs = [markers] + ([SECOND_MARKERS] if atlas else [])
    sel = ", ".join(f"snippet({fts}, {col}, '{o}', '{c}', ' … ', 40)" for o, c in pairs)
    r = conn.execute(f"SELECT {sel} FROM {fts} WHERE {fts} MATCH ? AND rowid = (SELECT rowid FROM {table} WHERE id = ?)",
                     (fts_query(query), id_)).fetchone()
    if r is None:
        return None, None
    return r[0], (r[1] if atlas else None)


_model_lock = threading.Lock()


def meaning_ranked(kinds, query):
    global _model
    v = vectors()
    if v is None:
        return []
    with _model_lock:
        if _model is None:
            _model = embed.load_model()
    q = _model.encode([query])[0]
    q /= np.linalg.norm(q) + 1e-9
    scores = v["vecs"] @ q
    scores[~np.isin(v["kinds"], kinds)] = -1
    top = np.argsort(-scores)[:CANDIDATES * 5]  # over-fetch: date filtering happens afterwards
    return [(str(v["kinds"][i]), str(v["ids"][i])) for i in top if scores[i] > 0]


@dataclass
class Hit:
    kind: str
    id: str
    created: str | None
    title: str | None       # MCP's title
    display: str | None     # the Atlas's title
    snippet: str | None     # the FTS snippet with the caller's markers; None for a hit found by meaning only
    preview: str | None     # the record's first 300 characters
    fact1: str | None = None
    fact2: str | None = None
    snippet2: str | None = None  # the Atlas only: the same snippet made with SECOND_MARKERS
    browsed: bool = False        # listed by date (browse), not found by a query


@dataclass
class SearchResult:
    hits: list = field(default_factory=list)
    more: bool = False
    keyword_only: bool = False
    notes: list = field(default_factory=list)
    error: str | None = None   # today's sentence for a bad source, mode, date, query, or a missing index
    empty_index: bool = False  # the error is "not indexed yet"


def facts_sql(conn, kind):
    out = []
    for fact in FACTS[kind]:
        out.append(f"t.{fact[1]}" if fact and has_column(conn, fact[0], fact[1]) else "NULL")
    return out


def search(query, source="all", mode="hybrid", since="", until="", limit=10, *, offset=0, kinds=None,
           event_source=None, markers=MCP_MARKERS, require_model=False):
    """Ranked hits, best first (spec §3.1). MCP passes the first six arguments only, exactly as before; the Atlas also
    passes kinds and event_source (its chips), its own markers, require_model, and pages with offset."""
    if kinds is None:
        kinds = list(SOURCES) if source == "all" else [source]
    if any(k not in SOURCES for k in kinds):
        return SearchResult(error=f"Unknown source {source!r}. Use one of: all, {', '.join(SOURCES)}")
    if mode not in ("hybrid", "keyword", "meaning"):
        return SearchResult(error=f"Unknown mode {mode!r}. Use hybrid, keyword, or meaning.")
    for bound in (since, until):
        if bound and len(bound) != 10:
            try:
                utc_bound(bound)
            except ValueError:
                return SearchResult(error=f"Bad date {bound!r}: use a date (2026-10-05) or a UTC time (2026-10-05T14:30).")
    if since or until:
        try:
            screen.bounds(since or "1970-01-01", until)
        except ValueError as e:
            return SearchResult(error=f"Bad range: {e}")
    if not paths.search_db().exists():
        return SearchResult(error="The vault hasn't been indexed yet. Run `inkvault rescue` first.", empty_index=True)

    if require_model and not fts_query(query):
        # A query with no word characters has no meaning to match either (Atlas only; MCP keeps v0.3.0 behaviour,
        # pinned by the golden test).
        return SearchResult(keyword_only=mode != "keyword" and not meaning_ready())

    opts = {}
    if event_source:
        opts["event_source"] = event_source
    meaning = mode != "keyword" and (meaning_ready() or not require_model)
    scores, matched = {}, set()  # reciprocal rank fusion: each list adds 1/(k + rank) per hit
    degraded = False
    with db() as conn:
        if source == "web" and not has_table(conn, "pages"):
            return SearchResult(error="Web pages aren't in the search index yet: run `inkvault index`.")
        kinds = [k for k in kinds if has_table(conn, SOURCES[k][0])]
        if mode != "meaning":
            for kind in kinds:
                try:
                    rows = keyword_ranked(conn, kind, query, since, until, **opts)
                except sqlite3.OperationalError as e:
                    if mode == "keyword":
                        return SearchResult(error=f"Bad query {query!r}: {e}. Try plain keywords.")
                    rows = []
                for rank, r in enumerate(rows):
                    key = (kind, r["id"])
                    scores[key] = scores.get(key, 0) + 1 / (RRF_K + rank)
                    matched.add(key)
        if meaning:
            try:
                ranked = meaning_ranked([k for k in kinds if k != "screen"], query)
            except Exception:
                if not require_model:
                    raise
                ranked, degraded = [], True  # the Atlas answers by keyword when the model can't load or run
            for rank, key in enumerate(ranked):
                scores[key] = scores.get(key, 0) + 1 / (RRF_K + rank)

        fetched = []
        has_stretch = has_column(conn, "events", "stretch_id")
        filter_source = event_source and has_column(conn, "events", "source")
        for kind, id_ in sorted(scores, key=scores.get, reverse=True):
            table, _, title, _, body, display = SOURCES[kind]
            f1, f2 = facts_sql(conn, kind)
            created = "t.started" if kind == "screen" else "t.created"
            stretch = ("t.id" if kind == "screen" else
                       "t.stretch_id" if kind == "events" and has_stretch else "NULL")
            params = [id_]
            sql = (f"SELECT {created} AS created, {title} AS title, {display} AS display, "
                   f"substr(t.{body}, 1, 300) AS preview, {stretch} AS stretch, {f1} AS fact1, {f2} AS fact2 "
                   f"FROM {table} t WHERE t.id = ?" + range_filter(kind, since, until, params))
            if kind == "events" and filter_source:
                sql += " AND t.source = ?"
                params.append(event_source)
            r = conn.execute(sql, params).fetchone()
            if r:
                fetched.append((kind, id_, r))
        ocr_stretches = {r["stretch"] for k, _, r in fetched if k == "events" and r["stretch"]}
        out, seen = [], set()
        for kind, id_, r in fetched:
            if r["stretch"]:
                if (kind == "screen" and r["stretch"] in ocr_stretches) or r["stretch"] in seen:
                    continue      # one hit per stretch; the OCR row wins (screen spec §6)
                seen.add(r["stretch"])
            out.append(Hit(kind, id_, r["created"], r["title"], r["display"], None, r["preview"],
                           r["fact1"], r["fact2"], None))
            if len(out) > offset + limit:
                break
        for h in out[offset:offset + limit]:  # snippets only for the hits actually returned
            if (h.kind, h.id) in matched:
                h.snippet, h.snippet2 = keyword_snippets(conn, h.kind, query, h.id, markers)
        notes = []
        if mode == "meaning" and "screen" in kinds:
            notes.append("(screen stretches are keyword-only; they weren't searched by meaning)")
        if {"screen", "events"} & set(kinds) and has_table(conn, "meta") and (
                c := conn.execute("SELECT value FROM meta WHERE key='screen_cutoff'").fetchone()):
            notes.append(f"(search covers screen activity up to {local(c[0]):%Y-%m-%d %H:%M}; "
                         "timeline() shows it live)")
    return SearchResult(hits=out[offset:offset + limit], more=len(out) > offset + limit,
                        keyword_only=mode != "keyword" and (degraded or not meaning_ready()), notes=notes)


BROWSE_BATCH = 100  # rows fetched per kind at a time while walking a date range


def browse(since, until, *, kinds=None, event_source=None, limit=10, offset=0):
    """Everything in a date range, oldest first, with no query (the Atlas's "explore this day"). The same range
    filters, chips, facts and one-hit-per-stretch rule as search(); every kind is read in time order (ties: kind,
    then id) a batch at a time and merged, so nothing is loaded beyond what the requested page needs."""
    kinds = list(SOURCES) if kinds is None else list(kinds)
    if any(k not in SOURCES for k in kinds):
        return SearchResult(error=f"Unknown source. Use one of: all, {', '.join(SOURCES)}")
    for bound in (since, until):
        if bound and len(bound) != 10:
            try:
                utc_bound(bound)
            except ValueError:
                return SearchResult(error=f"Bad date {bound!r}: use a date (2026-10-05) or a UTC time (2026-10-05T14:30).")
    if since or until:
        try:
            screen.bounds(since or "1970-01-01", until)
        except ValueError as e:
            return SearchResult(error=f"Bad range: {e}")
    if not paths.search_db().exists():
        return SearchResult(error="The vault hasn't been indexed yet. Run `inkvault rescue` first.", empty_index=True)

    with db() as conn:
        if kinds == ["web"] and not has_table(conn, "pages"):
            return SearchResult(error="Web pages aren't in the search index yet: run `inkvault index`.")
        kinds = [k for k in kinds if has_table(conn, SOURCES[k][0])]
        has_stretch = has_column(conn, "events", "stretch_id")
        filter_source = bool(event_source) and has_column(conn, "events", "source")
        stretch_indexed = any(r[1] == "events_stretch" for r in conn.execute("PRAGMA index_list('events')"))

        def rows(kind):
            table, _, title, _, body, display = SOURCES[kind]
            f1, f2 = facts_sql(conn, kind)
            created = "t.started" if kind == "screen" else "t.created"
            stretch = ("t.id" if kind == "screen" else
                       "t.stretch_id" if kind == "events" and has_stretch else "NULL")
            # The OCR row wins (screen spec §6): a screen stretch with an events row in the same range and chip is
            # left out in SQL, so suppressed stretches never reach Python. With the events(stretch_id) index
            # (current builds) each stretch costs one cheap probe. An index built before it has no such index, so
            # there SQLite builds the range's OCR stretch ids once per query instead (an uncorrelated NOT IN), not a
            # scan per stretch (Codex recheck).
            anti, anti_params = "", []
            if kind == "screen" and "events" in kinds and has_stretch:
                if stretch_indexed:
                    anti = " AND NOT EXISTS (SELECT 1 FROM events e WHERE e.stretch_id = t.id"
                else:
                    anti = " AND t.id NOT IN (SELECT e.stretch_id FROM events e WHERE e.stretch_id IS NOT NULL"
                anti += date_filter("e.created", since, until, anti_params)
                if filter_source:
                    anti += " AND e.source = ?"
                    anti_params.append(event_source)
                anti += ")"
            last = None  # (created, id) of the last row seen: keyset paging, so every batch starts where it ended
            while True:
                params = []
                sql = (f"SELECT t.id AS id, {created} AS created, {title} AS title, {display} AS display, "
                       f"substr(t.{body}, 1, 300) AS preview, {stretch} AS stretch, {f1} AS fact1, {f2} AS fact2 "
                       f"FROM {table} t WHERE 1=1" + range_filter(kind, since, until, params))
                if kind == "events" and filter_source:
                    sql += " AND t.source = ?"
                    params.append(event_source)
                sql += anti
                params += anti_params
                if last is not None:            # NULL times sort first, then by id
                    if last[0] is None:
                        sql += f" AND ({created} IS NOT NULL OR t.id > ?)"
                        params.append(last[1])
                    else:
                        sql += f" AND ({created} > ? OR ({created} = ? AND t.id > ?))"
                        params += [last[0], last[0], last[1]]
                sql += f" ORDER BY {created}, t.id LIMIT ?"
                batch = conn.execute(sql, params + [BROWSE_BATCH]).fetchall()
                for r in batch:
                    yield (r["created"] or "", kind, r["id"]), r
                if len(batch) < BROWSE_BATCH:
                    return
                last = (batch[-1]["created"], batch[-1]["id"])

        out, seen = [], set()
        for (_, kind, id_), r in heapq.merge(*(rows(k) for k in kinds), key=lambda x: x[0]):
            if r["stretch"]:
                if r["stretch"] in seen:
                    continue              # one hit per stretch
                seen.add(r["stretch"])
            out.append(Hit(kind, id_, r["created"], r["title"], r["display"], None, r["preview"],
                           r["fact1"], r["fact2"], None, browsed=True))
            if len(out) > offset + limit:
                break
        notes = []
        if {"screen", "events"} & set(kinds) and has_table(conn, "meta") and (
                c := conn.execute("SELECT value FROM meta WHERE key='screen_cutoff'").fetchone()):
            notes.append(f"(search covers screen activity up to {local(c[0]):%Y-%m-%d %H:%M}; "
                         "timeline() shows it live)")
    return SearchResult(hits=out[offset:offset + limit], more=len(out) > offset + limit, notes=notes)


def excerpt(hit, markers=ATLAS_MARKERS):
    """(text, spans) for the Atlas: the snippet without the markers FTS inserted, and where they were, as [start, end)
    offsets in UTF-16 code units (what the page's JavaScript counts).

    The snippet was made twice, with ATLAS_MARKERS and with SECOND_MARKERS. Walking both together, a position where
    they agree is the record's own character (kept, even if it equals a marker); a position where one has an opening
    marker and the other the matching opening marker of the second pair is an inserted boundary. Anything else, or
    two snippets that don't line up, gets no spans: a highlight is never invented. Meaning-only hits have no snippet
    and show the record's opening text."""
    plain = " ".join((hit.preview or "").split())
    if not hit.snippet:
        return plain, []
    a, b = hit.snippet, hit.snippet2 or ""
    (ao, ac), (bo, bc) = markers, SECOND_MARKERS
    if len(a) != len(b):
        return plain, []
    text, spans, start, pos = [], [], None, 0
    for x, y in zip(a, b):
        if x == y:
            text.append(x)
            pos += 2 if ord(x) > 0xFFFF else 1
        elif (x, y) == (ao, bo) and start is None:
            start = pos
        elif (x, y) == (ac, bc) and start is not None:
            spans.append([start, pos])
            start = None
        else:
            return plain, []
    return "".join(text), ([] if start is not None else spans)


def when(ts):
    """A stored UTC time as local "YYYY-MM-DD HH:MM", or "" when there's none or it can't be read."""
    try:
        return local(ts).strftime("%Y-%m-%d %H:%M") if ts else ""
    except (TypeError, ValueError):
        return ""


def source_label(hit):
    """What the Atlas shows above a hit's title (spec §4.2)."""
    if hit.kind == "chats":
        return f"Conversation · {NAMES.get(hit.fact1 or 'pieces', hit.fact1)}"
    if hit.kind == "events":
        return ("Screen text" if hit.fact1 == "screen" else "Pieces capture") + (f" · {hit.fact2}" if hit.fact2 else "")
    if hit.kind == "summaries":
        return "Pieces summary"
    if hit.kind == "snippets":
        return "Snippet" + (f" · {hit.fact1}" if hit.fact1 else "")
    if hit.kind == "screen":
        return f"Screen · {hit.fact1 or ''}".rstrip(" ·")
    return f"Web history · {hit.fact1 or ''}".rstrip(" ·")


STRETCH_TEXTS = 20


def screen_memory(id_, max_chars):
    if not paths.vault_db().exists():
        return f"No memory with id {id_}"
    v = paths.connect_ro(paths.vault_db())
    try:
        v.execute("BEGIN")
        if not has_table(v, "screen_stretches"):
            return f"No memory with id {id_}"
        if id_.startswith("screentext:"):
            t = v.execute("SELECT stretch_id, created, text FROM screen_text WHERE id=?", (id_,)).fetchone()
            if not t:
                return f"No memory with id {id_}"
            sid, texts, more = t[0], [(t[1], t[2])], 0
        else:
            sid = id_
            texts = v.execute("SELECT created, text FROM screen_text WHERE stretch_id=? ORDER BY created DESC LIMIT ?",
                              (sid, STRETCH_TEXTS)).fetchall()
            more = v.execute("SELECT COUNT(*) FROM screen_text WHERE stretch_id=?", (sid,)).fetchone()[0] - len(texts)
        st = v.execute("SELECT started, ended, app, title FROM screen_stretches WHERE id=?", (sid,)).fetchone()
        if not st:
            return f"No memory with id {id_}"
    finally:
        v.close()
    body = (f"SCREEN  {local(st[0]):%Y-%m-%d %H:%M}–{local(st[1]):%H:%M}\napp: {st[2]}\nwindow: {st[3] or ''}\n\n"
            + ("\n\n".join(f"{local(c):%H:%M}\n{t}" for c, t in texts) if texts else "(timeline only: no text read)"))
    if more > 0:
        body += f"\n\n… {more} more text reads in this window; search finds them by their own ids."
    return body if len(body) <= max_chars else body[:max_chars] + f"\n\n… [truncated, {len(body)} chars total]"


def memory_text(id_, max_chars=20000):
    """Exactly what MCP's get_memory returns (spec §3.1): the whole conversation for a chat, cut at max_chars with
    the usual note. The Atlas reader uses record() instead."""
    if id_.startswith(("screen:", "screentext:")):
        return screen_memory(id_, max_chars)
    with db() as conn:
        if id_.startswith("web:") and has_table(conn, "pages") and (
                r := conn.execute("SELECT day, url, title, profiles FROM pages WHERE id=?", (id_,)).fetchone()):
            seen = conn.execute("SELECT created, profile, synced FROM visits WHERE page_id=? ORDER BY created",
                                (id_,)).fetchall()
            body = (f"WEB PAGE  {r['day']}\n{r['title'] or '(no title)'}  (title as observed at sync)\n{r['url']}\n"
                    f"profiles: {r['profiles']}\n\nvisits:\n" + "\n".join(
                        f"{local(v['created']).strftime('%H:%M')}  {v['profile']}"
                        + ("  (synced from another device)" if v["synced"] else "") for v in seen))
        elif r := conn.execute("SELECT created, name, text FROM summaries WHERE id=?", (id_,)).fetchone():
            body = f"SUMMARY  {r['created']}\n{r['name']}\n\n{r['text']}"
        elif r := conn.execute("SELECT created, window_title, app, url, readable FROM events WHERE id=?", (id_,)).fetchone():
            body = (f"EVENT  {r['created']}\napp: {r['app']}\nwindow: {r['window_title']}\n"
                    f"url: {r['url']}\n\n{r['readable'] or '(no text captured)'}")
        elif r := conn.execute("SELECT conversation_id, conversation_name FROM messages WHERE id=?", (id_,)).fetchone():
            msgs = conn.execute("SELECT created, role, text FROM messages WHERE conversation_id=? ORDER BY created",
                                (r["conversation_id"],)).fetchall()
            body = f"CHAT CONVERSATION\n{r['conversation_name'] or ''}\n\n" + "\n\n".join(f"{(m['created'] or '')[:16]} {m['role']}:\n{m['text']}" for m in msgs)
        elif r := conn.execute("SELECT created, name, language, text FROM snippets WHERE id=?", (id_,)).fetchone():
            body = f"SNIPPET  {r['created']}\n{r['name']} ({r['language']})\n\n```{r['language']}\n{r['text']}\n```"
        else:
            return f"No memory with id {id_}"
    return body if len(body) <= max_chars else body[:max_chars] + f"\n\n… [truncated, {len(body)} chars total]"


CHAT_WINDOW = 20      # messages each side of the match, and per Earlier/Later page (spec §3.1)
TEXT_CAP = 100_000    # characters of record text in one answer
ITEM_SHARE = 20_000   # one chat message's share of a window before it is cut and offered for expansion
CURSOR_MAX = 2000


class BadCursor(ValueError):
    """A cursor the page couldn't have made."""


def encode_cursor(d):
    return base64.urlsafe_b64encode(json.dumps(d, separators=(",", ":")).encode()).decode().rstrip("=")


def decode_cursor(s):
    """Opaque to the page; checked here because it comes back over HTTP."""
    if not isinstance(s, str) or not s or len(s) > CURSOR_MAX:
        raise BadCursor("bad cursor")
    try:
        d = json.loads(base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)))
    except (ValueError, RecursionError):  # deeply nested JSON in a short cursor
        raise BadCursor("bad cursor") from None
    ok = (isinstance(d, dict) and d.get("op") in ("earlier", "later", "expand") and d.get("k") in (*SOURCES, "screentext")
          and isinstance(d.get("i"), str) and isinstance(d.get("g"), str)
          and isinstance(d.get("at" if d.get("op") != "expand" else "off"), int)
          and d.get("at" if d.get("op") != "expand" else "off") >= 0)
    if not ok:
        raise BadCursor("bad cursor")
    return d


def item(kind, id_, created, text, gen, role=None, matched=False, share=ITEM_SHARE, full=None):
    """full: the whole text's length, when text was already cut by the query."""
    text = text or ""
    cut = (len(text) if full is None else (full or 0)) > share
    return {"id": id_, "time": when(created), "role": role, "text": text[:share], "truncated": cut,
            "expand": encode_cursor({"op": "expand", "k": kind, "i": id_, "g": gen, "off": share}) if cut else None,
            "matched": matched}


def window(sizes, start, stop, anchor):
    """The contiguous messages [lo, hi) within [start, stop), grown outward from anchor (later side first), until
    adding one more would pass TEXT_CAP. At least one message is always included."""
    start, stop = max(0, start), min(len(sizes), stop)
    lo = hi = anchor
    total = 0
    while True:
        grew = False
        if hi < stop and not (total and total + sizes[hi] > TEXT_CAP):
            total += sizes[hi]; hi += 1; grew = True
        if lo > start and not (total and total + sizes[lo - 1] > TEXT_CAP):
            total += sizes[lo - 1]; lo -= 1; grew = True
        if not grew:
            return lo, hi


def chat(mid, cursor, gen):
    with db() as conn:
        src = "source" if has_column(conn, "messages", "source") else "NULL AS source"
        head = conn.execute(f"SELECT conversation_id, conversation_name, {src} FROM messages WHERE id=?",
                            (mid,)).fetchone()
        if not head:
            return {"status": "missing"}
        # Lengths only: a long conversation never loads every message's text (Codex plan review, limits).
        msgs = conn.execute("SELECT id, created, role, length(text) AS n FROM messages WHERE conversation_id=? "
                            "ORDER BY created, rowid", (head["conversation_id"],)).fetchall()
        if not msgs:  # e.g. a message with no conversation: nothing to window around
            return {"status": "missing"}
        sizes = [min(m["n"] or 0, ITEM_SHARE) for m in msgs]
        n = len(msgs)
        if cursor is None:
            center = next(i for i, m in enumerate(msgs) if m["id"] == mid)
            lo, hi = window(sizes, center - CHAT_WINDOW, center + CHAT_WINDOW + 1, center)
        elif cursor["op"] == "earlier":
            at = min(cursor["at"], n)
            lo, hi = window(sizes, at - CHAT_WINDOW, at, at)
        else:
            at = min(cursor["at"], n)
            lo, hi = window(sizes, at, at + CHAT_WINDOW, at)
        shown = [m["id"] for m in msgs[lo:hi]]  # at most 2 * CHAT_WINDOW + 1 ids
        texts = dict(conn.execute(f"SELECT id, substr(text, 1, {ITEM_SHARE}) FROM messages WHERE id IN "
                                  f"({','.join('?' * len(shown))})", shown).fetchall()) if shown else {}
    items = [item("chats", m["id"], m["created"], texts.get(m["id"]), gen, role=m["role"], full=m["n"],
                  matched=cursor is None and m["id"] == mid) for m in msgs[lo:hi]]
    cur = lambda op, at: encode_cursor({"op": op, "k": "chats", "i": mid, "g": gen, "at": at})
    return {"status": "ok", "kind": "chats", "id": mid, "title": head["conversation_name"] or "Conversation",
            "source_label": f"Conversation · {NAMES.get(head['source'] or 'pieces', head['source'])}",
            "meta": f"{n} messages", "note": None, "url": None, "code": False, "visits": None, "items": items,
            "earlier": cur("earlier", lo) if lo > 0 else None, "later": cur("later", hi) if hi < n else None}


def full_text(kind, id_):
    """The whole text that an expand cursor walks through, or None when the record is gone."""
    if kind == "screentext":  # screen text reads live in vault.db and may not be indexed yet
        if not paths.vault_db().exists():
            return None
        v = paths.connect_ro(paths.vault_db())
        try:
            if not has_table(v, "screen_text"):
                return None
            r = v.execute("SELECT text FROM screen_text WHERE id=?", (id_,)).fetchone()
        finally:
            v.close()
        return None if r is None else (r[0] or "")
    column = {"chats": ("messages", "text"), "summaries": ("summaries", "text"), "events": ("events", "readable"),
              "snippets": ("snippets", "text")}.get(kind)
    if not column:
        return None
    with db() as conn:
        r = conn.execute(f"SELECT {column[1]} FROM {column[0]} WHERE id=?", (id_,)).fetchone()
    return None if r is None else (r[0] or "")


def expand(kind, id_, off, gen):
    text = full_text(kind, id_)
    if text is None:
        return {"status": "missing"}
    nxt = off + TEXT_CAP
    return {"status": "ok", "text": text[off:nxt],
            "next": encode_cursor({"op": "expand", "k": kind, "i": id_, "g": gen, "off": nxt}) if nxt < len(text) else None}


def base(kind, id_, title, label, meta, items, **extra):
    out = {"status": "ok", "kind": kind, "id": id_, "title": title, "source_label": label, "meta": meta,
           "note": None, "url": None, "code": False, "visits": None, "items": items, "earlier": None, "later": None}
    out.update(extra)
    return out


def single(kind, id_, gen):
    """Every kind but chats and screen stretches."""
    with db() as conn:
        if kind == "summaries":
            r = conn.execute("SELECT created, name, text FROM summaries WHERE id=?", (id_,)).fetchone()
            return r and base(kind, id_, r["name"], "Pieces summary", when(r["created"]),
                              [item(kind, id_, r["created"], r["text"], gen, share=TEXT_CAP)])
        if kind == "snippets":
            r = conn.execute("SELECT created, name, language, text FROM snippets WHERE id=?", (id_,)).fetchone()
            return r and base(kind, id_, r["name"], "Snippet" + (f" · {r['language']}" if r["language"] else ""),
                              when(r["created"]), [item(kind, id_, r["created"], r["text"], gen, share=TEXT_CAP)],
                              code=True)
        if kind == "events":
            src = "source" if has_column(conn, "events", "source") else "'pieces' AS source"
            r = conn.execute(f"SELECT created, app, window_title, url, readable, {src} FROM events WHERE id=?",
                             (id_,)).fetchone()
            if not r:
                return None
            label = ("Screen text" if r["source"] == "screen" else "Pieces capture") + (f" · {r['app']}" if r["app"] else "")
            return base(kind, id_, r["window_title"] or "(no window title)", label, when(r["created"]),
                        [item(kind, id_, r["created"], r["readable"] or "(no text captured)", gen, share=TEXT_CAP)],
                        url=r["url"])
        if kind == "web" and has_table(conn, "pages"):
            r = conn.execute("SELECT day, url, host, title FROM pages WHERE id=?", (id_,)).fetchone()
            if not r:
                return None
            seen = conn.execute("SELECT created, profile, synced FROM visits WHERE page_id=? ORDER BY created",
                                (id_,)).fetchall()
            return base(kind, id_, r["title"] or r["url"], f"Web history · {r['host']}", r["day"], [],
                        url=r["url"], note="Browser history: the page itself isn't saved.",
                        visits=[{"time": when(v["created"]), "profile": v["profile"], "synced": bool(v["synced"])}
                                for v in seen])
    return None


def stretch(id_, gen):
    """A screen stretch, read live from vault.db like MCP's get_memory: app, window, times, newest text reads."""
    if not paths.vault_db().exists():
        return None
    v = paths.connect_ro(paths.vault_db())
    try:
        v.execute("BEGIN")
        if not has_table(v, "screen_stretches"):
            return None
        st = v.execute("SELECT started, ended, app, title FROM screen_stretches WHERE id=?", (id_,)).fetchone()
        if not st:
            return None
        texts = v.execute("SELECT id, created, text FROM screen_text WHERE stretch_id=? ORDER BY created DESC LIMIT ?",
                          (id_, STRETCH_TEXTS)).fetchall()
        more = v.execute("SELECT COUNT(*) FROM screen_text WHERE stretch_id=?", (id_,)).fetchone()[0] - len(texts)
    finally:
        v.close()
    share = TEXT_CAP // max(1, len(texts))  # all the stretch's texts together stay within one answer's cap
    note = ("Timeline only: no text was read in this app." if not texts else
            f"{more} more text reads in this window; search finds them." if more > 0 else None)
    return base("screen", id_, st[3] or st[2], f"Screen · {st[2]}", f"{when(st[0])}–{when(st[1])[-5:]}",
                [item("screentext", t[0], t[1], t[2], gen, share=share) for t in texts], note=note)


def record(kind, id_=None, cursor=None):
    """One record for the Atlas reader (spec §3.1). cursor is a decoded cursor; a cursor made under another
    published state answers {"status": "changed"} and nothing else."""
    gen = generation(published())
    if cursor is not None:
        if cursor["g"] != gen:
            return {"status": "changed"}
        kind, id_ = cursor["k"], cursor["i"]
        if cursor["op"] == "expand":
            return expand(kind, id_, cursor["off"], gen)
    if kind == "chats":
        return chat(id_, cursor, gen)
    found = stretch(id_, gen) if kind == "screen" else single(kind, id_, gen)
    return found or {"status": "missing"}


def freshness():
    """When search was built, and whether screen capture has recorded more since (spec §3.3, §4.2). Never guessed
    from file times: an index without a recorded build time says so."""
    with db() as conn:
        meta = dict(conn.execute("SELECT key, value FROM meta").fetchall()) if has_table(conn, "meta") else {}
    latest = None
    if paths.vault_db().exists():
        v = paths.connect_ro(paths.vault_db())
        try:
            if has_table(v, "screen_stretches"):
                latest = v.execute("SELECT MAX(ended) FROM screen_stretches").fetchone()[0]
        finally:
            v.close()
    built, cutoff = meta.get("built"), meta.get("screen_cutoff")
    return {"built": local(built).strftime("%Y-%m-%dT%H:%M") if built else None,
            "timezone_now": datetime.now().astimezone().strftime("%Z (UTC%z)"),
            "newer_screen": bool(latest and (not cutoff or latest > cutoff))}


def has_rows(conn, kind, event_source):
    table = SOURCES[kind][0]
    if not has_table(conn, table):
        return False
    if kind == "events" and event_source:
        if has_column(conn, "events", "source"):
            return conn.execute("SELECT EXISTS(SELECT 1 FROM events WHERE source=?)", (event_source,)).fetchone()[0] == 1
        return event_source == "pieces" and conn.execute("SELECT EXISTS(SELECT 1 FROM events)").fetchone()[0] == 1
    return conn.execute(f"SELECT EXISTS(SELECT 1 FROM {table})").fetchone()[0] == 1


def sources_present():
    """Which chips the Atlas shows: a source with no records gets no chip (spec §4.2)."""
    with db() as conn:
        return {chip: any(has_rows(conn, k, src) for k in kinds) for chip, (kinds, src) in CHIPS.items() if kinds}
