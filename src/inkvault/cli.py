"""The `inkvault` command.

    inkvault rescue      export from PiecesOS, index, digest (if Ollama is running), build the dashboard
    inkvault export      copy everything out of PiecesOS into the vault (re-runnable, only adds)
    inkvault sync        copy new Claude Code and Codex sessions and browser history into the vault, then rebuild search
    inkvault browsers    choose which browser profiles are yours, and sites never to keep
    inkvault index       rebuild keyword + meaning search
    inkvault digest      write per-day digests with a local Ollama model
    inkvault dashboard   rebuild and open the Memory Atlas
    inkvault serve       run the MCP server (for Claude Code, Codex, Hermes, …)
    inkvault status      what's in the vault and where it lives
    inkvault schedule    refresh and back up the vault every night (`--off` to stop)
    inkvault nightly     one refresh + backup now (what the schedule runs)
"""
import argparse
import contextlib
import os
import sqlite3
import sys
import webbrowser

from . import __version__

from .schedule import REPO


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


@contextlib.contextmanager
def locked(purpose, nothing_done, required=False):
    """The nightly lock, for a command that writes the vault, search, vectors, digests or dashboard (spec §1, "One
    writer at a time"). Yields False, after saying so, when another process holds it. On a drive that can't lock
    files at all, a command that removes (required=True) refuses; the others go ahead unguarded, as rescue and the
    nightly run always have: no removal can run on such a drive, so there is none for them to race."""
    from . import nightly
    with contextlib.ExitStack() as stack:
        try:
            stack.enter_context(nightly.lock(purpose))
        except nightly.Busy:
            print(f"A nightly run, rescue or sync is in progress right now; {nothing_done} Try again when it's done "
                  "(see `inkvault status`).")
            yield False
            return
        except OSError as e:  # e.g. a drive without file locking
            if required:
                print(f"Couldn't take the lock that keeps this apart from the nightly run ({e}); {nothing_done} "
                      "Changing browser choices and removing need it.")
                yield False
                return
            print(f"Couldn't take the lock that keeps this apart from the nightly run ({e}); continuing without it.")
        yield True


def cmd_rescue(args):
    from . import browsers, nightly
    profiles, answers = ask_browsers()
    with contextlib.ExitStack() as stack:
        try:
            stack.enter_context(nightly.lock())  # the purpose defaults to "rescue"
        except nightly.Busy:
            print("A nightly run is in progress right now; try again when it's done (see `inkvault status`).")
            return 1
        except OSError as e:  # e.g. a drive without file locking: better to rescue unguarded than not at all
            print(f"Couldn't take the lock that keeps a rescue and the nightly run apart ({e}); continuing without it.")
        code = 1
        try:
            if answers:
                browsers.record_answers(profiles, answers)
            code = rescue(args)
        finally:
            # Back up while the lock is still held, so a nightly run can't start in between and the vault
            # (which just grew) is protected even if the rescue itself blew up.
            backed_up = backup_after_rescue(nightly)
        return code if backed_up else code or 1


def backup_after_rescue(nightly):
    try:
        nightly.backup()
        return True
    except Exception as e:  # noqa: BLE001 - say so and fail, whatever went wrong
        print(f"Backup failed: {type(e).__name__}: {e}")
        return False


def rescue(args):
    from . import dashboard, digest, export, index
    try:
        if not export.run():
            print("Claude Code and Codex sessions and browser history don't need PiecesOS: "
                  "`inkvault sync` copies them without PiecesOS.")
            return 1
    except KeyboardInterrupt:
        return partial_rescue(args)
    sync_sessions()
    sync_browsers()  # applies any removal that was stopped part-way, first
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


def sync_sessions():
    """Claude Code and Codex sessions: "ok", "none" (none on this computer) or "failed". A failure is reported and
    never stops a rescue or the browser sync."""
    from . import sources
    try:
        return "ok" if sources.sync() else "none"
    except Exception as e:  # noqa: BLE001
        print(f"Couldn't sync Claude Code / Codex sessions: {type(e).__name__}: {e}")
        return "failed"


def sync_browsers():
    """Browser history: history.sync()'s outcome, or "failed". A failure is reported and never stops anything else."""
    from . import history
    try:
        return history.sync().outcome
    except Exception as e:  # noqa: BLE001
        print(f"Couldn't sync browser history: {type(e).__name__}: {e}")
        return "failed"


def ask_browsers():
    """In a terminal, ask about profiles not chosen yet: (profiles, answers). Before any lock is taken, because it
    waits for a person; the caller records the answers once it holds the lock."""
    from . import browsers
    if not browsers.interactive():
        return [], {}
    profiles = browsers.find_profiles()
    return profiles, browsers.ask_about_new(profiles, browsers.load_choices())


def cmd_sync(_args):
    from . import browsers, dashboard, forget, index, paths
    profiles, answers = ask_browsers()
    with locked("sync", "nothing was synced.") as ok:
        if not ok:
            return 1
        # A removal stopped part-way is finished first, whatever else there is to do, and search is rebuilt after
        # it even when nothing is new.
        recovering = forget.pending() > 0 or paths.rebuild_marker().exists()
        try:
            forget.apply_pending()
        except Exception as e:  # noqa: BLE001
            print(f"Couldn't finish a removal that was interrupted ({type(e).__name__}: {e}); nothing was synced, and "
                  "search stays off until it's finished.")
            return 1
        if answers:
            browsers.record_answers(profiles, answers)
        sessions = sync_sessions()
        web = sync_browsers()
        if sessions == "none" and web == "nothing" and not recovering:
            print("Nothing to sync: no Claude Code or Codex sessions on this computer, and no browser profile chosen "
                  "(`inkvault browsers`).")
            return 1
        # Still holding the lock: a removal can't start between reading the vault and publishing search.
        built = index.build() if recovering or sessions == "ok" or web in ("ok", "partial") else False
        if recovering and built:
            dashboard.build()
            print("Finished a removal that had been interrupted.")
    return 0 if built and sessions != "failed" and web in ("ok", "nothing") else 1


def confirm(question):
    try:
        return input(question).strip().lower() in ("y", "yes")
    except EOFError:
        return False


def stored_profiles():
    """Profile keys with visits in the vault, and how many each."""
    from . import paths
    if not paths.vault_db().exists():
        return {}
    db = paths.connect_ro(paths.vault_db())
    try:
        return dict(db.execute("SELECT profile, COUNT(*) FROM browser_visits GROUP BY profile"))
    except sqlite3.OperationalError:  # never synced
        return {}
    finally:
        db.close()


def cmd_browsers(args):
    """Work out the change first, asking whatever needs a person. Then, holding the lock: record every removal (one
    transaction), save the choices merged into browsers.json as it is now, and finish the removals. Recording comes
    first, so a stop anywhere leaves either nothing changed or a recorded removal the next search build finishes."""
    from . import browsers, forget, paths
    profiles = browsers.find_profiles()
    found = {p.key: p for p in profiles}
    removals, keeps = [], None
    if args.skip_site or args.unskip_site:
        host = (args.skip_site or args.unskip_site).strip().lower()

        def change(choices):
            sites = choices["skip_sites"]
            if args.skip_site and host not in sites:
                sites.append(host)
            if args.unskip_site and host in sites:
                sites.remove(host)
        said = (f"{host} and its subdomains won't be kept." if args.skip_site
                else f"{host} is no longer skipped; visits to it are kept from the next sync on.")
        if args.skip_site:
            removals.append(("site", host))
    elif args.yes:
        p = found.get(args.yes)
        if not p:
            print(f"No browser profile {args.yes!r} on this computer. Run `inkvault browsers` to list them.")
            return 1

        def change(choices):
            browsers.set_choice(choices, p, True)
        said = f"{args.yes}: yes"
    elif args.no:
        key, stored = args.no, stored_profiles()
        if key not in found and key not in browsers.load_choices()["profiles"] and key not in stored:
            print(f"No browser profile {key!r} here or in the vault. Run `inkvault browsers` to list them.")
            return 1

        def change(choices):
            choices["profiles"][key] = {"choice": "no"}
        said = f"{key}: no"
        # --forget is always queued: what it matches is counted under the lock, after any sync that ran meanwhile.
        if args.forget or (browsers.interactive() and stored.get(key) and confirm(
                f"Also remove the {stored[key]:,} visits already copied from it? [y/N] ")):
            removals.append(("profile", key))
        else:
            keeps = key
    else:
        if not profiles:
            print("No supported browser found on this computer.")
            return 0
        current = browsers.load_choices()
        if not browsers.interactive():
            for p in profiles:
                print("  " + browsers.describe(p, current))
            print("Choose with `inkvault browsers --yes KEY` or `--no KEY`, or run `inkvault browsers` in a terminal.")
            return 0
        answers = browsers.ask(profiles, choices=current)
        if answers is None:
            return 0
        stored = stored_profiles()
        for p in profiles:
            if browsers.state(p, current)[0] == "yes" and not answers[p.key] and stored.get(p.key) and confirm(
                    f"{p.key}: also remove the {stored[p.key]:,} visits already copied from it? [y/N] "):
                removals.append(("profile", p.key))

        def change(choices):
            for p in profiles:
                browsers.set_choice(choices, p, answers[p.key])
        said = "Saved."
    with locked("browsers", "nothing was changed.", required=True) as ok:
        if not ok:
            return 1
        try:
            n = forget.request(removals)
        except Exception as e:  # noqa: BLE001
            print(f"Couldn't record the removal ({type(e).__name__}: {e}); nothing was changed."
                  + (" Search stays off until `inkvault index` runs." if paths.rebuild_marker().exists() else ""))
            return 1
        try:
            choices = browsers.load_choices()
            change(choices)
            browsers.save_choices(choices)
        except Exception as e:  # noqa: BLE001
            print(f"Couldn't save your choice ({type(e).__name__}: {e})."
                  + (" The removal is recorded: the next `inkvault sync`, `inkvault index` or nightly run finishes it "
                     "and saves the choice that goes with it." if n else ""))
            return 1
        print(said)
        if keeps and (left := stored_profiles().get(keeps)):
            print(f"{left:,} visits already copied from it stay in the vault; add --forget to remove them.")
        if not removals:
            return 0
        if not n:
            print("Nothing had been copied, so there was nothing to remove.")
            return 0
        try:
            forget.finish()
        except Exception as e:  # noqa: BLE001
            print(f"Removing didn't finish ({type(e).__name__}: {e}). The next `inkvault sync`, `inkvault index` or "
                  "nightly run finishes it, and search stays off until then.")
            return 1
    print(f"Removed {n:,} visits and rebuilt search and the dashboard. The nightly backups still hold them until "
          "they rotate out (7 nights).")
    return 0


def session_status(db, meta):
    """One line per synced source: sessions and stored lines."""
    from .sources import NAMES, file_counts
    try:
        rows = db.execute("SELECT f.source, group_concat(f.file, char(10)), (SELECT COUNT(*) FROM session_lines l "
                          "WHERE l.source = f.source) FROM session_files f GROUP BY f.source").fetchall()
    except sqlite3.OperationalError:
        return []  # never synced
    return [f"  {NAMES.get(source, source)}: {file_counts(files.split(chr(10)))} ({lines:,} lines kept), last sync "
            f"{meta.get('last_sync', '?')}" for source, files, lines in rows]


def cmd_status(_args):
    from . import paths
    from .export import PiecesOS
    print(f"InkVault {__version__}\nhome: {paths.home()}")
    if paths.vault_db().exists():
        try:  # a damaged vault must not hide the nightly and schedule lines below
            db = paths.connect_ro(paths.vault_db())
            try:
                meta = dict(db.execute("SELECT key, value FROM meta"))
                events = db.execute("SELECT COUNT(*), MIN(created), MAX(created) FROM events").fetchone()
                kinds = dict(db.execute("SELECT kind, COUNT(*) FROM raw_records GROUP BY kind"))
                sessions = session_status(db, meta)
                from . import history
                web = history.status_lines(db)
            finally:
                db.close()
            print(f"vault: {paths.vault_db().stat().st_size / 1e6:,.0f} MB, last export {meta.get('last_export', '?')}"
                  f"{' (partial; continues next run)' if meta.get('last_export_partial') == '1' else ''} "
                  f"from PiecesOS {meta.get('pieces_version', '?')}")
            print(f"  {events[0]:,} captures ({(events[1] or '')[:10]} → {(events[2] or '')[:10]}), "
                  f"{kinds.get('summary', 0):,} summaries, {kinds.get('message', 0):,} chat messages, "
                  f"{kinds.get('asset', 0):,} snippets")
            for line in sessions + web:
                print(line)
        except sqlite3.Error as e:
            print(f"vault: unreadable ({e}); the nightly backups, if any, are in {paths.backups_dir()}")
    else:
        print("vault: empty (run `inkvault rescue`)")
    if paths.rebuild_marker().exists():
        print("search: being rebuilt after something was removed; run `inkvault index`")
    print(f"search index: {'ready' if paths.search_db().exists() else 'not built'}; "
          f"digests: {'yes' if paths.digests_db().exists() else 'none'}; "
          f"dashboard: {paths.dashboard() if paths.dashboard().exists() else 'not built'}")
    pos, version = PiecesOS.find()
    print(f"PiecesOS: {'running ' + version + ' at ' + pos.base if pos else 'not reachable'}")
    from . import nightly, schedule
    print(f"nightly: {schedule.describe()}")
    if nightly.fallback_newer():
        print("  nightly.log couldn't be written; see nightly-fallback.log")
    if nightly.running():
        print("  running now")
    elif (last := nightly.last_run()) == nightly.LOG_UNREADABLE:
        print(f"  last run: {last[2]}")
    elif last:
        started, finished, result = last
        print(f"  last run {started}: {result if finished else 'started but never finished (see nightly.log)'}")
    return 0


def ports_arg(text):
    """argparse type for --pieces-ports: comma-separated port numbers."""
    try:
        ports = [int(part) for part in text.split(",")]
    except ValueError:
        ports = []
    if not ports or not all(1 <= n <= 65535 for n in ports):
        raise argparse.ArgumentTypeError(f"{text!r} isn't a port list; use numbers from 1 to 65535, "
                                         "comma-separated (for example 39300,1000)")
    return ",".join(map(str, ports))


def main(argv=None):
    p = argparse.ArgumentParser(prog="inkvault", description="Rescue your Pieces memory and search it locally.")
    p.add_argument("--version", action="version", version=f"inkvault {__version__}")
    p.add_argument("--home", help="where to keep the vault (default: your app-data folder; or set INKVAULT_HOME)")
    p.add_argument("--pieces-ports", type=ports_arg, help="PiecesOS port(s) to try, comma-separated (or set INKVAULT_PIECES_PORTS)")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("rescue", help="export + index + digest + dashboard, in one go")
    r.add_argument("--no-digest", action="store_true", help="skip the local-model digests")
    r.add_argument("--no-open", action="store_true", help="don't open the dashboard when done")
    r.add_argument("--model", default=None, help="Ollama model for digests (default qwen3.5:4b)")
    sub.add_parser("export", help="copy everything out of PiecesOS into the vault")
    sub.add_parser("sync", help="copy new Claude Code and Codex sessions and browser history into the vault, then rebuild search")
    br = sub.add_parser("browsers", help="choose which browser profiles are yours, and sites never to keep")
    one = br.add_mutually_exclusive_group()
    one.add_argument("--yes", metavar="KEY", help='copy this profile\'s history (a key from the list, e.g. "chrome/Default")')
    one.add_argument("--no", metavar="KEY", help="don't copy this profile's history")
    one.add_argument("--skip-site", metavar="HOST", help="never keep visits to this site or its subdomains (removes stored ones)")
    one.add_argument("--unskip-site", metavar="HOST", help="keep visits to this site again")
    br.add_argument("--forget", action="store_true", help="with --no: also remove the visits already copied")
    sub.add_parser("index", help="rebuild keyword + meaning search")
    d = sub.add_parser("digest", help="per-day digests with a local Ollama model")
    d.add_argument("--model", default=None, help="Ollama model (default qwen3.5:4b)")
    d.add_argument("--redo", action="store_true", help="rewrite every day (after changing model)")
    b = sub.add_parser("dashboard", help="rebuild and open the Memory Atlas")
    b.add_argument("--no-open", action="store_true")
    sub.add_parser("serve", help="run the MCP server over stdio")
    sub.add_parser("status", help="what's in the vault and where it lives")
    s = sub.add_parser("schedule", help="refresh and back up the vault every night")
    s.add_argument("--at", default="03:00", help="time of day, 24-hour (default 03:00)")
    w = s.add_mutually_exclusive_group()
    w.add_argument("--wake", dest="wake", action="store_const", const=True, default=None,
                   help="wake the computer for the run")
    w.add_argument("--no-wake", dest="wake", action="store_const", const=False,
                   help="don't wake it; run the next time it's awake")
    s.add_argument("--off", action="store_true", help="stop the nightly run (keeps the vault and backups)")
    sub.add_parser("nightly", help="one refresh + backup now (what the schedule runs)")
    args = p.parse_args(argv)

    if args.home:
        os.environ["INKVAULT_HOME"] = args.home
    if args.pieces_ports:
        os.environ["INKVAULT_PIECES_PORTS"] = args.pieces_ports
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
    if args.cmd == "sync":
        return cmd_sync(args)
    if args.cmd == "browsers":
        return cmd_browsers(args)
    if args.cmd == "index":
        from . import index
        with locked("index", "search wasn't rebuilt.") as ok:
            return (0 if index.build() else 1) if ok else 1
    if args.cmd == "digest":
        from . import digest
        with locked("digest", "no digests were written.") as ok:
            return (0 if digest.run(model=args.model, redo=args.redo) else 1) if ok else 1
    if args.cmd == "dashboard":
        from . import dashboard
        with locked("dashboard", "the dashboard wasn't rebuilt.") as ok:
            if not ok:
                return 1
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
    if args.cmd == "schedule":
        from . import schedule
        try:
            if args.off:
                schedule.disable()
            else:
                schedule.enable(args.at, args.wake, interactive=bool(sys.stdin and sys.stdin.isatty()))
        except schedule.ScheduleError as e:
            print(e)
            return 1
        return 0
    if args.cmd == "nightly":
        from . import nightly
        return nightly.run()
    return 1


if __name__ == "__main__":
    sys.exit(main())
