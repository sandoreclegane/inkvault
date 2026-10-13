"""The worker (spec §2): checks before and after capture; capture validity and GDI hygiene; OCR of every valid
capture; the protocol. Native tests read only windows the test makes."""
import json
import struct
import subprocess
import sys

import pytest

windows = pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
REQ = {"id": 7, "hwnd": 1, "pid": 2, "created": 3, "exe": "x.exe", "title_sha256": "0" * 64}


def test_changed_when_the_window_moved_on(monkeypatch):
    from inkvault import screentext, winapi
    monkeypatch.setattr(winapi, "matches", lambda r: False)
    assert screentext.handle(REQ) == {"id": 7, "status": "changed", "lines": [], "colors": 0, "error": ""}


def test_two_color_capture_is_still_ocrd(monkeypatch):                         # R5
    from inkvault import screentext, winapi
    monkeypatch.setattr(winapi, "matches", lambda r: True)
    monkeypatch.setattr(screentext, "capture", lambda hwnd, m: ((b"\x00\x00\x00\xff" + b"\xff" * 4) * 500, 50, 20))
    monkeypatch.setattr(screentext, "ocr_available", lambda: True)
    monkeypatch.setattr(screentext, "max_dim", lambda: 10000)
    called = []
    monkeypatch.setattr(screentext, "ocr", lambda raw, w, h: called.append((w, h)) or ["HELLO WORLD"])
    r = screentext.handle(REQ)
    assert called == [(50, 20)] and r["status"] == "ok" and r["colors"] == 2


def test_no_ocr(monkeypatch):
    from inkvault import screentext, winapi
    monkeypatch.setattr(winapi, "matches", lambda r: True)
    monkeypatch.setattr(screentext, "ocr_available", lambda: False)
    assert screentext.handle(REQ)["status"] == "no-ocr"


def test_worker_main_speaks_the_protocol_and_rejects_bad_requests():
    code = ("import sys; from inkvault import screentext, winapi; "
            "winapi.matches = lambda r: False; sys.exit(screentext.worker_main())")
    frame = lambda o: (lambda b: struct.pack(">I", len(b)) + b)(json.dumps(o).encode())
    p = subprocess.run([sys.executable, "-c", code], input=frame(REQ), capture_output=True, timeout=60)
    n = struct.unpack(">I", p.stdout[:4])[0]
    assert json.loads(p.stdout[4:4 + n])["status"] == "changed" and p.returncode == 0
    p = subprocess.run([sys.executable, "-c", code], input=frame([]), capture_output=True, timeout=60)
    assert p.returncode == 2 and p.stdout == b""


@windows
def test_capture_round_trip_handles_and_size(tk_root):
    import tkinter
    from inkvault import screentext, winapi
    screentext.dpi_aware()
    win = tkinter.Toplevel(tk_root); win.title("inkvault-ocr-test"); win.geometry("700x200")
    tkinter.Label(win, text="INKVAULT KNOWN STRING 4217", font=("Segoe UI", 28), bg="white", fg="black").pack(
        expand=True, fill="both")
    win.update(); win.lift(); win.focus_force(); win.update()
    try:
        hwnd = int(win.wm_frame(), 16)
        r = winapi.RECT(); winapi.api().user32.GetWindowRect(hwnd, r)
        before = winapi.gdi_objects()
        for _ in range(50):
            got = screentext.capture(hwnd, 10000)
        assert winapi.gdi_objects() - before <= 2                              # P1: no leak
        raw, w, h = got
        assert (w, h) == (r.right - r.left, r.bottom - r.top) and len(raw) == w * h * 4
        if screentext.ocr_available():
            assert "4217" in " ".join(screentext.ocr(raw, w, h))
    finally:
        win.destroy()


@windows
def test_a_window_closed_mid_capture_returns_nothing(tk_root):                # Review Focus 5
    import tkinter
    from inkvault import screentext
    win = tkinter.Toplevel(tk_root); win.update()
    hwnd = int(win.wm_frame(), 16)
    win.destroy()
    assert screentext.capture(hwnd, 10000) is None


@windows
def test_the_worker_is_per_monitor_dpi_aware():
    code = ("from inkvault import screentext, winapi; screentext.dpi_aware(); u = winapi.api().user32; "
            "print(u.GetAwarenessFromDpiAwarenessContext(u.GetThreadDpiAwarenessContext()))")
    assert subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                          timeout=60).stdout.strip() == "2"
