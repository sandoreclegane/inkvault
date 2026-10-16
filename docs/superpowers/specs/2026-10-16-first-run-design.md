# First-run sync — design

Status: revision 2, 2026-10-16, after Codex design review
(`docs/reviews/2026-10-16-codex-first-run-design-findings.md`; how each finding was handled is at the end).
Base: public main `63b7139` (v0.3.2 + README #16). Branch `first-run`. Target release: 0.3.3.

## Why

A new user's first contact with InkVault is `inkvault sync` (README "Start your vault"). Today it opens with a bare
question about browser profiles, works silently for minutes (a ~130 MB model download, then search), ends without
saying what was saved, and leaves the user to find a second command (`inkvault dashboard`) to see anything. This is
the cheapest step toward the v0.4 goal "value within a few minutes": the first run should say what InkVault is,
show progress, report truthfully what it kept, and open the Atlas.

## Goals

1. **Hello line** before anything else, including the browser question.
2. **Search progress** while search is built, so a long first run doesn't look hung.
3. **Summary** of what the run kept, in units that are true: totals in the vault plus what this run added.
4. **Open the Atlas automatically** when the sync fully succeeds in an interactive session.
5. **Ink & Light styling** in the terminal (teal and gold), with no new dependency.

## Non-goals

- No installer, GUI onboarding or new sources (v0.4 Phase 1 proper). No new importers.
- No change to the vault schema, to what is synced, to MCP tools, or to the Atlas.
- No change to `nightly`, `rescue` or `index` output.
- No byte-level download progress for the search model (spinner only).

## Anti-goals (a failure even if it works)

- Colour codes, spinners or `\r` lines in the nightly log, a piped run, or a file.
- A browser window opening from the nightly job, a script, or a sync that wasn't fully successful.
- A summary that overstates or mislabels: every number names its unit and comes from committed vault data.
- A cosmetic failure (encoding, console mode) stopping or failing a sync.
- A new runtime dependency (`rich`, `colorama`) for cosmetics.

## What the user sees (styled; mockup B, approved 2026-10-16)

```
◆ InkVault  your memory, on this computer · nothing is uploaded

Which browser profiles are yours?
  1  Chrome · Person 1 (you@gmail.com)
  2  Edge · Profile 1
Numbers separated by spaces, all, or none: 1 2

Saving…
  ✓ Claude Code         812 sessions   14 updated this run
  ✓ Codex               428 sessions    3 updated this run
  ✓ Browser history  52,310 visits   +960 new (2 profiles)
  ⠹ Building search…  ━━━━━━━━━━━━━━━━━━━━╺━━━━━━━  71%
  ✓ Search              up to date

Your vault is ready. Opening the Memory Atlas…
Next time: inkvault sync  ·  skip the browser: --no-open
```

Colour: gold (`◆ InkVault`, list numbers), teal (`✓`, new/updated counts, "Your vault is ready."), amber (`!`
for partial), dim for secondary text, bold for totals. Exact column widths are the builder's.

The **plain rendering** uses the same words with ASCII-safe glyphs (see Encoding) and no colour, no spinner and no
`\r`: progress becomes stage lines printed once (`Building search: keyword index...`,
`Building search: meaning search...`).

## Behaviour

### Three separate decisions

1. **Use the new output at all**: only `inkvault sync` with `sys.stdout.isatty()`. Otherwise `sync` prints what it
   prints today, byte for byte.
2. **Decorate** (colour, spinner, redraw): stdout is a TTY **and** `NO_COLOR` is unset **and** `TERM != "dumb"`
   **and** (on Windows) enabling VT processing succeeded. Else the plain rendering.
3. **Auto-open the Atlas**: stdin **and** stdout are both TTYs (the same interactivity test the browser question
   uses, plus stdout), **and** the sync fully succeeded (exit 0, see Success), **and** `--no-open` wasn't given.
   TTY checks can't identify every script; this is the defined boundary, and `--no-open` is the escape hatch.

### Success

A sync is fully successful only when every source that was found and every chosen browser profile was read with no
unreadable files, and search was built. `sources.sync` today returns `True` even when some files were unreadable
(Codex P1); it changes to return a structured result per source (found, saved-session total, sessions updated this
run, lines added, unreadable count). Callers that test its truthiness (`cli.sync_sessions`, `nightly.step_sync`) are
updated deliberately so their current behaviour is preserved except that unreadable files now count as **partial**
in `cmd_sync` (and so exit 1, as browser partials already do). Partial runs still print the summary, mark the
affected line with `!` and the existing reason, do **not** auto-open, and end with "Open it with: inkvault
dashboard".

### Hello line

Printed first, before profile discovery or the question:
"InkVault keeps a private copy of your AI sessions and chosen browser history on this computer. Nothing is
uploaded." (styled form above).

### Browser question

Same logic and answers as today (`browsers.ask`); only the presentation changes (heading, numbered list styling).
Answer parsing is unchanged.

### Progress

An optional **reporter object is passed explicitly** from `cmd_sync` through `index.build` → `embed.build` →
`load_model`, defaulting to `None`. No module-wide state. With `None` (nightly, index, rescue), output is exactly
as today.
- keyword index stage: indeterminate spinner with the stage name;
- model download (first run only): indeterminate spinner, "Downloading the search model (once, ~130 MB)";
- meaning search: determinate bar, by encoding in chunks instead of one `encode` call per kind; percentage = rows
  encoded / total rows.

The reporter owns any spinner thread. It is stopped and joined (and the line finished) in a `try/finally` before any
ordinary print, prompt, error or return, including on exceptions. Redraw at most ~10 times a second.

**Chunking must not change search.** Chunk boundaries are multiples of the current `batch_size` (1024). Validation:
- stub tests for row order, ids/kinds alignment across chunk boundaries, empty kinds, and progress totals;
- an opt-in integration test (skipped when the model isn't cached; no download in CI) that encodes synthetic texts
  crossing the 1024 and chunk boundaries through the old single-call path and the chunked path with the real
  model, and requires identical float16 vectors, ids and kinds;
- a one-off offline benchmark on ~120k synthetic records, old vs chunked, recorded in the build notes. If chunking
  costs more than ~10%, enlarge chunks or report progress per kind instead.

### Summary

One line per source **found on this computer**, from committed vault data, read **while still holding the sync
lock** (after any pending removal and the imports), and printed after search is built:
- **AI sessions**, one line per source in `sources.SOURCES` that was found (today Claude Code, Claude desktop,
  Codex): total **sessions** saved for that source, and **sessions updated this run** (sessions that gained lines).
  Raw line counts are not shown.
- **Browser history**: total **visits** saved for the chosen profiles, and **new visits this run** (the sum of
  `history.store`'s committed per-profile inserts, which `history.sync` now returns alongside its outcome), with
  the chosen profile count. Pages are not shown.
- **Search**: "up to date", or the failure.

A source that was found but whose read failed outright shows `!` with the reason and no totals claim beyond what's
committed. Sources not on this computer are omitted (no "not found" lines in the new output). Successful commits in
a partial run are still counted. If a removal was finished in this run, the existing "Finished a removal that had
been interrupted." line is kept, and totals are simply what's in the vault now; "this run" counts never subtract.

### Auto-open

After the sync lock is **released** (summary already captured under it), when the auto-open test passes, call
`atlasserve.start_detached()` and print "Opening the Memory Atlas…". Note that `rescue` calls it from inside its
locked call chain; `sync` must not copy that placement. If launching raises `OSError`, the sync still counts as
successful: print "Couldn't open the Atlas; open it with: inkvault dashboard". `Popen` only confirms dispatch, so
the message says "Opening", not "Opened". An already-running Atlas helper is reused by `atlasserve` as today.

New flag: `inkvault sync --no-open`.

### Encoding

Every string the new output emits is written through one function that renders against the **actual** stream
encoding: if `sys.stdout.encoding` is missing, or can't encode the full styled message set (including `…`, `·`,
`—`, `◆`, `✓`, `━`, spinner frames), the whole output switches to an ASCII message set (`...`, `-`, `*`, `+`, `#`,
`|/-\`). A cosmetic encoding problem must never raise.

### Windows console

Enable VT processing with `GetStdHandle`/`GetConsoleMode`/`SetConsoleMode` using correctly typed handles
(`ctypes.wintypes`), OR-ing `ENABLE_VIRTUAL_TERMINAL_PROCESSING` into the existing mode bits. Restore the original
mode on exit only if this code changed it. Any failure means plain rendering, never an error. A short manual smoke
check on Windows Terminal, PowerShell 5.1 and legacy conhost is part of the build notes; mocked tests don't prove
real console behaviour.

### README

"Start your vault" becomes one command (`sync`), noting the Atlas opens by itself; `dashboard` stays documented for
later visits. The README's source list is not changed by this release.

## Constraints

- Python 3.10–3.13, Windows/macOS/Linux; CI on all three.
- No new dependencies. Escape codes, encoding fallback and the Windows console call live in one small module
  (e.g. `src/inkvault/term.py`).
- Existing tests that capture `sync` output keep passing (they don't run in a TTY).

## Tests

- Decision matrix: stdout TTY/non-TTY × stdin TTY/redirected × `NO_COLOR` × `TERM=dumb` × failed VT setup; check
  output mode, decoration, and auto-open separately (stdin redirected + stdout TTY: new output, no auto-open;
  stdin TTY + stdout redirected: today's output, no auto-open).
- Plain and non-TTY modes emit no ESC and no `\r` for hello, question, progress, summary and error messages.
- Full message set written through strict `ascii` and `cp1252` streams without error.
- Sources: mixed readable/unreadable files → partial, exit 1, no auto-open; all unreadable → partial/failed;
  resumed sessions count as updated, not new; truthiness callers keep working.
- Browser: repeated visits and filtered navigation types; new-visit count equals committed inserts; a vanished
  profile folder → partial.
- Summary under removal recovery: totals equal the vault, no negative "this run" counts.
- Reporter: a styled sync that fails mid-build, then `index` and a nightly run in the same process emit no ESC or
  `\r` and no late thread writes.
- Chunking: stub tests and the opt-in real-model parity test above.
- Auto-open: once on full success in an interactive session, after the lock is released; never on partial,
  failure, "Nothing to sync", non-interactive, or `--no-open`; `OSError` from launch leaves exit 0 and prints the
  fallback. No real browser is started in tests.
- Golden MCP test unchanged.

## Left to the builder

Module layout, the reporter's interface, chunk size (a multiple of 1024), spinner frames, column widths and the
exact ASCII message set, within the constraints above.

## Review disposition (Codex findings, 2026-10-16)

1. **P2 auto-open needs stdin too**: accepted; three separate decisions, auto-open needs stdin and stdout TTYs.
2. **P1 unreadable files counted as success**: accepted; structured per-source results, unreadable → partial, no
   auto-open; existing callers updated deliberately.
3. **P2 count units**: accepted; sessions + sessions updated this run, visits + new visits; no raw lines or pages;
   Hermes/typed prompts not promised; counts taken under the lock; never subtract.
4. **P2 chunking proof**: accepted; stubs plus opt-in real-model parity and an offline benchmark.
5. **P2 ASCII fallback**: accepted; whole-message-set rendering against the real encoding; never raises.
6. **P2 progress state**: accepted; explicit reporter, no globals, threads joined in `finally`, sequential test.
- **Lock note**: accepted; open after release, unlike rescue; `OSError` fallback.
