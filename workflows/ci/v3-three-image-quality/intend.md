---
workflow_id: ci/v3-three-image-quality
kind: github-actions
source: .github/workflows/v3-three-image-quality.yml
---

# V3 이미지 3종 및 품질 검사 — intend.md

이 문서는 실행 명령이나 권한 승인서가 아니며 기존 AGENTS.md 및 승인 Gate 규칙이 우선합니다.

## 목적

각 카테고리의 서로 다른 5개 글에 원본 맥락 사진, 정보 비교 그래픽, 실행 전 점검 그래픽을 최소 3개 서로 다른 역할로 조립한다. 글과 이미지를 실제 Chromium으로 렌더링하고 95점 기계 품질 기준에 도달한 것만 검수 후보로 만든다.

## 시작 조건

Python 3.13 / Playwright / Chromium / Pillow 환경, 저장소가 보유한 카테고리 스타일 Markdown, 고정된 예제 주제·근거, 합법적으로 확보된 원본 자료 또는 명시적 *합성 테스트 이미지*가 필요하다.

## 입력

editorial_studio/v3/engine.py, cases.json, INTEND.md, IMAGE_ROLES.md, QUALITY_GATE.md 및 승인받은 근거팩과 이미지. 자동화 동작의 출처는 실행 가능한 코드이고 MD는 사람 편집·정책 기준이다.

## 처리 및 산출물

이미지 3종의 HTML/CSS 및 PNG, PC·모바일 전체 글 스크린샷 10장, 원본 SHA-256, Playwright 글자 잘림·오버플로 검사 기록, 100점 평가 JSON, 예외 진단 로그 및 검수용 아티팩트.

## 검증·완료 조건

카테고리 5개, 글별 이미지 3개 이상, 15개 이미지, 10장 PC/모바일 캡처, 모든 필수 검사 성공, 모든 기계 점수 95점 이상, 게시 false, 사람 검수 PENDING. PR CI는 재현 가능한 합성 이미지 테스트이며 실제 원본으로 가장하지 않는다.

## 금지·중단 조건

유료 API·실제 계정 접근·Notion/네이버 쓰기·임시저장·발행 권한 없음. 사진·출처가 존재하지 않거나 글자 잘림·원본 무결성 오류 발생 시 실패로 기록한다. 사실이나 문장 삭제로 통과시키지 않으며 기존 테스트/점수 기준도 완화하지 않는다.

## 현재 구현 상태

v3 소스·역할 규칙·테스트·읽기 전용 GitHub Actions 미리보기 배포용. 실제 원본 사진을 이용한 5편은 별도 승인 자료 ZIP이 필요하다. 실제 출처 검증, 글의 고유 가치 및 검색 유입의 인과효과는 사람 또는 사후 분석 과정으로 남아 있다.

## 관련 원본

- [.github/workflows/v3-three-image-quality.yml](../../../.github/workflows/v3-three-image-quality.yml)
- [editorial_studio/v3/engine.py](../../../editorial_studio/v3/engine.py)
