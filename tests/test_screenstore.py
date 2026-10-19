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


def test_busy_within_budget_even_at_connect(store, monkeypatch):           # P11
    import time
    from inkvault import export
    ss, conn = store
    # Exercise real contention with a short fixture wait. OS sleep coalescing can inflate SQLite's requested
    # sleeps on shared runners; the production 250 ms setting is checked independently below.
    monkeypatch.setattr(ss, "BUSY_MS", 10)
    conn.execute("PRAGMA busy_timeout=10")
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


def test_coalescing_and_dependency_order(store):
    ss, conn = store
    q = ss.Queue()
    q.push(opened(ss))
    for i in range(200):
        q.push(ss.Write("extend", "screen:1", 0, ended=f"2026-10-12T15:{i % 60:02d}:00.000000Z"))
    q.push(text(ss))
    assert len(q) == 1
    r = q.flush(conn)
    assert r.written == 2 and [w.text_id for w in r.texts] == ["screentext:1"] and len(q) == 0


def test_flush_stops_at_busy_and_at_its_budget(store):
    from inkvault import export
    ss, conn = store
    q = ss.Queue()
    for i in range(30):
        q.push(opened(ss, sid=f"screen:{i}"))
    blocker = export.open_vault(); blocker.execute("BEGIN IMMEDIATE")
    try:
        r = q.flush(conn)
        assert r.busy and r.written == 0 and len(q) == 30
    finally:
        blocker.rollback(); blocker.close()
    assert q.flush(conn, max_writes=20, clock=lambda: 0.0).written == 20 and len(q) == 10           # P11: write budget
    ticks = iter([0.0, 0.0, 0.3, 0.3, 0.3])
    assert q.flush(conn, clock=lambda: next(ticks)).written == 1                 # P11: time budget


def test_the_cap_refuses_new_stretches_but_not_their_closes(store):
    ss, _ = store
    q = ss.Queue(max_stretches=3)
    assert all(q.push(opened(ss, sid=f"screen:{i}")) for i in range(3))
    assert q.blocked() and q.push(opened(ss, sid="screen:new")) is False
    assert q.push(ss.Write("close", "screen:2", 0, ended=E)) and len(q) == 3


def test_text_cap_and_has_text(store):
    ss, _ = store
    q = ss.Queue(max_text_bytes=100)
    q.push(opened(ss))
    for i in range(5):
        q.push(text(ss, tid=f"t{i}", body="z" * 40))
    assert q.text_bytes <= 100 and q.dropped_text == 3
    assert q.has_text("screen:1", "t4") and not q.has_text("screen:1", "t0")


def test_drop_text(store):
    ss, _ = store
    q = ss.Queue()
    q.push(opened(ss)); q.push(text(ss))
    assert q.drop_text(lambda w: True) == 1 and q.text_bytes == 0 and len(q) == 1


def test_a_non_busy_error_keeps_the_write_and_reports(store, monkeypatch):
    ss, conn = store
    q = ss.Queue()
    q.push(opened(ss))
    monkeypatch.setattr(ss, "apply", lambda *_: (_ for _ in ()).throw(ss.StoreError("OperationalError: disk full")))
    r = q.flush(conn)
    assert r.error.endswith("disk full") and len(q) == 1



def test_connection_configures_the_production_quarter_second_busy_timeout(store):
    _, conn = store
    assert conn.execute("PRAGMA busy_timeout").fetchone() == (250,)
