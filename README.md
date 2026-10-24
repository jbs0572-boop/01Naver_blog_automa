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

GitHub Actions의 **Blog v2 safe preview**는 PR에서 테스트를 실행하고 공개 데이터로 만든 근거팩/초안/manifest와 로컬 HTML 미리보기·화면 캡처를 아티팩트로 올립니다. Chrome이 있으면 한국어 글꼴(Noto CJK)을 설치해 한글이 네모로 깨지는 환경 문제를 막고 `korean-font.txt`에 사용된 글꼴을 기록합니다. PNG 존재와 폰트 검사 통과는 실제 육안 검수를 대신하지 않습니다. CI에는 비밀키가 없고 GitHub 저장소 쓰기 권한도 없습니다. 기존 전체 테스트는 별도의 **Python tests and function coverage**에서 진행합니다.

## 개발 상태

| 기능 | 상태 |
|---|---|
| 공개 근거팩 검증 → 초안 → SHA-256 검증 | 구현·테스트 |
| 사람 승인 → 로컬 수동 복사용 원고 | 구현·테스트 |
| 독립 PR CI 미리보기 | 브랜치/PR 실행 결과에서 판정 |
| 네이버 검색 API HUB 단건 조회 | 코드 구현·오프라인 모의 테스트 완료, 실계정 호출 미검증 |
| 원문 합법 수집 및 출처 내용 대조 | 차기 |
| 자동 LLM 글쓰기/그림과 환각 검사 | 차기 |
| Notion 실계정 저장·재조회 | 미연결, Gate A 필요 |
| Playwright 네이버 임시저장 | 미연결, Gate B 및 사용자 재확인 필요 |
| 자동 발행 | 기본적으로 금지 |

기존 로컬 작업 실행기(tools.automation_runner)는 대부분 생산 단계를 실제 생성하지 않고 상태만 기록할 수 있습니다. v2 생성 명령과 혼동해서 성공을 판단하지 마세요. 과거 추적되지 않은 테스트 로그가 없어 기존 테스트 하나가 실패하는 문제도 [검증 기록](docs/engineering/VALIDATION.md)에서 분리합니다.

## 읽기 전용 NAVER API HUB 조사 모듈

NCP 공식 문서의 GET /search/v1/blog 명세를 따라, 네이버 블로그 검색 결과를 **출처 후보**로만 가져오는 별도 도구를 추가했습니다.

먼저 키 없이 외부 호출 계획만 검사할 수 있습니다.

    python -m tools.naver_search_v2 --query '블로그 자동화' --as-of 2026-10-22 --limit 3

NAVER API HUB 사용 자격과 과금 조건을 별도 확인한 뒤, 본인이 명시적으로 1회 호출을 승인한 경우에만 환경변수 NAVER_API_HUB_CLIENT_ID / NAVER_API_HUB_CLIENT_SECRET를 주입하고 같은 명령에 --execute --out local-search.json을 붙입니다. 기본값은 **외부 요청 0회**이며 자동 재시도·리다이렉트·발행 기능이 없습니다. 검색 요약은 사실 원문이 아니라 검증 전 자료이므로 sourcepack의 official claim으로 자동 변환하지 않습니다. \`--out\` 파일이 이미 있거나 상위 폴더가 없으면 유료 호출 가능성이 있는 API 요청 전에 중단합니다. CI에서는 네트워크 요청을 수행하지 않습니다.

## 오프라인 원고 미리보기

초안 생성 후 `python -m tools.review_site_v2 --folder preview/RUN-<입력해시>`를 실행하면 `preview.html`이 만들어집니다. 파일을 브라우저에서 열어 원고, 근거의 출처·기준일, 검수 체크리스트를 함께 확인할 수 있습니다. 외부 스크립트나 이미지 요청을 허용하지 않고 승인·게시 버튼도 없습니다. 실제 브라우저 시각 검증은 가능한 환경에서 별도 수행해야 합니다.

## 검증 결과와 운영 제한 (2026-10-24)

- [PR #2](https://github.com/jbs0572-boop/01Naver_blog_automa/pull/2): 실행 경계 테스트 보강과 과장 없는 함수 커버리지 측정. [run 40084432799](https://github.com/jbs0572-boop/01Naver_blog_automa/actions/runs/40084432799): 210개 통과, 1개 실패(과거 원본 로그 누락), 줄·분기 84.89%, 검증된 함수 본문 96/100.
- [PR #3](https://github.com/jbs0572-boop/01Naver_blog_automa/pull/3) (PR #2 기준): 근거 기반 v2, 읽기 전용 검색 계획, 로컬 HTML 미리보기. [전체 CI run 40084912963](https://github.com/jbs0572-boop/01Naver_blog_automa/actions/runs/40084912963): 288개 통과, 동일 1개 실패, 줄·분기 86.21%, 함수 본문 120/124. [오프라인 미리보기 run 40084912987](https://github.com/jbs0572-boop/01Naver_blog_automa/actions/runs/40084912987): 81개 통과, Noto CJK 설치 후 모바일·데스크톱 화면 검사 완료.
- **두 PR 모두 병합·운영 배포 보류**: `runs/` 아래 2026-08-26의 실제 기록 2개가 없으며 해당 필수 테스트는 유지합니다. 실 네이버 게시, Notion 기록, 계정 변경, 유료 자원 생성은 수행하지 않았습니다.
- 이 수치는 위 **테스트된 커밋**에 대한 증거입니다. 이후 문서-only merge/CI의 통과 여부와 구별해 확인하세요.
