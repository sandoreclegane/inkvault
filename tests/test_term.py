import io
import sys

import pytest

from inkvault import term


def stream(encoding="utf-8", tty=True):
    class S(io.TextIOWrapper):
        def isatty(self):
            return tty

    return S(io.BytesIO(), encoding=encoding, errors="strict", newline="")


# Every non-ASCII glyph the styled output uses.
MESSAGES = [
    "Reading your sessions… done",
    "3 sources · 12 memories",
    "InkVault — your memory",
    "◆ InkVault",
    "✓ synced",
    "━━━━╺╺╺╺",
    "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏ working",
]


@pytest.mark.parametrize("stdout_tty", [True, False])
@pytest.mark.parametrize("stdin_tty", [True, False])
@pytest.mark.parametrize("no_color", [True, False])
@pytest.mark.parametrize("dumb", [True, False])
@pytest.mark.parametrize("vt_ok", [True, False])
def test_decisions_matrix(monkeypatch, stdout_tty, stdin_tty, no_color, dumb, vt_ok):
    monkeypatch.setattr(term, "enable_vt", lambda: vt_ok)
    env = {}
    if no_color:
        env["NO_COLOR"] = "1"
    if dumb:
        env["TERM"] = "dumb"
    t = term.Term.detect(stream(tty=stdout_tty), stream(tty=stdin_tty), env)
    assert t.new_output == stdout_tty
    assert t.interactive == (stdin_tty and stdout_tty)
    assert t.styled == (stdout_tty and not no_color and not dumb and vt_ok)


@pytest.mark.parametrize("encoding", ["utf-8", "ascii"])
def test_plain_has_no_escape(monkeypatch, encoding):
    monkeypatch.setattr(term, "enable_vt", lambda: True)
    t = term.Term.detect(stream(encoding, tty=False), stream(tty=False), {})
    assert not t.styled
    parts = [f(" x ") for f in (t.gold, t.teal, t.amber, t.dim, t.bold)]
    parts += [t.mark, t.ok, t.warn, t.dot, t.ell, t.bar_full, t.bar_empty, *t.frames]
    out = "".join(parts)
    assert "\x1b" not in out and "\r" not in out


def test_styled_wraps_with_reset(monkeypatch):
    monkeypatch.setattr(term, "enable_vt", lambda: True)
    t = term.Term.detect(stream(), stream(), {})
    assert t.styled
    assert t.gold("x") == "\x1b[38;2;201;168;76mx\x1b[0m"


@pytest.mark.parametrize("encoding", ["cp1252", "ascii"])
def test_full_message_set_survives_narrow_encoding(monkeypatch, encoding):
    monkeypatch.setattr(term, "enable_vt", lambda: True)
    out = stream(encoding)
    t = term.Term.detect(out, stream(), {})
    assert t.ascii is True
    for m in MESSAGES:
        before = len(out.buffer.getvalue())
        t.write(m)
        out.flush()
        written = out.buffer.getvalue()[before:]
        assert written == (m + "\n").encode(encoding, "replace")
        assert b"\x1b" not in written
        if any(ord(c) > 127 for c in m) and encoding == "ascii":
            assert b"?" in written  # replaced, not dropped


def test_utf8_keeps_glyphs(monkeypatch):
    monkeypatch.setattr(term, "enable_vt", lambda: True)
    t = term.Term.detect(stream("utf-8"), stream(), {})
    assert t.ascii is False
    assert (t.mark, t.ok, t.ell) == ("◆", "✓", "…")


def test_ascii_glyphs(monkeypatch):
    monkeypatch.setattr(term, "enable_vt", lambda: True)
    t = term.Term.detect(stream("ascii"), stream(), {})
    assert (t.mark, t.ok, t.warn, t.dot, t.ell) == ("*", "+", "!", "-", "...")
    assert (t.bar_full, t.bar_empty, t.frames) == ("#", "-", "|/-\\")


def test_missing_encoding_is_ascii(monkeypatch):
    monkeypatch.setattr(term, "enable_vt", lambda: True)

    class NoEnc:
        encoding = None

        def isatty(self):
            return True

        def __init__(self):
            self.got = []

        def write(self, s):
            s.encode("ascii")  # raises like a narrow stream would
            self.got.append(s)

        def flush(self):
            pass

    out = NoEnc()
    t = term.Term.detect(out, stream(), {})
    assert t.ascii is True
    t.write("a ◆ b")
    assert out.got == ["a ? b\n"]


def test_write_never_raises():
    class Broken:
        encoding = "ascii"

        def isatty(self):
            return False

        def write(self, s):
            raise OSError("closed")

    t = term.Term(Broken(), styled=False, new_output=False, interactive=False, ascii=True)
    t.write("anything")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows console only")
def test_enable_vt_failure_means_plain(monkeypatch):
    import ctypes

    monkeypatch.setattr(ctypes.windll.kernel32, "GetConsoleMode", lambda *a: 0, raising=False)
    assert term.enable_vt() is False
