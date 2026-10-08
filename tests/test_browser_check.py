"""The manual real-history check (tests/manual/browser_check.py), run here on synthetic history only."""
import importlib.util
from pathlib import Path

import pytest

import webfixtures


def load():
    spec = importlib.util.spec_from_file_location("browser_check", Path(__file__).parent / "manual" / "browser_check.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def harness(tmp_path, monkeypatch):
    from inkvault import embed
    monkeypatch.setenv("INKVAULT_HOME", str(tmp_path / "unused"))  # the check sets its own; this restores it after
    monkeypatch.setattr(embed, "build", embed.build)  # the check replaces it; this puts it back after
    h = webfixtures.chromium_profile(tmp_path / "real" / "chrome", "Default", name="Default")
    webfixtures.chromium_visit(h, 1, "https://example.com/", "2026-10-05T12:00:00Z")
    allowed = {("chrome", "Default"): h, ("edge", "Default"): tmp_path / "real" / "edge" / "Default" / "History"}
    temp = tmp_path / "temp"
    temp.mkdir()
    return load(), allowed, temp


def test_the_check_prints_counts_and_deletes_its_folder(harness, capsys):
    check, allowed, temp = harness
    assert check.check(allowed, temp) == 0
    out = capsys.readouterr().out
    assert "edge/Default: not found, skipped" in out and "sync: ok, 1 profiles, failed: none" in out
    assert "chrome/Default: 1 stored, 1 counted, 0 synced from other devices" in out
    assert "example.com" not in out
    assert list(temp.iterdir()) == []


def test_a_failed_check_still_closes_everything_and_deletes_its_folder(harness, monkeypatch):
    from inkvault import history
    check, allowed, temp = harness

    def broken(raw, db):
        raise RuntimeError("index broke")
    monkeypatch.setattr(history, "index_into", broken)
    assert check.check(allowed, temp) == 1
    assert list(temp.iterdir()) == []  # on Windows this needs every connection closed


def test_a_folder_that_cant_be_deleted_is_a_failure_that_says_where(harness, monkeypatch, capsys):
    check, allowed, temp = harness
    monkeypatch.setattr(check.shutil, "rmtree", lambda *a, **k: None)
    assert check.check(allowed, temp) == 2
    assert "COULDN'T DELETE" in capsys.readouterr().out
