# Atlas CI readiness fixes — 2026-10-14

Reviewed base: `cd168904846178783650bb34de41290ba4b57acc` (`atlas-search`, PR #14).

## Observed failures

Both CI runs at the base commit failed the same five helper-startup tests on macOS/Python 3.13 with only `no helper info`. The child output was discarded, so those logs do not establish the root cause. Windows/Python 3.13 failures varied between a 1.5-second per-tick assertion and the export test's assumption that a 0.3-second real-time budget always permits progress.

## Changes and review

- Atlas now binds its numeric loopback address without HTTPServer's unnecessary reverse-DNS lookup. A regression test that forbids DNS failed before this change and passes afterwards. This removes a demonstrated startup dependency; whether it caused the macOS failures must be confirmed by CI.
- Helper-startup tests report child exit codes, stdout and stderr on failure; failed idle-helper tests also clean up the process.
- Watcher integration tests retain real worker termination, SQLite contention, queue caps and stored-stretch assertions. Their per-tick wall-clock stopwatch is replaced by a separate real-worker test that forbids sleep/wait/communicate inside a tick and checks no timeout at 4.999 seconds and termination/cooldown at exactly 5.0 seconds. Production timeout settings are unchanged; SQLite's production 250 ms timeout remains tested separately.
- Export budget tests use a clock local to the export module and fictional records. They check that a record arriving before the deadline is committed, a record at the deadline is not, and a later unbounded run resumes to completion. Partial-export metadata and status tests use the same deterministic fixture. The separate real slow-fetch cancellation test remains in place.

The source change is limited to server binding; request authentication, numeric loopback binding, Host/Origin checks, and packaged assets are unchanged. Manual patch review found no additional blockers in these changes.

## Validation

- Windows/Python 3.11.15 full suite: 723 passed, 3 skipped.
- Windows/Python 3.13.15 full suite: 723 passed, 3 skipped.
- Windows/Python 3.10.21 focused helper/HTTP/watcher/export/worker/storage suite: 112 passed, 1 skipped.
- Fresh targeted startup/budget/deadline checks: 15 passed, 1 skipped.
- `uvx ruff check --select F src tests`: passed.
- `git diff --check`: passed.
- Patched v0.3.1 wheel: built successfully.

No live-vault access, scheduler changes, install, push, merge, tag or release was performed. Local validation cannot establish macOS behavior: push the reviewed commit and require all six CI jobs to pass before merge/release.
