"""Build search.db (clean tables + SQLite FTS5 keyword search) from vault.db, then the meaning vectors.

vault.db is opened read-only. The index is built in a temp file and swapped in, so a running
MCP server never sees a half-built index. Safe to re-run any time; it rebuilds from scratch.
"""
import json
import os
import sqlite3

from . import embed, paths


def indices(obj, key):
    return list(((obj.get(key) or {}).get("indices") or {}).keys())


def snippet_row(a):
    ref = (a.get("original") or {}).get("reference") or {}
    text = ((ref.get("fragment") or {}).get("string") or {}).get("raw") or ""
    lang = (ref.get("classification") or {}).get("specific") or ""
    return a["id"], (a.get("created") or {}).get("value"), a.get("name") or "", lang, text


def build():
    if not paths.vault_db().exists():
        print("Nothing to index yet: run `inkvault export` first.")
        return False
    raw = paths.connect_ro(paths.vault_db())

    def raws(kind):
        for (r,) in raw.execute("SELECT raw FROM raw_records WHERE kind=?", (kind,)):
            yield json.loads(r)

    tmp = paths.search_db().with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    db = sqlite3.connect(tmp)
    db.executescript("""
        CREATE TABLE events (id TEXT PRIMARY KEY, created TEXT, app TEXT, window_title TEXT, url TEXT, readable TEXT);
        CREATE TABLE summaries (id TEXT PRIMARY KEY, created TEXT, name TEXT, text TEXT, event_ids TEXT);
        CREATE TABLE messages (id TEXT PRIMARY KEY, created TEXT, conversation_id TEXT, conversation_name TEXT, role TEXT, text TEXT);
        CREATE TABLE snippets (id TEXT PRIMARY KEY, created TEXT, name TEXT, language TEXT, text TEXT);
    """)

    db.executemany("INSERT INTO events VALUES (?,?,?,?,?,?)",
                   raw.execute("SELECT id, created, app, window_title, url, readable FROM events"))

    # A summary's text lives in its annotations; a chat message's conversation has the name.
    annotations = {a["id"]: a.get("text") or "" for a in raws("annotation")}
    conv_names = {c["id"]: c.get("name") or "" for c in raws("conversation")}
    for s in raws("summary"):
        text = "\n\n".join(annotations[a] for a in indices(s, "annotations") if annotations.get(a))
        db.execute("INSERT INTO summaries VALUES (?,?,?,?,?)", (
            s["id"], (s.get("created") or {}).get("value"), s.get("name"), text, json.dumps(indices(s, "events"))))
    for m in raws("message"):
        conv = (m.get("conversation") or {}).get("id")
        text = (((m.get("fragment") or {}).get("string") or {}).get("raw")) or ""
        db.execute("INSERT INTO messages VALUES (?,?,?,?,?,?)", (
            m["id"], (m.get("created") or {}).get("value"), conv, conv_names.get(conv, ""), m.get("role"), text))
    db.executemany("INSERT INTO snippets VALUES (?,?,?,?,?)", (snippet_row(a) for a in raws("asset")))

    # External-content FTS tables: the index points back at rows, so text isn't stored twice.
    db.executescript("""
        CREATE VIRTUAL TABLE summaries_fts USING fts5(name, text, content='summaries', content_rowid='rowid', tokenize='porter unicode61');
        INSERT INTO summaries_fts(rowid, name, text) SELECT rowid, name, text FROM summaries;
        CREATE VIRTUAL TABLE messages_fts USING fts5(conversation_name, text, content='messages', content_rowid='rowid', tokenize='porter unicode61');
        INSERT INTO messages_fts(rowid, conversation_name, text) SELECT rowid, conversation_name, text FROM messages;
        CREATE VIRTUAL TABLE events_fts USING fts5(window_title, app, url, readable, content='events', content_rowid='rowid', tokenize='porter unicode61');
        INSERT INTO events_fts(rowid, window_title, app, url, readable) SELECT rowid, window_title, app, url, readable FROM events;
        CREATE VIRTUAL TABLE snippets_fts USING fts5(name, language, text, content='snippets', content_rowid='rowid', tokenize='porter unicode61');
        INSERT INTO snippets_fts(rowid, name, language, text) SELECT rowid, name, language, text FROM snippets;
        CREATE INDEX events_created ON events(created);
        CREATE INDEX summaries_created ON summaries(created);
        CREATE INDEX messages_created ON messages(created);
        CREATE INDEX snippets_created ON snippets(created);
    """)
    db.commit()
    counts = {t: db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("events", "summaries", "messages", "snippets")}
    print("indexed: " + ", ".join(f"{n:,} {t}" for t, n in counts.items()), flush=True)
    db.close()
    raw.close()
    os.replace(tmp, paths.search_db())

    embed.build()
    return True
