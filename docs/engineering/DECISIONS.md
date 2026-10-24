# Engineering decision log

## D-001: Preserve existing pipeline; test on feature branch
- Date: 2026-10-24 KST
- Decision: Branch from `main` to `test/coverage-audit-20261024`; do not alter runtime workflow or merge automatically.
- Reason: User requires preservation of existing behavior and only passing code to be committed.
- Alternatives: Direct main commits (rejected); refactor while adding tests (rejected).
- Revisit if: A reproducible failing test requires a minimal runtime correction.

## D-002: Measure both branch/line and function execution
- Date: 2026-10-24 KST
- Decision: Collect real coverage.py traces across `tools/` and audit each function body from the AST. Keep the two measures separate.
- Reason: Source-level function names and line coverage alone do not identify never-invoked code reliably.
- Limitation: Entering a function is weaker than full behavioral/branch validation; closures and decorators need careful review.

## D-003: Read-only CI; Codex reviewer via official integration
- Date: 2026-10-24 KST
- Decision: Pull request CI has `contents: read`, no secrets, no write-back or auto-merge. Describe review criteria in AGENTS rather than embedding an authenticated code-fixing bot in CI.
- Reason: Avoid running contributed code with elevated GitHub or API credentials.
- External prerequisite: Codex Code Review must be enabled at the connected account/repository level. Repository text alone cannot prove activation.
