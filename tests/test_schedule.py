"""Scheduling: the task definitions for each OS (pure, so tested on every OS) and `inkvault schedule` itself."""
import json

import pytest
from conftest import holder  # a real lock held by another process, as the nightly run would hold it

ARGV = ["C:/Users/a b/uv/tools/inkvault/Scripts/pythonw.exe", "-m", "inkvault", "--home", "D:/My Vault", "nightly"]
NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}


@pytest.fixture(autouse=True)
def never_touch_the_real_uv_or_schedulers(monkeypatch):
    """uv sets UV for the process running the tests, which would make the code under test find the real uv.
    Any subprocess a test hasn't faked fails loudly instead of running (uv tool install, schtasks, ...)."""
    import subprocess
    monkeypatch.delenv("UV", raising=False)

    def refuse(cmd, *a, **k):
        raise AssertionError(f"a test ran a real command: {cmd}")
    monkeypatch.setattr(subprocess, "run", refuse)


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
    assert root.find(".//t:Exec/t:Command", NS).text == '"' + ARGV[0] + '"'
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
    assert plist["ProgramArguments"] == ["/usr/bin/caffeinate", "-i", *ARGV]
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


def test_job_argv_always_pins_the_vault_folder(tmp_path, monkeypatch):
    from pathlib import Path
    from inkvault import schedule
    monkeypatch.delenv("INKVAULT_HOME", raising=False)  # a default location: cron wouldn't find it by itself
    monkeypatch.setattr(schedule.paths, "home", lambda: tmp_path)
    assert schedule.job_argv(Path("/p"))[3:5] == ["--home", str(tmp_path)]


def test_enable_says_when_there_is_no_vault_yet(home, monkeypatch, capsys):
    from pathlib import Path
    from inkvault import export, schedule
    fake = FakeBackend()
    monkeypatch.setattr(schedule, "backend", lambda: fake)
    monkeypatch.setattr(schedule, "ensure_installed", lambda: Path("/tools/python"))
    monkeypatch.setattr(schedule, "remember_pieces", lambda: None)
    schedule.enable("03:00", wake=False)
    out = capsys.readouterr().out
    assert f"No vault at {home.resolve()} yet" in out and "same --home" in out and fake.on  # still scheduled
    export.open_vault().close()
    schedule.enable("03:00", wake=False)
    assert "No vault" not in capsys.readouterr().out


def test_ask_wake(monkeypatch):
    from inkvault import schedule
    for platform in ("win32", "darwin"):
        assert schedule.ask_wake(True, interactive=False, platform=platform) is True
        assert schedule.ask_wake(False, interactive=True, platform=platform) is False  # a flag wins over asking
        assert schedule.ask_wake(None, interactive=False, platform=platform) is False  # no terminal: don't wake
        monkeypatch.setattr("builtins.input", lambda prompt: "y")
        assert schedule.ask_wake(None, interactive=True, platform=platform) is True
        monkeypatch.setattr("builtins.input", lambda prompt: "")
        assert schedule.ask_wake(None, interactive=True, platform=platform) is False  # default is no
    assert schedule.ask_wake(True, interactive=True, platform="linux") is False  # Linux can't wake


class FakeBackend:
    PLATFORM = "win32"  # so the wake prompt behaves the same whatever OS runs the tests

    def __init__(self, on=False, **job):
        self.calls = []
        self.on = on
        self.job = job  # what query() reports beyond present: enabled, home, executable

    def install(self, argv, hour, minute, wake):
        self.calls.append((argv, hour, minute, wake))
        self.on = True
        return "a fake scheduler"

    def remove(self):
        was, self.on = self.on, False
        return was

    def query(self):
        from inkvault.schedulers import Job
        return Job(self.on, **{"enabled": True, "scheduler": "Fake Scheduler", **self.job})

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
                        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=f"{tmp_path}\n".encode(), stderr=b""))
    assert schedule.tool_python() is None  # uv works, but InkVault isn't installed as a tool
    exe.parent.mkdir(parents=True)
    exe.touch()
    assert schedule.tool_python() == exe


def test_cli_schedule_and_status(home, monkeypatch, capsys):
    from pathlib import Path
    from inkvault import cli, nightly, schedule
    fake = FakeBackend()
    monkeypatch.setattr(schedule, "backend", lambda: fake)
    monkeypatch.setattr(schedule, "ensure_installed", lambda: Path("/tools/python"))
    monkeypatch.setattr(schedule, "remember_pieces", lambda: None)
    monkeypatch.setenv("INKVAULT_PIECES_PORTS", "1")

    assert cli.main(["schedule", "--at", "04:15", "--no-wake"]) == 0
    assert fake.calls[0][1:] == (4, 15, False)
    assert cli.main(["schedule", "--at", "4pm"]) == 1
    assert "24-hour time" in capsys.readouterr().out

    nightly.log(f"{nightly.START} (pid 1) ===")
    assert cli.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "nightly: daily at 04:15; next " in out
    assert "started but never finished" in out

    assert cli.main(["schedule", "--off"]) == 0
    assert not fake.on


def test_cli_pieces_ports_flag_sets_the_env(home, monkeypatch):
    import os
    from inkvault import cli
    monkeypatch.setenv("INKVAULT_PIECES_PORTS", "9")  # so monkeypatch restores the original after the test
    cli.main(["--pieces-ports", "1", "status"])
    assert os.environ["INKVAULT_PIECES_PORTS"] == "1"


def test_rescue_refuses_while_nightly_runs(home, capsys):
    from inkvault import cli
    with holder(home):
        assert cli.main(["rescue", "--no-open"]) == 1
    assert "nightly run is in progress" in capsys.readouterr().out


def fake_install(monkeypatch, schedule, versions, calls, stays_old=False):
    """Pretend uv works and the tool install reports versions[0] (None: the probe fails); a reinstall installs 0.1.1+."""
    from pathlib import Path
    import subprocess
    exe = Path("/tools/inkvault/bin/python")
    monkeypatch.delenv("UV", raising=False)
    monkeypatch.setattr(schedule.shutil, "which", lambda name: "/usr/bin/uv" if name == "uv" else None)
    monkeypatch.setattr(schedule, "tool_python", lambda: exe)
    monkeypatch.setattr(schedule, "installed_version", lambda e: versions[0])

    def run(cmd, **k):
        calls.append(cmd)
        if not stays_old:
            versions[0] = schedule.version_tuple(schedule.__version__)  # the install worked
        return subprocess.CompletedProcess(cmd, 0)
    monkeypatch.setattr(schedule.subprocess, "run", run)
    return exe


def test_ensure_installed_reinstalls_an_outdated_tool(monkeypatch, capsys):
    from inkvault import schedule
    calls = []
    exe = fake_install(monkeypatch, schedule, [(0, 0, 9)], calls)
    assert schedule.ensure_installed() == exe
    assert calls == [["/usr/bin/uv", "tool", "install", "--force", schedule.REPO]]
    assert "Updating the installed InkVault to" in capsys.readouterr().out


def test_ensure_installed_leaves_a_current_tool_alone(monkeypatch):
    from inkvault import __version__, schedule
    calls = []
    current = schedule.version_tuple(__version__)
    exe = fake_install(monkeypatch, schedule, [current], calls)
    assert schedule.ensure_installed() == exe
    assert calls == []
    monkeypatch.setattr(schedule, "installed_version", lambda e: (99, 0))
    assert schedule.ensure_installed() == exe and calls == []  # newer is fine too


def test_ensure_installed_reinstalls_when_the_probe_fails(monkeypatch):
    from inkvault import schedule
    calls = []
    fake_install(monkeypatch, schedule, [None], calls)
    schedule.ensure_installed()
    assert calls and "--force" in calls[0]


def test_ensure_installed_fails_clearly_if_the_update_did_not_take(monkeypatch):
    from inkvault import schedule
    calls = []
    fake_install(monkeypatch, schedule, [(0, 0, 9)], calls, stays_old=True)  # still reports old after --force
    with pytest.raises(schedule.ScheduleError, match="--force --reinstall"):
        schedule.ensure_installed()


def test_installed_version_reads_the_version_line(tmp_path, monkeypatch):
    import subprocess
    from inkvault import schedule
    exe = tmp_path / "python"
    monkeypatch.setattr(schedule.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout="inkvault 0.1.12\n", stderr=""))
    assert schedule.installed_version(exe) == (0, 1, 12)
    monkeypatch.setattr(schedule.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 1, stdout="", stderr="No module"))
    assert schedule.installed_version(exe) is None

    def boom(*a, **k):
        raise subprocess.TimeoutExpired(a, 60)
    monkeypatch.setattr(schedule.subprocess, "run", boom)
    assert schedule.installed_version(exe) is None


def test_uv_path_prefers_the_uv_variable(monkeypatch):
    from inkvault import schedule
    monkeypatch.setattr(schedule.shutil, "which", lambda name: "/usr/bin/uv")
    monkeypatch.setenv("UV", "/opt/uvx/uv")
    assert schedule.uv_path() == "/opt/uvx/uv"
    monkeypatch.delenv("UV")
    assert schedule.uv_path() == "/usr/bin/uv"


def test_describe_survives_a_corrupt_schedule_file(home, monkeypatch):
    from inkvault import schedule
    monkeypatch.setattr(schedule, "backend", lambda: FakeBackend(on=True))
    for text in ("{not json", "[]", '{"wake": true}', '{"at": "9pm"}', ""):
        schedule.paths.schedule_file().write_text(text)
        assert schedule.describe() == "on, but schedule.json is unreadable (run `inkvault schedule` again)"


def test_enable_reports_backend_and_settings_failures(home, monkeypatch):
    from pathlib import Path
    from inkvault import schedule
    fake = FakeBackend()
    monkeypatch.setattr(schedule, "backend", lambda: fake)
    monkeypatch.setattr(schedule, "ensure_installed", lambda: Path("/tools/python"))
    monkeypatch.setattr(schedule, "remember_pieces", lambda: None)

    def broken(*a):
        raise ValueError("bad schtasks output")
    monkeypatch.setattr(fake, "install", broken)
    with pytest.raises(schedule.ScheduleError, match="Couldn't set up the nightly run: bad schtasks output"):
        schedule.enable("03:00", wake=False)

    monkeypatch.undo()
    monkeypatch.setenv("INKVAULT_HOME", str(home))
    monkeypatch.setattr(schedule, "backend", lambda: fake)
    monkeypatch.setattr(schedule, "ensure_installed", lambda: Path("/tools/python"))
    monkeypatch.setattr(schedule, "remember_pieces", lambda: None)
    monkeypatch.setattr(schedule.os, "replace", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(schedule.ScheduleError, match="set up for 03:00, but .*disk full"):
        schedule.enable("03:00", wake=False)

    def remove_fails():
        raise RuntimeError("schtasks said no")
    monkeypatch.setattr(fake, "remove", remove_fails)
    with pytest.raises(schedule.ScheduleError, match="Couldn't remove the nightly run: schtasks said no"):
        schedule.disable()


def test_linux_never_asks_about_waking_but_still_shows_the_note(home, monkeypatch, capsys):
    from pathlib import Path
    from inkvault import schedule

    class LinuxFake(FakeBackend):
        PLATFORM = "linux"
    fake = LinuxFake()
    monkeypatch.setattr(schedule, "backend", lambda: fake)
    monkeypatch.setattr(schedule, "ensure_installed", lambda: Path("/tools/python"))
    monkeypatch.setattr(schedule, "remember_pieces", lambda: None)
    monkeypatch.setattr("builtins.input", lambda prompt: pytest.fail("asked"))
    schedule.enable("03:00", wake=None, interactive=True)
    assert fake.calls[0][3] is False and "WAKE NOTE" not in capsys.readouterr().out
    schedule.enable("03:00", wake=True)
    assert fake.calls[1][3] is False and "WAKE NOTE" in capsys.readouterr().out


def test_ask_wake_mentions_the_admin_command_on_a_mac(monkeypatch):
    from inkvault import schedule
    seen = []
    monkeypatch.setattr("builtins.input", lambda prompt: seen.append(prompt) or "n")
    schedule.ask_wake(None, True, "darwin")
    schedule.ask_wake(None, True, "win32")
    assert "administrator" in seen[0] and "administrator" not in seen[1]


def test_pieces_ports_must_be_valid_ports(home, capsys):
    from inkvault import cli
    for bad in ("abc", "0", "70000", "1,,2", ""):
        with pytest.raises(SystemExit) as e:
            cli.main(["--pieces-ports", bad, "status"])
        assert e.value.code == 2
    assert cli.ports_arg("39300, 1000") == "39300,1000"


def test_wake_and_no_wake_exclude_each_other(home):
    from inkvault import cli
    with pytest.raises(SystemExit) as e:
        cli.main(["schedule", "--wake", "--no-wake"])
    assert e.value.code == 2


def test_rescue_without_lock_support_warns_and_runs(home, monkeypatch, capsys):
    from inkvault import cli, nightly

    def no_locks():
        raise OSError("locking not supported")
    monkeypatch.setattr(nightly, "lock", no_locks)
    monkeypatch.setattr(cli, "rescue", lambda args: 0)
    assert cli.main(["rescue", "--no-open"]) == 0
    assert "continuing without it" in capsys.readouterr().out


# --- review round 3 --------------------------------------------------------------------------------------------

def scheduled(vault, monkeypatch, at="03:00", **job):
    """schedule.json says `at`, and the fake scheduler reports a job with these fields."""
    from inkvault import schedule
    fake = FakeBackend(on=True, **job)
    monkeypatch.setattr(schedule, "backend", lambda: fake)
    schedule.paths.schedule_file().write_text(json.dumps({"at": at, "wake": False}))
    return schedule


def test_describe_reports_a_disabled_task(home, monkeypatch):
    schedule = scheduled(home, monkeypatch, enabled=False)
    assert schedule.describe() == ("on, but the task is disabled in Fake Scheduler "
                                   "(run `inkvault schedule` again to turn it back on)")


def test_describe_reports_a_job_for_another_vault(home, monkeypatch, tmp_path_factory):
    other = str(tmp_path_factory.mktemp("vault-b"))
    schedule = scheduled(home, monkeypatch, home=other)
    assert schedule.describe().startswith(f"on, but it runs a different vault ({other})")
    schedule = scheduled(home, monkeypatch, home=str(home) + "/")  # the same folder, spelled differently
    assert schedule.describe().startswith("daily at 03:00")


def test_describe_reports_a_missing_program(home, monkeypatch):
    gone = str(home / "uv" / "tools" / "inkvault" / "python")
    schedule = scheduled(home, monkeypatch, home=str(home), executable=gone)
    assert schedule.describe().startswith(f"on, but its program is missing ({gone})")
    (home / "python").touch()
    schedule = scheduled(home, monkeypatch, home=str(home), executable=str(home / "python"))
    assert schedule.describe().startswith("daily at 03:00; next ")


def test_describe_is_off_when_the_scheduler_has_no_job(home, monkeypatch):
    schedule = scheduled(home, monkeypatch)
    schedule.backend().on = False  # schedule.json remains (another vault's schedule replaced ours, say)
    assert schedule.describe().startswith("off")


def test_describe_flags_an_overdue_run(home, monkeypatch):
    import os
    from datetime import datetime, timedelta
    from inkvault import nightly
    schedule = scheduled(home, monkeypatch)
    now = datetime(2026, 10, 1, 12, 0)
    log = schedule.paths.nightly_log()
    log.write_text(f"2026-09-28 03:00:01  {nightly.START} (pid 1) ===\n", encoding="utf-8")
    assert schedule.describe(now) == "daily at 03:00; next 2026-10-02 03:00; overdue: no run since 2026-09-28 03:00"
    log.write_text(f"2026-10-01 03:00:01  {nightly.START} (pid 1) ===\n", encoding="utf-8")
    assert schedule.describe(now) == "daily at 03:00; next 2026-10-02 03:00"

    log.unlink()  # never ran: overdue once it was set up more than 26 hours ago
    f = schedule.paths.schedule_file()
    recent = (now - timedelta(hours=2)).timestamp()
    os.utime(f, (recent, recent))
    assert "overdue" not in schedule.describe(now)
    old = (now - timedelta(hours=30)).timestamp()
    os.utime(f, (old, old))
    assert schedule.describe(now).endswith("; overdue: no run since it was set up on 2026-09-30 06:00")


def test_ensure_installed_checks_the_version_of_a_fresh_install(monkeypatch):
    from pathlib import Path
    import subprocess
    from inkvault import schedule
    exe = Path("/tools/inkvault/bin/python")
    found = iter([None, exe])  # not installed, then installed by uv
    monkeypatch.setattr(schedule.shutil, "which", lambda name: "/usr/bin/uv" if name == "uv" else None)
    monkeypatch.setattr(schedule, "tool_python", lambda: next(found))
    monkeypatch.setattr(schedule, "installed_version", lambda e: (0, 0, 1))  # uv resolved an old release
    monkeypatch.setattr(schedule.subprocess, "run", lambda cmd, **k: subprocess.CompletedProcess(cmd, 0))
    with pytest.raises(schedule.ScheduleError, match="older than"):
        schedule.ensure_installed()


def test_parse_ports():
    from inkvault import schedule
    assert schedule.parse_ports("39300, 1000") == "39300,1000"
    for bad in ("abc", "0", "70000", "1,,2", "", " "):
        with pytest.raises(schedule.ScheduleError, match="INKVAULT_PIECES_PORTS"):
            schedule.parse_ports(bad)


def test_enable_rejects_bad_ports_before_installing_anything(home, monkeypatch):
    from inkvault import schedule
    monkeypatch.setattr(schedule, "ensure_installed", lambda: pytest.fail("installed before checking ports"))
    monkeypatch.setattr(schedule, "backend", lambda: pytest.fail("touched the scheduler"))
    monkeypatch.setenv("INKVAULT_PIECES_PORTS", "39300,x")
    assert not schedule.paths.vault_db().exists()
    with pytest.raises(schedule.ScheduleError, match="39300,x"):
        schedule.enable("03:00", wake=False)


def test_job_argv_normalizes_ports(home, monkeypatch):
    from pathlib import Path
    from inkvault import schedule
    monkeypatch.setenv("INKVAULT_PIECES_PORTS", " 39300 , 1000")
    argv = schedule.job_argv(Path("/p"))
    assert argv[argv.index("--pieces-ports") + 1] == "39300,1000"


def test_tool_python_decodes_uv_output_as_utf8(tmp_path, monkeypatch):
    import subprocess
    import sys
    from inkvault import schedule
    tools = tmp_path / "Ünïcödé 工具"
    exe = (tools / "inkvault" / "Scripts" / "pythonw.exe" if sys.platform == "win32"
           else tools / "inkvault" / "bin" / "python")
    exe.parent.mkdir(parents=True)
    exe.touch()
    monkeypatch.setattr(schedule.shutil, "which", lambda name: "/usr/bin/uv")
    out = [f"{tools}\n".encode("utf-8")]
    monkeypatch.setattr(schedule.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=out[0], stderr=b""))
    assert schedule.tool_python() == exe
    out[0] = b"C:\\caf\xe9\\tools\n"  # not UTF-8 (an ANSI code page): a wrong path must not be guessed at
    with pytest.raises(schedule.ScheduleError, match="uv tool dir"):
        schedule.tool_python()

    def missing(*a, **k):
        raise FileNotFoundError("uv")
    monkeypatch.setattr(schedule.subprocess, "run", missing)
    with pytest.raises(schedule.ScheduleError, match="uv tool dir"):
        schedule.tool_python()


def test_enable_refuses_percent_names_on_windows(home, monkeypatch):
    from pathlib import Path
    from inkvault import schedule
    fake = FakeBackend()  # PLATFORM win32
    monkeypatch.setattr(schedule, "backend", lambda: fake)
    monkeypatch.setattr(schedule, "ensure_installed", lambda: Path("C:/Users/x/%TOOLS%/pythonw.exe"))
    monkeypatch.setattr(schedule, "remember_pieces", lambda: None)
    with pytest.raises(schedule.ScheduleError, match="would treat %TOOLS% in C:.*as an environment variable"):
        schedule.enable("03:00", wake=False)
    assert fake.calls == []
    schedule.check_task_scheduler_argv(["C:/100% sure/python", "nightly"])  # a lone % is fine

    class LinuxFake(FakeBackend):
        PLATFORM = "linux"
    linux = LinuxFake()
    monkeypatch.setattr(schedule, "backend", lambda: linux)
    schedule.enable("03:00", wake=False)  # cron and systemd have their own escaping
    assert linux.calls


def test_disable_keeps_the_settings_when_removal_fails(home, monkeypatch):
    schedule = scheduled(home, monkeypatch)

    def denied():
        raise RuntimeError("schtasks couldn't delete the task: Access is denied.")
    monkeypatch.setattr(schedule.backend(), "remove", denied)
    with pytest.raises(schedule.ScheduleError, match="Access is denied"):
        schedule.disable()
    assert schedule.paths.schedule_file().exists()
