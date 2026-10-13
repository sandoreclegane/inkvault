"""A small fictional vault with every kind of record, for the MCP golden test, retrieval, the reader and the
Atlas helper. Nothing here is real data."""
import importlib
import json
from datetime import datetime, timezone

import numpy as np

LONG = "word " * 1800  # 9,000 characters: three of these make a chat longer than get_memory's 20,000


def T(day, hour, minute=0):
    return f"2026-02-{day:02d}T{hour:02d}:{minute:02d}:00Z"


def S(hour, minute=0):
    return f"2026-02-27T{hour:02d}:{minute:02d}:00.000000Z"


def rec(kind, id_, **fields):
    return (kind, id_, json.dumps({"id": id_, **fields}))


class FakeModel:
    """Meaning search without a download: every query points the same way, so the stored vectors decide the order."""
    def encode(self, texts, **_):
        return np.ones((len(texts), 4), dtype=np.float32)


def utc_local(ts):
    """times.local, pinned to UTC so golden text is the same on every machine and in every time zone."""
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    t = datetime.fromisoformat(ts)
    return (t if t.tzinfo else t.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def utc_times(monkeypatch):
    for name in ("inkvault.history", "inkvault.server", "inkvault.retrieval", "inkvault.atlasserve"):
        try:
            mod = importlib.import_module(name)
        except ImportError:
            continue
        if hasattr(mod, "local"):
            monkeypatch.setattr(mod, "local", utc_local)


def reset_caches(monkeypatch):
    """The meaning model and vectors are cached per module; start every test without them."""
    for name in ("inkvault.retrieval", "inkvault.server"):
        try:
            mod = importlib.import_module(name)
        except ImportError:
            continue
        if hasattr(mod, "_model"):
            monkeypatch.setattr(mod, "_model", None)
        if hasattr(mod, "_vec"):
            monkeypatch.setattr(mod, "_vec", {"mtime": None})


def make_vault(home, monkeypatch, vectors=True):
    monkeypatch.setenv("INKVAULT_HOME", str(home))
    from inkvault import embed, export, index, paths
    monkeypatch.setattr(embed, "build", lambda: None)
    monkeypatch.setattr(embed, "load_model", FakeModel)
    reset_caches(monkeypatch)
    utc_times(monkeypatch)
    db = export.open_vault()
    db.executemany("INSERT INTO events VALUES (?,?,?,?,?,?,?)", [
        ("e1", T(26, 18), "chrome.exe", "Pricing dashboard", "https://dashboard.example.com/x",
         "Harbor Project pricing tiers", "{}"),
        ("e2", T(27, 15), "Code.exe", "retry.ts", None, "exponential backoff helper", "{}"),
    ])
    db.executemany("INSERT INTO raw_records VALUES (?,?,?)", [
        rec("annotation", "a1", text="Worked on the Harbor Project continuity audit."),
        *[rec("summary", f"s{i}", name=f"Harbor Project {w}", created={"value": f"2026-02-2{i}T12:00:00Z"},
              annotations={"indices": {"a1": 0}}) for i, w in enumerate(["Audit", "Review", "Launch"], start=5)],
        rec("summary", "s9", name="Garden Club Planning", created={"value": "2026-02-26T09:00:00Z"}),
        rec("conversation", "c1", name="Retry logic"),
        rec("message", "m1", created={"value": "2026-02-27T15:05:00Z"}, role="USER", conversation={"id": "c1"},
            fragment={"string": {"raw": "how do I add jitter to backoff?"}}),
        rec("conversation", "c2", name="Long notes"),
        *[rec("message", f"m2{i}", created={"value": f"2026-02-28T10:0{i}:00Z"},
              role="ASSISTANT" if i % 2 else "USER", conversation={"id": "c2"},
              fragment={"string": {"raw": f"part {i} lighthouse " + LONG}}) for i in range(3)],
        rec("asset", "x1", name="With Exponential Backoff Retry", created={"value": "2025-11-11T07:02:14Z"},
            original={"reference": {"fragment": {"string": {"raw": "async function retry(fn) { /* … */ }"}},
                                    "classification": {"specific": "ts"}}}),
    ])
    db.executemany("INSERT INTO screen_stretches VALUES (?,?,?,?,?)", [
        ("screen:a", S(9), S(9, 30), "Discord.exe", "Retry chat"),
        ("screen:b", S(10), S(10, 5), "chrome.exe", "Docs page"),
    ])
    db.executemany("INSERT INTO screen_text VALUES (?,?,?,?,?)",
                   [(f"screentext:{i}", "screen:a", S(9, i), f"backoff note number {i:02d} in the chat", str(i))
                    for i in range(3)])
    db.executemany(
        "INSERT INTO browser_visits (id, profile, visit_id, redirected, created, url, address, title, "
        "title_observed_at, duration_s, transition, origin, origin_visit_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", [
            ("v1", "chrome/Default", 1, 0, "2026-02-27T16:00:00.000000Z", "https://docs.example.com/backoff",
             "docs.example.com/backoff", "Backoff guide", "2026-02-27T16:00:00.000000Z", 3.0, 0, None, None),
            ("v2", "chrome/Default", 2, 0, "2026-02-27T17:00:00.000000Z", "https://docs.example.com/backoff",
             "docs.example.com/backoff", "Backoff guide", "2026-02-27T17:00:00.000000Z", 1.0, 0, "phone", None),
        ])
    db.commit()
    db.close()
    assert index.build()
    if vectors:
        np.savez(paths.vectors(), kinds=np.array(["summaries", "chats", "snippets", "events"]),
                 ids=np.array(["s5", "m1", "x1", "e1"]),
                 vecs=np.array([[1, 1, 1, 1], [1, 1, 1, 0], [1, 1, 0, 0], [1, 0, 0, 0]], dtype=np.float32))
        paths.model_dir().mkdir(exist_ok=True)
    return home


def bump(path):
    """A newer published file with the same, valid contents: what a search rebuild looks like to the guard."""
    import os
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))


def web_id():
    """The one page's id (a hash of its address and day)."""
    from inkvault import paths
    db = paths.connect_ro(paths.search_db())
    try:
        return db.execute("SELECT id FROM pages ORDER BY id LIMIT 1").fetchone()[0]
    finally:
        db.close()
