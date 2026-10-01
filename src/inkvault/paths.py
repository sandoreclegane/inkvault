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
    return home() / "dashboard.html"


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


def connect_ro(path):
    """Open a SQLite file read-only. as_uri() escapes characters like # ? % that would break a hand-built
    "file:" URI. SQLite rejects the host part of a network (UNC) path's URI, so those connect plainly."""
    path = Path(path)
    if str(path).startswith("\\\\"):
        return sqlite3.connect(str(path))
    return sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
