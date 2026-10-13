"""The supervisor (spec §2, R2, P2, P10): never blocks the loop; abandoning keeps the deadline; validated
messages; late, foreign, partial, oversized and malformed answers never land or block."""
import pathlib
import sys
import time

import pytest

FAKE = str(pathlib.Path(__file__).with_name("fakeworker.py"))


def sup(*mode, **kw):
    from inkvault import worker
    return worker.Supervisor([sys.executable, FAKE, *mode], **kw)


def wait_for(s, seconds=3.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if (r := s.poll()) is not None:
            return r
        time.sleep(0.02)
    return None


def req(exe="Notion.exe", title_sha256="0" * 64):
    return {"hwnd": 1, "pid": 2, "created": 3, "exe": exe, "title_sha256": title_sha256}


def ticks_while(s, seconds):
    n, end = 0, time.monotonic() + seconds
    while time.monotonic() < end:
        t = time.monotonic()
        assert s.poll() is None
        assert time.monotonic() - t < 0.05            # a poll never blocks
        n += 1
        time.sleep(0.01)
    return n


def test_round_trip():
    s = sup("ok")
    try:
        rid = s.submit(req())
        assert wait_for(s)["id"] == rid and not s.busy()
    finally:
        s.close()


def test_hang_is_killed_at_the_deadline_and_cools_the_app():
    s = sup("hang", timeout=0.5)
    try:
        s.submit(req("Notion.exe"))
        ticks_while(s, 1.2)  # OS sleep coalescing is not a minimum polling-rate guarantee
        assert s.timeouts == 1 and s.cooling("Notion.exe") and not s.busy()
        assert s.submit(req("Notion.exe")) is None and s.submit(req("Code.exe")) is not None
    finally:
        s.close()


def test_abandoning_a_hang_keeps_the_deadline():                          # P2: the reported defect
    s = sup("hang", timeout=0.5)
    try:
        s.submit(req()); proc = s.proc
        s.abandon()
        assert s.busy() and s.submit(req("Code.exe")) is None              # no new request to a busy worker
        ticks_while(s, 1.0)
        assert proc.poll() is not None and not s.busy()                    # killed anyway
    finally:
        s.close()


def test_an_abandoned_answer_is_discarded_and_resubmit_waits():
    s = sup("late", "0.3", timeout=2.0)
    try:
        s.submit(req()); s.abandon()
        assert wait_for(s, 1.0) is None and not s.busy()                   # answered, but not acceptable
        rid = s.submit(req())
        assert wait_for(s, 2.0)["id"] == rid
    finally:
        s.close()


def test_a_deaf_worker_never_blocks_submit():
    s = sup("deaf", timeout=0.5)
    try:
        t = time.monotonic()
        s.submit(req())
        assert time.monotonic() - t < 0.5
        ticks_while(s, 1.0)
        assert s.timeouts == 1
    finally:
        s.close()


@pytest.mark.parametrize("mode", ["huge", "garbage", "array", "null", "badstatus", "badlines", "nested"])
def test_bad_answers_restart_the_worker(mode):                               # P10
    s = sup(mode, timeout=2.0)
    try:
        s.submit(req())
        assert wait_for(s, 2.5) is None and s.restarts == 1 and not s.busy()
    finally:
        s.close()


def test_partial_never_blocks():
    s = sup("partial", timeout=0.5)
    try:
        s.submit(req())
        ticks_while(s, 0.3)
    finally:
        s.close()


def test_an_oversized_request_is_a_dropped_read_not_a_crash():
    s = sup("ok")
    try:
        assert s.submit({**req(), "exe": "x" * 5000 + ".exe"}) is None and s.dropped_requests == 1
        assert s.submit(req()) is not None                                   # the worker is still fine
    finally:
        s.close()


def test_malformed_requests_are_dropped_reads():
    s = sup("ok")
    try:
        assert s.submit(req(title_sha256="Z" * 64)) is None and s.dropped_requests == 1
        assert s.submit({**req(), "pid": "2"}) is None and s.dropped_requests == 2
    finally:
        s.close()


def test_worker_error_text_is_reduced_to_a_name():
    from inkvault import worker
    assert worker.error_name("OSError") == "OSError"
    assert worker.error_name("secret title text: hello") == "unknown"


def test_long_unicode_titles_fit_because_only_their_hash_is_sent():
    from inkvault import winapi, worker
    title = "@名前 🎮 " * 2000
    assert len(worker.encode({**req(title_sha256=winapi.title_sha256(title)), "id": 1})) < worker.MAX_REQUEST


def test_close_reaps_without_waiting_on_a_hang():
    s = sup("hang")
    s.submit(req()); proc = s.proc
    t = time.monotonic()
    s.close()
    assert time.monotonic() - t < 2.0 and proc.poll() is not None


def test_a_spawn_failure_is_not_raised_to_the_loop(monkeypatch):
    from inkvault import worker
    real = worker.spawn
    monkeypatch.setattr(worker, "spawn", lambda argv: (_ for _ in ()).throw(OSError(2, "missing")))
    s = sup("ok")
    try:
        assert s.submit(req()) is None and s.proc is None and not s.busy()
        monkeypatch.setattr(worker, "spawn", real)
        rid = s.submit(req())
        assert wait_for(s)["id"] == rid
    finally:
        s.close()


def test_requests_carry_exactly_their_keys():
    s = sup("ok")
    try:
        assert s.submit({**req(), "title": "secret"}) is None and s.dropped_requests == 1
        no_exe = req(); del no_exe["exe"]
        assert s.submit(no_exe) is None and s.dropped_requests == 2
        assert s.submit(req(exe=5)) is None and s.dropped_requests == 3
    finally:
        s.close()



def test_hang_timeout_uses_the_deadline_without_a_host_polling_rate():
    clock = {"t": 0.0}
    s = sup("hang", timeout=5.0, clock=lambda: clock["t"])
    try:
        assert s.submit(req("Notion.exe")) is not None
        clock["t"] = 4.999
        assert s.poll() is None and s.busy() and s.timeouts == 0
        clock["t"] = 5.0
        assert s.poll() is None and not s.busy() and s.timeouts == 1
        assert s.cooling("Notion.exe") and s.submit(req("Notion.exe")) is None
        assert s.submit(req("Code.exe")) is not None
    finally:
        s.close()
