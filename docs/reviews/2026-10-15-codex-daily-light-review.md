# Codex review request: Atlas Daily Light (branch `daily-light`)

Branch `daily-light`, base 3a079f0 (= tag v0.3.1), head = the commit that adds this file. Not pushed. Unreleased: no
version number chosen. Plan: `docs/superpowers/plans/2026-10-15-daily-light.md` (built from your handoff
"Atlas Daily Light — Claude Code build handoff", 2026-10-15).

Please write findings to `docs/reviews/2026-10-15-codex-daily-light-findings.md`, each with severity (P1/P2/P3),
file:line, a concrete failure scenario, and a suggested fix. Use synthetic fixtures only; don't read a live vault.

## Commits

- cf90dca plan · c6da917 `retrieval.browse` + `api_search` routing + `exploreDay` · d815142 quotes ·
  682f298 daily cards + styling · 3198bc4 changelog

## Checks already run

742 passed, 3 skipped; `ruff check --select F` clean; golden MCP test unchanged. Browser check on a synthetic vault
(desktop + 375 px): both cards render, Explore this day → 1 date hit → reader opens; hostile record text renders as
text; Why/Hide/Show keyboard focus and `aria-expanded` correct; Source link `https:`, `noopener noreferrer`; no
external requests; no console errors; no horizontal overflow at 375 px. Light theme not inspected.

## Please look hardest at

1. **`retrieval.browse`** (correctness and cost): `heapq.merge` across kinds with per-kind batches of 100; the
   screen-stretch de-dup (OCR events row wins) when the matching events row falls in a later batch or outside the
   page; `more` accuracy at page edges; paging cost at `MAX_OFFSET` on a busy day; web pages (day vs visit bounds).
2. **`api_search` routing**: empty query is accepted only with both From and To; generation check on later pages;
   any way to make the date path list a whole vault cheaply (very wide range) — is a range cap needed?
3. **Stale state**: the On this day card and browse results across forget/rebuild (`clearEverything`, atlas
   revision), saved session state restoring a browse after reload.
4. **Dates**: `localDay`, `pickQuote` (day count via `Date.UTC`), `onThisDay` (Feb 29), midnight re-arm timer and
   focus/visibility recheck; `day_bounds` on DST days for a one-day browse.
5. **Quotes** (`src/inkvault/atlas/quotes.json`): spot-check wording against the linked Gutenberg editions and the
   `note` locators — a few chapter/act/scene locators were written from memory, not mechanically checked.
   `source_url` is the whole-book page.
6. **Security surface**: new ASSETS (`/assets/daily.js`, `/assets/quotes.json`), CSP unchanged, no `innerHTML`,
   URL scheme check on Source.

## Known limits (accepted unless you see a problem)

- "Hide daily quote" uses `localStorage`; the helper binds a random port each launch, so it lasts until the Atlas
  closes. No new write API was added.
- Browse lists hits oldest first with no grouping; a chat-heavy day shows one row per message.
