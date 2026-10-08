"""Browser history: syncing chosen profiles into the vault, counting visits, and pages in search."""
import shutil
import sqlite3
from pathlib import Path

import numpy as np
import pytest

import webfixtures
from webfixtures import CHAIN_END, CHAIN_START, LINK, chromium_visit, firefox_visit


class FakeModel:
    """Stands in for the search model (a download): every text gets the same small vector, so meaning search and
    vectors.npz work for real without it."""
    def encode(self, texts, **_):
        return np.ones((len(texts), 4), dtype=np.float32)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("INKVAULT_HOME", str(tmp_path / "vault"))
    from inkvault import embed, server
    monkeypatch.setattr(embed, "load_model", FakeModel)
    monkeypatch.setattr(server, "_model", None)
    return tmp_path


def test_browser_files_live_in_the_vault_folder(home):
    from inkvault import paths
    assert paths.browsers_file() == paths.home() / "browsers.json"
    assert paths.rebuild_marker() == paths.home() / "rebuild-needed"


def test_the_vault_has_the_browser_tables(home):
    from inkvault import export
    db = export.open_vault()
    try:
        cols = lambda t: [r[1] for r in db.execute(f"PRAGMA table_info({t})")]
        assert cols("browser_visits") == ["id", "profile", "visit_id", "redirected", "created", "url", "address",
                                          "title", "title_observed_at", "duration_s", "transition", "origin",
                                          "origin_visit_id"]
        assert cols("browser_profiles") == ["profile", "last_attempt", "last_success", "last_error", "visits_in_file"]
        assert cols("browser_removals") == ["id", "what", "value", "days", "created"]
    finally:
        db.close()


T1, T2 = "2026-10-05T12:00:00Z", "2026-10-05T12:20:00Z"


def chrome(home, monkeypatch, *folders):
    """Chrome with these profile folders (each named after its folder); returns {folder: History path}."""
    root = home / "chrome"
    histories = {f: webfixtures.chromium_profile(root, f, name=f) for f in folders}
    webfixtures.install(monkeypatch, ("chrome", "chromium", root))
    return histories


def firefox(home, monkeypatch):
    root = home / "firefox"
    places = webfixtures.firefox_profile(root, "abcd.default-release")
    webfixtures.install(monkeypatch, ("firefox", "firefox", root))
    return places


FIREFOX = "firefox/abcd.default-release"


def choose(*keys, no=()):
    from inkvault import browsers
    found = {p.key: p for p in browsers.find_profiles()}
    choices = browsers.load_choices()
    for k in keys:
        browsers.set_choice(choices, found[k], True)
    for k in no:
        browsers.set_choice(choices, found[k], False)
    browsers.save_choices(choices)


def stored(sql="SELECT profile, visit_id, url FROM browser_visits ORDER BY profile, visit_id, url"):
    from inkvault import paths
    db = sqlite3.connect(paths.vault_db())
    try:
        return db.execute(sql).fetchall()
    finally:
        db.close()


def test_nothing_is_read_before_the_user_chooses(home, monkeypatch, capsys):
    from inkvault import history, paths
    h = chrome(home, monkeypatch, "Default", "Profile 1")
    chromium_visit(h["Default"], 1, "https://example.com/", T1)
    r = history.sync()
    assert (r.outcome, r.waiting) == ("nothing", 2)
    assert "2 browser profiles waiting for you to choose: run `inkvault browsers`." in capsys.readouterr().out
    assert not paths.vault_db().exists()


def test_sync_reads_only_the_chosen_profiles(home, monkeypatch):
    from inkvault import history
    h = chrome(home, monkeypatch, "Default", "Profile 2")
    chromium_visit(h["Default"], 1, "https://example.com/a", T1, title="A")
    chromium_visit(h["Profile 2"], 1, "https://someone-else.example/", T1)
    choose("chrome/Default", no=["chrome/Profile 2"])
    r = history.sync()
    assert (r.outcome, r.chosen, r.failed) == ("ok", 1, [])
    assert stored() == [("chrome/Default", 1, "https://example.com/a")]
    assert stored("SELECT created, title FROM browser_visits") == [("2026-10-05T12:00:00.000000Z", "A")]


def test_a_second_sync_adds_only_whats_new(home, monkeypatch):
    from inkvault import history
    h = chrome(home, monkeypatch, "Default")["Default"]
    chromium_visit(h, 1, "https://example.com/a", T1)
    choose("chrome/Default")
    history.sync()
    history.sync()
    assert len(stored()) == 1
    chromium_visit(h, 2, "https://example.com/b", T2)
    history.sync()
    assert [u for _, _, u in stored()] == ["https://example.com/a", "https://example.com/b"]


def test_two_browser_visits_with_the_same_time_and_address_are_both_kept(home, monkeypatch):
    from inkvault import history
    h = chrome(home, monkeypatch, "Default")["Default"]
    chromium_visit(h, 1, "https://example.com/a", T1)
    chromium_visit(h, 2, "https://example.com/a", T1)
    choose("chrome/Default")
    history.sync()
    assert [v for _, v, _ in stored()] == [1, 2]


def test_a_reused_visit_id_with_a_new_address_is_a_new_row_and_the_old_one_stays(home, monkeypatch):
    from inkvault import history
    h = chrome(home, monkeypatch, "Default")["Default"]
    chromium_visit(h, 1, "https://example.com/old", T1)
    choose("chrome/Default")
    history.sync()
    webfixtures.execute(h, "DELETE FROM visits WHERE id=1")  # the browser's cleanup
    chromium_visit(h, 1, "https://example.com/new", T2)
    history.sync()
    assert sorted(u for _, _, u in stored()) == ["https://example.com/new", "https://example.com/old"]


def test_a_later_sync_updates_what_the_browser_changed(home, monkeypatch):
    from inkvault import history
    h = chrome(home, monkeypatch, "Default")["Default"]
    chromium_visit(h, 1, "https://example.com/doc", T1, title="Loading…")
    choose("chrome/Default")
    history.sync()
    webfixtures.execute(h, "UPDATE visits SET visit_duration=?, transition=? WHERE id=1", 42_000_000, CHAIN_START)
    webfixtures.execute(h, "UPDATE urls SET title='Quarterly plan' WHERE id=1")
    monkeypatch.setattr(history, "now", lambda: "2026-10-09T03:00:00Z")
    history.sync()
    assert stored("SELECT title, title_observed_at, duration_s, transition FROM browser_visits") == [
        ("Quarterly plan", "2026-10-09T03:00:00Z", 42.0, CHAIN_START)]


def test_non_http_addresses_and_skipped_sites_are_never_stored(home, monkeypatch):
    from inkvault import browsers, history
    h = chrome(home, monkeypatch, "Default")["Default"]
    for i, url in enumerate(["chrome://settings", "file:///C:/notes.txt", "https://bank.example/login",
                             "https://www.bank.example/", "https://notbank.example/"], 1):
        chromium_visit(h, i, url, T1)
    choose("chrome/Default")
    choices = browsers.load_choices()
    choices["skip_sites"] = ["bank.example"]
    browsers.save_choices(choices)
    history.sync()
    assert [u for _, _, u in stored()] == ["https://notbank.example/"]


def test_a_visit_synced_from_another_device_keeps_where_it_came_from(home, monkeypatch):
    from inkvault import history
    h = chrome(home, monkeypatch, "Default")["Default"]
    chromium_visit(h, 1, "https://example.com/here", T1)
    chromium_visit(h, 2, "https://example.com/phone", T2, guid="phone-guid")
    choose("chrome/Default")
    history.sync()
    assert stored("SELECT visit_id, origin, origin_visit_id FROM browser_visits ORDER BY visit_id") == [
        (1, None, None), (2, "phone-guid", 2)]


def test_an_older_chromium_without_originator_columns_still_syncs(home, monkeypatch):
    from inkvault import history
    root = home / "chrome"
    h = webfixtures.chromium_profile(root, "Default", name="Default", originator=False)
    webfixtures.install(monkeypatch, ("chrome", "chromium", root))
    chromium_visit(h, 1, "https://example.com/", T1)
    choose("chrome/Default")
    assert history.sync().outcome == "ok" and len(stored()) == 1


def test_a_profile_that_cant_be_copied_twice_is_recorded_and_the_others_are_still_read(home, monkeypatch):
    from inkvault import history
    h = chrome(home, monkeypatch, "Default", "Profile 1")
    chromium_visit(h["Default"], 1, "https://example.com/d", T1)
    chromium_visit(h["Profile 1"], 1, "https://example.com/p", T1)
    choose("chrome/Default", "chrome/Profile 1")
    real, tries = shutil.copyfile, []

    def locked(src, dst, *a, **k):
        if Path(src) == h["Default"]:
            tries.append(src)
            raise PermissionError("in use")
        return real(src, dst, *a, **k)
    monkeypatch.setattr(shutil, "copyfile", locked)
    r = history.sync()
    assert (r.outcome, r.chosen, r.failed, len(tries)) == ("partial", 2, ["chrome/Default"], 2)
    assert [p for p, _, _ in stored()] == ["chrome/Profile 1"]
    (error,) = stored("SELECT last_error FROM browser_profiles WHERE profile='chrome/Default'")[0]
    assert error == "PermissionError: in use"


def test_a_copy_that_isnt_a_database_fails_and_every_chosen_profile_failing_is_failed(home, monkeypatch):
    from inkvault import history
    h = chrome(home, monkeypatch, "Default")["Default"]
    choose("chrome/Default")
    h.write_bytes(b"not a database " * 100)
    r = history.sync()
    assert (r.outcome, r.failed) == ("failed", ["chrome/Default"])


def test_firefox_visits_still_in_the_wal_file_are_read(home, monkeypatch):
    from inkvault import history
    places = firefox(home, monkeypatch)
    firefox_visit(places, 1, "https://example.com/old", T1, title="Old")
    live = sqlite3.connect(places)  # like a running Firefox: the newest visit is only in places.sqlite-wal
    try:
        live.execute("PRAGMA wal_autocheckpoint=0")
        firefox_visit(places, 2, "https://example.com/new", T2, title="New", db=live)
        assert places.with_name("places.sqlite-wal").stat().st_size > 0
        choose(FIREFOX)
        assert history.sync().outcome == "ok"
    finally:
        live.close()
    assert [u for _, _, u in stored()] == ["https://example.com/old", "https://example.com/new"]


def search_rows(sql):
    from inkvault import paths
    db = paths.connect_ro(paths.search_db())
    try:
        return db.execute(sql).fetchall()
    finally:
        db.close()


@pytest.mark.parametrize("transition, counted", [
    (LINK, True),                      # an ordinary visit: both chain bits
    (CHAIN_START, False),              # the first hop of a redirect chain
    (CHAIN_END, True),                 # where the chain ended
    (0, True),                         # no chain bits at all
    (LINK | 3, False), (LINK | 4, False),  # sub-frames
    (LINK | 6, True), (LINK | 7, True), (LINK | 8, True),  # automatic top-level, form submit, reload
])
def test_which_chromium_visits_count(transition, counted):
    from inkvault import history
    assert history.counts("chrome/Default", transition, 0) is counted


@pytest.mark.parametrize("visit_type, redirected, counted", [
    (1, 0, True), (5, 0, True), (6, 0, True), (9, 0, True),  # link, redirect destinations, reload
    (4, 0, False), (7, 0, False), (8, 0, False),             # embed, download, framed link
    (1, 1, False),                                           # redirected away from
])
def test_which_firefox_visits_count(visit_type, redirected, counted):
    from inkvault import history
    assert history.counts(FIREFOX, visit_type, redirected) is counted


def test_firefox_redirect_chains_count_only_where_they_end(home, monkeypatch):
    from inkvault import history, index
    places = firefox(home, monkeypatch)
    firefox_visit(places, 1, "https://a.example/", T1)                            # multi-hop: a -> b -> c
    firefox_visit(places, 2, "https://b.example/", T1, visit_type=5, from_visit=1)
    firefox_visit(places, 3, "https://c.example/", T1, visit_type=6, from_visit=2)
    firefox_visit(places, 4, "https://d.example/", T2)                            # branching: d -> e, d -> f
    firefox_visit(places, 5, "https://e.example/", T2, visit_type=5, from_visit=4)
    firefox_visit(places, 6, "https://f.example/", T2, visit_type=6, from_visit=4)
    firefox_visit(places, 7, "https://g.example/file.zip", T2, visit_type=7)      # a download
    choose(FIREFOX)
    history.sync()
    index.build()
    assert search_rows("SELECT host FROM pages ORDER BY host") == [("c.example",), ("e.example",), ("f.example",)]


def test_an_expired_firefox_visit_stays_counted_when_a_new_chain_reuses_its_id(home, monkeypatch):
    from inkvault import history, index
    places = firefox(home, monkeypatch)
    firefox_visit(places, 1, "https://old.example/", T1)
    choose(FIREFOX)
    history.sync()
    webfixtures.execute(places, "DELETE FROM moz_historyvisits WHERE id=1")
    firefox_visit(places, 1, "https://new.example/", T2)  # the id is reused, and redirected away from
    firefox_visit(places, 2, "https://dest.example/", T2, visit_type=5, from_visit=1)
    history.sync()
    index.build()
    assert search_rows("SELECT host FROM pages ORDER BY host") == [("dest.example",), ("old.example",)]


def test_a_chromium_visit_that_loses_its_chain_end_stops_counting(home, monkeypatch):
    from inkvault import history, index
    h = chrome(home, monkeypatch, "Default")["Default"]
    chromium_visit(h, 1, "https://hop.example/", T1)
    choose("chrome/Default")
    history.sync()
    index.build()
    assert search_rows("SELECT COUNT(*) FROM pages") == [(1,)]
    webfixtures.execute(h, "UPDATE visits SET transition=? WHERE id=1", CHAIN_START)  # a client redirect extended it
    history.sync()
    index.build()
    assert search_rows("SELECT COUNT(*) FROM pages") == [(0,)]


def test_pages_are_one_per_address_per_local_day(home, monkeypatch):
    from inkvault import history, index
    h = chrome(home, monkeypatch, "Default")["Default"]
    chromium_visit(h, 1, "https://example.com/a", "2026-10-05T12:00:00Z", title="A")
    chromium_visit(h, 2, "https://example.com/a", "2026-10-05T12:30:00Z")
    chromium_visit(h, 3, "https://example.com/a", "2026-10-08T12:00:00Z")
    # two different raw addresses that clean alike stay two pages
    chromium_visit(h, 4, "https://x.example/reset/Ab3dEf6hIj9kLm2nOp5q", "2026-10-05T12:00:00Z")
    chromium_visit(h, 5, "https://x.example/reset/Zy9xWv8uTs7rQp6oNm5l", "2026-10-05T12:00:00Z")
    choose("chrome/Default")
    history.sync()
    index.build()
    assert search_rows("SELECT url, visits FROM pages ORDER BY url, day") == [
        ("https://example.com/a", 2), ("https://example.com/a", 1),
        ("https://x.example/reset/…", 1), ("https://x.example/reset/…", 1)]
    assert search_rows("SELECT COUNT(*), COUNT(DISTINCT page_id) FROM visits") == [(5, 4)]
    assert all(pid.startswith("web:") and len(pid) == 20 for (pid,) in search_rows("SELECT id FROM pages"))
    assert search_rows("SELECT key FROM meta") == [("timezone",)]


def test_the_title_observed_last_wins_over_a_later_visit_from_another_profile(home, monkeypatch):
    from inkvault import history, index
    url = "https://example.com/doc"
    h = chrome(home, monkeypatch, "Default", "Profile 1")
    chromium_visit(h["Default"], 1, url, "2026-10-05T12:00:00Z", title="Old title")
    chromium_visit(h["Profile 1"], 1, url, "2026-10-05T12:10:00Z", title="Other title")
    choose("chrome/Default", "chrome/Profile 1")
    monkeypatch.setattr(history, "now", lambda: "2026-10-06T00:00:00Z")
    history.sync()
    webfixtures.execute(h["Default"], "UPDATE urls SET title='New title'")
    choose("chrome/Default", no=["chrome/Profile 1"])
    monkeypatch.setattr(history, "now", lambda: "2026-10-07T00:00:00Z")
    history.sync()
    index.build()
    assert search_rows("SELECT title, visits, profiles FROM pages") == [
        ("New title", 2, "chrome/Default,chrome/Profile 1")]


def test_meaning_search_has_a_web_query(home, monkeypatch):
    from inkvault import embed, history, index, paths
    h = chrome(home, monkeypatch, "Default")["Default"]
    chromium_visit(h, 1, "https://docs.example.com/retry", T1, title="Retry with backoff")
    choose("chrome/Default")
    history.sync()
    index.build()
    db = paths.connect_ro(paths.search_db())
    try:
        assert [t for _, t in db.execute(embed.QUERIES["web"])] == ["Retry with backoff\ndocs.example.com/retry"]
    finally:
        db.close()


def test_with_nothing_left_to_embed_the_old_vectors_go(home, monkeypatch):
    from inkvault import history, index, paths
    h = chrome(home, monkeypatch, "Default")["Default"]
    chromium_visit(h, 1, "https://docs.example.com/retry", T1, title="Retry with backoff")
    choose("chrome/Default")
    history.sync()
    index.build()
    assert paths.vectors().exists()
    db = sqlite3.connect(paths.vault_db())
    db.execute("DELETE FROM browser_visits")
    db.commit()
    db.close()
    index.build()
    assert not paths.vectors().exists()


def test_an_old_vault_without_browser_tables_still_indexes(home):
    from inkvault import export, index, paths
    db = export.open_vault()
    db.execute("DROP TABLE browser_visits")
    db.commit()
    db.close()
    assert index.build()
    assert search_rows("SELECT COUNT(*) FROM pages") == [(0,)]


def test_a_failed_search_build_leaves_no_file_open(home, monkeypatch):
    from inkvault import export, history, index, paths
    export.open_vault().close()

    def broken(raw, db):
        raise RuntimeError("index broke")
    monkeypatch.setattr(history, "index_into", broken)
    with pytest.raises(RuntimeError):
        index.build()
    paths.search_db().with_suffix(".tmp").unlink()  # Windows can't delete a file that is still open
    paths.vault_db().unlink()


def web_vault(home, monkeypatch):
    """One page opened twice on one day: at 12:00 UTC here, at 12:20 UTC on a phone (synced)."""
    from inkvault import history, index
    h = chrome(home, monkeypatch, "Default")["Default"]
    chromium_visit(h, 1, "https://docs.example.com/retry", T1, title="Retry with backoff")
    chromium_visit(h, 2, "https://docs.example.com/retry", T2, guid="phone-guid")
    choose("chrome/Default")
    history.sync()
    index.build()
    return search_rows("SELECT day FROM pages")[0][0]


def test_web_pages_in_search_get_memory_timeline_and_stats(home, monkeypatch):
    from inkvault import server
    day = web_vault(home, monkeypatch)
    hits = server.search_memories("backoff", source="web", mode="keyword")
    assert hits.startswith("[web] web:") and "Retry with backoff — docs.example.com" in hits
    body = server.get_memory(hits.split()[1])
    assert "https://docs.example.com/retry" in body and "(title as observed at sync)" in body
    assert body.count("chrome/Default") == 3 and "synced from another device" in body
    assert f"{day}  [web] 1 pages; most visited: docs.example.com" in server.timeline(since=day, until=day)
    stats = server.memory_stats()
    assert "web: 2 visits (1 synced from other devices), 1 pages" in stats
    assert "visits by browser: Chrome 2" in stats
    assert "[web]" in server.search_memories("backoff", mode="keyword")  # "all" includes web
    assert "[web]" in server.search_memories("anything at all", source="web", mode="meaning")


def test_web_time_ranges_follow_every_visit_not_just_the_first(home, monkeypatch):
    from inkvault import server
    web_vault(home, monkeypatch)  # visits at 12:00 and 12:20 UTC

    def found(**bounds):
        return "[web]" in server.search_memories("backoff", source="web", mode="keyword", **bounds)
    assert found(since="2026-10-05T12:10") and found(until="2026-10-05T12:10")
    assert found(since="2026-10-05T12:10", until="2026-10-05T12:30")
    assert not found(since="2026-10-05T12:30")
    assert not found(since="2026-10-05T12:21", until="2026-10-05T12:30")


@pytest.mark.parametrize("hours, when, day", [
    (14, "2026-10-05T12:00:00Z", "2026-10-06"),   # UTC+14: already the next day
    (-11, "2026-10-05T05:00:00Z", "2026-10-04"),  # UTC-11: still the day before
    (0, "2026-10-05T23:59:00Z", "2026-10-05"),    # a minute before midnight
])
def test_web_date_ranges_follow_the_local_day(home, monkeypatch, hours, when, day):
    from datetime import date, datetime, timedelta, timezone
    from inkvault import history, index, server
    zone = timezone(timedelta(hours=hours))
    monkeypatch.setattr(history, "local",
                        lambda ts: datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(zone))
    h = chrome(home, monkeypatch, "Default")["Default"]
    chromium_visit(h, 1, "https://docs.example.com/retry", when, title="Retry with backoff")
    choose("chrome/Default")
    history.sync()
    index.build()
    assert search_rows("SELECT day FROM pages") == [(day,)]
    before = (date.fromisoformat(day) - timedelta(days=1)).isoformat()
    after = (date.fromisoformat(day) + timedelta(days=1)).isoformat()

    def found(since, until):
        return "[web]" in server.search_memories("backoff", source="web", mode="keyword", since=since, until=until)
    assert found(day, day) and found(before, day) and found(day, after)
    assert not found(after, after) and not found(before, before)


def test_web_time_bounds_compare_as_times_not_text(home, monkeypatch):
    from inkvault import server
    web_vault(home, monkeypatch)  # the phone visit is at exactly 12:20:00 UTC

    def found(**bounds):
        return "[web]" in server.search_memories("backoff", source="web", mode="keyword", **bounds)
    for at in ("2026-10-05T12:20:00Z", "2026-10-05T12:20:00.000Z", "2026-10-05T12:20", "2026-10-05T14:20:00+02:00"):
        assert found(since=at), at                    # since is inclusive
        assert not found(since=at, until=at), at       # until is exclusive
    assert server.search_memories("backoff", since="yesterday").startswith("Bad date")


def test_an_index_from_before_0_3_0_has_no_web_source_yet(home, monkeypatch):
    from inkvault import paths, server
    web_vault(home, monkeypatch)
    db = sqlite3.connect(paths.search_db())
    db.executescript("DROP TABLE pages_fts; DROP TABLE pages; DROP TABLE visits;")
    db.close()
    assert server.search_memories("backoff", source="web") == \
        "Web pages aren't in the search index yet: run `inkvault index`."
    assert "No matches" in server.search_memories("backoff", mode="keyword")
    assert "web:" not in server.memory_stats()
    server.timeline(since="2026-10-01")  # no error


def test_the_tools_refuse_while_a_removal_is_unfinished(home, monkeypatch):
    from inkvault import paths, server
    web_vault(home, monkeypatch)
    paths.rebuild_marker().write_text("removing", encoding="utf-8")
    for answer in (server.search_memories("backoff"), server.get_memory("web:x"), server.timeline(since="2026-10-01"),
                   server.memory_stats()):
        assert answer == server.REBUILDING


def test_a_tool_whose_files_were_replaced_while_it_worked_asks_again(home, monkeypatch):
    from inkvault import paths, server
    web_vault(home, monkeypatch)
    real = server.keyword_ranked

    def slow(*a, **k):
        rows = real(*a, **k)
        paths.vectors().write_bytes(b"replaced")  # stands in for a search build publishing new files meanwhile
        return rows
    monkeypatch.setattr(server, "keyword_ranked", slow)
    assert server.search_memories("backoff", mode="keyword") == server.CHANGED
