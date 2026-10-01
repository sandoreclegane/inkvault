"""The nightly run: lock, log, backup, and steps that fail without stopping the others."""
import os
import subprocess
import sys

import pytest
from conftest import holder


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("INKVAULT_HOME", str(tmp_path))
    return tmp_path


def test_nightly_paths_live_in_the_vault_folder(home):
    from inkvault import paths
    assert paths.nightly_log() == home.resolve() / "nightly.log"
    assert paths.nightly_lock() == home.resolve() / "nightly.lock"
    assert paths.backups_dir() == home.resolve() / "backups"
    assert paths.schedule_file() == home.resolve() / "schedule.json"


def test_python_dash_m_runs_the_cli():
    r = subprocess.run([sys.executable, "-m", "inkvault", "--version"], capture_output=True, text=True)
    assert r.returncode == 0 and "inkvault" in r.stdout


def test_log_stream_writes_complete_lines_and_keeps_the_last_progress_update(home):
    from inkvault import nightly, paths
    s = nightly.LogStream()
    s.write("first line\nsecond ")
    s.write("half\n")
    s.write("10%\r50%\r100%\n")  # progress bars redraw with \r: keep only the final state
    s.write("\n\n")  # blank lines are dropped
    s.write("unterminated")
    s.finish()
    lines = [l[21:] for l in paths.nightly_log().read_text(encoding="utf-8").splitlines()]  # drop the timestamp
    assert lines == ["  first line", "  second half", "  100%", "  unterminated"]


def test_trim_keeps_the_newest_runs(home):
    from inkvault import nightly, paths
    for i in range(5):
        nightly.log(f"{nightly.START} run {i} ===")
        nightly.log("work")
    nightly.trim(keep=2)
    text = paths.nightly_log().read_text(encoding="utf-8")
    assert "run 2" not in text and "run 3" in text and "run 4" in text


def test_last_run_reports_finished_and_unfinished_runs(home):
    from inkvault import nightly
    assert nightly.last_run() is None
    nightly.log(f"{nightly.START} (pid 1) ===")
    nightly.log(f"{nightly.END}: ok (export skipped, backup ok) ===")
    started, finished, result = nightly.last_run()
    assert finished and result == "ok (export skipped, backup ok)"
    nightly.log(f"{nightly.START} (pid 2) ===")  # killed before it could finish
    started, finished, result = nightly.last_run()
    assert finished is None and result is None


TRY = """
import os, sys
os.environ["INKVAULT_HOME"] = sys.argv[1]
from inkvault import nightly
try:
    with nightly.lock():
        print("TOOK")
except nightly.Busy:
    print("BUSY")
"""


def try_in_subprocess(home):
    r = subprocess.run([sys.executable, "-c", TRY, str(home)], capture_output=True, text=True, timeout=60)
    return r.stdout.strip()


def test_lock_blocks_a_second_run_while_another_process_holds_it(home):
    from inkvault import nightly
    assert not nightly.running()
    with holder(home):
        assert nightly.running()
        assert nightly.lock_owner() not in (0, os.getpid())  # the holder's pid (not p.pid: a venv launcher may sit in between)
        with pytest.raises(nightly.Busy):
            with nightly.lock():
                pass
    assert not nightly.running()  # released the moment the holder ended
    with nightly.lock():
        pass


def test_lock_held_here_keeps_another_process_out(home):
    from inkvault import nightly
    with nightly.lock():
        assert try_in_subprocess(home) == "BUSY"
    assert try_in_subprocess(home) == "TOOK"


def test_a_lock_file_left_by_a_dead_process_does_not_block(home):
    from inkvault import nightly, paths
    paths.nightly_lock().write_text("424242")
    assert not nightly.running()
    with nightly.lock():
        pass


def test_lock_owner_ignores_garbage(home):
    from inkvault import nightly, paths
    for text in ("", "abc", "0", str(2**32)):
        paths.nightly_lock().write_text(text)
        assert nightly.lock_owner() == 0


def test_relative_home_is_made_absolute_and_backs_up(tmp_path, monkeypatch):
    from inkvault import export, nightly, paths
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("INKVAULT_HOME", "rel")
    assert paths.home().is_absolute() and paths.home() == (tmp_path / "rel").resolve()
    export.open_vault().close()
    assert nightly.backup(today="2026-10-01")


def test_trim_survives_an_unwritable_log(home, monkeypatch):
    from inkvault import nightly, paths
    for i in range(3):
        nightly.log(f"{nightly.START} run {i} ===")

    def deny(a, b):
        raise PermissionError("log is open elsewhere")
    monkeypatch.setattr(nightly.os, "replace", deny)
    nightly.trim(keep=1)  # must not raise
    assert not paths.nightly_log().with_name("nightly.log.tmp").exists()


def make_vault():
    from inkvault import export
    db = export.open_vault()
    db.execute("INSERT INTO events (id, created, raw) VALUES ('e1', '2026-02-26T18:00:00Z', '{}')")
    db.commit()
    return db  # left open, in WAL mode, like a vault being written to


def test_backup_copies_an_open_vault_and_keeps_seven(home):
    import sqlite3
    from inkvault import nightly, paths
    db = make_vault()
    for day in range(1, 10):
        assert nightly.backup(today=f"2026-10-0{day}")
    db.close()
    names = sorted(p.name for p in paths.backups_dir().iterdir())
    assert names == [f"vault-2026-10-0{d}.db" for d in range(3, 10)]
    copy = sqlite3.connect(paths.backups_dir() / "vault-2026-10-09.db")
    assert copy.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1


def test_backup_without_a_vault_is_skipped(home):
    from inkvault import nightly
    assert nightly.backup() is False


@pytest.fixture
def quick(home, monkeypatch):
    """No PiecesOS, no waiting, no model download, no Ollama."""
    from inkvault import digest, embed, export, nightly
    monkeypatch.setenv("INKVAULT_PIECES_PORTS", "1")  # nothing listens there
    monkeypatch.setattr(nightly, "PIECES_WAIT", 0)
    monkeypatch.setattr(export.PiecesOS, "find", staticmethod(lambda: (None, None)))  # skip the connection-refused delay
    monkeypatch.setattr(embed, "build", lambda: None)
    monkeypatch.setattr(digest, "run", lambda model=None, redo=False: False)
    return home


def test_failed_step_does_not_stop_the_backup(quick, monkeypatch):
    from inkvault import index, nightly, paths
    make_vault().close()

    def broken():
        raise RuntimeError("disk on fire")
    monkeypatch.setattr(index, "build", broken)
    assert nightly.run() == 0
    started, finished, result = nightly.last_run()
    assert result.startswith("ok (")
    assert "export skipped" in result and "index failed: RuntimeError: disk on fire" in result
    assert "backup ok" in result
    assert list(paths.backups_dir().glob("vault-*.db"))
    assert not nightly.running()


def test_failed_backup_fails_the_run(quick, monkeypatch):
    from inkvault import nightly
    make_vault().close()

    def broken(today=None):
        raise OSError("backup drive gone")
    monkeypatch.setattr(nightly, "backup", broken)
    assert nightly.run() == 1
    assert nightly.last_run()[2].startswith("failed (")


def test_step_output_goes_to_the_log(quick):
    from inkvault import nightly, paths
    nightly.run()
    text = paths.nightly_log().read_text(encoding="utf-8")
    assert "PiecesOS not reachable" in text and "no vault yet" in text


def test_second_run_while_one_is_going_exits_quietly(quick, home):
    from inkvault import nightly, paths
    with holder(home):
        assert nightly.run() == 0
    assert "another run is in progress" in paths.nightly_log().read_text(encoding="utf-8")
    assert nightly.last_run() is None  # it never started


def test_waits_for_piecesos_after_a_wake(quick, monkeypatch):
    from inkvault import export, nightly
    calls = []

    def find():
        calls.append(1)
        return (object(), "12.6.2") if len(calls) == 3 else (None, None)
    monkeypatch.setattr(export.PiecesOS, "find", staticmethod(find))
    monkeypatch.setattr(nightly, "PIECES_WAIT", 1000)
    monkeypatch.setattr(nightly.time, "sleep", lambda s: None)
    assert nightly.wait_for_pieces() is not None and len(calls) == 3


def test_flush_does_not_defeat_the_progress_bar_collapse(home):
    from inkvault import nightly, paths
    s = nightly.LogStream()
    s.write("10%\r")
    s.flush()  # tqdm flushes after every redraw
    s.write("100%\n")
    lines = [l[21:] for l in paths.nightly_log().read_text(encoding="utf-8").splitlines()]
    assert lines == ["  100%"]
    assert s.isatty() is False and s.encoding == "utf-8"


def test_unprintable_output_does_not_stop_the_backup(quick, monkeypatch):
    from inkvault import index, nightly
    make_vault().close()

    def noisy():
        print(chr(0xD83D), end="")  # a lone surrogate, no trailing newline
        return True
    monkeypatch.setattr(index, "build", noisy)
    assert nightly.run() == 0
    assert "backup ok" in nightly.last_run()[2]


def test_unexpected_error_outside_the_steps_is_logged(quick, monkeypatch):
    from inkvault import nightly, paths
    monkeypatch.setattr(nightly, "run_steps", lambda: 1 / 0)
    assert nightly.run() == 1
    assert "ZeroDivisionError" in paths.nightly_log().read_text(encoding="utf-8")
    started, finished, result = nightly.last_run()  # status must show it, not "never started"
    assert finished and result == "failed (ZeroDivisionError: division by zero)"
    assert not nightly.running()


def test_a_failure_after_the_start_line_does_not_log_a_second_one(quick, monkeypatch):
    from inkvault import nightly, paths
    monkeypatch.setattr(nightly, "steps", lambda: 1 / 0)  # blows up inside run_steps, after START
    assert nightly.run() == 1
    text = paths.nightly_log().read_text(encoding="utf-8")
    assert text.count(nightly.START) == 1 and "Traceback" in text
    assert nightly.last_run()[2].startswith("failed (ZeroDivisionError")


def test_backup_runs_right_after_export(quick, monkeypatch):
    from inkvault import nightly
    assert [name for name, _ in nightly.steps()] == ["export", "backup", "index", "digest", "dashboard"]


def test_no_vault_is_not_a_plain_ok(quick):
    from inkvault import nightly
    assert nightly.run() == 0
    assert nightly.last_run()[2].startswith("ok, nothing to back up yet (")


def test_backup_that_fails_its_integrity_check_keeps_the_older_ones(home, monkeypatch):
    from inkvault import nightly, paths
    make_vault().close()
    assert nightly.backup(today="2026-10-01")
    monkeypatch.setattr(nightly, "quick_check", lambda path: "*** in database main ***")
    with pytest.raises(RuntimeError, match="integrity check; older backups kept"):
        nightly.backup(today="2026-10-02")
    assert [p.name for p in paths.backups_dir().iterdir()] == ["vault-2026-10-01.db"]  # no partial, none rotated


def test_multiline_step_error_stays_on_one_line(quick, monkeypatch):
    from inkvault import index, nightly
    make_vault().close()

    def broken():
        raise RuntimeError("line one\nline two")
    monkeypatch.setattr(index, "build", broken)
    nightly.run()
    assert "index failed: RuntimeError: line one line two" in nightly.last_run()[2]


def patch_os_lock(monkeypatch, err):
    """Make the platform's lock call fail with errno err: whichever one try_lock really uses on this OS."""
    def fail(*args, **kwargs):
        raise OSError(err, os.strerror(err))
    if sys.platform == "win32":
        import msvcrt
        monkeypatch.setattr(msvcrt, "locking", fail)
    else:
        import fcntl
        monkeypatch.setattr(fcntl, "flock", fail)


def test_filesystem_without_lock_support_runs_unguarded_with_a_warning(quick, monkeypatch):
    import errno
    from inkvault import nightly, paths
    make_vault().close()
    patch_os_lock(monkeypatch, errno.ENOLCK)
    assert nightly.running() is False
    assert nightly.run() == 0
    text = paths.nightly_log().read_text(encoding="utf-8")
    assert "warning: couldn't take the lock" in text and "in progress" not in text
    assert "backup ok" in nightly.last_run()[2]
    assert list(paths.backups_dir().glob("vault-*.db"))


def test_real_contention_errors_count_as_busy(home, monkeypatch):
    import errno
    from inkvault import nightly
    monkeypatch.setattr(nightly.time, "sleep", lambda s: None)
    for err in (errno.EACCES, errno.EAGAIN):
        patch_os_lock(monkeypatch, err)
        assert nightly.running() is True
        with pytest.raises(nightly.Busy):
            with nightly.lock():
                pass


def test_lock_retries_once_before_giving_up(home, monkeypatch):
    from inkvault import nightly
    calls = []
    real = nightly.try_lock

    def flaky(fd):
        calls.append(1)
        if len(calls) == 1:
            return False
        return real(fd)
    monkeypatch.setattr(nightly, "try_lock", flaky)
    monkeypatch.setattr(nightly.time, "sleep", lambda s: calls.append("slept"))
    with nightly.lock():
        pass
    assert calls[:3] == [1, "slept", 1]


def test_busy_message_says_when_the_pid_is_unknown(quick, monkeypatch):
    from inkvault import nightly, paths
    import contextlib

    @contextlib.contextmanager
    def busy():
        raise nightly.Busy(0)
        yield
    monkeypatch.setattr(nightly, "lock", busy)
    assert nightly.run() == 0
    assert "pid unknown" in paths.nightly_log().read_text(encoding="utf-8")
