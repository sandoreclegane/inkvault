"""search.js driven in Node against a stand-in page (Codex plan review #2, #4, #7). Skipped where Node isn't installed."""
import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import REAL_POPEN, REAL_RUN  # conftest's guards allow only this Python; Node is the one program these tests start

JS = Path(__file__).parent / "js"
NODE = shutil.which("node")


@pytest.mark.skipif(not NODE, reason="Node isn't installed")
@pytest.mark.parametrize("scenario", ["restore", "late_record", "rebuild_between", "atlas_revision", "hidden_and_closed", "server_error"])
def test_search_js(scenario, monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", REAL_POPEN)  # only for this test: the one Node run it starts
    r = REAL_RUN([NODE, str(JS / f"{scenario}.cjs")], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr or r.stdout
