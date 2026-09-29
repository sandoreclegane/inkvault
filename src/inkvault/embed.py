"""Meaning-search vectors for everything in search.db, with a small static embedding model.

potion-retrieval-32M runs fast on CPU (minutes for ~100k records) and needs no GPU. It is
downloaded once from Hugging Face into the InkVault home; after that, nothing needs the network.
"""
import sqlite3
import time

import numpy as np

from . import paths

MODEL = "minishlab/potion-retrieval-32M"
MAX_CHARS = 4000  # static models average token vectors; past this, extra text mostly adds noise

QUERIES = {
    "events": "SELECT id, coalesce(window_title,'') || char(10) || coalesce(readable,'') FROM events",
    "summaries": "SELECT id, coalesce(name,'') || char(10) || coalesce(text,'') FROM summaries",
    "chats": "SELECT id, coalesce(conversation_name,'') || char(10) || coalesce(text,'') FROM messages "
             "WHERE role IN ('USER','ASSISTANT') AND length(text) > 0",
    "snippets": "SELECT id, name || char(10) || language || char(10) || text FROM snippets",
}


def load_model():
    from model2vec import StaticModel
    local = paths.model_dir()
    if not local.exists():
        print("downloading the search model (once, ~130 MB)…", flush=True)
        StaticModel.from_pretrained(MODEL).save_pretrained(str(local))
    return StaticModel.from_pretrained(str(local))


def build():
    start = time.time()
    db = sqlite3.connect(f"file:{paths.search_db()}?mode=ro", uri=True)
    model = load_model()
    kinds, ids, vecs = [], [], []
    for kind, sql in QUERIES.items():
        rows = [(i, t[:MAX_CHARS]) for i, t in db.execute(sql) if t and t.strip()]
        if not rows:
            continue
        v = model.encode([t for _, t in rows], batch_size=1024, show_progress_bar=False)
        v /= np.linalg.norm(v, axis=1, keepdims=True) + 1e-9
        vecs.append(v.astype(np.float16))
        kinds += [kind] * len(rows)
        ids += [i for i, _ in rows]
    db.close()
    if not vecs:
        print("nothing to embed yet")
        return
    tmp = paths.vectors().with_name("vectors.tmp.npz")
    np.savez(tmp, kinds=np.array(kinds), ids=np.array(ids), vecs=np.vstack(vecs))
    tmp.replace(paths.vectors())
    print(f"meaning search: {len(ids):,} records in {time.time() - start:.0f}s", flush=True)
