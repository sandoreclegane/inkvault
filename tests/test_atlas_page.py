"""The packaged page obeys its own security policy (spec §3.5, §5 rules 8-9). Browser behaviour is checked in Task 12."""
import hashlib
import re
from pathlib import Path

from inkvault import atlasserve

ATLAS = Path(atlasserve.__file__).with_name("atlas")


def text(name):
    return (ATLAS / name).read_text(encoding="utf-8")


def test_every_served_file_exists_and_the_page_uses_only_them():
    for name, _ in atlasserve.ASSETS.values():
        assert (ATLAS / name).is_file(), name
    html = text("index.html")
    for ref in re.findall(r'(?:src|href)="([^"]+)"', html):
        assert ref in atlasserve.ASSETS, ref


def test_no_inline_script_style_or_handlers():
    html = text("index.html")
    assert "<style" not in html and " style=" not in html
    assert not re.search(r"\s(on[a-z]+)=", html)  # no inline event handlers
    for script in re.findall(r"<script\b[^>]*>(.*?)</script>", html, re.S):
        assert script.strip() == ""


def test_tooltips_escape_and_carry_no_styles():
    js = text("atlas.js")
    assert ('const esc = s => String(s).replace(/[&<>"\']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '
            '\'"\': "&quot;", "\'": "&#39;"}[c]));') in js
    assert "style=" not in js and "innerHTML" not in js


def test_search_never_uses_html():
    js = text("search.js")
    assert "innerHTML" not in js and "outerHTML" not in js and "insertAdjacentHTML" not in js


def test_echarts_is_the_pinned_build_with_its_license():
    data = (ATLAS / "echarts.min.js").read_bytes()
    assert hashlib.sha256(data).hexdigest() == text("ECHARTS.sha256").split()[0]
    assert b"5.5.0" in data
    assert "Apache License" in text("ECHARTS-LICENSE") and text("ECHARTS-NOTICE").strip()


def test_mark_and_icon():
    png = (ATLAS / "mark.png").read_bytes()
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) < 100_000
    assert (ATLAS / "inkvault.ico").read_bytes()[:4] == b"\x00\x00\x01\x00"
