# 주제 선정·주간 피드백 운영 런북

이 런북의 운영 범위는 Creator Advisor 후보를 기준으로 수동 블로그 통계를 7일·28일 성과에 연결하고, 보조 신호의 효과를 shadow로만 비교하는 C MVP다. `as_of_date`는 항상 사람이 KST 날짜로 지정한다. Notion·네이버·브라우저 쓰기와 발행은 이 루프의 기능이 아니며, 네이버 임시저장 직전의 명시적 사용자 확인은 계속 필수다.

## 공통 불변 조건

- 자동 주제 후보 집합은 검증된 Creator Advisor 스냅샷뿐이다. DataLab, 검색광고, 제3자 도구의 연관 키워드로 후보를 추가하지 않는다.
- 모든 스냅샷·발행 연결·주간 산출물·manifest·rollback 기록은 append-only로 보존한다. 기존 파일을 수정하거나 삭제하지 않는다. 운영 보존 기간은 최소 90일이며, 비교 실험을 시작하면 해당 실험 종료 후 90일까지 연장한다.
- 입력 digest 불일치, 미래 시점 관측, 중복 outcome, 경로 symlink, 약관 미확인, 인증 누락은 즉시 중단 조건이다.
- 최소 4주 동안 shadow 결과만 관찰한다. 단일 게시물의 7일·28일 코호트는 진단 자료일 뿐 baseline/challenger 승격 근거가 아니다.
- 중단 시 다음 명령으로 영구 rollback fence를 추가한다. 같은 `rollback-id`의 다른 내용은 거부된다.

```bash
python -m tools.topic_feedback_cli rollback \
  --root <project-copy> \
  --config <project-copy>/config/topic-feedback-rollout.json \
  --rollback-id RB-YYYYMMDD-001 \
  --captured-at YYYY-MM-DDTHH:MM:SS+09:00
```

## Tier 0: 고정 fixture

외부 호출 없이 5주 고정 자료로 전체 digest 사슬과 운영 출력을 확인한다. fixture는 항상 임시 디렉터리로 복사한 뒤 실행한다.

```bash
python -m tools.topic_feedback_cli replay \
  --root <copied-e2e-root> \
  --as-of 2026-09-07 \
  --evidence-dir <empty-existing-evidence-dir>
```

성공 기준은 `digest_chain_verified=true`, 7d·28d mature 수 1, Creator 후보 부분집합, `challenger_verdict=insufficient_evidence`, `external_calls=0`, `external_writes=0`, `source_root_writes=0`이다. 재생기는 검증한 원본 디렉터리 descriptor에서 필요한 일반 파일만 private scratch로 복사하고 Task9·10·11 산출물을 그 안에서 생성한 뒤 scratch를 삭제한다. 따라서 호출자가 준 root에는 `.automation/reports`, 주간 피드백 또는 manifest를 만들지 않는다. 같은 canonical 출력이 이미 있으면 byte no-op으로 재사용하고, 다른 내용의 충돌은 덮어쓰지 않고 실패한다. 입력 변조·symlink·특수 파일·검증 후 root 교체는 출력 생성 전에 fail closed 처리한다.

## C MVP: 수동 Blog Stats → 7/28 → Creator shadow

1. 소유자 계정에서 직접 내보낸 집계 통계를 개인정보 최소 단위로 수동 입력한다.
2. 발행 연결 digest와 게시물 ID를 확인한다.
3. 7일과 28일 cutoff 후 48시간 grace 안의 첫 관측만 cohort로 사용한다. 7일 mature가 아니면 28일 결과는 승격 근거가 될 수 없다.
4. 주간 집계를 실행한다.

```bash
python -m tools.preflight_runner run weekly-improve --root <project-copy>
```

Creator baseline은 실제 선정 순서이며, shadow는 진단 순서다. `insufficient_evidence`이면 active 전환을 시도하지 않는다. 누락 통계, 누적값 감소, 상충 중복 또는 게시 이후에만 알 수 있는 신호가 있으면 해당 cohort를 제외하고 원본을 보존한다.

## Tier 1: DataLab, 선택적이며 현재 보류

현재 구현 범위에는 라이브 어댑터가 없다. 정상 replay는 DataLab 전송을 시도하지 않고, 승인된 API 자격증명과 최신 공식 계약·이용약관 검토 기록이 모두 준비될 때까지 `blocked_missing_credentials`로 유지한다. 403·429 대응은 네트워크 기능이 없는 주입형 읽기 transport로만 훈련한다. 403은 `blocked_missing_credentials`, 429는 `blocked_rate_limited`로 중단하며 signal snapshot이나 promotion 결과를 만들지 않는다. 자동 우회·스크래핑·브라우저 수집은 하지 않는다. 향후 활성화하더라도 Creator 후보와 동일한 키워드의 신선한 상대 지수만 최소 4주 shadow로 사용한다.

## Tier 2: 검색광고 키워드 도구, 명시적 opt-in

광고 계정 권한, API 라이선스, 최신 약관 검토와 사용자의 명시적 opt-in이 모두 필요하다. 하나라도 없으면 disabled 상태를 유지한다. 검색량 단위와 PC·모바일 구분을 원본 그대로 기록하되 후보 확장에는 사용하지 않는다. 활성화 후 최소 4주 shadow와 rollback 준비가 필수다.

## Tier 3: 검증된 수동 제3자 자료

제품 식별, 이용약관, 내보내기 권한, 원본 시각과 digest가 확인된 수동 파일만 격리된 shadow 신호로 가져온다. BlackKiwi·Daglo 등은 자동 로그인·자동 수집하지 않는다. 출처 또는 권리 상태가 불명확하거나 스키마가 다르면 전체 입력을 거부한다. 누락된 10번째 도구는 식별될 때까지 disabled placeholder다.

## 향후 두 팔 실험 해제 조건

실제 승격 판단은 발행 전에 고정된 baseline/challenger 예측, 선택 기록, 입력 digest, score version, training cutoff가 각 게시물에 있어야 한다. 서로 독립적인 선택 결과가 7일·28일 모두 성숙하고, 선택된 유일 outcome이 최소 30개이며, rolling-origin 평가와 고정 승인 registry가 모두 통과해야 한다. NDCG는 진단 전용이다. 이 조건 전에는 과거 단일 게시물 수치를 두 팔 성과로 복제하거나 promotion을 승인하지 않는다.
