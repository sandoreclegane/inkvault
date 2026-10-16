"""Terminal decisions: colour, interactivity, encoding, Windows VT.

Cosmetics never raise: anything that goes wrong here degrades to plain ASCII.
"""
import atexit
import os
import sys

RESET = "\x1b[0m"
CODES = {
    "gold": "\x1b[38;2;201;168;76m",
    "teal": "\x1b[38;2;93;202;165m",
    "amber": "\x1b[33m",
    "dim": "\x1b[2m",
    "bold": "\x1b[1m",
}
SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
# Every non-ASCII glyph the styled output uses; the stream must encode all.
GLYPHS = "…·—◆✓━╺" + SPINNER
ENABLE_VT = 0x0004


def enable_vt() -> bool:
    """Turn on ANSI processing in the Windows console; True elsewhere."""
    if sys.platform != "win32":
        return True
    try:
        import ctypes
        from ctypes import wintypes

        k = ctypes.windll.kernel32
        k.GetStdHandle.restype = wintypes.HANDLE
        k.GetStdHandle.argtypes = [wintypes.DWORD]
        k.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        k.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        handle = k.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = wintypes.DWORD()
        if not k.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        if mode.value & ENABLE_VT:
            return True
        if not k.SetConsoleMode(handle, mode.value | ENABLE_VT):
            return False
        # Leave the user's console as we found it.
        atexit.register(k.SetConsoleMode, handle, mode.value)
        return True
    except Exception:
        return False


def _tty(stream) -> bool:
    try:
        return bool(stream.isatty())
    except Exception:
        return False


def _is_ascii(stream) -> bool:
    enc = getattr(stream, "encoding", None)
    if not enc:
        return True
    try:
        GLYPHS.encode(enc)
        return False
    except (UnicodeEncodeError, LookupError):
        return True


class Term:
    def __init__(self, stream, *, styled, new_output, interactive, ascii):
        self.stream = stream
        self.styled = styled
        self.new_output = new_output
        self.interactive = interactive
        self.ascii = ascii
        if ascii:
            self.mark, self.ok, self.warn, self.dot = "*", "+", "!", "-"
            self.ell, self.bar_full, self.bar_empty = "...", "#", "-"
            self.frames = "|/-\\"
        else:
            self.mark, self.ok, self.warn, self.dot = "◆", "✓", "!", "·"
            self.ell, self.bar_full = "…", "━"
            self.bar_empty = self.dim("━") if styled else "╺"
            self.frames = SPINNER

    @classmethod
    def detect(cls, stdout=None, stdin=None, env=None) -> "Term":
        stdout = sys.stdout if stdout is None else stdout
        stdin = sys.stdin if stdin is None else stdin
        env = os.environ if env is None else env
        out_tty = _tty(stdout)
        styled = (
            out_tty
            and "NO_COLOR" not in env
            and env.get("TERM") != "dumb"
            and enable_vt()
        )
        return cls(
            stdout,
            styled=styled,
            new_output=out_tty,
            interactive=out_tty and _tty(stdin),
            ascii=_is_ascii(stdout),
        )

    def _paint(self, name: str, s: str) -> str:
        return f"{CODES[name]}{s}{RESET}" if self.styled else s

    def gold(self, s): return self._paint("gold", s)
    def teal(self, s): return self._paint("teal", s)
    def amber(self, s): return self._paint("amber", s)
    def dim(self, s): return self._paint("dim", s)
    def bold(self, s): return self._paint("bold", s)

    def write(self, text: str, end: str = "\n") -> None:
        try:
            try:
                self.stream.write(text + end)
            except UnicodeEncodeError:
                enc = getattr(self.stream, "encoding", None) or "ascii"
                self.stream.write((text + end).encode(enc, "replace").decode(enc))
            self.stream.flush()
        except Exception:
            pass
