# First-run sync — design

Status: draft for Codex review, 2026-10-16. Base: public main `63b7139` (v0.3.2 + README #16). Branch `first-run`.
Target release: 0.3.3.

## Why

A new user's first contact with InkVault is `inkvault sync` (README "Start your vault"). Today it opens with a bare
question about browser profiles, works silently for minutes (a ~130 MB model download, then search), ends without
saying what was saved, and leaves the user to find a second command (`inkvault dashboard`) to see anything. This is
the cheapest step toward the v0.4 goal "value within a few minutes": the first run should say what InkVault is,
show progress, report what it kept, and open the Atlas.

## Goals

1. **Hello line** before anything else, including the browser question.
2. **Search progress** while search is built, so a long first run doesn't look hung.
3. **Summary** of what the run kept: totals plus what's new this run, per source the user actually has.
4. **Open the Atlas automatically** when the sync succeeds.
5. **Ink & Light styling** in the terminal (teal and gold), with no new dependency.

## Non-goals

- No installer, GUI onboarding or new sources (v0.4 Phase 1 proper).
- No change to the vault schema, to what is synced, to MCP tools, or to the Atlas.
- No change to `nightly`, `rescue` or `index` output, apart from the shared progress hook being silent there.

## Anti-goals (a failure even if it works)

- Colour codes, spinners or `\r` lines in the nightly log, a piped run, or a file.
- A browser window opening from the nightly job, a script, or a failed sync.
- A summary that overstates: counts must come from the vault, not from what was attempted.
- A new runtime dependency (`rich`, `colorama`) for cosmetics.

## What the user sees (styled; mockup B, approved 2026-10-16)

```
◆ InkVault  your memory, on this computer · nothing is uploaded

Which browser profiles are yours?
  1  Chrome · Person 1 (you@gmail.com)
  2  Edge · Profile 1
Numbers separated by spaces, all, or none: 1 2

Saving…
  ✓ Claude Code and Codex   1,240 sessions  +312 new
  ✓ Browser history        18,402 pages     +96 new
  ⠹ Building search…  ━━━━━━━━━━━━━━━━━━━━╺━━━━━━━  71%
  ✓ Search                 up to date

Your vault is ready. Opening the Memory Atlas…
Next time: inkvault sync  ·  skip the browser: --no-open
```

Colour: gold (`◆ InkVault`, list numbers), teal (`✓`, `+N new`, "Your vault is ready."), dim for secondary text,
bold for counts. The plain rendering (no colour, no spinner, no `\r`) uses the same words with the progress line
replaced by stage lines that print once (`Building search: keyword index…`, `Building search: meaning search…`).

## Behaviour

**When the new output appears.** Only for `inkvault sync` when stdout is a terminal (`sys.stdout.isatty()`).
Otherwise `sync` prints what it prints today. `nightly`, `rescue`, `index` and the MCP server are unchanged.

**Styling switch.** Styled when stdout is a TTY **and** `NO_COLOR` is unset **and** `TERM != "dumb"`. On Windows,
enable virtual-terminal processing on the console handle (`SetConsoleMode` with
`ENABLE_VIRTUAL_TERMINAL_PROCESSING`); if that call fails, fall back to plain. Glyphs (`◆ ✓ ━ ⠹`) need a UTF-8
capable stdout; if `sys.stdout.encoding` can't encode them, use ASCII (`*`, `+`, `#`, `-`, `|/-\`).

**Hello line.** Printed first, before profile discovery or the question. Wording:
"InkVault keeps a private copy of your AI sessions and chosen browser history on this computer. Nothing is
uploaded." (styled form above).

**Browser question.** Same logic and answers as today (`browsers.ask`); only the presentation changes (heading,
numbered list styling). Answer parsing is unchanged.

**Progress.** A small progress reporter is passed (or set module-wide) for the search build:
- keyword index stage: indeterminate spinner with the stage name;
- model download (first run only): indeterminate spinner, "Downloading the search model (once, ~130 MB)";
- meaning search: determinate bar, by encoding in chunks (e.g. 4,096 rows) instead of one `encode` call per kind,
  percentage = rows encoded / total rows. Chunking must not change the vectors produced (same model, same
  normalisation, same float16 output); a test pins this.
Redraw at most ~10 times a second; the final state is printed as a normal line. With no reporter (nightly, index,
rescue), output is exactly as today.

**Summary.** After a successful build, one line per source present on this computer:
- Claude Code and Codex (and Hermes / typed prompts, if present): session count and new-line count from this run;
- Browser history: pages and chosen profile count, new visits this run;
- Search: "up to date", or the failure.
Totals are read from the vault after the sync; "new" comes from the counts the sync steps already compute
(`sources.sync`, `history.sync`), returned rather than only printed. Sources with nothing on this computer are
omitted. Partial outcomes show `!` (amber) with the existing reason; nothing is reported as saved that wasn't.

**Auto-open.** When the sync returns success (exit 0) and stdout is a TTY and `--no-open` wasn't given, call
`atlasserve.start_detached()` **after the sync lock is released** (same pattern as `rescue`), then print
"Opening the Memory Atlas…". On failure, partial sync, or "Nothing to sync", don't open; print what happened as
today. New flag: `inkvault sync --no-open`.

**README.** "Start your vault" becomes one command (`sync`), noting the Atlas opens by itself; `dashboard` stays
documented for later visits.

## Constraints

- Python 3.10–3.13, Windows/macOS/Linux; CI on all three.
- No new dependencies. Escape codes and the Windows console call are written by hand in one small module
  (e.g. `src/inkvault/term.py`).
- Existing tests that capture `sync` output keep passing (they don't run in a TTY).

## Tests

- Styled vs plain selection: TTY, non-TTY, `NO_COLOR`, `TERM=dumb`, failed Windows console call, non-UTF-8 stdout.
- No escape codes or `\r` in any non-TTY output of `sync`, `nightly`, `index`, `rescue`.
- Chunked encoding yields identical vectors to the single-call path (synthetic rows, fixed model stub).
- Summary: counts match the vault on a synthetic vault; absent sources omitted; partial outcome shown as partial.
- Auto-open: called once on success in a TTY; never on failure, "Nothing to sync", non-TTY, or `--no-open`; called
  after the lock is released.
- Golden MCP test unchanged.

## Left to the builder

Exact module layout, the progress reporter's interface, chunk size, spinner frames, and column widths, within the
constraints above.
