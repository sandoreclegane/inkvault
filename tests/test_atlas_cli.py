"""`inkvault dashboard`, the Windows icon, rescue and status (spec §3.6)."""
import json
import subprocess
import sys

import pytest

import atlasdata


def test_dashboard_command_launches_the_helper(tmp_path, monkeypatch):
    atlasdata.make_vault(tmp_path, monkeypatch)
    from inkvault import atlasserve, cli, paths
    calls = []
    monkeypatch.setattr(atlasserve, "serve", lambda open_browser=True, **_: calls.append(open_browser) or 0)
    assert cli.main(["dashboard"]) == 0 and calls == [True]
    assert paths.dashboard().exists()  # built before serving when missing
    assert cli.main(["dashboard", "--no-open"]) == 0 and calls == [True, False]


def fake_powershell(monkeypatch, readback):
    from inkvault import atlasserve
    seen = {}

    def run(cmd, **kw):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(readback(cmd)), stderr="")
    monkeypatch.setattr(atlasserve.subprocess, "run", run)
    monkeypatch.setattr(atlasserve.sys, "platform", "win32")
    return seen


def test_shortcut_script_quotes_the_home_path(tmp_path, monkeypatch, capsys):
    home = tmp_path / "O'Brien vault"
    home.mkdir()
    monkeypatch.setenv("INKVAULT_HOME", str(home))
    from inkvault import atlasserve, paths
    exe = atlasserve.pythonw()
    args = f'-m inkvault --home "{paths.home()}" dashboard'
    seen = fake_powershell(monkeypatch, lambda cmd: [{"path": "Desktop\\InkVault Atlas.lnk", "target": str(exe), "arguments": args},
                                                    {"path": "Programs\\InkVault Atlas.lnk", "target": str(exe), "arguments": args}])
    assert atlasserve.make_shortcut() == 0
    script = seen["cmd"][-1]
    assert "O''Brien vault" in script and "GetFolderPath('Programs')" in script and "inkvault.ico" in script
    assert "InkVault Atlas" in capsys.readouterr().out


def test_a_shortcut_that_reads_back_wrong_is_reported(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("INKVAULT_HOME", str(tmp_path))
    from inkvault import atlasserve
    fake_powershell(monkeypatch, lambda cmd: [{"path": "x.lnk", "target": "C:\\other.exe", "arguments": ""}])
    assert atlasserve.make_shortcut() == 1
    assert "didn't read back" in capsys.readouterr().out


def test_shortcut_elsewhere_says_windows_only(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("INKVAULT_HOME", str(tmp_path))
    from inkvault import atlasserve
    monkeypatch.setattr(atlasserve.sys, "platform", "linux")
    assert atlasserve.make_shortcut() == 0
    assert "Windows-only" in capsys.readouterr().out


def test_rescue_opens_the_live_atlas_in_the_background(tmp_path, monkeypatch):
    atlasdata.make_vault(tmp_path, monkeypatch)
    from inkvault import atlasserve, cli, export
    started = []
    monkeypatch.setattr(atlasserve, "start_detached", lambda: started.append(True))

    def interrupted():
        raise KeyboardInterrupt
    monkeypatch.setattr(export, "run", interrupted)
    assert cli.main(["rescue"]) == 130 and started == [True]
    started.clear()
    assert cli.main(["rescue", "--no-open"]) == 130 and started == []


def test_status_reports_the_atlas(tmp_path, monkeypatch, capsys):
    atlasdata.make_vault(tmp_path, monkeypatch)
    monkeypatch.setenv("INKVAULT_PIECES_PORTS", "1")
    from inkvault import cli, dashboard
    dashboard.build()
    capsys.readouterr()
    cli.main(["status"])
    out = capsys.readouterr().out
    assert "atlas: built" in out and "inkvault dashboard" in out
