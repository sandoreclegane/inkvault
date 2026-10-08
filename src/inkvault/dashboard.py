"""Build dashboard.html: a private, local visual overview of the vault ("Memory Atlas").

The page embeds the user's data, so it is written to the InkVault home and opened from disk,
never hosted. Charts are drawn in the browser with ECharts; only the library comes from a CDN,
and no data is sent anywhere.
"""
import json
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlsplit

from . import paths, sources, topics
from .times import local

MAX_THEMES = 14
SOURCE_LABEL = re.compile(rf"^({'|'.join(map(re.escape, sources.NAMES.values()))}) · ")
# Words that never make a project name on their own: function words and generic work words.
FUNCTION = {"a", "an", "and", "the", "of", "for", "with", "to", "in", "on", "at", "by", "from", "&", "vs", "via", "+", "-"}
GENERIC = {
    "ai", "research", "development", "strategy", "strategic", "planning", "integration", "framework", "setup",
    "review", "system", "systems", "analysis", "management", "infrastructure", "refinement", "project", "projects",
    "work", "data", "design", "code", "content", "platform", "technical", "workflow", "building", "tools", "tool",
    "updates", "update", "personal", "documentation", "session", "architecture", "deployment", "optimization",
    "exploration", "discussion", "troubleshooting", "operations", "synthesis", "coordination", "maintenance",
    "configuration", "admin", "administration", "call", "meeting", "daily", "deep", "launch", "monitoring",
    "reflection", "outreach", "community", "engagement", "growth", "model", "agent", "agents", "interaction",
    "performance", "debugging", "testing", "implementation", "communication", "collaboration", "progress",
    "overview", "summary", "notes", "task", "tasks", "new", "top", "what", "mind", "flow", "issues", "fixes",
    "enhancement", "enhancements", "improvements", "application", "app", "apps", "web", "website", "api",
    "audit", "governance", "logistics", "workspace", "protocol", "security", "support", "planning", "tracking",
    "investigation", "verification", "validation", "audits", "sync", "status", "check", "checks", "report",
}


def load_themes(titles):
    """Projects for the dashboard: themes.txt if the user wrote one, otherwise auto-detected."""
    f = paths.themes_file()
    if f.exists():
        themes = {}
        for line in f.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                name, pattern = line.split("=", 1)
                themes[name.strip()] = pattern.strip()
        if themes:
            return themes
    return auto_themes(titles)


def auto_themes(titles):
    """Recurring capitalized phrases (1-3 words) in session titles, e.g. 'Harbor Project', 'Garden Club'."""
    docs = []
    for t in titles:
        words = re.findall(r"[A-Za-z0-9][A-Za-z0-9&+.'-]*", t or "")
        terms = set()
        for n in (1, 2, 3):
            for i in range(len(words) - n + 1):
                gram = words[i:i + n]
                low = [w.lower() for w in gram]
                if not gram[0][0].isupper() or low[0] in FUNCTION or low[-1] in FUNCTION:
                    continue
                if all(w in GENERIC or w in FUNCTION for w in low) or (n == 1 and len(gram[0]) < 3):
                    continue
                terms.add(" ".join(gram))
        docs.append(terms)
    counts = Counter(t for terms in docs for t in terms)
    picked = []
    for term, n in sorted(counts.items(), key=lambda x: (-x[1], -len(x[0]))):
        if n < 3 or len(picked) >= MAX_THEMES:
            break
        low = term.lower()
        if any(low in p.lower() or p.lower() in low for p in picked):
            continue  # "Logos" and "Logos Math" would count the same sessions twice
        picked.append(term)
    return {t: r"\b" + re.escape(t) + r"\b" for t in picked}


def topic_data(db):
    """Pieces' topic tags -> the Topics card: the shown topics, and per tagged summary [day, [topic indices]].

    Indices refer to ongoing + bursts, in that order. Days are local, worked out here like every other day on the
    page; a timestamp that won't parse just leaves that summary out. A search.db built before topics existed has no
    summary_tags table; that reads as "no topics" until the next `inkvault index`.
    """
    try:
        stored = db.execute("SELECT summary_id, created, tag FROM summary_tags").fetchall()
    except sqlite3.OperationalError as e:
        if "no such table" not in str(e):
            raise
        return {"ongoing": [], "bursts": []}, []
    days, rows = {}, []
    for sid, created, tag in stored:
        if sid not in days:
            try:
                days[sid] = local(created).date().isoformat()
            except (TypeError, ValueError):
                days[sid] = None
        if days[sid]:
            rows.append((sid, days[sid], tag))
    tag_days = defaultdict(list)
    for _, day, tag in rows:
        tag_days[tag].append(date.fromisoformat(day))
    groups = topics.classify(tag_days)
    pos = {name: i for i, name in enumerate(groups["ongoing"] + groups["bursts"])}
    per_summary = {}
    for sid, day, tag in rows:
        if tag in pos:
            per_summary.setdefault(sid, [day, []])[1].append(pos[tag])
    return groups, sorted([day, sorted(found)] for day, found in per_summary.values())


def chat_sources(db):
    """Where the chat messages came from, most first, for the Chat messages tile."""
    names = {"pieces": "Pieces", **sources.NAMES}
    try:
        rows = db.execute("SELECT source FROM messages GROUP BY source ORDER BY COUNT(*) DESC").fetchall()
    except sqlite3.OperationalError:  # an index built before 0.2.0 has no source column: all Pieces
        return ["Pieces"]
    return [names.get(s, s) for (s,) in rows] or ["Pieces"]


def collect(db):
    days = defaultdict(lambda: {"captures": 0, "sessions": 0, "chats": 0, "pages": 0, "hours": 0})
    apps, sites, web_sites = defaultdict(Counter), defaultdict(Counter), defaultdict(Counter)

    def touch(ts, source):
        t = local(ts)
        d = days[t.date().isoformat()]
        d[source] += 1
        d["hours"] |= 1 << t.hour
        return t.date().isoformat()

    for ts, app, url in db.execute("SELECT created, app, url FROM events WHERE created IS NOT NULL"):
        day = touch(ts, "captures")
        if app:
            apps[day][re.sub(r"\.exe$", "", app, flags=re.I)] += 1
        if url and (host := urlsplit(url).hostname):
            sites[day][host.removeprefix("www.")] += 1

    # Pages opened in a browser on this computer. Synced visits happened on another device, so they don't mark
    # active hours here (they are in search). Counted apart from Pieces' captures: the two are sampled differently.
    try:
        opened = db.execute("SELECT created, host FROM visits WHERE synced = 0").fetchall()
    except sqlite3.OperationalError:  # an index built before 0.3.0
        opened = []
    for ts, host in opened:
        day = touch(ts, "pages")
        if host:
            web_sites[day][host] += 1

    titled = []  # (day, title, extra text) for project detection
    highlights = defaultdict(list)
    for ts, name in db.execute("SELECT created, name FROM summaries WHERE created IS NOT NULL ORDER BY created"):
        day = touch(ts, "sessions")
        titled.append((day, name or "", ""))
        if name and name not in highlights[day]:
            highlights[day].append(name)
    chats = {}
    for ts, conv, cname, role, text in db.execute(
            "SELECT created, conversation_id, conversation_name, role, text FROM messages WHERE created IS NOT NULL ORDER BY created"):
        day = touch(ts, "chats")
        c = chats.setdefault(conv, {"day": day, "name": cname or "", "asks": []})
        if role == "USER":
            c["asks"].append(text or "")
    for c in chats.values():
        # "Claude Code · harbor: …": the label names the tool, not a project, and is on every session.
        titled.append((c["day"], SOURCE_LABEL.sub("", c["name"]), "\n".join(c["asks"])))
        if c["name"]:
            highlights[c["day"]].append(c["name"])

    themes = load_themes([t for _, t, _ in titled])
    theme_res = [re.compile(p, re.I) for p in themes.values()]
    docs = []
    for day, title, extra in titled:
        found = [i for i, rx in enumerate(theme_res) if rx.search(title + "\n" + extra)]
        if found:
            docs.append([day, found])

    topic_groups, topic_docs = topic_data(db)

    digests = {}
    if paths.digests_db().exists():
        ddb = paths.connect_ro(paths.digests_db())
        digests = dict(ddb.execute("SELECT day, digest FROM digests"))
        ddb.close()

    return {
        "themes": list(themes),
        "days": dict(sorted(days.items())),
        "apps": {d: dict(c) for d, c in apps.items()},
        "sites": {d: dict(c) for d, c in sites.items()},
        "web_sites": {d: dict(c) for d, c in web_sites.items()},
        "docs": docs,
        "topics": topic_groups,
        "topic_docs": topic_docs,
        "digests": digests,
        "highlights": {d: h[:3] for d, h in highlights.items()},
        "snippets": db.execute("SELECT COUNT(*) FROM snippets").fetchone()[0],
        "chat_sources": chat_sources(db),
        "built": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M"),
    }


def build():
    if not paths.search_db().exists():
        print("Nothing to show yet: run `inkvault rescue` first.")
        return None
    if paths.rebuild_marker().exists():
        print("Search is being rebuilt after something was removed: run `inkvault index` first.")
        return None
    db = paths.connect_ro(paths.search_db())
    data = collect(db)
    db.close()
    if not data["days"]:
        print("The vault is empty.")
        return None
    payload = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    out = paths.dashboard()
    out.write_text(TEMPLATE.read_text(encoding="utf-8").replace("/*DATA*/null", payload), encoding="utf-8")
    n_topics = len(data["topics"]["ongoing"]) + len(data["topics"]["bursts"])
    print(f"dashboard: {len(data['days'])} days, {len(data['themes'])} projects, {n_topics} topics, "
          f"{len(data['digests'])} digests -> {out}")
    return out


TEMPLATE = Path(__file__).with_name("atlas.html")  # the page; "/*DATA*/null" is replaced with the vault's data
