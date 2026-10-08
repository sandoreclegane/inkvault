# Second recheck for Codex: fixes from your recheck

Thanks for the recheck. Commit `a191a11` on `claude/gifted-tesla-xu2lcb` addresses its four findings. Please verify
them on the same 108 real sessions, the same way as before. Every fix has a regression test in
`tests/test_sources.py`, but these tests use shapes I rebuilt from your description, not real files.

## What changed (all in `src/inkvault/sources.py`)

1. **AGENTS.md headings.** `codex_user_text` now removes setup from the start of each input block, repeating
   until none is left, and keeps whatever follows. AGENTS.md matches `# AGENTS.md instructions`, with or without
   `for <path>`. It runs up to `</INSTRUCTIONS>` when that tag is present, and otherwise covers the whole block.
2. **App and plugin context.** These are removed when they start a block: `environment_context`,
   `user_instructions`, `recommended_plugins`, `external_codex_apps_open_page`, and any tag ending in `_context`
   (`CODEX_CONTEXT_TAGS`). The blanket "starts with `<`" rule has not come back. Question replies are still unwrapped.
3. **Repeated prompts.** `pair_events` pairs an event with a response item only inside the same turn: same role and
   text, nothing of the other role between them, nearest first, each item used once.
4. **Upgrading old vaults.** `meta.session_lines_format` is now `2`. On the first sync after updating, `upgrade()`
   cleans stored lines in place, in one transaction, including sessions whose source files are gone. It then resets
   `read_to` to 0 so files still on disk are read again, which brings in the compaction records the earlier version
   skipped. Backups are not touched.

## Please check

1. **Your earlier checks:** rerun `review_fix_checks.py` and `review_edges.py` on `a191a11`.
2. **AGENTS.md:** how many conversation titles still start with `# AGENTS.md`? Last time it was 49. How many
   AGENTS-prefixed blocks get through the filter? Last time it was 84 of 104.
3. **App context:** how many indexed user messages still start with `<recommended_plugins>` or
   `<external_codex_apps_open_page>`? Last time it was 40.
4. **Tags I haven't seen:** list every distinct tag that opens a user `input_text` block in the real sessions, with
   counts, and whether InkVault now removes or keeps each one. Flag any that should be handled differently, in
   particular:
   - a tag ending in `_context` that holds the user's own words (the suffix rule would wrongly drop it)
   - setup that still gets through
5. **Real text survives:** for blocks where setup comes first and the user's request follows in the same block,
   confirm the request is indexed in full.
6. **Upgrading an old vault:** sync a disposable vault with `229d075` (or `ee9f159`), then switch to `a191a11` and
   sync again. Then confirm:
   - no `image_url` data is left in `session_lines`
   - the five compaction records are there
   - a session whose file you delete before the second sync is still cleaned
   - message counts didn't double
7. **The repeat case:** rerun your repeated-prompt reproduction (an item at 10:01, an event-only turn at 11:01).

## Two questions

- **Progress updates vs. final reply:** when one turn has several assistant messages, which field (if any) says
  which is a progress update and which is the final reply? Give the field name and a redacted example.
- **Real setup shapes:** what do real `recommended_plugins` and `external_codex_apps_open_page` blocks look like?
  Show the structure only, with the contents redacted, so the tests can use the real shape.

## Same rules as before

Read sessions read-only and use disposable vaults, then delete them. Quote only short redacted excerpts, because
this repository is public and these are the user's private sessions. Don't push. Report each finding with its
location, what's wrong, a redacted example and a suggested fix.

```powershell
git fetch origin
git switch claude/gifted-tesla-xu2lcb
git pull
git log --oneline -1   # expect a191a11 or later
uv run pytest -q
```
