"""`inkvault nightly`: the scheduled refresh.

Export new captures from PiecesOS, rebuild search, digests and the dashboard, then back up the vault.
Each step can fail without stopping the others, and only a failed backup fails the run, because vault.db is
the one file that can't be rebuilt. Everything goes to nightly.log in the vault folder. Under pythonw (how
Windows runs the job) there is no console, so the log is the only record.
"""
import contextlib
import os
import sqlite3
import sys
from datetime import datetime

from . import paths

KEEP_RUNS = 30
KEEP_BACKUPS = 7
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


class Busy(Exception):
    """Another nightly run or rescue holds the lock. args[0] is its process id."""


def pid_alive(pid):
    if sys.platform == "win32":
        # os.kill(pid, 0) would send Ctrl+C on Windows, so ask the kernel instead.
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel32.CloseHandle(handle)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # exists, owned by someone else
        return True
    return True


def lock_owner():
    try:
        return int(paths.nightly_lock().read_text().strip())
    except (OSError, ValueError):
        return 0


def running():
    pid = lock_owner()
    return bool(pid) and pid_alive(pid)


@contextlib.contextmanager
def lock():
    """Only one nightly run or rescue at a time. A lock left by a process that's gone is taken over."""
    f = paths.nightly_lock()
    for _ in range(2):
        try:
            fd = os.open(f, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            pid = lock_owner()
            if pid and pid_alive(pid):
                raise Busy(pid)
            f.unlink(missing_ok=True)
    else:
        raise Busy(lock_owner())
    with os.fdopen(fd, "w") as fh:
        fh.write(str(os.getpid()))
    try:
        yield
    finally:
        f.unlink(missing_ok=True)


def backup(today=None):
    """Copy vault.db to backups/vault-YYYY-MM-DD.db and keep the newest KEEP_BACKUPS.

    SQLite's backup API gives a consistent copy even while the vault is open in WAL mode, which a plain file
    copy doesn't. The copy is written beside its final name first, so a crash never leaves a half-written
    backup that looks complete.
    """
    src = paths.vault_db()
    if not src.exists():
        print("no vault yet, nothing to back up")
        return False
    folder = paths.backups_dir()
    folder.mkdir(exist_ok=True)
    day = today or datetime.now().strftime("%Y-%m-%d")
    out, partial = folder / f"vault-{day}.db", folder / f"vault-{day}.db.partial"
    partial.unlink(missing_ok=True)
    source = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    target = sqlite3.connect(partial)
    with target:
        source.backup(target)
    source.close()
    target.close()
    os.replace(partial, out)
    for old in sorted(folder.glob("vault-*.db"))[:-KEEP_BACKUPS]:
        old.unlink()
    print(f"saved {out.name} ({out.stat().st_size / 1e6:,.0f} MB)")
    return True
