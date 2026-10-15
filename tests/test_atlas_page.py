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


def test_search_uses_every_api_with_the_header_and_never_puts_queries_in_urls():
    js = text("search.js")
    for path in ('"/api/atlas"', '"/api/search"', '"/api/record"', '"/api/ping"'):
        assert path in js
    assert '"X-InkVault": "1"' in js and "sessionStorage" in js
    assert "location.hash" not in js and "history.pushState" not in js and "URLSearchParams" not in js


def test_every_id_the_scripts_use_is_in_the_page():
    html = text("index.html")
    for name in ("search.js", "atlas.js"):
        for id_ in re.findall(r'\$\("([\w-]+)"\)|getElementById\("([\w-]+)"\)', text(name)):
            id_ = id_[0] or id_[1]
            assert f'id="{id_}"' in html, (name, id_)


def test_daily_light_assets_are_served_with_the_right_types_and_the_page_loads_daily_js_in_order():
    assert atlasserve.ASSETS["/assets/daily.js"] == ("daily.js", "text/javascript; charset=utf-8")
    assert atlasserve.ASSETS["/assets/quotes.json"] == ("quotes.json", "application/json")
    html = text("index.html")
    order = [html.index(f'src="/assets/{n}"') for n in ("atlas.js", "daily.js", "search.js")]
    assert order == sorted(order)
    assert '<script src="/assets/daily.js" defer>' in html
    assert (ATLAS / "daily.js").is_file() and (ATLAS / "quotes.json").is_file()  # both live in the package directory


def test_daily_js_never_uses_html_or_inline_styles():
    js = text("daily.js")
    for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "style=", ".style", "toISOString", "eval("):
        assert banned not in js, banned
