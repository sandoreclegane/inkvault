# Codex recheck request: first-run sync design, revision 2 (branch `first-run`)

Branch `first-run`, base `63b7139`, head = the commit that adds this file. Not pushed. Design only; no code yet.
Spec: `docs/superpowers/specs/2026-10-16-first-run-design.md` (revision 2). Your findings:
`docs/reviews/2026-10-16-codex-first-run-design-findings.md`. The spec ends with a "Review disposition" section
saying how each finding was handled.

Please write results to `docs/reviews/2026-10-16-codex-first-run-design-recheck-findings.md`: for each of your six
findings and the lock note, **resolved / partly / not resolved** with one line why. List any **new** problems
revision 2 introduced, with severity (P1/P2/P3), spec section, a failure scenario, and a fix. Check against the
current code; synthetic fixtures only, no live vault.

## Please check in particular

1. **The `sources.sync` contract change** (Success): is the list of truthiness callers complete (`cli.sync_sessions`,
   `nightly.step_sync`, anything else)? Does unreadable → partial in `cmd_sync` change nightly or rescue behaviour?
2. **"Sessions updated this run"** (Summary): can it be computed truthfully from what `read_file` already knows,
   including resumed and rewritten files and the Claude desktop audit log?
3. **Visits totals under the lock**: is reading totals before releasing the lock, then building search, consistent
   with how `cmd_sync` holds the lock today (search is built inside it)?
4. **Chunk sizes as multiples of 1024**: is that enough for model2vec parity, or is the opt-in real-model test the
   only real guarantee?
5. **Scope**: is revision 2 still a small 0.3.3, or should anything move to a later release?

Keep it short: a recheck, not a fresh review.
