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


def test_ctrl_c_mid_export_keeps_progress_and_stops_fast(tmp_path, monkeypatch):
    import time
    monkeypatch.setenv("INKVAULT_HOME", str(tmp_path))
    from inkvault import export

    class SlowPiecesOS:  # 500 items at 20 ms each: waiting for the whole queue would take seconds
        def get(self, path, timeout=60):
            if path.endswith("identifiers"):
                return {"iterable": [{"id": f"e{i}"} for i in range(500)]}
            time.sleep(0.02)
            return {"id": path.rsplit("/", 1)[1], "created": {"value": "2026-01-01T00:00:00Z"}}

    db = export.open_vault()
    saved = []

    def save(ev):
        if len(saved) == 3:
            raise KeyboardInterrupt  # the user presses Ctrl+C
        db.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?)", export.event_row(ev))
        saved.append(ev["id"])

    start = time.time()
    with pytest.raises(KeyboardInterrupt):
        export.fetch_each(SlowPiecesOS(), db, "events", "/workstream_events/identifiers",
                          "/workstream_event/{id}", save, set())
    assert time.time() - start < 1.5  # didn't wait for the other ~490 queued requests
    db.close()
    check = sqlite3.connect(tmp_path / "vault.db")
    assert check.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 3  # progress was committed


def test_interrupted_rescue_still_builds_a_dashboard(vault, monkeypatch):
    from inkvault import cli, export, paths

    def interrupted():
        raise KeyboardInterrupt
    monkeypatch.setattr(export, "run", interrupted)
    assert cli.main(["rescue", "--no-open"]) == 130
    assert paths.dashboard().exists() and paths.search_db().exists()


def test_status_without_vault(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("INKVAULT_HOME", str(tmp_path))
    monkeypatch.setenv("INKVAULT_PIECES_PORTS", "1")  # nothing listens there
    from inkvault import cli
    assert cli.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "vault: empty" in out and "not reachable" in out


def test_port_file_is_tried_first_and_env_overrides(tmp_path, monkeypatch):
    from inkvault import export
    cfg = tmp_path / "Mesh Intelligent Technologies, Inc" / "Pieces OS" / export.PORT_FILE
    cfg.parent.mkdir(parents=True)
    cfg.write_text("39301\n")
    monkeypatch.setattr(export, "port_file_dirs", lambda: [tmp_path])
    monkeypatch.delenv("INKVAULT_PIECES_PORTS", raising=False)
    monkeypatch.setenv("INKVAULT_HOME", str(tmp_path / "home"))  # no saved port from a real vault
    assert export.ports() == [39301, 39300, 1000]
    cfg.write_text("39300")  # same as a default: no duplicate
    assert export.ports() == [39300, 1000]
    cfg.write_text("not a port")  # unreadable file falls back to the defaults
    assert export.ports() == [39300, 1000]
    monkeypatch.setenv("INKVAULT_PIECES_PORTS", "5,6")
    assert export.ports() == [5, 6]


def test_port_file_found_at_macos_location(tmp_path, monkeypatch):
    from pathlib import Path
    from inkvault import export
    cfg = tmp_path / "Documents" / export.PORT_FILE  # ~/Documents/com.pieces.os/production/Config/.port.txt
    cfg.parent.mkdir(parents=True)
    cfg.write_text("39305")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    assert export.ports_from_files(export.port_file_dirs()) == [39305]


def test_saved_port_is_tried_after_port_files_and_before_defaults(vault, monkeypatch):
    from inkvault import export
    monkeypatch.delenv("INKVAULT_PIECES_PORTS", raising=False)
    monkeypatch.setattr(export, "port_file_dirs", lambda: [])  # e.g. macOS kept the job out of ~/Documents
    assert export.ports() == [39300, 1000]

    class Found:
        base = "http://localhost:39317"
    export.remember(Found(), "12.6.2")
    assert export.ports() == [39317, 39300, 1000]


def test_embedding_never_uses_worker_processes(vault, monkeypatch):
    """model2vec's worker processes crash under pythonw, which is how the nightly job runs on Windows."""
    import importlib

    import numpy as np
    from inkvault import embed, index, paths
    index.build()
    embed = importlib.reload(embed)  # the vault fixture stubbed build(); get the real one back
    calls = []

    class FakeModel:
        def encode(self, texts, **kwargs):
            calls.append(kwargs)
            return np.ones((len(texts), 4), dtype=np.float32)
    monkeypatch.setattr(embed, "load_model", lambda: FakeModel())
    embed.build()
    assert calls and all(kw.get("use_multiprocessing") is False for kw in calls)
    assert paths.vectors().exists()


class SlowPiecesOS:
    """500 events at 20 ms each: only a time budget can end a run quickly."""
    base = "http://localhost:1"

    def get(self, path, timeout=60):
        import time
        if path.endswith("identifiers"):
            return {"iterable": [{"id": f"e{i}"} for i in range(500)]} if "workstream" in path else {"iterable": []}
        if path.startswith("/workstream_event/"):
            time.sleep(0.02)
            return {"id": path.rsplit("/", 1)[1], "created": {"value": "2026-01-01T00:00:00Z"}}
        return {"iterable": []}


def test_export_stops_cleanly_at_its_time_budget(tmp_path, monkeypatch, capsys):
    import time
    monkeypatch.setenv("INKVAULT_HOME", str(tmp_path))
    from inkvault import export
    monkeypatch.setattr(export.PiecesOS, "find", staticmethod(lambda: (SlowPiecesOS(), "12.0")))
    start = time.time()
    assert export.run(budget_seconds=0.3) == "partial"
    assert time.time() - start < 3
    assert "export stopped after its time budget; the next run continues where it stopped" in capsys.readouterr().out
    check = sqlite3.connect(tmp_path / "vault.db")
    n = check.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    assert 0 < n < 500  # progress was committed, and it did not run to the end
    # the next run continues where it stopped, and with no budget it finishes
    assert export.run() is True
    assert check.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 500


def fake_export(monkeypatch, nightly, result="partial"):
    from inkvault import export
    seen = []
    monkeypatch.setattr(nightly, "wait_for_pieces", lambda max_wait=None: object())
    monkeypatch.setattr(export, "run", lambda budget_seconds=None: seen.append(budget_seconds) or result)
    monkeypatch.setattr(nightly, "steps", lambda: [("export", nightly.step_export), ("backup", lambda: True)])
    return seen


def test_nightly_passes_the_remaining_run_budget_and_reports_partial(tmp_path, monkeypatch):
    monkeypatch.setenv("INKVAULT_HOME", str(tmp_path))
    from inkvault import nightly
    assert nightly.RUN_BUDGET == 3 * 3600 and nightly.LOCK_WAIT == 3600 and nightly.BACKUP_MARGIN == 600
    seen = fake_export(monkeypatch, nightly)
    assert nightly.run() == 0
    assert 3 * 3600 - 600 - 60 < seen[0] <= 3 * 3600 - 600
    assert "export partial, backup ok" in nightly.last_run()[2]


def test_the_export_budget_shrinks_by_the_time_spent_waiting(tmp_path, monkeypatch):
    monkeypatch.setenv("INKVAULT_HOME", str(tmp_path))
    import threading
    from conftest import holder
    from inkvault import nightly
    monkeypatch.setattr(nightly, "RUN_BUDGET", 100)
    monkeypatch.setattr(nightly, "BACKUP_MARGIN", 10)
    monkeypatch.setattr(nightly, "LOCK_POLL", 0.2)
    seen = fake_export(monkeypatch, nightly)
    with holder(tmp_path) as p:
        t = threading.Timer(2.0, p.stdin.close)
        t.start()
        assert nightly.run() == 0
        t.join()
    assert 85 < seen[0] < 88.5  # 100 - 10 margin - the ~2 s spent waiting for the lock


def test_export_is_skipped_but_backup_still_runs_when_no_time_is_left(tmp_path, monkeypatch):
    monkeypatch.setenv("INKVAULT_HOME", str(tmp_path))
    from inkvault import nightly, paths
    monkeypatch.setattr(nightly, "RUN_BUDGET", 5)
    monkeypatch.setattr(nightly, "BACKUP_MARGIN", 10)
    seen = fake_export(monkeypatch, nightly)
    assert nightly.run() == 0
    assert seen == []  # export never started
    result = nightly.last_run()[2]
    assert "export skipped" in result and "backup ok" in result
    assert "no time left after waiting" in paths.nightly_log().read_text(encoding="utf-8")


def test_the_piecesos_wait_is_capped_by_the_remaining_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("INKVAULT_HOME", str(tmp_path))
    from inkvault import nightly
    monkeypatch.setattr(nightly, "RUN_BUDGET", 100)
    monkeypatch.setattr(nightly, "BACKUP_MARGIN", 10)
    fake_export(monkeypatch, nightly)
    waits = []
    monkeypatch.setattr(nightly, "wait_for_pieces", lambda max_wait=None: waits.append(max_wait) or object())
    nightly.run()
    assert waits and waits[0] <= 90 and waits[0] < nightly.PIECES_WAIT


def test_export_records_last_export_at_the_end_and_whether_it_was_partial(tmp_path, monkeypatch):
    monkeypatch.setenv("INKVAULT_HOME", str(tmp_path))
    from inkvault import export
    monkeypatch.setattr(export.PiecesOS, "find", staticmethod(lambda: (SlowPiecesOS(), "12.0")))
    assert export.run(budget_seconds=0.3) == "partial"
    meta = dict(sqlite3.connect(tmp_path / "vault.db").execute("SELECT key, value FROM meta"))
    assert meta["last_export"] and meta["last_export_partial"] == "1"
    assert export.run() is True
    meta = dict(sqlite3.connect(tmp_path / "vault.db").execute("SELECT key, value FROM meta"))
    assert meta["last_export_partial"] == "0"


def test_status_says_when_the_last_export_was_partial(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("INKVAULT_HOME", str(tmp_path))
    monkeypatch.setenv("INKVAULT_PIECES_PORTS", "1")
    from inkvault import cli, export
    monkeypatch.setattr(export.PiecesOS, "find", staticmethod(lambda: (SlowPiecesOS(), "12.0")))
    export.run(budget_seconds=0.3)
    monkeypatch.setattr(export.PiecesOS, "find", staticmethod(lambda: (None, None)))
    assert cli.main(["status"]) == 0
    assert "(partial; continues next run)" in capsys.readouterr().out


def add_tagged_summaries(tags, summaries):
    """Extra tag records and summaries for the topic tests. tags: {id: text}; summaries: [(id, created, [tag ids])]."""
    from inkvault import export
    db = export.open_vault()
    db.executemany("INSERT INTO raw_records VALUES (?,?,?)",
                   [rec("tag", i, text=text) for i, text in tags.items()] +
                   [rec("summary", sid, name=f"Session {sid}", created={"value": created},
                        tags={"indices": {t: n for n, t in enumerate(ids)}}) for sid, created, ids in summaries])
    db.commit()
    db.close()


def test_index_stores_each_summarys_normalized_tags_once(vault):
    from inkvault import index, paths
    from inkvault.times import local
    add_tagged_summaries({"t1": "Stripe-Integration", "t2": "stripe integration ", "t3": "   ", "t4": "Billing"},
                         [("s20", "2026-03-02T00:30:00Z", ["t1", "t2", "t3", "t4", "missing"])])
    index.build()
    db = paths.connect_ro(paths.search_db())
    rows = db.execute("SELECT summary_id, day, tag FROM summary_tags ORDER BY tag").fetchall()
    db.close()
    day = local("2026-03-02T00:30:00Z").date().isoformat()  # the user's local day, not the UTC date
    assert rows == [("s20", day, "billing"), ("s20", day, "stripe integration")]


def test_tags_land_on_the_users_local_day_not_the_utc_date(vault, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from inkvault import index, paths
    utc_minus_8 = timezone(timedelta(hours=-8))  # a fixed zone, so this runs the same on any machine (CI is UTC)
    monkeypatch.setattr(index, "local", lambda ts: datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(utc_minus_8))
    add_tagged_summaries({"t1": "Billing"}, [("s21", "2026-03-02T00:30:00Z", ["t1"])])
    index.build()
    db = paths.connect_ro(paths.search_db())
    assert db.execute("SELECT day FROM summary_tags WHERE summary_id='s21'").fetchone() == ("2026-03-01",)
    db.close()


def test_dashboard_topics_card_data(vault):
    from inkvault import dashboard, index, paths
    week = [f"2026-03-0{d}T12:00:00Z" for d in range(2, 9)]  # 7 sessions in one week: a burst
    add_tagged_summaries({"t1": "Harbor Launch", "t2": "rare"},
                         [(f"s{30 + n}", ts, ["t1"] + (["t2"] if n == 0 else [])) for n, ts in enumerate(week)])
    index.build()
    db = paths.connect_ro(paths.search_db())
    data = dashboard.collect(db)
    db.close()
    assert data["topics"] == {"ongoing": [], "bursts": ["harbor launch"]}
    assert len(data["topic_docs"]) == 7 and all(found == [0] for _, found in data["topic_docs"])
    assert [day for day, _ in data["topic_docs"]] == sorted(day for day, _ in data["topic_docs"])
    html = dashboard.build().read_text(encoding="utf-8")
    assert '"bursts":["harbor launch"]' in html and "/*DATA*/null" not in html


def test_dashboard_without_tags_or_with_an_old_index_has_no_topics(vault):
    import sqlite3
    from inkvault import dashboard, index, paths
    index.build()
    db = paths.connect_ro(paths.search_db())
    assert dashboard.topic_data(db) == ({"ongoing": [], "bursts": []}, [])  # the vault has no tags
    db.close()
    rw = sqlite3.connect(paths.search_db())
    rw.execute("DROP TABLE summary_tags")  # as built by InkVault 0.1.x
    rw.commit()
    rw.close()
    db = paths.connect_ro(paths.search_db())
    assert dashboard.collect(db)["topics"] == {"ongoing": [], "bursts": []}
    db.close()
    assert dashboard.build() is not None
