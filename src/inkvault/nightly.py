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
STALE_LOCK = 12 * 3600  # a lock older than this is a crash's leftover, whatever its pid is now doing
FRESH_LOCK = 60  # an empty lock younger than this is another process mid-create
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
    lines = f.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    starts = [i for i, line in enumerate(lines) if START in line]
    if len(starts) > keep:
        tmp = f.with_name(f.name + ".tmp")
        tmp.write_text("".join(lines[starts[-keep]:]), encoding="utf-8")
        os.replace(tmp, f)


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


def pid_alive(pid):
    if sys.platform == "win32":
        # os.kill(pid, 0) would send Ctrl+C on Windows, so ask the kernel instead.
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        k32.GetExitCodeProcess.restype = wintypes.BOOL
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.get_last_error() == 5  # access denied: it exists, it's just not ours
        try:
            code = wintypes.DWORD()
            return bool(k32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259  # STILL_ACTIVE
        finally:
            k32.CloseHandle(handle)
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


def lock_held():
    """True if the lock file exists and its holder may still be working.

    Stale means: older than STALE_LOCK (a pid can be reused after a crash or reboot), or its pid is gone.
    An empty lock is another process part-way through creating it, so it counts as held for a minute.
    """
    try:
        age = time.time() - paths.nightly_lock().stat().st_mtime
    except OSError:
        return False
    if age > STALE_LOCK:
        return False
    pid = lock_owner()
    return pid_alive(pid) if pid else age < FRESH_LOCK


def running():
    return lock_held()


def publish_lock():
    """Create the lock with its pid already inside, atomically. False if someone else holds it.

    The pid goes into a temp file first and is then linked (POSIX) or renamed (Windows) into place; both
    refuse to replace an existing lock, and a reader never sees a half-written one.
    """
    f = paths.nightly_lock()
    tmp = f.with_name(f"{f.name}.{os.getpid()}.tmp")
    tmp.write_text(str(os.getpid()))
    try:
        if sys.platform == "win32":
            os.rename(tmp, f)
        else:
            os.link(tmp, f)
        return True
    except FileExistsError:
        return False
    finally:
        tmp.unlink(missing_ok=True)


@contextlib.contextmanager
def lock():
    """Only one nightly run or rescue at a time. A lock left by a process that's gone is taken over."""
    f = paths.nightly_lock()
    for _ in range(3):
        if publish_lock():
            break
        owner = lock_owner()
        if lock_held():
            raise Busy(owner)
        if lock_owner() != owner:  # someone replaced it while we looked: look again
            continue
        try:
            f.unlink(missing_ok=True)
        except OSError:
            raise Busy(owner)
    else:
        raise Busy(lock_owner())
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
    with contextlib.closing(sqlite3.connect(src.as_uri() + "?mode=ro", uri=True)) as source,             contextlib.closing(sqlite3.connect(partial)) as target:
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
