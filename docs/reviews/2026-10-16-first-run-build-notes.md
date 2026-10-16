# First-run sync: build notes

## Task 2 build gate: chunked meaning search

- Parity (`tests/test_embed_chunks.py::test_real_model_parity`, real potion-retrieval-32M, 6,000 synthetic texts spanning `MAX_CHARS`): PASS. float16 vectors, ids and kinds identical to one `encode` call per kind.
- Benchmark (one-off, 120,000 synthetic records, 5-400 words each, warm model, two runs each):
  - old, single `encode` per kind: 34.2 s, 34.5 s
  - chunked, `CHUNK = 4096`: 30.4 s, 30.7 s (about 11% faster, not slower)
  - outputs identical (`np.array_equal` true).
- Decision: chunking kept (parity held and cost is well under the 10% limit).
- With a `Progress` object, `index.build` / `embed.build` suppress their own summary prints (`indexed: ...`, `meaning search: ...`) so they cannot tear the live line; with no argument, output is unchanged byte for byte.

## Task 6 checks (0.3.3)

- Full suite: 833 passed, 4 skipped. `uvx ruff check --select F src tests`: All checks passed.
- `git diff --check`: clean. `uv build`: inkvault-0.3.3 sdist and wheel built.

## Manual smoke (pending)

Not run by the build agent (no interactive console). Run by hand with a throwaway `--home`, never the live vault.
Results are blank until checked.

| Console | Check | Result |
|---|---|---|
| Windows Terminal | hello, colour, spinner, rows, Atlas opens, `--no-open` skips | |
| PowerShell 5.1 | same; glyphs and colour render | |
| Legacy conhost (`conhost.exe powershell`) | ASCII fallback if glyphs/VT unavailable | |
| `NO_COLOR=1` | no colour codes; layout unchanged | |
| Piped (`sync \| more`) | plain legacy output, no hello, no auto-open | |
