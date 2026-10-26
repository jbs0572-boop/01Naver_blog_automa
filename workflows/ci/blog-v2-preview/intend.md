---
workflow_id: ci/blog-v2-preview
kind: github-actions
source: .github/workflows/blog-v2-preview.yml
---

# ci/blog-v2-preview — intend.md

이 문서는 실행 명령이나 권한 승인서가 아니며 기존 AGENTS.md 및 승인 Gate 규칙이 우선합니다.

## 목적
GitHub PR에서 새 v2 초안 엔진의 테스트와 오프라인 예제를 실행하고 검토 가능한 아티팩트를 제공한다.

## 시작 조건
- pull_request, main에 대한 push, 또는 명시적 workflow_dispatch 이벤트.
- GitHub Actions Python 3.13, 비밀키 없는 공개 테스트 입력.

## 입력
- tools/blog_v2.py, v2 테스트, 예시 JSON, workflow 의도 문서.

## 처리 및 산출물
- 새 엔진 및 의도 파일 검사에 대한 pytest 실행.
- demo 및 verify CLI 호출.
- Playwright Chromium을 반드시 설치·실행하고 한글 글꼴 지원을 확인한 뒤, 네트워크를 차단한 모바일/데스크톱 화면을 PNG로 렌더링한다. 브라우저 설치 또는 렌더링 실패는 CI 실패다.
- blog-v2-offline-preview Actions 아티팩트 업로드.

## 검증·완료 조건
- 새 엔진 테스트와 문서 목록 테스트가 모두 통과한다.
- approval.json과 naver-copy.md가 CI 아티팩트에 존재하지 않는다.
- 다운로드 가능한 실행 로그와 결과 폴더가 존재한다.
- 한글 글꼴 확인 기록(korean-font.txt), 모바일·데스크톱 PNG 모두 필수다. 어느 하나라도 없으면 CI가 실패한다. 렌더링 중 가로 넘침도 차단한다. 생성만으로 육안 시각 검수를 통과한 것은 아니다.

## 금지·중단 조건
- GitHub 토큰은 contents: read이며 secrets나 외부 서비스 키를 사용하지 않는다.
- 직접 Notion/Naver에 쓰거나 게시·배포 승인으로 간주하지 않는다.
- 기존 전체 CI가 실패한 상황을 녹색 성공으로 덮어쓰지 않는다.

## 현재 구현 상태
2026-10-26 점검에서 Chrome 실행 파일을 찾지 못하면 screenshot 검증을 건너뛰면서 CI가 성공할 수 있는 경로를 확인했다. 이를 필수 Playwright Chromium 설치·실행, 화면 증거 필수 존재 확인, 오프라인 탐색 및 main push 재검증으로 교체했다. 실제 CI 통과 여부는 해당 PR의 Actions 기록을 근거로 별도 판단한다. 과거 로그 누락에 대한 테스트의 기존 skip 정책은 변경하지 않았다.

## 관련 원본
- [.github/workflows/blog-v2-preview.yml](../../../.github/workflows/blog-v2-preview.yml)
