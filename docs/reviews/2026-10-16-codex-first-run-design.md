# Codex review request: first-run sync design (branch `first-run`)

Branch `first-run`, base `63b7139` (public main: v0.3.2 + README #16), head = the commit that adds this file. Not
pushed. Design only; no code yet. Spec: `docs/superpowers/specs/2026-10-16-first-run-design.md`. Target 0.3.3.

This is a quick design review. Please write findings to
`docs/reviews/2026-10-16-codex-first-run-design-findings.md`, each with severity (P1/P2/P3), the spec section, a
concrete failure scenario, and a suggested fix. Read the current code to check the spec against it; use synthetic
fixtures only, don't read a live vault.

## Please look hardest at

1. **Output leaking where it shouldn't**: any path where colour codes, `\r` or a spinner reach the nightly log
   (`nightly.py` wraps stdout; see its `isatty`), a pipe, or a file; any path where the browser opens from a
   non-interactive run. Is `sys.stdout.isatty()` the right test given `browsers.interactive()` uses stdin?
2. **Auto-open and the lock**: `cmd_sync` holds the `sync` lock; the spec opens the Atlas after it's released, as
   `rescue` does. Any race with a nightly run starting in between, or with an Atlas helper already running?
3. **Chunked encoding**: does splitting `model.encode` into chunks change vectors (model2vec batching, padding,
   normalisation) or cost noticeably more time on ~120k records?
4. **Summary accuracy**: can totals and "new" counts be read cheaply and truthfully after the sync, including
   partial and failed outcomes, Hermes and typed prompts, and a removal recovered in the same run?
5. **Windows console**: enabling VT processing via `SetConsoleMode`; old conhost, Windows Terminal, and
   PowerShell 5.1; stdout encoding under `uvx` (cp1252) and the ASCII fallback.
6. **Scope**: anything here that should be cut to keep 0.3.3 small, or anything missing a first-time user will hit.

## Known limits (accepted unless you see a problem)

- The model download shows a spinner, not a byte-level bar (model2vec/huggingface download hooks not used).
- Styling is not configurable beyond `NO_COLOR`.
