# Searchable Memory Atlas Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the static Memory Atlas into a live page served by a private helper on this computer, with search and a reader over every source, in the Ink & Light look, released as InkVault 0.3.1.

**Architecture:** A new `retrieval.py` holds today's search (moved out of `server.py`, MCP output unchanged) and adds structured hits, a chat-window reader and freshness facts. `dashboard.build()` writes `atlas.json` instead of an HTML page. A new standard-library `atlasserve.py` serves the bundled page (`inkvault/atlas/`), the Atlas data, search and records on `127.0.0.1` behind a one-time code, a cookie and a header, and stops itself when idle. `inkvault dashboard` starts or reuses it; Windows gets an "InkVault Atlas" icon.

**Tech Stack:** Python 3.10+ standard library (`http.server`, `http.client`, `secrets`, `json`), SQLite FTS5, NumPy (existing), ECharts 5.5.0 (vendored), plain JavaScript and CSS (no build step), pytest.

**Spec:** `docs/superpowers/specs/2026-10-13-atlas-search-design.md` (read it before any task; section numbers below refer to it). Review findings answered: `docs/reviews/2026-10-13-codex-atlas-search-design-findings.md`.

## Global Constraints

- Work only in the worktree `C:\Users\Tmatt\inkvault-atlas`, branch `atlas-search` (from tag `v0.3.0`, `b8dece5`). Never touch `C:\Users\Tmatt\inkvault`, a live InkVault home, or any trial folder.
- Run tests with `uv run pytest -q` from the worktree root. Every task ends with the full suite passing.
- Version **0.3.1**. Python `>=3.10` (CI: 3.10 and 3.13 on Windows, macOS, Linux). **No new runtime dependencies.** Pillow is used once, at development time only (`uv run --with pillow`).
- MCP tools (`search_memories`, `get_memory`, `timeline`, `memory_stats`) keep their names, signatures, docstrings and **exact output text**; `tests/test_mcp_golden.py` (Task 1) is the proof and is never regenerated after Task 1.
- The helper binds `127.0.0.1` only, never `0.0.0.0`; sends no CORS headers; logs no requests, queries or record text; makes no network requests; sets `HF_HUB_OFFLINE=1` in its own process.
- The page has no inline `<script>`, no `<style>` block, no `style="…"` attribute and no `on…=` handler; tooltip HTML uses classes only; search and reader text go in through `textContent` (spec §5 rules 8-9).
- Search terms never go into a URL. Page state lives in `sessionStorage` (no record text) and is deleted on a generation change.
- This branch is **public**: fixtures and docs are fictional; no personal names, paths, accounts or handles. Before pushing, the maintainer runs the private privacy check.
- Tests never start a real browser, PowerShell or Task Scheduler (`tests/conftest.py` already fails any real program other than `sys.executable`); fake `atlasserve.open_in_browser`, `atlasserve.start_detached` and `subprocess.run` where needed.
- Files are LF (`.gitattributes`). Match the codebase's style: short functions, docstrings that say *why*, comments that cite spec sections.
- End each commit message with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **An index built by an older InkVault** (no `pages` table, no `events.source`, no `meta.built`): search, chips and freshness must still answer. Test added to Task 2 (`test_search_on_an_old_index`) and Task 4 (`test_freshness_and_chips_on_an_old_index`).
2. **A query that is only punctuation or symbols** (`!!!`, `??`): the Atlas must answer "no matches", not an error. Test added to Task 2 (`test_punctuation_only_query_is_no_matches`) and Task 6 (`test_search_api_punctuation_only`).
3. **An InkVault home whose path has spaces, an apostrophe or non-ASCII letters**: the lock, the info file and the shortcut must still work. Tests added to Task 7 (`test_helper_in_a_home_with_spaces_and_accents`) and Task 10 (`test_shortcut_script_quotes_the_home_path`).
4. **Dates typed backwards or malformed** (To before From, `2026-02-30`): the helper answers a clear "bad" status, never a 500 or a silent empty list. Test added to Task 6 (`test_search_api_rejects_bad_dates`).
5. **A vault with records but nothing to chart** (only snippets, so `dashboard.build()` returns `None`): the Atlas view says there is nothing to chart and search still works. Test added to Task 6 (`test_atlas_api_without_days_still_searches`).

---

## File map

| File | Status | Responsibility |
|---|---|---|
| `src/inkvault/retrieval.py` | create | Guard, search, hits, excerpts, labels, reader records and cursors, `memory_text`, freshness, chips |
| `src/inkvault/server.py` | modify | MCP tools as thin formatters over `retrieval` |
| `src/inkvault/index.py` | modify | Record `built` in `meta` |
| `src/inkvault/paths.py` | modify | `dashboard()` → `atlas.json`; `atlas_lock()`, `atlas_info()` |
| `src/inkvault/dashboard.py` | modify | Write `atlas.json` atomically; remove leftover `dashboard.html` |
| `src/inkvault/atlas.html` | delete | Replaced by `atlas/` |
| `src/inkvault/atlasserve.py` | create | Helper: HTTP handler, auth, API, single instance, idle stop, launch, shortcut |
| `src/inkvault/atlas/index.html`, `atlas.css`, `atlas.js`, `search.js` | create | The page |
| `src/inkvault/atlas/echarts.min.js`, `ECHARTS-LICENSE`, `ECHARTS-NOTICE`, `ECHARTS.sha256` | create | Vendored chart library |
| `src/inkvault/atlas/mark.png`, `inkvault.ico` | create | Mark and shortcut icon |
| `tools/make_mark.py` | create | Reproducible crop of the mark from the public logo |
| `src/inkvault/cli.py` | modify | `dashboard` launches the helper; `--shortcut`; rescue opens the live Atlas; status line |
| `tests/atlasdata.py` | create | Shared fictional vault with every source |
| `tests/test_mcp_golden.py`, `tests/golden/mcp.json` | create | MCP byte-for-byte contract |
| `tests/test_retrieval.py`, `tests/test_reader.py`, `tests/test_atlasserve.py`, `tests/test_atlas_helper.py`, `tests/test_atlas_page.py`, `tests/test_atlas_cli.py` | create | New tests |
| `tests/test_forget.py`, `tests/test_history.py`, `tests/test_server_screen.py`, `tests/test_pipeline.py` | modify | Patch `retrieval` instead of `server`; read `atlas.json` |
| `tests/manual/atlas_demo.py`, `tests/manual/atlas_perf.py`, `tests/manual/atlas_real_copy.py` | create | Browser check, speed, real-history check (never run in CI) |
| `docs/superpowers/plans/2026-10-13-atlas-search-verification.md` | create | Results of the browser, speed and real-history checks |
| `README.md`, `CHANGELOG.md`, `pyproject.toml`, `src/inkvault/__init__.py`, `uv.lock` | modify | 0.3.1 |

---

### Task 1: Shared fictional vault and the MCP golden contract

**Files:**
- Create: `tests/atlasdata.py`
- Create: `tests/test_mcp_golden.py`
- Create: `tests/golden/mcp.json` (generated in Step 3)

**Interfaces:**
- Consumes: v0.3.0 `inkvault.server` tools, `inkvault.export.open_vault`, `inkvault.index.build`.
- Produces: `tests/atlasdata.py` with `make_vault(home, monkeypatch, vectors=True) -> Path`, `FakeModel`, `utc_times(monkeypatch)`, `web_id() -> str`, constant `LONG` (9,000 characters). Later tasks import these.

- [ ] **Step 1: Record the baseline**

Run: `uv run pytest -q`
Expected: all pass (about 606 passed, some skipped). Write the exact counts into the task report.

- [ ] **Step 2: Write the shared fixture module**

Create `tests/atlasdata.py`:

```python
"""A small fictional vault with every kind of record, for the MCP golden test, retrieval, the reader and the
Atlas helper. Nothing here is real data."""
import importlib
import json
from datetime import datetime, timezone

import numpy as np

LONG = "word " * 1800  # 9,000 characters: three of these make a chat longer than get_memory's 20,000


def T(day, hour, minute=0):
    return f"2026-02-{day:02d}T{hour:02d}:{minute:02d}:00Z"


def S(hour, minute=0):
    return f"2026-02-27T{hour:02d}:{minute:02d}:00.000000Z"


def rec(kind, id_, **fields):
    return (kind, id_, json.dumps({"id": id_, **fields}))


class FakeModel:
    """Meaning search without a download: every query points the same way, so the stored vectors decide the order."""
    def encode(self, texts, **_):
        return np.ones((len(texts), 4), dtype=np.float32)


def utc_local(ts):
    """times.local, pinned to UTC so golden text is the same on every machine and in every time zone."""
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    t = datetime.fromisoformat(ts)
    return (t if t.tzinfo else t.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def utc_times(monkeypatch):
    for name in ("inkvault.history", "inkvault.server", "inkvault.retrieval", "inkvault.atlasserve"):
        try:
            mod = importlib.import_module(name)
        except ImportError:
            continue
        if hasattr(mod, "local"):
            monkeypatch.setattr(mod, "local", utc_local)


def reset_caches(monkeypatch):
    """The meaning model and vectors are cached per module; start every test without them."""
    for name in ("inkvault.retrieval", "inkvault.server"):
        try:
            mod = importlib.import_module(name)
        except ImportError:
            continue
        if hasattr(mod, "_model"):
            monkeypatch.setattr(mod, "_model", None)
        if hasattr(mod, "_vec"):
            monkeypatch.setattr(mod, "_vec", {"mtime": None})


def make_vault(home, monkeypatch, vectors=True):
    monkeypatch.setenv("INKVAULT_HOME", str(home))
    from inkvault import embed, export, index, paths
    monkeypatch.setattr(embed, "build", lambda: None)
    monkeypatch.setattr(embed, "load_model", FakeModel)
    reset_caches(monkeypatch)
    utc_times(monkeypatch)
    db = export.open_vault()
    db.executemany("INSERT INTO events VALUES (?,?,?,?,?,?,?)", [
        ("e1", T(26, 18), "chrome.exe", "Pricing dashboard", "https://dashboard.example.com/x",
         "Harbor Project pricing tiers", "{}"),
        ("e2", T(27, 15), "Code.exe", "retry.ts", None, "exponential backoff helper", "{}"),
    ])
    db.executemany("INSERT INTO raw_records VALUES (?,?,?)", [
        rec("annotation", "a1", text="Worked on the Harbor Project continuity audit."),
        *[rec("summary", f"s{i}", name=f"Harbor Project {w}", created={"value": f"2026-02-2{i}T12:00:00Z"},
              annotations={"indices": {"a1": 0}}) for i, w in enumerate(["Audit", "Review", "Launch"], start=5)],
        rec("summary", "s9", name="Garden Club Planning", created={"value": "2026-02-26T09:00:00Z"}),
        rec("conversation", "c1", name="Retry logic"),
        rec("message", "m1", created={"value": "2026-02-27T15:05:00Z"}, role="USER", conversation={"id": "c1"},
            fragment={"string": {"raw": "how do I add jitter to backoff?"}}),
        rec("conversation", "c2", name="Long notes"),
        *[rec("message", f"m2{i}", created={"value": f"2026-02-28T10:0{i}:00Z"},
              role="ASSISTANT" if i % 2 else "USER", conversation={"id": "c2"},
              fragment={"string": {"raw": f"part {i} lighthouse " + LONG}}) for i in range(3)],
        rec("asset", "x1", name="With Exponential Backoff Retry", created={"value": "2025-11-11T07:02:14Z"},
            original={"reference": {"fragment": {"string": {"raw": "async function retry(fn) { /* … */ }"}},
                                    "classification": {"specific": "ts"}}}),
    ])
    db.executemany("INSERT INTO screen_stretches VALUES (?,?,?,?,?)", [
        ("screen:a", S(9), S(9, 30), "Discord.exe", "Retry chat"),
        ("screen:b", S(10), S(10, 5), "chrome.exe", "Docs page"),
    ])
    db.executemany("INSERT INTO screen_text VALUES (?,?,?,?,?)",
                   [(f"screentext:{i}", "screen:a", S(9, i), f"backoff note number {i:02d} in the chat", str(i))
                    for i in range(3)])
    db.executemany(
        "INSERT INTO browser_visits (id, profile, visit_id, redirected, created, url, address, title, "
        "title_observed_at, duration_s, transition, origin, origin_visit_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", [
            ("v1", "chrome/Default", 1, 0, "2026-02-27T16:00:00.000000Z", "https://docs.example.com/backoff",
             "docs.example.com/backoff", "Backoff guide", "2026-02-27T16:00:00.000000Z", 3.0, 0, None, None),
            ("v2", "chrome/Default", 2, 0, "2026-02-27T17:00:00.000000Z", "https://docs.example.com/backoff",
             "docs.example.com/backoff", "Backoff guide", "2026-02-27T17:00:00.000000Z", 1.0, 0, "phone", None),
        ])
    db.commit()
    db.close()
    assert index.build()
    if vectors:
        np.savez(paths.vectors(), kinds=np.array(["summaries", "chats", "snippets", "events"]),
                 ids=np.array(["s5", "m1", "x1", "e1"]),
                 vecs=np.array([[1, 1, 1, 1], [1, 1, 1, 0], [1, 1, 0, 0], [1, 0, 0, 0]], dtype=np.float32))
        paths.model_dir().mkdir(exist_ok=True)
    return home


def bump(path):
    """A newer published file with the same, valid contents: what a search rebuild looks like to the guard."""
    import os
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))


def web_id():
    """The one page's id (a hash of its address and day)."""
    from inkvault import paths
    db = paths.connect_ro(paths.search_db())
    try:
        return db.execute("SELECT id FROM pages ORDER BY id LIMIT 1").fetchone()[0]
    finally:
        db.close()
```

- [ ] **Step 3: Write the golden test and generate the golden file once**

Create `tests/test_mcp_golden.py`:

```python
"""MCP answers, byte for byte (spec §3.1 compatibility contract). Captured from v0.3.0 before retrieval.py existed;
moving search into retrieval.py must not change one character. Never regenerate after the move. Generating (once):
    INKVAULT_REGEN_GOLDEN=1 uv run pytest tests/test_mcp_golden.py
"""
import json
import os
from pathlib import Path

import pytest

import atlasdata

GOLDEN = Path(__file__).parent / "golden" / "mcp.json"

CASES = {
    "search keyword backoff": lambda s: s.search_memories("backoff", mode="keyword"),
    "search hybrid backoff": lambda s: s.search_memories("backoff"),
    "search meaning harbor": lambda s: s.search_memories("harbor", mode="meaning"),
    "search hybrid limit 2": lambda s: s.search_memories("backoff", limit=2),
    "search chats jitter": lambda s: s.search_memories("jitter", source="chats"),
    "search web backoff": lambda s: s.search_memories("backoff", source="web", mode="keyword"),
    "search screen docs": lambda s: s.search_memories("docs", source="screen", mode="keyword"),
    "search events note": lambda s: s.search_memories("note", source="events"),
    "search dates": lambda s: s.search_memories("harbor", since="2026-02-26", until="2026-02-26"),
    "search times": lambda s: s.search_memories("backoff", since="2026-02-27T15:00", until="2026-02-27T16:30"),
    "search bad mode": lambda s: s.search_memories("x", mode="fuzzy"),
    "search bad source": lambda s: s.search_memories("x", source="nope"),
    "search bad date": lambda s: s.search_memories("x", since="2026-13-45T99"),
    "search bad range": lambda s: s.search_memories("x", since="2026-02-28", until="2026-02-25"),
    "search bad keyword query": lambda s: s.search_memories('"unclosed', mode="keyword"),
    "search no matches": lambda s: s.search_memories("zzzzqqq"),
    "search punctuation": lambda s: s.search_memories("!!!"),
    "get snippet": lambda s: s.get_memory("x1"),
    "get summary": lambda s: s.get_memory("s5"),
    "get event": lambda s: s.get_memory("e1"),
    "get chat": lambda s: s.get_memory("m1"),
    "get long chat": lambda s: s.get_memory("m20"),
    "get long chat small": lambda s: s.get_memory("m20", max_chars=500),
    "get web": lambda s: s.get_memory(atlasdata.web_id()),
    "get screen": lambda s: s.get_memory("screen:a"),
    "get screentext": lambda s: s.get_memory("screentext:1"),
    "get missing": lambda s: s.get_memory("nope"),
    "timeline": lambda s: s.timeline("2026-02-25", "2026-02-28"),
    "timeline bad limit": lambda s: s.timeline("2026-02-25", limit=0),
    "stats": lambda s: s.memory_stats(),
}


@pytest.fixture
def answers(tmp_path, monkeypatch):
    atlasdata.make_vault(tmp_path, monkeypatch)
    from inkvault import server
    atlasdata.utc_times(monkeypatch)
    return {name: case(server) for name, case in CASES.items()}


def test_mcp_answers_are_unchanged(answers):
    if os.environ.get("INKVAULT_REGEN_GOLDEN") == "1":
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(json.dumps(answers, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        pytest.skip("golden file written; commit it and run again without INKVAULT_REGEN_GOLDEN")
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert sorted(golden) == sorted(CASES)
    for name in CASES:
        assert answers[name] == golden[name], name


def test_the_golden_cases_exercise_what_they_name():
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert "[snippets] x1" in golden["search keyword backoff"]
    assert golden["search meaning harbor"].startswith("[summaries] s5")
    assert "truncated" in golden["get long chat"] and "truncated" in golden["get long chat small"]
    assert golden["search bad mode"].startswith("Unknown mode")
    assert golden["search bad range"].startswith("Bad range")
    assert golden["get web"].startswith("WEB PAGE") and "synced from another device" in golden["get web"]
    assert "Screen (live, newest first)" in golden["timeline"]
```

Run (PowerShell): `$env:INKVAULT_REGEN_GOLDEN="1"; uv run pytest tests/test_mcp_golden.py -q; Remove-Item Env:INKVAULT_REGEN_GOLDEN`
(Bash: `INKVAULT_REGEN_GOLDEN=1 uv run pytest tests/test_mcp_golden.py -q`)
Expected: `1 skipped` for the first test, the second test passes or fails only because the file didn't exist yet; `tests/golden/mcp.json` now exists.

- [ ] **Step 4: Check the golden file by eye, then run normally**

Open `tests/golden/mcp.json`. Each value must look like a real tool answer (no tracebacks, no `None`). If `"search meaning harbor"` doesn't start with `[summaries] s5`, or `"get web"` isn't a `WEB PAGE`, fix the fixture (not the golden file) and regenerate.

Run: `uv run pytest tests/test_mcp_golden.py -q`
Expected: `2 passed`.

Run: `uv run pytest -q`
Expected: baseline count + 2, all pass.

- [ ] **Step 5: Commit**

```bash
git add tests/atlasdata.py tests/test_mcp_golden.py tests/golden/mcp.json
git commit -m "Atlas search: pin today's MCP answers before moving search (golden contract)"
```

---

### Task 2: `retrieval.py` search engine; MCP search becomes a formatter

**Files:**
- Create: `src/inkvault/retrieval.py`
- Modify: `src/inkvault/server.py` (whole file shown below)
- Modify: `tests/test_forget.py:26,133,142`, `tests/test_history.py:26,513,519`, `tests/test_server_screen.py:18`
- Create: `tests/test_retrieval.py`

**Interfaces:**
- Consumes: `paths`, `screen`, `embed`, `sources`, `times.local`.
- Produces (exact names later tasks use):
  - Constants: `SOURCES` (kind → `(table, fts, title_sql, snippet_column, body_column, display_sql)`), `FACTS`, `MCP_MARKERS = ("[", "]")`, `ATLAS_MARKERS = ("\ue000", "\ue001")`, `CHIPS`, `REBUILDING`, `CHANGED`, `UNAVAILABLE`, `CANDIDATES`, `RRF_K`.
  - `published() -> tuple`, `generation(stamp) -> str` (16 hex chars), `class Refused(Exception)` with `.status` in `{"rebuilding","unavailable","changed"}`, `guarded_call(fn, *a, **k) -> (answer, generation_str)`, `guarded(tool)` (MCP decorator, same behaviour as v0.3.0).
  - `db()` context manager, `has_table(conn, name)`, `has_column(conn, table, column)`, `utc_bound(text)`, `date_filter(col, since, until, params)`, `range_filter(kind, since, until, params)`, `fts_query(q)`, `vectors()`, `meaning_ready() -> bool`, `keyword_ranked(conn, kind, query, since, until, **opts)`, `meaning_ranked(kinds, query)`.
  - `@dataclass Hit(kind, id, created, title, display, snippet, preview, fact1, fact2)`; `@dataclass SearchResult(hits, more, keyword_only, notes, error, empty_index)`.
  - `search(query, source="all", mode="hybrid", since="", until="", limit=10, *, offset=0, kinds=None, event_source=None, markers=MCP_MARKERS, require_model=False) -> SearchResult`.
  - `excerpt(hit, markers=ATLAS_MARKERS) -> (str, list[[int, int]])` (UTF-16 offsets), `when(ts) -> str`, `source_label(hit) -> str`.

- [ ] **Step 1: Write the failing retrieval tests**

Create `tests/test_retrieval.py`:

```python
"""retrieval.search: the structures behind MCP's text and the Atlas's results (spec §3.1)."""
import sqlite3

import pytest

import atlasdata


@pytest.fixture
def vault(tmp_path, monkeypatch):
    return atlasdata.make_vault(tmp_path, monkeypatch)


def test_hits_are_structures_with_mcp_order(vault):
    from inkvault import retrieval
    r = retrieval.search("backoff", mode="keyword")
    assert r.error is None
    assert ("snippets", "x1") in [(h.kind, h.id) for h in r.hits]
    assert any("[" in (h.snippet or "") for h in r.hits)  # MCP markers by default


def test_atlas_markers_give_spans_in_utf16_units(vault, monkeypatch):
    from inkvault import index, paths, retrieval
    v = sqlite3.connect(paths.vault_db())
    v.execute("UPDATE raw_records SET raw=json_set(raw, '$.text', ?) WHERE id='a1'", ("🙂🙂 lighthouse beacon",))
    v.commit(); v.close()
    index.build()
    r = retrieval.search("beacon", kinds=["summaries"], markers=retrieval.ATLAS_MARKERS, mode="keyword")
    text, spans = retrieval.excerpt(r.hits[0])
    assert "\ue000" not in text and "\ue001" not in text
    start = len(text[:text.index("beacon")].encode("utf-16-le")) // 2
    assert [start, start + len("beacon")] in spans


def test_marker_characters_inside_the_text_never_fake_a_highlight(vault):
    from inkvault import index, paths, retrieval
    v = sqlite3.connect(paths.vault_db())
    v.execute("UPDATE raw_records SET raw=json_set(raw, '$.text', ?) WHERE id='a1'", ("a \ue000 trick beacon",))
    v.commit(); v.close()
    index.build()
    r = retrieval.search("beacon", kinds=["summaries"], markers=retrieval.ATLAS_MARKERS, mode="keyword")
    text, spans = retrieval.excerpt(r.hits[0])
    assert spans == [] and "\ue000" not in text and "\ue001" not in text


def test_meaning_only_hits_have_no_spans(vault):
    from inkvault import retrieval
    r = retrieval.search("harbor", mode="meaning", markers=retrieval.ATLAS_MARKERS)
    assert r.hits and all(h.snippet is None for h in r.hits)
    assert retrieval.excerpt(r.hits[0])[1] == []


def test_event_source_separates_captures_from_screen_text(vault):
    from inkvault import retrieval
    captures = retrieval.search("backoff", kinds=["events"], event_source="pieces", mode="keyword")
    screen = retrieval.search("backoff", kinds=["screen", "events"], event_source="screen", mode="keyword")
    assert {h.id for h in captures.hits} == {"e2"}
    assert {h.id for h in screen.hits} and all(h.id.startswith(("screentext:", "screen:")) for h in screen.hits)


def test_paging_with_offset_and_more(vault):
    from inkvault import retrieval
    whole = retrieval.search("backoff", mode="keyword", limit=50)
    first = retrieval.search("backoff", mode="keyword", limit=2)
    second = retrieval.search("backoff", mode="keyword", limit=2, offset=2)
    assert first.more is True
    assert [h.id for h in first.hits + second.hits] == [h.id for h in whole.hits][:4]


def test_atlas_needs_the_model_folder_for_meaning(vault, monkeypatch):
    from inkvault import embed, paths, retrieval
    paths.model_dir().rmdir()
    monkeypatch.setattr(embed, "load_model", lambda: pytest.fail("the Atlas must never load (or download) a model"))
    r = retrieval.search("harbor", require_model=True, markers=retrieval.ATLAS_MARKERS)
    assert r.keyword_only is True and r.error is None


def test_punctuation_only_query_is_no_matches(vault):
    from inkvault import retrieval
    r = retrieval.search("!!!", markers=retrieval.ATLAS_MARKERS, require_model=True)
    assert r.error is None and r.hits == []


def test_search_on_an_old_index(vault):
    from inkvault import paths, retrieval
    db = sqlite3.connect(paths.search_db())
    db.executescript("DROP TABLE pages; DROP TABLE pages_fts; DROP INDEX events_source;"
                     "ALTER TABLE events DROP COLUMN source;")
    db.commit(); db.close()
    r = retrieval.search("backoff", markers=retrieval.ATLAS_MARKERS, require_model=True)
    assert r.error is None and r.hits
    assert all(retrieval.source_label(h) for h in r.hits)


def test_labels_and_times(vault):
    from inkvault import retrieval
    hits = retrieval.search("backoff", mode="keyword", limit=50).hits
    labels = {h.kind: retrieval.source_label(h) for h in hits}
    assert labels["snippets"] == "Snippet · ts"
    assert labels["web"].startswith("Web history · docs.example.com")
    assert retrieval.when("2026-02-27T15:05:00Z") == "2026-02-27 15:05"  # utc_times pins local to UTC
    assert retrieval.when(None) == "" and retrieval.when("garbage") == ""


def test_guarded_call_reports_status_and_generation(vault):
    from inkvault import paths, retrieval
    answer, gen = retrieval.guarded_call(lambda: 42)
    assert answer == 42 and len(gen) == 16
    paths.rebuild_marker().write_text("x")
    with pytest.raises(retrieval.Refused) as e:
        retrieval.guarded_call(lambda: 42)
    assert e.value.status == "rebuilding"
```

Run: `uv run pytest tests/test_retrieval.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'inkvault.retrieval'`.

- [ ] **Step 2: Create `src/inkvault/retrieval.py`**

```python
"""Search over the InkVault index, for MCP (server.py) and the Memory Atlas (atlasserve.py).

Answers are structures, not text (spec §3.1): server.py formats them exactly as it always has, and the Atlas sends
them to the page as JSON. Everything here is read-only. Importing it starts nothing and changes no process setting;
each process that answers questions sets HF_HUB_OFFLINE itself.
"""
import functools
import hashlib
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone

import numpy as np

from . import embed, paths, screen, sources
from .times import local

# kind -> (table, fts table, title expr, snippet column, body column, the Atlas's title expr)
SOURCES = {
    "summaries": ("summaries", "summaries_fts", "t.name", 1, "text", "t.name"),
    "events": ("events", "events_fts", "t.window_title", 3, "readable", "t.window_title"),
    "chats": ("messages", "messages_fts", "t.conversation_name || ' (' || t.role || ')'", 1, "text",
              "t.conversation_name"),
    "snippets": ("snippets", "snippets_fts", "t.name || ' (' || t.language || ')'", 2, "text", "t.name"),
    "screen": ("screen_stretches", "stretches_fts", "t.app || ': ' || coalesce(t.title, '')", 0, "title",
               "coalesce(t.title, t.app)"),
    "web": ("pages", "pages_fts", "coalesce(t.title, t.url) || ' — ' || t.host", -1, "url",
            "coalesce(t.title, t.url)"),
}
# Two facts the Atlas shows in each hit's source label; (table, column) or None.
FACTS = {
    "summaries": (None, None),
    "events": (("events", "source"), ("events", "app")),
    "chats": (("messages", "source"), None),
    "snippets": (("snippets", "language"), None),
    "screen": (("screen_stretches", "app"), None),
    "web": (("pages", "host"), None),
}
MCP_MARKERS = ("[", "]")
ATLAS_MARKERS = ("\ue000", "\ue001")  # private-use characters: brackets in the text itself can't fake a match
# The Atlas's source chips (spec §4.2): the kinds each searches, and which events.source it keeps.
CHIPS = {
    "all": (None, None),
    "conversations": (["chats"], None),
    "summaries": (["summaries"], None),
    "captures": (["events"], "pieces"),
    "snippets": (["snippets"], None),
    "screen": (["screen", "events"], "screen"),
    "web": (["web"], None),
}
CANDIDATES = 60  # per ranked list, before fusion
RRF_K = 60
REBUILDING = "Search is being rebuilt after something was removed: run `inkvault index`."
CHANGED = "Search was updated while answering; ask again."
UNAVAILABLE = "The vault can't be read right now; try again in a moment."
NAMES = {"pieces": "Pieces", **sources.NAMES}


def has_table(conn, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE name=?", (name,)).fetchone() is not None


def has_column(conn, table, column):
    return any(r[1] == column for r in conn.execute(f"PRAGMA table_info({table})"))


def removal_gen():
    """0 only when there's genuinely no generation (no vault, or a vault without a meta table); None when the
    vault can't be read, which the guard turns into an explicit refusal (R8)."""
    if not paths.vault_db().exists():
        return 0
    try:
        v = paths.connect_ro(paths.vault_db())
    except sqlite3.Error:
        return None
    try:
        if not has_table(v, "meta"):
            return 0
        return screen.removal_gen(v)
    except sqlite3.Error:
        return None
    finally:
        v.close()


def published():
    """What a read uses: whether a removal is under way, which search files are published, and the screen removal
    generation (a removal that completes during a live read changes it)."""
    def stamp(p):
        try:
            st = p.stat()
        except FileNotFoundError:
            return None
        return st.st_mtime_ns, st.st_size
    return paths.rebuild_marker().exists(), stamp(paths.search_db()), stamp(paths.vectors()), removal_gen()


def generation(stamp):
    """An opaque name for one published state (spec §3.1): the page and every cursor carry it."""
    return hashlib.sha256(repr(stamp).encode()).hexdigest()[:16]


class Refused(Exception):
    """A read the guard refused. status: "rebuilding", "unavailable" or "changed"."""
    def __init__(self, status):
        super().__init__(status)
        self.status = status


def guarded_call(fn, *args, **kwargs):
    """Never answer from files a removal has started to change: check before starting and again before answering.
    Returns (answer, generation); raises Refused. An answer already shown can't be recalled."""
    before = published()
    if before[0]:
        raise Refused("rebuilding")
    if before[3] is None:
        raise Refused("unavailable")
    answer = fn(*args, **kwargs)
    after = published()
    if after[0]:
        raise Refused("rebuilding")
    if after[3] is None:
        raise Refused("unavailable")
    if after != before:
        raise Refused("changed")
    return answer, generation(before)


SENTENCES = {"rebuilding": REBUILDING, "unavailable": UNAVAILABLE, "changed": CHANGED}


def guarded(tool):
    """The guard for MCP tools: the same checks, answered with the same sentences as always."""
    @functools.wraps(tool)
    def run(*args, **kwargs):
        try:
            return guarded_call(tool, *args, **kwargs)[0]
        except Refused as r:
            return SENTENCES[r.status]
    return run


def utc_bound(text):
    """A time bound as stored: UTC, microseconds, "Z". A bound without a zone is UTC, like every stored time."""
    t = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def web_filter(since, until, params):
    """Pages by when they were opened. A date-only bound compares the page's local day; a bound with a time compares
    its visits (UTC, like every other source's times), so a later visit in the range finds the page."""
    sql, visit = "", []
    if since:
        if len(since) == 10:
            sql += " AND t.day >= ?"
            params.append(since)
        else:
            visit.append(("v.created >= ?", utc_bound(since)))
    if until:
        if len(until) == 10:
            sql += " AND t.day <= ?"
            params.append(until)
        else:
            visit.append(("v.created < ?", utc_bound(until)))
    if visit:
        sql += (" AND EXISTS (SELECT 1 FROM visits v WHERE v.page_id = t.id AND "
                + " AND ".join(c for c, _ in visit) + ")")
        params.extend(v for _, v in visit)
    return sql


def range_filter(kind, since, until, params):
    if kind == "web":
        return web_filter(since, until, params)
    if kind == "screen":
        if not (since or until):
            return ""
        s, u = screen.bounds(since or "1970-01-01", until)
        params.extend([u, s])
        return screen.overlap_sql("t.started", "t.ended")
    return date_filter("t.created", since, until, params)


def date_filter(col, since, until, params):
    sql = ""
    if since:
        sql += f" AND {col} >= ?"
        params.append(since)
    if until:
        sql += f" AND {col} < ?"
        params.append(until + "T99" if len(until) == 10 else until)
    return sql


_vec = {"mtime": None}
_model = None


@contextmanager
def db():
    # Close explicitly (sqlite3's `with` only ends the transaction) so a re-index can replace the file on Windows.
    conn = paths.connect_ro(paths.search_db())
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def fts_query(q: str) -> str:
    """Plain words -> AND of quoted terms. Queries already using FTS syntax pass through."""
    if re.search(r'"|\bOR\b|\bAND\b|\bNOT\b|\bNEAR\b|\*', q):
        return q
    return " ".join(f'"{t}"' for t in re.findall(r"[\w'-]+", q))


def vectors():
    """Meaning-search vectors, reloaded automatically after a re-index."""
    path = paths.vectors()
    if not path.exists():
        return None
    mtime = path.stat().st_mtime
    if _vec["mtime"] != mtime:
        with np.load(path) as z:  # close right away: Windows can't replace an open file
            _vec.update(mtime=mtime, kinds=z["kinds"], ids=z["ids"], vecs=z["vecs"].astype(np.float32))
    return _vec


def meaning_ready():
    """Meaning search can run without a download: the vectors and the model folder are both there (spec §3.1)."""
    return paths.vectors().exists() and paths.model_dir().exists()


def keyword_ranked(conn, kind, query, since, until, **opts):
    """opts (Atlas only; MCP passes none): markers=(open, close), event_source="pieces"|"screen"."""
    table, fts, _, col, _, _ = SOURCES[kind]
    o, c = opts.get("markers", MCP_MARKERS)
    params = [fts_query(query)]
    sql = (f"SELECT t.id, snippet({fts}, {col}, '{o}', '{c}', ' … ', 40) AS snip FROM {fts} JOIN {table} t "
           f"ON t.rowid = {fts}.rowid WHERE {fts} MATCH ?" + range_filter(kind, since, until, params))
    if kind == "events" and opts.get("event_source") and has_column(conn, "events", "source"):
        sql += " AND t.source = ?"
        params.append(opts["event_source"])
    sql += f" ORDER BY bm25({fts}) LIMIT ?"
    params.append(CANDIDATES)
    return conn.execute(sql, params).fetchall()


def meaning_ranked(kinds, query):
    global _model
    v = vectors()
    if v is None:
        return []
    if _model is None:
        _model = embed.load_model()
    q = _model.encode([query])[0]
    q /= np.linalg.norm(q) + 1e-9
    scores = v["vecs"] @ q
    scores[~np.isin(v["kinds"], kinds)] = -1
    top = np.argsort(-scores)[:CANDIDATES * 5]  # over-fetch: date filtering happens afterwards
    return [(str(v["kinds"][i]), str(v["ids"][i])) for i in top if scores[i] > 0]


@dataclass
class Hit:
    kind: str
    id: str
    created: str | None
    title: str | None       # MCP's title
    display: str | None     # the Atlas's title
    snippet: str | None     # the FTS snippet with the caller's markers; None for a hit found by meaning only
    preview: str | None     # the record's first 300 characters
    fact1: str | None = None
    fact2: str | None = None


@dataclass
class SearchResult:
    hits: list = field(default_factory=list)
    more: bool = False
    keyword_only: bool = False
    notes: list = field(default_factory=list)
    error: str | None = None   # today's sentence for a bad source, mode, date, query, or a missing index
    empty_index: bool = False  # the error is "not indexed yet"


def facts_sql(conn, kind):
    out = []
    for fact in FACTS[kind]:
        out.append(f"t.{fact[1]}" if fact and has_column(conn, fact[0], fact[1]) else "NULL")
    return out


def search(query, source="all", mode="hybrid", since="", until="", limit=10, *, offset=0, kinds=None,
           event_source=None, markers=MCP_MARKERS, require_model=False):
    """Ranked hits, best first (spec §3.1). MCP passes the first six arguments only, exactly as before; the Atlas also
    passes kinds and event_source (its chips), its own markers, require_model, and pages with offset."""
    if kinds is None:
        kinds = list(SOURCES) if source == "all" else [source]
    if any(k not in SOURCES for k in kinds):
        return SearchResult(error=f"Unknown source {source!r}. Use one of: all, {', '.join(SOURCES)}")
    if mode not in ("hybrid", "keyword", "meaning"):
        return SearchResult(error=f"Unknown mode {mode!r}. Use hybrid, keyword, or meaning.")
    for bound in (since, until):
        if bound and len(bound) != 10:
            try:
                utc_bound(bound)
            except ValueError:
                return SearchResult(error=f"Bad date {bound!r}: use a date (2026-10-05) or a UTC time (2026-10-05T14:30).")
    if since or until:
        try:
            screen.bounds(since or "1970-01-01", until)
        except ValueError as e:
            return SearchResult(error=f"Bad range: {e}")
    if not paths.search_db().exists():
        return SearchResult(error="The vault hasn't been indexed yet. Run `inkvault rescue` first.", empty_index=True)

    opts = {}
    if markers != MCP_MARKERS:
        opts["markers"] = markers
    if event_source:
        opts["event_source"] = event_source
    meaning = mode != "keyword" and (meaning_ready() or not require_model)
    scores, snippets = {}, {}  # reciprocal rank fusion: each list adds 1/(k + rank) per hit
    with db() as conn:
        if source == "web" and not has_table(conn, "pages"):
            return SearchResult(error="Web pages aren't in the search index yet: run `inkvault index`.")
        kinds = [k for k in kinds if has_table(conn, SOURCES[k][0])]
        if mode != "meaning":
            for kind in kinds:
                try:
                    rows = keyword_ranked(conn, kind, query, since, until, **opts)
                except sqlite3.OperationalError as e:
                    if mode == "keyword":
                        return SearchResult(error=f"Bad query {query!r}: {e}. Try plain keywords.")
                    rows = []
                for rank, r in enumerate(rows):
                    key = (kind, r["id"])
                    scores[key] = scores.get(key, 0) + 1 / (RRF_K + rank)
                    snippets[key] = r["snip"]
        if meaning:
            for rank, key in enumerate(meaning_ranked([k for k in kinds if k != "screen"], query)):
                scores[key] = scores.get(key, 0) + 1 / (RRF_K + rank)

        fetched = []
        has_stretch = has_column(conn, "events", "stretch_id")
        filter_source = event_source and has_column(conn, "events", "source")
        for kind, id_ in sorted(scores, key=scores.get, reverse=True):
            table, _, title, _, body, display = SOURCES[kind]
            f1, f2 = facts_sql(conn, kind)
            created = "t.started" if kind == "screen" else "t.created"
            stretch = ("t.id" if kind == "screen" else
                       "t.stretch_id" if kind == "events" and has_stretch else "NULL")
            params = [id_]
            sql = (f"SELECT {created} AS created, {title} AS title, {display} AS display, "
                   f"substr(t.{body}, 1, 300) AS preview, {stretch} AS stretch, {f1} AS fact1, {f2} AS fact2 "
                   f"FROM {table} t WHERE t.id = ?" + range_filter(kind, since, until, params))
            if kind == "events" and filter_source:
                sql += " AND t.source = ?"
                params.append(event_source)
            r = conn.execute(sql, params).fetchone()
            if r:
                fetched.append((kind, id_, r))
        ocr_stretches = {r["stretch"] for k, _, r in fetched if k == "events" and r["stretch"]}
        out, seen = [], set()
        for kind, id_, r in fetched:
            if r["stretch"]:
                if (kind == "screen" and r["stretch"] in ocr_stretches) or r["stretch"] in seen:
                    continue      # one hit per stretch; the OCR row wins (screen spec §6)
                seen.add(r["stretch"])
            out.append(Hit(kind, id_, r["created"], r["title"], r["display"], snippets.get((kind, id_)), r["preview"],
                           r["fact1"], r["fact2"]))
            if len(out) > offset + limit:
                break
        notes = []
        if mode == "meaning" and "screen" in kinds:
            notes.append("(screen stretches are keyword-only; they weren't searched by meaning)")
        if {"screen", "events"} & set(kinds) and has_table(conn, "meta") and (
                c := conn.execute("SELECT value FROM meta WHERE key='screen_cutoff'").fetchone()):
            notes.append(f"(search covers screen activity up to {local(c[0]):%Y-%m-%d %H:%M}; "
                         "timeline() shows it live)")
    return SearchResult(hits=out[offset:offset + limit], more=len(out) > offset + limit,
                        keyword_only=mode != "keyword" and not meaning_ready(), notes=notes)


def excerpt(hit, markers=ATLAS_MARKERS):
    """(text, spans): the snippet without its markers, and where they were, as [start, end) offsets in UTF-16 code
    units (what the page's JavaScript counts). Meaning-only hits, and snippets whose markers don't pair up
    (the text itself held a marker character), get no spans: a highlight is never invented."""
    o, c = markers
    if not hit.snippet:
        return " ".join((hit.preview or "").split()), []
    text, spans, start, pos = [], [], None, 0
    for ch in hit.snippet:
        if ch == o and start is None:
            start = pos
        elif ch == c and start is not None:
            spans.append([start, pos])
            start = None
        elif ch in (o, c):
            return hit.snippet.replace(o, "").replace(c, ""), []
        else:
            text.append(ch)
            pos += 2 if ord(ch) > 0xFFFF else 1
    if start is not None:
        return "".join(text), []
    return "".join(text), spans


def when(ts):
    """A stored UTC time as local "YYYY-MM-DD HH:MM", or "" when there's none or it can't be read."""
    try:
        return local(ts).strftime("%Y-%m-%d %H:%M") if ts else ""
    except (TypeError, ValueError):
        return ""


def source_label(hit):
    """What the Atlas shows above a hit's title (spec §4.2)."""
    if hit.kind == "chats":
        return f"Conversation · {NAMES.get(hit.fact1 or 'pieces', hit.fact1)}"
    if hit.kind == "events":
        return ("Screen text" if hit.fact1 == "screen" else "Pieces capture") + (f" · {hit.fact2}" if hit.fact2 else "")
    if hit.kind == "summaries":
        return "Pieces summary"
    if hit.kind == "snippets":
        return "Snippet" + (f" · {hit.fact1}" if hit.fact1 else "")
    if hit.kind == "screen":
        return f"Screen · {hit.fact1 or ''}".rstrip(" ·")
    return f"Web history · {hit.fact1 or ''}".rstrip(" ·")
```

- [ ] **Step 3: Make `server.py` a formatter over `retrieval`**

Edit `src/inkvault/server.py` (line numbers are v0.3.0's):

1. Replace lines 6-21 (the imports through `from .times import local`) with:

```python
import os
import sqlite3

# The search model is downloaded during indexing; the server never needs the network.
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")

from mcp.server.mcpserver import MCPServer

from . import browsers, paths, retrieval, screen, sources
from .retrieval import CHANGED, REBUILDING, UNAVAILABLE, date_filter, db, guarded, has_table  # noqa: F401
from .times import local
```

2. Delete lines 34-225: `SOURCES` through the end of `meaning_ranked`, **except** keep these two constants (put them right after the `mcp = MCPServer(…)` block):

```python
TIMELINE_MAX = 500
STRETCH_TEXTS = 20
```

3. Keep `search_memories`'s decorators, signature and docstring (lines 227-247) exactly, and replace its body (lines 248-319) with:

```python
    r = retrieval.search(query, source, mode, since, until, limit)
    if r.error:
        return r.error
    out = [f"[{h.kind}] {h.id}  {(h.created or '')[:16]}  {h.title or ''}\n"
           f"    {h.snippet or ' '.join((h.preview or '').split())}" for h in r.hits]
    return "\n".join([*out, *r.notes] if out else [f"No matches for {query!r} in {source}.", *r.notes])
```

4. Leave `screen_memory`, `screen_lines`, `get_memory`, `timeline`, `indexed_items`, `memory_stats` and `run` as they are: they use only `db`, `has_table`, `date_filter`, `local`, `paths`, `screen`, `browsers`, `sources`, `sqlite3`, `STRETCH_TEXTS` and `TIMELINE_MAX`, all still defined. `functools`, `re`, `contextmanager`, `datetime`, `timezone`, `numpy` and `embed` are no longer imported by `server.py`; run `uv run ruff check src/inkvault/server.py` if available, otherwise `uv run python -c "import inkvault.server"`, to confirm nothing is missing.

- [ ] **Step 4: Point the race tests at `retrieval`**

In `tests/test_forget.py`, `tests/test_history.py` and `tests/test_server_screen.py`: replace every `monkeypatch.setattr(server, "_model", None)` with `monkeypatch.setattr(retrieval, "_model", None)` and add `retrieval` to that function's `from inkvault import …` line. In `tests/test_forget.py` (around lines 133-142) and `tests/test_history.py` (around lines 513-519) replace `real = server.keyword_ranked` with `real = retrieval.keyword_ranked` and `monkeypatch.setattr(server, "keyword_ranked", …)` with `monkeypatch.setattr(retrieval, "keyword_ranked", …)`, adding `retrieval` to the local imports. Leave `server.screen_lines` patches as they are (it stays in `server.py`).

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_mcp_golden.py tests/test_retrieval.py -q`
Expected: all pass. If the golden test fails, diff the two strings and fix `retrieval.py`/`server.py`; never edit `tests/golden/mcp.json`.

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/inkvault/retrieval.py src/inkvault/server.py tests/test_retrieval.py tests/test_forget.py tests/test_history.py tests/test_server_screen.py
git commit -m "Atlas search: move search into retrieval.py with structured hits; MCP answers unchanged"
```

---

### Task 3: Reader records, cursors and `memory_text`

**Files:**
- Modify: `src/inkvault/retrieval.py` (append)
- Modify: `src/inkvault/server.py` (`get_memory`, `screen_memory`, `STRETCH_TEXTS` move to `retrieval`)
- Create: `tests/test_reader.py`

**Interfaces:**
- Consumes: Task 2's `db`, `has_table`, `has_column`, `published`, `generation`, `when`, `NAMES`, `SOURCES`.
- Produces: `STRETCH_TEXTS = 20`, `CHAT_WINDOW = 20`, `TEXT_CAP = 100_000`, `ITEM_SHARE = 20_000`, `class BadCursor(ValueError)`, `encode_cursor(d) -> str`, `decode_cursor(s) -> dict`, `memory_text(id_, max_chars=20000) -> str`, `record(kind, id_=None, cursor=None) -> dict`. Record dict keys: `status` (`"ok"|"missing"|"changed"`), and for `ok`: `kind, id, title, source_label, meta, note, url, code, visits, items, earlier, later`, or for an expand cursor `text, next`. Item keys: `id, time, role, text, truncated, expand, matched`.

- [ ] **Step 1: Write the failing reader tests**

Create `tests/test_reader.py`:

```python
"""The Atlas reader (spec §3.1): chat windows around the match, paging, advancing expansion, and every kind."""
import json
import sqlite3

import pytest

import atlasdata


@pytest.fixture
def vault(tmp_path, monkeypatch):
    return atlasdata.make_vault(tmp_path, monkeypatch)


def add_conversation(n, size=10, same_time=False, big=None):
    """Conversation c9 with n messages m9_000..; message `big` (an index) is 300,000 characters."""
    from inkvault import index, paths
    v = sqlite3.connect(paths.vault_db())
    v.execute("INSERT INTO raw_records VALUES ('conversation','c9',?)", (json.dumps({"id": "c9", "name": "Ninety"}),))
    for i in range(n):
        text = ("x" * 300_000) if i == big else f"msg {i:03d} " + "y" * size
        created = "2026-03-01T10:00:00Z" if same_time else f"2026-03-01T10:{i // 60:02d}:{i % 60:02d}Z"
        v.execute("INSERT INTO raw_records VALUES ('message',?,?)", (f"m9_{i:03d}", json.dumps({
            "id": f"m9_{i:03d}", "created": {"value": created}, "role": "USER" if i % 2 == 0 else "ASSISTANT",
            "conversation": {"id": "c9"}, "fragment": {"string": {"raw": text}}})))
    v.commit(); v.close()
    index.build()


def ids(r):
    return [it["id"] for it in r["items"]]


def test_a_chat_opens_at_the_match_with_twenty_each_side(vault):
    from inkvault import retrieval
    add_conversation(100)
    r = retrieval.record("chats", "m9_050")
    assert r["status"] == "ok" and r["title"] == "Ninety"
    assert ids(r) == [f"m9_{i:03d}" for i in range(30, 71)]
    assert [it["matched"] for it in r["items"]].count(True) == 1
    earlier = retrieval.record(None, cursor=retrieval.decode_cursor(r["earlier"]))
    later = retrieval.record(None, cursor=retrieval.decode_cursor(r["later"]))
    assert ids(earlier) == [f"m9_{i:03d}" for i in range(10, 30)]
    assert ids(later) == [f"m9_{i:03d}" for i in range(71, 91)]


def test_the_window_stays_under_the_text_cap(vault):
    from inkvault import retrieval
    add_conversation(60, size=15_000)
    r = retrieval.record("chats", "m9_030")
    assert sum(len(it["text"]) for it in r["items"]) <= retrieval.TEXT_CAP
    assert "m9_030" in ids(r) and r["earlier"] and r["later"]


def test_a_huge_message_expands_to_the_end_in_order(vault):
    from inkvault import retrieval
    add_conversation(3, big=1)
    r = retrieval.record("chats", "m9_001")
    item = next(it for it in r["items"] if it["id"] == "m9_001")
    assert item["truncated"] and len(item["text"]) == retrieval.ITEM_SHARE
    text, cursor = item["text"], item["expand"]
    while cursor:
        part = retrieval.record(None, cursor=retrieval.decode_cursor(cursor))
        assert part["status"] == "ok"
        text, cursor = text + part["text"], part["next"]
    assert text == "x" * 300_000


def test_same_timestamps_keep_insertion_order(vault):
    from inkvault import retrieval
    add_conversation(5, same_time=True)
    assert ids(retrieval.record("chats", "m9_002")) == [f"m9_{i:03d}" for i in range(5)]


def test_a_cursor_from_before_a_change_is_refused(vault):
    from inkvault import paths, retrieval
    add_conversation(100)
    r = retrieval.record("chats", "m9_050")
    atlasdata.bump(paths.vectors())  # a new published state
    assert retrieval.record(None, cursor=retrieval.decode_cursor(r["earlier"]))["status"] == "changed"


def test_bad_cursors_are_rejected():
    from inkvault import retrieval
    for bad in ("", "not base64!", retrieval.encode_cursor({"op": "fly"}), "A" * 3000, None, 5):
        with pytest.raises(retrieval.BadCursor):
            retrieval.decode_cursor(bad)


def test_each_kind_reads(vault):
    from inkvault import retrieval
    assert retrieval.record("snippets", "x1")["code"] is True
    s = retrieval.record("summaries", "s5")
    assert "continuity audit" in s["items"][0]["text"] and s["source_label"] == "Pieces summary"
    e = retrieval.record("events", "e1")
    assert e["url"] == "https://dashboard.example.com/x" and e["source_label"] == "Pieces capture · chrome.exe"
    w = retrieval.record("web", atlasdata.web_id())
    assert w["note"].startswith("Browser history") and len(w["visits"]) == 2 and w["visits"][1]["synced"]
    sc = retrieval.record("screen", "screen:a")
    assert sc["source_label"] == "Screen · Discord.exe" and len(sc["items"]) == 3
    st = retrieval.record("events", "screentext:1")
    assert st["source_label"].startswith("Screen text")
    assert retrieval.record("chats", "nope")["status"] == "missing"
    assert retrieval.record("web", "web:nope")["status"] == "missing"


def test_memory_text_is_get_memory(vault):
    from inkvault import retrieval, server
    for id_ in ("x1", "s5", "e1", "m1", "m20", "screen:a", "screentext:1", atlasdata.web_id(), "nope"):
        assert retrieval.memory_text(id_) == server.get_memory(id_)
```

Run: `uv run pytest tests/test_reader.py -q`
Expected: FAIL with `AttributeError: module 'inkvault.retrieval' has no attribute 'record'`.

- [ ] **Step 2: Move `get_memory`'s text into `retrieval.memory_text`**

Append to `src/inkvault/retrieval.py` (this is v0.3.0's `screen_memory` and `get_memory` body, moved, with `id` renamed `id_`):

```python
STRETCH_TEXTS = 20


def screen_memory(id_, max_chars):
    if not paths.vault_db().exists():
        return f"No memory with id {id_}"
    v = paths.connect_ro(paths.vault_db())
    try:
        v.execute("BEGIN")
        if not has_table(v, "screen_stretches"):
            return f"No memory with id {id_}"
        if id_.startswith("screentext:"):
            t = v.execute("SELECT stretch_id, created, text FROM screen_text WHERE id=?", (id_,)).fetchone()
            if not t:
                return f"No memory with id {id_}"
            sid, texts, more = t[0], [(t[1], t[2])], 0
        else:
            sid = id_
            texts = v.execute("SELECT created, text FROM screen_text WHERE stretch_id=? ORDER BY created DESC LIMIT ?",
                              (sid, STRETCH_TEXTS)).fetchall()
            more = v.execute("SELECT COUNT(*) FROM screen_text WHERE stretch_id=?", (sid,)).fetchone()[0] - len(texts)
        st = v.execute("SELECT started, ended, app, title FROM screen_stretches WHERE id=?", (sid,)).fetchone()
        if not st:
            return f"No memory with id {id_}"
    finally:
        v.close()
    body = (f"SCREEN  {local(st[0]):%Y-%m-%d %H:%M}–{local(st[1]):%H:%M}\napp: {st[2]}\nwindow: {st[3] or ''}\n\n"
            + ("\n\n".join(f"{local(c):%H:%M}\n{t}" for c, t in texts) if texts else "(timeline only: no text read)"))
    if more > 0:
        body += f"\n\n… {more} more text reads in this window; search finds them by their own ids."
    return body if len(body) <= max_chars else body[:max_chars] + f"\n\n… [truncated, {len(body)} chars total]"


def memory_text(id_, max_chars=20000):
    """Exactly what MCP's get_memory returns (spec §3.1): the whole conversation for a chat, cut at max_chars with
    the usual note. The Atlas reader uses record() instead."""
    if id_.startswith(("screen:", "screentext:")):
        return screen_memory(id_, max_chars)
    with db() as conn:
        if id_.startswith("web:") and has_table(conn, "pages") and (
                r := conn.execute("SELECT day, url, title, profiles FROM pages WHERE id=?", (id_,)).fetchone()):
            seen = conn.execute("SELECT created, profile, synced FROM visits WHERE page_id=? ORDER BY created",
                                (id_,)).fetchall()
            body = (f"WEB PAGE  {r['day']}\n{r['title'] or '(no title)'}  (title as observed at sync)\n{r['url']}\n"
                    f"profiles: {r['profiles']}\n\nvisits:\n" + "\n".join(
                        f"{local(v['created']).strftime('%H:%M')}  {v['profile']}"
                        + ("  (synced from another device)" if v["synced"] else "") for v in seen))
        elif r := conn.execute("SELECT created, name, text FROM summaries WHERE id=?", (id_,)).fetchone():
            body = f"SUMMARY  {r['created']}\n{r['name']}\n\n{r['text']}"
        elif r := conn.execute("SELECT created, window_title, app, url, readable FROM events WHERE id=?", (id_,)).fetchone():
            body = (f"EVENT  {r['created']}\napp: {r['app']}\nwindow: {r['window_title']}\n"
                    f"url: {r['url']}\n\n{r['readable'] or '(no text captured)'}")
        elif r := conn.execute("SELECT conversation_id, conversation_name FROM messages WHERE id=?", (id_,)).fetchone():
            msgs = conn.execute("SELECT created, role, text FROM messages WHERE conversation_id=? ORDER BY created",
                                (r["conversation_id"],)).fetchall()
            body = f"CHAT CONVERSATION\n{r['conversation_name'] or ''}\n\n" + "\n\n".join(f"{(m['created'] or '')[:16]} {m['role']}:\n{m['text']}" for m in msgs)
        elif r := conn.execute("SELECT created, name, language, text FROM snippets WHERE id=?", (id_,)).fetchone():
            body = f"SNIPPET  {r['created']}\n{r['name']} ({r['language']})\n\n```{r['language']}\n{r['text']}\n```"
        else:
            return f"No memory with id {id_}"
    return body if len(body) <= max_chars else body[:max_chars] + f"\n\n… [truncated, {len(body)} chars total]"
```

In `server.py`, delete `screen_memory` and `STRETCH_TEXTS`, and make the tool:

```python
@mcp.tool()
@guarded
def get_memory(id: str, max_chars: int = 20000) -> str:
    """Full text of one memory (summary, event, chat, snippet, web page, or screen window) by id from search_memories or timeline."""
    return retrieval.memory_text(id, max_chars)
```

`server.screen_lines` stays in `server.py`. Run `uv run pytest tests/test_mcp_golden.py -q`: must pass before going on.

- [ ] **Step 3: Append cursors and `record` to `retrieval.py`**

```python
import base64  # add to the imports at the top
import json    # add to the imports at the top

CHAT_WINDOW = 20      # messages each side of the match, and per Earlier/Later page (spec §3.1)
TEXT_CAP = 100_000    # characters of record text in one answer
ITEM_SHARE = 20_000   # one chat message's share of a window before it is cut and offered for expansion
CURSOR_MAX = 2000


class BadCursor(ValueError):
    """A cursor the page couldn't have made."""


def encode_cursor(d):
    return base64.urlsafe_b64encode(json.dumps(d, separators=(",", ":")).encode()).decode().rstrip("=")


def decode_cursor(s):
    """Opaque to the page; checked here because it comes back over HTTP."""
    if not isinstance(s, str) or not s or len(s) > CURSOR_MAX:
        raise BadCursor("bad cursor")
    try:
        d = json.loads(base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)))
    except ValueError:
        raise BadCursor("bad cursor") from None
    ok = (isinstance(d, dict) and d.get("op") in ("earlier", "later", "expand") and d.get("k") in SOURCES
          and isinstance(d.get("i"), str) and isinstance(d.get("g"), str)
          and isinstance(d.get("at" if d.get("op") != "expand" else "off"), int)
          and d.get("at" if d.get("op") != "expand" else "off") >= 0)
    if not ok:
        raise BadCursor("bad cursor")
    return d


def item(kind, id_, created, text, gen, role=None, matched=False, share=ITEM_SHARE):
    text = text or ""
    cut = len(text) > share
    return {"id": id_, "time": when(created), "role": role, "text": text[:share], "truncated": cut,
            "expand": encode_cursor({"op": "expand", "k": kind, "i": id_, "g": gen, "off": share}) if cut else None,
            "matched": matched}


def window(sizes, start, stop, anchor):
    """The contiguous messages [lo, hi) within [start, stop), grown outward from anchor (later side first), until
    adding one more would pass TEXT_CAP. At least one message is always included."""
    start, stop = max(0, start), min(len(sizes), stop)
    lo = hi = anchor
    total = 0
    while True:
        grew = False
        if hi < stop and not (total and total + sizes[hi] > TEXT_CAP):
            total += sizes[hi]; hi += 1; grew = True
        if lo > start and not (total and total + sizes[lo - 1] > TEXT_CAP):
            total += sizes[lo - 1]; lo -= 1; grew = True
        if not grew:
            return lo, hi


def chat(mid, cursor, gen):
    with db() as conn:
        src = "source" if has_column(conn, "messages", "source") else "NULL AS source"
        head = conn.execute(f"SELECT conversation_id, conversation_name, {src} FROM messages WHERE id=?",
                            (mid,)).fetchone()
        if not head:
            return {"status": "missing"}
        msgs = conn.execute("SELECT id, created, role, text FROM messages WHERE conversation_id=? "
                            "ORDER BY created, rowid", (head["conversation_id"],)).fetchall()
    sizes = [min(len(m["text"] or ""), ITEM_SHARE) for m in msgs]
    n = len(msgs)
    if cursor is None:
        center = next(i for i, m in enumerate(msgs) if m["id"] == mid)
        lo, hi = window(sizes, center - CHAT_WINDOW, center + CHAT_WINDOW + 1, center)
    elif cursor["op"] == "earlier":
        at = min(cursor["at"], n)
        lo, hi = window(sizes, at - CHAT_WINDOW, at, at)
    else:
        at = min(cursor["at"], n)
        lo, hi = window(sizes, at, at + CHAT_WINDOW, at)
    items = [item("chats", m["id"], m["created"], m["text"], gen, role=m["role"],
                  matched=cursor is None and m["id"] == mid) for m in msgs[lo:hi]]
    cur = lambda op, at: encode_cursor({"op": op, "k": "chats", "i": mid, "g": gen, "at": at})
    return {"status": "ok", "kind": "chats", "id": mid, "title": head["conversation_name"] or "Conversation",
            "source_label": f"Conversation · {NAMES.get(head['source'] or 'pieces', head['source'])}",
            "meta": f"{n} messages", "note": None, "url": None, "code": False, "visits": None, "items": items,
            "earlier": cur("earlier", lo) if lo > 0 else None, "later": cur("later", hi) if hi < n else None}


def full_text(kind, id_):
    """The whole text that an expand cursor walks through, or None when the record is gone."""
    column = {"chats": ("messages", "text"), "summaries": ("summaries", "text"), "events": ("events", "readable"),
              "snippets": ("snippets", "text")}.get(kind)
    if not column:
        return None
    with db() as conn:
        r = conn.execute(f"SELECT {column[1]} FROM {column[0]} WHERE id=?", (id_,)).fetchone()
    return None if r is None else (r[0] or "")


def expand(kind, id_, off, gen):
    text = full_text(kind, id_)
    if text is None:
        return {"status": "missing"}
    nxt = off + TEXT_CAP
    return {"status": "ok", "text": text[off:nxt],
            "next": encode_cursor({"op": "expand", "k": kind, "i": id_, "g": gen, "off": nxt}) if nxt < len(text) else None}


def base(kind, id_, title, label, meta, items, **extra):
    out = {"status": "ok", "kind": kind, "id": id_, "title": title, "source_label": label, "meta": meta,
           "note": None, "url": None, "code": False, "visits": None, "items": items, "earlier": None, "later": None}
    out.update(extra)
    return out


def single(kind, id_, gen):
    """Every kind but chats and screen stretches."""
    with db() as conn:
        if kind == "summaries":
            r = conn.execute("SELECT created, name, text FROM summaries WHERE id=?", (id_,)).fetchone()
            return r and base(kind, id_, r["name"], "Pieces summary", when(r["created"]),
                              [item(kind, id_, r["created"], r["text"], gen, share=TEXT_CAP)])
        if kind == "snippets":
            r = conn.execute("SELECT created, name, language, text FROM snippets WHERE id=?", (id_,)).fetchone()
            return r and base(kind, id_, r["name"], "Snippet" + (f" · {r['language']}" if r["language"] else ""),
                              when(r["created"]), [item(kind, id_, r["created"], r["text"], gen, share=TEXT_CAP)],
                              code=True)
        if kind == "events":
            src = "source" if has_column(conn, "events", "source") else "'pieces' AS source"
            r = conn.execute(f"SELECT created, app, window_title, url, readable, {src} FROM events WHERE id=?",
                             (id_,)).fetchone()
            if not r:
                return None
            label = ("Screen text" if r["source"] == "screen" else "Pieces capture") + (f" · {r['app']}" if r["app"] else "")
            return base(kind, id_, r["window_title"] or "(no window title)", label, when(r["created"]),
                        [item(kind, id_, r["created"], r["readable"] or "(no text captured)", gen, share=TEXT_CAP)],
                        url=r["url"])
        if kind == "web" and has_table(conn, "pages"):
            r = conn.execute("SELECT day, url, host, title FROM pages WHERE id=?", (id_,)).fetchone()
            if not r:
                return None
            seen = conn.execute("SELECT created, profile, synced FROM visits WHERE page_id=? ORDER BY created",
                                (id_,)).fetchall()
            return base(kind, id_, r["title"] or r["url"], f"Web history · {r['host']}", r["day"], [],
                        url=r["url"], note="Browser history: the page itself isn't saved.",
                        visits=[{"time": when(v["created"]), "profile": v["profile"], "synced": bool(v["synced"])}
                                for v in seen])
    return None


def stretch(id_, gen):
    """A screen stretch, read live from vault.db like MCP's get_memory: app, window, times, newest text reads."""
    if not paths.vault_db().exists():
        return None
    v = paths.connect_ro(paths.vault_db())
    try:
        v.execute("BEGIN")
        if not has_table(v, "screen_stretches"):
            return None
        st = v.execute("SELECT started, ended, app, title FROM screen_stretches WHERE id=?", (id_,)).fetchone()
        if not st:
            return None
        texts = v.execute("SELECT id, created, text FROM screen_text WHERE stretch_id=? ORDER BY created DESC LIMIT ?",
                          (id_, STRETCH_TEXTS)).fetchall()
        more = v.execute("SELECT COUNT(*) FROM screen_text WHERE stretch_id=?", (id_,)).fetchone()[0] - len(texts)
    finally:
        v.close()
    note = ("Timeline only: no text was read in this app." if not texts else
            f"{more} more text reads in this window; search finds them." if more > 0 else None)
    return base("screen", id_, st[3] or st[2], f"Screen · {st[2]}", f"{when(st[0])}–{when(st[1])[-5:]}",
                [item("events", t[0], t[1], t[2], gen, share=TEXT_CAP) for t in texts], note=note)


def record(kind, id_=None, cursor=None):
    """One record for the Atlas reader (spec §3.1). cursor is a decoded cursor; a cursor made under another
    published state answers {"status": "changed"} and nothing else."""
    gen = generation(published())
    if cursor is not None:
        if cursor["g"] != gen:
            return {"status": "changed"}
        kind, id_ = cursor["k"], cursor["i"]
        if cursor["op"] == "expand":
            return expand(kind, id_, cursor["off"], gen)
    if kind == "chats":
        return chat(id_, cursor, gen)
    found = stretch(id_, gen) if kind == "screen" else single(kind, id_, gen)
    return found or {"status": "missing"}
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_reader.py tests/test_mcp_golden.py -q`
Expected: all pass.

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/inkvault/retrieval.py src/inkvault/server.py tests/test_reader.py
git commit -m "Atlas search: reader records with chat windows, cursors and advancing expansion; get_memory unchanged"
```

---

### Task 4: Index build time, freshness and chips

**Files:**
- Modify: `src/inkvault/index.py:94` (next to the `timezone` meta row) and its imports
- Modify: `src/inkvault/retrieval.py` (append)
- Modify: `tests/test_retrieval.py` (append)

**Interfaces:**
- Consumes: `retrieval.db`, `has_table`, `has_column`, `CHIPS`, `SOURCES`.
- Produces: `meta.built` (UTC ISO with microseconds and `Z`) in search.db; `retrieval.freshness() -> {"built": "YYYY-MM-DDTHH:MM" | None, "timezone_now": str, "newer_screen": bool}`; `retrieval.sources_present() -> {chip: bool}` for every chip except `all`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_retrieval.py`:

```python
def test_index_records_when_it_was_built(vault):
    from inkvault import paths
    db = sqlite3.connect(paths.search_db())
    built = db.execute("SELECT value FROM meta WHERE key='built'").fetchone()[0]
    db.close()
    assert built.endswith("Z") and len(built) == 27


def test_freshness_says_when_and_whether_screen_is_newer(vault):
    from inkvault import paths, retrieval
    f = retrieval.freshness()
    assert f["built"] and f["newer_screen"] is False and f["timezone_now"]
    v = sqlite3.connect(paths.vault_db())
    v.execute("INSERT INTO screen_stretches VALUES ('screen:z','2026-02-27T23:00:00.000000Z',"
              "'2026-02-27T23:30:00.000000Z','Notes.exe','later')")
    v.commit(); v.close()
    assert retrieval.freshness()["newer_screen"] is True


def test_chips_show_only_sources_with_records(vault):
    from inkvault import retrieval
    assert retrieval.sources_present() == {"conversations": True, "summaries": True, "captures": True,
                                           "snippets": True, "screen": True, "web": True}


def test_freshness_and_chips_on_an_old_index(vault):
    from inkvault import paths, retrieval
    db = sqlite3.connect(paths.search_db())
    db.executescript("DELETE FROM meta WHERE key='built'; DROP TABLE pages; DROP TABLE pages_fts;"
                     "DROP INDEX events_source; ALTER TABLE events DROP COLUMN source;")
    db.commit(); db.close()
    assert retrieval.freshness()["built"] is None
    chips = retrieval.sources_present()
    assert chips["web"] is False and chips["captures"] is True
```

Run: `uv run pytest tests/test_retrieval.py -q`
Expected: the four new tests FAIL (`built` missing; no `freshness`).

- [ ] **Step 2: Record `built` in `index.py`**

Change the import `from datetime import datetime` to `from datetime import datetime, timezone`, and right after the line that inserts `'timezone'` into `meta` add:

```python
        db.execute("INSERT INTO meta VALUES ('built', ?)",  # the Atlas says how fresh search is (spec §3.3)
                   (datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),))
```

- [ ] **Step 3: Append `freshness` and `sources_present` to `retrieval.py`**

```python
def freshness():
    """When search was built, and whether screen capture has recorded more since (spec §3.3, §4.2). Never guessed
    from file times: an index without a recorded build time says so."""
    with db() as conn:
        meta = dict(conn.execute("SELECT key, value FROM meta").fetchall()) if has_table(conn, "meta") else {}
    latest = None
    if paths.vault_db().exists():
        v = paths.connect_ro(paths.vault_db())
        try:
            if has_table(v, "screen_stretches"):
                latest = v.execute("SELECT MAX(ended) FROM screen_stretches").fetchone()[0]
        finally:
            v.close()
    built, cutoff = meta.get("built"), meta.get("screen_cutoff")
    return {"built": local(built).strftime("%Y-%m-%dT%H:%M") if built else None,
            "timezone_now": datetime.now().astimezone().strftime("%Z (UTC%z)"),
            "newer_screen": bool(latest and (not cutoff or latest > cutoff))}


def has_rows(conn, kind, event_source):
    table = SOURCES[kind][0]
    if not has_table(conn, table):
        return False
    if kind == "events" and event_source:
        if has_column(conn, "events", "source"):
            return conn.execute("SELECT EXISTS(SELECT 1 FROM events WHERE source=?)", (event_source,)).fetchone()[0] == 1
        return event_source == "pieces" and conn.execute("SELECT EXISTS(SELECT 1 FROM events)").fetchone()[0] == 1
    return conn.execute(f"SELECT EXISTS(SELECT 1 FROM {table})").fetchone()[0] == 1


def sources_present():
    """Which chips the Atlas shows: a source with no records gets no chip (spec §4.2)."""
    with db() as conn:
        return {chip: any(has_rows(conn, k, src) for k in kinds) for chip, (kinds, src) in CHIPS.items() if kinds}
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_retrieval.py tests/test_mcp_golden.py -q`
Expected: all pass (the golden file never shows `meta`).

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/inkvault/index.py src/inkvault/retrieval.py tests/test_retrieval.py
git commit -m "Atlas search: index records its build time; freshness and source chips"
```

---

### Task 5: `atlas.json` replaces `dashboard.html`

**Files:**
- Modify: `src/inkvault/paths.py:41-42`
- Modify: `src/inkvault/dashboard.py` (module docstring, `build()`, remove `TEMPLATE`)
- Delete: `src/inkvault/atlas.html`
- Modify: `src/inkvault/cli.py:42-45` (`open_dashboard`)
- Modify: `tests/test_pipeline.py:66-81,398-399,416`, `tests/test_history.py:533-534`
- Create: tests appended to `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `dashboard.collect` (unchanged).
- Produces: `paths.dashboard() -> home/"atlas.json"`; `paths.atlas_lock() -> home/"atlas.lock"`; `paths.atlas_info() -> home/"atlas-helper.json"`; `dashboard.build() -> Path | None` writing compact JSON (`separators=(",", ":")`), atomically; `cli.open_dashboard(path)` prints where the data is (Task 10 replaces it).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pipeline.py`:

```python
def test_the_atlas_data_is_json_written_atomically(vault):
    import json
    from inkvault import dashboard, index, paths
    index.build()
    out = dashboard.build()
    assert out == paths.dashboard() and out.name == "atlas.json"
    data = json.loads(out.read_text(encoding="utf-8"))
    assert {"days", "themes", "topics", "digests", "built"} <= set(data)
    assert not list(paths.home().glob("*.tmp"))


def test_the_first_build_removes_the_old_static_page_once(vault, capsys):
    from inkvault import dashboard, index, paths
    index.build()
    old = paths.home() / "dashboard.html"
    old.write_text("<html>old</html>", encoding="utf-8")
    dashboard.build()
    assert not old.exists() and "The Memory Atlas is live now" in capsys.readouterr().out
    dashboard.build()
    assert "The Memory Atlas is live now" not in capsys.readouterr().out
```

Run: `uv run pytest tests/test_pipeline.py -q`
Expected: the two new tests FAIL (`dashboard.html` name).

- [ ] **Step 2: Change `paths.py`**

Replace `dashboard()` and add two paths after it:

```python
def dashboard() -> Path:
    """The Memory Atlas's data (spec §3.2): what the charts draw, rebuilt nightly. The page itself is in the package."""
    return home() / "atlas.json"


def atlas_lock() -> Path:
    """Held by the running Atlas helper for its whole life (spec §3.4)."""
    return home() / "atlas.lock"


def atlas_info() -> Path:
    """The running helper's port and control secret, readable only by this user (spec §3.4)."""
    return home() / "atlas-helper.json"
```

- [ ] **Step 3: Change `dashboard.py`**

Replace the module docstring with:

```python
"""Build atlas.json: the data behind the Memory Atlas, a private visual overview of the vault.

The data stays in the InkVault home. The page (inkvault/atlas/) is served on this computer only by atlasserve.py,
which reads this file; nothing is sent anywhere.
"""
```

Add `import os` to the imports. Replace the tail of `build()` from `payload = …` to the end of the module (including `TEMPLATE`) with:

```python
    out = paths.dashboard()
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, out)  # an open Atlas never reads half a file
    old = paths.home() / "dashboard.html"
    if old.exists():  # the static page of 0.3.0 and earlier: generated, never the user's data (spec §3.2)
        old.unlink()
        print("The Memory Atlas is live now: open it with `inkvault dashboard`.")
    n_topics = len(data["topics"]["ongoing"]) + len(data["topics"]["bursts"])
    print(f"atlas: {len(data['days'])} days, {len(data['themes'])} projects, {n_topics} topics, "
          f"{len(data['digests'])} digests -> {out}")
    return out
```

Delete `src/inkvault/atlas.html` (`git rm src/inkvault/atlas.html`). Its markup and script are ported from `git show v0.3.0:src/inkvault/atlas.html` in Task 8.

- [ ] **Step 4: Stop opening a JSON file in `cli.py`**

Replace `open_dashboard`:

```python
def open_dashboard(path):
    if path:
        print(f"Atlas data: {path}")
```

- [ ] **Step 5: Update the tests that read the page**

In `tests/test_pipeline.py`: rename `test_dashboard_detects_projects_and_embeds_no_template_marker` to `test_atlas_data_detects_projects` and delete its `assert "/*DATA*/null" not in html` line; at line ~399 delete `and "/*DATA*/null" not in html`. In `tests/test_history.py` around line 534 replace `assert 'id="web_sites"' in page and "Pages opened" in page` with `assert '"web_sites"' in page`.

- [ ] **Step 6: Run the tests**

Run: `uv run pytest -q`
Expected: all pass (`test_forget.py` still reads `paths.dashboard()` and finds JSON with the same compact text).

- [ ] **Step 7: Commit**

```bash
git add -A src/inkvault/paths.py src/inkvault/dashboard.py src/inkvault/atlas.html src/inkvault/cli.py tests/test_pipeline.py tests/test_history.py
git commit -m "Atlas search: dashboard.build writes atlas.json; the static page is retired"
```

---

### Task 6: The helper's server: authentication, headers and the API

**Files:**
- Create: `src/inkvault/atlasserve.py`
- Create: `src/inkvault/atlas/index.html` (placeholder: `<!doctype html><title>Memory Atlas</title>`; Task 8 replaces it)
- Create: `tests/test_atlasserve.py`

**Interfaces:**
- Consumes: `retrieval.search`, `record`, `decode_cursor`, `BadCursor`, `guarded_call`, `Refused`, `published`, `generation`, `freshness`, `sources_present`, `excerpt`, `when`, `source_label`, `CHIPS`, `ATLAS_MARKERS`, `SOURCES`; `dashboard.build`; `nightly.lock`, `nightly.Busy`; `paths`.
- Produces: `ASSETS` (path → `(file, content type)`), `ASSET_DIR`, `CSP`, `CODE_TTL = 60`, `MAX_BODY = 16384`, `MAX_QUERY = 500`, `PAGE_SIZE = 25`, `IDLE = 1800`, `COOKIE = "inkvault_atlas"`, `class Helper(port=0, now=time.monotonic)` with `.port`, `.server`, `.control`, `.last_ping`, `new_code()`, `redeem(code)`, `open_url(code)`; `class Handler`; `day_bounds(from_, to, tz=None) -> (since, until)`; `hit_json(hit) -> dict`; `build_atlas() -> "built"|"busy"|"no-days"`; `atlas_payload() -> dict`. API answers: `/api/atlas` `{"status": "ok"|"no-days"|"empty"|"rebuilding"|"busy"|"unavailable"|"changed", "generation", "data", "freshness", "sources"}`; `/api/search` `{"status": "ok"|"bad"|"empty"|"rebuilding"|"unavailable"|"changed", "generation", "hits", "more", "keyword_only", "message"}`; `/api/record` record dicts plus `"bad"`; `/api/ping` `{"status": "ok", "generation", "rebuilding"}`; `/control/code` `{"code"}`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_atlasserve.py`:

```python
"""The Atlas helper's HTTP surface (spec §3.4, §5), against a real server on a free port."""
import http.client
import json
import threading
from datetime import timedelta, timezone

import pytest

import atlasdata


@pytest.fixture
def helper(tmp_path, monkeypatch):
    atlasdata.make_vault(tmp_path, monkeypatch)
    from inkvault import atlasserve
    atlasdata.utc_times(monkeypatch)
    h = atlasserve.Helper()
    t = threading.Thread(target=h.server.serve_forever, daemon=True)
    t.start()
    yield h
    h.server.shutdown()
    h.server.server_close()


def req(h, method, path, body=None, headers=None, host=None):
    c = http.client.HTTPConnection("127.0.0.1", h.port, timeout=10)
    c.putrequest(method, path, skip_host=True)
    c.putheader("Host", host or f"127.0.0.1:{h.port}")
    data = b"" if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
    for k, v in (headers or {}).items():
        c.putheader(k, v)
    if method == "POST":
        c.putheader("Content-Type", "application/json")
        c.putheader("Content-Length", str(len(data)))
    c.endheaders(data if method == "POST" else None)
    r = c.getresponse()
    out = (r.status, dict(r.getheaders()), r.read())
    c.close()
    return out


@pytest.fixture
def session(helper):
    status, headers, _ = req(helper, "GET", f"/open?code={helper.new_code()}")
    assert status == 303 and headers["Location"] == "/"
    cookie = headers["Set-Cookie"]
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie
    return {"Cookie": cookie.split(";")[0]}


def api(helper, session, path, body=None):
    s, _, raw = req(helper, "POST" if body is not None else "GET", path, body, {**session, "X-InkVault": "1"})
    return s, json.loads(raw)


def test_a_code_works_once_and_expires(helper):
    code = helper.new_code()
    assert req(helper, "GET", f"/open?code={code}")[0] == 303
    assert req(helper, "GET", f"/open?code={code}")[0] == 403
    late = helper.new_code()
    helper.codes[late] = helper.now() - 1
    assert req(helper, "GET", f"/open?code={late}")[0] == 403


def test_the_page_needs_the_cookie_and_carries_strict_headers(helper, session):
    assert req(helper, "GET", "/")[0] == 403
    status, headers, body = req(helper, "GET", "/", headers=session)
    assert status == 200 and b"Memory Atlas" in body
    assert headers["Content-Security-Policy"].startswith("default-src 'none'; script-src 'self'")
    assert headers["Cache-Control"] == "no-store" and headers["Referrer-Policy"] == "no-referrer"
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert not any(k.lower().startswith("access-control") for k in headers)


def test_host_and_origin_must_be_this_helper(helper, session):
    assert req(helper, "GET", "/", headers=session, host=f"localhost:{helper.port}")[0] == 421
    assert req(helper, "GET", "/", headers=session, host="evil.example")[0] == 421
    assert req(helper, "GET", "/", headers={**session, "Origin": "https://evil.example"})[0] == 421


def test_the_api_needs_cookie_and_header(helper, session):
    assert req(helper, "POST", "/api/search", {"query": "x"}, session)[0] == 403
    assert req(helper, "POST", "/api/search", {"query": "x"}, {"X-InkVault": "1"})[0] == 403


def test_methods_sizes_and_paths(helper, session):
    h = {**session, "X-InkVault": "1"}
    assert req(helper, "GET", "/api/search", headers=h)[0] == 405
    for method in ("PUT", "DELETE", "PATCH", "OPTIONS"):
        assert req(helper, method, "/", headers=h)[0] == 405
    assert req(helper, "POST", "/api/search", b"x" * 20000, h)[0] == 413
    for path in ("/assets/../vault.db", "/assets/%2e%2e%2fvault.db", "/assets/nope.js", "/vault.db", "/?x=1"):
        assert req(helper, "GET", path, headers=session)[0] == 404


def test_control_code_needs_the_secret_not_the_cookie(helper, session):
    assert req(helper, "POST", "/control/code", {}, {**session, "X-InkVault": "1"})[0] == 403
    assert req(helper, "POST", "/control/code", {}, {"X-InkVault-Control": "wrong"})[0] == 403
    s, _, raw = req(helper, "POST", "/control/code", {}, {"X-InkVault-Control": helper.control})
    assert s == 200 and req(helper, "GET", f"/open?code={json.loads(raw)['code']}")[0] == 303


def test_search_api_returns_hits_with_labels_and_spans(helper, session):
    s, a = api(helper, session, "/api/search", {"query": "backoff", "source": "all"})
    assert s == 200 and a["status"] == "ok" and a["hits"] and len(a["generation"]) == 16
    # e2's snippet column (readable) holds the match; a snippet hit's snippet column (text) may not, so no spans
    hit = next(h for h in a["hits"] if h["id"] == "e2")
    assert hit["source_label"] == "Pieces capture · Code.exe" and hit["match"]["type"] == "keyword"
    a0, a1 = hit["match"]["spans"][0]
    assert hit["excerpt"][a0:a1].lower() == "backoff"
    s, cap = api(helper, session, "/api/search", {"query": "backoff", "source": "captures"})
    assert {h["id"] for h in cap["hits"]} == {"e2"}


def test_search_api_punctuation_only(helper, session):
    s, a = api(helper, session, "/api/search", {"query": "!!!"})
    assert s == 200 and a["status"] == "ok" and a["hits"] == []


def test_search_api_rejects_bad_dates(helper, session):
    for body in ({"query": "x", "from": "2026-02-28", "to": "2026-02-25"}, {"query": "x", "from": "2026-02-30"},
                 {"query": "x", "to": "yesterday"}, {"query": ""}, {"query": "x" * 501}, {"query": "x", "source": "z"},
                 {"query": "x", "offset": -1}, ["not", "an", "object"]):
        s, a = api(helper, session, "/api/search", body)
        assert s == 400 and a["status"] == "bad" and a["message"]


def test_later_pages_need_the_first_pages_generation(helper, session):
    from inkvault import paths
    _, first = api(helper, session, "/api/search", {"query": "backoff"})
    atlasdata.bump(paths.vectors())
    _, page2 = api(helper, session, "/api/search", {"query": "backoff", "offset": 25, "generation": first["generation"]})
    assert page2["status"] == "changed" and "hits" not in page2


def test_day_bounds_cover_local_days_across_daylight_saving():
    from zoneinfo import ZoneInfo
    from inkvault import atlasserve
    ny = ZoneInfo("America/New_York")
    since, until = atlasserve.day_bounds("2026-03-08", "2026-03-08", tz=ny)  # clocks go forward that night
    from datetime import datetime
    s = datetime.fromisoformat(since.replace("Z", "+00:00"))
    u = datetime.fromisoformat(until.replace("Z", "+00:00"))
    assert u - s == timedelta(hours=23) and s.astimezone(ny).hour == 0
    assert atlasserve.day_bounds(None, None) == ("", "")


def test_record_api(helper, session):
    _, r = api(helper, session, "/api/record", {"kind": "chats", "id": "m1"})
    assert r["status"] == "ok" and r["items"][0]["matched"]
    _, missing = api(helper, session, "/api/record", {"kind": "chats", "id": "nope"})
    assert missing["status"] == "missing"
    for body in ({"kind": "zzz", "id": "1"}, {"cursor": "garbage"}, {"kind": "chats"}):
        assert api(helper, session, "/api/record", body)[0] == 400


def test_atlas_api_builds_missing_data_then_serves_it(helper, session):
    from inkvault import paths
    assert not paths.dashboard().exists()
    _, a = api(helper, session, "/api/atlas")
    assert a["status"] == "ok" and a["data"]["days"] and paths.dashboard().exists()
    assert a["sources"]["web"] is True and a["freshness"]["built"]


def test_atlas_api_without_days_still_searches(tmp_path, monkeypatch):
    import sqlite3
    atlasdata.make_vault(tmp_path, monkeypatch)
    from inkvault import atlasserve, index, paths
    v = sqlite3.connect(paths.vault_db())
    v.executescript("DELETE FROM events; DELETE FROM screen_stretches; DELETE FROM screen_text; DELETE FROM browser_visits;"
                    "DELETE FROM raw_records WHERE kind != 'asset';")
    v.commit(); v.close()
    index.build()
    h = atlasserve.Helper()
    threading.Thread(target=h.server.serve_forever, daemon=True).start()
    try:
        status, headers, _ = req(h, "GET", f"/open?code={h.new_code()}")
        s = {"Cookie": headers["Set-Cookie"].split(";")[0]}
        assert api(h, s, "/api/atlas")[1]["status"] == "no-days"
        assert api(h, s, "/api/search", {"query": "retry"})[1]["hits"]
    finally:
        h.server.shutdown(); h.server.server_close()


def test_ping_keeps_alive_and_reports_generation(helper, session):
    helper.last_ping = 0
    _, a = api(helper, session, "/api/ping", {})
    assert a["status"] == "ok" and len(a["generation"]) == 16 and helper.last_ping > 0


def test_nothing_is_logged(helper, session, capfd):
    api(helper, session, "/api/search", {"query": "secretword"})
    req(helper, "GET", "/nope", headers=session)
    out, err = capfd.readouterr()
    assert "secretword" not in out + err and "GET" not in err and "POST" not in err
```

Run: `uv run pytest tests/test_atlasserve.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'inkvault.atlasserve'`.

- [ ] **Step 2: Create `src/inkvault/atlasserve.py`**

```python
"""The Memory Atlas helper: a private web server on this computer for the Atlas page, its data and search
(spec §3.4, §5). It listens on 127.0.0.1 only, answers only the browser tab it opened, and never writes the vault.
"""
import hmac
import json
import re
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import paths, retrieval

ASSET_DIR = Path(__file__).with_name("atlas")
ASSETS = {  # request path -> (packaged file, content type). Nothing else is ever served (spec §5 rule 7).
    "/": ("index.html", "text/html; charset=utf-8"),
    "/assets/atlas.css": ("atlas.css", "text/css; charset=utf-8"),
    "/assets/atlas.js": ("atlas.js", "text/javascript; charset=utf-8"),
    "/assets/search.js": ("search.js", "text/javascript; charset=utf-8"),
    "/assets/echarts.min.js": ("echarts.min.js", "text/javascript; charset=utf-8"),
    "/assets/mark.png": ("mark.png", "image/png"),
}
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; "
       "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
CODE_TTL = 60
MAX_BODY = 16 * 1024
MAX_QUERY = 500
PAGE_SIZE = 25
MAX_OFFSET = 10_000
IDLE = 30 * 60
COOKIE = "inkvault_atlas"
DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
CLOSED_TEXT = ("This Atlas link has already been used or has expired. Open the Atlas again from the "
               "InkVault Atlas icon or `inkvault dashboard`.").encode()


class Helper:
    """What request threads share: one-time codes, sessions, the control secret and the last ping."""

    def __init__(self, port=0, now=time.monotonic):
        self.now = now
        self.codes = {}       # one-time code -> expiry (spec §5 rule 2)
        self.sessions = set()
        self.control = secrets.token_hex(32)
        self.last_ping = now()
        self.lock = threading.Lock()
        self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.server.daemon_threads = True
        self.server.helper = self
        self.port = self.server.server_address[1]

    def new_code(self):
        code = secrets.token_urlsafe(32)
        with self.lock:
            self.codes[code] = self.now() + CODE_TTL
        return code

    def redeem(self, code):
        with self.lock:
            expiry = self.codes.pop(code, None)
            if expiry is None or expiry < self.now():
                return None
            session = secrets.token_urlsafe(32)
            self.sessions.add(session)
            return session

    def open_url(self, code):
        return f"http://127.0.0.1:{self.port}/open?code={code}"


class BadRequest(Exception):
    """Input the page couldn't have sent; answered 400 with a sentence."""


class TooLarge(Exception):
    pass


def day_bounds(from_, to, tz=None):
    """Local calendar days -> UTC instants for retrieval.search (spec §4.2): the start of From, and the start of the
    day after To (an exclusive end), each with that date's own UTC offset, so daylight-saving days are 23 or 25
    hours. "" for a side that wasn't given."""
    def start(day):
        if not isinstance(day, str) or not DAY.fullmatch(day):
            raise BadRequest("Dates look like 2026-10-05.")
        try:
            d = datetime.fromisoformat(day)
        except ValueError:
            raise BadRequest(f"{day} isn't a date.") from None
        local = d.replace(tzinfo=tz) if tz else d.astimezone()  # naive -> this computer's zone for that date
        return local.astimezone(timezone.utc)
    since = start(from_) if from_ else None
    until = start((datetime.fromisoformat(to) + timedelta(days=1)).date().isoformat()) if to and start(to) else None
    if since and until and since >= until:
        raise BadRequest("From is after To.")
    fmt = lambda t: t.strftime("%Y-%m-%dT%H:%M:%S.%fZ") if t else ""
    return fmt(since), fmt(until)


def hit_json(h):
    text, spans = retrieval.excerpt(h)
    return {"kind": h.kind, "id": h.id, "time": retrieval.when(h.created), "title": h.display or h.title or "",
            "source_label": retrieval.source_label(h), "excerpt": text,
            "match": {"type": "keyword", "spans": spans} if h.snippet else {"type": "meaning"}}


def build_atlas():
    """atlas.json when it's missing (spec §3.4), under the nightly lock, without waiting for a run in progress."""
    from . import dashboard, nightly
    try:
        with nightly.lock("dashboard"):
            return "built" if dashboard.build() else "no-days"
    except nightly.Busy:
        return "busy"


def atlas_payload():
    stamp = retrieval.published()
    if stamp[0]:
        return {"status": "rebuilding"}
    if stamp[3] is None:
        return {"status": "unavailable"}
    if not paths.search_db().exists():
        return {"status": "empty"}
    built = None
    if not paths.dashboard().exists():
        built = build_atlas()
        if built == "busy":
            return {"status": "busy"}

    def read():
        try:
            data = json.loads(paths.dashboard().read_text(encoding="utf-8"))
        except FileNotFoundError:
            data = None
        return data, retrieval.freshness(), retrieval.sources_present()
    try:
        (data, fresh, chips), gen = retrieval.guarded_call(read)
    except retrieval.Refused as r:
        return {"status": r.status}
    if data is None:
        return {"status": "no-days" if built == "no-days" else "rebuilding", "generation": gen,
                "freshness": fresh, "sources": chips}
    return {"status": "ok", "generation": gen, "data": data, "freshness": fresh, "sources": chips}


def api_search(body):
    q = body.get("query")
    if not isinstance(q, str) or not q.strip() or len(q) > MAX_QUERY:
        raise BadRequest(f"Type something to search for (at most {MAX_QUERY} characters).")
    chip = body.get("source", "all")
    if chip not in retrieval.CHIPS:
        raise BadRequest("Unknown source.")
    offset = body.get("offset", 0)
    if not isinstance(offset, int) or isinstance(offset, bool) or not 0 <= offset <= MAX_OFFSET:
        raise BadRequest("Bad page.")
    since, until = day_bounds(body.get("from"), body.get("to"))
    kinds, event_source = retrieval.CHIPS[chip]
    try:
        r, gen = retrieval.guarded_call(retrieval.search, q, "all", "hybrid", since, until, PAGE_SIZE, offset=offset,
                                        kinds=kinds, event_source=event_source, markers=retrieval.ATLAS_MARKERS,
                                        require_model=True)
    except retrieval.Refused as e:
        return {"status": e.status}
    if offset and body.get("generation") != gen:
        return {"status": "changed"}
    if r.empty_index:
        return {"status": "empty"}
    if r.error:
        raise BadRequest(r.error)
    return {"status": "ok", "generation": gen, "hits": [hit_json(h) for h in r.hits], "more": r.more,
            "keyword_only": r.keyword_only}


def api_record(body):
    if "cursor" in body:
        try:
            cursor = retrieval.decode_cursor(body["cursor"])
        except retrieval.BadCursor:
            raise BadRequest("Bad cursor.") from None
        kind, id_ = None, None
    else:
        cursor, kind, id_ = None, body.get("kind"), body.get("id")
        if kind not in retrieval.SOURCES or not isinstance(id_, str) or not 0 < len(id_) <= 200:
            raise BadRequest("Bad record.")
    try:
        r, _ = retrieval.guarded_call(retrieval.record, kind, id_, cursor)
    except retrieval.Refused as e:
        return {"status": e.status}
    return r


class Handler(BaseHTTPRequestHandler):
    server_version = "InkVault"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # spec §5 rule 10: no request log, so no query or record text anywhere
        pass

    @property
    def helper(self):
        return self.server.helper

    def send(self, status, body=b"", ctype="application/json; charset=utf-8", extra=()):
        self.send_response(status)
        for k, v in (("Content-Type", ctype), ("Content-Length", str(len(body))), ("Cache-Control", "no-store"),
                     ("Content-Security-Policy", CSP), ("Referrer-Policy", "no-referrer"),
                     ("X-Content-Type-Options", "nosniff"), ("X-Frame-Options", "DENY"), *extra):
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def answer(self, status, obj):
        self.send(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def host_ok(self):
        """Spec §5 rule 4: exactly this helper's host, so a rebinding DNS name never counts as same-site."""
        here = f"127.0.0.1:{self.helper.port}"
        origin = self.headers.get("Origin")
        return self.headers.get("Host") == here and origin in (None, f"http://{here}")

    def session_ok(self):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie") or "")
        except CookieError:
            return False
        morsel = cookie.get(COOKIE)
        return morsel is not None and morsel.value in self.helper.sessions

    def api_ok(self):
        return self.session_ok() and self.headers.get("X-InkVault") == "1"

    def body(self):
        if self.headers.get("Transfer-Encoding"):
            raise BadRequest("Send a plain request body.")
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise BadRequest("Bad length.") from None
        if n > MAX_BODY:
            # Read (up to 1 MB) and drop it, so the 413 arrives instead of a reset connection; then close.
            self.rfile.read(min(n, 1 << 20))
            raise TooLarge
        try:
            obj = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            raise BadRequest("Send JSON.") from None
        if not isinstance(obj, dict):
            raise BadRequest("Send a JSON object.")
        return obj

    def do_GET(self):
        if not self.host_ok():
            return self.send(421)
        url = urlsplit(self.path)
        if url.path == "/open":
            session = self.helper.redeem((parse_qs(url.query).get("code") or [""])[0])
            if not session:
                return self.send(403, CLOSED_TEXT, "text/plain; charset=utf-8")
            return self.send(303, extra=(("Location", "/"),
                                         ("Set-Cookie", f"{COOKIE}={session}; HttpOnly; SameSite=Strict; Path=/")))
        if url.path in ASSETS and not url.query:
            if not self.session_ok():
                return self.send(403, CLOSED_TEXT, "text/plain; charset=utf-8")
            name, ctype = ASSETS[url.path]
            return self.send(200, (ASSET_DIR / name).read_bytes(), ctype)
        if url.path == "/api/atlas" and not url.query:
            if not self.api_ok():
                return self.send(403)
            return self.answer(200, atlas_payload())
        if url.path in ("/api/search", "/api/record", "/api/ping", "/control/code"):
            return self.send(405)
        return self.send(404)

    def do_POST(self):
        if not self.host_ok():
            return self.send(421)
        path = urlsplit(self.path).path
        if path == "/control/code":
            given = self.headers.get("X-InkVault-Control") or ""
            if not hmac.compare_digest(given.encode(), self.helper.control.encode()):
                return self.send(403)
            return self.answer(200, {"code": self.helper.new_code()})
        if path not in ("/api/search", "/api/record", "/api/ping"):
            return self.send(405 if path in ASSETS or path in ("/api/atlas", "/open") else 404)
        if not self.api_ok():
            return self.send(403)
        try:
            body = self.body()
            if path == "/api/ping":
                self.helper.last_ping = self.helper.now()
                stamp = retrieval.published()
                return self.answer(200, {"status": "ok", "generation": retrieval.generation(stamp),
                                         "rebuilding": bool(stamp[0])})
            return self.answer(200, api_search(body) if path == "/api/search" else api_record(body))
        except TooLarge:
            self.close_connection = True
            return self.send(413)
        except BadRequest as e:
            return self.answer(400, {"status": "bad", "message": str(e)})

    def refuse(self):
        self.send(405)

    do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_HEAD = refuse
```

Create `src/inkvault/atlas/index.html` containing exactly `<!doctype html><title>Memory Atlas</title>` (Task 8 replaces it). The other `ASSETS` files don't exist yet; nothing requests them until Task 8.

- [ ] **Step 3: Run the tests**

Run: `uv run pytest tests/test_atlasserve.py -q`
Expected: all pass. If `test_day_bounds_cover_local_days_across_daylight_saving` fails with `ZoneInfoNotFoundError` on Windows, check that `tzdata` is in the dev group (it is: `pyproject.toml` `dev`).

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add src/inkvault/atlasserve.py src/inkvault/atlas/index.html tests/test_atlasserve.py
git commit -m "Atlas search: local helper with one-time code, cookie, header and host checks; atlas, search, record and ping APIs"
```

---

### Task 7: One helper at a time, and stopping when idle

**Files:**
- Modify: `src/inkvault/atlasserve.py` (append)
- Create: `tests/test_atlas_helper.py`

**Interfaces:**
- Consumes: `nightly.try_lock(fd)`, `nightly.unlock(fd)`, `paths.atlas_lock()`, `paths.atlas_info()`, `Helper`.
- Produces: `prepare_process()`, `write_info(helper)`, `request_code(info, timeout=2.0) -> str | None`, `open_in_browser(url)`, `reuse(open_browser, wait=10.0) -> int`, `watchdog(helper, idle, stop_event)`, `serve(open_browser=True, idle=IDLE, wait=10.0) -> int`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_atlas_helper.py`:

```python
"""One helper per home, decided by an OS lock (spec §3.4); idle stop; offline model setting."""
import json
import os
import subprocess
import sys
import time

import pytest

LAUNCH = """
import os, sys
os.environ["INKVAULT_HOME"] = sys.argv[1]
from inkvault import atlasserve
sys.exit(atlasserve.serve(open_browser=False, idle=float(sys.argv[2]), wait=float(sys.argv[3])))
"""
HOLD = """
import os, sys
os.environ["INKVAULT_HOME"] = sys.argv[1]
from inkvault import nightly, paths
fd = os.open(paths.atlas_lock(), os.O_RDWR | os.O_CREAT)
assert nightly.try_lock(fd)
print("held", flush=True)
sys.stdin.read()
"""


def launch(home, idle=60, wait=10):
    return subprocess.Popen([sys.executable, "-c", LAUNCH, str(home), str(idle), str(wait)],
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def wait_for_info(home, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            return json.loads((home / "atlas-helper.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            time.sleep(0.1)
    raise AssertionError("no helper info")


def stop(p):
    if p.poll() is None:
        p.kill()
    p.communicate(timeout=20)


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "José Ñ vault's home"
    h.mkdir()
    return h


# Helpers are told apart by their control secret, not by pid: on Windows a virtual environment's python.exe can be a
# launcher that runs the real interpreter as a child, so Popen.pid isn't the helper's own pid.

def test_two_launches_at_once_make_one_helper(home):
    from inkvault import atlasserve
    a, b = launch(home), launch(home)
    try:
        info = wait_for_info(home)
        deadline = time.monotonic() + 20
        while a.poll() is None and b.poll() is None and time.monotonic() < deadline:
            time.sleep(0.1)
        done = a if a.poll() is not None else b
        assert done.wait(timeout=20) == 0
        assert "already running" in done.communicate(timeout=20)[0]
        alive = b if done is a else a
        assert alive.poll() is None and atlasserve.request_code(info)
    finally:
        stop(a); stop(b)


def test_a_busy_lock_holder_is_never_replaced(home):
    holder = subprocess.Popen([sys.executable, "-c", HOLD, str(home)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        p = launch(home, wait=1)
        out, _ = p.communicate(timeout=30)
        assert p.returncode == 1 and "starting or busy" in out
        assert not (home / "atlas-helper.json").exists()
    finally:
        holder.stdin.close()
        holder.wait(timeout=20)


def test_a_killed_helpers_lock_is_free_at_once(home):
    from inkvault import atlasserve
    first = launch(home)
    try:
        old = wait_for_info(home)
        first.kill(); first.wait(timeout=20)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:   # gone: it no longer answers
            try:
                if not atlasserve.request_code(old, timeout=0.5):
                    break
            except OSError:
                break
            time.sleep(0.1)
        second = launch(home)
        try:
            deadline = time.monotonic() + 20
            info = old
            while info["control"] == old["control"] and time.monotonic() < deadline:
                time.sleep(0.1)
                info = wait_for_info(home)
            assert info["control"] != old["control"] and atlasserve.request_code(info)
            assert second.poll() is None  # it became the helper instead of reporting a busy one
        finally:
            stop(second)
    finally:
        stop(first)


def test_an_idle_helper_stops_and_cleans_up(home):
    p = launch(home, idle=1)
    wait_for_info(home)
    assert p.wait(timeout=30) == 0
    assert not (home / "atlas-helper.json").exists()


def test_helper_in_a_home_with_spaces_and_accents(home):
    p = launch(home)
    try:
        info = wait_for_info(home)
        from inkvault import atlasserve
        assert atlasserve.request_code(info)
    finally:
        stop(p)


def test_info_file_is_private_on_posix(home, monkeypatch):
    if os.name == "nt":
        pytest.skip("POSIX permissions")
    p = launch(home)
    try:
        wait_for_info(home)
        assert (home / "atlas-helper.json").stat().st_mode & 0o077 == 0
    finally:
        stop(p)


def test_the_helper_process_is_offline(monkeypatch):
    from inkvault import atlasserve
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    atlasserve.prepare_process()
    assert os.environ["HF_HUB_OFFLINE"] == "1"
```

Run: `uv run pytest tests/test_atlas_helper.py -q`
Expected: FAIL (`serve` and `prepare_process` don't exist).

- [ ] **Step 2: Append to `atlasserve.py`**

```python
import http.client  # add to the imports at the top
import os           # add to the imports at the top
import webbrowser   # add to the imports at the top


def prepare_process():
    """The helper never downloads a model or shows progress bars (spec §5 rule 11)."""
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")


def write_info(helper):
    """Port and control secret for a second launch, written whole (temp file, then replace), user-only on POSIX."""
    info = {"pid": os.getpid(), "port": helper.port, "control": helper.control,
            "started": datetime.now(timezone.utc).isoformat()}
    tmp = paths.atlas_info().with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(info, f)
    os.replace(tmp, paths.atlas_info())


def request_code(info, timeout=2.0):
    """Ask the running helper for a fresh one-time code (spec §3.4). None when it doesn't answer as expected."""
    port, control = int(info["port"]), str(info["control"])
    if not 0 < port < 65536:
        raise ValueError("bad port")
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        c.request("POST", "/control/code", body=b"{}",
                  headers={"X-InkVault-Control": control, "Content-Type": "application/json"})
        r = c.getresponse()
        body = r.read()
        return json.loads(body)["code"] if r.status == 200 else None
    finally:
        c.close()


def open_in_browser(url):
    webbrowser.open(url)


def reuse(open_browser, wait=10.0):
    """Another process holds the lock, so a helper is alive: get a code from it. Never start a second helper."""
    deadline = time.monotonic() + wait
    while True:
        try:
            info = json.loads(paths.atlas_info().read_text(encoding="utf-8"))
            code = request_code(info)
            if code:
                if open_browser:
                    open_in_browser(f"http://127.0.0.1:{int(info['port'])}/open?code={code}")
                    print("Opened the Memory Atlas in your browser.")
                else:
                    print("The Memory Atlas is already running.")
                return 0
        except (OSError, ValueError, KeyError, TypeError):
            pass  # just started and still writing its info, or busy: try again until the deadline
        if time.monotonic() >= deadline:
            print("The Atlas is starting or busy; try again in a moment.")
            return 1
        time.sleep(0.25)


def watchdog(helper, idle, stop_event):
    """Stop the helper when no open Atlas tab has pinged for `idle` seconds (spec §3.4)."""
    step = max(0.2, min(30.0, idle / 4))
    while not stop_event.wait(step):
        if helper.now() - helper.last_ping > idle:
            helper.server.shutdown()
            return


def serve(open_browser=True, idle=IDLE, wait=10.0):
    """Become the helper if the OS lock says nobody else is, or hand the request to the one that is."""
    from . import nightly
    prepare_process()
    fd = os.open(paths.atlas_lock(), os.O_RDWR | os.O_CREAT)
    try:
        try:
            mine = nightly.try_lock(fd)
        except OSError as e:  # a drive without file locking: run, without the one-helper guarantee
            print(f"Couldn't take the Atlas lock ({e}); starting anyway.")
            mine = True
        if not mine:
            return reuse(open_browser, wait)
        helper = Helper()
        stop_event = threading.Event()
        try:
            write_info(helper)
            threading.Thread(target=watchdog, args=(helper, idle, stop_event), daemon=True).start()
            if open_browser:
                open_in_browser(helper.open_url(helper.new_code()))
                print("The Memory Atlas is open in your browser. It stops by itself 30 minutes after you close it; "
                      "Ctrl+C stops it now.")
            else:
                print(f"The Memory Atlas helper is running on 127.0.0.1:{helper.port}. "
                      "Open it with `inkvault dashboard`.")
            try:
                helper.server.serve_forever()
            except KeyboardInterrupt:
                pass
            return 0
        finally:
            stop_event.set()
            helper.server.server_close()
            try:
                if json.loads(paths.atlas_info().read_text(encoding="utf-8")).get("control") == helper.control:
                    paths.atlas_info().unlink()
            except (OSError, ValueError):
                pass
            nightly.unlock(fd)
    finally:
        os.close(fd)
```

- [ ] **Step 3: Run the tests**

Run: `uv run pytest tests/test_atlas_helper.py -q`
Expected: all pass (the subprocesses are `sys.executable`, which `conftest.py` allows).

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add src/inkvault/atlasserve.py tests/test_atlas_helper.py
git commit -m "Atlas search: one helper per home by OS lock; second launch reuses it; idle stop; offline process"
```

---

### Task 8: The page, Atlas view (Ink & Light, offline charts, mark)

**Files:**
- Create: `tools/make_mark.py`; generated `src/inkvault/atlas/mark.png`, `src/inkvault/atlas/inkvault.ico`
- Create: `src/inkvault/atlas/echarts.min.js`, `ECHARTS-LICENSE`, `ECHARTS-NOTICE`, `ECHARTS.sha256`
- Replace: `src/inkvault/atlas/index.html`
- Create: `src/inkvault/atlas/atlas.css`, `src/inkvault/atlas/atlas.js`, `src/inkvault/atlas/search.js` (stub in this task: `"use strict";`)
- Create: `tests/test_atlas_page.py`

**Interfaces:**
- Consumes: `/api/atlas` payload `data` (v0.3.0's dashboard payload, unchanged).
- Produces: `window.Atlas = {show(data), clear()}` for `search.js` (Task 9); element ids listed in `index.html` below; CSS classes `tt-muted`, `tt-story`.

- [ ] **Step 1: Write the failing page tests**

Create `tests/test_atlas_page.py`:

```python
"""The packaged page obeys its own security policy (spec §3.5, §5 rules 8-9). Browser behaviour is checked in Task 12."""
import hashlib
import re
from pathlib import Path

from inkvault import atlasserve

ATLAS = Path(atlasserve.__file__).with_name("atlas")


def text(name):
    return (ATLAS / name).read_text(encoding="utf-8")


def test_every_served_file_exists_and_the_page_uses_only_them():
    for name, _ in atlasserve.ASSETS.values():
        assert (ATLAS / name).is_file(), name
    html = text("index.html")
    for ref in re.findall(r'(?:src|href)="([^"]+)"', html):
        assert ref in atlasserve.ASSETS, ref


def test_no_inline_script_style_or_handlers():
    html = text("index.html")
    assert "<style" not in html and " style=" not in html
    assert not re.search(r"\son[a-z]+=", html)
    for script in re.findall(r"<script\b[^>]*>(.*?)</script>", html, re.S):
        assert script.strip() == ""


def test_tooltips_escape_and_carry_no_styles():
    js = text("atlas.js")
    assert ('const esc = s => String(s).replace(/[&<>"\']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '
            '\'"\': "&quot;", "\'": "&#39;"}[c]));') in js
    assert "style=" not in js and "innerHTML" not in js


def test_search_never_uses_html():
    js = text("search.js")
    assert "innerHTML" not in js and "outerHTML" not in js and "insertAdjacentHTML" not in js


def test_echarts_is_the_pinned_build_with_its_license():
    data = (ATLAS / "echarts.min.js").read_bytes()
    assert hashlib.sha256(data).hexdigest() == text("ECHARTS.sha256").split()[0]
    assert b"5.5.0" in data
    assert "Apache License" in text("ECHARTS-LICENSE") and text("ECHARTS-NOTICE").strip()


def test_mark_and_icon():
    png = (ATLAS / "mark.png").read_bytes()
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) < 100_000
    assert (ATLAS / "inkvault.ico").read_bytes()[:4] == b"\x00\x00\x01\x00"
```

Run: `uv run pytest tests/test_atlas_page.py -q`
Expected: FAIL (files missing).

- [ ] **Step 2: Vendor ECharts 5.5.0**

```bash
curl -fsSL https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js -o src/inkvault/atlas/echarts.min.js
curl -fsSL https://cdn.jsdelivr.net/npm/echarts@5.5.0/LICENSE -o src/inkvault/atlas/ECHARTS-LICENSE
curl -fsSL https://cdn.jsdelivr.net/npm/echarts@5.5.0/NOTICE -o src/inkvault/atlas/ECHARTS-NOTICE
uv run python -c "import hashlib,pathlib;p=pathlib.Path('src/inkvault/atlas/echarts.min.js');print(hashlib.sha256(p.read_bytes()).hexdigest(), 'echarts.min.js 5.5.0')" > src/inkvault/atlas/ECHARTS.sha256
```

Confirm the file is the same as the CDN copy v0.3.0 loaded: `curl -fsSL https://cdnjs.cloudflare.com/ajax/libs/echarts/5.5.0/echarts.min.js | sha256sum` must print the same hash (on Windows: pipe to `uv run python -c "import sys,hashlib;print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())"`). If they differ, stop and report.

- [ ] **Step 3: Make the mark and icon from the public logo**

Create `tools/make_mark.py`:

```python
"""Make the Atlas mark and the shortcut icon from the InkVault logo. Run once; the outputs are committed.

    uv run --with pillow python tools/make_mark.py
"""
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "docs" / "assets" / "inkvault-logo-horizontal.png"
OUT = ROOT / "src" / "inkvault" / "atlas"
BOX = (200, 320, 510, 665)  # the mark alone, without the wordmark (left, top, right, bottom)


def main():
    mark = Image.open(SRC).convert("RGBA").crop(BOX)
    small = mark.resize((round(mark.width * 128 / mark.height), 128), Image.LANCZOS)
    small.save(OUT / "mark.png", optimize=True)
    side = max(mark.size)
    square = Image.new("RGBA", (side, side), (255, 255, 255, 255))
    square.paste(mark, ((side - mark.width) // 2, (side - mark.height) // 2))
    square.save(OUT / "inkvault.ico", sizes=[(16, 16), (32, 32), (48, 48), (256, 256)])
    print("wrote", OUT / "mark.png", "and", OUT / "inkvault.ico")


if __name__ == "__main__":
    main()
```

Run: `uv run --with pillow python tools/make_mark.py`
Open `src/inkvault/atlas/mark.png`: it must show the mark only (no letters) with nothing cut off. If letters show or the mark is clipped, adjust `BOX` and run again.

- [ ] **Step 4: Write `index.html`**

Replace `src/inkvault/atlas/index.html`:

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<title>Memory Atlas</title>
<link rel="icon" href="/assets/mark.png">
<link rel="stylesheet" href="/assets/atlas.css">
<script src="/assets/echarts.min.js" defer></script>
<script src="/assets/atlas.js" defer></script>
<script src="/assets/search.js" defer></script>
</head>
<body>
<header class="masthead">
  <img class="brand-mark" src="/assets/mark.png" alt="" width="40" height="44">
  <div class="masthead-title">Memory Atlas</div>
  <nav class="views" aria-label="Views">
    <button type="button" id="tab-atlas" aria-pressed="true">Atlas</button>
    <button type="button" id="tab-search" aria-pressed="false">Search</button>
  </nav>
  <form id="search-form" class="searchbox" role="search">
    <label class="visually-hidden" for="q">Search your memory</label>
    <input id="q" type="search" maxlength="500" autocomplete="off" spellcheck="false" placeholder="Search your memory">
    <button type="submit">Search</button>
  </form>
</header>
<div id="notice" class="notice" role="status" hidden><span id="notice-text"></span><button type="button" id="notice-retry" hidden>Retry</button></div>
<main>
  <section id="welcome" class="panel" hidden>
    <h2>Your vault is empty so far</h2>
    <p>Run <code>inkvault rescue</code> (with Pieces) or <code>inkvault sync</code> (Claude Code, Codex, Claude desktop and browser history), then open the Atlas again.</p>
  </section>
  <section id="closed" class="panel" hidden>
    <h2>The Atlas has closed</h2>
    <p>Open it again from the InkVault Atlas icon or <code>inkvault dashboard</code>.</p>
  </section>

  <section id="view-atlas" aria-label="Atlas">
    <p class="sub" id="sub"></p>
    <p id="atlas-empty" class="empty" hidden></p>
    <div class="filters" role="group" aria-label="Date range">
      <button type="button" data-range="all">All time</button>
      <button type="button" data-range="30">Last 30 days</button>
      <button type="button" data-range="90">Last 90 days</button>
      <button type="button" data-range="365">Last year</button>
      <span class="custom">from <input type="date" id="from" aria-label="From date"> to <input type="date" id="to" aria-label="To date"></span>
    </div>
    <div class="tiles" id="tiles"></div>
    <section class="card" data-table="calendar">
      <div class="head"><div><h2>Active hours per day</h2>
        <p class="desc">How many different hours of each day you were on the computer (Pieces captures, screen capture, session summaries, chats, pages opened in a browser). Hover a day for what it was about.</p></div>
        <button type="button" class="tbl">Show table</button></div>
      <div class="chart" id="calendar"></div>
    </section>
    <div class="grid2">
      <section class="card" data-table="rhythm">
        <div class="head"><div><h2>When you work</h2>
          <p class="desc">Share of days you were active in each hour, by weekday (local time).</p></div>
          <button type="button" class="tbl">Show table</button></div>
        <div class="chart" id="rhythm"></div>
      </section>
      <section class="card" data-table="graph">
        <div class="head"><div><h2>How projects connect</h2>
          <p class="desc">Projects that come up in the same session. Bigger dot = more sessions; thicker line = more shared sessions.</p></div>
          <button type="button" class="tbl">Show table</button></div>
        <div class="chart" id="graph"></div>
      </section>
    </div>
    <section class="card" data-table="themes">
      <div class="head"><div><h2>Projects over time</h2>
        <p class="desc">Sessions per week that mention each project, found automatically in your session and chat titles. To choose your own, write <code>Name = pattern</code> lines in <code>themes.txt</code> in your InkVault folder.</p></div>
        <button type="button" class="tbl">Show table</button></div>
      <div class="chart" id="themes"></div>
    </section>
    <section class="card" data-table="topics" id="topics-card">
      <div class="head"><div><h2>Topics over time</h2>
        <p class="desc">Pieces' own topic tags on your sessions. Ongoing topics recur across months; bursts are concentrated in a few weeks. Each group has its own color scale.</p></div>
        <button type="button" class="tbl">Show table</button></div>
      <div id="topics-ongoing-box"><h3 class="group">Ongoing</h3><div class="chart" id="topics-ongoing"></div></div>
      <div id="topics-bursts-box"><h3 class="group">Bursts</h3><div class="chart" id="topics-bursts"></div></div>
      <p class="empty" id="topics-none" hidden>No tagged sessions in this range</p>
    </section>
    <section class="card" data-table="sources">
      <div class="head"><div><h2>Activity by source</h2>
        <p class="desc">Records per week from each source. Each panel has its own scale — compare shapes, not heights.</p></div>
        <button type="button" class="tbl">Show table</button></div>
      <div class="multiples" id="sources"></div>
    </section>
    <div class="grid2">
      <section class="card" data-table="apps">
        <div class="head"><div><h2>Top apps</h2><p class="desc">Pieces captures per app.</p></div>
          <button type="button" class="tbl">Show table</button></div>
        <div class="chart" id="apps"></div>
      </section>
      <section class="card" data-table="sites">
        <div class="head"><div><h2>Top sites seen by Pieces</h2><p class="desc">Screen captures per website (from Pieces' browser captures).</p></div>
          <button type="button" class="tbl">Show table</button></div>
        <div class="chart" id="sites"></div>
      </section>
    </div>
    <section class="card" data-table="screen_apps">
      <div class="head"><div><h2>Screen time by app</h2><p class="desc">Minutes each app was in front, from screen capture.</p></div>
        <button type="button" class="tbl">Show table</button></div>
      <div class="chart" id="screen_apps"></div>
    </section>
    <section class="card" data-table="web_sites">
      <div class="head"><div><h2>Top sites opened in a browser</h2><p class="desc">Page visits per website, from the browser profiles you chose. Visits synced from your other devices aren't counted here.</p></div>
        <button type="button" class="tbl">Show table</button></div>
      <div class="chart" id="web_sites"></div>
    </section>
  </section>

  <section id="view-search" aria-label="Search" hidden>
    <div class="search-filters">
      <div class="chips" role="group" aria-label="Sources">
        <button type="button" data-source="all" aria-pressed="true">All</button>
        <button type="button" data-source="conversations" aria-pressed="false" hidden>Conversations</button>
        <button type="button" data-source="summaries" aria-pressed="false" hidden>Summaries</button>
        <button type="button" data-source="captures" aria-pressed="false" hidden>Captures</button>
        <button type="button" data-source="snippets" aria-pressed="false" hidden>Snippets</button>
        <button type="button" data-source="screen" aria-pressed="false" hidden>Screen</button>
        <button type="button" data-source="web" aria-pressed="false" hidden>Web history</button>
      </div>
      <span class="dates">From <input type="date" id="s-from" aria-label="Search from date"> To <input type="date" id="s-to" aria-label="Search to date"> <span id="tz"></span></span>
    </div>
    <p id="freshness" class="freshness"></p>
    <div class="search-layout">
      <div>
        <p id="search-status" class="search-status" role="status"></p>
        <ol id="results" class="results" aria-label="Results"></ol>
        <button type="button" id="more" hidden>More results</button>
      </div>
      <article id="reader" class="reader" aria-live="polite"></article>
    </div>
  </section>
  <footer id="foot"></footer>
</main>
</body>
</html>
```

- [ ] **Step 5: Write `atlas.css`**

```css
/* The Memory Atlas, Ink & Light (spec §1, §3.5). Every style lives in this file: the page's security policy refuses
   inline styles, so markup and chart tooltips use classes only. */
:root {
  color-scheme: light;
  --page: #211812; --surface-1: #eee0c3; --surface-2: #f6ecd6; --ink: #30251b; --ink-2: #655039; --muted: #745e43;
  --grid: #d1bb96; --axis: #ad8d60; --ring: #b69a6e; --series-1: #8d622d; --empty: #e4d4b4; --hit: #d3ac6d88;
  --frame-ink: #f1e2c5; --frame-muted: #cab493; --gold: #d3ac6d; --control: #30231a; --control-ring: #886a46;
  --seq: #e8d7b3,#d7bd8b,#c3a067,#ad8448,#926832,#745029,#543a20;
}
@media (prefers-color-scheme: dark) {
  :root:where(:not([data-theme="light"])) {
    color-scheme: dark;
    --surface-1: #30271d; --surface-2: #3a2f23; --ink: #eee0c5; --ink-2: #d5bd96; --muted: #bea47e;
    --grid: #51412e; --axis: #8d714d; --ring: #70583a; --series-1: #d5ab69; --empty: #3c3022; --hit: #d5ab6955;
    --seq: #443321,#5d4428,#785531,#967044,#b88b53,#d3ad70,#ebcf99;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --surface-1: #30271d; --surface-2: #3a2f23; --ink: #eee0c5; --ink-2: #d5bd96; --muted: #bea47e;
  --grid: #51412e; --axis: #8d714d; --ring: #70583a; --series-1: #d5ab69; --empty: #3c3022; --hit: #d5ab6955;
  --seq: #443321,#5d4428,#785531,#967044,#b88b53,#d3ad70,#ebcf99;
}
* { box-sizing: border-box; }
[hidden] { display: none !important; }
body { margin: 0; min-height: 100vh; color: var(--ink); font: 14px/1.6 system-ui, -apple-system, "Segoe UI", sans-serif;
  background: radial-gradient(ellipse at 90% 0%, #80512655, transparent 40%),
              radial-gradient(ellipse at 8% 75%, #8c5d2622, transparent 45%), var(--page); }
main { max-width: 1240px; margin: 0 auto; padding: 0 30px 64px; }
code { font: 12px ui-monospace, Consolas, monospace; }
.visually-hidden { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
button:focus-visible, input:focus-visible, a:focus-visible { outline: 3px solid #dda951; outline-offset: 3px; }

/* Masthead: mark, title, the two views and the search box, always there */
.masthead { max-width: 1240px; margin: 0 auto; padding: 22px 30px; display: flex; flex-wrap: wrap; align-items: center;
  gap: 20px; border-bottom: 1px solid #a5804b44; }
.brand-mark { width: 40px; height: auto; flex: 0 0 auto; filter: invert(1) sepia(1) saturate(.65) brightness(.9);
  mix-blend-mode: screen; }
.masthead-title { color: var(--frame-ink); font: 21px Georgia, serif; }
.views { display: flex; gap: 6px; }
.views button, .chips button, .filters button, .searchbox button, .searchbox input, .filters input, .dates input,
#more, .notice button { font: inherit; font-size: 12px; color: var(--frame-ink); background: var(--control);
  border: 1px solid var(--control-ring); border-radius: 3px; padding: 7px 12px; cursor: pointer; }
.views button[aria-pressed="true"], .chips button[aria-pressed="true"], .filters button[aria-pressed="true"] {
  color: #302114; background: var(--gold); border-color: var(--gold); }
.chips button[aria-pressed="true"]::before, .filters button[aria-pressed="true"]::before { content: "✓ "; }
.searchbox { margin-left: auto; display: flex; gap: 6px; flex: 1 1 320px; max-width: 460px; }
.searchbox input { flex: 1; min-width: 0; cursor: text; }
.searchbox button { color: #302114; background: var(--gold); border-color: var(--gold); font-weight: 600; }
input[type="date"] { color-scheme: dark; }
.notice { max-width: 1180px; margin: 16px auto 0; padding: 12px 16px; color: var(--ink); background: var(--surface-1);
  border: 1px solid var(--ring); border-radius: 3px; }
.notice button { margin-left: 10px; }
.panel { margin-top: 24px; padding: 24px; color: var(--ink); background: var(--surface-1); border: 1px solid var(--ring);
  border-radius: 3px; }
.panel h2 { font: 24px Georgia, serif; margin: 0 0 8px; }

/* Atlas view: v0.3.0's layout in Ink & Light */
.sub { color: var(--frame-muted); margin: 24px 0 22px; font-size: 13px; }
.filters { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-bottom: 25px; }
.filters .custom { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; color: var(--frame-muted);
  margin-left: 8px; font-size: 12px; }
.filters input { min-width: 0; max-width: 100%; }
.tiles { display: grid; grid-template-columns: 1.4fr repeat(3, 1fr); gap: 14px; margin-bottom: 24px; }
.tile, .card { background: radial-gradient(ellipse at top right, #b7914a0a, transparent 65%),
  repeating-linear-gradient(0deg, transparent, transparent 3px, #75501903 4px), var(--surface-1);
  border: 1px solid var(--ring); border-radius: 3px; box-shadow: 0 12px 32px #0003; padding: 24px; }
.tile .label { color: var(--ink-2); font-size: 11px; }
.tile .value { font: 35px/1.25 Georgia, serif; margin-top: 8px; overflow-wrap: anywhere; }
.tile.hero { grid-row: span 2; display: flex; flex-direction: column; justify-content: center; }
.tile.hero .value { font-size: 56px; line-height: 1.1; }
.tile .note { color: var(--muted); font-size: 11px; margin-top: 5px; }
.grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }
.card { margin-bottom: 24px; min-width: 0; }
.card h2 { font: 26px/1.3 Georgia, serif; margin: 0 0 8px; overflow-wrap: anywhere; }
.card .desc { color: var(--ink-2); font-size: 12px; margin: 0 0 20px; max-width: 850px; }
.head { display: flex; justify-content: space-between; gap: 16px; align-items: flex-start; }
.head button { font: inherit; font-size: 11px; color: var(--ink-2); background: none; border: 1px solid var(--ring);
  border-radius: 3px; padding: 5px 9px; cursor: pointer; white-space: nowrap; }
.chart { width: 100%; }
#calendar { height: 200px; }
#rhythm, #graph { height: 360px; }
#themes { height: 460px; }
#apps, #sites, #screen_apps, #web_sites { height: 380px; }
.card h3.group { font-size: 11px; font-weight: 600; letter-spacing: 1px; color: var(--ink-2); margin: 16px 0 0; }
.multiples { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 20px; }
.multiples .mini h3 { font-size: 12px; font-weight: 600; margin: 0; }
.multiples .mini .total { color: var(--ink-2); font-size: 11px; }
.tablewrap { max-height: 320px; overflow: auto; margin-top: 15px; }
table { border-collapse: collapse; width: 100%; font-size: 12px; }
th, td { text-align: left; padding: 8px; border-bottom: 1px solid var(--grid); }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
.empty { color: var(--frame-muted); padding: 24px 0; text-align: center; }
.card .empty { color: var(--muted); }
.tt-muted { color: var(--ink-2); }
.tt-story { margin-top: 6px; line-height: 1.45; }
footer { color: var(--frame-muted); font-size: 11px; margin-top: 28px; padding-top: 20px; border-top: 1px solid #a5804b44; }

/* Search view (spec §4.2) */
.search-filters { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; margin: 24px 0 6px; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; }
.dates { color: var(--frame-muted); font-size: 12px; display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.freshness, .search-status { color: var(--frame-muted); font-size: 12px; margin: 6px 0 12px; }
.search-layout { display: grid; grid-template-columns: 2fr 3fr; gap: 20px; align-items: start; }
.results { list-style: none; margin: 0; padding: 0; max-height: 75vh; overflow: auto; background: var(--surface-1);
  border: 1px solid var(--ring); border-radius: 3px; }
.results:empty { display: none; }
.hit { display: block; width: 100%; text-align: left; font: inherit; color: var(--ink); background: none; border: 0;
  border-bottom: 1px solid var(--grid); padding: 10px 12px; cursor: pointer; }
.hit[aria-current="true"] { background: #d7bd8b66; box-shadow: inset 3px 0 0 var(--series-1); }
.hit .src { font-size: 10px; letter-spacing: 1px; text-transform: uppercase; color: var(--muted); }
.hit .t { font: 15px Georgia, serif; overflow-wrap: anywhere; }
.hit .ex { color: var(--ink-2); font-size: 12px; overflow-wrap: anywhere; }
mark { background: var(--hit); color: inherit; padding: 0 1px; }
.by-meaning { color: var(--muted); font-style: italic; }
#more { margin-top: 10px; }
.reader { min-height: 200px; max-height: 75vh; overflow: auto; padding: 18px 20px; color: var(--ink);
  background: var(--surface-1); border: 1px solid var(--ring); border-radius: 3px; box-shadow: 0 12px 32px #0003; }
.reader:empty { display: none; }
.reader h2 { font: 22px/1.3 Georgia, serif; margin: 0 0 4px; overflow-wrap: anywhere; }
.reader .meta, .reader .note { color: var(--muted); font-size: 12px; margin: 0 0 10px; }
.reader a { color: var(--series-1); overflow-wrap: anywhere; }
.reader ul { padding-left: 18px; font-size: 12px; }
.msg { padding: 8px 10px; margin: 8px 0; border-radius: 3px; background: var(--surface-2); }
.msg.role-user { background: var(--empty); }
.msg.matched { outline: 2px solid var(--series-1); }
.msg .who { font-size: 10px; letter-spacing: 1px; text-transform: uppercase; color: var(--muted); }
.msg .text { margin: 0; font: inherit; white-space: pre-wrap; overflow-wrap: anywhere; }
.msg pre.text { font: 12px/1.5 ui-monospace, Consolas, monospace; }
.pager, .expand { display: block; width: 100%; margin: 8px 0; padding: 5px; font: inherit; font-size: 12px;
  color: var(--ink-2); background: none; border: 1px dashed var(--ring); border-radius: 3px; cursor: pointer; }

@media (max-width: 820px) {
  main, .masthead { padding-left: 16px; padding-right: 16px; }
  .grid2, .search-layout { grid-template-columns: 1fr; }
  .tiles { grid-template-columns: 1fr 1fr; }
  .tile.hero { grid-column: 1 / -1; grid-row: auto; }
  .filters .custom { margin-left: 0; }
  .searchbox { margin-left: 0; max-width: none; }
  .results, .reader { max-height: none; }
  .card { padding: 20px; }
  .card h2 { font-size: 23px; }
  .head { flex-wrap: wrap; }
}
@media (prefers-reduced-motion: reduce) { *, *::before, *::after { animation: none !important; transition: none !important; } }
```

- [ ] **Step 6: Port the chart script into `atlas.js`**

Start from v0.3.0's script: `git show v0.3.0:src/inkvault/atlas.html` lines 159-516 (between `<script>` and `</script>`). Save that text as `src/inkvault/atlas/atlas.js`, then make exactly these edits:

1. Replace the first five lines (`const DATA = /*DATA*/null;` through `const FIRST = …, LAST = …;`) with:

```js
"use strict";
// The Atlas view (spec §4.1): v0.3.0's charts, computing exactly what they did. The data now comes from the helper
// (search.js calls Atlas.show) instead of being baked into the page, and Atlas.clear() empties everything when
// something is forgotten (spec §4.4).
let DATA = null;
const SOURCES = [["captures", "Screen captures"], ["sessions", "Session summaries"], ["chats", "Chat messages"], ["pages", "Pages opened"]];
const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
let FIRST = null, LAST = null;
```

2. Replace `let range = {from: FIRST, to: LAST};` with `let range = {from: null, to: null};`
3. Replace every `<span style="color:${t.ink2}">` with `<span class="tt-muted">` (7 places).
4. Replace `` `<div style="margin-top:6px;line-height:1.45">${esc(digest)}</div>` `` with `` `<div class="tt-story">${esc(digest)}</div>` ``.
5. Replace `` `<div style="margin-top:6px;color:${t.ink2}">${hl.map(esc).join("<br>")}</div>` `` with `` `<div class="tt-story tt-muted">${hl.map(esc).join("<br>")}</div>` ``.
6. In `render()`, replace the line `renderCalendar(t); renderRhythm(t); renderGraph(t); renderThemes(t); renderTopics(t); renderSources(t);` and the two `renderBars(…)` lines after it with:

```js
  if (!window.echarts) {  // the bundled library failed to load: say so where the charts would be; search still works
    document.querySelectorAll(".chart").forEach(el => { el.textContent = "Charts couldn't load. Search still works."; });
  } else {
    renderCalendar(t); renderRhythm(t); renderGraph(t); renderThemes(t); renderTopics(t); renderSources(t);
    renderBars("apps", DATA.apps, t, "App"); renderBars("sites", DATA.sites, t, "Site"); renderBars("web_sites", DATA.web_sites || {}, t, "Site");
    renderBars("screen_apps", DATA.screen_apps || {}, t, "App");
  }
```

7. Replace everything from `document.querySelectorAll("[data-range]").forEach(b => b.addEventListener(` to the end of the file with:

```js
let bound = false;
function bind() {
  if (bound) return;
  bound = true;
  document.querySelectorAll("[data-range]").forEach(b => b.addEventListener("click", () => setRange(...presets[b.dataset.range](), b.dataset.range)));
  for (const id of ["from", "to"]) document.getElementById(id).addEventListener("change", () => {
    const f = document.getElementById("from").value, t = document.getElementById("to").value;
    if (f && t && f <= t) setRange(f, t, null);
  });
  document.querySelectorAll(".card .tbl").forEach(btn => btn.addEventListener("click", () => {
    const card = btn.closest(".card");
    let wrap = card.querySelector(".tablewrap");
    if (wrap) { wrap.remove(); btn.textContent = "Show table"; return; }
    wrap = document.createElement("div"); wrap.className = "tablewrap"; card.append(wrap);
    btn.textContent = "Hide table"; if (DATA) renderTableFor(card);
  }));
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { if (DATA) render(); });
}

function show(data) {
  clear();
  DATA = data;
  const days = Object.keys(DATA.days);
  FIRST = days[0]; LAST = days[days.length - 1];
  for (const id of ["from", "to"]) Object.assign(document.getElementById(id), {min: FIRST, max: LAST});
  bind();
  let saved = null;
  try { saved = JSON.parse(localStorage.getItem("inkvault-range")); } catch (e) {}
  if (saved && saved.preset && presets[saved.preset]) setRange(...presets[saved.preset](), saved.preset);
  else setRange(FIRST, LAST, "all");
}

// Forgetting (spec §4.4): nothing drawn from the old data may stay on screen, including an open tooltip.
function clear() {
  for (const [id, c] of Object.entries(charts)) { c.dispose(); delete charts[id]; }
  resizer.disconnect();
  for (const k of Object.keys(tables)) delete tables[k];
  DATA = null; FIRST = null; LAST = null;
  for (const id of ["tiles", "sources", "foot", "sub"]) document.getElementById(id).replaceChildren();
  document.querySelectorAll(".chart").forEach(el => el.replaceChildren());
  document.querySelectorAll(".tablewrap").forEach(w => w.remove());
  document.querySelectorAll(".card .tbl").forEach(b => { b.textContent = "Show table"; });
}

window.Atlas = {show, clear};
```

8. Delete the fixed `style="height:…"` values that were in v0.3.0's markup (they are in `atlas.css` now). Leave the run-time `el.style.height = …` and `c.style.height = "120px"` lines in the script as they are: setting `element.style` is allowed by the policy.

Write `src/inkvault/atlas/search.js` containing just `"use strict";` for now.

- [ ] **Step 7: Run the tests**

Run: `uv run pytest tests/test_atlas_page.py -q`
Expected: all pass.

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add tools/make_mark.py src/inkvault/atlas tests/test_atlas_page.py
git commit -m "Atlas search: Ink & Light page with bundled ECharts 5.5.0 and the InkVault mark; charts as in 0.3.0"
```

---

### Task 9: The page, search view and reader

**Files:**
- Replace: `src/inkvault/atlas/search.js`
- Modify: `tests/test_atlas_page.py` (append)

**Interfaces:**
- Consumes: `window.Atlas.show/clear` (Task 8); the API shapes from Task 6; element ids from Task 8's `index.html`.
- Produces: the finished page. No later task depends on its internals.

- [ ] **Step 1: Write the failing static checks**

Append to `tests/test_atlas_page.py`:

```python
def test_search_uses_every_api_with_the_header_and_never_puts_queries_in_urls():
    js = text("search.js")
    for path in ('"/api/atlas"', '"/api/search"', '"/api/record"', '"/api/ping"'):
        assert path in js
    assert '"X-InkVault": "1"' in js and "sessionStorage" in js
    assert "location.hash" not in js and "history.pushState" not in js and "URLSearchParams" not in js


def test_every_id_the_scripts_use_is_in_the_page():
    html = text("index.html")
    for name in ("search.js", "atlas.js"):
        for id_ in re.findall(r'\$\("([\w-]+)"\)|getElementById\("([\w-]+)"\)', text(name)):
            id_ = id_[0] or id_[1]
            assert f'id="{id_}"' in html, (name, id_)
```

Run: `uv run pytest tests/test_atlas_page.py -q`
Expected: the first new test FAILS (search.js is a stub).

- [ ] **Step 2: Write `search.js`**

```js
"use strict";
// Search, the reader and every request to the helper (spec §4.2-§4.4). Memory text reaches the page only through
// textContent and text nodes (spec §5 rule 8): nothing here builds HTML.
(() => {
const $ = id => document.getElementById(id);
const STATE = "inkvault-search";
const TEXT = {
  rebuilding: "Search is being rebuilt after something was removed. This page fills in again when it's ready.",
  updated: "Your memory was just updated, so this page was cleared.",
  changed: "Search was just updated: search again.",
  unavailable: "Can't read the vault right now.",
  busy: "The Atlas is being updated. Trying again shortly…",
  noDays: "Nothing to chart yet. Search still works.",
};
let generation = null;                        // the vault state everything on screen came from
const turn = {atlas: 0, search: 0, reader: 0}; // answers to older turns are ignored (spec §4.4)
let pollTimer = null, pingTimer = null;
let state = blank();
let hits = [];

function blank() { return {view: "atlas", query: "", source: "all", from: "", to: "", selected: null, scroll: 0}; }
function next(kind) { return ++turn[kind]; }
function el(tag, text, cls) { const e = document.createElement(tag); if (text !== undefined) e.textContent = text; if (cls) e.className = cls; return e; }

async function api(path, body) {
  const options = {method: body === undefined ? "GET" : "POST", credentials: "same-origin", cache: "no-store",
                   headers: {"X-InkVault": "1"}};
  if (body !== undefined) { options.headers["Content-Type"] = "application/json"; options.body = JSON.stringify(body); }
  let res;
  try { res = await fetch(path, options); } catch (e) { return {status: "closed"}; }
  if (res.status === 403) return {status: "closed"};
  try { return await res.json(); } catch (e) { return {status: "unavailable"}; }
}

// Every answer names the vault state it came from; a different one than what's on screen means something was
// removed or rebuilt, and everything shown is cleared before anything new appears (spec §4.4).
function current(answer) {
  if (!answer.generation) return true;
  if (generation && answer.generation !== generation) { clearEverything("updated"); return false; }
  generation = answer.generation;
  return true;
}

function save() {
  try { sessionStorage.setItem(STATE, JSON.stringify({...state, generation, scroll: $("results").scrollTop})); } catch (e) {}
}

function clearEverything(reason) {
  for (const k of Object.keys(turn)) turn[k]++;
  generation = null;
  hits = [];
  state = {...blank(), view: state.view};
  try { sessionStorage.removeItem(STATE); } catch (e) {}
  $("q").value = ""; $("s-from").value = ""; $("s-to").value = "";
  $("results").replaceChildren(); $("reader").replaceChildren();
  $("freshness").textContent = ""; $("search-status").textContent = ""; $("more").hidden = true;
  setChip("all", false);
  Atlas.clear();
  notice(TEXT[reason] || TEXT.updated);
  loadAtlas();
}

function notice(text, retry) {
  $("notice").hidden = !text;
  $("notice-text").textContent = text || "";
  $("notice-retry").hidden = !retry;
  $("notice-retry").onclick = retry || null;
}

function showOnly(id) {
  for (const s of ["welcome", "closed", "view-atlas", "view-search"]) $(s).hidden = s !== id;
}

function view(name) {
  state.view = name;
  $("tab-atlas").setAttribute("aria-pressed", String(name === "atlas"));
  $("tab-search").setAttribute("aria-pressed", String(name === "search"));
  showOnly(name === "atlas" ? "view-atlas" : "view-search");
  save();
}

function closed() {
  for (const k of Object.keys(turn)) turn[k]++;
  clearTimeout(pollTimer); clearInterval(pingTimer);
  notice(null);
  showOnly("closed");
}

async function loadAtlas() {
  clearTimeout(pollTimer);
  const my = next("atlas");
  const slow = setTimeout(() => notice("Building the Atlas…"), 800);  // the helper builds missing data in this call
  const a = await api("/api/atlas");
  clearTimeout(slow);
  if (my !== turn.atlas) return;
  if (a.status === "closed") return closed();
  if (a.status === "empty") { notice(null); return showOnly("welcome"); }
  if (a.status === "rebuilding" || a.status === "busy" || a.status === "changed") {
    notice(TEXT[a.status] || TEXT.changed);
    pollTimer = setTimeout(loadAtlas, 10000);
    return;
  }
  if (a.status !== "ok" && a.status !== "no-days") return notice(TEXT.unavailable, loadAtlas);
  if (!current(a)) return;
  notice(null);
  showFreshness(a.freshness);
  showChips(a.sources || {});
  $("atlas-empty").hidden = a.status === "ok";
  $("atlas-empty").textContent = a.status === "ok" ? "" : TEXT.noDays;
  if (a.status === "ok") Atlas.show(a.data);
  view(state.view);
  restore();
}

function showFreshness(f) {
  const built = f && f.built ? new Date(f.built) : null;
  const parts = [built ? `Search index updated ${built.toLocaleString(undefined, {dateStyle: "medium", timeStyle: "short"})}`
                       : "Search index: build time not recorded"];
  if (f && f.newer_screen) parts.push("Newer screen activity appears after the next search update");
  $("freshness").textContent = parts.join(". ") + ".";
  $("tz").textContent = f && f.timezone_now ? `(${f.timezone_now})` : "";
}

function showChips(sources) {
  document.querySelectorAll("[data-source]").forEach(b => { b.hidden = b.dataset.source !== "all" && !sources[b.dataset.source]; });
}

function setChip(name, search = true) {
  state.source = name;
  document.querySelectorAll("[data-source]").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.source === name)));
  if (search && state.query) runSearch(0);
}

async function runSearch(offset) {
  const q = offset ? state.query : $("q").value.trim();
  if (!q) return;
  if (!offset) {
    state.query = q; state.selected = null; hits = [];
    $("results").replaceChildren(); $("reader").replaceChildren(); $("more").hidden = true;
  }
  view("search");
  const my = next("search");
  $("search-status").textContent = "Searching…";
  const a = await api("/api/search", {query: state.query, source: state.source, from: state.from || null,
                                      to: state.to || null, offset, generation: offset ? generation : null});
  if (my !== turn.search) return;
  if (a.status === "closed") return closed();
  if (a.status === "changed") return clearEverything("changed");
  if (a.status === "rebuilding") return clearEverything("rebuilding");
  if (a.status === "empty") { notice(null); return showOnly("welcome"); }
  if (a.status === "bad") { $("search-status").textContent = a.message; return; }
  if (a.status !== "ok") { $("search-status").textContent = TEXT.unavailable; return notice(TEXT.unavailable, () => runSearch(offset)); }
  if (!current(a)) return;
  const start = hits.length;
  hits.push(...a.hits);
  a.hits.forEach((h, i) => $("results").append(hitNode(h, start + i)));
  $("more").hidden = !a.more;
  const count = hits.length ? `${hits.length}${a.more ? "+" : ""} result${hits.length === 1 ? "" : "s"}.`
                            : "No matches. Try All sources or a wider date range.";
  $("search-status").textContent = count + (a.keyword_only ? " Keyword search only." : "");
  save();
  return a;
}

function hitNode(h, index) {
  const li = document.createElement("li");
  const b = el("button", undefined, "hit");
  b.type = "button";
  b.dataset.index = String(index);
  b.append(el("div", [h.source_label, h.time].filter(Boolean).join(" · "), "src"), el("div", h.title || "(untitled)", "t"));
  const ex = el("div", undefined, "ex");
  excerpt(ex, h);
  b.append(ex);
  b.addEventListener("click", () => openHit(index));
  li.append(b);
  return li;
}

function excerpt(box, h) {
  if (h.match.type !== "keyword") {
    box.append(h.excerpt, el("span", " · related by meaning", "by-meaning"));
    return;
  }
  let at = 0;
  for (const [a, b] of h.match.spans) {
    box.append(h.excerpt.slice(at, a), el("mark", h.excerpt.slice(a, b)));
    at = b;
  }
  box.append(h.excerpt.slice(at));
}

async function openHit(index) {
  const h = hits[index];
  if (!h) return;
  document.querySelectorAll(".hit").forEach(b => b.setAttribute("aria-current", String(b.dataset.index === String(index))));
  state.selected = {kind: h.kind, id: h.id};
  save();
  await openRecord(h.kind, h.id);
}

async function openRecord(kind, id) {
  const my = next("reader");
  $("reader").replaceChildren(el("p", "Opening…", "meta"));
  const r = await api("/api/record", {kind, id});
  if (my !== turn.reader) return;
  if (!readerAnswer(r)) return;
  renderRecord(r);
}

// Shared by every reader request: true when r is a record to show.
function readerAnswer(r) {
  if (r.status === "closed") { closed(); return false; }
  if (r.status === "changed") { clearEverything("changed"); return false; }
  if (r.status === "rebuilding") { clearEverything("rebuilding"); return false; }
  if (r.status === "missing") { $("reader").replaceChildren(el("p", "This memory was removed.", "note")); return false; }
  if (r.status !== "ok") { $("reader").replaceChildren(el("p", TEXT.unavailable, "note")); return false; }
  return true;
}

const ROLES = {USER: "You", ASSISTANT: "Assistant"};

function renderRecord(r) {
  const box = $("reader");
  box.replaceChildren(el("h2", r.title || "(untitled)"), el("p", [r.source_label, r.meta].filter(Boolean).join(" · "), "meta"));
  if (r.note) box.append(el("p", r.note, "note"));
  if (r.url) box.append(link(r.url));
  if (r.visits) {
    const ul = document.createElement("ul");
    for (const v of r.visits) ul.append(el("li", `${v.time} · ${v.profile}${v.synced ? " (synced from another device)" : ""}`));
    box.append(ul);
  }
  const list = el("div");
  if (r.earlier) list.append(pager("Earlier messages", r.earlier, "earlier", list));
  for (const it of r.items) list.append(itemNode(it, r.code));
  if (r.later) list.append(pager("Later messages", r.later, "later", list));
  box.append(list);
  const m = list.querySelector(".matched");
  if (m) m.scrollIntoView({block: "center"});
}

function link(url) {
  if (!/^https?:\/\//i.test(url)) return el("p", url, "meta");
  const p = el("p", undefined, "meta");
  const a = el("a", url);
  a.href = url; a.target = "_blank"; a.rel = "noopener noreferrer";
  p.append(a);
  return p;
}

function itemNode(it, code) {
  const role = (it.role || "").toLowerCase().replace(/[^a-z]/g, "");
  const div = el("div", undefined, "msg" + (role ? " role-" + role : "") + (it.matched ? " matched" : ""));
  const who = [ROLES[it.role] || it.role, it.time, it.matched ? "matched" : ""].filter(Boolean).join(" · ");
  if (who) div.append(el("div", who, "who"));
  const body = el(code ? "pre" : "div", it.text, "text");
  div.append(body);
  if (it.truncated && it.expand) div.append(expander(body, it.expand));
  return div;
}

function expander(body, cursor) {
  const b = el("button", "Show more of this", "expand");
  b.type = "button";
  b.addEventListener("click", async () => {
    const my = turn.reader;
    b.disabled = true;
    const a = await api("/api/record", {cursor: b.dataset.cursor || cursor});
    if (my !== turn.reader || !readerAnswer(a)) return;
    body.append(a.text);
    if (a.next) { b.dataset.cursor = a.next; b.disabled = false; } else b.remove();
  });
  return b;
}

function pager(label, cursor, dir, list) {
  const b = el("button", label, "pager");
  b.type = "button";
  b.addEventListener("click", async () => {
    const my = turn.reader;
    b.disabled = true;
    const a = await api("/api/record", {cursor});
    if (my !== turn.reader || !readerAnswer(a)) return;
    const nodes = a.items.map(it => itemNode(it, a.code));
    const more = dir === "earlier" ? a.earlier : a.later;
    if (more) nodes[dir === "earlier" ? "unshift" : "push"](pager(label, more, dir, list));
    b.replaceWith(...nodes);
  });
  return b;
}

async function restore() {
  let saved = null;
  try { saved = JSON.parse(sessionStorage.getItem(STATE)); } catch (e) {}
  if (!saved || saved.generation !== generation || !saved.query) return;
  Object.assign(state, {query: saved.query, source: saved.source, from: saved.from, to: saved.to, view: saved.view});
  $("q").value = saved.query; $("s-from").value = saved.from || ""; $("s-to").value = saved.to || "";
  setChip(saved.source || "all", false);
  const a = await runSearch(0);
  if (!a) return;
  $("results").scrollTop = saved.scroll || 0;
  if (saved.selected) {
    const i = hits.findIndex(h => h.kind === saved.selected.kind && h.id === saved.selected.id);
    if (i >= 0) openHit(i); else openRecord(saved.selected.kind, saved.selected.id);
  }
  view(saved.view || "search");
}

async function ping() {
  if (document.visibilityState !== "visible") return;
  const a = await api("/api/ping", {});
  if (a.status === "closed") return closed();
  if (a.rebuilding && generation) return clearEverything("rebuilding");
  if (a.generation && generation && a.generation !== generation) clearEverything("updated");
}

$("search-form").addEventListener("submit", e => { e.preventDefault(); runSearch(0); });
$("tab-atlas").addEventListener("click", () => view("atlas"));
$("tab-search").addEventListener("click", () => view("search"));
$("more").addEventListener("click", () => runSearch(hits.length));
document.querySelectorAll("[data-source]").forEach(b => b.addEventListener("click", () => setChip(b.dataset.source)));
for (const id of ["s-from", "s-to"]) $(id).addEventListener("change", () => {
  state.from = $("s-from").value; state.to = $("s-to").value;
  if (state.query) runSearch(0);
});
$("results").addEventListener("keydown", e => {
  if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
  const all = [...document.querySelectorAll(".hit")];
  const i = all.indexOf(document.activeElement);
  const j = e.key === "ArrowDown" ? Math.min(all.length - 1, i + 1) : Math.max(0, i - 1);
  if (all[j]) { all[j].focus(); e.preventDefault(); }
});
$("results").addEventListener("scroll", () => { state.scroll = $("results").scrollTop; save(); });
document.addEventListener("visibilitychange", ping);
window.addEventListener("focus", ping);
pingTimer = setInterval(ping, 60000);
loadAtlas();
})();
```

- [ ] **Step 3: Run the tests**

Run: `uv run pytest tests/test_atlas_page.py -q`
Expected: all pass.

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add src/inkvault/atlas/search.js tests/test_atlas_page.py
git commit -m "Atlas search: search view, reader with paging and expansion, full clear on any vault change"
```

---

### Task 10: CLI: `inkvault dashboard`, the Windows icon, rescue and status

**Files:**
- Modify: `src/inkvault/atlasserve.py` (append `launch`, `start_detached`, `make_shortcut`)
- Modify: `src/inkvault/cli.py` (`open_dashboard`, `rescue`, `partial_rescue`, `cmd_status`, the `dashboard` parser and dispatch, the module docstring's command list)
- Modify: `tests/test_pipeline.py` (`test_interrupted_rescue_still_builds_a_dashboard`)
- Create: `tests/test_atlas_cli.py`

**Interfaces:**
- Consumes: `serve`, `build_atlas` (Tasks 6-7); `paths`.
- Produces: `atlasserve.launch(open_browser=True) -> int`, `atlasserve.start_detached()`, `atlasserve.make_shortcut() -> int`, `SHORTCUT = "InkVault Atlas"`, `ICON`; CLI `inkvault dashboard [--no-open] [--shortcut]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_atlas_cli.py`:

```python
"""`inkvault dashboard`, the Windows icon, rescue and status (spec §3.6)."""
import json
import subprocess
import sys

import pytest

import atlasdata


def test_dashboard_command_launches_the_helper(tmp_path, monkeypatch):
    atlasdata.make_vault(tmp_path, monkeypatch)
    from inkvault import atlasserve, cli, paths
    calls = []
    monkeypatch.setattr(atlasserve, "serve", lambda open_browser=True, **_: calls.append(open_browser) or 0)
    assert cli.main(["dashboard"]) == 0 and calls == [True]
    assert paths.dashboard().exists()  # built before serving when missing
    assert cli.main(["dashboard", "--no-open"]) == 0 and calls == [True, False]


def fake_powershell(monkeypatch, readback):
    from inkvault import atlasserve
    seen = {}

    def run(cmd, **kw):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(readback(cmd)), stderr="")
    monkeypatch.setattr(atlasserve.subprocess, "run", run)
    monkeypatch.setattr(atlasserve.sys, "platform", "win32")
    return seen


def test_shortcut_script_quotes_the_home_path(tmp_path, monkeypatch, capsys):
    home = tmp_path / "O'Brien vault"
    home.mkdir()
    monkeypatch.setenv("INKVAULT_HOME", str(home))
    from inkvault import atlasserve, paths
    exe = atlasserve.pythonw()
    args = f'-m inkvault --home "{paths.home()}" dashboard'
    seen = fake_powershell(monkeypatch, lambda cmd: [{"path": "Desktop\\InkVault Atlas.lnk", "target": str(exe), "arguments": args},
                                                    {"path": "Programs\\InkVault Atlas.lnk", "target": str(exe), "arguments": args}])
    assert atlasserve.make_shortcut() == 0
    script = seen["cmd"][-1]
    assert "O''Brien vault" in script and "GetFolderPath('Programs')" in script and "inkvault.ico" in script
    assert "InkVault Atlas" in capsys.readouterr().out


def test_a_shortcut_that_reads_back_wrong_is_reported(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("INKVAULT_HOME", str(tmp_path))
    from inkvault import atlasserve
    fake_powershell(monkeypatch, lambda cmd: [{"path": "x.lnk", "target": "C:\\other.exe", "arguments": ""}])
    assert atlasserve.make_shortcut() == 1
    assert "didn't read back" in capsys.readouterr().out


def test_shortcut_elsewhere_says_windows_only(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("INKVAULT_HOME", str(tmp_path))
    from inkvault import atlasserve
    monkeypatch.setattr(atlasserve.sys, "platform", "linux")
    assert atlasserve.make_shortcut() == 0
    assert "Windows-only" in capsys.readouterr().out


def test_rescue_opens_the_live_atlas_in_the_background(tmp_path, monkeypatch):
    atlasdata.make_vault(tmp_path, monkeypatch)
    from inkvault import atlasserve, cli, export
    started = []
    monkeypatch.setattr(atlasserve, "start_detached", lambda: started.append(True))

    def interrupted():
        raise KeyboardInterrupt
    monkeypatch.setattr(export, "run", interrupted)
    assert cli.main(["rescue"]) == 130 and started == [True]
    started.clear()
    assert cli.main(["rescue", "--no-open"]) == 130 and started == []


def test_status_reports_the_atlas(tmp_path, monkeypatch, capsys):
    atlasdata.make_vault(tmp_path, monkeypatch)
    monkeypatch.setenv("INKVAULT_PIECES_PORTS", "1")
    from inkvault import cli, dashboard
    dashboard.build()
    capsys.readouterr()
    cli.main(["status"])
    out = capsys.readouterr().out
    assert "atlas: built" in out and "inkvault dashboard" in out
```

Run: `uv run pytest tests/test_atlas_cli.py -q`
Expected: FAIL (`launch`, `make_shortcut`, `pythonw` missing; `dashboard` still builds and prints).

- [ ] **Step 2: Append to `atlasserve.py`**

```python
import subprocess  # add to the imports at the top
import sys         # add to the imports at the top

SHORTCUT = "InkVault Atlas"
ICON = ASSET_DIR / "inkvault.ico"


def launch(open_browser=True):
    """`inkvault dashboard` (spec §3.6): build the Atlas data if it's missing, then start or reuse the helper."""
    if paths.search_db().exists() and not paths.dashboard().exists() and not paths.rebuild_marker().exists():
        print("Building the Atlas…", flush=True)
        if build_atlas() == "busy":
            print("A nightly run is in progress; the Atlas fills in when it's done.")
    return serve(open_browser=open_browser)


def pythonw():
    """This environment's windowless Python, so the icon opens no console window."""
    exe = Path(sys.executable)
    w = exe.with_name("pythonw.exe")
    return w if w.exists() else exe


def start_detached():
    """Open the Atlas without holding the terminal (after `rescue`): a separate helper process."""
    argv = [str(pythonw() if sys.platform == "win32" else Path(sys.executable)), "-m", "inkvault",
            "--home", str(paths.home()), "dashboard"]
    kw = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "close_fds": True}
    if sys.platform == "win32":
        kw["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    subprocess.Popen(argv, **kw)


def make_shortcut():
    """"InkVault Atlas" on the desktop and in the Start menu (spec §3.6), checked by reading each one back."""
    if sys.platform != "win32":
        print("The InkVault Atlas icon is Windows-only for now. Open the Atlas with `inkvault dashboard`.")
        return 0
    q = lambda s: str(s).replace("'", "''")
    exe = pythonw()
    args = f'-m inkvault --home "{paths.home()}" dashboard'
    script = (
        "$ErrorActionPreference='Stop'; $w=New-Object -ComObject WScript.Shell; $out=@(); "
        "foreach ($dir in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) { "
        f"$p=Join-Path $dir '{SHORTCUT}.lnk'; $s=$w.CreateShortcut($p); $s.TargetPath='{q(exe)}'; "
        f"$s.Arguments='{q(args)}'; $s.IconLocation='{q(ICON)},0'; $s.WorkingDirectory='{q(paths.home())}'; "
        "$s.Description='Open the InkVault Memory Atlas'; $s.Save(); $r=$w.CreateShortcut($p); "
        "$out+=@{path=$p; target=$r.TargetPath; arguments=$r.Arguments} }; ConvertTo-Json -Compress @($out)")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                           capture_output=True, text=True, encoding="utf-8")
        made = json.loads(r.stdout) if r.returncode == 0 else []
    except (OSError, ValueError):
        made = []
    made = made if isinstance(made, list) else [made]
    good = [m for m in made if isinstance(m, dict) and str(m.get("target", "")).lower() == str(exe).lower()
            and m.get("arguments") == args]
    if len(good) != 2:
        print("The InkVault Atlas icon couldn't be made, or didn't read back as expected. "
              "Open the Atlas with `inkvault dashboard`.")
        return 1
    print(f'Added "{SHORTCUT}" to your desktop and Start menu.')
    return 0
```

- [ ] **Step 3: Change `cli.py`**

1. Delete `open_dashboard`.
2. In `rescue(args)`, replace `if not args.no_open: open_dashboard(path)` with:

```python
    if path and not args.no_open:
        from . import atlasserve
        atlasserve.start_detached()  # the rescue holds the nightly lock; the helper must not run inside it
        print("Opening the Memory Atlas in your browser.")
```

3. Make the same replacement in `partial_rescue`.
4. Replace the `dashboard` parser lines with:

```python
    b = sub.add_parser("dashboard", help="open the Memory Atlas (a live page, on this computer only)")
    b.add_argument("--no-open", action="store_true", help="start it without opening the browser")
    b.add_argument("--shortcut", action="store_true", help="Windows: add an InkVault Atlas icon to the desktop and Start menu")
```

5. Replace the `dashboard` branch of `dispatch` with:

```python
    if args.cmd == "dashboard":
        from . import atlasserve
        if args.shortcut:
            return atlasserve.make_shortcut()
        return atlasserve.launch(open_browser=not args.no_open)
```

6. In `cmd_status`, replace the `dashboard: …` part of the `search index:` line with an atlas line printed after it:

```python
    print(f"search index: {'ready' if paths.search_db().exists() else 'not built'}; "
          f"digests: {'yes' if paths.digests_db().exists() else 'none'}")
    try:
        built = json.loads(paths.dashboard().read_text(encoding="utf-8")).get("built", "?")
        print(f"atlas: built {built} (open it with `inkvault dashboard`)")
    except (OSError, ValueError):
        print("atlas: not built yet (`inkvault dashboard` builds it)")
```

(add `import json` to `cli.py` if it isn't imported). 7. In the module docstring's command list, change the `dashboard` line to `inkvault dashboard   open the Memory Atlas (search and charts), on this computer only`. 8. After the MCP instructions printed at the end of a successful `rescue`, on Windows add: `print("Tip: `inkvault dashboard --shortcut` adds an InkVault Atlas icon.")` guarded by `if sys.platform == "win32":`.

- [ ] **Step 4: Keep the interrupted-rescue test from opening anything**

In `tests/test_pipeline.py::test_interrupted_rescue_still_builds_a_dashboard` it already passes `--no-open`; leave it. Search the tests for any other `cli.main(["rescue"` without `--no-open` and add `monkeypatch.setattr(atlasserve, "start_detached", lambda: None)` to each.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_atlas_cli.py -q`
Expected: all pass.

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/inkvault/atlasserve.py src/inkvault/cli.py tests/test_atlas_cli.py tests/test_pipeline.py
git commit -m "Atlas search: inkvault dashboard runs the live Atlas; Windows icon; rescue opens it in the background"
```

---

### Task 11: Version 0.3.1, README and CHANGELOG

**Files:**
- Modify: `pyproject.toml` (`version`), `src/inkvault/__init__.py`, `uv.lock`, `README.md`, `CHANGELOG.md`
- Modify: `docs/superpowers/specs/2026-10-13-atlas-search-design.md` (two wording fixes)

**Interfaces:** none.

- [ ] **Step 1: Bump the version**

Set `version = "0.3.1"` in `pyproject.toml` and `__version__ = "0.3.1"` in `src/inkvault/__init__.py`. Run `uv lock`. Run `uv run inkvault --version`; expected: `inkvault 0.3.1`.

- [ ] **Step 2: CHANGELOG**

Add at the top of `CHANGELOG.md`, matching the existing entry style:

```markdown
## 0.3.1 — Searchable Memory Atlas

- **Search in the Atlas.** Type what you remember; results come from every source in your vault (conversations,
  Pieces summaries and captures, snippets, screen capture, browser history), and a reader opens the original —
  chats open at the matching message, with earlier and later messages a click away.
- **The Atlas is live now.** `inkvault dashboard` starts a small private helper on this computer and opens the
  Atlas in your browser. It listens on 127.0.0.1 only, answers only the tab it opened, never changes your vault,
  and stops by itself 30 minutes after you close the tab. On Windows, `inkvault dashboard --shortcut` adds an
  **InkVault Atlas** icon.
- **Behaviour change:** the static `dashboard.html` is gone (the first update removes it). The Atlas's data is now
  `atlas.json` in your InkVault folder, rebuilt nightly as before.
- **New look:** Ink & Light, with the InkVault mark; charts work offline (ECharts 5.5.0 is bundled).
- MCP tools answer exactly as before.
```

- [ ] **Step 3: README**

In the README section that describes the Memory Atlas / `inkvault dashboard`, replace the description of a dashboard file with: the Atlas is opened with `inkvault dashboard` (or the Windows icon from `inkvault dashboard --shortcut`), shows charts and a **Search** view with a reader, runs on this computer only (127.0.0.1, private link, stops when idle), and needs no internet. Keep the README's tone and headings; do not add screenshots with real data.

- [ ] **Step 4: Spec wording**

In the spec: §4.2 change "Newer screen activity appears after tonight's update." to "Newer screen activity appears after the next search update."; §3.1's "One deliberate difference" paragraph: replace its last two sentences with "MCP keeps today's behaviour exactly. The Atlas runs meaning search only when both `vectors.npz` and the model folder exist (`require_model=True`); otherwise it answers keyword-only with `keyword_only: true`."

- [ ] **Step 5: Run the tests and commit**

Run: `uv run pytest -q`
Expected: all pass.

```bash
git add pyproject.toml src/inkvault/__init__.py uv.lock README.md CHANGELOG.md docs/superpowers/specs/2026-10-13-atlas-search-design.md
git commit -m "InkVault 0.3.1: searchable Memory Atlas (version, README, CHANGELOG)"
```

---

### Task 12: Browser check and speed (manual, recorded)

**Files:**
- Create: `tests/manual/atlas_demo.py`, `tests/manual/atlas_perf.py`
- Create: `docs/superpowers/plans/2026-10-13-atlas-search-verification.md`

**Interfaces:** uses `atlasdata.make_vault`-style building but in a standalone script (no pytest).

- [ ] **Step 1: Write the demo vault script**

Create `tests/manual/atlas_demo.py`:

```python
"""A throwaway Atlas on fictional, hostile data, for the browser check. Never touches a real vault.

    uv run python tests/manual/atlas_demo.py
Prints the temporary folder, then runs the helper there and opens the browser. Ctrl+C stops it; the folder is kept
for inspection and can be deleted afterwards.
"""
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

home = Path(tempfile.mkdtemp(prefix="inkvault-atlas-demo-"))
os.environ["INKVAULT_HOME"] = str(home)
from inkvault import atlasserve, dashboard, embed, export, index  # noqa: E402

embed.build = lambda: None  # keyword search only: no model download
HOSTILE = ["<script>alert('x')</script>", "<img src=x onerror=alert(1)>", "\ue000fake\ue001 highlight",
           "\"quotes\" & 'apostrophes'", "right-to-left \u202egnirts"]

db = export.open_vault()
records = [("conversation", "c1", {"id": "c1", "name": HOSTILE[0]})]
for i in range(120):
    text = HOSTILE[i % len(HOSTILE)] + f" lighthouse message {i}" + (" long" * 70_000 if i == 60 else "")
    records.append(("message", f"m{i}", {"id": f"m{i}", "created": {"value": f"2026-03-01T{10 + i // 60:02d}:{i % 60:02d}:00Z"},
                                         "role": "USER" if i % 2 == 0 else "ASSISTANT", "conversation": {"id": "c1"},
                                         "fragment": {"string": {"raw": text}}}))
for d in range(1, 29):
    records.append(("summary", f"s{d}", {"id": f"s{d}", "name": HOSTILE[d % len(HOSTILE)] + f" Harbor day {d}",
                                         "created": {"value": f"2026-02-{d:02d}T12:00:00Z"}}))
db.executemany("INSERT INTO raw_records VALUES (?,?,?)", [(k, i, json.dumps(r)) for k, i, r in records])
db.execute("INSERT INTO events VALUES ('e1','2026-02-20T10:00:00Z','chrome.exe',?, 'javascript:alert(1)', ?, '{}')",
           (HOSTILE[1], HOSTILE[0] + " lighthouse"))
db.commit()
db.close()
index.build()
dashboard.build()
print(f"Demo home: {home}")
sys.exit(atlasserve.serve(open_browser=True))
```

- [ ] **Step 2: Run the browser check**

Run `uv run python tests/manual/atlas_demo.py` and check each line in a real browser (the in-app browser is fine), recording pass/fail and notes in `docs/superpowers/plans/2026-10-13-atlas-search-verification.md`:

1. Atlas view renders every chart; tooltips show the hostile summary names as visible text; nothing runs (no alert).
2. The browser console shows **no Content-Security-Policy violations** and no errors.
3. Search `lighthouse`: results show labels, times and highlights; every hostile string is visible text; `\ue000fake\ue001` shows no highlight.
4. Open `m60`: the reader lands on it, marked "matched"; **Show more of this** loads the rest in order until the button disappears.
5. **Earlier messages** / **Later messages** extend the conversation without gaps or repeats.
6. The event with `javascript:alert(1)` shows that address as plain text, not a link.
7. Keyboard only: Tab to the search box, type, Enter, Tab into results, ArrowDown/ArrowUp, Enter opens, focus ring visible throughout.
8. At 375 px wide: the reader sits below results; no horizontal scroll.
9. Dark and light (system setting): both palettes readable.
10. Atlas → Search → Atlas → Search keeps query, chip, selection and scroll; the address bar only ever shows `/`.
11. Delete `atlas.json` and touch `vectors.npz` (or write the `rebuild-needed` marker) in the demo home while the page is open: within a minute (or on focus) the page clears charts, tooltip, results, reader and query, and shows the updating message; `sessionStorage` is empty (DevTools → Application).
12. Close the tab; after 30 minutes (or start the demo with a short idle in a Python shell: `atlasserve.serve(idle=60)`) the helper exits and a reloaded tab shows "The Atlas has closed".
13. Run `uv run inkvault --home <demo home> dashboard` while the demo runs: a new tab opens on the same helper, no second process.

- [ ] **Step 3: Write and run the speed script**

Create `tests/manual/atlas_perf.py`:

```python
"""Search and reader timings on a synthetic vault of about 100,000 records (spec §6). Fictional data only.

    uv run python tests/manual/atlas_perf.py
"""
import json
import os
import platform
import random
import statistics
import tempfile
import time
import tracemalloc
from pathlib import Path

import numpy as np

home = Path(tempfile.mkdtemp(prefix="inkvault-perf-"))
os.environ["INKVAULT_HOME"] = str(home)
from inkvault import embed, export, index, paths, retrieval  # noqa: E402


class Model:
    def encode(self, texts, **_):
        return np.random.default_rng(len(texts[0])).random((len(texts), 256), dtype=np.float32)


embed.build = lambda: None
embed.load_model = Model
words = [f"w{i}" for i in range(5000)] + ["lighthouse", "harbor", "backoff"]
rnd = random.Random(7)
db = export.open_vault()
rows = []
for c in range(2000):
    rows.append(("conversation", f"c{c}", json.dumps({"id": f"c{c}", "name": " ".join(rnd.choices(words, k=4))})))
    for m in range(40):
        rows.append(("message", f"m{c}_{m}", json.dumps({"id": f"m{c}_{m}", "created": {"value": f"2026-0{1 + c % 9}-{1 + m % 28:02d}T10:00:00Z"},
                     "role": "USER" if m % 2 == 0 else "ASSISTANT", "conversation": {"id": f"c{c}"},
                     "fragment": {"string": {"raw": " ".join(rnd.choices(words, k=60))}}})))
for s in range(20000):
    rows.append(("summary", f"s{s}", json.dumps({"id": f"s{s}", "name": " ".join(rnd.choices(words, k=5)),
                 "created": {"value": f"2026-0{1 + s % 9}-{1 + s % 28:02d}T12:00:00Z"}})))
db.executemany("INSERT INTO raw_records VALUES (?,?,?)", [(k, i, r) for k, i, r in rows])
db.commit(); db.close()
index.build()
db = paths.connect_ro(paths.search_db())
ids = [("chats", r[0]) for r in db.execute("SELECT id FROM messages")] + [("summaries", r[0]) for r in db.execute("SELECT id FROM summaries")]
db.close()
np.savez(paths.vectors(), kinds=np.array([k for k, _ in ids]), ids=np.array([i for _, i in ids]),
         vecs=np.random.default_rng(1).random((len(ids), 256), dtype=np.float32))
paths.model_dir().mkdir(exist_ok=True)

def timed(fn):
    t = time.perf_counter(); out = fn(); return time.perf_counter() - t, out

tracemalloc.start()
cold, r = timed(lambda: retrieval.search("lighthouse harbor", markers=retrieval.ATLAS_MARKERS, require_model=True, limit=25))
warm = statistics.median(timed(lambda: retrieval.search("lighthouse harbor", markers=retrieval.ATLAS_MARKERS, require_model=True, limit=25))[0] for _ in range(5))
reader, rec = timed(lambda: retrieval.record("chats", "m1000_20"))
peak = tracemalloc.get_traced_memory()[1]
print(json.dumps({"records": len(ids), "hardware": platform.processor() or platform.machine(), "python": platform.python_version(),
                  "cold_search_s": round(cold, 3), "warm_search_s": round(warm, 3), "reader_s": round(reader, 3),
                  "reader_chars": sum(len(i["text"]) for i in rec["items"]), "peak_mb": round(peak / 1e6, 1),
                  "hits": len(r.hits)}, indent=1))
print(f"(temporary folder {home}; delete it when done)")
```

Run: `uv run python tests/manual/atlas_perf.py`
Record the JSON in the verification doc with the machine description. Targets: warm search < 2 s, reader < 1 s. If a target is missed, note it and investigate before Task 13 (likely suspects: fetching every scored hit row; `chat()` loading a whole conversation).

- [ ] **Step 4: Commit**

```bash
git add tests/manual/atlas_demo.py tests/manual/atlas_perf.py docs/superpowers/plans/2026-10-13-atlas-search-verification.md
git commit -m "Atlas search: browser check and speed results"
```

---

### Task 13: Real-history check on a disposable copy (maintainer-run)

**Files:**
- Create: `tests/manual/atlas_real_copy.py`
- Modify: `docs/superpowers/plans/2026-10-13-atlas-search-verification.md` (append counts and timings only)

**Interfaces:** reads a real InkVault home **read-only**, under its nightly lock, and works on a copy.

- [ ] **Step 1: Write the script**

```python
"""Run the Atlas against a coherent, disposable copy of a real vault (spec §6). Prints counts and timings only —
never titles, text, queries' results or paths inside the vault. The live vault is never opened by the helper.

    uv run python tests/manual/atlas_real_copy.py "<real InkVault home>" "query one" "query two" ...
The queries are typed on the command line and are not printed.
"""
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
from pathlib import Path

real = Path(sys.argv[1]).resolve()
queries = sys.argv[2:]
copy = Path(tempfile.mkdtemp(prefix="inkvault-realcopy-"))
os.environ["INKVAULT_HOME"] = str(real)
from inkvault import nightly  # noqa: E402

with nightly.lock("dashboard"):  # nothing changes the real files while they're copied together
    for name in ("vault.db", "search.db"):
        src = sqlite3.connect(f"file:{real / name}?mode=ro", uri=True)
        dst = sqlite3.connect(copy / name)
        src.backup(dst)
        src.close(); dst.close()
    for name in ("vectors.npz", "atlas.json"):
        if (real / name).exists():
            shutil.copy2(real / name, copy / name)
    if (real / "model").exists():
        shutil.copytree(real / "model", copy / "model")

os.environ["INKVAULT_HOME"] = str(copy)
import importlib  # noqa: E402
from inkvault import paths  # noqa: E402
importlib.reload(paths)
from inkvault import atlasserve, retrieval  # noqa: E402

report = {"copy_made": True}
try:
    t = time.perf_counter(); payload = atlasserve.atlas_payload(); report["atlas_s"] = round(time.perf_counter() - t, 3)
    report["atlas_status"] = payload["status"]
    report["atlas_days"] = len(payload.get("data", {}).get("days", {}))
    report["chips"] = payload.get("sources")
    for n, q in enumerate(queries, 1):
        t = time.perf_counter()
        r = retrieval.search(q, markers=retrieval.ATLAS_MARKERS, require_model=True, limit=25)
        took = time.perf_counter() - t
        kinds = {}
        for h in r.hits:
            kinds[h.kind] = kinds.get(h.kind, 0) + 1
        opened = None
        if r.hits:
            t = time.perf_counter(); rec = retrieval.record(r.hits[0].kind, r.hits[0].id); opened = round(time.perf_counter() - t, 3)
        report[f"query_{n}"] = {"search_s": round(took, 3), "hits": len(r.hits), "by_kind": kinds,
                                "keyword_only": r.keyword_only, "first_open_s": opened}
finally:
    shutil.rmtree(copy, ignore_errors=True)
    report["copy_deleted"] = not copy.exists()
print(json.dumps(report, indent=1))
```

- [ ] **Step 2: Run it with the maintainer**

The maintainer (not a subagent) runs it against their own home with three queries for things they know are there. Paste only the printed JSON (counts and timings) into the verification doc under "Real history (disposable copy)". Confirm `copy_deleted: true`.

- [ ] **Step 3: Commit**

```bash
git add tests/manual/atlas_real_copy.py docs/superpowers/plans/2026-10-13-atlas-search-verification.md
git commit -m "Atlas search: real-history check on a disposable copy (counts and timings)"
```

---

## After the tasks

Whole-branch code review (Codex), the maintainer's private privacy check against `v0.3.0`, then the pull request. Nothing is pushed until the maintainer says so.
