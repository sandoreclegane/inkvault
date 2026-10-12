"""The watch command (spec §3, §7): preview before recording, truthful install and uninstall, cooperative stop."""
import json
import os
import sys

import pytest

TOOL = "C:\\Tools\\inkvault\\Scripts\\pythonw.exe"


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("INKVAULT_HOME", str(tmp_path))
    return tmp_path


def run(*argv):
    from inkvault import cli
    return cli.main(list(argv))


def test_apps_on_a_fresh_install_previews_without_recording(home, capsys):    # P5
    from inkvault import paths, watch
    assert run("watch", "--apps") == 0
    out = capsys.readouterr().out
    assert "comet.exe" in out and "never titles" in out and paths.screen_file().exists()
    assert not watch.lock_held() and not paths.watch_heartbeat().exists()


def test_edits_report_truthfully(home, capsys):
    from inkvault import paths
    assert run("watch", "--add-app", "chrome.exe") == 0
    assert "every Chrome profile" in capsys.readouterr().out
    assert run("watch", "--never-title", "Account overview") == 0
    assert run("watch", "--pause", "1h") == 0
    d = json.loads(paths.screen_file().read_text())
    assert "chrome.exe" in d["text_apps"] and "Account overview" in d["never_titles"] and d["paused_until"]
    assert "within 2 seconds" in capsys.readouterr().out
    assert run("watch", "--pause", "forever") == 1


def test_stop_without_a_watcher(home, capsys):
    assert run("watch", "--stop") == 0 and "isn't running" in capsys.readouterr().out


def test_stop_timeout_is_not_success(home, monkeypatch, capsys):
    from inkvault import watch
    monkeypatch.setattr(watch, "lock_held", lambda: True)
    monkeypatch.setattr(watch, "STOP_WAIT", 0.2)
    assert run("watch", "--stop") == 1 and "may still be running" in capsys.readouterr().out


@pytest.fixture
def win(home, monkeypatch):
    """A fake Windows: a tool, schtasks via the scheduler module, and a watcher that may or may not start."""
    from inkvault import schedule, watch
    from inkvault.schedulers import windows
    monkeypatch.setattr(sys, "platform", "win32")
    state = {"xml": None, "enabled": True, "starts": True, "version": (0, 3, 0), "calls": []}
    monkeypatch.setattr(schedule, "tool_python", lambda: __import__("pathlib").PureWindowsPath(TOOL))
    monkeypatch.setattr(schedule, "installed_version", lambda exe: state["version"])
    monkeypatch.setattr(windows, "install_logon", lambda argv, user, task=windows.WATCH_TASK:
                        state.update(xml=windows.render_logon(argv, user)) or state["calls"].append("create"))

    def read_back(task):
        if state.get("unknown"):
            raise windows.TaskUnknown("access denied")
        if state["xml"] is None:
            return None
        return {"command": TOOL, "arguments": ["-m", "inkvault", "--home", str(home), "watch"], "user": "PC\\me",
                "logon_type": "InteractiveToken", "limit": "PT0S", "restart": ("PT1M", "3"),
                "instances": "IgnoreNew", "enabled": state["enabled"], "trigger_enabled": True,
                "trigger_user": "PC\\me"}
    monkeypatch.setattr(windows, "read_back", read_back)
    monkeypatch.setattr(windows, "current_user", lambda: "PC\\me")
    monkeypatch.setattr(windows, "run_task", lambda task: state["calls"].append("run"))
    monkeypatch.setattr(windows, "set_enabled", lambda on, task=None: state.update(enabled=on)
                        or state["calls"].append("enable" if on else "disable"))
    monkeypatch.setattr(windows, "remove", lambda task=None: state.update(xml=None) or state["calls"].append("delete")
                        or True)
    monkeypatch.setattr(watch, "running_confirmed", lambda wait, since: state["starts"])
    monkeypatch.setattr(watch, "make_shortcuts", lambda pythonw: None)
    return state


def test_stop_needs_no_settings_and_no_vault(home, monkeypatch, capsys):          # R2
    from inkvault import paths, screen, watch
    screen.prepare()
    paths.screen_file().unlink()                                                   # lost settings
    held, stops = {"v": True}, []
    monkeypatch.setattr(watch, "lock_held", lambda: held["v"])
    monkeypatch.setattr(paths, "watch_stop", lambda: type("F", (), {
        "write_text": lambda self, *a, **k: stops.append(1) or held.update(v=False)})())
    assert run("watch", "--stop") == 0 and stops == [1] and "Stopped." in capsys.readouterr().out


def test_pause_works_while_a_sqlite_writer_holds_the_vault(home):                  # R2
    from inkvault import export, paths, screen
    screen.prepare()
    blocker = export.open_vault(); blocker.execute("BEGIN IMMEDIATE")
    try:
        assert run("watch", "--pause", "1h") == 0
    finally:
        blocker.rollback(); blocker.close()
    assert json.loads(paths.screen_file().read_text())["paused_until"]


def test_install_with_a_watcher_already_running_says_so(win, monkeypatch, capsys):  # R4
    from inkvault import watch
    monkeypatch.setattr(watch, "lock_held", lambda: True)
    monkeypatch.setattr(watch, "lock_owner", lambda: 4242)
    assert watch.install(ask=lambda q: False) == 0
    out = capsys.readouterr().out
    assert "already running (pid 4242)" in out and "run" not in win["calls"]


def test_uninstall_refuses_unknown_task_state(win, capsys):                        # R4
    from inkvault import watch
    win["unknown"] = True
    assert watch.uninstall() == 1 and win["calls"] == [] and "couldn't say" in capsys.readouterr().out


def test_heartbeat_checks(home, monkeypatch):                                       # R4
    from datetime import timedelta
    from inkvault import screen, watch
    monkeypatch.setattr(watch, "lock_held", lambda: True)
    monkeypatch.setattr(watch, "lock_owner", lambda: 7)
    now = screen.now()
    ok = {"pid": 7, "utc": screen.iso(now)}
    assert watch.heartbeat_ok(ok, since=now - timedelta(seconds=5))
    assert not watch.heartbeat_ok({**ok, "utc": screen.iso(now + timedelta(minutes=5))})       # from the future
    assert not watch.heartbeat_ok({**ok, "utc": screen.iso(now - timedelta(minutes=5))})       # stale
    assert not watch.heartbeat_ok({**ok, "pid": 8})                                             # someone else's
    assert not watch.heartbeat_ok(ok, since=now + timedelta(seconds=5))                         # before this start


def test_install_confirms_before_claiming_on(win, capsys):
    from inkvault import watch
    assert watch.install(ask=lambda q: False) == 0
    assert win["calls"] == ["create", "run"] and "on now and at every sign-in" in capsys.readouterr().out


def test_install_without_a_running_watcher_is_not_success(win, capsys):
    from inkvault import watch
    win["starts"] = False
    assert watch.install(ask=lambda q: False) == 1 and "hasn't confirmed" in capsys.readouterr().out


def test_install_refuses_an_old_tool(win, capsys):
    from inkvault import watch
    win["version"] = (0, 2, 1)
    assert watch.install(ask=lambda q: False) == 1 and "inkvault[screen]" in capsys.readouterr().out
    assert win["calls"] == []
    from inkvault import paths
    assert not paths.screen_file().exists()


def test_install_removes_a_task_that_reads_back_wrong(win, monkeypatch, capsys):
    from inkvault import watch
    from inkvault.schedulers import windows
    real = windows.read_back
    monkeypatch.setattr(windows, "read_back", lambda t: {**real(t), "limit": "PT72H"} if real(t) else None)
    assert watch.install(ask=lambda q: False) == 1 and "delete" in win["calls"] and "run" not in win["calls"]


def test_install_asks_about_messaging_and_chrome(win):
    from inkvault import paths, screen, watch
    asked = []
    watch.install(ask=lambda q: asked.append(q) or "messag" in q.lower())
    d = json.loads(paths.screen_file().read_text())
    assert len(asked) == 2 and "Discord.exe" in d["text_apps"] and "chrome.exe" not in d["text_apps"]


def test_uninstall_order_and_unknown_home(win, monkeypatch, capsys):
    from inkvault import watch
    from inkvault.schedulers import windows
    win["xml"] = "x"
    monkeypatch.setattr(watch, "stop_watcher", lambda wait=None: win["calls"].append("stop") or True)
    assert watch.uninstall() == 0 and win["calls"] == ["disable", "stop", "delete"]          # never force-ended
    win["xml"], win["calls"] = "x", []
    real = windows.read_back
    monkeypatch.setattr(windows, "read_back", lambda t: {**real(t), "arguments": ["-m", "inkvault", "watch"]})
    assert watch.uninstall() == 1 and win["calls"] == [] and "can't tell" in capsys.readouterr().out.lower()


def test_uninstall_stop_timeout_leaves_the_task_disabled(win, monkeypatch, capsys):
    from inkvault import watch
    win["xml"] = "x"
    monkeypatch.setattr(watch, "stop_watcher", lambda wait=None: False)
    assert watch.uninstall() == 1 and win["xml"] == "x" and win["enabled"] is False
    assert "may still be running" in capsys.readouterr().out


def test_status_lines(home, monkeypatch):
    from inkvault import watch
    from inkvault.schedulers import windows
    monkeypatch.setattr(windows, "read_back", lambda task: None)
    assert any(l.startswith("screen capture:") for l in watch.status_lines())


def test_install_reports_a_failed_removal_truthfully(win, monkeypatch, capsys):
    from inkvault import watch
    from inkvault.schedulers import windows
    real = windows.read_back
    monkeypatch.setattr(windows, "read_back", lambda t: {**real(t), "limit": "PT72H"} if real(t) else None)

    def boom(task=None):
        raise RuntimeError("denied")
    monkeypatch.setattr(windows, "remove", boom)
    assert watch.install(ask=lambda q: False) == 1
    out = capsys.readouterr().out
    assert "couldn't be removed" in out and "so it was removed" not in out


def test_install_handles_an_unknown_read_back(win, monkeypatch, capsys):
    from inkvault import watch
    from inkvault.schedulers import windows

    def unknown(task):
        raise windows.TaskUnknown("access denied")
    monkeypatch.setattr(windows, "read_back", unknown)
    assert watch.install(ask=lambda q: False) == 1
    assert "so it was removed" not in capsys.readouterr().out


def test_install_reports_a_registration_failure(win, monkeypatch, capsys):
    from inkvault import watch
    from inkvault.schedulers import windows

    def refuse(*a, **k):
        raise RuntimeError("no")
    monkeypatch.setattr(windows, "install_logon", refuse)
    assert watch.install(ask=lambda q: False) == 1 and "refused to register" in capsys.readouterr().out


def test_install_reports_a_start_failure(win, monkeypatch, capsys):
    from inkvault import watch
    from inkvault.schedulers import windows

    def refuse(task):
        raise RuntimeError("no")
    monkeypatch.setattr(windows, "run_task", refuse)
    assert watch.install(ask=lambda q: False) == 1 and "couldn't be started now" in capsys.readouterr().out


def test_status_lock_held_without_a_heartbeat(home, monkeypatch):
    from inkvault import watch
    monkeypatch.setattr(watch, "lock_held", lambda: True)
    monkeypatch.setattr(watch, "lock_owner", lambda: 99)
    assert "screen capture: running (pid 99 from watch.lock), heartbeat unreadable" in watch.status_lines()


def test_status_shows_heartbeat_errors_and_never_raises_on_a_malformed_one(home, monkeypatch):
    from inkvault import paths, screen, watch
    monkeypatch.setattr(watch, "lock_held", lambda: True)
    monkeypatch.setattr(watch, "lock_owner", lambda: 5)
    paths.watch_heartbeat().write_text(json.dumps(
        {"pid": 5, "utc": screen.iso(screen.now()), "settings_error": "SettingsError", "db_error": "OperationalError"}))
    out = watch.status_lines()
    assert "  settings: SettingsError" in out and "  database: OperationalError" in out
    for bad in ('{"pid": 5}', '{"utc": "garbage"}', '{"utc": 5, "open": 3}', '[1, 2]', 'not json',
                json.dumps({"utc": screen.iso(screen.now()), "open": "x", "cooling": 4})):
        paths.watch_heartbeat().write_text(bad)
        assert any("heartbeat unreadable" in l for l in watch.status_lines()), bad


def test_status_unknown_task_state(home, monkeypatch):
    from inkvault import watch
    from inkvault.schedulers import windows
    monkeypatch.setattr(sys, "platform", "win32")

    def unknown(task):
        raise windows.TaskUnknown("x")
    monkeypatch.setattr(windows, "read_back", unknown)
    assert any("sign-in task: unknown" in l for l in watch.status_lines())
