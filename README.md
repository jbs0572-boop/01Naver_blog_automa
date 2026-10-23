# 네이버 블로그 자동화 — evidence-first v2 (preview)

**상태:** 2026-10-24 기준 새로운 로컬 **근거 기반 초안 생성 엔진**과 GitHub Actions 미리보기 구현. **네이버 자동 발행/임시저장 서비스가 활성화된 것은 아닙니다.**

## 목적과 이유

공식 네이버 블로그 글쓰기 API는 2020년 종료됐습니다. 대신 먼저 공개 출처와 주장 연결을 검증하고, 사람이 승인한 원고만 수동 입력할 수 있도록 합니다. 과거 Markdown 지침·Gate A/B와 테스트는 보존한 채 v2를 별도 모듈로 추가했습니다.

[2026-10-22 기준 조사](docs/RESEARCH_2026-10-22.md) · [개편 계획/100점 기준](docs/REBUILD_PLAN.md) · [워크플로별 intend.md](workflows/README.md)

## 1분 데모

저장소를 로컬에 내려받은 후 Python 3.13에서 다음을 실행합니다.

    python -m tools.blog_v2 demo --output-root preview

명령은 Git에 포함된 공개 자료 예제를 읽고 다음을 만듭니다.

    preview/RUN-<입력해시>/
      evidence.json      # 출처·주장·기준일
      draft.md           # 근거 링크가 있는 한국어 초안
      manifest.json      # 입력/원고 SHA-256, 검수 대기

출력된 경로를 그대로 넣어 검사합니다.

    python -m tools.blog_v2 verify --folder preview/RUN-<입력해시>

파일을 읽고 공식 원문·날짜·권리를 확인한 다음 **수동 복사용 승인**을 명시할 수 있습니다.

    python -m tools.blog_v2 approve --folder preview/RUN-<입력해시> --reviewer 본인 --digest <manifest.json의 draft_sha256>
    python -m tools.blog_v2 export --folder preview/RUN-<입력해시>

승인 후에는 naver-copy.md가 생성됩니다. **실제 네이버/Notion 계정에 연결하지 않고 글을 올리지도 않습니다.**

## 자신의 글로 사용

examples/blog-v2-sourcepack.json을 복사하여 제목, 독자, 관점, 기준일, 출처 목록, 주장 목록을 채웁니다.

    python -m tools.blog_v2 build --input examples/blog-v2-sourcepack.json --output-root review-bundles

입력의 각 주장은 반드시 출처 ID에 연결됩니다. 출판일이 기준일 이후이거나 위험 URL, 중복 주장, 미확인 등급을 입력하면 실행을 차단합니다. 구조 검사는 **원문 사실관계의 자동 확인이 아니므로** 사람이 출처를 반드시 읽어야 합니다.

## 테스트와 안전한 미리보기

    python -m pip install 'pytest>=9,<10'
    python -m pytest -q tests/test_blog_v2.py tests/test_workflow_intend_docs.py

GitHub Actions의 **Blog v2 safe preview**는 PR에서 테스트를 실행하고 공개 데이터로 만든 근거팩/초안/manifest만 아티팩트로 올립니다. CI에는 비밀키가 없고 GitHub 저장소 쓰기 권한도 없습니다. 기존 전체 테스트는 별도의 **Python tests and function coverage**에서 진행합니다.

## 개발 상태

| 기능 | 상태 |
|---|---|
| 공개 근거팩 검증 → 초안 → SHA-256 검증 | 구현·테스트 |
| 사람 승인 → 로컬 수동 복사용 원고 | 구현·테스트 |
| 독립 PR CI 미리보기 | 브랜치/PR 실행 결과에서 판정 |
| 네이버 검색 API HUB 연동·원문 합법 수집 | 차기 |
| 자동 LLM 글쓰기/그림과 환각 검사 | 차기 |
| Notion 실계정 저장·재조회 | 미연결, Gate A 필요 |
| Playwright 네이버 임시저장 | 미연결, Gate B 및 사용자 재확인 필요 |
| 자동 발행 | 기본적으로 금지 |

기존 로컬 작업 실행기(tools.automation_runner)는 대부분 생산 단계를 실제 생성하지 않고 상태만 기록할 수 있습니다. v2 생성 명령과 혼동해서 성공을 판단하지 마세요. 과거 추적되지 않은 테스트 로그가 없어 기존 테스트 하나가 실패하는 문제도 [검증 기록](docs/engineering/VALIDATION.md)에서 분리합니다.
