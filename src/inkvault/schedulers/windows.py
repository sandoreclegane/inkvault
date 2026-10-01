"""Windows: a Task Scheduler task, created from XML so every setting is explicit (schtasks flags can't set most)."""
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from xml.sax.saxutils import escape

from . import Job, home_of

TASK = "InkVault Nightly"
WAKE_TIMERS = "BD3B718A-0680-4D9D-8AB2-E1D2B4AC806D"  # power setting "Allow wake timers"
# schtasks and powercfg print in the OEM code page; the default (ANSI) can fail to decode localized output
TEXT = {"encoding": "oem", "errors": "replace"} if sys.platform == "win32" else {"errors": "replace"}


def render(argv, hour, minute, wake):
    program = f'"{argv[0]}"' if " " in argv[0] else argv[0]  # an unquoted path with a space is split
    command, arguments = escape(program), escape(subprocess.list2cmdline(argv[1:]))
    # StartWhenAvailable: run as soon as possible after a missed start (asleep, off).
    # RestartOnFailure: Task Scheduler restarts the task if it fails (per its own rules); nightly's own lock and
    # log make a restart safe. A sleep suspends the run rather than killing it, and nightly asks Windows to stay
    # awake while it works.
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
    return subprocess.run(["schtasks", *args], capture_output=True, **TEXT)


def install(argv, hour, minute, wake):
    with tempfile.TemporaryDirectory() as tmp:
        xml = Path(tmp) / "inkvault-nightly.xml"
        xml.write_text(render(argv, hour, minute, wake), encoding="utf-16")  # BOM included, what schtasks /XML expects
        r = schtasks("/Create", "/XML", str(xml), "/TN", TASK, "/F")
    if r.returncode:
        raise RuntimeError(f"schtasks couldn't create the task: {(r.stderr or r.stdout).strip()}")
    return f'Task Scheduler task "{TASK}"'


def remove():
    """True if the task was deleted, False if there was none; RuntimeError if deleting failed (access denied)."""
    if not query().present:
        return False
    r = schtasks("/Delete", "/TN", TASK, "/F")
    if r.returncode:
        raise RuntimeError(f"schtasks couldn't delete the task: {(r.stderr or r.stdout).strip()}")
    return True


def decode(data):
    """schtasks /XML output: UTF-16 (with a BOM, or without one: every other byte zero), else the OEM code page."""
    if isinstance(data, str):
        return data
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16", errors="replace")
    if len(data) > 1 and data[1] == 0:
        return data.decode("utf-16-le", errors="replace")
    return data.decode("oem" if sys.platform == "win32" else "utf-8", errors="replace")


def split_args(text):
    """The inverse of subprocess.list2cmdline: split a command line the way a Windows program's C runtime does."""
    args, cur, quoted, slashes, started = [], "", False, 0, False
    for ch in text:
        if ch == "\\":
            slashes += 1
            continue
        if ch == '"':
            cur += "\\" * (slashes // 2)
            if slashes % 2:
                cur += '"'
            else:
                quoted = not quoted
            slashes, started = 0, True
            continue
        cur += "\\" * slashes
        slashes = 0
        if ch in " \t" and not quoted:
            if started or cur:
                args.append(cur)
            cur, started = "", False
        else:
            cur += ch
            started = True
    cur += "\\" * slashes
    if started or cur:
        args.append(cur)
    return args


def query():
    """What Task Scheduler has under our task name: whether it's enabled, and which Python and vault it runs.

    The name is global, so it may be another vault's job (scheduling vault B replaces vault A's)."""
    r = subprocess.run(["schtasks", "/Query", "/TN", TASK, "/XML"], capture_output=True)
    if r.returncode:
        return Job(False, scheduler="Task Scheduler")
    text = re.sub(r"^\ufeff?\s*<\?xml[^>]*\?>", "", decode(r.stdout))  # a str can't declare UTF-16
    ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return Job(True, scheduler="Task Scheduler")
    enabled = root.findtext("t:Settings/t:Enabled", None, ns)
    command = root.findtext(".//t:Exec/t:Command", None, ns)
    arguments = root.findtext(".//t:Exec/t:Arguments", "", ns)
    return Job(True, enabled=None if enabled is None else enabled.strip().lower() == "true",
               home=home_of(split_args(arguments)),
               executable=command.strip().strip('"') if command else None, scheduler="Task Scheduler")


def installed():
    return query().present


def wake_timers_allowed(powercfg_output):
    """From `powercfg /query`: False unless 'Allow wake timers' is clearly Enable (1) on AC power.

    0 is Disable and 2 is "Important Wake Timers Only", which blocks an ordinary task's wake too.
    powercfg translates its labels, so this doesn't look for English: the current AC and DC values are the last
    two lines ending in 0x plus eight hex digits (the "possible settings" lines above them have no 0x), AC first.
    """
    values = re.findall(r"0x([0-9a-fA-F]{8})[ \t]*$", powercfg_output, re.MULTILINE)
    if not values:
        return True  # can't read it: don't warn on a guess
    ac = values[-2] if len(values) >= 2 else values[-1]
    return int(ac, 16) == 1


def wake_note(hour, minute):
    r = subprocess.run(["powercfg", "/query", "SCHEME_CURRENT", "SUB_SLEEP", WAKE_TIMERS],
                       capture_output=True, **TEXT)
    if r.returncode == 0 and not wake_timers_allowed(r.stdout):
        return ("Note: wake timers are off (or set to \"Important Wake Timers Only\") in your power plan, so Windows "
                "won't wake for the run; it will run the next time the computer is awake. To allow them: Control "
                "Panel > Power Options > Change plan settings > Change advanced power settings > Sleep > Allow "
                "wake timers > Enable. Computers with Modern Standby may ignore wake timers regardless.")
    return None
