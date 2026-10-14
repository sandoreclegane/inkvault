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

1. Atlas view renders every chart; tooltips show the hostile summary names as visible text; nothing runs (no alert). — pending
2. Browser console shows no Content-Security-Policy violations and no errors. — pending
3. Search `lighthouse`: results show labels, times and highlights; hostile strings are visible text; `fake` shows no highlight. — pending
4. Open `m60`: reader lands on it, marked "matched"; **Show more of this** loads the rest in order until the button disappears. — pending
5. **Earlier messages** / **Later messages** extend the conversation without gaps or repeats. — pending
6. Event with `javascript:alert(1)` shows that address as plain text, not a link. — pending
7. Keyboard only: Tab to search, type, Enter, Tab into results, ArrowDown/ArrowUp, Enter opens, focus ring visible throughout. — pending
8. At 375 px wide: reader sits below results; no horizontal scroll. — pending
9. Dark and light (system setting): both palettes readable. — pending
10. Atlas → Search → Atlas → Search keeps query, chip, selection and scroll; address bar only ever shows `/`. — pending
11. `rebuild-needed` marker written while the page is open: within a minute (or on focus) the page clears charts, tooltip, results, reader and query, shows the rebuilding message; `sessionStorage` is empty. Marker removed: Atlas comes back. — pending
11b. Charts only: with results showing, delete `atlas.json` alone; on the next ping or focus the charts redraw and search, results and reader stay as they were. — pending
12. Tab left open, helper stopped (Ctrl+C): the open tab shows "The Atlas has closed" on its next request or focus. — pending
13. `uv run inkvault --home <demo home> dashboard` while the demo runs: a new tab opens on the same helper, no second process. — pending
14. Refresh (F5) with query, chip, dates, selected record and scrolled results: all come back, including a selected record from **More results**. — pending
