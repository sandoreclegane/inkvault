"""Run the Atlas against a disposable snapshot made from one nightly backup (spec §6). Prints counts and timings only.

It reads exactly what it's given: one finished nightly backup (backups/vault-YYYY-MM-DD.db) and, optionally, the model
folder. Both are copied as plain files; no live vault, search file, lock or SQLite sidecar is opened, and no other
source (browser profiles, other tools' data) is read. Search, vectors and the Atlas data are built inside the snapshot.
The snapshot is deleted whatever happens.

    uv run python tests/manual/atlas_real_copy.py <backups/vault-YYYY-MM-DD.db> [<model folder>]
Queries are asked for one at a time without echo (an empty line finishes); they're never printed, stored or put on
the command line, so they don't reach shell history or process listings.
"""
import getpass
import json
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

BACKUP_NAME = re.compile(r"vault-\d{4}-\d{2}-\d{2}\.db")


def snapshot_root():
    return Path(tempfile.mkdtemp(prefix="inkvault-snapshot-"))


def check(root, queries, have_model):
    """Build search and the Atlas inside the snapshot, then measure. Without a model, embedding is switched off for
    this call only and always put back, even when building fails (Codex plan recheck): this runs inside pytest too."""
    from inkvault import embed
    real_build = embed.build
    if not have_model:
        embed.build = lambda: None  # keyword-only: never fetch a model
    try:
        return measure(queries, have_model)
    finally:
        embed.build = real_build


def measure(queries, have_model):
    """Counts and timings only."""
    from inkvault import atlasserve, dashboard, index, retrieval
    report = {}
    t = time.perf_counter(); index.build(); report["index_s"] = round(time.perf_counter() - t, 1)
    dashboard.build()
    t = time.perf_counter(); payload = atlasserve.atlas_payload(); report["atlas_s"] = round(time.perf_counter() - t, 3)
    report["atlas_status"] = payload["status"]
    report["atlas_days"] = len((payload.get("data") or {}).get("days", {}))
    report["chips"] = payload.get("sources")
    if not have_model:
        report["keyword_only_reason"] = "no model folder given"
    for n, q in enumerate(queries, 1):
        t = time.perf_counter()
        r = retrieval.search(q, markers=retrieval.ATLAS_MARKERS, require_model=True, limit=25)
        took = time.perf_counter() - t
        kinds = {}
        for h in r.hits:
            kinds[h.kind] = kinds.get(h.kind, 0) + 1
        opened = None
        if r.hits:
            t = time.perf_counter()
            retrieval.record(r.hits[0].kind, r.hits[0].id)
            opened = round(time.perf_counter() - t, 3)
        report[f"query_{n}"] = {"search_s": round(took, 3), "hits": len(r.hits), "by_kind": kinds,
                                "keyword_only": r.keyword_only, "first_open_s": opened}
    return report


def run(backup, model=None, queries=(), make_root=snapshot_root):
    backup = Path(backup)
    if not BACKUP_NAME.fullmatch(backup.name) or not backup.is_file():
        raise ValueError("Give one nightly backup file named vault-YYYY-MM-DD.db.")
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
    root = make_root()
    report = {}
    try:  # one try for creating, using and deleting the snapshot: a failure anywhere leaves nothing behind
        os.environ["INKVAULT_HOME"] = str(root)
        shutil.copyfile(backup, root / "vault.db")
        if model:
            shutil.copytree(model, root / "model")
        report = check(root, list(queries), have_model=bool(model))
    finally:
        shutil.rmtree(root, ignore_errors=True)
        report["snapshot_deleted"] = not root.exists()
    return report


def ask_queries():
    out = []
    while q := getpass.getpass(f"Query {len(out) + 1} (empty to finish): ").strip():
        out.append(q)
    return out


if __name__ == "__main__":
    if not 2 <= len(sys.argv) <= 3:
        sys.exit(__doc__)
    result = run(sys.argv[1], sys.argv[2] if len(sys.argv) == 3 else None, ask_queries())
    print(json.dumps(result, indent=1))
    if not result["snapshot_deleted"]:
        sys.exit("The snapshot folder couldn't be deleted; remove it by hand (it holds a copy of your vault).")
