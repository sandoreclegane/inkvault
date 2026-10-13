"""Search build with screen data (spec §6, F7)."""
import sqlite3

import numpy as np
import pytest


class FakeModel:
    def encode(self, texts, **_):
        return np.ones((len(texts), 4), dtype=np.float32)


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setenv("INKVAULT_HOME", str(tmp_path))
    from inkvault import embed, export
    monkeypatch.setattr(embed, "load_model", FakeModel)
    v = export.open_vault()
    v.execute("INSERT INTO screen_stretches VALUES ('screen:a','2026-10-12T09:00:00.000000Z',"
              "'2026-10-12T09:30:00.000000Z','Discord.exe','Retry chat')")
    v.execute("INSERT INTO screen_stretches VALUES ('screen:b','2026-10-12T10:00:00.000000Z',"
              "'2026-10-12T10:05:00.000000Z','chrome.exe','Docs page')")
    v.execute("INSERT INTO screen_text VALUES ('screentext:1','screen:a','2026-10-12T09:01:00.000000Z',"
              "'exponential backoff helper discussion','h')")
    v.commit()
    yield v
    v.close()


def test_screen_rows_land_in_search(vault):
    from inkvault import index, paths
    index.build()
    db = sqlite3.connect(paths.search_db())
    assert db.execute("SELECT source, stretch_id, app, window_title FROM events WHERE id='screentext:1'"
                      ).fetchone() == ("screen", "screen:a", "Discord.exe", "Retry chat")
    assert db.execute("SELECT s.id FROM screen_stretches s JOIN stretches_fts f ON f.rowid = s.rowid "
                      "WHERE stretches_fts MATCH 'docs'").fetchone() == ("screen:b",)
    assert dict(db.execute("SELECT key, value FROM meta"))["screen_cutoff"] == "2026-10-12T10:05:00.000000Z"
    assert db.execute("SELECT COUNT(*) FROM events WHERE source IS NULL").fetchone()[0] == 0


def test_a_commit_during_build_is_wholly_out(vault, monkeypatch):
    from inkvault import export, history, index, paths
    real = history.index_into

    def during(raw, db):
        w = export.open_vault()
        w.execute("INSERT INTO screen_stretches VALUES ('screen:late','2026-10-12T11:00:00.000000Z',"
                  "'2026-10-12T11:00:02.000000Z','Notion.exe','Late')")
        w.execute("INSERT INTO screen_text VALUES ('screentext:late','screen:late','2026-10-12T11:00:01.000000Z',"
                  "'text written while the index was building','h')")
        w.commit(); w.close()
        return real(raw, db)
    monkeypatch.setattr(history, "index_into", during)
    index.build()
    db = sqlite3.connect(paths.search_db())
    assert db.execute("SELECT 1 FROM screen_stretches WHERE id='screen:late'").fetchone() is None
    assert db.execute("SELECT 1 FROM events WHERE id='screentext:late'").fetchone() is None
