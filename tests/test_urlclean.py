"""What cleaning keeps and removes. Shapes come from Codex's measurements on real history; the values are made up."""
from urllib.parse import quote

import pytest

from inkvault.urlclean import clean_title, clean_url

JWT = "eyJhbGciOi.eyJzdWIi.c2ln"
JWE = "eyJhbGciOi.a1.b2.c3.d4"


@pytest.mark.parametrize("raw, cleaned", [
    # nothing to remove: the address stays exactly as the browser wrote it
    ("https://example.com/a?q=retry+backoff&page=2", "https://example.com/a?q=retry+backoff&page=2"),
    # a search keeps its terms; a credential next to it goes
    ("https://www.google.com/search?q=retry+backoff&rapt=AEjH", "https://www.google.com/search?q=retry%20backoff"),
    # OAuth callback
    ("https://accounts.example.com/cb?code=abc&state=xyz&next=%2Fhome", "https://accounts.example.com/cb?next=/home"),
    # names compared without case or separators; cloud-storage signatures
    ("https://e.example/x?token_id=1&__clerk_handshake=2&_vercel_jwt=3&login_verifier=4&user_code=5&xsrf=6"
     "&X-Amz-Signature=7&tokenid=8&keep=1", "https://e.example/x?keep=1"),
    # identity parameters
    ("https://login.example.com/?login_hint=me%40example.com&email=a@b.co&upn=x&lang=en",
     "https://login.example.com/?lang=en"),
    # an email or a JWT/JWE value goes whatever its name
    ("https://e.example/?to=someone@example.org&lang=en", "https://e.example/?lang=en"),
    (f"https://e.example/?data={JWT}&x=1", "https://e.example/?x=1"),
    (f"https://e.example/?data={JWE}&x=1", "https://e.example/?x=1"),
    # an address nested in a parameter is cleaned too
    ("https://app.example.com/login?redirect_uri=" + quote("https://cb.example.com/done?access_token=abc&tab=2", safe=""),
     "https://app.example.com/login?redirect_uri=https://cb.example.com/done%3Ftab%3D2"),
    # fragments: route plus parameters, parameters, a bare token, an app route
    ("https://app.example.com/#/callback?access_token=abc&view=list", "https://app.example.com/#/callback?view=list"),
    ("https://x.example/cb#access_token=abc&expires_in=3600", "https://x.example/cb#expires_in=3600"),
    (f"https://x.example/#{JWT}", "https://x.example/"),
    ("https://x.example/#" + "a1" * 20, "https://x.example/"),
    ("https://mail.example.com/#inbox/FMfcgzQ", "https://mail.example.com/#inbox/FMfcgzQ"),
    # paths: only after a sign-in word, or a JWT anywhere
    ("https://example.com/reset/Ab3dEf6hIj9kLm2nOp5q", "https://example.com/reset/…"),
    ("https://example.com/oauth/callback", "https://example.com/oauth/callback"),
    (f"https://example.com/v/{JWT}/x", "https://example.com/v/…/x"),
    ("https://docs.google.com/document/d/1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789abcdEFG/edit",
     "https://docs.google.com/document/d/1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789abcdEFG/edit"),
    ("https://example.com/issues/123e4567-e89b-12d3-a456-426614174000",
     "https://example.com/issues/123e4567-e89b-12d3-a456-426614174000"),
    # user:password@ goes; the host is never changed
    ("https://user:pw@example.com/", "https://example.com/"),
    # shapes are matched decoded: percent-encoding doesn't hide a token in a fragment
    ("https://x.example/#%65yJhbGciOi.eyJzdWIi.c2ln", "https://x.example/"),
    ("https://x.example/#%61" + "1a" * 20, "https://x.example/"),
    ("https://x.example/cb#access%5Ftoken%3Dabc%26tab%3D2", "https://x.example/cb#tab=2"),
    ("https://x.example/#data%3D" + JWT + "&tab=2", "https://x.example/#tab=2"),
    ("https://x.example/#/cb%3Faccess_token=abc", "https://x.example/#/cb"),
    ("http://[::1]:8080/x?q=1", "http://[::1]:8080/x?q=1"),
])
def test_cleaning(raw, cleaned):
    assert clean_url(raw) == cleaned


@pytest.mark.parametrize("raw", ["chrome://settings", "file:///C:/notes.txt", "about:blank", "edge://newtab",
                                 "chrome-extension://abc/page.html", "http://[::1", "https:///reset/abc"])
def test_only_http_addresses_are_kept(raw):
    assert clean_url(raw) is None


def test_a_nested_address_past_the_third_level_is_dropped():
    level4 = "https://d.example/x?a=1"
    level3 = "https://c.example/?next=" + quote(level4, safe="")
    level2 = "https://b.example/?next=" + quote(level3, safe="")
    level1 = "https://a.example/?next=" + quote(level2, safe="")
    cleaned = clean_url("https://top.example/?next=" + quote(level1, safe=""))
    assert "c.example" in cleaned and "d.example" not in cleaned


def test_titles_are_cleaned_and_clipped():
    assert clean_title("Signed in — https://x.example/cb?code=abc&tab=2") == "Signed in — https://x.example/cb?tab=2"
    assert len(clean_title("x" * 500)) == 300
    assert clean_title("") == "" and clean_title(None) is None
