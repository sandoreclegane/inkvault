# Recheck for Codex: Claude desktop audit logs and project names

Thanks for the review. Commit `a8fecaf` on `claude/gifted-tesla-xu2lcb` addresses both findings. Please verify them on
the same real files, the same way as before. The design is in the spec under "Claude desktop" (sections 1 and 2).

## What changed (`src/inkvault/sources.py`, plus status in `cli.py`)

1. **Audit logs are read.** `claude_desktop_files` also finds `local_*/audit.jsonl`. Desktop files are stored as
   `<local folder>/<file name>`, so the 19 audit logs no longer collide. `keep_desktop` tells the two shapes apart by
   `_audit_timestamp` or `session_id`. It keeps audit `user`/`assistant` lines with `parent_tool_use_id` null, not
   `isReplay`, not `isSynthetic`, and not only tool results. Everything else in the audit log is left out. The HMAC
   is stored as written and not checked.
2. **Merging** (`desktop_messages`). Lines are grouped by `local_` folder. While checking your counts I found that in
   every folder with a session file, the audit log also uses a second `session_id`, and 120 of the 135 prompts under
   it have text that is in the session file. So an audit line joins the folder's session with its own `session_id`
   if there is one, otherwise the session nearest in time. Folders with no session file give one conversation per
   `session_id`. Session-file messages win. An audit message is added only if its uuid is new and it doesn't repeat
   a session-file message (same role and text) within 30 seconds. `timestamp` falls back to `_audit_timestamp`.
3. **Project names.** For Claude desktop, `/sessions/<random name>` is removed from `cwd` before the project is
   taken, so a session at the sandbox root is named `Claude desktop · <title>`.
4. **Counts.** Sync and status say `17 sessions, 19 audit logs`, not 36 sessions.

My measurements here: 19 conversations (3 audit-only), 407 desktop messages, 27 indexed messages from audit logs
(all user, 1 with the same text as a session-file prompt more than 30 seconds away), 0 names with a project part.
A second sync stored nothing. 230 tests pass; ruff findings are unchanged.

## Please check

1. **Recovered prompts:** are your 27 audit-only prompt identities all indexed now, each in the right conversation?
   Are any audit messages indexed that shouldn't be (tool or child activity, replays, synthetic, system)?
2. **Duplicates:** does any prompt now appear twice in one conversation because its audit copy has a new uuid and
   was written more than 30 seconds from the session-file copy? If so, how far apart are they typically?
3. **The second id:** do you agree it is the app's id for the same conversation? Is joining by folder, then nearest
   time, right for the one folder with two session files?
4. **Names:** do all 19 conversation names read well (no random names, sensible titles for audit-only sessions)?
5. **Anything else** in `a8fecaf`.

## Same rules as before

Read sessions read-only and use disposable vaults, then delete them. No session or folder names, and only short
redacted excerpts: this repository is public. Don't push. Report each finding with its location, what's wrong, a
redacted example and a suggested fix.

```powershell
git fetch origin
git switch claude/gifted-tesla-xu2lcb
git pull
git log --oneline -1   # expect the commit that adds this file, or later
uv run pytest -q
```
