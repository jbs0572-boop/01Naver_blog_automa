---
workflow_id: v2/editorial-preview
kind: static-human-review
source: tools/review_site_v2.py
---

# v2/editorial-preview — intend.md

이 문서는 실행 명령이나 권한 승인서가 아니며 기존 AGENTS.md 및 승인 Gate 규칙이 우선합니다.

## 목적
실제 게시 없이, 사람이 원고에 달린 근거와 검수 항목을 한 화면에서 확인할 수 있는 오프라인 HTML을 제공한다.

## 시작 조건
- 정상 생성·검증된 evidence-packet이 있다.
- 네이버나 Notion 인증 자격 증명이 필요하지 않다.

## 입력
- 유효한 RUN-.../manifest.json, draft.md, evidence.json.

## 처리 및 산출물
- 원고 근거팩의 제목, 섹션, 주장, 출처 URL/발표일을 안전한 HTML로 구성한다.
- 단일 파일 HTML/CSS를 RUN-.../preview.html에 기록한다.
- CSP와 텍스트/속성 이스케이프로 외부 스크립트·이미지 로드를 막는다.

## 검증·완료 조건
- 검증되지 않은 원고라면 미리보기 생성을 거부한다.
- 한글·반응형 메타태그·접근성 표기·근거 목록이 있다.
- HTML을 다시 만들 때 같은 결과가 나오며 기존 수정 파일을 덮어쓰지 않는다.

## 금지·중단 조건
- 사람의 검수를 자동 승인으로 대체하지 않는다.
- 외부 서비스 접근, 쿠키/토큰 저장, API 발행·네이버 입력은 하지 않는다.
- 실브라우저 검사 결과가 없다면 시각 확인 완료로 보고하지 않는다.

## 현재 구현 상태
정적 HTML 생성과 자동 보안·동일성 테스트를 구현한다. 실제 Chrome/Firefox 렌더링 비교는 별도 검증 항목이다.

## 관련 원본
- [tools/review_site_v2.py](../../../tools/review_site_v2.py)
