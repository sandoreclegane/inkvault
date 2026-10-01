"""The nightly run: lock, log, backup, and steps that fail without stopping the others."""
import subprocess
import sys

import pytest


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("INKVAULT_HOME", str(tmp_path))
    return tmp_path


def test_nightly_paths_live_in_the_vault_folder(home):
    from inkvault import paths
    assert paths.nightly_log() == home / "nightly.log"
    assert paths.nightly_lock() == home / "nightly.lock"
    assert paths.backups_dir() == home / "backups"
    assert paths.schedule_file() == home / "schedule.json"


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
    s.flush()
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


def test_lock_blocks_a_second_run_while_the_first_is_alive(home):
    import os
    from inkvault import nightly, paths
    paths.nightly_lock().write_text(str(os.getpid()))  # this test process is certainly alive
    with pytest.raises(nightly.Busy):
        with nightly.lock():
            pass
    assert nightly.running()


def test_stale_lock_is_taken_over_and_released(home, monkeypatch):
    from inkvault import nightly, paths
    paths.nightly_lock().write_text("424242")
    monkeypatch.setattr(nightly, "pid_alive", lambda pid: False)  # its process is gone
    with nightly.lock():
        assert paths.nightly_lock().exists()
    assert not paths.nightly_lock().exists()
    assert not nightly.running()


def test_pid_alive_knows_this_process():
    import os
    from inkvault import nightly
    assert nightly.pid_alive(os.getpid())


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
    from inkvault import digest, embed, nightly
    monkeypatch.setenv("INKVAULT_PIECES_PORTS", "1")  # nothing listens there
    monkeypatch.setattr(nightly, "PIECES_WAIT", 0)
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
    assert not paths.nightly_lock().exists()


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


def test_second_run_while_one_is_going_exits_quietly(quick):
    import os
    from inkvault import nightly, paths
    paths.nightly_lock().write_text(str(os.getpid()))
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
