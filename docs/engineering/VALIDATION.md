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

## Stacked PR #3 v2 implementation evidence (2026-10-24 KST)

- PR #3 target: `rebuild/v2-evidence-pipeline-20261024` **based on PR #2**, not `main`. Reviewed implementation at `00b679bdb51b21e24fe6333c74ce1c472a6b4b1b`.
- [Full-repo CI run 40084912963](https://github.com/jbs0572-boop/01Naver_blog_automa/actions/runs/40084912963): **288 passed / 1 failed**, aggregate line/branch coverage **86.21%**, **120/124** verifiably exercised function bodies; three lambdas are ambiguous in line-only measurement and one Protocol declaration is not executable. Artifact [12556941509](https://github.com/jbs0572-boop/01Naver_blog_automa/actions/runs/40084912963/artifacts/12556941509).
- [Isolated v2 preview CI run 40084912987](https://github.com/jbs0572-boop/01Naver_blog_automa/actions/runs/40084912987): **81 passed**, offline HTML, mobile 390×844 and desktop 1440×900 screenshots, [build artifact 12556222886](https://github.com/jbs0572-boop/01Naver_blog_automa/actions/runs/40084912987/artifacts/12556222886). Artifact was inspected offline: evidence/draft SHA-256 and run ID match manifest, approval/export files are absent, and fontconfig reports `Noto Sans CJK KR`.
- A previous preview screenshot [12524962605](https://github.com/jbs0572-boop/01Naver_blog_automa/actions/runs/40008212448/artifacts/12524962605) visibly rendered Korean as square glyphs. Installed Noto CJK and inspected updated desktop/mobile screenshots: Korean text is now legible. This verifies the shown viewport screenshots, **not** every responsive breakpoint or full end-to-end publishing workflow.
- Regression added: reject falsified `manifest.state`, `external_writes`, `as_of`, and source/claim counts even when draft/input SHA-256 remain valid. Added 7 tests; no evidence or log edits.
- Latest Codex **code** review at `00b679b` completed with no current findings; six historical comments were marked resolved after code/test fixes. Security-review approval is **not** inferred.
- **Hard stop remains:** original `runs/beta-2026-08-26-energy-day.jsonl` (8 events) and `runs/beta-gate0-2026-08-26-9topics.jsonl` (66 events) are absent; whole CI stays red and PR #2/#3 are not eligible for merging. No production deployment, paid provider calls, real Notion/Naver writes or account changes were performed.


## 2026-10-24 코드 수정 및 최신 원본 CI 재검증 (추가 기록)

- 기준일을 2026-10-22로 제한한 조사 원문은 `docs/RESEARCH_2026-10-22.md`에 유지하며, 이 항목은 **2026-10-24에 실행한 실제 테스트**를 기록한다.
- PR #2 head `9b227c0b99cc63d1b7972740308cfcb1d8c5c6a1`: [Actions 40085306133](https://github.com/jbs0572-boop/01Naver_blog_automa/actions/runs/40085306133), 210 passed / 1 failed, 84.89% 줄·분기 합산, 실행 입증 96/100. [품질 아티팩트 12556892449](https://github.com/jbs0572-boop/01Naver_blog_automa/actions/runs/40085306133/artifacts/12556892449).
- PR #3 코드 head `441c0c23955d860e7fc896b5bee3f627ef535962`: [전체 Actions 40086464082](https://github.com/jbs0572-boop/01Naver_blog_automa/actions/runs/40086464082), **292 passed / 1 failed**, 합산 86.32%(coverage.json 정밀값 86.324%), 입증 120/124. [품질 아티팩트 12557565834](https://github.com/jbs0572-boop/01Naver_blog_automa/actions/runs/40086464082/artifacts/12557565834)에서 coverage.json, function-coverage.json, pip-freeze.txt가 존재함을 확인했다. lambda 3개는 여전히 측정 불명확, Protocol 메서드 하나는 실행 대상이 아니다.
- [미리보기 Actions 40086464190](https://github.com/jbs0572-boop/01Naver_blog_automa/actions/runs/40086464190): **85 passed**, [아티팩트 12556974025](https://github.com/jbs0572-boop/01Naver_blog_automa/actions/runs/40086464190/artifacts/12556974025)에 8개 파일. 입력·초안 SHA-256이 manifest와 일치하고 승인/게시 산출물은 없으며 API 계획의 외부 요청은 0건. 실제 PNG 390×844/1440×900을 직접 열어 한글이 정상 표시되는 것을 확인했다(`Noto Sans CJK KR`). 미리보기 성공은 운영 배포가 아니다.
- 추가 회귀 버그: `--execute --out`에서 출력 파일이 이미 존재하거나 부모 디렉터리가 없어도 API 요청을 **먼저** 하던 순서로 인해 유효한 저장처 없이 호출을 낭비할 수 있었다. 출력 경로 사전검사 후에만 API를 요청하도록 수정하고 4개 오프라인 경계 테스트를 추가했다. 별도 실 API 호출은 없었다.
- PR #3 head `441c0c2`의 Codex **코드 리뷰**는 새 지적 0개로 완료됐다(2026-10-24 19:36 KST 확인). PR #2 head `9b227c0`의 최신 Codex 코드는 당시 리뷰 진행 중이었다. 두 PR 모두 원본 과거 로그가 없으므로 전체 CI는 실패이며 병합 금지. 보안 리뷰 승인·운영 승인으로 해석하지 않는다.
- 이 단락을 추가하는 후속 문서 커밋의 CI 결과는 코드 head `441c0c2`의 결과와 구분해 확인해야 한다.
