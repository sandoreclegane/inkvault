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
