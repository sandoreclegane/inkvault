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
    r = subprocess.run([uv, "tool", "dir"], capture_output=True, text=True)
    if r.returncode:
        return None
    env = Path(r.stdout.strip()) / "inkvault"
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
    if shutil.which("inkvault"):
        print("Done. Update it anytime with `uv tool upgrade inkvault`.")
    else:
        print("Done. To type `inkvault` directly, run `uv tool update-shell` and open a new terminal.")
    return exe


def job_argv(python):
    """The scheduled command. Settings go as flags: Task Scheduler can't give a task environment variables."""
    argv = [str(python), "-m", "inkvault"]
    if os.environ.get("INKVAULT_HOME"):
        argv += ["--home", str(paths.home())]
    if os.environ.get("INKVAULT_PIECES_PORTS"):
        argv += ["--pieces-ports", os.environ["INKVAULT_PIECES_PORTS"]]
    return argv + ["nightly"]


def platform_of(b):
    """sys.platform-style name for a backend module (tests' fakes carry PLATFORM)."""
    name = getattr(b, "__name__", "").rsplit(".", 1)[-1]
    return getattr(b, "PLATFORM", None) or {"windows": "win32", "macos": "darwin", "linux": "linux"}.get(name)         or sys.platform


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
    exe = ensure_installed()
    b = backend()
    requested = wake
    wake = ask_wake(wake, interactive, platform_of(b))
    remember_pieces()
    try:
        where = b.install(job_argv(exe), hour, minute, wake)
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


def describe(now=None):
    """One line for `inkvault status`."""
    f = paths.schedule_file()
    if not f.exists() or not backend().installed():
        return "off (turn on with `inkvault schedule`)"
    try:
        s = json.loads(f.read_text(encoding="utf-8"))
        hour, minute = parse_at(s["at"])
        upcoming = next_run(hour, minute, now or datetime.now())
        return f"daily at {s['at']}{', wakes the computer' if s.get('wake') else ''}; next {upcoming:%Y-%m-%d %H:%M}"
    except (OSError, ValueError, KeyError, TypeError, ScheduleError):
        return "on, but schedule.json is unreadable (run `inkvault schedule` again)"
