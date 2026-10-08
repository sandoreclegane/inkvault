"""Helpers shared by the test files, and guards every test gets."""
import contextlib
import os
import subprocess
import sys

import pytest

REAL_RUN = subprocess.run

HOLD = """
import os, sys
os.environ["INKVAULT_HOME"] = sys.argv[1]
from inkvault import nightly
with nightly.lock():
    print("held", flush=True)
    sys.stdin.read()  # hold until the test closes our stdin
"""


def pytest_configure(config):
    config.addinivalue_line("markers", "real_pieces_find: use the real PiecesOS.find (slow: it probes ports)")


@pytest.fixture(autouse=True)
def only_python_subprocesses(monkeypatch):
    """A test may start this Python (`sys.executable ...`); any other command it hasn't faked fails loudly instead
    of running: uv tool install, schtasks, launchctl, systemctl, crontab, pmset, powercfg, ..."""
    def guarded(cmd, *a, **k):
        program = cmd[0] if isinstance(cmd, (list, tuple)) and cmd else cmd
        if isinstance(program, (str, os.PathLike)) and os.path.normcase(os.fspath(program)) == \
                os.path.normcase(sys.executable):
            return REAL_RUN(cmd, *a, **k)
        raise AssertionError(f"a test ran a real command: {cmd}")
    monkeypatch.setattr(subprocess, "run", guarded)


@pytest.fixture(autouse=True)
def no_real_sessions(tmp_path_factory, monkeypatch):
    """Sync must never read this machine's own Claude Code, Claude desktop or Codex sessions: point them all at
    empty folders. A test that wants sessions writes them under these same variables (for the desktop app, under
    sources.claude_desktop_roots()[0])."""
    base = tmp_path_factory.mktemp("agents")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(base / "claude"))
    monkeypatch.setenv("CODEX_HOME", str(base / "codex"))
    from inkvault import sources
    monkeypatch.setattr(sources, "claude_desktop_roots", lambda: [base / "claude-desktop"])


@pytest.fixture(autouse=True)
def no_real_browsers(monkeypatch):
    """No test may read this machine's own browser history: every test starts with no browsers installed. A test
    that wants browsers builds them with webfixtures.py and points browsers.user_data_dirs at them. The real
    function stays reachable as browsers.real_user_data_dirs, for tests of the platform paths themselves."""
    from inkvault import browsers
    monkeypatch.setattr(browsers, "real_user_data_dirs", browsers.user_data_dirs, raising=False)
    monkeypatch.setattr(browsers, "user_data_dirs", lambda: [])


@pytest.fixture(autouse=True)
def no_pieces_probe(request, monkeypatch):
    """PiecesOS.find probes several ports and waits on each; no test needs a real PiecesOS. A test can fake find
    itself (that wins), or use @pytest.mark.real_pieces_find."""
    if request.node.get_closest_marker("real_pieces_find"):
        return
    from inkvault import export
    monkeypatch.setattr(export.PiecesOS, "find", staticmethod(lambda: (None, None)))


@contextlib.contextmanager
def holder(home):
    """Another process holding the nightly lock. A separate process, because that's what the lock is for, and
    it behaves the same on Windows, macOS and Linux (flock and msvcrt locks can be ambiguous within one process)."""
    p = subprocess.Popen([sys.executable, "-c", HOLD, str(home)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True)
    try:
        assert p.stdout.readline().strip() == "held", p.stderr.read()
        yield p
    finally:
        p.stdin.close()
        try:
            p.wait(timeout=30)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait()
        err = p.stderr.read()
        assert not err, f"lock holder crashed: {err}"
