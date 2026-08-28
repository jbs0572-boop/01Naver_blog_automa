# 병렬 처리 및 승인 단계 분리 기술 분석·실행 계획

## 문서 정보

- 기술 기준일: 2026-08-25 KST
- 현지 확인일: 2026-08-26 KST
- 상태: 계획 문서 작성 완료, 구현 미실행
- 범위: 병렬 처리와 외부 저장 승인 분리만 다룸
- 최우선 제약: 현재 워크플로와 기존 단계별 입출력·품질 Gate를 변경하지 않음

## 1. 결론

현재 워크플로는 그대로 유지한다.

```text
topic-selector → researcher → writer → image-maker → content-assembler → notion-rider → naver-rider
```

개선은 다음 두 가지로 제한한다.

1. `researcher` 내부의 읽기 전용 조사와 `image-maker` 내부의 준비·검증 작업만 제한적으로 병렬화한다.
2. Notion 저장과 네이버 임시저장은 각각 독립된 명시적 승인 조건을 충족해야 실행되도록 한다.

승인 조건은 새 파이프라인 단계나 새 에이전트가 아니다. 기존 `notion-rider`, `naver-rider`가 실행되기 전에 확인하는 선행 조건이다. 따라서 단계 순서, 기존 산출물 경로, 품질 Gate는 바뀌지 않는다.

## 2. 기술 분석 결과

### 2.1 Codex 병렬 처리

- 현재 로컬 환경은 `codex-cli 0.149.0`이며 안정화된 다중 에이전트 기능을 사용할 수 있다.
- 로컬의 차세대 다중 에이전트 기능은 비활성 상태이므로 이 계획의 의존 요소로 사용하거나 활성화하지 않는다.
- 공식 지침상 서브에이전트는 서로 독립적인 읽기·탐색·요약 작업에 적합하다. 동시에 같은 파일을 수정하는 쓰기 중심 작업은 충돌 위험과 토큰 사용량이 커진다.
- 따라서 서브에이전트는 근거 수집·출처 검증·시각 슬롯 검토까지만 담당하고, 정식 산출물 파일은 각 단계의 주 담당 에이전트 한 명만 기록한다.
- `codex exec --json`과 출력 스키마를 사용한 별도 오케스트레이터도 가능하지만, 현재 프로젝트는 Git 저장소가 아니며 별도 실행 계층을 추가하면 운영 복잡도가 커진다. 이번 범위에서는 도입하지 않는다.

### 2.2 Notion 저장

- Notion API는 연결별 평균 요청 한도가 있으므로 여러 작업자가 직접 저장하거나 각자 재시도하면 제한 초과와 중복 생성 위험이 커진다.
- 파일 업로드는 업로드 완료 여부와 첨부 상태를 재조회해야 한다.
- Notion 쓰기는 단일 큐·단일 작성자로 직렬화하고, 제한 응답은 `Retry-After`를 존중하며 지수 백오프와 지터를 적용한다.
- 응답이 불확실한 경우 같은 쓰기를 바로 반복하지 않고 대상 페이지와 업로드 상태를 먼저 재조회한다.

### 2.3 네이버 임시저장

- 네이버의 로그인 기반 블로그 글쓰기 Open API는 2020-05-06 종료되었다.
- 블로그 앱 글쓰기 URL Scheme도 2022-12-23 종료되었다.
- 따라서 현재의 UI 기반 `naver-rider` 방식과 사용자 확인 절차를 유지하는 것이 타당하다.
- 에디터 입력 자체가 자동 저장을 유발할 수 있으므로 네이버 승인은 임시저장 버튼 직전만이 아니라 에디터에 제목·본문·이미지를 입력하기 전에 확인한다. 기존의 임시저장 직전 사용자 확인도 재확인 절차로 유지한다.

### 2.4 현재 로그 상태

- 기존 `runs/*.jsonl`에는 `passed`, `completed`, `success`, `not-run` 등 상태 표현과 시간 필드가 혼재한다.
- 기존 로그는 수정하지 않는다.
- 앞으로 생성되는 이벤트만 표준 규격을 사용하고, 대시보드나 리더에서 과거 값을 호환 변환한다.

## 3. 병렬 처리 계획

### 3.1 1차 적용 범위

#### `researcher` 내부

최대 두 개의 읽기 전용 조사 Lane을 동시에 실행한다.

| Lane | 책임 | 금지 사항 |
|---|---|---|
| `research-official` | 공식 사실, 최신성, 원문 근거 확인 | 정식 리서치 파일 수정 |
| `research-supporting-visual` | 보조 출처, 시각 자산 출처·권리·확실도 확인 | 정식 리서치 파일 및 자산 수정 |

각 Lane은 아래 정보만 주 담당 `researcher`에게 반환한다.

- Lane ID와 완료 상태
- 원문 URL과 확인 시각
- 채택 가능한 주장과 근거 상태
- 시각 자료의 출처·권리 상태
- 확인하지 못한 항목과 한계

주 담당 `researcher`만 결과를 병합해 `research/[키워드].md`를 쓰고 기존 품질 Gate를 수행한다.

#### `image-maker` 내부

1차에서는 다음 읽기·준비 작업만 최대 두 개까지 병렬화한다.

- 시각 슬롯별 출처와 권리 검증
- 생성 프롬프트·자산 브리프 작성
- 슬롯 계약과 결과 후보의 적합성 검토

실제 이미지 생성·파일 기록, `image-map.md` 작성, 최종 이미지 검수는 주 담당 `image-maker`가 직렬로 수행한다.

### 3.2 2차 적용 조건

베타 10회에서 아래 조건을 모두 충족한 경우에만 서로 독립된 이미지 슬롯의 실제 생성을 최대 두 개까지 병렬화한다.

- 정식 산출물 파일 충돌 0건
- 필수 이미지 또는 연결표 누락 0건
- 기존 품질 Gate 통과율 저하 없음
- 병렬 대상 단계의 중앙값 소요 시간이 기준선보다 감소
- 추가 토큰·비용이 얻은 시간 단축에 비해 수용 가능함

각 작업자는 하나의 고유 `visual_slot_id`와 고유 출력 경로만 소유한다. `image-map.md` 갱신과 최종 통과 판정은 주 담당 `image-maker`만 수행한다.

### 3.3 병렬화하지 않는 작업

- 한 실행 안의 `writer`
- 한 실행 안의 `content-assembler`
- Notion 페이지 생성·업로드·재검증
- 네이버 에디터 입력·이미지 업로드·임시저장
- 동일한 정식 산출물 파일에 대한 동시 쓰기

향후 여러 `run_id`의 사전 제작 구간을 겹치는 방안은 단일 실행의 안정성이 검증된 뒤 별도로 판단한다. 그 경우에도 Notion 쓰기 큐와 네이버 작업은 직렬로 유지한다.

## 4. 승인 단계 분리 계획

정식 모드 승인은 실행 흐름을 바꾸는 새 단계가 아니라 외부 상태를 변경하는 기존 Rider의 실행 조건이다. 베타 Notion 저장은 아래 별도 흐름을 사용한다.

```text
content-assembler 통과
  → Gate A 승인 확인
  → notion-rider 저장·업로드·재조회
  → Notion 사람 검수 통과
  → Gate B 승인 확인
  → naver-rider 입력·임시저장·재검증

베타: content-assembler Q1·manifest 통과
  → notion-rider 저장·업로드·재조회
  → Notion 사람 검수
```

### 4.1 Gate A: 정식 모드 Notion 쓰기 승인

정식 모드에서는 다음 조건이 모두 참일 때만 `notion-rider`가 페이지 생성과 파일 업로드를 시작한다.

- `content-assembler`의 기존 검수가 통과됨
- 승인 대상 Notion 데이터 소스 ID가 `notion-config.md`와 일치함
- 최종 글, `image-map.md`, 참조 이미지로 계산한 산출물 해시가 승인 기록과 일치함
- 승인이 거절 또는 만료되지 않았음

승인 이후 산출물이나 대상 데이터 소스가 변경되면 기존 승인은 즉시 무효다. 새 해시로 다시 승인받는다.

### 4.2 Gate B: 네이버 에디터 입력·임시저장 승인

다음 조건이 모두 참일 때만 `naver-rider`가 네이버 에디터를 변경한다.

- Notion 저장 후 페이지를 다시 열어 본문과 이미지 검증이 완료됨
- 기존 Notion 속성 `사람 검수`가 `통과`임
- Notion 페이지 ID와 검증 시각이 승인 기록에 연결됨
- 승인 대상 블로그 ID와 산출물 해시가 일치함
- 승인이 거절 또는 만료되지 않았음

에디터 입력 후 임시저장 버튼을 누르기 직전에 기존 사용자 확인을 한 번 더 받는다. 발행은 절대 수행하지 않는다.

### 4.3 정식 모드와 베타 모드

- 정식 모드: 실행별 Gate A와 Gate B가 모두 필요하다.
- 베타 모드: Gate A 승인 이벤트 없이 Q1·manifest·지정 데이터 소스 검증 직후 Notion에 저장하며 `naver-rider`는 호출하지 않는다.
- 베타 일괄 저장은 정확한 실행 목록과 현재 manifest를 고정한 단일 직렬 큐로 수행한다. 산출물이나 대상 데이터 소스가 달라지면 해당 실행을 중단하고 다시 검증한다.

### 4.4 Codex 실행 승인과의 구분

CLI 명령·샌드박스 승인은 도구 실행 권한일 뿐 업무 승인 기록이 아니다. 이를 Gate A 또는 Gate B 승인으로 간주하지 않는다.

## 5. 승인 및 실행 이벤트 규격

별도 승인 폴더를 만들지 않고 기존 `runs/<run_id>.jsonl`에 승인 이벤트를 추가한다. 실행별 로그 파일은 한 작성자만 기록한다.

승인 이벤트 예시는 다음과 같다.

```json
{
  "event_type": "approval",
  "run_id": "RUN-YYYYMMDD-HHMMSS",
  "gate": "notion_write",
  "decision": "approved",
  "scope": "per-run",
  "target_id": "notion-data-source-id",
  "artifact_digest": "sha256:...",
  "requested_at": "2026-08-26T10:00:00+09:00",
  "decided_at": "2026-08-26T10:05:00+09:00"
}
```

필수 원칙은 다음과 같다.

- `gate`: `notion_write` 또는 `naver_draft_save`
- `decision`: `approved`, `rejected`, `expired`
- `scope`: `per-run` 또는 `batch`
- 배치 승인에는 `batch_id`, 정확한 실행 목록, 최대 건수, 만료 시각을 추가
- `artifact_digest`: 최종 Markdown, `image-map.md`, 참조 이미지의 순서 고정 manifest를 SHA-256으로 계산
- Gate B는 Notion 페이지 ID와 마지막 검증 시각도 결합
- 토큰, 쿠키, 인증정보와 불필요한 개인정보는 기록 금지

일반 실행 이벤트의 신규 표준 상태는 다음으로 통일한다.

```text
pending | running | passed | failed | blocked | skipped
```

신규 이벤트에는 가능한 경우 `started_at`, `ended_at`, `parent_stage`, `lane_id`, `depends_on`, `worker_role`, `attempt`, `artifacts`, 안전하게 정리한 오류 정보를 기록한다.

기존 로그는 그대로 두고 조회 시에만 다음처럼 해석한다.

- `success`, `completed` → `passed`
- `not-run` → `skipped`

## 6. 구현 시 수정할 기존 파일

이 문서를 승인한 뒤 아래 기존 지침만 순서대로 업데이트한다. 현재 단계에서는 아직 수정하지 않는다.

| 파일 | 추가할 내용 |
|---|---|
| `AGENTS.md` | 불변 파이프라인, 병렬 허용 경계, Gate A/B, 단일 작성자 원칙 |
| `BETA-AGENTS.md` | 최대 동시 작업 수, Gate A 승인 생략, 네이버 호출 금지, 직렬 Notion 저장 범위 |
| `researcher.md` | 읽기 전용 두 Lane, 반환 계약, 주 담당만 병합·기록 |
| `image-maker.md` | 단계적 병렬화, 슬롯 소유권, 주 담당만 연결표·최종 판정 |
| `notion-rider.md` | 모드별 Gate A 조건, 직렬 큐, 제한 응답 재시도, 불확실 결과 재조회 |
| `naver-rider.md` | 에디터 변경 전 Gate B, 저장 직전 재확인, 발행 금지 |
| `metrics-spec.md` | 상태·병렬 이벤트·승인 이벤트 규격과 과거 로그 호환 해석 |

초기에는 `notion-config.md` 속성을 추가하지 않는다. 현재의 `사람 검수`, `검수 결과` 등 기존 속성을 사용한다.

새 오케스트레이터, 별도 승인 디렉터리, Responses API 계층은 만들지 않는다.

## 7. 실행 및 검증 순서

1. 사용자가 이 계획 문서의 범위와 승인 규칙을 확인한다.
2. 위 일곱 개 기존 지침 파일만 업데이트한다. 런타임 병렬 기능은 아직 켜지 않는다.
3. 신규 실행·승인 이벤트 규격을 적용하되 과거 로그는 수정하지 않는다.
4. 외부 쓰기를 비활성화한 Dry-run으로 아래 차단 동작을 검증한다.
   - 정식 모드 Gate A 없음·거절·만료·해시 불일치 시 Notion 작업 차단
   - 베타 모드 Q1·manifest·대상·Notion 도구 불일치 시 Notion 작업 차단
   - Notion 재조회와 사람 검수 없이 Gate B 진행 차단
   - Gate B 없음, 거절, 만료 또는 해시 불일치 시 네이버 에디터 변경 차단
5. 고유한 `run_id`로 베타 10회를 수행한다.
   - `researcher` 읽기 전용 Lane 최대 2개
   - `image-maker` 준비·검증 Lane 최대 2개
   - 실제 이미지 생성은 직렬
   - Notion은 Gate A 승인 없이 Q1·manifest 검증과 단일 큐 사용
   - 네이버는 호출하지 않음
6. 현재 기준선과 단계별 소요 시간, 전체 소요 시간, 토큰 사용, 실패·재시도, 품질 Gate 결과를 비교한다.
7. 2차 적용 조건이 모두 충족될 때만 독립 이미지 슬롯 생성을 최대 2개까지 병렬화한다.
8. 정식 모드 단일 주제로 Gate A → Notion 재검증 → 사람 검수 → Gate B → 네이버 임시저장 순서를 시험한다.
9. 제목·본문·이미지·대표 이미지·임시저장 목록과 미발행 상태를 확인한 뒤에만 운영 적용을 결정한다.

## 8. 완료 판정 기준

- 각 실행의 단계 순서가 기존 파이프라인과 동일함
- 기존 입력·출력 경로와 품질 Gate가 유지됨
- 단계 주 담당자만 정식 산출물을 기록하며 동시 쓰기가 없음
- 일치하고 유효한 승인 기록 없이는 Notion과 네이버 상태가 변경되지 않음
- 산출물 또는 대상 변경 시 기존 승인이 무효화됨
- Notion과 네이버 작업이 직렬로 실행됨
- 베타 모드는 네이버를 호출하지 않고 정식 모드는 발행하지 않음
- 신규 로그는 표준 규격을 사용하고 기존 로그는 보존됨
- 시간 단축이 확인되고 품질이 저하되지 않은 병렬화만 운영에 반영됨

## 9. 공식 참고 자료

- OpenAI, Subagents: <https://learn.chatgpt.com/docs/agent-configuration/subagents>
- OpenAI, Configuration reference: <https://learn.chatgpt.com/docs/config-file/config-reference>
- OpenAI, Latest model guide: <https://developers.openai.com/api/docs/guides/latest-model>
- OpenAI, Non-interactive mode: <https://learn.chatgpt.com/docs/non-interactive-mode>
- Notion, Request limits: <https://developers.notion.com/reference/request-limits>
- Notion, File upload: <https://developers.notion.com/reference/file-upload>
- NAVER Developers, 블로그 글쓰기 Open API 종료: <https://developers.naver.com/notice/article/7527>
- NAVER Developers, 블로그 글쓰기 URL Scheme 종료: <https://developers.naver.com/notice/article/8595>

## 10. 이 문서 작성 이후 할 일

다음 작업은 이 문서의 사용자 승인 후 **7개 기존 지침 파일 업데이트**다. 그 다음은 외부 쓰기 없는 Dry-run, 베타 10회, 정식 모드 단일 주제 검증 순으로 진행한다. 계획 승인 전에는 기존 워크플로 파일, Notion, 네이버를 변경하지 않는다.
