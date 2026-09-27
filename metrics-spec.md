# 운영 워크플로우 성능 지표 사양

## 목적

단일 운영 워크플로우의 처리량·지연·실패·비용을 같은 방식으로 기록하고, Notion에서는 사람이 읽을 수 있는 요약을 확인한다.

신규 기록의 `pipeline_version`은 `workflow-optimized-v1`로 고정한다. 원시 JSONL은 Schema 계약을 따르고, 기존 로그는 덮어쓰지 않는다.

## 작업 단위

- `batch_id`: 실행 묶음 식별자. 단일 실행에도 안전한 값으로 기록할 수 있다.
- `batch_slot`: 과거 대시보드 자동 선정 배치의 순서(1, 2, 3). 이는 역사 로그의 읽기 호환 필드이며 신규 요청 계약을 뜻하지 않는다. 신규 자동·사용자 요청은 `dashboard_auto_envelope=one_daily_generate_child` 또는 `dashboard_user_envelope=one_daily_generate_child`로 일반 `daily-generate` 자식 1개만 접수한다. 배치에 속하지 않는 실행에는 기록하지 않는다.
- `run_id`: 글 한 편의 전체 파이프라인 실행 식별자
- `topic_id`: 주제 manifest의 고유 식별자
- `stage`: `topic-selector`, `researcher`, `writer`, `image-maker`, `content-assembler`, `notion-rider`, `naver-rider`
- 시간대: `Asia/Seoul`

Q1·Q2·Q3는 단계명이 아니라 판정 시점 필드다. Q1은 외부 저장 전 자동 품질·manifest, Q2는 Notion 저장 후 무결성·round-trip, Q3는 사람 검수·모바일 이미지 평가를 뜻한다. `quality_pass`와 `storage_integrity`와 `human_review`를 한 점수로 합치지 않는다.

## 필수 실행 이벤트

실행·Lane 이벤트는 `runs/*.jsonl`에 한 줄씩 저장한다. 한 실행 로그는 단일 작성자가 기록하며, Lane은 읽기·준비 결과만 반환하고 정식 산출물이나 공유 로그를 직접 수정하지 않는다.

```json
{"event_type":"stage","pipeline_version":"workflow-optimized-v1","batch_id":"ops-2026-08-25-01","run_id":"ops-0001","topic_id":"2026-01-001","stage":"researcher","parent_stage":null,"lane_id":null,"worker_role":"researcher","depends_on":[],"started_at":"2026-08-25T09:00:00+09:00","ended_at":"2026-08-25T09:02:00+09:00","status":"passed","attempt":1,"artifacts":[],"error_type":null,"error_message_safe":null}
```

stage·lane 이벤트 필수 필드:

- `event_type`: `stage` 또는 `lane`
- `pipeline_version`: 신규 이벤트는 `workflow-optimized-v1`
- `batch_id`, `run_id`, `topic_id`, `stage`
- `started_at`, `ended_at`
- `status`: `pending`, `running`, `passed`, `failed`, `blocked`, `skipped`
- `attempt`
- 실패 시 `error_type`과 `error_message_safe`

선택 필드:

- `parent_stage`, `lane_id`, `depends_on`, `worker_role`
- `artifacts`: 안전한 상대 경로와 SHA-256 해시를 포함한 산출물 목록
- 입력·출력 토큰 수
- 이미지 수·이미지 업로드 시간
- 외부 API 비용
- 생성 파일 경로와 해시
- Notion 페이지 ID

신규 단계 이벤트는 `duration_ms`, `attempt`, `error_type`, `error_message_safe`를 시도마다 기록한다. 오류가 분류되면 `retryable`, `next_action`, `retry_stage`, `process_attempts`도 함께 기록한다. 단계 모델 호출 로그는 다음 경로에 append-safe하게 보존한다.

```text
.automation/work/<run_id>/<stage>/attempt-<stage_attempt>/codex-attempt-<process_attempt>.jsonl
```

조회기는 위 경로와 기존 `<stage>/codex-attempt-*.jsonl`을 모두 읽는다. 같은 단계에 신규 경로와 기존 경로가 함께 있으면 신규 경로를 canonical 원본으로 선택해 토큰을 중복 합산하지 않는다. 토큰 집계 키는 `(run_id, stage, stage_attempt, process_attempt)`이며 `input_tokens + output_tokens`를 총량으로 삼고 `cached_input_tokens`는 입력의 부분집합으로 별도 표시한다. 유효 usage가 기록된 실패 호출도 합계에 포함한다.

오류 분류는 `rate_limited`, `process_timeout`, `temporary_io`, `process_unavailable`, `invalid_configuration`, `contract_failed`, `external_result_uncertain`를 사용한다. 429는 프로세스 계층에서 최대 3회 지수 대기하고, 생성 단계 시간 초과는 10초 후 1회만 추가 시도한다. 로컬 일시 I/O 오류는 실행기에서 1초 후 1회만 재시도한다. 실행 파일·설정·계약 오류와 결과 불명 외부 쓰기는 일반 자동 재시도를 하지 않는다. Q1 content-assembler는 이 정책과 무관하게 총 3회 상한을 유지한다.

비밀값·토큰·쿠키·개인정보·전체 인증 헤더는 기록하지 않는다.

## 외부 쓰기 검증 기록

Notion과 네이버 외부 쓰기는 별도 승인 이벤트를 기록하지 않는다. 실행 로그의 단계 이벤트에 `run_id`, `topic_id`, 대상 ID, `artifact_digest`, Q1·Q2 결과와 외부 쓰기 결과를 기록한다. 네이버 임시저장 직전 사용자 확인은 별도의 `confirmation` 이벤트로 기록하며 토큰·쿠키·인증정보와 불필요한 개인정보를 포함하지 않는다.

## 기존 로그 호환

기존 `runs/*.jsonl`은 수정하지 않는다. 조회·집계할 때만 과거 상태를 신규 상태로 해석한다.

- `success`, `completed` → `passed`
- `not-run` → `skipped`
- 매핑되지 않는 값은 임의로 성공으로 바꾸지 않고 원래 값과 호환 불가 상태를 함께 보고한다.

## 지표 정의

| 지표 | 정의 |
|---|---|
| 단계 처리시간 | 해당 단계의 `ended_at - started_at` |
| 전체 처리시간 | 첫 단계 시작부터 Notion 페이지 재조회 완료까지 |
| 성공률 | `passed` 작업 수 ÷ 시작된 작업 수 |
| 실패율 | `failed` 작업 수 ÷ 시작된 작업 수 |
| 재시도율 | 2회 이상 시도한 작업 수 ÷ 시작된 작업 수 |
| 처리량 | 측정 구간의 완료 작업 수 ÷ 시간 |
| P50/P95/P99 | 완료 작업 처리시간의 해당 백분위수 |
| Notion 저장 성공률 | 페이지 생성 후 재조회까지 성공한 수 ÷ 저장 시도 수 |
| 품질 통과율 | `품질 점수≥85`이고 즉시 실패 조건이 없는 수 ÷ 평가 완료 수 |

## 이미지 생성·검수 원시 기록

생성 이미지마다 `generation_provider`, `generation_model`, `generation_snapshot`, `generation_control`, `quality`, `size`, `prompt_template_version`, `prompt_sha256`, `reference_sha256`, `output_sha256`, `output_path`, `generated_at`, `provenance_status`를 기록한다. 신규 레코드는 실제 제작 방식에 따라 `production_method=ai_generation` 또는 `production_method=local_render`를 추가한다. `output_path`는 metadata JSONL 기준 안전한 상대 경로이며 검증기가 실제 파일 byte의 `output_sha256`를 다시 계산한다. 고정 profile을 실제 호출에 전달하지 못한 결과는 `generation_control=unlocked`이며 운영 품질 Gate를 통과할 수 없다. seed는 기록하거나 재현성 근거로 사용하지 않는다.

`local_render` 레코드는 `generation_provider=pillow`, `generation_model=not_applicable`, `generation_snapshot=pillow-<renderer-version>`, `renderer_version`, `renderer_sha256`, `input_sha256`를 실제 실행과 연결해 기록한다. `ai_generation` 레코드는 실제 provider·model·snapshot과 호출 증거가 출력 해시와 연결된 경우에만 운영 근거로 인정한다. 기존 `production_method` 누락 레코드는 역사 기록으로 읽되 신규 제작의 신뢰 근거로 자동 승격하지 않는다. 구현 결과와 현재 검증 한계는 `docs/task-7-image-production-result.md`와 실행별 evidence artifact에 기록한다.

image-maker의 산출물·자동 검사 결과는 `assets/[키워드]/image-quality.jsonl`에 기록한다. Q2 이후의 사람 이미지 검수는 `metadata/quality-reviews/<run_id>-images.jsonl`에 별도 기록해 run ID와 글 평가 digest에 연결하고, Q2 이후 검수 시각을 보존한다. 사람 평가 항목은 5개 점수 필드로 0~4점씩 기록하며 총점 16/20 이상, 개별 3점 미만 없음, 즉시 실패 없음, `mobile_viewport=390x844`의 실제 `mobile_render_path`와 `mobile_render_sha256` 재검증을 모두 만족해야 한다.

## Notion 기록 매핑

글별 요약은 다음 속성에 기록한다.

- `실행 ID` ← `run_id`
- `주제 ID` ← `topic_id`
- `배치 ID` ← `batch_id`
- `파이프라인 버전` ← 실행에 사용한 지침 버전
- `전체 처리 시간(초)` ← 전체 처리시간
- `재시도 횟수` ← 전체 시도 횟수 - 1
- `실패 단계` ← 최초 실패 단계, 없으면 `없음`
- `품질 점수` ← evaluation-rubric 최종 점수
- `성능 판정` ← 성능 기준에 따른 `대기`, `통과`, `경고`, `실패`

단계별 상세 시간과 원시 이벤트는 Notion 본문에 반복해서 넣지 않고 `runs/*.jsonl`에서 관리한다. 운영 요약 보고서에는 집계값만 넣는다.

## 성능 판정

- `대기`: 측정값 또는 평가가 아직 없음
- `통과`: 기준선 대비 허용 범위이며 중대한 오류가 없음
- `경고`: P95 급증·재시도 증가·특정 단계 병목이 있으나 데이터 무결성은 유지됨
- `실패`: 데이터 손상·중복 저장·재조회 실패·중대한 보안 또는 출처 오류

절대 시간 기준은 Gate 0에서 기준선을 확보한 뒤 정한다. 기준선 대비 대표군 P95가 1.5배를 넘으면 원인을 분석하고 다음 Gate 진행을 보류한다.
