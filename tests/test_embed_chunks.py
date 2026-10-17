import os
from pathlib import Path

import numpy as np
import pytest

from inkvault import embed


class RowModel:
    """Each text is 'N'; its vector is [N, 1, 0, 0] so row identity survives normalising."""

    def encode(self, texts, **_):
        return np.array([[float(t), 1.0, 0.0, 0.0] for t in texts], dtype=np.float32)


class Recorder:
    def __init__(self):
        self.calls = []

    def stage(self, label):
        pass

    def update(self, done, total):
        self.calls.append((done, total))


def fake_search(monkeypatch, tmp_path, sizes):
    monkeypatch.setenv("INKVAULT_HOME", str(tmp_path))
    from inkvault import paths
    import sqlite3
    db = sqlite3.connect(paths.search_db())
    db.execute("CREATE TABLE events (id, window_title, readable)")
    db.execute("CREATE TABLE summaries (id, name, text)")
    db.execute("CREATE TABLE messages (id, conversation_name, text, role)")
    db.execute("CREATE TABLE snippets (id, name, language, text)")
    db.execute("CREATE TABLE pages (id, title, host, path)")
    n = 0
    for kind_rows, table in zip(sizes, ("events", "summaries")):
        for _ in range(kind_rows):
            if table == "events":
                db.execute("INSERT INTO events VALUES (?,?,?)", (f"e{n}", "", str(n)))
            else:
                db.execute("INSERT INTO summaries VALUES (?,?,?)", (f"s{n}", "", str(n)))
            n += 1
    db.commit()
    db.close()


@pytest.mark.parametrize("rows", [0, 1, 1023, 1024, 4097])
def test_chunked_alignment(monkeypatch, tmp_path, rows):
    fake_search(monkeypatch, tmp_path, [rows, 3])
    # the row vectors encode the row index in the text; recover it from the saved vectors
    monkeypatch.setattr(embed, "load_model", lambda progress=None: RowModel())
    rec = Recorder()
    embed.build(rec)
    from inkvault import paths
    z = np.load(paths.vectors())
    total = rows + 3
    assert len(z["ids"]) == total
    assert list(z["kinds"]) == ["events"] * rows + ["summaries"] * 3
    assert list(z["ids"]) == [f"e{i}" for i in range(rows)] + [f"s{i}" for i in range(rows, total)]
    old = RowModel().encode([str(i) for i in range(total)])  # the single-call path
    old /= np.linalg.norm(old, axis=1, keepdims=True) + 1e-9
    assert np.array_equal(z["vecs"], old.astype(np.float16))
    assert rec.calls[-1] == (total, total)
    assert [d for d, _ in rec.calls] == sorted(d for d, _ in rec.calls)


def model_dir():
    p = os.environ.get("INKVAULT_TEST_MODEL_DIR")
    return Path(p) if p else None


@pytest.mark.skipif(not model_dir() or not model_dir().exists(), reason="set INKVAULT_TEST_MODEL_DIR to a cached model")
def test_real_model_parity(monkeypatch, tmp_path):
    from model2vec import StaticModel
    model = StaticModel.from_pretrained(str(model_dir()))
    rng = np.random.default_rng(7)
    words = [f"w{int(x)}" for x in rng.integers(0, 5000, 3000)]
    lengths = rng.integers(1, embed.MAX_CHARS + 500, 6000)
    texts = [(" ".join(words[(i * 7 + j) % 3000] for j in range(int(n) // 5 + 1)))[:embed.MAX_CHARS]
             for i, n in enumerate(lengths)]
    old = model.encode(texts, batch_size=1024, show_progress_bar=False, use_multiprocessing=False)
    old /= np.linalg.norm(old, axis=1, keepdims=True) + 1e-9
    old = old.astype(np.float16)

    monkeypatch.setenv("INKVAULT_HOME", str(tmp_path))
    from inkvault import paths
    import sqlite3
    db = sqlite3.connect(paths.search_db())
    db.execute("CREATE TABLE events (id, window_title, readable)")
    db.executemany("INSERT INTO events VALUES (?,?,?)", ((f"e{i}", "", t) for i, t in enumerate(texts)))
    for ddl in ("summaries (id, name, text)", "messages (id, conversation_name, text, role)",
                "snippets (id, name, language, text)", "pages (id, title, host, path)"):
        db.execute(f"CREATE TABLE {ddl}")
    db.commit()
    db.close()
    monkeypatch.setattr(embed, "load_model", lambda: model)
    embed.build()
    z = np.load(paths.vectors())
    # build() prefixes each text with window_title + newline; encode the same strings the old way
    ref = [("\n" + t)[:embed.MAX_CHARS] for t in texts]
    exp = model.encode(ref, batch_size=1024, show_progress_bar=False, use_multiprocessing=False)
    exp /= np.linalg.norm(exp, axis=1, keepdims=True) + 1e-9
    assert np.array_equal(z["vecs"], exp.astype(np.float16))
    assert list(z["ids"]) == [f"e{i}" for i in range(len(texts))]
    assert set(z["kinds"]) == {"events"}
