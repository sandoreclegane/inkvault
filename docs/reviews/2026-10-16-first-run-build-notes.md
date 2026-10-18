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

## Manual smoke (2026-10-16)

Run by the user on this PC (Windows 11) against a throwaway `--home` (model copied in, browser question answered
`none`), deleted afterwards. The live vault was not used.

| Console | Check | Result |
|---|---|---|
| Windows Terminal (Command Prompt tab) | hello, colour, bar, rows, Atlas opens | Pass: gold header, ✓ rows with counts, no stray glyphs, Atlas opened |
| Windows PowerShell 5.1 | same; glyphs and colour render | Pass |
| Legacy conhost (`conhost.exe`, cmd) | glyphs/VT or ASCII fallback | Pass: clean, Atlas opened |
| `NO_COLOR=1` in PowerShell 5.1 | no colour, stage lines once, layout unchanged | Pass |
| Piped / non-TTY (stdin and stdout redirected) | plain legacy output, no hello, no auto-open | Pass: legacy lines, 0 ESC, 0 CR, exit 0; also with `NO_COLOR` |

`--no-open` was not exercised by hand; it is covered by `tests/test_first_run.py`. In cmd,
`set UV_LINK_MODE=copy && ...` keeps the trailing space; use `set "UV_LINK_MODE=copy" && ...`.
