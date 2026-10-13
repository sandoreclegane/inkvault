"""Forgetting screen data (spec §8, R3, R6): whole overlapping stretches; recorded targets; UTC days ±1."""
import json

import pytest

S = lambda d, h: f"2026-10-{d:02d}T{h:02d}:00:00.000000Z"


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setenv("INKVAULT_HOME", str(tmp_path))
    from inkvault import export
    v = export.open_vault()
    v.executemany("INSERT INTO screen_stretches VALUES (?,?,?,?,?)", [
        ("screen:a", S(11, 9), S(11, 11), "Discord.exe", "Chat"),
        ("screen:b", S(11, 23), S(12, 1), "Notion.exe", "Plan"),
        ("screen:c", S(12, 5), S(12, 5), "Discord.exe", "Zero"),
    ])
    v.execute("INSERT INTO screen_text VALUES ('screentext:1','screen:a',?,'hello there everyone','h')", (S(11, 10),))
    v.commit()
    yield v
    v.close()


def rng(since, until):
    return json.dumps({"since": since, "until": until})


def test_range_inside_one_stretch_removes_it_whole(vault):
    from inkvault import forget
    n, lo, hi = forget.screen_request("screen-range", rng(S(11, 10), "2026-10-11T10:30:00.000000Z"))
    assert (n, lo, hi) == (1, S(11, 9), S(11, 11)) and forget.apply_pending() == 1
    assert [r[0] for r in vault.execute("SELECT id FROM screen_stretches ORDER BY id")] == ["screen:b", "screen:c"]
    assert vault.execute("SELECT COUNT(*) FROM screen_text").fetchone()[0] == 0


def test_edges_match_timeline(vault):
    from inkvault import forget
    assert forget.screen_request("screen-range", rng(S(12, 5), S(12, 6)))[0] == 1


def test_app_removal_records_targets_and_bumps_the_generation(vault):
    from inkvault import forget, screen
    assert forget.screen_request("screen-app", "discord.exe")[0] == 2 and screen.removal_gen(vault) == 1
    assert {r[0] for r in vault.execute("SELECT stretch_id FROM screen_removal_targets")} == {"screen:a", "screen:c"}


def test_a_watcher_write_after_the_request_survives_a_delayed_apply(vault):    # R3 with the real writer
    from inkvault import forget, screenstore
    forget.screen_request("screen-app", "Discord.exe")
    c = screenstore.connect()
    try:
        screenstore.apply(c, screenstore.Write("open", "screen:new", 1, started=S(13, 9), ended=S(13, 9),
                                               app="Discord.exe", title="Later"))
    finally:
        c.close()
    forget.apply_pending()                                              # e.g. the next day
    assert [r[0] for r in vault.execute("SELECT id FROM screen_stretches WHERE app='Discord.exe'")] == ["screen:new"]


def test_an_interrupted_request_records_nothing(vault, monkeypatch):
    from inkvault import forget, screen
    monkeypatch.setattr(forget, "screen_days", lambda spans: (_ for _ in ()).throw(RuntimeError("stop")))
    with pytest.raises(RuntimeError):
        forget.screen_request("screen-app", "Discord.exe")
    assert screen.removal_gen(vault) == 0
    assert vault.execute("SELECT COUNT(*) FROM screen_removal_targets").fetchone()[0] == 0
    assert vault.execute("SELECT COUNT(*) FROM browser_removals").fetchone()[0] == 0


def test_days_cover_every_utc_day_plus_neighbors():
    from inkvault import forget
    assert forget.screen_days([(S(11, 23), S(12, 1))]) == {"2026-10-10", "2026-10-11", "2026-10-12", "2026-10-13"}


def test_nothing_matched_records_nothing(vault):
    from inkvault import forget, paths, screen
    assert forget.screen_request("screen-app", "Nope.exe") == (0, None, None)
    assert screen.removal_gen(vault) == 0 and not paths.rebuild_marker().exists()


def test_unknown_kinds_are_errors_and_sites_still_work():
    from inkvault import forget
    with pytest.raises(ValueError):
        forget.matcher("screen-everything", "x")
    pick = forget.matcher("site", "bank.example")
    assert pick("Default", "https://login.bank.example/") and not pick("Default", "https://docs.example.com/")



def test_preview_of_a_pre_screen_vault_is_empty(vault):
    from inkvault import forget
    vault.executescript("DROP TABLE screen_text; DROP TABLE screen_stretches;")
    assert forget.screen_preview("screen-app", "Discord.exe") == (0, None, None)
