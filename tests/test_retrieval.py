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
