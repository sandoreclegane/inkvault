"""`inkvault nightly`: the scheduled refresh.

Export new captures from PiecesOS, back up the vault right away (only export writes vault.db, so a slow digest
can never starve the backup), then rebuild search, digests and the dashboard.
Each step can fail without stopping the others, and only a failed backup fails the run, because vault.db is
the one file that can't be rebuilt. Everything goes to nightly.log in the vault folder. Under pythonw (how
Windows runs the job) there is no console, so the log is the only record.
"""
import contextlib
import errno
import os
import re
import sqlite3
import sys
import time
import traceback
from datetime import datetime

from . import __version__, paths

KEEP_RUNS = 30
KEEP_BACKUPS = 7
PIECES_WAIT = 600  # after a wake PiecesOS may still be starting: keep looking this many seconds
PIECES_POLL = 60
LOCK_WAIT = 3 * 3600  # a nightly run that finds a rescue holding the lock waits this long for it
LOCK_POLL = 30
EXPORT_BUDGET = 3 * 3600  # export stops cleanly after this, leaving time to back up inside Windows' 4-hour task limit
START, END = "=== started", "=== finished"
# A record is a line that begins with its timestamp. Step output is indented under it, so a step that happens to
# print a marker is never taken for one, and a line cut off mid-write is not a finished run.
STAMP = r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d  "
START_RE = re.compile(STAMP + re.escape(START))
END_RE = re.compile(STAMP + re.escape(END) + r": (.*) ===\s*$")
LOG_UNREADABLE = ("unknown", "unknown", "unknown (log unreadable)")  # what last_run returns when it can't read


def stamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


log_failures = []  # why nightly.log couldn't be written during this run (empty if it always could)


def append(path, text):
    with open(path, "a", encoding="utf-8", errors="replace") as f:
        f.write(text)


def log(msg):
    """Add lines to nightly.log. Never raises: a locked or full log must not stop the backup.

    If nightly.log can't be written the failure is remembered (run() then exits 1, so the scheduler records it)
    and the lines go to nightly-fallback.log in the same folder instead."""
    text = "".join(f"{stamp()}  {line}\n" for line in str(msg).splitlines() or [""])
    try:
        append(paths.nightly_log(), text)
        return
    except Exception as e:  # noqa: BLE001 - OSError mostly, but nothing here may stop the run
        failure = f"{type(e).__name__}: {e}"
    try:
        if not log_failures:
            text = f"{stamp()}  couldn't write nightly.log ({failure}); logging here instead\n" + text
        append(paths.nightly_fallback_log(), text)
    except Exception:  # noqa: BLE001 - nowhere left to write; the exit code still reports it
        pass
    log_failures.append(failure)


class LogStream:
    """Stands in for stdout/stderr during a run, so whatever a step prints lands in the log."""

    encoding, errors = "utf-8", "replace"

    def __init__(self):
        self.buf = ""

    def write(self, s):
        self.buf += s
        *lines, self.buf = self.buf.split("\n")
        for line in lines:
            self._emit(line)
        return len(s)

    def flush(self):
        """A no-op: tqdm flushes after every redraw, which would log each one instead of the final state."""

    def finish(self):
        self._emit(self.buf)
        self.buf = ""

    def isatty(self):
        return False

    @staticmethod
    def _emit(line):
        line = line.rsplit("\r", 1)[-1].rstrip()  # a progress bar's final state, not every redraw
        if line.strip():
            log("  " + line)


def trim(keep=KEEP_RUNS):
    f = paths.nightly_log()
    if not f.exists():
        return
    tmp = f.with_name(f.name + ".tmp")
    try:
        lines = f.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        starts = [i for i, line in enumerate(lines) if START_RE.match(line)]
        if len(starts) > keep:
            tmp.write_text("".join(lines[starts[-keep]:]), encoding="utf-8")
            os.replace(tmp, f)
    except OSError:  # e.g. another process has the log open: trimming can wait for tomorrow
        pass
    finally:
        with contextlib.suppress(OSError):  # e.g. antivirus holds the temp file: it's overwritten next time
            tmp.unlink(missing_ok=True)


def last_run():
    """(started, finished, result) for the newest run in the log, or None. finished is None if it never finished
    (or its end line is cut off). LOG_UNREADABLE if the log can't be read."""
    f = paths.nightly_log()
    try:
        if not f.exists():
            return None
        lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return LOG_UNREADABLE
    started = finished = result = None
    for line in lines:
        if START_RE.match(line):
            started, finished, result = line[:19], None, None
        elif started and (m := END_RE.match(line)):
            finished, result = line[:19], m.group(1)
    return (started, finished, result) if started else None


class Busy(Exception):
    """Another nightly run or rescue holds the lock. args[0] is its process id."""


# The errors a lock call gives when someone else really holds the lock. Anything else (no lock support on a
# network or FUSE drive, a bad descriptor) is a real problem that must show up, not look like "busy" forever.
BUSY_ERRNOS = {errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK, getattr(errno, "EDEADLOCK", errno.EDEADLK)}
LOCK_BYTE = 1024  # Windows locks this byte, past the pid text, so other processes can still read the pid


def try_lock(fd):
    """Take the kernel's exclusive lock on fd without waiting. False if another process holds it.

    Raises OSError for anything that isn't contention (see BUSY_ERRNOS)."""
    try:
        if sys.platform == "win32":
            import msvcrt
            os.lseek(fd, LOCK_BYTE, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError as e:
        if e.errno in BUSY_ERRNOS:
            return False
        raise


def unlock(fd):
    try:
        if sys.platform == "win32":
            import msvcrt
            os.lseek(fd, LOCK_BYTE, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass


def lock_lines():
    try:
        return paths.nightly_lock().read_text(errors="replace").splitlines()
    except OSError:
        return []


def lock_owner():
    """The pid written in the lock file. For display only: the kernel lock is what decides who holds it."""
    try:
        pid = int((lock_lines() or [""])[0].strip())
    except ValueError:
        return 0
    return pid if 0 < pid < 2**32 else 0


def lock_purpose():
    """What the holder is doing, "nightly" or "rescue" (from the lock file's second line), or "" if unknown."""
    lines = lock_lines()
    return lines[1].strip() if len(lines) > 1 and lines[1].strip() in ("nightly", "rescue") else ""


def running():
    """Is a run going? Call it from a process that does NOT hold the lock: on NFS, Linux emulates flock with
    per-process locks, so probing from inside lock() would release the holder's own lock."""
    try:
        fd = os.open(paths.nightly_lock(), os.O_RDWR | os.O_CREAT)
    except OSError:
        return False
    try:
        if not try_lock(fd):
            return True
        unlock(fd)
        return False
    except OSError:  # can't tell (no lock support): don't claim a run is going
        return False
    finally:
        os.close(fd)


@contextlib.contextmanager
def lock(purpose="rescue"):
    """Only one nightly run or rescue at a time.

    The lock is the operating system's, held on a permanent nightly.lock file, so it is released the moment
    the process ends however it ends: a crash or reboot can never leave a stale lock behind. The pid written
    inside (and the purpose, "nightly" or "rescue") is only so a message can say who has it.
    """
    fd = os.open(paths.nightly_lock(), os.O_RDWR | os.O_CREAT)
    try:
        if not try_lock(fd):
            time.sleep(0.5)  # a running() probe from another process holds it for microseconds: try once more
            if not try_lock(fd):
                raise Busy(lock_owner())
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            os.ftruncate(fd, 0)
            os.write(fd, f"{os.getpid()}\n{purpose}".encode())
            yield
        finally:
            unlock(fd)
    finally:
        os.close(fd)


def quick_check(path):
    """SQLite's `PRAGMA quick_check` on the file: "ok", or what it found."""
    with contextlib.closing(sqlite3.connect(path)) as db:
        return db.execute("PRAGMA quick_check").fetchone()[0]


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
    with contextlib.closing(paths.connect_ro(src)) as source:
        with contextlib.closing(sqlite3.connect(partial)) as target:
            source.backup(target)
    if quick_check(partial) != "ok":  # never rotate good backups out for a bad copy
        partial.unlink(missing_ok=True)
        raise RuntimeError("backup copy failed its integrity check; older backups kept")
    os.replace(partial, out)
    for old in sorted(folder.glob("vault-*.db"))[:-KEEP_BACKUPS]:
        old.unlink()
    print(f"saved {out.name} ({out.stat().st_size / 1e6:,.0f} MB)")
    return True


def wait_for_pieces():
    from .export import PiecesOS
    deadline = time.monotonic() + PIECES_WAIT
    while True:
        pos, _version = PiecesOS.find()
        if pos or time.monotonic() >= deadline:
            return pos
        print(f"PiecesOS isn't answering yet; trying again in {PIECES_POLL}s")
        time.sleep(PIECES_POLL)


def step_export():
    from . import export
    if not wait_for_pieces():
        print(f"PiecesOS not reachable on port(s) {', '.join(map(str, export.ports()))}; skipped. "
              "If it uses another port: inkvault --pieces-ports PORT schedule")
        return False
    return export.run(budget_seconds=EXPORT_BUDGET)


def step_index():
    from . import index
    return index.build()


def step_digest():
    from . import digest
    return digest.run(model=digest.DEFAULT_MODEL)


def step_dashboard():
    from . import dashboard
    return bool(dashboard.build())


def steps():
    # Looked up at call time, so a test can replace any one of them.
    # Only export writes vault.db, so the backup follows it directly: a slow digest can't starve it.
    return [("export", step_export), ("backup", backup), ("index", step_index), ("digest", step_digest),
            ("dashboard", step_dashboard)]


started = []  # non-empty once this run has written its START line


def acquire(stack):
    """Take the lock, waiting for a rescue (or any holder of unknown purpose) to finish. Raises Busy if another
    nightly run holds it (it is already doing this job) or if the wait runs out."""
    deadline = time.monotonic() + LOCK_WAIT
    waited = False
    while True:
        try:
            return stack.enter_context(lock("nightly"))
        except Busy as busy:
            if lock_purpose() == "nightly":
                raise
            if time.monotonic() >= deadline:
                busy.timed_out = True
                raise
            if not waited:
                who = f"pid {busy.args[0]}" if busy.args[0] else "pid unknown"
                log(f"waiting for a rescue or another run to finish ({who} holds the lock)")
                waited = True
            time.sleep(LOCK_POLL)


def run():
    """One nightly run. Returns the exit code: 1 if the backup failed, if nightly.log couldn't be written, or
    if the run never got the lock. Run `inkvault nightly` while another nightly run is going and it exits 0."""
    started.clear()
    log_failures.clear()
    code = _run()
    return 1 if log_failures else code


def _run():
    try:
        with contextlib.ExitStack() as stack:
            try:
                acquire(stack)
            except Busy:
                raise
            except OSError as e:  # e.g. a drive without file locking: better to run unguarded than not at all
                log(f"warning: couldn't take the lock ({e}); running without it")
            return run_steps()
    except Busy as busy:
        if getattr(busy, "timed_out", False):
            log(f"{START} (InkVault {__version__}, pid {os.getpid()}) ===")
            log(f"{END}: failed (another run held the lock for {LOCK_WAIT / 3600:g} hours) ===")
            return 1
        who = f"pid {busy.args[0]}" if busy.args[0] else "pid unknown"
        log(f"another nightly run is already in progress ({who}); not starting a second one")
        return 0
    except Exception as e:  # noqa: BLE001 - pythonw has no console, so the log is the only place this can go
        trace = traceback.format_exc()
        if not started:
            log(f"{START} (InkVault {__version__}, pid {os.getpid()}) ===")
        log(f"{END}: failed ({type(e).__name__}: {' '.join(str(e).split())}) ===")
        log(trace)
        return 1


@contextlib.contextmanager
def keep_awake():
    """Ask Windows not to sleep while the run is going (a sleep mid-run would suspend it). No-op elsewhere.

    The request is per thread and lapses on its own when the process ends. It can never raise: failing to
    ask must not stop the backup. (macOS does this through caffeinate in the LaunchAgent.)
    """
    ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
    set_state = None
    if sys.platform == "win32":
        try:
            import ctypes
            set_state = ctypes.windll.kernel32.SetThreadExecutionState
            set_state(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
        except Exception:  # noqa: BLE001
            set_state = None
    try:
        yield
    finally:
        if set_state:
            try:
                set_state(ES_CONTINUOUS)
            except Exception:  # noqa: BLE001
                pass


def run_steps():
    with keep_awake():
        return _run_steps()


def _run_steps():
    log(f"{START} (InkVault {__version__}, pid {os.getpid()}) ===")  # first, so even a killed run shows up
    started.append(1)
    results = {}
    stream = LogStream()
    for name, step in steps():
        log(f"{name}:")
        try:
            with contextlib.redirect_stdout(stream), contextlib.redirect_stderr(stream):
                done = step()
                results[name] = "partial" if done == "partial" else "ok" if done else "skipped"
        except Exception as e:  # noqa: BLE001 - one broken step must not stop the backup
            results[name] = f"failed: {type(e).__name__}: {' '.join(str(e).split())}"  # one line, or the END line splits
        finally:
            try:
                stream.finish()
            except Exception:  # noqa: BLE001 - logging trouble must never stop the backup
                pass
    if results["backup"].startswith("failed"):
        overall = "failed"
    elif results["backup"] == "skipped":  # backup() only skips when there is no vault
        overall = "ok, nothing to back up yet"
    else:
        overall = "ok"
    log(f"{END}: {overall} ({', '.join(f'{k} {v}' for k, v in results.items())}) ===")
    trim()
    return 1 if overall == "failed" else 0
