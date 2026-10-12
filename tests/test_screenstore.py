"""Watcher writes (spec §5, F1): one BEGIN IMMEDIATE each; a stale generation or a missing parent is never
written, so removed text can't come back. Two real connections, like the watcher and forget."""
import pytest

S, E = "2026-10-12T14:00:00.000000Z", "2026-10-12T14:00:02.000000Z"


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("INKVAULT_HOME", str(tmp_path))
    from inkvault import screenstore
    conn = screenstore.connect()
    yield screenstore, conn
    conn.close()


def opened(ss, gen=0, sid="screen:1"):
    return ss.Write("open", sid, gen, started=S, ended=E, app="Discord.exe", title="Chat")


def text(ss, gen=0, sid="screen:1", tid="screentext:1", body="hello there everyone"):
    return ss.Write("text", sid, gen, text_id=tid, created=E, text=body, text_hash=tid)


def bump():
    from inkvault import export
    v = export.open_vault()
    v.execute("INSERT OR REPLACE INTO meta VALUES ('screen_removal_gen', CAST(coalesce((SELECT value FROM meta "
              "WHERE key='screen_removal_gen'), 0) + 1 AS TEXT))")
    v.commit(); v.close()


def test_open_text_close(store):
    ss, conn = store
    ss.apply(conn, opened(ss)); ss.apply(conn, text(ss))
    ss.apply(conn, ss.Write("close", "screen:1", 0, ended="2026-10-12T14:05:00.000000Z"))
    assert conn.execute("SELECT ended FROM screen_stretches").fetchone()[0].startswith("2026-10-12T14:05")
    assert conn.execute("SELECT COUNT(*) FROM screen_text").fetchone()[0] == 1


def test_a_removal_makes_older_writes_stale(store):
    ss, conn = store
    ss.apply(conn, opened(ss))
    bump()
    for w in (ss.Write("extend", "screen:1", 0, ended=E), text(ss), opened(ss, 0, "screen:2")):
        with pytest.raises(ss.Stale):
            ss.apply(conn, w)
    assert conn.execute("SELECT COUNT(*) FROM screen_text").fetchone()[0] == 0


def test_a_missing_parent_is_stale_and_never_recreated(store):
    ss, conn = store
    ss.apply(conn, opened(ss))
    conn.execute("DELETE FROM screen_stretches")
    for w in (text(ss), ss.Write("close", "screen:1", 0, ended=E)):
        with pytest.raises(ss.Stale):
            ss.apply(conn, w)
    assert conn.execute("SELECT COUNT(*) FROM screen_stretches").fetchone()[0] == 0


def test_a_restart_reads_the_persisted_generation(store):
    from inkvault import screen
    ss, conn = store
    bump()
    fresh = ss.connect()
    try:
        assert screen.removal_gen(fresh) == 1
        ss.apply(fresh, opened(ss, gen=1, sid="screen:9"))
    finally:
        fresh.close()


def test_busy_within_budget_even_at_connect(store):                        # P11
    import time
    from inkvault import export
    ss, conn = store
    blocker = export.open_vault(); blocker.execute("BEGIN IMMEDIATE")
    try:
        t = time.monotonic()
        with pytest.raises(ss.Busy):
            ss.apply(conn, opened(ss))
        assert time.monotonic() - t < 1.0
        t = time.monotonic()
        try:
            ss.connect().close()                                            # DDL under contention: fails fast
        except ss.Busy:
            pass
        assert time.monotonic() - t < 1.5
    finally:
        blocker.rollback(); blocker.close()
