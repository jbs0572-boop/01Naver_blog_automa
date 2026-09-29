# `topic-selector.md` 개선 상세 구현 계획

## 1. 계획 목적

네이버 블로그 자동화 시스템의 주제 선정 단계를 다음 계약으로 개편한다.

- 실행 방식은 `지정 주제`와 `자동 주제 선정` 두 가지를 유지한다.
- 두 방식 모두 사용자가 `KST 기준일`을 입력한다.
- 분야·주요 독자·발행목적은 입력받지 않는다.
- 지정 주제는 사용자가 입력한 키워드를 보존한다.
- 자동 선정은 Aside Browser로 네이버 Creator Advisor를 읽고, 제공된 트렌드 키워드 중 하나를 선택한다.
- Creator Advisor에서 확인하지 못한 키워드·수치·순위는 생성하거나 추정하지 않는다.
- Creator Advisor 원시 스냅샷과 선정 결과를 로컬 메타데이터에 누적한다.
- 초기에는 설명 가능한 규칙 기반 점수를 사용하고, 실제 유입 데이터가 누적된 뒤 가중치를 보정한다.

이 문서는 구현 순서와 완료 조건만 정의한다. 기존 활성 지침·코드·스키마는 이 계획 실행 전까지 변경하지 않는다.

## 2. 최종 사용자 동작

### 2.1 지정 주제

사용자 입력:

```json
{
  "keyword": "사용자 지정 키워드",
  "as_of_date": "2026-09-01"
}
```

처리:

1. `as_of_date`를 KST 날짜로 검증한다.
2. 키워드의 오탈자·중복·검색 의도·조사 가능성을 확인한다.
3. 입력 키워드를 다른 키워드로 교체하지 않는다.
4. 문제가 있으면 원본 키워드와 보류 사유를 기록한다.

### 2.2 자동 주제 선정

사용자 입력:

```json
{
  "auto_topic": true,
  "as_of_date": "2026-09-01"
}
```

처리:

1. `as_of_date`를 KST 날짜로 검증한다.
2. Aside Browser의 지정 프로필과 Creator Advisor 로그인 상태를 점검한다.
3. Creator Advisor 트렌드 화면에서 기준일에 조회 가능한 후보를 수집한다.
4. 후보별 원시 지표를 저장한다.
5. 결측·중복·기준일 위반 후보를 제외한다.
6. 데이터 축적 단계에 맞는 점수를 계산한다.
7. 최고점 키워드 하나를 최종 주제로 확정한다.

## 3. 범위와 비범위

### 구현 범위

- `topic-selector.md` 역할·입력·자동 선정·결과·점검 계약 개정
- 대시보드와 CLI의 `as_of_date` 입력 계약 통일
- Aside Browser 기반 Creator Advisor 읽기 절차 정의
- 원시 스냅샷과 파생 점수 메타데이터 저장
- 초기 점수 함수와 데이터 축적 단계별 선정 규칙
- 상태·로그·스키마·테스트 정합성 확보
- 기존 파이프라인의 researcher 이후 단계와 연결

### 구현하지 않는 범위

- Creator Advisor 값을 절대 검색량으로 환산
- Creator Advisor에 표시되지 않은 키워드 생성
- 초기 데이터만으로 DeepAR·TFT 같은 학습 모델 도입
- 지정 주제를 자동 선정 결과로 대체
- Q1·Q2·Notion·네이버 임시저장 Gate 변경
- 네이버 발행·예약 발행·공개 설정 변경

## 4. 핵심 데이터 계약

### 4.1 공통 주제 입력

```text
topic_source: user_defined | auto_selected
keyword: string | null
as_of_date: YYYY-MM-DD
timezone: Asia/Seoul
```

검증 규칙:

- `user_defined`: `keyword`와 `as_of_date` 필수
- `auto_selected`: 실행 전 `keyword=null`, `as_of_date` 필수
- `keyword`와 `auto_topic=true` 동시 입력 금지
- `as_of_date`는 유효한 달력 날짜여야 한다.
- 실행·조사 자료는 `as_of_date` 이후 정보를 선정 근거로 사용하지 않는다.
- `timezone`은 내부 고정값이며 사용자 선택값으로 노출하지 않는다.

### 4.2 Creator Advisor 원시 스냅샷

신규 파일 권장 경로:

```text
metadata/creator-advisor/YYYY-MM-DD/<capture_id>.json
```

필수 필드:

```json
{
  "schema_version": 1,
  "capture_id": "CAP-...",
  "as_of_date": "2026-09-01",
  "captured_at": "2026-09-01T10:30:00+09:00",
  "timezone": "Asia/Seoul",
  "source_url": "https://creator-advisor.naver.com/naver_blog/sola_note/trends",
  "channel_id": "redacted-or-stable-id",
  "query_period": {
    "start": "2026-08-26",
    "end": "2026-09-01",
    "unit": "day"
  },
  "candidates": [],
  "access_status": "ok",
  "limitations": []
}
```

후보 필드:

```json
{
  "keyword": "후보 키워드",
  "candidate_rank": 1,
  "trend_index": 100.0,
  "channel_inflow": null,
  "exposure": null,
  "average_exposure_rank": null,
  "competition_index": null,
  "duplicate_key": "normalized-key",
  "raw_observation": "화면에서 확인한 의미",
  "missing_fields": []
}
```

저장 규칙:

- 원시 스냅샷은 append-only로 저장한다.
- 같은 날짜를 다시 조회해도 기존 파일을 덮어쓰지 않는다.
- 인증 토큰·쿠키·개인정보는 저장하지 않는다.
- 화면에 없는 값은 `null`과 `missing_fields`로 기록한다.
- 상대지수는 원래 단위 그대로 보존한다.

### 4.3 선정 결과 메타데이터

`research/topic-selection-[키워드].md`에 다음 필드를 추가한다.

```text
topic_source
keyword
as_of_date
timezone
capture_id
source_url
captured_at
query_period
candidate_count
selection_stage
score_version
score_components
final_score
selection_reason
excluded_candidates
duplicate_check
data_freshness
limitations
```

## 5. Aside Browser 수집 계약

### 5.1 사전 점검

1. Aside Browser 실행 가능 여부를 확인한다.
2. 전용 계정 프로필을 명시적으로 선택한다.
3. Creator Advisor 로그인 상태를 확인한다.
4. 대상 블로그 채널이 올바른지 확인한다.
5. 트렌드 화면 URL과 기준 레이블을 확인한다.
6. 실패하면 `creator_advisor_preflight_failed`로 종료한다.

### 5.2 세션 규칙

- 자동 선정 실행마다 하나의 Aside 세션을 사용한다.
- 동시 자동 선정은 브라우저 잠금으로 직렬화한다.
- 이전 실행의 주제·기간 필터를 신뢰하지 않는다.
- 매 실행에서 채널·기간·기준일을 다시 확인한다.
- 브라우저 작업은 읽기 전용으로 제한한다.
- 페이지 내부 저장·설정 변경·다운로드는 수행하지 않는다.

### 5.3 수집 순서

```text
직접 URL 접속
→ 로그인·채널 확인
→ 기준일·조회 가능 기간 확인
→ 인기 유입 검색어 후보 수집
→ 후보 순위와 상대지수 수집
→ 상위 후보 상세 통계 조회
→ 원시 스냅샷 작성
→ 구조 검증
```

### 5.4 실패 규칙

- 로그인 실패: 즉시 중단
- 대상 채널 불일치: 즉시 중단
- 기준일 조회 불가: 즉시 중단
- 일시적 로딩·네트워크 오류: 동일 세션에서 1회만 재시도
- UI 구조 변경: `creator_advisor_ui_changed`로 중단
- 후보 0개: `no_eligible_candidates`로 중단
- 화면에 없는 지표: 해당 지표만 결측 처리
- 전체 후보 점수 계산 불가: 임의 키워드를 만들지 않고 중단

## 6. 키워드 선정 규칙

### 6.1 후보 제외 순서

점수 계산 전에 다음 순서로 제외한다.

1. 기준일 이후 자료에 의존하는 후보
2. 빈 키워드 또는 파싱 실패 후보
3. 기존 산출물·Notion 행과 `duplicate_key`가 같은 후보
4. 신뢰 가능한 순위 또는 트렌드 지표가 전혀 없는 후보
5. 데이터 신선도 제한을 초과한 후보

### 6.2 데이터 축적 단계

#### 단계 A: 유효 스냅샷 1~6일

```text
final_score = 0.70 * normalized_candidate_rank
            + 0.30 * normalized_current_trend
```

- 현재 Creator Advisor 순위를 우선한다.
- 상승률·지속성·예측값은 계산하지 않는다.

#### 단계 B: 유효 스냅샷 7~27일

```text
final_score = 0.30 * current_trend
            + 0.25 * surge
            + 0.20 * momentum
            + 0.15 * persistence
            + 0.10 * channel_capture
```

- 동일 조회 기간과 단위의 스냅샷만 연결한다.
- `channel_capture`가 없으면 나머지 가중치를 합계 1로 재정규화한다.

#### 단계 C: 유효 스냅샷 28일 이상

```text
final_score = 0.25 * surge
            + 0.20 * momentum
            + 0.20 * persistence
            + 0.20 * forecast
            + 0.15 * channel_capture
            - 0.10 * volatility_penalty
```

- 28일 이동중앙값과 MAD를 기준선으로 사용한다.
- 예측 모델의 출력이 단순 기준선보다 검증 성능이 좋을 때만 `forecast`를 활성화한다.

### 6.3 점수 요소

```text
surge = 현재 상대지수와 28일 이동중앙값 차이의 robust z-score
momentum = 최근 7개 유효 관측치의 로그 상대지수 기울기
persistence = 최근 7개 관측 중 기준선 초과 비율
forecast = 게시 예상 시점의 예측 중앙값 정규화 점수
channel_capture = 유입과 노출을 함께 고려한 채널 포착 효율
volatility_penalty = 잔차 MAD 기반 단기 변동성 감점
```

### 6.4 동점 처리

다음 순서로 결정한다.

1. 데이터 신선도가 높은 후보
2. 상승 지속성이 높은 후보
3. 변동성이 낮은 후보
4. Creator Advisor 원래 순위가 높은 후보
5. 키워드 문자열 오름차순

### 6.5 실제 유입 기반 개선

선정·게시된 키워드에 다음 결과를 연결한다.

```text
published_at
search_inflow_7d
search_inflow_28d
exposure_7d
exposure_28d
average_exposure_rank_7d
average_exposure_rank_28d
```

- 게시 결과 30건 미만: 초기 가중치 유지
- 30건 이상: rolling-origin 검증으로 가중치 후보 비교
- 새 가중치는 기존 기준선보다 게시 후 7일·28일 유입 평가가 모두 개선될 때만 채택
- 모델 버전과 평가 기간을 모든 선정 결과에 기록

## 7. `topic-selector.md` 개정 순서

1. `역할`에 두 입력 방식과 Creator Advisor 책임을 명시한다.
2. `입력`을 `keyword/auto_topic + as_of_date` 계약으로 변경한다.
3. `시작 방식과 주제 보존`에서 지정 주제 보존 규칙을 분리한다.
4. `Creator Advisor 자동 선정` 장을 신설한다.
5. `Aside Browser 수집 계약`을 추가한다.
6. `로컬 메타데이터` 장을 추가한다.
7. `후보 제외 규칙`을 추가한다.
8. `데이터 축적 단계별 점수`를 추가한다.
9. `실패·중단 조건`을 구체적인 오류 코드로 정리한다.
10. `결과`에 capture·score·freshness 필드를 추가한다.
11. `저장 전 점검`을 새 입력·수집·점수·중복 계약에 맞게 교체한다.
12. 기존 총정리·독창성·시각 계약은 선정 이후 설계 규칙으로 유지한다.

## 8. 파일별 구현 작업

| 순서 | 파일 | 작업 |
|---:|---|---|
| 1 | `topic-selector.md` | 본 계획의 입력·수집·선정·실패·결과 계약 반영 |
| 2 | `EXECUTION_AGENT.md` | 두 입력 방식 모두 KST 기준일 필수, 자동 선정만 Creator Advisor 사용하도록 정합화 |
| 3 | `tools/runner_types.py` | 기존 분야·독자·발행목적 문맥을 제거하고 기준일 중심 타입으로 변경 |
| 4 | `tools/dashboard_manual_request.py` | 두 입력 payload 파싱과 KST 날짜 검증 변경 |
| 5 | `tools/runner_cli.py` | 지정·자동 실행에서 `--as-of-date`를 필수 옵션으로 처리 |
| 6 | `dashboard/index.html` | 분야·독자·발행목적 제거, 두 방식 모두 기준일 표시 |
| 7 | `dashboard/app.js` | 방식별 payload를 새 계약으로 전송 |
| 8 | Creator Advisor 수집 모듈 | Aside 세션·사전 점검·원시 스냅샷 생성 구현 |
| 9 | 키워드 점수 모듈 | 단계 A/B/C 점수와 동점 처리 구현 |
| 10 | `tools/runner_state.py`·`tools/runner_records.py` | 기준일·capture ID·점수 버전·실패 코드 기록 |
| 11 | `schemas/workflow-contract.schema.json` | 신규 필드와 허용 상태를 추가하되 기존 역사 기록은 계속 읽을 수 있게 유지 |
| 12 | 테스트 | 입력·브라우저·메타데이터·점수·실패·E2E 시나리오 추가 |
| 13 | `DESIGN.md`·`test-dashboard.md` | 최종 UI·API 계약과 실행 방법 문서화 |

## 9. 테스트 우선 구현 순서

### 9.1 입력 계약 테스트

- 지정 주제에 키워드와 기준일이 있으면 통과
- 자동 선정에 기준일이 있으면 통과
- 분야·독자·발행목적이 들어오면 비정규 입력으로 거부
- 두 주제 방식이 동시에 들어오면 거부
- 기준일 누락·잘못된 날짜·시간대 변조를 거부

### 9.2 스냅샷 테스트

- 화면 관측값이 원시 JSON 필드에 손실 없이 저장됨
- 결측값이 0으로 변환되지 않고 `null`로 저장됨
- 같은 날짜 재수집이 기존 파일을 덮어쓰지 않음
- 쿠키·토큰·개인정보가 저장되지 않음
- 조회 기간과 기준일이 다르면 점수 계산을 거부

### 9.3 점수 테스트

- 1~6일은 단계 A만 사용
- 7~27일은 단계 B로 전환
- 28일 이상은 검증된 예측값이 있을 때만 단계 C 사용
- 결측 요소 가중치가 올바르게 재정규화됨
- 동점 규칙이 항상 같은 결과를 반환
- 중복 후보가 점수 계산 전에 제외됨

### 9.4 Aside Browser 어댑터 테스트

- 로그인 실패 시 사전 점검에서 중단
- 잘못된 채널이면 중단
- UI 레이블 누락 시 구조 변경 오류 반환
- 일시적 로딩 실패만 1회 재시도
- 후보와 상세 통계 수집은 읽기 전용 동작만 수행

### 9.5 파이프라인 E2E 테스트

1. 지정 주제 + 기준일 → 원래 키워드가 선정 파일에 유지
2. 자동 선정 + 기준일 → Creator Advisor 후보 중 하나만 선택
3. 자동 선정 데이터 부족 → 임의 키워드 없이 단계 중단
4. 중복 후보만 존재 → `duplicate` 또는 `no_eligible_candidates`로 중단
5. 선정 성공 → researcher가 동일 키워드와 기준일을 전달받음

## 10. 마이그레이션과 호환성

- 기존 Notion 행·실행 로그·manifest는 수정하거나 삭제하지 않는다.
- 과거 `selection_context`의 분야·독자·발행목적 필드는 읽기 전용 역사 데이터로만 허용한다.
- 신규 실행은 새 입력 계약만 생성한다.
- 상태 복구 코드는 과거 형식과 신규 형식을 모두 읽되, 신규 실행에 과거 필드를 다시 기록하지 않는다.
- `score_version`이 없는 과거 주제 선정 결과는 `legacy-unscored`로 해석한다.
- 메타데이터 스키마 변경 시 `schema_version`을 올리고 기존 스냅샷을 덮어쓰지 않는다.

## 11. 단계별 완료 조건

### Gate A: 계약 확정

- `topic-selector.md`와 `EXECUTION_AGENT.md`의 입력 방식·기준일·자동 선정 출처가 일치한다.
- 분야·독자·발행목적이 신규 입력 계약에서 제거된다.

### Gate B: 수집 안정성

- 동일 조건으로 최소 2회 Creator Advisor를 조회한다.
- 두 실행 모두 후보·기간·확인 시각·출처를 구조화한다.
- 잘못된 채널·로그아웃·UI 변경을 정상 성공으로 처리하지 않는다.

### Gate C: 선정 재현성

- 같은 스냅샷과 점수 버전은 항상 같은 키워드를 선택한다.
- 모든 점수 요소를 원시값으로 역추적할 수 있다.
- 결측·중복·기준일 위반 후보가 선정되지 않는다.

### Gate D: 파이프라인 연결

- 선정된 키워드가 researcher 이후 단계에 변경 없이 전달된다.
- 기존 Q1·Q2·Notion·네이버 확인 Gate가 유지된다.
- 신규 필드가 manifest와 실행 로그 검증을 통과한다.

### Gate E: 운영 준비

- 대시보드에서 두 실행 방식을 정상 제출할 수 있다.
- 자동 선정의 브라우저 동시 실행이 직렬화된다.
- 실패 상태와 필요한 조치가 운영 화면에 안전하게 표시된다.

## 12. 최종 인수 기준

- 지정 주제는 `keyword + KST 기준일`만 입력받는다.
- 자동 선정은 `KST 기준일`만 입력받는다.
- 분야·주요 독자·발행목적 입력이 UI·API·CLI 신규 계약에서 제거된다.
- 자동 선정 키워드는 Creator Advisor에서 실제 확인한 후보여야 한다.
- 원시 스냅샷 없이 자동 선정 성공 상태를 만들 수 없다.
- 상대지수를 절대 검색량으로 기록하지 않는다.
- 같은 스냅샷과 점수 버전의 선정 결과가 결정적이다.
- Creator Advisor 접근 실패 시 다른 출처나 추정값으로 대체하지 않는다.
- 기존 외부 저장 Gate와 임시저장 직전 사용자 확인이 유지된다.
- 관련 단위·통합·E2E 테스트가 모두 통과한다.

## 13. 권장 구현 단위

1. 입력 계약과 문서 정합화
2. 원시 메타데이터 스키마와 저장소
3. Aside Browser 사전 점검과 수집기
4. 단계 A 점수와 결정적 동점 처리
5. 대시보드·CLI 연결
6. 단계 B/C 계산기와 성과 데이터 연결
7. 전체 파이프라인 E2E·운영 검증

각 단위는 테스트 통과 후 다음 단위로 진행하며, Creator Advisor 실제 화면을 사용하는 검증은 쓰기 동작 없이 수행한다.
