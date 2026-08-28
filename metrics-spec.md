# 베타 성능 지표 사양

## 목적

대량 베타 작업의 처리량·지연·실패·비용을 같은 방식으로 기록하고, Notion에서는 사람이 읽을 수 있는 요약을 확인한다.

신규 기록의 `pipeline_version`은 `workflow-optimized-v1`로 고정한다. 원시 JSONL은 Schema 계약을 따르고, 기존 로그는 덮어쓰지 않는다.

## 작업 단위

- `batch_id`: 하나의 베타 배치 식별자
- `run_id`: 글 한 편의 전체 파이프라인 실행 식별자
- `topic_id`: 주제 manifest의 고유 식별자
- `stage`: `topic-selector`, `researcher`, `writer`, `image-maker`, `content-assembler`, `notion-rider`, `naver-rider`
- 시간대: `Asia/Seoul`

Q1·Q2·Q3는 단계명이 아니라 판정 시점 필드다. Q1은 외부 저장 전 자동 품질·manifest, Q2는 Notion 저장 후 무결성·round-trip, Q3는 사람 검수·모바일 이미지 평가를 뜻한다. `quality_pass`와 `storage_integrity`와 `human_review`를 한 점수로 합치지 않는다.

## 필수 실행 이벤트

실행·Lane 이벤트는 `runs/*.jsonl`에 한 줄씩 저장한다. 한 실행 로그는 단일 작성자가 기록하며, Lane은 읽기·준비 결과만 반환하고 정식 산출물이나 공유 로그를 직접 수정하지 않는다.

```json
{"event_type":"stage","pipeline_version":"workflow-optimized-v1","batch_id":"beta-2026-08-25-01","run_id":"beta-0001","topic_id":"2026-01-001","stage":"researcher","parent_stage":null,"lane_id":null,"worker_role":"researcher","depends_on":[],"started_at":"2026-08-25T09:00:00+09:00","ended_at":"2026-08-25T09:02:00+09:00","status":"passed","attempt":1,"artifacts":[],"error_type":null,"error_message_safe":null}
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

비밀값·토큰·쿠키·개인정보·전체 인증 헤더는 기록하지 않는다.

## 승인 이벤트

승인 이벤트도 같은 `runs/<run_id>.jsonl`에 기록하며, 실행별 로그의 단일 작성자가 기록한다. 승인 이벤트는 단계 성공 이벤트로 대체하지 않는다. 정식 모드 Gate A와 Gate B에만 기록하며, 베타 Notion 저장은 승인 이벤트를 만들지 않는다.

승인 이벤트 필수 필드는 `event_type`, `pipeline_version`, `run_id`, `gate`, `decision`, `scope`, `target_id`, `artifact_digest`, `requested_at`, `decided_at`이다. `batch_id`, `topic_id`, `stage`, `status`, `attempt`은 실행 맥락에 필요할 때 추가한다.

    {
      "event_type": "approval",
      "pipeline_version": "workflow-optimized-v1",
      "run_id": "RUN-YYYYMMDD-HHMMSS",
      "gate": "notion_write",
      "decision": "approved",
      "scope": "per-run",
      "target_id": "notion-data-source-id",
      "artifact_digest": "sha256:...",
      "requested_at": "2026-08-26T10:00:00+09:00",
      "decided_at": "2026-08-26T10:05:00+09:00"
    }

- `gate`: `notion_write` 또는 `naver_draft_save`
- `decision`: `approved`, `rejected`, `expired`
- `scope`: `per-run` 또는 `batch`
- `artifact_digest`: 최종 Markdown, image-map.md, 참조 이미지의 고정 순서 manifest를 SHA-256으로 계산한 값
- canonical 순서: `final Markdown → naver-layout → naver-copy → image-map → 본문 이미지 등장 순서 → thumbnail`
- 화면·복사 원본·Notion 본문 이미지 순서: 전용 썸네일을 첫 번째 이미지 블록으로 고정하고 이후 본문 이미지 순서를 따른다. canonical 순서는 승인·해시 계산용이므로 이 배치 순서와 다르다.
- 개별 artifact의 `size_bytes`와 raw-byte SHA-256을 manifest에 기록한다. `artifact_digest`는 자기 필드를 제외한 canonical JSON의 UTF-8·정렬 key·무공백 SHA-256이다.
- Gate B 승인에는 `notion_page_id`, `notion_last_verified_at`, `blog_id`를 추가하고, 승인 대상과 현재 값이 일치해야 한다.
- Gate B 승인에는 `notion_roundtrip_digest`를 추가하고 현재 manifest digest와 일치시킨다.
- 배치 승인에는 정확한 실행 목록, `max_items`, 만료 시각을 추가한다. 실행 목록·대상·해시가 달라지면 승인하지 않는다.
- 승인 기록에는 토큰·쿠키·인증정보와 불필요한 개인정보를 넣지 않는다.

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

생성 이미지마다 `generation_provider`, `generation_model`, `generation_snapshot`, `generation_control`, `quality`, `size`, `prompt_template_version`, `prompt_sha256`, `reference_sha256`, `output_sha256`, `output_path`, `generated_at`, `provenance_status`를 기록한다. `output_path`는 metadata JSONL 기준 안전한 상대 경로이며 검증기가 실제 파일 byte의 `output_sha256`를 다시 계산한다. `gpt-image-2-2026-04-21`·`high`·고정 profile을 실제 호출에 전달하지 못한 결과는 `generation_control=unlocked`이며 정식 품질 통과가 아니다. seed는 기록하거나 재현성 근거로 사용하지 않는다.

자동 검수 결과와 사람 검수 결과는 `assets/[키워드]/image-quality.jsonl`에 원시값으로 남긴다. 사람 평가 항목은 계획서의 5개 점수 필드로 0~4점씩 기록하며 총점 16/20 이상, 개별 3점 미만 없음, 즉시 실패 없음, `mobile_viewport=390x844`의 실제 `mobile_render_path`와 `mobile_render_sha256` 재검증을 모두 만족해야 한다.

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

단계별 상세 시간과 원시 이벤트는 Notion 본문에 반복해서 넣지 않고 `runs/*.jsonl`에서 관리한다. 베타 요약 보고서에는 집계값만 넣는다.

## 성능 판정

- `대기`: 측정값 또는 평가가 아직 없음
- `통과`: 기준선 대비 허용 범위이며 중대한 오류가 없음
- `경고`: P95 급증·재시도 증가·특정 단계 병목이 있으나 데이터 무결성은 유지됨
- `실패`: 데이터 손상·중복 저장·재조회 실패·중대한 보안 또는 출처 오류

절대 시간 기준은 Gate 0에서 기준선을 확보한 뒤 정한다. 기준선 대비 대표군 P95가 1.5배를 넘으면 원인을 분석하고 다음 Gate 진행을 보류한다.
