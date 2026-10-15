# Changelog

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

## 0.3.0 — screen capture

- **`inkvault watch`** (Windows): a timeline of the windows you use, and the text of windows in apps you choose.
  Install with `uv tool install "inkvault[screen]"`, preview with `inkvault watch --apps` (records nothing), then
  `inkvault watch --install`. No images are ever kept: the window image lives in memory long enough to read.
- Text is read with Windows' own OCR in a separate, supervised worker process, after you settle on a window for 30
  seconds, at most every 2 minutes per window, and only if it changed. It's dropped if the window changed around the
  read. The `screen` extra adds the Windows OCR packages (including `winrt-Windows.Foundation.Collections`).
- Default text apps: Comet, Edge, the AI tools, terminals and editors, notes apps and Microsoft 365. Messaging apps
  and Chrome are off; `--install` asks about each and leaves them off without a terminal. Everyone using the Windows
  account is captured.
- Never captured: password managers, the lock screen and permission prompts, private windows, and anything you add
  with `--never-app` or `--never-title`. Secrets in titles and text are masked before storing.
- Controls: `--stop`, `--pause 30m|1h|until-tomorrow`, `--resume`, `--add-app`, `--remove-app`, `--never-app`,
  `--never-title`, `--reset-settings`, `--uninstall`, and two desktop shortcuts for pause and resume. Settings that
  are missing or invalid stop all recording until fixed.
- Starts at sign-in through Task Scheduler. Install reads the task back and waits for a live heartbeat before saying
  it works; uninstall disables, stops cooperatively, then deletes.
- `timeline` shows screen activity live in a separate *Screen* section. Search (new `screen` source) covers it after
  the nightly index and says how far it reaches. `get_memory` opens a stretch or a text read. `inkvault status` shows
  the watcher, the sign-in task and counts.
- The dashboard has *Screen time* and *Screen time by app*; digests add screen time by app and the longest windows
  (never screen text). "Top apps" is labeled as Pieces captures.
- `inkvault forget --screen --app EXE` or `--since/--until` (local time) removes whole windows, their text, and what
  was derived from them. Backups made earlier and answers already given aren't erased.
- Search builds read the vault in one snapshot, so they stay consistent while the watcher writes.
- Limits: bank sites need `--never-title`; private-window markers are English; the last moment between the final
  check and the capture can't be closed; Store apps are timeline-only; Windows only.

## 0.2.1

- **Browser history** in the vault: Chrome, Edge, Comet, Brave, Arc, Vivaldi, Opera and Firefox, from the profiles
  you choose with `inkvault browsers` (nothing is read before you choose; the nightly run never asks). Each sync
  copies the history file and reads the copy. Visits the browser later deletes stay in the vault.
- Addresses are cleaned before they are stored: sign-in codes, tokens, signed links, reset tokens, and email or
  username parameters are removed; searches stay. Page titles get the same cleaning.
- Search has a `web` source, one entry per page per day; `get_memory` lists a page's visits; the timeline has a
  browsing line per day; `memory_stats` counts visits by browser. Visits synced from other devices are searchable
  but don't count as active hours here.
- The dashboard has a Pages opened tile and series, and a separate *Top sites opened in a browser* card. Digests
  include the pages you opened; days that gain browsing are rewritten on the next run.
- `inkvault browsers --no KEY --forget` and `--skip-site HOST` remove visits from the vault, search, the dashboard
  and the digests (each removed visit's UTC day and the day either side). A removal is recorded first, so an
  interrupted one is finished by the next search build; search stays off until then.
- `inkvault sync` works without Pieces or sessions, and exits 1 when a chosen browser profile couldn't be read.
- `index`, `digest` and `dashboard` take the same lock as the nightly run, so they never overlap a removal or a sync.
- With nothing left to embed, `vectors.npz` is deleted instead of kept.

## 0.2.0

- **Claude Code and Codex sessions** in the vault: `inkvault sync` copies your prompts, the replies and session
  titles from the session files both tools keep on disk, reading only what's new. `rescue` and the nightly run sync
  too, before the backup. Sessions Claude Code deletes after 30 days stay in the vault. Tool output and attachments
  are left out (most of the bytes, and where secrets show up), and so are pasted images. Codex's own setup text
  (AGENTS.md, environment context) isn't counted as your words. Sessions are searchable as chats (named
  `Claude Code · project: title`), and feed the timeline, digests and dashboard.
- **Claude desktop app sessions** (agent mode, Windows) are synced too, as their own source (`Claude desktop`).
  They live in the app's package folder, apart from `~/.claude`, at paths past Windows' 260-character limit.
  Prompts that only the app's audit log kept are added to their conversation, and sessions known only from an
  audit log are kept too. The app's random sandbox folder name is never shown as a project.
- `inkvault status` lists synced sessions per source; `memory_stats` counts chat messages per source; the
  dashboard's Chat messages tile says which sources it counts; `get_memory` shows a conversation's name.

## 0.1.2

- **Topics over time** on the Memory Atlas: Pieces' own topic tags on your session summaries, in two groups:
  *ongoing* (recurring across months) and *bursts* (concentrated in a few weeks). Tags that differ only in case,
  hyphens or spacing are merged. Built by the next `inkvault index`; until then the card stays hidden. Idea from
  Anthony at Pieces.
- Long names on the Projects and Topics heatmaps are shortened with "…"; hover a cell for the full name.
- Dashboard weeks are right in every time zone. From UTC+13 on (New Zealand in summer, Tonga, Kiribati) they were
  shifted by a day and some heatmap counts went missing.
- A custom date range picked wholly before or after your data no longer comes out backwards; the date pickers stay
  within your data's first and last day.
- `inkvault nightly`: the export's time budget is computed from elapsed time, fixing an intermittent off-by-a-hair
  budget on Windows.

## 0.1.1

- Scheduling is tested end to end on Windows. On macOS and Linux it is covered by automated tests only so far;
  reports from real machines are welcome.
- **`inkvault schedule`**: a nightly refresh and backup, using Task Scheduler (Windows), launchd (macOS) or a systemd
  user timer / cron (Linux). The backup happens right after the export, then search, digests and the dashboard.
  Missed runs: Windows and systemd catch up after sleep or power-off, macOS after sleep (not power-off), cron
  doesn't catch up. The Windows task runs only while you're logged in. Optionally wakes the computer (Windows; macOS
  with a one-time `sudo pmset` command it prints). Keeps the computer awake during a run (Windows and macOS), keeps
  7 dated backups of `vault.db` (each checked before older ones are rotated out), always pins the vault folder, and
  logs every run, including crashes, to `nightly.log` (or `nightly-fallback.log` if that can't be written).
  The export has a time budget; a backlog that doesn't fit continues the next night, after the backup. The Windows
  task may run up to 8 hours.
  It also installs or updates InkVault as a uv tool (checking the installed version), and tells you if uv's tool
  folder isn't on your PATH.
- **`inkvault nightly`**: the same refresh + backup, run by hand. `rescue` won't start while a nightly run is going;
  it tells you to try again. A nightly run that finds a rescue going waits up to an hour for it (the rescue makes
  its own backup).
- `inkvault status` shows the schedule and the last run, checked against the scheduler itself: it says if the task
  is disabled, runs a different vault, or points at a missing InkVault install, and flags a run that's overdue.
- Finds PiecesOS through its own `.port.txt` on Windows and macOS, then the port that worked last time.
  Thanks to the Pieces team for the port-file locations.
- `--pieces-ports` option (checked for valid port numbers), and `python -m inkvault` works.

## 0.1.0

- First release: `inkvault rescue` exports PiecesOS's long-term memory into a local vault, with search for MCP
  clients, a dashboard and optional local digests.
