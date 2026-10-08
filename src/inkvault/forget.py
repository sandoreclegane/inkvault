"""Removing browsing from the vault and from everything built from it (browser-history spec, §1 "Removing").

A removal is recorded (browser_removals) before anything changes and applied idempotently, so a stop at any point is
finished later instead of undone: every search build applies pending removals before it reads anything. The
rebuild-needed marker is up from before the record until a search build finishes with nothing pending; the MCP tools,
the digests and the dashboard refuse while it is up. Callers hold the nightly lock (cli.py).
"""
import json
import sqlite3
from datetime import datetime, timedelta, timezone

from . import browsers, dashboard, export, history, index, paths

CHUNK = 500  # ids per statement: well under SQLite's limit on bound parameters
DIGEST_WAIT = 5  # seconds to wait for a busy digests.db before the removal stops (and stays recorded)


def chunks(items):
    for i in range(0, len(items), CHUNK):
        yield items[i:i + CHUNK]


def missing_table(e):
    return "no such table" in str(e)


def matcher(what, value):
    if what == "profile":
        return lambda profile, url: profile == value
    return lambda profile, url: history.skipped(url, [value])


def digest_days(visits):
    """Digests are keyed by the local day when digest ran, in a time zone that may differ from the index's and today's.
    Every UTC offset from -12 to +14 puts an instant on its UTC date or the day before or after it."""
    days = set()
    for _, created in visits:
        day = datetime.fromisoformat(created.replace("Z", "+00:00")).astimezone(timezone.utc).date()
        days |= {(day + timedelta(d)).isoformat() for d in (-1, 0, 1)}
    return days


def record(vault, planned):
    """All of a command's removals in one transaction: none is recorded unless all are."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    vault.executemany("INSERT INTO browser_removals (what, value, days, created) VALUES (?,?,?,?)",
                      ((what, value, json.dumps(sorted(days)), now) for what, value, days in planned))
    vault.commit()


def request(removals):
    """Find what each removal ([(what, value)]) matches, put the marker up, and record them all in one transaction.
    Returns how many stored visits they match; with none, nothing is recorded and nothing changes. A command saves
    the choices that go with its removals only after this returns (apply_pending saves them again in case it
    stopped before)."""
    if not removals or not paths.vault_db().exists():
        return 0
    vault = export.open_vault()
    try:
        stored = vault.execute("SELECT id, created, profile, url FROM browser_visits").fetchall()
        planned, total = [], 0
        for what, value in removals:
            pick = matcher(what, value)
            found = [(i, c) for i, c, p, u in stored if pick(p, u)]
            if found:
                days = digest_days(found)
                planned.append((what, value, days))
                total += len(found)
        if not planned:
            return 0
        paths.rebuild_marker().write_text("removing " + ", ".join(f"{w} {v}" for w, v, _ in planned) + "\n",
                                          encoding="utf-8")
        paths.dashboard().unlink(missing_ok=True)  # a page on disk can't check the marker
        record(vault, planned)
        return total
    finally:
        vault.close()


def keep_choice(what, value):
    """The choice that goes with a removal, saved again in case the command stopped before it was saved."""
    choices = browsers.load_choices()
    if what == "profile" and (choices["profiles"].get(value) or {}).get("choice") != "no":
        choices["profiles"][value] = {"choice": "no"}
    elif what == "site" and value not in choices["skip_sites"]:
        choices["skip_sites"].append(value)
    else:
        return
    browsers.save_choices(choices)


def delete_digests(days):
    if not days or not paths.digests_db().exists():
        return
    db = sqlite3.connect(paths.digests_db(), timeout=DIGEST_WAIT)
    try:
        db.executemany("DELETE FROM digests WHERE day=?", ((d,) for d in days))
        db.commit()
    except sqlite3.OperationalError as e:
        if not missing_table(e):  # locked or unwritable: stop, and leave the removal recorded
            raise
    finally:
        db.close()


def delete_visits(vault, ids):
    for part in chunks(ids):
        vault.execute(f"DELETE FROM browser_visits WHERE id IN ({','.join('?' * len(part))})", part)


def apply_pending():
    """Finish every recorded removal. Idempotent, so it is safe to repeat after a stop anywhere. Returns how many it
    finished."""
    if not paths.vault_db().exists():
        return 0
    vault = export.open_vault()
    try:
        pending_rows = vault.execute("SELECT id, what, value, days FROM browser_removals ORDER BY id").fetchall()
        for rid, what, value, days in pending_rows:
            keep_choice(what, value)
            delete_digests(json.loads(days))
            pick = matcher(what, value)
            delete_visits(vault, [i for i, p, u in vault.execute("SELECT id, profile, url FROM browser_visits")
                                  if pick(p, u)])
            vault.execute("DELETE FROM browser_removals WHERE id=?", (rid,))
            vault.commit()  # the visits and their record go together
        return len(pending_rows)
    except BaseException:
        vault.rollback()
        raise
    finally:
        vault.close()


def pending():
    """How many removals are recorded but not yet applied."""
    if not paths.vault_db().exists():
        return 0
    db = paths.connect_ro(paths.vault_db())
    try:
        return db.execute("SELECT COUNT(*) FROM browser_removals").fetchone()[0]
    except sqlite3.OperationalError as e:
        if missing_table(e):  # a vault no 0.2.1 command has opened yet
            return 0
        raise
    finally:
        db.close()


def finish():
    """Apply what's recorded, then rebuild search (which takes the marker down) and the dashboard."""
    apply_pending()
    index.build()
    dashboard.build()


def remove(removals):
    """Request and finish removals. Returns how many visits they removed. Raises when a step fails; whatever was
    recorded is finished by the next search build or browser sync."""
    n = request(removals)
    if n:
        finish()
    return n


def forget_profile(key):
    return remove([("profile", key)])


def forget_site(host):
    return remove([("site", host)])
