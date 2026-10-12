# Screen capture (`inkvault watch`): Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship InkVault v0.3.0 with screen capture, built on the public v0.2.1. It records a timeline of every
foreground window and OCR text from chosen apps into `vault.db`. `timeline()` shows it live, search covers it after
the nightly index, and `inkvault forget --screen` removes it. Windows only.

**Architecture:**
- **The loop.** `inkvault watch` is one long-running process. A pure core (`stretches.py`) turns 2-second samples
  into stretch actions.
- **Writes.** A per-stretch queue (`screenstore.py`) writes them in short `BEGIN IMMEDIATE` transactions, each
  checked against a removal generation.
- **Capture.** Capture and OCR run in a reusable child process (`screentext.py`) behind a non-blocking supervisor
  (`worker.py`). Every native call goes through typed ctypes bindings (`winapi.py`).
- **Settings** live in `screen.json`, with their own short lock (`screen.py`).
- **Forgetting** records exact target ids.
- **Search** copies stretches into `search.db`, and `timeline()` reads them live.

**Tech Stack:** Python 3.10+, stdlib `sqlite3` and `ctypes`, the Windows-only optional extra `inkvault[screen]`
(`winrt-Windows.Media.Ocr`, `-Graphics.Imaging`, `-Storage.Streams`, `-Foundation`), pytest, and `tzdata` (dev only,
for the daylight-saving tests). Windows Task Scheduler runs the sign-in task.

**Spec:** `docs/superpowers/specs/2026-10-12-screen-capture-design.md` (revision 5). Read it all first; §1–§8 below
refer to it. **[P#]** marks fixes from the plan review.

## Global Constraints

- **Base:** branch `screen-capture` from the tag `v0.2.1`, not from `main`. The v0.2.1 suite is **368 passed, 1
  skipped**. Every task keeps it green.
- **Public code and docs.** Everything on this branch will be published. No personal names, account names, local
  paths, contact handles or details about who shares the computer go in code, tests, docs or commit messages. Use
  `C:\Users\me\…` and `Discord.exe`-style placeholders.
- **Windows only, importable everywhere.** New modules import on macOS/Linux. `ctypes.WinDLL` and `winrt` are touched
  only inside functions, behind `sys.platform == "win32"`. On other platforms `inkvault watch` prints `screen capture
  is Windows-only for now` and returns 1.
- **Python floor 3.10.** No backslashes or nested quotes inside f-string expressions (3.10/3.11 reject them). No
  new required dependency. winrt goes only in the `screen` extra (`sys_platform == 'win32'`). `tzdata` goes only in
  the dev group.
- **Privacy:**
  - no images on disk, ever;
  - never print or log window titles, screen text or `screen.json` contents (logs, the heartbeat and `status`
    carry app names, counts, times and error class names);
  - never read browser profiles other than the ones a test creates.
- **Native calls** go only through `winapi.api()`'s typed bindings: pointer-sized handles, `BOOL`/`DWORD` results,
  `use_last_error=True`. Never through `ctypes.windll.*` directly.
- **Stored times:** UTC ISO with microseconds and `Z`, via `screen.iso()`.
- **Locks:**
  - the watcher never takes the nightly lock;
  - `screen.json` changes only under `screen.lock`;
  - lock order: a holder of the nightly lock may take `screen.lock`, never the reverse.
- **Vault tables** are created in `export.open_vault()` with `CREATE TABLE IF NOT EXISTS`, like every v0.2.1 table.
  There's no migration module on this base.
- **Tests never touch the real system:**
  - `INKVAULT_HOME` under `tmp_path`;
  - no real `schtasks`, `powershell` or `uv` (conftest guards both `subprocess.run` and `subprocess.Popen`, Task 0);
  - native tests only read windows the test itself creates (tkinter).
- **Never run against this PC during the build:** no `uv tool install`, `inkvault watch --install` or `schtasks`.
  Publishing (Task 20) needs the maintainer's explicit OK at that moment.
- **Defaults** (spec §1–§5):
  - timing: poll 2 s; flick = fewer than 2 polls; dwell 30 s; per-window re-read 120 s; idle 300 s; poll gap 30 s;
    extend every 30 s;
  - worker: deadline 5 s; cooldown 600 s; request ≤ 3,072 bytes; response ≤ 1,000,000 bytes;
  - storage: busy timeout 250 ms; flush budget 20 writes or 250 ms per poll; queue caps 50 stretches / 2,000,000
    text bytes;
  - text under 20 characters is dropped; stop and confirm waits are 15 s; vault reopen retry every 10 s; timeline
    `limit` 1–500; stretch reads return the newest 20 text rows.
- Every commit message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Don't push. Task 20
  publishes, and only with the maintainer's OK.

## Review Focus

1. **Elevated (administrator) apps.** `OpenProcess` fails for them, so identity can't be read. Expected: the window
   counts as *no window*, the timeline shows a gap, and nothing crashes or retries in a loop. Pinned in Task 7.
2. **Remote Desktop, fast user switching, the secure desktop.** `OpenInputDesktop` fails or isn't `Default`.
   Expected: treated as locked, nothing recorded, no crash. Pinned in Task 7.
3. **`screen.json` saved by Notepad with a UTF-8 BOM.** Expected: it loads (read as `utf-8-sig`) instead of failing
   closed for a reason the user can't see. Pinned in Task 3.
4. **A home path with spaces or non-ASCII characters** (`C:\Users\José Ñ\AppData\Local\InkVault`). Expected: the
   task's action quotes it, read-back matches it exactly, and `%NAME%` is still refused. Pinned in Task 16.
5. **A window closing mid-capture** (the `hwnd` destroyed between the check and `PrintWindow`). Expected: the
   capture returns nothing, the worker stays healthy, and nothing is stored. Pinned in Task 9.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/inkvault/screen.py` (new) | Time format, the overlap rule, MCP bounds, local forget bounds; `Settings`, `screen.json` (validate, atomic write, `screen.lock`, `SettingsSource`, `prepare`); vault markers |
| `src/inkvault/redact.py` (new) | Secret redaction; `clean_title`, `clean_text` |
| `src/inkvault/stretches.py` (new) | Pure core: `Identity`, `Sample`, actions, `Core` |
| `src/inkvault/screenstore.py` (new) | `Write`, guarded `apply()`, the per-stretch `Queue` |
| `src/inkvault/winapi.py` (new) | Typed Win32 bindings; identity, cloak, desktop, idle, job objects, pipe peek |
| `src/inkvault/worker.py` (new) | Validated length-prefixed protocol; non-blocking `Supervisor` |
| `src/inkvault/screentext.py` (new) | Worker side: checks, GDI capture, scaling, OCR, `worker_main()` |
| `src/inkvault/watch.py` (new) | `Watcher` (tick, accept, flush, heartbeat, stop), `run()`, the `watch` command, install/uninstall, status lines |
| `src/inkvault/screentime.py` (new) | Duration split by local hour (daylight-saving safe), shared by the Atlas and digests |
| `src/inkvault/export.py` | `open_vault(timeout=5.0)`; screen tables |
| `src/inkvault/paths.py` | `screen_file`, `screen_lock`, `watch_lock`, `watch_stop`, `watch_heartbeat`, `watch_log` |
| `src/inkvault/forget.py` | `screen-app` / `screen-range` with recorded targets |
| `src/inkvault/index.py` | Read snapshot; `events.source`/`stretch_id`; `screen_stretches` + `stretches_fts`; `screen_cutoff` |
| `src/inkvault/server.py` | `screen` source, Screen timeline section, live `get_memory`, removal generation in the guard |
| `src/inkvault/dashboard.py`, `atlas.html`, `digest.py` | Pieces-only counts; screen time by duration |
| `src/inkvault/schedulers/windows.py` | `task=` parameters, `set_enabled`, `render_logon`, `read_back`, `run_task` |
| `src/inkvault/cli.py` | `watch` and `forget` subcommands; status lines |
| `tests/conftest.py` | `Popen` guard |
| `tests/fakeworker.py` (new) | A synthetic capture worker for supervisor and integration tests |
| `pyproject.toml`, `README.md`, `CHANGELOG.md`, `src/inkvault/__init__.py` | Extra, dev deps, docs, 0.3.0 |

---

### Task 0: The branch, the docs, and a `Popen` guard

**Files:**
- Modify: `tests/conftest.py`
- Test: `tests/test_guards.py` (new)

- [ ] **Step 1: Make the branch and bring the spec and plan**

```bash
git checkout -b screen-capture v0.2.1
git checkout main -- docs/superpowers/specs/2026-10-12-screen-capture-design.md docs/superpowers/plans/2026-10-12-screen-capture.md
uv run pytest -q
```

Expected: `368 passed, 1 skipped`.

- [ ] **Step 2: Write the failing test** (`tests/test_guards.py`)

```python
"""Tests can start this Python and nothing else, through run *or* Popen [P12]."""
import subprocess
import sys

import pytest


def test_popen_of_another_program_fails_loudly():
    # a program that doesn't exist: before the guard this fails with FileNotFoundError, so the red phase never
    # launches anything real (R8)
    with pytest.raises(AssertionError, match="real program"):
        subprocess.Popen(["inkvault-no-such-program-xyz", "/Query"])


def test_popen_of_this_python_works():
    p = subprocess.Popen([sys.executable, "-c", "print('ok')"], stdout=subprocess.PIPE, text=True)
    assert p.communicate(timeout=30)[0].strip() == "ok"
```

- [ ] **Step 3: Run to see it fail**

Run: `uv run pytest tests/test_guards.py -q`
Expected: FAIL (`FileNotFoundError` instead of `AssertionError`).

- [ ] **Step 4: Implement** (append to `tests/conftest.py`)

```python
REAL_POPEN = subprocess.Popen


@pytest.fixture(autouse=True)
def only_python_popen(monkeypatch):
    """Popen too: a test may start this Python (fake workers, lock holders), never schtasks, powershell or uv."""
    class Guarded(REAL_POPEN):
        def __init__(self, args, *a, **k):
            program = args[0] if isinstance(args, (list, tuple)) and args else args
            if not (isinstance(program, (str, os.PathLike))
                    and os.path.normcase(os.fspath(program)) == os.path.normcase(sys.executable)):
                raise AssertionError(f"a test started a real program: {args}")
            super().__init__(args, *a, **k)
    monkeypatch.setattr(subprocess, "Popen", Guarded)
```

- [ ] **Step 5: Run the whole suite, then commit**

Run: `uv run pytest -q`
Expected: `370 passed, 1 skipped`.

```bash
git add tests/conftest.py tests/test_guards.py docs/superpowers/specs/2026-10-12-screen-capture-design.md docs/superpowers/plans/2026-10-12-screen-capture.md
git commit -m "Screen capture: spec and plan; tests may only start this Python, through run or Popen

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 1: Paths, vault tables, time helpers and bounds

**Files:**
- Modify: `src/inkvault/paths.py` (after `rebuild_marker()`), `src/inkvault/export.py` (`open_vault`), `pyproject.toml`
  (`tzdata` in the dev group)
- Create: `src/inkvault/screen.py` (time section)
- Test: `tests/test_screen_time.py`

**Interfaces:**
- Produces:
  - `paths.screen_file()`, `screen_lock()`, `watch_lock()`, `watch_stop()`, `watch_heartbeat()`, `watch_log()`
  - `export.open_vault(timeout=5.0)`, which creates `screen_stretches`, `screen_text`, `screen_removal_targets`
    and their indexes
  - `screen.iso(dt) -> str`, `screen.now() -> datetime`, `screen.parse(ts) -> datetime` (aware UTC)
  - `screen.overlaps(started, ended, since, until) -> bool`; `screen.overlap_sql(started="started",
    ended="ended") -> str` (parameters `[until, since]`)
  - `screen.bounds(since, until, now_=None) -> (str, str)` for the MCP convention; raises `ValueError` unless
    `since < until`
  - `screen.local_bounds(since, until, tz=None, now_=None) -> (str, str)` for forget's local input; raises
    `ValueError`. `screen.LOCAL_TZ = None` (tests set a `ZoneInfo`)

- [ ] **Step 1: Add `tzdata` to the dev group, then write the failing tests**

In `pyproject.toml`'s `[dependency-groups] dev`, add `"tzdata>=2024.1"`, then run `uv sync`.

`tests/test_screen_time.py`:

```python
"""Time helpers (spec §6): one format, one overlap rule, MCP bounds (UTC convention), forget bounds (local input)."""
from datetime import datetime, timezone

import pytest
from zoneinfo import ZoneInfo

CHI = ZoneInfo("America/Chicago")  # 2026: clocks go forward Mar 8 02:00, back Nov 1 02:00
S = lambda h: f"2026-10-12T{h:02d}:00:00.000000Z"


def test_iso_round_trip():
    from inkvault import screen
    t = datetime(2026, 10, 12, 9, 3, 7, 120000, tzinfo=timezone.utc)
    assert screen.iso(t) == "2026-10-12T09:03:07.120000Z" and screen.parse(screen.iso(t)) == t


def test_overlap_rule_and_sql_agree():
    import sqlite3
    from inkvault import screen
    rows = [(1, S(0), S(5)), (2, S(5), S(5)), (3, S(5), S(6)), (4, S(10), S(11)), (5, S(0), S(4))]
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE t (id, started, ended)")
    db.executemany("INSERT INTO t VALUES (?,?,?)", rows)
    got = [r[0] for r in db.execute("SELECT id FROM t WHERE 1=1" + screen.overlap_sql() + " ORDER BY id",
                                    [S(10), S(5)])]
    assert got == [i for i, a, b in rows if screen.overlaps(a, b, S(5), S(10))] == [1, 2, 3]


def test_mcp_bounds_utc_convention_and_nonempty():
    from inkvault import screen
    now = datetime(2026, 10, 12, 15, 0, tzinfo=timezone.utc)
    assert screen.bounds("2026-10-11", "2026-10-11", now) == ("2026-10-11T00:00:00.000000Z",
                                                                "2026-10-12T00:00:00.000000Z")
    assert screen.bounds("2026-10-12T14:30", "", now) == ("2026-10-12T14:30:00.000000Z", screen.iso(now))
    assert screen.bounds("2026-10-12T09:30-05:00", "", now)[0] == "2026-10-12T14:30:00.000000Z"
    for since, until in (("2026-10-12", "2026-10-11"),            # reversed dates [P9]
                         ("2026-10-12T10:00", "2026-10-12T10:00"),  # empty
                         ("yesterday", ""), ("2026-13-01", "")):
        with pytest.raises(ValueError):
            screen.bounds(since, until, now)


def test_local_bounds_naive_is_local_and_offsets_are_respected():
    from inkvault import screen
    local = screen.local_bounds("2026-10-12T10:00", "2026-10-12T10:30", tz=CHI)
    explicit = screen.local_bounds("2026-10-12T10:00-05:00", "2026-10-12T15:30Z", tz=CHI)
    assert local == explicit == ("2026-10-12T15:00:00.000000Z", "2026-10-12T15:30:00.000000Z")
    assert screen.local_bounds("2026-10-12", "2026-10-12", tz=CHI) == ("2026-10-12T05:00:00.000000Z",
                                                                        "2026-10-13T05:00:00.000000Z")


def test_local_bounds_daylight_saving():
    from inkvault import screen
    # the repeated hour widens: earliest instant for since, latest for until
    assert screen.local_bounds("2026-11-01T01:30", "2026-11-01T01:30", tz=CHI) == (
        "2026-11-01T06:30:00.000000Z", "2026-11-01T07:30:00.000000Z")
    with pytest.raises(ValueError, match="doesn't exist"):          # the skipped hour
        screen.local_bounds("2026-03-08T02:30", "2026-03-08T04:00", tz=CHI)


def test_vault_has_the_screen_tables(tmp_path, monkeypatch):
    monkeypatch.setenv("INKVAULT_HOME", str(tmp_path))
    from inkvault import export
    db = export.open_vault()
    try:
        names = {r[0] for r in db.execute("SELECT name FROM sqlite_master")}
        assert {"screen_stretches", "screen_text", "screen_removal_targets", "screen_stretches_started",
                "screen_stretches_ended", "screen_stretches_app", "screen_text_stretch"} <= names
    finally:
        db.close()
```

- [ ] **Step 2: Run to see them fail**

Run: `uv run pytest tests/test_screen_time.py -q`
Expected: FAIL (`No module named 'inkvault.screen'`).

- [ ] **Step 3: Implement**

`src/inkvault/paths.py`, after `rebuild_marker()`:

```python
def screen_file() -> Path:
    """Screen capture settings: text apps, never rules, pause (`inkvault watch --apps`)."""
    return home() / "screen.json"


def screen_lock() -> Path:
    """Held briefly while screen.json is read, changed and saved (never by the watcher)."""
    return home() / "screen.lock"


def watch_lock() -> Path:
    """Held by the running watcher for its whole life; holds its process id."""
    return home() / "watch.lock"


def watch_stop() -> Path:
    """Asks the running watcher to stop (`inkvault watch --stop`, uninstall)."""
    return home() / "watch.stop"


def watch_heartbeat() -> Path:
    """The watcher's state, rewritten every 30 s: time, app in front, paused, error, counts. Never titles or text."""
    return home() / "watch.heartbeat"


def watch_log() -> Path:
    """The watcher's diagnostics: app names, counts, times and error classes; never titles, text or settings."""
    return home() / "watch.log"
```

`src/inkvault/export.py`, `open_vault`: change the signature to `def open_vault(timeout=5.0):` and the connect to
`db = sqlite3.connect(paths.vault_db(), timeout=timeout)`. Then append inside its `executescript`, after
`browser_removals`:

```sql
        -- Screen capture (screen spec §5): stretches of a foreground window, OCR text read from them, and the
        -- exact stretches each recorded screen removal deletes. Times are UTC ISO with microseconds and "Z".
        CREATE TABLE IF NOT EXISTS screen_stretches (
            id TEXT PRIMARY KEY, started TEXT NOT NULL, ended TEXT NOT NULL, app TEXT NOT NULL, title TEXT
        );
        CREATE TABLE IF NOT EXISTS screen_text (
            id TEXT PRIMARY KEY, stretch_id TEXT NOT NULL, created TEXT NOT NULL, text TEXT NOT NULL,
            text_hash TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS screen_removal_targets (
            removal_id INTEGER NOT NULL, stretch_id TEXT NOT NULL, PRIMARY KEY (removal_id, stretch_id)
        );
        CREATE INDEX IF NOT EXISTS screen_stretches_started ON screen_stretches(started);
        CREATE INDEX IF NOT EXISTS screen_stretches_ended ON screen_stretches(ended);
        CREATE INDEX IF NOT EXISTS screen_stretches_app ON screen_stretches(app);
        CREATE INDEX IF NOT EXISTS screen_text_stretch ON screen_text(stretch_id);
```

`src/inkvault/screen.py`:

```python
"""Screen capture's shared pieces (screen spec §3, §6): the time format, the one overlap rule, bounds for the MCP
tools (UTC convention) and for `forget` (local input), the settings in screen.json, and the vault markers the
watcher and forget agree on. Windows-only at run time, importable everywhere."""
from datetime import datetime, timedelta, timezone

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
```

- [ ] **Step 4: Run to see them pass, then the whole suite**

Run: `uv run pytest tests/test_screen_time.py -q`, then `uv run pytest -q`
Expected: PASS; the suite stays green.

- [ ] **Step 5: Commit**

```bash
git add src/inkvault/paths.py src/inkvault/export.py src/inkvault/screen.py tests/test_screen_time.py pyproject.toml uv.lock
git commit -m "Screen capture: vault tables, paths, the overlap rule, MCP and local bounds

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Redaction (`redact.py`)

**Files:**
- Create: `src/inkvault/redact.py`
- Test: `tests/test_redact.py`

**Interfaces:**
- Consumes: `urlclean.clean_title`, `urlclean.JWT` (both in v0.2.1)
- Produces: `redact.MASK = "[redacted]"`, `redact.redact(text) -> str`, `redact.clean_title(title) -> str | None`,
  `redact.clean_text(lines: list[str]) -> str | None` (None under 20 characters)

- [ ] **Step 1: Write the failing tests**

```python
"""Redaction of every stored screen field (spec §4): secrets go, ordinary text stays."""
import pytest

SECRET = [
    "sk-proj-AbCdEf0123456789AbCdEf0123456789",
    "ghp_0123456789abcdefABCDEF0123456789abcd",
    "xoxb-" + "0" * 12 + "-" + "0" * 13 + "-" + "a" * 24,
    "AKIAIOSFODNN7EXAMPLE",
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1gFWFOEjXk",
    "token=9f8e7d6c5b4a39281706f5e4d3c2b1a0",
    "password: hunter2hunter2hunter2",
]
HARMLESS = [
    "Fix the retry helper with exponential backoff",
    "git commit -m 'Merge the browser-history branch'",
    "The key to this design is a durable generation",
    "4dda9763fc17c14a0b2f93d8c32a86b02fc6a746",
    "@名前 🎮 - Discord",
]


@pytest.mark.parametrize("s", SECRET)
def test_secret_shapes_are_masked(s):
    from inkvault import redact
    out = redact.redact(f"before {s} after")
    assert redact.MASK in out and out.startswith("before") and out.endswith("after")
    assert s.split("=")[-1].split(":")[-1].strip() not in out


@pytest.mark.parametrize("s", HARMLESS)
def test_ordinary_text_is_untouched(s):
    from inkvault import redact
    assert redact.redact(s) == s


def test_titles_get_url_cleaning_then_redaction_and_are_clipped():
    from inkvault import redact
    t = ("PowerShell - curl https://api.example.com/v1?access_token=abc123def456 "
         "ghp_0123456789abcdefABCDEF0123456789abcd")
    out = redact.clean_title(t)
    assert "abc123def456" not in out and "ghp_" not in out
    assert out.startswith("PowerShell - curl https://api.example.com")
    assert len(redact.clean_title("x" * 1000)) == 300 and redact.clean_title(None) is None


def test_clean_text():
    from inkvault import redact
    assert redact.clean_text(["ok", "  "]) is None
    out = redact.clean_text(["export OPENAI_API_KEY=sk-proj-AbCdEf0123456789AbCdEf0123456789", "next line here"])
    assert out.splitlines()[1] == "next line here" and "sk-proj" not in out


def test_long_unicode_titles_survive():
    from inkvault import redact
    out = redact.clean_title("@名前 🎮 " * 60 + "- Discord")
    assert len(out) == 300 and out.startswith("@名前 🎮")
```

- [ ] **Step 2: Run to see them fail**

Run: `uv run pytest tests/test_redact.py -q`
Expected: FAIL (`No module named 'inkvault.redact'`).

- [ ] **Step 3: Implement `src/inkvault/redact.py`**

```python
"""Secrets out of everything screen capture stores: window titles and OCR text alike (spec §4).

Titles first go through urlclean.clean_title (addresses lose secret values, 300-character clip), then both get the
secret shapes below. Rules match the raw title in memory; only cleaned values are stored."""
import re

from . import urlclean

MASK = "[redacted]"
MIN_TEXT = 20

SECRET_WORDS = r"(?:api[_-]?key|key|token|secret|password|passwd|pwd|auth|bearer|credential)"
SHAPES = [
    re.compile(r"\bsk-(?:proj-|live-|test-)?[A-Za-z0-9_-]{20,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(r"\bxox[abpr]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    urlclean.JWT,
]
AFTER_WORD = re.compile(rf"(?i)(\b{SECRET_WORDS}\b\s*[:=]\s*[\"']?)([A-Za-z0-9+/_.-]{{12,}})")


def redact(text):
    if not text:
        return text
    for p in SHAPES:
        text = p.sub(MASK, text)
    return AFTER_WORD.sub(lambda m: m.group(1) + MASK, text)


def clean_title(title):
    return redact(urlclean.clean_title(title)) if title else title


def clean_text(lines):
    text = "\n".join(line.rstrip() for line in lines if line and line.strip())
    return redact(text) if len(text.strip()) >= MIN_TEXT else None
```

- [ ] **Step 4: Run to see them pass**

Run: `uv run pytest tests/test_redact.py -q`
Expected: PASS. If a HARMLESS case fails, narrow the pattern. Never delete a SECRET case.

- [ ] **Step 5: Commit**

```bash
git add src/inkvault/redact.py tests/test_redact.py
git commit -m "Screen capture: redaction for stored titles and text

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Settings (`screen.json`, `screen.lock`, preparing a home)

**Files:**
- Modify: `src/inkvault/screen.py` (append)
- Test: `tests/test_screen_settings.py`

**Interfaces:**
- Consumes: `nightly.try_lock`, `nightly.unlock` (v0.2.1), `export.open_vault`, `paths`
- Produces:
  - `screen.DEFAULT_TEXT_APPS` (18), `MESSAGING_APPS` (5), `CHROME = "chrome.exe"`, `DEFAULT_NEVER_APPS`,
    `DEFAULT_NEVER_TITLES`
  - `SettingsError`, `SettingsBusy`
  - `Settings(text_apps, never_apps, never_titles, paused_until)`, with methods `.paused(utc)`, `.never(exe,
    title)`, `.text_app(exe)`
  - `screen.defaults() -> dict`; `screen.validate(data) -> Settings` (type-checks everything, raises only
    `SettingsError`)
  - `screen.write_atomic(path, data)`; `screen.settings_lock(wait=5.0)`; `screen.edit(change) -> Settings`;
    `screen.read_file() -> dict` (`utf-8-sig`)
  - `SettingsSource`, with `.poll() -> (Settings | None, str | None)` and `.generation: int`
  - `screen.initialized(vault)`, `screen.ensure_initialized(vault)`, `screen.prepare() -> None`,
    `screen.removal_gen(vault) -> int`, `screen.parse_pause(text, now_=None) -> datetime`

- [ ] **Step 1: Write the failing tests**

```python
"""screen.json (spec §3): its own short lock, typed validation, fail-closed loading, first run vs. lost file."""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

import pytest

HOLD_SCREEN = """
import os, sys
os.environ["INKVAULT_HOME"] = sys.argv[1]
from inkvault import screen
with screen.settings_lock():
    print("held", flush=True)
    sys.stdin.read()
"""


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("INKVAULT_HOME", str(tmp_path))
    return tmp_path


def test_public_defaults():
    from inkvault import screen
    s = screen.validate(screen.defaults())
    assert len(screen.DEFAULT_TEXT_APPS) == 18
    assert not s.text_app("chrome.exe") and not any(s.text_app(a) for a in screen.MESSAGING_APPS)
    assert s.text_app("Comet.EXE") and s.never("1Password.exe", "") and s.never("x.exe", "New InPrivate tab")


@pytest.mark.parametrize("bad", [
    [], {}, {"text_apps": "x"},
    {"text_apps": ["a"], "never_apps": [], "never_titles": [1], "paused_until": None},
    {"text_apps": ["a"], "never_apps": [], "never_titles": [], "paused_until": 5},          # P3: was a crash
    {"text_apps": ["a"], "never_apps": [], "never_titles": [], "paused_until": "soon"},
])
def test_invalid_settings_raise_only_settings_error(bad):
    from inkvault import screen
    with pytest.raises(screen.SettingsError) as e:
        screen.validate(bad)
    assert "soon" not in str(e.value)                                     # never echo contents


def test_edit_is_atomic_and_validated(home):
    from inkvault import paths, screen
    screen.write_atomic(paths.screen_file(), screen.defaults())
    s = screen.edit(lambda d: d["never_titles"].append("Account overview"))
    assert s.never("comet.exe", "Account overview — Bank")
    with pytest.raises(screen.SettingsError):
        screen.edit(lambda d: d.update(text_apps=None))
    assert json.loads(paths.screen_file().read_text())["text_apps"] and not list(home.glob("screen.json.*"))


def test_notepad_bom_is_accepted(home):                                    # Review Focus 3
    from inkvault import paths, screen
    paths.screen_file().write_bytes(b"\xef\xbb\xbf" + json.dumps(screen.defaults()).encode())
    s, err = screen.SettingsSource().poll()
    assert s and err is None


def test_pause_works_while_the_nightly_lock_is_held(home):                 # R1
    import conftest
    from inkvault import paths, screen
    screen.write_atomic(paths.screen_file(), screen.defaults())
    with conftest.holder(home):
        screen.edit(lambda d: d.update(paused_until=screen.iso(screen.parse_pause("1h"))))
    assert screen.validate(screen.read_file()).paused(screen.now())


def test_a_busy_screen_lock_changes_nothing(home):
    from inkvault import paths, screen
    screen.write_atomic(paths.screen_file(), screen.defaults())
    p = subprocess.Popen([sys.executable, "-c", HOLD_SCREEN, str(home)], stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, text=True)
    try:
        assert p.stdout.readline().strip() == "held"
        with pytest.raises(screen.SettingsBusy):
            with screen.settings_lock(wait=0.2):
                pass
    finally:
        p.stdin.close()
        p.wait(timeout=10)


def test_source_recovers_with_the_same_mtime(home):                         # R8, P12: no private resets
    from inkvault import paths, screen
    f = paths.screen_file()
    screen.write_atomic(f, screen.defaults())
    src = screen.SettingsSource()
    assert src.poll()[0] and src.generation == 1
    t1 = f.stat().st_mtime_ns + 10_000_000_000
    f.write_text("{broken", encoding="utf-8"); os.utime(f, ns=(t1, t1))      # a new mtime: reloaded, invalid
    s, err = src.poll()
    assert s is None and "screen.json" in err and "broken" not in err
    f.write_text(json.dumps(screen.defaults()), encoding="utf-8"); os.utime(f, ns=(t1, t1))   # fixed, SAME mtime
    s, err = src.poll()
    assert s and err is None and src.generation == 2


def test_prepare_writes_defaults_once_and_never_after_loss(home):          # R8, P5
    from inkvault import export, paths, screen
    screen.prepare()
    assert paths.screen_file().exists()
    paths.screen_file().unlink()
    with pytest.raises(screen.SettingsError):
        screen.prepare()
    assert not paths.screen_file().exists()


def test_a_crash_between_defaults_and_marker_keeps_the_file(home):
    from inkvault import export, paths, screen
    screen.write_atomic(paths.screen_file(), {**screen.defaults(), "never_titles": ["Mine"]})
    screen.prepare()
    vault = export.open_vault()
    try:
        assert screen.initialized(vault) and "Mine" in screen.read_file()["never_titles"]
    finally:
        vault.close()


def test_parse_pause():
    from inkvault import screen
    t0 = datetime(2026, 10, 12, 15, 0, tzinfo=timezone.utc)
    assert (screen.parse_pause("30m", t0) - t0).total_seconds() == 1800
    assert (screen.parse_pause("1h", t0) - t0).total_seconds() == 3600
    assert screen.parse_pause("until-tomorrow", t0) > t0
    for bad in ("forever", "0m", "h"):
        with pytest.raises(ValueError):
            screen.parse_pause(bad, t0)
```

- [ ] **Step 2: Run to see them fail**

Run: `uv run pytest tests/test_screen_settings.py -q`
Expected: FAIL (`AttributeError: … 'DEFAULT_TEXT_APPS'`).

- [ ] **Step 3: Implement** (merge the imports into `screen.py`'s import block)

```python
import contextlib
import json
import os
import time
from dataclasses import dataclass

from . import paths

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
        os.replace(tmp, path)
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


def edit(change):
    with settings_lock():
        data = read_file()
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
```

- [ ] **Step 4: Run to see them pass**

Run: `uv run pytest tests/test_screen_settings.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/inkvault/screen.py tests/test_screen_settings.py
git commit -m "Screen capture: screen.json with typed validation, its own lock, preparing a home

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: The pure core (`stretches.py`)

**Files:**
- Create: `src/inkvault/stretches.py`
- Test: `tests/test_stretches.py`

**Interfaces:**
- Consumes: `screen.Settings` (`paused`, `never`, `text_app`)
- Produces:
  - `Identity(hwnd, pid, created, exe, title)` (frozen), with property `.window -> (hwnd, pid, created)`
  - `Sample(mono, utc, window: Identity | None, idle_s, locked)`
  - Actions: `Opened(stretch_id, identity, started, ended)`, `Extended(stretch_id, ended)`, `Closed(stretch_id,
    ended)`, `ReadDue(stretch_id, identity)`
  - `Core(new_id=…)`, with `.step(sample, settings) -> list`, `.close(ended) -> list`, `.drop()`, `.open`
  - Constants `POLL_GAP=30.0`, `IDLE_LIMIT=300.0`, `DWELL=30.0`, `REREAD=120.0`, `EXTEND_EVERY=30.0`, `MIN_POLLS=2`

- [ ] **Step 1: Write the failing tests**

```python
"""The watcher's pure core (spec §1): stretches from 2 s samples, without Windows."""
from datetime import datetime, timedelta, timezone

T0 = datetime(2026, 10, 12, 14, 0, tzinfo=timezone.utc)


def settings(**kw):
    from inkvault import screen
    d = screen.defaults()
    d["text_apps"].append("Discord.exe")
    d.update(kw)
    return screen.validate(d)


def win(title="Chat", exe="Discord.exe", hwnd=1, pid=10, created=1):
    from inkvault.stretches import Identity
    return Identity(hwnd, pid, created, exe, title)


def run(core, polls, s=None, start=0.0, step=2.0):
    from inkvault.stretches import Sample
    s = s or settings()
    out = []
    for i, (w, idle, locked) in enumerate(polls):
        t = start + i * step
        out += [(i, a) for a in core.step(Sample(t, T0 + timedelta(seconds=t), w, idle, locked), s)]
    return out


def kinds(actions):
    return [type(a).__name__ for _, a in actions]


def core():
    from inkvault.stretches import Core
    n = iter(range(10_000))
    return Core(new_id=lambda: f"screen:{next(n)}")


def test_one_poll_is_a_flick():
    acts = run(core(), [(win("A"), 0, False), (win("B"), 0, False), (win("B"), 0, False)])
    assert kinds(acts) == ["Opened"] and acts[0][1].identity.title == "B"


def test_open_extend_close():
    acts = run(core(), [(win(), 0, False)] * 18 + [(win("Other"), 0, False)])
    assert kinds(acts)[0] == "Opened" and kinds(acts).count("Extended") == 1 and kinds(acts)[-1] == "Closed"
    assert acts[0][1].started == T0 and acts[0][1].ended == T0 + timedelta(seconds=2)


def test_idle_closes_at_last_input_never_before_start():
    from inkvault.stretches import IDLE_LIMIT
    acts = run(core(), [(win(), 0, False), (win(), 0, False), (win(), IDLE_LIMIT + 50, False)])
    assert kinds(acts)[-1] == "Closed" and acts[-1][1].ended == T0


def test_lock_and_exclusion_close_at_the_observing_poll():
    for polls in ([(win(), 0, False)] * 2 + [(win(), 0, True)],
                  [(win(), 0, False)] * 2 + [(win("New InPrivate tab"), 0, False)]):
        acts = run(core(), polls)
        assert kinds(acts) == ["Opened", "Closed"] and acts[-1][1].ended == T0 + timedelta(seconds=4)


def test_a_pause_starting_mid_stretch_closes_at_the_observing_poll():          # P12: the pause case
    c = core()
    acts = run(c, [(win(), 0, False)] * 2)
    paused = settings(paused_until=(T0 + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"))
    acts += run(c, [(win(), 0, False)], paused, start=4.0)
    assert kinds(acts) == ["Opened", "Closed"] and acts[-1][1].ended == T0 + timedelta(seconds=4)


def test_poll_gap_closes_at_the_earlier_poll():
    from inkvault.stretches import Sample
    c, s = core(), settings()
    c.step(Sample(0, T0, win(), 0, False), s)
    c.step(Sample(2, T0 + timedelta(seconds=2), win(), 0, False), s)
    acts = c.step(Sample(100, T0 + timedelta(seconds=100), win(), 0, False), s)
    assert type(acts[0]).__name__ == "Closed" and acts[0].ended == T0 + timedelta(seconds=2)
    assert c.open.started == T0 + timedelta(seconds=100)


def test_dwell_and_per_window_budget_are_separate():
    c, s = core(), settings()
    assert kinds(run(c, [(win("A"), 0, False)] * 16, s)).count("ReadDue") == 1      # read at t = 30
    assert "ReadDue" not in kinds(run(c, [(win("B"), 0, False)] * 20, s, start=32.0))
    acts = run(c, [(win("B"), 0, False)] * 41, s, start=72.0)                        # t = 72 … 152, no gap
    due = [i for i, a in acts if type(a).__name__ == "ReadDue"]
    assert len(due) == 1 and 72.0 + due[0] * 2.0 == 150.0


def test_a_reused_pid_is_a_different_window():
    c = core()
    run(c, [(win(created=1), 0, False)] * 16)
    assert "ReadDue" in kinds(run(c, [(win(created=2), 0, False)] * 16, start=32.0))


def test_no_window_closes_and_timeline_only_apps_never_read():
    assert kinds(run(core(), [(win(), 0, False)] * 2 + [(None, 0, False)])) == ["Opened", "Closed"]
    assert "ReadDue" not in kinds(run(core(), [(win(exe="chrome.exe"), 0, False)] * 40))


def test_wall_clock_going_backwards_never_ends_before_start():
    from inkvault.stretches import Sample
    c, s = core(), settings()
    c.step(Sample(0, T0, win(), 0, False), s)
    c.step(Sample(2, T0 + timedelta(seconds=2), win(), 0, False), s)
    acts = c.step(Sample(4, T0 - timedelta(hours=1), win("Other"), 0, False), s)
    assert [a for a in acts if type(a).__name__ == "Closed"][0].ended >= T0


def test_drop_forgets_without_writing():
    c = core()
    run(c, [(win(), 0, False)] * 3)
    c.drop()
    assert c.open is None and c.close(T0) == []
```

- [ ] **Step 2: Run to see them fail**

Run: `uv run pytest tests/test_stretches.py -q`
Expected: FAIL (`No module named 'inkvault.stretches'`).

- [ ] **Step 3: Implement `src/inkvault/stretches.py`**

```python
"""The watcher's pure core (spec §1): 2-second samples in, stretch actions out. No Windows, no SQLite.

A stretch is written from its second poll (one poll is a flick), extended every 30 s, and closed: at the observing
poll for a window change, lock, pause or exclusion; at the last input (never before its start) for idle; at the
earlier poll when two polls are more than 30 s apart. Reads need 30 s of dwell in the stretch AND 120 s since this
window (hwnd, pid, process creation time) was last read."""
import uuid
from dataclasses import dataclass
from datetime import timedelta

POLL_GAP = 30.0
IDLE_LIMIT = 300.0
DWELL = 30.0
REREAD = 120.0
EXTEND_EVERY = 30.0
MIN_POLLS = 2


@dataclass(frozen=True)
class Identity:
    hwnd: int
    pid: int
    created: int  # process creation time (FILETIME): a reused pid is a different window
    exe: str
    title: str    # raw, memory only

    @property
    def window(self):
        return self.hwnd, self.pid, self.created


@dataclass(frozen=True)
class Sample:
    mono: float
    utc: object
    window: object  # Identity | None
    idle_s: float
    locked: bool


@dataclass(frozen=True)
class Opened:
    stretch_id: str
    identity: Identity
    started: object
    ended: object


@dataclass(frozen=True)
class Extended:
    stretch_id: str
    ended: object


@dataclass(frozen=True)
class Closed:
    stretch_id: str
    ended: object


@dataclass(frozen=True)
class ReadDue:
    stretch_id: str
    identity: Identity


@dataclass
class OpenStretch:
    id: str
    identity: Identity
    started: object
    started_mono: float
    last: object
    last_mono: float
    polls: int = 1
    written: bool = False
    extended_mono: float = 0.0


class Core:
    def __init__(self, new_id=lambda: "screen:" + str(uuid.uuid4())):
        self.new_id, self.open, self.last_read, self.last_mono = new_id, None, {}, None

    def step(self, s, settings):
        out = []
        if self.open and self.last_mono is not None and s.mono - self.last_mono > POLL_GAP:
            out += self.close(self.open.last)
        self.last_mono = s.mono
        self.last_read = {k: v for k, v in self.last_read.items() if s.mono - v < REREAD}
        idle = s.idle_s >= IDLE_LIMIT
        if idle and self.open and not s.locked:
            out += self.close(s.utc - timedelta(seconds=s.idle_s))
        w = s.window
        if s.locked or idle or w is None or settings.paused(s.utc) or settings.never(w.exe, w.title):
            if self.open:
                out += self.close(s.utc)
            return out
        o = self.open
        if o and o.identity == w:
            o.last, o.last_mono, o.polls = max(s.utc, o.started), s.mono, o.polls + 1
            if not o.written and o.polls >= MIN_POLLS:
                o.written, o.extended_mono = True, s.mono
                out.append(Opened(o.id, o.identity, o.started, o.last))
            elif o.written and s.mono - o.extended_mono >= EXTEND_EVERY:
                o.extended_mono = s.mono
                out.append(Extended(o.id, o.last))
        else:
            if o:
                out += self.close(s.utc)
            o = self.open = OpenStretch(self.new_id(), w, s.utc, s.mono, s.utc, s.mono)
        if (o.written and settings.text_app(w.exe) and s.mono - o.started_mono >= DWELL
                and w.window not in self.last_read):
            self.last_read[w.window] = s.mono
            out.append(ReadDue(o.id, w))
        return out

    def close(self, ended):
        o, self.open = self.open, None
        if not o or not o.written:
            return []
        return [Closed(o.id, max(ended, o.started))]

    def drop(self):
        """After a removal (spec §5): forget the open stretch without writing anything."""
        self.open = None
```

- [ ] **Step 4: Run to see them pass**

Run: `uv run pytest tests/test_stretches.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/inkvault/stretches.py tests/test_stretches.py
git commit -m "Screen capture: the pure stretch core

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Guarded writes (`screenstore.apply`)

**Files:**
- Create: `src/inkvault/screenstore.py`
- Test: `tests/test_screenstore.py`

**Interfaces:**
- Consumes: `export.open_vault(timeout=…)`, `screen.removal_gen`
- Produces:
  - `Write(kind, stretch_id, gen, settings_gen=0, started="", ended="", app="", title=None, text_id="",
    created="", text="", text_hash="")`, with `.size()`
  - Exceptions: `Stale`, `Busy`, `StoreError`
  - `connect() -> sqlite3.Connection` (autocommit; 250 ms busy timeout *from the start*, P11)
  - `apply(conn, w)`
  - `BUSY_MS = 250`

- [ ] **Step 1: Write the failing tests**

```python
"""Watcher writes (spec §5, F1): one BEGIN IMMEDIATE each; a stale generation or a missing parent is never
written, so removed text can't come back. Two real connections, like the watcher and forget."""
import pytest

S, E = "2026-10-12T14:00:00.000000Z", "2026-10-12T14:00:02.000000Z"


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("INKVAULT_HOME", str(tmp_path))
    from inkvault import screenstore
    conn = screenstore.connect()
    yield screenstore, conn
    conn.close()


def opened(ss, gen=0, sid="screen:1"):
    return ss.Write("open", sid, gen, started=S, ended=E, app="Discord.exe", title="Chat")


def text(ss, gen=0, sid="screen:1", tid="screentext:1", body="hello there everyone"):
    return ss.Write("text", sid, gen, text_id=tid, created=E, text=body, text_hash=tid)


def bump():
    from inkvault import export
    v = export.open_vault()
    v.execute("INSERT OR REPLACE INTO meta VALUES ('screen_removal_gen', CAST(coalesce((SELECT value FROM meta "
              "WHERE key='screen_removal_gen'), 0) + 1 AS TEXT))")
    v.commit(); v.close()


def test_open_text_close(store):
    ss, conn = store
    ss.apply(conn, opened(ss)); ss.apply(conn, text(ss))
    ss.apply(conn, ss.Write("close", "screen:1", 0, ended="2026-10-12T14:05:00.000000Z"))
    assert conn.execute("SELECT ended FROM screen_stretches").fetchone()[0].startswith("2026-10-12T14:05")
    assert conn.execute("SELECT COUNT(*) FROM screen_text").fetchone()[0] == 1


def test_a_removal_makes_older_writes_stale(store):
    ss, conn = store
    ss.apply(conn, opened(ss))
    bump()
    for w in (ss.Write("extend", "screen:1", 0, ended=E), text(ss), opened(ss, 0, "screen:2")):
        with pytest.raises(ss.Stale):
            ss.apply(conn, w)
    assert conn.execute("SELECT COUNT(*) FROM screen_text").fetchone()[0] == 0


def test_a_missing_parent_is_stale_and_never_recreated(store):
    ss, conn = store
    ss.apply(conn, opened(ss))
    conn.execute("DELETE FROM screen_stretches")
    for w in (text(ss), ss.Write("close", "screen:1", 0, ended=E)):
        with pytest.raises(ss.Stale):
            ss.apply(conn, w)
    assert conn.execute("SELECT COUNT(*) FROM screen_stretches").fetchone()[0] == 0


def test_a_restart_reads_the_persisted_generation(store):
    from inkvault import screen
    ss, conn = store
    bump()
    fresh = ss.connect()
    try:
        assert screen.removal_gen(fresh) == 1
        ss.apply(fresh, opened(ss, gen=1, sid="screen:9"))
    finally:
        fresh.close()


def test_busy_within_budget_even_at_connect(store):                        # P11
    import time
    from inkvault import export
    ss, conn = store
    blocker = export.open_vault(); blocker.execute("BEGIN IMMEDIATE")
    try:
        t = time.monotonic()
        with pytest.raises(ss.Busy):
            ss.apply(conn, opened(ss))
        assert time.monotonic() - t < 1.0
        t = time.monotonic()
        try:
            ss.connect().close()                                            # DDL under contention: fails fast
        except ss.Busy:
            pass
        assert time.monotonic() - t < 1.5
    finally:
        blocker.rollback(); blocker.close()
```

- [ ] **Step 2: Run to see them fail**

Run: `uv run pytest tests/test_screenstore.py -q`
Expected: FAIL (`No module named 'inkvault.screenstore'`).

- [ ] **Step 3: Implement `src/inkvault/screenstore.py`**

```python
"""The watcher's vault writes (spec §5). Each write is one short BEGIN IMMEDIATE transaction that first checks the
removal generation it was observed under, and for an extend, close or text, that its stretch still exists.
Otherwise it raises Stale and writes nothing: a stretch a removal deleted is never recreated (F1)."""
import sqlite3
from dataclasses import dataclass

from . import export, screen

BUSY_MS = 250


class Stale(Exception):
    """The removal generation moved, or the stretch is gone."""


class Busy(Exception):
    """Another writer holds the vault."""


class StoreError(Exception):
    """Any other database error (disk full, I/O): the write stays queued and the error is reported."""


@dataclass
class Write:
    kind: str
    stretch_id: str
    gen: int
    settings_gen: int = 0
    started: str = ""
    ended: str = ""
    app: str = ""
    title: object = None
    text_id: str = ""
    created: str = ""
    text: str = ""
    text_hash: str = ""

    def size(self):
        return len(self.text.encode("utf-8")) if self.kind == "text" else 0


def classify(e):
    msg = str(e)
    if isinstance(e, sqlite3.OperationalError) and ("locked" in msg or "busy" in msg):
        return Busy(msg)
    return StoreError(type(e).__name__ + ": " + msg)


def connect():
    try:
        conn = export.open_vault(timeout=BUSY_MS / 1000)
        conn.commit()
    except sqlite3.Error as e:
        raise classify(e) from None
    conn.isolation_level = None
    return conn


def apply(conn, w):
    try:
        conn.execute("BEGIN IMMEDIATE")
    except sqlite3.Error as e:
        raise classify(e) from None
    try:
        if screen.removal_gen(conn) != w.gen:
            raise Stale("removal generation moved")
        if w.kind == "open":
            conn.execute("INSERT OR IGNORE INTO screen_stretches VALUES (?,?,?,?,?)",
                         (w.stretch_id, w.started, w.ended, w.app, w.title))
        elif w.kind in ("extend", "close"):
            if conn.execute("UPDATE screen_stretches SET ended=? WHERE id=?", (w.ended, w.stretch_id)).rowcount == 0:
                raise Stale("stretch gone")
        elif w.kind == "text":
            if not conn.execute("SELECT 1 FROM screen_stretches WHERE id=?", (w.stretch_id,)).fetchone():
                raise Stale("stretch gone")
            conn.execute("INSERT OR IGNORE INTO screen_text VALUES (?,?,?,?,?)",
                         (w.text_id, w.stretch_id, w.created, w.text, w.text_hash))
        else:
            raise ValueError(f"unknown write {w.kind!r}")
        conn.execute("COMMIT")
    except BaseException as e:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        if isinstance(e, sqlite3.Error):
            raise classify(e) from None
        raise
```

- [ ] **Step 4: Run to see them pass**

Run: `uv run pytest tests/test_screenstore.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/inkvault/screenstore.py tests/test_screenstore.py
git commit -m "Screen capture: guarded vault writes (removal generation + parent check)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: The per-stretch queue

**Files:**
- Modify: `src/inkvault/screenstore.py` (append)
- Test: `tests/test_screenstore.py` (append)

**Interfaces:**
- Consumes: Task 5
- Produces:
  - `Queue(max_stretches=50, max_text_bytes=2_000_000)`, with:
    - `push(w) -> bool` (False: a new stretch refused at the cap);
    - `flush(conn, max_writes=20, budget_s=0.25, clock=time.monotonic) -> Flush`;
    - `blocked()`, `has_text(sid, h)`, `drop_text(pred) -> int`, `clear()`, `__len__`;
    - attributes `dropped_text` and `text_bytes`
  - `Flush(written=0, stale=False, busy=False, error=None, texts=[])`: `texts` lists the text writes that
    committed, so the watcher marks hashes stored only after their insert commits (P6)

- [ ] **Step 1: Write the failing tests** (append)

```python
def test_coalescing_and_dependency_order(store):
    ss, conn = store
    q = ss.Queue()
    q.push(opened(ss))
    for i in range(200):
        q.push(ss.Write("extend", "screen:1", 0, ended=f"2026-10-12T15:{i % 60:02d}:00.000000Z"))
    q.push(text(ss))
    assert len(q) == 1
    r = q.flush(conn)
    assert r.written == 2 and [w.text_id for w in r.texts] == ["screentext:1"] and len(q) == 0


def test_flush_stops_at_busy_and_at_its_budget(store):
    from inkvault import export
    ss, conn = store
    q = ss.Queue()
    for i in range(30):
        q.push(opened(ss, sid=f"screen:{i}"))
    blocker = export.open_vault(); blocker.execute("BEGIN IMMEDIATE")
    try:
        r = q.flush(conn)
        assert r.busy and r.written == 0 and len(q) == 30
    finally:
        blocker.rollback(); blocker.close()
    assert q.flush(conn, max_writes=20).written == 20 and len(q) == 10           # P11: write budget
    ticks = iter([0.0, 0.0, 0.3, 0.3, 0.3])
    assert q.flush(conn, clock=lambda: next(ticks)).written == 1                 # P11: time budget


def test_the_cap_refuses_new_stretches_but_not_their_closes(store):
    ss, _ = store
    q = ss.Queue(max_stretches=3)
    assert all(q.push(opened(ss, sid=f"screen:{i}")) for i in range(3))
    assert q.blocked() and q.push(opened(ss, sid="screen:new")) is False
    assert q.push(ss.Write("close", "screen:2", 0, ended=E)) and len(q) == 3


def test_text_cap_and_has_text(store):
    ss, _ = store
    q = ss.Queue(max_text_bytes=100)
    q.push(opened(ss))
    for i in range(5):
        q.push(text(ss, tid=f"t{i}", body="z" * 40))
    assert q.text_bytes <= 100 and q.dropped_text == 3
    assert q.has_text("screen:1", "t4") and not q.has_text("screen:1", "t0")


def test_drop_text(store):
    ss, _ = store
    q = ss.Queue()
    q.push(opened(ss)); q.push(text(ss))
    assert q.drop_text(lambda w: True) == 1 and q.text_bytes == 0 and len(q) == 1


def test_a_non_busy_error_keeps_the_write_and_reports(store, monkeypatch):
    ss, conn = store
    q = ss.Queue()
    q.push(opened(ss))
    monkeypatch.setattr(ss, "apply", lambda *_: (_ for _ in ()).throw(ss.StoreError("OperationalError: disk full")))
    r = q.flush(conn)
    assert r.error.endswith("disk full") and len(q) == 1
```

- [ ] **Step 2: Run to see them fail**

Run: `uv run pytest tests/test_screenstore.py -q`
Expected: FAIL (`… has no attribute 'Queue'`).

- [ ] **Step 3: Implement (append to `screenstore.py`)**

```python
import time
from collections import OrderedDict
from dataclasses import field


@dataclass
class Flush:
    written: int = 0
    stale: bool = False
    busy: bool = False
    error: object = None
    texts: list = field(default_factory=list)


class Entry:
    def __init__(self):
        self.open, self.state, self.texts = None, None, []

    def empty(self):
        return self.open is None and self.state is None and not self.texts


class Queue:
    """Pending writes by stretch (spec §5, R4, P11). An unwritten open absorbs later extends and its close; text
    follows its open in order. One flush per poll, stopping at the first failure, after max_writes, or budget_s."""

    def __init__(self, max_stretches=50, max_text_bytes=2_000_000):
        self.max_stretches, self.max_text_bytes = max_stretches, max_text_bytes
        self.entries, self.text_bytes, self.dropped_text = OrderedDict(), 0, 0

    def __len__(self):
        return len(self.entries)

    def blocked(self):
        return len(self.entries) >= self.max_stretches

    def push(self, w):
        if w.kind == "open" and w.stretch_id not in self.entries and self.blocked():
            return False
        e = self.entries.setdefault(w.stretch_id, Entry())
        if w.kind == "open":
            e.open = w
        elif w.kind in ("extend", "close"):
            if e.open is not None:
                e.open.ended = w.ended
            elif not (e.state and e.state.kind == "close" and w.kind == "extend"):
                e.state = w
        else:
            e.texts.append(w)
            self.text_bytes += w.size()
            self._trim()
        return True

    def _trim(self):
        while self.text_bytes > self.max_text_bytes:
            for sid, e in self.entries.items():
                if e.texts:
                    self.text_bytes -= e.texts.pop(0).size()
                    self.dropped_text += 1
                    if e.empty():
                        del self.entries[sid]
                    break
            else:
                return

    def has_text(self, sid, h):
        e = self.entries.get(sid)
        return bool(e) and any(w.text_hash == h for w in e.texts)

    def drop_text(self, pred):
        n = 0
        for sid in list(self.entries):
            e = self.entries[sid]
            keep = []
            for w in e.texts:
                if pred(w):
                    self.text_bytes -= w.size()
                    n += 1
                else:
                    keep.append(w)
            e.texts = keep
            if e.empty():
                del self.entries[sid]
        self.dropped_text += n
        return n

    def clear(self):
        self.entries.clear()
        self.text_bytes = 0

    def flush(self, conn, max_writes=20, budget_s=0.25, clock=time.monotonic):
        r, start = Flush(), clock()

        def one(w):
            if r.written >= max_writes or clock() - start > budget_s:
                return False
            try:
                apply(conn, w)
            except Stale:
                r.stale = True
                return False
            except Busy:
                r.busy = True
                return False
            except StoreError as e:
                r.error = str(e)
                return False
            r.written += 1
            if w.kind == "text":
                r.texts.append(w)
            return True

        for sid in list(self.entries):
            e = self.entries[sid]
            for slot in ("open", "state"):
                w = getattr(e, slot)
                if w is not None:
                    if not one(w):
                        return r
                    setattr(e, slot, None)
            while e.texts:
                if not one(e.texts[0]):
                    return r
                self.text_bytes -= e.texts.pop(0).size()
            del self.entries[sid]
        return r
```

`flush` calls the module-level `apply`, so tests can patch `screenstore.apply`.

- [ ] **Step 4: Run to see them pass**

Run: `uv run pytest tests/test_screenstore.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/inkvault/screenstore.py tests/test_screenstore.py
git commit -m "Screen capture: per-stretch queue with caps and per-poll budgets

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Typed Win32 bindings and the Windows layer (`winapi.py`)

**Files:**
- Create: `src/inkvault/winapi.py`
- Test: `tests/test_winapi.py`

**Interfaces:**
- Consumes: `stretches.Identity`, `stretches.Sample`
- Produces (Windows-only except `idle_ms` and `title_sha256`):
  - `api() -> SimpleNamespace(user32, kernel32, gdi32, dwmapi)`: `WinDLL(use_last_error=True)` with full
    `argtypes`/`restype` for every function used here, in `worker.py` and in `screentext.py`
  - `idle_ms(now64, last32) -> int`; `title_sha256(title) -> str`
  - `identity(hwnd) -> Identity | None`: None when invisible, minimized, cloaked, *or unreadable* (fail closed)
  - `foreground()`, `input_desktop_default() -> bool` (False on any failure), `idle_seconds()`,
    `locked(window)`, `sample(mono, utc) -> Sample`
  - `matches(request: dict) -> bool` (foreground, uncloaked, unminimized, `Default` desktop, same `hwnd`, pid,
    creation time, executable and title hash)
  - `RECT`, `LASTINPUTINFO`
  - `kill_on_close_job(proc) -> int`: closes the job and raises `OSError` on any failure; `close_handle(h)`;
    `process_alive(pid) -> bool`; `gdi_objects() -> int`

- [ ] **Step 1: Write the failing tests**

```python
"""Typed Win32 layer (spec §1, §2, P1). Pure parts run everywhere; real calls only on Windows, against windows
and processes the test creates itself."""
import subprocess
import sys
import time

import pytest

windows = pytest.mark.skipif(sys.platform != "win32", reason="Windows only")


def test_idle_ms_survives_rollover_and_title_hash_is_stable():
    from inkvault import winapi
    assert winapi.idle_ms(5_000, 4_000) == 1_000
    assert winapi.idle_ms(2**32 + 500, 2**32 - 500) == 1_000
    assert winapi.idle_ms(3 * 2**32 + 10, 0) == 10
    assert winapi.title_sha256("é") == winapi.title_sha256("é") and len(winapi.title_sha256("")) == 64


@windows
def test_every_binding_is_typed():
    import ctypes
    from inkvault import winapi
    a = winapi.api()
    for dll in (a.user32, a.kernel32, a.gdi32, a.dwmapi):
        for name, f in vars(dll).items():
            if isinstance(f, ctypes._CFuncPtr):
                assert f.argtypes is not None, name


@windows
def test_identity_of_our_own_window_then_minimized():
    import tkinter
    from inkvault import winapi
    root = tkinter.Tk(); root.title("inkvault-test-window"); root.geometry("400x200"); root.update()
    try:
        hwnd = int(root.wm_frame(), 16)
        ident = winapi.identity(hwnd)
        assert ident and ident.title == "inkvault-test-window" and ident.created > 0
        assert ident.exe.lower().startswith("python")
        root.iconify(); root.update()
        assert winapi.identity(hwnd) is None
    finally:
        root.destroy()


@windows
def test_unreadable_process_or_desktop_fails_closed(monkeypatch):           # Review Focus 1 and 2
    from inkvault import winapi
    a = winapi.api()
    monkeypatch.setattr(winapi, "_process", lambda pid: None)               # OpenProcess denied (elevated app)
    assert winapi.identity(a.user32.GetForegroundWindow()) is None
    monkeypatch.setattr(winapi, "_input_desktop_name", lambda: None)        # RDP / secure desktop
    assert winapi.input_desktop_default() is False and winapi.locked(None) is True


@windows
def test_job_kills_the_child_when_its_parent_dies():
    parent = ("import subprocess, sys, time; from inkvault import winapi; "
              "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
              "winapi.kill_on_close_job(c); print(c.pid, flush=True); time.sleep(60)")
    p = subprocess.Popen([sys.executable, "-c", parent], stdout=subprocess.PIPE, text=True)
    try:
        child = int(p.stdout.readline())
        from inkvault import winapi
        assert winapi.process_alive(child)
        p.kill(); p.wait(timeout=10)
        end = time.monotonic() + 5
        while winapi.process_alive(child) and time.monotonic() < end:
            time.sleep(0.1)
        assert not winapi.process_alive(child)
    finally:
        if p.poll() is None:
            p.kill()
```

- [ ] **Step 2: Run to see them fail**

Run: `uv run pytest tests/test_winapi.py -q`
Expected: FAIL (`No module named 'inkvault.winapi'`).

- [ ] **Step 3: Implement `src/inkvault/winapi.py`**

```python
"""Windows calls for screen capture (spec §1–§2), through typed bindings only (P1): every function gets argtypes
and restype, handles are pointer-sized, and anything that can't be read counts as "no window" (fail closed).
Importable everywhere; call only on Windows."""
import ctypes
import hashlib
import os
from ctypes import wintypes as W
from types import SimpleNamespace

from .stretches import Identity, Sample

DWMWA_CLOAKED = 14
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SYNCHRONIZE = 0x00100000
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
JobObjectExtendedLimitInformation = 9
GR_GDIOBJECTS = 0
_api = None


class RECT(ctypes.Structure):
    _fields_ = [("left", W.LONG), ("top", W.LONG), ("right", W.LONG), ("bottom", W.LONG)]


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", W.UINT), ("dwTime", W.DWORD)]


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [(n, ctypes.c_ulonglong) for n in ("r", "w", "o", "rb", "wb", "ob")]


class BASIC_LIMIT(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", W.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", W.DWORD),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", W.DWORD), ("SchedulingClass", W.DWORD)]


class EXTENDED_LIMIT(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", BASIC_LIMIT), ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]


def _sig(f, res, *args):
    f.restype, f.argtypes = res, list(args)


def api():
    global _api
    if _api is not None:
        return _api
    u = ctypes.WinDLL("user32", use_last_error=True)
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    g = ctypes.WinDLL("gdi32", use_last_error=True)
    d = ctypes.WinDLL("dwmapi", use_last_error=True)
    H, P = W.HANDLE, ctypes.POINTER
    _sig(u.GetForegroundWindow, W.HWND)
    _sig(u.IsWindowVisible, W.BOOL, W.HWND)
    _sig(u.IsIconic, W.BOOL, W.HWND)
    _sig(u.GetWindowThreadProcessId, W.DWORD, W.HWND, P(W.DWORD))
    _sig(u.GetWindowTextLengthW, ctypes.c_int, W.HWND)
    _sig(u.GetWindowTextW, ctypes.c_int, W.HWND, W.LPWSTR, ctypes.c_int)
    _sig(u.GetWindowRect, W.BOOL, W.HWND, P(RECT))
    _sig(u.GetWindowDC, W.HDC, W.HWND)
    _sig(u.ReleaseDC, ctypes.c_int, W.HWND, W.HDC)
    _sig(u.PrintWindow, W.BOOL, W.HWND, W.HDC, W.UINT)
    _sig(u.OpenInputDesktop, H, W.DWORD, W.BOOL, W.DWORD)
    _sig(u.GetUserObjectInformationW, W.BOOL, H, ctypes.c_int, ctypes.c_void_p, W.DWORD, P(W.DWORD))
    _sig(u.CloseDesktop, W.BOOL, H)
    _sig(u.GetLastInputInfo, W.BOOL, P(LASTINPUTINFO))
    _sig(u.SetProcessDpiAwarenessContext, W.BOOL, H)
    _sig(u.GetThreadDpiAwarenessContext, H)
    _sig(u.GetAwarenessFromDpiAwarenessContext, ctypes.c_int, H)
    _sig(u.GetGuiResources, W.DWORD, H, W.DWORD)
    _sig(k.OpenProcess, H, W.DWORD, W.BOOL, W.DWORD)
    _sig(k.CloseHandle, W.BOOL, H)
    _sig(k.QueryFullProcessImageNameW, W.BOOL, H, W.DWORD, W.LPWSTR, P(W.DWORD))
    _sig(k.GetProcessTimes, W.BOOL, H, P(W.FILETIME), P(W.FILETIME), P(W.FILETIME), P(W.FILETIME))
    _sig(k.GetExitCodeProcess, W.BOOL, H, P(W.DWORD))
    _sig(k.GetTickCount64, ctypes.c_ulonglong)
    _sig(k.GetCurrentProcess, H)
    _sig(k.CreateJobObjectW, H, ctypes.c_void_p, W.LPCWSTR)
    _sig(k.SetInformationJobObject, W.BOOL, H, ctypes.c_int, ctypes.c_void_p, W.DWORD)
    _sig(k.AssignProcessToJobObject, W.BOOL, H, H)
    _sig(k.PeekNamedPipe, W.BOOL, H, ctypes.c_void_p, W.DWORD, ctypes.c_void_p, P(W.DWORD), ctypes.c_void_p)
    _sig(g.CreateCompatibleDC, W.HDC, W.HDC)
    _sig(g.CreateCompatibleBitmap, W.HBITMAP, W.HDC, ctypes.c_int, ctypes.c_int)
    _sig(g.SelectObject, W.HGDIOBJ, W.HDC, W.HGDIOBJ)
    _sig(g.DeleteObject, W.BOOL, W.HGDIOBJ)
    _sig(g.DeleteDC, W.BOOL, W.HDC)
    _sig(g.SetStretchBltMode, ctypes.c_int, W.HDC, ctypes.c_int)
    _sig(g.StretchBlt, W.BOOL, W.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
         W.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, W.DWORD)
    _sig(g.GetDIBits, ctypes.c_int, W.HDC, W.HBITMAP, W.UINT, W.UINT, ctypes.c_void_p, ctypes.c_void_p, W.UINT)
    _sig(d.DwmGetWindowAttribute, ctypes.c_long, W.HWND, W.DWORD, ctypes.c_void_p, W.DWORD)
    # Resolve every function now, so test_every_binding_is_typed sees them all.
    _api = SimpleNamespace(user32=u, kernel32=k, gdi32=g, dwmapi=d)
    return _api


def idle_ms(now64, last32):
    return ((now64 & 0xFFFFFFFF) - last32) % 2**32


def title_sha256(title):
    return hashlib.sha256((title or "").encode("utf-8", "surrogatepass")).hexdigest()


def _process(pid):
    k = api().kernel32
    h = k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return None
    try:
        buf, n = ctypes.create_unicode_buffer(1024), W.DWORD(1024)
        if not k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
            return None
        t = [W.FILETIME() for _ in range(4)]
        if not k.GetProcessTimes(h, *(ctypes.byref(x) for x in t)):
            return None
        return os.path.basename(buf.value), (t[0].dwHighDateTime << 32) | t[0].dwLowDateTime
    finally:
        k.CloseHandle(h)


def cloaked(hwnd):
    v = W.DWORD()
    if api().dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(v), ctypes.sizeof(v)) != 0:
        return True  # can't tell: fail closed
    return v.value != 0


def identity(hwnd):
    u = api().user32
    if not hwnd or not u.IsWindowVisible(hwnd) or u.IsIconic(hwnd) or cloaked(hwnd):
        return None
    pid = W.DWORD()
    if not u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)):
        return None
    proc = _process(pid.value)
    if proc is None:
        return None
    n = u.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    u.GetWindowTextW(hwnd, buf, n + 1)
    return Identity(int(hwnd), pid.value, proc[1], proc[0], buf.value)


def foreground():
    return identity(api().user32.GetForegroundWindow())


def _input_desktop_name():
    u = api().user32
    desk = u.OpenInputDesktop(0, False, 0x0001)  # DESKTOP_READOBJECTS
    if not desk:
        return None
    try:
        buf, n = ctypes.create_unicode_buffer(256), W.DWORD()
        if not u.GetUserObjectInformationW(desk, 2, buf, ctypes.sizeof(buf), ctypes.byref(n)):  # UOI_NAME
            return None
        return buf.value
    finally:
        u.CloseDesktop(desk)


def input_desktop_default():
    name = _input_desktop_name()
    return name is not None and name.lower() == "default"


def idle_seconds():
    info = LASTINPUTINFO(ctypes.sizeof(LASTINPUTINFO), 0)
    if not api().user32.GetLastInputInfo(ctypes.byref(info)):
        return 0.0
    return idle_ms(api().kernel32.GetTickCount64(), info.dwTime) / 1000.0


def locked(window):
    return (window is not None and window.exe.lower() == "lockapp.exe") or not input_desktop_default()


def sample(mono, utc):
    w = foreground()
    return Sample(mono, utc, w, idle_seconds(), locked(w))


def matches(req):
    if not input_desktop_default():
        return False
    fg = foreground()
    return (fg is not None and (fg.hwnd, fg.pid, fg.created, fg.exe) == (req["hwnd"], req["pid"], req["created"],
            req["exe"]) and title_sha256(fg.title) == req["title_sha256"])


def close_handle(h):
    if h:
        api().kernel32.CloseHandle(h)


def kill_on_close_job(proc):
    k = api().kernel32
    job = k.CreateJobObjectW(None, None)
    if not job:
        raise OSError(ctypes.get_last_error(), "CreateJobObject failed")
    info = EXTENDED_LIMIT()
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    ok = (k.SetInformationJobObject(job, JobObjectExtendedLimitInformation, ctypes.byref(info), ctypes.sizeof(info))
          and k.AssignProcessToJobObject(job, W.HANDLE(int(proc._handle))))
    if not ok:
        err = ctypes.get_last_error()
        k.CloseHandle(job)
        raise OSError(err, "the worker couldn't be put in a job")
    return job


def process_alive(pid):
    k = api().kernel32
    h = k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, False, pid)
    if not h:
        return False
    try:
        code = W.DWORD()
        return bool(k.GetExitCodeProcess(h, ctypes.byref(code))) and code.value == 259  # STILL_ACTIVE
    finally:
        k.CloseHandle(h)


def gdi_objects():
    a = api()
    return a.user32.GetGuiResources(a.kernel32.GetCurrentProcess(), GR_GDIOBJECTS)
```

For `test_every_binding_is_typed` to see each function, `vars(dll)` must contain it: `WinDLL` caches a function as
an attribute on first access, which `_sig` does. Adjust the test to iterate the names set in `api()` if this
Python's ctypes caches differently.

- [ ] **Step 4: Run to see them pass**

Run: `uv run pytest tests/test_winapi.py -q`
Expected: PASS on Windows (four native tests run); elsewhere only the pure test runs.

- [ ] **Step 5: Commit**

```bash
git add src/inkvault/winapi.py tests/test_winapi.py
git commit -m "Screen capture: typed Win32 bindings, fail-closed identity and desktop, job objects

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Validated protocol and the non-blocking supervisor (`worker.py`)

**Files:**
- Create: `src/inkvault/worker.py`, `tests/fakeworker.py`
- Test: `tests/test_worker.py`

**Interfaces:**
- Consumes: `winapi.api`, `winapi.kill_on_close_job`, `winapi.close_handle` (Windows only)
- Produces:
  - `MAX_REQUEST = 3072`, `MAX_RESPONSE = 1_000_000`, `TIMEOUT = 5.0`, `COOLDOWN = 600.0`,
    `STATUSES = ("ok", "changed", "failed", "no-ocr")`
  - `encode(obj) -> bytes`; `Decoder(limit, check)`, whose `.feed(data) -> list[dict]` raises `ProtocolError`
  - `check_request(obj)` and `check_response(obj)` (raise `ProtocolError`)
  - `available(pipe) -> int`; `spawn(argv) -> (Popen, job_handle | None, job_error | None)`
  - `Supervisor(argv, timeout, cooldown, clock)`, with:
    - `.submit(request) -> int | None` (`request` = `hwnd`, `pid`, `created`, `exe`, `title_sha256`);
    - `.poll() -> dict | None`, `.abandon()`, `.busy()`, `.cooling(exe)`, `.close()`;
    - counters `.timeouts`, `.restarts`, `.dropped_requests`, `.job_error`, `.cooldowns`

- [ ] **Step 1: Write the fake worker** (`tests/fakeworker.py`)

```python
"""A synthetic capture worker for supervisor tests. Speaks the real protocol; behavior is chosen by argv[1]."""
import json
import struct
import sys
import time

mode = sys.argv[1]
inp, out = sys.stdin.buffer, sys.stdout.buffer


def send(o):
    b = json.dumps(o).encode()
    out.write(struct.pack(">I", len(b)) + b)
    out.flush()


if mode == "deaf":                       # never reads its input
    time.sleep(3600)
while True:
    head = inp.read(4)
    if len(head) < 4:
        break
    req = json.loads(inp.read(struct.unpack(">I", head)[0]))
    if mode == "hang":
        time.sleep(3600)
    elif mode == "huge":
        send({"id": req["id"], "status": "ok", "lines": ["x" * 2_000_000], "colors": 1, "error": ""})
    elif mode == "garbage":
        out.write(struct.pack(">I", 5) + b"nope!"); out.flush()
    elif mode in ("array", "null", "badstatus", "badlines"):
        send({"array": [], "null": None,
              "badstatus": {"id": req["id"], "status": "maybe", "lines": [], "colors": 0, "error": ""},
              "badlines": {"id": req["id"], "status": "ok", "lines": "text", "colors": 0, "error": ""}}[mode])
    elif mode == "partial":
        out.write(struct.pack(">I", 50) + b'{"id"'); out.flush(); time.sleep(3600)
    elif mode == "late":                 # answers after a delay (abandon/late-answer tests)
        time.sleep(float(sys.argv[2]))
        send({"id": req["id"], "status": "ok", "lines": ["late answer from the fake"], "colors": 9, "error": ""})
    else:
        send({"id": req["id"], "status": "ok", "lines": ["hello from the fake worker"], "colors": 9, "error": ""})
```

- [ ] **Step 2: Write the failing tests** (`tests/test_worker.py`)

```python
"""The supervisor (spec §2, R2, P2, P10): never blocks the loop; abandoning keeps the deadline; validated
messages; late, foreign, partial, oversized and malformed answers never land or block."""
import pathlib
import sys
import time

import pytest

FAKE = str(pathlib.Path(__file__).with_name("fakeworker.py"))


def sup(*mode, **kw):
    from inkvault import worker
    return worker.Supervisor([sys.executable, FAKE, *mode], **kw)


def wait_for(s, seconds=3.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if (r := s.poll()) is not None:
            return r
        time.sleep(0.02)
    return None


def req(exe="Notion.exe", title_sha256="0" * 64):
    return {"hwnd": 1, "pid": 2, "created": 3, "exe": exe, "title_sha256": title_sha256}


def ticks_while(s, seconds):
    n, end = 0, time.monotonic() + seconds
    while time.monotonic() < end:
        t = time.monotonic()
        assert s.poll() is None
        assert time.monotonic() - t < 0.05            # a poll never blocks
        n += 1
        time.sleep(0.01)
    return n


def test_round_trip():
    s = sup("ok")
    try:
        rid = s.submit(req())
        assert wait_for(s)["id"] == rid and not s.busy()
    finally:
        s.close()


def test_hang_is_killed_at_the_deadline_and_cools_the_app():
    s = sup("hang", timeout=0.5)
    try:
        s.submit(req("Notion.exe"))
        assert ticks_while(s, 1.2) > 40
        assert s.timeouts == 1 and s.cooling("Notion.exe") and not s.busy()
        assert s.submit(req("Notion.exe")) is None and s.submit(req("Code.exe")) is not None
    finally:
        s.close()


def test_abandoning_a_hang_keeps_the_deadline():                          # P2: the reported defect
    s = sup("hang", timeout=0.5)
    try:
        s.submit(req()); proc = s.proc
        s.abandon()
        assert s.busy() and s.submit(req("Code.exe")) is None              # no new request to a busy worker
        ticks_while(s, 1.0)
        assert proc.poll() is not None and not s.busy()                    # killed anyway
    finally:
        s.close()


def test_an_abandoned_answer_is_discarded_and_resubmit_waits():
    s = sup("late", "0.3", timeout=2.0)
    try:
        s.submit(req()); s.abandon()
        assert wait_for(s, 1.0) is None and not s.busy()                   # answered, but not acceptable
        rid = s.submit(req())
        assert wait_for(s, 2.0)["id"] == rid
    finally:
        s.close()


def test_a_deaf_worker_never_blocks_submit():
    s = sup("deaf", timeout=0.5)
    try:
        t = time.monotonic()
        s.submit(req())
        assert time.monotonic() - t < 0.5
        ticks_while(s, 1.0)
        assert s.timeouts == 1
    finally:
        s.close()


@pytest.mark.parametrize("mode", ["huge", "garbage", "array", "null", "badstatus", "badlines"])
def test_bad_answers_restart_the_worker(mode):                               # P10
    s = sup(mode, timeout=2.0)
    try:
        s.submit(req())
        assert wait_for(s, 2.5) is None and s.restarts == 1 and not s.busy()
    finally:
        s.close()


def test_partial_never_blocks():
    s = sup("partial", timeout=0.5)
    try:
        s.submit(req())
        ticks_while(s, 0.3)
    finally:
        s.close()


def test_an_oversized_request_is_a_dropped_read_not_a_crash():
    s = sup("ok")
    try:
        assert s.submit({**req(), "exe": "x" * 5000 + ".exe"}) is None and s.dropped_requests == 1
        assert s.submit(req()) is not None                                   # the worker is still fine
    finally:
        s.close()


def test_malformed_requests_are_dropped_reads():
    s = sup("ok")
    try:
        assert s.submit(req(title_sha256="Z" * 64)) is None and s.dropped_requests == 1
        assert s.submit({**req(), "pid": "2"}) is None and s.dropped_requests == 2
    finally:
        s.close()


def test_worker_error_text_is_reduced_to_a_name():
    from inkvault import worker
    assert worker.error_name("OSError") == "OSError"
    assert worker.error_name("secret title text: hello") == "unknown"


def test_long_unicode_titles_fit_because_only_their_hash_is_sent():
    from inkvault import winapi, worker
    title = "@名前 🎮 " * 2000
    assert len(worker.encode({**req(title_sha256=winapi.title_sha256(title)), "id": 1})) < worker.MAX_REQUEST


def test_close_reaps_without_waiting_on_a_hang():
    s = sup("hang")
    s.submit(req()); proc = s.proc
    t = time.monotonic()
    s.close()
    assert time.monotonic() - t < 2.0 and proc.poll() is not None
```

- [ ] **Step 3: Run to see them fail**

Run: `uv run pytest tests/test_worker.py -q`
Expected: FAIL (`No module named 'inkvault.worker'`).

- [ ] **Step 4: Implement `src/inkvault/worker.py`**

```python
"""The capture worker's protocol and supervisor (spec §2, R2, P2, P10).

At most one request is outstanding, and only an idle worker gets one; requests stay under the pipe buffer, so
writing never blocks. Each poll peeks the pipe and reads what's there. Abandoning only makes an answer
unacceptable: the deadline still runs until the worker answers or is killed. Killing never waits; later polls reap."""
import json
import os
import struct
import subprocess
import sys
import time

MAX_REQUEST = 3072
MAX_RESPONSE = 1_000_000
TIMEOUT = 5.0
COOLDOWN = 600.0
STATUSES = ("ok", "changed", "failed", "no-ocr")


class ProtocolError(Exception):
    pass


def encode(obj):
    body = json.dumps(obj).encode("utf-8")
    return struct.pack(">I", len(body)) + body


def _int(v):
    return isinstance(v, int) and not isinstance(v, bool)


HEX = frozenset("0123456789abcdef")


def check_request(o):
    if not (isinstance(o, dict) and all(_int(o.get(k)) for k in ("id", "hwnd", "pid", "created"))
            and isinstance(o.get("exe"), str) and isinstance(o.get("title_sha256"), str)
            and len(o["title_sha256"]) == 64 and set(o["title_sha256"]) <= HEX):
        raise ProtocolError("bad request")
    return o


ERROR_NAME = __import__("re").compile(r"[A-Za-z_][A-Za-z0-9_]{0,60}")


def error_name(text):
    """What the watcher may log from a worker's error field: a class-like name, or "unknown" (R8)."""
    return text if ERROR_NAME.fullmatch(text or "") else "unknown"


def check_response(o):
    if not (isinstance(o, dict) and _int(o.get("id")) and o.get("status") in STATUSES
            and isinstance(o.get("lines"), list) and all(isinstance(x, str) for x in o["lines"])
            and _int(o.get("colors")) and isinstance(o.get("error"), str)):
        raise ProtocolError("bad response")
    return o


class Decoder:
    def __init__(self, limit, check):
        self.limit, self.check, self.buf = limit, check, b""

    def feed(self, data):
        self.buf += data
        out = []
        while len(self.buf) >= 4:
            n = struct.unpack(">I", self.buf[:4])[0]
            if n > self.limit:
                raise ProtocolError("message too large")
            if len(self.buf) < 4 + n:
                break
            body, self.buf = self.buf[4:4 + n], self.buf[4 + n:]
            try:
                obj = json.loads(body)
            except ValueError:
                raise ProtocolError("not JSON") from None
            out.append(self.check(obj))
        return out


def available(pipe):
    """Bytes readable from pipe now, without blocking; -1 when the other end is gone."""
    if sys.platform == "win32":
        import ctypes
        import msvcrt
        from ctypes import wintypes
        from . import winapi
        avail = wintypes.DWORD()
        if not winapi.api().kernel32.PeekNamedPipe(wintypes.HANDLE(msvcrt.get_osfhandle(pipe.fileno())), None, 0,
                                                   None, ctypes.byref(avail), None):
            return -1
        return avail.value
    import select
    return 65536 if select.select([pipe], [], [], 0)[0] else 0


def spawn(argv):
    flags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW
    proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            creationflags=flags)
    job, error = None, None
    if sys.platform == "win32":
        from . import winapi
        try:
            job = winapi.kill_on_close_job(proc)
        except OSError as e:
            error = f"worker not in a job ({e.errno})"
    return proc, job, error


class Pending:
    def __init__(self, id_, exe, started):
        self.id, self.exe, self.started, self.acceptable = id_, exe, started, True


class Supervisor:
    def __init__(self, argv, timeout=TIMEOUT, cooldown=COOLDOWN, clock=time.monotonic):
        self.argv, self.timeout, self.cooldown, self.clock = argv, timeout, cooldown, clock
        self.proc = self.job = self.decoder = self.pending = None
        self.dying, self.next_id = [], 0
        self.timeouts = self.restarts = self.dropped_requests = 0
        self.job_error, self.cooldowns = None, {}

    def busy(self):
        return self.pending is not None

    def cooling(self, exe):
        until = self.cooldowns.get(exe.lower())
        return until is not None and self.clock() < until

    def submit(self, request):
        if self.busy() or self.cooling(request["exe"]):
            return None
        full = {**request, "id": self.next_id + 1}
        try:
            check_request(full)
        except ProtocolError:
            self.dropped_requests += 1
            return None
        msg = encode(full)
        if len(msg) > MAX_REQUEST:
            self.dropped_requests += 1
            return None
        if self.proc is None:
            self.proc, self.job, self.job_error = spawn(self.argv)
            self.decoder = Decoder(MAX_RESPONSE, check_response)
        self.next_id += 1
        try:
            self.proc.stdin.write(msg)   # an idle worker and < 4 KB: never blocks
            self.proc.stdin.flush()
        except OSError:
            self._restart()
            return None
        self.pending = Pending(self.next_id, request["exe"].lower(), self.clock())
        return self.next_id

    def abandon(self):
        if self.pending:
            self.pending.acceptable = False

    def poll(self):
        self._reap()
        if self.proc is None:
            return None
        try:
            n = available(self.proc.stdout)
            if n < 0:
                raise ProtocolError("worker gone")
            messages = []
            if n:
                data = os.read(self.proc.stdout.fileno(), min(n, MAX_RESPONSE + 4))
                if not data:
                    raise ProtocolError("worker gone")
                messages = self.decoder.feed(data)
        except (ProtocolError, OSError):
            self._restart()
            return None
        for m in messages:
            if self.pending and m["id"] == self.pending.id:
                p, self.pending = self.pending, None
                return m if p.acceptable else None
        if self.pending and self.clock() - self.pending.started >= self.timeout:
            self.timeouts += 1
            self.cooldowns[self.pending.exe] = self.clock() + self.cooldown
            self._kill()
        return None

    def _restart(self):
        self.restarts += 1
        self._kill()

    def _kill(self):
        self.pending = None
        p, job, self.proc, self.job = self.proc, self.job, None, None
        if p is None:
            return
        try:
            p.kill()
        except OSError:
            pass
        self.dying.append((p, job))

    def _reap(self):
        for p, job in list(self.dying):
            if p.poll() is not None:
                for f in (p.stdin, p.stdout):
                    try:
                        f.close()
                    except OSError:
                        pass
                if job and sys.platform == "win32":
                    from . import winapi
                    winapi.close_handle(job)
                self.dying.remove((p, job))

    def close(self, wait=1.0):
        if self.proc is not None:
            try:
                self.proc.stdin.close()
            except OSError:
                pass
        self._kill()
        end = time.monotonic() + wait
        while self.dying and time.monotonic() < end:
            self._reap()
            time.sleep(0.02)
```

- [ ] **Step 5: Run to see them pass**

Run: `uv run pytest tests/test_worker.py -q`
Expected: PASS on Windows and elsewhere.

- [ ] **Step 6: Commit**

```bash
git add src/inkvault/worker.py tests/fakeworker.py tests/test_worker.py
git commit -m "Screen capture: validated protocol, non-blocking supervisor (abandon keeps the deadline)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: The worker side: capture and OCR (`screentext.py`), and the `screen` extra

**Files:**
- Create: `src/inkvault/screentext.py`
- Modify: `pyproject.toml` (extra; `inkvault[screen]` in dev), `uv.lock`
- Test: `tests/test_screentext.py`

**Interfaces:**
- Consumes: `winapi.api`, `winapi.matches`, `winapi.RECT`, `worker.Decoder`, `worker.check_request`, `worker.encode`,
  `worker.MAX_REQUEST`
- Produces: `dpi_aware()`, `colors(raw) -> int`, `capture(hwnd, max_dim) -> (bytes, w, h) | None`,
  `ocr_available()`, `max_dim()`, `ocr(raw, w, h) -> list[str]`, `handle(request) -> dict`, `worker_main() -> int`

- [ ] **Step 1: Write the failing tests**

```python
"""The worker (spec §2): checks before and after capture; capture validity and GDI hygiene; OCR of every valid
capture; the protocol. Native tests read only windows the test makes."""
import json
import struct
import subprocess
import sys

import pytest

windows = pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
REQ = {"id": 7, "hwnd": 1, "pid": 2, "created": 3, "exe": "x.exe", "title_sha256": "0" * 64}


def test_changed_when_the_window_moved_on(monkeypatch):
    from inkvault import screentext, winapi
    monkeypatch.setattr(winapi, "matches", lambda r: False)
    assert screentext.handle(REQ) == {"id": 7, "status": "changed", "lines": [], "colors": 0, "error": ""}


def test_two_color_capture_is_still_ocrd(monkeypatch):                         # R5
    from inkvault import screentext, winapi
    monkeypatch.setattr(winapi, "matches", lambda r: True)
    monkeypatch.setattr(screentext, "capture", lambda hwnd, m: ((b"\x00\x00\x00\xff" + b"\xff" * 4) * 500, 50, 20))
    monkeypatch.setattr(screentext, "ocr_available", lambda: True)
    monkeypatch.setattr(screentext, "max_dim", lambda: 10000)
    called = []
    monkeypatch.setattr(screentext, "ocr", lambda raw, w, h: called.append((w, h)) or ["HELLO WORLD"])
    r = screentext.handle(REQ)
    assert called == [(50, 20)] and r["status"] == "ok" and r["colors"] == 2


def test_no_ocr(monkeypatch):
    from inkvault import screentext, winapi
    monkeypatch.setattr(winapi, "matches", lambda r: True)
    monkeypatch.setattr(screentext, "ocr_available", lambda: False)
    assert screentext.handle(REQ)["status"] == "no-ocr"


def test_worker_main_speaks_the_protocol_and_rejects_bad_requests():
    code = ("import sys; from inkvault import screentext, winapi; "
            "winapi.matches = lambda r: False; sys.exit(screentext.worker_main())")
    frame = lambda o: (lambda b: struct.pack(">I", len(b)) + b)(json.dumps(o).encode())
    p = subprocess.run([sys.executable, "-c", code], input=frame(REQ), capture_output=True, timeout=60)
    n = struct.unpack(">I", p.stdout[:4])[0]
    assert json.loads(p.stdout[4:4 + n])["status"] == "changed" and p.returncode == 0
    p = subprocess.run([sys.executable, "-c", code], input=frame([]), capture_output=True, timeout=60)
    assert p.returncode == 2 and p.stdout == b""


@windows
def test_capture_round_trip_handles_and_size():
    import tkinter
    from inkvault import screentext, winapi
    screentext.dpi_aware()
    root = tkinter.Tk(); root.title("inkvault-ocr-test"); root.geometry("700x200")
    tkinter.Label(root, text="INKVAULT KNOWN STRING 4217", font=("Segoe UI", 28), bg="white", fg="black").pack(
        expand=True, fill="both")
    root.update(); root.lift(); root.focus_force(); root.update()
    try:
        hwnd = int(root.wm_frame(), 16)
        r = winapi.RECT(); winapi.api().user32.GetWindowRect(hwnd, r)
        before = winapi.gdi_objects()
        for _ in range(50):
            got = screentext.capture(hwnd, 10000)
        assert winapi.gdi_objects() - before <= 2                              # P1: no leak
        raw, w, h = got
        assert (w, h) == (r.right - r.left, r.bottom - r.top) and len(raw) == w * h * 4
        if screentext.ocr_available():
            assert "4217" in " ".join(screentext.ocr(raw, w, h))
    finally:
        root.destroy()


@windows
def test_a_window_closed_mid_capture_returns_nothing():                       # Review Focus 5
    import tkinter
    from inkvault import screentext
    root = tkinter.Tk(); root.update()
    hwnd = int(root.wm_frame(), 16)
    root.destroy()
    assert screentext.capture(hwnd, 10000) is None


@windows
def test_the_worker_is_per_monitor_dpi_aware():
    code = ("from inkvault import screentext, winapi; screentext.dpi_aware(); u = winapi.api().user32; "
            "print(u.GetAwarenessFromDpiAwarenessContext(u.GetThreadDpiAwarenessContext()))")
    assert subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                          timeout=60).stdout.strip() == "2"
```

- [ ] **Step 2: Run to see them fail**

Run: `uv run pytest tests/test_screentext.py -q`
Expected: FAIL (`No module named 'inkvault.screentext'`).

- [ ] **Step 3: Implement `src/inkvault/screentext.py`**

```python
"""The capture worker (spec §2). Runs as `python -m inkvault.screentext --worker`: validated requests in on stdin,
validated answers out on stdout. Pixels live only in this process's memory.

Before and after capture: winapi.matches (same window, identity and title hash; uncloaked, unminimized, Default
desktop). No BitBlt fallback. Each bitmap is deselected before GetDIBits and before it's deleted (P1); any failed
allocation or call means no capture. Every valid capture is OCR'd (R5)."""
import asyncio
import ctypes
import sys
from ctypes import wintypes as W

from . import winapi, worker

PW_RENDERFULLCONTENT = 2
HALFTONE = 4
SRCCOPY = 0x00CC0020
_engine = None


class BIH(ctypes.Structure):
    _fields_ = [("biSize", W.DWORD), ("biWidth", W.LONG), ("biHeight", W.LONG), ("biPlanes", W.WORD),
                ("biBitCount", W.WORD), ("biCompression", W.DWORD), ("biSizeImage", W.DWORD),
                ("biXPelsPerMeter", W.LONG), ("biYPelsPerMeter", W.LONG), ("biClrUsed", W.DWORD),
                ("biClrImportant", W.DWORD)]


def dpi_aware():
    if sys.platform == "win32":
        winapi.api().user32.SetProcessDpiAwarenessContext(W.HANDLE(-4))  # PER_MONITOR_AWARE_V2


def colors(raw):
    return len({raw[i:i + 3] for i in range(0, len(raw), 4 * 997)})


def _rect(hwnd):
    r = winapi.RECT()
    if not winapi.api().user32.GetWindowRect(hwnd, ctypes.byref(r)):
        return None
    return r.right - r.left, r.bottom - r.top


def capture(hwnd, max_dim):
    a = winapi.api()
    u, g = a.user32, a.gdi32
    size = _rect(hwnd)
    if not size or size[0] <= 0 or size[1] <= 0:
        return None
    w, h = size
    scale = min(1.0, max_dim / max(w, h))
    ow, oh = max(1, int(w * scale)), max(1, int(h * scale))
    hdc = u.GetWindowDC(hwnd)
    if not hdc:
        return None
    mdc = bmp = old = sdc = sbmp = sold = None
    try:
        mdc, bmp = g.CreateCompatibleDC(hdc), g.CreateCompatibleBitmap(hdc, w, h)
        if not mdc or not bmp:
            return None
        old = g.SelectObject(mdc, bmp)
        if not old or not u.PrintWindow(hwnd, mdc, PW_RENDERFULLCONTENT):
            return None
        src_dc, src_bmp = mdc, bmp
        if scale < 1.0:
            sdc, sbmp = g.CreateCompatibleDC(hdc), g.CreateCompatibleBitmap(hdc, ow, oh)
            if not sdc or not sbmp:
                return None
            sold = g.SelectObject(sdc, sbmp)
            g.SetStretchBltMode(sdc, HALFTONE)
            if not sold or not g.StretchBlt(sdc, 0, 0, ow, oh, mdc, 0, 0, w, h, SRCCOPY):
                return None
            src_dc, src_bmp = sdc, sbmp
            g.SelectObject(sdc, sold); sold = None          # deselect before GetDIBits
        else:
            g.SelectObject(mdc, old); old = None
        bih = BIH(ctypes.sizeof(BIH), ow, -oh, 1, 32, 0, 0, 0, 0, 0, 0)  # top-down BGRA
        buf = (ctypes.c_ubyte * (ow * oh * 4))()
        if g.GetDIBits(src_dc, src_bmp, 0, oh, buf, ctypes.byref(bih), 0) != oh:
            return None
        if _rect(hwnd) != (w, h):
            return None
        return bytes(buf), ow, oh
    finally:
        if sold:
            g.SelectObject(sdc, sold)
        if old:
            g.SelectObject(mdc, old)
        for obj in (sbmp, bmp):
            if obj:
                g.DeleteObject(obj)
        for dc in (sdc, mdc):
            if dc:
                g.DeleteDC(dc)
        u.ReleaseDC(hwnd, hdc)


def engine():
    global _engine
    if _engine is None:
        from winrt.windows.media.ocr import OcrEngine
        _engine = OcrEngine.try_create_from_user_profile_languages()
    return _engine


def ocr_available():
    if sys.platform != "win32":
        return False
    try:
        return engine() is not None
    except ImportError:
        return False


def max_dim():
    from winrt.windows.media.ocr import OcrEngine
    return OcrEngine.max_image_dimension


def ocr(raw, w, h):
    from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
    from winrt.windows.storage.streams import DataWriter
    dw = DataWriter()
    dw.write_bytes(raw)
    bmp = SoftwareBitmap.create_copy_from_buffer(dw.detach_buffer(), BitmapPixelFormat.BGRA8, w, h)  # 4 arguments
    return [line.text for line in asyncio.run(_recognize(bmp)).lines]


async def _recognize(bmp):
    return await engine().recognize_async(bmp)


def handle(req):
    out = {"id": req["id"], "status": "failed", "lines": [], "colors": 0, "error": ""}
    try:
        if not winapi.matches(req):
            out["status"] = "changed"
            return out
        if not ocr_available():
            out["status"] = "no-ocr"
            return out
        got = capture(req["hwnd"], max_dim())
        if got is None:
            return out
        raw, w, h = got
        out["colors"] = colors(raw)
        if not winapi.matches(req):
            out["status"] = "changed"
            return out
        out["lines"], out["status"] = ocr(raw, w, h), "ok"
    except Exception as e:  # noqa: BLE001 — the class name only
        out["error"] = type(e).__name__
    return out


def worker_main():
    dpi_aware()
    dec = worker.Decoder(worker.MAX_REQUEST, worker.check_request)
    inp, out = sys.stdin.buffer, sys.stdout.buffer
    while chunk := inp.read1(65536):
        try:
            requests = dec.feed(chunk)
        except worker.ProtocolError:
            return 2
        for req in requests:
            out.write(worker.encode(handle(req)))
            out.flush()
    return 0


if __name__ == "__main__" and "--worker" in sys.argv:
    sys.exit(worker_main())
```

`pyproject.toml`:

```toml
[project.optional-dependencies]
screen = [
    "winrt-Windows.Media.Ocr>=3; sys_platform == 'win32'",
    "winrt-Windows.Graphics.Imaging>=3; sys_platform == 'win32'",
    "winrt-Windows.Storage.Streams>=3; sys_platform == 'win32'",
    "winrt-Windows.Foundation>=3; sys_platform == 'win32'",
]
```

Add `"inkvault[screen]"` to the dev group, then run `uv lock` and `uv sync`.

- [ ] **Step 4: Run to see them pass**

Run: `uv run pytest tests/test_screentext.py -q`
Expected: PASS (native tests on Windows).

- [ ] **Step 5: Commit**

```bash
git add src/inkvault/screentext.py tests/test_screentext.py pyproject.toml uv.lock
git commit -m "Screen capture: the worker (checks, GDI-safe capture, OCR of every capture) and the screen extra

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: The watcher loop, deterministic (`watch.py`)

**Files:**
- Create: `src/inkvault/watch.py`
- Test: `tests/test_watch.py`

**Interfaces:**
- Consumes: Tasks 1–8
- Produces:
  - `Watcher(sampler, supervisor, conn, source, clock=time.monotonic, utc=screen.now)`, with `.tick() -> bool`
    (False = stop), `.heartbeat() -> dict`, `.core`, `.queue`, and counters `.busy_gaps`, `.stale_resets`,
    `.last_error`
  - `POLL = 2.0`, `HEARTBEAT_EVERY = 30.0`, `STOP_WAIT = 15.0`, `REOPEN_EVERY = 10.0`
  - `run() -> int` (lock, reopen-retry, loop)

- [ ] **Step 1: Write the failing tests**

```python
"""The watcher with a strict fake supervisor and a real vault (spec §1–§5)."""
import json
from datetime import datetime, timedelta, timezone

import pytest

T0 = datetime(2026, 10, 12, 14, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class FakeSupervisor:
    """The real Supervisor's contract: one request at a time, ids, abandon = unacceptable but still busy."""

    def __init__(self):
        self.requests, self.pending, self.answers = [], None, {}
        self.timeouts, self.cooldowns = 0, {}

    def busy(self):
        return self.pending is not None

    def cooling(self, exe):
        return False

    def submit(self, req):
        if self.pending:
            return None
        self.requests.append(req)
        self.pending = {"id": len(self.requests), "ok": True}
        return self.pending["id"]

    def answer(self, lines, status="ok"):
        self.answers[self.pending["id"]] = {"id": self.pending["id"], "status": status, "lines": lines,
                                            "colors": 30, "error": ""}

    def poll(self):
        if self.pending and self.pending["id"] in self.answers:
            p, self.pending = self.pending, None
            a = self.answers.pop(p["id"])
            return a if p["ok"] else None
        return None

    def abandon(self):
        if self.pending:
            self.pending["ok"] = False

    def close(self):
        pass


@pytest.fixture
def rig(tmp_path, monkeypatch):
    monkeypatch.setenv("INKVAULT_HOME", str(tmp_path))
    from inkvault import screen, screenstore, watch
    from inkvault.stretches import Identity, Sample
    screen.prepare()
    screen.edit(lambda d: d["text_apps"].append("Discord.exe"))
    conn = screenstore.connect()
    clock, sup = Clock(), FakeSupervisor()
    state = {"window": Identity(1, 10, 1, "Discord.exe", "Chat"), "idle": 0.0, "locked": False}

    def sampler():
        return Sample(clock.t, T0 + timedelta(seconds=clock.t), state["window"], state["idle"], state["locked"])

    w = watch.Watcher(sampler, sup, conn, screen.SettingsSource(), clock=clock,
                      utc=lambda: T0 + timedelta(seconds=clock.t))
    yield w, clock, sup, state, conn
    conn.close()


def ticks(w, clock, n, step=2.0):
    for _ in range(n):
        assert w.tick()
        clock.t += step


def rows(conn, table):
    return conn.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()


def read_once(w, clock, sup, lines):
    sup.answer(lines)
    ticks(w, clock, 1)


def test_text_is_accepted_and_stored(rig):
    w, clock, sup, state, conn = rig
    ticks(w, clock, 16)
    assert sup.requests and "title" not in sup.requests[0] and len(sup.requests[0]["title_sha256"]) == 64
    read_once(w, clock, sup, ["hello everyone in the channel"])
    assert rows(conn, "screen_text")[0][3] == "hello everyone in the channel"


def test_a_changed_window_at_acceptance_drops_the_result(rig):
    from inkvault.stretches import Identity
    w, clock, sup, state, conn = rig
    ticks(w, clock, 16)
    state["window"] = Identity(1, 10, 1, "Discord.exe", "Other chat")
    read_once(w, clock, sup, ["secret from the other chat"])
    assert rows(conn, "screen_text") == []


def test_same_text_once_per_stretch_but_again_in_a_new_stretch(rig):           # P6
    from inkvault.stretches import Identity
    w, clock, sup, state, conn = rig
    for _ in range(2):
        ticks(w, clock, 61)
        if sup.pending:
            read_once(w, clock, sup, ["the same text on screen again"])
    assert len(rows(conn, "screen_text")) == 1
    state["window"] = Identity(2, 20, 2, "Notion.exe", "B"); ticks(w, clock, 3)            # A -> B -> A
    state["window"] = Identity(1, 10, 1, "Discord.exe", "Chat"); ticks(w, clock, 61)
    if sup.pending:
        read_once(w, clock, sup, ["the same text on screen again"])
    assert len(rows(conn, "screen_text")) == 2


def test_hash_counts_only_after_the_insert_commits(rig, monkeypatch):         # P6
    from inkvault import screenstore
    w, clock, sup, state, conn = rig
    ticks(w, clock, 16)
    real, fails = screenstore.apply, {"n": 0}

    def flaky(c, wr):
        if wr.kind == "text" and fails["n"] == 0:
            fails["n"] += 1
            raise screenstore.StoreError("OperationalError: disk I/O error")
        return real(c, wr)
    monkeypatch.setattr(screenstore, "apply", flaky)
    read_once(w, clock, sup, ["text that fails to write the first time"])
    assert rows(conn, "screen_text") == [] and "disk I/O" in w.last_error
    ticks(w, clock, 1)                                                              # retried from the queue
    assert len(rows(conn, "screen_text")) == 1


def test_pause_drops_queued_text(rig):                                          # P3: real busy DB
    from inkvault import export, screen
    w, clock, sup, state, conn = rig
    ticks(w, clock, 16)
    blocker = export.open_vault(); blocker.execute("BEGIN IMMEDIATE")
    try:
        read_once(w, clock, sup, ["queued while the vault is busy"])
        assert w.queue.text_bytes > 0
        screen.edit(lambda d: d.update(paused_until=screen.iso(T0 + timedelta(hours=2))))
        ticks(w, clock, 1)
        assert w.queue.text_bytes == 0
    finally:
        blocker.rollback(); blocker.close()
    ticks(w, clock, 2)
    assert rows(conn, "screen_text") == []


def test_a_change_seen_at_acceptance_empties_the_backlog_before_the_flush(rig):  # R1: a real mid-tick barrier
    from inkvault import export, screen
    w, clock, sup, state, conn = rig
    ticks(w, clock, 16)                                         # first read submitted at t = 30
    blocker = export.open_vault(); blocker.execute("BEGIN IMMEDIATE")
    try:
        read_once(w, clock, sup, ["older text queued while the vault is busy"])
        assert w.queue.text_bytes > 0
        ticks(w, clock, 60)                                     # past the 120 s budget: a second read is pending
        assert sup.busy()
    finally:
        blocker.rollback(); blocker.close()
    real, calls = w.source.poll, {"n": 0}

    def second_load_sees_a_pause():
        calls["n"] += 1
        if calls["n"] == 2:                                     # inside the same tick, at acceptance
            screen.edit(lambda d: d.update(paused_until=screen.iso(T0 + timedelta(hours=5))))
        return real()
    w.source.poll = second_load_sees_a_pause
    sup.answer(["newer text read just before the pause"])
    ticks(w, clock, 1)
    assert w.queue.text_bytes == 0 and rows(conn, "screen_text") == []


def test_an_edit_between_the_tick_and_acceptance_drops_the_result(rig):        # P3
    from inkvault import screen
    w, clock, sup, state, conn = rig
    ticks(w, clock, 16)
    screen.edit(lambda d: d["never_titles"].append("chat"))
    read_once(w, clock, sup, ["text that must not be kept anymore"])
    assert rows(conn, "screen_text") == []


@pytest.mark.parametrize("content", ["{nope", '{"text_apps": [], "never_apps": [], "never_titles": [], '
                                              '"paused_until": 5}'])
def test_invalid_settings_fail_closed_without_crashing(rig, content):          # P3
    from inkvault import paths
    w, clock, sup, state, conn = rig
    ticks(w, clock, 5)
    paths.screen_file().write_text(content, encoding="utf-8")
    ticks(w, clock, 10)
    assert w.core.open is None and any(s in (w.last_error or "") for s in ("screen.json", "paused_until"))


def test_a_removal_resets_the_open_stretch_and_drops_inflight(rig):
    from inkvault import export
    w, clock, sup, state, conn = rig
    ticks(w, clock, 16)
    v = export.open_vault()
    v.execute("DELETE FROM screen_stretches")
    v.execute("INSERT OR REPLACE INTO meta VALUES ('screen_removal_gen', '1')"); v.commit(); v.close()
    sup.answer(["text read before the removal finished"])
    ticks(w, clock, 20)
    assert rows(conn, "screen_text") == [] and w.stale_resets >= 1 and len(rows(conn, "screen_stretches")) == 1


def test_stop_file(rig):
    from inkvault import paths
    w, clock, sup, state, conn = rig
    ticks(w, clock, 5)
    paths.watch_stop().write_text("stop")
    assert w.tick() is False and not paths.watch_stop().exists() and w.core.open is None


def test_heartbeat_has_no_titles_and_survives_write_failure(rig, monkeypatch):    # P11
    from inkvault import screen
    w, clock, sup, state, conn = rig
    ticks(w, clock, 20)
    assert "Chat" not in json.dumps(w.heartbeat()) and "Discord.exe" in json.dumps(w.heartbeat())
    monkeypatch.setattr(screen, "write_atomic", lambda *a: (_ for _ in ()).throw(PermissionError("denied")))
    ticks(w, clock, 20)
    assert "heartbeat" in w.last_error


def test_a_restart_never_extends_the_old_row(rig):
    from inkvault import screen, watch
    w, clock, sup, state, conn = rig
    ticks(w, clock, 20)
    before = rows(conn, "screen_stretches")
    w2 = watch.Watcher(w.sampler, FakeSupervisor(), conn, screen.SettingsSource(), clock=clock, utc=w.utc)
    ticks(w2, clock, 20)
    after = rows(conn, "screen_stretches")
    assert before[0] in after and len(after) == 2
```

- [ ] **Step 2: Run to see them fail**

Run: `uv run pytest tests/test_watch.py -q`
Expected: FAIL (`No module named 'inkvault.watch'`).

- [ ] **Step 3: Implement `src/inkvault/watch.py`**

```python
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
```

`text` writes carry `app` so a test can tell them apart. `Write` has no `rule_title`: queued text is dropped whole on
any settings change, so it's never re-checked.

- [ ] **Step 4: Run to see them pass**

Run: `uv run pytest tests/test_watch.py -q`, then `uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/inkvault/watch.py tests/test_watch.py
git commit -m "Screen capture: the watcher loop (reload before accepting, per-stretch dedupe after commit, fail closed)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: The watcher with a real supervisor and real contention

**Files:**
- Test: `tests/test_watch_integration.py`
- Modify: `src/inkvault/watch.py` only if a test exposes a defect

**Interfaces:**
- Consumes: `watch.Watcher`, `watch.open_store`, `worker.Supervisor`, `tests/fakeworker.py`

- [ ] **Step 1: Write the tests**

```python
"""The real supervisor and real SQLite contention under the watcher (P12): abandoned hangs still die; the loop
keeps ticking; more than 50 window changes under contention stay within caps and budgets."""
import pathlib
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

FAKE = str(pathlib.Path(__file__).with_name("fakeworker.py"))
T0 = datetime(2026, 10, 12, 14, 0, tzinfo=timezone.utc)


@pytest.fixture
def rig(tmp_path, monkeypatch):
    monkeypatch.setenv("INKVAULT_HOME", str(tmp_path))
    from inkvault import screen, screenstore, watch, worker
    from inkvault.stretches import Identity, Sample
    screen.prepare()
    screen.edit(lambda d: d["text_apps"].append("Discord.exe"))
    conn = screenstore.connect()
    clock = {"t": 0.0}
    state = {"window": Identity(1, 10, 1, "Discord.exe", "Chat")}

    def make(mode, timeout=0.5):
        sup = worker.Supervisor([sys.executable, FAKE, mode], timeout=timeout)
        sampler = lambda: Sample(clock["t"], T0 + timedelta(seconds=clock["t"]), state["window"], 0.0, False)
        return watch.Watcher(sampler, sup, conn, screen.SettingsSource(), clock=lambda: clock["t"],
                             utc=lambda: T0 + timedelta(seconds=clock["t"])), sup

    yield make, clock, state, conn
    conn.close()


def step(w, clock, n=1):
    for _ in range(n):
        t = time.monotonic()
        assert w.tick()
        assert time.monotonic() - t < 0.6                       # the loop never waits on the worker or the DB
        clock["t"] += 2.0


@pytest.mark.parametrize("then", ["pause", "removal", "invalid", "lost"])
def test_hang_then_pause_removal_or_broken_settings_still_kills_the_worker(rig, then):   # R3
    from inkvault import export, paths, screen
    make, clock, state, conn = rig
    w, sup = make("hang")
    step(w, clock, 16)
    proc = sup.proc
    assert sup.busy()
    if then == "pause":
        screen.edit(lambda d: d.update(paused_until=screen.iso(T0 + timedelta(hours=1))))
    elif then == "invalid":
        paths.screen_file().write_text("{nope", encoding="utf-8")
    elif then == "lost":
        paths.screen_file().unlink()
    else:
        v = export.open_vault()
        v.execute("INSERT OR REPLACE INTO meta VALUES ('screen_removal_gen', '1')"); v.commit(); v.close()
    end = time.monotonic() + 2.0
    while time.monotonic() < end:
        step(w, clock)
        time.sleep(0.05)
    assert proc.poll() is not None and not sup.busy()
    sup.close()


def test_many_window_changes_under_contention(rig):
    from inkvault import export
    from inkvault.stretches import Identity
    make, clock, state, conn = rig
    w, sup = make("ok")
    blocker = export.open_vault(); blocker.execute("BEGIN IMMEDIATE")
    try:
        for i in range(110):                                     # 55 stretches of 2 polls each
            state["window"] = Identity(100 + i // 2, 10, 1, "Notion.exe", f"Page {i // 2}")
            step(w, clock)
        assert len(w.queue) <= w.queue.max_stretches and w.busy_gaps > 0           # the actual cap (R8)
    finally:
        blocker.rollback(); blocker.close()
    for _ in range(10):
        step(w, clock)
    assert len(w.queue) <= 1
    ids = [r[0] for r in conn.execute("SELECT id FROM screen_stretches ORDER BY started")]
    assert len(ids) >= 49 and len(set(ids)) == len(ids)
    sup.close()


def test_the_vault_reopen_publishes_a_heartbeat_and_stops_promptly(tmp_path, monkeypatch):   # R8
    import json
    monkeypatch.setenv("INKVAULT_HOME", str(tmp_path))
    from inkvault import paths, screenstore, watch
    monkeypatch.setattr(screenstore, "connect", lambda: (_ for _ in ()).throw(screenstore.StoreError("disk")))
    start = time.monotonic()
    threading.Timer(0.3, lambda: paths.watch_stop().write_text("stop")).start()
    assert watch.open_store(lambda: paths.watch_stop().exists()) is None
    assert time.monotonic() - start < 1.2                                    # not the full 10 s retry wait
    hb = json.loads(paths.watch_heartbeat().read_text())
    assert hb["state"] == "waiting for the vault" and hb["db_error"] == "StoreError" and hb["attempts"] >= 1
```

- [ ] **Step 2: Run them**

Run: `uv run pytest tests/test_watch_integration.py -q`
Expected: PASS. If one fails, fix `watch.py` or `worker.py` (never the test's contract) and say so in the ledger.

- [ ] **Step 3: Commit**

```bash
git add tests/test_watch_integration.py src/inkvault/watch.py src/inkvault/worker.py
git commit -m "Screen capture: integration tests with a real supervisor and real contention

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: Forgetting screen data (`forget.py`)

**Files:**
- Modify: `src/inkvault/forget.py` (`matcher`, new functions, `apply_pending`)
- Test: `tests/test_forget_screen.py`

**Interfaces:**
- Consumes: `screen.overlap_sql`, `screen.parse`, `forget.chunks`, `forget.delete_digests`, `export.open_vault`
- Produces:
  - `SCREEN_KINDS = ("screen-app", "screen-range")`
  - `screen_days(spans) -> set[str]`
  - `screen_preview(what, value) -> (n, lo, hi)` and `screen_request(what, value) -> (n, lo, hi)`
  - `matcher` raises `ValueError` for unknown kinds; `apply_pending()` handles screen kinds by recorded targets
  - `screen-range` values are `json.dumps({"since": utc_iso, "until": utc_iso})` (canonical UTC only)

- [ ] **Step 1: Write the failing tests**

```python
"""Forgetting screen data (spec §8, R3, R6): whole overlapping stretches; recorded targets; UTC days ±1."""
import json

import pytest

S = lambda d, h: f"2026-10-{d:02d}T{h:02d}:00:00.000000Z"


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setenv("INKVAULT_HOME", str(tmp_path))
    from inkvault import export
    v = export.open_vault()
    v.executemany("INSERT INTO screen_stretches VALUES (?,?,?,?,?)", [
        ("screen:a", S(11, 9), S(11, 11), "Discord.exe", "Chat"),
        ("screen:b", S(11, 23), S(12, 1), "Notion.exe", "Plan"),
        ("screen:c", S(12, 5), S(12, 5), "Discord.exe", "Zero"),
    ])
    v.execute("INSERT INTO screen_text VALUES ('screentext:1','screen:a',?,'hello there everyone','h')", (S(11, 10),))
    v.commit()
    yield v
    v.close()


def rng(since, until):
    return json.dumps({"since": since, "until": until})


def test_range_inside_one_stretch_removes_it_whole(vault):
    from inkvault import forget
    n, lo, hi = forget.screen_request("screen-range", rng(S(11, 10), "2026-10-11T10:30:00.000000Z"))
    assert (n, lo, hi) == (1, S(11, 9), S(11, 11)) and forget.apply_pending() == 1
    assert [r[0] for r in vault.execute("SELECT id FROM screen_stretches ORDER BY id")] == ["screen:b", "screen:c"]
    assert vault.execute("SELECT COUNT(*) FROM screen_text").fetchone()[0] == 0


def test_edges_match_timeline(vault):
    from inkvault import forget
    assert forget.screen_request("screen-range", rng(S(12, 5), S(12, 6)))[0] == 1


def test_app_removal_records_targets_and_bumps_the_generation(vault):
    from inkvault import forget, screen
    assert forget.screen_request("screen-app", "discord.exe")[0] == 2 and screen.removal_gen(vault) == 1
    assert {r[0] for r in vault.execute("SELECT stretch_id FROM screen_removal_targets")} == {"screen:a", "screen:c"}


def test_a_watcher_write_after_the_request_survives_a_delayed_apply(vault):    # R3 with the real writer
    from inkvault import forget, screenstore
    forget.screen_request("screen-app", "Discord.exe")
    c = screenstore.connect()
    try:
        screenstore.apply(c, screenstore.Write("open", "screen:new", 1, started=S(13, 9), ended=S(13, 9),
                                               app="Discord.exe", title="Later"))
    finally:
        c.close()
    forget.apply_pending()                                              # e.g. the next day
    assert [r[0] for r in vault.execute("SELECT id FROM screen_stretches WHERE app='Discord.exe'")] == ["screen:new"]


def test_an_interrupted_request_records_nothing(vault, monkeypatch):
    from inkvault import forget, screen
    real = forget.screen_days
    monkeypatch.setattr(forget, "screen_days", lambda spans: (_ for _ in ()).throw(RuntimeError("stop")))
    with pytest.raises(RuntimeError):
        forget.screen_request("screen-app", "Discord.exe")
    assert screen.removal_gen(vault) == 0
    assert vault.execute("SELECT COUNT(*) FROM screen_removal_targets").fetchone()[0] == 0
    assert vault.execute("SELECT COUNT(*) FROM browser_removals").fetchone()[0] == 0


def test_days_cover_every_utc_day_plus_neighbors():
    from inkvault import forget
    assert forget.screen_days([(S(11, 23), S(12, 1))]) == {"2026-10-10", "2026-10-11", "2026-10-12", "2026-10-13"}


def test_nothing_matched_records_nothing(vault):
    from inkvault import forget, paths, screen
    assert forget.screen_request("screen-app", "Nope.exe") == (0, None, None)
    assert screen.removal_gen(vault) == 0 and not paths.rebuild_marker().exists()


def test_unknown_kinds_are_errors_and_sites_still_work():
    from inkvault import forget
    with pytest.raises(ValueError):
        forget.matcher("screen-everything", "x")
    pick = forget.matcher("site", "bank.example")
    assert pick("Default", "https://login.bank.example/") and not pick("Default", "https://docs.example.com/")
```

- [ ] **Step 2: Run to see them fail**

Run: `uv run pytest tests/test_forget_screen.py -q`
Expected: FAIL (`… has no attribute 'screen_request'`).

- [ ] **Step 3: Implement (in `src/inkvault/forget.py`)**

Replace `matcher`:

```python
def matcher(what, value):
    if what == "profile":
        return lambda profile, url: profile == value
    if what == "site":
        return lambda profile, url: history.skipped(url, [value])
    raise ValueError(f"unknown removal kind {what!r}")
```

Add after `digest_days`:

```python
SCREEN_KINDS = ("screen-app", "screen-range")


def screen_days(spans):
    """Every UTC date a stretch touches, ±1 day: the superset digest_days uses (spec §8, R6)."""
    from . import screen
    days = set()
    for started, ended in spans:
        day, last = screen.parse(started).date(), screen.parse(ended).date()
        while day <= last:
            days |= {(day + timedelta(d)).isoformat() for d in (-1, 0, 1)}
            day += timedelta(1)
    return days


def screen_select(db, what, value):
    from . import screen
    if what == "screen-app":
        return db.execute("SELECT id, started, ended FROM screen_stretches WHERE lower(app) = lower(?)",
                          (value,)).fetchall()
    if what == "screen-range":
        r = json.loads(value)
        return db.execute("SELECT id, started, ended FROM screen_stretches WHERE 1=1" + screen.overlap_sql(),
                          (r["until"], r["since"])).fetchall()
    raise ValueError(f"unknown removal kind {what!r}")


def span(found):
    return (min(f[1] for f in found), max(f[2] for f in found)) if found else (None, None)


def screen_preview(what, value):
    if not paths.vault_db().exists():
        return 0, None, None
    db = paths.connect_ro(paths.vault_db())
    try:
        found = screen_select(db, what, value)
    finally:
        db.close()
    return (len(found), *span(found))


def screen_request(what, value):
    """Select, record the exact targets and their days, and bump the removal generation, in one BEGIN IMMEDIATE:
    the watcher's writes take the same lock, so nothing grows or appears in between (R3)."""
    if not paths.vault_db().exists():
        return 0, None, None
    vault = export.open_vault()
    vault.commit()
    vault.isolation_level = None
    try:
        vault.execute("BEGIN IMMEDIATE")
        found = screen_select(vault, what, value)
        if not found:
            vault.execute("ROLLBACK")
            return 0, None, None
        days = sorted(screen_days([(f[1], f[2]) for f in found]))
        paths.rebuild_marker().write_text(f"removing {what}\n", encoding="utf-8")
        paths.dashboard().unlink(missing_ok=True)
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        rid = vault.execute("INSERT INTO browser_removals (what, value, days, created) VALUES (?,?,?,?)",
                            (what, value, json.dumps(days), now)).lastrowid
        vault.executemany("INSERT INTO screen_removal_targets VALUES (?,?)", ((rid, f[0]) for f in found))
        vault.execute("INSERT OR REPLACE INTO meta VALUES ('screen_removal_gen', CAST(coalesce((SELECT value FROM "
                      "meta WHERE key='screen_removal_gen'), 0) + 1 AS TEXT))")
        vault.execute("COMMIT")
        return (len(found), *span(found))
    except BaseException:
        if vault.in_transaction:
            vault.execute("ROLLBACK")
        raise
    finally:
        vault.close()


def delete_screen(vault, rid):
    ids = [r[0] for r in vault.execute("SELECT stretch_id FROM screen_removal_targets WHERE removal_id=?", (rid,))]
    for part in chunks(ids):
        marks = ",".join("?" * len(part))
        vault.execute(f"DELETE FROM screen_text WHERE stretch_id IN ({marks})", part)
        vault.execute(f"DELETE FROM screen_stretches WHERE id IN ({marks})", part)
    vault.execute("DELETE FROM screen_removal_targets WHERE removal_id=?", (rid,))
```

In `apply_pending()`, at the top of the loop over `pending_rows`:

```python
        for rid, what, value, days in pending_rows:
            if what in SCREEN_KINDS:
                delete_digests(json.loads(days))
                delete_screen(vault, rid)
                vault.execute("DELETE FROM browser_removals WHERE id=?", (rid,))
                vault.commit()
                continue
            # … the existing browser code, unchanged …
```

The days are computed before the marker is written, so an interrupted request leaves no marker either.

- [ ] **Step 4: Run to see them pass, plus the browser forget tests**

Run: `uv run pytest tests/test_forget_screen.py tests/test_forget.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/inkvault/forget.py tests/test_forget_screen.py
git commit -m "Screen capture: forgetting by app or range, with recorded targets and a generation bump

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 13: Index: one read snapshot, screen rows, cutoff (`index.py`)

**Files:**
- Modify: `src/inkvault/index.py` (`build()`)
- Test: `tests/test_index_screen.py`

**Interfaces:**
- Produces in `search.db`: `events.source`, `events.stretch_id`; `screen_stretches`, `stretches_fts`;
  `meta('screen_cutoff')`

- [ ] **Step 1: Write the failing tests**

```python
"""Search build with screen data (spec §6, F7)."""
import sqlite3

import numpy as np
import pytest


class FakeModel:
    def encode(self, texts, **_):
        return np.ones((len(texts), 4), dtype=np.float32)


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setenv("INKVAULT_HOME", str(tmp_path))
    from inkvault import embed, export
    monkeypatch.setattr(embed, "load_model", FakeModel)
    v = export.open_vault()
    v.execute("INSERT INTO screen_stretches VALUES ('screen:a','2026-10-12T09:00:00.000000Z',"
              "'2026-10-12T09:30:00.000000Z','Discord.exe','Retry chat')")
    v.execute("INSERT INTO screen_stretches VALUES ('screen:b','2026-10-12T10:00:00.000000Z',"
              "'2026-10-12T10:05:00.000000Z','chrome.exe','Docs page')")
    v.execute("INSERT INTO screen_text VALUES ('screentext:1','screen:a','2026-10-12T09:01:00.000000Z',"
              "'exponential backoff helper discussion','h')")
    v.commit()
    yield v
    v.close()


def test_screen_rows_land_in_search(vault):
    from inkvault import index, paths
    index.build()
    db = sqlite3.connect(paths.search_db())
    assert db.execute("SELECT source, stretch_id, app, window_title FROM events WHERE id='screentext:1'"
                      ).fetchone() == ("screen", "screen:a", "Discord.exe", "Retry chat")
    assert db.execute("SELECT s.id FROM screen_stretches s JOIN stretches_fts f ON f.rowid = s.rowid "
                      "WHERE stretches_fts MATCH 'docs'").fetchone() == ("screen:b",)
    assert dict(db.execute("SELECT key, value FROM meta"))["screen_cutoff"] == "2026-10-12T10:05:00.000000Z"
    assert db.execute("SELECT COUNT(*) FROM events WHERE source IS NULL").fetchone()[0] == 0


def test_a_commit_during_build_is_wholly_out(vault, monkeypatch):
    from inkvault import export, history, index, paths
    real = history.index_into

    def during(raw, db):
        w = export.open_vault()
        w.execute("INSERT INTO screen_stretches VALUES ('screen:late','2026-10-12T11:00:00.000000Z',"
                  "'2026-10-12T11:00:02.000000Z','Notion.exe','Late')")
        w.execute("INSERT INTO screen_text VALUES ('screentext:late','screen:late','2026-10-12T11:00:01.000000Z',"
                  "'text written while the index was building','h')")
        w.commit(); w.close()
        return real(raw, db)
    monkeypatch.setattr(history, "index_into", during)
    index.build()
    db = sqlite3.connect(paths.search_db())
    assert db.execute("SELECT 1 FROM screen_stretches WHERE id='screen:late'").fetchone() is None
    assert db.execute("SELECT 1 FROM events WHERE id='screentext:late'").fetchone() is None
```

- [ ] **Step 2: Run to see them fail**

Run: `uv run pytest tests/test_index_screen.py -q`
Expected: FAIL (`no such column: source`).

- [ ] **Step 3: Implement (in `index.build()`)**

1. Right after `raw = paths.connect_ro(paths.vault_db())` and its callback, add
   `raw.execute("BEGIN")  # one read snapshot for every vault query below (F7); closing raw ends it, before
   embed.build()`.
2. In the `CREATE TABLE events …` line, add the columns `source TEXT, stretch_id TEXT`. Add
   `CREATE TABLE screen_stretches (id TEXT PRIMARY KEY, started TEXT, ended TEXT, app TEXT, title TEXT);` to the
   same script.
3. Change the events copy to `"INSERT INTO events VALUES (?,?,?,?,?,?,'pieces',NULL)"`.
4. Make sure `history.index_into(raw, db)` runs **before** the screen copy, then add right after it:

```python
        # Screen capture (spec §6): OCR rows are events; stretches have their own table and FTS.
        if raw.execute("SELECT 1 FROM sqlite_master WHERE name='screen_stretches'").fetchone():
            db.executemany("INSERT INTO screen_stretches VALUES (?,?,?,?,?)",
                           raw.execute("SELECT id, started, ended, app, title FROM screen_stretches"))
            db.executemany("INSERT INTO events VALUES (?,?,?,?,NULL,?,'screen',?)", raw.execute(
                "SELECT t.id, t.created, s.app, s.title, t.text, s.id FROM screen_text t "
                "JOIN screen_stretches s ON s.id = t.stretch_id"))
            if cutoff := raw.execute("SELECT MAX(ended) FROM screen_stretches").fetchone()[0]:
                db.execute("INSERT INTO meta VALUES ('screen_cutoff', ?)", (cutoff,))
```

5. In the FTS/index script, add:

```sql
            CREATE VIRTUAL TABLE stretches_fts USING fts5(title, app, content='screen_stretches', content_rowid='rowid', tokenize='porter unicode61');
            INSERT INTO stretches_fts(rowid, title, app) SELECT rowid, title, app FROM screen_stretches;
            CREATE INDEX screen_stretches_started ON screen_stretches(started);
            CREATE INDEX screen_stretches_ended ON screen_stretches(ended);
            CREATE INDEX events_source ON events(source);
```

`embed.QUERIES["events"]` already embeds every event, so screen OCR is meaning-searchable. Stretches aren't
embedded.

- [ ] **Step 4: Run to see them pass, then the whole suite**

Run: `uv run pytest tests/test_index_screen.py -q`, then `uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/inkvault/index.py tests/test_index_screen.py
git commit -m "Screen capture: index screen rows in one read snapshot; stretches_fts; screen_cutoff

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 14: The MCP server (`server.py`)

**Files:**
- Modify: `src/inkvault/server.py`
- Test: `tests/test_server_screen.py`

**Interfaces:**
- Consumes: `screen.bounds`, `screen.overlap_sql`, `screen.removal_gen`, the Task 13 layout, the vault screen tables
- Produces:
  - `SOURCES["screen"]`
  - `published()` now includes the vault's removal generation
  - `screen_lines(since, until, limit) -> list[str]`; `screen_memory(id, max_chars) -> str`
  - `TIMELINE_MAX = 500`, `STRETCH_TEXTS = 20`

- [ ] **Step 1: Write the failing tests**

```python
"""MCP tools over screen data (spec §6, F2, R7, P8, P9)."""
import numpy as np
import pytest

S = lambda h, m=0: f"2026-10-12T{h:02d}:{m:02d}:00.000000Z"


class FakeModel:
    def encode(self, texts, **_):
        return np.ones((len(texts), 4), dtype=np.float32)


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setenv("INKVAULT_HOME", str(tmp_path))
    from inkvault import embed, export, server
    monkeypatch.setattr(embed, "load_model", FakeModel)
    monkeypatch.setattr(server, "_model", None)
    v = export.open_vault()
    v.executemany("INSERT INTO screen_stretches VALUES (?,?,?,?,?)", [
        ("screen:a", S(9), S(9, 30), "Discord.exe", "Retry chat"),
        ("screen:b", S(10), S(10, 5), "chrome.exe", "Docs page"),
    ])
    v.executemany("INSERT INTO screen_text VALUES (?,?,?,?,?)",
                  [(f"screentext:{i}", "screen:a", S(9, i), f"backoff note number {i:02d} in the chat", str(i))
                   for i in range(25)])
    v.commit()
    yield v
    v.close()


def test_timeline_screen_section_without_any_index(vault):
    from inkvault import paths, server
    assert not paths.search_db().exists()
    out = server.timeline("2026-10-12")
    assert "Screen" in out and "[screen] Discord.exe: Retry chat" in out and "[screen] chrome.exe: Docs page" in out


def test_screen_section_survives_many_older_items(vault):                        # P8
    import sqlite3
    from inkvault import paths, server
    s = sqlite3.connect(paths.search_db())                       # a hand-made index with 80 older summaries
    s.execute("CREATE TABLE summaries (id TEXT PRIMARY KEY, created TEXT, name TEXT, text TEXT, event_ids TEXT)")
    s.executemany("INSERT INTO summaries VALUES (?,?,?,?,?)",
                  [(f"s{i}", f"2026-10-12T00:{i % 60:02d}:00Z", "old", "x", "[]") for i in range(80)])
    s.commit(); s.close()
    out = server.timeline("2026-10-12", limit=10)
    assert "[screen] chrome.exe: Docs page" in out and out.count("old") == 10


def test_limits_and_ranges_are_validated(vault):
    from inkvault import server
    assert "limit" in server.timeline("2026-10-12", limit=0)
    assert "Bad range" in server.timeline("2026-10-12", until="2026-10-11")
    assert "Bad range" in server.search_memories("docs", since="2026-10-12", until="2026-10-11")


def test_screentext_id_returns_that_row_and_stretch_ids_are_bounded(vault):     # P8
    from inkvault import server
    one = server.get_memory("screentext:3")
    assert "backoff note number 03" in one and "Retry chat" in one and "number 04" not in one
    many = server.get_memory("screen:a")
    assert "number 24" in many and "number 04" not in many and "5 more" in many


def test_a_vault_without_screen_tables(tmp_path, monkeypatch):
    import sqlite3
    monkeypatch.setenv("INKVAULT_HOME", str(tmp_path))
    from inkvault import paths, server
    sqlite3.connect(paths.vault_db()).execute("CREATE TABLE meta (key, value)")
    assert "No memory" in server.get_memory("screen:x") and "Screen" not in server.timeline("2026-10-12")


def test_a_removal_during_a_live_read_is_never_answered(vault, monkeypatch):    # P12
    from inkvault import forget, server
    real = server.screen_lines

    def racing(*a):
        out = real(*a)
        from inkvault import paths
        forget.screen_request("screen-app", "chrome.exe")      # a removal starts during the read…
        paths.rebuild_marker().unlink(missing_ok=True)         # …and its rebuild finishes before the answer
        return out
    monkeypatch.setattr(server, "screen_lines", racing)
    assert server.timeline("2026-10-12") == server.CHANGED   # only the removal generation can catch this


def test_search_screen_keyword_meaning_and_one_hit_per_stretch(vault):
    from inkvault import index, server
    vault.execute("UPDATE screen_stretches SET title='Retry backoff' WHERE id='screen:a'"); vault.commit()
    index.build()
    out = server.search_memories("docs", source="screen", mode="keyword")
    assert "[screen] screen:b" in out and "search covers screen activity up to" in out
    assert "keyword-only" in server.search_memories("docs", source="screen", mode="meaning")
    hits = [l for l in server.search_memories("backoff", mode="keyword", limit=50).splitlines() if l.startswith("[")]
    assert not any(l.startswith("[screen] screen:a") for l in hits)              # the OCR rows win
```

- [ ] **Step 2: Run to see them fail**

Run: `uv run pytest tests/test_server_screen.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement**

1. Imports: `from . import browsers, embed, paths, screen, sources`. Constants: `TIMELINE_MAX = 500`,
   `STRETCH_TEXTS = 20`.

2. `published()`: add the vault's removal generation, so a removal that starts and completes during a live read
   is caught in homes without an index too:

```python
def removal_gen():
    """0 only when there's genuinely no generation (no vault, or a vault without a meta table); None when the
    vault can't be read, which the guard turns into an explicit refusal (R8)."""
    if not paths.vault_db().exists():
        return 0
    try:
        v = paths.connect_ro(paths.vault_db())
    except sqlite3.Error:
        return None
    try:
        if not has_table(v, "meta"):
            return 0
        return screen.removal_gen(v)
    except sqlite3.Error:
        return None
    finally:
        v.close()


def published():
    """What a tool reads from: whether a removal is under way, which search files are published, and the screen
    removal generation (a removal that completes during a live read changes it)."""
    def stamp(p):
        try:
            st = p.stat()
        except FileNotFoundError:
            return None
        return st.st_mtime_ns, st.st_size
    return paths.rebuild_marker().exists(), stamp(paths.search_db()), stamp(paths.vectors()), removal_gen()
```

   In `guarded`, after each `published()` call: `if before[3] is None: return UNAVAILABLE` (and the same for
   `after`), with `UNAVAILABLE = "The vault can't be read right now; try again in a moment."` next to `REBUILDING`.
   Add a test: a `vault.db` that isn't a database makes `timeline` return `UNAVAILABLE`, and a vault without a
   `meta` table answers normally.

3. `SOURCES["screen"] = ("screen_stretches", "stretches_fts", "t.app || ': ' || coalesce(t.title, '')",
   "snippet(stretches_fts, 0, '[', ']', ' … ', 40)", "title")`.

4. `range_filter`:

```python
def range_filter(kind, since, until, params):
    if kind == "web":
        return web_filter(since, until, params)
    if kind == "screen":
        if not (since or until):
            return ""
        s, u = screen.bounds(since or "1970-01-01", until)
        params.extend([u, s])
        return screen.overlap_sql("t.started", "t.ended")
    return date_filter("t.created", since, until, params)
```

5. `search_memories`:
   - Right after the mode checks, validate bounds once, returning on the first error:

     ```python
     if since or until:
         try:
             screen.bounds(since or "1970-01-01", until)
         except ValueError as e:
             return f"Bad range: {e}"
     ```

   - Exclude `"screen"` from the kinds passed to `meaning_ranked`.
   - Replace the final lookup loop:

```python
        hits = []
        for kind, id_ in sorted(scores, key=scores.get, reverse=True):
            table, _, title, _, body = SOURCES[kind]
            created = "t.started" if kind == "screen" else "t.created"
            stretch = ("t.id" if kind == "screen" else
                       "t.stretch_id" if kind == "events" and has_column(conn, "events", "stretch_id") else "NULL")
            params = [id_]
            r = conn.execute(f"SELECT {created} AS created, {title} AS title, substr(t.{body}, 1, 300) AS preview, "
                             f"{stretch} AS stretch FROM {table} t WHERE t.id = ?"
                             + range_filter(kind, since, until, params), params).fetchone()
            if r:
                hits.append((kind, id_, r))
        ocr_stretches = {r["stretch"] for k, _, r in hits if k == "events" and r["stretch"]}
        out, seen = [], set()
        for kind, id_, r in hits:
            if r["stretch"]:
                if (kind == "screen" and r["stretch"] in ocr_stretches) or r["stretch"] in seen:
                    continue      # one hit per stretch; the OCR row wins (spec §6)
                seen.add(r["stretch"])
            snip = snippets.get((kind, id_)) or " ".join((r["preview"] or "").split())
            out.append(f"[{kind}] {id_}  {(r['created'] or '')[:16]}  {r['title'] or ''}\n    {snip}")
            if len(out) >= limit:
                break
        notes = []
        if mode == "meaning" and "screen" in kinds:
            notes.append("(screen stretches are keyword-only; they weren't searched by meaning)")
        if {"screen", "events"} & set(kinds) and has_table(conn, "meta") and (
                c := conn.execute("SELECT value FROM meta WHERE key='screen_cutoff'").fetchone()):
            notes.append(f"(search covers screen activity up to {local(c[0]):%Y-%m-%d %H:%M}; "
                         "timeline() shows it live)")
    return "\n".join([*out, *notes]) or f"No matches for {query!r} in {source}."
```

   with:

```python
def has_column(conn, table, column):
    return any(r[1] == column for r in conn.execute(f"PRAGMA table_info({table})"))
```

6. Live screen lines and the timeline:

```python
def screen_lines(since, until, limit):
    """The Screen section: stretches overlapping the range, newest first, read live from vault.db (spec §6)."""
    if not paths.vault_db().exists():
        return []
    s, u = screen.bounds(since, until)
    v = paths.connect_ro(paths.vault_db())
    try:
        v.execute("BEGIN")
        if not has_table(v, "screen_stretches"):
            return []
        rows = v.execute("SELECT id, started, ended, app, title FROM screen_stretches WHERE 1=1"
                         + screen.overlap_sql() + " ORDER BY started DESC LIMIT ?", (u, s, limit)).fetchall()
    finally:
        v.close()
    out = []
    for id_, started, ended, app, title in rows:
        a, b = local(started), local(ended)
        minutes = max(0, round((b - a).total_seconds() / 60))
        out.append(f"{id_}  {a:%Y-%m-%d %H:%M}–{b:%H:%M}  [screen] {app}: {title or ''} ({minutes} min)")
    return out
```

   In `timeline()`:
   - at the top: `if not 1 <= limit <= TIMELINE_MAX: return f"limit must be 1-{TIMELINE_MAX}."`;
   - wrap the existing `with db() as conn:` block in `if paths.search_db().exists():`;
   - after the existing `items[:limit]` line is built, append:

```python
    body = "\n".join(line for _, line in items[:limit])
    try:
        live = screen_lines(since, until, limit)
    except ValueError as e:
        return f"Bad range: {e}"
    if live:
        body = (body + "\n\n" if body else "") + "Screen (live, newest first):\n" + "\n".join(live)
    return body or "Nothing in that range."
```

   Docstring: "…and a Screen section: the windows you had open, from screen capture, live (including right now)."

7. `get_memory`: first line inside the function: `if id.startswith(("screen:", "screentext:")): return
   screen_memory(id, max_chars)`.

```python
def screen_memory(id_, max_chars):
    if not paths.vault_db().exists():
        return f"No memory with id {id_}"
    v = paths.connect_ro(paths.vault_db())
    try:
        v.execute("BEGIN")
        if not has_table(v, "screen_stretches"):
            return f"No memory with id {id_}"
        if id_.startswith("screentext:"):
            t = v.execute("SELECT stretch_id, created, text FROM screen_text WHERE id=?", (id_,)).fetchone()
            if not t:
                return f"No memory with id {id_}"
            sid, texts, more = t[0], [(t[1], t[2])], 0
        else:
            sid = id_
            texts = v.execute("SELECT created, text FROM screen_text WHERE stretch_id=? ORDER BY created DESC LIMIT ?",
                              (sid, STRETCH_TEXTS)).fetchall()
            more = v.execute("SELECT COUNT(*) FROM screen_text WHERE stretch_id=?", (sid,)).fetchone()[0] - len(texts)
        st = v.execute("SELECT started, ended, app, title FROM screen_stretches WHERE id=?", (sid,)).fetchone()
        if not st:
            return f"No memory with id {id_}"
    finally:
        v.close()
    body = (f"SCREEN  {local(st[0]):%Y-%m-%d %H:%M}–{local(st[1]):%H:%M}\napp: {st[2]}\nwindow: {st[3] or ''}\n\n"
            + ("\n\n".join(f"{local(c):%H:%M}\n{t}" for c, t in texts) if texts else "(timeline only: no text read)"))
    if more > 0:
        body += f"\n\n… {more} more text reads in this window; search finds them by their own ids."
    return body if len(body) <= max_chars else body[:max_chars] + f"\n\n… [truncated, {len(body)} chars total]"
```

   Note `"5 more"` in the test: 25 rows, 20 shown.

8. `mcp` instructions and the `search_memories` docstring: add the `"screen"` source (windows you had open, app and
   title; keyword-only), and say `events` also holds text read by screen capture.

- [ ] **Step 4: Run to see them pass, then the whole suite**

Run: `uv run pytest tests/test_server_screen.py -q`, then `uv run pytest -q`
Expected: PASS. The guard test's expected answer is `CHANGED` (the generation moved). Accept `REBUILDING` too if
the marker is still up.

- [ ] **Step 5: Commit**

```bash
git add src/inkvault/server.py tests/test_server_screen.py
git commit -m "Screen capture: live Screen timeline section and get_memory; screen search source; guarded by generation

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 15: Atlas and digests: Pieces-only counts, screen time by duration

**Files:**
- Create: `src/inkvault/screentime.py`
- Modify: `src/inkvault/dashboard.py` (`collect`), `src/inkvault/atlas.html`, `src/inkvault/digest.py` (`gather`)
- Test: `tests/test_screentime.py`

**Interfaces:**
- Produces:
  - `screentime.split(started, ended, tz=None) -> list[(local_day, local_hour, seconds)]` (steps in UTC, so it's
    daylight-saving safe)
  - `screentime.stretches(db)`, `screentime.pieces_events_sql(db) -> str`, `screentime.app_name(exe) -> str`,
    `screentime.fmt(seconds) -> str`
  - Dashboard data: `days[d]["screen_minutes"]` and `"screen_apps"`. Digest material: "Screen time by app",
    "Longest windows"

- [ ] **Step 1: Write the failing tests**

```python
"""Screen time by duration (spec §6, F8, P7): split by local hour stepping through UTC; Pieces-only counts;
screen-only digest days; no screen text in digests."""
import sqlite3

from zoneinfo import ZoneInfo

CHI = ZoneInfo("America/Chicago")
SCHEMA = """
    CREATE TABLE events (id, created, app, window_title, url, readable, source, stretch_id);
    CREATE TABLE summaries (id, created, name, text, event_ids);
    CREATE TABLE messages (id, created, conversation_id, conversation_name, role, text, source);
    CREATE TABLE snippets (id, created, name, language, text);
    CREATE TABLE summary_tags (summary_id, created, tag); CREATE TABLE meta (key, value);
    CREATE TABLE screen_stretches (id, started, ended, app, title);
"""


def test_split_across_the_fall_back_hour_keeps_elapsed_time():
    from inkvault import screentime
    parts = screentime.split("2026-11-01T05:30:00.000000Z", "2026-11-01T08:30:00.000000Z", tz=CHI)
    assert sum(p[2] for p in parts) == 3 * 3600
    assert sum(p[2] for p in parts if p[1] == 1) == 7200            # the repeated 1 o'clock hour, twice


def test_split_across_spring_forward_and_midnight():
    from inkvault import screentime
    parts = screentime.split("2026-03-08T07:30:00.000000Z", "2026-03-08T09:30:00.000000Z", tz=CHI)
    # 01:30–02:00 CST, then the clocks jump: 03:00–04:00 and 04:00–04:30 CDT (R5: the old {1, 3} was wrong)
    assert sum(p[2] for p in parts) == 7200 and [(p[1], p[2]) for p in parts] == [(1, 1800), (3, 3600), (4, 1800)]
    parts = screentime.split("2026-10-12T04:30:00.000000Z", "2026-10-12T05:45:00.000000Z", tz=CHI)
    assert [(p[0], p[1]) for p in parts] == [("2026-10-11", 23), ("2026-10-12", 0)]


def test_half_hour_offsets_label_by_local_hour():
    from inkvault import screentime
    parts = screentime.split("2026-10-12T04:00:00.000000Z", "2026-10-12T05:00:00.000000Z", tz=ZoneInfo("Asia/Kolkata"))
    assert [(p[1], p[2]) for p in parts] == [(9, 1800), (10, 1800)]          # 09:30–10:00, 10:00–10:30 IST


def test_a_full_mixed_day_keeps_its_screen_summary(tmp_path, monkeypatch):   # R5: budgets reserved together
    monkeypatch.setenv("INKVAULT_HOME", str(tmp_path))
    from inkvault import digest, screen
    monkeypatch.setattr(screen, "LOCAL_TZ", CHI)
    db = sqlite3.connect(":memory:")
    db.executescript(SCHEMA + """
        CREATE TABLE pages (id, day, url, title, host, path, profiles, visits);
        CREATE TABLE visits (id, page_id, created, profile, host, synced);
        INSERT INTO screen_stretches VALUES ('screen:a','2026-10-12T17:00:00.000000Z','2026-10-12T18:10:00.000000Z','Discord.exe','Chat');
    """)
    db.executemany("INSERT INTO summaries VALUES (?,?,?,?,?)",
                   [(f"s{i}", "2026-10-12T17:30:00Z", "x" * 300 + str(i), "", "[]") for i in range(200)])
    db.executemany("INSERT INTO pages VALUES (?,?,?,?,?,?,?,?)",
                   [(f"p{i}", "2026-10-12", f"https://h{i}.example/", "t" * 80 + str(i), f"h{i}.example", "/", "", 1)
                    for i in range(40)])
    db.executemany("INSERT INTO visits VALUES (?,?,?,?,?,?)",
                   [(f"v{i}", f"p{i}", "2026-10-12T17:40:00Z", "p", f"h{i}.example", 0) for i in range(40)])
    text = digest.gather(db)["2026-10-12"]
    assert len(text) <= digest.MAX_INPUT and "Screen time by app: Discord 1h 10m" in text and "Pages opened" in text


def test_old_index_and_formatting():
    from inkvault import screentime
    old = sqlite3.connect(":memory:"); old.execute("CREATE TABLE events (id, created, app)")
    assert screentime.pieces_events_sql(old) == "" and screentime.stretches(old) == []
    assert screentime.fmt(4200) == "1h 10m" and screentime.fmt(59) == "0m" and screentime.app_name("Code.EXE") == "Code"


def test_dashboard_counts_pieces_only_and_screen_minutes(tmp_path, monkeypatch):
    monkeypatch.setenv("INKVAULT_HOME", str(tmp_path))
    from inkvault import dashboard, screen
    monkeypatch.setattr(screen, "LOCAL_TZ", CHI)            # same local day on any test machine
    db = sqlite3.connect(":memory:")
    db.executescript(SCHEMA + """
        INSERT INTO events VALUES ('e1','2026-10-12T15:00:00Z','Code.exe',NULL,NULL,NULL,'pieces',NULL);
        INSERT INTO events VALUES ('s1','2026-10-12T15:01:00Z','Discord.exe',NULL,NULL,'x','screen','screen:a');
        INSERT INTO screen_stretches VALUES ('screen:a','2026-10-12T15:00:00.000000Z','2026-10-12T15:30:00.000000Z','Discord.exe','t');
    """)
    data = dashboard.collect(db)    # add an empty table above for anything else collect() reads
    day = next(iter(data["days"]))
    assert data["days"][day]["captures"] == 1 and data["screen_apps"][day] == {"Discord": 30}


def test_digest_screen_only_day_by_duration_without_text(tmp_path, monkeypatch):
    monkeypatch.setenv("INKVAULT_HOME", str(tmp_path))
    from inkvault import digest, screen
    monkeypatch.setattr(screen, "LOCAL_TZ", CHI)            # 12:00–13:10 local: one day on any test machine
    db = sqlite3.connect(":memory:")
    db.executescript(SCHEMA + """
        INSERT INTO events VALUES ('s1','2026-10-12T17:01:00Z','Discord.exe','Chat',NULL,'SECRET OCR WORDS','screen','screen:a');
        INSERT INTO screen_stretches VALUES ('screen:a','2026-10-12T17:00:00.000000Z','2026-10-12T18:10:00.000000Z','Discord.exe','Chat');
    """)
    material = digest.gather(db)
    text = "\n".join(material.values())
    assert material and "Discord 1h 10m" in text and "Chat (1h 10m)" in text and "SECRET OCR WORDS" not in text
```

- [ ] **Step 2: Run to see them fail**

Run: `uv run pytest tests/test_screentime.py -q`
Expected: FAIL (`No module named 'inkvault.screentime'`).

- [ ] **Step 3: Implement**

`src/inkvault/screentime.py`:

```python
"""Screen time by duration (spec §6, F8, P7). A stretch's elapsed seconds are split across the local hours and days
it spans. The split steps through UTC with each instant's own fixed offset, so time across a daylight-saving change
is counted exactly once."""
import re
import sqlite3
from datetime import timedelta, timezone

from . import screen


def split(started, ended, tz=None):
    tz = tz if tz is not None else screen.LOCAL_TZ
    a, b, out = screen.parse(started), screen.parse(ended), []
    while a < b:
        la = a.astimezone(tz) if tz is not None else a.astimezone()
        fixed = la.astimezone(timezone(la.utcoffset()))
        boundary = (fixed.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)).astimezone(timezone.utc)
        nxt = min(b, boundary)
        out.append((la.date().isoformat(), la.hour, (nxt - a).total_seconds()))
        a = nxt
    return out


def has_column(db, table, column):
    try:
        return any(r[1] == column for r in db.execute(f"PRAGMA table_info({table})"))
    except sqlite3.Error:
        return False


def pieces_events_sql(db):
    return " AND source='pieces'" if has_column(db, "events", "source") else ""


def stretches(db):
    try:
        return db.execute("SELECT started, ended, app, title FROM screen_stretches").fetchall()
    except sqlite3.OperationalError:
        return []


def app_name(exe):
    return re.sub(r"\.exe$", "", exe, flags=re.I)


def fmt(seconds):
    s = int(seconds)
    return f"{s // 3600}h {s % 3600 // 60}m" if s >= 3600 else f"{s // 60}m"
```

`dashboard.collect`:
- Change the events query to `"SELECT created, app, url FROM events WHERE created IS NOT NULL" +
  screentime.pieces_events_sql(db)`.
- After the browser-visits loop:

```python
    screen_apps = defaultdict(Counter)
    for started, ended, app, _ in screentime.stretches(db):
        for day, hour, secs in screentime.split(started, ended):
            d = days[day]
            d["screen_minutes"] = d.get("screen_minutes", 0) + secs / 60
            d["hours"] |= 1 << hour
            screen_apps[day][screentime.app_name(app)] += secs / 60
```

- Before returning: `for d in days.values(): d["screen_minutes"] = round(d.get("screen_minutes", 0))`.
- Add to the returned dict: `"screen_apps": {d: {a: round(m) for a, m in c.items()} for d, c in
  screen_apps.items()},`.
- Add `from . import screentime`.

`atlas.html`:
- In the tiles loop, add `let screenMinutes = 0;` and `screenMinutes += d.screen_minutes || 0;`. Define:

  ```js
  const hoursMinutes = m => m >= 60 ? `${Math.floor(m / 60)}h ${Math.round(m % 60)}m` : `${Math.round(m)}m`;
  ```

  Add the tile `["Screen time", hoursMinutes(screenMinutes), "windows you had open, from screen capture"],` after
  "Screen captures".
- Change the Top apps description to "Pieces captures per app."
- Add a card copying the Top apps card's markup, with `data-table="screen_apps"`, title "Screen time by app",
  description "Minutes each app was in front, from screen capture." and chart `id="screen_apps"`.
- Add `renderBars("screen_apps", DATA.screen_apps || {}, t, "App");` next to the other `renderBars` calls.
- In the active-hours description, add "screen capture" to the list.

`digest.gather`:
- Append `+ screentime.pieces_events_sql(db)` to the events query.
- Before `material = {}`:

```python
    screen_apps, screen_windows = defaultdict(Counter), defaultdict(Counter)
    for started, ended, app, title in screentime.stretches(db):
        for day, _, secs in screentime.split(started, ended):
            screen_apps[day][screentime.app_name(app)] += secs
            if title:
                screen_windows[day][clip(title, 90)] += secs
```

- Change the day loop to `for day in sorted(set(sessions) | set(chats) | set(apps) | set(hosts) |
  set(screen_apps)):`.
- After the `web = "\n".join(web)[:WEB_SHARE]` line, **replace** the existing `if web:` block with one combined
  reservation (R5). The screen and web parts are reserved together before the base material is clipped, so
  neither can cut the other out:

```python
        screen_part = ""
        if screen_apps[day]:
            by_app = ", ".join(a + " " + screentime.fmt(s) for a, s in screen_apps[day].most_common(6))
            windows_ = "; ".join(w + " (" + screentime.fmt(s) + ")" for w, s in screen_windows[day].most_common(8))
            screen_part = ("Screen time by app: " + by_app + ("\nLongest windows: " + windows_ if windows_ else ""))
            screen_part = screen_part[:SCREEN_SHARE]
        tail = "\n".join(p for p in (screen_part, web) if p)
        if tail:  # a day with neither keeps exactly the material it had; with only web, the same as before
            text = (text[:MAX_INPUT - len(tail) - 1] + "\n" + tail) if text else tail
```

  With only `web`, this produces exactly what the old block did, so existing digests aren't rewritten.
  `SCREEN_SHARE + WEB_SHARE` must stay below `MAX_INPUT`; add an assert next to the constants.
- Add `SCREEN_SHARE = 700` next to `WEB_SHARE`.
- Add `from . import screentime`.
- Never read `events.readable` for screen rows.

- [ ] **Step 4: Run to see them pass, then the dashboard/digest suites**

Run: `uv run pytest tests/test_screentime.py tests/test_digest.py tests/test_pipeline.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/inkvault/screentime.py src/inkvault/dashboard.py src/inkvault/atlas.html src/inkvault/digest.py tests/test_screentime.py
git commit -m "Screen capture: Atlas and digests count Pieces only; screen time by duration, DST-safe

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 16: The sign-in task (`schedulers/windows.py`)

**Files:**
- Create: `tests/fixtures/nightly-task.xml` (from the **unchanged** v0.2.1 `render`, before any edit)
- Modify: `src/inkvault/schedulers/windows.py`
- Test: `tests/test_schedulers.py` (append)

**Interfaces:**
- Produces:
  - `WATCH_TASK = "InkVault Watch"`, `current_user()`, `render_logon(argv, user)`
  - `install(..., task=TASK)`, `query(task=TASK)`, `remove(task=TASK)`, `set_enabled(on, task=TASK)`
  - `install_logon(argv, user, task=WATCH_TASK)`, `run_task(task)`
  - `read_back(task) -> dict | None`: None only when the full task list confirms it's absent; raises `TaskUnknown`
    when its state can't be read (R4). `task_names() -> set[str]`

- [ ] **Step 1: Save the nightly XML fixture from the current code, before touching it**

```bash
mkdir -p tests/fixtures
uv run python -c "from inkvault.schedulers import windows; open('tests/fixtures/nightly-task.xml','w',encoding='utf-8',newline='').write(windows.render(['C:\\\\Program Files\\\\py\\\\python.exe','-m','inkvault','--home','C:\\\\v','nightly'], 3, 0, False))"
git add tests/fixtures/nightly-task.xml
```

- [ ] **Step 2: Write the failing tests** (append to `tests/test_schedulers.py`)

```python
import pathlib

FIX = pathlib.Path(__file__).with_name("fixtures") / "nightly-task.xml"
ARGV = ["C:\\Program Files\\py\\python.exe", "-m", "inkvault", "--home", "C:\\v", "nightly"]


def test_nightly_xml_is_byte_identical_to_the_v021_fixture():
    from inkvault.schedulers import windows
    assert windows.render(ARGV, 3, 0, False) == FIX.read_text(encoding="utf-8")


def test_task_names_are_forwarded(monkeypatch):
    from inkvault.schedulers import windows
    seen = []
    monkeypatch.setattr(windows, "schtasks", lambda *a: seen.append(a) or proc())
    monkeypatch.setattr(windows.subprocess, "run", lambda cmd, **k: seen.append(tuple(cmd[1:])) or proc(1))
    windows.install(ARGV, 3, 0, False, task="T1")
    windows.query(task="T2")
    windows.set_enabled(False, task="T3")
    windows.run_task("T4")
    assert [a[a.index("/TN") + 1] for a in seen] == ["T1", "T2", "T3", "T4"]


def test_logon_task_xml_and_read_back(monkeypatch):
    from inkvault.schedulers import windows
    home = "C:\\Users\\José Ñ\\AppData\\Local\\InkVault"                         # Review Focus 4
    argv = ["C:\\Tools\\inkvault\\Scripts\\pythonw.exe", "-m", "inkvault", "--home", home, "watch"]
    xml = windows.render_logon(argv, "PC\\me")
    monkeypatch.setattr(windows.subprocess, "run",
                        lambda cmd, **k: proc(0, xml.encode("utf-16")) if "/XML" in cmd else proc(1))
    rb = windows.read_back("InkVault Watch")
    assert rb == {"command": argv[0], "arguments": argv[1:], "user": "PC\\me", "logon_type": "InteractiveToken",
                  "limit": "PT0S", "restart": ("PT1M", "3"), "instances": "IgnoreNew", "enabled": True,
                  "trigger_enabled": True, "trigger_user": "PC\\me"}


def test_read_back_tells_absent_from_unknown(monkeypatch):                       # R4
    import pytest
    from inkvault.schedulers import windows
    listing = '"\\InkVault Nightly","N/A","Ready"\n'
    monkeypatch.setattr(windows.subprocess, "run",
                        lambda cmd, **k: proc(0, listing.encode()) if "/FO" in cmd else proc(1))
    assert windows.read_back("InkVault Watch") is None                           # listed nowhere: absent
    with pytest.raises(windows.TaskUnknown):
        windows.read_back("InkVault Nightly")                                    # listed but unreadable
    monkeypatch.setattr(windows.subprocess, "run", lambda cmd, **k: proc(1))
    with pytest.raises(windows.TaskUnknown):
        windows.read_back("InkVault Watch")                                      # can't even list
    monkeypatch.setattr(windows.subprocess, "run", lambda cmd, **k: proc(0, "<Task".encode("utf-16")))
    with pytest.raises(windows.TaskUnknown):
        windows.read_back("InkVault Watch")                                      # unparseable


def test_a_disabled_logon_trigger_shows_in_read_back(monkeypatch):
    from inkvault.schedulers import windows
    xml = windows.render_logon(["C:\\p\\pythonw.exe", "-m", "inkvault", "--home", "C:\\v", "watch"], "PC\\me")
    xml = xml.replace("<Enabled>true</Enabled>\n      <UserId>", "<Enabled>false</Enabled>\n      <UserId>", 1)
    monkeypatch.setattr(windows.subprocess, "run", lambda cmd, **k: proc(0, xml.encode("utf-16")))
    assert windows.read_back("InkVault Watch")["trigger_enabled"] is False
```

`proc(code, out)` is the file's helper. Extend it to accept bytes for `stdout` if it doesn't already.

- [ ] **Step 3: Run to see them fail**

Run: `uv run pytest tests/test_schedulers.py -q`
Expected: FAIL (the fixture test passes; the others fail).

- [ ] **Step 4: Implement**

- Add `task=TASK` keyword parameters to `install`, `remove` and `query`, using `task` everywhere inside them.
  `query` runs `["schtasks", "/Query", "/TN", task, "/XML"]`, and `remove` calls `query(task)`.
- Do **not** change `render`.
- Add `import os`.
- Then add:

```python
WATCH_TASK = "InkVault Watch"
NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}


def set_enabled(on, task=TASK):
    r = schtasks("/Change", "/TN", task, "/ENABLE" if on else "/DISABLE")
    if r.returncode:
        raise RuntimeError(f"schtasks couldn't {'enable' if on else 'disable'} the task: "
                           f"{(r.stderr or r.stdout).strip()}")


def run_task(task):
    r = schtasks("/Run", "/TN", task)
    if r.returncode:
        raise RuntimeError(f"schtasks couldn't start the task: {(r.stderr or r.stdout).strip()}")


def current_user():
    return f"{os.environ.get('USERDOMAIN', '')}\\{os.environ.get('USERNAME', '')}".lstrip("\\")


def render_logon(argv, user):
    """The screen watcher: starts at this user's sign-in, never times out, restarts after a crash (spec §7)."""
    program = f'"{argv[0]}"' if " " in argv[0] else argv[0]
    command, arguments = escape(program), escape(subprocess.list2cmdline(argv[1:]))
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>InkVault screen capture: a timeline of your windows, and text from the apps you chose.</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{escape(user)}</UserId>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{escape(user)}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <RestartOnFailure>
      <Interval>PT1M</Interval>
      <Count>3</Count>
    </RestartOnFailure>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{command}</Command>
      <Arguments>{arguments}</Arguments>
    </Exec>
  </Actions>
</Task>
"""


def install_logon(argv, user, task=WATCH_TASK):
    with tempfile.TemporaryDirectory() as tmp:
        xml = Path(tmp) / "inkvault-watch.xml"
        xml.write_text(render_logon(argv, user), encoding="utf-16")
        r = schtasks("/Create", "/XML", str(xml), "/TN", task, "/F")
    if r.returncode:
        raise RuntimeError(f"schtasks couldn't create the task: {(r.stderr or r.stdout).strip()}")


class TaskUnknown(Exception):
    """Task Scheduler couldn't say what's there (access denied, unreadable XML): never treated as "no task"."""


def task_names():
    """Every task name Task Scheduler lists, or TaskUnknown."""
    r = subprocess.run(["schtasks", "/Query", "/FO", "CSV", "/NH"], capture_output=True)
    if r.returncode:
        raise TaskUnknown("schtasks couldn't list tasks")
    import csv
    return {row[0].lstrip("\\") for row in csv.reader(decode(r.stdout).splitlines()) if row}


def read_back(task):
    """Every field install checks (spec §7, R4). None only when the task is known to be absent (the full list
    doesn't have it); TaskUnknown when its state can't be read."""
    r = subprocess.run(["schtasks", "/Query", "/TN", task, "/XML"], capture_output=True)
    if r.returncode:
        if task not in task_names():
            return None
        raise TaskUnknown(f"the task {task!r} exists but couldn't be read")
    try:
        root = ET.fromstring(re.sub(r"^\ufeff?\s*<\?xml[^>]*\?>", "", decode(r.stdout)))
    except ET.ParseError:
        raise TaskUnknown(f"the task {task!r} couldn't be parsed") from None
    f = lambda path: (root.findtext(path, None, NS) or "").strip()
    on = lambda path: (root.findtext(path, None, NS) or "true").strip().lower() == "true"  # absent = enabled
    return {"command": f(".//t:Exec/t:Command").strip('"'), "arguments": split_args(f(".//t:Exec/t:Arguments")),
            "user": f("t:Principals/t:Principal/t:UserId"), "logon_type": f("t:Principals/t:Principal/t:LogonType"),
            "limit": f("t:Settings/t:ExecutionTimeLimit"),
            "restart": (f("t:Settings/t:RestartOnFailure/t:Interval"), f("t:Settings/t:RestartOnFailure/t:Count")),
            "instances": f("t:Settings/t:MultipleInstancesPolicy"),
            "enabled": on("t:Settings/t:Enabled"),
            "trigger_enabled": on("t:Triggers/t:LogonTrigger/t:Enabled"),
            "trigger_user": f("t:Triggers/t:LogonTrigger/t:UserId")}
```

`decode` and `split_args` already exist in v0.2.1's `windows.py`. Check `split_args` round-trips
`subprocess.list2cmdline` for a path with spaces and non-ASCII. If it doesn't, fix `split_args` and add a test.

- [ ] **Step 5: Run to see them pass**

Run: `uv run pytest tests/test_schedulers.py tests/test_schedule.py -q`
Expected: PASS (nightly unchanged).

- [ ] **Step 6: Commit**

```bash
git add src/inkvault/schedulers/windows.py tests/test_schedulers.py tests/fixtures/nightly-task.xml
git commit -m "Screen capture: sign-in task with full read-back; scheduler takes a task name; nightly XML pinned

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 17: `inkvault watch …` and status

**Files:**
- Modify: `src/inkvault/watch.py` (append), `src/inkvault/cli.py` (parser, dispatch, status)
- Test: `tests/test_watch_cli.py`

**Interfaces:**
- Consumes: Tasks 3, 10, 16; `schedule.tool_python`, `schedule.installed_version`,
  `schedule.check_task_scheduler_argv`, `schedule.ScheduleError`; `browsers.interactive`, `browsers.load_choices`;
  `cli.confirm`
- Produces:
  - `watch.cmd(args) -> int`; `watch.lock_held() -> bool`; `watch.stop_watcher(wait=None) -> bool`
  - `watch.install(ask=None, confirm_wait=STOP_WAIT) -> int`; `watch.uninstall() -> int`
  - `watch.status_lines() -> list[str]`; `watch.MIN_TOOL = (0, 3, 0)`

- [ ] **Step 1: Write the failing tests**

```python
"""The watch command (spec §3, §7): preview before recording, truthful install and uninstall, cooperative stop."""
import json
import os
import sys

import pytest

TOOL = "C:\\Tools\\inkvault\\Scripts\\pythonw.exe"


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("INKVAULT_HOME", str(tmp_path))
    return tmp_path


def run(*argv):
    from inkvault import cli
    return cli.main(list(argv))


def test_apps_on_a_fresh_install_previews_without_recording(home, capsys):    # P5
    from inkvault import paths, watch
    assert run("watch", "--apps") == 0
    out = capsys.readouterr().out
    assert "comet.exe" in out and "never titles" in out and paths.screen_file().exists()
    assert not watch.lock_held() and not paths.watch_heartbeat().exists()


def test_edits_report_truthfully(home, capsys):
    from inkvault import paths
    assert run("watch", "--add-app", "chrome.exe") == 0
    assert "every Chrome profile" in capsys.readouterr().out
    assert run("watch", "--never-title", "Account overview") == 0
    assert run("watch", "--pause", "1h") == 0
    d = json.loads(paths.screen_file().read_text())
    assert "chrome.exe" in d["text_apps"] and "Account overview" in d["never_titles"] and d["paused_until"]
    assert "within 2 seconds" in capsys.readouterr().out
    assert run("watch", "--pause", "forever") == 1


def test_stop_without_a_watcher(home, capsys):
    assert run("watch", "--stop") == 0 and "isn't running" in capsys.readouterr().out


def test_stop_timeout_is_not_success(home, monkeypatch, capsys):
    from inkvault import watch
    monkeypatch.setattr(watch, "lock_held", lambda: True)
    monkeypatch.setattr(watch, "STOP_WAIT", 0.2)
    assert run("watch", "--stop") == 1 and "may still be running" in capsys.readouterr().out


@pytest.fixture
def win(home, monkeypatch):
    """A fake Windows: a tool, schtasks via the scheduler module, and a watcher that may or may not start."""
    from inkvault import schedule, watch
    from inkvault.schedulers import windows
    monkeypatch.setattr(sys, "platform", "win32")
    state = {"xml": None, "enabled": True, "starts": True, "version": (0, 3, 0), "calls": []}
    monkeypatch.setattr(schedule, "tool_python", lambda: __import__("pathlib").PureWindowsPath(TOOL))
    monkeypatch.setattr(schedule, "installed_version", lambda exe: state["version"])
    monkeypatch.setattr(windows, "install_logon", lambda argv, user, task=windows.WATCH_TASK:
                        state.update(xml=windows.render_logon(argv, user)) or state["calls"].append("create"))

    def read_back(task):
        if state.get("unknown"):
            raise windows.TaskUnknown("access denied")
        if state["xml"] is None:
            return None
        return {"command": TOOL, "arguments": ["-m", "inkvault", "--home", str(home), "watch"], "user": "PC\\me",
                "logon_type": "InteractiveToken", "limit": "PT0S", "restart": ("PT1M", "3"),
                "instances": "IgnoreNew", "enabled": state["enabled"], "trigger_enabled": True,
                "trigger_user": "PC\\me"}
    monkeypatch.setattr(windows, "read_back", read_back)
    monkeypatch.setattr(windows, "current_user", lambda: "PC\\me")
    monkeypatch.setattr(windows, "run_task", lambda task: state["calls"].append("run"))
    monkeypatch.setattr(windows, "set_enabled", lambda on, task=None: state.update(enabled=on)
                        or state["calls"].append("enable" if on else "disable"))
    monkeypatch.setattr(windows, "remove", lambda task=None: state.update(xml=None) or state["calls"].append("delete")
                        or True)
    monkeypatch.setattr(watch, "running_confirmed", lambda wait, since: state["starts"])
    monkeypatch.setattr(watch, "make_shortcuts", lambda pythonw: None)
    return state


def test_stop_needs_no_settings_and_no_vault(home, monkeypatch, capsys):          # R2
    from inkvault import paths, screen, watch
    screen.prepare()
    paths.screen_file().unlink()                                                   # lost settings
    held, stops = {"v": True}, []
    monkeypatch.setattr(watch, "lock_held", lambda: held["v"])
    monkeypatch.setattr(paths, "watch_stop", lambda: type("F", (), {
        "write_text": lambda self, *a, **k: stops.append(1) or held.update(v=False)})())
    assert run("watch", "--stop") == 0 and stops == [1] and "Stopped." in capsys.readouterr().out


def test_pause_works_while_a_sqlite_writer_holds_the_vault(home):                  # R2
    from inkvault import export, paths, screen
    screen.prepare()
    blocker = export.open_vault(); blocker.execute("BEGIN IMMEDIATE")
    try:
        assert run("watch", "--pause", "1h") == 0
    finally:
        blocker.rollback(); blocker.close()
    assert json.loads(paths.screen_file().read_text())["paused_until"]


def test_install_with_a_watcher_already_running_says_so(win, monkeypatch, capsys):  # R4
    from inkvault import watch
    monkeypatch.setattr(watch, "lock_held", lambda: True)
    monkeypatch.setattr(watch, "lock_owner", lambda: 4242)
    assert watch.install(ask=lambda q: False) == 0
    out = capsys.readouterr().out
    assert "already running (pid 4242)" in out and "run" not in win["calls"]


def test_uninstall_refuses_unknown_task_state(win, capsys):                        # R4
    from inkvault import watch
    win["unknown"] = True
    assert watch.uninstall() == 1 and win["calls"] == [] and "couldn't say" in capsys.readouterr().out


def test_heartbeat_checks(home, monkeypatch):                                       # R4
    from datetime import timedelta
    from inkvault import screen, watch
    monkeypatch.setattr(watch, "lock_held", lambda: True)
    monkeypatch.setattr(watch, "lock_owner", lambda: 7)
    now = screen.now()
    ok = {"pid": 7, "utc": screen.iso(now)}
    assert watch.heartbeat_ok(ok, since=now - timedelta(seconds=5))
    assert not watch.heartbeat_ok({**ok, "utc": screen.iso(now + timedelta(minutes=5))})       # from the future
    assert not watch.heartbeat_ok({**ok, "utc": screen.iso(now - timedelta(minutes=5))})       # stale
    assert not watch.heartbeat_ok({**ok, "pid": 8})                                             # someone else's
    assert not watch.heartbeat_ok(ok, since=now + timedelta(seconds=5))                         # before this start


def test_install_confirms_before_claiming_on(win, capsys):
    from inkvault import watch
    assert watch.install(ask=lambda q: False) == 0
    assert win["calls"] == ["create", "run"] and "on now and at every sign-in" in capsys.readouterr().out


def test_install_without_a_running_watcher_is_not_success(win, capsys):
    from inkvault import watch
    win["starts"] = False
    assert watch.install(ask=lambda q: False) == 1 and "hasn't confirmed" in capsys.readouterr().out


def test_install_refuses_an_old_tool(win, capsys):
    from inkvault import watch
    win["version"] = (0, 2, 1)
    assert watch.install(ask=lambda q: False) == 1 and "inkvault[screen]" in capsys.readouterr().out
    assert win["calls"] == []


def test_install_removes_a_task_that_reads_back_wrong(win, monkeypatch, capsys):
    from inkvault import watch
    from inkvault.schedulers import windows
    real = windows.read_back
    monkeypatch.setattr(windows, "read_back", lambda t: {**real(t), "limit": "PT72H"} if real(t) else None)
    assert watch.install(ask=lambda q: False) == 1 and "delete" in win["calls"] and "run" not in win["calls"]


def test_install_asks_about_messaging_and_chrome(win):
    from inkvault import paths, screen, watch
    asked = []
    watch.install(ask=lambda q: asked.append(q) or "messag" in q.lower())
    d = json.loads(paths.screen_file().read_text())
    assert len(asked) == 2 and "Discord.exe" in d["text_apps"] and "chrome.exe" not in d["text_apps"]


def test_uninstall_order_and_unknown_home(win, monkeypatch, capsys):
    from inkvault import watch
    from inkvault.schedulers import windows
    win["xml"] = "x"
    monkeypatch.setattr(watch, "stop_watcher", lambda wait=None: win["calls"].append("stop") or True)
    assert watch.uninstall() == 0 and win["calls"] == ["disable", "stop", "delete"]          # never force-ended
    win["xml"], win["calls"] = "x", []
    real = windows.read_back
    monkeypatch.setattr(windows, "read_back", lambda t: {**real(t), "arguments": ["-m", "inkvault", "watch"]})
    assert watch.uninstall() == 1 and win["calls"] == [] and "can't tell" in capsys.readouterr().out


def test_uninstall_stop_timeout_leaves_the_task_disabled(win, monkeypatch, capsys):
    from inkvault import watch
    win["xml"] = "x"
    monkeypatch.setattr(watch, "stop_watcher", lambda wait=None: False)
    assert watch.uninstall() == 1 and win["xml"] == "x" and win["enabled"] is False
    assert "may still be running" in capsys.readouterr().out


def test_status_lines(home):
    from inkvault import watch
    assert any(l.startswith("screen capture:") for l in watch.status_lines())
```

- [ ] **Step 2: Run to see them fail**

Run: `uv run pytest tests/test_watch_cli.py -q`
Expected: FAIL (`invalid choice: 'watch'`).

- [ ] **Step 3: Implement (append to `watch.py`)**

```python
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


def change(edit, done):
    """Settings edits touch only screen.json under screen.lock: no vault, no nightly lock (R1, R2). Only a home
    with no screen.json at all is prepared first (a true first use, or lost settings, which prepare refuses)."""
    try:
        if not paths.screen_file().exists():
            screen.prepare()
        screen.edit(edit)
    except screen.SettingsBusy:
        print("Another command is changing screen capture settings right now; nothing changed. Try again.")
        return 1
    except screen.SettingsError as e:
        print(f"Nothing changed: {e}")
        return 1
    print(f"{done} Saved; a running watcher applies it within 2 seconds.")
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


def install(ask=None, confirm_wait=None):
    from . import schedule
    from .schedulers import windows
    screen.prepare()
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
    user = windows.current_user()
    windows.install_logon(argv, user)
    try:
        rb = windows.read_back(windows.WATCH_TASK)
    except windows.TaskUnknown:
        rb = None
    if rb != expected_task(pythonw, user):
        try:
            windows.remove(task=windows.WATCH_TASK)
        except RuntimeError:
            pass
        print("The sign-in task didn't read back as expected, so it was removed; nothing new is recording.")
        return 1
    make_shortcuts(pythonw)
    if lock_held():   # R4: a watcher already running (started by hand) isn't proof the task's action works
        print(f"A watcher is already running (pid {lock_owner()}) and keeps recording. The sign-in task is set up "
              "and starts screen capture from the next sign-in.")
        return 0
    since = screen.now()
    windows.run_task(windows.WATCH_TASK)
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
    home = str(paths.home())
    script = "; ".join(
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+"
        f"'\\{name}.lnk'); $s.TargetPath='{pythonw}'; $s.Arguments='-m inkvault --home \"{home}\" watch {args}'; "
        "$s.Save()" for name, args in (("Pause screen capture (1 hour)", "--pause 1h"),
                                       ("Resume screen capture", "--resume")))
    r = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True)
    if r.returncode:
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
            print("Screen capture may still be running: the watcher didn't stop within 15 seconds. The sign-in "
                  "task is disabled; run `inkvault watch --uninstall` again.")
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
            print(e)
            return 1
        return change(lambda d: d.update(paused_until=screen.iso(until)),
                      f"Paused until {until.astimezone():%Y-%m-%d %H:%M}.")
    if args.resume:
        return change(lambda d: d.update(paused_until=None), "Resumed.")
    if args.reset_settings:
        with screen.settings_lock():
            screen.write_atomic(paths.screen_file(), screen.defaults())
        try:
            screen.prepare()
        except Exception as e:  # noqa: BLE001 — the file is written; the marker can wait for the next start
            print(f"(The vault couldn't be opened to record the setup: {type(e).__name__}.)")
        print("Screen capture settings reset to the defaults (messaging apps and Chrome are timeline-only).")
        return 0
    # First use and running: these prepare the home.
    try:
        screen.prepare()
    except screen.SettingsError as e:
        print(f"Screen capture settings need attention: {e}")
        return 1
    if args.apps:
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
    try:
        hb = json.loads(paths.watch_heartbeat().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        hb = None
    if hb and lock_held():
        age = int((screen.now() - screen.parse(hb["utc"])).total_seconds())
        state = hb.get("state") or ("paused" if hb.get("paused") else
                                    ("on, in " + hb["open"]["app"] if hb.get("open") else "on"))
        out.append(f"screen capture: {state} (heartbeat {age} s ago, pid {hb.get('pid')})")
        for key, label in (("settings_error", "settings"), ("db_error", "database"), ("heartbeat_error", "heartbeat")):
            if hb.get(key):
                out.append(f"  {label}: {hb[key]}")
        if hb.get("cooling"):
            out.append("  text paused after a slow read: " + ", ".join(hb["cooling"]))
        if hb.get("job_error"):
            out.append(f"  {hb['job_error']}")
        if hb.get("dropped_text") or hb.get("busy_gaps"):
            out.append(f"  dropped text: {hb.get('dropped_text', 0)}, busy gaps: {hb.get('busy_gaps', 0)} polls")
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
```

`cli.py`:
- Add the `watch` parser. Its mutually exclusive group holds `--apps`, `--add-app EXE`, `--remove-app EXE`,
  `--never-app EXE`, `--never-title TEXT`, `--pause FOR`, `--resume`, `--stop`, `--install`, `--uninstall` and
  `--reset-settings`, each with a one-line help.
- Dispatch with `if args.cmd == "watch": from . import watch; return watch.cmd(args)`.
- In `cmd_status`:
  - rename the captures line's label to `Pieces captures`;
  - after the dashboard line, print each of `watch.status_lines()`;
  - Windows calls in `status_lines` are skipped off Windows, so the existing status tests keep passing.

- [ ] **Step 4: Run to see them pass, then the whole suite**

Run: `uv run pytest tests/test_watch_cli.py -q`, then `uv run pytest -q`
Expected: PASS. No test reaches a real `schtasks` or `powershell` (the guards fail loudly).

- [ ] **Step 5: Commit**

```bash
git add src/inkvault/watch.py src/inkvault/cli.py tests/test_watch_cli.py
git commit -m "Screen capture: watch command (preview first, verified install, ordered uninstall) and status

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 18: `inkvault forget --screen`

**Files:**
- Modify: `src/inkvault/cli.py`
- Test: `tests/test_forget_cli.py`

**Interfaces:**
- Consumes: `screen.local_bounds`, `screen.LOCAL_TZ`, `forget.screen_preview`, `forget.screen_request`,
  `forget.finish`, `cli.locked`, `cli.confirm`
- Produces: `cli.cmd_forget(args) -> int`, `cli.fmt_span(lo, hi) -> str`

- [ ] **Step 1: Write the failing tests**

```python
"""`inkvault forget --screen` (spec §8, P4): local input, the normalized range and the actual span shown."""
import json

import pytest
from zoneinfo import ZoneInfo


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("INKVAULT_HOME", str(tmp_path))
    from inkvault import export, screen
    monkeypatch.setattr(screen, "LOCAL_TZ", ZoneInfo("America/Chicago"))
    monkeypatch.setattr("inkvault.forget.finish", lambda: None)
    v = export.open_vault()
    v.execute("INSERT INTO screen_stretches VALUES ('screen:a','2026-10-12T14:00:00.000000Z',"
              "'2026-10-12T16:00:00.000000Z','Discord.exe','Chat')")         # 09:00–11:00 in Chicago
    v.commit(); v.close()
    return tmp_path


def run(*argv):
    from inkvault import cli
    return cli.main(list(argv))


def recorded():
    from inkvault import export
    v = export.open_vault()
    try:
        return v.execute("SELECT value FROM browser_removals").fetchall()
    finally:
        v.close()


def test_local_input_is_local(home, capsys):
    assert run("forget", "--screen", "--since", "2026-10-12T10:00", "--until", "2026-10-12T10:30", "--yes") == 0
    out = capsys.readouterr().out
    assert "1 stretch" in out and "wider than the range" in out and "backups" in out
    assert json.loads(recorded()[0][0]) == {"since": "2026-10-12T15:00:00.000000Z",
                                             "until": "2026-10-12T15:30:00.000000Z"}


def test_a_utc_range_that_misses_locally_removes_nothing(home, capsys):
    assert run("forget", "--screen", "--since", "2026-10-12T17:00Z", "--until", "2026-10-12T18:00Z", "--yes") == 0
    assert "Nothing recorded matches" in capsys.readouterr().out and recorded() == []


def test_bad_ranges(home, capsys):
    assert run("forget", "--screen", "--since", "2026-03-08T02:30", "--until", "2026-03-08T04:00", "--yes") == 1
    assert "doesn't exist" in capsys.readouterr().out
    assert run("forget", "--screen", "--since", "2026-10-12", "--until", "2026-10-11", "--yes") == 1
    assert run("forget", "--screen", "--since", "2026-10-12", "--yes") == 1


def test_app_reminder(home, capsys):
    assert run("forget", "--screen", "--app", "discord.exe", "--yes") == 0
    assert "inkvault watch --never-app discord.exe" in capsys.readouterr().out
```

- [ ] **Step 2: Run to see them fail**

Run: `uv run pytest tests/test_forget_cli.py -q`
Expected: FAIL (`invalid choice: 'forget'`).

- [ ] **Step 3: Implement (in `cli.py`)**

Parser:

```python
    f = sub.add_parser("forget", help="remove screen capture by app or time range")
    f.add_argument("--screen", action="store_true", required=True)
    fg = f.add_mutually_exclusive_group(required=True)
    fg.add_argument("--app", metavar="EXE")
    fg.add_argument("--since", metavar="TIME", help="local time or date (or with an offset, e.g. Z)")
    f.add_argument("--until", metavar="TIME")
    f.add_argument("--yes", action="store_true", help="don't ask")
```

Dispatch: `if args.cmd == "forget": return cmd_forget(args)`.

```python
def fmt_span(lo, hi):
    from .times import local
    a, b = local(lo), local(hi)
    return f"{a:%Y-%m-%d %H:%M}–{b:%H:%M}" if a.date() == b.date() else f"{a:%Y-%m-%d %H:%M} – {b:%Y-%m-%d %H:%M}"


def cmd_forget(args):
    from . import browsers, forget, screen
    if args.app:
        what, value, asked = "screen-app", args.app, None
    else:
        if not args.until:
            print("--since needs --until.")
            return 1
        try:
            s, u = screen.local_bounds(args.since, args.until)
        except ValueError as e:
            print(f"Bad range: {e}")
            return 1
        what, value, asked = "screen-range", json.dumps({"since": s, "until": u}), (s, u)
    n, lo, hi = forget.screen_preview(what, value)
    if not n:
        print("Nothing recorded matches; nothing was removed.")
        return 0
    plural = "es" if n != 1 else ""
    if asked:
        print(f"Range: {fmt_span(*asked)} (local time).")
    if not args.yes and not (browsers.interactive() and confirm(
            f"Remove {n:,} stretch{plural} of screen capture ({fmt_span(lo, hi)}) and their text? [y/N] ")):
        print("Nothing was removed.")
        return 1
    with locked("forget", "nothing was removed.", required=True) as ok:
        if not ok:
            return 1
        n, lo, hi = forget.screen_request(what, value)
        if not n:
            print("Nothing recorded matches any more; nothing was removed.")
            return 0
        wider = asked and (lo < asked[0] or hi > asked[1])
        print(f"Removing {n:,} stretch{'es' if n != 1 else ''}, {fmt_span(lo, hi)}"
              + (" (wider than the range you asked for: whole windows are removed)" if wider else "") + ".")
        try:
            forget.finish()
        except Exception as e:  # noqa: BLE001
            print(f"Removing didn't finish ({type(e).__name__}). The next `inkvault index` or nightly run finishes "
                  "it, and search stays off until then.")
            return 1
    print("Backups made before now, and answers already given to an AI client, still have it.")
    if what == "screen-app":
        print(f"Screen capture still records {args.app}: to stop, `inkvault watch --never-app {args.app}`.")
    return 0
```

(`json` and `locked`/`confirm` are already in `cli.py`; add `import json` at the top if it's missing.)

- [ ] **Step 4: Run to see them pass**

Run: `uv run pytest tests/test_forget_cli.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/inkvault/cli.py tests/test_forget_cli.py
git commit -m "Screen capture: forget --screen with local-time input

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 19: Docs, version 0.3.0, the code-review request

**Files:**
- Modify: `README.md`, `CHANGELOG.md`, `pyproject.toml` (`version = "0.3.0"`), `src/inkvault/__init__.py`
- Create: `docs/reviews/2026-10-12-codex-screen-capture-code.md`, `docs/reviews/2026-10-12-screen-build-ledger.md`

- [ ] **Step 1: README.** A "Screen capture (Windows)" section covering:
  - what it records (a timeline everywhere, text from chosen apps, no images);
  - the default text apps, and the two install questions with their reasons;
  - that everyone using the Windows account is captured;
  - install (`uv tool install "inkvault[screen]"`, then `inkvault watch --apps`, then `inkvault watch --install`);
  - every `watch` flag, the pause shortcuts and `forget --screen` (local times, whole windows, backups not
    erased);
  - the limits: bank sites via `--never-title`; private-window markers may be missing or localized; the last
    moment between the check and the capture; search updates nightly while `timeline` is live; Store apps are
    timeline-only; Windows-only.
- [ ] **Step 2: CHANGELOG.** `## 0.3.0 — screen capture`, with the same points in short.
- [ ] **Step 3: Version.** Set `0.3.0` in `pyproject.toml` and `__init__.py`, then run `uv lock`.
- [ ] **Step 4: Ledger** (`docs/reviews/2026-10-12-screen-build-ledger.md`): for each task, its commit, its tests,
  and any deviation from this plan with the reason.
- [ ] **Step 5: Code-review request** (`docs/reviews/2026-10-12-codex-screen-capture-code.md`):
  - review branch `screen-capture` against spec rev 5 and this plan;
  - ground rules: no installs, no scheduled tasks, no live vault, no reading browser profiles, never print titles
    or screen text;
  - a **synthetic gate**: the F1/R3 races against the real `screenstore`/`forget`; the supervisor with
    `tests/fakeworker.py`; the index snapshot under concurrent writes; Task 11's integration tests; on Windows, the
    native tests (typed bindings, GDI counts, job death, DPI, OCR round trip);
  - findings into `docs/reviews/2026-10-12-codex-screen-capture-code-findings.md`.
- [ ] **Step 6: Whole suite**

Run: `uv run pytest -q`
Expected: PASS (368 + all new; Windows-only tests run on Windows).

- [ ] **Step 7: Commit**

```bash
git add README.md CHANGELOG.md pyproject.toml uv.lock src/inkvault/__init__.py docs/reviews/2026-10-12-codex-screen-capture-code.md docs/reviews/2026-10-12-screen-build-ledger.md
git commit -m "Screen capture: docs, 0.3.0, build ledger and code-review request

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 20: Publish (only with the maintainer's explicit OK)

Not delegated. It runs after the code review passes and its findings are fixed.

- [ ] **Step 1: Privacy gate.** Run the maintainer's private scrub check over every file this branch changed since
  `v0.2.1` (`git diff --name-only v0.2.1..screen-capture`) and over its commit messages. It must find nothing.
- [ ] **Step 2: Ask the maintainer.** Show the branch's commit list and diff stat, and ask whether to publish. Stop
  here without a clear yes.
- [ ] **Step 3: Publish.**
  1. `git push origin screen-capture`;
  2. open the pull request against the public `main`;
  3. after the maintainer merges, create release `v0.3.0` with `gh release create v0.3.0 --target <full merge
     SHA>` (a short SHA fails), using the CHANGELOG section as notes.

---

## Self-Review

- **Spec coverage:**

  | Spec section | Task(s) |
  |---|---|
  | §1 (loop, ending rules, identity, heartbeat, reopen, stop) | 4, 7, 10, 11 |
  | §2 (supervisor, validation, checks, capture, GDI, DPI, OCR) | 7, 8, 9, 10, 11 |
  | §3 (settings, lock, generation, fail closed, preparing a home) | 3, 10, 17 |
  | §4 (never rules, banks, pause, redaction) | 2, 3, 17 |
  | §5 (tables, guarded writes, dedupe, queue) | 1, 5, 6, 10, 11 |
  | §6 (snapshot, search adapter, live timeline and `get_memory`, bounds, Atlas and digests) | 1, 13, 14, 15 |
  | §7 (install, uninstall, stop, status) | 16, 17 |
  | §8 (forgetting) | 12, 18 |
  | Testing gates | every task; Task 11 is the integration gate; the live trial run is the maintainer's, after Task 20 |

- **Plan-review findings:**

  | Finding | Fixed in |
  |---|---|
  | P1 (typed bindings, GDI) | 7, 9 |
  | P2 (abandon keeps the deadline; sends never block) | 8, 11 |
  | P3 (settings generation drops queued text; reload before accepting; typed validation) | 3, 10 |
  | P4 (local forget input) | 1, 18 |
  | P5 (preview before recording; verified install; ordered uninstall) | 3, 17 |
  | P6 (per-stretch dedupe after commit) | 6, 10 |
  | P7 (complete digest code; DST split) | 15 |
  | P8 (Screen section; `screentext:` ids) | 14 |
  | P9 (non-empty ranges, handled the same everywhere) | 1, 14, 18 |
  | P10 (schemas; title hash) | 8 |
  | P11 (flush budgets; timeout at connect; heartbeat failures) | 5, 6, 10, 11 |
  | P12 (gates) | 0, 3, 4, 11, 14, 16, 17 |

- **Plan recheck** (`…-plan-recheck-findings.md`, cited as R1–R8 inside Tasks 0, 8, 10, 11, 14–17):

  | Finding | Fixed in |
  |---|---|
  | R1 (one settings refresh that applies transitions; flush filters by generation; a real mid-tick barrier test) | 10 |
  | R2 (controls before preparation; edits touch only `screen.json`) | 17 |
  | R3 (supervisor polled every tick; hang with invalid or lost settings) | 10, 11 |
  | R4 (absent vs. unknown task state; trigger enabled; heartbeat pid, age and start checks; an existing watcher reported) | 16, 17 |
  | R5 (spring-forward expectation; combined digest budget; half-hour offsets) | 15 |
  | R6 (merge checklist: `uv.lock`, a baseline taken before the merge, reviewing auto-merged files, the L2 handshake) | local doc |
  | R7 (a real, self-tested privacy check) | local doc, Task 20 |
  | R8 (red phase without launching anything, hex-checked requests, error names, the exact cap, prompt stop, unreadable generation, per-source errors, recovery heartbeat) | 0, 8, 10, 11, 14 |

- **Placeholders:** none. Task 15's dashboard test tells the implementer to add any table `collect()` reads. Task
  16 names the existing `proc` and `split_args` helpers to check. Both are instructions, not deferred decisions.
- **Type consistency:**
  - `Identity(hwnd, pid, created, exe, title)` in Tasks 4, 7, 10;
  - requests carry `title_sha256` (Tasks 7–10);
  - `Write(..., settings_gen=...)` in Tasks 5, 6, 10;
  - `Flush.texts` in Tasks 6 and 10;
  - `Supervisor.submit/poll/abandon/busy/cooling/close` in Tasks 8, 10, 11;
  - `screen.bounds` (MCP) vs `screen.local_bounds` (forget) in Tasks 1, 14, 18;
  - `windows.read_back` in Tasks 16 and 17.
- **Deviations from the spec:** none. Revision 5 records the earlier four (microseconds, UTC date bounds for MCP,
  process creation time, the Atlas tiles) and the one-hit-per-stretch rule.
