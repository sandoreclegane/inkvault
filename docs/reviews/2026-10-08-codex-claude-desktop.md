# Review for Codex: Claude desktop app sessions

Commit `94a1121` on `claude/gifted-tesla-xu2lcb` adds a third source to `inkvault sync`: the Claude desktop app's
agent-mode sessions (`claude_desktop`, shown as "Claude desktop"). Please review it on this PC's real files, the same
way as your earlier reviews. The design is in `docs/superpowers/specs/2026-10-08-coding-sessions-design.md` (search
for "Claude desktop").

## What changed

1. **Where the files are** (`sources.claude_desktop_roots`, `claude_desktop_files`). On Windows the app is an MSIX
   package. Sessions are read from
   `%LOCALAPPDATA%\Packages\Claude_*\LocalCache\Roaming\Claude\local-agent-mode-sessions` and from `%APPDATA%\Claude\local-agent-mode-sessions`, which resolved to the same folder here. Each root is
   resolved and read once. Only `*/*/local_*/.claude/projects/*/*.jsonl` is read: not `audit.jsonl`, not subagents.
   Other platforms return no roots.
2. **Long paths** (`long_path`). The full paths run past 260 characters, and `LongPathsEnabled` is 0 on this PC, so
   a plain `glob` raised `FileNotFoundError`. Roots get the `\\?\` prefix on Windows.
3. **Reading.** The lines have Claude Code's format, so `keep_claude` and `claude_messages` are reused, with the
   source passed in. Ids are `claude_desktop:<uuid>`, conversations `claude_desktop:<sessionId>`, names
   `Claude desktop · <project>: <title>`. The title line here is `ai-title`, which the existing `*title` rule takes.
4. **Names in one place.** The dashboard's project-label filter (`SOURCE_LABEL`), its Chat messages tile and
   `memory_stats` now take source names from `sources.NAMES`, so "Claude desktop" never becomes a project.

Tried here: 17 desktop sessions (2026-02-18 to 2026-10), 1,126 lines kept, 399 more indexed messages; a second sync
added nothing. 225 tests pass, and ruff reports the same findings as before the change.

## Please check

1. **Sessions only in `audit.jsonl`.** There are 19 `local_*` folders with an `audit.jsonl`, but only 17 session
   files under `.claude/projects`. Are there sessions whose turns exist only in `audit.jsonl`? If so, how many, how
   many turns, and is the audit format (`session_id`, `_audit_timestamp`, `_audit_hmac`, `message`) safe to read as
   a fallback? Compare the `user`/`assistant` turns in both formats for sessions that have both: does either have
   turns the other lacks?
2. **Nothing missed or doubled.** Count every `.jsonl` under the desktop app's data folder (with the `\\?\` prefix)
   and say which ones `claude_desktop_files()` skips and whether each skip is right. Check that no session file is
   read under two roots.
3. **Long paths.** Is `long_path` right for every case: a root that is already `\\?\`-prefixed, a UNC path
   (`\\server\share`), a root on another drive, and a `resolve()` that fails or follows the MSIX redirection
   differently when run outside the app?
4. **Project names.** `cwd` here is a sandbox path (`/sessions/<random-name>/...`). Does the project come out as a
   meaningful folder or as the random sandbox name? If it's the random name on most sessions, suggest what to use
   instead, from fields that are in the real lines.
5. **Text.** For a few real desktop sessions, confirm the indexed messages are what was typed and replied, with no
   system reminders, tool output or attachments.
6. **Anything else** in `94a1121` that looks wrong.

## Same rules as before

Read sessions read-only and use disposable vaults (`INKVAULT_HOME` under `%TEMP%`), then delete them. Never write in
the desktop app's folders. Quote only short redacted excerpts, and no session or folder names: this repository is
public and these are the user's private sessions. Don't push. Report each finding with its location, what's wrong, a
redacted example and a suggested fix.

```powershell
git fetch origin
git switch claude/gifted-tesla-xu2lcb
git pull
git log --oneline -1   # expect the commit that adds this file, or later
uv run pytest -q
```
