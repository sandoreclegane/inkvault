"""The Memory Atlas helper: a private web server on this computer for the Atlas page, its data and search
(spec §3.4, §5). It listens on 127.0.0.1 only, answers only the browser tab it opened, and never writes the vault.
"""
import hmac
import json
import re
import secrets
import threading
import time
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
COOKIE = "inkvault_atlas"
DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
CLOSED_TEXT = ("This Atlas link has already been used or has expired. Open the Atlas again from the "
               "InkVault Atlas icon or `inkvault dashboard`.").encode()


class Helper:
    """What request threads share: one-time codes, sessions, the control secret and the last ping."""

    def __init__(self, port=0, now=time.monotonic):
        self.now = now
        self.codes = {}       # one-time code -> expiry (spec §5 rule 2)
        self.sessions = set()
        self.control = secrets.token_hex(32)
        self.last_ping = now()
        self.lock = threading.Lock()
        self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.server.daemon_threads = True
        self.server.helper = self
        self.port = self.server.server_address[1]

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
    def start(day):
        if not isinstance(day, str) or not DAY.fullmatch(day):
            raise BadRequest("Dates look like 2026-10-05.")
        try:
            d = datetime.fromisoformat(day)
        except ValueError:
            raise BadRequest(f"{day} isn't a date.") from None
        local = d.replace(tzinfo=tz) if tz else d.astimezone()  # naive -> this computer's zone for that date
        return local.astimezone(timezone.utc)
    since = start(from_) if from_ else None
    until = start((datetime.fromisoformat(to) + timedelta(days=1)).date().isoformat()) if to and start(to) else None
    if since and until and since >= until:
        raise BadRequest("From is after To.")
    fmt = lambda t: t.strftime("%Y-%m-%dT%H:%M:%S.%fZ") if t else ""
    return fmt(since), fmt(until)


def hit_json(h):
    text, spans = retrieval.excerpt(h)
    return {"kind": h.kind, "id": h.id, "time": retrieval.when(h.created), "title": h.display or h.title or "",
            "source_label": retrieval.source_label(h), "excerpt": text,
            "match": {"type": "keyword", "spans": spans} if h.snippet else {"type": "meaning"}}


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
        if (kind not in retrieval.SOURCES or not isinstance(id_, str) or not 0 < len(id_) <= 200
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
        self.send_response(status)
        for k, v in (("Content-Type", ctype), ("Content-Length", str(len(body))), ("Cache-Control", "no-store"),
                     ("Content-Security-Policy", CSP), ("Referrer-Policy", "no-referrer"),
                     ("X-Content-Type-Options", "nosniff"), ("X-Frame-Options", "DENY"), *extra):
            self.send_header(k, v)
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
        morsel = cookie.get(COOKIE)
        return morsel is not None and morsel.value in self.helper.sessions

    def api_ok(self):
        return self.session_ok() and self.headers.get("X-InkVault") == "1"

    def body(self):
        """The JSON object sent, read only after its length is known to be sane (Codex plan review #6)."""
        if self.headers.get("Transfer-Encoding"):
            raise BadRequest("Send a plain request body.")
        length = (self.headers.get("Content-Length") or "0").strip()
        if not length.isdigit():  # also refuses "-1", which rfile.read would treat as "read until the end"
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

    def do_GET(self):
        if not self.host_ok():
            return self.send(421)
        url = urlsplit(self.path)
        if url.path == "/open":
            session = self.helper.redeem((parse_qs(url.query).get("code") or [""])[0])
            if not session:
                return self.send(403, CLOSED_TEXT, "text/plain; charset=utf-8")
            return self.send(303, extra=(("Location", "/"),
                                         ("Set-Cookie", f"{COOKIE}={session}; HttpOnly; SameSite=Strict; Path=/")))
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

    def do_POST(self):
        if not self.host_ok():
            return self.send(421)
        path = urlsplit(self.path).path
        if path == "/control/code":
            given = self.headers.get("X-InkVault-Control") or ""
            if not hmac.compare_digest(given.encode(), self.helper.control.encode()):
                return self.send(403)
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
