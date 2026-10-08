# Review request for Codex: the browser-history implementation plan

Hi Codex. Thanks for the two design reviews; the spec now has both rounds folded in. This is the step after: the
**implementation plan**, written task by task with the code and tests each step adds. Nothing is built yet. Please
review the plan before anyone implements it.

## What to read

- Branch: `browser-history` (local; not pushed)
- `docs/superpowers/plans/2026-10-08-browser-history.md`: the plan. Read it all.
- `docs/superpowers/specs/2026-10-08-browser-history-design.md`: the design it implements.
- The current code the plan changes: `src/inkvault/{paths,export,index,embed,server,dashboard,digest,cli,nightly}.py`,
  `src/inkvault/atlas.html`, `tests/conftest.py`, `tests/test_nightly.py`.

The plan follows the order you suggested: schema and configuration → cleaning → discovery and consent → snapshot
sync → indexing and embeddings → MCP → dashboard and digests → removal and recovery → CLI and nightly →
documentation and a check on real files.

## Questions

1. **Does the plan implement the spec?** Any requirement with no task, or a task that does something the spec
   doesn't say (or says differently)? One known difference: `sync` and `rescue` prompt only about `new` profiles,
   not every profile; and `inkvault index` clears the rebuild marker and deletes the old dashboard (the spec is
   corrected in Task 10).
2. **Will the code work as written?** Look for bugs a test wouldn't catch: SQL that fails on real Chromium or
   Firefox schemas, upsert behavior, `urlclean` edge cases (encoding round trips, `parse_qsl` quirks, IPv6 hosts,
   fragments), Windows file handling in `snapshot` (copying a file SQLite has open, temp-folder cleanup while a
   connection is open), and Python 3.10 compatibility (the project's minimum).
3. **Will the tests pass, and do they test the right thing?** Any test that passes for the wrong reason, depends on
   the time zone or platform, or would break on Windows paths? Any spec behavior with no test?
4. **Removal and the marker (Task 8):** can a removal still leave stale search, digests or dashboard anywhere,
   including an MCP server that is already running, or the nightly run's order (index → digest → dashboard)?
5. **Task order:** can each task be committed with the full suite passing, as the plan claims?
6. **Task 10, Step 5** runs the result on this PC against copies of the real profiles. Is it safe as written
   (throwaway home, never `chrome/Profile 2`, counts only)?

You don't need to read browser history for this review. If you do want to try the plan's code against real files,
work only on copies in a throwaway folder, leave `chrome/Profile 2` alone entirely, and report counts, not
addresses or titles.

## How to reply

For each finding, give:
- the task and step
- what's wrong, and how you know (a failing case, a line of the current code, a measurement)
- the change you suggest

Please don't commit to the branch. Send the findings back; the plan will be updated before it's built.
