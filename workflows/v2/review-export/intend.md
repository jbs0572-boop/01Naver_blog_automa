---
workflow_id: v2/review-export
kind: offline-human-review-export
source: tools/blog_v2.py
---

# v2/review-export — intend.md

## 목적
원고를 사람이 직접 검토한 뒤 현재 해시와 연결된 로컬 복사 승인만 부여한다.

## 시작 조건
- evidence-packet의 검증이 통과했고 draft.md를 읽어 원문·날짜·저작권을 점검했다.
- reviewer 식별자와 현재 manifest의 draft_sha256 값을 명시한다.

## 입력
- RUN-.../evidence.json, draft.md, manifest.json.
- 사람이 명시한 reviewer, 해당 원고 digest.

## 처리 및 산출물
- verify 재검사 후 불변 approval.json을 한 번만 기록한다.
- 승인 이후 export를 호출하면 수동 복사 파일 naver-copy.md만 생성한다.

## 검증·완료 조건
- 승인 digest와 실제 draft digest가 일치한다.
- 승인 없이는 export가 오류를 반환한다.
- 기존 수동 복사 파일이 달라졌으면 덮어쓰지 않는다.
- posted=false로 결과를 표시한다.

## 금지·중단 조건
- approval.json은 Gate A/B 또는 네이버 게시 동의로 해석하지 않는다.
- 실제 네이버 편집기, Notion, 외부 저장 API를 호출하지 않는다.
- 다른 내용으로 원고를 바꾼 뒤 같은 승인을 재사용하지 않는다.

## 현재 구현 상태
로컬 승인과 수동 복사 출력을 구현했으며 실제 게시·브라우저 E2E는 미구현이다.

## 관련 원본
- [tools/blog_v2.py](../../../tools/blog_v2.py)
