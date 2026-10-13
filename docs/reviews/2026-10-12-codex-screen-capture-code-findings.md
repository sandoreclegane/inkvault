# Screen capture: final review and interrupted-fix handoff

Review date: 2026-10-12. Public base: `v0.2.1`. Branch: `screen-capture`, version `0.3.0`.

The interrupted final fix wave is complete. Part 1 was already committed as `308a456`; its changes and the
unfinished work were inspected before continuing. The final code fixes are committed as `0a94963`. A fresh independent reviewer examined the whole branch in
passes over capture/privacy, persistence/removal, readers, and Windows controls. No unresolved Critical or
Important finding remains. Publication and operational installation are separate steps.

## Final-fix checklist

| Item | Completed behavior | Covering tests |
|---|---|---|
| 1 | Reject `forget --screen --app ... --until ...` before preview or deletion. | `test_until_without_since_is_rejected_and_removes_nothing` |
| 2 | The real command reaches `run()`'s recovery path; install reports preparation failures. | Entry-point and install failure cases in `test_watch_cli.py`; real startup recovery test below |
| 3 | Task read-back accepts the current account name without case sensitivity or its current SID; other fields remain exact. | SID/case/different-user cases in `test_watch_cli.py`; native SID test in `test_winapi.py` |
| 4 | Spinner/timer titles extend one stretch, while real title/process changes separate stretches; worker requests hash the current raw title. | Normalization cases in `test_stretches.py`; watcher request/acceptance cases in `test_watch.py` |
| 5 | Bounded atomic-replace retries, useful settings-write errors, and pause/resume outcomes under `pythonw`. | Replace tests in `test_screen_settings.py`; notification and write-failure cases in `test_watch_cli.py` |
| 6 | Redact Stripe, Google, GitLab, Hugging Face and npm token shapes. | Secret/harmless cases in `test_redact.py` |
| 7 | Remove a stale stop request after acquiring the watcher lock. | Stale stop-file cases in `test_watch_cli.py` |
| 8 | Reject non-object/missing-list settings before invoking an edit callback. | `test_edit_checks_the_shape_before_changing_anything` |
| 9 | Stretch extensions do not move backward when the wall clock does. | `test_ended_never_goes_backwards_when_the_clock_steps_back` |
| 10 | Log database errors on a change, rather than every poll. | Database-error logging test in `test_watch.py` |
| 11 | Screen preview returns an empty result for pre-screen vaults; removal-kind schema comment is current. | `test_preview_of_a_pre_screen_vault_is_empty` |
| 12 | View day-key tests use the same Chicago zone as their duration fixtures. | Mixed-day, dashboard and digest cases in `test_screentime.py` |
| 13 | Scheduler read-back uses an escaped BOM pattern. | BOM read-back case in `test_schedulers.py` |
| 14 | Reset reports busy/write failures without altering the settings; shortcuts escape apostrophes and report launch errors; absent-task timeout does not claim disabling. | Six reproduced regressions in `test_watch_cli.py` |
| 15 | README distinguishes midnight `--since` from whole-day `--until`, and documents valid flag combinations. | Documentation checked against the bounds implementation and CLI regressions |

## Findings from the fresh final review

### Important: current-title exclusions after normalization — fixed

`Watcher.collect()` compared normalized title identities but tested never-title rules against the earlier raw
title only. A newly excluded current title could therefore accept the earlier OCR response. Acceptance now
checks both submitted and current raw titles against never-capture rules.

Evidence: `test_acceptance_rechecks_never_rules_on_the_current_raw_title` failed before the fix and passed after
it. The test uses the real watcher, real settings and real disposable database; it asserts no text was stored.

### Important: first-use initialization after transient startup failure — fixed

After the first `prepare()` failed, `open_store()` could recover the database without creating first-use settings.
The process then stayed closed indefinitely. Recovery now retries first-use initialization and closes a
connection whose initialization fails. An initialized home with lost settings still requires explicit repair.

Evidence: the real-entry-point regression and three transient initialization cases failed before the fix; the
lost-settings protection passed throughout. All five pass after the fix. An independent bounded `run()` probe
changed from zero valid settings ticks to 3/3 valid ticks, with a settings file and initialization marker.

## Validation

- Required synthetic gate: **82 passed**. Includes two-connection removal races, fake-worker supervision,
  index snapshot contention, watcher integration, typed native Windows APIs, GDI handle checks, job death,
  DPI and the OCR round trip. OCR was available on this machine.
- Final-fix component suite: **130 passed**.
- Watcher/control/integration suite after the review fixes: **78 passed**.
- Focused fresh-review regressions: **6 passed**, independently repeated by the reviewer.
- Final complete suite: **603 passed, 1 skipped in 127.82 seconds** (`python -m pytest -q -rs --tb=no` in the existing build venv).
- Python 3.10 syntax: **64 source/test files passed** using `ast.parse(feature_version=(3, 10))`.
- `git diff --check`: passed.
- Private privacy-check self-test: passed. Final committed-branch check: **clean (exit 0)**.

Tests used disposable homes, synthetic data and test-owned windows. Existing guards prevented real scheduler,
installer and non-Python subprocess calls. No live vault or real browser profile was inspected or modified.

## Rulings made during this handoff

- Preserve the existing title-normalization tradeoff: differing numeric/edge-symbol titles may share a stretch.
  The new raw-title privacy check is independent of that tradeoff. Cost if wrong: distinct work may be grouped.
- Retry initialization only for a home without its permanent initialization marker. Cost if wrong: a genuinely
  lost settings file still needs the explicit reset or restoration already required by the design.
- Preserve the interrupted planning workspace and existing worktree for the maintainer's handoff. Cost if wrong:
  local scratch files remain until intentionally cleaned up.

## Deferred minors

- A hand-edited list containing a non-string member can still make an app-edit command raise `AttributeError`.
  The watcher rejects those malformed settings and records nothing. This is recovery UX, not capture permission;
  validating members before the callback would improve it.
- Retain the earlier review's oversized-OCR-response and uncounted-stale-drop deferrals. No new important harm
  was established; a large response can cause a bounded worker restart.
- Earlier per-task test-coverage and polish deferrals remain recorded exhaustively in
  `2026-10-12-screen-build-ledger.md`. The final fix checklist above identifies the ones resolved in this wave.

## Declined to judge

- Actual scheduled-task registration, sign-in startup and desktop-shortcut execution: excluded from this review;
  mocked tests do not establish those operational outcomes.
- Day-long CPU use, storage growth and capture quality across real apps: require the separately planned live trial.
- Existing native failure-path/downscaling coverage gaps: no new concrete defect justified reclassification.

The independent review verdict is ready from the code perspective once the final guarded suite passes. The
suite and privacy result above are the completion evidence. Task 20 still requires explicit maintainer approval
before pushing the branch and opening the public pull request.


## Publication fixture cleanup

GitHub push protection flagged the deliberately synthetic Slack token in the redaction fixture and the plan's
test example. The unpublished branch history now generates a clearly synthetic token from repeated characters,
retaining a realistic token shape and the same redaction assertions. No secret-scanning exception was requested.
Review commit references were updated to the rewritten history. Production code is unchanged by this cleanup.

Post-cleanup verification: 24 redaction tests passed; production source matches the pre-cleanup branch exactly.
