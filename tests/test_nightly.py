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
