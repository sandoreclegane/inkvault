# Topics from Pieces' topic tags: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Topics over time" card to the Memory Atlas, built from Pieces' own topic tags and split into Ongoing and Bursts.

**Architecture:** `index.py` writes a new `summary_tags` table (one normalized tag per summary per row, on the user's local day). A new pure module `topics.py` sorts tags into Ongoing and Bursts. `dashboard.py` passes the groups and per-summary topic indices to the page. `atlas.html` draws them with the Projects heatmap, which this plan moves into a shared helper.

**Tech Stack:** Python 3.10+, SQLite (stdlib), pytest, ECharts (already on the page). No new dependencies.

**Spec:** `docs/superpowers/specs/2026-10-02-topic-themes-design.md`

**Prototype status:** every code block below was run on 2026-10-02 in a throwaway worktree: 182 passed, 1 skipped (171 existing + 11 new). It was also built from a copy of a real vault (2,371 summaries), giving 24 topics, and checked in a browser at desktop width and at 375 px, with the 30-day range and with no tags. There were no console errors.

---

## Files

| File | Change | Responsibility |
|---|---|---|
| `src/inkvault/topics.py` | create | Tag normalization and Ongoing/Bursts sorting. Pure functions; no I/O. |
| `tests/test_topics.py` | create | Unit tests for `topics.py` with hand-made dates. |
| `src/inkvault/index.py` | modify | Write `summary_tags` while indexing summaries. |
| `src/inkvault/dashboard.py` | modify | `topic_data(db)`; add `topics` and `topic_docs` to `collect()`; topic count in `build()`'s summary line. |
| `src/inkvault/atlas.html` | modify | Shared weekly-heatmap helpers; new Topics card. |
| `tests/test_pipeline.py` | modify | Index and dashboard tests on the synthetic vault. |
| `README.md`, `CHANGELOG.md` | modify | Document the card. |

Run tests with `uv run pytest -q` from the repo root. Baseline before Task 1: `171 passed, 1 skipped`.

---

### Task 1: `topics.py`, the sorting rules

**Files:**
- Create: `src/inkvault/topics.py`
- Test: `tests/test_topics.py`

**How this could fail:**
- Off by one at the window edge. 21 days means the first and last date are at most 20 days apart. Covered by `test_peak_window_is_21_days_inclusive`.
- Threshold comparisons using the wrong operator. Bursts use `>= 0.7` and ongoing uses `< 0.5` plus `>= 4` months. Each boundary has its own test.
- Several summaries on one day must count separately, not as one day.
- Unstable ordering, which would make the page change between builds. Ties are broken by name.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_topics.py`:

```python
"""Rules for turning Pieces' topic tags into the dashboard's Ongoing and Bursts groups."""
from datetime import date, timedelta

from inkvault import topics

D0 = date(2026, 1, 1)


def every(n, step, start=D0):
    """n dates, `step` days apart."""
    return [start + timedelta(days=step * i) for i in range(n)]


def test_normalize_merges_case_punctuation_and_spacing():
    assert {topics.normalize(t) for t in ["Work-Life-Balance", "work_life  balance", " work life balance "]} \
        == {"work life balance"}
    assert topics.normalize("") == "" and topics.normalize(None) == "" and topics.normalize(" - ") == ""


def test_peak_window_is_21_days_inclusive():
    assert topics.peak_share([D0, D0 + timedelta(days=20)]) == 1.0
    assert topics.peak_share([D0, D0 + timedelta(days=21)]) == 0.5


def test_several_summaries_on_one_day_count_separately():
    assert topics.peak_share([D0, D0, D0, date(2026, 6, 1)]) == 0.75


def test_clear_burst_and_clear_ongoing():
    got = topics.classify({"launch": every(8, 1), "philosophy": every(10, 19)})
    assert got == {"ongoing": ["philosophy"], "bursts": ["launch"]}


def test_burst_at_exactly_the_threshold_is_included():
    tag = every(7, 3) + every(3, 40, start=date(2026, 4, 1))  # 7 of 10 in one window
    assert topics.peak_share(tag) == 0.7
    assert topics.classify({"x": tag})["bursts"] == ["x"]


def test_ongoing_needs_four_months():
    four = every(7, 19)   # Jan 1 .. Apr 25: Jan, Feb, Mar, Apr
    three = every(6, 15)  # Jan 1 .. Mar 17: Jan, Feb, Mar
    assert len({(d.year, d.month) for d in four}) == 4 and len({(d.year, d.month) for d in three}) == 3
    assert topics.peak_share(four) < 0.5 and topics.peak_share(three) < 0.5
    got = topics.classify({"four": four, "three": three})
    assert got == {"ongoing": ["four"], "bursts": []}


def test_in_between_and_too_rare_tags_are_dropped():
    between = every(4, 2) + every(4, 30, start=date(2026, 3, 1))  # peak 0.5: neither group
    rare = every(5, 1)                                             # below MIN_SESSIONS
    assert topics.peak_share(between) == 0.5
    assert topics.classify({"between": between, "rare": rare}) == {"ongoing": [], "bursts": []}


def test_groups_are_capped_and_ordered_by_count_then_name():
    tags = {f"t{i:02}": every(6 + i % 3, 1) for i in range(20)}
    got = topics.classify(tags)["bursts"]
    assert len(got) == topics.MAX_PER_GROUP
    assert got[:3] == ["t02", "t05", "t08"]  # 8 sessions each, alphabetical
    assert got == sorted(got, key=lambda t: (-len(tags[t]), t))
```

- [ ] **Step 2: Run them to confirm they fail**

Run: `uv run pytest tests/test_topics.py -q`
Expected: collection error, `ImportError: cannot import name 'topics' from 'inkvault'`

- [ ] **Step 3: Implement**

Create `src/inkvault/topics.py`:

```python
"""Topics from Pieces' own topic tags: ongoing interests and short bursts, for the dashboard's Topics card.

Pure functions: no database or file access, so the rules are easy to test with hand-made dates.
"""
import re

MIN_SESSIONS = 6        # a tag needs this many summaries to be considered
WINDOW_DAYS = 21        # length of a tag's "busiest window"
BURST_MIN_PEAK = 0.7    # burst: at least this share of its summaries fall in its busiest window
ONGOING_MONTHS = 4      # ongoing: appears in at least this many calendar months...
ONGOING_MAX_PEAK = 0.5  # ...and less than this share falls in its busiest window
MAX_PER_GROUP = 12


def normalize(text):
    """'Work-Life-Balance', 'work_life  balance' -> 'work life balance'."""
    return re.sub(r"[\s_-]+", " ", (text or "").lower()).strip()


def peak_share(days):
    """Largest share of the dates that fit in one WINDOW_DAYS-day span (first and last at most WINDOW_DAYS-1 apart)."""
    ds = sorted(days)
    best = start = 0
    for end, d in enumerate(ds):
        while (d - ds[start]).days >= WINDOW_DAYS:
            start += 1
        best = max(best, end - start + 1)
    return best / len(ds)


def classify(tag_days):
    """{tag: [date per summary]} -> {"ongoing": [...], "bursts": [...]}, each by count desc, then name."""
    ongoing, bursts = [], []
    for tag, days in tag_days.items():
        if len(days) < MIN_SESSIONS:
            continue
        peak = peak_share(days)
        if peak >= BURST_MIN_PEAK:
            bursts.append(tag)
        elif peak < ONGOING_MAX_PEAK and len({(d.year, d.month) for d in days}) >= ONGOING_MONTHS:
            ongoing.append(tag)
    order = lambda tag: (-len(tag_days[tag]), tag)
    return {"ongoing": sorted(ongoing, key=order)[:MAX_PER_GROUP], "bursts": sorted(bursts, key=order)[:MAX_PER_GROUP]}
```

- [ ] **Step 4: Run them to confirm they pass**

Run: `uv run pytest tests/test_topics.py -q`
Expected: `8 passed`

- [ ] **Step 5: Commit**

```bash
git add src/inkvault/topics.py tests/test_topics.py
git commit -m "Topics: sort Pieces' topic tags into ongoing and bursts

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Index each summary's tags

**Files:**
- Modify: `src/inkvault/index.py` (imports; `CREATE TABLE` script; the summary loop; the `CREATE INDEX` script)
- Test: `tests/test_pipeline.py` (append)

**How this could fail:**
- Using the UTC date (`created[:10]`) instead of the local day, so tags near midnight land on a different day from the rest of the dashboard. The test uses `00:30Z` and expects `times.local(...)`. On a UTC machine (CI) both give the same date, so this is also covered by code review: the code must call `local()`.
- Duplicates after normalizing producing two rows. A set prevents this.
- A missing tag id or blank text raising an error or writing a `""` row. `.get()` plus removing `None` and `""` handles both.
- Loading the 59k tag records once per summary. The `tag_texts` dict is built once, before the loop.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_pipeline.py`:

```python


def add_tagged_summaries(tags, summaries):
    """Extra tag records and summaries for the topic tests. tags: {id: text}; summaries: [(id, created, [tag ids])]."""
    from inkvault import export
    db = export.open_vault()
    db.executemany("INSERT INTO raw_records VALUES (?,?,?)",
                   [rec("tag", i, text=text) for i, text in tags.items()] +
                   [rec("summary", sid, name=f"Session {sid}", created={"value": created},
                        tags={"indices": {t: n for n, t in enumerate(ids)}}) for sid, created, ids in summaries])
    db.commit()
    db.close()


def test_index_stores_each_summarys_normalized_tags_once(vault):
    from inkvault import index, paths
    from inkvault.times import local
    add_tagged_summaries({"t1": "Stripe-Integration", "t2": "stripe integration ", "t3": "   ", "t4": "Billing"},
                         [("s20", "2026-03-02T00:30:00Z", ["t1", "t2", "t3", "t4", "missing"])])
    index.build()
    db = paths.connect_ro(paths.search_db())
    rows = db.execute("SELECT summary_id, day, tag FROM summary_tags ORDER BY tag").fetchall()
    db.close()
    day = local("2026-03-02T00:30:00Z").date().isoformat()  # the user's local day, not the UTC date
    assert rows == [("s20", day, "billing"), ("s20", day, "stripe integration")]
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `uv run pytest tests/test_pipeline.py::test_index_stores_each_summarys_normalized_tags_once -q`
Expected: FAIL with `sqlite3.OperationalError: no such table: summary_tags`

- [ ] **Step 3: Implement**

In `src/inkvault/index.py`, replace the import line:

```python
from . import embed, paths
```

with:

```python
from . import embed, paths, topics
from .times import local
```

In the first `db.executescript(...)`, add the table after the `snippets` table:

```python
        CREATE TABLE snippets (id TEXT PRIMARY KEY, created TEXT, name TEXT, language TEXT, text TEXT);
        CREATE TABLE summary_tags (summary_id TEXT, day TEXT, tag TEXT);
    """)
```

Replace the summary loop:

```python
    for s in raws("summary"):
        text = "\n\n".join(annotations[a] for a in indices(s, "annotations") if annotations.get(a))
        db.execute("INSERT INTO summaries VALUES (?,?,?,?,?)", (
            s["id"], (s.get("created") or {}).get("value"), s.get("name"), text, json.dumps(indices(s, "events"))))
```

with:

```python
    tag_texts = {t["id"]: topics.normalize(t.get("text")) for t in raws("tag")}
    for s in raws("summary"):
        created = (s.get("created") or {}).get("value")
        text = "\n\n".join(annotations[a] for a in indices(s, "annotations") if annotations.get(a))
        db.execute("INSERT INTO summaries VALUES (?,?,?,?,?)", (
            s["id"], created, s.get("name"), text, json.dumps(indices(s, "events"))))
        # Pieces' topic tags: normalized, once per summary, on the user's local day (the day the dashboard counts).
        tags = {tag_texts.get(i) for i in indices(s, "tags")} - {None, ""}
        if created and tags:
            day = local(created).date().isoformat()
            db.executemany("INSERT INTO summary_tags VALUES (?,?,?)", ((s["id"], day, t) for t in sorted(tags)))
```

In the second `db.executescript(...)`, add the index after `snippets_created`:

```python
        CREATE INDEX snippets_created ON snippets(created);
        CREATE INDEX summary_tags_tag ON summary_tags(tag);
    """)
```

- [ ] **Step 4: Run the test, then the whole suite**

Run: `uv run pytest tests/test_pipeline.py::test_index_stores_each_summarys_normalized_tags_once -q`
Expected: `1 passed`

Run: `uv run pytest -q`
Expected: `180 passed, 1 skipped`

- [ ] **Step 5: Commit**

```bash
git add src/inkvault/index.py tests/test_pipeline.py
git commit -m "Index: store each summary's normalized topic tags

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Pass topics to the dashboard

**Files:**
- Modify: `src/inkvault/dashboard.py` (imports; new `topic_data`; `collect()`; `build()` print line)
- Test: `tests/test_pipeline.py` (append)

**How this could fail:**
- An old `search.db` without `summary_tags` crashing `inkvault dashboard` for someone who upgrades and opens the dashboard before re-indexing. Only `no such table` is caught; any other database error still raises.
- Indices into the wrong list. They must index `ongoing + bursts` in that order, because the page relies on it.
- `topic_docs` in a different order from build to build. It's sorted.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pipeline.py`:

```python


def test_dashboard_topics_card_data(vault):
    from inkvault import dashboard, index, paths
    week = [f"2026-03-0{d}T12:00:00Z" for d in range(2, 9)]  # 7 sessions in one week: a burst
    add_tagged_summaries({"t1": "Harbor Launch", "t2": "rare"},
                         [(f"s{30 + n}", ts, ["t1"] + (["t2"] if n == 0 else [])) for n, ts in enumerate(week)])
    index.build()
    db = paths.connect_ro(paths.search_db())
    data = dashboard.collect(db)
    db.close()
    assert data["topics"] == {"ongoing": [], "bursts": ["harbor launch"]}
    assert len(data["topic_docs"]) == 7 and all(found == [0] for _, found in data["topic_docs"])
    assert [day for day, _ in data["topic_docs"]] == sorted(day for day, _ in data["topic_docs"])
    html = dashboard.build().read_text(encoding="utf-8")
    assert '"bursts":["harbor launch"]' in html and "/*DATA*/null" not in html


def test_dashboard_without_tags_or_with_an_old_index_has_no_topics(vault):
    import sqlite3
    from inkvault import dashboard, index, paths
    index.build()
    db = paths.connect_ro(paths.search_db())
    assert dashboard.topic_data(db) == ({"ongoing": [], "bursts": []}, [])  # the vault has no tags
    db.close()
    rw = sqlite3.connect(paths.search_db())
    rw.execute("DROP TABLE summary_tags")  # as built by InkVault 0.1.x
    rw.commit()
    rw.close()
    db = paths.connect_ro(paths.search_db())
    assert dashboard.collect(db)["topics"] == {"ongoing": [], "bursts": []}
    db.close()
    assert dashboard.build() is not None
```

- [ ] **Step 2: Run them to confirm they fail**

Run: `uv run pytest tests/test_pipeline.py -q -k "topics"`
Expected: 2 failed (`KeyError: 'topics'` and `AttributeError: ... no attribute 'topic_data'`)

- [ ] **Step 3: Implement**

In `src/inkvault/dashboard.py`, replace the imports:

```python
import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from . import paths
from .times import local
```

with:

```python
import json
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlsplit

from . import paths, topics
from .times import local
```

Add this function directly above `def collect(db):`:

```python
def topic_data(db):
    """Pieces' topic tags -> the Topics card: the shown topics, and per tagged summary [day, [topic indices]].

    Indices refer to ongoing + bursts, in that order. A search.db built before topics existed has no
    summary_tags table; that reads as "no topics" until the next `inkvault index`.
    """
    try:
        rows = db.execute("SELECT summary_id, day, tag FROM summary_tags").fetchall()
    except sqlite3.OperationalError as e:
        if "no such table" not in str(e):
            raise
        return {"ongoing": [], "bursts": []}, []
    tag_days = defaultdict(list)
    for _, day, tag in rows:
        tag_days[tag].append(date.fromisoformat(day))
    groups = topics.classify(tag_days)
    pos = {name: i for i, name in enumerate(groups["ongoing"] + groups["bursts"])}
    per_summary = {}
    for sid, day, tag in rows:
        if tag in pos:
            per_summary.setdefault(sid, [day, []])[1].append(pos[tag])
    return groups, sorted([day, sorted(found)] for day, found in per_summary.values())
```

In `collect()`, directly above `digests = {}`:

```python
    topic_groups, topic_docs = topic_data(db)

    digests = {}
```

In `collect()`'s returned dict, after `"docs": docs,`:

```python
        "docs": docs,
        "topics": topic_groups,
        "topic_docs": topic_docs,
```

In `build()`, replace the summary print:

```python
    print(f"dashboard: {len(data['days'])} days, {len(data['themes'])} projects, {len(data['digests'])} digests -> {out}")
```

with:

```python
    n_topics = len(data["topics"]["ongoing"]) + len(data["topics"]["bursts"])
    print(f"dashboard: {len(data['days'])} days, {len(data['themes'])} projects, {n_topics} topics, "
          f"{len(data['digests'])} digests -> {out}")
```

- [ ] **Step 4: Run the tests, then the whole suite**

Run: `uv run pytest tests/test_pipeline.py -q -k "topics"`
Expected: `2 passed`

Run: `uv run pytest -q`
Expected: `182 passed, 1 skipped`

- [ ] **Step 5: Check nothing else reads the summary line**

Run: `git grep -n "digests ->"`
Expected: only `src/inkvault/dashboard.py`. The nightly log copies the dashboard step's output as-is, so the new wording just appears there.

- [ ] **Step 6: Commit**

```bash
git add src/inkvault/dashboard.py tests/test_pipeline.py
git commit -m "Dashboard: pass ongoing and burst topics to the page

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Move the Projects heatmap into shared helpers (no visible change)

**Files:**
- Modify: `src/inkvault/atlas.html` (`renderThemes`)

**How this could fail:**
- Changing the Projects card: row order, row height, tooltip text or the date-range filter. The helpers keep the old code's lines and only replace `DATA.themes` with `names` and `themedDocs()` with an `inRange` check inside the loop. `themedDocs()` stays, because `renderGraph` uses it.
- The page has no JavaScript tests, so Step 3 compares the Projects table before and after the change.

- [ ] **Step 1: Save the Projects table before the change**

Run: `uv run python -m inkvault dashboard` with `INKVAULT_HOME` set to a vault that has data. A copy of a real vault is best; see Task 6 Step 1. Open the page, click **Show table** on *Projects over time*, and save the table text with the browser's devtools:

```js
copy(JSON.stringify(tables.themes))
```

Paste it into a scratch file named `projects-before.json`, outside the repo.

- [ ] **Step 2: Replace `renderThemes`**

In `src/inkvault/atlas.html`, replace the whole `function renderThemes(t) { ... }` block (from `function renderThemes(t) {` to its closing `}` after `}, true);`) with:

```js
// Sessions per week for each name. docs: [[day, [name indices]]]; only: the indices that may become rows.
// Rows with no sessions in range are left out; the rest are ordered by total, most first.
function weeklyGrid(names, docs, only) {
  const weeks = weeksBetween(range.from, range.to);
  const idx = Object.fromEntries(weeks.map((w, i) => [w, i]));
  const grid = names.map(() => new Array(weeks.length).fill(0));
  for (const [day, found] of docs) {
    if (!inRange(day)) continue;
    const w = idx[weekOf(day)];
    if (w !== undefined) for (const i of found) grid[i][w]++;
  }
  const order = (only || names.map((_, i) => i)).filter(i => grid[i].some(v => v))
    .sort((a, b) => grid[b].reduce((x, y) => x + y) - grid[a].reduce((x, y) => x + y));
  return {weeks, grid, order};
}

function drawWeekly(id, names, {weeks, grid, order}, t) {
  const pts = [];
  let max = 1;
  order.forEach((ti, row) => grid[ti].forEach((v, w) => { pts.push([w, row, v || null]); max = Math.max(max, v); }));
  document.getElementById(id).style.height = (order.length * 26 + 80) + "px";
  chart(id).resize();
  chart(id).setOption({
    tooltip: {...tooltipBase(t), formatter: p => `<b>${fmt(p.value[2] || 0)} sessions</b><br>` +
              `<span style="color:${t.ink2}">${esc(names[order[p.value[1]]])} · week of ${esc(weeks[p.value[0]])}</span>`},
    grid: {top: 8, left: 170, right: 12, bottom: 56},
    xAxis: {type: "category", data: weeks.map(w => new Date(w + "T12:00:00").toLocaleDateString(undefined, {month: "short", day: "numeric"})),
            axisTick: {show: false}, axisLine: {lineStyle: {color: t.axis}}, axisLabel: {color: t.muted, fontSize: 10}},
    yAxis: {type: "category", data: order.map(i => names[i]), inverse: true, axisTick: {show: false},
            axisLine: {show: false}, axisLabel: {color: t.ink2, fontSize: 12}},
    visualMap: {min: 1, max, orient: "horizontal", left: "center", bottom: 0, itemWidth: 10, itemHeight: 120,
                inRange: {color: t.seq}, textStyle: {color: t.muted, fontSize: 11}, text: [String(max), "1"]},
    series: [{type: "heatmap", data: pts, itemStyle: {borderColor: t.surface, borderWidth: 2, borderRadius: 2},
              emphasis: {itemStyle: {borderColor: t.ink, borderWidth: 1}}}],
  }, true);
}

function renderThemes(t) {
  const g = weeklyGrid(DATA.themes, DATA.docs);
  tables.themes = {head: ["Project", ...g.weeks.map(w => "wk " + w)], rows: g.order.map(i => [DATA.themes[i], ...g.grid[i]])};
  drawWeekly("themes", DATA.themes, g, t);
}
```

- [ ] **Step 3: Check the Projects card is unchanged**

Rebuild with `uv run python -m inkvault dashboard` and reload the page. Then, in devtools:

```js
JSON.stringify(tables.themes) === JSON.stringify(<paste projects-before.json here>)
```

Expected: `true`. Also check by eye: same rows, same height, the tooltip on a cell reads "N sessions / Project · week of …", and switching to *Last 30 days* still filters the card. Console: no errors.

Run: `uv run pytest -q`
Expected: `182 passed, 1 skipped`

- [ ] **Step 4: Commit**

```bash
git add src/inkvault/atlas.html
git commit -m "Atlas: move the weekly heatmap into shared helpers

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: The Topics card

**Files:**
- Modify: `src/inkvault/atlas.html` (CSS; new `<section>` after Projects; `renderTopics`; `render()`)

**How this could fail:**
- ECharts drawing into a hidden or zero-size box. A group's chart is drawn only when it has rows, and it's hidden otherwise.
- A vault with no tags showing an empty card. The whole card is hidden when there are no topic names.
- "No tagged sessions in this range" showing when the card is already hidden. That message only shows when there are topic names but no rows.
- The table builder (`renderTableFor`) running for a hidden card. It still sets `tables.topics`, so the table toggle never reads `undefined`.
- Topic names with `<` or `&`. Tooltips go through `esc()`, and ECharts axis labels are plain text.

- [ ] **Step 1: Add the CSS**

In the `<style>` block, directly after `.chart { width: 100%; }`, add:

```css
.card h3.group { font-size: 13px; font-weight: 600; color: var(--ink-2); margin: 12px 0 0; }
```

- [ ] **Step 2: Add the card**

Directly after the closing `</section>` of the `data-table="themes"` card (the one with `<div class="chart" id="themes" ...>`), add:

```html

  <section class="card" data-table="topics" id="topics-card">
    <div class="head"><div><h2>Topics over time</h2>
      <p class="desc">Pieces' own topic tags on your sessions. Ongoing topics recur across months; bursts are concentrated in a few weeks.</p></div>
      <button class="tbl">Show table</button></div>
    <div id="topics-ongoing-box"><h3 class="group">Ongoing</h3><div class="chart" id="topics-ongoing"></div></div>
    <div id="topics-bursts-box"><h3 class="group">Bursts</h3><div class="chart" id="topics-bursts"></div></div>
    <p class="empty" id="topics-none" hidden>No tagged sessions in this range</p>
  </section>
```

- [ ] **Step 3: Add `renderTopics`**

Directly after `function renderThemes(t) { ... }` (from Task 4), add:

```js

// Pieces' topic tags in two groups. The card is hidden when the vault has none; an empty group is left out.
function renderTopics(t) {
  const {ongoing, bursts} = DATA.topics;
  const names = [...ongoing, ...bursts];
  document.getElementById("topics-card").hidden = !names.length;
  let weeks = weeksBetween(range.from, range.to);
  const rows = [];
  for (const [key, label, only] of [["ongoing", "Ongoing", ongoing.map((_, i) => i)],
                                    ["bursts", "Bursts", bursts.map((_, i) => ongoing.length + i)]]) {
    const g = weeklyGrid(names, DATA.topic_docs, only);
    weeks = g.weeks;
    document.getElementById(`topics-${key}-box`).hidden = !g.order.length;
    if (g.order.length) drawWeekly(`topics-${key}`, names, g, t);
    rows.push(...g.order.map(i => [label, names[i], ...g.grid[i]]));
  }
  document.getElementById("topics-none").hidden = !names.length || rows.length > 0;
  tables.topics = {head: ["Group", "Topic", ...weeks.map(w => "wk " + w)], rows};
}
```

- [ ] **Step 4: Call it from `render()`**

Replace:

```js
  renderCalendar(t); renderRhythm(t); renderGraph(t); renderThemes(t); renderSources(t);
```

with:

```js
  renderCalendar(t); renderRhythm(t); renderGraph(t); renderThemes(t); renderTopics(t); renderSources(t);
```

- [ ] **Step 5: Run the suite**

Run: `uv run pytest -q`
Expected: `182 passed, 1 skipped`

- [ ] **Step 6: Commit**

```bash
git add src/inkvault/atlas.html
git commit -m "Atlas: Topics over time card (ongoing and bursts)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Check it by hand on a real vault

Nothing is committed in this task, and the real vault is never written to: everything runs on a copy.

- [ ] **Step 1: Build from a copy of a real vault**

```bash
mkdir -p "$TEMP/ivhome" && cp <path to a real vault.db> "$TEMP/ivhome/vault.db"
INKVAULT_HOME="$TEMP/ivhome" uv run python -c "from inkvault import embed, index, dashboard; embed.build = lambda: None; index.build(); dashboard.build()"
```

Expected output ends with `dashboard: N days, P projects, T topics, D digests -> …`, with T > 0 for a vault rescued from Pieces. (In the prototype on a 2,371-summary vault, T was 24: 12 ongoing and 12 bursts.)

- [ ] **Step 2: Serve it and look**

```bash
python -m http.server 8765 --bind 127.0.0.1 --directory "$TEMP/ivhome"
```

Open `http://127.0.0.1:8765/dashboard.html` and check:
- *Topics over time* sits right after *Projects over time*, with **Ongoing** above **Bursts** and their weeks lined up.
- Ongoing rows span the year, and burst rows are short clusters.
- *Last 30 days*: only that month's topics. A range with no tagged sessions (pick a custom range before the first tagged day) shows *No tagged sessions in this range*.
- **Show table** gives Group, Topic and weekly columns.
- Light and dark themes are both readable.
- At 375 px and 320 px wide, the page doesn't scroll sideways. In devtools: `document.documentElement.scrollWidth > innerWidth` is `false`.
- No console errors.
- In devtools, simulate a vault with no tags: `DATA.topics = {ongoing: [], bursts: []}; DATA.topic_docs = []; render();`. The card disappears. Reload afterwards.

- [ ] **Step 3: Stop the server and delete `$TEMP/ivhome`**

---

### Task 7: Docs

**Files:**
- Modify: `README.md` (the Memory Atlas bullet near line 28; the themes note near line 132)
- Modify: `CHANGELOG.md` (new section at the top)

- [ ] **Step 1: README**

Replace:

```markdown
- **Memory Atlas**: a dashboard of your year. Active hours per day (hover a day for what it was about), when you work,
  how your projects connect, projects over time, top apps and sites. Every chart has a table view.
```

with:

```markdown
- **Memory Atlas**: a dashboard of your year. Active hours per day (hover a day for what it was about), when you work,
  how your projects connect, projects over time, topics over time (from Pieces' own topic tags: ongoing interests
  and short bursts), top apps and sites. Every chart has a table view.
```

After the `themes.txt` paragraph (the one starting `- **Your projects** on the dashboard`), add:

```markdown
- **Topics** come from the topic tags Pieces wrote on each session summary. *Ongoing* topics recur across months;
  *bursts* are concentrated in a few weeks. They appear after the next `inkvault index` (part of `rescue` and the
  nightly run). Thanks to Anthony at Pieces for the idea.
```

- [ ] **Step 2: CHANGELOG**

Add at the top, under `# Changelog`:

```markdown
## Unreleased (0.2.0)

- **Topics over time** on the Memory Atlas: Pieces' own topic tags on your session summaries, in two groups:
  *ongoing* (recurring across months) and *bursts* (concentrated in a few weeks). Tags that differ only in case,
  hyphens or spacing are merged. Built by the next `inkvault index`; until then the card stays hidden. Idea from
  Anthony at Pieces.
```

- [ ] **Step 3: Commit**

```bash
git add README.md CHANGELOG.md
git commit -m "Docs: topics over time

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Done when

- `uv run pytest -q` → `182 passed, 1 skipped`.
- Task 6's checks all pass on a real vault.
- The branch `topic-themes-v0.2.0` holds the spec, this plan, and Tasks 1–5 and 7 as separate commits. It's pushed and opened as a PR only when the user says so. CI runs the same suite on Windows, macOS and Linux.
