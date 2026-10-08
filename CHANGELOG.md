# Changelog

## Unreleased (0.2.0)

- **Claude Code and Codex sessions** in the vault: `inkvault sync` copies your prompts, the replies and session
  titles from the session files both tools keep on disk, reading only what's new. `rescue` and the nightly run sync
  too, before the backup. Sessions Claude Code deletes after 30 days stay in the vault. Tool output and attachments
  are left out (most of the bytes, and where secrets show up), and so are pasted images. Codex's own setup text
  (AGENTS.md, environment context) isn't counted as your words. Sessions are searchable as chats (named
  `Claude Code · project: title`), and feed the timeline, digests and dashboard.
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
