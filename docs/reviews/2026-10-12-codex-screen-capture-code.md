# Review request for Codex: screen capture, the built code (Tasks 0-19)

Hi Codex. The screen-capture plan is built on branch `screen-capture` (local, not pushed), from the public tag
v0.2.1. Version 0.3.0. The suite is at 551 passed, 1 skipped on the build machine. Task 20 (publishing) hasn't
happened.

Please review **the code as built** against the design (`docs/superpowers/specs/2026-10-12-screen-capture-design.md`,
rev 5, including "Rules added by the plan recheck") and the plan (`docs/superpowers/plans/2026-10-12-screen-capture.md`).
The build ledger (`docs/reviews/2026-10-12-screen-build-ledger.md`) lists every task, its commits, its rulings and
the minor items deferred to this review. Some of those are labeled "for the final review": please triage them, and
say which you'd fix before release.

## Ground rules

- **Do not install anything.** No `uv tool install`, no `inkvault watch --install`, no pip installs into the
  system.
- **Do not create, change, run or query scheduled tasks.** No `schtasks`.
- **Do not touch a live vault.** Use an `INKVAULT_HOME` under a temporary directory for everything.
- **Do not read browser profiles** other than ones a test creates itself.
- **Never print window titles or screen text** (from tests you run, logs, heartbeats or databases), including in
  your findings. Describe a problem by app name, count, time or error class instead.
- Don't commit, push or change the branch. Put findings in
  `docs/reviews/2026-10-12-codex-screen-capture-code-findings.md` as a new file.

## A synthetic gate to run first

These use only synthetic data and windows the tests create. Run them, and report any failure before anything else:

1. **The F1 and R3 races against the real store.** The two-connection tests in `tests/test_screenstore.py` and
   `tests/test_forget_screen.py`, run against the real `screenstore` and `forget` modules: a removal between
   writes, during a read and across a restart, and "request, then new capture, then a delayed apply".
2. **The supervisor with the fake worker.** `tests/test_worker.py` and `tests/test_watch.py`, using
   `tests/fakeworker.py`: a hang, then a pause; a hang, then a removal; repeated abandon and resubmit; a worker that
   never reads its input; malformed shapes; a late or foreign-id answer; an oversized Unicode title.
3. **The index snapshot under concurrent writes.** `tests/test_index_screen.py`: `index.build()` reads one snapshot
   while another connection writes.
4. **Task 11's integration tests.** `tests/test_watch_integration.py`: a real supervisor and real database
   contention.
5. **On Windows, the native tests.** `tests/test_winapi.py` and `tests/test_screentext.py`: typed bindings, GDI
   handle counts over repeated captures, the watcher dying must end the worker (job death), DPI awareness with
   capture size equal to window size, and an OCR round trip of a known string. These only read windows the tests
   create.

Then the full suite: `uv run pytest -q`.

## Where to look

- Priority: `src/inkvault/stretches.py`, `watch.py`, `screenstore.py` (the loop, guarded
  writes, the queue), `worker.py` and `screentext.py` (the supervisor, protocol, capture and OCR), `winapi.py`
  (typed bindings, handles, job objects).
- `src/inkvault/forget.py`, `screen.py`, `screentime.py`, `redact.py`, `index.py`, `server.py`, `digest.py`,
  `dashboard.py`, `cli.py`, `schedulers/windows.py`.
- Tests: all `tests/test_screen*.py`, `tests/test_watch*.py`, `tests/test_winapi.py`, `tests/test_forget_screen.py`,
  `tests/fakeworker.py`, `tests/conftest.py`.

## Questions

1. **Privacy.** Can a title, screen text or the contents of `screen.json` reach a log, the heartbeat, `status`, an
   error message or the pipe to the worker? Can an image reach the disk? Can text be read in an app that isn't a
   text app, a never-app, a never-title window, while paused, locked or idle, or under old settings?
2. **Removal.** Can removed stretches or their text come back or be served, through the watcher's queue, the search
   index, `get_memory`, `timeline`, digests or the dashboard? Are the lock orders (nightly lock then `screen.lock`,
   never the reverse) kept everywhere?
3. **The loop.** Can a bad worker, a busy or failing database, a settings change, a clock jump or an exception in
   one tick stop the watcher, block a poll, or lose the pause, stop or uninstall controls?
4. **Windows specifics.** Handle and GDI leaks, 64-bit handle truncation, `os.replace` on a file another process has
   open (the ledger flags this for `screen.json`), job-object edge cases, Task Scheduler XML and read-back.
5. **Python 3.10 and other platforms.** f-string rules, imports that fail off Windows, behavior of `inkvault watch`
   on macOS and Linux.
6. **Known deferred items.** From the ledger, in particular: `forget --screen --app X --until D` removing all of X
   and ignoring `--until`; an OCR response over the size cap causing a restart on every read of that window;
   `--reset-settings` and edits not handling a busy lock cleanly.
7. **Tests that pass for the wrong reason**, or are skipped silently.

For each finding give the file and line, the problem, how you know (a command or a test you ran, without printing
titles or text), and the fix you suggest.
