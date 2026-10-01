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
