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

    def make(mode, timeout=0.5, worker_clock=time.monotonic):
        sup = worker.Supervisor([sys.executable, FAKE, mode], timeout=timeout, clock=worker_clock)
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
        # These integration tests assert state/queue behavior, not the runner's scheduling latency.
        # The separate hang test below forbids synchronous waits and pins the supervisor deadline.
        assert w.tick()
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
    # timeout is pinned in test_screenstore; short fixture waits keep the contention exercise quick.
    conn.execute("PRAGMA busy_timeout=10")
    blocker = export.open_vault(); blocker.execute("BEGIN IMMEDIATE")
    try:
        for i in range(110):                                     # 55 stretches of 2 polls each
            state["window"] = Identity(100 + i // 2, 10, 1, "Notion.exe", f"Page {i // 2}")
            step(w, clock)
        assert len(w.queue) <= w.queue.max_stretches and w.busy_gaps > 0           # the actual cap (R8)
    finally:
        blocker.rollback(); blocker.close()
    # The real worker drains the backlog at the runner's pace, so wait on the queue, not a fixed tick count.
    end = time.monotonic() + 15.0
    while len(w.queue) > 1 and time.monotonic() < end:
        step(w, clock)
        time.sleep(0.02)
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


def test_ticks_never_wait_for_a_hung_worker_and_keep_its_exact_deadline(rig, monkeypatch):
    from inkvault import watch
    make, clock, state, conn = rig
    worker_clock = {"t": 0.0}
    w, sup = make("hang", timeout=5.0, worker_clock=lambda: worker_clock["t"])
    step(w, clock, 16)
    proc = sup.proc
    assert proc is not None and sup.busy()

    def blocking(*args, **kwargs):
        pytest.fail("a watcher tick must not sleep or wait for its worker")

    with monkeypatch.context() as m:
        m.setattr(watch.time, "sleep", blocking)
        m.setattr(proc, "wait", blocking)
        m.setattr(proc, "communicate", blocking)
        worker_clock["t"] = 4.999
        step(w, clock)
        assert sup.busy() and sup.timeouts == 0
        worker_clock["t"] = 5.0
        step(w, clock)
        assert not sup.busy() and sup.timeouts == 1 and sup.cooling("Discord.exe")
    proc.wait(timeout=20)  # outside the tick: prove the real hung process was killed
