# Roadmap

## v0.2.0: keep your memory going after Pieces

v0.1 rescues what Pieces captured. v0.2 keeps capturing, from sources you already use, into the same vault, so
search, the timeline and the Memory Atlas cover all of it.

### Better use of what's already in the vault

- **Projects from Pieces' own topic tags.** Pieces tagged each summary with a topic (tip from Anthony at Pieces,
  Pieces Discord, 2026-09-29). Build the dashboard's projects from those tags instead of guessing from session
  titles, and write them to `themes.txt` so they can be edited. The tags are already saved in the vault
  (`raw_records` kind `tag`); nothing new to export.
- **Real people, not fragments.** PiecesOS keeps duplicate person records while its dedup runs. Count a person as
  real if they have an email or at least 100 active events (`workstream_events.indices` values >= 0); treat the
  rest as fragments. Same rule Pieces used internally (also from Anthony). The person records are already in the
  vault (`raw_records` kind `person`).

### New sources

Every source lands in the existing tables with a `source` field (pieces, claude, chatgpt, ollama, browser).
One `inkvault sync` pulls what's new from each, safe to stop and re-run like `export`.

- **Claude**: Claude Code conversation files on disk, and the claude.ai data export.
- **ChatGPT**: the data export's `conversations.json`.
- **Ollama**: Ollama doesn't seem to keep chat history itself; check, then capture through the chat app in use or
  by recording as chats happen.
- **Browser**: history from the local Chrome, Edge and Firefox databases. No extension needed.
- One more source was discussed and not yet recalled.

Open question: import exports now and then, or record continuously?

### Housekeeping

- README: ask people to open an issue when a rescue fails. InkVault has no telemetry, so issues are the only way
  to hear about it.
- Confirm where PiecesOS writes `.port.txt` on Linux (macOS: `~/Documents/com.pieces.os/production/Config/`).
- Test a full rescue on macOS before a wider launch (Show HN draft in `docs/show-hn.md`).

## Done

- v0.1.1: nightly schedule with backups, export time budget, status health checks.
- PiecesOS port read from its own `.port.txt` (tip from Anthony at Pieces).
