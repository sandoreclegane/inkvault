"""The `inkvault` command.

    inkvault rescue      export from PiecesOS, index, digest (if Ollama is running), build the dashboard
    inkvault export      copy everything out of PiecesOS into the vault (re-runnable, only adds)
    inkvault index       rebuild keyword + meaning search
    inkvault digest      write per-day digests with a local Ollama model
    inkvault dashboard   rebuild and open the Memory Atlas
    inkvault serve       run the MCP server (for Claude Code, Codex, Hermes, …)
    inkvault status      what's in the vault and where it lives
"""
import argparse
import os
import sqlite3
import sys
import webbrowser

from . import __version__

REPO = "git+https://github.com/sandoreclegane/inkvault"


def mcp_instructions():
    from . import paths
    env = f" -e INKVAULT_HOME={paths.home()}" if os.environ.get("INKVAULT_HOME") else ""
    return f"""
Connect it to your AI tools (they read the vault; nothing leaves this machine):

  Claude Code:
    claude mcp add inkvault --scope user{env} -- uvx --from {REPO} inkvault serve

  Codex (~/.codex/config.toml), Hermes and other MCP clients: run this command over stdio
    uvx --from {REPO} inkvault serve
"""


def open_dashboard(path):
    if path:
        print(f"opening {path}")
        webbrowser.open(path.as_uri())


def cmd_rescue(args):
    from . import dashboard, digest, export, index
    try:
        if not export.run():
            return 1
    except KeyboardInterrupt:
        return partial_rescue(args)
    index.build()
    if not args.no_digest:
        digest.run(model=args.model)
    path = dashboard.build()
    print(mcp_instructions())
    if not args.no_open:
        open_dashboard(path)
    return 0


def partial_rescue(args):
    """Ctrl+C during a long export: show what's saved so far instead of nothing."""
    from . import dashboard, index, paths
    print("\n\nStopped. Everything fetched so far is saved.")
    if not paths.vault_db().exists():
        return 130
    print("Building search and a dashboard from what you have so far (skipping digests)…\n", flush=True)
    index.build()
    path = dashboard.build()
    print("\nThis is a partial rescue. Run `inkvault rescue` again anytime to continue where it stopped.")
    if not args.no_open:
        open_dashboard(path)
    return 130  # the conventional exit code for "stopped with Ctrl+C"


def cmd_status(_args):
    from . import paths
    from .export import PiecesOS
    print(f"InkVault {__version__}\nhome: {paths.home()}")
    if paths.vault_db().exists():
        db = sqlite3.connect(f"file:{paths.vault_db()}?mode=ro", uri=True)
        meta = dict(db.execute("SELECT key, value FROM meta"))
        events = db.execute("SELECT COUNT(*), MIN(created), MAX(created) FROM events").fetchone()
        kinds = dict(db.execute("SELECT kind, COUNT(*) FROM raw_records GROUP BY kind"))
        db.close()
        print(f"vault: {paths.vault_db().stat().st_size / 1e6:,.0f} MB, last export {meta.get('last_export', '?')} "
              f"from PiecesOS {meta.get('pieces_version', '?')}")
        print(f"  {events[0]:,} captures ({(events[1] or '')[:10]} → {(events[2] or '')[:10]}), "
              f"{kinds.get('summary', 0):,} summaries, {kinds.get('message', 0):,} chat messages, "
              f"{kinds.get('asset', 0):,} snippets")
    else:
        print("vault: empty (run `inkvault rescue`)")
    print(f"search index: {'ready' if paths.search_db().exists() else 'not built'}; "
          f"digests: {'yes' if paths.digests_db().exists() else 'none'}; "
          f"dashboard: {paths.dashboard() if paths.dashboard().exists() else 'not built'}")
    pos, version = PiecesOS.find()
    print(f"PiecesOS: {'running ' + version + ' at ' + pos.base if pos else 'not reachable'}")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="inkvault", description="Rescue your Pieces memory and search it locally.")
    p.add_argument("--version", action="version", version=f"inkvault {__version__}")
    p.add_argument("--home", help="where to keep the vault (default: your app-data folder; or set INKVAULT_HOME)")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("rescue", help="export + index + digest + dashboard, in one go")
    r.add_argument("--no-digest", action="store_true", help="skip the local-model digests")
    r.add_argument("--no-open", action="store_true", help="don't open the dashboard when done")
    r.add_argument("--model", default=None, help="Ollama model for digests (default qwen3.5:4b)")
    sub.add_parser("export", help="copy everything out of PiecesOS into the vault")
    sub.add_parser("index", help="rebuild keyword + meaning search")
    d = sub.add_parser("digest", help="per-day digests with a local Ollama model")
    d.add_argument("--model", default=None, help="Ollama model (default qwen3.5:4b)")
    d.add_argument("--redo", action="store_true", help="rewrite every day (after changing model)")
    b = sub.add_parser("dashboard", help="rebuild and open the Memory Atlas")
    b.add_argument("--no-open", action="store_true")
    sub.add_parser("serve", help="run the MCP server over stdio")
    sub.add_parser("status", help="what's in the vault and where it lives")
    args = p.parse_args(argv)

    if args.home:
        os.environ["INKVAULT_HOME"] = args.home
    if getattr(args, "model", None) is None and hasattr(args, "model"):
        from .digest import DEFAULT_MODEL
        args.model = DEFAULT_MODEL
    if args.cmd != "serve":
        # Windows consoles default to a legacy code page; titles and digests are full of Unicode.
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="replace")

    try:
        return dispatch(args)
    except KeyboardInterrupt:  # e.g. a second Ctrl+C while the partial dashboard builds
        print("\nStopped. Your vault is saved; run the same command again to continue.")
        return 130


def dispatch(args):
    if args.cmd == "rescue":
        return cmd_rescue(args)
    if args.cmd == "export":
        from . import export
        return 0 if export.run() else 1
    if args.cmd == "index":
        from . import index
        return 0 if index.build() else 1
    if args.cmd == "digest":
        from . import digest
        return 0 if digest.run(model=args.model, redo=args.redo) else 1
    if args.cmd == "dashboard":
        from . import dashboard
        path = dashboard.build()
        if not args.no_open:
            open_dashboard(path)
        return 0 if path else 1
    if args.cmd == "serve":
        from . import server
        server.run()
        return 0
    if args.cmd == "status":
        return cmd_status(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
