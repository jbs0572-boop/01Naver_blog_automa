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
