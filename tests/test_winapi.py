"""Typed Win32 layer (spec §1, §2, P1). Pure parts run everywhere; real calls only on Windows, against windows
and processes the test creates itself."""
import subprocess
import sys
import time

import pytest

windows = pytest.mark.skipif(sys.platform != "win32", reason="Windows only")


def test_idle_ms_survives_rollover_and_title_hash_is_stable():
    from inkvault import winapi
    assert winapi.idle_ms(5_000, 4_000) == 1_000
    assert winapi.idle_ms(2**32 + 500, 2**32 - 500) == 1_000
    assert winapi.idle_ms(3 * 2**32 + 10, 0) == 10
    assert winapi.title_sha256("é") == winapi.title_sha256("é") and len(winapi.title_sha256("")) == 64


@pytest.fixture
def own_window(tk_root):
    """A titled Toplevel on the session's shared Tk root."""
    import tkinter
    win = tkinter.Toplevel(tk_root); win.title("inkvault-test-window"); win.geometry("400x200"); win.update()
    try:
        yield win
    finally:
        win.destroy()


INT_RESULT = {"GetWindowTextLengthW", "GetWindowTextW", "ReleaseDC", "SetStretchBltMode", "GetDIBits",
              "GetAwarenessFromDpiAwarenessContext", "DwmGetWindowAttribute"}
BOOL_RESULT = {"IsWindowVisible", "IsIconic", "GetWindowRect", "PrintWindow", "GetUserObjectInformationW",
               "CloseDesktop", "GetLastInputInfo", "SetProcessDpiAwarenessContext", "CloseHandle",
               "QueryFullProcessImageNameW", "GetProcessTimes", "GetExitCodeProcess", "SetInformationJobObject",
               "AssignProcessToJobObject", "PeekNamedPipe", "DeleteObject", "DeleteDC", "StretchBlt"}
HANDLE_RESULT = {"GetForegroundWindow", "GetWindowDC", "OpenInputDesktop", "OpenProcess", "CreateJobObjectW",
                 "GetCurrentProcess", "CreateCompatibleDC", "CreateCompatibleBitmap", "SelectObject",
                 "GetThreadDpiAwarenessContext"}


@windows
def test_every_binding_is_typed():
    import ctypes
    from inkvault import winapi
    a = winapi.api()
    seen = set()
    for dll in (a.user32, a.kernel32, a.gdi32, a.dwmapi):
        for name, f in vars(dll).items():
            if isinstance(f, ctypes._CFuncPtr):
                seen.add(name)
                assert f.argtypes is not None, name
                if name not in INT_RESULT | BOOL_RESULT:   # BOOL is c_long, the same type as c_int
                    assert f.restype is not ctypes.c_int, name   # default restype would truncate handles
                if name in HANDLE_RESULT:
                    assert ctypes.sizeof(f.restype) == ctypes.sizeof(ctypes.c_void_p), name
    assert HANDLE_RESULT | INT_RESULT | BOOL_RESULT <= seen


@windows
def test_identity_of_our_own_window_then_minimized(own_window):
    from inkvault import winapi
    root = own_window
    root.deiconify(); root.update()
    try:
        hwnd = int(root.wm_frame(), 16)
        ident = winapi.identity(hwnd)
        assert ident and ident.title == "inkvault-test-window" and ident.created > 0
        assert ident.exe.lower().startswith("python")
        root.iconify(); root.update()
        assert winapi.identity(hwnd) is None
    finally:
        root.deiconify(); root.update()


@windows
def test_unreadable_process_or_desktop_fails_closed(own_window, monkeypatch):   # Review Focus 1 and 2
    from inkvault import winapi
    root = own_window
    root.deiconify(); root.update()
    hwnd = int(root.wm_frame(), 16)
    assert winapi.identity(hwnd) is not None                                # readable before the patch
    monkeypatch.setattr(winapi, "_process", lambda pid: None)               # OpenProcess denied (elevated app)
    assert winapi.identity(hwnd) is None
    monkeypatch.setattr(winapi, "_input_desktop_name", lambda: None)        # RDP / secure desktop
    assert winapi.input_desktop_default() is False and winapi.locked(None) is True


@windows
def test_job_kills_the_child_when_its_parent_dies():
    parent = ("import subprocess, sys, time; from inkvault import winapi; "
              "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
              "winapi.kill_on_close_job(c); print(c.pid, flush=True); time.sleep(60)")
    p = subprocess.Popen([sys.executable, "-c", parent], stdout=subprocess.PIPE, text=True)
    try:
        child = int(p.stdout.readline())
        from inkvault import winapi
        assert winapi.process_alive(child)
        p.kill(); p.wait(timeout=10)
        end = time.monotonic() + 5
        while winapi.process_alive(child) and time.monotonic() < end:
            time.sleep(0.1)
        assert not winapi.process_alive(child)
    finally:
        if p.poll() is None:
            p.kill()
