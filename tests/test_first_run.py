"""`inkvault sync` in a terminal: hello, summary, and opening the Atlas; and unchanged output everywhere else."""
import io
import os
import re
import sqlite3
import sys
import threading

import pytest

from test_history import FakeModel, T1, chrome, choose, chromium_visit
from test_sources import SESSION, claude_dir, turn, write


class Stream(io.TextIOWrapper):
    tty = True

    def isatty(self):
        return self.tty


def stream(tty=True, encoding="utf-8"):
    s = Stream(io.BytesIO(), encoding=encoding, errors="strict", newline="")
    s.tty = tty
    return s


def text(s):
    s.flush()
    return s.buffer.getvalue().decode(s.encoding)


class World:
    def __init__(self):
        self.opened = []
        self.out = None


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("INKVAULT_HOME", str(tmp_path / "vault"))
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("TERM", "xterm")
    from inkvault import atlasserve, embed, retrieval, term
    monkeypatch.setattr(embed, "load_model", lambda progress=None: FakeModel())
    monkeypatch.setattr(retrieval, "_model", None)
    monkeypatch.setattr(term, "enable_vt", lambda: True)
    w = World()
    w.home = tmp_path
    monkeypatch.setattr(atlasserve, "start_detached", lambda: w.opened.append(1))
    return w


def terminal(monkeypatch, w, stdin=True, stdout=True, typed=b""):
    w.out = stream(stdout)
    monkeypatch.setattr(sys, "stdout", w.out)
    monkeypatch.setattr(sys, "stdin", stream(stdin))
    sys.stdin.buffer.write(typed)
    sys.stdin.buffer.seek(0)
    return w.out


def one_session():
    write(os.path.join(claude_dir(), "sess-1.jsonl"), *SESSION)


def one_profile(w, monkeypatch, n=2):
    h = chrome(w.home, monkeypatch, "Default")["Default"]
    for i in range(n):
        chromium_visit(h, i + 1, f"https://example.com/{i}", T1)
    choose("chrome/Default")
    return h


def run_sync(*argv):
    from inkvault import cli
    return cli.main(["sync", *argv])


def fail_reading(monkeypatch, bad):
    from inkvault import sources
    real = sources.read_file

    def read_file(db, source, path, keep, name):
        if str(path) == bad:
            raise OSError("locked")
        return real(db, source, path, keep, name)
    monkeypatch.setattr(sources, "read_file", read_file)


# --- non-TTY output is today's output -------------------------------------------------------------------------

BASE_OUTPUT = ("Claude desktop: not found\nCodex: not found\nClaude Code: 1 sessions, 6 new lines\n"
               "Chrome Default (chrome/Default): 2 visits in the file, 2 new\n"
               "indexed: 0 events, 0 summaries, 3 messages, 0 snippets, 2 pages\nmeaning search: 5 records in 0s\n")


def test_non_tty_output_unchanged(world, monkeypatch, capsys):
    one_session()
    one_profile(world, monkeypatch)
    assert run_sync() == 0
    out = capsys.readouterr().out
    assert re.sub(r"in \d+s", "in 0s", out) == BASE_OUTPUT
    assert world.opened == []


# --- auto-open ------------------------------------------------------------------------------------------------

def test_auto_open_when_everything_works(world, monkeypatch):
    one_session()
    one_profile(world, monkeypatch)
    out = terminal(monkeypatch, world)
    assert run_sync() == 0
    shown = text(out)
    assert world.opened == [1]
    assert "Your vault is ready. Opening the Memory Atlas" in shown
    assert shown.rstrip().endswith("Next time: inkvault sync · skip the browser: --no-open")
    assert "\x1b" not in shown and "\r" not in shown
    assert re.search(r"Claude Code +1 conversation +1 with new messages", shown)
    assert re.search(r"Browser history +2 visits +\+2 new \(1 profile\)", shown)
    assert re.search(r"Search +up to date", shown)


@pytest.mark.parametrize("case", ["stdin_redirected", "no_open", "session_partial", "browser_partial", "nothing"])
def test_auto_open_matrix(world, monkeypatch, case):
    if case == "session_partial":
        a = os.path.join(claude_dir(), "sess-a.jsonl")
        b = os.path.join(claude_dir(), "sess-b.jsonl")
        write(a, *SESSION)
        write(b, *SESSION)
        fail_reading(monkeypatch, b)
    elif case == "browser_partial":
        h = chrome(world.home, monkeypatch, "Default", "Profile 1")
        chromium_visit(h["Default"], 1, "https://example.com/d", T1)
        chromium_visit(h["Profile 1"], 1, "https://example.com/p", T1)
        choose("chrome/Default", "chrome/Profile 1")
        h["Profile 1"].write_bytes(b"not a database " * 100)
    elif case != "nothing":
        one_session()
        one_profile(world, monkeypatch)
    out = terminal(monkeypatch, world, stdin=case != "stdin_redirected")
    code = run_sync(*(["--no-open"] if case == "no_open" else []))
    shown = text(out)
    assert world.opened == []
    assert code == (0 if case in ("stdin_redirected", "no_open") else 1)
    assert ("Open it with: inkvault dashboard" in shown) == (case != "nothing")
    assert "Next time" not in shown
    if case == "nothing":
        assert "Nothing to sync" in shown
        assert "inkvault dashboard" not in shown
    if case in ("session_partial", "browser_partial"):
        assert re.search(r"^ +! ", shown, re.M)


def test_open_after_lock_release(world, monkeypatch):
    from inkvault import atlasserve, nightly
    one_session()
    seen = []
    monkeypatch.setattr(atlasserve, "start_detached", lambda: seen.append(nightly.running()))
    terminal(monkeypatch, world)
    assert run_sync() == 0
    assert seen == [False]


def test_launch_oserror_keeps_success(world, monkeypatch):
    from inkvault import atlasserve

    def boom():
        raise OSError("no browser")
    monkeypatch.setattr(atlasserve, "start_detached", boom)
    one_session()
    out = terminal(monkeypatch, world)
    assert run_sync() == 0
    assert "Couldn't open the Atlas; open it with: inkvault dashboard" in text(out)


def test_session_partial_still_builds_search(world, monkeypatch):
    from inkvault import paths
    one_session()
    bad = os.path.join(claude_dir(), "sess-bad.jsonl")
    write(bad, turn("user", "x1", "unreadable words"))
    fail_reading(monkeypatch, bad)
    out = terminal(monkeypatch, world)
    assert run_sync() == 1
    db = sqlite3.connect(paths.search_db())
    try:
        texts = " ".join(r[0] for r in db.execute("SELECT text FROM messages"))
    finally:
        db.close()
    assert "jitter" in texts and "unreadable words" not in texts
    assert world.opened == []
    assert re.search(r"! Claude Code .*1 file", text(out))


def test_search_failure_reported(world, monkeypatch):
    from inkvault import embed

    def no_model(progress=None):
        raise RuntimeError("model unavailable")
    monkeypatch.setattr(embed, "load_model", no_model)
    one_session()
    out = terminal(monkeypatch, world)
    assert run_sync() == 1
    shown = text(out)
    assert re.search(r"^ +! Search +.*model unavailable", shown, re.M)
    assert re.search(r"Claude Code +1 conversation", shown)
    assert world.opened == []


def test_auto_open_when_nothing_new(world, monkeypatch):
    one_session()
    terminal(monkeypatch, world)
    assert run_sync() == 0
    out = terminal(monkeypatch, world)
    assert run_sync() == 0
    assert "0 with new messages" in text(out)
    assert world.opened == [1, 1]


def test_styled_failure_then_nightly_clean(world, monkeypatch):
    from inkvault import embed, nightly, paths
    monkeypatch.delenv("NO_COLOR")
    one_session()

    def boom(progress=None):
        raise RuntimeError("mid-build")
    monkeypatch.setattr(embed, "load_model", boom)
    out = terminal(monkeypatch, world)
    assert run_sync() == 1
    assert "\x1b[" in text(out)  # it really was the styled path
    assert not [t for t in threading.enumerate() if t.name == "Progress"]
    monkeypatch.setattr(embed, "load_model", lambda progress=None: FakeModel())
    monkeypatch.setattr(nightly, "steps", lambda: [("sync", nightly.step_sync), ("index", nightly.step_index)])
    nightly.run()
    log = paths.nightly_log().read_text(encoding="utf-8")
    assert log and "\x1b" not in log and "\r" not in log
    assert not [t for t in threading.enumerate() if t.name == "Progress"]


# --- the fixed message set survives any console ---------------------------------------------------------------

@pytest.mark.parametrize("encoding", ["ascii", "cp1252"])
def test_first_run_messages_survive_strict_streams(encoding):
    from inkvault import cli
    from inkvault.term import Term
    assert cli.FIRST_RUN_MESSAGES
    for msg in cli.FIRST_RUN_MESSAGES:
        s = stream(True, encoding)
        cli.say(Term.detect(s, s, {"NO_COLOR": "1"}), msg)
        s.flush()
        data = s.buffer.getvalue()
        assert data and b"\x1b" not in data, msg
        if encoding == "ascii":
            data.decode("ascii")


def test_browser_question_is_styled_and_parsed_as_before(world, monkeypatch):
    h = chrome(world.home, monkeypatch, "Default")["Default"]
    chromium_visit(h, 1, "https://example.com/", T1)
    out = terminal(monkeypatch, world, typed=b"huh\n1\n")
    code = run_sync()
    shown = text(out)
    assert code == 0, shown
    assert shown.index("InkVault keeps a private copy") < shown.index("Which browser profiles are yours?")
    assert "Please type numbers from the list" in shown
    assert re.search(r"Browser history +1 visit +\+1 new", shown)


def test_non_tty_session_partial_exits_1_with_unchanged_output(world, monkeypatch, capsys):
    from inkvault import paths
    one_session()
    bad = os.path.join(claude_dir(), "sess-bad.jsonl")
    write(bad, turn("user", "x1", "unreadable words"))
    fail_reading(monkeypatch, bad)
    assert run_sync() == 1
    out = re.sub(r"in \d+s", "in 0s", capsys.readouterr().out)
    assert out == ("Claude desktop: not found\nCodex: not found\n"
                   "Claude Code: 2 sessions, 6 new lines, 1 unreadable\n"
                   "Browsers: no profile chosen (no supported browser found)\n"
                   "indexed: 0 events, 0 summaries, 3 messages, 0 snippets, 0 pages\nmeaning search: 3 records in 0s\n")
    assert paths.search_db().exists()


def test_ctrl_c_at_browser_question(world, monkeypatch):
    from inkvault import paths
    h = chrome(world.home, monkeypatch, "Default")["Default"]
    chromium_visit(h, 1, "https://example.com/0", T1)
    out = terminal(monkeypatch, world)

    class Interrupting(io.StringIO):
        def isatty(self):
            return True

        def readline(self, *a):
            raise KeyboardInterrupt
    monkeypatch.setattr(sys, "stdin", Interrupting())
    from inkvault import cli
    code = cli.main(["sync"])
    shown = text(out)
    assert code == 130
    assert "Stopped; nothing was synced." in shown
    assert "Traceback" not in shown
    assert not paths.vault_db().exists()


# --- no stale summary when search isn't rebuilt ---------------------------------------------------------------

def _fail_before_publish(monkeypatch):
    from inkvault import index

    def boom(src, dst):
        raise OSError("disk full")
    monkeypatch.setattr(index.os, "replace", boom)


def _second_session():
    write(os.path.join(claude_dir(), "sess-2.jsonl"), turn("user", "n1", "brand new words"))


def test_stale_summary_not_shown_when_publish_fails(world, monkeypatch):
    one_session()
    terminal(monkeypatch, world)
    assert run_sync("--no-open") == 0
    _second_session()
    _fail_before_publish(monkeypatch)
    out = terminal(monkeypatch, world)
    assert run_sync() == 1
    shown = text(out)
    assert re.search(r"! Claude Code +saved . summary unavailable \(search wasn't rebuilt\)", shown)
    assert "with new messages" not in shown
    assert world.opened == []


def test_counts_shown_when_only_meaning_step_fails(world, monkeypatch):
    from inkvault import embed
    one_session()
    terminal(monkeypatch, world)
    assert run_sync("--no-open") == 0
    _second_session()

    def boom(progress=None):
        raise RuntimeError("model unavailable")
    monkeypatch.setattr(embed, "load_model", boom)
    out = terminal(monkeypatch, world)
    assert run_sync() == 1
    shown = text(out)
    assert "summary unavailable" not in shown
    assert re.search(r"Claude Code +\d+ conversations? +1 with new messages", shown)
    assert world.opened == []


def test_first_run_publish_failure_no_crash(world, monkeypatch):
    one_session()
    _fail_before_publish(monkeypatch)
    out = terminal(monkeypatch, world)
    assert run_sync() == 1
    shown = text(out)
    assert "summary unavailable" in shown
    assert "with new messages" not in shown
    assert world.opened == []
