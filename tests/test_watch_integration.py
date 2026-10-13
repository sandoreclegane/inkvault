"""The real supervisor and real SQLite contention under the watcher (P12): abandoned hangs still die; the loop
keeps ticking; more than 50 window changes under contention stay within caps and budgets."""
import pathlib
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

FAKE = str(pathlib.Path(__file__).with_name("fakeworker.py"))
T0 = datetime(2026, 10, 12, 14, 0, tzinfo=timezone.utc)


@pytest.fixture
def rig(tmp_path, monkeypatch):
    monkeypatch.setenv("INKVAULT_HOME", str(tmp_path))
    from inkvault import screen, screenstore, watch, worker
    from inkvault.stretches import Identity, Sample
    screen.prepare()
    screen.edit(lambda d: d["text_apps"].append("Discord.exe"))
    conn = screenstore.connect()
    clock = {"t": 0.0}
    state = {"window": Identity(1, 10, 1, "Discord.exe", "Chat")}
    sups = []

    def make(mode, timeout=0.5):
        sup = worker.Supervisor([sys.executable, FAKE, mode], timeout=timeout)
        sups.append(sup)
        sampler = lambda: Sample(clock["t"], T0 + timedelta(seconds=clock["t"]), state["window"], 0.0, False)
        return watch.Watcher(sampler, sup, conn, screen.SettingsSource(), clock=lambda: clock["t"],
                             utc=lambda: T0 + timedelta(seconds=clock["t"])), sup

    yield make, clock, state, conn
    for s in sups:
        s.close()
    conn.close()


def step(w, clock, n=1):
    for _ in range(n):
        t = time.monotonic()
        assert w.tick()
        # Shared runners can preempt a tick or delay process startup; still bound it below the 2-second poll.
        assert time.monotonic() - t < 1.5
        clock["t"] += 2.0


@pytest.mark.parametrize("then", ["pause", "removal", "invalid", "lost"])
def test_hang_then_pause_removal_or_broken_settings_still_kills_the_worker(rig, then):   # R3
    from inkvault import export, paths, screen
    make, clock, state, conn = rig
    w, sup = make("hang")
    step(w, clock, 16)
    proc = sup.proc
    assert sup.busy()
    if then == "pause":
        screen.edit(lambda d: d.update(paused_until=screen.iso(T0 + timedelta(hours=1))))
    elif then == "invalid":
        paths.screen_file().write_text("{nope", encoding="utf-8")
    elif then == "lost":
        paths.screen_file().unlink()
    else:
        v = export.open_vault()
        v.execute("INSERT OR REPLACE INTO meta VALUES ('screen_removal_gen', '1')"); v.commit(); v.close()
    end = time.monotonic() + 2.0
    while time.monotonic() < end:
        step(w, clock)
        time.sleep(0.05)
    assert proc.poll() is not None and not sup.busy()
    sup.close()


def test_many_window_changes_under_contention(rig):
    from inkvault import export
    from inkvault.stretches import Identity
    make, clock, state, conn = rig
    w, sup = make("ok")
    # This test covers queue behavior under a real writer, not the host's timer precision. The production
    # timeout is pinned in test_screenstore; shorter fixture waits retain the wall-time nonblocking guard.
    conn.execute("PRAGMA busy_timeout=10")
    blocker = export.open_vault(); blocker.execute("BEGIN IMMEDIATE")
    try:
        for i in range(110):                                     # 55 stretches of 2 polls each
            state["window"] = Identity(100 + i // 2, 10, 1, "Notion.exe", f"Page {i // 2}")
            step(w, clock)
        assert len(w.queue) <= w.queue.max_stretches and w.busy_gaps > 0           # the actual cap (R8)
    finally:
        blocker.rollback(); blocker.close()
    for _ in range(10):
        step(w, clock)
    assert len(w.queue) <= 1
    ids = [r[0] for r in conn.execute("SELECT id FROM screen_stretches ORDER BY started")]
    assert len(ids) >= 49 and len(set(ids)) == len(ids)
    sup.close()


def test_the_vault_reopen_publishes_a_heartbeat_and_stops_promptly(tmp_path, monkeypatch):   # R8
    import json
    monkeypatch.setenv("INKVAULT_HOME", str(tmp_path))
    from inkvault import paths, screenstore, watch
    monkeypatch.setattr(screenstore, "connect", lambda: (_ for _ in ()).throw(screenstore.StoreError("disk")))
    start = time.monotonic()
    threading.Timer(0.3, lambda: paths.watch_stop().write_text("stop")).start()
    assert watch.open_store(lambda: paths.watch_stop().exists()) is None
    assert time.monotonic() - start < 1.2                                    # not the full 10 s retry wait
    hb = json.loads(paths.watch_heartbeat().read_text())
    assert hb["state"] == "waiting for the vault" and hb["db_error"] == "StoreError" and hb["attempts"] >= 1
