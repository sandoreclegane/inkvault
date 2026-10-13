"""Windows calls for screen capture (spec §1–§2), through typed bindings only (P1): every function gets argtypes
and restype, handles are pointer-sized, and anything that can't be read counts as "no window" (fail closed).
Importable everywhere; call only on Windows."""
import ctypes
import hashlib
import os
from ctypes import wintypes as W
from types import SimpleNamespace

from .stretches import Identity, Sample

DWMWA_CLOAKED = 14
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SYNCHRONIZE = 0x00100000
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
JobObjectExtendedLimitInformation = 9
GR_GDIOBJECTS = 0
TOKEN_QUERY = 0x0008
TokenUser = 1
MB_OK, MB_ICONINFORMATION, MB_SETFOREGROUND = 0x0, 0x40, 0x10000
_api = None


class RECT(ctypes.Structure):
    _fields_ = [("left", W.LONG), ("top", W.LONG), ("right", W.LONG), ("bottom", W.LONG)]


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", W.UINT), ("dwTime", W.DWORD)]


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [(n, ctypes.c_ulonglong) for n in ("r", "w", "o", "rb", "wb", "ob")]


class BASIC_LIMIT(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", W.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", W.DWORD),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", W.DWORD), ("SchedulingClass", W.DWORD)]


class EXTENDED_LIMIT(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", BASIC_LIMIT), ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]


def _sig(f, res, *args):
    f.restype, f.argtypes = res, list(args)


def api():
    global _api
    if _api is not None:
        return _api
    u = ctypes.WinDLL("user32", use_last_error=True)
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    g = ctypes.WinDLL("gdi32", use_last_error=True)
    d = ctypes.WinDLL("dwmapi", use_last_error=True)
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    H, P = W.HANDLE, ctypes.POINTER
    _sig(u.GetForegroundWindow, W.HWND)
    _sig(u.IsWindowVisible, W.BOOL, W.HWND)
    _sig(u.IsIconic, W.BOOL, W.HWND)
    _sig(u.GetWindowThreadProcessId, W.DWORD, W.HWND, P(W.DWORD))
    _sig(u.GetWindowTextLengthW, ctypes.c_int, W.HWND)
    _sig(u.GetWindowTextW, ctypes.c_int, W.HWND, W.LPWSTR, ctypes.c_int)
    _sig(u.GetWindowRect, W.BOOL, W.HWND, P(RECT))
    _sig(u.GetWindowDC, W.HDC, W.HWND)
    _sig(u.ReleaseDC, ctypes.c_int, W.HWND, W.HDC)
    _sig(u.PrintWindow, W.BOOL, W.HWND, W.HDC, W.UINT)
    _sig(u.OpenInputDesktop, H, W.DWORD, W.BOOL, W.DWORD)
    _sig(u.GetUserObjectInformationW, W.BOOL, H, ctypes.c_int, ctypes.c_void_p, W.DWORD, P(W.DWORD))
    _sig(u.CloseDesktop, W.BOOL, H)
    _sig(u.GetLastInputInfo, W.BOOL, P(LASTINPUTINFO))
    _sig(u.SetProcessDpiAwarenessContext, W.BOOL, H)
    _sig(u.GetThreadDpiAwarenessContext, H)
    _sig(u.GetAwarenessFromDpiAwarenessContext, ctypes.c_int, H)
    _sig(u.GetGuiResources, W.DWORD, H, W.DWORD)
    _sig(u.MessageBoxW, ctypes.c_int, W.HWND, W.LPCWSTR, W.LPCWSTR, W.UINT)
    _sig(k.OpenProcess, H, W.DWORD, W.BOOL, W.DWORD)
    _sig(k.CloseHandle, W.BOOL, H)
    _sig(k.QueryFullProcessImageNameW, W.BOOL, H, W.DWORD, W.LPWSTR, P(W.DWORD))
    _sig(k.GetProcessTimes, W.BOOL, H, P(W.FILETIME), P(W.FILETIME), P(W.FILETIME), P(W.FILETIME))
    _sig(k.GetExitCodeProcess, W.BOOL, H, P(W.DWORD))
    _sig(k.GetTickCount64, ctypes.c_ulonglong)
    _sig(k.GetCurrentProcess, H)
    _sig(k.LocalFree, ctypes.c_void_p, ctypes.c_void_p)
    _sig(k.CreateJobObjectW, H, ctypes.c_void_p, W.LPCWSTR)
    _sig(k.SetInformationJobObject, W.BOOL, H, ctypes.c_int, ctypes.c_void_p, W.DWORD)
    _sig(k.AssignProcessToJobObject, W.BOOL, H, H)
    _sig(k.PeekNamedPipe, W.BOOL, H, ctypes.c_void_p, W.DWORD, ctypes.c_void_p, P(W.DWORD), ctypes.c_void_p)
    _sig(g.CreateCompatibleDC, W.HDC, W.HDC)
    _sig(g.CreateCompatibleBitmap, W.HBITMAP, W.HDC, ctypes.c_int, ctypes.c_int)
    _sig(g.SelectObject, W.HGDIOBJ, W.HDC, W.HGDIOBJ)
    _sig(g.DeleteObject, W.BOOL, W.HGDIOBJ)
    _sig(g.DeleteDC, W.BOOL, W.HDC)
    _sig(g.SetStretchBltMode, ctypes.c_int, W.HDC, ctypes.c_int)
    _sig(g.StretchBlt, W.BOOL, W.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
         W.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, W.DWORD)
    _sig(g.GetDIBits, ctypes.c_int, W.HDC, W.HBITMAP, W.UINT, W.UINT, ctypes.c_void_p, ctypes.c_void_p, W.UINT)
    _sig(d.DwmGetWindowAttribute, ctypes.c_long, W.HWND, W.DWORD, ctypes.c_void_p, W.DWORD)
    _sig(adv.OpenProcessToken, W.BOOL, H, W.DWORD, P(H))
    _sig(adv.GetTokenInformation, W.BOOL, H, ctypes.c_int, ctypes.c_void_p, W.DWORD, P(W.DWORD))
    _sig(adv.ConvertSidToStringSidW, W.BOOL, ctypes.c_void_p, P(ctypes.c_void_p))
    # Resolve every function now, so test_every_binding_is_typed sees them all.
    _api = SimpleNamespace(user32=u, kernel32=k, gdi32=g, dwmapi=d, advapi32=adv)
    return _api


def idle_ms(now64, last32):
    return ((now64 & 0xFFFFFFFF) - last32) % 2**32


def title_sha256(title):
    return hashlib.sha256((title or "").encode("utf-8", "surrogatepass")).hexdigest()


def _process(pid):
    k = api().kernel32
    h = k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return None
    try:
        buf, n = ctypes.create_unicode_buffer(1024), W.DWORD(1024)
        if not k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
            return None
        t = [W.FILETIME() for _ in range(4)]
        if not k.GetProcessTimes(h, *(ctypes.byref(x) for x in t)):
            return None
        return os.path.basename(buf.value), (t[0].dwHighDateTime << 32) | t[0].dwLowDateTime
    finally:
        k.CloseHandle(h)


def cloaked(hwnd):
    v = W.DWORD()
    if api().dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(v), ctypes.sizeof(v)) != 0:
        return True  # can't tell: fail closed
    return v.value != 0


def identity(hwnd):
    u = api().user32
    if not hwnd or not u.IsWindowVisible(hwnd) or u.IsIconic(hwnd) or cloaked(hwnd):
        return None
    pid = W.DWORD()
    if not u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)):
        return None
    proc = _process(pid.value)
    if proc is None:
        return None
    n = u.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    u.GetWindowTextW(hwnd, buf, n + 1)
    return Identity(int(hwnd), pid.value, proc[1], proc[0], buf.value)


def foreground():
    return identity(api().user32.GetForegroundWindow())


def _input_desktop_name():
    u = api().user32
    desk = u.OpenInputDesktop(0, False, 0x0001)  # DESKTOP_READOBJECTS
    if not desk:
        return None
    try:
        buf, n = ctypes.create_unicode_buffer(256), W.DWORD()
        if not u.GetUserObjectInformationW(desk, 2, buf, ctypes.sizeof(buf), ctypes.byref(n)):  # UOI_NAME
            return None
        return buf.value
    finally:
        u.CloseDesktop(desk)


def input_desktop_default():
    name = _input_desktop_name()
    return name is not None and name.lower() == "default"


def idle_seconds():
    info = LASTINPUTINFO(ctypes.sizeof(LASTINPUTINFO), 0)
    if not api().user32.GetLastInputInfo(ctypes.byref(info)):
        return 0.0
    return idle_ms(api().kernel32.GetTickCount64(), info.dwTime) / 1000.0


def locked(window):
    return (window is not None and window.exe.lower() == "lockapp.exe") or not input_desktop_default()


def sample(mono, utc):
    w = foreground()
    return Sample(mono, utc, w, idle_seconds(), locked(w))


def matches(req):
    if not input_desktop_default():
        return False
    fg = foreground()
    return (fg is not None and (fg.hwnd, fg.pid, fg.created, fg.exe) == (req["hwnd"], req["pid"], req["created"],
            req["exe"]) and title_sha256(fg.title) == req["title_sha256"])


def close_handle(h):
    if h:
        api().kernel32.CloseHandle(h)


def kill_on_close_job(proc):
    k = api().kernel32
    job = k.CreateJobObjectW(None, None)
    if not job:
        raise OSError(ctypes.get_last_error(), "CreateJobObject failed")
    info = EXTENDED_LIMIT()
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    ok = (k.SetInformationJobObject(job, JobObjectExtendedLimitInformation, ctypes.byref(info), ctypes.sizeof(info))
          and k.AssignProcessToJobObject(job, W.HANDLE(int(proc._handle))))
    if not ok:
        err = ctypes.get_last_error()
        k.CloseHandle(job)
        raise OSError(err, "the worker couldn't be put in a job")
    return job


def process_alive(pid):
    k = api().kernel32
    h = k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, False, pid)
    if not h:
        return False
    try:
        code = W.DWORD()
        return bool(k.GetExitCodeProcess(h, ctypes.byref(code))) and code.value == 259  # STILL_ACTIVE
    finally:
        k.CloseHandle(h)


def gdi_objects():
    a = api()
    return a.user32.GetGuiResources(a.kernel32.GetCurrentProcess(), GR_GDIOBJECTS)


def current_user_sid():
    """The signed-in user's SID as a string (S-1-5-21-...), or None if anything fails. Task Scheduler may export
    a task's user as this instead of the domain and account name."""
    try:
        a = api()
        k, adv = a.kernel32, a.advapi32
        token = W.HANDLE()
        if not adv.OpenProcessToken(k.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(token)):
            return None
        try:
            size = W.DWORD()
            adv.GetTokenInformation(token, TokenUser, None, 0, ctypes.byref(size))   # fails, filling in the size
            if not size.value:
                return None
            buf = ctypes.create_string_buffer(size.value)
            if not adv.GetTokenInformation(token, TokenUser, buf, size.value, ctypes.byref(size)):
                return None
            sid = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]   # TOKEN_USER starts with the SID pointer
            text = ctypes.c_void_p()
            if not sid or not adv.ConvertSidToStringSidW(sid, ctypes.byref(text)):
                return None
            try:
                return ctypes.wstring_at(text.value)
            finally:
                k.LocalFree(text)
        finally:
            k.CloseHandle(token)
    except Exception:  # noqa: BLE001 — a missing SID only makes the install check stricter
        return None
