# Changelog

## 0.1.1

- **`inkvault schedule`**: a nightly refresh and backup, using Task Scheduler (Windows), launchd (macOS) or a systemd
  user timer / cron (Linux). The backup happens right after the export, then search, digests and the dashboard.
  Missed runs: Windows and systemd catch up after sleep or power-off, macOS after sleep (not power-off), cron
  doesn't catch up. The Windows task runs only while you're logged in. Optionally wakes the computer (Windows; macOS
  with a one-time `sudo pmset` command it prints). Keeps the computer awake during a run (Windows and macOS), keeps
  7 dated backups of `vault.db` (each checked before older ones are rotated out), always pins the vault folder, and
  logs every run, including crashes, to `nightly.log`.
  It also updates an older installed InkVault and tells you if uv's tool folder isn't on your PATH.
- **`inkvault nightly`**: the same refresh + backup, run by hand. `rescue` won't start while a nightly run is going; it tells you to try again.
- `inkvault status` shows the schedule and the last run.
- Finds PiecesOS through its own `.port.txt` on Windows and macOS, then the port that worked last time.
  Thanks to the Pieces team for the port-file locations.
- `--pieces-ports` option (checked for valid port numbers), and `python -m inkvault` works.

## 0.1.0

- First release: `inkvault rescue` exports PiecesOS's long-term memory into a local vault, with search for MCP
  clients, a dashboard and optional local digests.
