---
workflow_id: ci/visual-qa
kind: github-actions
source: .github/workflows/visual-qa.yml
---

# Original-first Chromium visual QA — intend.md

이 문서는 실행 명령이나 권한 승인서가 아니며 기존 AGENTS.md 및 승인 Gate 규칙이 우선합니다.

## 목적

실제 원본 사진 우선 사용과 HTML/CSS 이미지 생성, Playwright를 통한 글자 잘림·여백·정렬 검사를 구현한다.

## 시작 조건

지정된 테스트용 또는 승인된 로컬 이미지와 Python, Chromium, Playwright, Pillow가 있다. PR 또는 workflow_dispatch로 실행한다.

## 입력

5개 카테고리 원고·시각 사양, 사용자 승인 로컬 사진, 검사 기준과 실행 명령.

## 처리 및 산출물

원본은 바이트 단위로 보존하고, 1536x1024 데스크톱/390px 모바일 이미지를 만든다. 브라우저 DOM 좌표, 픽셀, 해상도와 원본 해시를 확인한다. 실패하면 3회 내에서 글꼴/간격만 변경하고 시도별 PNG·검사 JSON을 남긴다.

## 검증·완료 조건

5개 카테고리 브라우저 캡처, 원본 유지, 오류 상태 테스트가 실제로 통과해야 한다. 미완료는 완료로 기록하지 않는다.

## 금지·중단 조건

유료 API 호출, 인증 키 입력, Notion·Naver 게시, 외부 데이터 임의 수집을 금지한다. 검수 기준을 완화하거나 HTML의 문장을 삭제해 통과시키지 않는다.

## 현재 구현 상태

로컬 Chromium 영상 캡처와 정량 검사 기능을 전용 테스트·CI로 검증한다. 시각적 미감/콘텐츠 적합성은 사람 검수로 남긴다.

## 관련 원본

- [.github/workflows/visual-qa.yml](../../../.github/workflows/visual-qa.yml)
