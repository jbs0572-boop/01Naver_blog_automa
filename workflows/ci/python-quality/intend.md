---
workflow_id: ci/python-quality
kind: github-actions
source: .github/workflows/python-quality.yml
---

# ci/python-quality — intend.md

> **문서 목적:** 이 워크플로가 왜 존재하고 언제 완료로 판정되는지 기록합니다. 이 문서는 실행 명령이나 권한 승인서가 아니며, `AGENTS.md`·`BETA-AGENTS.md`·승인 Gate·기존 스키마가 우선합니다.

## 목적
PR 및 main 변경 시 Python 전체 테스트와 함수 실행 범위를 측정해 회귀를 차단한다.

## 시작 조건
- GitHub Actions `pull_request` 또는 `push` to `main`; 공개 Actions 워크플로 사용.


## 입력
- 저장소 체크아웃된 Python 코드와 tests
- Python 3.13, pytest·hypothesis·jsonschema·coverage.py

## 처리 및 산출물
- `python -m coverage run --branch --source=tools -m pytest -q`로 기존·신규 테스트 전체를 실행한다.
- 실패 여부와 별개로 coverage JSON·함수 미실행 목록 및 pip freeze를 생성한다.
- 테스트에 실패하면 `Enforce full suite result` 단계에서 CI 실패로 판정한다.

**출력**
- GitHub Actions job 판정 및 실행 로그
- 아티팩트: `coverage.json`, `function-coverage.json`, `pip-freeze.txt`

## 검증·완료 조건
- 전체 pytest 0건 실패, 비밀키·쓰기 권한 없이 실행
- 함수 커버리지는 본문 실행 여부 지표일 뿐 완전한 분기 검증이라고 과장하지 않는다.

## 금지·중단 조건
- 미통과 상태를 성공으로 보고하거나 기존 기준을 낮추지 않는다.
- 외부 PR 코드를 비밀키 또는 GitHub 쓰기 권한과 함께 실행하지 않는다.
- CI만으로 Codex 리뷰 완료·운영 배포를 주장하지 않는다.

## 현재 구현 상태
PR #2에 기존 CI가 있다. 2026-10-26에 클린 체크아웃에서 `runs/`가 `.gitignore`로 제외되어 원본 로그 2개가 불가함을 확인했다. 원본이 있는 환경에서는 기존 8/66건 읽기 검증을 유지하며, 둘 다 없는 CI에서는 **미검증 skip**으로 명확히 표시하고 별도의 필수 합성 JSONL 형식 호환성 테스트를 실행한다. 수정 후 CI 통과 여부는 실제 GitHub Actions 결과로만 보고한다. 상세: `tests/fixtures/LEGACY_LOG_POLICY.md`.

## 관련 원본
- [.github/workflows/python-quality.yml](../../../.github/workflows/python-quality.yml)
