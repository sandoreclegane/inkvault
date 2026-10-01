"""Scheduling: the task definitions for each OS (pure, so tested on every OS) and `inkvault schedule` itself."""
import json

import pytest

ARGV = ["C:/Users/a b/uv/tools/inkvault/Scripts/pythonw.exe", "-m", "inkvault", "--home", "D:/My Vault", "nightly"]
NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}


def test_windows_task_xml():
    import xml.etree.ElementTree as ET
    from inkvault.schedulers import windows
    root = ET.fromstring(windows.render(ARGV, 3, 5, wake=True).split("?>", 1)[1])
    s = root.find("t:Settings", NS)
    assert root.find(".//t:CalendarTrigger/t:StartBoundary", NS).text.endswith("T03:05:00")
    assert root.find(".//t:ScheduleByDay/t:DaysInterval", NS).text == "1"
    assert s.find("t:StartWhenAvailable", NS).text == "true"
    assert s.find("t:WakeToRun", NS).text == "true"
    assert s.find("t:RestartOnFailure/t:Count", NS).text == "3"
    assert s.find("t:RestartOnFailure/t:Interval", NS).text == "PT15M"
    assert s.find("t:ExecutionTimeLimit", NS).text == "PT4H"
    assert s.find("t:DisallowStartIfOnBatteries", NS).text == "false"
    assert root.find(".//t:Principal/t:LogonType", NS).text == "InteractiveToken"
    assert root.find(".//t:Exec/t:Command", NS).text == ARGV[0]
    assert root.find(".//t:Exec/t:Arguments", NS).text == '-m inkvault --home "D:/My Vault" nightly'
    no_wake = ET.fromstring(windows.render(ARGV, 3, 5, wake=False).split("?>", 1)[1])
    assert no_wake.find("t:Settings/t:WakeToRun", NS).text == "false"


def test_windows_wake_timer_check():
    from inkvault.schedulers import windows
    off = "    Current AC Power Setting Index: 0x00000000\n    Current DC Power Setting Index: 0x00000000\n"
    on = "    Current AC Power Setting Index: 0x00000001\n"
    assert windows.wake_timers_allowed(off) is False
    assert windows.wake_timers_allowed(on) is True
    assert windows.wake_timers_allowed("(localized output we can't read)") is True  # don't warn on a guess


def test_macos_launch_agent():
    import plistlib
    from pathlib import Path
    from inkvault.schedulers import macos
    plist = plistlib.loads(macos.render(ARGV, 3, 5, Path("/tmp/launchd.log")))
    assert plist["Label"] == "org.inkvault.nightly"
    assert plist["ProgramArguments"] == ARGV
    assert plist["StartCalendarInterval"] == {"Hour": 3, "Minute": 5}
    assert plist["StandardErrorPath"] == str(Path("/tmp/launchd.log"))


def test_macos_wake_note_wakes_two_minutes_early():
    from inkvault.schedulers import macos
    assert "sudo pmset repeat wakeorpoweron MTWRFSU 02:58:00" in macos.wake_note(3, 0)
    assert "23:59:00" in macos.wake_note(0, 1)  # wraps past midnight


def test_linux_systemd_units():
    from inkvault.schedulers import linux
    service = linux.render_service(ARGV)
    timer = linux.render_timer(3, 5)
    assert "Type=oneshot" in service
    assert "'C:/Users/a b/uv/tools/inkvault/Scripts/pythonw.exe'" in service
    assert "OnCalendar=*-*-* 03:05:00" in timer
    assert "Persistent=true" in timer
    assert "WantedBy=timers.target" in timer


def test_linux_crontab_merge_replaces_only_our_line():
    from inkvault.schedulers import linux
    line = linux.cron_line(["/home/u/.local/share/uv/tools/inkvault/bin/python", "-m", "inkvault", "nightly"], 3, 5)
    assert line.startswith("5 3 * * * ") and line.endswith(linux.TAG)
    theirs = "0 9 * * 1 backup.sh\n"
    merged = linux.merge_crontab(theirs, line)
    assert merged == theirs + line + "\n"
    assert linux.merge_crontab(merged, line) == merged  # running schedule twice doesn't duplicate it
    assert linux.merge_crontab(merged, None) == theirs  # --off removes only ours
    assert linux.merge_crontab(line + "\n", None) == ""



@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("INKVAULT_HOME", str(tmp_path))
    monkeypatch.delenv("INKVAULT_PIECES_PORTS", raising=False)
    return tmp_path


def test_parse_at_and_next_run():
    from datetime import datetime
    from inkvault import schedule
    assert schedule.parse_at("03:00") == (3, 0)
    assert schedule.parse_at("23:45") == (23, 45)
    for bad in ("3am", "25:00", ""):
        with pytest.raises(schedule.ScheduleError):
            schedule.parse_at(bad)
    assert schedule.next_run(3, 0, datetime(2026, 10, 1, 1, 0)) == datetime(2026, 10, 1, 3, 0)
    assert schedule.next_run(3, 0, datetime(2026, 10, 1, 3, 0)) == datetime(2026, 10, 2, 3, 0)


def test_job_argv_carries_home_and_ports(home, monkeypatch):
    from pathlib import Path
    from inkvault import schedule
    exe = Path("/tools/inkvault/bin/python")
    assert schedule.job_argv(exe)[-1] == "nightly"
    assert schedule.job_argv(exe)[:3] == [str(exe), "-m", "inkvault"]
    assert ["--home", str(home.resolve())] == schedule.job_argv(exe)[3:5]
    monkeypatch.setenv("INKVAULT_PIECES_PORTS", "39301")
    assert "--pieces-ports" in schedule.job_argv(exe) and "39301" in schedule.job_argv(exe)


def test_ask_wake(monkeypatch):
    from inkvault import schedule
    assert schedule.ask_wake(True, interactive=False) is True
    assert schedule.ask_wake(False, interactive=True) is False  # a flag wins over asking
    assert schedule.ask_wake(None, interactive=False) is False  # no terminal: don't wake
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    assert schedule.ask_wake(None, interactive=True) is True
    monkeypatch.setattr("builtins.input", lambda prompt: "")
    assert schedule.ask_wake(None, interactive=True) is False  # default is no


class FakeBackend:
    def __init__(self):
        self.calls = []
        self.on = False

    def install(self, argv, hour, minute, wake):
        self.calls.append((argv, hour, minute, wake))
        self.on = True
        return "a fake scheduler"

    def remove(self):
        was, self.on = self.on, False
        return was

    def installed(self):
        return self.on

    def wake_note(self, hour, minute):
        return "WAKE NOTE"


def test_enable_describe_disable(home, monkeypatch, capsys):
    from pathlib import Path
    from inkvault import schedule
    fake = FakeBackend()
    monkeypatch.setattr(schedule, "backend", lambda: fake)
    monkeypatch.setattr(schedule, "ensure_installed", lambda: Path("/tools/python"))
    monkeypatch.setattr(schedule, "remember_pieces", lambda: None)
    assert schedule.describe().startswith("off")

    schedule.enable("02:30", wake=True)
    argv, hour, minute, wake = fake.calls[0]
    assert (hour, minute, wake) == (2, 30, True) and argv[-1] == "nightly"
    out = capsys.readouterr().out
    assert "02:30" in out and "a fake scheduler" in out and "WAKE NOTE" in out
    assert json.loads(schedule.paths.schedule_file().read_text()) == {"at": "02:30", "wake": True}
    assert schedule.describe().startswith("daily at 02:30, wakes the computer; next ")

    schedule.disable()
    assert not fake.on and not schedule.paths.schedule_file().exists()
    assert schedule.describe().startswith("off")


def test_ensure_installed_explains_missing_uv(monkeypatch):
    from inkvault import schedule
    monkeypatch.setattr(schedule.shutil, "which", lambda name: None)
    with pytest.raises(schedule.ScheduleError, match="needs uv"):
        schedule.ensure_installed()


def test_tool_python_points_into_uv_tool_dir(tmp_path, monkeypatch):
    import subprocess
    import sys
    from inkvault import schedule
    exe = (tmp_path / "inkvault" / "Scripts" / "pythonw.exe" if sys.platform == "win32"
           else tmp_path / "inkvault" / "bin" / "python")
    monkeypatch.setattr(schedule.shutil, "which", lambda name: "/usr/bin/uv")
    monkeypatch.setattr(schedule.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=f"{tmp_path}\n", stderr=""))
    assert schedule.tool_python() is None  # uv works, but InkVault isn't installed as a tool
    exe.parent.mkdir(parents=True)
    exe.touch()
    assert schedule.tool_python() == exe
