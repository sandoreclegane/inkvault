"""`inkvault forget --screen` (spec §8, P4): local input, the normalized range and the actual span shown."""
import json

import pytest
from zoneinfo import ZoneInfo


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("INKVAULT_HOME", str(tmp_path))
    from inkvault import export, screen
    monkeypatch.setattr(screen, "LOCAL_TZ", ZoneInfo("America/Chicago"))
    monkeypatch.setattr("inkvault.forget.finish", lambda: None)
    v = export.open_vault()
    v.execute("INSERT INTO screen_stretches VALUES ('screen:a','2026-10-12T14:00:00.000000Z',"
              "'2026-10-12T16:00:00.000000Z','Discord.exe','Chat')")         # 09:00–11:00 in Chicago
    v.commit(); v.close()
    return tmp_path


def run(*argv):
    from inkvault import cli
    return cli.main(list(argv))


def recorded():
    from inkvault import export
    v = export.open_vault()
    try:
        return v.execute("SELECT value FROM browser_removals").fetchall()
    finally:
        v.close()


def test_local_input_is_local(home, capsys):
    assert run("forget", "--screen", "--since", "2026-10-12T10:00", "--until", "2026-10-12T10:30", "--yes") == 0
    out = capsys.readouterr().out
    assert "1 stretch" in out and "wider than the range" in out and "Backups" in out
    assert json.loads(recorded()[0][0]) == {"since": "2026-10-12T15:00:00.000000Z",
                                             "until": "2026-10-12T15:30:00.000000Z"}


def test_a_utc_range_that_misses_locally_removes_nothing(home, capsys):
    assert run("forget", "--screen", "--since", "2026-10-12T17:00Z", "--until", "2026-10-12T18:00Z", "--yes") == 0
    assert "Nothing recorded matches" in capsys.readouterr().out and recorded() == []


def test_bad_ranges(home, capsys):
    assert run("forget", "--screen", "--since", "2026-03-08T02:30", "--until", "2026-03-08T04:00", "--yes") == 1
    assert "doesn't exist" in capsys.readouterr().out
    assert run("forget", "--screen", "--since", "2026-10-12", "--until", "2026-10-11", "--yes") == 1
    assert run("forget", "--screen", "--since", "2026-10-12", "--yes") == 1


def test_app_reminder(home, capsys):
    assert run("forget", "--screen", "--app", "discord.exe", "--yes") == 0
    assert "inkvault watch --never-app discord.exe" in capsys.readouterr().out


def test_a_failed_finish_is_reported_not_a_traceback(home, capsys, monkeypatch):
    def boom():
        raise RuntimeError("disk")
    monkeypatch.setattr("inkvault.forget.finish", boom)
    assert run("forget", "--screen", "--app", "discord.exe", "--yes") == 1
    assert "didn't finish (RuntimeError)" in capsys.readouterr().out


def test_an_error_while_recording_is_reported(home, capsys, monkeypatch):
    def boom(what, value):
        raise RuntimeError("locked")
    monkeypatch.setattr("inkvault.forget.screen_request", boom)
    assert run("forget", "--screen", "--app", "discord.exe", "--yes") == 1
    assert "RuntimeError" in capsys.readouterr().out
