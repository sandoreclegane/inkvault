"""Time helpers (spec §6): one format, one overlap rule, MCP bounds (UTC convention), forget bounds (local input)."""
from datetime import datetime, timezone

import pytest
from zoneinfo import ZoneInfo

CHI = ZoneInfo("America/Chicago")  # 2026: clocks go forward Mar 8 02:00, back Nov 1 02:00
S = lambda h: f"2026-10-12T{h:02d}:00:00.000000Z"


def test_iso_round_trip():
    from inkvault import screen
    t = datetime(2026, 10, 12, 9, 3, 7, 120000, tzinfo=timezone.utc)
    assert screen.iso(t) == "2026-10-12T09:03:07.120000Z" and screen.parse(screen.iso(t)) == t


def test_overlap_rule_and_sql_agree():
    import sqlite3
    from inkvault import screen
    rows = [(1, S(0), S(5)), (2, S(5), S(5)), (3, S(5), S(6)), (4, S(10), S(11)), (5, S(0), S(4))]
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE t (id, started, ended)")
    db.executemany("INSERT INTO t VALUES (?,?,?)", rows)
    got = [r[0] for r in db.execute("SELECT id FROM t WHERE 1=1" + screen.overlap_sql() + " ORDER BY id",
                                    [S(10), S(5)])]
    assert got == [i for i, a, b in rows if screen.overlaps(a, b, S(5), S(10))] == [1, 2, 3]


def test_mcp_bounds_utc_convention_and_nonempty():
    from inkvault import screen
    now = datetime(2026, 10, 12, 15, 0, tzinfo=timezone.utc)
    assert screen.bounds("2026-10-11", "2026-10-11", now) == ("2026-10-11T00:00:00.000000Z",
                                                                "2026-10-12T00:00:00.000000Z")
    assert screen.bounds("2026-10-12T14:30", "", now) == ("2026-10-12T14:30:00.000000Z", screen.iso(now))
    assert screen.bounds("2026-10-12T09:30-05:00", "", now)[0] == "2026-10-12T14:30:00.000000Z"
    for since, until in (("2026-10-12", "2026-10-11"),            # reversed dates [P9]
                         ("2026-10-12T10:00", "2026-10-12T10:00"),  # empty
                         ("yesterday", ""), ("2026-13-01", "")):
        with pytest.raises(ValueError):
            screen.bounds(since, until, now)


def test_local_bounds_naive_is_local_and_offsets_are_respected():
    from inkvault import screen
    local = screen.local_bounds("2026-10-12T10:00", "2026-10-12T10:30", tz=CHI)
    explicit = screen.local_bounds("2026-10-12T10:00-05:00", "2026-10-12T15:30Z", tz=CHI)
    assert local == explicit == ("2026-10-12T15:00:00.000000Z", "2026-10-12T15:30:00.000000Z")
    assert screen.local_bounds("2026-10-12", "2026-10-12", tz=CHI) == ("2026-10-12T05:00:00.000000Z",
                                                                        "2026-10-13T05:00:00.000000Z")


def test_local_bounds_daylight_saving():
    from inkvault import screen
    # the repeated hour widens: earliest instant for since, latest for until
    assert screen.local_bounds("2026-11-01T01:30", "2026-11-01T01:30", tz=CHI) == (
        "2026-11-01T06:30:00.000000Z", "2026-11-01T07:30:00.000000Z")
    with pytest.raises(ValueError, match="doesn't exist"):          # the skipped hour
        screen.local_bounds("2026-03-08T02:30", "2026-03-08T04:00", tz=CHI)


def test_vault_has_the_screen_tables(tmp_path, monkeypatch):
    monkeypatch.setenv("INKVAULT_HOME", str(tmp_path))
    from inkvault import export
    db = export.open_vault()
    try:
        names = {r[0] for r in db.execute("SELECT name FROM sqlite_master")}
        assert {"screen_stretches", "screen_text", "screen_removal_targets", "screen_stretches_started",
                "screen_stretches_ended", "screen_stretches_app", "screen_text_stretch"} <= names
    finally:
        db.close()
