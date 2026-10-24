---
workflow_id: stage/notion-rider
kind: pipeline-stage
source: notion-rider.md
---

# stage/notion-rider — intend.md

> **문서 목적:** 이 워크플로가 왜 존재하고 언제 완료로 판정되는지 기록합니다. 이 문서는 실행 명령이나 권한 승인서가 아니며, `AGENTS.md`·`BETA-AGENTS.md`·승인 Gate·기존 스키마가 우선합니다.

## 목적
완성 글과 이미지를 승인된 Notion 데이터 소스에 저장하고 재조회해 보존 상태를 검증한다.

## 시작 조건
- Q1 및 canonical manifest 검증을 통과한다.
- Notion 대상 데이터 소스 ID·속성을 `notion-config.md` 및 실제 조회와 대조한다.
- Gate A (`notion_write`) 승인, 만료·범위·run_id·digest 일치가 확인된다.

## 입력
- `final/` 3개 파일 및 `assets/[키워드]/` 자산
- Gate A 승인 기록과 대상 데이터 소스 ID

## 처리 및 산출물
- 단일 작성자·직렬 큐로 Notion 항목과 이미지 블록을 저장한다.
- 저장 후 페이지를 다시 열어 제목·블록·이미지 순서·중복 run_id·roundtrip digest를 검사한다.

**출력**
- Notion 새 페이지 ID와 재조회 검증 시각
- Q2 저장 무결성·검수 상태 및 실행 로그

## 검증·완료 조건
- Q2 재조회가 통과하며 이미지가 실제 첨부됐음을 확인한다.
- 저장 성공과 콘텐츠 품질 통과를 별도 상태로 남긴다.

## 금지·중단 조건
- 승인 거절·만료·누락, 대상 ID 또는 해시 불일치 시 외부 쓰기를 시작하지 않는다.
- 실패한 업로드나 기존 페이지 덮어쓰기를 성공 처리하지 않는다.

## 현재 구현 상태
에이전트 지침에는 Notion 저장 흐름이 정의되어 있다. 현재 로컬 `daily-generate`는 notion-rider를 `skipped` 처리하며 Notion 호출을 실행하지 않는다.

## 관련 원본
- [notion-rider.md](../../../notion-rider.md)
