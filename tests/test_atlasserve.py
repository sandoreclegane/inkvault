"""The Atlas helper's HTTP surface (spec §3.4, §5), against a real server on a free port."""
import http.client
import json
import threading
import time
from datetime import timedelta

import pytest

import atlasdata


@pytest.fixture
def helper(tmp_path, monkeypatch):
    atlasdata.make_vault(tmp_path, monkeypatch)
    from inkvault import atlasserve
    atlasdata.utc_times(monkeypatch)
    h = atlasserve.Helper()
    t = threading.Thread(target=h.server.serve_forever, daemon=True)
    t.start()
    yield h
    h.server.shutdown()
    h.server.server_close()


def req(h, method, path, body=None, headers=None, host=None):
    c = http.client.HTTPConnection("127.0.0.1", h.port, timeout=10)
    c.putrequest(method, path, skip_host=True)
    c.putheader("Host", host or f"127.0.0.1:{h.port}")
    data = b"" if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
    for k, v in (headers or {}).items():
        c.putheader(k, v)
    if method == "POST":
        c.putheader("Content-Type", "application/json")
        c.putheader("Content-Length", str(len(data)))
    c.endheaders(data if method == "POST" else None)
    r = c.getresponse()
    out = (r.status, dict(r.getheaders()), r.read())
    c.close()
    return out


@pytest.fixture
def session(helper):
    status, headers, _ = req(helper, "GET", f"/open?code={helper.new_code()}")
    assert status == 303 and headers["Location"] == "/"
    cookie = headers["Set-Cookie"]
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie
    return {"Cookie": cookie.split(";")[0]}


def api(helper, session, path, body=None):
    s, _, raw = req(helper, "POST" if body is not None else "GET", path, body, {**session, "X-InkVault": "1"})
    return s, json.loads(raw)


def test_a_code_works_once_and_expires(helper):
    code = helper.new_code()
    assert req(helper, "GET", f"/open?code={code}")[0] == 303
    assert req(helper, "GET", f"/open?code={code}")[0] == 403
    late = helper.new_code()
    helper.codes[late] = helper.now() - 1
    assert req(helper, "GET", f"/open?code={late}")[0] == 403


def test_the_page_needs_the_cookie_and_carries_strict_headers(helper, session):
    assert req(helper, "GET", "/")[0] == 403
    status, headers, body = req(helper, "GET", "/", headers=session)
    assert status == 200 and b"Memory Atlas" in body
    assert headers["Content-Security-Policy"].startswith("default-src 'none'; script-src 'self'")
    assert headers["Cache-Control"] == "no-store" and headers["Referrer-Policy"] == "no-referrer"
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert not any(k.lower().startswith("access-control") for k in headers)


def test_host_and_origin_must_be_this_helper(helper, session):
    assert req(helper, "GET", "/", headers=session, host=f"localhost:{helper.port}")[0] == 421
    assert req(helper, "GET", "/", headers=session, host="evil.example")[0] == 421
    assert req(helper, "GET", "/", headers={**session, "Origin": "https://evil.example"})[0] == 421


def test_the_api_needs_cookie_and_header(helper, session):
    assert req(helper, "POST", "/api/search", {"query": "x"}, session)[0] == 403
    assert req(helper, "POST", "/api/search", {"query": "x"}, {"X-InkVault": "1"})[0] == 403


def test_methods_sizes_and_paths(helper, session):
    h = {**session, "X-InkVault": "1"}
    assert req(helper, "GET", "/api/search", headers=h)[0] == 405
    for method in ("PUT", "DELETE", "PATCH", "OPTIONS"):
        assert req(helper, method, "/", headers=h)[0] == 405
    for path in ("/assets/../vault.db", "/assets/%2e%2e%2fvault.db", "/assets/nope.js", "/vault.db", "/?x=1"):
        assert req(helper, "GET", path, headers=session)[0] == 404


def raw(h, head, session, body=b"", wait=5.0):
    """Send exactly these bytes and return what comes back before the server closes (or `wait` passes)."""
    import socket
    s = socket.create_connection(("127.0.0.1", h.port), timeout=wait)
    s.sendall(head.format(port=h.port, cookie=session["Cookie"]).encode() + body)
    got = b""
    try:
        while chunk := s.recv(65536):
            got += chunk
    except OSError:
        pass
    s.close()
    return got


HEAD = ("POST /api/search HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nCookie: {cookie}\r\nX-InkVault: 1\r\n"
        "Content-Type: application/json\r\n")


def test_bad_lengths_are_refused_before_reading(helper, session):          # Codex plan review #6
    assert raw(helper, HEAD + "Content-Length: -1\r\n\r\n", session).startswith(b"HTTP/1.1 400")
    assert raw(helper, HEAD + "Content-Length: abc\r\n\r\n", session).startswith(b"HTTP/1.1 400")
    assert raw(helper, HEAD + "Transfer-Encoding: chunked\r\n\r\n", session).startswith(b"HTTP/1.1 400")
    t = time.monotonic()
    got = raw(helper, HEAD + "Content-Length: 20000\r\n\r\n", session)  # declared, never sent
    assert got.startswith(b"HTTP/1.1 413") and time.monotonic() - t < 3  # answered at once, nothing drained


def test_stalled_and_idle_connections_time_out_and_never_block_others(helper, session, monkeypatch):
    import socket
    from inkvault import atlasserve
    monkeypatch.setattr(atlasserve.Handler, "timeout", 1)
    idle = socket.create_connection(("127.0.0.1", helper.port))                    # connects, says nothing
    stalled = socket.create_connection(("127.0.0.1", helper.port))                 # promises 100 bytes, sends 10
    stalled.sendall((HEAD + "Content-Length: 100\r\n\r\n").format(port=helper.port, cookie=session["Cookie"]).encode()
                    + b'{"query":')
    t = time.monotonic()
    assert api(helper, session, "/api/search", {"query": "backoff"})[1]["status"] == "ok"
    assert time.monotonic() - t < 3
    for s in (idle, stalled):
        s.settimeout(5)
        try:
            got = s.recv(1024)
        except ConnectionResetError:
            got = b""
        assert got == b""  # closed after the timeout, without an answer (and without a traceback)
        s.close()
    assert api(helper, session, "/api/ping", {})[1]["status"] == "ok"  # and the helper keeps serving


def test_control_code_needs_the_secret_not_the_cookie(helper, session):
    assert req(helper, "POST", "/control/code", {}, {**session, "X-InkVault": "1"})[0] == 403
    assert req(helper, "POST", "/control/code", {}, {"X-InkVault-Control": "wrong"})[0] == 403
    s, _, raw = req(helper, "POST", "/control/code", {}, {"X-InkVault-Control": helper.control})
    assert s == 200 and req(helper, "GET", f"/open?code={json.loads(raw)['code']}")[0] == 303


def test_search_api_returns_hits_with_labels_and_spans(helper, session):
    s, a = api(helper, session, "/api/search", {"query": "backoff", "source": "all"})
    assert s == 200 and a["status"] == "ok" and a["hits"] and len(a["generation"]) == 16
    # e2's snippet column (readable) holds the match; a snippet hit's snippet column (text) may not, so no spans
    hit = next(h for h in a["hits"] if h["id"] == "e2")
    assert hit["source_label"] == "Pieces capture · Code.exe" and hit["match"]["type"] == "keyword"
    a0, a1 = hit["match"]["spans"][0]
    assert hit["excerpt"][a0:a1].lower() == "backoff"
    s, cap = api(helper, session, "/api/search", {"query": "backoff", "source": "captures"})
    # hybrid search: e2 matches by keyword; e1 (the other Pieces capture) may join by meaning (its fixture vector).
    # Either way only captures come back: no chat, summary, snippet, screen or web hit.
    assert {h["id"] for h in cap["hits"]} <= {"e1", "e2"} and "e2" in {h["id"] for h in cap["hits"]}
    assert {h["kind"] for h in cap["hits"]} == {"events"}
    assert all(h["source_label"].startswith("Pieces capture") for h in cap["hits"])


def test_search_api_punctuation_only(helper, session):
    s, a = api(helper, session, "/api/search", {"query": "!!!"})
    assert s == 200 and a["status"] == "ok" and a["hits"] == []


def test_search_api_rejects_bad_dates(helper, session):
    for body in ({"query": "x", "from": "2026-02-28", "to": "2026-02-25"}, {"query": "x", "from": "2026-02-30"},
                 {"query": "x", "to": "yesterday"}, {"query": ""}, {"query": "x" * 501}, {"query": "x", "source": "z"},
                 {"query": "x", "offset": -1}, ["not", "an", "object"]):
        s, a = api(helper, session, "/api/search", body)
        assert s == 400 and a["status"] == "bad" and a["message"]


def test_later_pages_need_the_first_pages_generation(helper, session):
    from inkvault import paths
    _, first = api(helper, session, "/api/search", {"query": "backoff"})
    atlasdata.bump(paths.vectors())
    _, page2 = api(helper, session, "/api/search", {"query": "backoff", "offset": 25, "generation": first["generation"]})
    assert page2["status"] == "changed" and "hits" not in page2


def test_day_bounds_cover_local_days_across_daylight_saving():
    from zoneinfo import ZoneInfo
    from inkvault import atlasserve
    ny = ZoneInfo("America/New_York")
    since, until = atlasserve.day_bounds("2026-03-08", "2026-03-08", tz=ny)  # clocks go forward that night
    from datetime import datetime
    s = datetime.fromisoformat(since.replace("Z", "+00:00"))
    u = datetime.fromisoformat(until.replace("Z", "+00:00"))
    assert u - s == timedelta(hours=23) and s.astimezone(ny).hour == 0
    assert atlasserve.day_bounds(None, None) == ("", "")


def test_record_api(helper, session):
    _, first = api(helper, session, "/api/search", {"query": "jitter"})
    gen = first["generation"]
    _, r = api(helper, session, "/api/record", {"kind": "chats", "id": "m1", "generation": gen})
    assert r["status"] == "ok" and r["items"][0]["matched"] and r["generation"] == gen
    _, missing = api(helper, session, "/api/record", {"kind": "chats", "id": "nope", "generation": gen})
    assert missing["status"] == "missing"
    for body in ({"kind": "zzz", "id": "1", "generation": gen}, {"cursor": "garbage"}, {"kind": "chats", "generation": gen},
                 {"kind": "chats", "id": "m1"}):  # an initial open must say which vault state the page shows
        assert api(helper, session, "/api/record", body)[0] == 400


def test_a_record_opened_after_a_rebuild_is_refused(helper, session):   # Codex plan review #2
    from inkvault import paths
    _, first = api(helper, session, "/api/search", {"query": "jitter"})
    atlasdata.bump(paths.vectors())  # rebuilt between choosing the hit and opening it
    _, r = api(helper, session, "/api/record", {"kind": "chats", "id": "m1", "generation": first["generation"]})
    assert r["status"] == "changed" and "items" not in r


def test_atlas_api_builds_missing_data_then_serves_it(helper, session):
    from inkvault import paths
    assert not paths.dashboard().exists()
    _, a = api(helper, session, "/api/atlas")
    assert a["status"] == "ok" and a["data"]["days"] and paths.dashboard().exists()
    assert a["sources"]["web"] is True and a["freshness"]["built"]


def test_atlas_api_without_days_still_searches(tmp_path, monkeypatch):
    import sqlite3
    atlasdata.make_vault(tmp_path, monkeypatch)
    from inkvault import atlasserve, index, paths
    v = sqlite3.connect(paths.vault_db())
    v.executescript("DELETE FROM events; DELETE FROM screen_stretches; DELETE FROM screen_text; DELETE FROM browser_visits;"
                    "DELETE FROM raw_records WHERE kind != 'asset';")
    v.commit(); v.close()
    index.build()
    h = atlasserve.Helper()
    threading.Thread(target=h.server.serve_forever, daemon=True).start()
    try:
        status, headers, _ = req(h, "GET", f"/open?code={h.new_code()}")
        s = {"Cookie": headers["Set-Cookie"].split(";")[0]}
        assert api(h, s, "/api/atlas")[1]["status"] == "no-days"
        assert api(h, s, "/api/search", {"query": "retry"})[1]["hits"]
    finally:
        h.server.shutdown(); h.server.server_close()


def test_ping_keeps_alive_and_reports_generation(helper, session):
    helper.last_ping = 0
    _, a = api(helper, session, "/api/ping", {})
    assert a["status"] == "ok" and len(a["generation"]) == 16 and helper.last_ping > 0


def test_atlas_data_has_its_own_revision(helper, session):              # Codex plan review #7
    from inkvault import dashboard, paths
    _, a = api(helper, session, "/api/atlas")
    rev = a["atlas_revision"]
    assert rev and api(helper, session, "/api/ping", {})[1]["atlas_revision"] == rev
    gen = a["generation"]
    atlasdata.bump(paths.dashboard())            # charts rebuilt; search.db and vectors untouched
    _, p = api(helper, session, "/api/ping", {})
    assert p["generation"] == gen and p["atlas_revision"] not in (None, rev)
    paths.dashboard().unlink()                   # removed (as forgetting does)
    assert api(helper, session, "/api/ping", {})[1]["atlas_revision"] is None
    dashboard.build()
    _, again = api(helper, session, "/api/atlas")
    assert again["atlas_revision"] == api(helper, session, "/api/ping", {})[1]["atlas_revision"]


def test_nothing_is_logged(helper, session, capfd):
    api(helper, session, "/api/search", {"query": "secretword"})
    req(helper, "GET", "/nope", headers=session)
    out, err = capfd.readouterr()
    assert "secretword" not in out + err and "GET" not in err and "POST" not in err


def test_malformed_input_is_a_400_and_nothing_is_printed(helper, session, capfd):
    for body in ({"kind": [], "id": "1", "generation": "g"}, {"kind": {}, "id": "1", "generation": "g"}):
        assert api(helper, session, "/api/record", body)[0] == 400
    for body in ({"query": "x", "to": "9999-12-31"}, {"query": "x", "from": "0001-01-01"}):
        assert api(helper, session, "/api/search", body)[0] == 400
    assert raw(helper, HEAD + "Content-Length: ²\r\n\r\n", session).startswith(b"HTTP/1.1 400")
    out, err = capfd.readouterr()
    assert err == "" and out == ""


def test_an_unexpected_error_is_a_bare_500_with_nothing_printed(helper, session, monkeypatch, capfd):
    from inkvault import atlasserve

    def boom(body):
        raise RuntimeError("secretword detail")
    monkeypatch.setattr(atlasserve, "api_search", boom)
    s, _, body = req(helper, "POST", "/api/search", {"query": "x"}, {**session, "X-InkVault": "1"})
    assert s == 500 and body == b""
    out, err = capfd.readouterr()
    assert "secretword" not in out + err and err == ""
    assert api(helper, session, "/api/ping", {})[1]["status"] == "ok"


def test_refused_requests_close_the_connection(helper, session):
    _, headers, _ = req(helper, "POST", "/api/search", {"query": "x"}, {})
    assert headers.get("Connection", "").lower() == "close"
