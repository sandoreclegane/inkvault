# Codex review: searchable Memory Atlas design (2026-10-13)

Reviewed `docs/superpowers/specs/2026-10-13-atlas-search-design.md` at afff386. Verdict: good foundation (shared
retrieval engine, local helper, bundled assets are the right direction); revise before implementation.

| # | Sev | Finding | Resolution |
|---|---|---|---|
| 1 | P1 | MCP compatibility: the new API omits search `mode`, and paginated chat reading could change `get_memory` output. | `search()` takes `mode` with today's meaning and messages; `memory_text()` keeps `get_memory` byte for byte; reader windows are Atlas-only. Golden tests cover every mode and a truncated chat (§3.1, §6). |
| 2 | P1 | Helper ownership: a two-second timeout doesn't prove the helper is dead. | An OS exclusive lock (same mechanism as `nightly.lock()`) decides ownership; a busy helper is never replaced; simultaneous launches yield one helper (§3.4, §6). |
| 3 | P1 | Forgetting must clear stale charts, digests, tooltips and cached browser data, not just results and reader. | New §4.4: full page clear, `sessionStorage` holds no content and is deleted, late responses discarded, `no-store` (§4.3, §5, §6). |
| 4 | P2 | Define local-to-UTC dates, generation-bound result paging and advancing expansion cursors. | Helper converts local days to UTC with DST-correct bounds; later pages require the first page's generation; expansion advances to the end (§3.1, §4.2, §6). |
| 5 | P2 | The design drops the daily quote. | Maintainer's decision for this step: see the spec's decisions table. |
| 6 | P2 | Validate on coherent disposable copies of real history, not read-only access alone. | Real-history check on a coherent copy taken under the nightly lock, in a temporary home, deleted afterwards; counts and timings only (§6). |
| — | note | "Date unknown, rebuilt tonight" predicts a rebuild. | Now "Search index: build time not recorded" (§3.3). |
