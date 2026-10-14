"""Run the Atlas against a disposable snapshot made from one nightly backup (spec §6). Prints counts and timings only.

It reads exactly what it's given: one finished nightly backup (backups/vault-YYYY-MM-DD.db) and, optionally, the model
folder. Both are copied as plain files; no live vault, search file, lock or SQLite sidecar is opened, and no other
source (browser profiles, other tools' data) is read. Search, vectors and the Atlas data are built inside the snapshot.
The snapshot is deleted whatever happens, and the real outcome (deleted or not) is reported.

    uv run python tests/manual/atlas_real_copy.py <backups/vault-YYYY-MM-DD.db> [<model folder>]
Queries are asked for one at a time without echo (an empty line finishes); they're never printed, stored or put on
the command line, so they don't reach shell history or process listings.
"""
import contextlib
import getpass
import io
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
        with contextlib.redirect_stdout(io.StringIO()):  # index and dashboard print the snapshot's path: keep it out
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
    if backup.is_symlink():
        raise ValueError("Give the backup file itself, not a link to it.")
    if not BACKUP_NAME.fullmatch(backup.name) or not backup.is_file():
        raise ValueError("Give one nightly backup file named vault-YYYY-MM-DD.db.")
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
    root = make_root()
    try:  # one try for creating and using the snapshot; it is deleted on both paths and the outcome is checked
        os.environ["INKVAULT_HOME"] = str(root)
        shutil.copyfile(backup, root / "vault.db")
        if model:
            shutil.copytree(model, root / "model")
        report = check(root, list(queries), have_model=bool(model))
    except BaseException as e:
        e.snapshot_deleted = delete_snapshot(root)  # main() reports the real outcome, never an assumed one
        e.snapshot_folder = root
        raise
    report["snapshot_deleted"] = delete_snapshot(root)
    report["snapshot_folder"] = None if report["snapshot_deleted"] else str(root)
    return report


def delete_snapshot(root):
    """Delete the snapshot and say whether it is really gone (a failed delete is not an error here)."""
    shutil.rmtree(root, ignore_errors=True)
    return not root.exists()


def ask_queries():
    out = []
    while q := getpass.getpass(f"Query {len(out) + 1} (empty to finish): ").strip():
        out.append(q)
    return out


def main(argv):
    if not 2 <= len(argv) <= 3:
        return __doc__
    try:
        result = run(argv[1], argv[2] if len(argv) == 3 else None, ask_queries())
    except Exception as e:  # only the kind of failure is printed: a message could carry a path or a query
        print(f"The check failed ({type(e).__name__}).")
        folder = getattr(e, "snapshot_folder", None)  # absent when the error came before any snapshot was made
        if folder is not None:
            if getattr(e, "snapshot_deleted", False) and not Path(folder).exists():
                print("The snapshot was deleted.")
            else:
                print(f"WARNING: The snapshot was NOT deleted; it holds a copy of your vault. "
                      f"Remove this folder by hand: {folder}")
        return 1
    folder = result.pop("snapshot_folder", None)
    print(json.dumps(result, indent=1))
    if not result["snapshot_deleted"]:
        return f"The snapshot was NOT deleted; it holds a copy of your vault. Remove this folder by hand: {folder}"
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
