# Screen capture: build ledger (0.3.0)

What was built, task by task, against `docs/superpowers/plans/2026-10-12-screen-capture.md` and the design in
`docs/superpowers/specs/2026-10-12-screen-capture-design.md` (rev 5). Branch `screen-capture`, built on the public
tag v0.2.1 (368 passed, 1 skipped). Each task was written test-first and reviewed on its own before the next began.

"Tests" counts the test functions a task's commits added. A "ruling" is a place where the plan or spec text was
changed on purpose, with the reason and what it would cost if the call were wrong. "Deferred" items are minor
findings left for the final review to triage.

## Build-wide notes

- **Branch setup.** Task 0's step to create the branch from the tag was replaced by a separate git worktree on the
  same tag; the spec and plan were copied in as the plan says. Same branch, same base.
- **Native tests share one Tk root.** A second `tkinter.Tk()` in one process fails on some Python builds, so native
  tests use one shared, session-scoped window fixture (`tests/conftest.py`).
- **Test isolation.** `conftest.py` makes any test that tries to start a program other than the running Python fail,
  through both `subprocess.run` and `subprocess.Popen`.

## Per task

### Task 0: test guard and spec/plan
- Commit: `93ef3ff`. Tests: 2.
- Deferred: the guard's arguments are positional-only; one test matches message text.

### Task 1: vault tables, paths, overlap rule, local bounds
- Commit: `f1278b0`. Tests: 6.
- Deferred: `local_bounds` lacks tests for empty or reversed ranges, a missing zone and a daylight-saving date span;
  `overlap_sql` is unparenthesized (only used inside AND chains).

### Task 2: redaction
- Commits: `10ea388`, `afa6856` (fix). Tests: 7.
- Ruling: `clean_title` redacts the raw title first and then runs the URL cleaning and 300-character clip. The plan's
  order let a token cut at character 300 escape masking (reproduced). The 20-character minimum for text is checked
  after redaction. Cost if wrong: a URL-embedded secret shape is masked before URL cleaning instead of after, with
  the same result.
- Deferred: the "word, then long value" rule can over-mask; quoted values with spaces and some key prefixes are
  untested; secrets split across OCR lines aren't matched.

### Task 3: `screen.json`, its lock, preparing a home
- Commit: `7e7eb10`. Tests: 10.
- Deferred, for the final review: `os.replace` can raise `PermissionError` on Windows if the watcher has the file
  open that instant, so a pause could surface a raw `OSError`; a non-contention error from the lock escapes; an edit
  runs its change before validating a non-object file; failed-edit tests don't assert the file is unchanged.

### Task 4: the pure stretch core
- Commit: `30f9843`. Tests: 11.
- Deferred: only the idle clamp is tested, not a normal idle end; `last` is clamped to `started` rather than the
  previous `last`; some internal labels remain in test names.

### Task 5: guarded vault writes
- Commit: `a2bde34`. Tests: 5.
- Deferred: `connect()` doesn't close the connection on failure; error classification matches message text; no
  tests for `StoreError`, extends with a missing parent, or an idempotent re-apply.

### Task 6: per-stretch queue
- Commit: `b208c9c`. Tests: 6.
- Resolved: a stale flush clears the queue (done in Task 10's `reset_after_removal`).
- Deferred: structural writes of already-flushed stretches can exceed the stretch cap (intended, uncommented); the
  text cap evicts per stretch in insertion order rather than globally oldest; no stale-flush or retry tests.

### Task 7: typed Win32 bindings, identity, job objects
- Commits: `5db6764`, `4832603` (fix). Tests: 7.
- Ruling: the plan's fail-closed test uses a window the test creates itself (the global rule outranks the plan
  text). Cost if wrong: none.
- Ruling: "the typed-binding test only checks `argtypes`" was raised from minor to important. The point of the typed
  bindings is to stop 64-bit handles being truncated by a default `int` return type, so the test must catch a
  missing `restype`. Cost: one assertion.
- Deferred: `idle_seconds` returns 0 when the call fails (fail-open); no tests for job-assignment failure or for
  `matches()` and `sample()` natively.

### Task 8: validated protocol and non-blocking supervisor
- Commits: `1b21ddf`, `26a9bc6` (fix). Tests: 14.
- Ruling: `poll()` no longer raises `RecursionError` and `submit()` no longer lets a spawn `OSError` escape, even
  though the plan's code did. A bad worker must never take the watcher's tick down. Cost: none.
- Ruling: "requests may carry extra keys" was raised to important. The design says no title crosses the pipe, so the
  protocol enforces exact keys, and validation happens before the cooldown lookup. Cost: a stricter check.
- Deferred: `close()` may leave a dying process if it outlives the wait; cooldowns are never evicted; no foreign-id
  or worker-crash tests.

### Task 9: the worker (capture and OCR)
- Commit: `054520d`. Tests: 7.
- Ruling: the `screen` extra also includes `winrt-Windows.Foundation.Collections`. `OcrResult.lines` needs it
  (the `ModuleNotFoundError` was reproduced); the design's package list was incomplete. Cost: one small package.
- Ruling: native tests use the shared Tk root with a toplevel per test; assertions unchanged.
- Deferred, for the final review: an OCR response over the 1,000,000-byte cap is sent whole and causes a protocol
  error and restart on every read of that window (the worker could trim lines to the cap); the downscale path
  (`StretchBlt`) has no test; the OCR assertion is skipped silently when no OCR language is installed.

### Task 10: the watcher loop
- Commits: `17b969f`, `5f55dd0` (shared Tk fixture), `a7eaf63` (fix). Tests: 17.
- Ruling: all four important findings fixed (guard removal-generation reads; construct the supervisor and watcher
  inside `run()`'s `try/finally`; re-anchor the scheduler after a stall; add tests for stale-generation adoption,
  fail-closed close time and the acceptance guards). "The heartbeat write must never raise" was promoted to a fix.
- Ruling: the shared Tk fixture went into the same round as its own commit, after a flaky `TclError` across modules.
- Deferred: `db_error` can persist after its write is dropped and is logged every tick; a fatal exit removes the
  heartbeat; the acceptance-guard test doesn't isolate each guard.

### Task 11: integration tests with a real supervisor
- Commit: `790b790`. Tests: 3.
- Deferred: the cap is asserted against the queue's own constant; flush order isn't asserted; the pause and removal
  hang cases pass through the deadline kill alone.

### Task 12: forgetting by app or range
- Commit: `a6a74b9`. Tests: 8.
- Resolved: the timeline uses the shared overlap rule (done in Task 14).
- Deferred: the marker is written inside the open transaction; no idempotency or multi-chunk tests; previewing on a
  pre-0.3.0 vault raises "no such table".

### Task 13: indexing screen rows in one snapshot
- Commit: `3e20b36`. Tests: 2.
- Deferred: no test for a vault without screen tables.

### Task 14: search, `timeline` and `get_memory`
- Commits: `01456a3`, `265d9b2`, `0e16c9e` (fixes). Tests: 10.
- Ruling: an unparseable bound keeps v0.2.1's "Bad date" answer (existing public behavior with an existing test);
  only a parseable but empty or reversed range gets "Bad range". Cost: one message string.
- Ruling: `timeline("yesterday")` no longer raises an uncaught `ValueError` when `search.db` exists; it returns the
  same "Bad range" answer. Cost: none.
- Deferred: a date-only bound is a UTC day in the screen code but a raw text compare in the older date filter
  (pre-existing).

### Task 15: dashboard and digests
- Commit: `1566270`. Tests: 7.
- Resolved: stretches are stored with cleaned titles (done in Task 10).
- Deferred: the tests pin the screen module's time zone, but digest and dashboard day keys use the machine's local
  zone (could fail on far-east test machines); no byte-identical regression test for a vault without screen data;
  per-app rounding can show 0-minute bars.

### Task 16: the sign-in task and its read-back
- Commit: `dce51d6`. Tests: 6.
- Deferred: a literal U+FEFF in a regex (use the escape); `trigger_enabled` defaults true when no logon trigger
  exists; `current_user()` may be empty.

### Task 17: `watch` command and `status`
- Commits: `efcc6ba`, `87abd86` (fix). Tests: 24.
- Ruling: three important findings fixed (removal is verified after a bad read-back, including an unknown task;
  version and argv checks happen before the home is prepared; `status` reports a watcher that holds the lock but has
  no readable heartbeat). Two minor ones were promoted (`status` survives a malformed heartbeat; install reports
  scheduler failures truthfully). The design says to report truthfully and `status` must never break. Cost: small
  extra handling.
- Deferred: a non-interactive reinstall resets the messaging and Chrome answers to off (privacy-safe, as designed);
  `--reset-settings` doesn't handle a busy settings lock; the shortcut script breaks on a home path containing an
  apostrophe; `status` doesn't catch an overflow from an extreme heartbeat timestamp.

### Task 18: `forget --screen`
- Commit: `b92d7c7`. Tests: 6.
- Deferred, for the final review: `forget --screen --app X --until D` ignores `--until` and removes every stretch of
  X. `--until` without `--since` should be rejected. The "nothing more was done" message can be wrong if the request
  had already been recorded; no tests for a non-terminal run without `--yes`.

### Task 19: docs, version, ledger, review request
- This commit. README "Screen capture (Windows)" section, changelog entry, version 0.3.0 in `pyproject.toml` and
  `__init__.py`, `uv lock`, this ledger and the code-review request.

## Totals

368 tests at v0.2.1; 551 passed and 1 skipped at the end of Task 18. Windows-only tests run on Windows and are
skipped elsewhere.


## Final interrupted-fix completion

- Part 1: `308a456` (entry-point preparation, SID/case read-back, title normalization, destructive flag rejection,
  non-decreasing extensions, database-error rate limiting, escaped BOM).
- Final code fixes: `0a94963` (settings replacement/shape checks and control errors, pause/resume notifications,
  stale-stop cleanup, token shapes, old-vault preview, shortcut escaping/error handling).
- Fresh review: two Important findings fixed with failing regression tests first: current raw-title exclusion at
  OCR acceptance, and retrying first-use initialization after transient startup failures. Lost initialized
  settings remain fail-closed. Independent startup probe: valid settings on 3/3 ticks after the fix.
- Verification: synthetic/native gate 82 passed; final-fix components 130 passed; post-review watcher/control/
  integration 78 passed; focused review regressions 6 passed; final complete suite 603 passed, 1 skipped.
  OCR available and exercised; Python 3.10 syntax 64 files passed; diff check and private privacy gate clean.
- Ruling: keep the accepted numeric/edge-symbol title normalization tradeoff with an independent raw-title
  privacy guard. Cost if wrong: distinct work can share a stretch.
- Ruling: recover initialization only when the permanent initialization marker is absent. Cost if wrong:
  genuinely lost settings still require explicit reset or restoration, as the design requires.
- Minor deferred: non-string list members in hand-edited settings can cause an app-edit traceback; the watcher
  rejects the malformed file and records nothing. Previously accepted oversized responses/stale-drop accounting
  and remaining coverage/polish deferrals stand.
- Full evidence and checklist: `2026-10-12-codex-screen-capture-code-findings.md`.
- Task 20: publication awaits explicit maintainer approval. No operational installation or live trial performed.
