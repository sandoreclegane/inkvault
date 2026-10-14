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

- `search(query, sources=None, mode="hybrid", since=None, until=None, limit=25, offset=0, generation=None)
  -> SearchResult`
  - `sources`: a subset of the existing kinds `summaries, events, chats, snippets, screen, web`; `None` is all.
  - `mode`: `hybrid`, `keyword` or `meaning`, with exactly today's meaning, validation and messages. MCP passes
    its `mode` through; the Atlas always uses `hybrid`.
  - `since` / `until`: UTC instants (ISO). MCP's existing parsing (a date, or a UTC time) stays in `server.py`;
    the Atlas converts local dates itself (§4.2).
  - Same ranking as today: keyword (FTS5/bm25) and meaning (vectors) lists fused with RRF, same candidate counts,
    same screen de-duplication (a screen stretch with OCR text is not listed twice). Ranking is deterministic for
    a given query, filters and generation, so paging re-runs it and slices.
  - Paging: `offset` > 0 requires the `generation` of the first page. If the current generation differs, the
    answer is `changed` and no hits are returned, so later pages never mix two states of the vault.
  - `SearchResult`: `hits`, `more` (bool), `keyword_only` (bool, true when vectors or the model are missing),
    `generation` (the `published()` stamp the answer was read under).
  - `Hit`: `kind`, `id`, `title`, `time` (UTC ISO), `source_label`, `excerpt`, `match`.
    `match` is either `{"type": "keyword", "spans": [[start, end], ...]}` (UTF-16 offsets into `excerpt`, never
    from HTML) or `{"type": "meaning"}`. Spans come from FTS snippets made twice with two different private-use
    marker pairs: a character that differs between the two copies is an inserted boundary, one that matches is the
    record's own text (kept, even if it equals a marker), so a record can't fake a highlight. A meaning-only hit's excerpt is the
    record's opening text and is never highlighted.
- `record(ref, around=None, cursor=None) -> Record`
  - `ref` is the namespaced `(kind, id)` pair from a hit, so ids never need to be globally unique.
  - Chats: a window of at most 20 messages before and after `around` (the matched message), plus opaque
    `earlier` / `later` cursors that load 20 more. A cursor is bound to the conversation and the generation; a
    cursor from an older generation is refused with `changed`.
  - Summaries, events, snippets, screen text: the whole record. Web: title, URL, profile and visit times, labelled
    as browser history.
  - Every response is capped at 100,000 characters of record text. A single message or record longer than its
    share is cut with `truncated: true` and an `expand` cursor. Expansion **advances**: each call returns the next
    chunk of that one item (up to the cap) and a new `expand` cursor until the item is complete, so an item of any
    length can be read in full. Cursors carry the item, the character offset and the generation; a cursor from an
    older generation is refused with `changed`.
- `memory_text(id, max_chars=20000) -> str`: what `get_memory` returns today, byte for byte, including the whole
  conversation for a chat and today's truncation note. The chat windows and cursors above are for the Atlas reader
  only and never change MCP output.
- Errors are values, not exceptions: `rebuilding`, `changed`, `unavailable`, `missing`. These reuse the exact
  conditions and wording of today's `REBUILDING`, `CHANGED` and `UNAVAILABLE`.
- The guard (`published()` before and after, `removal_gen`, the rebuild marker) moves here unchanged, so the
  browser path cannot bypass it.
- Imports never start the MCP server and never change process-wide settings. Today `server.py` sets
  `HF_HUB_OFFLINE=1` at import so a missing model is never downloaded; that stays in `server.py`, and the helper's
  entry point sets the same flag for its own process before anything loads the model (§5 rule 11).

**Compatibility contract:** before the move, a test captures the exact text of `search_memories`, `get_memory`,
`timeline` and `memory_stats` for a set of calls on the fixture vault; after the move the text is identical.
One deliberate difference: today, vectors present but the model folder missing makes `embed.load_model()` try a
download, which fails offline and errors the tool. MCP keeps today's behaviour exactly. The Atlas runs meaning search only when both `vectors.npz` and the model folder exist (`require_model=True`); otherwise it answers keyword-only with `keyword_only: true`.

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
through `retrieval`. If it is absent (an index built by 0.3.0 or earlier), the line says "Search index: build time
not recorded". It does not predict when the next build happens. File modification times are never used as
freshness.

### 3.4 `atlasserve.py` (new): the helper

- `ThreadingHTTPServer` bound to `127.0.0.1`, port 0 (the OS picks a free one).
- **Single instance, decided by an operating-system lock.** The helper holds an exclusive lock on `atlas.lock` in
  the InkVault home for its whole life, using the same mechanism as `nightly.lock()` (`msvcrt.locking` on Windows,
  `fcntl.flock` elsewhere). The OS releases it when the process ends, however it ends, so a held lock means a live
  helper and a free lock means none; no timeout or process check ever decides this.
  - A launcher first tries to take the lock without waiting. If it succeeds, it *is* the helper: it binds its port,
    then writes `{pid, port, started, control}` into a separate `atlas-helper.json` file (temp file, then replace),
    where `control` is a random 32-byte secret. The home folder is readable only by the user, like the vault.
  - If the lock is held, the launcher never starts a helper. It reads the info file (retrying for up to 10 seconds
    while a just-started helper is still writing it) and calls `POST /control/code` with header
    `X-InkVault-Control: <control>`. The running helper answers with a fresh one-time code; the launcher opens
    `/open?code=…` and exits. If no answer arrives in time, the launcher says "The Atlas is starting or busy; try
    again in a moment" and exits, without starting a second helper.
  - Two simultaneous launches therefore produce exactly one helper: one wins the lock, the other becomes a client.
  - A lock that can't be taken at all (an error other than "held", e.g. a drive without file locking) starts
    nothing and says so: no helper runs without owning the lock.
  - `/control/code` accepts only that header, is subject to the same Host check, and is never reachable with the
    browser cookie alone.
- **Lifetime.** Each open Atlas tab sends `/api/ping` every 60 seconds while visible. With no ping for 30 minutes
  the helper stops and removes its lock. Ctrl+C stops it immediately when run from a terminal.
- **Routes** (anything else is 404; wrong method is 405):
  - `GET /open?code=…`: exchanges a one-time code for the session cookie, then redirects to `/`.
  - `GET /` and `GET /assets/<name>`: a fixed allow-list of packaged files from `inkvault/atlas/`.
  - `GET /api/atlas`: the current `atlas.json`, read fresh on every request (forgetting can delete it at any time).
    If it is missing and no removal is pending, it is built once (under the nightly lock, non-blocking: if the lock
    is busy the page shows "being updated, try again shortly").
  - `POST /api/search`, `POST /api/record`: JSON in, JSON out, through `retrieval`. Every answer carries the
    generation it was read under. Opening a record sends the generation the page shows; a different current one
    answers `changed`.
  - `POST /api/ping`: keep-alive; returns the current generation so an open page can notice a removal, and the
    **Atlas revision** (from `atlas.json`'s published file, or none), so charts rebuilt on their own are redrawn
    while the search stays. `/api/atlas` returns the same revision, read before and after the file so one file's
    data is never labelled with another's revision.
- Request bodies over 16 KB are refused (413) at once, without reading them. A missing, negative or non-numeric
  length, or any `Transfer-Encoding`, is refused (400) before reading. Each connection has a 10-second socket
  timeout: an idle connection or a body that never fully arrives is closed without an answer and never holds a
  thread. Query text is limited to 500 characters.
- The vault and search files are opened read-only, per request, exactly as MCP does today.

### 3.5 `atlas/` (new): the page

`index.html`, `atlas.css`, `atlas.js` (charts, ported from today's template with identical chart ids,
calculations, presets, date ranges, tooltips and table views), `search.js` (search view and reader),
`echarts.min.js` 5.5.0 with its Apache-2.0 `LICENSE` and `NOTICE`, and the mark as a small image (target under
100 KB), cropped from the existing `docs/assets/inkvault-logo-horizontal.png`, not a reconstruction. No inline
scripts, no `<style>` blocks and no `style="…"` attributes in markup or in tooltip HTML, so the content security
policy can be strict (§5); fixed chart heights move into `atlas.css`, and heights computed at run time are set
through `element.style`, which the policy allows. The look follows the Ink & Light palette; light and dark follow
the system setting. The date-range preset is remembered in `localStorage` as today; because the helper's port
changes per launch, it resets to "All time" on each launch, which is accepted.

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
  that source has records. Optional From/To dates in local time with the time zone shown. Chips map to existing
  kinds: Conversations = `chats`; Summaries = `summaries`; Captures = `events` whose `source` is `pieces`;
  Snippets = `snippets`; Screen = `screen` stretches plus `events` whose `source` is `screen` (text read from the
  screen); Web history = `web`. `retrieval.search` takes an optional `event_source` filter for this; MCP never
  passes it.
- Date conversion happens in the helper, not the browser: From is the start of that local calendar day and To is
  the start of the local day after it (an exclusive end), each converted to UTC with the computer's time-zone
  rules for that date, so days that start or end a daylight-saving change are exactly 23 or 25 hours. The
  converted instants go to `retrieval.search`. Times shown on results and in the reader are converted back to
  local time the same way.
- Results, 25 at a time with **More results**: source label, local date and time, title, excerpt. Keyword matches
  are highlighted from `spans`; meaning matches say "related by meaning".
- Screen text is labelled "Screen text". Timeline-only apps show app, window title and times only.
- Freshness line: "Search index updated 3:04 AM". When screen capture has recorded activity after that, it adds
  "Newer screen activity appears after the next search update."
- Reader: chats show role and time per message, the matched message marked, **Earlier** / **Later** buttons;
  **Show all** on a cut message. Snippets keep code formatting. Web history shows "Browser history: the page
  itself isn't saved"; its address opens only on click, in a new tab, without a referrer.
- Switching to Atlas and back keeps query, filters, selection and scroll position, held in `sessionStorage` only:
  never in the address bar, so searches never reach browser history or browser sync. `sessionStorage` holds only
  the query, filters, the selected `(kind, id)` and the scroll offset, never record text, excerpts or chart data,
  and every entry is tagged with the generation it was made under.
- Keyboard: every control reachable by Tab with a visible focus ring; arrow keys move through results; Enter opens.
  Labels on every input.

### 4.3 States

| Situation | Page |
|---|---|
| Nothing in the vault yet | Welcome naming `inkvault rescue` / `inkvault sync` |
| Atlas data being built | "Building the Atlas…" |
| Searching | "Searching…" |
| No matches | "No matches", with a hint to try All sources or wider dates |
| Removal under way, or the page notices a new generation (on any answer, on ping, or when the tab regains focus) | **Everything derived from the old state is cleared** (§4.4); today's rebuilding message, or "Your memory was just updated", then the Atlas reloads its data when it is ready |
| Search changed during an answer | Same full clear; "Search was just updated: search again" |
| Vault unreadable | "Can't read the vault right now" with Retry |
| No meaning search | Results labelled "keyword search only" |
| Record gone | "This memory was removed" |
| Helper stopped | "The Atlas has closed. Open it again from the InkVault Atlas icon or `inkvault dashboard`." |
| Chart library failed | The chart areas say so; search still works |

### 4.4 Forgetting clears the whole page

When something is removed, the Atlas data, digests and search all change, so a page showing the old state must not
keep showing it. On a generation change the page:

1. Disposes every chart instance and empties its container, closes any open tooltip, and empties the tile values,
   day-detail and digest text, and every table view.
2. Empties the results list, the reader and the freshness line.
3. Deletes its `sessionStorage` entries (including the query itself, which may be what was forgotten).
4. Drops every in-flight request: responses carry their generation and any answer from an older one is discarded.
5. Shows the updating message, then fetches `/api/atlas` again. While a rebuild is pending the helper answers
   `rebuilding` and the page polls every 10 seconds while visible.

Responses are `no-store`, so nothing removed survives in the browser cache. What a user already saw, copied or
screenshotted cannot be recalled, as today.

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
   returns `changed` and the page clears everything derived from the old state (§4.4). An answer already displayed,
   copied or screenshotted cannot be recalled, as today.
7. **Fixed files only.** Assets come from an allow-list; request paths are never joined onto directories.
8. **Plain text only.** The search view and reader insert memory text with `textContent` and build highlights as
   separate text nodes; no `innerHTML` with record data there. The one place HTML is built from vault text is
   the chart tooltips (digests, titles, project names), which ECharts renders as HTML: those keep v0.3.0's `esc()`
   escaping of `& < > " '` for every interpolated value, carry no `style` attributes (classes only, §3.5), and
   a test pins `esc()`.
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
  exists; identical afterwards. The calls cover every `mode` (`hybrid`, `keyword`, `meaning`) and bad mode, every
  source, date and time bounds and bad dates, `get_memory` for each kind including a chat long enough to hit the
  20,000-character truncation note, `timeline` and `memory_stats`.
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
- **Forgetting in the page:** with charts, a tooltip, results and a reader open, a removal makes the page show none
  of the old values, text or tooltip, leaves no `sessionStorage` entry, and ignores a late response from before
  the removal.
- **Paging and expansion:** a second results page after a removal answers `changed`; repeated expansion of a
  300,000-character message returns all of it in order with no gaps or repeats.
- **Single instance:** two launches started at the same moment yield one helper; a helper that is slow to answer
  is never replaced; a killed helper's lock is free immediately and the next launch starts a new one.
- **Real history, on a disposable snapshot.** Near the end of the build, and again in the code review, the Atlas
  runs against a snapshot made from **one finished nightly backup** (and, optionally, the model folder), both
  named by the maintainer and copied as plain files: no live vault, search file, lock or SQLite sidecar is opened,
  and no other source is read. Search, vectors and the Atlas data are built inside the snapshot, which is created,
  used and deleted in one `try`/`finally`, so a failure at any step leaves nothing behind. Queries are typed
  without echo, never put on a command line. Results are counts and timings only, never content.
- **Speed:** cold and warm search time, reader time, response size and peak memory on a synthetic vault of about
  100,000 records and on the disposable real copy, on stated hardware. Targets: warm search under 2 s, first reader under 1 s. Misses are
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
