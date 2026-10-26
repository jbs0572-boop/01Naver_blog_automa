## 2026-10-27 추가: 카테고리·개별 글마다 intend.md 적용

문체와 레이아웃은 `profiles/{category}.md`에, **글을 쓰는 목적·검색 의도·원문 조사·이미지 역할·완료 기준은 `topics/{category}/intend.md`**에 나눠 관리합니다.

- [생활·건강](topics/life_health/intend.md): 문제·측정 조건·안전한 점검 순서
- [뉴스·정책](topics/news_policy/intend.md): 공식 발표·시행일·대상·예외
- [영화·드라마·인물](topics/culture_people/intend.md): 공식 사실과 작품 해석·스포일러 구분
- [부동산](topics/real_estate/intend.md): 실거래 조건·시차·권리·계약 점검
- [경제](topics/economy/intend.md): 지표의 정의·기준연도·기간·단위·해석

`editorial_studio.studio build`는 **주제별 별도 `intend.md`**와 글쓰기·이미지 제작 지시서를 같이 생성합니다. `style-lock.json`이 스타일 SHA와 의도 SHA, 이 글의 의도 파일 해시를 검증합니다. v3도 5개 글 출력 폴더 각각에 `intend.md`를 저장하고 JSON 증거에 SHA를 기록합니다.

원본 사진·공식 자료 이미지와 정보 도해·실행 점검 도해는 서로 다른 정보 역할을 맡아야 합니다. 원문 조사를 자동 수행했다고 주장하지 않으며 인간 검수와 게시 승인·유입 측정은 별도입니다.
# 독립 편집 스튜디오 — 5개 카테고리, 5개 고정 스타일

이 디렉터리는 이전 복잡한 Gate/Notion/Naver 체계와 연결되지 않은 독립적인 새 편집 모듈입니다.

## 다섯 개는 서로 다른 스타일입니다

- life_health (생활·건강): 친절한 원리 설명, 해요체, 상황별 실천 도식
- news_policy (뉴스·정책·이슈): 날짜·출처 우선, 합니다체, 시행 일정 정보 그래픽
- culture_people (영화·드라마·인물): 서사 해설, 해요체, 상징적인 무드보드
- real_estate (부동산): 위험·계약 조건 우선, 점검형 합니다체, 서류 체크리스트
- economy (경제): 숫자와 인과 설명 우선, 분석형 합니다체, 데이터 그래프

카테고리 내부에서는 서로 다른 주제라도 같은 MD 원본, 같은 문체·관점·고정 5개 편집 역할(소제목 문구는 주제별 변경 가능)·이미지 정책을 적용합니다.
다른 카테고리의 지침을 혼합하지 않습니다.

## 실행

저장소 루트에서 파이썬 표준 라이브러리만 사용합니다.

    python -m editorial_studio build --topic "아파트 창문 환기 방법" --output-root editorial-preview

선정이 모호하면 오류와 카테고리별 근거 점수를 내보내므로 명시적으로 선택합니다.

    python -m editorial_studio build --topic "전세 기준금리" --category real_estate --output-root editorial-preview

출력 폴더에는 style-lock.json, writer-brief.md, image-brief.md, layout-template.md가 있습니다.
각 작업 지시서는 선택된 스타일 1개만 포함합니다. 프로필 원본 SHA-256을 저장하고 변경·훼손을 감지합니다.

    python -m editorial_studio check --bundle editorial-preview/life_health-<해시>

실제 초안을 작성한 뒤 지정된 카테고리 편집 역할(5개 구획)과 금지 표현을 검사하려면:

    python -m editorial_studio check --bundle <생성된폴더> --draft <본문.md>
    python -m unittest discover -s tests -p test_editorial_studio.py -v

## 전체 작업 원칙: 3단계

1. 검색·출처 검증·주제 선택: 정확한 수치/날짜/공식 근거 확인 → 5개 중 하나 선택.
2. 스타일 고정 글·이미지 제작: 해당 카테고리 MD만 불러와 글과 이미지를 만들고 개별 품질 검수.
3. 글+이미지 조립·미리보기: 이미지가 실제로 생성되었을 때만 배치하고 사람이 확인.

### 구현 범위와 한계

현재 구현된 경로는 5개 독립 스타일 선택·SHA-256 잠금, 저장된 근거팩에서 10편 오프라인 원고 생성, HTML 미리보기, 실제 이미지를 불러온 경우의 기술적 픽셀 검사와 구조 채점입니다. 이 경로는 **실시간 AI 집필이나 발행이 아니라 fixture 검증**입니다.
OpenAI 글·이미지 생성 어댑터 코드는 있지만 명시적 비용 승인과 API 키 없이 호출하지 않으며, CI 성공만으로 실모델의 생성 품질을 주장하지 않습니다. 원본 사진·정보 그래픽 통합은 상위 v3 PR에서 별도로 검증하며, 실시간 근거 수집과 실제 네이버 자동 발행은 아직 미완료입니다.
기계 검사만으로 논조·문체의 모든 미세한 차이와 사실관계를 검증할 수 없습니다.
출처 확인과 글·이미지 육안 검수는 필수이며, 생성하지 않은 이미지를 생성 완료로 기록하지 않습니다.

외부 Notion·Naver 계정 입력, 게시·배포, API 비용 발생은 없습니다.

## 실제 블로그 글 기반 조사 업데이트 (2026-10-25)

- [5개 카테고리별 5편, 총 25편 조사 및 도입·논조·전개·이미지 배치 분석](RESEARCH_STYLES_2026-10-25.md)
- [카테고리당 서로 다른 주제 두 개의 문체 샘플](examples/VOICE_TEST_5_CATEGORIES.md)
- 문체 정체성과 편집 역할은 고정하지만, 주제에 맞는 제목은 바꿀 수 있습니다. 기계 검사는 5개 구획·금지 문구·지침 파일 해시만 확인합니다. 실제 논조·품질은 별도 편집 검수가 필요합니다.
- 새 조사일은 2026-10-25이며 저장소의 `2026-10-22 기준 역사 조사`를 수정하거나 소급하지 않습니다.


## 제작 엔진 연결 — 글+이미지+미리보기

원고와 이미지를 만들 때 **카테고리당 한 프로필 MD만** 로딩합니다.
실제 OpenAI Responses API 모델, Images API 생성 모델과
고해상도 픽셀 검증, 로컬 HTML 미리보기를 연결했습니다.
현재 API 기본값은 비활성입니다. 사용자가 비용 발생을 명시한
`--execute-paid` 명령과 환경변수 `OPENAI_API_KEY`가 있을 때만
API 호출을 시도합니다. 실계정 이미지/원고 호출 성공 여부는 별도 확인이 필요합니다.

**데이터 입력**: `benchmarks/cases.json` 내 한 `case` 객체를
`my-case.json`으로 저장합니다. 실제 작성 전 근거 팩은 사람이 공식 원문과
대조해야 합니다. 검색 요약만으로 공식 사실을 확정하지 않습니다.

```bash
# 1) 비용 없는 구조 검증용 예시 생성기 (실제 LLM 호출 아님)
python -m editorial_studio.engine --case my-case.json --output-root output \
  --text-backend fixture --image-backend none

# 2) 사람이 준비한 고품질 실이미지로 글·이미지·미리보기 조립
python -m editorial_studio.engine --case my-case.json --output-root output \
  --text-backend fixture --image-backend import --image-file cover.png

# 3) 유료 실모델 호출은 사용자가 명시적으로 선택해야 하며 기본 금지
# export OPENAI_API_KEY=...   # 코드/로그에 키를 저장하지 않음
python -m editorial_studio.engine --case my-case.json --output-root output \
  --text-backend openai --image-backend openai --execute-paid

# 4) 다섯 카테고리에서 2주제씩 총 10건 반복 점검
python -m editorial_studio.benchmark \
  --cases editorial_studio/benchmarks/cases.json \
  --out-dir editorial-benchmark
```

1건 결과에는 `sourcepack.json`, `writer-brief.md`, `image-brief.md`,
`cover-prompt.txt`, `article-structure.json`, `draft.md`,
`preview.html`, `result.json`, `style-lock.json`이 생성됩니다.
이미지를 실제로 만들거나 입력해 검사에 통과한 경우에만 `cover.png`가
추가되며 상태가 `IMAGE_READY_FOR_REVIEW`로 바뀝니다.

정량 이미지 검사는 Pillow로 실제 PNG 픽셀을 열어서 최소 크기
1280×720, 종횡비, 컬러 변화량, 단색 이미지를 검사합니다.
**시각적 아름다움·주제 연관성·텍스트 왜곡·저작권은 육안 검수 필수**입니다.
이미지가 없는 경우는 `DRAFT_ONLY`이며 게시 가능으로 보고하지 않습니다.

## 채점 기준과 개선 관리

[100점 평가 루브릭 v1/v2](QUALITY_RUBRIC.md): 근거, 카테고리 일치, 검색 의도,
독자 가치, 이미지, 모바일 가독성을 평가합니다. v2는 이미지·검색 의도 비중을
높이고 v1 결과를 보존합니다. 근거 원문 대조, 생성된 이미지의 적합성,
실제 네이버 유입은 자동 점수로 증명할 수 없으므로 따로 기록합니다.

[10개 반복 검증 케이스](benchmarks/cases.json)와
[`Editorial studio offline 5x2 quality`](../.github/workflows/editorial-studio-quality.yml)
CI는 읽기 전용·무계정·무과금으로 실행됩니다. 본문 생성 테스트는
`fixture` 제공자이고 실제 API 모델 검증은 HTTP 응답을 모킹합니다.
**성공을 실제 LLM 서비스 호출·네이버 게시·트래픽 달성으로 간주하지 않습니다.**

## 실제 검색 수요 신호 (Naver Datalab)

키워드 수요는 추측으로 점수를 매기지 않습니다. [네이버 공식 통합검색어 트렌드 API](https://developers.naver.com/docs/serviceapi/datalab/search/search.md)를 **읽기 전용**으로 연동했습니다.

```bash
# candidates.json: {"topics":[{"query":"아파트 환기","category":"life_health"},{"query":"실내 습도","category":"life_health"}]}
python -m editorial_studio.discovery --topics candidates.json \
  --start 2026-08-01 --end 2026-10-24 --out datalab-plan.json

# 실계정 API 권한 등록과 클라이언트 키가 있을 때만 명시적 읽기 요청
# NAVER_CLIENT_ID와 NAVER_CLIENT_SECRET는 환경변수로만 주입
python -m editorial_studio.discovery --topics candidates.json \
  --start 2026-08-01 --end 2026-10-24 --execute --out datalab-live.json
```

같은 호출에서 2~5개 주제의 **상대 검색 비율**만 비교합니다. 서로 다른 호출에서 반환된 비율을 합쳐 절대 검색량·예상 조회수로 해석하지 않으며, 실제 글에 쓸 사실·공식 원문은 별도 검증합니다. 키가 없으면 네트워크 호출하지 않고 실행 계획만 기록합니다.

## 지속적 평가표 강화

[평가표 진화 방식](QUALITY_RUBRIC.md#v3-이후-점수표의-자동-제안-프로세스)을 구현했습니다. 사람이 채점한 15개 이상의 독립 데이터와 손대지 않은 홀드아웃이 있어야 v3 변경 **후보**를 제안합니다. 실사용자 승인 없이 과거 점수나 운영 채점 기준을 자동으로 덮어쓰지 않으며, 트래픽 상승도 허위로 기록하지 않습니다.

대표 이미지 생성 모델은 2026-10-25 공식 문서에 있는 `gpt-image-2.5-flare`의 `1536x1024` / `xhigh` 품질로 설정했습니다. 실제 이미지당 과금이 발생할 수 있으므로 기본 API 호출은 꺼져 있습니다. 

## 실제 자연 검색 유입 수치 관리

`python -m editorial_studio.traffic --analytics analytics.json --out traffic-report.json`으로 사람이 별도로 내보낸 **자신의 발행 후 분석 데이터**만 집계합니다. 데이터에는 `post_id`, `category`, `start_date`, `end_date`, `analytics_source`, `organic_impressions`, `organic_clicks`, `organic_sessions`을 기록합니다.

- 글 작성·품질 점수와 자연 검색 유입 수치는 별도 필드입니다. **글을 게시하지 않은 경우 유입은 0이 아니라 미측정(null)**입니다.
- CTR=자연 검색 클릭수/자연 검색 노출수로 계산합니다. 자연 검색 세션 수는 별도 측정치로 취급합니다.
- 동일 기간에 5개 카테고리 모두 발행 글 3편 이상이 없으면 **어느 스타일이 유입을 더 늘렸다고 추천하지 않습니다**. 이는 최소 집계 조건이며 인과증명을 보장하지 않습니다.
- 자동으로 네이버/Notion에 로그인하거나 분석 데이터를 가져오지 않습니다.

### 설치 및 코드 검증

```bash
python -m pip install -r editorial_studio/requirements.txt
python -m pip install pytest coverage
python -m coverage run --branch --source=editorial_studio -m pytest -q tests/test_editorial_*.py
python -m coverage report -m
```

전용 GitHub Actions에서는 실제 실행된 편집 엔진의 줄·분기 커버리지 JSON도 아티팩트로 남깁니다. 모델 과금 API는 테스트에서 모킹합니다.

## 2026-10-25 이미지 정책 업데이트: 원본 사진 우선

사용자가 권한을 확인한 원본 인물·장소·영화·제품·건물 사진은 다시 승인 절차를 반복하지 않고 우선 활용합니다. 이미지를 생성해야 할 때에는 정보 그래픽을 HTML/CSS → Playwright 캡처 → DOM·PNG·모바일 검수 → 최대 3회 조정으로 처리합니다.

기본 엔진의 새로운 이미지 선택 옵션:
- --image-backend auto --image-file /path/to/approved.jpg : 원본 사진 보존+합성
- --image-backend auto : HTML/CSS 기반 정확한 정보 이미지
- --image-backend original, html : 제작 방식 강제
- 기존 import, openai, none은 유지

모든 원본 파일은 byte-for-byte 및 SHA-256으로 기록합니다. Chromium 캡처 결과에는 cover.png, mobile.png, slide.html, 시도별 PNG/검사 JSON이 남습니다. 텍스트·이미지 잘림이 끝내 해결되지 않으면 QA_FAIL로 처리하고 생성 완료를 선언하지 않습니다.

자세한 기준: MEDIA_POLICY.md. 사람의 맥락·저작권·미적 검수는 기술 검사와 별개입니다.

## 실제 원본 사진 우선 — 5편 재캡처

`editorial_studio/examples/gallery5_original_sources.json`에 5개의 실제 자료 이미지 링크를 저장합니다. 기존 gallery의 `--image-backend html` 고정값은 오류였으며, 신규 경로는 실제 원본을 별도 다운로드·SHA-256 보존 후 `--image-backend original --image-file ...`로 편집 엔진을 호출합니다. 본문에는 복합 표지뿐 아니라 **실제 원본 사진 또는 실제 과거 웹 화면을 전체 폭으로** 삽입하고 출처를 연결합니다. 사용자 승인 범위의 실제 자료를 사용하며 허구 이미지로 대체하지 않습니다. 다운로드 실패/자료 식별 실패 시 원본으로 가장하지 않고 작업을 중단합니다.

사진 4종(실내 습도기/실제 영화 스틸/서울 아파트/마트 물가), 네이버 서치어드바이저 **과거 실제 화면** 1종입니다. 과거 화면은 현행 정책 UI라는 증거가 아닙니다. 이용 조건과 출처 크레딧은 manifest 및 출력 `asset-provenance.json`에 기록합니다. `python -m editorial_studio.gallery --out-dir five-gallery` 실행 시 승인 원본 다운로드(네트워크)와 실제 Chromium 검수가 필요합니다. 유료 이미지 API·게시/발행 호출은 없습니다.
