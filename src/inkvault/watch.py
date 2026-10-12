"""`inkvault watch` (spec §1–§5): the timeline loop. Samples every 2 s, feeds the pure core, writes through the
per-stretch queue, and hands reads to the worker without ever waiting on it."""
import hashlib
import json
import os
import sys
import time
import uuid
from dataclasses import replace

from . import paths, redact, screen, screenstore, stretches, winapi, worker

POLL = 2.0
HEARTBEAT_EVERY = 30.0
STOP_WAIT = 15.0
REOPEN_EVERY = 10.0


class Watcher:
    def __init__(self, sampler, supervisor, conn, source, clock=time.monotonic, utc=screen.now):
        self.sampler, self.supervisor, self.conn, self.source = sampler, supervisor, conn, source
        self.clock, self.utc = clock, utc
        self.core, self.queue = stretches.Core(), screenstore.Queue()
        self.gen = screen.removal_gen(conn)
        self.settings, self.settings_gen = None, 0
        self.pending = None                    # (request id, Identity, stretch id, removal gen, settings gen)
        self.stored = {"sid": None, "hash": None}   # last text hash *committed* for the open stretch (P6)
        self.last_valid_utc = None
        self.busy_gaps = self.stale_resets = 0
        self.settings_error = self.db_error = self.heartbeat_error = None
        self.last_beat = None

    @property
    def last_error(self):
        """Each source clears only its own error, so one success never hides another failure."""
        return self.settings_error or self.db_error or self.heartbeat_error

    def refresh_settings(self):
        """The one place settings are loaded (R1): every load, wherever it happens, applies its transition before
        anything else, so a change seen while accepting a result empties the queue's text before the flush."""
        settings, error = self.source.poll()
        if settings is None:
            self.fail_closed(error)
            return None
        if self.source.generation != self.settings_gen:
            self.settings_changed()
        self.settings, self.settings_error = settings, None
        return settings

    def tick(self):
        if paths.watch_stop().exists():
            self.stop()
            return False
        settings = self.refresh_settings()
        if settings is not None:
            s = self.sampler()
            self.last_valid_utc = s.utc
            if self.queue.blocked():
                s = replace(s, window=None)
                self.busy_gaps += 1
            for action in self.core.step(s, settings):
                self.handle(action)
        self.collect()   # every tick, valid settings or not: deadlines and reaping never pause (R3)
        self.flush()
        if self.last_beat is None or self.clock() - self.last_beat >= HEARTBEAT_EVERY:
            self.write_heartbeat()
        return True

    def handle(self, a):
        if isinstance(a, stretches.Opened):
            ok = self.queue.push(screenstore.Write("open", a.stretch_id, self.gen, started=screen.iso(a.started),
                                                   ended=screen.iso(a.ended), app=a.identity.exe,
                                                   title=redact.clean_title(a.identity.title)))
            if not ok:
                self.core.drop()
                self.busy_gaps += 1
        elif isinstance(a, (stretches.Extended, stretches.Closed)):
            kind = "extend" if isinstance(a, stretches.Extended) else "close"
            self.queue.push(screenstore.Write(kind, a.stretch_id, self.gen, ended=screen.iso(a.ended)))
        elif isinstance(a, stretches.ReadDue) and not self.supervisor.busy():
            i = a.identity
            rid = self.supervisor.submit({"hwnd": i.hwnd, "pid": i.pid, "created": i.created, "exe": i.exe,
                                          "title_sha256": winapi.title_sha256(i.title)})
            if rid is not None:
                self.pending = (rid, i, a.stretch_id, self.gen, self.settings_gen)

    def collect(self):
        r = self.supervisor.poll()
        if r is None or self.pending is None or r["id"] != self.pending[0]:
            return
        rid, ident, sid, gen, sgen = self.pending
        self.pending = None
        if r["status"] != "ok":
            if r["error"]:
                self.log(f"read failed in {ident.exe}: {worker.error_name(r['error'])}")
            return
        settings = self.refresh_settings()                     # reload right before accepting (P3, R1)
        if settings is None or self.settings_gen != sgen:
            return
        fresh = self.sampler()
        if (gen != self.gen
                or fresh.window != ident or fresh.locked or fresh.idle_s >= stretches.IDLE_LIMIT
                or settings.paused(fresh.utc) or settings.never(ident.exe, ident.title)
                or not settings.text_app(ident.exe) or self.core.open is None or self.core.open.id != sid):
            return
        text = redact.clean_text(r["lines"])
        if not text:
            return
        h = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if (self.stored["sid"] == sid and self.stored["hash"] == h) or self.queue.has_text(sid, h):
            return
        self.queue.push(screenstore.Write("text", sid, gen, settings_gen=sgen,
                                          text_id="screentext:" + str(uuid.uuid4()), created=screen.iso(fresh.utc),
                                          text=text, text_hash=h, app=ident.exe))

    def flush(self):
        # belt and braces (R1): text from any settings generation but the accepted one never reaches the vault
        self.queue.drop_text(lambda w: w.settings_gen != self.settings_gen)
        r = self.queue.flush(self.conn)
        for w in r.texts:
            self.stored = {"sid": w.stretch_id, "hash": w.text_hash}
        if r.error:
            self.db_error = f"database: {r.error}"
            self.log(self.db_error)
        elif r.written:
            self.db_error = None
        if r.stale:
            self.reset_after_removal()

    def reset_after_removal(self):
        self.stale_resets += 1
        self.core.drop()
        self.queue.clear()
        self.supervisor.abandon()
        self.pending = None
        self.stored = {"sid": None, "hash": None}
        self.gen = screen.removal_gen(self.conn)

    def settings_changed(self):
        """A new settings generation (P3): no text read under the old rules is ever written."""
        self.settings_gen = self.source.generation
        self.supervisor.abandon()
        self.pending = None
        self.queue.drop_text(lambda w: True)

    def fail_closed(self, error):
        self.settings_error = error
        for a in self.core.close(self.last_valid_utc or self.utc()):
            self.handle(a)
        self.supervisor.abandon()
        self.pending = None
        self.queue.drop_text(lambda w: True)
        self.stored = {"sid": None, "hash": None}

    def stop(self):
        for a in self.core.close(self.utc()):
            self.handle(a)
        self.flush()
        self.supervisor.close()
        paths.watch_stop().unlink(missing_ok=True)

    def heartbeat(self):
        o = self.core.open
        sup = self.supervisor
        return {"pid": os.getpid(), "utc": screen.iso(self.utc()),
                "open": {"app": o.identity.exe, "started": screen.iso(o.started)} if o else None,
                "paused": bool(self.settings and self.settings.paused(self.utc())),
                "settings_error": self.settings_error, "db_error": self.db_error,
                "heartbeat_error": self.heartbeat_error, "worker_requests_dropped": getattr(sup, "dropped_requests", 0),
                "queue": len(self.queue), "dropped_text": self.queue.dropped_text,
                "busy_gaps": self.busy_gaps, "timeouts": getattr(sup, "timeouts", 0),
                "job_error": getattr(sup, "job_error", None),
                "cooling": sorted(k for k in getattr(sup, "cooldowns", {}) if sup.cooling(k))}

    def write_heartbeat(self):
        self.last_beat = self.clock()
        try:
            screen.write_atomic(paths.watch_heartbeat(), self.heartbeat())
            self.heartbeat_error = None
        except OSError as e:
            self.heartbeat_error = f"heartbeat: {type(e).__name__}"

    def log(self, line):
        try:
            with open(paths.watch_log(), "a", encoding="utf-8") as f:
                f.write(f"{screen.iso(self.utc())} {line}\n")
        except OSError:
            pass


def open_store(stopping, sleep=time.sleep, clock=time.monotonic, every=None):
    """Open the watcher's connection, retrying every REOPEN_EVERY s while the vault can't be opened (spec §1). A
    recovery heartbeat says so (R8), and a stop is honoured within half a second. None if a stop was requested."""
    every = REOPEN_EVERY if every is None else every
    attempts = 0
    while not stopping():
        try:
            return screenstore.connect()
        except (screenstore.Busy, screenstore.StoreError) as e:
            attempts += 1
            try:
                screen.write_atomic(paths.watch_heartbeat(), {
                    "pid": os.getpid(), "utc": screen.iso(screen.now()), "open": None, "paused": False,
                    "state": "waiting for the vault", "attempts": attempts,
                    "db_error": type(e).__name__, "settings_error": None, "heartbeat_error": None})
            except OSError:
                pass
            end = clock() + every
            while clock() < end and not stopping():
                sleep(min(0.5, every))
    return None


def run():
    if sys.platform != "win32":
        print("screen capture is Windows-only for now")
        return 1
    from . import nightly, worker
    fd = os.open(paths.watch_lock(), os.O_RDWR | os.O_CREAT)
    try:
        if not nightly.try_lock(fd):
            pid = (paths.watch_lock().read_text(errors="replace").splitlines() or ["?"])[0]
            print(f"already running (pid {pid})")
            return 0
        os.lseek(fd, 0, os.SEEK_SET); os.ftruncate(fd, 0); os.write(fd, f"{os.getpid()}\nwatch".encode())
        try:
            screen.prepare()
        except Exception as e:  # noqa: BLE001 — settings or vault trouble: the loop fails closed and retries
            print(f"screen capture is waiting: {type(e).__name__}")
        conn = open_store(lambda: paths.watch_stop().exists())
        if conn is None:
            paths.watch_stop().unlink(missing_ok=True)
            return 0
        python = sys.executable.replace("pythonw.exe", "python.exe")
        sup = worker.Supervisor([python, "-m", "inkvault.screentext", "--worker"])
        w = Watcher(lambda: winapi.sample(time.monotonic(), screen.now()), sup, conn, screen.SettingsSource())
        try:
            next_tick = time.monotonic()
            while w.tick():
                next_tick += POLL
                time.sleep(max(0.0, next_tick - time.monotonic()))
            return 0
        except Exception as e:  # noqa: BLE001
            w.log(f"fatal: {type(e).__name__}")
            return 1
        finally:
            sup.close()
            conn.close()
            paths.watch_heartbeat().unlink(missing_ok=True)
            nightly.unlock(fd)
    finally:
        os.close(fd)
