"""The packaged "a little light" quotations (Memory Atlas daily note)."""
import json
import re
from pathlib import Path
from urllib.parse import urlparse

import inkvault

FIELDS = ("id", "text", "author", "work", "source_url", "note")
DENY = ("brainyquote", "goodreads", "quotefancy", "azquotes", "wikiquote")


def _load():
    path = Path(inkvault.__file__).parent / "atlas" / "quotes.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_quotes_file_shape():
    data = _load()
    assert data["version"] == 1
    assert len(data["quotes"]) >= 30


def test_every_quote_is_complete_and_sourced():
    ids = []
    for q in _load()["quotes"]:
        for field in FIELDS:
            assert isinstance(q.get(field), str) and q[field].strip(), (q.get("id"), field)
        assert re.fullmatch(r"[a-z0-9-]+", q["id"]), q["id"]
        assert q["source_url"].startswith("https://"), q["id"]
        assert len(q["text"]) <= 280, q["id"]
        host = (urlparse(q["source_url"]).hostname or "").lower()
        assert not any(bad in host for bad in DENY), q["id"]
        ids.append(q["id"])
    assert len(ids) == len(set(ids))
