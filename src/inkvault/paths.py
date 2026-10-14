"""Where InkVault keeps a user's data: outside the code, in the platform's app-data folder.

Override with the INKVAULT_HOME environment variable (or --home on the command line).
"""
import os
import sqlite3
import sys
from pathlib import Path


def home() -> Path:
    if os.environ.get("INKVAULT_HOME"):
        base = Path(os.environ["INKVAULT_HOME"])
    elif sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "InkVault"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "InkVault"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "inkvault"
    base.mkdir(parents=True, exist_ok=True)
    return base.resolve()  # absolute, so a relative --home works after a chdir and in file: URIs


def vault_db() -> Path:
    """The raw export from PiecesOS: the one irreplaceable file."""
    return home() / "vault.db"


def search_db() -> Path:
    return home() / "search.db"


def vectors() -> Path:
    return home() / "vectors.npz"


def digests_db() -> Path:
    return home() / "digests.db"


def dashboard() -> Path:
    """The Memory Atlas's data (spec §3.2): what the charts draw, rebuilt nightly. The page itself is in the package."""
    return home() / "atlas.json"


def atlas_lock() -> Path:
    """Held by the running Atlas helper for its whole life (spec §3.4)."""
    return home() / "atlas.lock"


def atlas_info() -> Path:
    """The running helper's port and control secret, readable only by this user (spec §3.4)."""
    return home() / "atlas-helper.json"


def model_dir() -> Path:
    return home() / "model"


def themes_file() -> Path:
    """Optional 'Name = regex' lines that override the dashboard's auto-detected projects."""
    return home() / "themes.txt"


def nightly_log() -> Path:
    """What each scheduled run did, newest last (the last 30 runs)."""
    return home() / "nightly.log"


def nightly_fallback_log() -> Path:
    """Where the nightly run writes when nightly.log itself can't be written."""
    return home() / "nightly-fallback.log"


def nightly_lock() -> Path:
    """Exists while a nightly run or rescue is working; holds its process id."""
    return home() / "nightly.lock"


def backups_dir() -> Path:
    """Dated copies of vault.db made by the nightly run."""
    return home() / "backups"


def schedule_file() -> Path:
    """What `inkvault schedule` set up (time, wake), for `inkvault status`."""
    return home() / "schedule.json"


def browsers_file() -> Path:
    """Which browser profiles are the user's, and sites never to keep (`inkvault browsers`)."""
    return home() / "browsers.json"


def rebuild_marker() -> Path:
    """Exists while search must not be used: something was removed and search hasn't been rebuilt yet."""
    return home() / "rebuild-needed"


def screen_file() -> Path:
    """Screen capture settings: text apps, never rules, pause (`inkvault watch --apps`)."""
    return home() / "screen.json"


def screen_lock() -> Path:
    """Held briefly while screen.json is read, changed and saved (never by the watcher)."""
    return home() / "screen.lock"


def watch_lock() -> Path:
    """Held by the running watcher for its whole life; holds its process id."""
    return home() / "watch.lock"


def watch_stop() -> Path:
    """Asks the running watcher to stop (`inkvault watch --stop`, uninstall)."""
    return home() / "watch.stop"


def watch_heartbeat() -> Path:
    """The watcher's state, rewritten every 30 s: time, app in front, paused, error, counts. Never titles or text."""
    return home() / "watch.heartbeat"


def watch_log() -> Path:
    """The watcher's diagnostics: app names, counts, times and error classes; never titles, text or settings."""
    return home() / "watch.log"


def connect_ro(path):
    """Open a SQLite file read-only. as_uri() escapes characters like # ? % that would break a hand-built
    "file:" URI. SQLite rejects the host part of a network (UNC) path's URI, so those connect plainly."""
    path = Path(path)
    if str(path).startswith("\\\\"):
        return sqlite3.connect(str(path))
    return sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
