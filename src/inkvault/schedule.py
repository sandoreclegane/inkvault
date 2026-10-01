"""`inkvault schedule`: run `inkvault nightly` every day, using the operating system's own scheduler.

uvx runs InkVault from a cache that uv may clear, so the job doesn't point there. InkVault is installed once
as a permanent `uv tool`, and the job runs that install's Python.
"""
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

from . import __version__, paths

REPO = "git+https://github.com/sandoreclegane/inkvault"


class ScheduleError(Exception):
    """Something the user has to fix; the message says what."""


def parse_at(text):
    try:
        t = datetime.strptime(text, "%H:%M")
    except ValueError:
        raise ScheduleError(f"--at wants a 24-hour time like 03:00, not {text!r}") from None
    return t.hour, t.minute


def next_run(hour, minute, now):
    run = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return run if run > now else run + timedelta(days=1)


def backend():
    from .schedulers import linux, macos, windows
    if sys.platform == "win32":
        return windows
    if sys.platform == "darwin":
        return macos
    return linux


def uv_path():
    """uv's own path: uvx sets UV, and a scheduled or minimal environment may not have uv on PATH."""
    return os.environ.get("UV") or shutil.which("uv")


def tool_python():
    """The Python of InkVault installed with `uv tool install`, or None. pythonw on Windows: no console window."""
    uv = uv_path()
    if not uv:
        return None
    try:
        r = subprocess.run([uv, "tool", "dir"], capture_output=True)
        if r.returncode:
            return None
        # uv prints UTF-8 whatever the console code page; no replacement characters: a wrong path must fail
        tools = r.stdout.decode("utf-8").strip()
    except (OSError, UnicodeDecodeError) as e:
        raise ScheduleError(f"Couldn't ask uv where its tools live (`uv tool dir`): {e}") from None
    env = Path(tools) / "inkvault"
    exe = env / "Scripts" / "pythonw.exe" if sys.platform == "win32" else env / "bin" / "python"
    return exe if exe.exists() else None


def version_tuple(text):
    m = re.search(r"\d+(?:\.\d+)*", text or "")
    return tuple(int(n) for n in m.group().split(".")) if m else None


def installed_version(exe):
    """The version of the tool install at exe, or None if it can't say (a 0.1.0 install has no `-m inkvault`)."""
    console = exe.with_name("python.exe") if sys.platform == "win32" else exe  # pythonw prints nothing
    try:
        r = subprocess.run([str(console), "-m", "inkvault", "--version"], capture_output=True, text=True,
                           timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    return version_tuple(r.stdout) if r.returncode == 0 else None


def ensure_installed():
    uv = uv_path()
    exe = tool_python()
    if exe:
        have = installed_version(exe)
        if have and have >= version_tuple(__version__):
            return exe
        # A tool install from before `nightly` existed would make the job fail silently every night.
        print(f"Updating the installed InkVault to {__version__}", flush=True)
        cmd = [uv, "tool", "install", "--force", REPO]
    elif not uv:
        raise ScheduleError("The nightly run needs uv to install InkVault as a permanent command. Install uv "
                            "(https://docs.astral.sh/uv/getting-started/installation/), then run `inkvault schedule` again.")
    else:
        print(f"Installing InkVault as a permanent command: uv tool install {REPO}", flush=True)
        cmd = [uv, "tool", "install", REPO]
    r = subprocess.run(cmd)
    if r.returncode or not (exe := tool_python()):
        raise ScheduleError("`uv tool install` didn't finish; see the message above.")
    # Fresh or forced, check what uv actually installed: an old release would fail every night.
    if not ((v := installed_version(exe)) and v >= version_tuple(__version__)):
        found = ".".join(map(str, v)) if v else "a version that can't run `nightly`"
        raise ScheduleError(f"uv installed InkVault {found}, older than this {__version__}. Run "
                            f"`uv tool install --force --reinstall {REPO}` and then `inkvault schedule` again.")
    if shutil.which("inkvault"):
        print("Done. Update it anytime with `uv tool upgrade inkvault`.")
    else:
        print("Done. To type `inkvault` directly, run `uv tool update-shell` and open a new terminal.")
    return exe


def job_argv(python):
    """The scheduled command. Settings go as flags: Task Scheduler can't give a task environment variables."""
    # Always pin the vault folder: cron and systemd don't see this shell's XDG_DATA_HOME or a --home used to rescue.
    argv = [str(python), "-m", "inkvault", "--home", str(paths.home())]
    if ports := pieces_ports():
        argv += ["--pieces-ports", ports]
    return argv + ["nightly"]


def parse_ports(text):
    """Comma-separated port numbers (1-65535), normalized ("39300, 1000" -> "39300,1000"); the rules of --pieces-ports."""
    try:
        ports = [int(part) for part in text.split(",")]
    except ValueError:
        ports = []
    if not ports or not all(1 <= n <= 65535 for n in ports):
        raise ScheduleError(f"INKVAULT_PIECES_PORTS is {text!r}, which isn't a port list; use numbers from 1 to "
                            "65535, comma-separated (for example 39300,1000)")
    return ",".join(map(str, ports))


def pieces_ports():
    """INKVAULT_PIECES_PORTS checked and normalized, or None if unset. A bad value would break every nightly run."""
    text = os.environ.get("INKVAULT_PIECES_PORTS")
    return parse_ports(text) if text else None


def check_task_scheduler_argv(argv):
    """Task Scheduler expands %NAME% in a task's command and arguments, and there's no way to escape it."""
    for arg in argv:
        if m := re.search(r"%[^%]+%", arg):
            raise ScheduleError(f"Task Scheduler would treat {m.group()} in {arg} as an environment variable; move "
                                "the vault or the uv tools folder to a path without %…%")


def platform_of(b):
    """sys.platform-style name for a backend module (tests' fakes carry PLATFORM)."""
    name = getattr(b, "__name__", "").rsplit(".", 1)[-1]
    return getattr(b, "PLATFORM", None) or {"windows": "win32", "macos": "darwin", "linux": "linux"}.get(name) or sys.platform


def ask_wake(wake, interactive, platform=None):
    """--wake / --no-wake win; otherwise ask in a terminal (default no); with no terminal, don't wake.

    Linux can't wake for a user's timer, so it never asks (and never wakes)."""
    platform = platform or sys.platform
    if platform not in ("win32", "darwin"):
        return False
    if wake is not None:
        return wake
    if not interactive:
        return False
    extra = " This needs a one-time administrator command, which I'll show you." if platform == "darwin" else ""
    answer = input("If the computer is asleep at that time, wake it for the run?" + extra +
                   " (Otherwise it runs the next time the computer is awake.) [y/N] ")
    return answer.strip().lower() in ("y", "yes")


def remember_pieces():
    """Save PiecesOS's port now, from a terminal that can read its port file (a scheduled job on macOS may not)."""
    from .export import PiecesOS, remember
    if paths.vault_db().exists():
        pos, version = PiecesOS.find()
        if pos:
            remember(pos, version)


def enable(at="03:00", wake=None, interactive=False):
    hour, minute = parse_at(at)
    pieces_ports()  # before installing anything
    exe = ensure_installed()
    b = backend()
    argv = job_argv(exe)
    if platform_of(b) == "win32":
        check_task_scheduler_argv(argv)
    requested = wake
    wake = ask_wake(wake, interactive, platform_of(b))
    remember_pieces()
    if not paths.vault_db().exists():
        print(f"No vault at {paths.home()} yet. If you rescued with --home, run schedule with the same --home.")
    try:
        where = b.install(argv, hour, minute, wake)
    except Exception as e:  # noqa: BLE001 - whatever the scheduler tool did, the user needs the message
        raise ScheduleError(f"Couldn't set up the nightly run: {e}") from None
    at = f"{hour:02d}:{minute:02d}"
    try:
        f = paths.schedule_file()
        tmp = f.with_name(f.name + ".tmp")
        tmp.write_text(json.dumps({"at": at, "wake": wake}), encoding="utf-8")
        os.replace(tmp, f)
    except OSError as e:
        raise ScheduleError(f"The nightly run is set up for {at}, but InkVault couldn't save its settings "
                            f"({e}), so `inkvault status` won't show it. Run `inkvault schedule` again.") from None
    print(f"Nightly run set for {at} every day ({where}).")
    if (wake or requested) and (note := b.wake_note(hour, minute)):
        print(note)
    print(f"Each run is logged in {paths.nightly_log()}.\n"
          "Run it now with `inkvault nightly`, check on it with `inkvault status`, "
          "or turn it off with `inkvault schedule --off`.")


def disable():
    try:
        removed = backend().remove()
    except Exception as e:  # noqa: BLE001
        raise ScheduleError(f"Couldn't remove the nightly run: {e}") from None
    paths.schedule_file().unlink(missing_ok=True)
    print("Nightly run removed. Your vault, backups and log are untouched." if removed
          else "There was no nightly run set up.")


OVERDUE = timedelta(hours=26)  # a daily run, plus slack for a late start or a long run


def same_path(a, b):
    """The same folder? Case-insensitive on Windows and macOS, whose default file systems (NTFS, APFS) are."""
    def norm(p):
        p = os.path.normcase(str(Path(p).resolve()))
        return p.casefold() if sys.platform in ("win32", "darwin") else p
    return norm(a) == norm(b)


def overdue(now):
    """"; overdue: ..." if the healthy-looking job hasn't started a run in OVERDUE, else "".

    Counted from the later of the newest run's start and the last `inkvault schedule` (schedule.json's time), so
    turning the schedule back on or moving its time doesn't raise a false alarm."""
    from . import nightly
    started = set_up = None
    last = nightly.last_run()
    if last == getattr(nightly, "LOG_UNREADABLE", object()):
        return ""  # can't tell when it last ran; status reports the unreadable log itself
    if last:
        try:
            started = datetime.strptime(last[0], "%Y-%m-%d %H:%M:%S")
        except (TypeError, ValueError):
            pass
    try:
        set_up = datetime.fromtimestamp(paths.schedule_file().stat().st_mtime)
    except OSError:
        pass
    if started and (not set_up or started >= set_up):
        return f"; overdue: no run since {started:%Y-%m-%d %H:%M}" if now - started > OVERDUE else ""
    if set_up:
        return f"; overdue: no run since it was set up on {set_up:%Y-%m-%d %H:%M}" if now - set_up > OVERDUE else ""
    return ""


def describe(now=None):
    """One line for `inkvault status`: what the OS scheduler really has, not just that something is there."""
    now = now or datetime.now()
    f = paths.schedule_file()
    if not f.exists():
        return "off (turn on with `inkvault schedule`)"
    try:
        job = backend().query()
    except Exception as e:  # noqa: BLE001 - status must not crash on a scheduler problem
        return f"on, but the scheduler couldn't be asked about it ({e})"
    if not job.present:
        return "off (turn on with `inkvault schedule`)"
    try:
        s = json.loads(f.read_text(encoding="utf-8"))
        hour, minute = parse_at(s["at"])
        wake = ", wakes the computer" if s.get("wake") else ""
    except (OSError, ValueError, KeyError, TypeError, ScheduleError):
        return "on, but schedule.json is unreadable (run `inkvault schedule` again)"
    if job.enabled is False:
        return f"on, but the task is disabled in {job.scheduler} (run `inkvault schedule` again to turn it back on)"
    if job.home and not same_path(job.home, paths.home()):
        return (f"on, but it runs a different vault ({job.home}); run `inkvault schedule` here to switch the "
                "nightly run to this vault (there is one nightly run per computer user)")
    if job.executable and not Path(job.executable).exists():
        return f"on, but its program is missing ({job.executable}); run `inkvault schedule` again to reinstall it"
    upcoming = next_run(hour, minute, now)
    return f"daily at {s['at']}{wake}; next {upcoming:%Y-%m-%d %H:%M}{overdue(now)}"
