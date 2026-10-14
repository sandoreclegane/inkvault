"""The Atlas reader (spec §3.1): chat windows around the match, paging, advancing expansion, and every kind."""
import base64
import json
import sqlite3

import pytest

import atlasdata


@pytest.fixture
def vault(tmp_path, monkeypatch):
    return atlasdata.make_vault(tmp_path, monkeypatch)


def add_conversation(n, size=10, same_time=False, big=None):
    """Conversation c9 with n messages m9_000..; message `big` (an index) is 300,000 characters."""
    from inkvault import index, paths
    v = sqlite3.connect(paths.vault_db())
    v.execute("INSERT INTO raw_records VALUES ('conversation','c9',?)", (json.dumps({"id": "c9", "name": "Ninety"}),))
    for i in range(n):
        text = ("x" * 300_000) if i == big else f"msg {i:03d} " + "y" * size
        created = "2026-03-01T10:00:00Z" if same_time else f"2026-03-01T10:{i // 60:02d}:{i % 60:02d}Z"
        v.execute("INSERT INTO raw_records VALUES ('message',?,?)", (f"m9_{i:03d}", json.dumps({
            "id": f"m9_{i:03d}", "created": {"value": created}, "role": "USER" if i % 2 == 0 else "ASSISTANT",
            "conversation": {"id": "c9"}, "fragment": {"string": {"raw": text}}})))
    v.commit(); v.close()
    index.build()


def ids(r):
    return [it["id"] for it in r["items"]]


def test_a_chat_opens_at_the_match_with_twenty_each_side(vault):
    from inkvault import retrieval
    add_conversation(100)
    r = retrieval.record("chats", "m9_050")
    assert r["status"] == "ok" and r["title"] == "Ninety"
    assert ids(r) == [f"m9_{i:03d}" for i in range(30, 71)]
    assert [it["matched"] for it in r["items"]].count(True) == 1
    earlier = retrieval.record(None, cursor=retrieval.decode_cursor(r["earlier"]))
    later = retrieval.record(None, cursor=retrieval.decode_cursor(r["later"]))
    assert ids(earlier) == [f"m9_{i:03d}" for i in range(10, 30)]
    assert ids(later) == [f"m9_{i:03d}" for i in range(71, 91)]


def test_the_window_stays_under_the_text_cap(vault):
    from inkvault import retrieval
    add_conversation(60, size=15_000)
    r = retrieval.record("chats", "m9_030")
    assert sum(len(it["text"]) for it in r["items"]) <= retrieval.TEXT_CAP
    assert "m9_030" in ids(r) and r["earlier"] and r["later"]


def test_a_huge_message_expands_to_the_end_in_order(vault):
    from inkvault import retrieval
    add_conversation(3, big=1)
    r = retrieval.record("chats", "m9_001")
    item = next(it for it in r["items"] if it["id"] == "m9_001")
    assert item["truncated"] and len(item["text"]) == retrieval.ITEM_SHARE
    text, cursor = item["text"], item["expand"]
    while cursor:
        part = retrieval.record(None, cursor=retrieval.decode_cursor(cursor))
        assert part["status"] == "ok"
        text, cursor = text + part["text"], part["next"]
    assert text == "x" * 300_000


def test_same_timestamps_keep_insertion_order(vault):
    from inkvault import retrieval
    add_conversation(5, same_time=True)
    assert ids(retrieval.record("chats", "m9_002")) == [f"m9_{i:03d}" for i in range(5)]


def test_a_cursor_from_before_a_change_is_refused(vault):
    from inkvault import paths, retrieval
    add_conversation(100)
    r = retrieval.record("chats", "m9_050")
    atlasdata.bump(paths.vectors())  # a new published state
    assert retrieval.record(None, cursor=retrieval.decode_cursor(r["earlier"]))["status"] == "changed"


def test_bad_cursors_are_rejected():
    from inkvault import retrieval
    for bad in ("", "not base64!", retrieval.encode_cursor({"op": "fly"}), "A" * 3000, None, 5):
        with pytest.raises(retrieval.BadCursor):
            retrieval.decode_cursor(bad)


def test_a_deeply_nested_cursor_is_refused_not_a_crash():
    from inkvault import retrieval
    s = base64.urlsafe_b64encode(("[" * 1400).encode()).decode()  # short enough to pass the length check
    assert len(s) < retrieval.CURSOR_MAX
    with pytest.raises(retrieval.BadCursor):
        retrieval.decode_cursor(s)


def test_a_message_with_no_conversation_is_missing(vault):
    from inkvault import paths, retrieval
    add_conversation(5)
    db = sqlite3.connect(paths.search_db())
    db.execute("UPDATE messages SET conversation_id=NULL WHERE id='m9_002'")
    db.commit(); db.close()
    assert retrieval.record("chats", "m9_002") == {"status": "missing"}


def test_each_kind_reads(vault):
    from inkvault import retrieval
    assert retrieval.record("snippets", "x1")["code"] is True
    s = retrieval.record("summaries", "s5")
    assert "continuity audit" in s["items"][0]["text"] and s["source_label"] == "Pieces summary"
    e = retrieval.record("events", "e1")
    assert e["url"] == "https://dashboard.example.com/x" and e["source_label"] == "Pieces capture · chrome.exe"
    w = retrieval.record("web", atlasdata.web_id())
    assert w["note"].startswith("Browser history") and len(w["visits"]) == 2 and w["visits"][1]["synced"]
    sc = retrieval.record("screen", "screen:a")
    assert sc["source_label"] == "Screen · Discord.exe" and len(sc["items"]) == 3
    st = retrieval.record("events", "screentext:1")
    assert st["source_label"].startswith("Screen text")
    assert retrieval.record("chats", "nope")["status"] == "missing"
    assert retrieval.record("web", "web:nope")["status"] == "missing"


def test_memory_text_is_get_memory(vault):
    from inkvault import retrieval, server
    for id_ in ("x1", "s5", "e1", "m1", "m20", "screen:a", "screentext:1", atlasdata.web_id(), "nope"):
        assert retrieval.memory_text(id_) == server.get_memory(id_)


def test_a_stretch_stays_within_the_answer_cap_and_its_texts_expand(vault):
    from inkvault import paths, retrieval
    v = sqlite3.connect(paths.vault_db())
    v.executemany("INSERT INTO screen_text VALUES (?,?,?,?,?)",
                  [(f"screentext:big{i}", "screen:b", atlasdata.S(10, i), "z" * 30_000, str(i)) for i in range(20)])
    v.commit(); v.close()      # newer than the search file: not in search.db
    r = retrieval.record("screen", "screen:b")
    assert r["status"] == "ok" and len(r["items"]) == 20
    assert sum(len(it["text"]) for it in r["items"]) <= retrieval.TEXT_CAP
    first = r["items"][0]
    assert first["truncated"] and first["expand"]
    cur = retrieval.decode_cursor(first["expand"])
    assert cur["k"] == "screentext"
    more = retrieval.record("screen", cursor=cur)
    assert more["status"] == "ok" and more["text"] == "z" * 25_000   # the rest of the 30,000, from the first cut on


def test_screentext_is_not_a_record_kind(vault):
    from inkvault import atlasserve
    with pytest.raises(atlasserve.BadRequest):
        atlasserve.api_record({"kind": "screentext", "id": "screentext:1", "generation": "x"})
