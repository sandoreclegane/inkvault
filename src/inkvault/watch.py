"""`inkvault watch` (spec §1–§5): the timeline loop. Samples every 2 s, feeds the pure core, writes through the
per-stretch queue, and hands reads to the worker without ever waiting on it."""
import hashlib
import json
import os
import sqlite3
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
        try:
            self.gen = screen.removal_gen(conn)
        except sqlite3.Error:
            self.gen = 0       # the first write's Stale reset adopts the real generation
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
                or fresh.window is None or not stretches.same_window(fresh.window, ident)
                or fresh.locked or fresh.idle_s >= stretches.IDLE_LIMIT
                or settings.paused(fresh.utc) or settings.never(ident.exe, ident.title)
                or settings.never(fresh.window.exe, fresh.window.title)
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
            error = f"database: {r.error}"
            if error != self.db_error:        # log a failure when it starts or changes, not on every tick
                self.log(error)
            self.db_error = error
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
        try:
            self.gen = screen.removal_gen(self.conn)
        except sqlite3.Error as e:
            # keep the old generation: the next write raises Stale again and the reset retries
            self.db_error = f"database: {type(e).__name__}"

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
        except Exception as e:  # noqa: BLE001 — a heartbeat must never take the loop down
            self.heartbeat_error = f"heartbeat: {type(e).__name__}"

    def log(self, line):
        try:
            with open(paths.watch_log(), "a", encoding="utf-8") as f:
                f.write(f"{screen.iso(self.utc())} {line}\n")
        except OSError:
            pass


def open_store(stopping, sleep=time.sleep, clock=time.monotonic, every=None):
    """Open the watcher's connection, retrying every REOPEN_EVERY s while the vault can't be opened (spec §1). A
    recovery heartbeat says so (R8), first-use settings are retried too, and a stop is honoured
    during the retry wait within half a second. None if a stop was requested."""
    every = REOPEN_EVERY if every is None else every
    attempts = 0
    while not stopping():
        conn = None
        try:
            conn = screenstore.connect()
            # A failed prepare() on first use must not leave the recovered watcher waiting for a file that
            # was never created. An initialized home with lost settings still fails closed in SettingsSource.
            if not screen.initialized(conn):
                screen.ensure_initialized(conn)
            return conn
        except (screenstore.Busy, screenstore.StoreError, screen.SettingsBusy, OSError, sqlite3.Error) as e:
            if conn is not None:
                try:
                    conn.close()
                except sqlite3.Error:
                    pass
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


def claim_stop_file():
    """A watch.stop is addressed to whoever holds watch.lock; one left behind by a watcher that has gone is stale."""
    paths.watch_stop().unlink(missing_ok=True)


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
        claim_stop_file()
        os.lseek(fd, 0, os.SEEK_SET); os.ftruncate(fd, 0); os.write(fd, f"{os.getpid()}\nwatch".encode())
        try:
            screen.prepare()
        except Exception as e:  # noqa: BLE001 — settings or vault trouble: the loop fails closed and retries
            print(f"screen capture is waiting: {type(e).__name__}")
        conn = open_store(lambda: paths.watch_stop().exists())
        if conn is None:
            paths.watch_stop().unlink(missing_ok=True)
            return 0
        sup = w = None
        try:
            python = sys.executable.replace("pythonw.exe", "python.exe")
            sup = worker.Supervisor([python, "-m", "inkvault.screentext", "--worker"])
            w = Watcher(lambda: winapi.sample(time.monotonic(), screen.now()), sup, conn, screen.SettingsSource())
            next_tick = time.monotonic()
            while w.tick():
                # re-anchor after a stall (sleep/suspend) so it never produces a burst of catch-up ticks
                next_tick = max(next_tick + POLL, time.monotonic())
                time.sleep(max(0.0, next_tick - time.monotonic()))
            return 0
        except Exception as e:  # noqa: BLE001
            if w is not None:
                w.log(f"fatal: {type(e).__name__}")
            return 1
        finally:
            for cleanup in (sup.close if sup else None, conn.close,
                            lambda: paths.watch_heartbeat().unlink(missing_ok=True), lambda: nightly.unlock(fd)):
                try:
                    if cleanup:
                        cleanup()
                except Exception:  # noqa: BLE001 — cleanup must not replace the return value
                    pass
    finally:
        os.close(fd)


MIN_TOOL = (0, 3, 0)


def lock_held():
    from . import nightly
    if not paths.watch_lock().exists():
        return False
    fd = os.open(paths.watch_lock(), os.O_RDWR)
    try:
        if nightly.try_lock(fd):
            nightly.unlock(fd)
            return False
        return True
    finally:
        os.close(fd)


def lock_owner():
    try:
        return int((paths.watch_lock().read_text(errors="replace").splitlines() or [""])[0])
    except (OSError, ValueError):
        return None


def heartbeat():
    try:
        return json.loads(paths.watch_heartbeat().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def heartbeat_ok(hb, since=None, max_age=10.0):
    """A heartbeat from the live lock holder, 0–max_age s old, and (if since) written after since (R4)."""
    try:
        t = screen.parse(hb["utc"])
    except (TypeError, KeyError, ValueError):
        return False
    age = (screen.now() - t).total_seconds()
    return (0 <= age <= max_age and hb.get("pid") == lock_owner() and lock_held()
            and (since is None or t >= since))


def running_confirmed(wait, since):
    end = time.monotonic() + wait
    while time.monotonic() < end:
        if heartbeat_ok(heartbeat(), since):
            return True
        time.sleep(0.5)
    return False


def stop_watcher(wait=None):
    """Cooperative stop (spec §7): ask through watch.stop, then wait for watch.lock to be released. Never kills a
    pid from a file. True only when the lock was actually released."""
    wait = STOP_WAIT if wait is None else wait
    if not lock_held():
        return True
    paths.watch_stop().write_text("stop\n", encoding="utf-8")
    end = time.monotonic() + wait
    while time.monotonic() < end:
        if not lock_held():
            return True
        time.sleep(0.2)
    return False


def under_pythonw():
    return sys.platform == "win32" and sys.executable.lower().endswith("pythonw.exe")


def message_box(text):
    a = winapi.api()
    a.user32.MessageBoxW(None, text, "InkVault", winapi.MB_OK | winapi.MB_ICONINFORMATION | winapi.MB_SETFOREGROUND)


def notify(text):
    """Say something to whoever ran the command. Under pythonw (the desktop shortcuts) there's no console, so the
    outcome is shown in a message box instead; a failure to show it never changes the result."""
    print(text)
    if under_pythonw():
        try:
            message_box(text)
        except Exception:  # noqa: BLE001
            pass


def change(edit, done, say=print):
    """Settings edits touch only screen.json under screen.lock: no vault, no nightly lock (R1, R2). Only a home
    with no screen.json at all is prepared first (a true first use, or lost settings, which prepare refuses)."""
    try:
        if not paths.screen_file().exists():
            screen.prepare()
        screen.edit(edit)
    except screen.SettingsBusy:
        say("Another command is changing screen capture settings right now; nothing changed. Try again.")
        return 1
    except screen.SettingsError as e:
        say(f"Nothing changed: {e}")
        return 1
    except OSError as e:
        say(f"Nothing changed: {type(e).__name__}")
        return 1
    say(f"{done} Saved; a running watcher applies it within 2 seconds.")
    return 0


def add_to(key, value):
    def f(d):
        if value.lower() not in {x.lower() for x in d[key]}:
            d[key].append(value)
    return f


def remove_from(key, value):
    return lambda d: d.update({key: [x for x in d[key] if x.lower() != value.lower()]})


def show_apps():
    s = screen.validate(screen.read_file())
    print("text:  " + ", ".join(sorted(s.text_apps)))
    print("never: " + ", ".join(sorted(s.never_apps)))
    print("never titles: " + ", ".join(s.never_titles))
    try:
        from . import browsers
        sites = browsers.load_choices()["skip_sites"]
    except Exception:  # noqa: BLE001
        sites = []
    print("Banks and other sites: add their page-title text with --never-title (browser-history's skip list holds "
          "domains, which titles often don't show)" + (f"; your skip list: {', '.join(sites)}" if sites else "") + ".")


def expected_task(pythonw, user):
    return {"command": str(pythonw), "arguments": ["-m", "inkvault", "--home", str(paths.home()), "watch"],
            "user": user, "logon_type": "InteractiveToken", "limit": "PT0S", "restart": ("PT1M", "3"),
            "instances": "IgnoreNew", "enabled": True, "trigger_enabled": True, "trigger_user": user}


def same_task(read, expected):
    """The install read-back check (R4, I2). Task Scheduler may export a principal or trigger user as the user's SID,
    or in another case; those two fields accept either form of the current user. Everything else is exact."""
    user = expected["user"]
    sid = winapi.current_user_sid()
    ok_user = lambda v: isinstance(v, str) and (v.lower() == user.lower() or (bool(sid) and v.upper() == sid.upper()))
    if not (ok_user(read.get("user")) and ok_user(read.get("trigger_user"))):
        return False
    skip = ("user", "trigger_user")
    return ({k: v for k, v in read.items() if k not in skip} == {k: v for k, v in expected.items() if k not in skip})


def install(ask=None, confirm_wait=None):
    from . import schedule
    from .schedulers import windows
    tool = schedule.tool_python()
    if not tool or (schedule.installed_version(tool) or (0,)) < MIN_TOOL:
        print("Install or update the tool with screen capture first: "
              "uv tool install --force \"inkvault[screen]\"")
        return 1
    pythonw = tool.with_name("pythonw.exe")
    argv = [str(pythonw), "-m", "inkvault", "--home", str(paths.home()), "watch"]
    try:
        schedule.check_task_scheduler_argv(argv)
    except schedule.ScheduleError as e:
        print(e)
        return 1
    try:
        screen.prepare()
    except screen.SettingsError as e:
        print(f"Screen capture settings need attention: {e}")
        return 1
    except Exception as e:  # noqa: BLE001 — e.g. the vault can't be opened
        print(f"Couldn't set up screen capture ({type(e).__name__}); nothing new is recording.")
        return 1
    if ask is None:
        from . import browsers
        from .cli import confirm
        ask = confirm if browsers.interactive() else (lambda q: False)
    messaging = ask("Read text in messaging apps (Discord, WhatsApp, Telegram, Teams, LinkedIn)? This stores other "
                    "people's messages and names. [y/N] ")
    chrome = ask("Read text in Chrome? Chrome's window titles don't say which profile they are, so this includes "
                 "every Chrome profile on this Windows account, including other people's. [y/N] ")

    def apply_answers(d):
        for app in screen.MESSAGING_APPS:
            (add_to if messaging else remove_from)("text_apps", app)(d)
        (add_to if chrome else remove_from)("text_apps", screen.CHROME)(d)
    if change(apply_answers, "Text apps chosen.") != 0:
        return 1
    try:
        user = windows.current_user()
        windows.install_logon(argv, user)
    except (RuntimeError, OSError) as e:
        print(f"Task Scheduler refused to register the sign-in task ({e}); nothing new is recording. "
              "Run `inkvault watch --uninstall` to clear anything left over.")
        return 1
    try:
        rb = windows.read_back(windows.WATCH_TASK)
    except windows.TaskUnknown:
        rb = None
    if rb is None or not same_task(rb, expected_task(pythonw, user)):
        try:
            windows.remove(task=windows.WATCH_TASK)
            try:
                gone = windows.read_back(windows.WATCH_TASK) is None
            except windows.TaskUnknown:
                gone = False
        except (RuntimeError, OSError):
            gone = False
        if gone:
            print("The sign-in task didn't read back as expected, so it was removed; nothing new is recording.")
        else:
            print("The sign-in task didn't read back as expected and couldn't be removed or confirmed gone; "
                  "run `inkvault watch --uninstall`. Nothing new is recording.")
        return 1
    make_shortcuts(pythonw)
    if lock_held():   # R4: a watcher already running (started by hand) isn't proof the task's action works
        print(f"A watcher is already running (pid {lock_owner()}) and keeps recording. The sign-in task is set up "
              "and starts screen capture from the next sign-in.")
        return 0
    since = screen.now()
    try:
        windows.run_task(windows.WATCH_TASK)
    except (RuntimeError, OSError) as e:
        print(f"The sign-in task is registered, but couldn't be started now ({e}); it starts at the next sign-in.")
        return 1
    if not running_confirmed(STOP_WAIT if confirm_wait is None else confirm_wait, since):
        print("The sign-in task is registered, but the watcher hasn't confirmed it's running. "
              "Check `inkvault status` and watch.log.")
        return 1
    print("Screen capture is on now and at every sign-in. Pause it with the desktop shortcut or "
          "`inkvault watch --pause 1h`.")
    return 0


def make_shortcuts(pythonw):
    """Two desktop shortcuts (spec §4). A failure is reported, not fatal."""
    import subprocess
    home = str(paths.home()).replace("'", "''")
    pythonw = str(pythonw).replace("'", "''")
    script = "; ".join(
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+"
        f"'\\{name}.lnk'); $s.TargetPath='{pythonw}'; $s.Arguments='-m inkvault --home \"{home}\" watch {args}'; "
        "$s.Save()" for name, args in (("Pause screen capture (1 hour)", "--pause 1h"),
                                       ("Resume screen capture", "--resume")))
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True)
        failed = bool(r.returncode)
    except OSError:
        failed = True
    if failed:
        print("Couldn't add the desktop shortcuts; use `inkvault watch --pause 1h` and `--resume`.")


def task_home(rb):
    args = rb["arguments"] if rb else []
    return args[args.index("--home") + 1] if "--home" in args and args.index("--home") + 1 < len(args) else None


def uninstall():
    """Spec §7 (R9, P5, R2, R4): needs no vault and no settings. Known state only; matching home; disable and
    verify; cooperative stop; delete and verify. Never force-ends; a stop that times out leaves the task disabled."""
    from .schedulers import windows
    try:
        rb = windows.read_back(windows.WATCH_TASK)
        if rb is not None:
            home = task_home(rb)
            if home is None:
                print("Can't tell which vault the InkVault Watch task belongs to; nothing was changed.")
                return 1
            if os.path.normcase(home) != os.path.normcase(str(paths.home())):
                print(f"The InkVault Watch task belongs to another vault ({home}); nothing was changed.")
                return 1
            windows.set_enabled(False, task=windows.WATCH_TASK)
            after = windows.read_back(windows.WATCH_TASK)
            if after is None or after["enabled"]:
                print("The sign-in task couldn't be confirmed disabled; nothing else was changed.")
                return 1
        if not stop_watcher():
            task_state = "The sign-in task is disabled; " if rb is not None else ""
            print("Screen capture may still be running: the watcher didn't stop within 15 seconds. "
                  + task_state + "Run `inkvault watch --uninstall` again.")
            return 1
        if rb is not None:
            windows.remove(task=windows.WATCH_TASK)
            if windows.read_back(windows.WATCH_TASK) is not None:
                print("Screen capture is stopped, but the sign-in task couldn't be removed.")
                return 1
    except windows.TaskUnknown as e:
        print(f"Task Scheduler couldn't say what's there ({e}); nothing more was changed.")
        return 1
    except RuntimeError as e:
        print(f"Task Scheduler refused ({e}); nothing more was changed.")
        return 1
    print("Screen capture is off and won't start at sign-in.")
    return 0


def cmd(args):
    # Controls first, before any vault or settings work: they need only the home, the lock and the task (R2).
    if args.stop:
        if not lock_held():
            print("Screen capture isn't running.")
            return 0
        ok = stop_watcher()
        print("Stopped." if ok else "Screen capture may still be running: it didn't stop within 15 seconds.")
        return 0 if ok else 1
    if args.uninstall:
        if sys.platform != "win32":
            print("screen capture is Windows-only for now")
            return 1
        return uninstall()
    # Settings edits touch screen.json only.
    if args.add_app:
        if args.add_app.lower() == screen.CHROME:
            print("Note: Chrome's window titles don't say which profile they are, so this reads text from every "
                  "Chrome profile on this Windows account.")
        return change(add_to("text_apps", args.add_app), f"{args.add_app} gets text capture.")
    if args.remove_app:
        return change(remove_from("text_apps", args.remove_app), f"{args.remove_app} is timeline-only.")
    if args.never_app:
        return change(add_to("never_apps", args.never_app), f"{args.never_app} is never captured.")
    if args.never_title:
        return change(add_to("never_titles", args.never_title), "Windows with that title text are skipped.")
    if args.pause:
        try:
            until = screen.parse_pause(args.pause)
        except ValueError as e:
            notify(str(e))
            return 1
        return change(lambda d: d.update(paused_until=screen.iso(until)),
                      f"Paused until {until.astimezone():%Y-%m-%d %H:%M}.", say=notify)
    if args.resume:
        return change(lambda d: d.update(paused_until=None), "Resumed.", say=notify)
    if args.reset_settings:
        try:
            with screen.settings_lock():
                screen.write_atomic(paths.screen_file(), screen.defaults())
        except screen.SettingsBusy:
            print("Nothing changed: another command is changing screen capture settings. Try again.")
            return 1
        except OSError as e:
            print(f"Nothing changed: {type(e).__name__}")
            return 1
        try:
            screen.prepare()
        except Exception as e:  # noqa: BLE001 — the file is written; the marker can wait for the next start
            print(f"(The vault couldn't be opened to record the setup: {type(e).__name__}.)")
        print("Screen capture settings reset to the defaults (messaging apps and Chrome are timeline-only).")
        return 0
    if args.apps:
        # --apps and --install prepare the home (a first use, or lost settings, which prepare refuses). The run path
        # doesn't: run() prepares with a catch-all, fails closed and retries, so a broken home never ends it early.
        try:
            screen.prepare()
        except screen.SettingsError as e:
            print(f"Screen capture settings need attention: {e}")
            return 1
        show_apps()
        return 0
    if sys.platform != "win32":
        print("screen capture is Windows-only for now")
        return 1
    if args.install:
        return install()
    return run()


def status_lines():
    out = []
    held = lock_held()
    hb = heartbeat()
    line = None
    if isinstance(hb, dict) and held:
        try:
            age = int((screen.now() - screen.parse(hb["utc"])).total_seconds())
            open_ = hb.get("open")
            state = hb.get("state") or ("paused" if hb.get("paused") else
                                        ("on, in " + open_["app"] if open_ else "on"))
            line = f"screen capture: {state} (heartbeat {age} s ago, pid {hb.get('pid')})"
        except (TypeError, KeyError, ValueError, AttributeError):
            line = None
    if line:
        out.append(line)
        for key, label in (("settings_error", "settings"), ("db_error", "database"), ("heartbeat_error", "heartbeat")):
            if hb.get(key):
                out.append(f"  {label}: {hb[key]}")
        if isinstance(hb.get("cooling"), list):
            out.append("  text paused after a slow read: " + ", ".join(str(x) for x in hb["cooling"]))
        if hb.get("job_error"):
            out.append(f"  {hb['job_error']}")
        if hb.get("dropped_text") or hb.get("busy_gaps"):
            out.append(f"  dropped text: {hb.get('dropped_text', 0)}, busy gaps: {hb.get('busy_gaps', 0)} polls")
    elif held:
        out.append(f"screen capture: running (pid {lock_owner()} from watch.lock), heartbeat unreadable")
    else:
        out.append("screen capture: not running")
    if sys.platform == "win32":
        from .schedulers import windows
        try:
            rb = windows.read_back(windows.WATCH_TASK)
            task = ("enabled" if rb["enabled"] and rb["trigger_enabled"] else "disabled") if rb else "not set up"
        except windows.TaskUnknown:
            task = "unknown (Task Scheduler couldn't be read)"
        out.append(f"  sign-in task: {task}")
        from . import screentext
        if not screentext.ocr_available():
            out.append("  text capture unavailable: install inkvault[screen] and an OCR language")
    if paths.vault_db().exists():
        db = paths.connect_ro(paths.vault_db())
        try:
            today = screen.iso(screen.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0))
            n, t = db.execute("SELECT COUNT(*), SUM(started >= ?) FROM screen_stretches", (today,)).fetchone()
            texts = db.execute("SELECT COUNT(*) FROM screen_text").fetchone()[0]
            out.append(f"  {n:,} windows recorded ({t or 0:,} today), {texts:,} text reads")
        except Exception:  # noqa: BLE001 — a vault no 0.3.0 command has opened
            pass
        finally:
            db.close()
    return out
