"""Synthetic search and reader timings on a vault of about 100,000 records (spec §6). Fictional data only.

What it measures, honestly: retrieval timings with synthetic vectors and a fake model (not the real model's cold
start), and memory as the traced Python allocations each call adds above what was allocated just before it (`*_traced_increase_mb`;
not whole-process peak, and not the script's own data). It includes one very long conversation
(20,000 messages, one of them 5 MB) so the reader's bounded loading is exercised, not just many small chats.

    uv run python tests/manual/atlas_perf.py
"""
import json
import os
import platform
import random
import statistics
import tempfile
import time
import tracemalloc
from pathlib import Path

import numpy as np

home = Path(tempfile.mkdtemp(prefix="inkvault-perf-"))
os.environ["INKVAULT_HOME"] = str(home)
from inkvault import embed, export, index, paths, retrieval  # noqa: E402


class Model:
    def encode(self, texts, **_):
        return np.random.default_rng(len(texts[0])).random((len(texts), 256), dtype=np.float32)


embed.build = lambda: None
embed.load_model = Model
words = [f"w{i}" for i in range(5000)] + ["lighthouse", "harbor", "backoff"]
rnd = random.Random(7)
db = export.open_vault()
rows = []
for c in range(2000):
    rows.append(("conversation", f"c{c}", json.dumps({"id": f"c{c}", "name": " ".join(rnd.choices(words, k=4))})))
    for m in range(40):
        rows.append(("message", f"m{c}_{m}", json.dumps({"id": f"m{c}_{m}", "created": {"value": f"2026-0{1 + c % 9}-{1 + m % 28:02d}T10:00:00Z"},
                     "role": "USER" if m % 2 == 0 else "ASSISTANT", "conversation": {"id": f"c{c}"},
                     "fragment": {"string": {"raw": " ".join(rnd.choices(words, k=60))}}})))
for s in range(20000):
    rows.append(("summary", f"s{s}", json.dumps({"id": f"s{s}", "name": " ".join(rnd.choices(words, k=5)),
                 "created": {"value": f"2026-0{1 + s % 9}-{1 + s % 28:02d}T12:00:00Z"}})))
rows.append(("conversation", "long", json.dumps({"id": "long", "name": "one very long conversation"})))
for m in range(20000):
    text = ("lighthouse " * 500_000) if m == 10000 else " ".join(rnd.choices(words, k=60))  # one 5 MB message
    rows.append(("message", f"long_{m:05d}", json.dumps({"id": f"long_{m:05d}", "created": {"value": f"2026-09-01T{m // 3600:02d}:{m // 60 % 60:02d}:{m % 60:02d}Z"},
                 "role": "USER" if m % 2 == 0 else "ASSISTANT", "conversation": {"id": "long"},
                 "fragment": {"string": {"raw": text}}})))
db.executemany("INSERT INTO raw_records VALUES (?,?,?)", [(k, i, r) for k, i, r in rows])
db.commit(); db.close()
index.build()
db = paths.connect_ro(paths.search_db())
ids = [("chats", r[0]) for r in db.execute("SELECT id FROM messages")] + [("summaries", r[0]) for r in db.execute("SELECT id FROM summaries")]
db.close()
np.savez(paths.vectors(), kinds=np.array([k for k, _ in ids]), ids=np.array([i for _, i in ids]),
         vecs=np.random.default_rng(1).random((len(ids), 256), dtype=np.float32))
paths.model_dir().mkdir(exist_ok=True)

def timed(fn):
    t = time.perf_counter(); out = fn(); return time.perf_counter() - t, out

def traced(fn):
    """(seconds, answer, increase in MB): the traced Python allocations the call itself added at its peak, above what
    was already allocated just before it (the script's own in-memory rows are baseline, not the call's)."""
    base = tracemalloc.get_traced_memory()[0]
    tracemalloc.reset_peak()
    seconds, out = timed(fn)
    return seconds, out, (tracemalloc.get_traced_memory()[1] - base) / 1e6

tracemalloc.start()
search = lambda: retrieval.search("lighthouse harbor", markers=retrieval.ATLAS_MARKERS, require_model=True, limit=25)
cold, r, cold_mb = traced(search)
warms = [traced(search) for _ in range(5)]
warm, warm_mb = statistics.median(w[0] for w in warms), max(w[2] for w in warms)
reader, rec, reader_mb = traced(lambda: retrieval.record("chats", "m1000_20"))
long_reader, long_rec, long_mb = traced(lambda: retrieval.record("chats", "long_10000"))  # the 5 MB message, mid-conversation
print(json.dumps({"records": len(ids), "hardware": platform.processor() or platform.machine(), "python": platform.python_version(),
                  "synthetic_cold_search_s": round(cold, 3), "synthetic_warm_search_s": round(warm, 3),
                  "synthetic_cold_search_traced_increase_mb": round(cold_mb, 1),
                  "synthetic_warm_search_traced_increase_mb": round(warm_mb, 1),
                  "reader_s": round(reader, 3), "reader_chars": sum(len(i["text"]) for i in rec["items"]),
                  "reader_traced_increase_mb": round(reader_mb, 1),
                  "long_conversation_reader_s": round(long_reader, 3),
                  "long_conversation_reader_chars": sum(len(i["text"]) for i in long_rec["items"]),
                  "long_conversation_reader_traced_increase_mb": round(long_mb, 1),
                  "hits": len(r.hits)}, indent=1))
print(f"(temporary folder {home}; delete it when done)")
