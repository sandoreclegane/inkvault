"""Build search.db (clean tables + SQLite FTS5 keyword search) from vault.db, then the meaning vectors.

vault.db is opened read-only. The index is built in a temp file and swapped in, so a running
MCP server never sees a half-built index. Safe to re-run any time; it rebuilds from scratch.
"""
import json
import os
import sqlite3
from datetime import datetime

from . import embed, history, paths, sources, topics


def indices(obj, key):
    return list(((obj.get(key) or {}).get("indices") or {}).keys())


def snippet_row(a):
    ref = (a.get("original") or {}).get("reference") or {}
    text = ((ref.get("fragment") or {}).get("string") or {}).get("raw") or ""
    lang = (ref.get("classification") or {}).get("specific") or ""
    return a["id"], (a.get("created") or {}).get("value"), a.get("name") or "", lang, text


def build():
    from . import forget  # imported here: forget.py calls index.build
    if not paths.vault_db().exists():
        print("Nothing to index yet: run `inkvault export` first.")
        return False
    # A removal stopped part-way is finished before anything is read, so its visits can't be indexed again.
    forget.apply_pending()
    raw = paths.connect_ro(paths.vault_db())

    def raws(kind):
        for (r,) in raw.execute("SELECT raw FROM raw_records WHERE kind=?", (kind,)):
            yield json.loads(r)

    tmp = paths.search_db().with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    db = sqlite3.connect(tmp)
    try:
        db.executescript("""
            CREATE TABLE events (id TEXT PRIMARY KEY, created TEXT, app TEXT, window_title TEXT, url TEXT, readable TEXT);
            CREATE TABLE summaries (id TEXT PRIMARY KEY, created TEXT, name TEXT, text TEXT, event_ids TEXT);
            CREATE TABLE messages (id TEXT PRIMARY KEY, created TEXT, conversation_id TEXT, conversation_name TEXT, role TEXT, text TEXT, source TEXT);
            CREATE TABLE snippets (id TEXT PRIMARY KEY, created TEXT, name TEXT, language TEXT, text TEXT);
            CREATE TABLE summary_tags (summary_id TEXT, created TEXT, tag TEXT);
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        """)

        db.executemany("INSERT INTO events VALUES (?,?,?,?,?,?)",
                       raw.execute("SELECT id, created, app, window_title, url, readable FROM events"))

        # A summary's text lives in its annotations; a chat message's conversation has the name.
        annotations = {a["id"]: a.get("text") or "" for a in raws("annotation")}
        conv_names = {c["id"]: c.get("name") or "" for c in raws("conversation")}
        tag_texts = {t["id"]: topics.normalize(t.get("text")) for t in raws("tag")}
        for s in raws("summary"):
            created = (s.get("created") or {}).get("value")
            text = "\n\n".join(annotations[a] for a in indices(s, "annotations") if annotations.get(a))
            db.execute("INSERT INTO summaries VALUES (?,?,?,?,?)", (
                s["id"], created, s.get("name"), text, json.dumps(indices(s, "events"))))
            # Pieces' topic tags: normalized, once per summary. The timestamp is kept as recorded; the dashboard picks
            # the local day when it builds, like every other day it counts (so a time-zone change needs no re-index).
            tags = {tag_texts.get(i) for i in indices(s, "tags")} - {None, ""}
            if created and tags:
                db.executemany("INSERT INTO summary_tags VALUES (?,?,?)", ((s["id"], created, t) for t in sorted(tags)))
        for m in raws("message"):
            conv = (m.get("conversation") or {}).get("id")
            text = (((m.get("fragment") or {}).get("string") or {}).get("raw")) or ""
            db.execute("INSERT INTO messages VALUES (?,?,?,?,?,?,'pieces')", (
                m["id"], (m.get("created") or {}).get("value"), conv, conv_names.get(conv, ""), m.get("role"), text))
        # Claude Code and Codex sessions. A resumed session can repeat earlier lines (same id): the first copy wins.
        db.executemany("INSERT OR IGNORE INTO messages VALUES (?,?,?,?,?,?,?)", sources.messages(raw))
        db.executemany("INSERT INTO snippets VALUES (?,?,?,?,?)", (snippet_row(a) for a in raws("asset")))

        # Browser history: the visits that count, and one page per address per local day.
        history.index_into(raw, db)
        db.execute("INSERT INTO meta VALUES ('timezone', ?)", (datetime.now().astimezone().strftime("%Z (UTC%z)"),))

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
            CREATE VIRTUAL TABLE pages_fts USING fts5(title, url, content='pages', content_rowid='rowid', tokenize='porter unicode61');
            INSERT INTO pages_fts(rowid, title, url) SELECT rowid, title, url FROM pages;
            CREATE INDEX pages_created ON pages(created);
            CREATE INDEX visits_created ON visits(created);
            CREATE INDEX visits_page ON visits(page_id);
            CREATE INDEX events_created ON events(created);
            CREATE INDEX summaries_created ON summaries(created);
            CREATE INDEX messages_created ON messages(created);
            CREATE INDEX snippets_created ON snippets(created);
            CREATE INDEX summary_tags_tag ON summary_tags(tag);
        """)
        db.commit()
        counts = {t: db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("events", "summaries", "messages", "snippets", "pages")}
        print("indexed: " + ", ".join(f"{n:,} {t}" for t, n in counts.items()), flush=True)
    finally:
        db.close()
        raw.close()
    os.replace(tmp, paths.search_db())

    embed.build()
    marker = paths.rebuild_marker()
    if marker.exists() and not forget.pending():
        # Search is fresh and no removal is pending. The dashboard on disk was built from the old index, so it goes
        # too; the next dashboard build replaces it.
        paths.dashboard().unlink(missing_ok=True)
        marker.unlink()
    return True
