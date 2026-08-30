# 로컬 워크플로 실행기 PRD

## 1. 목적

이 제품은 네이버 블로그 콘텐츠 파이프라인을 시작하고, 담당 에이전트를 정해진 순서로 실행하며, 검증된 결과를 Notion에 저장한 뒤 네이버 새 글에 입력하고 임시저장하는 로컬 워크플로 실행기다. 발행·예약 발행·공개 설정 변경은 지원하지 않는다.

`AGENTS.md`가 유일한 활성 운영 계약이며, 이 문서는 실행기 구현 요구사항을 정의한다. 기존 베타 Notion 행·보기·로그는 역사 기록으로 보존하지만 신규 실행의 입력·분기·완료 조건에는 사용하지 않는다.

## 2. 사용자 시작 방식

새 `daily-generate` 실행은 정확히 하나의 주제 입력 방식으로 시작한다.

| 입력 방식 | 실행기 입력 | topic-selector 책임 |
|---|---|---|
| 직접 입력 | `keyword` | 사용자가 제공한 주제·키워드를 보존하고 유효성·중복·검색 의도·조사 가능성을 확인 |
| 자동 선정 | `auto_topic=true` | 확인 가능한 근거로 주제 하나를 선정 |

두 경우 모두 `research/topic-selection-[키워드].md`를 만든 뒤 같은 파이프라인을 실행한다. UI·CLI·상태·job key·resume·recover·외부 쓰기 판단에 사용자 선택형 `mode`를 노출하거나 사용하지 않는다. `keyword`와 `auto_topic`이 함께 있거나 둘 다 없으면 실행하지 않고 종료 코드 `2`를 반환한다.

```text
uv run python -m tools.automation_runner run daily-generate --keyword <keyword>
uv run python -m tools.automation_runner run daily-generate --auto-topic
uv run python -m tools.automation_runner run weekly-improve
uv run python -m tools.automation_runner run naver-publish --run-id <run_id> --dry-run
```

이 네 명령이 현재 공용 CLI 계약이다. 주제 입력 방식 외에 실행 경로를 나누는 선택값은 받지 않는다.

## 3. 단일 실행 경로

```text
topic-selector → researcher → writer → image-maker → content-assembler
→ Q1 + canonical manifest → notion-rider → Q2 → naver-rider
→ 명시적 사용자 확인 → 네이버 임시저장
```

- `topic-selector`부터 `content-assembler`까지는 각 단계의 Markdown 계약과 산출물이 있어야 다음 단계로 넘어간다.
- Q1 실패 시 Notion 쓰기를 시작하지 않는다.
- Q2 실패 시 네이버 에디터를 변경하지 않는다.
- Q2 통과 후 네이버 입력은 한 실행에서 항상 같은 경로로 진행하고, 임시저장 버튼 직전의 명시적 사용자 확인이 없으면 저장하지 않는다.
- Notion과 네이버는 신규 생성만 허용한다. 기존 페이지·글의 수정·복제·이동·삭제는 지원하지 않는다.

## 4. 역할과 권한

| 역할 | 할 수 있는 일 | 할 수 없는 일 |
|---|---|---|
| 운영자 | 직접 입력/자동 선정 선택, 상태 조회, 재개·복구 요청, 네이버 임시저장 직전 확인 | 검증 실패를 강제 성공 처리, 기존 외부 항목 변경 |
| topic-selector | 선택 방식에 맞는 주제 선정 artifact 생성 | 사용자 제공 주제를 자동 선정 결과로 대체 |
| researcher | 근거·시각 자산 원장 작성 | 원문 없는 사실·수치 채택 |
| writer | 근거 기반 초안 작성 | 리서치 밖 주장 작성 |
| image-maker | 계약에 맞는 자산·연결표·썸네일 작성 | 공식 화면·문서·로고를 생성 이미지로 대체 |
| content-assembler | final 파일·Q1·manifest 생성 | 본문 사실·문체 재작성 |
| notion-rider | 지정 데이터 소스에 첨부·새 페이지 생성 및 Q2 재조회 | 기존 페이지 수정·복제·이동·삭제 |
| naver-rider | Q2 통과 글을 새 글에 입력하고 확인 후 임시저장 | 발행·예약·공개 설정 변경·기존 글 덮어쓰기 |

## 5. 실행 계약

### FR-01. 단계 실행과 결과

- 실행기는 실제 담당 에이전트를 호출하고, 단계별 입력·출력·상태·시도·안전한 오류를 `runs/<run_id>.jsonl`과 상태 파일에 기록한다.
- 필수 단계가 `not_called`·`skipped`·빈 산출물·Schema 불일치이면 실행은 실패한다.
- 중간 단계 실패 후 이후 담당 에이전트 호출 수는 0이어야 한다.
- 재개·복구는 이미 통과한 단계나 완료된 외부 쓰기를 다시 실행하지 않는다.

### FR-02. Q1과 manifest

- `content-assembler`는 final 본문, 네이버 파생본, image-map, 본문 이미지, 썸네일의 canonical manifest와 `artifact_digest`를 만든다.
- canonical 순서는 `final → naver-layout → naver-copy → naver-input → image-map → 본문 이미지 → thumbnail`이며, 화면·Notion 본문은 썸네일을 첫 이미지 블록으로 둔다.
- 파일 byte 크기·SHA-256·`artifact_digest`가 바뀌면 Q1을 다시 수행한다.
- `gate notion_write`와 `gate naver_draft_save`는 승인 단계가 아니라 외부 쓰기 종류 식별자다.

### FR-03. Notion Q2

- Q1, manifest, 실행 로그, `notion-config.md`의 데이터 소스 ID, 실제 생성 부모의 데이터 소스 ID가 모두 일치할 때만 첨부 생성과 신규 페이지 생성을 시작한다.
- 저장 후 새 페이지를 다시 열어 제목·본문·목록·표·링크·이미지·첫 이미지 썸네일·첨부·중복 `run_id`·`notion_roundtrip_digest`를 확인한다.
- Q2 성공 상태는 `ready_for_naver`다. Q2 실패는 `storage_integrity=failed`이며 네이버 입력을 차단한다.

### FR-04. 네이버 입력과 임시저장

- `naver-rider`는 Q2 결과, Notion 페이지 ID·검증 시각·round-trip digest, 대상 블로그 ID, 현재 artifact digest, `naver-input.md`를 대조한 뒤에만 새 글을 입력한다.
- 모바일 에디터를 확인한 뒤 제목·본문·이미지·대표 이미지를 입력한다. 대표 이미지는 전용 썸네일이며 발행은 하지 않는다.
- 임시저장 직전에는 대상 블로그·제목·업로드 이미지·저장 동작에 대한 명시적 사용자 확인을 받는다. 확인 전 임시저장 호출 수는 0, 일치하는 확인 후 호출 수는 정확히 1이다.

### FR-05. 상태와 식별자

| 필드 | 형식 | 규칙 |
|---|---|---|
| `run_id` | string | 실행 시도마다 전역 고유인 안전한 경로 구성요소 |
| `job_key` | SHA-256 | 논리 작업 중복 방지 키. 모드 값으로 분기하지 않음 |
| `job` | enum | `daily-generate`, `naver-publish`, `weekly-improve` |
| `topic_source` | enum | `user_defined` 또는 `auto_selected`; 출처 기록 전용이며 라우팅에는 사용하지 않음 |
| `topic_id` | string | 주제 추적 식별자 |
| `keyword` | string/null | 자동 선정 전에는 null 가능 |
| `pipeline_version` | string | `workflow-optimized-v1` 고정 |
| `status` | RunStatus | 아래 상태 집합 |
| `artifact_digest` | SHA-256/null | Q1 이후 필수 |
| `notion_page_id` | string/null | Q2 이후 필수 |

실행 상태는 `pending → running → ready_for_naver → awaiting_user_confirmation → draft_saved`를 사용한다. 어느 단계에서나 `failed` 또는 `blocked`로 전이할 수 있고, 외부 어댑터 없이 끝난 dry-run에만 `local-only`를 사용한다.

### FR-06. Notion 호환·감사 값

기존 Notion 데이터베이스·데이터 소스 ID·속성·과거 행·과거 보기는 변경하지 않는다. 기존 `모드` select 속성은 감사 호환을 위해 남기고, 신규 페이지에는 고정값 `정식`만 기록한다. 이 값은 사용자 입력, CLI, UI, 상태 전이, job key 또는 외부 쓰기 판단에 사용하지 않는다.

### FR-07. 병렬 경계

- researcher는 `research-official`, `research-supporting-visual`의 읽기 전용 Lane을 최대 둘까지 사용할 수 있다.
- image-maker는 출처·권리 검증, 생성 브리프, 후보 적합성의 준비 Lane을 최대 둘까지 사용할 수 있다.
- writer, content-assembler, Notion 생성·업로드·재조회, 네이버 입력·업로드·임시저장은 직렬이다.
- Lane은 정식 산출물·공유 로그를 직접 수정하지 않으며 주 담당자만 병합·기록·최종 판정을 한다.

## 6. 보안과 관찰 가능성

- `.codex/hooks.json`의 `PreToolUse` Hook은 외부 쓰기 전에 manifest·run log·run ID·target ID·쓰기 종류를 검증한다. 필요한 환경 값이 하나라도 없으면 쓰기를 차단한다.
- 인증 토큰·쿠키·개인정보·본문 전체는 로그에 기록하지 않는다.
- 원시 이벤트는 `runs/*.jsonl`, 운영 요약은 Notion 속성에 기록한다. Q1·Q2·Q3은 서로 다른 판정이며 하나의 점수로 합치지 않는다.
- 기존 로그는 읽기 전용으로 보존하고, 과거 상태 해석이 불가능하면 임의로 성공 처리하지 않는다.

## 7. 수용 기준

1. 직접 입력과 자동 선정이 각각 `topic-selector`에서 같은 주제 선정 artifact로 수렴하고, 이후 필수 단계와 Q1·Q2·네이버 대기 경로가 동일하다.
2. 두 입력 방식이 함께 있거나 모두 없거나 공용 계약에 없는 선택값이 들어오면 실행이 종료 코드 `2`로 거부된다.
3. Q1·manifest·대상 데이터 소스·실제 쓰기 대상 중 하나라도 불일치하면 Notion 쓰기 호출 수는 0이다.
4. Q2가 통과한 실행은 `ready_for_naver`가 되고, 사용자 확인 전 네이버 임시저장 호출 수는 0이다.
5. 일치하는 사용자 확인 후 새 임시저장 호출 수는 정확히 1이며, 발행·예약 발행 호출 수는 0이다.
6. 기존 Notion `모드` 속성의 과거 값·행·보기는 변경하지 않고, 신규 실행은 고정 감사값 `정식`만 남긴다.
7. `uv run pytest -q`, `uv run ruff check .`, `uv run basedpyright`, `python3 -m compileall -q tools tests`, `plutil -lint launchd/*.plist`가 성공한다.
