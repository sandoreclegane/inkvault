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
import time
import traceback
from datetime import datetime

from . import __version__, paths

KEEP_RUNS = 30
KEEP_BACKUPS = 7
PIECES_WAIT = 600  # after a wake PiecesOS may still be starting: keep looking this many seconds
PIECES_POLL = 60
START, END = "=== started", "=== finished"


def stamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(msg):
    with open(paths.nightly_log(), "a", encoding="utf-8", errors="replace") as f:
        for line in str(msg).splitlines() or [""]:
            f.write(f"{stamp()}  {line}\n")


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
        starts = [i for i, line in enumerate(lines) if START in line]
        if len(starts) > keep:
            tmp.write_text("".join(lines[starts[-keep]:]), encoding="utf-8")
            os.replace(tmp, f)
    except OSError:  # e.g. another process has the log open: trimming can wait for tomorrow
        pass
    finally:
        tmp.unlink(missing_ok=True)


def last_run():
    """(started, finished, result) for the newest run in the log, or None. finished is None if it never finished."""
    f = paths.nightly_log()
    if not f.exists():
        return None
    started = finished = result = None
    for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
        if START in line:
            started, finished, result = line[:19], None, None
        elif END in line and started:
            finished, result = line[:19], line.split(END + ": ", 1)[1].rstrip(" =")
    return (started, finished, result) if started else None


class Busy(Exception):
    """Another nightly run or rescue holds the lock. args[0] is its process id."""


LOCK_BYTE = 1024  # Windows locks this byte, past the pid text, so other processes can still read the pid


def try_lock(fd):
    """Take the kernel's exclusive lock on fd without waiting. False if another process holds it."""
    try:
        if sys.platform == "win32":
            import msvcrt
            os.lseek(fd, LOCK_BYTE, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


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


def lock_owner():
    """The pid written in the lock file. For display only: the kernel lock is what decides who holds it."""
    try:
        pid = int(paths.nightly_lock().read_text().strip())
    except (OSError, ValueError):
        return 0
    return pid if 0 < pid < 2**32 else 0


def running():
    try:
        fd = os.open(paths.nightly_lock(), os.O_RDWR | os.O_CREAT)
    except OSError:
        return False
    try:
        if not try_lock(fd):
            return True
        unlock(fd)
        return False
    finally:
        os.close(fd)


@contextlib.contextmanager
def lock():
    """Only one nightly run or rescue at a time.

    The lock is the operating system's, held on a permanent nightly.lock file, so it is released the moment
    the process ends however it ends: a crash or reboot can never leave a stale lock behind. The pid written
    inside is only so a message can say who has it.
    """
    fd = os.open(paths.nightly_lock(), os.O_RDWR | os.O_CREAT)
    try:
        if not try_lock(fd):
            raise Busy(lock_owner())
        os.lseek(fd, 0, os.SEEK_SET)
        os.ftruncate(fd, 0)
        os.write(fd, str(os.getpid()).encode())
        try:
            yield
        finally:
            unlock(fd)
    finally:
        os.close(fd)


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
    # A URI keeps the source read-only, but SQLite rejects the host part of a network (UNC) path's URI.
    if str(src).startswith("\\\\"):
        opened = sqlite3.connect(str(src))
    else:
        opened = sqlite3.connect(src.as_uri() + "?mode=ro", uri=True)
    with contextlib.closing(opened) as source:
        with contextlib.closing(sqlite3.connect(partial)) as target:
            source.backup(target)
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
    return export.run()


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
    return [("export", step_export), ("index", step_index), ("digest", step_digest),
            ("dashboard", step_dashboard), ("backup", backup)]


def run():
    """One nightly run. Returns the exit code: 1 only if the backup failed."""
    try:
        with lock():
            return run_steps()
    except Busy as busy:
        log(f"another run is in progress (pid {busy.args[0]}); not starting a second one")
        return 0
    except Exception:  # noqa: BLE001 - pythonw has no console, so the log is the only place this can go
        log(traceback.format_exc())
        return 1


def run_steps():
    log(f"{START} (InkVault {__version__}, pid {os.getpid()}) ===")  # first, so even a killed run shows up
    results = {}
    stream = LogStream()
    for name, step in steps():
        log(f"{name}:")
        try:
            with contextlib.redirect_stdout(stream), contextlib.redirect_stderr(stream):
                results[name] = "ok" if step() else "skipped"
        except Exception as e:  # noqa: BLE001 - one broken step must not stop the backup
            results[name] = f"failed: {type(e).__name__}: {' '.join(str(e).split())}"  # one line, or the END line splits
        finally:
            try:
                stream.finish()
            except Exception:  # noqa: BLE001 - logging trouble must never stop the backup
                pass
    overall = "failed" if results["backup"].startswith("failed") else "ok"
    log(f"{END}: {overall} ({', '.join(f'{k} {v}' for k, v in results.items())}) ===")
    trim()
    return 1 if overall == "failed" else 0
