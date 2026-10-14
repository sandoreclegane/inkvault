"""One helper per home, decided by an OS lock (spec §3.4); idle stop; offline model setting."""
import json
import os
import subprocess
import sys
import time

import pytest

LAUNCH = """
import os, sys
os.environ["INKVAULT_HOME"] = sys.argv[1]
from inkvault import atlasserve
sys.exit(atlasserve.serve(open_browser=False, idle=float(sys.argv[2]), wait=float(sys.argv[3])))
"""
HOLD = """
import os, sys
os.environ["INKVAULT_HOME"] = sys.argv[1]
from inkvault import nightly, paths
fd = os.open(paths.atlas_lock(), os.O_RDWR | os.O_CREAT)
assert nightly.try_lock(fd)
print("held", flush=True)
sys.stdin.read()
"""


def launch(home, idle=60, wait=10):
    return subprocess.Popen([sys.executable, "-c", LAUNCH, str(home), str(idle), str(wait)],
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def wait_for_info(home, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            return json.loads((home / "atlas-helper.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            time.sleep(0.1)
    raise AssertionError("no helper info")


def stop(p):
    if p.poll() is None:
        p.kill()
    p.communicate(timeout=20)


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "José Ñ vault's home"
    h.mkdir()
    return h


# Helpers are told apart by their control secret, not by pid: on Windows a virtual environment's python.exe can be a
# launcher that runs the real interpreter as a child, so Popen.pid isn't the helper's own pid.

def test_two_launches_at_once_make_one_helper(home):
    from inkvault import atlasserve
    a, b = launch(home), launch(home)
    try:
        info = wait_for_info(home)
        deadline = time.monotonic() + 20
        while a.poll() is None and b.poll() is None and time.monotonic() < deadline:
            time.sleep(0.1)
        done = a if a.poll() is not None else b
        assert done.wait(timeout=20) == 0
        assert "already running" in done.communicate(timeout=20)[0]
        alive = b if done is a else a
        assert alive.poll() is None and atlasserve.request_code(info)
    finally:
        stop(a); stop(b)


def test_a_busy_lock_holder_is_never_replaced(home):
    holder = subprocess.Popen([sys.executable, "-c", HOLD, str(home)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        p = launch(home, wait=1)
        out, _ = p.communicate(timeout=30)
        assert p.returncode == 1 and "starting or busy" in out
        assert not (home / "atlas-helper.json").exists()
    finally:
        holder.stdin.close()
        holder.wait(timeout=20)


def test_a_killed_helpers_lock_is_free_at_once(home):
    from inkvault import atlasserve
    first = launch(home)
    try:
        old = wait_for_info(home)
        first.kill(); first.wait(timeout=20)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:   # gone: it no longer answers
            try:
                if not atlasserve.request_code(old, timeout=0.5):
                    break
            except OSError:
                break
            time.sleep(0.1)
        second = launch(home)
        try:
            deadline = time.monotonic() + 20
            info = old
            while info["control"] == old["control"] and time.monotonic() < deadline:
                time.sleep(0.1)
                info = wait_for_info(home)
            assert info["control"] != old["control"] and atlasserve.request_code(info)
            assert second.poll() is None  # it became the helper instead of reporting a busy one
        finally:
            stop(second)
    finally:
        stop(first)


def test_an_idle_helper_stops_and_cleans_up(home):
    p = launch(home, idle=1)
    wait_for_info(home)
    assert p.wait(timeout=30) == 0
    assert not (home / "atlas-helper.json").exists()


def test_helper_in_a_home_with_spaces_and_accents(home):
    p = launch(home)
    try:
        info = wait_for_info(home)
        from inkvault import atlasserve
        assert atlasserve.request_code(info)
    finally:
        stop(p)


def test_info_file_is_private_on_posix(home, monkeypatch):
    if os.name == "nt":
        pytest.skip("POSIX permissions")
    p = launch(home)
    try:
        wait_for_info(home)
        assert (home / "atlas-helper.json").stat().st_mode & 0o077 == 0
    finally:
        stop(p)


def test_a_lock_error_starts_nothing(tmp_path, monkeypatch, capsys):      # Codex plan review #1
    monkeypatch.setenv("INKVAULT_HOME", str(tmp_path))
    from inkvault import atlasserve, nightly

    def broken(fd):
        raise OSError(38, "Function not implemented")
    monkeypatch.setattr(nightly, "try_lock", broken)
    monkeypatch.setattr(atlasserve, "Helper", lambda *a, **k: pytest.fail("no helper may start without the lock"))
    monkeypatch.setattr(atlasserve, "open_in_browser", lambda url: pytest.fail("nothing may open"))
    assert atlasserve.serve(open_browser=True) == 1
    assert not (tmp_path / "atlas-helper.json").exists()
    assert "Nothing was started" in capsys.readouterr().out


def test_the_helper_process_is_offline(monkeypatch):
    from inkvault import atlasserve
    monkeypatch.setenv("HF_HUB_OFFLINE", "0")
    atlasserve.prepare_process()
    assert os.environ["HF_HUB_OFFLINE"] == "1"


def test_an_unopenable_lock_file_starts_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("INKVAULT_HOME", str(tmp_path))
    from inkvault import atlasserve, paths
    monkeypatch.setattr(paths, "atlas_lock", lambda: tmp_path / "missing dir" / "atlas.lock")
    assert atlasserve.serve(open_browser=False) == 1
    assert "Nothing was started" in capsys.readouterr().out


def test_reuse_survives_a_non_helper_on_the_port(home, monkeypatch, capsys):
    from inkvault import atlasserve, paths
    monkeypatch.setenv("INKVAULT_HOME", str(home))
    (home / "atlas-helper.json").write_text(json.dumps({"port": 9, "control": "x"}), encoding="utf-8")

    def bad(info, timeout=2.0):
        raise __import__("http.client").client.BadStatusLine("junk")
    monkeypatch.setattr(atlasserve, "request_code", bad)
    assert atlasserve.reuse(False, wait=0.5) == 1
