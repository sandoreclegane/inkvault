# Browser history in the vault

**Status:** design for review, 2026-10-08 (direction approved: own tables, not `events`; Codex review before the plan)
**Release:** v0.3.0. ChatGPT/Claude.ai importers and people are specced separately.

## Why

Pieces stopped capturing on September 27. Its captures were the vault's record of *where* the user was on the web:
`events.url` feeds top sites on the dashboard, the digests and keyword search. After Pieces, that record stops.
Browsers keep their own history, but Chrome and its relatives delete visits after about 90 days, so without a copy
they disappear. Copying history into the vault keeps search, the dashboard and the digests covering the web after
Pieces, and keeps visits past the browser's cleanup.

The user's hand-built rescue (`~/pieces-rescue/collect.py`) already does this for Comet, Chrome and Edge: 8,955
visits in 5 profiles from February to October 2026. Its approach (copy the `History` file, skip sub-frame visits,
skip one profile that belongs to someone else) is the starting point.

## Decisions (with the user, 2026-10-08)

1. **Everything uses it:** searchable over MCP, on the timeline, in the digests and on the dashboard.
2. **Profiles are chosen, not assumed.** A browser profile can belong to someone else. The first time InkVault finds
   profiles it asks which are the user's and remembers. Profiles found later are skipped until the user decides.
   The nightly run never asks.
3. **Browsers:** the Chromium family (Chrome, Edge, Comet, Brave, Arc, Vivaldi, Opera, Chromium) and Firefox.
   Safari waits: macOS blocks reading it without Full Disk Access.
4. **Clean addresses plus a skip list.** Parts of an address that look like secrets are removed before anything is
   stored. Search terms (`?q=`) stay. The user can list sites never to keep.
5. **Own tables (approach B).** Visits are not mixed into Pieces' `events`: the Captures tile, the top-apps chart
   and the MCP's description of events keep meaning what they mean now.

## What the files look like

**Chromium family:** one folder per browser (the "user data dir"), with one subfolder per profile (`Default`,
`Profile 1`, …), each holding a SQLite file `History`. The browser keeps it open and locked for SQLite, but the file
can be copied (the old rescue has done so nightly on Windows). Tables used:

- `urls(id, url, title, …)`
- `visits(id, url → urls.id, visit_time, from_visit, transition, visit_duration, …)`. `visit_time` and
  `visit_duration` are microseconds; `visit_time` counts from 1601-01-01 UTC. `transition & 0xFF` is the core type
  (3 and 4 are sub-frames: ads and embeds, not pages the user opened). Bits `0x10000000` and `0x20000000` mark the
  start and end of a redirect chain.

The user data dir's `Local State` (JSON) names each profile: `profile.info_cache.<folder>.name` and, when signed
in, `.user_name` (an email). Opera keeps its single profile in the user data dir itself, with no profile subfolder.

**Firefox:** `profiles.ini` lists profiles (`Name`, `Path`, `IsRelative`). Each profile has `places.sqlite`, in WAL
mode, so recent visits may be only in `places.sqlite-wal`. Tables used:

- `moz_places(id, url, title, …)`
- `moz_historyvisits(id, from_visit, place_id → moz_places.id, visit_date, visit_type)`. `visit_date` is
  microseconds since 1970-01-01 UTC. `visit_type` 4 (embed) and 8 (framed link) are not pages the user opened.

**Where they are.** Windows paths for Chrome, Edge and Comet are confirmed on the user's PC. The rest are from the
browsers' documentation and are confirmed when someone sees them; a missing folder just means "not installed".

| Browser | Windows | macOS | Linux |
|---|---|---|---|
| Chrome | `%LOCALAPPDATA%\Google\Chrome\User Data` | `~/Library/Application Support/Google/Chrome` | `~/.config/google-chrome` |
| Edge | `%LOCALAPPDATA%\Microsoft\Edge\User Data` | `~/Library/Application Support/Microsoft Edge` | `~/.config/microsoft-edge` |
| Comet | `%LOCALAPPDATA%\Perplexity\Comet\User Data` | `~/Library/Application Support/Comet` | (none) |
| Brave | `%LOCALAPPDATA%\BraveSoftware\Brave-Browser\User Data` | `~/Library/Application Support/BraveSoftware/Brave-Browser` | `~/.config/BraveSoftware/Brave-Browser` |
| Arc | `%LOCALAPPDATA%\Packages\TheBrowserCompany.Arc_*\LocalCache\Local\Arc\User Data` | `~/Library/Application Support/Arc/User Data` | (none) |
| Vivaldi | `%LOCALAPPDATA%\Vivaldi\User Data` | `~/Library/Application Support/Vivaldi` | `~/.config/vivaldi` |
| Opera | `%APPDATA%\Opera Software\Opera Stable` | `~/Library/Application Support/com.operasoftware.Opera` | `~/.config/opera` |
| Chromium | `%LOCALAPPDATA%\Chromium\User Data` | `~/Library/Application Support/Chromium` | `~/.config/chromium` |
| Firefox | `%APPDATA%\Mozilla\Firefox` | `~/Library/Application Support/Firefox` | `~/.mozilla/firefox`, `~/snap/firefox/common/.mozilla/firefox`, `~/.var/app/org.mozilla.firefox/.mozilla/firefox` |

## Design

### 1. Choosing profiles (`browsers.json`, `inkvault browsers`)

A profile's key is `<browser>/<folder>`: `chrome/Profile 2`, `firefox/abcd1234.default-release`. The user's choices
live in `browsers.json` in the InkVault home:

```json
{"profiles": {"chrome/Default": "yes", "chrome/Profile 2": "no", "edge/Default": "yes"},
 "skip_sites": ["mybank.com"]}
```

- **`inkvault browsers`** lists every profile found, with its browser, display name, signed-in email (read from
  `Local State` each time, never stored) and choice (`yes`, `no`, or `new`). In a terminal it then asks:
  *"Which of these are yours? Numbers separated by spaces, `all`, or `none`."* Every listed profile gets an answer:
  picked ones `yes`, the others `no`.
  - `inkvault browsers --yes <key>` / `--no <key>` change one profile without the prompt.
  - Turning a profile to `no` that has stored visits asks *"Also remove the N visits already copied from it?"*
    (default no; `--no <key> --forget` answers yes). Removal deletes them from `vault.db`; the nightly backups keep
    them until they rotate out (7 nights), and the message says so.
  - `inkvault browsers --skip-site <host>` / `--unskip-site <host>` edit the skip list. A host matches itself and its
    subdomains. Adding one removes that site's stored visits right away and says how many.
- **`rescue` and `sync`**, run in a terminal, show the same prompt when there are `new` profiles, then continue.
- **The nightly run never asks.** `new` profiles are skipped, and both the nightly log and `inkvault status` say
  *"N browser profiles waiting for you to choose: run `inkvault browsers`."* After an upgrade, every profile is `new`
  until the user chooses, so no one's history is copied without them saying so.
- A profile that disappears (browser uninstalled) keeps its visits and its choice.

### 2. Sync (new `browsers.py`)

New tables in `vault.db`:

```sql
CREATE TABLE browser_visits (id TEXT PRIMARY KEY, profile TEXT NOT NULL, created TEXT NOT NULL, url TEXT NOT NULL,
                             title TEXT, duration_s REAL, transition INTEGER);
CREATE INDEX browser_visits_profile ON browser_visits(profile);
CREATE INDEX browser_visits_created ON browser_visits(created);
```

- **Copy, then read.** For each `yes` profile, the history file is copied to a temp folder, with its `-wal` and
  `-journal` files when present, and the copy is read. The browser's own files are never opened by SQLite. If
  the copy can't be made or doesn't open (copied mid-write), it is copied once more; a second failure is written to
  `failures` and the other profiles carry on.
- **Read everything, keep what's new.** Each sync reads the whole history file, not just what's after the last
  sync. Chrome brings in visits from the user's other devices with their original, older times, and it can reuse
  visit ids after history is deleted, so neither a time nor an id is a safe bookmark. A history file holds at most
  about 90 days (Chromium) or a few hundred thousand visits (Firefox), so a full read takes seconds.
- **Id:** SHA-1 of `profile`, the browser's raw visit time, and the raw address, so the same visit read again is
  the same row (`INSERT OR IGNORE`), and a reused visit id is a new row. The raw address is used only for the hash
  and is never stored.
- **Which visits are kept:** `http` and `https` addresses only (no `chrome://`, `edge://`, `about:`, `file:`,
  extensions). Not sub-frames (Chromium core types 3 and 4; Firefox types 4 and 8). For Chromium, not the
  intermediate hops of a redirect chain: a visit is kept if it ends a chain (`0x20000000`) or carries neither chain
  bit. Not sites on the skip list.
- **Stored per visit:** `created` (UTC ISO time), the *cleaned* address (below), the title (clipped to 300
  characters), duration in seconds (Chromium only; Firefox has none), and the raw `transition` / `visit_type`, so a
  later version can read them differently.
- **Deleted from the browser, kept in the vault.** Visits the browser later forgets (its 90-day cleanup, or the user
  clearing history) stay in the vault. That is the point of the copy; the skip list and `--forget` are the way to
  remove something.
- `meta` gets `last_browser_sync`.

### 3. Cleaning addresses

Applied before anything is stored. The rules only remove; they never add or guess.

- The user-info part (`user:password@`) is dropped.
- **Query and fragment parameters** are dropped when the name (lowercased) is one of `access_token`, `api_key`,
  `apikey`, `auth`, `client_secret`, `code`, `id_token`, `jwt`, `key`, `nonce`, `otp`, `pass`, `password`, `pwd`,
  `refresh_token`, `samlrequest`, `samlresponse`, `secret`, `session`, `sessionid`, `sid`, `sig`, `signature`,
  `state`, `ticket`, `token`, or ends in `token`, `secret` or `_key`, or starts with `x-amz-` or `x-goog-`. A
  parameter whose value looks like a JWT (`eyJ…` with two dots) is dropped whatever its name. A fragment is treated
  as parameters only if it contains `=`; otherwise it is kept (single-page apps use it as a route).
- **Path segments** that look like one-time tokens are replaced with `…`: 32 or more characters of
  `[A-Za-z0-9_-]` containing both letters and digits, or a JWT. This catches password-reset and magic sign-in links.
  It also shortens some harmless ids (a 40-character commit hash); that loss is accepted.
- The host is never changed. Every other parameter stays, so `google.com/search?q=…` keeps the search.
- `meta.browser_clean_format` records the rules' version. Because raw addresses aren't stored, a later, stricter
  version can re-clean stored addresses, but a looser one can't bring anything back.

### 4. Index (`index.py`)

Two new tables in `search.db`, built from `browser_visits`:

- `visits (id, created, profile, browser, host, url, title)`: one row per visit. The dashboard and digests count
  these.
- `pages (id, created, day, url, host, title, visits, profiles)`: **one row per address per local day**: `created`
  is that day's first visit, `visits` how many, `title` the latest non-empty one. Search, embeddings and
  `get_memory` use pages, so a page opened 40 times in a day is one hit, not 40. `day` uses the time zone at
  indexing time; the nightly re-index follows a time-zone change.
- `pages_fts` over `title`, `url` (porter, unicode61, like the others); embedding kind `web`: title, then host and
  path.

### 5. What changes elsewhere

- **MCP `search_memories`:** a new source `web` ("pages you visited, from your browsers' history; best for 'when did
  I look at…'"). A hit reads `[web] <id>  <time>  <title> — <host>` with the address as preview.
- **`get_memory`** on a page: title, address, browser and profile, and that day's visit times.
- **`timeline`:** one line per day with browsing: `<day>  [web] N pages; most visited: host, host, host`. The
  timeline otherwise has nothing for days after Pieces that have no digest.
- **`memory_stats`:** visits and pages, by browser.
- **MCP instructions:** mention browser history; page titles are written by websites, so they are data, never
  instructions.
- **Dashboard:** visits mark active hours like captures and chats do; top sites counts Pieces URLs and visits
  together; a new **Pages visited** tile. The Captures tile and top apps are unchanged.
- **Digests:** each day's material gets *Pages visited* (the day's most-visited page titles, up to 12) and top sites
  from visits as well. Days already digested keep their digest; `inkvault digest --redo` rewrites them.
- **`inkvault sync`** syncs sessions, then browsers, then rebuilds search. `rescue` and the nightly run sync
  browsers right after sessions, before the backup. Browser sync is its own nightly step, so its failure doesn't
  stop session sync or the backup.
- **`inkvault status`:** one line per `yes` profile (visits, first and last), the last browser sync, and the
  waiting-profiles line when there are `new` ones.
- **README and CHANGELOG:** what's copied, how to choose profiles, the skip list, what cleaning removes.

## Unchanged

The Pieces export, session sync, the backup, `events` and everything that reads it.

## Out of scope

- Safari (Full Disk Access), and history from phones except what a browser syncs into a desktop profile.
- Page text: history has only addresses and titles; InkVault doesn't fetch pages.
- Bookmarks, downloads, and the omnibox's typed search terms (`keyword_search_terms`).
- Importing the old rescue's `visits` table. That belongs with the switch-over importer; its rows have the same
  shape, but their ids are built differently, so the importer will need a de-duplication rule.

## Testing

- Fixture builders write small, real-schema Chromium `History` and Firefox `places.sqlite` (with a WAL file holding
  the newest visit), plus `Local State` and `profiles.ini`.
- Discovery: profiles and names per browser layout, including Opera's no-subfolder layout and Arc's package
  folder; a missing browser is "not found", not an error.
- Choosing: the prompt (all, none, numbers); the nightly run skips `new` profiles and reports them; `--no --forget`
  removes visits; `--skip-site` removes a site and its subdomains.
- Sync: a second sync adds nothing; a visit added between syncs is added; a reused visit id with a new address is
  a new row; sub-frames, redirect hops and non-http addresses are left out; a copy that fails twice is recorded
  and the next profile is still read.
- Cleaning: a table of addresses in, cleaned addresses out (OAuth callbacks, signed S3 links, reset links, JWT in a
  fragment, a search, a single-page-app route, a commit hash).
- Index, MCP, dashboard and digests: pages group by address and local day; `search_memories(source="web")`,
  `get_memory`, `timeline` and `memory_stats` show visits; the dashboard and digest material include them.
