# Validation evidence

## Local boundary-test execution (2026-10-24 KST)

- Environment: Python 3.13.5; pytest 9.0.2; coverage 7.13.3.
- Source inspected: GitHub `main` commit `776ba8db3c2da97edeea6d36a427ec481b838da0`. A local focused mirror of four target modules was used because GitHub checkout was unavailable in the local container.
- Command: `coverage run --branch --source=tools.runner_lock,tools.manifest_parsing,tools.tool_policy,tools.runner_types -m pytest -q tests/test_runner_lock_boundaries.py tests/test_manifest_parsing_boundaries.py tests/test_tool_policy_boundaries.py tests/test_runner_types_boundaries.py tests/test_coverage_audit.py`
- Result: **87 passed**; **100% line/branch coverage of the four selected modules**, not the entire repository.
- Verified the six committed test/audit files against locally tested file contents using Git blob SHA.

## Whole-repository verification

- GitHub Actions first attempt failed during dependency installation (editable install not supported by this repo's flat package layout); corrected to install the declared dependencies directly.
- Second attempt executed **151 test cases: 147 passed, 4 failed**. Three existing `launchd` tests assume the CI checkout lives at the user's macOS `WorkingDirectory`; one existing test requires two historical log files under ignored `runs/`, neither tracked in repository history. No new test failed.
- These are **open blocking failures**. Do not mark the full suite green, remove expectations, invent a historical fixture, or auto-merge.
- See latest GitHub Actions job `Python tests and function coverage` for the full percentage and function inventory. Successful execution is required to claim whole-suite pass.

## Coverage definitions

- Percent from `coverage report`: executable statements and branches within `tools/`.
- Function inventory from `python scripts/coverage_audit.py`: function body with at least one executed statement; this is an execution indicator, not complete behavioral testing.
- Output: `coverage.json`, `function-coverage.json` and `pip-freeze.txt`, uploaded by CI.
