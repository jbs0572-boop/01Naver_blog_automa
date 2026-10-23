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
- blog-v2-offline-preview Actions 아티팩트 업로드.

## 검증·완료 조건
- 새 엔진 테스트와 문서 목록 테스트가 모두 통과한다.
- approval.json과 naver-copy.md가 CI 아티팩트에 존재하지 않는다.
- 다운로드 가능한 실행 로그와 결과 폴더가 존재한다.

## 금지·중단 조건
- GitHub 토큰은 contents: read이며 secrets나 외부 서비스 키를 사용하지 않는다.
- 직접 Notion/Naver에 쓰거나 게시·배포 승인으로 간주하지 않는다.
- 기존 전체 CI가 실패한 상황을 녹색 성공으로 덮어쓰지 않는다.

## 현재 구현 상태
새 preview Actions 워크플로를 PR 브랜치에 작성했다. 실제 GitHub job 성공은 실행 기록으로 별도 판정한다.

## 관련 원본
- [.github/workflows/blog-v2-preview.yml](../../../.github/workflows/blog-v2-preview.yml)
