"""`inkvault schedule`: run `inkvault nightly` every day, using the operating system's own scheduler.

uvx runs InkVault from a cache that uv may clear, so the job doesn't point there. InkVault is installed once
as a permanent `uv tool`, and the job runs that install's Python.
"""
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

from . import paths

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


def tool_python():
    """The Python of InkVault installed with `uv tool install`, or None. pythonw on Windows: no console window."""
    if not shutil.which("uv"):
        return None
    r = subprocess.run(["uv", "tool", "dir"], capture_output=True, text=True)
    if r.returncode:
        return None
    env = Path(r.stdout.strip()) / "inkvault"
    exe = env / "Scripts" / "pythonw.exe" if sys.platform == "win32" else env / "bin" / "python"
    return exe if exe.exists() else None


def ensure_installed():
    if exe := tool_python():
        return exe
    if not shutil.which("uv"):
        raise ScheduleError("The nightly run needs uv to install InkVault as a permanent command. Install uv "
                            "(https://docs.astral.sh/uv/getting-started/installation/), then run `inkvault schedule` again.")
    print(f"Installing InkVault as a permanent command: uv tool install {REPO}", flush=True)
    r = subprocess.run(["uv", "tool", "install", REPO])
    if r.returncode or not (exe := tool_python()):
        raise ScheduleError("`uv tool install` didn't finish; see the message above.")
    print("Done. `inkvault` is now a normal command; update it anytime with `uv tool upgrade inkvault`.")
    return exe


def job_argv(python):
    """The scheduled command. Settings go as flags: Task Scheduler can't give a task environment variables."""
    argv = [str(python), "-m", "inkvault"]
    if os.environ.get("INKVAULT_HOME"):
        argv += ["--home", str(paths.home())]
    if os.environ.get("INKVAULT_PIECES_PORTS"):
        argv += ["--pieces-ports", os.environ["INKVAULT_PIECES_PORTS"]]
    return argv + ["nightly"]


def ask_wake(wake, interactive):
    """--wake / --no-wake win; otherwise ask in a terminal (default no); with no terminal, don't wake."""
    if wake is not None:
        return wake
    if not interactive:
        return False
    answer = input("If the computer is asleep at that time, wake it for the run? "
                   "(Otherwise it runs the next time the computer is awake.) [y/N] ")
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
    wake = ask_wake(wake, interactive)
    remember_pieces()
    b = backend()
    try:
        where = b.install(job_argv(exe), hour, minute, wake)
    except (OSError, RuntimeError, subprocess.CalledProcessError) as e:
        raise ScheduleError(f"Couldn't set up the nightly run: {e}") from None
    at = f"{hour:02d}:{minute:02d}"
    paths.schedule_file().write_text(json.dumps({"at": at, "wake": wake}), encoding="utf-8")
    print(f"Nightly run set for {at} every day ({where}).")
    if wake and (note := b.wake_note(hour, minute)):
        print(note)
    print(f"Each run is logged in {paths.nightly_log()}.\n"
          "Run it now with `inkvault nightly`, check on it with `inkvault status`, "
          "or turn it off with `inkvault schedule --off`.")


def disable():
    removed = backend().remove()
    paths.schedule_file().unlink(missing_ok=True)
    print("Nightly run removed. Your vault, backups and log are untouched." if removed
          else "There was no nightly run set up.")


def describe(now=None):
    """One line for `inkvault status`."""
    f = paths.schedule_file()
    if not f.exists() or not backend().installed():
        return "off (turn on with `inkvault schedule`)"
    s = json.loads(f.read_text(encoding="utf-8"))
    hour, minute = parse_at(s["at"])
    upcoming = next_run(hour, minute, now or datetime.now())
    return f"daily at {s['at']}{', wakes the computer' if s.get('wake') else ''}; next {upcoming:%Y-%m-%d %H:%M}"
