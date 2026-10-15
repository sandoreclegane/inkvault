# Atlas Daily Light — implementation plan

Source brief: "Atlas Daily Light — Claude Code build handoff" (2026-10-15, kept outside the repo). Public work, branch
`daily-light` from public main 3a079f0 (= tag v0.3.1, no differences). Baseline: 723 passed, 3 skipped.
Unreleased: no version number is chosen here.

## Audit results (brief step 1)

- **Date-only browsing does not exist.** `atlasserve.api_search` refuses an empty query and `retrieval.search` is
  ranked by keyword/meaning only. Smallest extension: `retrieval.browse(since, until, *, kinds, event_source, limit,
  offset)` — the same range filters (`range_filter`), the same chip kinds, the same screen-stretch de-duplication and
  the same `Hit` shape, ordered by time (oldest first), with no snippet. `api_search` routes an empty query to it
  **only when both From and To are set**; otherwise the old "Type something" refusal stands. `hit_json` gets a third
  match type, `"date"`, for these hits (never labelled "related by meaning"). MCP (`server.py`, `search()`) is
  untouched; the golden test must stay byte-identical.
- **Preference lifetime.** The helper binds port 0, so the page's origin changes every launch and `localStorage` does
  not survive a relaunch (the date-range preset already lives with this). The hide/show quote preference uses
  `localStorage` under the established try/catch pattern and is reported honestly as "lasts until the Atlas closes".
  No new write API.
- **On this day data.** `atlas.json`'s `DATA.days` (keys = local ISO days with any indexed activity) is the
  candidate list; it is generation-guarded by `atlas_revision` already, and the card re-renders whenever
  `loadAtlas()` renders new data and is removed by `clearEverything()`. The card claims no counts.
- **Assets/CSP.** New files are served only via `atlasserve.ASSETS`: `/assets/daily.js` (text/javascript) and
  `/assets/quotes.json` (application/json, fetched with `connect-src 'self'`). Packaging already includes
  `src/inkvault/atlas/*`; a test asserts both are in the built wheel's file list or the package data glob.

## Tasks

1. **Quote collection** — `src/inkvault/atlas/quotes.json`: `{"version": 1, "quotes": [...]}`, ≥ 30 entries, each
   `{id, text, author, work, source_url, note}`; public-domain works, wording checked against the linked primary
   source (Project Gutenberg, Wikisource, archive.org, or a publisher's edition), excerpts marked with "…".
   `tests/test_quotes.py`: schema, unique stable ids, https URLs only, text ≤ 280 chars, no empty fields.
2. **Date-only browse (backend + search.js)** — `retrieval.browse`, `api_search` routing, `hit_json` `"date"` type;
   `search.js` exposes `exploreDay(day)`: clears the query, sets source "all", From = To = day, runs the normal
   search path, shows the day in the date inputs, and keeps the normal Back behaviour. A browse hit opens in the
   reader like any other. Tests: pytest for browse (range edges incl. DST day, chips, stretch de-dup, paging,
   forgotten records absent, empty query without dates still refused) and a `.cjs` scenario for `exploreDay`.
3. **Daily cards (front end)** — `src/inkvault/atlas/daily.js` + markup in `index.html` above the activity charts:
   - pure helpers (`localDay`, `pickQuote`, `onThisDay`, `msToMidnight`) tested in `tests/js/daily.cjs` with fake
     clocks: UTC-vs-local date difference, year rollover, Feb 29 (matches only an earlier Feb 29), no eligible day.
   - Quote card "A little light" + local date, `<blockquote>` + `<figcaption>`; "Why this quote?" disclosure
     (`aria-expanded`) with the fixed sentence; "Source" link (https only, `rel="noopener noreferrer"`,
     `target="_blank"`, labelled as external); "Hide daily quote" / "Show daily quote" with focus moved to the
     remaining control; invalid/empty collection → no card.
   - On this day card: "On this day" / "Revisit October 15, 2025." / button "Explore this day" → `exploreDay`.
     No card when no eligible day or the Atlas data isn't loaded.
   - Recheck the day on `focus`/`visibilitychange` and on a timer set to the next local midnight.
4. **Styling** — Ink & Light tokens already in `atlas.css`; quiet parchment card, 2-column on desktop (quote wider),
   stacked under 700 px, focus rings, `prefers-reduced-motion` respected (no transitions needed).
5. **Checks + report** — full pytest, `node tests/js/*.cjs`, `ruff check --select F`, privacy check; browser check on a
   synthetic vault at desktop and 375 px, keyboard only; CHANGELOG "Unreleased" entry; screenshots; report.
