"""`inkvault nightly`: the scheduled refresh.

Export new captures from PiecesOS, rebuild search, digests and the dashboard, then back up the vault.
Each step can fail without stopping the others, and only a failed backup fails the run, because vault.db is
the one file that can't be rebuilt. Everything goes to nightly.log in the vault folder. Under pythonw (how
Windows runs the job) there is no console, so the log is the only record.
"""
from datetime import datetime

from . import paths

KEEP_RUNS = 30
START, END = "=== started", "=== finished"


def stamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(msg):
    with open(paths.nightly_log(), "a", encoding="utf-8") as f:
        for line in str(msg).splitlines() or [""]:
            f.write(f"{stamp()}  {line}\n")


class LogStream:
    """Stands in for stdout/stderr during a run, so whatever a step prints lands in the log."""

    def __init__(self):
        self.buf = ""

    def write(self, s):
        self.buf += s
        *lines, self.buf = self.buf.split("\n")
        for line in lines:
            self._emit(line)
        return len(s)

    def flush(self):
        self._emit(self.buf)
        self.buf = ""

    @staticmethod
    def _emit(line):
        line = line.rsplit("\r", 1)[-1].rstrip()  # a progress bar's final state, not every redraw
        if line.strip():
            log("  " + line)


def trim(keep=KEEP_RUNS):
    f = paths.nightly_log()
    if not f.exists():
        return
    lines = f.read_text(encoding="utf-8").splitlines(keepends=True)
    starts = [i for i, line in enumerate(lines) if START in line]
    if len(starts) > keep:
        f.write_text("".join(lines[starts[-keep]:]), encoding="utf-8")


def last_run():
    """(started, finished, result) for the newest run in the log, or None. finished is None if it never finished."""
    f = paths.nightly_log()
    if not f.exists():
        return None
    started = finished = result = None
    for line in f.read_text(encoding="utf-8").splitlines():
        if START in line:
            started, finished, result = line[:19], None, None
        elif END in line and started:
            finished, result = line[:19], line.split(END + ": ", 1)[1].rstrip(" =")
    return (started, finished, result) if started else None
