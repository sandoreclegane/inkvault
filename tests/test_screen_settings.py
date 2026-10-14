"""screen.json (spec §3): its own short lock, typed validation, fail-closed loading, first run vs. lost file."""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

import pytest

HOLD_SCREEN = """
import os, sys
os.environ["INKVAULT_HOME"] = sys.argv[1]
from inkvault import screen
with screen.settings_lock():
    print("held", flush=True)
    sys.stdin.read()
"""


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("INKVAULT_HOME", str(tmp_path))
    return tmp_path


def test_public_defaults():
    from inkvault import screen
    s = screen.validate(screen.defaults())
    assert len(screen.DEFAULT_TEXT_APPS) == 18
    assert not s.text_app("chrome.exe") and not any(s.text_app(a) for a in screen.MESSAGING_APPS)
    assert s.text_app("Comet.EXE") and s.never("1Password.exe", "") and s.never("x.exe", "New InPrivate tab")


@pytest.mark.parametrize("bad", [
    [], {}, {"text_apps": "x"},
    {"text_apps": ["a"], "never_apps": [], "never_titles": [1], "paused_until": None},
    {"text_apps": ["a"], "never_apps": [], "never_titles": [], "paused_until": 5},          # P3: was a crash
    {"text_apps": ["a"], "never_apps": [], "never_titles": [], "paused_until": "soon"},
])
def test_invalid_settings_raise_only_settings_error(bad):
    from inkvault import screen
    with pytest.raises(screen.SettingsError) as e:
        screen.validate(bad)
    assert "soon" not in str(e.value)                                     # never echo contents


def test_edit_is_atomic_and_validated(home):
    from inkvault import paths, screen
    screen.write_atomic(paths.screen_file(), screen.defaults())
    s = screen.edit(lambda d: d["never_titles"].append("Account overview"))
    assert s.never("comet.exe", "Account overview — Bank")
    with pytest.raises(screen.SettingsError):
        screen.edit(lambda d: d.update(text_apps=None))
    assert json.loads(paths.screen_file().read_text())["text_apps"] and not list(home.glob("screen.json.*"))


def test_notepad_bom_is_accepted(home):                                    # Review Focus 3
    from inkvault import paths, screen
    paths.screen_file().write_bytes(b"\xef\xbb\xbf" + json.dumps(screen.defaults()).encode())
    s, err = screen.SettingsSource().poll()
    assert s and err is None


def test_pause_works_while_the_nightly_lock_is_held(home):                 # R1
    import conftest
    from inkvault import paths, screen
    screen.write_atomic(paths.screen_file(), screen.defaults())
    with conftest.holder(home):
        screen.edit(lambda d: d.update(paused_until=screen.iso(screen.parse_pause("1h"))))
    assert screen.validate(screen.read_file()).paused(screen.now())


def test_a_busy_screen_lock_changes_nothing(home):
    from inkvault import paths, screen
    screen.write_atomic(paths.screen_file(), screen.defaults())
    p = subprocess.Popen([sys.executable, "-c", HOLD_SCREEN, str(home)], stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, text=True)
    try:
        assert p.stdout.readline().strip() == "held"
        with pytest.raises(screen.SettingsBusy):
            with screen.settings_lock(wait=0.2):
                pass
    finally:
        p.stdin.close()
        p.wait(timeout=10)


def test_source_recovers_with_the_same_mtime(home):                         # R8, P12: no private resets
    from inkvault import paths, screen
    f = paths.screen_file()
    screen.write_atomic(f, screen.defaults())
    src = screen.SettingsSource()
    assert src.poll()[0] and src.generation == 1
    t1 = f.stat().st_mtime_ns + 10_000_000_000
    f.write_text("{broken", encoding="utf-8"); os.utime(f, ns=(t1, t1))      # a new mtime: reloaded, invalid
    s, err = src.poll()
    assert s is None and "screen.json" in err and "broken" not in err
    f.write_text(json.dumps(screen.defaults()), encoding="utf-8"); os.utime(f, ns=(t1, t1))   # fixed, SAME mtime
    s, err = src.poll()
    assert s and err is None and src.generation == 2


def test_prepare_writes_defaults_once_and_never_after_loss(home):          # R8, P5
    from inkvault import paths, screen
    screen.prepare()
    assert paths.screen_file().exists()
    paths.screen_file().unlink()
    with pytest.raises(screen.SettingsError):
        screen.prepare()
    assert not paths.screen_file().exists()


def test_a_crash_between_defaults_and_marker_keeps_the_file(home):
    from inkvault import export, paths, screen
    screen.write_atomic(paths.screen_file(), {**screen.defaults(), "never_titles": ["Mine"]})
    screen.prepare()
    vault = export.open_vault()
    try:
        assert screen.initialized(vault) and "Mine" in screen.read_file()["never_titles"]
    finally:
        vault.close()


def test_parse_pause():
    from inkvault import screen
    t0 = datetime(2026, 10, 12, 15, 0, tzinfo=timezone.utc)
    assert (screen.parse_pause("30m", t0) - t0).total_seconds() == 1800
    assert (screen.parse_pause("1h", t0) - t0).total_seconds() == 3600
    assert screen.parse_pause("until-tomorrow", t0) > t0
    for bad in ("forever", "0m", "h"):
        with pytest.raises(ValueError):
            screen.parse_pause(bad, t0)


@pytest.mark.parametrize("content", ["[]", "5", "{}", '{"text_apps": [], "never_apps": []}',
                                     '{"text_apps": "x", "never_apps": [], "never_titles": []}'])
def test_edit_checks_the_shape_before_changing_anything(home, content):
    from inkvault import paths, screen
    screen.prepare()
    paths.screen_file().write_text(content, encoding="utf-8")
    called = []
    with pytest.raises(screen.SettingsError):
        screen.edit(lambda d: called.append(1))
    assert called == [] and paths.screen_file().read_text(encoding="utf-8") == content


def test_write_atomic_retries_a_busy_replace(home, monkeypatch):
    from inkvault import paths, screen
    real, fails = os.replace, {"n": 0}

    def flaky(src, dst):
        if fails["n"] < 2:
            fails["n"] += 1
            raise PermissionError("in use")
        return real(src, dst)
    monkeypatch.setattr(os, "replace", flaky)
    monkeypatch.setattr(screen, "REPLACE_WAIT", 0)
    target = paths.home() / "x.json"
    screen.write_atomic(target, {"a": 1})
    assert fails["n"] == 2 and json.loads(target.read_text(encoding="utf-8")) == {"a": 1}
    assert not list(paths.home().glob("*.tmp"))


def test_write_atomic_gives_up_after_five_tries(home, monkeypatch):
    from inkvault import paths, screen
    calls = []
    monkeypatch.setattr(os, "replace", lambda s, d: calls.append(1) or (_ for _ in ()).throw(PermissionError("x")))
    monkeypatch.setattr(screen, "REPLACE_WAIT", 0)
    with pytest.raises(PermissionError):
        screen.write_atomic(paths.home() / "x.json", {})
    assert len(calls) == 5 and not list(paths.home().glob("*.tmp"))
