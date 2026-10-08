# Claude Code and Codex sessions in the vault (v0.2.0)

**Status:** approved direction, 2026-10-08 (Claude Code + Codex first; synced nightly)
**Release:** v0.2.0, with ChatGPT/Claude.ai importers, browser history and people (specced separately)

## Why

Pieces stopped capturing on September 27. The work that used to land in Pieces now happens in Claude Code and Codex,
and both keep every session on disk as JSONL. Claude Code deletes those files after 30 days by default
(`cleanupPeriodDays`), so without a copy they disappear. Bringing them into the vault keeps search, the timeline, the
digests and the Memory Atlas going after Pieces, and keeps the sessions past Claude Code's cleanup.

## What the files look like

**Claude Code:** `~/.claude/projects/<project>/<session-id>.jsonl` (or `$CLAUDE_CONFIG_DIR/projects`). One JSON
object per line. `type` is `user`, `assistant`, `attachment`, `system`, `queue-operation`, `last-prompt`, `summary`,
`custom-title`, and others. `user` and `assistant` lines carry `message.content`, which is a string or a list of
blocks (`text`, `thinking`, `tool_use`, `tool_result`), plus `uuid`, `sessionId`, `timestamp`, `cwd`,
`isSidechain` and `isMeta`. Subagent transcripts are in `<session-id>/subagents/` next to the session file.

On a real session, tool results and attachments are about 85% of the bytes: file dumps, command output, and any
secrets those contain.

**Claude desktop app (agent mode):** the desktop app runs agent-mode sessions with Claude Code inside and keeps them
apart from `~/.claude`, under the app's data folder at
`local-agent-mode-sessions/<id>/<id>/local_<id>/.claude/projects/<project>/<session-id>.jsonl`. On Windows the app
is an MSIX package, so that folder is `%LOCALAPPDATA%\Packages\Claude_*\LocalCache\Roaming\Claude\`;
`%APPDATA%\Claude\` is redirected to the same place. The lines are Claude Code's (same `type`s, `sessionId`,
`timestamp`, `isSidechain`; the title line is `ai-title`). `cwd` is a path inside the app's sandbox: the sandbox
root `/sessions/<random name>`, or a folder below it. The full paths run past Windows' 260-character limit, and long
paths are off by default, so a plain `open` or directory listing fails there.

Each `local_<id>` folder also has an `audit.jsonl` in another shape: `{type, uuid, session_id, message,
parent_tool_use_id, _audit_timestamp, _audit_hmac}`, with `timestamp`, `isReplay` and `isSynthetic` on some lines,
and no `sessionId` or `cwd`. It mostly repeats the session file, but not entirely. On this PC (Codex review,
2026-10-08): 19 audit logs and 17 session files in 16 folders; 3 folders have only an audit log; 27 top-level user
prompts (by uuid) are in an audit log and not in the session file, and 26 of those have text the session file lacks.
No non-empty assistant message was audit-only. The same prompt text can appear under two uuids. In every folder
that has a session file, the audit log also uses a second `session_id` that isn't the session file's: the app's own
id for the same conversation (120 of the 135 prompts under it have text that is in the session file; local check,
2026-10-08). So a folder, not a `session_id`, is what ties an audit log to its session.

**Codex:** `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` (or `$CODEX_HOME/sessions`). Archived sessions move to
`archived_sessions/`. Current files wrap each line as `{timestamp, type, payload}`, where `type` is `session_meta`,
`response_item`, `event_msg` or `turn_context`. What the user typed and what the agent replied come through as
`event_msg` payloads `user_message` and `agent_message`. `response_item` payloads of type `message` hold the same
turns, plus injected context such as `<environment_context>`. Older files have no wrapper: the first line is the
session's metadata (`id`, `timestamp`), and messages are top-level `{"type": "message", ...}` lines.

## Design

### 1. Sync (new `sources.py`, `inkvault sync`)

Two new tables in `vault.db`:

```sql
CREATE TABLE session_lines (source TEXT NOT NULL, file TEXT NOT NULL, offset INTEGER NOT NULL, raw TEXT NOT NULL,
                            PRIMARY KEY (source, file, offset));
CREATE TABLE session_files (source TEXT NOT NULL, file TEXT NOT NULL, read_to INTEGER NOT NULL, head TEXT NOT NULL,
                            PRIMARY KEY (source, file));
```

- `source` is `claude_code` or `codex`. `file` is the file's name. Session files are named by a unique id, so
  Codex moving a session to `archived_sessions/` doesn't read it twice.
- **Kept lines are stored verbatim**: the text of the line as written, so a later version can read them differently
  without another sync. One exception: image, document and tool-result blocks anywhere inside a kept line (Codex's
  compaction records nest whole earlier messages) are replaced by `{"type": ..., "omitted": true}`, matched by block
  type, never by text, and that line is stored re-serialized. Pasted images arrive as
  inline base64 and would otherwise fill the vault and its backups (Codex review, 2026-10-08).
- **What's kept:**
  - Claude Code: `user` and `assistant` lines, except user lines that are only tool results; and title lines
    (`summary`, any type ending in `title`).
  - Codex: `session_meta`, `response_item` messages, `event_msg` user and agent messages, `compacted` records
    (kept, not indexed), and the old format's message and metadata lines.
  - Everything else (tool output, attachments, queue and hook bookkeeping) is left out. Tool *calls* stay, inside
    the assistant lines.
- **Incremental:** each file is read from `read_to` (a byte offset), and only complete lines (ending in `\n`) are
  taken, so a session in progress is picked up where it stopped. `head` is the SHA-1 of the first line; if it
  changed, or the file got shorter, the file was rewritten, so its lines are dropped and it is read from the start.
  This is an assumption that the files are append-only, not a full rewrite detector: a rewrite that keeps the first
  line and doesn't shrink the file goes unnoticed.
- **Deleted files keep their lines.** That is the point: Claude Code's 30-day cleanup no longer loses anything.
- Only Claude Code's top-level session files are read (`projects/*/*.jsonl`), not subagent transcripts.
- **Claude desktop** is a third source, `claude_desktop` ("Claude desktop"). Its session files
  (`local_*/.claude/projects/*/*.jsonl`) and audit logs (`local_*/audit.jsonl`) are read; subagents are not. Each
  data folder is resolved first and read once, so the redirected `%APPDATA%` path doesn't read it twice. On Windows
  the folder is opened with the `\\?\` long-path prefix. Only the Windows locations above are read; another
  platform's location is added when it is seen, not guessed.
  - Desktop files are stored under `file` = `<local folder>/<file name>` (`local_<id>/<session-id>.jsonl`,
    `local_<id>/audit.jsonl`): every audit log is named `audit.jsonl`, and the index needs the folder. Session files
    use Claude Code's keep rules. Which rules apply is decided by the line's shape: a line with `_audit_timestamp`
    or `session_id` is an audit line.
  - Audit lines kept: `user` and `assistant` lines with no `parent_tool_use_id` (not a subagent or tool's child),
    not `isReplay`, not `isSynthetic`, and not only tool results. `system`, `result`, `tool_use_summary` and
    `rate_limit_event` lines are left out. Kept audit lines get the same attachment cleanup, and are otherwise
    stored as written, `_audit_hmac` included. The HMAC is not checked: nothing here can verify it.
- A file that can't be read, or a line that isn't JSON, is skipped (the line still counts as read); one bad file
  never stops a sync.
- `inkvault sync` takes the same lock as `rescue`, syncs, then rebuilds search. `rescue` and the nightly run sync
  right after the Pieces export, so the backup that follows includes the sessions.

### 2. Index (`index.py`)

`messages` gets a `source` column (`pieces`, `claude_code`, `claude_desktop`, `codex`). Each session becomes one
conversation:

- `conversation_id`: `claude_code:<sessionId>`, `claude_desktop:<sessionId>` or `codex:<session id>`.
- `conversation_name`: `Claude Code · <project>: <title>`, likewise `Claude desktop ·` and `Codex ·`.
  The project is the last part of the session's working folder. For Claude desktop, the sandbox root
  (`/sessions/<random name>`) is taken off first, so a session at the root has no project part (the name is
  `Claude desktop · <title>`) and one below it uses that folder. The random name is never used. The title is the
  session's custom title or summary if it has one, else its first prompt (clipped to 80 characters).
- Roles are `USER` and `ASSISTANT`, like Pieces chats, so embeddings, digests and project detection treat them the
  same.
- Claude Code text: string content or `text` blocks only (no thinking, tool calls or tool results).
  `<system-reminder>` blocks are removed. Sidechain, `isMeta` and slash-command lines (`<command-…>`,
  `<local-command-…>`) are skipped.
- Claude desktop: lines are grouped by folder. Session-file lines form one conversation per `sessionId`. An audit
  line joins the folder's session with its `session_id` if there is one; otherwise the folder's session nearest to
  it in time (usually the only one). In a folder with no session file, audit lines form one conversation per
  `session_id`. An audit line is read as a Claude Code line with `timestamp` from `timestamp`, else
  `_audit_timestamp`. Session-file messages come first and win:
  an audit message is added only if its uuid isn't one of the session file's, and it doesn't repeat a session-file
  message of the same role and text within 30 seconds (the same prompt under a second uuid). The name comes from the
  session file (title, `cwd`) when there is one; a session known only from its audit log is named after its first
  prompt.
- Codex text: `response_item` messages are the turns (on 108 real sessions, CLI 0.101 to 0.162, every assistant
  reply was there). An `event_msg` user or agent message that repeats a response item (same role and text, written
  within 30 seconds of it, nothing of the other role between them, each item used once) is dropped; any other is
  added where it appears. A file mixing both shapes loses nothing, and a prompt repeated later, with or without a
  reply between, isn't taken for an echo. (No real session sampled had event conversation records.)
- Codex user text: the setup Codex puts in front of what the user typed is taken off, repeatedly, from the start of
  each input block. That covers an explicit list of wrapped blocks (`environment_context`, `user_instructions`,
  `recommended_plugins`, `external_codex_apps_open_page`, `in-app-browser-context`, `guardian_tool_descriptions`,
  `task-notification`, `turn_aborted`; tags may have hyphens and attributes) and AGENTS.md
  instructions (`# AGENTS.md instructions`, with or without `for <path>`, up to `</INSTRUCTIONS>` when that tag is
  there, else the whole block). Whatever follows the setup is kept. An answer to an in-app question
  (`<send_user_message_question_reply>…</send_user_message_question_reply>`) keeps the answer. Any other text,
  markup included, is the user's, including tags not on the list (`skill`, `command-name`,
  `external_codex_apps_writing_block_edits`). The list comes from a tag audit of 108 real sessions (Codex reviews,
  2026-10-08); a new setup tag is added when it is seen, not guessed from its name.
- Codex message ids are `codex:<session id>:<hash of role, time and text>:<occurrence>`, so the same session saved
  under two file names is indexed once, while a prompt repeated in one session is kept each time. A fork with its
  own session id stays its own conversation.
- Empty messages are skipped. A line that doesn't parse, or has no timestamp, never stops indexing (Codex's older
  files use the session's start time).

### 3. What changes elsewhere

- **MCP server:** instructions and the `chats` source mention Claude Code and Codex. `memory_stats` lists chat
  messages by source. Search output needs no change: the conversation name says where a hit came from.
- **Dashboard:** the Chat messages tile says which sources it counts. Active hours, highlights, projects and the
  timeline already read `messages`.
- **Digests:** already read `messages`, so days after Pieces get digests from coding sessions.
- **Status:** a line per source with sessions, messages and the last sync.

## Unchanged

The Pieces export, the backup, the topic tags, and every existing table and column (apart from the new `source`
column on `messages`).

## Edge cases

- Neither tool installed: sync reports "not found" for each source and returns "skipped" to the nightly run.
- A line still being written (no `\n` yet): left for the next sync.
- A file that isn't valid UTF-8: decoded with replacement characters.
- A session file copied to another machine with the same name: the same session, read once.
- A very large session: read line by line, never whole.

### Stored format

`meta.session_lines_format` records how kept lines are stored (`2`: attachment blocks omitted, compaction kept;
now `3`: omitted at any depth).
A vault from an earlier format is upgraded once, on the next sync, in one transaction. Stored lines are cleaned in
place, including those whose session file is gone. Files still on disk are read again from the start, which adds
the records an earlier format skipped. Backups made before the upgrade are not rewritten.

## Not yet

- Codex's progress commentary and final reply are both kept as assistant messages. None of 3,245 real assistant
  messages had a field telling them apart, so no distinction is claimed.
- `compacted` records are stored but not shown anywhere.
- The Claude desktop app's sessions on macOS and Linux: their location hasn't been seen yet.
- A prompt the audit log has under a new uuid more than 30 seconds from its session-file copy is indexed twice.
  Codex's recheck found 1 of 27 recovered prompts like this (47 seconds apart); the median gap between the two copies
  is about 3 seconds. Nothing in the records says whether it is a late copy or the user asking again, so both are kept
  rather than risk dropping a real prompt. Widening the window would swallow real repeats.
- No upgrade for desktop files stored under their bare file name (`<session-id>.jsonl`, the first desktop commit on
  the 0.2.0 branch): that version was never released, so no vault has them. Such a vault would show its desktop
  sessions twice in `status`; the index is unaffected (uuids are the same).
- The second audit `session_id` is read as the app's id for the folder's conversation, because 120 of 135 prompts
  under it repeat the session file. A folder with two distinct conversations hasn't been seen; nearest-in-time is the
  fallback there.

## Testing

Synthetic files under a temporary `CLAUDE_CONFIG_DIR` and `CODEX_HOME`, covering:

- Keep rules: tool results, attachments and bookkeeping are dropped; prompts, replies and titles are kept.
- Incremental sync: an append is read once, a partial last line waits, a rewritten file is re-read, a deleted file
  keeps its lines, and an archived Codex session isn't duplicated.
- Index: conversation names and roles, system-reminder stripping, sidechain, meta and command lines skipped, both
  Codex formats, and the `source` column.
- Claude desktop: its sessions become `claude_desktop` conversations; subagents are skipped; a folder listed twice is
  read once; on Windows, a session whose path is over 260 characters is read; a session at the sandbox root has no
  project part. Audit logs: an audit-only session, an audit-extra prompt next to a session file, a shared uuid, the
  same prompt under two uuids, an audit log under the app's own id joining its folder's session, two audit logs
  that don't collide, lines without `timestamp`, and child, tool,
  replay, synthetic and system lines left out.
- Nightly: the step list includes `sync`, before `backup`.
