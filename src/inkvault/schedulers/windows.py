"""Windows: a Task Scheduler task, created from XML so every setting is explicit (schtasks flags can't set most)."""
import re
import subprocess
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape

TASK = "InkVault Nightly"
WAKE_TIMERS = "BD3B718A-0680-4D9D-8AB2-E1D2B4AC806D"  # power setting "Allow wake timers"


def render(argv, hour, minute, wake):
    command, arguments = escape(argv[0]), escape(subprocess.list2cmdline(argv[1:]))
    # StartWhenAvailable: run as soon as possible after a missed start (asleep, off).
    # RestartOnFailure: a run killed by sleep or a crash gets three more tries.
    # InteractiveToken: runs only while you're logged in, so no password is stored.
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>InkVault nightly refresh: new Pieces captures, search, digests, dashboard, backup.</Description>
  </RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>2026-01-01T{hour:02d}:{minute:02d}:00</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByDay>
        <DaysInterval>1</DaysInterval>
      </ScheduleByDay>
    </CalendarTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <WakeToRun>{"true" if wake else "false"}</WakeToRun>
    <ExecutionTimeLimit>PT4H</ExecutionTimeLimit>
    <RestartOnFailure>
      <Interval>PT15M</Interval>
      <Count>3</Count>
    </RestartOnFailure>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{command}</Command>
      <Arguments>{arguments}</Arguments>
    </Exec>
  </Actions>
</Task>
"""


def schtasks(*args):
    return subprocess.run(["schtasks", *args], capture_output=True, text=True)


def install(argv, hour, minute, wake):
    with tempfile.TemporaryDirectory() as tmp:
        xml = Path(tmp) / "inkvault-nightly.xml"
        xml.write_text(render(argv, hour, minute, wake), encoding="utf-16")  # BOM included, what schtasks /XML expects
        r = schtasks("/Create", "/XML", str(xml), "/TN", TASK, "/F")
    if r.returncode:
        raise RuntimeError(f"schtasks couldn't create the task: {(r.stderr or r.stdout).strip()}")
    return f'Task Scheduler task "{TASK}"'


def remove():
    return schtasks("/Delete", "/TN", TASK, "/F").returncode == 0


def installed():
    return schtasks("/Query", "/TN", TASK).returncode == 0


def wake_timers_allowed(powercfg_output):
    """From `powercfg /query`: False only if 'Allow wake timers' is clearly off on AC power."""
    m = re.search(r"Current AC Power Setting Index:\s*0x([0-9a-fA-F]+)", powercfg_output)
    return m is None or int(m.group(1), 16) != 0


def wake_note(hour, minute):
    r = subprocess.run(["powercfg", "/query", "SCHEME_CURRENT", "SUB_SLEEP", WAKE_TIMERS],
                       capture_output=True, text=True)
    if r.returncode == 0 and not wake_timers_allowed(r.stdout):
        return ("Note: wake timers are turned off in your power plan, so Windows won't wake for the run; it will "
                "run the next time the computer is awake. To allow them: Control Panel > Power Options > "
                "Change plan settings > Change advanced power settings > Sleep > Allow wake timers.")
    return None
