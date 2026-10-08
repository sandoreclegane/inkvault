# Review request for Codex: InkVault reading Codex session files

Hi Codex. InkVault (https://github.com/sandoreclegane/inkvault) is a local-only tool that saves a person's Pieces
memory and makes it searchable over MCP. Version 0.2.0 adds Claude Code and Codex sessions: InkVault copies the
session files into its own SQLite vault every night, so search, the timeline, the dashboard and the daily digests
cover them.

I built the Codex part from what I know of your rollout format, without any real Codex sessions to test on. You know
that format first-hand, and you're on the machine where the real sessions are. Please review **the Codex side**: is
InkVault reading your files correctly and completely?

## Where the code is

- Branch: `claude/gifted-tesla-xu2lcb`
- `src/inkvault/sources.py`: everything to read. The Codex parts are `codex_root`, `codex_files`, `keep_codex` and
  `codex_messages`.
- `docs/superpowers/specs/2026-10-08-coding-sessions-design.md`: the design, and what it assumes about your format.
- `tests/test_sources.py`: the Codex tests (`CODEX_NEW`, `test_codex_older_format`,
  `test_an_archived_codex_session_is_not_read_twice`) use files I wrote by hand to look like yours.

## What InkVault assumes about Codex

1. Sessions are in `$CODEX_HOME/sessions/**/*.jsonl` (default `~/.codex`) and `$CODEX_HOME/archived_sessions/`.
   InkVault keys each file by its name alone, so a session moved into `archived_sessions/` isn't read twice.
2. Current files have one `{timestamp, type, payload}` per line. InkVault **keeps**:
   - `session_meta` lines (it uses `payload.id`, `payload.timestamp` and `payload.cwd`)
   - `response_item` lines whose payload type is `message`
   - `event_msg` lines whose payload type is `user_message` or `agent_message`

   It **drops** everything else: `function_call`, `function_call_output`, `reasoning`, `token_count`,
   `turn_context`, and so on.
3. For the conversation, it prefers `event_msg` `user_message` and `agent_message` (`payload.message`), on the
   assumption that these are exactly what the person typed and what you answered. If a file has none, it falls back
   to `response_item` messages with role `user` or `assistant` (text from `input_text`, `output_text` and `text`
   blocks), and skips user text that starts with `<` (`<environment_context>`, `<user_instructions>`).
4. Older files have no wrapper: the first line is `{id, timestamp, instructions}`, then lines like
   `{"type": "message", "role": ..., "content": [...]}`, with no timestamp of their own. Each message gets the
   session's start time.
5. A file is read incrementally by byte offset, taking only complete lines. If the first line changes or the file
   gets shorter, InkVault assumes the file was rewritten and reads it again from the start. Codex only appends to a
   rollout file, never rewrites it in place.
6. Each session's name in search is `Codex · <last part of cwd>: <first user message, clipped to 80 characters>`.

## Questions

1. Which of the assumptions above are wrong or out of date? Please include real field names, and the Codex versions
   where the format changed.
2. Are `event_msg` `user_message` and `agent_message` the right record of the conversation? Do they ever leave out
   turns, or include injected context (AGENTS.md, environment context, images)? Is one `agent_message` per reply
   the norm, or can one turn have several?
3. What else should be kept, or dropped? For example: compaction summaries (`compacted`?), the session's title if
   one exists, or `turn_context`. Is anything kept likely to hold secrets? Tool output is dropped on purpose.
4. Resumed and forked sessions: do they write a new rollout file that repeats earlier lines? If so, how does
   InkVault tell the copy apart? Right now it would show up as a second conversation.
5. Is there a better title than the first user message?
6. Anything Windows-specific: paths, line endings, files locked while Codex has them open?

## Please run it on your real sessions

This writes only to a throwaway folder. Your sessions are read, never changed. It also reads Claude Code sessions,
if any are on this machine.

```powershell
git fetch origin
git switch claude/gifted-tesla-xu2lcb
uv run pytest -q
$env:INKVAULT_HOME = "$env:TEMP\inkvault-codex-review"
uv run inkvault sync
uv run inkvault status
uv run python -c "from inkvault import server; print(server.memory_stats())"
uv run python -c "from inkvault import server; print(server.search_memories('a word from a recent Codex chat', source='chats', limit=5))"
```

Then compare a few of your sessions with what InkVault indexed: `get_memory('<id from the search results>')`
shows a conversation (up to 20,000 characters by default; pass `max_chars` for more). Look for missing turns, duplicated turns, injected context showing up as the user's
words, and wrong timestamps. Please quote only short excerpts in your reply; these are the user's private
sessions. When you're done, delete `%TEMP%\inkvault-codex-review`.

## How to reply

For each finding, give:
- the file and line
- what's wrong, with a real (redacted) example line from a rollout file if you can
- the fix you suggest

Please don't push to the branch. Send the findings back, and the fixes will be made on the branch with tests that
use your example lines.
