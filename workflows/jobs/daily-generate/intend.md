---
workflow_id: job/daily-generate
kind: runner-job
source: tools/runner_stages.py
---

# job/daily-generate — intend.md

> **문서 목적:** 이 워크플로가 왜 존재하고 언제 완료로 판정되는지 기록합니다. 이 문서는 실행 명령이나 권한 승인서가 아니며, `AGENTS.md`·`BETA-AGENTS.md`·승인 Gate·기존 스키마가 우선합니다.

## 목적
하루 단위 주제의 7개 단계 진행 상태를 고정 순서로 기록하고, 이미 준비된 산출물의 manifest를 검증한다.

## 시작 조건
- `python -m tools.automation_runner run daily-generate --mode beta --keyword <키워드> --dry-run` (로컬 수동 실행, launchd 설정은 비활성).


## 입력
- 안전한 단일 경로 요소인 `keyword`, 모드(`beta|formal`), 선택적 `run_id`
- 미리 작성된 초안·이미지·final 및 manifest 생성에 필요한 파일

## 처리 및 산출물
- `topic-selector → researcher → writer → image-maker → content-assembler → notion-rider → naver-rider` 순서를 상태와 JSONL 이벤트에 기록한다.
- `content-assembler`에서 이미 존재하는 최종 파일/이미지를 근거로 canonical manifest를 생성하고 재검증한다.
- Notion 및 네이버 단계는 실제 외부 호출 없이 `skipped` 처리한다.

**출력**
- `.automation/state/[run_id].json` 및 `.automation/logs/[run_id].jsonl`
- 기존 산출물이 충족될 때 `manifests/[run_id]-workflow-manifest.json`

## 검증·완료 조건
- 입력 해시 동일, manifest 실파일 SHA-256/파일 순서/정체성 일치
- 상태·로그가 남고 실패 단계 이후 단계가 실행되지 않는다.

## 금지·중단 조건
- 로컬 runner가 콘텐츠를 직접 생성했다고 보고하지 않는다.
- `skipped` 단계나 승인 없는 외부 저장을 완료로 간주하지 않는다.

## 현재 구현 상태
현재 구현은 로컬 순서/상태/manifest 검증기이며 실제 주제 조사·글쓰기·이미지 생성·Notion 저장·네이버 입력을 자동 실행하지 않는다.

## 관련 원본
- [tools/runner_stages.py](../../../tools/runner_stages.py)
