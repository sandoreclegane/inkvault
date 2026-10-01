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
