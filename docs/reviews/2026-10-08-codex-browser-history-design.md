# Review request for Codex: the browser-history design, before it's built

Hi Codex. InkVault (https://github.com/sandoreclegane/inkvault) is a local-only tool that saves a person's Pieces
memory and makes it searchable over MCP. Version 0.3.0 adds browser history: visits from Chromium-family browsers and
Firefox, copied into the vault so search, the timeline, the dashboard and the digests cover the web after Pieces.

This time there's **no code yet**. Please review the design, and check its assumptions against the real browser
files on this PC, before the implementation plan is written. Mistakes are cheaper to fix here than after the tests
are written around them.

## What to read

- Branch: `browser-history` (local; not pushed)
- `docs/superpowers/specs/2026-10-08-browser-history-design.md`: the design. Read it all.
- `C:\Users\Tmatt\pieces-rescue\collect.py`, `collect_browsers()`: the user's hand-built rescue, which has copied
  Comet, Chrome and Edge history nightly since February. **Read only: never write to `pieces-rescue` or run it.**
- For how the rest of InkVault fits: `src/inkvault/sources.py` (0.2.0's session sync, the pattern this follows),
  `index.py`, `server.py`, `dashboard.py`, `digest.py`, `nightly.py`.

## Questions

1. **Which visits count.** The design keeps a Chromium visit if it ends a redirect chain (`0x20000000`) or carries
   neither chain bit, and drops core types 3 and 4. For Firefox it drops `visit_type` 4 and 8. On real history:
   does this leave exactly the pages the person opened? Are reloads (Chromium core type 8, Firefox 9), form
   submits, or visits synced in from other devices (`originator_cache_guid`, `is_known_to_sync`) worth treating
   differently? Do redirect chains really carry both bits on single-hop visits in current Chrome/Edge/Comet?
2. **Copying a live file.** The browser holds `History` open. Is copying it (plus `-journal` / `-wal`) and reading
   the copy safe on Windows while the browser runs? Has the old rescue ever logged a failed or corrupt copy
   (`pieces-rescue/logs/`)? Is "retry once, then record the failure" enough?
3. **Cleaning addresses.** Is the parameter list right: anything missing that commonly carries secrets, or anything
   on it that removes useful memory too often? Is the path-segment rule (32+ characters, letters and digits) too
   eager or too timid on real history?
4. **Ids.** The id is a hash of profile, raw visit time and raw address, and every sync reads the whole file. Can
   one real visit change its time or address between syncs (and so become two rows)? Can two real visits share all
   three?
5. **Profile discovery.** Does `Local State`'s `profile.info_cache` name every profile folder that has a `History`
   file on this PC, including Comet's? Are there folders with a `History` that aren't profiles (`System Profile`,
   `Guest Profile`) that should be ignored?
6. **Pages per day.** Search shows one row per address per local day instead of one per visit. Is that the right
   unit, or would one per address per day **per title** (single-page apps keep one address) be better?
7. Anything the design gets wrong about the rest of InkVault (index, server, dashboard, digests, nightly), or any
   simpler way to the same result.

## Please check against the real files

Work on **copies** in a throwaway folder (`%TEMP%\inkvault-codex-browser`), never on the browsers' own files, and
delete the folder when you're done. Comet, Chrome (profiles `Default`, `Profile 1`, `Profile 2`, `Profile 5`) and
Edge are installed. **Leave `chrome/Profile 2` alone entirely: it belongs to someone else.** Don't open it, count it
or list its contents. Firefox isn't installed, so the Firefox questions can only be answered from what you know.

Useful things to measure (counts and shapes, not contents):

- per profile: visits in the file, how many each keep rule removes (sub-frames, redirect hops, non-http)
- the distribution of `transition & 0xFF` and of the chain bits
- for question 3: across all addresses, the **names** of query and fragment parameters by frequency, and how many
  addresses the path rule would change. Report parameter names and counts only.

**These are a person's private browsing records.** Don't paste addresses, titles, parameter values or email
addresses into your reply. If an example is needed, make it up in the same shape (`https://example.com/reset/<40
letters and digits>`).

## How to reply

For each finding, give:
- the part of the design (section and bullet)
- what's wrong or missing, with the measurement that shows it
- the change you suggest

Please don't commit to the branch. Send the findings back; the design will be updated, and the tests will be
written around your measurements.
