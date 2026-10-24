---
workflow_id: stage/content-assembler
kind: pipeline-stage
source: content-assembler.md
---

# stage/content-assembler — intend.md

> **문서 목적:** 이 워크플로가 왜 존재하고 언제 완료로 판정되는지 기록합니다. 이 문서는 실행 명령이나 권한 승인서가 아니며, `AGENTS.md`·`BETA-AGENTS.md`·승인 Gate·기존 스키마가 우선합니다.

## 목적
초안과 검증된 이미지를 정확한 순서로 조립하고 최종 파일·해시 manifest를 만든다.

## 시작 조건
- writer, image-maker의 초안·이미지·연결표 검수가 끝나 있다.
- 제목 약속과 시각 슬롯 검사(Q1)를 통과한다.

## 입력
- `drafts/[키워드].md`
- `assets/[키워드]/image-map.md` 및 연결된 이미지·썸네일

## 처리 및 산출물
- 이미지 마커를 정확히 한 번씩 치환하고 나머지 원문을 보존한다.
- 네이버 레이아웃·복사용 파일, 해시 기반 canonical manifest를 작성·재검증한다.

**출력**
- `final/[키워드].md`
- `final/[키워드]-naver-layout.md`
- `final/[키워드]-naver-copy.md`
- `manifests/[run_id]-workflow-manifest.json`

## 검증·완료 조건
- 필수 파일·이미지가 존재하고 manifest 순서·SHA-256·digest가 일치한다.
- 미치환 마커·이미지 누락·경로 오류·원문 훼손이 없다.

## 금지·중단 조건
- 실패한 Q1을 통과했다고 주장하거나 `final/` 산출물을 승인 없이 외부에 저장하지 않는다.

## 현재 구현 상태
로컬 `daily-generate`는 기존 최종 산출물이 존재할 때 manifest 생성/검증만 시도한다. 초안과 이미지를 새로 조립하는 기능은 로컬 실행기가 구현하지 않았다.

## 관련 원본
- [content-assembler.md](../../../content-assembler.md)
