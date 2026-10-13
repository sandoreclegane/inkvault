# Screen capture: `inkvault watch`

**Status:** design, revision 5 (2026-10-12), ready to build. This is the public version of the design. Revisions 1–4
were worked out privately with two design reviews. Revision 5 adds the implementation-plan review and the move to a
public release built on v0.2.1. The rules cite the review findings that shaped them: **[F#]** design review, **[R#]**
recheck, **[P#]** plan review.
**Release:** v0.3.0, built on the public v0.2.1. Windows only.
**Plan:** `docs/superpowers/plans/2026-10-12-screen-capture.md`.

## Why

Pieces recorded the foreground app, window title and on-screen text, and that was the bulk of most people's Pieces
memory. Pieces has wound down, so the record of what you did *outside* chats and the browser's address bar stops
with it. Screen capture brings it back. Mostly it answers "what was I working on, and when?" Secondly, it keeps the
text of the moments that mattered, from apps you choose.

## Product decisions

1. **Timeline everywhere, text where chosen.** Every foreground window is recorded as a *stretch* (app, cleaned
   title, start, end). Text (OCR of the window) is read only in the chosen apps, only after you settle on a window,
   and only when it changed.
2. **Built into InkVault.** One app, one store, no new software that sees the screen.
3. **No images kept.** The window image lives in memory only, long enough to OCR, and never touches the disk.
4. **No hotkey** for now.
5. **One Windows account.** The watcher runs in the account that installed it, starting at sign-in. Everyone who uses
   that account is captured. The README says so plainly.
6. **Messaging apps are off by default** (Discord, WhatsApp, Telegram, Teams, LinkedIn). Reading them stores other
   people's messages and names (Discord's title is `@<contact> - Discord`). `inkvault watch --install` asks, and
   without a terminal they stay timeline-only.
7. **Chrome text is off by default.** Chrome's window title is `<page> - Google Chrome`, with no profile name, so
   screen capture can't tell your Chrome profiles from someone else's on the same account. Install asks, and the
   question says this. Comet and Edge get text by default.
8. **Range forgetting removes whole stretches.** Any stretch overlapping the range goes, with all its text. The
   command shows the actual span removed.

## The apps

| Category | Text by default | Asked at install |
|---|---|---|
| Browsers | `comet.exe`, `msedge.exe` | `chrome.exe` |
| AI tools | `claude.exe`, `ChatGPT.exe`, `Perplexity.exe`, `Codex.exe` | — |
| Coding | `WindowsTerminal.exe`, `Code.exe`, `Cursor.exe` | — |
| Notes | `Notion.exe`, `Obsidian.exe`, `Notepad.exe` | — |
| Microsoft 365 | `WINWORD.EXE`, `EXCEL.EXE`, `POWERPNT.EXE`, `OUTLOOK.EXE`, `olk.exe`, `ONENOTE.EXE` | — |
| Messaging | — | `Discord.exe`, `WhatsApp.Root.exe`, `Telegram.exe`, `ms-teams.exe`, `LinkedIn.exe` |

That's 18 text apps by default, plus 6 that install asks about. Every other app is timeline-only. Anyone can add
or remove apps (`--add-app`, `--remove-app`). Matching ignores case.

## Design

### 1. The watcher loop (`watch.py`, pure core in `stretches.py`)

`inkvault --home <home> watch` is a long-running process, run hidden under `pythonw`. It owns the timeline and the
rules, and never calls `PrintWindow` or OCR itself (§2). Every 2 seconds:

1. **Foreground window:** `hwnd`, process id, the process's creation time, executable, raw title. A cloaked window
   (another virtual desktop, a suspended Store app) or a minimized one counts as *no window*. If the cloak state,
   desktop or identity can't be read, that also counts as no window (fail closed) [P1].
2. **Away:** no input for 5 minutes (`GetLastInputInfo`, compared modulo 2³² against `GetTickCount64`), or locked
   (foreground is `LockApp.exe`, or the input desktop isn't `Default`).
3. **Stretch:** the same (`hwnd`, pid, creation time, executable, title) as the last poll extends the open stretch;
   anything else closes it and opens a new one. A stretch seen on fewer than 2 polls is a flick and is never stored
   (with a 2 s poll this means "roughly 2–4 s", which is approximate and documented).
4. **Text is due** when the app is a text app and nothing excludes it (§4), the stretch has lasted 30 s (dwell, per
   stretch), and this window (`hwnd`, pid, creation time) hasn't been read in the last 2 minutes (read budget, per
   window).

**Where a stretch ends:**
- window change, lock, pause or exclusion: at the poll that saw it;
- idle: at the last input, never before the stretch's start;
- two polls more than 30 s apart on the monotonic clock (sleep, a stall): at the earlier poll.

`ended` is always ≥ `started`, even if the wall clock jumps backwards.

**Time:** dwell, budgets, cooldowns and idle use `time.monotonic()`. Stored times are UTC ISO with microseconds and
`Z` (`2026-10-12T14:03:07.120000Z`), the format the search server already uses for bounds.

**One instance:** `watch.lock` in the InkVault home, held with the nightly lock's kernel-lock helpers. A second
watcher exits at once with "already running (pid N)".

**Heartbeat, log, errors:** `watch.heartbeat` (JSON: time, open stretch's app, paused, error, counts) is rewritten
every 30 s. `watch.log` keeps app names, counts, times and error *classes* only, never titles, text or settings
contents. A failure to write the heartbeat or log is recorded in memory and retried, never fatal [P11]. A fatal
error ends the process with a nonzero code, so Task Scheduler's restart applies.

**Storage trouble:** if the vault can't be opened at start, the watcher records nothing, reports it, and retries
every 10 s. Errors once running are §5's.

**Stopping:** the watcher checks for `watch.stop` every poll. When it finds it, it closes the open stretch, makes
one flush attempt, ends its worker, removes `watch.stop`, releases the lock and exits 0.

### 2. Reading a window's text (`screentext.py`, supervised by `worker.py`)

**Worker process.** Reads run in a child process (`python -m inkvault.screentext --worker`), reused across reads
and replaced only after a timeout, a protocol error or a native failure. The child is put in a Windows job object
that kills it when the watcher's job handle closes, so an abruptly killed watcher can't leave it behind. A failed
job assignment is reported (heartbeat, `status`), not swallowed [P1].

**Supervision never blocks the loop** [R2, P2]:
- At most one request is outstanding per worker. A request goes only to an idle worker.
- Requests are capped at 3,072 bytes, under the pipe's 4 KB buffer, so writing one never blocks.
- The request carries the window's `hwnd`, pid, creation time, executable and a SHA-256 of the raw title, never
  the title itself. So a long title can't exceed the cap, and no title crosses the pipe [P10].
- Each poll the watcher peeks the pipe and reads only what's there. A partial message is buffered across polls.
- **Abandoning** a request (pause, settings change, removal) only marks its answer as unacceptable. The deadline
  still runs: the worker stays "busy" until it answers or is killed at **5 s**. A killed worker is replaced at the
  next eligible read. Killing never waits: the dead process is reaped by later polls. On a timeout, that app goes
  on a 10-minute cooldown, shown in `status`.
- Messages are length-prefixed JSON, and **validated** on both sides [P10]:
  - requests: integer `id`, `hwnd`, `pid` and `created`; string `exe`; 64-hex `title_sha256`;
  - responses: an object with integer `id`, `status` ∈ {`ok`, `changed`, `failed`, `no-ocr`}, `lines` a list of
    strings, integer `colors`, string `error`, at most 1,000,000 bytes in total.

  Anything else is a protocol error, and the worker is restarted.

**Checks on both sides of the capture** [F4, R2, P3]:
- In the worker, just before capture: the window is still the foreground window, with the same identity and title
  hash, uncloaked, unminimized, on the `Default` desktop. Otherwise the answer is `changed`.
- The same check runs again just after capture.
- In the watcher, before accepting an answer: a **fresh** sample matches the request; the session isn't locked or
  idle; settings are **reloaded** and their generation is unchanged; the app is still a text app and nothing
  excludes it or pauses capture. Otherwise the answer is dropped.
- The removal generation is checked again inside the write (§5).

What remains is the gap between the last check and the pixels being drawn. Sampling can't close it, and the README
says so.

**Capture.** `PrintWindow(hwnd, …, PW_RENDERFULLCONTENT)` into a memory DC, with the worker set to per-monitor DPI
awareness. There's no `BitBlt` fallback.
- **When it counts:** `PrintWindow` returns nonzero; every allocation (DC, bitmap, the `StretchBlt` when scaling)
  succeeds; the window rectangle is the same before and after; and `GetDIBits` fills every row.
- **GDI handling:** the pixels are 32-bit BGRA, top-down. Each bitmap is deselected (the DC's previous object
  restored) before `GetDIBits` and before it's deleted [P1]. Every native call goes through typed ctypes bindings
  (pointer-sized handles, `BOOL`/`DWORD` results, last-error) [P1].
- **Size:** a window larger than `OcrEngine.max_image_dimension` is scaled down to fit (HALFTONE).

**OCR.** Windows' `Windows.Media.Ocr` through the `winrt` projections, in the optional Windows-only extra
`inkvault[screen]`, using the user's profile languages. Every valid capture is OCR'd [R5]. The sampled color count
is a diagnostic only. An empty result stores nothing. Without the extra, or with no OCR language installed, the
watcher records the timeline only and `status` says why.

**Clean-up before storing:** OCR lines are joined, text under 20 characters is dropped, and secrets are redacted
(§4).

**Budget:** on a typical PC, 20–40 ms to capture and 40–220 ms to OCR. Spawning a fresh worker after a kill adds
roughly 100 ms, at most once per eligible read.

### 3. Settings (`screen.json` in the InkVault home)

```json
{"text_apps": ["comet.exe", "msedge.exe", "claude.exe"],
 "never_apps": ["1Password.exe", "Bitwarden.exe", "KeePassXC.exe", "KeePass.exe", "CredentialUIBroker.exe",
                "LockApp.exe", "consent.exe"],
 "never_titles": ["InPrivate", "Incognito", "Private Browsing"],
 "paused_until": null}
```

- **Edits:** `--apps`, `--add-app`, `--remove-app`, `--never-app`, `--never-title`, `--pause`, `--resume`,
  `--reset-settings`.
- **Its own short lock** [R1]. Each edit holds `screen.lock` only for its read-validate-write, never the nightly
  lock, so *Pause* works during a nightly run. If `screen.lock` is busy, a command retries for 5 s, then says
  nothing changed. The watcher never takes it.
- **Atomic and validated** [P3]:
  - `text_apps`, `never_apps` and `never_titles` must be lists of non-empty strings;
  - `paused_until` must be `null` or an ISO time string;
  - anything else is a `SettingsError` whose message names the field, never the file's contents;
  - a file is written to a temporary name and validated before `os.replace`.
- **Generation.** The watcher reloads the file each poll when its mtime changed, and every poll while the last load
  failed, so a permissions fix is picked up even with the same mtime. Every successful reload bumps the **settings
  generation**. A new generation abandons the read in flight and drops **all** queued text [P3]: text read under
  old rules is never written under new ones. Stretch writes already queued keep their observed times.
- **Fail closed** [R8]. Unreadable or invalid settings make the watcher:
  - close the open stretch at the last poll that had valid settings;
  - abandon the read in flight and drop queued text;
  - record nothing until the file validates again.
- **First run vs. lost settings** [R8, P5]:
  - Any `watch` command first *prepares* the home: it opens the vault, and if the vault's
    `meta('screen_initialized')` marker is unset, writes the defaults and then sets the marker. Nothing ever clears
    the marker, including forgetting.
  - So `inkvault watch --apps` on a fresh install shows the defaults **before anything is recorded**. Preparing
    never starts capture.
  - If the marker is set and `screen.json` is missing, everything fails closed until `--reset-settings` or the file
    comes back.

### 4. Never capture, and redaction

- **`never_apps` and `never_titles`** (matched case-insensitively against the raw title) are skipped entirely: no
  stretch, no text. The time is a gap.
- **Private windows:** `never_titles` catches the usual English markers. These can be missing or localized, and the
  README says so.
- **Banks and other sensitive sites:** browser-history's `skip_sites` holds *domains*, and a page title often
  doesn't name its domain. So those aren't applied to titles. Sites to keep out of screen capture go in
  `never_titles` as title text. `--apps` and the first `--install` say this and list the current `skip_sites` as
  a reminder.
- **Pause:** `--pause 30m | 1h | until-tomorrow`, and `--resume`. While paused, nothing is recorded. `--install`
  adds two desktop shortcuts, *Pause screen capture (1 hour)* and *Resume screen capture*.
- **Redaction** (`redact.py`) applies to every stored field. Titles first go through `urlclean.clean_title` (URL
  secrets, 300-character clip). Then titles and text both get the secret patterns:
  - `sk-…`, `gh[pousr]_…`, `xox[abpr]-…`, `AKIA…`/`ASIA…`;
  - JWTs;
  - a secret word (`key`, `token`, `secret`, `password`, …) followed by `:`/`=` and a long value.

  Rule matching uses the raw title in memory. Only cleaned titles are stored.

### 5. Storage (`vault.db`)

The tables are created by `export.open_vault()` with `CREATE TABLE IF NOT EXISTS`, like every other v0.2.1 table.

```sql
CREATE TABLE IF NOT EXISTS screen_stretches (id TEXT PRIMARY KEY, started TEXT NOT NULL, ended TEXT NOT NULL,
                                             app TEXT NOT NULL, title TEXT);
CREATE TABLE IF NOT EXISTS screen_text (id TEXT PRIMARY KEY, stretch_id TEXT NOT NULL, created TEXT NOT NULL,
                                        text TEXT NOT NULL, text_hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS screen_removal_targets (removal_id INTEGER NOT NULL, stretch_id TEXT NOT NULL,
                                                   PRIMARY KEY (removal_id, stretch_id));
-- indexes: screen_stretches(started), (ended), (app); screen_text(stretch_id)
-- meta('screen_removal_gen'): bumped by every screen removal, never reset
-- meta('screen_initialized'): set once, never cleared
```

**Guarded writes** [F1]. Every watcher write (open, extend, close, text) is one short `BEGIN IMMEDIATE` transaction
on a watcher connection opened with a 250 ms busy timeout from the start [P11]. Inside it:
1. The removal generation must equal the one the write was observed under. Otherwise it's **stale**: the watcher
   drops its open stretch, queue, read in flight and text caches, adopts the new generation, and opens a fresh
   stretch at the next poll. A stretch a removal deleted is never recreated.
2. For an extend, close or text write, the parent stretch row must still exist (otherwise stale).
3. A text write also carries the settings generation it was read under. Text from an earlier generation never
   reaches the queue, because acceptance checks it, and a generation change empties the queue's text.

**Unchanged text** [P6]: deduplication is per stretch. Each stretch's first readable text is always stored. A later
read in the same stretch is stored only if its hash differs from the last text *stored* for that stretch. A hash
counts as stored only after its insert commits; while a matching text is still queued, a duplicate isn't queued
again.

**The queue** [R4, P11]. Writes that fail with `SQLITE_BUSY` or another database error wait in memory, organized
by stretch:
- each stretch keeps its latest state (an unwritten open absorbs later extends and its close), with its text in
  order behind it, so an open is always written before its text;
- every entry keeps the generations it was observed under;
- a flush runs once per poll, in order, and stops at the first failure, after 20 writes, or after 250 ms, whichever
  comes first;
- caps: 50 stretches and 2,000,000 bytes of text. Over the text cap, the oldest queued text is dropped. At 50
  stretches, the queue refuses new stretches and the watcher records a visible "database busy" gap instead;
- a non-busy error (disk full, I/O) keeps the write queued and is reported in the heartbeat and `status`.

Drops and gaps are counted. Nothing is dropped silently.

The watcher never takes the nightly lock. `vault.db` is in WAL mode, so the nightly backup's SQLite backup API takes
a consistent copy while the watcher writes.

### 6. Search, timeline and the MCP tools

**One snapshot per build** [F7]. `index.build()` opens one explicit read transaction on the vault before its first
query and keeps it until the last vault row is copied, ending it before the embedding step.

**What goes into `search.db`:**
- `events` gains `source` (`pieces` / `screen`) and `stretch_id`. Each `screen_text` row becomes an event (app and
  cleaned title from its stretch, the text as `readable`, `source = 'screen'`). Screen text is keyword- and
  meaning-searchable, like Pieces captures.
- `screen_stretches` is copied, with `stretches_fts(title, app)`. The `screen` search source finds stretches by
  title, keyword only (no embeddings). With `mode="meaning"`, the response says so.
- `meta('screen_cutoff')` holds the latest `ended` inside the build's snapshot. Search results say "search covers
  screen activity up to <time>; timeline() shows it live".
- An index without these columns or tables (built by v0.2.1) is treated as Pieces-only.

**Search hits** [R7, P9]:
- A `screen` hit's time is `started`, and range filters use the overlap rule below.
- Results show one hit per stretch: when OCR text and the title of the same stretch both match, the OCR row is
  shown, because it's the more specific one, and `get_memory` on it reaches the stretch.
- Bounds are normalized once per request. A bad or reversed range returns a "Bad range" answer in every mode.

**`timeline()` shows screen activity live** [F2, P8]. After its existing chronological list (summaries, browsing,
digests, still bounded by `limit`), it adds a separate **Screen** section, read straight from `vault.db` in one read
transaction. The section shows up to `limit` stretches overlapping the range, newest first, including the one open
now. It needs no search index. `limit` must be 1–500.

**`get_memory`** on a `screentext:` id returns that text row and its stretch's app, title and times. On a
`screen:` id it returns the stretch and its newest 20 text rows, with a note saying how many more exist and how to
read them. Both read live, with limits in SQL. Both run inside the existing `@guarded` wrapper, so the removal
marker and the publication stamps are checked before and after the whole response. A vault without screen tables
answers "no memory with id …".

**One overlap rule** (`screen.overlaps`, `screen.overlap_sql`), used by `timeline`, search and forgetting:
`started < until AND ended >= since`. `ended` is the last observation, inclusive, so a zero-length stretch exactly
at `since` is included.

**Bounds**:
- **MCP tools** keep the existing convention: a date-only bound is that UTC day, inclusive for `until`; a time
  without a zone is UTC; an omitted `until` is now. The normalized range must not be empty (`since < until`),
  otherwise it's a bad range [P9].
- **The `forget` command** takes *local* input [P4]:
  - a time without a zone, or a date, is local wall time (a date means local midnight; an until date includes its
    whole day);
  - an explicit offset or `Z` is respected;
  - an ambiguous time (the repeated hour when clocks go back) widens: the earlier instant for `since`, the later
    for `until`;
  - a time that doesn't exist (the skipped hour when clocks go forward) is an error naming the gap;
  - the command shows the normalized local range before asking, and only canonical UTC bounds are stored.

**Atlas and digests** [F8, P7]:
- Pieces counts and app votes read only `source='pieces'` events, or every event on an old index.
- Screen time is a separate series: each stretch's elapsed seconds are split across the local hours and days it
  spans. The split steps through UTC, so time across a daylight-saving change is counted correctly.
- **The Atlas** adds a "Screen time" tile and a "Screen time by app" chart. The "Top apps" chart is labeled as
  Pieces captures.
- **Digests** add "Screen time by app" and "Longest windows" (cleaned titles by duration) for every day with
  screen time, including days with nothing else. Screen days are added to the set of days a digest covers. Digests
  never include screen text.
- **`inkvault status`** keeps the Pieces capture count, labeled as Pieces, and adds the screen lines (§7).

### 7. Installing, starting at sign-in, stopping (`schedulers/windows.py`, `watch.py`)

**`inkvault watch --install`:**
1. **Prepare the home** (§3). With a terminal, ask the two questions from product decisions 6 and 7. Without one,
   both stay off.
2. **Check the tool:** the installed tool's version must be ≥ 0.3.0 (otherwise it says to reinstall with
   `inkvault[screen]`), and its argv must pass `schedule.check_task_scheduler_argv`.
3. **Register** "InkVault Watch":
   - trigger: `LogonTrigger` for the current user; principal: that user, `InteractiveToken`;
   - settings: `ExecutionTimeLimit` `PT0S`, restart every 1 minute up to 3 times, `MultipleInstancesPolicy`
     `IgnoreNew`;
   - action: the tool's `pythonw.exe -m inkvault --home <home> watch`.
4. **Read it back** (`schtasks /Query /XML`) and check every one of those fields. A mismatch is an error, and the
   task is removed.
5. **Start it** (`schtasks /Run`) and wait up to 15 s for `watch.lock` and a fresh heartbeat. Only then does it say
   "screen capture is on now and at every sign-in". If neither appears, it says "registered, but the watcher hasn't
   confirmed it's running" and exits nonzero.
6. **Add the pause and resume shortcuts.** A failure here is reported, not fatal.

**`inkvault watch --uninstall`** [R9, P5], in order:
1. Read the task back. If its pinned `--home` is unknown or another home, refuse and change nothing.
2. Disable the task and read back that it's disabled.
3. Ask the watcher to stop (`watch.stop`). This also reaches a hand-started watcher.
4. Wait up to 15 s for `watch.lock` to be released.
5. Delete the task and read back that it's gone.

The task is never force-ended before the cooperative stop. If the lock isn't released, the command says "screen
capture may still be running", leaves the task disabled, and exits nonzero. It never kills a pid read from a file.

**`inkvault watch --stop`** is the same cooperative stop for a hand-started watcher.

**`inkvault status`** shows, as separate lines: the watcher (heartbeat age, app in front, paused, cooldowns, queue,
drops, last error), the sign-in task (present, enabled), today's and total stretches and text reads, and whether
text capture is available (the extra is installed and an OCR language exists).

### 8. Forgetting (`forget.py`, `inkvault forget --screen`)

`inkvault forget --screen --app <exe>` or `--since <time> --until <time>` (local input, §6), with `--yes` to skip
the question.

**Selection:**
- `screen-app`: every stretch of that app, case-insensitively.
- `screen-range`: every stretch matching the overlap rule, removed whole, including the open one.
- An unknown kind is an error, never treated as a browser site.

**Recording targets, not predicates** [R3]. In one `BEGIN IMMEDIATE` (the same writer lock the watcher's writes
take): select the matching stretch ids; write the removal row and the ids into `screen_removal_targets`; record the
affected days; bump `meta('screen_removal_gen')`. The rebuild marker goes up before, as browser removals do. A
request matching nothing records nothing and says so.

**Affected days:** every UTC date from each target's `started` to its `ended`, plus the day before and after, the
same superset browser removals use for digests made in another time zone.

**Applying** (`apply_pending()`, idempotent): delete the recorded targets' text, then the stretches, then the
target rows and the removal row. A retry hours later deletes the same set, never anything captured since. Digests,
the dashboard, `search.db` and vectors are invalidated through the existing marker lifecycle.

**Afterwards:**
- The generation bump makes the watcher drop its open stretch and any read in flight. It starts a new stretch at
  the next poll. So forgetting an app only removes the past, and the command ends with "screen capture still
  records X: to stop, `inkvault watch --never-app X`".
- Backups made before the removal, and answers already given to an AI client, aren't erased. The command says so.

## Rules added by the plan recheck

These sharpen §1, §3, §6 and §7. Each is a test in the plan.

- **Settings, wherever they're loaded,** apply their transition before anything else happens. A change noticed
  while accepting a result empties the queue's text before that tick's flush. A flush also drops any text not
  from the current settings generation.
- **The worker is supervised every poll**, whether settings are valid or not. Deadlines and cleanup never pause.
- **Controls need no vault and no settings.** `--stop` and `--uninstall` work from the home, the lock and the
  task alone. Settings edits touch only `screen.json`, and prepare the home only if the file doesn't exist.
- **Task state is never guessed.**
  - "No task" means the full task list confirms it's absent. Anything unreadable is *unknown*, and every change
    refuses on unknown.
  - Read-back includes whether the sign-in trigger is enabled.
  - Install's confirmation needs a heartbeat from the current lock holder, no older than 10 s and not from the
    future, written after the start attempt.
  - A watcher that was already running is reported as such; install doesn't claim to have started it.
- **An unreadable removal generation is withheld, never treated as zero.** The tools answer "the vault can't be
  read right now".
- **The heartbeat carries separate errors** (settings, database, heartbeat), and a waiting state while the vault
  can't be opened.
- **Worker errors are logged only as a class-like name.** Requests are validated before they're sent.
- **Digest budgets:** the screen and web parts are reserved together before the rest is clipped.
- **Screen time** is exact elapsed time everywhere. Hour labels assume offset changes fall on local-hour
  boundaries, which is true everywhere except a few half-hour daylight-saving zones.

## Unchanged

The nightly job's steps (indexing simply finds the new rows); macOS and Linux (`inkvault watch` there says "screen
capture is Windows-only for now").

## Out of scope

Hotkey; keeping images; audio; macOS/Linux; per-tab browser URLs (browser history has them, and the timeline lines
them up by time); a tray icon; OCR text in digests; splitting stretches at a forget range's edges; Windows Store
(UWP) apps' text. Their frame (`ApplicationFrameHost.exe`) holds no text, so the right executable would come from
the hosted child window, which this version doesn't do.

## Testing (the gates before release)

Every gate below is a test in the plan. The live trial run (one day: CPU under 1%, database growth, a manual look
at stored titles and text) comes after them, never instead of them.

- **Pure core:** flicks; ending rules; a backwards clock; dwell vs. read budget; process identity.
- **Settings:** type validation (a number for `paused_until`); same-mtime recovery; first-run preview; lost
  settings after forgetting everything.
- **Store and queue:** two-connection races (removal between writes, during a read, across a restart); real
  contention with more than 50 window changes; the cap; flush budgets; disk errors at open, read, commit and
  heartbeat.
- **Supervisor**, with a real synthetic worker:
  - a hang, then a pause; a hang, then a removal;
  - repeated abandon and resubmit;
  - a worker that never reads its input;
  - malformed JSON shapes; a late answer, or one with a foreign id;
  - an oversized Unicode title;
  - the watcher process dying, which must end the worker.
- **Native**, against windows the tests create themselves:
  - typed bindings, capture and identity on 64-bit;
  - GDI handle counts stay flat over repeated captures;
  - DPI awareness, and capture size equal to the window's size;
  - an OCR round trip of a known string.
- **Forgetting:**
  - edges equal to `timeline`'s; midnight; daylight-saving changes;
  - local vs. explicit-offset input;
  - request, then new capture, then a delayed apply;
  - an interrupted request; a resume after midnight;
  - browser removals unchanged.
- **Search and timeline:**
  - one snapshot under concurrent writes;
  - the Screen section with more rows than `limit`;
  - a home without an index;
  - `screentext:` ids;
  - an old v0.2.1 index;
  - a removal starting during a live read.
- **Atlas and digests:** screen-only days; cross-midnight and daylight-saving durations; no screen text in digests.
- **Scheduler:**
  - the nightly XML byte-identical to a saved fixture;
  - the task name passed through install, query and remove;
  - the watch task's full read-back.
- **Install and uninstall:**
  - an old tool; a failed start; a wrong action;
  - an unknown home;
  - a hand-started watcher; a stop that times out.
