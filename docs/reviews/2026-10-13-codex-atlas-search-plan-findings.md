# Codex review: searchable Memory Atlas build plan (2026-10-13)

Reviewed the plan at 856a476. Verdict: revise before building. Quote deferral accepted. Codex made no branch changes,
pushes or live-history reads.

| # | Sev | Finding | Resolution |
|---|---|---|---|
| 1 | P1 | A lock error started a helper anyway. | A lock that can't be taken starts nothing and says so; test `test_a_lock_error_starts_nothing` (Task 7; spec §3.4). |
| 2 | P1 | A delayed record answer could appear under a new search, and record answers weren't checked against the page's generation. | A new search invalidates pending reader requests; an initial open sends the page's generation and the helper answers `changed` if it differs; every record answer carries its generation and the page checks it. Tests: `test_a_record_opened_after_a_rebuild_is_refused` and the Node scenarios `late_record`, `rebuild_between` (Tasks 6, 9). |
| 3 | P1 | The real-history check touched live files and could leave a partial private copy. | Rewritten: a snapshot from one finished nightly backup (plus optional model folder) copied as plain files, built and used inside one try/finally, queries typed without echo; CI tests for cleanup on copy and build failures (Task 13; spec §6). |
| 4 | P2 | Startup overwrote saved state before restoring it. | Saved state is read before anything saves; saving is suppressed while restoring; a selection beyond the first page reopens directly. Node scenario `restore` (Task 9). |
| 5 | P2 | Balanced literal markers could fabricate a highlight. | Snippets are made twice with two marker pairs; only positions where they differ are boundaries. Probed on real SQLite FTS5 with unpaired, balanced, crossed, nested, second-pair and emoji cases (Task 2; spec §3.1). |
| 6 | P2 | Negative lengths, drained oversize bodies and no timeouts. | Lengths must be digits; Transfer-Encoding refused; oversize answered 413 at once without reading; 10 s socket timeout; stalled bodies closed without an answer. Raw-socket tests (Task 6; spec §3.4). |
| 7 | P2 | Atlas-only updates didn't change the generation. | Separate `atlas_revision` in `/api/atlas` and ping; the page redraws charts and keeps the search. Tests `test_atlas_data_has_its_own_revision` and Node `atlas_revision` (Tasks 6, 9). |
| — | limit | `chat()` loaded every full message. | Reads lengths, then only the shown window cut at 20,000 characters; speed script adds a 20,000-message conversation with a 5 MB message (Tasks 3, 12). |
| — | limit | Speed numbers described as real. | Labelled as synthetic timings and traced Python allocations (Task 12). |
| — | limit | A reloaded tab can't be told the helper closed. | Manual check corrected: an open tab says so; a reload shows the browser's connection error (Task 12). |

## Recheck (1390055)

Codex confirmed all seven fixed and ran the four Node scenarios independently (all passed). One P2: Task 13 replaced `embed.build` permanently during keyword-only runs, which inside pytest could switch embedding off for later tests. Fixed: `check()` restores it in `finally`, including on failure; test `test_embedding_is_put_back_even_when_building_fails`. Codex approved starting the early build tasks (plan approval, not implementation validation).
