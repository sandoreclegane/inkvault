"""A throwaway Atlas on fictional, hostile data, for the browser check. Never touches a real vault.

    uv run python tests/manual/atlas_demo.py
Prints the temporary folder, then runs the helper there and opens the browser. Ctrl+C stops it; the folder is kept
for inspection and can be deleted afterwards.
"""
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

home = Path(tempfile.mkdtemp(prefix="inkvault-atlas-demo-"))
os.environ["INKVAULT_HOME"] = str(home)
from inkvault import atlasserve, dashboard, embed, export, index  # noqa: E402

embed.build = lambda: None  # keyword search only: no model download
HOSTILE = ["<script>alert('x')</script>", "<img src=x onerror=alert(1)>", "fake highlight",
           "\"quotes\" & 'apostrophes'", "right-to-left ‮gnirts"]

db = export.open_vault()
records = [("conversation", "c1", {"id": "c1", "name": HOSTILE[0]})]
for i in range(120):
    text = HOSTILE[i % len(HOSTILE)] + f" lighthouse message {i}" + (" long" * 70_000 if i == 60 else "")
    records.append(("message", f"m{i}", {"id": f"m{i}", "created": {"value": f"2026-03-01T{10 + i // 60:02d}:{i % 60:02d}:00Z"},
                                         "role": "USER" if i % 2 == 0 else "ASSISTANT", "conversation": {"id": "c1"},
                                         "fragment": {"string": {"raw": text}}}))
for d in range(1, 29):
    records.append(("summary", f"s{d}", {"id": f"s{d}", "name": HOSTILE[d % len(HOSTILE)] + f" Harbor day {d}",
                                         "created": {"value": f"2026-02-{d:02d}T12:00:00Z"}}))
db.executemany("INSERT INTO raw_records VALUES (?,?,?)", [(k, i, json.dumps(r)) for k, i, r in records])
db.execute("INSERT INTO events VALUES ('e1','2026-02-20T10:00:00Z','chrome.exe',?, 'javascript:alert(1)', ?, '{}')",
           (HOSTILE[1], HOSTILE[0] + " lighthouse"))
db.commit()
db.close()
index.build()
dashboard.build()
print(f"Demo home: {home}")
sys.exit(atlasserve.serve(open_browser=True))
