"""Screen capture's shared pieces (screen spec §3, §6): the time format, the one overlap rule, bounds for the MCP
tools (UTC convention) and for `forget` (local input), the settings in screen.json, and the vault markers the
watcher and forget agree on. Windows-only at run time, importable everywhere."""
import contextlib
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from . import paths

FMT = "%Y-%m-%dT%H:%M:%S.%fZ"
LOCAL_TZ = None  # None: this computer's local time. Tests set a ZoneInfo.


def iso(dt):
    return dt.astimezone(timezone.utc).strftime(FMT)


def now():
    return datetime.now(timezone.utc)


def parse(ts):
    t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t.astimezone(timezone.utc)


def overlaps(started, ended, since, until):
    """The one overlap rule (spec §6): ended is the last observation, inclusive. FMT strings compare as times."""
    return started < until and ended >= since


def overlap_sql(started="started", ended="ended"):
    """overlaps() as SQL. Parameters: [until, since]."""
    return f" AND {started} < ? AND {ended} >= ?"


def _checked(s, u, since, until):
    if s >= u:
        raise ValueError(f"the range is empty or reversed ({since} → {until or 'now'})")
    return iso(s), iso(u)


def bounds(since, until, now_=None):
    """MCP tools (spec §6): a date-only bound is that UTC day (an until date includes its whole day), a time
    without a zone is UTC, omitted until is now. Raises ValueError for anything unparseable, empty or reversed."""
    def one(text, end):
        if len(text) == 10:
            day = datetime.fromisoformat(text).replace(tzinfo=timezone.utc)
            return day + timedelta(days=1) if end else day
        return parse(text)
    s = one(since, False)
    u = one(until, True) if until else (now_ or now())
    return _checked(s, u, since, until)


def _to_utc(naive, fold, tz):
    if tz is None:
        return naive.replace(fold=fold).astimezone().astimezone(timezone.utc)
    return naive.replace(fold=fold, tzinfo=tz).astimezone(timezone.utc)


def _wall(utc, tz):
    return (utc.astimezone() if tz is None else utc.astimezone(tz)).replace(tzinfo=None, fold=0)


def from_local(naive, later, tz=None):
    """A local wall time -> UTC. The repeated hour widens (earliest for since, latest for until); a skipped one is
    an error (spec §6, P4)."""
    a, b = _to_utc(naive, 0, tz), _to_utc(naive, 1, tz)
    if _wall(a, tz) != naive and _wall(b, tz) != naive:
        raise ValueError(f"{naive:%Y-%m-%d %H:%M} doesn't exist here (the clocks went forward); "
                         "pick a time outside that hour")
    return max(a, b) if later else min(a, b)


def local_bounds(since, until, tz=None, now_=None):
    """`inkvault forget --screen` (spec §6, P4): no zone or a date means local wall time (a date is local
    midnight; an until date includes its whole day); an explicit offset or Z is respected."""
    tz = tz if tz is not None else LOCAL_TZ

    def one(text, end):
        if len(text) == 10:
            day = datetime.fromisoformat(text)
            return from_local(day + timedelta(days=1) if end else day, end, tz)
        t = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return t.astimezone(timezone.utc) if t.tzinfo else from_local(t, end, tz)
    s = one(since, False)
    u = one(until, True) if until else (now_ or now())
    return _checked(s, u, since, until)

DEFAULT_TEXT_APPS = (
    "comet.exe", "msedge.exe",
    "claude.exe", "ChatGPT.exe", "Perplexity.exe", "Codex.exe",
    "WindowsTerminal.exe", "Code.exe", "Cursor.exe",
    "Notion.exe", "Obsidian.exe", "Notepad.exe",
    "WINWORD.EXE", "EXCEL.EXE", "POWERPNT.EXE", "OUTLOOK.EXE", "olk.exe", "ONENOTE.EXE",
)
# Asked at install (spec decisions 6-7): text from these stores other people's messages, or can't tell whose
# Chrome profile a window belongs to. Off without a terminal.
MESSAGING_APPS = ("Discord.exe", "WhatsApp.Root.exe", "Telegram.exe", "ms-teams.exe", "LinkedIn.exe")
CHROME = "chrome.exe"
DEFAULT_NEVER_APPS = ("1Password.exe", "Bitwarden.exe", "KeePassXC.exe", "KeePass.exe", "CredentialUIBroker.exe",
                      "LockApp.exe", "consent.exe")
DEFAULT_NEVER_TITLES = ("InPrivate", "Incognito", "Private Browsing")
LOCK_WAIT = 5.0
REPLACE_TRIES, REPLACE_WAIT = 5, 0.06      # about 250 ms in all


class SettingsError(Exception):
    """screen.json is missing, unreadable or invalid: the watcher records nothing until it's fixed."""


class SettingsBusy(Exception):
    """Another command is changing screen.json right now."""


@dataclass(frozen=True)
class Settings:
    text_apps: frozenset
    never_apps: frozenset
    never_titles: tuple
    paused_until: object  # datetime | None

    def paused(self, utc):
        return self.paused_until is not None and utc < self.paused_until

    def never(self, exe, title):
        t = (title or "").lower()
        return exe.lower() in self.never_apps or any(n in t for n in self.never_titles)

    def text_app(self, exe):
        return exe.lower() in self.text_apps and exe.lower() not in self.never_apps


def defaults():
    return {"text_apps": list(DEFAULT_TEXT_APPS), "never_apps": list(DEFAULT_NEVER_APPS),
            "never_titles": list(DEFAULT_NEVER_TITLES), "paused_until": None}


def validate(data):
    """Every field's type is checked before use; the only exception is SettingsError, naming the field only."""
    if not isinstance(data, dict):
        raise SettingsError("screen.json must hold an object")

    def names(key):
        v = data.get(key)
        if not isinstance(v, list) or not all(isinstance(x, str) and x.strip() for x in v):
            raise SettingsError(f"{key} must be a list of names")
        return [x.strip().lower() for x in v]
    text, never, titles = names("text_apps"), names("never_apps"), names("never_titles")
    paused = data.get("paused_until")
    if paused is not None:
        if not isinstance(paused, str):
            raise SettingsError("paused_until must be a time or null")
        try:
            paused = parse(paused)
        except Exception:  # noqa: BLE001 — any parse failure is a settings error, never a crash
            raise SettingsError("paused_until must be a time or null") from None
    return Settings(frozenset(text), frozenset(never), tuple(titles), paused)


def read_file():
    try:
        return json.loads(paths.screen_file().read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        raise SettingsError("screen.json is missing: run `inkvault watch --reset-settings`") from None
    except json.JSONDecodeError as e:
        raise SettingsError(f"screen.json isn't valid JSON (line {e.lineno})") from None
    except (OSError, UnicodeDecodeError) as e:
        raise SettingsError(f"screen.json can't be read ({type(e).__name__})") from None


def write_atomic(path, data):
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        for attempt in range(REPLACE_TRIES):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:       # a reader (the watcher, an antivirus scan) has it open for a moment
                if attempt == REPLACE_TRIES - 1:
                    raise
                time.sleep(REPLACE_WAIT)
    finally:
        tmp.unlink(missing_ok=True)


@contextlib.contextmanager
def settings_lock(wait=LOCK_WAIT):
    """screen.lock: held only for one read-validate-write (R1). Never the nightly lock; the watcher never takes it."""
    from . import nightly
    fd = os.open(paths.screen_lock(), os.O_RDWR | os.O_CREAT)
    try:
        deadline = time.monotonic() + wait
        while not nightly.try_lock(fd):
            if time.monotonic() >= deadline:
                raise SettingsBusy("another command is changing screen capture settings")
            time.sleep(0.1)
        try:
            yield
        finally:
            nightly.unlock(fd)
    finally:
        os.close(fd)


LIST_KEYS = ("text_apps", "never_apps", "never_titles")


def edit(change):
    with settings_lock():
        data = read_file()
        if not isinstance(data, dict) or not all(isinstance(data.get(k), list) for k in LIST_KEYS):
            raise SettingsError("screen.json doesn't have the expected shape: run `inkvault watch --reset-settings`")
        change(data)
        settings = validate(data)
        write_atomic(paths.screen_file(), data)
        return settings


class SettingsSource:
    """What the watcher reads each poll: reload on an mtime change, and every poll while the last load failed (a
    permissions fix may not change mtime). generation goes up with each successful reload."""

    def __init__(self):
        self.generation, self._mtime, self._settings = 0, None, None

    def poll(self):
        try:
            mtime = paths.screen_file().stat().st_mtime_ns
        except OSError:
            self._settings = self._mtime = None
            return None, "screen.json is missing"
        if self._settings is not None and mtime == self._mtime:
            return self._settings, None
        try:
            settings = validate(read_file())
        except SettingsError as e:
            self._settings = self._mtime = None
            return None, str(e)
        self._settings, self._mtime = settings, mtime
        self.generation += 1
        return settings, None


def meta_get(vault, key):
    r = vault.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return r[0] if r else None


def initialized(vault):
    return meta_get(vault, "screen_initialized") is not None


def ensure_initialized(vault):
    """Spec §3 (R8, P5): defaults only on a true first run, judged by a marker nothing ever clears."""
    f = paths.screen_file()
    if initialized(vault):
        if not f.exists():
            raise SettingsError("screen.json is missing: run `inkvault watch --reset-settings` or restore it")
        return
    if not f.exists():
        with settings_lock():
            if not f.exists():
                write_atomic(f, defaults())
    vault.execute("INSERT OR IGNORE INTO meta VALUES ('screen_initialized', ?)", (iso(now()),))
    vault.commit()


def prepare():
    """Every `watch` command starts here: the vault exists and settings are initialized. Never starts capture."""
    from . import export
    vault = export.open_vault()
    try:
        ensure_initialized(vault)
    finally:
        vault.close()


def removal_gen(vault):
    return int(meta_get(vault, "screen_removal_gen") or 0)


def parse_pause(text, now_=None):
    t = now_ or now()
    if text == "until-tomorrow":
        local = t.astimezone()
        return (local.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)).astimezone(timezone.utc)
    if len(text) > 1 and text[:-1].isdigit() and text[-1] in "mh" and int(text[:-1]) > 0:
        return t + timedelta(minutes=int(text[:-1]) * (60 if text[-1] == "h" else 1))
    raise ValueError(f"{text!r}: use 30m, 1h or until-tomorrow")
