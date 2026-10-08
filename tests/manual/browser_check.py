"""A read-only check of browser sync against this PC's real history. Run it by hand, never from pytest:

    uv run python tests/manual/browser_check.py

It copies only the History files named in ALLOWED (and their journal or WAL) into a fresh folder under the system
temp folder, points InkVault at those copies alone, syncs and indexes them there, prints counts only, and deletes
the folder however it ends. No browser folder is ever listed, so a profile that isn't named is never looked at.
Claude Code and Codex sessions aren't read, nothing asks a question, and nothing outside the temp folder is written.
Exit code: 0 the check ran and its folder is gone; 1 the check failed (the folder is gone); 2 the folder couldn't be
deleted, so copies of real history are still on disk (the message says where).
"""
import contextlib
import os
import shutil
import sqlite3
import sys
import tempfile
from collections import Counter
from pathlib import Path

LOCAL = Path(os.environ.get("LOCALAPPDATA", ""))
# The profiles the user said are theirs. chrome/Profile 2 belongs to someone else: never add it.
ALLOWED = {
    ("chrome", "Default"): LOCAL / "Google/Chrome/User Data/Default/History",
    ("chrome", "Profile 1"): LOCAL / "Google/Chrome/User Data/Profile 1/History",
    ("chrome", "Profile 5"): LOCAL / "Google/Chrome/User Data/Profile 5/History",
    ("comet", "Default"): LOCAL / "Perplexity/Comet/User Data/Default/History",
    ("edge", "Default"): LOCAL / "Microsoft/Edge/User Data/Default/History",
}
assert ("chrome", "Profile 2") not in ALLOWED


def copy_profiles(allowed, base):
    """Copy each named History file (and its sidecars) to base/browsers/<browser>/<folder>/History."""
    roots = {}
    for (browser, folder), src in allowed.items():
        if not src.is_file():
            print(f"{browser}/{folder}: not found, skipped")
            continue
        dst = base / "browsers" / browser / folder
        dst.mkdir(parents=True)
        for suffix in ("", "-journal", "-wal"):
            side = Path(str(src) + suffix)
            if side.is_file():
                shutil.copyfile(side, Path(str(dst / "History") + suffix))
        roots[browser] = base / "browsers" / browser
    return roots


def report(result, paths):
    """Counts only. Every connection is closed however this ends, so the folder can be deleted on Windows."""
    with contextlib.ExitStack() as stack:
        vault = sqlite3.connect(paths.vault_db())
        stack.callback(vault.close)
        search = sqlite3.connect(paths.search_db())
        stack.callback(search.close)
        print(f"\nsync: {result.outcome}, {result.chosen} profiles, failed: {result.failed or 'none'}")
        for profile, n, synced, cut in vault.execute(
                "SELECT profile, COUNT(*), SUM(origin IS NOT NULL), SUM(instr(url, '…') > 0) "
                "FROM browser_visits GROUP BY profile ORDER BY profile"):
            counted = search.execute("SELECT COUNT(*) FROM visits WHERE profile=?", (profile,)).fetchone()[0]
            print(f"{profile}: {n:,} stored, {counted:,} counted, {synced or 0:,} synced from other devices, "
                  f"{cut or 0:,} with a redacted path part")
        print(f"pages: {search.execute('SELECT COUNT(*) FROM pages').fetchone()[0]:,}")
        core = Counter(t & 0xFF for (t,) in vault.execute("SELECT transition FROM browser_visits"))
        print("core transition types stored:", dict(sorted(core.items())))


def check(allowed, temp_root):
    temp_root = Path(temp_root).resolve()
    base = Path(tempfile.mkdtemp(prefix="inkvault-browser-check-", dir=temp_root)).resolve()
    assert base.parent == temp_root and base.name.startswith("inkvault-browser-check-"), base
    code = 1
    try:
        os.environ["INKVAULT_HOME"] = str(base / "vault")
        from inkvault import browsers, embed, history, index, paths
        embed.build = lambda: None  # meaning search would download a model; keyword search is enough here
        roots = copy_profiles(allowed, base)
        browsers.user_data_dirs = lambda: [(b, "chromium", r) for b, r in roots.items()]
        found = browsers.find_profiles()
        assert {p.key for p in found} <= {f"{b}/{f}" for b, f in allowed}, "found a profile that wasn't copied"
        browsers.record_answers(found, {p.key: True for p in found})
        result = history.sync()
        index.build()
        report(result, paths)
        code = 0
    except Exception as e:  # noqa: BLE001 - say what failed (never a row's contents), then clean up
        print(f"the check failed: {type(e).__name__}: {e}")
    finally:
        shutil.rmtree(base, ignore_errors=True)
        if base.exists():
            print(f"COULDN'T DELETE {base}: it holds copies of your browser history. Close any program that may "
                  "have it open, then delete that folder by hand.")
            code = 2
        else:
            print(f"deleted {base}")
    return code


def main():
    if sys.platform != "win32" or not LOCAL.is_dir():
        print("This check is written for the Windows PC the profiles above are on.")
        return 1
    return check(ALLOWED, tempfile.gettempdir())


if __name__ == "__main__":
    sys.exit(main())
