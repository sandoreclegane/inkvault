"""The real-history check's own safety, on fictional data (Codex plan review #3): it only reads the backup it's
given, never changes it, and deletes its snapshot whatever fails."""
import importlib.util
import shutil
from pathlib import Path

import pytest

import atlasdata

SCRIPT = Path(__file__).parent / "manual" / "atlas_real_copy.py"


@pytest.fixture
def script():
    spec = importlib.util.spec_from_file_location("atlas_real_copy", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def backup(tmp_path, monkeypatch):
    """A fictional vault, saved the way the nightly run names its backups."""
    atlasdata.make_vault(tmp_path / "home", monkeypatch, vectors=False)
    from inkvault import paths
    out = tmp_path / "backups" / "vault-2026-02-28.db"
    out.parent.mkdir()
    shutil.copyfile(paths.vault_db(), out)
    monkeypatch.setenv("INKVAULT_HOME", str(tmp_path / "unused"))  # run() sets its own home; restored after the test
    monkeypatch.setenv("HF_HUB_OFFLINE", "0")
    return out


def roots(tmp_path):
    made = []

    def make():
        r = tmp_path / f"snapshot-{len(made)}"
        r.mkdir()
        made.append(r)
        return r
    return made, make


def test_a_run_reports_counts_and_deletes_its_snapshot(script, backup, tmp_path):
    before = backup.read_bytes()
    made, make = roots(tmp_path)
    report = script.run(backup, queries=["backoff"], make_root=make)
    assert report["snapshot_deleted"] is True and not made[0].exists()
    assert report["query_1"]["hits"] >= 1 and report["atlas_status"] in ("ok", "no-days")
    assert backup.read_bytes() == before  # the backup itself is never changed
    assert "backoff" not in str(report)    # queries are never echoed back


def test_a_failure_while_copying_the_model_leaves_nothing(script, backup, tmp_path, monkeypatch):
    model = tmp_path / "model"
    model.mkdir()
    (model / "weights").write_text("fictional")
    made, make = roots(tmp_path)

    def broken(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(script.shutil, "copytree", broken)
    with pytest.raises(OSError):
        script.run(backup, model=model, make_root=make)
    assert not made[0].exists()


def test_a_failure_while_building_leaves_nothing(script, backup, tmp_path, monkeypatch):
    from inkvault import index
    made, make = roots(tmp_path)
    monkeypatch.setattr(index, "build", lambda: (_ for _ in ()).throw(RuntimeError("index failed")))
    with pytest.raises(RuntimeError):
        script.run(backup, make_root=make)
    assert not made[0].exists()


def test_embedding_is_put_back_even_when_building_fails(script, backup, tmp_path, monkeypatch):  # Codex recheck
    from inkvault import embed, index
    before = embed.build
    made, make = roots(tmp_path)
    monkeypatch.setattr(index, "build", lambda: (_ for _ in ()).throw(RuntimeError("index failed")))
    with pytest.raises(RuntimeError):
        script.run(backup, make_root=make)  # no model: embedding is switched off for the run, then put back
    assert embed.build is before


def test_only_a_nightly_backup_is_accepted(script, tmp_path):
    for name in ("vault.db", "search.db", "local.db", "vault-2026-02-28.db.1234.partial"):
        f = tmp_path / name
        f.write_bytes(b"x")
        with pytest.raises(ValueError):
            script.run(f)


def test_the_snapshot_process_never_downloads(script, backup, tmp_path):
    made, make = roots(tmp_path)
    report = script.run(backup, make_root=make)  # no model given: keyword-only, nothing fetched
    import os
    assert os.environ["HF_HUB_OFFLINE"] == "1" and report["keyword_only_reason"] == "no model folder given"
