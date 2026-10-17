"""Terminal decisions: colour, interactivity, encoding, Windows VT.

Cosmetics never raise: anything that goes wrong here degrades to plain ASCII.
"""
import atexit
import os
import sys
import threading

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


class Progress:
    """Live progress for the search build: a spinner or bar on a terminal, plain lines elsewhere.

    Live drawing needs a real terminal (`new_output`); otherwise `stage` prints one line and `update`
    prints nothing, with no carriage returns or escapes. Leaving the `with` block always stops the
    spinner thread and clears the live line, including on KeyboardInterrupt.
    """

    INTERVAL = 0.1  # at most 10 redraws a second
    WIDTH = 24

    def __init__(self, term: Term):
        self.term = term
        self._live = term.styled  # unstyled or piped: stage lines only, no redraws
        self._lock = threading.Lock()
        self._label = ""
        self._done = 0
        self._total = 0
        self._tick = 0
        self._drawn = False
        self._stop = threading.Event()
        self._thread = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._halt()
        return False

    def stage(self, label: str, animate: bool = True) -> None:
        """animate=False prints a plain line once (e.g. while another library writes to the terminal)."""
        if not self._live or not animate:
            self._halt()
            self.term.write(f"Building search: {label}...")
            return
        with self._lock:
            self._label, self._done, self._total = label, 0, 0
        self._start()

    def update(self, done: int, total: int) -> None:
        if not self._live:
            return
        with self._lock:
            self._done, self._total = done, total

    def finish(self, label: str, ok: bool = True) -> None:
        self._halt()
        glyph = self.term.teal(self.term.ok) if ok else self.term.amber(self.term.warn)
        self.term.write(f"  {glyph} {label}")

    def _start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="Progress", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            self._draw()
            self._stop.wait(self.INTERVAL)

    def _draw(self) -> None:
        t = self.term
        with self._lock:
            label, done, total = self._label, self._done, self._total
            frame = t.frames[self._tick % len(t.frames)]
            self._tick += 1
            if total > 0:
                full = min(self.WIDTH, int(self.WIDTH * done / total))
                bar = t.gold(t.bar_full * full) + t.bar_empty * (self.WIDTH - full)
                line = f"  {bar} {label} {min(100, 100 * done // total)}%"
            else:
                line = f"  {t.gold(frame)} {label}"
            t.write("\r" + line + ("\x1b[K" if t.styled else " " * 4), end="")
            self._drawn = True

    def _halt(self) -> None:
        self._stop.set()
        th, self._thread = self._thread, None
        if th is not None:
            th.join()
        with self._lock:
            if self._drawn:
                self.term.write("\r\x1b[K" if self.term.styled else "\r" + " " * (self.WIDTH + 60) + "\r", end="")
                self._drawn = False
