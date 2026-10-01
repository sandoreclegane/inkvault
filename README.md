# InkVault

**Rescue your Pieces memory before it's gone, then search it from Claude, Codex, or any MCP client, and see it as a map of your year.**

Pieces for Developers shut down on September 27, 2026. PiecesOS still runs in read-only mode on your computer, and
your long-term memory is still inside it: every capture, session summary, chat and saved snippet. Once you
uninstall PiecesOS, it's gone.

InkVault copies all of it into a single file on your machine, makes it searchable by your AI tools, and draws it
as a private dashboard. Nothing is uploaded anywhere.

```bash
uvx --from git+https://github.com/sandoreclegane/inkvault inkvault rescue
```

That's the whole thing. It needs [uv](https://docs.astral.sh/uv/getting-started/installation/) and PiecesOS running.

**It takes a while.** PiecesOS hands records over at about 8 per second, so a year of captures (~100,000) takes
around 3-4 hours. Leave it running, or press **Ctrl+C** anytime: InkVault keeps everything saved so far and opens
a dashboard of what you have. Run the same command again later and it picks up where it left off.

## What you get

- **The vault**: everything PiecesOS will hand over (captures, summaries and their text, chats, snippets, tags,
  websites, people), saved as the raw records Pieces returned, so nothing is lost to interpretation. One SQLite file.
- **Search for your AI tools**: an MCP server with `search_memories` (keyword + meaning, merged), `timeline`
  ("what was I doing in March?"), `get_memory`, and `memory_stats`.
- **Memory Atlas**: a dashboard of your year. Active hours per day (hover a day for what it was about), when you work,
  how your projects connect, projects over time, top apps and sites. Every chart has a table view.
- **Daily digests** (optional): a 2-3 sentence summary of each day, written by a *local* model through
  [Ollama](https://ollama.com). Pieces stopped writing summaries; this picks up where it left off.

## Connect your AI tools

**Claude Code**

```bash
claude mcp add inkvault --scope user -- uvx --from git+https://github.com/sandoreclegane/inkvault inkvault serve
```

**Codex** (`~/.codex/config.toml`)

```toml
[mcp_servers.inkvault]
command = "uvx"
args = ["--from", "git+https://github.com/sandoreclegane/inkvault", "inkvault", "serve"]
```

**Hermes, Claude Desktop, and other MCP clients**: run `uvx --from git+https://github.com/sandoreclegane/inkvault inkvault serve` over stdio.

Then ask: *"What was I working on the week of March 10?"*, *"Find that retry helper I saved"*, *"When did I last look at the Stripe dashboard?"*

## Keep it up to date

PiecesOS keeps capturing for as long as it runs on your computer. To pull in what's new every night and back up
your vault:

```bash
uvx --from git+https://github.com/sandoreclegane/inkvault inkvault schedule
```

This installs InkVault as a permanent `inkvault` command (with `uv tool install`, or updates an older one) and sets
up a nightly run at 03:00 with your system's own scheduler: Task Scheduler on Windows, launchd on macOS, a systemd
user timer (or cron) on Linux. If uv's tool folder isn't on your PATH, it tells you to run `uv tool update-shell`.

Each run exports new captures (if PiecesOS is running) and saves a dated copy of `vault.db` in `backups/` right
after (the last 7 are kept). Then it rebuilds search, writes new digests (if Ollama is running) and rebuilds the
dashboard. One step failing never stops the others. The computer is kept awake while it runs (Windows and macOS).
`rescue` won't start while a nightly run is going; it tells you to try again. See how it went with
`inkvault status`, or read `nightly.log` in your InkVault folder.

The schedule is tied to your vault folder, so if you rescued with `--home`, run `schedule` with the same `--home`.

If the computer is asleep or off at run time:

- **Windows** and **systemd** catch up when the computer is next on.
- **macOS** catches up after sleep, but not after a power-off.
- **cron** doesn't catch up.

The Windows task runs only while you're logged in. You can have the computer wake for the run:

- **Windows**: `schedule` asks. Your power plan must allow wake timers, and some laptops (Modern Standby) ignore them.
- **macOS**: `schedule` asks, then prints a one-time `sudo pmset repeat ...` command for you to run. It replaces any
  `pmset repeat` schedule you already have.
- **Linux**: a user timer can't wake the computer. systemd runs the timer while you're logged in;
  `loginctl enable-linger $USER` keeps it going always.

`inkvault schedule --at 02:30` picks another time, `--wake` or `--no-wake` skips the question, `--off` turns it off,
and `uv tool upgrade inkvault` updates InkVault.

## Commands

| Command | Does |
|---|---|
| `inkvault rescue` | Export, index, digest (if Ollama is running), build and open the dashboard |
| `inkvault export` | Copy everything out of PiecesOS. Safe to stop and re-run: it only adds what's new |
| `inkvault index` | Rebuild search |
| `inkvault digest [--model M] [--redo]` | Daily digests with a local model (default `qwen3.5:4b`) |
| `inkvault dashboard` | Rebuild and open the Memory Atlas |
| `inkvault serve` | Run the MCP server |
| `inkvault status` | What's in your vault and where it lives, plus the schedule and last nightly run |
| `inkvault schedule [--at HH:MM] [--wake\|--no-wake] [--off]` | Refresh and back up the vault every night |
| `inkvault nightly` | One refresh + backup now (what the schedule runs) |

## Privacy

This is your screen history, your chats and your code. InkVault is built so that none of it leaves your machine:

- Everything is stored in your app-data folder (`%LOCALAPPDATA%\InkVault`, `~/Library/Application Support/InkVault`,
  or `~/.local/share/inkvault`), or wherever `INKVAULT_HOME` points. Never inside the code folder.
- No telemetry, no accounts, no cloud.
- Two things are downloaded, and nothing of yours is uploaded: the search model (~130 MB from Hugging Face, once)
  and the dashboard's chart library (from cdnjs, when you open the page).
- Digests use Ollama on your own machine. Skip them with `--no-digest`.
- Captured text was written by other people and apps. The MCP server tells your AI tool to treat it as data, not
  instructions.

**Back up your vault.** It is one file: `vault.db` in your InkVault folder. Everything else can be rebuilt from it
with `inkvault index`.

## Good to know

- **Your projects** on the dashboard are detected from recurring phrases in your session titles. To choose your own,
  create `themes.txt` in your InkVault folder with lines like `Website = website|landing page|stripe` and run
  `inkvault dashboard`.
- **PiecesOS port**: InkVault reads the port PiecesOS saved in its own config (`.port.txt`), then the port that worked
  last time, then tries 39300 and 1000. To force a port, set `INKVAULT_PIECES_PORTS` or pass `--pieces-ports`
  (for the nightly run: `inkvault --pieces-ports 39301 schedule`).
- **Pieces' own export tool**: Pieces said they'd send one. Use it too; two copies are better than one. Support
  for importing its format into InkVault is planned.
- Tested on Windows with PiecesOS 12.6.2 and a vault of 120,000 captures. macOS and Linux should work;
  reports are welcome.

## Not affiliated with Pieces

InkVault is an independent, community tool for people moving on from Pieces. "Pieces" is a trademark of its owner.

MIT licensed. Made by [T. Matthew Chase](https://github.com/sandoreclegane) / Logos 7.
