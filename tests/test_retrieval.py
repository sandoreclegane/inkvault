"""retrieval.search: the structures behind MCP's text and the Atlas's results (spec §3.1)."""
import sqlite3

import pytest

import atlasdata


@pytest.fixture
def vault(tmp_path, monkeypatch):
    return atlasdata.make_vault(tmp_path, monkeypatch)


def test_hits_are_structures_with_mcp_order(vault):
    from inkvault import retrieval
    r = retrieval.search("backoff", mode="keyword")
    assert r.error is None
    assert ("snippets", "x1") in [(h.kind, h.id) for h in r.hits]
    assert any("[" in (h.snippet or "") for h in r.hits)  # MCP markers by default


def test_atlas_markers_give_spans_in_utf16_units(vault, monkeypatch):
    from inkvault import index, paths, retrieval
    v = sqlite3.connect(paths.vault_db())
    v.execute("UPDATE raw_records SET raw=json_set(raw, '$.text', ?) WHERE id='a1'", ("🙂🙂 lighthouse beacon",))
    v.commit(); v.close()
    index.build()
    r = retrieval.search("beacon", kinds=["summaries"], markers=retrieval.ATLAS_MARKERS, mode="keyword")
    text, spans = retrieval.excerpt(r.hits[0])
    assert "\ue000" not in text and "\ue001" not in text
    start = len(text[:text.index("beacon")].encode("utf-16-le")) // 2
    assert [start, start + len("beacon")] in spans


@pytest.mark.parametrize("raw", [
    "a \ue000 lone beacon",                    # unpaired literal marker
    "\ue000fake\ue001 beacon",                 # balanced literal pair (Codex plan review #5)
    "\ue001crossed\ue000 beacon",              # crossed
    "\ue000\ue000nested\ue001 beacon \ue001",  # nested around the real match (private-use characters are word
                                               # characters to FTS, so "beacon\ue001" would be one token: keep a space)
    "\ue002second\ue003 pair beacon",          # the second marker pair, literally
])
def test_marker_characters_in_the_text_are_kept_and_never_highlighted(vault, raw):
    from inkvault import index, paths, retrieval
    v = sqlite3.connect(paths.vault_db())
    v.execute("UPDATE raw_records SET raw=json_set(raw, '$.text', ?) WHERE id='a1'", (raw,))
    v.commit(); v.close()
    index.build()
    r = retrieval.search("beacon", kinds=["summaries"], markers=retrieval.ATLAS_MARKERS, mode="keyword")
    text, spans = retrieval.excerpt(r.hits[0])
    assert [text[a:b] for a, b in spans] == ["beacon"]  # all characters here are in the BMP: UTF-16 = Python offsets
    for ch in "\ue000\ue001\ue002\ue003":
        assert text.count(ch) == raw.count(ch)  # the record's own characters survive


def test_meaning_only_hits_have_no_spans(vault):
    from inkvault import retrieval
    r = retrieval.search("harbor", mode="meaning", markers=retrieval.ATLAS_MARKERS)
    assert r.hits and all(h.snippet is None for h in r.hits)
    assert retrieval.excerpt(r.hits[0])[1] == []


def test_event_source_separates_captures_from_screen_text(vault):
    from inkvault import retrieval
    captures = retrieval.search("backoff", kinds=["events"], event_source="pieces", mode="keyword")
    screen = retrieval.search("backoff", kinds=["screen", "events"], event_source="screen", mode="keyword")
    assert {h.id for h in captures.hits} == {"e2"}
    assert {h.id for h in screen.hits} and all(h.id.startswith(("screentext:", "screen:")) for h in screen.hits)


def test_paging_with_offset_and_more(vault):
    from inkvault import retrieval
    whole = retrieval.search("backoff", mode="keyword", limit=50)
    first = retrieval.search("backoff", mode="keyword", limit=2)
    second = retrieval.search("backoff", mode="keyword", limit=2, offset=2)
    assert first.more is True
    assert [h.id for h in first.hits + second.hits] == [h.id for h in whole.hits][:4]


def test_atlas_needs_the_model_folder_for_meaning(vault, monkeypatch):
    from inkvault import embed, paths, retrieval
    paths.model_dir().rmdir()
    monkeypatch.setattr(embed, "load_model", lambda: pytest.fail("the Atlas must never load (or download) a model"))
    r = retrieval.search("harbor", require_model=True, markers=retrieval.ATLAS_MARKERS)
    assert r.keyword_only is True and r.error is None


def test_punctuation_only_query_is_no_matches(vault):
    from inkvault import retrieval
    r = retrieval.search("!!!", markers=retrieval.ATLAS_MARKERS, require_model=True)
    assert r.error is None and r.hits == []


def test_search_on_an_old_index(vault):
    from inkvault import paths, retrieval
    db = sqlite3.connect(paths.search_db())
    db.executescript("DROP TABLE pages; DROP TABLE pages_fts; DROP INDEX events_source;"
                     "ALTER TABLE events DROP COLUMN source;")
    db.commit(); db.close()
    r = retrieval.search("backoff", markers=retrieval.ATLAS_MARKERS, require_model=True)
    assert r.error is None and r.hits
    assert all(retrieval.source_label(h) for h in r.hits)


def test_labels_and_times(vault):
    from inkvault import retrieval
    hits = retrieval.search("backoff", mode="keyword", limit=50).hits
    labels = {h.kind: retrieval.source_label(h) for h in hits}
    assert labels["snippets"] == "Snippet · ts"
    assert labels["web"].startswith("Web history · docs.example.com")
    assert retrieval.when("2026-02-27T15:05:00Z") == "2026-02-27 15:05"  # utc_times pins local to UTC
    assert retrieval.when(None) == "" and retrieval.when("garbage") == ""


def test_guarded_call_reports_status_and_generation(vault):
    from inkvault import paths, retrieval
    answer, gen = retrieval.guarded_call(lambda: 42)
    assert answer == 42 and len(gen) == 16
    paths.rebuild_marker().write_text("x")
    with pytest.raises(retrieval.Refused) as e:
        retrieval.guarded_call(lambda: 42)
    assert e.value.status == "rebuilding"


def test_mcp_limit_zero_keeps_one_hit_like_v030(vault):
    from inkvault import server
    out = server.search_memories("backoff", mode="keyword", limit=0)
    assert sum(1 for line in out.splitlines() if line.startswith("[")) == 1


def test_index_records_when_it_was_built(vault):
    from inkvault import paths
    db = sqlite3.connect(paths.search_db())
    built = db.execute("SELECT value FROM meta WHERE key='built'").fetchone()[0]
    db.close()
    assert built.endswith("Z") and len(built) == 27


def test_freshness_says_when_and_whether_screen_is_newer(vault):
    from inkvault import paths, retrieval
    f = retrieval.freshness()
    assert f["built"] and f["newer_screen"] is False and f["timezone_now"]
    v = sqlite3.connect(paths.vault_db())
    v.execute("INSERT INTO screen_stretches VALUES ('screen:z','2026-02-27T23:00:00.000000Z',"
              "'2026-02-27T23:30:00.000000Z','Notes.exe','later')")
    v.commit(); v.close()
    assert retrieval.freshness()["newer_screen"] is True


def test_chips_show_only_sources_with_records(vault):
    from inkvault import retrieval
    assert retrieval.sources_present() == {"conversations": True, "summaries": True, "captures": True,
                                           "snippets": True, "screen": True, "web": True}


def test_freshness_and_chips_on_an_old_index(vault):
    from inkvault import paths, retrieval
    db = sqlite3.connect(paths.search_db())
    db.executescript("DELETE FROM meta WHERE key='built'; DROP TABLE pages; DROP TABLE pages_fts;"
                     "DROP INDEX events_source; ALTER TABLE events DROP COLUMN source;")
    db.commit(); db.close()
    assert retrieval.freshness()["built"] is None
    chips = retrieval.sources_present()
    assert chips["web"] is False and chips["captures"] is True


def add_raw(*records):
    from inkvault import index, paths
    v = sqlite3.connect(paths.vault_db())
    v.executemany("INSERT INTO raw_records VALUES (?,?,?)", records)
    v.commit(); v.close()
    index.build()


def test_one_huge_message_does_not_slow_a_search(vault):
    import json
    import time
    from inkvault import retrieval
    word = "lighthouse "
    big = word * 31819   # 350,009 characters, ~32,000 matches
    add_raw(("conversation", "cbig", json.dumps({"id": "cbig", "name": "Huge"})),
            ("message", "mbig", json.dumps({"id": "mbig", "created": {"value": "2026-03-02T10:00:00Z"}, "role": "USER",
                                            "conversation": {"id": "cbig"}, "fragment": {"string": {"raw": big}}})))
    t = time.perf_counter()
    r = retrieval.search("lighthouse", markers=retrieval.ATLAS_MARKERS, require_model=True)
    took = time.perf_counter() - t
    print(f"350k-char search: {took:.3f}s")
    assert took < 2
    hit = next(h for h in r.hits if h.id == "mbig")
    assert hit.snippet == "" and retrieval.excerpt(hit)[1] == []   # a keyword match with no spans
    mcp = next((h for h in retrieval.search("lighthouse", mode="keyword", limit=50).hits if h.id == "mbig"), None)
    assert mcp is None or "[" in mcp.snippet                       # MCP still makes its snippet


def test_a_model_that_cannot_load_degrades_to_keyword_only(vault, monkeypatch):
    from inkvault import embed, retrieval

    def broken():
        raise OSError("model folder is damaged")
    monkeypatch.setattr(embed, "load_model", broken)
    r = retrieval.search("backoff", markers=retrieval.ATLAS_MARKERS, require_model=True)
    assert r.error is None and r.keyword_only is True and r.hits
    with pytest.raises(OSError):                        # MCP keeps its v0.3.0 behaviour
        retrieval.search("backoff")


# --- retrieval.browse: everything in a date range, no query ---------------------------------------------------------

def ids(r):
    return [(h.kind, h.id) for h in r.hits]


def test_browse_lists_a_day_oldest_first_with_no_snippets(vault):
    from inkvault import retrieval
    r = retrieval.browse("2026-02-27T00:00:00.000000Z", "2026-02-28T00:00:00.000000Z", limit=50)
    assert r.error is None and r.more is False and r.keyword_only is False
    assert [h.created for h in r.hits] == sorted(h.created for h in r.hits)
    assert ("events", "e2") in ids(r) and ("chats", "m1") in ids(r) and ("screen", "screen:b") in ids(r)
    assert ("events", "e1") not in ids(r)  # the day before
    assert all(h.snippet is None and h.snippet2 is None and h.browsed for h in r.hits)
    assert all(len(h.preview or "") <= 300 for h in r.hits)
    assert retrieval.excerpt(r.hits[0])[1] == []


def test_browse_range_edges_are_start_inclusive_end_exclusive(vault):
    from inkvault import retrieval
    # e2 is at 15:00:00Z exactly; the end bound is exclusive, the start bound inclusive
    def got(s, u):
        return ids(retrieval.browse(s, u, kinds=["events"], event_source="pieces"))
    assert got("2026-02-27T15:00:00.000000Z", "2026-02-27T15:00:01.000000Z") == [("events", "e2")]
    assert got("2026-02-27T14:00:00.000000Z", "2026-02-27T15:00:00.000000Z") == []
    assert got("2026-02-27T15:00:00.000000Z", "") == [("events", "e2")]


def test_browse_a_daylight_saving_day_through_day_bounds(vault):
    from zoneinfo import ZoneInfo
    from inkvault import atlasserve, index, paths, retrieval
    ny = ZoneInfo("America/New_York")  # 2026-03-08 is 23 hours long: 05:00Z to 04:00Z the next day
    since, until = atlasserve.day_bounds("2026-03-08", "2026-03-08", tz=ny)
    assert (since, until) == ("2026-03-08T05:00:00.000000Z", "2026-03-09T04:00:00.000000Z")
    v = sqlite3.connect(paths.vault_db())
    for n, t in enumerate(["2026-03-08T04:59:59Z", "2026-03-08T05:00:00Z", "2026-03-09T03:59:59Z",
                           "2026-03-09T04:00:00Z"]):
        v.execute("INSERT INTO raw_records VALUES (?,?,?)",
                  atlasdata.rec("summary", f"dst{n}", name=f"Day {n}", created={"value": t}))
    v.commit()
    v.close()
    index.build()
    r = retrieval.browse(since, until, kinds=["summaries"], limit=50)
    assert [h.id for h in r.hits] == ["dst1", "dst2"]


def test_browse_chips_filter_like_search(vault):
    from inkvault import retrieval
    day = ("2026-02-26T00:00:00.000000Z", "2026-03-01T00:00:00.000000Z")
    for chip, (kinds, event_source) in retrieval.CHIPS.items():
        r = retrieval.browse(*day, kinds=kinds, event_source=event_source, limit=50)
        assert r.error is None
        if kinds:
            assert {h.kind for h in r.hits} <= set(kinds)
    cap = retrieval.browse(*day, kinds=["events"], event_source="pieces", limit=50)
    assert {h.id for h in cap.hits} == {"e1", "e2"}
    scr = retrieval.browse(*day, kinds=["screen", "events"], event_source="screen", limit=50)
    assert scr.hits and all(h.id.startswith(("screen:", "screentext:")) for h in scr.hits)
    assert {h.kind for h in retrieval.browse(*day, kinds=["web"], limit=50).hits} == {"web"}


def test_browse_shows_one_hit_per_stretch_and_the_ocr_row_wins(vault):
    from inkvault import retrieval
    day = ("2026-02-27T00:00:00.000000Z", "2026-02-28T00:00:00.000000Z")
    r = retrieval.browse(*day, kinds=["screen", "events"], event_source="screen", limit=50)
    assert ("screen", "screen:a") not in ids(r)               # its text is indexed as events rows: those win
    assert sum(1 for h in r.hits if h.kind == "events") == 1  # three text reads, one stretch, one hit
    assert ("screen", "screen:b") in ids(r)                   # a stretch without text still shows
    only = retrieval.browse(*day, kinds=["screen"], limit=50)  # no events kind: nothing to win, the stretches show
    assert {h.id for h in only.hits} == {"screen:a", "screen:b"}


def test_browse_pages_without_gaps_or_repeats_and_reports_more(vault):
    from inkvault import retrieval
    day = ("2026-02-25T00:00:00.000000Z", "2026-03-01T00:00:00.000000Z")
    whole = ids(retrieval.browse(*day, limit=500))
    assert len(whole) > 6
    got, offset, pages = [], 0, 0
    while True:
        r = retrieval.browse(*day, limit=3, offset=offset)
        got += ids(r)
        pages += 1
        assert r.more == (offset + 3 < len(whole))
        if not r.more:
            break
        offset += 3
    assert got == whole and pages == -(-len(whole) // 3)
    assert retrieval.browse(*day, limit=len(whole)).more is False


def test_browse_walks_batches_across_kinds(vault, monkeypatch):
    from inkvault import retrieval
    day = ("2026-02-25T00:00:00.000000Z", "2026-03-01T00:00:00.000000Z")
    whole = ids(retrieval.browse(*day, limit=500))
    monkeypatch.setattr(retrieval, "BROWSE_BATCH", 1)  # every kind refilled one row at a time
    assert ids(retrieval.browse(*day, limit=500)) == whole


def test_browse_leaves_out_what_was_forgotten(vault):
    from inkvault import forget, retrieval
    day = ("2026-02-27T00:00:00.000000Z", "2026-02-28T00:00:00.000000Z")
    assert any(k == "web" for k, _ in ids(retrieval.browse(*day, limit=50)))
    forget.forget_profile("chrome/Default")
    assert not any(k == "web" for k, _ in ids(retrieval.browse(*day, limit=50)))


def test_browse_errors_like_search(vault):
    from inkvault import paths, retrieval
    assert "Bad date" in retrieval.browse("garbage", "").error
    assert retrieval.browse("2026-02-28", "2026-02-25").error
    assert retrieval.browse("2026-02-25", "2026-02-26", kinds=["nope"]).error
    paths.search_db().unlink()
    r = retrieval.browse("2026-02-25", "2026-02-26")
    assert r.empty_index and r.error


def _dense_ocr_index(n):
    """Add n screen stretches, each with one OCR events row, two minutes apart through 2026-03 onward."""
    from datetime import datetime, timedelta, timezone
    from inkvault import paths
    t0 = datetime(2026, 3, 1, tzinfo=timezone.utc)
    iso = lambda d: d.strftime("%Y-%m-%dT%H:%M:%S.000000Z")
    c = sqlite3.connect(paths.search_db())
    stretches = [(f"screen:z{i:05d}", iso(t0 + timedelta(minutes=2 * i)), iso(t0 + timedelta(minutes=2 * i + 1)), "App", f"T{i}")
                 for i in range(n)]
    events = [(f"zev{i:05d}", iso(t0 + timedelta(minutes=2 * i)), "App", f"T{i}", None, "text", "screen", f"screen:z{i:05d}")
              for i in range(n)]
    c.executemany("INSERT INTO screen_stretches VALUES (?,?,?,?,?)", stretches)
    c.executemany("INSERT INTO events (id, created, app, window_title, url, readable, source, stretch_id) VALUES (?,?,?,?,?,?,?,?)", events)
    c.commit(); c.close()
    return ("2026-03-01", "2027-03-01")


@pytest.mark.parametrize("indexed", [True, False])
def test_browse_screen_suppression_work_is_bounded_by_the_page(vault, monkeypatch, indexed):
    """Finding 1: 5,000 OCR-backed stretches must not be walked to return the first screen-chip page."""
    import time
    from inkvault import paths, retrieval
    bounds = _dense_ocr_index(5000)
    if not indexed:                       # an index built before events_stretch existed: same answer, only slower
        c = sqlite3.connect(paths.search_db()); c.execute("DROP INDEX IF EXISTS events_stretch"); c.commit(); c.close()
    fetched = {"rows": 0}
    real = retrieval.db

    from contextlib import contextmanager

    @contextmanager
    def counting():
        with real() as conn:
            class Cursor:
                def __init__(self, cur): self.cur = cur
                def fetchall(self):
                    rows = self.cur.fetchall(); fetched["rows"] += len(rows); return rows
                def __getattr__(self, name): return getattr(self.cur, name)
                def __iter__(self): return iter(self.cur)

            class Conn:
                def execute(self, *a): return Cursor(conn.execute(*a))
                def __getattr__(self, name): return getattr(conn, name)
            yield Conn()
    monkeypatch.setattr(retrieval, "db", counting)
    t = time.perf_counter()
    r = retrieval.browse(*bounds, kinds=["screen", "events"], event_source="screen", limit=20)
    took = time.perf_counter() - t
    print(f"indexed={indexed} took={took:.3f}s rows_fetched={fetched['rows']}")
    assert len(r.hits) == 20 and r.more and all(h.kind == "events" for h in r.hits)
    assert fetched["rows"] <= 2 * retrieval.BROWSE_BATCH + 20   # one batch per kind, not the whole range
    assert took < (5 if indexed else 60)
