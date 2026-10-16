# Codex recheck: first-run design, revision 2

2026-10-16. Rechecked revision 2 against current source and the previous findings. Design dispositions only, not implementation validation. No live vault reads or model downloads.

## Previous findings

| Finding | Status | Reason |
| --- | --- | --- |
| 1. Auto-open interactivity | Resolved | stdin + stdout TTY, success and --no-open are now separate from rendering; the script boundary is explicit. |
| 2. Unreadable session files | Partly resolved | Structured results and partial exit/no-open are specified, but partial session imports must explicitly remain eligible for search building (B below). |
| 3. Summary units/accuracy | Partly resolved | Visits and committed inserts now match units, source visibility and lock timing are defined; conversation identity and updated-session accounting still need a precise rule (A below). |
| 4. Chunking proof | Resolved | Real-model float16 parity plus alignment tests and an offline benchmark are specified. Running those remains a build gate. |
| 5. Encoding/Windows | Resolved | Complete message rendering, conservative fallback, typed handles, mode preservation and real-console smoke checks address the design concern. |
| 6. Reporter state | Resolved | Explicit optional reporter and finally-based thread cleanup remove global state; sequential failure tests are included. |
| Lock/helper note | Resolved | Summary capture stays inside the writer scope, launch follows release, rescue placement is explicitly distinguished and launch OSError preserves sync success. |

## Open details introduced by revision 2

### A. P2 — define updated sessions by conversation identity, not files with stored lines

**Spec:** Success / Summary (sessions that gained lines).

**Evidence:** `sources.py:187-219` returns the number of kept lines written, including replacements after a changed head, truncation or format reread. It does not return newly gained logical conversation lines. `desktop_messages` (`sources.py:435-471`) merges session files and audit records, maps audit IDs to a nearest session, and suppresses echoes. One audit file can affect multiple conversations; two changed files can affect one conversation. `file_counts` counts filenames rather than saved conversations.

**Scenario:** A rewritten file rereads the same messages and appears updated despite gaining none; a desktop session and its audit file both grow and are counted twice; an audit-only conversation is omitted from totals if audit files are simply excluded.

**Fix:** Specify one canonical identity for both totals and updates, using the same conversation grouping as indexing. Count distinct identities that actually gain committed logical messages, with new conversations included, and explicitly decide how replacement-only edits are labeled. Capture the pre-import baseline after removal recovery; compare against committed post-import state with existing merge/dedup rules. `read_file`'s integer alone cannot prove this. Add synthetic rewrite, repeated-resume, audit-only, multi-session audit and echo tests. If this is too much for 0.3.3, use explicitly labeled saved raw lines / lines written this run instead; do not call file-write counts updated sessions.

### B. P2 — partial session results must still trigger a search rebuild

**Spec:** Success / Progress / Summary.

**Evidence:** `cli.py:197-200` currently builds only for recovery, sessions == "ok", or web in ("ok", "partial"). It has no session "partial" branch.

**Scenario:** A computer has no chosen browser profiles. One session file imports and another is unreadable. Mapping the new result to "partial" makes the existing build condition false, leaving committed new sessions out of search. The partial summary then cannot accurately explain search freshness.

**Fix:** Explicitly include session partial outcomes in build eligibility (or use a separate found/committed-work signal), while still returning exit 1 and suppressing auto-open. Test session-only mixed readability with web == "nothing", checking that search includes the committed messages. Keep exceptions/found-but-no-usable-data distinct from normal absence.

## Five requested checks

- **Contract/callers:** The production callers of `sources.sync()` are exactly `cli.sync_sessions` and `nightly.step_sync`. Rescue calls the former and ignores its return; sync consumes it. `nightly.run_step` (`nightly.py:487-494`) interprets strings specially and otherwise uses truthiness. Have step_sync explicitly return the legacy found/not-found boolean, including True for found-but-unreadable sources, to preserve current nightly outcomes and output. Do not pass a structured container through implicitly. Existing tests also include `sources.sync() is False` (`tests/test_sources.py:124`); update that assertion to the deliberate new contract. Rescue can continue ignoring partial status as specified, with its existing diagnostics intact.
- **Updated sessions:** Not derivable truthfully from read_file's current integer alone; see A. Totals should count canonical saved conversations, not discovered files or checkpoint rows.
- **Totals under lock:** Reading after imports and before index.build is consistent with today's lock scope: search is already built inside it. Capture all totals and deltas there and retain the captured values for output afterward. Existing `locked` can continue after a locking OSError (`cli.py:57-61`), so actual exclusive ownership is conditional on lock acquisition; do not introduce a new fail-closed policy silently. A coherent SQLite read snapshot is appropriate for multi-query totals in that fallback.
- **Chunk multiples:** Multiples of 1024 preserve intended batch boundaries within each kind, but are not by themselves proof of numerical parity. Preserve truncation, order, encode options, normalization and float16 conversion. Run and record the specified cached real-model check before shipping chunking; a skipped check leaves that gate open. The real-model test demonstrates parity for its tested model/version/fixtures, not every future dependency. Fall back to the unchanged encode path with per-kind progress if validation cannot be completed.
- **Scope:** Still a plausible small release if A is resolved with a clear bounded counting rule or simpler honest units. Keep importer additions, parser redesign and schema changes out. Explicit result adapters, partial rebuild eligibility, terminal rendering and existing-helper reuse fit this release; chunking can be deferred independently if parity/performance validation is unavailable.

No additional P1 regression identified in revision 2. Resolve A and B in the design/build plan before implementation; the remaining previous concerns are addressed at design level.
