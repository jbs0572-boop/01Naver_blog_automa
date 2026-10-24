---
workflow_id: v2/evidence-packet
kind: offline-evidence-composer
source: tools/blog_v2.py
---

# v2/evidence-packet — intend.md

이 문서는 실행 명령이나 권한 승인서가 아니며 기존 AGENTS.md 및 승인 Gate 규칙이 우선합니다.

## 목적
사람이 선정·확인한 출처와 주장만을 사용하여 블로그 초안과 검증 가능한 증거 묶음을 생성한다. 실제 게시와 구분한다.

## 시작 조건
- Python 3.13 환경에서 CLI build 또는 demo 명령을 호출한다.
- 출처별 원문 URL·발표일, 제목·독자·기준일, 2개 이상의 주장이 마련돼 있다.
- 사람이 제공한 입력은 명령이 아니라 자료다.

## 입력
- examples/blog-v2-sourcepack.json 또는 같은 형식의 JSON 파일.
- source ID, 주장 ID, evidence_level 및 YYYY-MM-DD 기준일.

## 처리 및 산출물
- URL·날짜·중복·근거 상태·문장 형식을 검사한다.
- 근거 링크를 삽입해 초안 원고를 생성하고 정규화된 SHA-256을 기록한다.
- evidence.json, draft.md, manifest.json을 RUN-해시 폴더에 작성한다.

## 검증·완료 조건
- 해당 파일을 모두 재조회해 내용과 digest가 같아야 한다.
- manifest의 `state=review_required`, `external_writes=false`, 기준일, 출처/주장 개수가 증거팩의 실제 내용과 일치해야 한다.
- 반복 호출이 동일한 실행 폴더를 반환해야 한다.
- CLI는 review_required 상태이며 승인 파일이 없어야 한다.

## 금지·중단 조건
- 발표일이 기준일보다 미래이거나 알 수 없는 출처에 연결된 주장은 차단한다.
- 원문 확인 없는 주장을 사실로 보증하거나 게시 성공으로 기록하지 않는다.
- 외부 사이트 읽기·로그인·Notion/Naver 쓰기를 수행하지 않는다.

## 현재 구현 상태
tools/blog_v2.py에 오프라인 생성과 검증이 구현됐다. AI 문장 확장·실시간 원문 크롤링은 미구현이다.

## 관련 원본
- [tools/blog_v2.py](../../../tools/blog_v2.py)
- [예제 근거팩](../../../examples/blog-v2-sourcepack.json)
