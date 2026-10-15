# Codex review — Atlas Daily Light

Reviewed October 15, 2026. Branch `daily-light`, HEAD `6632c050af335d79c5c36069bc666a8399e9347e`, compared with `3a079f0` (v0.3.1). Worktree was clean before this findings file. Review only: no implementation changes, installation, live-vault access, scheduler changes or publication.

## Findings

### 1. [P2] Screen suppression can scan the entire range before returning the first page

Location: `src/inkvault/retrieval.py:496-508`, especially `498-502`; related lookup `469-479`.

Scenario: browse All sources or Screen over a range containing many stretches with indexed OCR. `heapq.merge` must obtain a first item from every input iterator. The screen iterator suppresses OCR-backed stretches before yielding, so if all stretches have OCR it exhausts the entire selected range before the merge can return even the first OCR result. Every batch repeats an OFFSET query and an OCR existence lookup; the current index has no events.stretch_id index. The page limit and MAX_OFFSET therefore do not bound first-page work.

Synthetic reproduction: 20,000 stretches, one OCR event per stretch, distinct timestamps two minutes apart starting January 1, 2024; index schema includes the production created/source/started/ended indexes. Browse January 1, 2024 through January 1, 2025, limit 20, offset 0. Events only: 0.007 s. Screen plus events with event_source='screen': 2.241 s, for the same 20 returned hits. These are local measurements, not a general performance guarantee. The screen stream walks all 20,000 suppressed rows; larger ranges amplify that cost.

Suggested fix: suppress redundant stretches in SQL using a suitably indexed NOT EXISTS/anti-join, and use keyset iteration instead of growing OFFSET batches. Alternatively merge first and perform bounded dedup with a representative-selection design that preserves the OCR-wins contract. Add a synthetic regression for a large all-OCR-backed stretch range and demonstrate bounded first-page work. A documented maximum date span can be an interim safeguard, but does not by itself bound dense days or fix this iterator behavior.

### 2. [P2] Clearing either date leaves a date-only browse active under invalid filters

Location: `src/inkvault/atlas/search.js:378-380`; early return at `161-163`.

Scenario: Explore this day, then clear From or To. The change handler updates state, but `searching()` becomes false and does nothing. Existing results, the reader and More remain visible; pending search/reader requests are not invalidated and may still render. The displayed controls no longer describe the active result set, and subsequent More silently returns without doing anything because the date-only request no longer has both bounds.

Reproduced using the existing Node harness: Explore 2025-10-15 returned one row with more=true; clearing From left From empty, To=2025-10-15, one row and More visible, status '1+ result.', and exactly one search request. No new request or clearing occurred.

Suggested fix: explicitly handle an incomplete date-only range. Advance the search/reader turn counters, clear results/reader/More and saved selection, and show an instruction to supply both dates. Do not send an unbounded request. Test both date fields and clearing a bound while an earlier browse response is pending.

### 3. [P2] More offers a page that the API always rejects at the offset ceiling

Location: `src/inkvault/atlasserve.py:247-248` (`more` response); limit enforcement at `224-226` and client pagination at `src/inkvault/atlas/search.js:376`.

Scenario: a date range contains more than 10,020 unique hits. Offset 10,000 is allowed and returns 20 hits plus more=true. The client now has 10,020 hits and sends offset=10,020 when More is clicked; the API rejects it as 'Bad page.' The button remains available and remaining records cannot be reached in that range. Broad date browsing makes this readily reachable without a matching keyword.

Synthetic reproduction: 10,050 indexed messages, limit 20. retrieval.browse at offset 10,000 returns 20 hits and more=true. MAX_OFFSET is 10,000; the UI's next offset is 10,020. The API's validation rejects it before retrieval. This is distinct from query cost.

Suggested fix: enforce an explicit result-window contract: suppress More at the last permitted page and tell the user to narrow dates/source, or introduce generation-bound keyset pagination. Add an API-to-client boundary regression with more than MAX_OFFSET + PAGE_SIZE results. Do not advertise a next page that cannot be requested.

### 4. [P3] Correct the verified quote metadata before shipping

Locations: `src/inkvault/atlas/quotes.json:128`, `136`, `144`, and `162`.

The three Dickinson entries display 'Poems, Series One (1890)', but the linked Gutenberg #12242 places all three in Third Series: 'A Word' (Life VI), 'A Book' (Life XVI), and 'The Brain' (Life XLIII). The Third Series preface is dated October 1896. This is visible attribution metadata, not just an internal locator.

The Whitman entry `whitman-song-of-myself-2` says its passage is in Song of Myself section 4. The linked edition places it in the separate poem 'Miracles'. The wording is present, but the verification note directs a reviewer to the wrong poem.

Suggested fix: correct the Dickinson work/edition labels and the Whitman locator (and misleading ID if appropriate before release), then recheck the remaining memory-written locators against their linked editions. Prefer section anchors where practical. The current structural quote tests cannot verify bibliographic accuracy.

Primary sources checked:
- https://www.gutenberg.org/cache/epub/12242/pg12242-images.html — Third Series heading/preface and the three poems.
- https://www.gutenberg.org/cache/epub/1322/pg1322-images.html — 'Miracles'; also spot-checked the other two Whitman passages.
- https://www.gutenberg.org/cache/epub/1051/pg1051-images.html — both Carlyle passages occur in Book III, chapter III, 'Symbols', as stated.

## Validation and other reviewed areas

Focused run: `.venv/Scripts/python.exe -m pytest tests/test_retrieval.py tests/test_atlasserve.py tests/test_quotes.py tests/test_atlas_page_js.py -q -p no:cacheprovider --basetemp C:/Users/Tmatt/inkvault-daily-review-temp` — **76 passed in 46.46 s**. Includes the date-browse merge/batch/DST tests, API routing tests and Node scenarios. Initial sandboxed execution encountered filesystem/temp-database restrictions and was stopped; the successful run used approved unsandboxed execution and disposable synthetic fixtures. No full-suite/lint/privacy rerun or fresh visual browser inspection was performed for this review. Prior builder checks are not presented as my own.

- API routing requires both nonempty date fields for an empty/whitespace query; normal date parsing validates them. No route accepting an unbounded empty query was identified. Broad but valid ranges remain permitted and expose findings 1 and 3.
- Screen dedup checks for matching OCR throughout the selected range, not merely the current OCR batch, so a later-batch OCR event does suppress its stretch. The correctness approach avoids that requested duplicate case, but causes finding 1's cost.
- Web filtering reuses visit EXISTS bounds for UTC instants rather than relying solely on the page's first visit; reader metadata can still show the page's preserved day/visits.
- Generation guards, restore-generation matching, clearEverything -> Atlas.clear -> DailyLight.clear, and atlas-revision refresh are wired through the existing paths. No separate deletion/rebuild bypass was identified in the reviewed changes. Finding 2 concerns filter edits, not a proven forgetting leak.
- Quote selection uses local calendar parts with a UTC day ordinal; midnight scheduling uses the next local midnight and rearms on focus/visibility. Feb 29 selects only an earlier Feb 29. Focused tests passed, including the 23-hour DST browse test. This is not a claim of exhaustive native timezone testing.
- New asset routes are fixed allowlist entries. Quote DOM uses textContent; Source accepts only parsed https URLs and sets noopener/noreferrer. No new arbitrary-path serving, executable quote markup or automatic external quote fetch was identified. CSP is unchanged. No security finding from this diff.
- Accepted limitation retained: Hide daily quote is a browser-origin preference, not a durable vault setting; random helper ports prevent dependable persistence across launches. I did not request a write API.

## Disposition

Address the three P2 findings and correct quote metadata before calling the update ready. Re-review fixes, run the required final checks, and inspect both themes before release. This review does not authorize push, installation or release.
