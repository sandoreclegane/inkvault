"""Redaction of every stored screen field (spec §4): secrets go, ordinary text stays."""
import pytest

SECRET = [
    "sk-proj-AbCdEf0123456789AbCdEf0123456789",
    "ghp_0123456789abcdefABCDEF0123456789abcd",
    "xoxb-" + "0" * 12 + "-" + "0" * 13 + "-" + "a" * 24,
    "AKIAIOSFODNN7EXAMPLE",
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1gFWFOEjXk",
    "token=9f8e7d6c5b4a39281706f5e4d3c2b1a0",
    "password: hunter2hunter2hunter2",
]
HARMLESS = [
    "Fix the retry helper with exponential backoff",
    "git commit -m 'Merge the browser-history branch'",
    "The key to this design is a durable generation",
    "4dda9763fc17c14a0b2f93d8c32a86b02fc6a746",
    "@名前 🎮 - Discord",
]


@pytest.mark.parametrize("s", SECRET)
def test_secret_shapes_are_masked(s):
    from inkvault import redact
    out = redact.redact(f"before {s} after")
    assert redact.MASK in out and out.startswith("before") and out.endswith("after")
    assert s.split("=")[-1].split(":")[-1].strip() not in out


@pytest.mark.parametrize("s", HARMLESS)
def test_ordinary_text_is_untouched(s):
    from inkvault import redact
    assert redact.redact(s) == s


def test_titles_get_url_cleaning_then_redaction_and_are_clipped():
    from inkvault import redact
    t = ("PowerShell - curl https://api.example.com/v1?access_token=abc123def456 "
         "ghp_0123456789abcdefABCDEF0123456789abcd")
    out = redact.clean_title(t)
    assert "abc123def456" not in out and "ghp_" not in out
    assert out.startswith("PowerShell - curl https://api.example.com")
    assert len(redact.clean_title("x" * 1000)) == 300 and redact.clean_title(None) is None


def test_clean_text():
    from inkvault import redact
    assert redact.clean_text(["ok", "  "]) is None
    out = redact.clean_text(["export OPENAI_API_KEY=sk-proj-AbCdEf0123456789AbCdEf0123456789", "next line here"])
    assert out.splitlines()[1] == "next line here" and "sk-proj" not in out


def test_long_unicode_titles_survive():
    from inkvault import redact
    out = redact.clean_title("@名前 🎮 " * 60 + "- Discord")
    assert len(out) == 300 and out.startswith("@名前 🎮")


def test_secret_straddling_the_clip_is_fully_masked():
    from inkvault import redact
    out = redact.clean_title("a" * 290 + " ghp_0123456789abcdefABCDEF0123456789abcd")
    assert "ghp_" not in out and len(out) == 300


def test_clean_text_drops_a_line_that_is_only_a_secret():
    from inkvault import redact
    assert redact.clean_text(["ghp_0123456789abcdefABCDEF0123456789abcd"]) is None
