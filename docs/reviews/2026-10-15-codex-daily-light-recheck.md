# Codex recheck request: Atlas Daily Light fixes

Branch `daily-light`, reviewed head 6632c05, fixes in **e6688b9** (findings 1–3) and **3fe414d** (finding 4); your
findings are committed as `docs/reviews/2026-10-15-codex-daily-light-findings.md`. Not pushed. Full suite 747 passed,
3 skipped; `ruff check --select F` clean.

Please confirm each fix and look for regressions. Append results to the findings file under a "Recheck" heading
(same format: severity, file:line, scenario, fix). Synthetic fixtures only.

1. **Browse cost** (`retrieval.browse`, `index.py`): OCR-backed stretches are suppressed in SQL (`NOT EXISTS` on
   `events.stretch_id` with the same range/chip filters); keyset paging on (time, id) replaces OFFSET batches; new
   index `events_stretch ON events(stretch_id)`, with an older index still correct (slower). Regression
   `test_browse_screen_suppression_work_is_bounded_by_the_page`: 5,000 OCR-backed stretches, first page fetches 100
   rows (was 5,100). Please rerun your 20,000-stretch reproduction. Check NULL-time rows and ties at batch edges.
2. **Incomplete date range** (`search.js`): clearing From or To during a date-only browse advances the turn counters,
   clears results/reader/More/saved selection, shows "Choose both dates to list a day or range.", and sends nothing.
   Test `tests/js/date_range.cjs` (incl. a late answer after clearing).
3. **Offset ceiling** (`atlasserve.api_search`, `search.js`): `more` is false and `capped: true` when
   `offset + PAGE_SIZE > MAX_OFFSET`, for search and browse; the page hides More and says to narrow dates or pick a
   source. Tests `test_more_is_never_offered_past_the_offset_ceiling`, `tests/js/capped.cjs`.
4. **Quotes**: Dickinson ×3 now "Poems, Third Series (1896)" with Life VI/XVI/XLIII locators; the Whitman entry is now
   `whitman-miracles-1`, located in "Miracles". The remaining locators were rechecked mechanically against the linked
   Gutenberg texts, and the three Ruskin passages now use the em dashes printed in #1293 instead of "--".

Still open on my side: light-theme inspection of the cards.
