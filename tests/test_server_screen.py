"""MCP tools over screen data (spec §6, F2, R7, P8, P9)."""
import numpy as np
import pytest

S = lambda h, m=0: f"2026-10-12T{h:02d}:{m:02d}:00.000000Z"


class FakeModel:
    def encode(self, texts, **_):
        return np.ones((len(texts), 4), dtype=np.float32)


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setenv("INKVAULT_HOME", str(tmp_path))
    from inkvault import embed, export, server
    monkeypatch.setattr(embed, "load_model", FakeModel)
    monkeypatch.setattr(server, "_model", None)
    v = export.open_vault()
    v.executemany("INSERT INTO screen_stretches VALUES (?,?,?,?,?)", [
        ("screen:a", S(9), S(9, 30), "Discord.exe", "Retry chat"),
        ("screen:b", S(10), S(10, 5), "chrome.exe", "Docs page"),
    ])
    v.executemany("INSERT INTO screen_text VALUES (?,?,?,?,?)",
                  [(f"screentext:{i}", "screen:a", S(9, i), f"backoff note number {i:02d} in the chat", str(i))
                   for i in range(25)])
    v.commit()
    yield v
    v.close()


def test_timeline_screen_section_without_any_index(vault):
    from inkvault import paths, server
    assert not paths.search_db().exists()
    out = server.timeline("2026-10-12")
    assert "Screen" in out and "[screen] Discord.exe: Retry chat" in out and "[screen] chrome.exe: Docs page" in out


def test_screen_section_survives_many_older_items(vault):                        # P8
    import sqlite3
    from inkvault import paths, server
    s = sqlite3.connect(paths.search_db())                       # a hand-made index with 80 older summaries
    s.execute("CREATE TABLE summaries (id TEXT PRIMARY KEY, created TEXT, name TEXT, text TEXT, event_ids TEXT)")
    s.executemany("INSERT INTO summaries VALUES (?,?,?,?,?)",
                  [(f"s{i}", f"2026-10-12T00:{i % 60:02d}:00Z", "old", "x", "[]") for i in range(80)])
    s.commit(); s.close()
    out = server.timeline("2026-10-12", limit=10)
    assert "[screen] chrome.exe: Docs page" in out and out.count("old") == 10


def test_limits_and_ranges_are_validated(vault):
    from inkvault import server
    assert "limit" in server.timeline("2026-10-12", limit=0)
    assert "Bad range" in server.timeline("2026-10-12", until="2026-10-11")
    assert "Bad range" in server.search_memories("docs", since="2026-10-12", until="2026-10-11")


def test_screentext_id_returns_that_row_and_stretch_ids_are_bounded(vault):     # P8
    from inkvault import server
    one = server.get_memory("screentext:3")
    assert "backoff note number 03" in one and "Retry chat" in one and "number 04" not in one
    many = server.get_memory("screen:a")
    assert "number 24" in many and "number 04" not in many and "5 more" in many


def test_a_vault_without_screen_tables(tmp_path, monkeypatch):
    import sqlite3
    monkeypatch.setenv("INKVAULT_HOME", str(tmp_path))
    from inkvault import paths, server
    sqlite3.connect(paths.vault_db()).execute("CREATE TABLE meta (key, value)")
    assert "No memory" in server.get_memory("screen:x") and "Screen" not in server.timeline("2026-10-12")


def test_a_vault_that_is_not_a_database_is_unavailable(tmp_path, monkeypatch):
    monkeypatch.setenv("INKVAULT_HOME", str(tmp_path))
    from inkvault import paths, server
    paths.vault_db().write_bytes(b"this is not a sqlite database" * 100)
    assert server.timeline("2026-10-12") == server.UNAVAILABLE


def test_a_removal_during_a_live_read_is_never_answered(vault, monkeypatch):    # P12
    from inkvault import forget, server
    real = server.screen_lines

    def racing(*a):
        out = real(*a)
        from inkvault import paths
        forget.screen_request("screen-app", "chrome.exe")      # a removal starts during the read…
        paths.rebuild_marker().unlink(missing_ok=True)         # …and its rebuild finishes before the answer
        return out
    monkeypatch.setattr(server, "screen_lines", racing)
    assert server.timeline("2026-10-12") == server.CHANGED   # only the removal generation can catch this


def test_search_screen_keyword_meaning_and_one_hit_per_stretch(vault):
    from inkvault import index, server
    vault.execute("UPDATE screen_stretches SET title='Retry backoff' WHERE id='screen:a'"); vault.commit()
    index.build()
    out = server.search_memories("docs", source="screen", mode="keyword")
    assert "[screen] screen:b" in out and "search covers screen activity up to" in out
    assert "keyword-only" in server.search_memories("docs", source="screen", mode="meaning")
    hits = [l for l in server.search_memories("backoff", mode="keyword", limit=50).splitlines() if l.startswith("[")]
    assert not any(l.startswith("[screen] screen:a") for l in hits)              # the OCR rows win
