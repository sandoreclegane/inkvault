"""Screen time by duration (spec §6, F8, P7): split by local hour stepping through UTC; Pieces-only counts;
screen-only digest days; no screen text in digests."""
import sqlite3

from zoneinfo import ZoneInfo

CHI = ZoneInfo("America/Chicago")
SCHEMA = """
    CREATE TABLE events (id, created, app, window_title, url, readable, source, stretch_id);
    CREATE TABLE summaries (id, created, name, text, event_ids);
    CREATE TABLE messages (id, created, conversation_id, conversation_name, role, text, source);
    CREATE TABLE snippets (id, created, name, language, text);
    CREATE TABLE summary_tags (summary_id, created, tag); CREATE TABLE meta (key, value);
    CREATE TABLE screen_stretches (id, started, ended, app, title);
"""


def test_split_across_the_fall_back_hour_keeps_elapsed_time():
    from inkvault import screentime
    parts = screentime.split("2026-11-01T05:30:00.000000Z", "2026-11-01T08:30:00.000000Z", tz=CHI)
    assert sum(p[2] for p in parts) == 3 * 3600
    assert sum(p[2] for p in parts if p[1] == 1) == 7200            # the repeated 1 o'clock hour, twice


def test_split_across_spring_forward_and_midnight():
    from inkvault import screentime
    parts = screentime.split("2026-03-08T07:30:00.000000Z", "2026-03-08T09:30:00.000000Z", tz=CHI)
    # 01:30–02:00 CST, then the clocks jump: 03:00–04:00 and 04:00–04:30 CDT (R5: the old {1, 3} was wrong)
    assert sum(p[2] for p in parts) == 7200 and [(p[1], p[2]) for p in parts] == [(1, 1800), (3, 3600), (4, 1800)]
    parts = screentime.split("2026-10-12T04:30:00.000000Z", "2026-10-12T05:45:00.000000Z", tz=CHI)
    assert [(p[0], p[1]) for p in parts] == [("2026-10-11", 23), ("2026-10-12", 0)]


def test_half_hour_offsets_label_by_local_hour():
    from inkvault import screentime
    parts = screentime.split("2026-10-12T04:00:00.000000Z", "2026-10-12T05:00:00.000000Z", tz=ZoneInfo("Asia/Kolkata"))
    assert [(p[1], p[2]) for p in parts] == [(9, 1800), (10, 1800)]          # 09:30–10:00, 10:00–10:30 IST


def test_a_full_mixed_day_keeps_its_screen_summary(tmp_path, monkeypatch):   # R5: budgets reserved together
    monkeypatch.setenv("INKVAULT_HOME", str(tmp_path))
    from inkvault import digest, screen
    monkeypatch.setattr(screen, "LOCAL_TZ", CHI)
    db = sqlite3.connect(":memory:")
    db.executescript(SCHEMA + """
        CREATE TABLE pages (id, day, url, title, host, path, profiles, visits);
        CREATE TABLE visits (id, page_id, created, profile, host, synced);
        INSERT INTO screen_stretches VALUES ('screen:a','2026-10-12T17:00:00.000000Z','2026-10-12T18:10:00.000000Z','Discord.exe','Chat');
    """)
    db.executemany("INSERT INTO summaries VALUES (?,?,?,?,?)",
                   [(f"s{i}", "2026-10-12T17:30:00Z", "x" * 300 + str(i), "", "[]") for i in range(200)])
    db.executemany("INSERT INTO pages VALUES (?,?,?,?,?,?,?,?)",
                   [(f"p{i}", "2026-10-12", f"https://h{i}.example/", "t" * 80 + str(i), f"h{i}.example", "/", "", 1)
                    for i in range(40)])
    db.executemany("INSERT INTO visits VALUES (?,?,?,?,?,?)",
                   [(f"v{i}", f"p{i}", "2026-10-12T17:40:00Z", "p", f"h{i}.example", 0) for i in range(40)])
    text = digest.gather(db)["2026-10-12"]
    assert len(text) <= digest.MAX_INPUT and "Screen time by app: Discord 1h 10m" in text and "Pages opened" in text


def test_old_index_and_formatting():
    from inkvault import screentime
    old = sqlite3.connect(":memory:"); old.execute("CREATE TABLE events (id, created, app)")
    assert screentime.pieces_events_sql(old) == "" and screentime.stretches(old) == []
    assert screentime.fmt(4200) == "1h 10m" and screentime.fmt(59) == "0m" and screentime.app_name("Code.EXE") == "Code"


def test_dashboard_counts_pieces_only_and_screen_minutes(tmp_path, monkeypatch):
    monkeypatch.setenv("INKVAULT_HOME", str(tmp_path))
    from inkvault import dashboard, screen
    monkeypatch.setattr(screen, "LOCAL_TZ", CHI)            # same local day on any test machine
    db = sqlite3.connect(":memory:")
    db.executescript(SCHEMA + """
        INSERT INTO events VALUES ('e1','2026-10-12T15:00:00Z','Code.exe',NULL,NULL,NULL,'pieces',NULL);
        INSERT INTO events VALUES ('s1','2026-10-12T15:01:00Z','Discord.exe',NULL,NULL,'x','screen','screen:a');
        INSERT INTO screen_stretches VALUES ('screen:a','2026-10-12T15:00:00.000000Z','2026-10-12T15:30:00.000000Z','Discord.exe','t');
    """)
    data = dashboard.collect(db)    # add an empty table above for anything else collect() reads
    day = next(iter(data["days"]))
    assert data["days"][day]["captures"] == 1 and data["screen_apps"][day] == {"Discord": 30}


def test_digest_screen_only_day_by_duration_without_text(tmp_path, monkeypatch):
    monkeypatch.setenv("INKVAULT_HOME", str(tmp_path))
    from inkvault import digest, screen
    monkeypatch.setattr(screen, "LOCAL_TZ", CHI)            # 12:00–13:10 local: one day on any test machine
    db = sqlite3.connect(":memory:")
    db.executescript(SCHEMA + """
        INSERT INTO events VALUES ('s1','2026-10-12T17:01:00Z','Discord.exe','Chat',NULL,'SECRET OCR WORDS','screen','screen:a');
        INSERT INTO screen_stretches VALUES ('screen:a','2026-10-12T17:00:00.000000Z','2026-10-12T18:10:00.000000Z','Discord.exe','Chat');
    """)
    material = digest.gather(db)
    text = "\n".join(material.values())
    assert material and "Discord 1h 10m" in text and "Chat (1h 10m)" in text and "SECRET OCR WORDS" not in text
