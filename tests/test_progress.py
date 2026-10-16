import io
import re
import threading
import time

import numpy as np
import pytest

from inkvault import term
from inkvault.term import Progress, Term


def stream(tty=True, encoding="utf-8"):
    class S(io.TextIOWrapper):
        def isatty(self):
            return tty

    return S(io.BytesIO(), encoding=encoding, errors="strict", newline="")


def written(s):
    s.flush()
    return s.buffer.getvalue().decode(s.encoding)


def mk(tty, monkeypatch):
    monkeypatch.setattr(term, "enable_vt", lambda: True)
    s = stream(tty)
    return Term.detect(s, stream(tty), {}), s


def test_reporter_stops_on_keyboard_interrupt(monkeypatch):
    t, s = mk(True, monkeypatch)
    assert t.styled
    with pytest.raises(KeyboardInterrupt):
        with Progress(t) as p:
            p.stage("x")
            time.sleep(0.25)
            raise KeyboardInterrupt
    assert not [th for th in threading.enumerate() if th.name == "Progress"]
    after = written(s)
    time.sleep(0.3)
    assert written(s) == after
    assert after.endswith("\r\x1b[K")  # the live line is cleared, not left dangling


def test_plain_progress_lines(monkeypatch):
    t, s = mk(False, monkeypatch)
    with Progress(t) as p:
        p.stage("x")
        p.update(1, 2)
    assert written(s) == "Building search: x...\n"
    assert "\r" not in written(s)


def test_finish_lines(monkeypatch):
    t, s = mk(False, monkeypatch)
    with Progress(t) as p:
        p.finish("done")
        p.finish("trouble", ok=False)
    assert written(s) == "  ✓ done\n  ! trouble\n"


def test_bar_reaches_total(monkeypatch):
    t, s = mk(True, monkeypatch)
    with Progress(t) as p:
        p.stage("meaning search")
        p.update(5, 5)
        time.sleep(0.25)
    assert "100%" in written(s)


# What index.build() printed on the base commit, on this synthetic vault (timing normalised).
BASELINE = "indexed: 1 events, 0 summaries, 0 messages, 0 snippets, 0 pages\nmeaning search: 1 records in Ns\n"


def test_none_progress_output_unchanged(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("INKVAULT_HOME", str(tmp_path))
    from inkvault import embed, export, index

    class FakeModel:
        def encode(self, texts, **_):
            return np.ones((len(texts), 4), dtype=np.float32)

    monkeypatch.setattr(embed, "load_model", FakeModel)
    v = export.open_vault()
    v.execute("INSERT INTO screen_stretches VALUES ('screen:a','2026-10-12T09:00:00.000000Z',"
              "'2026-10-12T09:30:00.000000Z','Discord.exe','Retry chat')")
    v.execute("INSERT INTO screen_text VALUES ('screentext:1','screen:a','2026-10-12T09:01:00.000000Z',"
              "'exponential backoff helper discussion','h')")
    v.commit()
    v.close()
    index.build()
    assert re.sub(r" in \d+s", " in Ns", capsys.readouterr().out) == BASELINE


def test_unstyled_tty_is_plain(monkeypatch):
    monkeypatch.setattr(term, "enable_vt", lambda: True)
    s = stream(True)
    t = Term.detect(s, stream(True), {"NO_COLOR": "1"})
    assert t.new_output and not t.styled
    with Progress(t) as p:
        p.stage("meaning search")
        p.update(1, 2)
        time.sleep(0.25)
    out = written(s)
    assert out == "Building search: meaning search...\n"
    assert "\r" not in out and "" not in out


def test_download_stage_is_a_plain_line(monkeypatch):
    t, s = mk(True, monkeypatch)
    with Progress(t) as p:
        p.stage("meaning search")
        time.sleep(0.25)
        p.stage("Downloading", animate=False)
        mark = len(written(s))
        time.sleep(0.3)  # a library writing to stderr here would not be overdrawn
        assert len(written(s)) == mark
    assert "Building search: Downloading...\n" in written(s)


def test_index_build_with_progress(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("INKVAULT_HOME", str(tmp_path))
    from inkvault import embed, export, index
    seen = []
    monkeypatch.setattr(embed, "build", lambda progress=None: seen.append(progress))
    export.open_vault().close()

    class P:
        def stage(self, label): seen.append(label)

    p = P()
    index.build(p)
    assert seen == ["keyword index", p]
    assert capsys.readouterr().out == ""


def test_meaning_time_is_elapsed_not_epoch(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("INKVAULT_HOME", str(tmp_path))
    from inkvault import embed, export, index
    monkeypatch.setattr(embed, "load_model", lambda: type("M", (), {
        "encode": lambda self, texts, **_: np.ones((len(texts), 4), dtype=np.float32)})())
    v = export.open_vault()
    v.execute("INSERT INTO screen_stretches VALUES ('screen:a','2026-10-12T09:00:00.000000Z',"
              "'2026-10-12T09:30:00.000000Z','D.exe','t')")
    v.execute("INSERT INTO screen_text VALUES ('screentext:1','screen:a','2026-10-12T09:01:00.000000Z','x y','h')")
    v.commit(); v.close()
    index.build()
    m = re.search(r"in (\d+)s", capsys.readouterr().out)
    assert int(m.group(1)) < 60
