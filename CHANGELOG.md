# Changelog

## 0.1.1

- **`inkvault schedule`**: a nightly refresh and backup, using Task Scheduler (Windows), launchd (macOS) or a systemd
  user timer / cron (Linux). A run missed while the computer was off or asleep happens when it next wakes (not with
  cron). Optionally wakes the computer (Windows; macOS with a one-time `sudo pmset` command it prints). Keeps the
  computer awake during a run, keeps 7 dated backups of `vault.db`, and logs every run to `nightly.log`.
  It also updates an older installed InkVault and tells you if uv's tool folder isn't on your PATH.
- **`inkvault nightly`**: the same refresh + backup, run by hand. `rescue` waits for a running nightly run.
- `inkvault status` shows the schedule and the last run.
- Finds PiecesOS through its own `.port.txt` on Windows and macOS, then the port that worked last time.
  Thanks to the Pieces team for the port-file locations.
- `--pieces-ports` option (checked for valid port numbers), and `python -m inkvault` works.

## 0.1.0

- First release: `inkvault rescue` exports PiecesOS's long-term memory into a local vault, with search for MCP
  clients, a dashboard and optional local digests.
