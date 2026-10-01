"""One module per OS for `inkvault schedule`.

Each has render functions (pure text, tested on every OS) and install / remove / query / installed / wake_note, which
call the OS's own scheduler. They all share the signature install(argv, hour, minute, wake) -> description.
remove() returns True (removed) or False (there was nothing to remove), and raises RuntimeError if it failed.
"""
from dataclasses import dataclass
from typing import Optional


@dataclass
class Job:
    """What the OS scheduler actually has, for `inkvault status`. None means "couldn't tell"."""

    present: bool
    enabled: Optional[bool] = None
    home: Optional[str] = None  # the --home the job passes
    executable: Optional[str] = None  # the Python it runs
    scheduler: str = ""  # "Task Scheduler", "launchd", "systemd", "cron"


def home_of(argv):
    """The value after --home in a job's argv, or None."""
    for i, arg in enumerate(argv[:-1]):
        if arg == "--home":
            return argv[i + 1]
    return None
