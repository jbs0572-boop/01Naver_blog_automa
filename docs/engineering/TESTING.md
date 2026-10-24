# Test and review runbook

## Commands (repo root)

Install Python 3.13 and the declared requirements (without editable-installing the flat-layout repository):

```sh
python -m pip install 'jsonschema[format-nongpl]>=4.26,<5' 'pytest>=9,<10' 'hypothesis>=6.165,<7' 'coverage>=7,<8'
python -m coverage run --branch --source=tools -m pytest -q
python -m coverage report -m
python -m coverage json -o coverage.json
python scripts/coverage_audit.py --coverage-json coverage.json --out function-coverage.json
```

The final JSON enumerates every Python function in `tools/` and whether its body was executed by the suite. For the full strict gate after closing all gaps, add `--fail-untested`; until then unresolved gaps must be reported rather than hidden. New tests must assert real behavior and error cases, not merely call a function. Do not change the assertion to make a regression disappear.

## PR review requirements

Check severity, file/line, reproduction condition, impact, and fix guidance for each valid finding. Prioritize data loss, approval bypass, unsafe input, concurrency, path traversal, schema drift and old workflow regressions. Verify claims with a test or a direct trace. Only merge after the full CI and required review pass. Codex auto-review requires an official account/repo setting; this repository must not add credentials to PR CI to simulate it.

## Historical log availability (read-only diagnosis)

Run `python -m scripts.historical_log_preflight` before an acceptance run to
report **both** required August 26 logs, expected record counts (8 and 66),
readability, and SHA-256 of any supplied bytes. Exit 1 means a missing, invalid,
wrong-count, substituted, or changing input. Exit 0 means only that the inputs
are readable with the expected counts. It never establishes historical
provenance, never replaces the full test suite, and never grants approval.

The unchanged `test_historical_logs_remain_readable` is an archival compatibility
regression gate, not a synthetic unit fixture. The `runs/` ignore rule means a
fresh clone does not contain these operational records. Restore only authentic
originals from the original execution workspace or a verified backup. Keep the
source, recovery time, original digest if available, and recovered digest in a
review record. A newly computed hash alone does not prove authenticity. Review
personal/operational content before deciding whether any recovered bytes may be
tracked in Git. Do not fabricate 8/66 events, delete the assertions, skip/xfail
the test, or report a filtered test run as full acceptance.

The new diagnostic tests use synthetic temporary directories solely to test the
diagnostic, explicitly report `historical_authenticity=not_established`, and do
not create or modify the repository's historical `runs/` inputs. After authentic
restoration, rerun every original and new test with coverage on Python 3.13.
