# Recheck request for Codex: the browser-history plan after your review

Hi Codex. Your plan review's eleven findings are folded into both the spec and the plan. Please check that the
fixes hold, and look for anything they broke.

## What changed

- **Spec** (`docs/superpowers/specs/2026-10-08-browser-history-design.md`): §1 "Removing" is now a recorded
  operation (`browser_removals`), applied idempotently by every search build; "One writer at a time"; choices
  written under the lock; forgetting a profile whose folder is gone; MCP tools check before and after. §4 adds
  `pages.path` and deleting `vectors.npz` when nothing is left. §5 defines `web` date ranges. A table at the end maps
  each of your findings to its change.
- **Plan** (`docs/superpowers/plans/2026-10-08-browser-history.md`):
  - Task 2: fragments matched decoded; hostless addresses refused (33 cases, all passing when run from the plan's
    snippets).
  - Task 3: `save_choices` uses a unique temp file; `ask_about_new` returns answers; `record_answers` merges them
    into the file as it is now.
  - Task 5: `pages.path`; the `web` embedding is title, host and path; `embed.build()` deletes old vectors when
    nothing is left. Tests now run real embedding with a tiny fake model instead of skipping it.
  - Task 6: `guarded()` wraps every MCP tool (marker and published-file check before and after); `web_filter()`
    for date ranges; tests for later visits, UTC+14, UTC−11 and a minute before midnight.
  - Task 8: rewritten. `request()` → marker → record (one transaction) → `apply_pending()` → `index.build()` (which
    applies pending removals first and clears the marker only with none pending). Narrow "no such table" handling.
    A parametrized test stops the removal at each step and checks the next search build finishes it; a locked
    digest database; a request in flight while a removal runs.
  - Task 9: `locked()` around `index`, `digest`, `dashboard`, `browsers` and all of `sync` (through indexing);
    answers collected before the lock and merged under it; `--no` accepts keys from `browsers.json` or the vault;
    `lock_purpose()` knows the new purposes; tests with a second process holding the lock.
  - Task 10: the real-machine commands are replaced by `tests/manual/browser_check.py`, which copies only the
    named History files, never lists a browser folder, prints counts only and deletes its folder in `finally`.

## Questions

1. Does each fix actually close its finding? In particular: can any order of interruption, concurrent command, or
   in-flight MCP request still serve or resurrect removed visits?
2. Did the fixes introduce new problems (deadlocks, a command that now holds the lock while waiting for a person,
   a test that passes for the wrong reason, Windows file-replacement behavior)?
3. Is `tests/manual/browser_check.py` safe to run on this PC as written? Please read it; don't run it.

Same rules as before: don't read browser history, don't commit to the branch, and send findings with the task, the
problem, how you know, and the change you suggest.
