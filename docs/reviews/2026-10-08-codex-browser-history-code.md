# Review request for Codex: browser history, the built code (Tasks 1–8)

Hi Codex. The plan you reviewed three times is now built through Task 8, on branch `browser-history` (local; not
pushed). Commits `6fbeedc` … `d3bda85`, one per task. The suite is at 338 passed, 1 skipped. Tasks 9 (CLI, locking,
nightly step) and 10 (docs, real-history harness) aren't built yet.

Please review **the code as built**, not the plan. Your earlier reviews found the problems on paper; this one is to
find what the paper missed.

## Where to look

- `src/inkvault/forget.py` (Task 8): recorded removals, `apply_pending`, the marker. This is the priority.
- `src/inkvault/index.py` (`build`: pending removals applied first, the marker cleared only with none pending, the
  databases closed on failure), `src/inkvault/server.py` (`guarded`, `web_filter`, `utc_bound`),
  `src/inkvault/history.py`, `src/inkvault/browsers.py`, `src/inkvault/urlclean.py`, `src/inkvault/embed.py`,
  `src/inkvault/dashboard.py`, `src/inkvault/digest.py`, `src/inkvault/atlas.html`.
- Tests: `tests/test_forget.py`, `tests/test_history.py`, `tests/test_browsers.py`, `tests/test_urlclean.py`,
  `tests/webfixtures.py`, `tests/conftest.py`.
- One change not in the plan: `dashboard.collect()` now treats a `digests.db` with no `digests` table as having no
  digests (the Task 8 test `test_a_digest_database_without_its_table_is_nothing_to_delete` exposed it). Only "no such
  table" is ignored.

## Questions

1. Can removed visits come back, or be served, through any path in the code as it is now (bearing in mind Task 9's
   locking isn't built yet, so judge `forget.py` and `index.py` as if callers hold the lock)?
2. Bugs the tests don't catch: SQL on real Chromium/Firefox schemas, Windows file handling, Python 3.10, encoding.
3. Tests that pass for the wrong reason.

Same rules as before: don't read browser history, don't commit; findings with file and line, the problem, how you
know, and the fix you suggest.
