---
workflow_id: job/weekly-improve
kind: runner-job
source: tools/runner_stages.py
---

# job/weekly-improve — intend.md

> **문서 목적:** 이 워크플로가 왜 존재하고 언제 완료로 판정되는지 기록합니다. 이 문서는 실행 명령이나 권한 승인서가 아니며, `AGENTS.md`·`BETA-AGENTS.md`·승인 Gate·기존 스키마가 우선합니다.

## 목적
기존 실행 로그와 산출물 수를 읽기 전용으로 집계하여 개선 입력 현황을 파악한다.

## 시작 조건
- `python -m tools.automation_runner run weekly-improve --mode beta --dry-run` (로컬 수동 실행, launchd 스케줄은 비활성).


## 입력
- `runs/*.jsonl`의 실행 이벤트
- `research/`, `drafts/`, `final/`, `assets/`의 실제 파일 목록

## 처리 및 산출물
- 실행 로그 이벤트 수와 각 폴더의 파일 수를 계산한다.
- `researcher` 단계에서 집계 결과를 기록하고 나머지 단계는 `skipped` 처리한다.

**출력**
- `.automation/state/[run_id].json` 및 `.automation/logs/[run_id].jsonl`
- 집계 요약 메시지

## 검증·완료 조건
- 기존 로그 형식이 유효하고 읽기·집계 결과가 재현 가능하다.
- 원본 파일에 기록하거나 삭제하지 않았다.

## 금지·중단 조건
- 점수 상승·실제 콘텐츠 품질 개선을 집계 숫자만으로 주장하지 않는다.
- 자동 규칙 변경·재학습·외부 저장을 수행하지 않는다.

## 현재 구현 상태
현재는 이벤트/파일 개수 집계만 구현돼 있다. 품질 개선안 자동 생성·A/B 검증·규칙 자기수정은 구현돼 있지 않다.

## 관련 원본
- [tools/runner_stages.py](../../../tools/runner_stages.py)
