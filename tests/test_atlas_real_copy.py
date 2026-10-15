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


def test_a_symlinked_backup_is_rejected(script, backup, tmp_path):
    link = tmp_path / "links" / "vault-2026-02-28.db"
    link.parent.mkdir()
    try:
        link.symlink_to(backup)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks can't be created here")
    with pytest.raises(ValueError):
        script.run(link)


def test_no_snapshot_path_reaches_the_output(script, backup, tmp_path, capsys):
    made, make = roots(tmp_path)
    report = script.run(backup, queries=["backoff"], make_root=make)
    out = capsys.readouterr()
    assert str(made[0]) not in out.out + out.err and str(made[0]) not in str(report)


def test_a_failing_run_prints_only_the_kind_of_error(script, backup, monkeypatch, capsys, tmp_path):
    secret = str(tmp_path / "secret-path")

    def boom(*a, **k):
        raise OSError(f"cannot open {secret}")
    monkeypatch.setattr(script, "run", boom)
    monkeypatch.setattr(script, "ask_queries", lambda: ["q"])
    assert script.main(["x", str(backup)]) == 1
    out = capsys.readouterr()
    assert "OSError" in out.out and "secret-path" not in out.out + out.err


def _main_with_failing_check(script, backup, tmp_path, monkeypatch, keep_folder):
    from inkvault import index
    made, make = roots(tmp_path)
    monkeypatch.setattr(script, "snapshot_root", make)
    monkeypatch.setattr(script.run, "__defaults__", (None, (), make))  # make_root default is bound at definition
    monkeypatch.setattr(index, "build", lambda: (_ for _ in ()).throw(RuntimeError("secret-query-text")))
    monkeypatch.setattr(script, "ask_queries", lambda: ["q"])
    if keep_folder:
        monkeypatch.setattr(script.shutil, "rmtree", lambda *a, **k: None)  # cleanup fails: the folder stays
    return made, script.main(["x", str(backup)])


def test_a_failed_check_with_a_failed_cleanup_says_not_deleted_and_names_the_folder(script, backup, tmp_path,
                                                                                     monkeypatch, capsys):
    made, code = _main_with_failing_check(script, backup, tmp_path, monkeypatch, keep_folder=True)
    out = capsys.readouterr().out
    assert code == 1 and made[0].exists()
    assert "NOT deleted" in out and str(made[0]) in out and "was deleted" not in out
    assert "secret-query-text" not in out and "RuntimeError" in out


def test_a_failed_check_with_a_working_cleanup_says_deleted(script, backup, tmp_path, monkeypatch, capsys):
    made, code = _main_with_failing_check(script, backup, tmp_path, monkeypatch, keep_folder=False)
    out = capsys.readouterr().out
    assert code == 1 and not made[0].exists()
    assert "The snapshot was deleted." in out and "NOT" not in out and str(made[0]) not in out


def test_a_good_check_with_a_failed_cleanup_names_the_folder(script, backup, tmp_path, monkeypatch, capsys):
    made, make = roots(tmp_path)
    monkeypatch.setattr(script.run, "__defaults__", (None, (), make))
    monkeypatch.setattr(script, "ask_queries", lambda: [])
    monkeypatch.setattr(script.shutil, "rmtree", lambda *a, **k: None)
    result = script.main(["x", str(backup)])
    assert isinstance(result, str) and "NOT deleted" in result and str(made[0]) in result and made[0].exists()


def test_bad_input_raises_before_anything_is_created(script, tmp_path):
    made, make = roots(tmp_path)
    f = tmp_path / "vault.db"
    f.write_bytes(b"x")
    with pytest.raises(ValueError):
        script.run(f, make_root=make)
    assert made == []


def test_bad_input_through_main_prints_no_snapshot_warning(script, tmp_path, monkeypatch, capsys):
    f = tmp_path / "vault.db"
    f.write_bytes(b"x")
    monkeypatch.setattr(script, "ask_queries", lambda: [])
    assert script.main(["x", str(f)]) == 1
    out = capsys.readouterr().out
    assert "ValueError" in out and "NOT deleted" not in out and "was deleted" not in out
