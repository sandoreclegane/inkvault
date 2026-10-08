"""Browser history: syncing chosen profiles into the vault, counting visits, and pages in search."""
import sqlite3

import numpy as np
import pytest


class FakeModel:
    """Stands in for the search model (a download): every text gets the same small vector, so meaning search and
    vectors.npz work for real without it."""
    def encode(self, texts, **_):
        return np.ones((len(texts), 4), dtype=np.float32)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("INKVAULT_HOME", str(tmp_path / "vault"))
    from inkvault import embed, server
    monkeypatch.setattr(embed, "load_model", FakeModel)
    monkeypatch.setattr(server, "_model", None)
    return tmp_path


def test_browser_files_live_in_the_vault_folder(home):
    from inkvault import paths
    assert paths.browsers_file() == paths.home() / "browsers.json"
    assert paths.rebuild_marker() == paths.home() / "rebuild-needed"


def test_the_vault_has_the_browser_tables(home):
    from inkvault import export
    db = export.open_vault()
    try:
        cols = lambda t: [r[1] for r in db.execute(f"PRAGMA table_info({t})")]
        assert cols("browser_visits") == ["id", "profile", "visit_id", "redirected", "created", "url", "address",
                                          "title", "title_observed_at", "duration_s", "transition", "origin",
                                          "origin_visit_id"]
        assert cols("browser_profiles") == ["profile", "last_attempt", "last_success", "last_error", "visits_in_file"]
        assert cols("browser_removals") == ["id", "what", "value", "days", "created"]
    finally:
        db.close()
