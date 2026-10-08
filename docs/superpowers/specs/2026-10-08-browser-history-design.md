# Browser history in the vault

**Status:** design, revised after two Codex reviews (2026-10-08); ready for the implementation plan
**Release:** v0.3.0. ChatGPT/Claude.ai importers and people are specced separately.

## Why

Pieces stopped capturing on September 27. Its captures were the vault's record of *where* the user was on the web:
`events.url` feeds top sites on the dashboard, the digests and keyword search. After Pieces, that record stops.
Browsers keep their own history, but Chrome and its relatives delete visits after about 90 days, so without a copy
they disappear. Copying history into the vault keeps search, the dashboard and the digests covering the web after
Pieces, and keeps visits past the browser's cleanup.

The user's hand-built rescue (`~/pieces-rescue/collect.py`) already does this for Comet, Chrome and Edge. Its
approach (copy the `History` file, skip sub-frame visits, skip one profile that belongs to someone else) is the
starting point.

## Decisions (with the user, 2026-10-08)

1. **Everything uses it:** searchable over MCP, on the timeline, in the digests and on the dashboard.
2. **Profiles are chosen, not assumed.** A browser profile can belong to someone else. The first time InkVault finds
   profiles it asks which are the user's and remembers. Profiles found later are skipped until the user decides.
   The nightly run never asks.
3. **Browsers:** the Chromium family (Chrome, Edge, Comet, Brave, Arc, Vivaldi, Opera, Chromium) and Firefox.
   Safari waits: macOS blocks reading it without Full Disk Access.
4. **Clean addresses plus a skip list.** Parts of an address that carry secrets or the user's identity (email,
   username) are removed before anything is stored. Search terms (`?q=`) stay. Paths are cleaned only after sign-in
   words, so document and issue links stay whole. The user can list sites never to keep.
5. **Own tables (approach B).** Visits are not mixed into Pieces' `events`: the Captures tile, the top-apps chart
   and the MCP's description of events keep meaning what they mean now.

## What the real data looks like

Measured by Codex on copies of this PC's history (2026-10-08), five profiles the user chose (Chrome `Default`,
`Profile 1`, `Profile 5`, Comet `Default`, Edge `Default`); Chrome `Profile 2` belongs to someone else and was not
opened.

- 8,302 visits in the files; the keep rules below leave 7,800 (83 sub-frames, 397 redirect hops, 22 non-http).
- Ordinary navigations carry both redirect-chain bits (6,920 visits). The kept set includes 505 reloads and 57 form
  submits. Core transition type 0 (link) dominates everywhere; 1, 2, 5, 6, 7 and 8 appear too, though
  not in every profile (Chrome `Default` had no 6, Edge no 8).
- **Seven groups of distinct visits share the same profile, time and address** (nine extra rows). Time plus
  address is not a unique key.
- 735 visits had zero duration. Chromium fills in duration after a page is left, can update a URL's title, and can
  remove the chain-end bit from an earlier visit when a client redirect extends the chain. A visit copied early can
  change later.
- 35 visits in Chrome `Default` came from another device (`originator_cache_guid` set). `is_known_to_sync` was 1
  on every row of that profile, so it can't tell them apart.
- Every profile had a `History-journal` and no WAL. All copies passed `quick_check`. The old rescue's logs since
  September 28 hold 91 browser log lines and no failed copy (the old collector copies only the main file and
  doesn't catch errors while reading, so this is encouraging, not proof).
- Query parameter names across all addresses include credentials the first list missed (`rapt` 68, `xsrf` 21,
  `tokenid` 3, `authcode` 2, `__clerk_handshake` 2, `login_verifier`, `consent_verifier`, `_vercel_jwt`,
  `_vercel_jwe`, `user_code`) and identity (`login_hint` 7, `email` 6, `login_identifier` 2, `upn` 2,
  `username` 2). Two kept visits had a credential inside an encoded address nested in a parameter.
- A rule redacting every long letters-and-digits path part would have changed 327 of 4,793 addresses; 114 kept
  visits had UUID-shaped path parts that it would also redact; it was replaced by the narrower rule in §3.
- 12 kept visits had a page title containing address-shaped text.
- `Local State`'s `profile.info_cache` named every profile folder holding a `History` file; there was no System or
  Guest profile with history.

## What the files look like

**Chromium family:** one folder per browser (the "user data dir"), with one subfolder per profile (`Default`,
`Profile 1`, …), each holding a SQLite file `History` (rollback journal: `History-journal`). Tables used:

- `urls(id, url, title, …)`. The title is the URL's *current* title, not the title at visit time.
- `visits(id, url → urls.id, visit_time, from_visit, transition, visit_duration, originator_cache_guid,
  originator_visit_id, …)`. Times are microseconds; `visit_time` counts from 1601-01-01 UTC. `transition & 0xFF` is
  the core type; bits `0x10000000` / `0x20000000` mark the start / end of a redirect chain. The `originator_*`
  columns are missing in older versions and are read only when present.

The user data dir's `Local State` (JSON) names each profile: `profile.info_cache.<folder>.name` and, when signed
in, `.user_name` (an email) and `.gaia_id`. Opera keeps its single profile in the user data dir itself.

**Firefox:** `profiles.ini` lists profiles (`Name`, `Path`, `IsRelative`). Each profile has `places.sqlite` in WAL
mode (`places.sqlite-wal`). Tables used: `moz_places(id, url, title)` and `moz_historyvisits(id, from_visit,
place_id, visit_date, visit_type)`, `visit_date` in microseconds since 1970 UTC. Types: 1 link, 2 typed,
3 bookmark, 4 embed, 5/6 reached through a permanent/temporary redirect, 7 download, 8 framed link, 9 reload.
Firefox was not measured on this PC; its rules come from Mozilla's definitions and are covered by fixtures.

**Where they are.** Windows paths for Chrome, Edge and Comet are confirmed on the user's PC. The rest are from the
browsers' documentation; a missing folder means "not installed".

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

### 1. Finding and choosing profiles (`browsers.json`, `inkvault browsers`)

**Finding.** A Chromium profile is any immediate subfolder of a user data dir that has a `History` file (or the dir
itself, for Opera), except `System Profile` and `Guest Profile`. `Local State` supplies names and accounts only; a
folder it doesn't list is still found, shown by its folder name. A Firefox profile is any `profiles.ini` entry
whose folder has `places.sqlite`.

A profile's key is `<browser>/<folder>`: `chrome/Profile 2`, `firefox/abcd1234.default-release`.

**Choices** live in `browsers.json` in the InkVault home:

```json
{"profiles": {"chrome/Default": {"choice": "yes", "created": "2025-11-02T09:14:00Z", "account": "3f2a…"},
              "chrome/Profile 2": {"choice": "no"}},
 "skip_sites": ["mybank.com"]}
```

- **A choice is bound to the profile, not just its folder name.** Chromium reuses folder names (`Profile 3`) after a
  profile is deleted and another created. With a choice, InkVault records the folder's creation time (where the OS
  gives one: Windows and macOS) and a SHA-256 of the profile's `gaia_id` (or `user_name` when there's no
  `gaia_id`), when signed in. If either piece of evidence changes (differs, appears, or disappears), the profile
  goes back to `new`, and `status` says why: *"chrome/Profile 3: signed-in account changed since you chose it."*
  Choosing again records the new evidence. The email itself is never stored.
- **This is best effort.** Linux gives no folder creation time, and a profile that was never signed in has no
  account, so there a replaced profile with a reused folder name can inherit the earlier choice. The README says
  so: when someone else starts using a browser on this computer, check `inkvault browsers`.
- **`inkvault browsers`** lists every profile found, with its browser, display name, signed-in email (read live,
  never stored) and choice (`yes`, `no`, `new`). In a terminal it then asks: *"Which of these are yours? Numbers
  separated by spaces, `all`, or `none`."* Every listed profile gets an answer: picked ones `yes`, the others `no`.
  - `inkvault browsers --yes <key>` / `--no <key>` change one profile without the prompt.
  - Turning a profile with stored visits to `no` asks *"Also remove the N visits already copied from it?"*
    (default no; `--no <key> --forget` answers yes).
  - `inkvault browsers --skip-site <host>` / `--unskip-site <host>` edit the skip list. A host matches itself and its
    subdomains. Adding one removes that site's stored visits right away.
- **Removing** (`--forget`, `--skip-site`) takes the same lock as `sync` and the nightly run, then, in this order:
  1. finds the affected days, in both the time zone recorded by the last index and the current one;
  2. writes a `rebuild-needed` marker file in the InkVault home;
  3. deletes those days' digests from `digests.db` (the next `inkvault digest` writes them again without that
     browsing);
  4. deletes the visits from `vault.db`;
  5. rebuilds search (tables and vectors), then the dashboard;
  6. deletes the marker.

  While the marker exists, the MCP tools answer only *"Search is being rebuilt after something was removed: run
  `inkvault index`."*, `status` says the same, and a successful `index` followed by a successful dashboard build
  deletes it. So a failed rebuild, or a process killed anywhere in between, never leaves the old index serving
  what was removed; the next nightly run's rebuild recovers. The nightly backups still hold the removed visits
  until they rotate out (7 nights); the message says so.
- **`rescue` and `sync`**, run in a terminal, show the same prompt when there are `new` profiles, then continue.
- **The nightly run never asks.** `new` profiles are skipped, and both the nightly log and `inkvault status` say
  *"N browser profiles waiting for you to choose: run `inkvault browsers`."* After an upgrade every profile is `new`,
  so no one's history is copied before the user says so.
- A profile that disappears (browser uninstalled) keeps its visits and its choice.

### 2. Sync (new `browsers.py`)

New tables in `vault.db`:

```sql
CREATE TABLE browser_visits (
    id TEXT PRIMARY KEY,          -- see "Id"
    profile TEXT NOT NULL,        -- chrome/Default
    visit_id INTEGER NOT NULL,    -- the browser's own visit id
    redirected INTEGER NOT NULL,  -- 1: Firefox redirected away from this visit (resolved per snapshot, below)
    created TEXT NOT NULL,        -- UTC ISO time of the visit
    url TEXT NOT NULL,            -- cleaned (§3)
    address TEXT NOT NULL,        -- SHA-1 of the raw address: groups pages without storing it
    title TEXT,                   -- cleaned, as observed at the last sync that saw the visit
    title_observed_at TEXT,       -- when that title was read (the sync's start time)
    duration_s REAL,              -- Chromium only
    transition INTEGER NOT NULL,  -- raw Chromium transition / Firefox visit_type
    origin TEXT,                  -- Chromium originator_cache_guid when set: synced from another device
    origin_visit_id INTEGER
);
CREATE INDEX browser_visits_profile ON browser_visits(profile);
CREATE INDEX browser_visits_created ON browser_visits(created);
CREATE TABLE browser_profiles (profile TEXT PRIMARY KEY, last_attempt TEXT, last_success TEXT, last_error TEXT,
                               visits_in_file INTEGER);
```

- **A best-effort copy.** For each `yes` profile, each attempt makes a fresh temp folder and copies the history file
  and its sidecars there under their exact names (`History`, `History-journal`; `places.sqlite`,
  `places.sqlite-wal`). Only the copy is opened by SQLite, read-write so it can recover from its own journal or WAL.
  It must pass `PRAGMA quick_check`, and all its rows are read before anything is written to the vault. That
  profile's rows then go in one transaction. On any failure the attempt is retried once after 5 seconds; a second
  failure is recorded in `browser_profiles.last_error` and the other profiles carry on. Copying a live database can
  catch it mid-write: a copy that passes can still miss the very latest visits, which the next sync picks up. Only
  a closed browser gives a guaranteed snapshot; InkVault doesn't ask for one.
- **Read everything; add new, update present.** Each sync reads the whole file. Chrome brings in other devices'
  visits with their original, older times and can reuse visit ids, so neither a time nor an id is a safe bookmark;
  a full read of a 90-day history takes seconds. Visits not yet stored are added. Visits already stored and still
  in the file get their `title` (when non-empty, with `title_observed_at`), `duration_s`, `transition` and
  `redirected` updated, because the browser changes those after the fact. Visits no longer in the file (the browser's cleanup, or the user clearing
  history) are kept as they were.
- **Id:** SHA-1 over the length-prefixed fields profile, native visit id, raw visit time and raw address. Distinct
  browser rows stay distinct (the seven measured collisions), and a reused visit id with a new time or address is a
  new row. If a profile's history file is replaced or restored from an older copy, its visits can be stored twice;
  that is accepted.
- **What is stored:** every `http`/`https` visit not on the skip list, whatever its transition. Which visits count
  as pages is decided when indexing (§4), so a visit that later loses its chain-end bit simply stops counting, and a
  later version can change the rule without another sync. `file:`, `chrome://`, `about:`, extensions and the like
  are never stored.
- **Firefox redirects are resolved inside one snapshot.** Visit ids can be reused, and stored visits outlive the
  browser's copy, so a stored `from_visit` could point at an unrelated old visit. Instead, while reading a copy, a
  visit is marked `redirected` when another visit *in the same copy* has type 5 or 6 and `from_visit` pointing at
  it. The flag is stored, and updated on later syncs while the visit is still in the file. Chromium needs no flag:
  its chain bits are in `transition`.
- `browser_profiles` records each profile's last attempt, last success and last error. Browser failures don't go in
  the shared `failures` table (the Pieces export clears it).

### 3. Cleaning addresses and titles

Applied to every address before it is stored, and to address-shaped text (`https?://…`) inside titles. The rules
only remove. They reduce what's stored; they don't promise to find every secret. `meta.browser_clean_format`
records the rules' version: because raw addresses aren't stored, a later, stricter version can re-clean stored
addresses, but a looser one can't bring anything back.

- The user-info part (`user:password@`) is dropped. The host is never changed.
- **Parameter names** are compared after lowercasing and removing everything but letters and digits (`__clerk_handshake`
  → `clerkhandshake`, `token_id` → `tokenid`). A query or fragment parameter is dropped when its name:
  - is one of `apikey`, `auth`, `authcode`, `code`, `csrf`, `key`, `nonce`, `otp`, `pass`, `pwd`, `rapt`,
    `samlrequest`, `samlresponse`, `session`, `sessionid`, `sid`, `sig`, `signature`, `state`, `ticket`,
    `usercode`, `xsrf`;
  - contains `token`, `secret`, `password`, `passwd`, `verifier`, `handshake`, `credential`, `jwt` or `jwe`;
  - starts with `xamz` or `xgoog` (signed cloud-storage links);
  - is identity: `email`, `emailaddress`, `loginhint`, `loginidentifier`, `upn`, `username`, `userid`, `phone`.
- **Parameter values** are dropped whatever the name when they look like a JWT or JWE (base64url parts starting
  `eyJ`, joined by two or four dots) or an email address. A value that, percent-decoded, is itself an `http(s)`
  address is cleaned by these same rules and put back encoded, up to three levels deep. A nested address found
  past the third level is dropped with its parameter, not kept as it is.
- **Fragments:** a fragment starting with `/` or `!/` that contains `?` is a route plus parameters: the route stays,
  the parameters are cleaned. Otherwise a fragment containing `=` is parameters. A bare fragment is dropped if it is
  JWT/JWE-shaped or 32+ characters of letters, digits, `-` and `_` with both letters and digits; otherwise it stays.
- **Paths:** a path part is replaced with `…` when it is JWT/JWE-shaped, or when it is 16+ characters of letters,
  digits, `-` and `_` and comes right after a part naming a sign-in step: `activate`, `activation`, `auth`,
  `callback`, `confirm`, `invite`, `invitation`, `login`, `magic`, `oauth`, `password`, `reset`, `signin`,
  `sign-in`, `sso`, `token`, `unsubscribe`, `verify`, `verification`. Document, issue and other resource ids stay
  whole. A token in a path that doesn't follow one of these words is not caught.
- Everything else stays, so `google.com/search?q=…` keeps the search.

### 4. Index (`index.py`, `embed.py`)

**Which visits count.** A stored visit counts as a page visit, a *recorded top-level navigation*, when:

- Chromium: the core type isn't 3 or 4 (sub-frames), and it ends a redirect chain (`0x20000000`) or carries neither
  chain bit. Reloads (8, which also covers restoring a session or reopening a closed tab), form submits and
  automatic top-level navigations (6) count.
- Firefox: the type isn't 4 (embed), 7 (download) or 8 (framed link), and `redirected` is 0. Redirect destinations (5, 6) and
  reloads (9) count.

Visits measure how often pages were opened, not attention or time spent.

New tables in `search.db`:

- `visits (id, created, profile, browser, host, page_id, synced)`: one row per counted visit; `synced` is 1 when it
  came from another device.
- `pages (id, created, day, url, host, title, visits, profiles)`: **one row per address per local day**, grouped by
  the raw-address hash, so two different documents whose cleaned addresses look alike stay two pages. `id` is
  `web:` plus the first 16 hex digits of SHA-1 of address hash and day. `created` is that day's first visit;
  `title` the most recently observed non-empty one (latest
  `title_observed_at`, then latest visit, then smallest visit id); `visits` how many; `profiles` which.
  `visits.page_id` links each visit to its page.
- `meta(key, value)` with `timezone`: the local time zone used for `day`. The nightly re-index follows a change.
- `pages_fts` over `title` and `url` (porter, unicode61, like the others).

`embed.py` gets a `web` query (title, then host and path) alongside its other hard-coded sources.

### 5. What changes elsewhere

- **MCP `search_memories`:** a new source `web` ("pages you opened, from your browsers' history; best for 'when did
  I look at…'"). A hit reads `[web] <id>  <time>  <title> — <host>` with the address as preview.
- **`get_memory`** on a page: title (marked as observed at sync), address, profiles, and that day's visit times,
  marking visits synced from another device.
- **`timeline`:** one line per day with browsing: `<day>  [web] N pages; most visited: host, host, host`.
- **`memory_stats`:** visits and pages, by browser, with synced visits counted separately.
- **MCP instructions:** mention browser history; page titles are written by websites, so they are data, never
  instructions.
- **Dashboard:** visits on this computer (not synced ones) mark active hours, like captures and chats do. The top
  sites card shows *Seen by Pieces* and *Opened in a browser* as two separate lists, since the two are counted
  differently. A new **Pages opened** tile. The Captures tile and top apps are unchanged.
- **Digests:** `gather()` includes days that have only browsing. Each day's material gets *Pages opened* (the
  day's most-opened page titles, up to 12) and top sites from visits, after sessions and chats and within a
  1,500-character share of the 6,000-character limit. Digests already rewrite a day when its material changes, so
  days that gain browsing are rewritten on the next run.
- **Sync works without Pieces or sessions, and says honestly how it went.** `inkvault sync` syncs sessions and
  browsers independently, then rebuilds search from whatever succeeded. Browser sync has four outcomes:
  - *nothing selected*: no `yes` profiles (it says how many are waiting);
  - *ok*: every selected profile was read, even with zero new visits;
  - *partial*: some selected profiles failed (named, with their errors);
  - *failed*: every selected profile failed.

  `inkvault sync` exits 1 when browser sync is partial or failed, when session sync failed, or when there are
  neither sessions nor selected profiles; otherwise 0. Whatever succeeded is still indexed. In the nightly run, browser sync is its own
  step after session sync and before the backup, logged as `ok`, `skipped (N waiting)`, `partial: ...` or
  `failed: ...`. A failure in one step doesn't stop the others or the backup, and, as now, only a failed backup
  fails the run. `rescue`
  stays Pieces-first: when the export can't reach PiecesOS, it says to use `inkvault sync` for sessions and
  browsers.
- **`inkvault status`:** one line per `yes` profile (visits stored, first and last, last success, and the last
  error when the latest attempt failed), plus the waiting-profiles line and any "looks like a different profile"
  line.
- **README and CHANGELOG:** what's copied, how to choose profiles, the skip list, what cleaning removes and what it
  can miss, and that removal reaches backups only as they rotate.

## Unchanged

The Pieces export, session sync, the backup, `events` and everything that reads it.

## Out of scope

- Safari (Full Disk Access), and history from phones except what a browser syncs into a desktop profile.
- Page text: history has only addresses and titles; InkVault doesn't fetch pages. Titles at visit time (single-page
  apps change them under one address) can't be recovered from history.
- Bookmarks, downloads, and the omnibox's typed search terms (`keyword_search_terms`).
- Importing the old rescue's `visits` table. That belongs with the switch-over importer; its ids are built
  differently, so the importer will need a de-duplication rule.

## Testing

Fixtures are small, real-schema Chromium `History` and Firefox `places.sqlite` files (with a WAL holding the newest
visit), plus `Local State` and `profiles.ini`, built in the tests. Shapes come from Codex's measurements; no real
addresses are used.

- **Finding and choosing:** each browser layout (Opera's, Arc's package folder); a `History` folder `Local State`
  doesn't list; System and Guest profiles ignored; the prompt (all, none, numbers); the nightly run skips `new`
  profiles and reports them; a reused folder name with a different creation time or account goes back to `new`.
- **Removing:** `--no --forget` and `--skip-site` delete the affected days' digests (in both time zones) before
  rebuilding search, vectors and the dashboard; a failed rebuild, or a stop between any two steps, leaves the
  `rebuild-needed` marker and the MCP tools refuse until a rebuild succeeds.
- **Consent:** an account that disappears, appears or changes sends a `yes` profile back to `new` with the reason.
- **Sync:** a second sync adds nothing; a visit added between syncs is added; two browser visits sharing profile,
  time and address are both kept; a reused visit id with a new address is a new row; a later sync updates duration,
  title and transition; a visit gone from the file stays; a copy failing twice is recorded and the next profile is
  still read; the four outcomes and their exit codes; sync works with no sessions and no Pieces; a title observed
  later wins over an older observation from another profile.
- **Counting:** sub-frames; a single-hop visit with both chain bits; a multi-hop Chromium chain keeps only its end; a
  visit that loses its chain-end bit stops counting; Firefox embeds, framed links and downloads left out; Firefox
  multi-hop and branching redirects keep only their destinations; an expired visit stays counted when a new chain
  reuses its native id; reloads and form submits counted; synced visits
  counted for search but not active hours.
- **Cleaning:** a table of addresses in, cleaned addresses out: each listed parameter name and its separator
  variants (`token_id`, `tokenid`, `__clerk_handshake`), JWT and JWE values, an email value under any name, an OAuth
  callback nested and encoded in a `redirect_uri`, a nested address four levels deep (dropped), `#/callback?access_token=…`, a bare JWT fragment, a reset link,
  a Google-Docs-shaped id and a UUID left alone, a search kept, a title containing an address.
- **Index, MCP, dashboard, digests:** two different raw addresses that clean alike stay two pages; pages group by
  address and local day with their visits linked; `search_memories(source="web")` (keyword and meaning),
  `get_memory`, `timeline` and `memory_stats` show browsing; the dashboard's two top-sites lists and the digest
  material (browsing-only days, the 1,500-character share) include visits.

## Changes from Codex's review (2026-10-08)

| # | Finding | Change |
|---|---|---|
| 1 | Profile + time + address collides (7 groups) | Id adds the native visit id, length-prefixed |
| 2 | A copy passing a check isn't a consistent snapshot | Described as best effort; fresh folder per attempt, exact sidecar names, `quick_check`, read fully before writing, retry after 5 s |
| 3 | Duration, title and chain bits change after the fact | Present visits are updated each sync; which visits count moved to indexing |
| 4 | Credential and identity names missed; JWE | Name normalization, longer lists, identity parameters, JWE and email values |
| 5 | Nested addresses, route fragments | Nested addresses cleaned to depth 3; fragment parsing defined |
| 6 | Path rule too eager; cleaned addresses merge pages; titles leak | Path rule only after sign-in words (user's choice); pages grouped by raw-address hash; titles cleaned |
| 7 | Removal left search, vectors, digests, dashboard | Removal rebuilds them under the lock, deletes affected digests, fails closed |
| 8 | "Pages the user opened" too absolute | "Recorded top-level navigations"; reloads and forms kept with their types |
| 9 | Synced visits look like local activity | `origin` stored; synced visits searchable, not active hours |
| 10 | Firefox redirects and downloads | Downloads left out; redirect sources found through `from_visit` |
| 11 | `info_cache` as sole discovery; reused folder names | Discovery by `History` file; System/Guest excluded; choice bound to creation time and account hash |
| 12 | Page identity, titles, time zone | Deterministic page ids, page-to-visit link, `timezone` recorded, titles "observed at sync" |
| 13 | Digests already rewrite changed days; sync stops without sessions; shared `failures`; `embed.py` | All four addressed in §2, §4 and §5; top sites shown as two lists |

## Changes from Codex's second review (2026-10-08)

| # | Finding | Change |
|---|---|---|
| 1 | Nothing said what happens past three nested levels | Dropped with its parameter |
| 2 | Firefox `from_visit` could match a reused id in an old visit | Redirects resolved within one snapshot; `redirected` flag stored |
| 3 | Removal rebuilt the dashboard before deleting digests; a stop mid-way left stale search | Digests deleted first, days in both time zones; `rebuild-needed` marker checked by the MCP tools |
| 4 | An account disappearing went unnoticed; Linux has no creation time | Any change in evidence resets to `new`; binding described as best effort |
| 5 | "Fails only when neither exists" hid failed copies | Four outcomes, exit codes, nightly log wording |
| 6 | No time recorded for title observations | `title_observed_at` and a fixed tie-breaker |
| 7 | Three measurement claims overstated | Corrected in "What the real data looks like" |
