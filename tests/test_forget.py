"""Removing browsing: from the vault, search, vectors, digests and the dashboard. A removal stopped anywhere is
finished by the next search build, never undone, and nothing serves removed visits in between."""
import sqlite3

import numpy as np
import pytest

import webfixtures
from webfixtures import chromium_visit

T1, T2 = "2026-10-05T12:00:00Z", "2026-10-07T12:00:00Z"


class FakeModel:
    def encode(self, texts, **_):
        return np.ones((len(texts), 4), dtype=np.float32)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    """Chrome/Default with a docs visit (day 1) and a bank visit (day 2), synced and indexed with real (fake-model)
    vectors, a dashboard, and digests for both days plus a Pieces-only day."""
    monkeypatch.setenv("INKVAULT_HOME", str(tmp_path / "vault"))
    from inkvault import browsers, dashboard, digest, embed, forget, history, index, paths, server
    monkeypatch.setattr(embed, "load_model", FakeModel)
    monkeypatch.setattr(server, "_model", None)
    monkeypatch.setattr(forget, "DIGEST_WAIT", 0)  # a locked digest database fails at once, not after 5 s
    root = tmp_path / "chrome"
    h = webfixtures.chromium_profile(root, "Default", name="Default")
    webfixtures.install(monkeypatch, ("chrome", "chromium", root))
    chromium_visit(h, 1, "https://docs.example.com/retry", T1, title="Retry with backoff")
    chromium_visit(h, 2, "https://login.bank.example/", T2, title="Bank")
    (p,) = browsers.find_profiles()
    browsers.record_answers([p], {p.key: True})
    history.sync()
    index.build()
    assert dashboard.build() and paths.vectors().exists()
    days = [digest.local_day(T1), digest.local_day(T2), "2026-09-01"]
    db = sqlite3.connect(paths.digests_db())
    db.execute("CREATE TABLE digests (day TEXT PRIMARY KEY, digest TEXT, input_hash TEXT, model TEXT, created TEXT)")
    db.executemany("INSERT INTO digests VALUES (?, 'a day', 'h', 'm', 'now')", ((d,) for d in days))
    db.commit()
    db.close()
    return h, days


def rows(path, sql):
    db = sqlite3.connect(path)
    try:
        return db.execute(sql).fetchall()
    finally:
        db.close()


def bank_is_gone(days):
    from inkvault import browsers, forget, paths, server
    assert rows(paths.vault_db(), "SELECT url FROM browser_visits") == [("https://docs.example.com/retry",)]
    assert rows(paths.search_db(), "SELECT host FROM pages") == [("docs.example.com",)]
    assert sorted(rows(paths.digests_db(), "SELECT day FROM digests")) == sorted([(days[0],), (days[2],)])
    assert "bank.example" in browsers.load_choices()["skip_sites"]
    assert forget.pending() == 0 and not paths.rebuild_marker().exists()
    assert "bank.example" not in server.search_memories("bank", source="web", mode="keyword")


def test_forgetting_a_profile_removes_it_everywhere_vectors_included(setup):
    from inkvault import forget, paths
    assert forget.forget_profile("chrome/Default") == 2
    assert rows(paths.vault_db(), "SELECT COUNT(*) FROM browser_visits") == [(0,)]
    assert rows(paths.search_db(), "SELECT COUNT(*) FROM pages") == [(0,)]
    assert rows(paths.digests_db(), "SELECT day FROM digests") == [("2026-09-01",)]
    assert not paths.vectors().exists()  # those were the last records: their vectors went too
    assert not paths.rebuild_marker().exists()


def test_skipping_a_site_removes_it_and_its_subdomains_only(setup):
    from inkvault import forget, paths
    _, days = setup
    assert forget.forget_site("bank.example") == 1
    bank_is_gone(days)
    page = paths.dashboard().read_text(encoding="utf-8")
    assert "bank.example" not in page and "docs.example.com" in page


@pytest.mark.parametrize("step", ["record", "keep_choice", "delete_digests", "delete_visits", "index.build"])
def test_a_removal_stopped_at_any_step_is_finished_by_the_next_search_build(setup, monkeypatch, step):
    from inkvault import forget, index, paths, server
    _, days = setup
    module, name = (index, "build") if step == "index.build" else (forget, step)
    real = getattr(module, name)

    def stop(*a, **k):
        raise RuntimeError("stopped")
    monkeypatch.setattr(module, name, stop)
    with pytest.raises(RuntimeError):
        forget.forget_site("bank.example")
    assert paths.rebuild_marker().exists() and not paths.dashboard().exists()
    assert server.search_memories("bank") == server.REBUILDING
    monkeypatch.setattr(module, name, real)
    assert index.build()
    if step == "record":  # stopped before the removal was recorded: nothing changed, and the command said so
        assert len(rows(paths.vault_db(), "SELECT id FROM browser_visits")) == 2
        assert forget.pending() == 0 and not paths.rebuild_marker().exists()
    else:
        bank_is_gone(days)


def test_a_digest_database_that_cant_be_written_stops_the_removal_and_keeps_it_recorded(setup):
    from inkvault import forget, index, paths
    _, days = setup
    hold = sqlite3.connect(paths.digests_db())
    hold.execute("BEGIN EXCLUSIVE")
    try:
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            forget.forget_site("bank.example")
        assert forget.pending() == 1 and paths.rebuild_marker().exists()
    finally:
        hold.rollback()
        hold.close()
    assert index.build()
    bank_is_gone(days)


def test_a_digest_database_without_its_table_is_nothing_to_delete(setup):
    from inkvault import forget, paths
    paths.digests_db().unlink()
    sqlite3.connect(paths.digests_db()).close()  # exists, but empty
    assert forget.forget_site("bank.example") == 1 and forget.pending() == 0


def test_a_request_in_flight_when_a_removal_starts_never_returns_what_was_removed(setup, monkeypatch):
    from inkvault import forget, server
    real = server.keyword_ranked

    def paused(*a, **k):
        found = real(*a, **k)  # the old index has the bank page
        try:
            forget.forget_site("bank.example")
        except OSError:  # Windows can't replace search.db while this request has it open: the removal stays recorded
            pass
        return found
    monkeypatch.setattr(server, "keyword_ranked", paused)
    answer = server.search_memories("bank", source="web", mode="keyword")
    assert answer in (server.REBUILDING, server.CHANGED)


def test_a_commands_removals_are_recorded_together(setup, monkeypatch):
    from inkvault import forget

    def stop():
        raise RuntimeError("stopped")
    monkeypatch.setattr(forget, "apply_pending", stop)  # leave them recorded
    with pytest.raises(RuntimeError):
        forget.remove([("site", "bank.example"), ("site", "docs.example.com"), ("site", "nowhere.example")])
    assert forget.pending() == 2  # the two that match anything, in one transaction


def test_a_browser_sync_finishes_a_recorded_removal_first(setup):
    from inkvault import browsers, forget, history, paths
    assert forget.request([("site", "bank.example")]) == 1  # recorded, then stopped: the skip list wasn't saved
    history.sync()  # the bank visit is still in Chrome's file; the finished removal's skip list keeps it out
    assert rows(paths.vault_db(), "SELECT url FROM browser_visits") == [("https://docs.example.com/retry",)]
    assert forget.pending() == 0 and "bank.example" in browsers.load_choices()["skip_sites"]


def test_nothing_to_remove_touches_nothing(setup):
    from inkvault import forget, paths
    before = paths.dashboard().stat().st_mtime_ns
    assert forget.forget_site("nowhere.example") == 0
    assert paths.dashboard().stat().st_mtime_ns == before
    assert forget.pending() == 0 and not paths.rebuild_marker().exists()


def test_a_removal_clears_digests_for_every_day_its_visits_could_fall_on(setup):
    """Digests are keyed by the local day when digest ran: any time zone puts an instant within a day of its UTC date."""
    from inkvault import forget, paths
    db = sqlite3.connect(paths.digests_db())
    db.executemany("INSERT OR IGNORE INTO digests VALUES (?, 'a day', 'h', 'm', 'now')",
                   ((d,) for d in ("2026-10-06", "2026-10-07", "2026-10-08", "2026-10-10")))
    db.commit()
    db.close()
    forget.forget_site("bank.example")
    left = {d for (d,) in rows(paths.digests_db(), "SELECT day FROM digests")}
    assert not left & {"2026-10-06", "2026-10-07", "2026-10-08"}
    assert "2026-10-10" in left


def no_model():
    raise RuntimeError("no model")


def test_a_failed_embedding_leaves_search_db_free_to_replace(setup, monkeypatch):
    import os
    import shutil
    from inkvault import embed, paths
    monkeypatch.setattr(embed, "load_model", no_model)
    with pytest.raises(RuntimeError) as failed:  # the traceback keeps build()'s frame, and any handle in it, alive
        embed.build()
    copy = paths.search_db().with_name("copy.db")
    shutil.copy(paths.search_db(), copy)
    os.replace(copy, paths.search_db())  # fails on Windows while a handle is still open


def test_clearing_old_vectors_needs_no_model(setup, monkeypatch):
    from inkvault import embed, forget, paths
    forget.forget_profile("chrome/Default")
    paths.vectors().write_bytes(b"old")
    monkeypatch.setattr(embed, "load_model", no_model)
    embed.build()
    assert not paths.vectors().exists()
