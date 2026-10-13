"""The capture worker's protocol and supervisor (spec §2, R2, P2, P10).

At most one request is outstanding, and only an idle worker gets one; requests stay under the pipe buffer, so
writing never blocks. Each poll peeks the pipe and reads what's there. Abandoning only makes an answer
unacceptable: the deadline still runs until the worker answers or is killed. Killing never waits; later polls reap."""
import json
import os
import re
import struct
import subprocess
import sys
import time

MAX_REQUEST = 3072
MAX_RESPONSE = 1_000_000
TIMEOUT = 5.0
COOLDOWN = 600.0
STATUSES = ("ok", "changed", "failed", "no-ocr")


class ProtocolError(Exception):
    pass


def encode(obj):
    body = json.dumps(obj).encode("utf-8")
    return struct.pack(">I", len(body)) + body


def _int(v):
    return isinstance(v, int) and not isinstance(v, bool)


HEX = frozenset("0123456789abcdef")
REQUEST_KEYS = frozenset(("id", "hwnd", "pid", "created", "exe", "title_sha256"))


def check_request(o):
    if not (isinstance(o, dict) and set(o) == REQUEST_KEYS
            and all(_int(o.get(k)) for k in ("id", "hwnd", "pid", "created"))
            and isinstance(o.get("exe"), str) and isinstance(o.get("title_sha256"), str)
            and len(o["title_sha256"]) == 64 and set(o["title_sha256"]) <= HEX):
        raise ProtocolError("bad request")
    return o


ERROR_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,60}")


def error_name(text):
    """What the watcher may log from a worker's error field: a class-like name, or "unknown" (R8)."""
    return text if ERROR_NAME.fullmatch(text or "") else "unknown"


def check_response(o):
    if not (isinstance(o, dict) and _int(o.get("id")) and o.get("status") in STATUSES
            and isinstance(o.get("lines"), list) and all(isinstance(x, str) for x in o["lines"])
            and _int(o.get("colors")) and isinstance(o.get("error"), str)):
        raise ProtocolError("bad response")
    return o


class Decoder:
    def __init__(self, limit, check):
        self.limit, self.check, self.buf = limit, check, b""

    def feed(self, data):
        self.buf += data
        out = []
        while len(self.buf) >= 4:
            n = struct.unpack(">I", self.buf[:4])[0]
            if n > self.limit:
                raise ProtocolError("message too large")
            if len(self.buf) < 4 + n:
                break
            body, self.buf = self.buf[4:4 + n], self.buf[4 + n:]
            try:
                obj = json.loads(body)
            except (ValueError, RecursionError):
                raise ProtocolError("not JSON") from None
            out.append(self.check(obj))
        return out


def available(pipe):
    """Bytes readable from pipe now, without blocking; -1 when the other end is gone."""
    if sys.platform == "win32":
        import ctypes
        import msvcrt
        from ctypes import wintypes
        from . import winapi
        avail = wintypes.DWORD()
        if not winapi.api().kernel32.PeekNamedPipe(wintypes.HANDLE(msvcrt.get_osfhandle(pipe.fileno())), None, 0,
                                                   None, ctypes.byref(avail), None):
            return -1
        return avail.value
    import select
    return 65536 if select.select([pipe], [], [], 0)[0] else 0


def spawn(argv):
    flags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW
    proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            creationflags=flags)
    job, error = None, None
    if sys.platform == "win32":
        from . import winapi
        try:
            job = winapi.kill_on_close_job(proc)
        except OSError as e:
            error = f"worker not in a job ({e.errno})"
    return proc, job, error


class Pending:
    def __init__(self, id_, exe, started):
        self.id, self.exe, self.started, self.acceptable = id_, exe, started, True


class Supervisor:
    def __init__(self, argv, timeout=TIMEOUT, cooldown=COOLDOWN, clock=time.monotonic):
        self.argv, self.timeout, self.cooldown, self.clock = argv, timeout, cooldown, clock
        self.proc = self.job = self.decoder = self.pending = None
        self.dying, self.next_id = [], 0
        self.timeouts = self.restarts = self.dropped_requests = 0
        self.job_error, self.cooldowns = None, {}

    def busy(self):
        return self.pending is not None

    def cooling(self, exe):
        until = self.cooldowns.get(exe.lower())
        return until is not None and self.clock() < until

    def submit(self, request):
        if self.busy():
            return None
        full = {**request, "id": self.next_id + 1} if isinstance(request, dict) else None
        try:
            check_request(full)
        except ProtocolError:
            self.dropped_requests += 1
            return None
        if self.cooling(full["exe"]):
            return None
        msg = encode(full)
        if len(msg) > MAX_REQUEST:
            self.dropped_requests += 1
            return None
        if self.proc is None:
            try:
                self.proc, self.job, self.job_error = spawn(self.argv)
            except OSError:
                self.restarts += 1
                return None
            self.decoder = Decoder(MAX_RESPONSE, check_response)
        self.next_id += 1
        try:
            self.proc.stdin.write(msg)   # an idle worker and < 4 KB: never blocks
            self.proc.stdin.flush()
        except OSError:
            self._restart()
            return None
        self.pending = Pending(self.next_id, request["exe"].lower(), self.clock())
        return self.next_id

    def abandon(self):
        if self.pending:
            self.pending.acceptable = False

    def poll(self):
        self._reap()
        if self.proc is None:
            return None
        try:
            n = available(self.proc.stdout)
            if n < 0:
                raise ProtocolError("worker gone")
            messages = []
            if n:
                data = os.read(self.proc.stdout.fileno(), min(n, MAX_RESPONSE + 4))
                if not data:
                    raise ProtocolError("worker gone")
                messages = self.decoder.feed(data)
        except (ProtocolError, OSError):
            self._restart()
            return None
        for m in messages:
            if self.pending and m["id"] == self.pending.id:
                p, self.pending = self.pending, None
                return m if p.acceptable else None
        if self.pending and self.clock() - self.pending.started >= self.timeout:
            self.timeouts += 1
            self.cooldowns[self.pending.exe] = self.clock() + self.cooldown
            self._kill()
        return None

    def _restart(self):
        self.restarts += 1
        self._kill()

    def _kill(self):
        self.pending = None
        p, job, self.proc, self.job = self.proc, self.job, None, None
        if p is None:
            return
        try:
            p.kill()
        except OSError:
            pass
        self.dying.append((p, job))

    def _reap(self):
        for p, job in list(self.dying):
            if p.poll() is not None:
                for f in (p.stdin, p.stdout):
                    try:
                        f.close()
                    except OSError:
                        pass
                if job and sys.platform == "win32":
                    from . import winapi
                    winapi.close_handle(job)
                self.dying.remove((p, job))

    def close(self, wait=1.0):
        if self.proc is not None:
            try:
                self.proc.stdin.close()
            except OSError:
                pass
        self._kill()
        end = time.monotonic() + wait
        while self.dying and time.monotonic() < end:
            self._reap()
            time.sleep(0.02)
