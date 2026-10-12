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


@windows
def test_every_binding_is_typed():
    import ctypes
    from inkvault import winapi
    a = winapi.api()
    for dll in (a.user32, a.kernel32, a.gdi32, a.dwmapi):
        for name, f in vars(dll).items():
            if isinstance(f, ctypes._CFuncPtr):
                assert f.argtypes is not None, name


@windows
def test_identity_of_our_own_window_then_minimized():
    import tkinter
    from inkvault import winapi
    root = tkinter.Tk(); root.title("inkvault-test-window"); root.geometry("400x200"); root.update()
    try:
        hwnd = int(root.wm_frame(), 16)
        ident = winapi.identity(hwnd)
        assert ident and ident.title == "inkvault-test-window" and ident.created > 0
        assert ident.exe.lower().startswith("python")
        root.iconify(); root.update()
        assert winapi.identity(hwnd) is None
    finally:
        root.destroy()


@windows
def test_unreadable_process_or_desktop_fails_closed(monkeypatch):           # Review Focus 1 and 2
    from inkvault import winapi
    a = winapi.api()
    monkeypatch.setattr(winapi, "_process", lambda pid: None)               # OpenProcess denied (elevated app)
    assert winapi.identity(a.user32.GetForegroundWindow()) is None
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
