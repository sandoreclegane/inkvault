"""The watcher with a strict fake supervisor and a real vault (spec §1–§5)."""
import json
from datetime import datetime, timedelta, timezone

import pytest

T0 = datetime(2026, 10, 12, 14, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class FakeSupervisor:
    """The real Supervisor's contract: one request at a time, ids, abandon = unacceptable but still busy."""

    def __init__(self):
        self.requests, self.pending, self.answers = [], None, {}
        self.timeouts, self.cooldowns = 0, {}

    def busy(self):
        return self.pending is not None

    def cooling(self, exe):
        return False

    def submit(self, req):
        if self.pending:
            return None
        self.requests.append(req)
        self.pending = {"id": len(self.requests), "ok": True}
        return self.pending["id"]

    def answer(self, lines, status="ok"):
        self.answers[self.pending["id"]] = {"id": self.pending["id"], "status": status, "lines": lines,
                                            "colors": 30, "error": ""}

    def poll(self):
        if self.pending and self.pending["id"] in self.answers:
            p, self.pending = self.pending, None
            a = self.answers.pop(p["id"])
            return a if p["ok"] else None
        return None

    def abandon(self):
        if self.pending:
            self.pending["ok"] = False

    def close(self):
        pass


@pytest.fixture
def rig(tmp_path, monkeypatch):
    monkeypatch.setenv("INKVAULT_HOME", str(tmp_path))
    from inkvault import screen, screenstore, watch
    from inkvault.stretches import Identity, Sample
    screen.prepare()
    screen.edit(lambda d: d["text_apps"].append("Discord.exe"))
    conn = screenstore.connect()
    clock, sup = Clock(), FakeSupervisor()
    state = {"window": Identity(1, 10, 1, "Discord.exe", "Chat"), "idle": 0.0, "locked": False}

    def sampler():
        return Sample(clock.t, T0 + timedelta(seconds=clock.t), state["window"], state["idle"], state["locked"])

    w = watch.Watcher(sampler, sup, conn, screen.SettingsSource(), clock=clock,
                      utc=lambda: T0 + timedelta(seconds=clock.t))
    yield w, clock, sup, state, conn
    conn.close()


def ticks(w, clock, n, step=2.0):
    for _ in range(n):
        assert w.tick()
        clock.t += step


def rows(conn, table):
    return conn.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()


def read_once(w, clock, sup, lines):
    sup.answer(lines)
    ticks(w, clock, 1)


def test_text_is_accepted_and_stored(rig):
    w, clock, sup, state, conn = rig
    ticks(w, clock, 16)
    assert sup.requests and "title" not in sup.requests[0] and len(sup.requests[0]["title_sha256"]) == 64
    read_once(w, clock, sup, ["hello everyone in the channel"])
    assert rows(conn, "screen_text")[0][3] == "hello everyone in the channel"


def test_a_changed_window_at_acceptance_drops_the_result(rig):
    from inkvault.stretches import Identity
    w, clock, sup, state, conn = rig
    ticks(w, clock, 16)
    state["window"] = Identity(1, 10, 1, "Discord.exe", "Other chat")
    read_once(w, clock, sup, ["secret from the other chat"])
    assert rows(conn, "screen_text") == []


def test_same_text_once_per_stretch_but_again_in_a_new_stretch(rig):           # P6
    from inkvault.stretches import Identity
    w, clock, sup, state, conn = rig
    for _ in range(2):
        ticks(w, clock, 61)
        if sup.pending:
            read_once(w, clock, sup, ["the same text on screen again"])
    assert len(rows(conn, "screen_text")) == 1
    state["window"] = Identity(2, 20, 2, "Notion.exe", "B"); ticks(w, clock, 3)            # A -> B -> A
    state["window"] = Identity(1, 10, 1, "Discord.exe", "Chat"); ticks(w, clock, 61)
    if sup.pending:
        read_once(w, clock, sup, ["the same text on screen again"])
    assert len(rows(conn, "screen_text")) == 2


def test_hash_counts_only_after_the_insert_commits(rig, monkeypatch):         # P6
    from inkvault import screenstore
    w, clock, sup, state, conn = rig
    ticks(w, clock, 16)
    real, fails = screenstore.apply, {"n": 0}

    def flaky(c, wr):
        if wr.kind == "text" and fails["n"] == 0:
            fails["n"] += 1
            raise screenstore.StoreError("OperationalError: disk I/O error")
        return real(c, wr)
    monkeypatch.setattr(screenstore, "apply", flaky)
    read_once(w, clock, sup, ["text that fails to write the first time"])
    assert rows(conn, "screen_text") == [] and "disk I/O" in w.last_error
    ticks(w, clock, 1)                                                              # retried from the queue
    assert len(rows(conn, "screen_text")) == 1


def test_pause_drops_queued_text(rig):                                          # P3: real busy DB
    from inkvault import export, screen
    w, clock, sup, state, conn = rig
    ticks(w, clock, 16)
    blocker = export.open_vault(); blocker.execute("BEGIN IMMEDIATE")
    try:
        read_once(w, clock, sup, ["queued while the vault is busy"])
        assert w.queue.text_bytes > 0
        screen.edit(lambda d: d.update(paused_until=screen.iso(T0 + timedelta(hours=2))))
        ticks(w, clock, 1)
        assert w.queue.text_bytes == 0
    finally:
        blocker.rollback(); blocker.close()
    ticks(w, clock, 2)
    assert rows(conn, "screen_text") == []


def test_a_change_seen_at_acceptance_empties_the_backlog_before_the_flush(rig):  # R1: a real mid-tick barrier
    from inkvault import export, screen
    w, clock, sup, state, conn = rig
    ticks(w, clock, 16)                                         # first read submitted at t = 30
    blocker = export.open_vault(); blocker.execute("BEGIN IMMEDIATE")
    try:
        read_once(w, clock, sup, ["older text queued while the vault is busy"])
        assert w.queue.text_bytes > 0
        ticks(w, clock, 60)                                     # past the 120 s budget: a second read is pending
        assert sup.busy()
    finally:
        blocker.rollback(); blocker.close()
    real, calls = w.source.poll, {"n": 0}

    def second_load_sees_a_pause():
        calls["n"] += 1
        if calls["n"] == 2:                                     # inside the same tick, at acceptance
            screen.edit(lambda d: d.update(paused_until=screen.iso(T0 + timedelta(hours=5))))
        return real()
    w.source.poll = second_load_sees_a_pause
    sup.answer(["newer text read just before the pause"])
    ticks(w, clock, 1)
    assert w.queue.text_bytes == 0 and rows(conn, "screen_text") == []


def test_an_edit_between_the_tick_and_acceptance_drops_the_result(rig):        # P3
    from inkvault import screen
    w, clock, sup, state, conn = rig
    ticks(w, clock, 16)
    screen.edit(lambda d: d["never_titles"].append("chat"))
    read_once(w, clock, sup, ["text that must not be kept anymore"])
    assert rows(conn, "screen_text") == []


@pytest.mark.parametrize("content", ["{nope", '{"text_apps": [], "never_apps": [], "never_titles": [], '
                                              '"paused_until": 5}'])
def test_invalid_settings_fail_closed_without_crashing(rig, content):          # P3
    from inkvault import paths
    w, clock, sup, state, conn = rig
    ticks(w, clock, 5)
    paths.screen_file().write_text(content, encoding="utf-8")
    ticks(w, clock, 10)
    assert w.core.open is None and any(s in (w.last_error or "") for s in ("screen.json", "paused_until"))


def test_a_removal_resets_the_open_stretch_and_drops_inflight(rig):
    from inkvault import export
    w, clock, sup, state, conn = rig
    ticks(w, clock, 16)
    v = export.open_vault()
    v.execute("DELETE FROM screen_stretches")
    v.execute("INSERT OR REPLACE INTO meta VALUES ('screen_removal_gen', '1')"); v.commit(); v.close()
    sup.answer(["text read before the removal finished"])
    ticks(w, clock, 20)
    assert rows(conn, "screen_text") == [] and w.stale_resets >= 1 and len(rows(conn, "screen_stretches")) == 1


def test_stop_file(rig):
    from inkvault import paths
    w, clock, sup, state, conn = rig
    ticks(w, clock, 5)
    paths.watch_stop().write_text("stop")
    assert w.tick() is False and not paths.watch_stop().exists() and w.core.open is None


def test_heartbeat_has_no_titles_and_survives_write_failure(rig, monkeypatch):    # P11
    from inkvault import screen
    w, clock, sup, state, conn = rig
    ticks(w, clock, 20)
    assert "Chat" not in json.dumps(w.heartbeat()) and "Discord.exe" in json.dumps(w.heartbeat())
    monkeypatch.setattr(screen, "write_atomic", lambda *a: (_ for _ in ()).throw(PermissionError("denied")))
    ticks(w, clock, 20)
    assert "heartbeat" in w.last_error


def test_a_restart_never_extends_the_old_row(rig):
    from inkvault import screen, watch
    w, clock, sup, state, conn = rig
    ticks(w, clock, 20)
    before = rows(conn, "screen_stretches")
    w2 = watch.Watcher(w.sampler, FakeSupervisor(), conn, screen.SettingsSource(), clock=clock, utc=w.utc)
    ticks(w2, clock, 20)
    after = rows(conn, "screen_stretches")
    assert before[0] in after and len(after) == 2
