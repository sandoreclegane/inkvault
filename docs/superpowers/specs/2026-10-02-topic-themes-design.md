# Topics from Pieces' topic tags (v0.2.0)

**Status:** approved design, 2026-10-02
**Release:** v0.2.0 (with Claude Code/Codex/browser sources and ChatGPT/Claude.ai importers, specced separately)

## Why

Pieces tagged nearly every workstream summary with topic tags, and the vault already stores them (`raw_records` kind `tag`), but nothing uses them. Anthony at Pieces suggested building themes from them, and we said it would be in v0.2. The dashboard's Projects come from `themes.txt` or from capitalized phrases in titles; tags show a different, complementary picture: what the work was *about*.

## What the real data looks like

Measured on a real vault (2,371 summaries over 270 days):

- 2,342 summaries (99%) have tags; 3,052 distinct tag texts; 242 appear on 6 or more summaries.
- The most frequent tags are broad and spread across the year: *philosophy of ai* (183 summaries, 6 months), *ai architecture*, *collaboration*, *gaming*. As projects they would say little.
- Tags concentrated in a few weeks read like projects: *deployment issues*, *vocal practice*, *stripe integration*, *vercel deployment*.
- Near-duplicates differ only in punctuation: *work-life balance* / *work-life-balance*.

Tags are topics, not projects, so they get their own view and do not replace Projects.

## Design

A separate **Topics over time** card on the dashboard with two groups:

- **Ongoing:** topics that recur across months.
- **Bursts:** topics concentrated in a few weeks.

Tags in neither group are not shown. Classification is done in Python when the dashboard is built, over the whole vault; the page only draws.

### 1. Index (`index.py`)

`build()` adds one table to `search.db`:

```sql
CREATE TABLE summary_tags (summary_id TEXT, day TEXT, tag TEXT);
CREATE INDEX summary_tags_tag ON summary_tags(tag);
```

- One row per (summary, normalized tag). A summary lists each normalized tag at most once.
- Tag text comes from the summary's `tags.indices` keys, looked up in the `tag` raw records by id. Missing ids and blank texts are skipped.
- `normalize(text)`: lowercase, strip, and collapse every run of whitespace, `-` and `_` into one space. So `Work-Life-Balance`, `work_life balance` and `work life  balance` are all `work life balance`.
- `day` is the summary's created time converted with `times.local(...).date().isoformat()`, the same local day the dashboard uses. Summaries without a created time are skipped.
- No migration: `search.db` is rebuilt from scratch on every `index` run.

### 2. Classification (new `topics.py`)

Pure functions, no database or file access:

```python
normalize(text: str) -> str
classify(tag_days: dict[str, list[date]]) -> {"ongoing": [str], "bursts": [str]}
```

Constants (named, at the top of the module):

| Name | Value | Meaning |
|---|---|---|
| `MIN_SESSIONS` | 6 | a tag needs at least this many summaries to be considered |
| `WINDOW_DAYS` | 21 | length of the "busiest window" |
| `BURST_MIN_PEAK` | 0.7 | burst: at least this share of its summaries fall in its busiest window |
| `ONGOING_MONTHS` | 4 | ongoing: appears in at least this many distinct calendar months |
| `ONGOING_MAX_PEAK` | 0.5 | ongoing: less than this share falls in its busiest window |
| `MAX_PER_GROUP` | 12 | how many tags each group shows |

- **Peak share** of a tag = the largest number of its summaries whose days fall within any `WINDOW_DAYS`-day span (`last - first < WINDOW_DAYS`), divided by its total summaries. Each summary counts once, so several summaries on one day count separately.
- **Burst:** `count >= MIN_SESSIONS` and `peak >= BURST_MIN_PEAK`.
- **Ongoing:** `count >= MIN_SESSIONS`, distinct months `>= ONGOING_MONTHS` and `peak < ONGOING_MAX_PEAK`.
- The two conditions cannot both hold, because `ONGOING_MAX_PEAK < BURST_MIN_PEAK`.
- Each group is sorted by count, descending, then tag name, ascending, and cut to `MAX_PER_GROUP`.

On the real vault these thresholds give 64 ongoing and 109 burst tags before the cut.

### 3. Dashboard data (`dashboard.py`)

`collect(db)` adds two keys:

```python
"topics": {"ongoing": [names], "bursts": [names]},
"topic_docs": [[day, [topic indices]], ...],
```

- Topic indices refer to the combined list `ongoing + bursts`, in that order.
- One `topic_docs` entry per summary that has at least one shown topic. This is the same shape as `docs`, so week counting on the page works the same.
- If `search.db` has no `summary_tags` table (an index built by an older InkVault), both keys are empty and nothing fails. The next `inkvault index` adds the table.
- The `build()` summary line adds the topic count, e.g. `... 14 projects, 24 topics, 299 digests -> ...`.

### 4. Page (`atlas.html`)

- New card **Topics over time**, directly after **Projects over time**, with `data-table="topics"`.
- Description: *Pieces' own topic tags on your sessions. Ongoing topics recur across months; bursts are concentrated in a few weeks. Each group has its own color scale.* (Separate scales are deliberate: ongoing topics recur at low weekly counts, and a shared scale would wash them out.)
- Long topic names are truncated on the axis (`width: 160, overflow: "truncate"`); the tooltip shows the full name.
- The weekly heatmap drawing in `renderThemes` moves into a shared helper used by both cards (rows, weeks, colors, tooltip, table). The Projects card must look and behave exactly as before.
- The card holds two heatmaps drawn by the shared helper, each under a small heading: **Ongoing** first, then **Bursts**. Both use the same weeks on the x-axis so they line up.
- Date range, light/dark theme and the "show as table" toggle work as for Projects. The table has a Group column.
- If `topics` is empty in both groups, the card is hidden (not shown empty). If one group is empty, only the other is drawn. If the selected range has no tagged sessions, the card says *No tagged sessions in this range*.
- Works at 320 and 375 px width without horizontal page scroll, the same bar the date-range fix (e1fd36d) met.

## Unchanged

Export, `themes.txt`, Projects detection, search, the MCP server, digests and the nightly run. No new dependencies.

## Edge cases

| Case | Behavior |
|---|---|
| Vault with no tag records, or no summaries | Topics card hidden |
| Tag id on a summary with no matching tag record, or blank text | Skipped |
| Summary without a created time | Skipped |
| Same tag twice on one summary (after normalizing) | Counted once |
| One group empty | Only the other group is drawn |
| Selected date range has no tagged sessions | Message in the card instead of a chart |
| `search.db` from an older version (no `summary_tags`) | Empty topics; no error |

## Testing

pytest, extending `tests/test_pipeline.py`'s synthetic vault and adding `tests/test_topics.py`:

1. `classify` with hand-made date lists: a clear burst; a clear ongoing tag; a tag in between (in neither group); a tag below `MIN_SESSIONS`; boundaries at exactly `BURST_MIN_PEAK` (included) and `ONGOING_MONTHS` (included); the `MAX_PER_GROUP` cap; deterministic tie order.
2. `normalize`: punctuation, case and whitespace variants collapse to one key.
3. Index: the synthetic vault gains `tag` records and summaries that reference them (including a missing id, a blank tag and a duplicate after normalizing); `summary_tags` holds exactly the expected rows.
4. Dashboard: `collect()` returns `topics` and `topic_docs` with the right shape and indices; empty for a vault without tags; empty, without error, for a `search.db` lacking `summary_tags`; the built page contains no template marker.
5. By hand: build the dashboard from a real vault; check the card at 320/375 px and in both themes; no console errors; Projects card unchanged.

## Failure modes the plan must address

The v0.1.1 plan missed most of its hard parts, so each plan task lists how it could fail. Known ones:

- Local-day conversion: a summary just after midnight UTC must land on the user's local day, consistent with the rest of the dashboard.
- The peak-window calculation off by one at the window boundary (21 days means `last - first <= 20`).
- Refactoring `renderThemes` subtly changing the Projects card (row order, height, tooltip).
- Topic names containing HTML special characters: everything shown goes through the page's existing `esc()`.
- Very large vaults: the tag lookup is one dict built once; no per-summary queries.
