# 워크플로 의도 문서 안내

프로젝트의 실제 정의와 일대일 대응하는 `intend.md` 목록입니다. 이 이름은 요청된 **`intend.md`** 표기를 그대로 사용합니다.

각 문서는 **목적, 시작 조건, 입력, 처리/산출물, 검증·완료 조건, 금지·중단 조건, 현재 구현 상태, 관련 원본**을 기록합니다. 문서만으로 작업을 실행하거나 Gate A/B 승인을 생성할 수 없습니다.

## 실행 작업 (3개)
- [daily-generate](jobs/daily-generate/intend.md) — `tools.runner_types.JobName`
- [weekly-improve](jobs/weekly-improve/intend.md) — `tools.runner_types.JobName`
- [naver-publish](jobs/naver-publish/intend.md) — `tools.runner_types.JobName`

## 콘텐츠 파이프라인 단계 (7개, 순서 고정)
1. [topic-selector](stages/topic-selector/intend.md)
2. [researcher](stages/researcher/intend.md)
3. [writer](stages/writer/intend.md)
4. [image-maker](stages/image-maker/intend.md)
5. [content-assembler](stages/content-assembler/intend.md)
6. [notion-rider](stages/notion-rider/intend.md)
7. [naver-rider](stages/naver-rider/intend.md)

## GitHub Actions (1개)
- [python-quality](ci/python-quality/intend.md) — `.github/workflows/python-quality.yml`

## 일치성 검사
`python -m pytest -q tests/test_workflow_intend_docs.py`

이 테스트는 `JobName`, `STAGE_ORDER`, 실제 `.github/workflows/*.yml|*.yaml` 목록과 문서 경로를 비교합니다. 새 실행 작업·단계·CI 워크플로 추가 시 해당 `intend.md`를 함께 추가해야 합니다.

## 원칙
- 단일 기준: `AGENTS.md`, `BETA-AGENTS.md`, `schemas/workflow-contract.schema.json` 및 실제 실행 코드.
- 기존 로컬 runner와 에이전트 지침의 **목표 동작을 구분**합니다. 문서가 실제 서비스 연결·발행을 증명하지 않습니다.
- 각 작업의 신규 커밋은 구현 검사, 코드 리뷰 및 필요한 승인 절차를 바꾸지 않습니다.

## v2 evidence-first 실행 단계 (2개)
- [evidence-packet](v2/evidence-packet/intend.md) — 근거팩 검증·초안·manifest
- [review-export](v2/review-export/intend.md) — 사람 승인·수동 복사

## 추가 GitHub Actions
- [blog-v2-preview](ci/blog-v2-preview/intend.md) — 읽기 전용 PR 테스트와 미리보기 아티팩트

**v2 설계·사용법:** [재설계 계획](../docs/REBUILD_PLAN.md) · [2026-10-22 기준 조사](../docs/RESEARCH_2026-10-22.md). 기존 7단계 워크플로와 v2를 혼동하지 마세요. v2의 승인 파일은 네이버 발행 허가가 아닙니다.

- [naver-search-discovery](v2/naver-search-discovery/intend.md) — 공식 API HUB 읽기 전용 검색 후보 수집(명시적 외부 호출 허가 필요)

- [editorial-preview](v2/editorial-preview/intend.md) — 출처를 함께 확인하는 오프라인 반응형 HTML 미리보기
