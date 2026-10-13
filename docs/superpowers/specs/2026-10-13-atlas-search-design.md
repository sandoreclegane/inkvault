# Searchable Memory Atlas: design

Status: draft for review, 2026-10-13. Target version **0.3.1**, a step toward v0.4 (not v0.4 itself).
Branch `atlas-search` from tag `v0.3.0` (`b8dece5`).

## 1. Goal

Open the Memory Atlas, type what you remember, read the original. Someone who installed InkVault finds an old
memory in under a minute without an AI client, MCP or a terminal for day-to-day use.

This step delivers three things and nothing else:

1. **A live Atlas.** The Atlas stops being a static HTML file and becomes a page served by a small, private helper
   on this computer, opened from `inkvault dashboard` or (on Windows) an "InkVault Atlas" icon.
2. **Search and a reader** inside that page: results from every source the vault holds, and the original record,
   with chats opened at the matching message.
3. **The Ink & Light look**: a candlelit dark frame, parchment cards, brown-ink type, an amber chart palette and the
   InkVault mark, with coordinated light and dark variants, and charts that need no network.

### Not in this step

Daily quote panel; Surprise Me, discoveries and ranking for novelty; clicking a chart to open its records;
knowledge graph, time machine, session replay; editing settings, forgetting or deleting from the page; a
one-click installer or automatic updates; Mac or Linux icons (the command works everywhere); any change to
capture, sync, the vault schema or what MCP returns.

## 2. Decisions already made

| Question | Decision |
|---|---|
| One Atlas or two pages? | One live Atlas. No separate `explore` page, no static copy kept alongside. |
| Layout | Two views in one page: **Atlas** (charts) and **Search** (results left, reader right; reader below results on narrow windows). Search box always in the header. |
| Reader | Chats open at the matched message with earlier/later paging; other records show in full. |
| How it's opened | `inkvault dashboard`, plus a Windows desktop and Start-menu icon made on request. Helper stops itself when no Atlas tab has been open for 30 minutes. |
| Helper technology | Python standard library `http.server`; no new dependencies, no reliance on MCP's transitive web stack. |
| Old `dashboard.html` | Removed by the first new build (it is generated, never user data); one line says the Atlas is now live. |
| Daily quote | Not in this step. |

## 3. Architecture

```
nightly / rescue / sync / forget ──► dashboard.build() ──► atlas.json      (aggregate chart data only)
                                                             │
inkvault dashboard ─► atlasserve ──► GET  /                  │  page + bundled assets (atlas/)
  (or the icon)       127.0.0.1      GET  /api/atlas ◄────────┘
                      random port    POST /api/search ─┐
                      one-time code  POST /api/record ─┼──► retrieval.py ──► search.db, vectors.npz, vault.db (read-only)
                                     POST /api/ping    │         ▲
                                                       │         └── server.py (MCP tools, unchanged output)
```

### 3.1 `retrieval.py` (new): one search and reading engine

Everything MCP's `search_memories` and `get_memory` do today moves here and returns structures instead of text.
`server.py` keeps its tool names, signatures and docstrings and becomes a thin formatter over this module.

- `search(query, sources=None, since=None, until=None, limit=25, offset=0) -> SearchResult`
  - `sources`: a subset of the existing kinds `summaries, events, chats, snippets, screen, web`; `None` is all.
  - Same ranking as today: keyword (FTS5/bm25) and meaning (vectors) lists fused with RRF, same candidate counts,
    same screen de-duplication (a screen stretch with OCR text is not listed twice).
  - `SearchResult`: `hits`, `more` (bool), `keyword_only` (bool, true when vectors or the model are missing),
    `generation` (the `published()` stamp the answer was read under).
  - `Hit`: `kind`, `id`, `title`, `time` (UTC ISO), `source_label`, `excerpt`, `match`.
    `match` is either `{"type": "keyword", "spans": [[start, end], ...]}` (character offsets into `excerpt`, built
    from the FTS snippet markers, never from HTML) or `{"type": "meaning"}`; a meaning-only hit's excerpt is the
    record's opening text and is never highlighted.
- `record(ref, around=None, cursor=None) -> Record`
  - `ref` is the namespaced `(kind, id)` pair from a hit, so ids never need to be globally unique.
  - Chats: a window of at most 20 messages before and after `around` (the matched message), plus opaque
    `earlier` / `later` cursors that load 20 more. A cursor is bound to the conversation and the generation; a
    cursor from an older generation is refused with `changed`.
  - Summaries, events, snippets, screen text: the whole record. Web: title, URL, profile and visit times, labelled
    as browser history.
  - Every response is capped at 100,000 characters of record text. A single message or record longer than its
    share is cut with `truncated: true` and an `expand` cursor that returns that one item, still under the cap.
- Errors are values, not exceptions: `rebuilding`, `changed`, `unavailable`, `missing`. These reuse the exact
  conditions and wording of today's `REBUILDING`, `CHANGED` and `UNAVAILABLE`.
- The guard (`published()` before and after, `removal_gen`, the rebuild marker) moves here unchanged, so the
  browser path cannot bypass it.
- Imports never start the MCP server and never change process-wide settings. Today `server.py` sets
  `HF_HUB_OFFLINE=1` at import so a missing model is never downloaded; that stays in `server.py`, and the helper's
  entry point sets the same flag for its own process before anything loads the model (§5 rule 11).

**Compatibility contract:** before the move, a test captures the exact text of `search_memories`, `get_memory`,
`timeline` and `memory_stats` for a set of calls on the fixture vault; after the move the text is identical.

### 3.2 `dashboard.py` (changed): data, not a page

- `collect()` is unchanged: same numbers, same DST-safe screen time, same Pieces-only counting rules.
- `build()` writes the payload to **`atlas.json`** (atomically: temp file then replace) instead of substituting it
  into an HTML template. The `paths.dashboard()` function now returns `atlas.json`, so every existing caller that
  deletes it after a removal (`forget.py`, `index.py`) keeps working without change.
- The first build that writes `atlas.json` deletes a leftover `dashboard.html` and prints:
  "The Memory Atlas is live now: open it with `inkvault dashboard`."
- `atlas.json` also records `built` (local time) so the page can say when the charts were made.

### 3.3 `index.py` (small change): when search was built

`index.build()` adds `built` (UTC ISO) to search.db's existing `meta` table. The page's freshness line reads it
through `retrieval`. If it is absent (an index from 0.3.0), the line says "Search index: date unknown, rebuilt
tonight". File modification times are never used as freshness.

### 3.4 `atlasserve.py` (new): the helper

- `ThreadingHTTPServer` bound to `127.0.0.1`, port 0 (the OS picks a free one).
- **Single instance.** `atlas.lock` in the InkVault home (which only the user can read, like the vault itself)
  holds `{pid, port, started, control}`, where `control` is a random 32-byte secret made at start. A second launch
  reads the lock and calls `POST /control/code` with header `X-InkVault-Control: <control>`; a running helper
  answers with a fresh one-time code, and the launcher opens `/open?code=…` and exits. If the call fails (no
  process, wrong secret, no answer within 2 seconds) the lock is stale and is replaced by a new helper.
  `/control/code` accepts only that header, is subject to the same Host check, and is never reachable with the
  browser cookie alone.
- **Lifetime.** Each open Atlas tab sends `/api/ping` every 60 seconds while visible. With no ping for 30 minutes
  the helper stops and removes its lock. Ctrl+C stops it immediately when run from a terminal.
- **Routes** (anything else is 404; wrong method is 405):
  - `GET /open?code=…`: exchanges a one-time code for the session cookie, then redirects to `/`.
  - `GET /` and `GET /assets/<name>`: a fixed allow-list of packaged files from `inkvault/atlas/`.
  - `GET /api/atlas`: the current `atlas.json`, read fresh on every request (forgetting can delete it at any time).
    If it is missing and no removal is pending, it is built once (under the nightly lock, non-blocking: if the lock
    is busy the page shows "being updated, try again shortly").
  - `POST /api/search`, `POST /api/record`: JSON in, JSON out, through `retrieval`.
  - `POST /api/ping`: keep-alive; returns the current generation so an open page can notice a removal.
- Request bodies over 16 KB are refused (413). Query text is limited to 500 characters.
- The vault and search files are opened read-only, per request, exactly as MCP does today.

### 3.5 `atlas/` (new): the page

`index.html`, `atlas.css`, `atlas.js` (charts, ported from today's template with identical chart ids,
calculations, presets, date ranges, tooltips and table views), `search.js` (search view and reader),
`echarts.min.js` 5.5.0 with its Apache-2.0 `LICENSE` and `NOTICE`, and the mark as a small image (target under
100 KB; the original artwork, not a reconstruction). No inline scripts or styles, so the content security policy
can be strict (§5). The look follows the Ink & Light palette; light and dark follow the system setting.

### 3.6 CLI

- `inkvault dashboard`: builds `atlas.json` if missing, starts the helper (or reuses a running one), opens the
  browser. `--no-open` starts it without a browser and prints nothing secret.
- `inkvault dashboard --shortcut` (Windows): creates "InkVault Atlas" on the desktop and in the Start menu, pointing
  at the tool's `pythonw.exe -m inkvault dashboard` (no console window), with the InkVault icon. Same creation and
  read-back approach as `watch`'s shortcuts; a failure is reported, not fatal. On other systems it explains that
  the icon is Windows-only for now.
- `rescue` opens the live Atlas at the end instead of a file (unless `--no-open`), and mentions `--shortcut` on
  Windows. `status` reports whether the Atlas data exists and when it was built.

## 4. What people see

### 4.1 Atlas view

Today's Atlas content and behaviour in the Ink & Light look: masthead with the mark and title, the date presets
and custom range, the stat tiles, and every existing chart and its table view.

### 4.2 Search view

- The header search box submits on **Enter** or the **Search** button only.
- Source chips: All, Conversations, Summaries, Captures, Snippets, Screen, Web history. A chip appears only when
  that source has records. Optional From/To dates in local time with the time zone shown.
- Results, 25 at a time with **More results**: source label, local date and time, title, excerpt. Keyword matches
  are highlighted from `spans`; meaning matches say "related by meaning".
- Screen text is labelled "Screen text". Timeline-only apps show app, window title and times only.
- Freshness line: "Search index updated 3:04 AM". When screen capture has recorded activity after that, it adds
  "Newer screen activity appears after tonight's update."
- Reader: chats show role and time per message, the matched message marked, **Earlier** / **Later** buttons;
  **Show all** on a cut message. Snippets keep code formatting. Web history shows "Browser history: the page
  itself isn't saved"; its address opens only on click, in a new tab, without a referrer.
- Switching to Atlas and back keeps query, filters, selection and scroll position, held in `sessionStorage` only:
  never in the address bar, so searches never reach browser history or browser sync.
- Keyboard: every control reachable by Tab with a visible focus ring; arrow keys move through results; Enter opens.
  Labels on every input.

### 4.3 States

| Situation | Page |
|---|---|
| Nothing in the vault yet | Welcome naming `inkvault rescue` / `inkvault sync` |
| Atlas data being built | "Building the Atlas…" |
| Searching | "Searching…" |
| No matches | "No matches", with a hint to try All sources or wider dates |
| Removal under way | Results and reader cleared; today's rebuilding message |
| Search changed during an answer, or a page notices a new generation on ping or focus | Results and reader cleared; "Search was just updated: search again" |
| Vault unreadable | "Can't read the vault right now" with Retry |
| No meaning search | Results labelled "keyword search only" |
| Record gone | "This memory was removed" |
| Helper stopped | "The Atlas has closed. Open it again from the InkVault Atlas icon or `inkvault dashboard`." |
| Chart library failed | The chart areas say so; search still works |

## 5. Privacy and security

The helper opens a door to the whole vault on this computer. These rules keep it closed to everything but the
tab the user opened.

1. **Loopback only.** Bound to `127.0.0.1`; never `0.0.0.0` or a LAN address.
2. **One-time code, then a cookie.** Each launch or second-launch creates a random 32-byte code, valid once and for
   60 seconds, passed in the opened address. `/open` swaps it for a random session secret in a cookie
   (`HttpOnly`, `SameSite=Strict`, `Path=/`) and redirects to `/`. The code is useless after that, so its appearance
   in browser history or browser sync exposes nothing.
3. **Every API request needs the cookie and an `X-InkVault: 1` header.** Other websites cannot attach that header to
   a cross-site request without a CORS grant, and the helper sends no CORS headers at all.
4. **Host check.** Any request whose `Host` is not exactly `127.0.0.1:<port>` is refused (421), which defeats DNS
   rebinding. A present `Origin` must equal `http://127.0.0.1:<port>`.
5. **Read-only.** No route writes the vault, search files or settings. The only file the helper writes is its own
   lock (and `atlas.json` via `dashboard.build()` when it is missing, under the same lock the nightly uses).
6. **Forgetting wins.** The guard runs before and after every search, record and Atlas read; a changed generation
   returns `changed` and the page clears what it shows. An answer already displayed, copied or screenshotted cannot
   be recalled, as today.
7. **Fixed files only.** Assets come from an allow-list; request paths are never joined onto directories.
8. **Plain text only.** The page inserts memory text with `textContent` and builds highlights as separate text
   nodes. No `innerHTML` with record data anywhere.
9. **Headers.** `Content-Security-Policy: default-src 'none'; script-src 'self'; style-src 'self';
   img-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'`;
   `Cache-Control: no-store`; `Referrer-Policy: no-referrer`; `X-Content-Type-Options: nosniff`.
   ECharts sets element styles through the DOM, which this policy permits. If the build finds a chart that needs
   inline style attributes, the only allowed relaxation is `style-src 'self' 'unsafe-inline'`; scripts stay
   `'self'` without exception, and the browser check in §6 confirms no policy violations are reported.
10. **No logs of content.** The request log is switched off; errors are logged by type only, never with the query
    or record text. Secrets are never printed.
11. **No network, no downloads.** All assets are bundled. A missing model means keyword-only search, never a
    download.

## 6. Testing

- **MCP compatibility:** golden text for each MCP tool on the fixture vault, captured before `retrieval.py`
  exists; identical afterwards.
- **Retrieval:** structured hits for every kind; keyword spans line up with the excerpt; meaning-only hits carry no
  spans; chat windows around the match; earlier/later/expand cursors; the 100,000-character cap; dates across a
  DST change; keyword-only fallback without vectors; `rebuilding`, `changed`, `unavailable`, `missing`, including a
  removal that completes during a call and a stale cursor.
- **Dashboard:** `atlas.json` carries exactly the payload today's page embeds; the leftover `dashboard.html` is
  removed once; forgetting still deletes and rebuilds the Atlas data.
- **Helper, against a real server on a free port:** wrong `Host`; foreign `Origin`; missing cookie; missing header;
  reused or expired code; path traversal and unknown assets; 405 and 413; idle shutdown; second launch reuses the
  running helper; `/control/code` refused with the cookie alone or a wrong secret; stale lock replaced; the helper
  process has `HF_HUB_OFFLINE=1` and a missing model gives keyword-only results without network access; nothing
  secret or private in output.
- **Page, in a real browser on a fixture vault seeded with hostile text** (`<script>`, `<img onerror>`, look-alike
  markers): everything renders as text; keyboard-only search, open, page through context and return; narrow
  layout; light and dark; Atlas↔Search round trip keeps state; nothing in the address bar but the clean `/`.
  Results recorded in the build notes; no new CI dependency.
- **Speed:** cold and warm search time, reader time, response size and peak memory on a synthetic vault of about
  100,000 records, on stated hardware. Targets: warm search under 2 s, first reader under 1 s. Misses are
  investigated, not promised away.
- The full suite on the existing CI matrix (Windows, macOS, Linux; Python 3.10 and 3.13).

## 7. Shipping

- Work happens on `atlas-search` from `v0.3.0` in its own worktree. Nothing from earlier trials is copied; their
  look is the visual reference only.
- Order: this spec → independent design review → implementation plan → plan review → build in small reviewed
  steps → whole-branch code review, including a read-only check against a real vault that reports counts only →
  privacy check of everything public → pull request → maintainer approval → merge, tag `v0.3.1`, GitHub release.
- CHANGELOG calls out the behaviour change: `inkvault dashboard` now starts a local helper and opens a live Atlas;
  the static `dashboard.html` is gone.
