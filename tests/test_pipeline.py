"""End-to-end on a small synthetic vault: no PiecesOS, no network, no real data."""
import json
import sqlite3

import pytest


def rec(kind, id_, **fields):
    return (kind, id_, json.dumps({"id": id_, **fields}))


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setenv("INKVAULT_HOME", str(tmp_path))
    from inkvault import embed, export
    monkeypatch.setattr(embed, "build", lambda: None)  # meaning search needs a model download; keyword search is tested
    db = export.open_vault()
    db.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?)",
               ("e1", "2026-02-26T18:00:00Z", "chrome.exe", "Stripe dashboard", "https://dashboard.stripe.com/x",
                "Harbor Project pricing tiers", "{}"))
    db.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?)",
               ("e2", "2026-02-27T15:00:00Z", "Code.exe", "retry.ts", None, "exponential backoff helper", "{}"))
    db.executemany("INSERT INTO raw_records VALUES (?,?,?)", [
        rec("annotation", "a1", text="Worked on the Harbor Project continuity audit."),
        *[rec("summary", f"s{i}", name=f"Harbor Project {w}", created={"value": f"2026-02-2{i}T12:00:00Z"},
              annotations={"indices": {"a1": 0}}) for i, w in enumerate(["Audit", "Review", "Launch"], start=5)],
        rec("summary", "s9", name="Garden Club Planning", created={"value": "2026-02-26T09:00:00Z"}),
        rec("conversation", "c1", name="Retry logic"),
        rec("message", "m1", created={"value": "2026-02-27T15:05:00Z"}, role="USER", conversation={"id": "c1"},
            fragment={"string": {"raw": "how do I add jitter to backoff?"}}),
        rec("asset", "x1", name="With Exponential Backoff Retry", created={"value": "2025-11-11T07:02:14Z"},
            original={"reference": {"fragment": {"string": {"raw": "async function retry(fn) { /* … */ }"}},
                                    "classification": {"specific": "ts"}}}),
    ])
    db.commit()
    db.close()
    return tmp_path


def test_index_and_keyword_search(vault):
    from inkvault import index, server
    assert index.build()
    hits = server.search_memories("backoff", mode="keyword")
    assert "[snippets] x1" in hits and "[events] e2" in hits
    assert "[chats] m1" in server.search_memories("jitter", source="chats", mode="keyword")
    assert server.search_memories("backoff", source="nope").startswith("Unknown source")


def test_get_memory_formats_each_kind(vault):
    from inkvault import index, server
    index.build()
    assert "```ts" in server.get_memory("x1")
    assert "Harbor Project continuity audit" in server.get_memory("s5")
    assert "CHAT CONVERSATION" in server.get_memory("m1")
    assert server.get_memory("missing").startswith("No memory")


def test_timeline_and_stats(vault):
    from inkvault import index, server
    index.build()
    t = server.timeline("2026-02-25", "2026-02-27")
    assert t.index("Harbor Project Audit") < t.index("Garden Club Planning")  # chronological
    assert "snippets: 1" in server.memory_stats()


def test_dashboard_detects_projects_and_embeds_no_template_marker(vault):
    from inkvault import dashboard, index
    index.build()
    out = dashboard.build()
    html = out.read_text(encoding="utf-8")
    assert "/*DATA*/null" not in html
    assert '"Harbor Project"' in html  # recurring title phrase became a project
    assert '"Audit"' not in html.split('"themes":')[1].split("]")[0]  # one-off words did not


def test_themes_file_overrides_auto_detection(vault):
    from inkvault import dashboard, index, paths
    index.build()
    paths.themes_file().write_text("# my projects\nPricing = stripe|pricing\n", encoding="utf-8")
    html = dashboard.build().read_text(encoding="utf-8")
    assert '"themes":["Pricing"]' in html


def test_auto_themes_skips_generic_words():
    from inkvault.dashboard import auto_themes
    titles = ["Acme 3 Strategy", "Acme 3 Launch", "Acme 3 Planning", "AI Research", "AI Research", "AI Research"]
    assert list(auto_themes(titles)) == ["Acme 3"]


def test_export_event_row_reads_ocr_fallbacks():
    from inkvault.export import event_row
    row = event_row({"id": "e", "created": {"value": "t"}, "readable": "r",
                     "context": {"native_ocr": {"appTitle": "App", "windowTitle": "Win", "browserUrl": "https://u"}}})
    assert row[:6] == ("e", "t", "App", "Win", "https://u", "r")


def test_status_without_vault(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("INKVAULT_HOME", str(tmp_path))
    monkeypatch.setenv("INKVAULT_PIECES_PORTS", "1")  # nothing listens there
    from inkvault import cli, export
    monkeypatch.setattr(export, "PORTS", [1])
    assert cli.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "vault: empty" in out and "not reachable" in out
