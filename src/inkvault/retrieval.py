"""Search over the InkVault index, for MCP (server.py) and the Memory Atlas (atlasserve.py).

Answers are structures, not text (spec §3.1): server.py formats them exactly as it always has, and the Atlas sends
them to the page as JSON. Everything here is read-only. Importing it starts nothing and changes no process setting;
each process that answers questions sets HF_HUB_OFFLINE itself.
"""
import functools
import hashlib
import re
import sqlite3
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
ATLAS_MARKERS = ("\ue000", "\ue001")  # the Atlas's markers (private-use characters)\u2026
SECOND_MARKERS = ("\ue002", "\ue003")  # \u2026and a second pair: each snippet is made with both, so a character that
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
    """opts (Atlas only; MCP passes none): markers=(open, close), event_source="pieces"|"screen"."""
    table, fts, _, col, _, _ = SOURCES[kind]
    o, c = opts.get("markers", MCP_MARKERS)
    second = ""
    if (o, c) != MCP_MARKERS:
        p, q = SECOND_MARKERS
        second = f", snippet({fts}, {col}, '{p}', '{q}', ' … ', 40) AS snip2"
    params = [fts_query(query)]
    sql = (f"SELECT t.id, snippet({fts}, {col}, '{o}', '{c}', ' … ', 40) AS snip{second} FROM {fts} JOIN {table} t "
           f"ON t.rowid = {fts}.rowid WHERE {fts} MATCH ?" + range_filter(kind, since, until, params))
    if kind == "events" and opts.get("event_source") and has_column(conn, "events", "source"):
        sql += " AND t.source = ?"
        params.append(opts["event_source"])
    sql += f" ORDER BY bm25({fts}) LIMIT ?"
    params.append(CANDIDATES)
    return conn.execute(sql, params).fetchall()


def meaning_ranked(kinds, query):
    global _model
    v = vectors()
    if v is None:
        return []
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
    if markers != MCP_MARKERS:
        opts["markers"] = markers
    if event_source:
        opts["event_source"] = event_source
    meaning = mode != "keyword" and (meaning_ready() or not require_model)
    scores, snippets, snippets2 = {}, {}, {}  # reciprocal rank fusion: each list adds 1/(k + rank) per hit
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
                    snippets[key] = r["snip"]
                    if "snip2" in r.keys():
                        snippets2[key] = r["snip2"]
        if meaning:
            for rank, key in enumerate(meaning_ranked([k for k in kinds if k != "screen"], query)):
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
            out.append(Hit(kind, id_, r["created"], r["title"], r["display"], snippets.get((kind, id_)), r["preview"],
                           r["fact1"], r["fact2"], snippets2.get((kind, id_))))
            if len(out) > offset + limit:
                break
        notes = []
        if mode == "meaning" and "screen" in kinds:
            notes.append("(screen stretches are keyword-only; they weren't searched by meaning)")
        if {"screen", "events"} & set(kinds) and has_table(conn, "meta") and (
                c := conn.execute("SELECT value FROM meta WHERE key='screen_cutoff'").fetchone()):
            notes.append(f"(search covers screen activity up to {local(c[0]):%Y-%m-%d %H:%M}; "
                         "timeline() shows it live)")
    return SearchResult(hits=out[offset:offset + limit], more=len(out) > offset + limit,
                        keyword_only=mode != "keyword" and not meaning_ready(), notes=notes)


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
