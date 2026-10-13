"""The capture worker (spec §2). Runs as `python -m inkvault.screentext --worker`: validated requests in on stdin,
validated answers out on stdout. Pixels live only in this process's memory.

Before and after capture: winapi.matches (same window, identity and title hash; uncloaked, unminimized, Default
desktop). No BitBlt fallback. Each bitmap is deselected before GetDIBits and before it's deleted (P1); any failed
allocation or call means no capture. Every valid capture is OCR'd (R5)."""
import asyncio
import ctypes
import sys
from ctypes import wintypes as W

from . import winapi, worker

PW_RENDERFULLCONTENT = 2
HALFTONE = 4
SRCCOPY = 0x00CC0020
_engine = None


class BIH(ctypes.Structure):
    _fields_ = [("biSize", W.DWORD), ("biWidth", W.LONG), ("biHeight", W.LONG), ("biPlanes", W.WORD),
                ("biBitCount", W.WORD), ("biCompression", W.DWORD), ("biSizeImage", W.DWORD),
                ("biXPelsPerMeter", W.LONG), ("biYPelsPerMeter", W.LONG), ("biClrUsed", W.DWORD),
                ("biClrImportant", W.DWORD)]


def dpi_aware():
    if sys.platform == "win32":
        winapi.api().user32.SetProcessDpiAwarenessContext(W.HANDLE(-4))  # PER_MONITOR_AWARE_V2


def colors(raw):
    return len({raw[i:i + 3] for i in range(0, len(raw), 4 * 997)})


def _rect(hwnd):
    r = winapi.RECT()
    if not winapi.api().user32.GetWindowRect(hwnd, ctypes.byref(r)):
        return None
    return r.right - r.left, r.bottom - r.top


def capture(hwnd, max_dim):
    a = winapi.api()
    u, g = a.user32, a.gdi32
    size = _rect(hwnd)
    if not size or size[0] <= 0 or size[1] <= 0:
        return None
    w, h = size
    scale = min(1.0, max_dim / max(w, h))
    ow, oh = max(1, int(w * scale)), max(1, int(h * scale))
    hdc = u.GetWindowDC(hwnd)
    if not hdc:
        return None
    mdc = bmp = old = sdc = sbmp = sold = None
    try:
        mdc, bmp = g.CreateCompatibleDC(hdc), g.CreateCompatibleBitmap(hdc, w, h)
        if not mdc or not bmp:
            return None
        old = g.SelectObject(mdc, bmp)
        if not old or not u.PrintWindow(hwnd, mdc, PW_RENDERFULLCONTENT):
            return None
        src_dc, src_bmp = mdc, bmp
        if scale < 1.0:
            sdc, sbmp = g.CreateCompatibleDC(hdc), g.CreateCompatibleBitmap(hdc, ow, oh)
            if not sdc or not sbmp:
                return None
            sold = g.SelectObject(sdc, sbmp)
            g.SetStretchBltMode(sdc, HALFTONE)
            if not sold or not g.StretchBlt(sdc, 0, 0, ow, oh, mdc, 0, 0, w, h, SRCCOPY):
                return None
            src_dc, src_bmp = sdc, sbmp
            g.SelectObject(sdc, sold); sold = None          # deselect before GetDIBits
        else:
            g.SelectObject(mdc, old); old = None
        bih = BIH(ctypes.sizeof(BIH), ow, -oh, 1, 32, 0, 0, 0, 0, 0, 0)  # top-down BGRA
        buf = (ctypes.c_ubyte * (ow * oh * 4))()
        if g.GetDIBits(src_dc, src_bmp, 0, oh, buf, ctypes.byref(bih), 0) != oh:
            return None
        if _rect(hwnd) != (w, h):
            return None
        return bytes(buf), ow, oh
    finally:
        if sold:
            g.SelectObject(sdc, sold)
        if old:
            g.SelectObject(mdc, old)
        for obj in (sbmp, bmp):
            if obj:
                g.DeleteObject(obj)
        for dc in (sdc, mdc):
            if dc:
                g.DeleteDC(dc)
        u.ReleaseDC(hwnd, hdc)


def engine():
    global _engine
    if _engine is None:
        from winrt.windows.media.ocr import OcrEngine
        _engine = OcrEngine.try_create_from_user_profile_languages()
    return _engine


def ocr_available():
    if sys.platform != "win32":
        return False
    try:
        return engine() is not None
    except ImportError:
        return False


def max_dim():
    from winrt.windows.media.ocr import OcrEngine
    return OcrEngine.max_image_dimension


def ocr(raw, w, h):
    from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
    from winrt.windows.storage.streams import DataWriter
    dw = DataWriter()
    dw.write_bytes(raw)
    bmp = SoftwareBitmap.create_copy_from_buffer(dw.detach_buffer(), BitmapPixelFormat.BGRA8, w, h)  # 4 arguments
    return [line.text for line in asyncio.run(_recognize(bmp)).lines]


async def _recognize(bmp):
    return await engine().recognize_async(bmp)


def handle(req):
    out = {"id": req["id"], "status": "failed", "lines": [], "colors": 0, "error": ""}
    try:
        if not winapi.matches(req):
            out["status"] = "changed"
            return out
        if not ocr_available():
            out["status"] = "no-ocr"
            return out
        got = capture(req["hwnd"], max_dim())
        if got is None:
            return out
        raw, w, h = got
        out["colors"] = colors(raw)
        if not winapi.matches(req):
            out["status"] = "changed"
            return out
        out["lines"], out["status"] = ocr(raw, w, h), "ok"
    except Exception as e:  # noqa: BLE001 — the class name only
        out["error"] = type(e).__name__
    return out


def worker_main():
    dpi_aware()
    dec = worker.Decoder(worker.MAX_REQUEST, worker.check_request)
    inp, out = sys.stdin.buffer, sys.stdout.buffer
    while chunk := inp.read1(65536):
        try:
            requests = dec.feed(chunk)
        except worker.ProtocolError:
            return 2
        for req in requests:
            out.write(worker.encode(handle(req)))
            out.flush()
    return 0


if __name__ == "__main__" and "--worker" in sys.argv:
    sys.exit(worker_main())
