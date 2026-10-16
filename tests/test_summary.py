"""The first-run summary's numbers: conversations and new messages, by the search index's own identity."""
import json
import os
import sqlite3

import pytest


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("INKVAULT_HOME", str(tmp_path / "vault"))
    from inkvault import embed
    monkeypatch.setattr(embed, "build", lambda: None)  # meaning search needs a model download
    return tmp_path


def write(path, *objs, mode="w"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, mode, encoding="utf-8", newline="") as f:
        f.write("".join(json.dumps(o) + "\n" for o in objs))


def turn(type_, uuid, text, sid="sess-1", ts="2026-10-05T14:00:00Z"):
    return {"type": type_, "uuid": uuid, "sessionId": sid, "timestamp": ts, "cwd": "/home/me/harbor",
            "isSidechain": False, "message": {"role": type_, "content": text}}


def claude_file(name="sess-1"):
    return os.path.join(os.environ["CLAUDE_CONFIG_DIR"], "projects", "-home-me-harbor", name + ".jsonl")


def desktop_path(*parts, local="local_s1"):
    from inkvault import sources
    return os.path.join(str(sources.claude_desktop_roots()[0]), "acct", "org", local, *parts)


def audit(type_, uuid, text, sid="desk-1", ts="2026-10-05T14:00:00Z"):
    return {"type": type_, "uuid": uuid, "session_id": sid, "parent_tool_use_id": None,
            "message": {"role": type_, "content": text}, "_audit_timestamp": ts, "_audit_hmac": "h"}


def sync_and_build():
    from inkvault import index, sources
    results = sources.sync()
    assert index.build()
    return results


def two_sessions():
    write(claude_file("sess-1"), turn("user", "u1", "first question"), turn("assistant", "a1", "first answer"))
    write(claude_file("sess-2"), turn("user", "v1", "other question", sid="sess-2"))


def lines(before, results):
    from inkvault import summary
    after = summary.conversation_ids()
    return {(l.source): l for l in summary.session_lines(before, after, results)}


def test_first_run_without_search_db(home):
    from inkvault import summary
    two_sessions()
    assert summary.conversation_ids() == {}
    results = sync_and_build()
    got = lines({}, results)["claude_code"]
    assert (got.name, got.conversations, got.with_new) == ("Claude Code", 2, 2)


def test_rebuild_unchanged_reports_zero_new(home):
    from inkvault import summary
    two_sessions()
    write(desktop_path(".claude", "projects", "-sessions-x", "desk-1.jsonl"),
          turn("user", "d1", "desk q", sid="desk-1"), turn("assistant", "d2", "desk a", sid="desk-1"))
    write(desktop_path("audit.jsonl"), audit("user", "d1", "desk q"))
    sync_and_build()
    before = summary.conversation_ids()
    assert before
    results = sync_and_build()
    got = lines(before, results)
    assert got and all(l.with_new == 0 for l in got.values())
    assert summary.conversation_ids() == before


def test_rewritten_file_not_new(home):
    from inkvault import summary
    two_sessions()
    sync_and_build()
    before = summary.conversation_ids()
    write(claude_file("sess-1"), turn("user", "u1", "first question"), turn("assistant", "a1", "first answer"))
    results = sync_and_build()
    assert lines(before, results)["claude_code"].with_new == 0


def test_resume_adds_new(home):
    from inkvault import summary
    two_sessions()
    sync_and_build()
    before = summary.conversation_ids()
    write(claude_file("sess-1"), turn("user", "u2", "one more", ts="2026-10-05T15:00:00Z"), mode="a")
    results = sync_and_build()
    line = lines(before, results)["claude_code"]
    assert (line.conversations, line.with_new) == (2, 1)
    key = ("claude_code", "claude_code:sess-1")
    assert len(summary.conversation_ids()[key]) - len(before[key]) == 1


def test_desktop_audit_merging(home):
    from inkvault import summary
    write(desktop_path(".claude", "projects", "-sessions-x", "desk-1.jsonl"),
          turn("user", "d-u1", "Add retries", sid="desk-1"), turn("assistant", "d-a1", "Done", sid="desk-1"))
    # the audit log grows with the session: the same prompt under another uuid is an echo
    write(desktop_path("audit.jsonl"), audit("user", "y1", "Add retries", ts="2026-10-05T14:00:02Z"))
    # an audit-only conversation, and one audit file touching two conversations
    write(desktop_path("audit.jsonl", local="local_s2"),
          audit("user", "x1", "Draft the FAQ", sid="desk-2", ts="2026-10-05T09:00:01Z"),
          audit("user", "x2", "Plan the launch", sid="desk-3", ts="2026-10-05T10:00:01Z"))
    results = sync_and_build()
    line = lines({}, results)["claude_desktop"]
    assert (line.name, line.conversations, line.with_new) == ("Claude desktop", 3, 3)
    ids = summary.conversation_ids()[("claude_desktop", "claude_desktop:desk-1")]
    assert ids == {"claude_desktop:d-u1", "claude_desktop:d-a1"}  # the echo is not a second message


def test_removal_recovery_no_negatives(home):
    """A pending removal finished during the build, and the index shrinks (a conversation is gone from the vault):
    totals come from the new index, and "with new" is a count of unseen ids, never a difference."""
    from inkvault import export, forget, paths, summary
    two_sessions()
    sync_and_build()
    before = summary.conversation_ids()
    assert len(before) == 2
    db = export.open_vault()
    for i, (profile, host) in enumerate([("p", "gone.example"), ("p", "gone.example"), ("p", "keep.example")]):
        db.execute("INSERT INTO browser_visits (id, profile, visit_id, redirected, transition, created, url, address) "
                   "VALUES (?,?,?,0,0,?,?,?)",
                   (f"v{i}", profile, i, "2026-10-05T00:00:00Z", f"https://{host}/", host))
    db.execute("INSERT INTO browser_removals (what, value, days, created) VALUES ('site','gone.example','[]','x')")
    db.execute("DELETE FROM session_lines WHERE file LIKE '%sess-2%'")  # the index shrinks by a whole conversation
    db.commit()
    db.close()
    assert summary.visit_total(["p"]) == 3 and forget.pending() == 1
    write(claude_file("sess-1"), turn("user", "u2", "one more", ts="2026-10-05T15:00:00Z"), mode="a")
    results = sync_and_build()
    assert forget.pending() == 0 and summary.visit_total(["p"]) == 1  # the removed visits are gone
    after = summary.conversation_ids()
    s = sqlite3.connect(paths.search_db())
    total = s.execute("SELECT COUNT(DISTINCT conversation_id) FROM messages WHERE source='claude_code'").fetchone()[0]
    s.close()
    assert total == 1 == len(after) and ("claude_code", "claude_code:sess-2") not in after
    got = lines(before, results)["claude_code"]
    assert (got.conversations, got.with_new) == (1, 1)  # sess-1 resumed once; nothing subtracted, nothing from before
    unchanged = summary.session_lines(after, after, results)
    assert [(l.conversations, l.with_new) for l in unchanged] == [(1, 0)]


def test_only_found_sources_get_lines(home):
    from inkvault import sources, summary
    results = [sources.SourceResult("codex", "Codex", False, 0, 0)]
    assert summary.session_lines({}, {("codex", "c1"): frozenset({"m"})}, results) == []
    found = [sources.SourceResult("codex", "Codex", True, 0, 0)]
    assert summary.session_lines({}, {}, found) == [summary.SessionLine("codex", "Codex", 0, 0)]


def test_visit_total_counts_chosen_profiles(home):
    from inkvault import export, summary
    assert summary.visit_total(["a"]) == 0  # no vault yet
    db = export.open_vault()
    for i, p in enumerate(["a", "a", "b"]):
        db.execute("INSERT INTO browser_visits (id, profile, visit_id, redirected, transition, created, url, address) VALUES (?,?,?,0,0,?,?,?)",
                   (f"{p}{i}", p, i, "2026-10-05T00:00:00Z", "https://x.example/", "x.example"))
    db.commit()
    db.close()
    assert summary.visit_total(["a"]) == 2 and summary.visit_total(["a", "b"]) == 3 and summary.visit_total([]) == 0
