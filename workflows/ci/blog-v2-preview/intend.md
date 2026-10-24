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
- pull_request 또는 명시적 workflow_dispatch 이벤트.
- GitHub Actions Python 3.13, 비밀키 없는 공개 테스트 입력.

## 입력
- tools/blog_v2.py, v2 테스트, 예시 JSON, workflow 의도 문서.

## 처리 및 산출물
- 새 엔진 및 의도 파일 검사에 대한 pytest 실행.
- demo 및 verify CLI 호출.
- 브라우저가 있는 경우 무료 Noto CJK 글꼴을 CI에 설치하고 한글 지원을 검사한 뒤 모바일/데스크톱 PNG를 렌더링한다.
- blog-v2-offline-preview Actions 아티팩트 업로드.

## 검증·완료 조건
- 새 엔진 테스트와 문서 목록 테스트가 모두 통과한다.
- approval.json과 naver-copy.md가 CI 아티팩트에 존재하지 않는다.
- 다운로드 가능한 실행 로그와 결과 폴더가 존재한다.
- Chrome 렌더링 시 한글 글꼴 확인 기록(korean-font.txt)과 두 화면의 PNG가 존재한다. 생성만으로 육안 시각 검수를 통과한 것은 아니다.

## 금지·중단 조건
- GitHub 토큰은 contents: read이며 secrets나 외부 서비스 키를 사용하지 않는다.
- 직접 Notion/Naver에 쓰거나 게시·배포 승인으로 간주하지 않는다.
- 기존 전체 CI가 실패한 상황을 녹색 성공으로 덮어쓰지 않는다.

## 현재 구현 상태
2026-10-24 PR #3의 [Actions run 40084912987](https://github.com/jbs0572-boop/01Naver_blog_automa/actions/runs/40084912987)에서 미리보기 테스트 81개가 통과했다. 아티팩트 12556222886의 모바일/데스크톱 PNG를 육안 검사했고 한국어 글꼴 깨짐은 수정됐다. 전체 Python CI는 기존 역사 로그 누락으로 실패한다.

## 관련 원본
- [.github/workflows/blog-v2-preview.yml](../../../.github/workflows/blog-v2-preview.yml)
