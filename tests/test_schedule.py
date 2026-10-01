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
