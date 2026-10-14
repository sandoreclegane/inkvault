"""The Memory Atlas helper: a private web server on this computer for the Atlas page, its data and search
(spec §3.4, §5). It listens on 127.0.0.1 only, answers only the browser tab it opened, and never writes the vault.
"""
import hmac
import http.client
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime, timedelta, timezone
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import paths, retrieval

ASSET_DIR = Path(__file__).with_name("atlas")
ASSETS = {  # request path -> (packaged file, content type). Nothing else is ever served (spec §5 rule 7).
    "/": ("index.html", "text/html; charset=utf-8"),
    "/assets/atlas.css": ("atlas.css", "text/css; charset=utf-8"),
    "/assets/atlas.js": ("atlas.js", "text/javascript; charset=utf-8"),
    "/assets/search.js": ("search.js", "text/javascript; charset=utf-8"),
    "/assets/echarts.min.js": ("echarts.min.js", "text/javascript; charset=utf-8"),
    "/assets/mark.png": ("mark.png", "image/png"),
}
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; "
       "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
CODE_TTL = 60
MAX_BODY = 16 * 1024
MAX_QUERY = 500
PAGE_SIZE = 25
MAX_OFFSET = 10_000
IDLE = 30 * 60
READ_TIMEOUT = 10  # seconds a connection may sit silent, or a body may take to arrive, before it's closed
COOKIE = "inkvault_atlas"  # the helper adds its port: one cookie name per helper
DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
CLOSED_TEXT = ("This Atlas link has already been used or has expired. Open the Atlas again from the "
               "InkVault Atlas icon or `inkvault dashboard`.").encode()


class QuietServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):  # socketserver would print a traceback: nothing may be logged
        pass


class Helper:
    """What request threads share: one-time codes, sessions, the control secret and the last ping."""

    def __init__(self, port=0, now=time.monotonic):
        self.now = now
        self.codes = {}       # one-time code -> expiry (spec §5 rule 2)
        self.sessions = set()
        self.control = secrets.token_hex(32)
        self.last_ping = now()
        self.lock = threading.Lock()
        self.server = QuietServer(("127.0.0.1", port), Handler)
        self.server.daemon_threads = True
        self.server.helper = self
        self.port = self.server.server_address[1]
        self.cookie = f"{COOKIE}_{self.port}"  # cookies ignore ports: another helper on 127.0.0.1 mustn't share one

    def new_code(self):
        code = secrets.token_urlsafe(32)
        with self.lock:
            self.codes[code] = self.now() + CODE_TTL
        return code

    def redeem(self, code):
        with self.lock:
            expiry = self.codes.pop(code, None)
            if expiry is None or expiry < self.now():
                return None
            session = secrets.token_urlsafe(32)
            self.sessions.add(session)
            return session

    def open_url(self, code):
        return f"http://127.0.0.1:{self.port}/open?code={code}"


class BadRequest(Exception):
    """Input the page couldn't have sent; answered 400 with a sentence."""


class TooLarge(Exception):
    pass


class Stalled(Exception):
    """The body never arrived in full: the connection is closed without an answer."""


def atlas_revision():
    """Which atlas.json is published (Codex plan review #7): it changes whenever the charts' data is rebuilt, replaced
    or removed, independently of search's generation, so an open page refreshes its charts. None when there's none."""
    try:
        st = paths.dashboard().stat()
    except FileNotFoundError:
        return None
    return retrieval.generation((st.st_mtime_ns, st.st_size))


def day_bounds(from_, to, tz=None):
    """Local calendar days -> UTC instants for retrieval.search (spec §4.2): the start of From, and the start of the
    day after To (an exclusive end), each with that date's own UTC offset, so daylight-saving days are 23 or 25
    hours. "" for a side that wasn't given."""
    def parse(day):
        if not isinstance(day, str) or not DAY.fullmatch(day):
            raise BadRequest("Dates look like 2026-10-05.")
        try:
            return datetime.fromisoformat(day)
        except ValueError:
            raise BadRequest(f"{day} isn't a date.") from None

    def start(d):
        try:
            local = d.replace(tzinfo=tz) if tz else d.astimezone()  # naive -> this computer's zone for that date
            return local.astimezone(timezone.utc)
        except (OverflowError, OSError, ValueError):  # year 1 or 9999 has no instant here
            raise BadRequest("That date is out of range.") from None
    since = start(parse(from_)) if from_ else None
    until = None
    if to:
        try:
            until = start(parse(to) + timedelta(days=1))
        except OverflowError:
            raise BadRequest("That date is out of range.") from None
    if since and until and since >= until:
        raise BadRequest("From is after To.")
    fmt = lambda t: t.strftime("%Y-%m-%dT%H:%M:%S.%fZ") if t else ""
    return fmt(since), fmt(until)


def hit_json(h):
    text, spans = retrieval.excerpt(h)
    return {"kind": h.kind, "id": h.id, "time": retrieval.when(h.created), "title": h.display or h.title or "",
            "source_label": retrieval.source_label(h), "excerpt": text,
            "match": {"type": "keyword", "spans": spans} if h.snippet is not None else {"type": "meaning"}}


def build_atlas():
    """atlas.json when it's missing (spec §3.4), under the nightly lock, without waiting for a run in progress."""
    from . import dashboard, nightly
    try:
        with nightly.lock("dashboard"):
            return "built" if dashboard.build() else "no-days"
    except nightly.Busy:
        return "busy"


def atlas_payload():
    stamp = retrieval.published()
    if stamp[0]:
        return {"status": "rebuilding"}
    if stamp[3] is None:
        return {"status": "unavailable"}
    if not paths.search_db().exists():
        return {"status": "empty"}
    built = None
    if not paths.dashboard().exists():
        built = build_atlas()
        if built == "busy":
            return {"status": "busy"}

    def read():
        rev = atlas_revision()
        try:
            data = json.loads(paths.dashboard().read_text(encoding="utf-8"))
        except FileNotFoundError:
            data = None
        if atlas_revision() != rev:  # replaced while being read: never label one file's data with another's revision
            raise retrieval.Refused("changed")
        return data, rev, retrieval.freshness(), retrieval.sources_present()
    try:
        (data, rev, fresh, chips), gen = retrieval.guarded_call(read)
    except retrieval.Refused as r:
        return {"status": r.status}
    if data is None:
        return {"status": "no-days" if built == "no-days" else "rebuilding", "generation": gen,
                "atlas_revision": None, "freshness": fresh, "sources": chips}
    return {"status": "ok", "generation": gen, "atlas_revision": rev, "data": data, "freshness": fresh,
            "sources": chips}


def api_search(body):
    q = body.get("query")
    if not isinstance(q, str) or not q.strip() or len(q) > MAX_QUERY:
        raise BadRequest(f"Type something to search for (at most {MAX_QUERY} characters).")
    chip = body.get("source", "all")
    if chip not in retrieval.CHIPS:
        raise BadRequest("Unknown source.")
    offset = body.get("offset", 0)
    if not isinstance(offset, int) or isinstance(offset, bool) or not 0 <= offset <= MAX_OFFSET:
        raise BadRequest("Bad page.")
    since, until = day_bounds(body.get("from"), body.get("to"))
    kinds, event_source = retrieval.CHIPS[chip]
    try:
        r, gen = retrieval.guarded_call(retrieval.search, q, "all", "hybrid", since, until, PAGE_SIZE, offset=offset,
                                        kinds=kinds, event_source=event_source, markers=retrieval.ATLAS_MARKERS,
                                        require_model=True)
    except retrieval.Refused as e:
        return {"status": e.status}
    if offset and body.get("generation") != gen:
        return {"status": "changed"}
    if r.empty_index:
        return {"status": "empty"}
    if r.error:
        raise BadRequest(r.error)
    return {"status": "ok", "generation": gen, "hits": [hit_json(h) for h in r.hits], "more": r.more,
            "keyword_only": r.keyword_only}


def api_record(body):
    if "cursor" in body:
        try:
            cursor = retrieval.decode_cursor(body["cursor"])
        except retrieval.BadCursor:
            raise BadRequest("Bad cursor.") from None
        kind, id_ = None, None
    else:
        cursor, kind, id_ = None, body.get("kind"), body.get("id")
        expected = body.get("generation")  # the vault state the page is showing (Codex plan review #2)
        if (not isinstance(kind, str) or kind not in retrieval.SOURCES or not isinstance(id_, str) or not 0 < len(id_) <= 200
                or not isinstance(expected, str)):
            raise BadRequest("Bad record.")
    try:
        r, gen = retrieval.guarded_call(retrieval.record, kind, id_, cursor)
    except retrieval.Refused as e:
        return {"status": e.status}
    if cursor is None and expected != gen:  # rebuilt between choosing the hit and opening it
        return {"status": "changed"}
    return {**r, "generation": gen}


class Handler(BaseHTTPRequestHandler):
    server_version = "InkVault"
    sys_version = ""
    protocol_version = "HTTP/1.1"
    timeout = READ_TIMEOUT  # socket timeout: a silent or stalled connection is closed, never holds a thread forever

    def log_message(self, *args):  # spec §5 rule 10: no request log, so no query or record text anywhere
        pass

    @property
    def helper(self):
        return self.server.helper

    def send(self, status, body=b"", ctype="application/json; charset=utf-8", extra=()):
        if status >= 400:
            self.close_connection = True  # a refused request's body (if any) must not be read as the next request
        self.send_response(status)
        for k, v in (("Content-Type", ctype), ("Content-Length", str(len(body))), ("Cache-Control", "no-store"),
                     ("Content-Security-Policy", CSP), ("Referrer-Policy", "no-referrer"),
                     ("X-Content-Type-Options", "nosniff"), ("X-Frame-Options", "DENY"), *extra):
            self.send_header(k, v)
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def answer(self, status, obj):
        self.send(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def host_ok(self):
        """Spec §5 rule 4: exactly this helper's host, so a rebinding DNS name never counts as same-site."""
        here = f"127.0.0.1:{self.helper.port}"
        origin = self.headers.get("Origin")
        return self.headers.get("Host") == here and origin in (None, f"http://{here}")

    def session_ok(self):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie") or "")
        except CookieError:
            return False
        morsel = cookie.get(self.helper.cookie)
        return morsel is not None and morsel.value in self.helper.sessions

    def api_ok(self):
        return self.session_ok() and self.headers.get("X-InkVault") == "1"

    def body(self):
        """The JSON object sent, read only after its length is known to be sane (Codex plan review #6)."""
        if self.headers.get("Transfer-Encoding"):
            raise BadRequest("Send a plain request body.")
        length = (self.headers.get("Content-Length") or "0").strip()
        if not (length.isascii() and length.isdigit()):  # also refuses "-1", which rfile.read would treat as "read until the end"
            raise BadRequest("Bad length.")
        n = int(length)
        if n > MAX_BODY:
            raise TooLarge  # answered at once and the connection closed: nothing is read or drained
        try:
            data = self.rfile.read(n)
        except OSError:  # includes the socket timeout (READ_TIMEOUT)
            raise Stalled from None
        if len(data) < n:
            raise Stalled
        try:
            obj = json.loads(data or b"{}")
        except ValueError:
            raise BadRequest("Send JSON.") from None
        if not isinstance(obj, dict):
            raise BadRequest("Send a JSON object.")
        return obj

    def guarded(self, handle):
        """Anything unexpected is a fixed 500 with no detail, and nothing is printed (spec §5 rule 10)."""
        try:
            return handle()
        except Exception:
            self.close_connection = True
            try:
                self.send(500)
            except Exception:
                pass

    def do_GET(self):
        return self.guarded(self.get)

    def do_POST(self):
        return self.guarded(self.post)

    def get(self):
        if not self.host_ok():
            return self.send(421)
        url = urlsplit(self.path)
        if url.path == "/open":
            session = self.helper.redeem((parse_qs(url.query).get("code") or [""])[0])
            if not session:
                return self.send(403, CLOSED_TEXT, "text/plain; charset=utf-8")
            return self.send(303, extra=(("Location", "/"),
                                         ("Set-Cookie", f"{self.helper.cookie}={session}; HttpOnly; SameSite=Strict; Path=/")))
        if url.path in ASSETS and not url.query:
            if not self.session_ok():
                return self.send(403, CLOSED_TEXT, "text/plain; charset=utf-8")
            name, ctype = ASSETS[url.path]
            return self.send(200, (ASSET_DIR / name).read_bytes(), ctype)
        if url.path == "/api/atlas" and not url.query:
            if not self.api_ok():
                return self.send(403)
            return self.answer(200, atlas_payload())
        if url.path in ("/api/search", "/api/record", "/api/ping", "/control/code"):
            return self.send(405)
        return self.send(404)

    def post(self):
        if not self.host_ok():
            return self.send(421)
        path = urlsplit(self.path).path
        if path == "/control/code":
            given = self.headers.get("X-InkVault-Control") or ""
            if not hmac.compare_digest(given.encode(), self.helper.control.encode()):
                return self.send(403)
            self.close_connection = True  # the request body (if any) is never read: don't parse it as a next request
            return self.answer(200, {"code": self.helper.new_code()})
        if path not in ("/api/search", "/api/record", "/api/ping"):
            return self.send(405 if path in ASSETS or path in ("/api/atlas", "/open") else 404)
        if not self.api_ok():
            return self.send(403)
        try:
            body = self.body()
            if path == "/api/ping":
                self.helper.last_ping = self.helper.now()
                stamp = retrieval.published()
                if stamp[3] is None:  # the vault can't be read right now: no generation to offer
                    return self.answer(200, {"status": "unavailable"})
                return self.answer(200, {"status": "ok", "generation": retrieval.generation(stamp),
                                         "rebuilding": bool(stamp[0]), "atlas_revision": atlas_revision()})
            return self.answer(200, api_search(body) if path == "/api/search" else api_record(body))
        except TooLarge:
            self.close_connection = True
            return self.send(413)
        except Stalled:
            self.close_connection = True  # no answer: the request never arrived
            return None
        except BadRequest as e:
            self.close_connection = True  # what's left of a bad request can't be trusted as the next one
            return self.answer(400, {"status": "bad", "message": str(e)})

    def refuse(self):
        self.send(405)

    do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_HEAD = refuse


def prepare_process():
    """The helper never downloads a model or shows progress bars (spec §5 rule 11)."""
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")


def write_info(helper):
    """Port and control secret for a second launch, written whole (temp file, then replace), user-only on POSIX."""
    info = {"pid": os.getpid(), "port": helper.port, "control": helper.control,
            "started": datetime.now(timezone.utc).isoformat()}
    tmp = paths.atlas_info().with_suffix(".tmp")
    tmp.unlink(missing_ok=True)  # a leftover file may have looser permissions: create it fresh
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(info, f)
        os.replace(tmp, paths.atlas_info())
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def request_code(info, timeout=2.0):
    """Ask the running helper for a fresh one-time code (spec §3.4). None when it doesn't answer as expected."""
    port, control = int(info["port"]), str(info["control"])
    if not 0 < port < 65536:
        raise ValueError("bad port")
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        c.request("POST", "/control/code", body=b"{}",
                  headers={"X-InkVault-Control": control, "Content-Type": "application/json"})
        r = c.getresponse()
        body = r.read()
        return json.loads(body)["code"] if r.status == 200 else None
    finally:
        c.close()


def open_in_browser(url):
    webbrowser.open(url)


def reuse(open_browser, wait=10.0):
    """Another process holds the lock, so a helper is alive: get a code from it. Never start a second helper."""
    deadline = time.monotonic() + wait
    while True:
        try:
            info = json.loads(paths.atlas_info().read_text(encoding="utf-8"))
            code = request_code(info)
            if code:
                if open_browser:
                    open_in_browser(f"http://127.0.0.1:{int(info['port'])}/open?code={code}")
                    print("Opened the Memory Atlas in your browser.")
                else:
                    print("The Memory Atlas is already running.")
                return 0
        except (OSError, ValueError, KeyError, TypeError, http.client.HTTPException):
            pass  # just started and still writing its info, or busy: try again until the deadline
        if time.monotonic() >= deadline:
            print("The Atlas is starting or busy; try again in a moment.")
            return 1
        time.sleep(0.25)


def watchdog(helper, idle, stop_event):
    """Stop the helper when no open Atlas tab has pinged for `idle` seconds (spec §3.4)."""
    step = max(0.2, min(30.0, idle / 4))
    while not stop_event.wait(step):
        if helper.now() - helper.last_ping > idle:
            helper.server.shutdown()
            return


def serve(open_browser=True, idle=IDLE, wait=10.0):
    """Become the helper if the OS lock says nobody else is, or hand the request to the one that is."""
    from . import nightly
    prepare_process()
    cant = ("The Atlas can't start: its lock couldn't be taken ({}). Nothing was started. If your InkVault "
            "folder is on a network or synced drive, move it to a local drive (`--home`).")
    try:
        fd = os.open(paths.atlas_lock(), os.O_RDWR | os.O_CREAT)
    except OSError as e:
        print(cant.format(e))
        return 1
    try:
        try:
            mine = nightly.try_lock(fd)
        except OSError as e:  # e.g. a drive without file locking: without the lock there's no safe owner (review #1)
            print(cant.format(e))
            return 1
        if not mine:
            return reuse(open_browser, wait)
        helper = Helper()
        stop_event = threading.Event()
        try:
            write_info(helper)
            threading.Thread(target=watchdog, args=(helper, idle, stop_event), daemon=True).start()
            if open_browser:
                open_in_browser(helper.open_url(helper.new_code()))
                print("The Memory Atlas is open in your browser. It stops by itself 30 minutes after you close it; "
                      "Ctrl+C stops it now.")
            else:
                print(f"The Memory Atlas helper is running on 127.0.0.1:{helper.port}. "
                      "Open it with `inkvault dashboard`.")
            try:
                helper.server.serve_forever()
            except KeyboardInterrupt:
                pass
            return 0
        finally:
            stop_event.set()
            helper.server.server_close()
            try:
                if json.loads(paths.atlas_info().read_text(encoding="utf-8")).get("control") == helper.control:
                    paths.atlas_info().unlink()
            except (OSError, ValueError):
                pass
            nightly.unlock(fd)
    finally:
        os.close(fd)


SHORTCUT = "InkVault Atlas"
ICON = ASSET_DIR / "inkvault.ico"


def launch(open_browser=True):
    """`inkvault dashboard` (spec §3.6): build the Atlas data if it's missing, then start or reuse the helper."""
    if paths.search_db().exists() and not paths.dashboard().exists() and not paths.rebuild_marker().exists():
        print("Building the Atlas…", flush=True)
        if build_atlas() == "busy":
            print("A nightly run is in progress; the Atlas fills in when it's done.")
    return serve(open_browser=open_browser)


def pythonw():
    """This environment's windowless Python, so the icon opens no console window."""
    exe = Path(sys.executable)
    w = exe.with_name("pythonw.exe")
    return w if w.exists() else exe


def start_detached():
    """Open the Atlas without holding the terminal (after `rescue`): a separate helper process."""
    argv = [str(pythonw() if sys.platform == "win32" else Path(sys.executable)), "-m", "inkvault",
            "--home", str(paths.home()), "dashboard"]
    kw = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "close_fds": True}
    if sys.platform == "win32":
        kw["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    subprocess.Popen(argv, **kw)


def make_shortcut():
    """"InkVault Atlas" on the desktop and in the Start menu (spec §3.6), checked by reading each one back."""
    if sys.platform != "win32":
        print("The InkVault Atlas icon is Windows-only for now. Open the Atlas with `inkvault dashboard`.")
        return 0
    q = lambda s: str(s).replace("'", "''")
    exe = pythonw()
    args = f'-m inkvault --home "{paths.home()}" dashboard'
    script = (
        "[Console]::OutputEncoding=[Text.Encoding]::UTF8; $ErrorActionPreference='Stop'; $w=New-Object -ComObject WScript.Shell; $out=@(); "
        "foreach ($dir in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) { "
        f"$p=Join-Path $dir '{SHORTCUT}.lnk'; $s=$w.CreateShortcut($p); $s.TargetPath='{q(exe)}'; "
        f"$s.Arguments='{q(args)}'; $s.IconLocation='{q(ICON)},0'; $s.WorkingDirectory='{q(paths.home())}'; "
        "$s.Description='Open the InkVault Memory Atlas'; $s.Save(); $r=$w.CreateShortcut($p); "
        "$out+=@{path=$p; target=$r.TargetPath; arguments=$r.Arguments; iconLocation=$r.IconLocation} }; ConvertTo-Json -Compress @($out)")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                           capture_output=True, text=True, encoding="utf-8")
        made = json.loads(r.stdout) if r.returncode == 0 else []
    except (OSError, ValueError):
        made = []
    made = made if isinstance(made, list) else [made]
    good = [m for m in made if isinstance(m, dict) and str(m.get("target", "")).lower() == str(exe).lower()
            and m.get("arguments") == args
            and str(m.get("iconLocation", "")).lower() == f"{ICON},0".lower()]
    if len(good) != 2:
        print("The InkVault Atlas icon couldn't be made, or didn't read back as expected. "
              "Open the Atlas with `inkvault dashboard`.")
        return 1
    print(f'Added "{SHORTCUT}" to your desktop and Start menu.')
    return 0
