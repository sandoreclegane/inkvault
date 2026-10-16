"""Numbers for the first-run summary, taken from the search index's own identity.

A conversation is (source, conversation_id) and a message is messages.id in search.db, so Claude desktop session and
audit merging, echo suppression and audit-only conversations follow exactly the rules indexing already applies.
Nothing here is a subtraction: "with new messages" counts conversations holding an id the earlier index lacked.
"""
import sqlite3
from collections import defaultdict
from typing import NamedTuple

from . import paths


class SessionLine(NamedTuple):
    source: str
    name: str
    conversations: int
    with_new: int


def conversation_ids():
    """(source, conversation_id) -> message ids, from search.db in one read transaction; {} if there is none."""
    path = paths.search_db()
    if not path.exists():
        return {}
    db = paths.connect_ro(path)
    try:
        db.execute("BEGIN")
        found = defaultdict(set)
        for source, conv, id_ in db.execute("SELECT source, conversation_id, id FROM messages"):
            found[(source, conv)].add(id_)
        return {k: frozenset(v) for k, v in found.items()}
    except sqlite3.OperationalError:  # an index with no messages table is no index
        return {}
    finally:
        db.close()


def session_lines(before, after, results):
    """One SessionLine per source found on this computer."""
    out = []
    for r in results:
        if not r.found:
            continue
        keys = [k for k in after if k[0] == r.source]
        with_new = sum(1 for k in keys if after[k] - before.get(k, frozenset()))
        out.append(SessionLine(r.source, r.name, len(keys), with_new))
    return out


def visit_total(profile_keys):
    """Visits saved in the vault for these browser profiles."""
    if not profile_keys or not paths.vault_db().exists():
        return 0
    db = paths.connect_ro(paths.vault_db())
    try:
        marks = ",".join("?" * len(profile_keys))
        return db.execute(f"SELECT COUNT(*) FROM browser_visits WHERE profile IN ({marks})",
                          list(profile_keys)).fetchone()[0]
    except sqlite3.OperationalError:  # a vault with no browser tables yet
        return 0
    finally:
        db.close()
