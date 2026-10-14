# Atlas search verification

Fictional data only. Browser check and speed results for the Atlas search work (Task 12).

## Speed (synthetic)

Run with `uv run python tests/manual/atlas_perf.py`. These are synthetic timings: synthetic vectors and a fake model, so they do not measure the real model's cold start. Memory is traced Python allocations, not whole-process peak.

Machine: `Windows-10-10.0.26300-SP0 AMD64 Family 25 Model 117 Stepping 2, AuthenticAMD`, Python 3.11.15.

```json
{
 "records": 120000,
 "hardware": "AMD64 Family 25 Model 117 Stepping 2, AuthenticAMD",
 "python": "3.11.15",
 "synthetic_cold_search_s": 0.225,
 "synthetic_warm_search_s": 0.086,
 "reader_s": 0.092,
 "reader_chars": 9657,
 "long_conversation_reader_s": 0.201,
 "long_conversation_reader_chars": 33802,
 "long_conversation_traced_peak_mb": 139.2,
 "traced_python_peak_mb": 139.2,
 "hits": 25
}
```

Note on the memory figures: the script's `long_conversation_traced_peak_mb` is measured as an absolute peak, and the script still holds about 132 MB of baseline data (the in-memory row list) at that point, so 139.2 MB is mostly baseline. A second run of the same script with the baseline subtracted (a scratch copy, not committed) gave a long-conversation read peak of 6.9 MB above baseline, against a 5 MB message.

Targets:

- Synthetic warm search under 2 s: met (0.086 s).
- Reader under 1 s, including the long conversation: met (0.092 s; 0.201 s for the long conversation).
- Long-conversation traced peak well under 5 MB times a small factor: met on the baseline-subtracted figure (6.9 MB, about 1.4 times the 5 MB message). The absolute figure in the JSON above (139.2 MB) is not a valid measure of the reader because of the baseline, so the script's memory metric should be fixed before it is relied on.

## Browser check

Run by the controller after this task with `uv run python tests/manual/atlas_demo.py`.

1. Atlas view renders every chart; tooltips show the hostile summary names as visible text; nothing runs (no alert). — pass — charts render; hostile names show as text; no alert ran.
2. Browser console shows no Content-Security-Policy violations and no errors. — pass — no console messages; no resource loaded from outside the helper.
3. Search `lighthouse`: results show labels, times and highlights; hostile strings are visible text; `fake` shows no highlight. — pass — 25 marks, all on `lighthouse`; the `fake` record shows no highlight; `<img onerror>` never became an element.
4. Open `m60`: reader lands on it, marked "matched"; **Show more of this** loads the rest in order until the button disappears. — pass — opened in 0.2 s, matched message marked; 4 clicks of Show more gave all 350,049 characters in order.
5. **Earlier messages** / **Later messages** extend the conversation without gaps or repeats. — pass — paging both ways gave messages 0-119, contiguous, no repeats.
6. Event with `javascript:alert(1)` shows that address as plain text, not a link. — pass — `javascript:alert(1)` shown as text, no link.
7. Keyboard only: Tab to search, type, Enter, Tab into results, ArrowDown/ArrowUp, Enter opens, focus ring visible throughout. — partly — focus ring and Enter/arrow handlers present; a full keyboard-only pass was not done in the pane.
8. At 375 px wide: reader sits below results; no horizontal scroll. — pass — checked at the pane's ~390 px width: one column, reader below results, no horizontal scroll.
9. Dark and light (system setting): both palettes readable. — pass — dark (system) and light (emulated) both readable.
10. Atlas → Search → Atlas → Search keeps query, chip, selection and scroll; address bar only ever shows `/`. — pass — round trip kept query, selection and results; address bar `/` only; sessionStorage holds query, filters, selection, scroll and generation, no record text. A refresh (F5) restored query, view, results and the open record.
11. `rebuild-needed` marker written while the page is open: within a minute (or on focus) the page clears charts, tooltip, results, reader and query, shows the rebuilding message; `sessionStorage` is empty. Marker removed: Atlas comes back. — pass — on focus: charts, tiles, results, reader and query cleared, rebuilding message shown, sessionStorage empty; marker removed: Atlas back within about 1 s of the next poll.
11b. Charts only: with results showing, delete `atlas.json` alone; on the next ping or focus the charts redraw and search, results and reader stay as they were. — pass — atlas.json deleted alone: on focus the charts redrew (new build time) and the search and its 25 results stayed.
12. Tab left open, helper stopped (Ctrl+C): the open tab shows "The Atlas has closed" on its next request or focus. — pass — after the helper stopped, the next request showed "The Atlas has closed". (A focus ping with only a 2 s wait didn't show it yet: Windows takes about 2 s to refuse a local connection.)
13. `uv run inkvault --home <demo home> dashboard` while the demo runs: a new tab opens on the same helper, no second process. — pass — `inkvault --home <demo> dashboard --no-open` printed "The Memory Atlas is already running." and started nothing.
14. Refresh (F5) with query, chip, dates, selected record and scrolled results: all come back, including a selected record from **More results**. — pass — see 10.

### Found by the browser check (fixed in the final fix wave)

- A search matching one 350,000-character message that repeats the word 70,000 times took about 35 s. SQLite's own snippet builder takes about 22 s on that message (the same in v0.3.0's MCP search); the Atlas builds two snippets. Fix: snippets only for the hits returned, and none for matched text over 100,000 characters in the Atlas.
- Keyword hits whose body is empty (a summary matched on its title) were labelled "related by meaning". Fix: label by how the hit was found.

## Real history (disposable snapshot)

Run by the maintainer on one finished nightly backup plus the model folder, with three queries typed without echo. Counts and timings only; the snapshot was deleted afterwards (`snapshot_deleted: true`).

```json
{
 "index_s": 26.6, "atlas_s": 0.033, "atlas_status": "ok", "atlas_days": 319,
 "chips": {"conversations": true, "summaries": true, "captures": true, "snippets": true, "screen": false, "web": true},
 "query_1": {"search_s": 0.6, "hits": 25, "by_kind": {"chats": 14, "events": 6, "summaries": 4, "web": 1}, "keyword_only": false, "first_open_s": 0.018},
 "query_2": {"search_s": 0.142, "hits": 25, "by_kind": {"events": 24, "chats": 1}, "keyword_only": false, "first_open_s": 0.003},
 "query_3": {"search_s": 0.126, "hits": 25, "by_kind": {"events": 23, "chats": 1, "summaries": 1}, "keyword_only": false, "first_open_s": 0.003},
 "snapshot_deleted": true
}
```

All targets met: search 0.13-0.6 s (first search includes loading the model), records open in under 0.02 s, meaning search used. Screen shows no records because that backup predates screen capture being switched on.

Note: this vault was made by a build that imports visits from an older tool with no navigation type. The public code never writes such visits, so the check ran from a throwaway copy with that build's one-line guard for them; the guard reaches the Atlas when this release is merged into that build.
