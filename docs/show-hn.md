# Show HN draft

Submit at https://news.ycombinator.com/submit with the title and URL below and the text box left empty. Then
open the post and add the first comment right away. Stay around for 2-3 hours to answer.

Before posting: make the first paragraph's "a year of my own work" line true to your story.

## Title

Show HN: InkVault: rescue your Pieces memory to SQLite, search it over MCP

## URL

https://github.com/sandoreclegane/inkvault

## First comment

Pieces for Developers shut down on September 27. PiecesOS still runs in read-only mode, and your long-term memory
is still inside it: every capture, session summary, chat and saved snippet. Uninstall it and that's gone. I had a
year of my own work in there, so I built a way out.

InkVault copies everything PiecesOS will hand over into one SQLite file on your machine. It stores the raw records
exactly as Pieces returned them, so nothing is lost to my interpretation of their schema. One command:

    uvx --from git+https://github.com/sandoreclegane/inkvault inkvault rescue

Once your data is out:

- An MCP server, so Claude, Codex or any MCP client can search it. Keyword and semantic search merged, plus a
  timeline ("what was I doing in March?").
- A local dashboard of your year: active hours, when you work, how your projects connect, top apps and sites.
- Optional daily summaries written by a local model through Ollama. Pieces stopped writing them; this picks up
  where it left off.
- A nightly scheduled run (Task Scheduler, launchd, or systemd/cron) that pulls in new captures and keeps 7 dated
  backups.

No telemetry, no accounts, no cloud. The only downloads are a ~130 MB embedding model and a chart library.

Honest limits: PiecesOS hands records over at about 8 per second, so a year of captures (~100k) takes 3-4 hours.
Ctrl+C keeps everything saved so far, and re-running picks up where it stopped. I've tested it on Windows with a
120k-capture vault. macOS and Linux should work, but I'd like to hear from anyone who runs it there.

Next I'm working on keeping memory going after Pieces: capturing Claude and ChatGPT conversations, Ollama chats
and browser history into the same vault, so it becomes your own local memory layer rather than a one-time rescue.

MIT licensed. Happy to answer anything about the PiecesOS API, the export, or the MCP side.
