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
  without another sync. One exception: image, document and tool-result blocks inside a kept line's message content
  are replaced by `{"type": ..., "omitted": true}`, and that line is stored re-serialized. Pasted images arrive as
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
- A file that can't be read, or a line that isn't JSON, is skipped (the line still counts as read); one bad file
  never stops a sync.
- `inkvault sync` takes the same lock as `rescue`, syncs, then rebuilds search. `rescue` and the nightly run sync
  right after the Pieces export, so the backup that follows includes the sessions.

### 2. Index (`index.py`)

`messages` gets a `source` column (`pieces`, `claude_code`, `codex`). Each session becomes one conversation:

- `conversation_id`: `claude_code:<sessionId>` or `codex:<session id>`.
- `conversation_name`: `Claude Code · <project>: <title>` or `Codex · <project>: <title>`. The project is the last
  part of the session's working folder. The title is the session's custom title or summary if it has one, else its
  first prompt (clipped to 80 characters).
- Roles are `USER` and `ASSISTANT`, like Pieces chats, so embeddings, digests and project detection treat them the
  same.
- Claude Code text: string content or `text` blocks only (no thinking, tool calls or tool results).
  `<system-reminder>` blocks are removed. Sidechain, `isMeta` and slash-command lines (`<command-…>`,
  `<local-command-…>`) are skipped.
- Codex text: `response_item` messages are the turns (on 108 real sessions, CLI 0.101 to 0.162, every assistant
  reply was there). An `event_msg` user or agent message with no matching response item (same role and text, each
  match used once) is added where it appears, so a file mixing both shapes loses nothing.
- Codex user text: whole input blocks Codex injects are dropped: `<environment_context>…</environment_context>`,
  `<user_instructions>…</user_instructions>`, and `# AGENTS.md instructions for …` followed by
  `<INSTRUCTIONS>…</INSTRUCTIONS>`. An answer to an in-app question
  (`<send_user_message_question_reply>…</send_user_message_question_reply>`) keeps the answer. Any other text,
  markup included, is the user's.
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

## Not yet

- Codex's progress commentary and final reply are both kept as assistant messages; telling them apart needs the
  field that marks the channel.
- `compacted` records are stored but not shown anywhere.

## Testing

Synthetic files under a temporary `CLAUDE_CONFIG_DIR` and `CODEX_HOME`, covering:

- Keep rules: tool results, attachments and bookkeeping are dropped; prompts, replies and titles are kept.
- Incremental sync: an append is read once, a partial last line waits, a rewritten file is re-read, a deleted file
  keeps its lines, and an archived Codex session isn't duplicated.
- Index: conversation names and roles, system-reminder stripping, sidechain, meta and command lines skipped, both
  Codex formats, and the `source` column.
- Nightly: the step list includes `sync`, before `backup`.
