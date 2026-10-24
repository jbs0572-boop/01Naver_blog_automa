# Project specification (2026-10-24 KST)

## Goal and users

Operate a Codex-first Naver Blog workflow with evidence-based topic selection, research, writing, image validation, Notion storage and Naver draft entry. The human interacts with one manager. Existing stage order and approval gates in `AGENTS.md` and `schemas/workflow-contract.schema.json` remain authoritative.

## Scope of this change

Establish read-only, repeatable Python tests and function-level coverage evidence; target missing boundary and error-handling tests before changing production behavior. Build an audit of functions that never executed during the test suite.

## Non-goals and constraints

No blog publication, Notion writes, Naver editor actions, secret/config changes, auto-merge, or production deployment. Preserve `main`, existing tests and generated business artifacts. Tests cannot grant external-write approval. GitHub Actions runs on untrusted PR code without secrets or repository write permission.

## Success criteria

1. Existing and new Python tests pass on Python 3.13 in GitHub Actions.
2. Coverage branch/line percentage and the names and source locations of all never-executed functions are saved as CI artifacts.
3. Newly added boundary tests pass locally before commit and pass in the complete GitHub suite.
4. No critical regression/security issue; unresolved coverage gaps remain explicitly open until independently tested.

## Work contract and limits

Implementation: add test cases and coverage tool only; do not modify core runtime behavior. Verification: separately inspect test failure output, exact Git blobs, whole-repository Actions results, and coverage JSON. Evidence: test logs, coverage JSON, function inventory, commit SHA and branch/PR link. Dependency: CI requires the new tests and audit script. Limit: three corrective iterations per task; no paid API, credentials, or live external writes. A single AI execution is **not** represented as independent worker runs.
