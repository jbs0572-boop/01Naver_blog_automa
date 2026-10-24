# Validation evidence (2026-10-24 KST)

## Inputs and environment

- Source baseline: GitHub `main` `776ba8db3c2da97edeea6d36a427ec481b838da0`.
- Tested work branch: `test/coverage-audit-20261024`. Existing `main` and Mac `launchd` deployment remain unchanged.
- GitHub Actions: Ubuntu, Python 3.13, `pytest`, `jsonschema`, `hypothesis`, and `coverage.py` installed explicitly. The installed dependency versions are preserved as `pip-freeze.txt` with the CI coverage reports.
- CLI: `python -m coverage run --branch --source=tools -m pytest -q` then `coverage report -m`, `coverage json`, `python scripts/coverage_audit.py`.
- All newly committed test files matched locally inspected and tested Git blob SHAs. Some local target-focused tests used exact or deliberately reduced module mirrors for isolated tests; only GitHub Actions runs used the full actual repository.

## Measured full-repo progress (history retained)

| Evidence | Passed | Failed | Statement/branch coverage | Functions with executed body |
| --- | ---: | ---: | ---: | ---: |
| [CI baseline run 39998129150](https://github.com/jbs0572-boop/01Naver_blog_automa/actions/runs/39998129150) | 147 | 4 | 70.43% | 79/97 |
| [CI run 39999816405](https://github.com/jbs0572-boop/01Naver_blog_automa/actions/runs/39999816405) | 204 | 1 | 84.84% | 96/97 (lambdas omitted) |
| [CI run 40001988568](https://github.com/jbs0572-boop/01Naver_blog_automa/actions/runs/40001988568) | 207 | 1 | 84.84% | 96/97 (lambdas omitted) |
| [PR #2 latest audited run 40084432799](https://github.com/jbs0572-boop/01Naver_blog_automa/actions/runs/40084432799) | **210** | **1** | **84.89%** | **96/100** (3 lambda bodies ambiguous) |

- The latest inventory is deliberately conservative. Three real lambda bodies at `tools/gate.py:66`, `tools/manifest.py:112` and `tools/runner_execution.py:83` cannot be proven to have run by line-level traces, and one Protocol declaration (`_ValidatorProtocol.iter_errors`) has no executable implementation. The earlier **96/97** inventory omitted lambdas; the corrected number is **96/100**. Do not count import-time one-line declarations as function calls.
- Full branch/line coverage remains below 100%; the function-body measurement does not imply all branches have meaningful assertions.
- Focused local unit tests of the runner lock, manifest parsing, tool policy, runner types: **87 passed**; each target module measured **100%** statement/branch coverage.
- Additional focused local tests: `runner_cli` **19 passed**, **100%** target coverage; `workflow_verifier` **21 passed**, **97%** target coverage; public serializers/error classifiers **14 passed**. These were also included in GitHub's complete run.

## Remaining failure: original historical run logs not in source control

`tests/test_log_contract.py::test_historical_logs_remain_readable` needs actual historical files, excluded by `.gitignore`:

- `runs/beta-2026-08-26-energy-day.jsonl`: expected 8 events
- `runs/beta-gate0-2026-08-26-9topics.jsonl`: expected 66 events

Neither file appears in repository history. The missing files were **not synthesized, bypassed or marked as a pass**. Without authentic fixtures, that test cannot be verified in GitHub CI, and the PR must **remain unmerged**. The previously failing three `launchd` tests were repaired by checking Mac installation-path consistency instead of falsely equating Mac paths with a Linux checkout; all 3 now pass.

## Operational status and scoring

- Added CI measures/test inventory/artifacts: **implemented and executed**.
- Existing tests except missing historic fixtures: **passing**.
- Whole suite: **FAIL — 210 passed, 1 blocked on missing authentic historical input** (PR #2, head `37fe0a685c99`, run `40084432799`).
- Quality score: **unassigned**, because the mandatory full-suite condition is unmet. No claimed 91/100 pass.
- Official Codex code review: **completed for PR #2 head `37fe0a685c99`, no current findings**; three historical threads were resolved only after code fixes, regression tests, and CI confirmation. This is not a security-review approval.
- External Notion writes, Naver edits, posting, scheduling and production deployment: **not attempted**.

## Next action

Restore authentic historical run logs into CI-provisioned test fixtures via a reviewed, non-secret mechanism, rerun full suite, review coverage JSON and Codex findings, then consider approval/merge. Do not fabricate history or disable assertions for a green badge.

## Audit correction and CI artifact (2026-10-24)

PR #2 commit [37fe0a685c99](https://github.com/jbs0572-boop/01Naver_blog_automa/commit/37fe0a685c9938b994f34b124dd19f050fc64502) makes cleanup failures observable instead of leaving a live-PID lock silently orphaned, adds tests for same-line function declarations, and enumerates all lambdas conservatively. The latest raw CI evidence is [python-quality-evidence artifact 12556582066](https://github.com/jbs0572-boop/01Naver_blog_automa/actions/runs/40084432799/artifacts/12556582066). The only test failure remains the original missing logs; no history was fabricated or tests skipped.
