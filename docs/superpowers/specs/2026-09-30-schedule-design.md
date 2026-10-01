# `inkvault schedule`: nightly refresh (v0.1.1)

**Status:** approved design, 2026-09-30
**Release:** v0.1.1 (v0.2.0 adds topic themes, Claude Code/Codex/browser sources and ChatGPT/Claude.ai importers; they plug into the same nightly run)

## Why

PiecesOS keeps capturing after the Pieces shutdown for as long as it runs locally, so a vault rescued once goes stale. Users need a set-and-forget nightly refresh that survives sleep, reboots and a cleared `uvx` cache, and that never risks the vault.

## Commands

```
inkvault schedule              set up the nightly run (asks about waking the computer when run in a terminal)
inkvault schedule --at 02:30   time of day, local, 24h (default 03:00)
inkvault schedule --wake       wake the computer for the run (no prompt)
inkvault schedule --no-wake    run at the next wake instead (no prompt; the default answer)
inkvault schedule --off        remove the schedule (keeps the vault, backups and log)
inkvault nightly               the job itself; safe to run by hand
inkvault status                also shows: schedule on/off, time, next run, last run time and result
```

Running `inkvault schedule` again replaces the existing schedule (it is idempotent). If `--home` / `INKVAULT_HOME` or `INKVAULT_PIECES_PORTS` is set, the scheduled job gets the same settings as command-line flags (`--home`, and a new global `--pieces-ports`), because Task Scheduler can't give a task environment variables.

## Installing a permanent command

`uvx` runs from a cache that uv may clear, so the scheduled job must not point into it. `schedule`:

1. Checks for an InkVault installed with `uv tool` (`uv tool list`). If missing, it runs `uv tool install git+https://github.com/sandoreclegane/inkvault` and says so. If `uv` is not on PATH, it stops with install instructions for uv.
2. Points the job at that install's interpreter: `<uv tool dir>/inkvault/<Scripts|bin>/<pythonw.exe|python> -m inkvault nightly`. On Windows, `pythonw.exe` means no console window appears at 03:00.
3. Tells the user that `inkvault` is now a normal command and that `uv tool upgrade inkvault` updates it.

The README switches its examples to `uv tool install` + `inkvault …`, keeping the `uvx` one-liner for a single rescue.

## `inkvault nightly`

Steps, in order:

1. Take the lock (`nightly.lock` in the vault folder, containing the PID). If another live run holds it, log "already running" and exit 0. A lock whose PID is gone is stale and is taken over. `rescue` takes the same lock, so the two never overlap. If `rescue` finds the lock held, it says the nightly run is in progress and exits 1.
2. Log `started` (the very first thing it does, so a run killed at startup is still visible).
3. **Export.** Find PiecesOS. If it does not answer, retry every 60 s for up to 10 minutes (it may still be starting after a wake), then log "PiecesOS not reachable, skipped" and continue. Otherwise run the incremental export.
4. **Index:** `index.build()`.
5. **Digest:** `digest.run()` (it already skips itself when Ollama is not running).
6. **Dashboard:** `dashboard.build()` without opening a browser.
7. **Backup** (always runs, even if earlier steps failed): copy `vault.db` with SQLite's online backup API (consistent while in WAL mode) to `backups/vault-YYYY-MM-DD.db` in the vault folder, and keep the newest 7. A second run on the same day overwrites that day's file.
8. Log `finished` with one line per step (`ok` / `skipped` / `failed: <error>`), then release the lock.

Each step runs in its own `try`, and a failure is logged and the run moves on. Exit code: 0 if the backup succeeded, else 1. A failed backup is the only failure that makes the run fail, because `vault.db` is the irreplaceable file.

**Log:** `nightly.log` in the vault folder, timestamped lines, trimmed to the last 30 runs. stdout/stderr of the steps are captured into it. `status` reports the last run's start time, finish time and result, and reports "started but never finished" if there is a `started` with no `finished`.

## Per-OS backends

One module per OS, each with two parts: a **pure function** that renders the task definition from (command, time, wake, env), and a thin **install/remove** wrapper that writes the file and calls the OS tool. `schedule.py` picks the backend from `sys.platform`.

| | Windows | macOS | Linux |
|---|---|---|---|
| Mechanism | Task Scheduler, task `InkVault Nightly`, created from rendered XML with `schtasks /Create /XML <file> /TN "InkVault Nightly" /F` | LaunchAgent `~/Library/LaunchAgents/org.inkvault.nightly.plist`, `launchctl bootout` (if loaded) then `launchctl bootstrap gui/<uid> <plist>` | systemd user units `~/.config/systemd/user/inkvault-nightly.{service,timer}`, `systemctl --user daemon-reload` and `enable --now inkvault-nightly.timer`. If `systemctl --user` is unavailable, use a `crontab` line tagged `# inkvault-nightly` |
| Trigger | Daily calendar trigger at `--at` | `StartCalendarInterval` {Hour, Minute} | `OnCalendar=*-*-* HH:MM:00` (cron: `MM HH * * *`) |
| Missed run | `StartWhenAvailable` | launchd runs a calendar job missed during sleep on wake (not one missed while powered off) | `Persistent=true` (asleep or off). Cron: no catch-up, as `schedule` says |
| `--wake` | `WakeToRun` | Not settable by a LaunchAgent. `schedule` prints `sudo pmset repeat wakeorpoweron MTWRFSU <at minus 2 min>` for the user to run; InkVault never calls sudo | Not available to user timers. `schedule` prints a note and continues without waking |
| Retries | `RestartOnFailure` 3 × PT15M if the task fails to start | none | none |
| Limits | `ExecutionTimeLimit` PT4H; runs on battery; `DisallowStartIfOnBatteries`/`StopIfGoingOnBatteries` false; `LeastPrivilege`, `InteractiveToken` (only when logged in, no stored password) | runs when logged in | runs when logged in. `schedule` mentions `loginctl enable-linger $USER` for running while logged out |
| Remove (`--off`) | `schtasks /Delete /TN "InkVault Nightly" /F` | `launchctl bootout`, delete plist | `disable --now`, delete units (or remove the tagged crontab line) |

The wake prompt only appears when stdin is a TTY and neither `--wake` nor `--no-wake` was given. Its default is no. On a Mac or Linux machine the prompt text says what waking requires there.

On Windows, `schedule` also checks `powercfg` "Allow wake timers". If wake was chosen and wake timers are disabled, it prints a warning and how to enable them. This check is read-only and InkVault never changes power settings.

## macOS: the port file in `~/Documents`

PiecesOS writes `.port.txt` under `~/Documents`, which macOS privacy controls (TCC) may block for a background job. `ports_from_files` already ignores unreadable files (`OSError`). Additions:

- `schedule` (run interactively, where access is normally allowed) finds PiecesOS and saves where it answered in the vault's `meta` table (`pieces_url`, which `export` already saves on every run).
- `ports()` order becomes: `INKVAULT_PIECES_PORTS` → port files → port from the saved `pieces_url` → defaults.
- If no port file could be read and the defaults fail, the log line says so and suggests `inkvault --pieces-ports PORT schedule`.

Ask the Pieces team (Anthony) to confirm the TCC behavior and the Linux port-file location.

## Code layout

- `src/inkvault/nightly.py`: lock, the step runner, backup, log write and trim, reading the last-run state.
- `src/inkvault/schedule.py`: install-a-permanent-command logic, backend selection, prompt, `status` info.
- `src/inkvault/schedulers/{windows,macos,linux}.py`: render and install/remove for each OS.
- `cli.py`: `schedule` and `nightly` subcommands; `status` additions; `rescue` takes the lock.
- `paths.py`: `nightly_log()`, `backups_dir()`, `nightly_lock()`.
- `export.py`: `saved_port()` (read `pieces_url`) and `remember()` (write it).
- Version → 0.1.1; README section "Keep it up to date"; CHANGELOG entry crediting the Pieces team's port-file help.

## Testing

- **Renderers (all OSes, run on every CI OS):** the Windows XML parses and has the trigger time, `StartWhenAvailable`, `WakeToRun` matching the flag, the restart policy, the command and env. The plist parses with `plistlib` and has Hour/Minute and ProgramArguments. The systemd units have `OnCalendar` and `Persistent=true`. The cron line round-trips its tag.
- **nightly:** with the synthetic vault and PiecesOS absent (retry wait patched to 0): a failing index step still produces a backup; backup rotation keeps 7; the lock blocks a second run and a stale lock is taken over; the log trims to 30 runs; `status` reports "never finished" for a run that only has `started`.
- **ports:** the saved port is tried after port files and before defaults.
- **Manual:** real `schtasks` on Windows, including a sleep-through-trigger catch-up; `launchctl` on a Mac (Pieces team or a Discord user); `systemctl --user` on Linux if a tester turns up.

## Out of scope (v0.1.1)

Off-site or custom-folder backups (`--backup-to`), running while logged out on Windows, auto-updating InkVault, and notifications on failure.
