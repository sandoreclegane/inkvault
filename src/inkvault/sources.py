"""Claude Code and Codex sessions, copied into vault.db so they outlive the tools' own cleanup.

Both keep every session on disk as JSONL. Each sync reads only what was added since the last one, and stores the
lines that matter (prompts, replies, titles) exactly as written, except that images, documents and tool results
inside a kept line are replaced by a short placeholder (that line is then stored re-serialized). Tool output and
attachments are most of the bytes, and where secrets end up. Claude Code deletes old sessions after 30 days by
default; lines already in the vault stay there.

The files are assumed to be append-only: a rewrite is noticed only if the first line changes or the file shrinks.

At index time, messages() turns the stored lines into chat messages for search, digests and the dashboard.
"""
import datetime
import hashlib
import json
import os
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

from . import export
from .times import local

NAMES = {"claude_code": "Claude Code", "claude_desktop": "Claude desktop", "codex": "Codex"}


def claude_root():
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "projects"


def claude_desktop_roots():
    """Where the Claude desktop app keeps agent-mode sessions. Only locations seen on a real install are listed:
    on Windows the app is an MSIX package, and %APPDATA%\\Claude is redirected into its package folder."""
    if os.name != "nt":
        return []
    found = [Path(os.environ["APPDATA"], "Claude")] if os.environ.get("APPDATA") else []
    if os.environ.get("LOCALAPPDATA"):
        found += Path(os.environ["LOCALAPPDATA"], "Packages").glob("Claude_*/LocalCache/Roaming/Claude")
    return [p / "local-agent-mode-sessions" for p in found]


def codex_root():
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def claude_files():
    root = claude_root()
    return sorted(root.glob("*/*.jsonl")) if root.is_dir() else None  # top-level sessions, not subagents


def long_path(p):
    """p resolved, and on Windows prefixed with \\\\?\\: the desktop app's session paths run past 260 characters."""
    p = p.resolve()
    return Path("\\\\?\\" + str(p)) if os.name == "nt" and not str(p).startswith("\\\\") else p


def claude_desktop_files():
    """Agent-mode session files and audit logs (not subagents), each folder read once."""
    roots = {long_path(r) for r in claude_desktop_roots() if r.is_dir()}
    if not roots:
        return None
    files = {desktop_key(f): f for r in sorted(roots)
             for pattern in ("*/*/local_*/.claude/projects/*/*.jsonl", "*/*/local_*/audit.jsonl")
             for f in r.glob(pattern)}
    return sorted(files.values())


def file_name(path):
    """The name a file is stored under: its own. Session files are named by a unique id."""
    return path.name


def desktop_key(path):
    """A desktop file is stored as <local_ folder>/<name>: every audit log is named audit.jsonl, and the index ties
    an audit log to its session by folder."""
    folder = path.parent if path.name == "audit.jsonl" else path.parents[3]  # local_*/.claude/projects/<p>/<file>
    return f"{folder.name}/{path.name}"


def file_counts(names):
    """'3 sessions', or '3 sessions, 4 audit logs': an audit log repeats sessions, so it isn't counted as one."""
    names = list(names)
    audits = sum(n.endswith("/audit.jsonl") for n in names)
    return f"{len(names) - audits} sessions" + (f", {audits} audit logs" if audits else "")


def codex_files():
    root = codex_root()
    dirs = [d for d in (root / "sessions", root / "archived_sessions") if d.is_dir()]
    return sorted(f for d in dirs for f in d.rglob("*.jsonl")) if dirs else None


def keep_claude(o):
    t = o.get("type")
    if t in ("user", "assistant"):
        content = (o.get("message") or {}).get("content")
        return not (isinstance(content, list) and content and
                    all(isinstance(b, dict) and b.get("type") == "tool_result" for b in content))
    return t == "summary" or (isinstance(t, str) and t.endswith("title"))


def is_audit(o):
    return "_audit_timestamp" in o or "session_id" in o


def keep_desktop(o):
    """Session-file lines by Claude Code's rules. Audit-log lines: top-level prompts and replies only, not a child of
    a tool or subagent, not replayed or synthetic, not only tool results."""
    if not is_audit(o):
        return keep_claude(o)
    return (o.get("type") in ("user", "assistant") and o.get("parent_tool_use_id") is None
            and not o.get("isReplay") and not o.get("isSynthetic") and keep_claude(o))


def keep_codex(o):
    t, payload = o.get("type"), o.get("payload")
    payload = payload if isinstance(payload, dict) else {}
    if t == "session_meta":
        return True
    if t == "response_item":
        return payload.get("type") == "message"
    if t == "event_msg":
        return payload.get("type") in ("user_message", "agent_message")
    if t == "compacted":  # kept so a later version can show where context was summarized; not indexed
        return True
    return t == "message" or (t is None and "id" in o and "timestamp" in o)  # the older, unwrapped format


# source -> (find its files, which lines to keep, the name a file is stored under)
SOURCES = {"claude_code": (claude_files, keep_claude, file_name),
           "claude_desktop": (claude_desktop_files, keep_desktop, desktop_key),
           "codex": (codex_files, keep_codex, file_name)}
# Blocks never stored, even inside a kept line: pasted images and files (often inline base64) and tool output.
OMITTED = {"image", "input_image", "document", "tool_result"}


def omit_attachments(o):
    """o with every OMITTED block inside it replaced by {"type": ..., "omitted": true}, at any depth (Codex's
    compaction records nest whole earlier messages); None if it had none. Matched by block type, never by text."""
    changed = False

    def clean(x):
        nonlocal changed
        if isinstance(x, list):
            return [clean(v) for v in x]
        if isinstance(x, dict):
            kind = x.get("type")  # not always a string: a JSON Schema type can be a list, e.g. ["string", "null"]
            if isinstance(kind, str) and kind in OMITTED and x is not o:
                changed = True
                return {"type": x["type"], "omitted": True}
            return {k: clean(v) for k, v in x.items()}
        return x

    cleaned = clean(o)
    return cleaned if changed else None


# How kept lines are stored. 2: OMITTED blocks replaced, Codex compaction records kept. 3: OMITTED blocks replaced
# at any depth (images inside compaction history).
FORMAT = "3"


def upgrade(db):
    """Bring lines stored by an earlier format up to this one, once, in one transaction. Lines whose session file is
    gone are cleaned too (they can't be read again); files still there are read again from the start, which adds
    records an earlier format skipped. Backups made before stay as they were."""
    if db.execute("SELECT value FROM meta WHERE key='session_lines_format'").fetchone() == (FORMAT,):
        return
    last = 0
    while rows := db.execute("SELECT rowid, raw FROM session_lines WHERE rowid > ? ORDER BY rowid LIMIT 1000",
                             (last,)).fetchall():
        for rowid, raw in rows:
            try:
                o = json.loads(raw)
            except ValueError:
                continue
            if isinstance(o, dict) and (stripped := omit_attachments(o)):
                db.execute("UPDATE session_lines SET raw=? WHERE rowid=?",
                           (json.dumps(stripped, ensure_ascii=False), rowid))
        last = rows[-1][0]
    db.execute("UPDATE session_files SET read_to = 0")
    db.execute("INSERT OR REPLACE INTO meta VALUES ('session_lines_format', ?)", (FORMAT,))
    db.commit()


def read_file(db, source, path, keep, name):
    """Store the kept lines added to path (stored as name) since the last sync. Returns how many were stored."""
    row = db.execute("SELECT read_to, head FROM session_files WHERE source=? AND file=?", (source, name)).fetchone()
    read_to, head = row or (0, None)
    stored = 0
    with open(path, "rb") as f:
        first = f.readline()
        if not first.endswith(b"\n"):
            return 0  # not even one complete line yet
        size = os.fstat(f.fileno()).st_size
        now_head = hashlib.sha1(first).hexdigest()
        if now_head != head or size < read_to:  # new, or rewritten: start over
            db.execute("DELETE FROM session_lines WHERE source=? AND file=?", (source, name))
            read_to = 0
        f.seek(read_to)
        offset = read_to
        while line := f.readline():
            if not line.endswith(b"\n"):
                break  # still being written; the next sync reads it
            try:
                o = json.loads(line)
                kept = isinstance(o, dict) and keep(o)
            except ValueError:
                kept = False
            if kept:
                stripped = omit_attachments(o)
                raw = json.dumps(stripped, ensure_ascii=False) if stripped else \
                    line.decode("utf-8", "replace").rstrip("\r\n")
                db.execute("INSERT OR REPLACE INTO session_lines VALUES (?,?,?,?)", (source, name, offset, raw))
                stored += 1
            offset += len(line)
    db.execute("INSERT OR REPLACE INTO session_files VALUES (?,?,?,?)", (source, name, offset, now_head))
    return stored


def sync():
    """Copy what's new from each source into the vault. Returns False if no source is on this computer."""
    found = {}
    for source, (files, keep, key) in SOURCES.items():
        paths = files()
        if paths is None:
            print(f"{NAMES[source]}: not found")
        else:
            found[source] = paths, keep, key
    if not found:
        return False  # and no empty vault is made
    db = export.open_vault()
    try:
        upgrade(db)
        for source, (paths, keep, key) in found.items():
            new, failed = 0, 0
            for path in paths:
                try:
                    new += read_file(db, source, path, keep, key(path))
                except OSError as e:  # one unreadable file must not stop the rest
                    db.execute("INSERT INTO failures VALUES (?, ?, ?)", (source, path.name, str(e)))
                    failed += 1
                db.commit()
            unreadable = f", {failed} unreadable" if failed else ""
            print(f"{NAMES[source]}: {file_counts(key(p) for p in paths)}, {new} new lines{unreadable}",
                  flush=True)
        db.execute("INSERT OR REPLACE INTO meta VALUES ('last_sync', ?)",
                   (datetime.datetime.now().astimezone().isoformat(timespec="seconds"),))
        db.commit()
    finally:
        db.close()
    return True


# --- Reading the stored lines back as chat messages (used by index.py) ---

REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)


def clip(text, n=80):
    text = " ".join(text.split())
    return text if len(text) <= n else text[:n - 1] + "…"


def project(cwd):
    return re.split(r"[\\/]", cwd.rstrip("\\/"))[-1] if isinstance(cwd, str) and cwd.strip("\\/") else ""


def block_text(content, kinds):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n\n".join(b["text"] for b in content
                           if isinstance(b, dict) and b.get("type") in kinds and isinstance(b.get("text"), str))
    return ""


def claude_messages(file, lines, source="claude_code"):
    """One Claude Code (or Claude desktop) session -> (id, created, conversation_id, role, text), plus its name."""
    title, cwd, session, out = None, None, None, []
    for offset, o in lines:
        t = o.get("type")
        if t == "summary" and isinstance(o.get("summary"), str):
            title = title or o["summary"]
        elif isinstance(t, str) and t.endswith("title"):
            title = next((v for k, v in o.items()
                          if k != "type" and "title" in k.lower() and isinstance(v, str)), title)
        if t not in ("user", "assistant") or o.get("isSidechain") or o.get("isMeta"):
            continue
        cwd, session = cwd or o.get("cwd"), session or o.get("sessionId")
        text = REMINDER.sub("", block_text((o.get("message") or {}).get("content"), ("text",))).strip()
        if not text or text.startswith(("<command-", "<local-command-")) or not isinstance(o.get("timestamp"), str):
            continue
        out.append((f"{source}:{o.get('uuid') or f'{file}:{offset}'}", o["timestamp"], t.upper(), text))
    conv = f"{source}:{session or Path(file).stem}"
    first = next((text for _, _, role, text in out if role == "USER"), "untitled")
    return conv, conversation_name(source, cwd, title or first), out


# Setup Codex puts in front of what the user typed: wrapped context blocks, and AGENTS.md instructions (with or
# without "for <path>"; up to </INSTRUCTIONS> when that tag is there, else the whole block). Seen on real sessions.
CODEX_CONTEXT_TAGS = {"environment_context", "user_instructions", "recommended_plugins",
                      "external_codex_apps_open_page", "in-app-browser-context", "guardian_tool_descriptions",
                      "task-notification", "turn_aborted"}
CODEX_WRAPPED = re.compile(r"\A\s*<([A-Za-z][\w-]*)(?:\s[^>]*)?>.*?</\1\s*>", re.S)
CODEX_AGENTS = re.compile(r"\A\s*# AGENTS\.md instructions\b[^\n]*(?:\n.*?</INSTRUCTIONS>|.*\Z)", re.S)
# The user's answer to a question Codex asked in the app: keep the answer, drop the wrapper.
CODEX_REPLY = re.compile(r"\A\s*<send_user_message_question_reply>(.*)</send_user_message_question_reply>\s*\Z", re.S)


def codex_user_text(text):
    """What the user wrote, with Codex's setup taken off the front. Text after the setup is kept, markup included."""
    while True:
        m = CODEX_WRAPPED.match(text)
        if m and m.group(1) in CODEX_CONTEXT_TAGS:
            text = text[m.end():]
        elif m := CODEX_AGENTS.match(text):
            text = text[m.end():]
        else:
            break
    if m := CODEX_REPLY.match(text):
        return m.group(1)
    return text


def codex_text(item):
    content = item.get("content")
    if item.get("role") == "user" and isinstance(content, list):
        content = [dict(b, text=codex_user_text(b["text"])) if isinstance(b, dict) and isinstance(b.get("text"), str)
                   else b for b in content]
    return block_text(content, ("input_text", "output_text", "text")).strip()


ECHO_SECONDS = 30  # an event and the response item it repeats are written together


def near(a, b):
    """True if two timestamps are within ECHO_SECONDS, or either is missing or unreadable (then position decides)."""
    try:
        return abs((local(a) - local(b)).total_seconds()) <= ECHO_SECONDS
    except (TypeError, ValueError, AttributeError):
        return True


def pair_events(items, events):
    """Response items, plus each event that doesn't repeat one of them. An event repeats an item with the same role
    and text, written at about the same time, with nothing of the other role (a reply, or the next prompt) between
    them. Each item is used once, the nearest first. Returns everything in file order."""
    seq = sorted([(m, False) for m in items] + [(m, True) for m in events], key=lambda x: x[0][0])
    used, keep = set(), []
    for i, (m, is_event) in enumerate(seq):
        if not is_event:
            continue
        match = None
        for step in (-1, 1):
            j = i + step
            while 0 <= j < len(seq) and seq[j][0][2] == m[2]:
                other, other_is_event = seq[j]
                if not other_is_event and j not in used and other[3] == m[3] and near(other[1], m[1]):
                    if match is None or abs(j - i) < abs(match - i):
                        match = j
                    break
                j += step
        if match is None:
            keep.append(i)
        else:
            used.add(match)
    return [m for i, (m, is_event) in enumerate(seq) if not is_event or i in keep]


def codex_messages(file, lines):
    """One Codex session -> (id, created, role, text), plus its id and name.

    Turns come from response_item messages. event_msg user and agent messages usually repeat them; one that doesn't
    (see pair_events) is added where it appears. Ids come from the session id and the message itself, so a copy of a
    session under another file name isn't indexed twice.
    """
    meta, events, items = {}, [], []
    for offset, o in lines:
        t, payload = o.get("type"), o.get("payload") if isinstance(o.get("payload"), dict) else {}
        ts = o.get("timestamp")
        if t == "session_meta":
            meta = meta or payload
        elif t is None and "id" in o:
            meta = meta or o
        elif t == "event_msg" and isinstance(payload.get("message"), str):
            role = "USER" if payload.get("type") == "user_message" else "ASSISTANT"
            text = codex_user_text(payload["message"]) if role == "USER" else payload["message"]
            events.append((offset, ts, role, text.strip()))
        elif t in ("response_item", "message"):
            item = payload if t == "response_item" else o
            if item.get("role") in ("user", "assistant"):
                items.append((offset, ts, item["role"].upper(), codex_text(item)))
    items = pair_events(items, events)
    start = meta.get("timestamp") if isinstance(meta.get("timestamp"), str) else None
    session = meta.get("id") if isinstance(meta.get("id"), str) else Path(file).stem
    seen, out = Counter(), []
    for _, ts, role, text in items:
        ts = ts if isinstance(ts, str) else start
        if not ts or not text:
            continue
        key = hashlib.sha1(f"{role}\0{ts}\0{text}".encode()).hexdigest()[:16]
        out.append((f"codex:{session}:{key}:{seen[key]}", ts, role, text))
        seen[key] += 1
    first = next((text for _, _, role, text in out if role == "USER"), "untitled")
    return f"codex:{session}", conversation_name("codex", meta.get("cwd"), first), out


SANDBOX = re.compile(r"\A/sessions/[^/]+/?")  # the desktop app's sandbox root; its name is random


def conversation_name(source, cwd, title):
    if source == "claude_desktop" and isinstance(cwd, str):
        cwd = SANDBOX.sub("", cwd)
    where = project(cwd)
    return f"{NAMES[source]} · {where + ': ' if where else ''}{clip(title)}"


def nearest_session(sessions, ts):
    """The session (id -> lines) whose time span is nearest ts: 0 inside it. The first one if ts can't be read."""
    def distance(lines):
        try:
            t = local(ts)
            times = [local(o["timestamp"]) for _, o in lines if isinstance(o.get("timestamp"), str)]
            lo, hi = min(times), max(times)
        except (TypeError, ValueError, AttributeError):
            return float("inf")
        return 0 if lo <= t <= hi else min(abs((t - lo).total_seconds()), abs((t - hi).total_seconds()))
    return min(sorted(sessions), key=lambda s: distance(sessions[s]))


def desktop_messages(stored):
    """Claude desktop lines from every stored file, [(file, offset, line)] -> (conversation_id, name, messages) per
    session. Files are grouped by their local_ folder. An audit line joins the folder's session with its session_id,
    else the one nearest in time: the audit log also uses the app's own id for the same conversation. Session-file
    messages come first and win; an audit message is added only if its uuid is new and it doesn't repeat a
    session-file message (same role and text, within ECHO_SECONDS): the same prompt under a second uuid. The name
    comes from the session file when there is one."""
    folders = defaultdict(lambda: (defaultdict(list), []))  # folder -> ({session id: session-file lines}, audit lines)
    for file, offset, o in stored:
        sessions, audit_lines = folders[file.split("/", 1)[0]]
        if is_audit(o):
            audit_lines.append((offset, dict(o, timestamp=o.get("timestamp") or o.get("_audit_timestamp"))))
        else:
            sessions[o["sessionId"] if isinstance(o.get("sessionId"), str) else Path(file).stem].append((offset, o))
    for folder, (sessions, audit_lines) in sorted(folders.items()):
        extra = defaultdict(list)
        for offset, o in audit_lines:
            sid = o.get("session_id") if isinstance(o.get("session_id"), str) else folder
            if sessions and sid not in sessions:
                sid = nearest_session(sessions, o.get("timestamp"))
            extra[sid].append((offset, dict(o, sessionId=sid)))
        for sid in sorted(sessions.keys() | extra.keys()):
            main = sessions.get(sid, [])
            conv, name, out = claude_messages(sid, main, "claude_desktop")
            _, audit_name, more = claude_messages(f"{sid}/audit", extra.get(sid, []), "claude_desktop")
            ids = {m[0] for m in out}
            added = [m for m in more if m[0] not in ids and not any(
                role == m[2] and text == m[3] and near(created, m[1]) for _, created, role, text in out)]
            yield conv, name if main else audit_name, out + added


def messages(vault):
    """Every stored session as message rows: (id, created, conversation_id, conversation_name, role, text, source)."""
    readers = {"claude_code": claude_messages, "codex": codex_messages}
    try:
        files = vault.execute("SELECT DISTINCT source, file FROM session_lines").fetchall()
    except sqlite3.OperationalError as e:  # a vault that has never been synced has no session tables
        if "no such table" in str(e):
            return
        raise
    desktop = []
    for source, file in files:
        if source not in readers and source != "claude_desktop":
            continue
        lines = []
        for offset, raw in vault.execute(
                "SELECT offset, raw FROM session_lines WHERE source=? AND file=? ORDER BY offset", (source, file)):
            try:
                o = json.loads(raw)
            except ValueError:
                continue
            if isinstance(o, dict):
                lines.append((offset, o))
        if source == "claude_desktop":  # a session's lines can be in two files: merged below
            desktop += [(file, offset, o) for offset, o in lines]
            continue
        conv, name, out = readers[source](file, lines)
        for id_, created, role, text in out:
            yield id_, created, conv, name, role, text, source
    for conv, name, out in desktop_messages(desktop):
        for id_, created, role, text in out:
            yield id_, created, conv, name, role, text, "claude_desktop"
