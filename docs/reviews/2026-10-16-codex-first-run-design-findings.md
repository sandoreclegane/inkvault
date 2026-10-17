# Codex findings: first-run sync design

Reviewed 2026-10-16 against `docs/superpowers/specs/2026-10-16-first-run-design.md` and the current source in this folder. Design review only: no implementation changes, live vault reads, model downloads, or runtime validation.

## 1. P2 — stdout TTY is insufficient for auto-open

**Spec:** Behaviour / Auto-open; Anti-goals.

**Evidence:** `browsers.py:211-212` tests stdin, whereas the proposed launch condition tests only stdout. `cli.py:171-208` already separates prompting from sync work.

**Failure scenario:** A script runs `inkvault sync < input.txt` with stdout inherited from a terminal. It skips the interactive profile question but opens a browser after a successful sync, violating the script anti-goal.

**Suggested fix:** Separate rendering from interaction: stdout TTY controls terminal rendering; require both stdin and stdout TTY plus success and no `--no-open` for automatic launch. Add stdin-redirected/stdout-TTY and stdin-TTY/stdout-redirected tests. TTYs alone cannot identify every script; define this boundary explicitly.

## 2. P1 — unreadable session files can become a successful sync and launch

**Spec:** Behaviour / Summary and Auto-open; Tests / partial outcome.

**Evidence:** `sources.py:222-255` records individual OSError failures and prints unreadable counts, but still returns True. `cli.py:140-149` collapses that to `ok`; `cmd_sync` can consequently exit 0. Browser sync already distinguishes partial and failed outcomes.

**Failure scenario:** One session file is unreadable while another imports. Search builds and the proposed success gate opens the Atlas with a ready message, even though the spec explicitly forbids opening on partial sync.

**Suggested fix:** Return structured per-source outcomes and committed counts, including unreadable files, and propagate partial status through the CLI success gate. Preserve existing callers deliberately when changing the return contract. Test a mixed readable/unreadable source and an all-unreadable source; do not infer success from a truthy result object.

## 3. P2 — the summary mixes incompatible count units and lacks returned browser deltas

**Spec:** Mockup; Behaviour / Summary.

**Evidence:** `sources.file_counts` counts discovered filenames, excludes audit logs, and does not count saved conversations. `sources.sync` reports new raw lines. `history.store` returns newly inserted raw visits, but `history.sync` returns only outcome/profile/failure/waiting fields. `history.index_into` filters navigation types and aggregates pages by address and local day. `sources.SOURCES` currently contains Claude Code, Claude desktop and Codex; Hermes and typed prompts are not separate importers here.

**Failure scenario:** An existing session appends 312 raw lines; `1,240 sessions +312 new` reads as 312 new sessions. Several visits to one page similarly produce a new-visit delta beside a page total. Removed records recovered during this run make before/after net totals an incorrect import count.

**Suggested fix:** Name every unit explicitly: e.g. saved sessions and `+N raw lines`, or implement actual newly saved conversation counts. Prefer saved visits with new visits for the small release; if showing pages, obtain page totals from the built index and label the visit delta separately. Return committed per-profile insert counts from history.sync. Capture totals under the sync lock after pending removals and imports; retain successful commits in partial results. Specify stored versus currently discoverable source visibility, and omit unsupported importer promises. Test resumed sessions, audit logs, filtered/repeated visits, vanished source folders, rollback, and removal recovery.

## 4. P2 — a fixed stub cannot prove chunking preserves real search vectors

**Spec:** Behaviour / Progress; Tests / chunked encoding.

**Evidence:** `embed.py:34-65` currently encodes per kind with batch_size=1024, no multiprocessing, truncation at MAX_CHARS, explicit L2 normalization and float16 conversion. The proposed test uses a fixed model stub, which cannot expose model-dependent batching behavior or runtime overhead.

**Failure scenario:** A stub passes while a real dependency behaves differently across call boundaries, or ids/kinds become misaligned across chunks and change retrieval results. No benchmark currently supports the proposed performance claim.

**Suggested fix:** Retain stub tests for row order, chunk boundaries, empty kinds and progress totals. Add an opt-in integration check using an already cached real model and synthetic texts crossing both 1024 and 4096 boundaries, comparing final float16 vectors and ids/kinds with the current path. Compare rankings if vector differences occur. Benchmark the two paths on roughly 120k synthetic records before claiming equivalent cost; keep this validation offline. No real-model parity or timing was verified in this review.

## 5. P2 — ASCII fallback must cover the complete output

**Spec:** Behaviour / Styling switch; Mockup; Tests / non-UTF-8 stdout.

**Evidence:** Proposed prose also uses an ellipsis, middle dot and em dash outside the listed fallback glyphs. The encoding check lists only selected decorative characters. Windows VT support and stdout character encoding are separate capabilities.

**Failure scenario:** A cp1252 stream takes the ASCII glyph fallback but later raises UnicodeEncodeError on an unhandled character such as the em dash in a stage label, stopping sync for a cosmetic reason. TERM=dumb, NO_COLOR and failed VT setup also need an explicit non-redrawing path.

**Suggested fix:** Define complete ASCII messages/frames or safely render every emitted string against the actual stream encoding. Treat missing encoding conservatively. Preserve existing console mode bits, use correctly typed Win32 handles, and restore only changes owned by this reporter. Tests should write full hello/progress/summary/error messages through strict ASCII and cp1252 streams, and verify plain mode emits neither ESC nor carriage returns. Mocked console tests do not establish real PowerShell 5.1/conhost compatibility; retain a small manual Windows smoke check.

## 6. P2 — module-wide progress state risks leaking into later commands and logs

**Spec:** Behaviour / Progress permits a reporter to be set module-wide; Non-goals / shared hook silent elsewhere.

**Evidence:** `nightly.LogStream.isatty` returns False (`nightly.py:97`); it strips carriage-return prefixes but does not strip ANSI. `index.build` calls the same `embed.build` used by sync. A module-wide reporter selected earlier can retain a terminal stream or styling state.

**Failure scenario:** A sync exits through an exception while global reporting remains installed; a subsequent index/nightly invocation in the same process redraws into its log or writes to the earlier terminal. Standalone non-TTY tests would miss this state leak.

**Suggested fix:** Prefer an explicitly passed optional reporter from cmd_sync through index/build/load_model, defaulting to None. If global state is retained, scope and restore it with try/finally and recheck stream capability. Stop/join spinner threads before ordinary output, prompts, errors and return. Add a sequential styled-sync-failure then nightly/index test in one process; assert no ESC or carriage returns and no lingering thread writes.

## Lock/helper assessment and scope

Opening after the sync lock exits is the correct placement. `atlasserve.launch/build_atlas` handles a nightly lock winner as busy, `atlas_payload_unguarded` retries a missing data build, and `serve/reuse` uses a separate helper lock to reuse the existing process. I found no additional design blocker in that race. Capture the summary before releasing the writer lock; launch afterward. Test busy-helper/nightly races without starting a real browser.

The request's “same pattern as rescue” is misleading: rescue currently calls start_detached from inside its locked call chain; its comment means the child must do work outside that lock, not that the parent has released it. Follow the explicit after-release requirement rather than copying that placement. Catch launch OSError separately so saved/search success survives, with a `inkvault dashboard` fallback; Popen only confirms dispatch, not a browser actually opening.

Keep 0.3.3 small with an explicit reporter, truthful labeled counts, and no new importer scope. Download byte progress can remain deferred. Existing stdout-TTY checks are appropriate for choosing decoration and LogStream is already non-TTY, provided reporter state and browser interaction are handled separately as above.
