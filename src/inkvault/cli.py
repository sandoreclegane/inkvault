"""The `inkvault` command.

    inkvault rescue      export from PiecesOS, index, digest (if Ollama is running), build the dashboard
    inkvault export      copy everything out of PiecesOS into the vault (re-runnable, only adds)
    inkvault sync        copy new Claude Code and Codex sessions and browser history into the vault, then rebuild search
    inkvault browsers    choose which browser profiles are yours, and sites never to keep
    inkvault index       rebuild keyword + meaning search
    inkvault digest      write per-day digests with a local Ollama model
    inkvault dashboard   open the Memory Atlas (search and charts), on this computer only
    inkvault serve       run the MCP server (for Claude Code, Codex, Hermes, …)
    inkvault status      what's in the vault and where it lives
    inkvault schedule    refresh and back up the vault every night (`--off` to stop)
    inkvault nightly     one refresh + backup now (what the schedule runs)
"""
import argparse
import contextlib
import io
import json
import os
import sqlite3
import sys

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
    if path and not args.no_open:
        from . import atlasserve
        atlasserve.start_detached()  # the rescue holds the nightly lock; the helper must not run inside it
        print("Opening the Memory Atlas in your browser.")
    if sys.platform == "win32":
        print("Tip: `inkvault dashboard --shortcut` adds an InkVault Atlas icon.")
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
    if path and not args.no_open:
        from . import atlasserve
        atlasserve.start_detached()  # the rescue holds the nightly lock; the helper must not run inside it
        print("Opening the Memory Atlas in your browser.")
    return 130  # the conventional exit code for "stopped with Ctrl+C"


def sync_sessions():
    """Claude Code and Codex sessions: (status, per-source results). Status is "ok", "partial" (some session file
    couldn't be read), "none" (none on this computer) or "failed". A failure is reported and never stops a rescue
    or the browser sync."""
    from . import sources
    try:
        results = sources.sync()
    except Exception as e:  # noqa: BLE001
        print(f"Couldn't sync Claude Code / Codex sessions: {type(e).__name__}: {e}")
        return "failed", []
    if not sources.any_found(results):
        return "none", results
    return ("partial" if any(r.unreadable for r in results if r.found) else "ok"), results


def sync_browsers_result():
    """Browser history: history.sync()'s full SyncResult, or None if it failed outright (reported, and never stops
    anything else)."""
    from . import history
    try:
        return history.sync()
    except Exception as e:  # noqa: BLE001
        print(f"Couldn't sync browser history: {type(e).__name__}: {e}")
        return None


def sync_browsers():
    """Browser history: history.sync()'s outcome, or "failed"."""
    result = sync_browsers_result()
    return "failed" if result is None else result.outcome


def ask_browsers(term=None):
    """In a terminal, ask about profiles not chosen yet: (profiles, answers). Before any lock is taken, because it
    waits for a person; the caller records the answers once it holds the lock."""
    from . import browsers
    if not browsers.interactive():
        return [], {}
    profiles = browsers.find_profiles()
    return profiles, browsers.ask_about_new(profiles, browsers.load_choices(), term=term)


# Every fixed string the first-run output can emit (the counts between them are built from numbers). A test writes
# each through strict ascii and cp1252 streams.
HELLO_TAGLINE = "your memory, on this computer · nothing is uploaded"
HELLO_LINE = ("InkVault keeps a private copy of your AI sessions and chosen browser history on this computer. "
              "Nothing is uploaded.")
SAVING = "Saving…"
READY = "Your vault is ready."
OPENING = "Your vault is ready. Opening the Memory Atlas…"
OPEN_FAILED = "Couldn't open the Atlas; open it with: inkvault dashboard"
OPEN_WITH = "Open it with: inkvault dashboard"
NEXT_TIME = "Next time: inkvault sync · skip the browser: --no-open"
RECOVERED = "Finished a removal that had been interrupted."
FIRST_RUN_MESSAGES = (
    "◆ InkVault  " + HELLO_TAGLINE, HELLO_LINE, "Which browser profiles are yours?",
    "Numbers separated by spaces, all, or none: ", SAVING, "Browser history", "Search", "up to date",
    "conversations", "with new messages", "visits", "new", "profiles", "files couldn't be read", READY, OPENING,
    OPEN_FAILED, OPEN_WITH, NEXT_TIME, RECOVERED, "Building search: keyword index…",
    "Building search: meaning search…", "✓ ! · … — ━ ╺ ⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏",
)


def say(term, text):
    """Write one line of the new output; on an ASCII-only console the few non-ASCII characters become ASCII."""
    if term.ascii:
        text = text.replace("…", term.ell).replace("·", term.dot).replace("—", "-").replace("◆", term.mark)
        text = "".join(c if ord(c) < 128 else "?" for c in text)
    term.write(text)


@contextlib.contextmanager
def captured():
    """Hold back what a step prints. If the step raises, what it had printed is shown before the error goes on."""
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer):
            yield buffer
    except BaseException:
        sys.stdout.write(buffer.getvalue())
        raise


def plural(n, one, many=None):
    return f"{n:,} {one if n == 1 else many or one + 's'}"


def cmd_sync(args):
    from .term import Term
    term = Term.detect()
    if not term.new_output:
        return plain_sync()
    return terminal_sync(args, term)


def plain_sync():
    """`sync` when output isn't a terminal (a script, a pipe, a log): exactly what it has always printed."""
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
        sessions = sync_sessions()[0]
        web = sync_browsers()
        if sessions == "none" and web == "nothing" and not recovering:
            print("Nothing to sync: no Claude Code or Codex sessions on this computer, and no browser profile chosen "
                  "(`inkvault browsers`).")
            return 1
        # Still holding the lock: a removal can't start between reading the vault and publishing search.
        built = index.build() if recovering or sessions in ("ok", "partial") or web in ("ok", "partial") else False
        if recovering and built:
            dashboard.build()
            print("Finished a removal that had been interrupted.")
    return 0 if built and sessions in ("ok", "none") and web in ("ok", "nothing") else 1


def chosen_profile_keys():
    from . import browsers
    choices = browsers.load_choices()
    return [p.key for p in browsers.find_profiles() if browsers.state(p, choices)[0] == "yes"]


def step_notes(buffer):
    """What a step printed, as lines (for a step that failed outright)."""
    return [l.strip() for l in buffer.getvalue().splitlines() if l.strip()]


def terminal_sync(args, term):
    """`sync` in a terminal: a hello, progress while search is built, a summary, and the Atlas opening."""
    from . import browsers, dashboard, forget, index, paths, summary
    from .history import waiting_line
    from .term import Progress
    say(term, f"{term.gold(term.mark)} {term.bold('InkVault')}  {term.dim(HELLO_TAGLINE)}")
    say(term, term.dim(HELLO_LINE))
    term.write("")
    profiles, answers = ask_browsers(term)
    with locked("sync", "nothing was synced.") as ok:
        if not ok:
            return 1
        recovering = forget.pending() > 0 or paths.rebuild_marker().exists()
        try:
            forget.apply_pending()
        except Exception as e:  # noqa: BLE001
            print(f"Couldn't finish a removal that was interrupted ({type(e).__name__}: {e}); nothing was synced, and "
                  "search stays off until it's finished.")
            return 1
        if answers:
            browsers.record_answers(profiles, answers)
        before = summary.conversation_ids()
        with captured() as sessions_out:
            sessions, results = sync_sessions()
        with captured() as web_out:
            web_result = sync_browsers_result()
        web = "failed" if web_result is None else web_result.outcome
        if sessions == "none" and web == "nothing" and not recovering:
            print("Nothing to sync: no Claude Code or Codex sessions on this computer, and no browser profile chosen "
                  "(`inkvault browsers`).")
            term.write("")
            say(term, OPEN_WITH)
            return 1
        say(term, term.bold(SAVING))
        built, search_error = False, None
        if recovering or sessions in ("ok", "partial") or web in ("ok", "partial"):
            try:
                with Progress(term) as progress, captured() as search_out:
                    built = index.build(progress)
                    if recovering and built:
                        dashboard.build()
            except Exception as e:  # noqa: BLE001
                search_error = f"{type(e).__name__}: {e}"
            else:
                if not built:
                    said = step_notes(search_out)
                    search_error = said[-1] if said else "search wasn't built"
        else:
            search_error = "not built: nothing was saved"
        after = summary.conversation_ids()
        session_lines = summary.session_lines(before, after, results)
        keys = chosen_profile_keys() if web_result and web_result.chosen else []
        total_visits = summary.visit_total(keys)
    # The lock is released: everything below only reads what was captured above.
    width = max([len(l.name) for l in session_lines] + [len("Browser history"), len("Search")])
    unreadable = {r.source: r.unreadable for r in results}

    def row(good, name, body):
        glyph = term.teal(term.ok) if good else term.amber(term.warn)
        say(term, f"  {glyph} {name:<{width}}  {body}")

    for line in session_lines:
        bad = unreadable.get(line.source, 0)
        body = f"{term.bold(plural(line.conversations, 'conversation'))}   {term.teal(str(line.with_new))} with new messages"
        if bad:
            body += term.amber(f"   {plural(bad, 'file')} couldn't be read")
        row(not bad, line.name, body)
    if sessions == "failed":
        row(False, "Sessions", "couldn't be read")
        for note in step_notes(sessions_out) or ["see `inkvault status`"]:
            say(term, term.dim(f"      {note}"))
    if web_result is None:
        row(False, "Browser history", "couldn't be read")
        for note in step_notes(web_out) or ["see `inkvault status`"]:
            say(term, term.dim(f"      {note}"))
    elif web != "nothing":
        added = term.teal("+" + format(web_result.new_visits, ",") + " new")
        body = f"{term.bold(plural(total_visits, 'visit'))}   {added} ({plural(web_result.chosen, 'profile')})"
        if web_result.failed:
            body += term.amber(f"   couldn't read: {', '.join(web_result.failed)}")
        row(web == "ok", "Browser history", body)
    if web_result is not None and web_result.waiting:
        say(term, term.dim(f"      {waiting_line(web_result.waiting)}"))
    if search_error:
        row(False, "Search", search_error)
    else:
        row(True, "Search", "up to date")
    if recovering and built:
        say(term, RECOVERED)
    success = built and sessions in ("ok", "none") and web in ("ok", "nothing")
    term.write("")
    if success and term.interactive and not getattr(args, "no_open", False):
        from . import atlasserve
        say(term, term.teal(READY) + OPENING[len(READY):])
        try:
            atlasserve.start_detached()
        except Exception:  # noqa: BLE001  (a cosmetic launch problem never changes the exit code)
            say(term, OPEN_FAILED)
        else:
            say(term, term.dim(NEXT_TIME))
    else:
        if success:
            say(term, term.teal(READY))
        say(term, OPEN_WITH)
    return 0 if success else 1


def confirm(question):
    try:
        return input(question).strip().lower() in ("y", "yes")
    except EOFError:
        return False


def fmt_span(lo, hi):
    from .times import local
    a, b = local(lo), local(hi)
    return f"{a:%Y-%m-%d %H:%M}–{b:%H:%M}" if a.date() == b.date() else f"{a:%Y-%m-%d %H:%M} – {b:%Y-%m-%d %H:%M}"


def cmd_forget(args):
    from . import browsers, forget, screen
    if args.until and not args.since:
        print("--until only goes with --since (a time range); --app removes everything of that app. "
              "Nothing was removed.")
        return 1
    if args.app:
        what, value, asked = "screen-app", args.app, None
    else:
        if not args.until:
            print("--since needs --until.")
            return 1
        try:
            s, u = screen.local_bounds(args.since, args.until)
        except ValueError as e:
            print(f"Bad range: {e}")
            return 1
        what, value, asked = "screen-range", json.dumps({"since": s, "until": u}), (s, u)
    n, lo, hi = forget.screen_preview(what, value)
    if not n:
        print("Nothing recorded matches; nothing was removed.")
        return 0
    plural = "es" if n != 1 else ""
    if asked:
        print(f"Range: {fmt_span(*asked)} (local time).")
    if not args.yes and not (browsers.interactive() and confirm(
            f"Remove {n:,} stretch{plural} of screen capture ({fmt_span(lo, hi)}) and their text? [y/N] ")):
        print("Nothing was removed.")
        return 1
    try:
        with locked("forget", "nothing was removed.", required=True) as ok:
            if not ok:
                return 1
            n, lo, hi = forget.screen_request(what, value)
            if not n:
                print("Nothing recorded matches any more; nothing was removed.")
                return 0
            wider = asked and (lo < asked[0] or hi > asked[1])
            print(f"Removing {n:,} stretch{'es' if n != 1 else ''}, {fmt_span(lo, hi)}"
                  + (" (wider than the range you asked for: whole windows are removed)" if wider else "") + ".")
            try:
                forget.finish()
            except Exception as e:  # noqa: BLE001
                print(f"Removing didn't finish ({type(e).__name__}). The next `inkvault index` or nightly run "
                      "finishes it, and search stays off until then.")
                return 1
    except Exception as e:  # noqa: BLE001
        print(f"Couldn't remove screen capture ({type(e).__name__}); nothing more was done. "
              "Run the same command again.")
        return 1
    print("Backups made before now, and answers already given to an AI client, still have it.")
    if what == "screen-app":
        print(f"Screen capture still records {args.app}: to stop, `inkvault watch --never-app {args.app}`.")
    return 0


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
            print(f"  {events[0]:,} Pieces captures ({(events[1] or '')[:10]} → {(events[2] or '')[:10]}), "
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
          f"digests: {'yes' if paths.digests_db().exists() else 'none'}")
    try:
        data = json.loads(paths.dashboard().read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("not an Atlas file")
        built = data.get("built", "?")
        print(f"atlas: built {built} (open it with `inkvault dashboard`)")
    except (OSError, ValueError):
        print("atlas: not built yet (`inkvault dashboard` builds it)")
    from . import watch
    for line in watch.status_lines():
        print(line)
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
    sy = sub.add_parser("sync", help="copy new Claude Code and Codex sessions and browser history into the vault, then rebuild search")
    sy.add_argument("--no-open", action="store_true", help="don't open the Memory Atlas when done")
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
    b = sub.add_parser("dashboard", help="open the Memory Atlas (a live page, on this computer only)")
    b.add_argument("--no-open", action="store_true", help="start it without opening the browser")
    b.add_argument("--shortcut", action="store_true", help="Windows: add an InkVault Atlas icon to the desktop and Start menu")
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
    wp = sub.add_parser("watch", help="screen capture: which window you're in, and text from the apps you allow")
    wg = wp.add_mutually_exclusive_group()
    wg.add_argument("--apps", action="store_true", help="show which apps get text capture and which are never captured")
    wg.add_argument("--add-app", metavar="EXE", help="read text in this app (for example Discord.exe)")
    wg.add_argument("--remove-app", metavar="EXE", help="back to timeline-only for this app")
    wg.add_argument("--never-app", metavar="EXE", help="never record this app, not even in the timeline")
    wg.add_argument("--never-title", metavar="TEXT", help="skip windows whose title contains this text")
    wg.add_argument("--pause", metavar="FOR", help="pause capture, for example 1h or 30m")
    wg.add_argument("--resume", action="store_true", help="end a pause")
    wg.add_argument("--stop", action="store_true", help="stop the running watcher")
    wg.add_argument("--install", action="store_true", help="start screen capture at every sign-in")
    wg.add_argument("--uninstall", action="store_true", help="stop screen capture and remove the sign-in task")
    wg.add_argument("--reset-settings", action="store_true", help="restore the default capture settings")
    f = sub.add_parser("forget", help="remove screen capture by app or time range")
    f.add_argument("--screen", action="store_true", required=True)
    fg = f.add_mutually_exclusive_group(required=True)
    fg.add_argument("--app", metavar="EXE")
    fg.add_argument("--since", metavar="TIME", help="local time or date (or with an offset, e.g. Z)")
    f.add_argument("--until", metavar="TIME")
    f.add_argument("--yes", action="store_true", help="don't ask")
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
        from . import atlasserve
        if args.shortcut:
            return atlasserve.make_shortcut()
        return atlasserve.launch(open_browser=not args.no_open)
    if args.cmd == "serve":
        from . import server
        server.run()
        return 0
    if args.cmd == "watch":
        from . import watch
        return watch.cmd(args)
    if args.cmd == "forget":
        return cmd_forget(args)
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
