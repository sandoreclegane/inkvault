"""MCP answers, byte for byte (spec §3.1 compatibility contract). Captured from v0.3.0 before retrieval.py existed;
moving search into retrieval.py must not change one character. Never regenerate after the move. Generating (once):
    INKVAULT_REGEN_GOLDEN=1 uv run pytest tests/test_mcp_golden.py
"""
import json
import os
from pathlib import Path

import pytest

import atlasdata

GOLDEN = Path(__file__).parent / "golden" / "mcp.json"

CASES = {
    "search keyword backoff": lambda s: s.search_memories("backoff", mode="keyword"),
    "search hybrid backoff": lambda s: s.search_memories("backoff"),
    "search meaning harbor": lambda s: s.search_memories("harbor", mode="meaning"),
    "search hybrid limit 2": lambda s: s.search_memories("backoff", limit=2),
    "search chats jitter": lambda s: s.search_memories("jitter", source="chats"),
    "search web backoff": lambda s: s.search_memories("backoff", source="web", mode="keyword"),
    "search screen docs": lambda s: s.search_memories("docs", source="screen", mode="keyword"),
    "search events note": lambda s: s.search_memories("note", source="events"),
    "search dates": lambda s: s.search_memories("harbor", since="2026-02-26", until="2026-02-26"),
    "search times": lambda s: s.search_memories("backoff", since="2026-02-27T15:00", until="2026-02-27T16:30"),
    "search bad mode": lambda s: s.search_memories("x", mode="fuzzy"),
    "search bad source": lambda s: s.search_memories("x", source="nope"),
    "search bad date": lambda s: s.search_memories("x", since="2026-13-45T99"),
    "search bad range": lambda s: s.search_memories("x", since="2026-02-28", until="2026-02-25"),
    "search bad keyword query": lambda s: s.search_memories('"unclosed', mode="keyword"),
    "search no matches": lambda s: s.search_memories("zzzzqqq"),
    "search punctuation": lambda s: s.search_memories("!!!"),
    "get snippet": lambda s: s.get_memory("x1"),
    "get summary": lambda s: s.get_memory("s5"),
    "get event": lambda s: s.get_memory("e1"),
    "get chat": lambda s: s.get_memory("m1"),
    "get long chat": lambda s: s.get_memory("m20"),
    "get long chat small": lambda s: s.get_memory("m20", max_chars=500),
    "get web": lambda s: s.get_memory(atlasdata.web_id()),
    "get screen": lambda s: s.get_memory("screen:a"),
    "get screentext": lambda s: s.get_memory("screentext:1"),
    "get missing": lambda s: s.get_memory("nope"),
    "timeline": lambda s: s.timeline("2026-02-25", "2026-02-28"),
    "timeline bad limit": lambda s: s.timeline("2026-02-25", limit=0),
    "stats": lambda s: s.memory_stats(),
}


@pytest.fixture
def answers(tmp_path, monkeypatch):
    atlasdata.make_vault(tmp_path, monkeypatch)
    from inkvault import server
    atlasdata.utc_times(monkeypatch)
    return {name: case(server) for name, case in CASES.items()}


def test_mcp_answers_are_unchanged(answers):
    if os.environ.get("INKVAULT_REGEN_GOLDEN") == "1":
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(json.dumps(answers, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        pytest.skip("golden file written; commit it and run again without INKVAULT_REGEN_GOLDEN")
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert sorted(golden) == sorted(CASES)
    for name in CASES:
        assert answers[name] == golden[name], name


def test_the_golden_cases_exercise_what_they_name():
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert "[snippets] x1" in golden["search keyword backoff"]
    assert golden["search meaning harbor"].startswith("[summaries] s5")
    assert "truncated" in golden["get long chat"] and "truncated" in golden["get long chat small"]
    assert golden["search bad mode"].startswith("Unknown mode")
    assert golden["search bad range"].startswith("Bad range")
    assert golden["get web"].startswith("WEB PAGE") and "synced from another device" in golden["get web"]
    assert "Screen (live, newest first)" in golden["timeline"]
