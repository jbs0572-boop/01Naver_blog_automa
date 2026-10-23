---
workflow_id: job/naver-publish
kind: runner-job
source: tools/runner_stages.py
---

# job/naver-publish — intend.md

> **문서 목적:** 이 워크플로가 왜 존재하고 언제 완료로 판정되는지 기록합니다. 이 문서는 실행 명령이나 권한 승인서가 아니며, `AGENTS.md`·`BETA-AGENTS.md`·승인 Gate·기존 스키마가 우선합니다.

## 목적
지정된 실행의 manifest와 Gate B 조건을 확인하고 네이버 외부 쓰기 가능 여부를 안전하게 판정한다.

## 시작 조건
- `python -m tools.automation_runner run naver-publish --mode formal --run-id <run_id> --dry-run` (dry-run만 허용).


## 입력
- 확정된 `run_id`, `formal` 모드, `--dry-run`
- 있으면 해당 `manifests/[run_id]-workflow-manifest.json`

## 처리 및 산출물
- 기존 manifest 파일이 있을 때 무결성을 확인한다.
- `naver-rider`만 검토하고 이전 단계는 `skipped` 처리한다.
- Gate B 승인 검증이 별도 필요하므로 현재 로컬 실행 결과를 `blocked`로 남긴다.

**출력**
- 상태 파일/JSONL 이벤트와 차단 이유
- 네이버 계정이나 블로그에 대한 외부 변경 없음

## 검증·완료 조건
- 실제 네이버 도구 호출이 0회다.
- 승인 부재와 dry-run 전용 제한이 `blocked`로 표현된다.

## 금지·중단 조건
- `--dry-run`이 없으면 외부 쓰기 불가; `--dry-run`을 써도 임시저장 성공으로 표시하지 않는다.
- 실제 운영 입력·발행 권한으로 승격하지 않는다.

## 현재 구현 상태
로컬 러너는 naver-publish 실행을 의도적으로 차단한다. 승인된 네이버 임시저장 실행은 기존 에이전트/외부 도구 및 Gate B를 따로 필요로 한다.

## 관련 원본
- [tools/runner_stages.py](../../../tools/runner_stages.py)
