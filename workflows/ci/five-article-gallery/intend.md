---
workflow_id: ci/five-article-gallery
kind: offline-article-screenshot-regression
source: .github/workflows/five-article-gallery.yml
---

# 다섯 카테고리 글 실제 캡처 — intend.md

이 문서는 실행 명령이나 권한 승인서가 아니며 기존 AGENTS.md 및 승인 Gate 규칙이 우선합니다.

## 목적

생활·건강, 뉴스·정책, 영화·드라마·인물, 부동산, 경제의 새로운 5개 글을 편집 엔진으로 실제 생성한다. 서로 다른 글쓰기 목소리, HTML/CSS 시각 테마, 정보 그래픽을 연결하고 Chromium에서 전체 글 스크린샷을 찍어 변경을 검증한다.

## 시작 조건

- editorial_studio/benchmarks/cases.json 내 다섯 카테고리의 2번 주제 사용.
- editorial_studio/examples/gallery5_copy.json 내 사람이 별도로 작성한 사례 설명 사용.
- Python, Playwright Chromium, Pillow를 설치할 수 있는 PR 실행 환경.

## 입력

실제 editorial_studio.engine.run 함수와 고정 스타일 프로필, 출처팩, 5개 섹션 편집 원고 및 HTML/CSS 렌더링 사양.

## 처리 및 산출물

- image_backend=html로 실제 Chromium 그래픽 QA를 통과시킨다.
- 편집자 문장을 기존 근거 ID가 연결된 섹션에 추가하고 출처를 보존한다.
- 엔진의 render_markdown 및 render_html로 다시 출력한다.
- 모바일 430px, 데스크톱 1280px 전체 화면을 캡처한다.
- 스크린샷, 원고, 실제 표지 PNG, 시각 QA, contact sheet, proof.json을 아티팩트로 제공한다.

## 검증·완료 조건

5개 주제/제목/스타일이 존재하고 5개 본문 구획, 이미지 로딩, 출처 링크, 가로 스크롤, 브라우저 오류를 검사한다. 글 검수는 PENDING, 외부 게시 상태는 false이다. 실패가 있으면 통과로 표시하지 않는다.

## 금지·중단 조건

원본 사진이 없는데 실제 사진을 사용했다고 주장하지 않는다. 네이버 발행/임시저장이나 Notion 저장, 실계정 인증, 과금 API 호출을 하지 않는다. 자동검사와 사람의 내용·미감 검수를 혼동하지 않는다.

## 현재 구현 상태

실제 엔진 HTML/CSS 표지를 활용한 다섯 편 글과 스크린샷을 CI에서 생성·검증한다. 전체 발행 플로우는 미구현이다.

## 관련 원본

- .github/workflows/five-article-gallery.yml
- editorial_studio/gallery.py
