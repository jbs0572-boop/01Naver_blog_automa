# 네이버 블로그 자동화 워크플로 최적화 구현 계획

## 문서 정보

- 기술 기준일: 2026-08-26 KST
- 상태: Phase 0~4 로컬 구현 완료, Notion 베타 10회 운영 검증 대기
- 목적: 현재 워크플로의 승인 무결성, 이미지 품질 일관성, 평가 신뢰성, Notion 저장 안정성을 기술적으로 강화한다.
- 이번 문서의 범위: 구현 순서와 통과 조건을 정의한다. 기존 지침 파일, 실행 산출물, Notion, 네이버는 이 문서 작성만으로 변경하지 않는다.
- 우선순위: `승인 무결성 → 이미지 품질 → 평가·저장 검증 → 성능 → 네이버 자동화`

## 1. 최종 결정

최상위 단계 순서는 변경하지 않는다.

### 베타 모드

```text
topic-selector
  → researcher
  → writer
  → image-maker
  → content-assembler
  → notion-rider
  → Notion 저장 무결성 검사
  → (선택) 사람 검수 기록
```

### 정식 모드

```text
topic-selector
  → researcher
  → writer
  → image-maker
  → content-assembler
  → notion-rider
  → Notion 저장 무결성 검사
  → (선택) 사람 검수 기록
  → 네이버 쓰기 선행 검증
  → naver-rider
```

자동 품질검사, 산출물 manifest 생성, 해시 검증은 새 최상위 단계로 추가하지 않고 현재 단계의 내부 Gate로 구현한다. 네이버 자동화 재구성은 첫 최적화 범위에서 제외하고 마지막 어댑터로 보존한다.

## 2. 현재 결함과 목표 상태

| ID | 현재 결함 | 위험 | 목표 상태 | 해결 수준 |
|---|---|---|---|---|
| `OPT-01` | `naver-rider`가 사용하는 `naver-layout.md`, `naver-copy.md`가 현재 `artifact_digest` 범위에 없음 | 검증 후 네이버 입력물이 바뀌어도 변경을 놓칠 수 있음 | 외부 저장에 사용되는 모든 파일을 하나의 canonical manifest와 해시에 결합 | 완전 해결 가능 |
| `OPT-02` | 이미지 연결표에 모델 스냅샷·품질·크기·프롬프트·참조 해시가 없음 | 같은 지침으로도 결과를 재현하거나 품질 하락 원인을 추적하기 어려움 | 생성 조건과 입력·출력 해시를 실행별로 고정·기록 | 크게 개선 가능 |
| `OPT-03` | 이미지 Gate가 파일 정상 여부와 내용 정확성 중심이며 기준 사례 대비 미감·모바일 렌더링을 측정하지 않음 | 정상 파일이지만 조악하거나 반복적인 이미지가 통과할 수 있음 | 자동 검사, 기준 사례 비교, 실제 렌더링, 사람 검수를 결합 | 크게 개선 가능 |
| `OPT-04` | 단계명·상태·필수 필드가 여러 MD와 Notion 설정에 중복됨 | 한 파일만 수정하면 계약 불일치 발생 | 실행 가능한 단일 JSON Schema를 기준 계약으로 사용 | 완전 해결 가능 |
| `OPT-05` | 저장 전 자동 품질검사와 Notion 저장 후 사람 평가의 경계가 불명확함 | 품질 실패 글이 외부 저장되거나 저장 오류가 콘텐츠 점수에 섞일 수 있음 | 저장 전 품질, 저장 후 무결성, 사람 평가를 분리 기록 | 완전 해결 가능 |
| `OPT-06` | Notion API 버전·비동기 완료·중복 생성 대응이 실행 가능한 검증으로 고정되지 않음 | 응답 지연 때 중복 페이지·누락 이미지·불완전 저장 가능 | 최신 API 버전 고정, 업로드 완료 확인, `run_id` 재조회 후 재시도 | 크게 개선 가능 |
| `OPT-07` | 네이버 공식 글쓰기 API가 없어 UI 자동화에 의존 | 로그인·에디터 변경으로 자동화 실패 가능 | 후순위 화면 어댑터, Trace, 모바일 캡처, 사용자 재확인 | 완화 가능 |
| `OPT-08` | 현재 규칙은 대부분 MD 지침이며 실행 차단 프로그램이 없음 | 지침을 잘못 따라도 기술적으로 차단되지 않을 수 있음 | Schema·manifest·Gate를 검사하는 최소 실행 검증기와 Codex Hook 도입 | 완전 해결 가능 |

## 3. 구현 원칙

1. 기존 단계 ID와 출력 경로를 유지한다.
2. 베타에서는 네이버를 호출하지 않는다.
3. 정식 산출물과 실행 로그는 단계별 단일 작성자만 기록한다.
4. 조사·준비·검토만 최대 두 Lane으로 병렬화하고 외부 쓰기는 직렬화한다.
5. MD 지침 추가만으로 기술 결함이 해결됐다고 판정하지 않는다. 실제 검증기가 실패 입력을 차단해야 한다.
6. 생성 이미지 품질은 모델 판정만으로 통과시키지 않고 사람이 최종 렌더링을 확인한다.
7. GPT Image 계열에 제공되지 않는 `seed`를 재현성 필드로 만들지 않는다. 모델 스냅샷, 입력 해시, 출력 해시로 실행을 고정한다.
8. 공식 화면·제품·문서·로고는 생성 이미지로 대체하지 않는다.
9. 기존 로그·산출물·Notion 항목은 수정하지 않고 신규 `pipeline_version`부터 새 계약을 적용한다.

## 4. 최소 기술 구성

최적화 구현 시 신규 구성 요소는 최소화한다.

| 구성 요소 | 역할 | 필수 여부 |
|---|---|---|
| `schemas/workflow-contract.schema.json` | 단계·상태·시각 슬롯·이미지 생성 정보·manifest의 단일 계약 | 필수 |
| 실행 검증기 1개 | Schema 검사, manifest 생성, SHA-256 계산, Notion·네이버 쓰기 선행 검증, 실패 시 비정상 종료 | 필수 |
| Codex Hook | Notion·네이버 쓰기 도구 실행 전에 검증기를 호출하고 불일치 시 차단 | 필수 |
| 이미지 생성 호출 계층 | 모델 스냅샷·품질·크기·참조 이미지를 명시적으로 전달하고 응답 메타데이터 기록 | 생성 이미지 사용 시 필수 |
| 고정 뷰포트 시각검사 | 원본 이미지와 최종 모바일 렌더링 캡처·비교·Trace 저장 | 필수 |

현재 프로젝트에는 실행 차단 검증기가 없으므로, 기존 MD 파일만 업데이트하는 작업은 `정책 반영`이고 검증기와 Hook이 작동해야 `기술 구현 완료`로 판정한다.

## 5. Canonical artifact manifest

### 5.1 포함 대상

`content-assembler`가 통과한 직후 실행별 manifest를 만든다. 다음 파일을 반드시 포함한다.

1. `final/[키워드].md`
2. `final/[키워드]-naver-layout.md`
3. `final/[키워드]-naver-copy.md`
4. `assets/[키워드]/image-map.md`
5. 본문에서 참조하는 모든 이미지
6. 전용 썸네일

베타에서 네이버를 호출하지 않더라도 `content-assembler`의 정식 출력 계약이 세 파일이므로 세 파일 모두 고정한다. 누락 파일을 선택적으로 무시하지 않는다.

### 5.2 manifest 필수 필드

```json
{
  "schema_version": "1.0",
  "pipeline_version": "workflow-optimized-v1",
  "run_id": "RUN-YYYYMMDD-HHMMSS",
  "topic_id": "TOPIC-ID",
  "mode": "beta",
  "created_at": "2026-08-26T00:00:00+09:00",
  "files": [
    {
      "role": "final_markdown",
      "path": "final/example.md",
      "order": 1,
      "size_bytes": 0,
      "sha256": "..."
    }
  ],
  "artifact_digest": "sha256:..."
}
```

### 5.3 해시 규칙

- 개별 파일 SHA-256은 파일의 실제 byte를 대상으로 계산한다.
- 파일 목록 순서는 `role → order → path` 규칙으로 고정한다.
- `artifact_digest`는 `artifact_digest` 필드 자체를 제외한 canonical JSON을 UTF-8, 정렬된 key, 불필요한 공백 없이 직렬화한 byte로 계산한다.
- manifest 생성 후 파일 하나라도 바뀌면 외부 쓰기 선행 검증을 실패시킨다.
- 네이버 쓰기 선행 검증은 같은 `artifact_digest`에 `notion_page_id`, `notion_verified_at`, `notion_roundtrip_digest`, `blog_id`를 추가로 결합한다.

## 6. 이미지 품질 일관성 구현

### 6.1 생성 조건 고정

생성 이미지는 호출 환경이 다음 제어값을 실제로 전달하고 기록할 때만 재현성 검사를 통과한다.

- 모델: `gpt-image-2`
- 모델 스냅샷: `gpt-image-2-2026-04-21`
- 품질: 역할별 `high` 또는 승인된 고정값
- 크기·비율: 썸네일, 본문 사진형, 정보 도식별 고정 profile
- 입력 이미지 충실도와 참조 이미지 목록
- 프롬프트 template 버전과 최종 프롬프트 SHA-256
- 참조 이미지별 SHA-256
- 생성 결과 파일 SHA-256

현재 Codex 이미지 호출 표면처럼 모델 스냅샷·품질·크기를 지정할 수 없는 호출은 `generation_control=unlocked`로 기록하고 정식 품질 기준을 통과시키지 않는다. 구현 단계에서는 환경 변수로 인증을 주입하는 별도 OpenAI 이미지 API 호출 계층을 사용하며 토큰을 파일이나 실행 로그에 저장하지 않는다.

### 6.2 역할별 제작 방식

| 이미지 역할 | 기본 방식 | 금지 사항 |
|---|---|---|
| 공식 화면·제품·문서·행사 증거 | 공식·허가 자산 또는 검증된 캡처 | 생성 이미지로 정체성 대체 |
| 사진형 썸네일·대표 장면 | 고품질 생성 또는 권리 확인 자산, 주제별 기준 이미지 사용 | 빈 배경과 단순 텍스트 카드로 대체 |
| 인물·관계 설명 | 확인된 범위의 공식 자산 또는 생성 사실을 표시한 카드·도식 | 인물명·관계·사건 창작 |
| 일정·절차·비교 도식 | 검증된 데이터를 결정론적 레이아웃으로 렌더링 | 생성 모델에 긴 한글·숫자·날짜를 맡김 |

사진형 배경과 정확한 한글 정보가 함께 필요한 경우 `시각 베이스 생성 → 결정론적 텍스트·도형 합성 → OCR 대조` 순서로 제작한다.

### 6.3 image-map 확장 필드

`image-map.md`의 각 생성 자산에 다음 필드를 추가한다.

```yaml
generation_provider: "openai"
generation_model: "gpt-image-2"
generation_snapshot: "gpt-image-2-2026-04-21"
generation_control: "locked | unlocked"
quality: "high"
size: "WIDTHxHEIGHT"
prompt_template_version: "image-prompt-v1"
prompt_sha256: "sha256:..."
reference_sha256: ["sha256:..."]
output_sha256: "sha256:..."
generated_at: "KST ISO-8601"
provenance_status: "generated | official | licensed | captured"
```

### 6.4 자동 이미지 검사

다음 검사를 모두 수행하고 원시 결과를 `runs/*.jsonl`에 기록한다.

- 파일 decode, 실제 크기, 비율, 색상 공간, 용량
- 완전 동일 SHA-256과 근사 중복 이미지 검사
- 이미지 안 필수 한글·숫자·날짜 OCR 대조
- 시각 슬롯과 실제 자산 유형·주제·본문 위치의 Schema 검사
- 자동 시각 평가의 구조화된 점수와 사유
- 고정 모바일 뷰포트에서의 최종 렌더링 캡처
- 이전 통과 이미지와 비교한 품질 회귀 여부

자동 시각 평가기는 보조 판정만 한다. 자동 점수가 통과여도 사람 검수가 실패하면 해당 이미지는 실패다.

### 6.5 사람 이미지 평가 기준

사용자가 제공한 우수 사례 3장은 아래 특성을 정의하는 평가 기준으로만 사용한다. 원본 자산으로 재배포하거나 무단 합성 입력으로 사용하지 않는다.

- 사진·영화 포스터 수준의 시각적 밀도와 사실감
- 피사체와 배경의 자연스러운 통합
- 핵심 정보가 한눈에 보이는 계층
- 모바일에서도 읽히는 제목과 라벨
- 썸네일과 본문 이미지의 서로 다른 정보 역할

각 이미지를 0~4점으로 평가한다.

| 평가 항목 | 판정 내용 |
|---|---|
| 주제 적합성 | 제목 약속과 독자 질문을 정확히 시각화하는가 |
| 구도·가독성 | 모바일 크기에서 피사체와 텍스트가 명확한가 |
| 렌더링 완성도 | 얼굴·손·경계·빛·원근·합성에 눈에 띄는 오류가 없는가 |
| 정보 기여 | 본문을 반복하지 않고 새로운 이해를 추가하는가 |
| 스타일 일관성 | 같은 글 안에서 품질과 시각 언어가 일관적인가 |

통과 기준은 `총점 16/20 이상`, `개별 항목 3점 미만 없음`이다. 사실 오류, 잘못된 인물·제품 정체성, 틀린 한글·숫자·날짜, 권리 상태 위반은 총점과 관계없이 실패다.

## 7. 품질평가 시점 분리

평가는 다음 세 검사로 분리한다. 새 파이프라인 단계는 만들지 않는다.

### Q1: 외부 저장 전 자동 품질 Gate

담당: 각 단계 주 담당자와 `content-assembler`

- 근거·최신성·제목 약속·총정리 범위
- 본문 반복·독자 질문·독창성
- 시각 슬롯 계약·이미지 정확성·이미지 품질
- 세 최종 파일과 이미지의 canonical manifest

Q1 실패 시 모든 모드에서 Notion 쓰기를 시작하지 않는다.

### Q2: Notion 저장 후 무결성 검사

담당: `notion-rider`

- 저장된 제목·본문 구조·링크·목록·표 순서
- 이미지·썸네일 수, 순서, 첨부 완료 상태
- 로컬 원본과 Notion 재조회 결과의 정규화된 구조 digest
- 같은 `run_id`의 중복 페이지 여부

Q2 실패는 콘텐츠 품질 점수와 별개로 `storage_integrity=failed`로 기록한다.

### Q3: 선택적 사람 검수 기록

담당: 사용자 또는 승인된 검수자

- `evaluation-rubric.md`의 본문·근거·이미지 평가
- 원본 이미지와 Notion 모바일 렌더링을 함께 확인
- Q3 결과는 다음 단계의 차단 조건으로 사용하지 않음

## 8. Notion 저장 안정화

1. API 직접 호출 계층을 사용할 경우 `Notion-Version: 2026-03-11`을 고정한다.
2. 연결 도구가 Enhanced Markdown 생성·전체 조회를 지원하면 Markdown 왕복 검증을 우선 사용한다.
3. 지원하지 않으면 현재 블록 저장 방식을 유지하고 정규화된 블록 유형·텍스트·순서·첨부 ID manifest를 비교한다.
4. 파일 업로드는 상태가 `uploaded`가 된 뒤에만 페이지에 첨부한다.
5. 비동기 쓰기는 완료 또는 실패 상태까지 확인한 뒤 다음 작업으로 진행한다.
6. 시간 초과 시 같은 페이지를 즉시 다시 만들지 않는다. 먼저 `실행 ID=run_id`로 조회해 기존 페이지와 업로드를 복구한다.
7. Notion에는 공식 idempotency key를 가정하지 않는다. `run_id` 사전 조회와 응답 불확실 시 재조회로 중복을 방지한다.
8. 재조회에서 반환되는 만료형 파일 URL 문자열은 해시 비교 대상에서 제외하고 첨부 ID·블록 위치·순서를 비교한다.
9. 429 응답은 `Retry-After`를 따르고 모든 Notion 쓰기는 단일 큐에서 직렬 처리한다.

초기 구현에서는 Notion 속성을 추가하지 않는다. 상세 원시 결과는 `runs/*.jsonl`에 기록하고 기존 `검수 메모`, `검수 결과`, `사람 검수`, `성능 판정` 속성으로 요약한다.

## 9. 네이버 자동화 후순위 계획

첫 최적화 구현과 베타 10회에서는 `naver-rider`를 실행하거나 재구성하지 않는다. 다음 계약만 보존한다.

- `final/[키워드]-naver-layout.md`
- `final/[키워드]-naver-copy.md`
- 전용 썸네일과 이미지 순서
- 현재 canonical `artifact_digest`
- Notion 페이지 ID와 마지막 검증 시각

다음 조건이 모두 충족된 뒤 별도 구현한다.

- 최적화 베타 10회에서 필수 산출물 누락·충돌 0건
- 이미지 사람 검수 최종본 10/10 통과
- Notion 저장·재조회 무결성 10/10 통과
- 베타·정식 manifest 해시 변조 차단 시험 100% 통과

후속 네이버 구현은 `기록·재생 가능한 안정 경로 → 모바일 뷰포트 확인 → 입력 구간별 화면 캡처 → Playwright Trace → Q1·Q2 선행 검증 → 임시저장 직전 사용자 확인` 순서로 구성한다. 공식 글쓰기 API가 없으므로 UI 변경과 로그인 상태 위험은 잔여 위험으로 남긴다. 예상 시간은 기본 구성 2~4시간, 캡처·Trace·오류 복구를 포함한 안정화 4~8시간, 반복 검증 1~2일이다.

## 10. 구현 단계와 수정 대상

### Phase 0: 기준선 동결

예상 실작업: 반나절

- 기존 베타 10건의 단계별 시간·오류·이미지 유형·사람 평가를 읽기 전용으로 집계한다.
- 사용자 제공 우수 사례 3장에서 품질 특성만 기준선으로 기록한다.
- 기존 산출물과 로그는 수정하지 않는다.
- 신규 `pipeline_version=workflow-optimized-v1`을 확정한다.

통과 조건:

- 비교 대상 run 목록과 원시 근거가 고정됨
- 이미지 품질 기준과 현재 하락 사례가 구분됨
- 측정하지 않은 값이 추정값으로 채워지지 않음

### Phase 1: 계약과 강제 Gate 구현

예상 실작업: 1일

| 대상 | 구현 내용 |
|---|---|
| `schemas/workflow-contract.schema.json` | 단계·이벤트·시각 슬롯·이미지 메타데이터·manifest Schema |
| 실행 검증기 | Schema 검사, manifest 생성, digest 재계산, Notion·네이버 쓰기 선행 검증 |
| Codex Hook | 외부 쓰기 전에 검증기 실행, 실패 시 도구 호출 차단 |
| `metrics-spec.md` | Schema를 기준으로 이벤트·해시 규칙 정의 |
| `AGENTS.md` | canonical manifest와 Q1·Q2 경계 |
| `archive/legacy-beta/docs/BETA-AGENTS.md` | 당시 Q1 → Notion → Q2 흐름 기록 |
| `content-assembler.md` | 세 최종 파일과 이미지 manifest 생성 책임 |

통과 조건:

- 필수 파일 하나를 변경한 변조 시험에서 외부 쓰기가 차단됨
- `naver-layout.md`와 `naver-copy.md` 변경도 차단됨
- 잘못된 단계명·상태·시각 슬롯 필드가 Schema 검사에서 실패함
- 베타와 정식 모두 Q1·manifest·지정 데이터 소스 검증 후 지정 Notion 도구만 허용됨

### Phase 2: 이미지 품질 경로 구현

예상 실작업: 1~2일

| 대상 | 구현 내용 |
|---|---|
| `image-maker.md` | 생성 조건 잠금, 메타데이터, 자동·사람 품질 Gate |
| `image-style-guide.md` | 사진형·정보형 profile, 우수 사례 특성, 모바일 기준 |
| 이미지 생성 호출 계층 | 모델 스냅샷·품질·크기·참조 입력 고정과 결과 기록 |
| 시각검사 | decode·중복·OCR·구조화 평가·모바일 렌더링 캡처 |
| `evaluation-rubric.md` | 이미지 5개 평가 항목과 Q3 사람 판정 |

먼저 서로 다른 유형의 주제 3건으로 외부 저장 없는 calibration을 수행한다. 3건이 품질 기준을 통과해야 베타 10회로 넘어간다.

통과 조건:

- 생성 이미지 메타데이터 완전성 100%
- `generation_control=unlocked` 이미지의 정식 통과 0건
- OCR 대상 한글·숫자·날짜 오류 0건
- 근사 중복 또는 승인되지 않은 템플릿 재사용 0건
- 사람 이미지 평가가 모든 최종 이미지에서 기준 통과

### Phase 3: Notion 저장 왕복 검증

예상 실작업: 반나절~1일

| 대상 | 구현 내용 |
|---|---|
| `notion-config.md` | 사용 API 버전과 지원 기능 확인 결과 기록 |
| `notion-rider.md` | 업로드 완료, 비동기 완료, `run_id` 중복 방지, round-trip digest |
| `evaluation-rubric.md` | 저장 무결성과 콘텐츠 품질 점수 분리 |

베타와 정식 외부 저장은 Q1·manifest·지정 데이터 소스 검증 후 시험한다.

통과 조건:

- 동일 `run_id` 중복 페이지 0건
- 이미지 업로드 완료 전 첨부 0건
- 본문·목록·표·링크·이미지·썸네일 순서 불일치 0건
- 불확실한 응답 재시도에서 중복 생성 0건

### Phase 4: 외부 저장 없는 Dry-run

예상 실작업: 반나절

다음 실패 주입을 수행한다.

1. 필수 최종 파일 누락
2. manifest 생성 후 `naver-layout.md` 변경
3. manifest 생성 후 이미지 byte 변경
4. 이미지 생성 메타데이터 누락
5. 이미지 품질 점수 미달
6. 베타·정식 Q1·manifest·대상·Notion 도구 불일치
7. 베타 모드에서 `naver-rider` 호출 시도
8. 네이버 쓰기 선행 검증의 Notion 페이지 ID·검증 시각 불일치

통과 조건은 8개 시험 모두 외부 상태를 변경하기 전에 차단되는 것이다.

### Phase 5: 최적화 베타 10회

예상 소요: 실행량에 따라 1~2일

- 고유 `batch_id`, `run_id`, `topic_id`를 사용한다.
- 실제 이미지 생성은 우선 직렬로 유지한다.
- Q1·manifest·지정 데이터 소스 검증 범위 안에서만 Notion에 저장한다.
- 네이버는 호출하지 않는다.
- Q1, Q2와 선택적 Q3 결과, 단계별 시간·재시도·비용을 기록한다.

운영 전 필수 통과 기준:

- 단계 순서 위반 0건
- 파일 충돌·필수 산출물 누락 0건
- 해시 불일치 상태의 외부 쓰기 0건
- Notion 중복 페이지·누락 이미지·순서 오류 0건
- 최종 이미지 사람 평가 10/10 통과
- 이미지 생성 메타데이터 완전성 100%
- 기존 기준선보다 품질 저하 없음
- 성능·비용 증가가 있으면 실제 측정값과 품질 개선 폭을 함께 보고

독립 이미지 슬롯 병렬 생성은 위 기준을 충족한 뒤 별도 승인으로만 검토한다.

### Phase 6: 네이버 자동화 재구성

현재 범위에서는 실행하지 않는다. Phase 5가 통과하고 사용자가 별도 승인할 때 `naver-rider.md`와 화면 자동화만 업데이트한다. 정식 모드 단일 주제로 임시저장까지 시험하며 발행은 수행하지 않는다.

## 11. 구현 중 금지 사항

- 이미지 모델만 교체하고 품질 결함이 해결됐다고 판정하지 않는다.
- MD 지침만 추가하고 Gate가 기술적으로 차단된다고 보고하지 않는다.
- `content-assembler` 단계명을 변경하지 않는다.
- `naver-layout.md`와 `naver-copy.md`를 제거하거나 승인 해시 밖에 두지 않는다.
- GPT Image에 없는 seed 값을 만들거나 재현성을 보장한다고 기록하지 않는다.
- 자동 평가 모델 단독으로 이미지 최종 통과를 결정하지 않는다.
- 베타 중 네이버 로그인·입력·임시저장·발행을 실행하지 않는다.
- Notion 응답이 불확실할 때 같은 쓰기를 즉시 반복하지 않는다.
- 기존 파일·로그·Notion 항목을 덮어쓰거나 삭제하지 않는다.

## 12. 중단·롤백 기준

다음 중 하나라도 발생하면 해당 Phase에서 중단한다.

- 기존 통과 산출물이 새 Schema에서 이유 없이 대량 실패함
- 이미지 품질은 개선됐지만 사실 정확성·권리 상태가 하락함
- Gate가 정상 승인도 차단하거나 잘못된 승인을 통과시킴
- Notion 중복 페이지 또는 이미지 누락이 발생함
- 베타에서 네이버 상태가 변경됨

롤백은 이번 최적화에서 새로 추가한 Hook 비활성화와 `pipeline_version` 복귀로 제한한다. 기존 실행 산출물과 외부 항목은 자동 삭제하지 않는다. 롤백 후 실패 입력·로그·재현 절차를 보존하고 원인을 수정한 뒤 새 버전으로 다시 검증한다.

## 13. 최종 완료 정의

다음 조건을 모두 충족해야 최적화 구현 완료로 판정한다.

- 기존 베타·정식 파이프라인 순서가 유지됨
- 단일 Schema와 실행 검증기가 실제 실패 입력을 차단함
- 모든 외부 저장용 산출물이 canonical `artifact_digest`에 포함됨
- 이미지 생성 조건과 입력·출력 해시가 100% 기록됨
- 자동 이미지 검사와 사람 모바일 검수가 모두 작동함
- 저장 전 품질, 저장 후 무결성, 사람 평가가 분리됨
- Notion 베타 10회에 중복·누락·순서 오류가 없음
- 네이버 자동화는 후순위로 격리되고 입력 계약은 보존됨
- 기존 산출물·로그·Notion 항목이 보존됨

## 14. 공식 기술 근거

- OpenAI, GPT-Image-2: <https://developers.openai.com/api/docs/models/gpt-image-2>
- OpenAI, Image generation guide: <https://developers.openai.com/api/docs/guides/image-generation>
- OpenAI, GPT Image prompting guide: <https://developers.openai.com/cookbook/examples/multimodal/image-gen-models-prompting-guide>
- OpenAI, Structured Outputs: <https://developers.openai.com/api/docs/guides/structured-outputs>
- OpenAI, Graders: <https://developers.openai.com/api/docs/guides/graders>
- OpenAI, Codex Hooks: <https://learn.chatgpt.com/docs/hooks>
- OpenAI, Record and Replay: <https://learn.chatgpt.com/docs/extend/record-and-replay>
- Notion, Working with Markdown content: <https://developers.notion.com/guides/data-apis/working-with-markdown-content>
- Notion, File upload: <https://developers.notion.com/reference/create-file>
- Notion, Request limits: <https://developers.notion.com/reference/request-limits>
- Playwright, Visual comparisons: <https://playwright.dev/docs/test-snapshots>
- Playwright, Emulation: <https://playwright.dev/docs/emulation>
- Playwright, Trace Viewer: <https://playwright.dev/docs/trace-viewer>
- NAVER Developers, API 목록: <https://developers.naver.com/docs/common/openapiguide/apilist.md>
- NAVER Developers, 블로그 검색 API: <https://developers.naver.com/docs/serviceapi/search/blog/blog.md>

## 15. 구현 결과와 남은 운영 단계

Phase 0의 읽기 전용 기준선과 Phase 1~4의 로컬 구현을 완료했다. 기준선은 `baseline/phase0-2026-08-26.json`에 고정했고, 신규 계약·manifest·Gate 검증기·Codex Hook·이미지 메타데이터/Q3 검증기·dry-run 시험을 추가했다. 기존 산출물·로그·Notion 항목에는 쓰지 않았다.

베타 Notion 쓰기는 Q1·manifest·지정 데이터 소스와 실제 연결·스키마·첨부 재조회 확인이 있을 때 Phase 5의 10회로 진행한다. 네이버 자동화는 Phase 5 통과와 별도 운영 결정이 있기 전까지 실행·재구성하지 않는다.
