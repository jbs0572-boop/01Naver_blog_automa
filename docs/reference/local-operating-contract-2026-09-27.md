# 네이버 블로그 자동화 시스템 운영 계약

## 1. 목적과 활성 기준

Codex만 사용해 네이버 블로그 글을 만들고, 검수된 결과를 지정된 Notion 데이터베이스에 저장한 뒤 네이버 새 글에 입력하고 임시저장한다. Claude Code와 n8n은 사용하지 않는다.

이 파일이 유일한 활성 총괄 지침이다. 프로젝트 루트의 다음 Markdown은 이 계약의 단계별 입력·출력 규칙을 보완한다.

```text
topic-selector.md → researcher.md → writer.md → image-maker.md
→ content-assembler.md → notion-rider.md → naver-rider.md
```

`archive/legacy-beta/`는 과거 실행을 설명하기 위한 읽기 전용 보존 영역이며 활성 지침이나 실행 분기에는 사용하지 않는다. 기존 Notion 행·보기·실행 로그·manifest는 역사 기록으로 보존하며 수정하거나 삭제하지 않는다.

## 2. 시작 선택과 단일 파이프라인

실행 시작 시 선택은 정확히 하나다.

1. 사용자가 주제 또는 키워드를 제공한다. `topic-selector`는 이를 바꾸지 않고 유효성·중복·검색 의도·조사 가능성을 확인한다.
2. 사용자가 자동 선정을 요청한다. `topic-selector`가 확인 가능한 근거로 주제를 선택한다.

두 모드 모두 실행 시 KST 기준일(`as_of_date`, `YYYY-MM-DD`)을 사람에게 받는다. 새 입력에는 분야·주요 독자·발행목적을 받지 않는다. 자동 선정의 후보 원천은 네이버 Creator Advisor 화면이며 Aside Browser 읽기 전용 접근과 원시 스냅샷을 사용한다.

대시보드 신규 자동 요청은 `dashboard_auto_envelope=one_daily_generate_child`로 일반 `daily-generate` 자식 1개만 실행한다. 사용자 정의 요청도 `dashboard_user_envelope=one_daily_generate_child`로 자식 1개를 실행한다. 자식은 `child_lifecycle=q1_then_q2_then_confirmation`을 통과한다. 과거 `dashboard_auto_envelope=three_sequential_daily_generate_children` 기록은 보존하고 읽을 수 있지만 신규 실행에는 사용하지 않는다. 예약은 대시보드의 '매일 자동 작성'에서 KST 시간·실행 모드를 저장하고 시작하며, 각 예약일의 KST 날짜를 기준일로 자동 주제 1건을 생성한다. 기본 시간은 08:00·12:00·18:00이며 UI에서 변경·중지할 수 있다. 대시보드 서버가 예약을 실행하고 Mac 로그인 시 launchd가 서버를 유지한다. 별도 Codex 예약이나 비활성 daily-generate launchd를 중복 활성화하지 않는다.

두 선택은 모두 같은 `research/topic-selection-[키워드].md`를 만들고, 이후 단계·품질 Gate·외부 저장 경로는 동일하다. 모드는 사용자 입력, 라우팅, 권한 판단 또는 단계 생략 조건이 아니다.

```mermaid
flowchart TD
  A{시작: 주제 입력 방식} -->|사용자 주제·키워드| TS[topic-selector.md\n검증·보존]
  A -->|자동 선정 요청| TS[topic-selector.md\n근거 기반 선정]
  TS --> TSO[research/topic-selection-[키워드].md]
  TSO --> R[researcher.md]
  R --> RO[research/[키워드].md]
  RO --> W[writer.md + style-guide.md + seo-guide.md]
  W --> DO[drafts/[키워드].md\n시각 슬롯 포함]
  DO --> IM[image-maker.md + image-style-guide.md]
  IM --> AO[assets/[키워드]/\nimage-map.md + thumbnail]
  DO --> CA[content-assembler.md]
  AO --> CA
  CA --> FO[final/[키워드].md\nnaver-layout.md · naver-copy.md · naver-input.md]
  FO --> Q1{{Q1: 품질·manifest 통과?}}
  Q1 -->|아니오| STOP[중단: 해당 단계로 반환]
  Q1 -->|예| NR[notion-rider.md\n새 페이지·첨부만]
  NR --> Q2{{Q2: Notion 재조회·digest 통과?}}
  Q2 -->|아니오| STOP
  Q2 -->|예| NAV[naver-rider.md\n모바일 에디터 입력]
  NAV --> CONFIRM{{임시저장 직전\n명시적 사용자 확인}}
  CONFIRM -->|아니오| HOLD[입력 유지, 임시저장 안 함]
  CONFIRM -->|예| DRAFT[네이버 임시저장\n발행하지 않음]
```

도식의 사각형은 담당 Markdown 지침 또는 그 산출물이며, 마름모는 다음 단계로 넘어가기 전에 반드시 통과해야 하는 Gate다.

## 3. 파일·산출물 책임

| 단계 | 읽는 Markdown | 필수 산출물 | 다음 단계 조건 |
|---|---|---|---|
| topic-selector | `topic-selector.md` | `research/topic-selection-[키워드].md` | 사용자 주제는 보존하거나 자동 선정 근거를 남김 |
| researcher | `researcher.md`, 주제 선정 파일 | `research/[키워드].md` | 주장·출처·기준일·본문 사용 위치가 연결됨 |
| writer | `writer.md`, 리서치, `style-guide.md`, `seo-guide.md` | `drafts/[키워드].md` | 제목 약속·독자 질문·시각 슬롯을 충족 |
| image-maker | `image-maker.md`, 초안, 리서치, `image-style-guide.md` | `assets/[키워드]/`, `image-map.md`, 전용 썸네일 | 슬롯·권리·파일·본문 정보 기여가 일치 |
| content-assembler | `content-assembler.md`, 초안·자산·연결표 | 네 최종 Markdown과 manifest | Q1 통과 |
| notion-rider | `notion-rider.md`, `notion-config.md`, Q1 산출물 | 지정 데이터 소스의 신규 Notion 페이지와 Q2 기록 | Q2 통과 |
| naver-rider | `naver-rider.md`, Q2 확인 페이지와 네이버 입력본 | 네이버 새 임시저장 글 | 명시적 사용자 확인 후 임시저장, 발행 없음 |

이전 단계의 산출물 또는 Gate 통과 기록이 없으면 다음 단계를 호출하지 않는다. 총괄 에이전트는 산출물을 직접 작성하지 않고 담당 단계의 결과와 검증만 확인한다.

## 4. 실행·manifest 계약

모든 신규 실행은 `pipeline_version=workflow-optimized-v1`을 사용한다. `schemas/workflow-contract.schema.json`은 단계 이벤트·시각 슬롯·이미지 생성 메타데이터·품질 기록·canonical manifest의 단일 계약이다.

```text
python3 -m tools.workflow_verifier manifest --root . --keyword <키워드> --run-id <run_id> --topic-id <topic_id> --created-at <KST ISO-8601> --output manifests/<run_id>-workflow-manifest.json
python3 -m tools.workflow_verifier verify-manifest --root . --manifest manifests/<run_id>-workflow-manifest.json
python3 -m tools.workflow_verifier gate --root . --manifest manifests/<run_id>-workflow-manifest.json --run-log runs/<run_id>.jsonl --gate notion_write --run-id <run_id> --target-id <데이터 소스 ID>
python3 -m tools.workflow_verifier validate-schema --input <JSON 또는 JSONL 계약 기록>
```

주제 입력 출처는 실행 상태와 로그에 `user_defined` 또는 `auto_selected`로 기록하되 이후 단계 라우팅에는 사용하지 않는다. manifest 작성기는 기존 저장 계약과의 호환을 위해 감사 필드 `mode=formal`을 내부에서 고정하며, 호출자가 이 값을 입력하거나 변경할 수 없다.

canonical manifest 파일 순서는 `final/[키워드].md → final/[키워드]-naver-layout.md → final/[키워드]-naver-copy.md → final/[키워드]-naver-input.md → assets/[키워드]/image-map.md → 본문 Markdown에 등장하는 이미지 순서 → 전용 썸네일`이다. 이 순서는 해시 계산용이고, 화면·복사 원본·Notion 본문에서는 전용 썸네일을 첫 이미지 블록으로 둔 뒤 본문 이미지 순서를 따른다. 각 파일의 실제 byte `size_bytes`와 SHA-256을 기록하며, `artifact_digest`는 자기 필드를 제외한 canonical JSON을 UTF-8·정렬 key·무공백으로 직렬화해 계산한다. 네 최종 Markdown·image-map·참조 이미지·썸네일은 manifest 검증에서 제외할 수 없다.

## 5. 품질과 외부 쓰기 Gate

각 단계는 최신성, 제목 약속, 총정리 범위, 반복, 독자 질문, 독창적 구성과 시각 계약을 저장 전에 점검한다. 미확인 정보는 `공식 확인`, `신뢰 가능한 보조 출처 확인`, `자료 기반 해석`, `미확인`으로 구분한다.

- Q1: `content-assembler`가 근거·최신성·제목 약속·독자 질문·시각 계약·네 최종 파일·canonical manifest를 자동 검사한다. 실패 원인은 민감값을 제거한 뒤 같은 실행 ID의 다음 content-assembler 시도에만 전달하며, 이 단계는 동일 원인 반복을 포함해 총 3회까지만 재시도한다. 이전 producer와 Notion 쓰기는 Q1 통과 전 재호출하지 않으며, 3회가 소진되면 구체적 안전 원인으로 실패 처리한다.
- Q2: `notion-rider`가 신규 페이지 저장 후 제목·본문·목록·표·링크·이미지·썸네일 순서, 첨부, 중복 `run_id`, 정규화 구조 digest를 재조회한다. 실패는 `storage_integrity=failed`로 기록하고 네이버 입력을 시작하지 않는다.
- Q3: 사용자 또는 승인된 검수자의 원본·모바일 렌더링 평가이며 선택적 기록이다. Q1·Q2를 대체하지 않으며 네이버 입력 차단 조건이 아니다.

Notion 쓰기는 Q1, 현재 manifest, 실행 로그, 설정과 실제 조회 결과가 같은 데이터 소스 ID임을 모두 확인한 뒤에만 시작한다. 허용되는 Notion 작업은 첨부 생성과 지정 데이터 소스의 신규 페이지 생성뿐이다. 기존 페이지 수정·복제·이동·삭제와 브라우저 우회 쓰기는 허용하지 않는다.

외부 Notion·네이버·브라우저·컴퓨터 도구의 쓰기는 `.codex/hooks.json`의 `PreToolUse` Hook이 manifest·로그·`run_id`·대상·쓰기 종류를 검증한 뒤에만 허용한다. Hook 환경에는 `WORKFLOW_GATE`, `WORKFLOW_MANIFEST`, `WORKFLOW_RUN_LOG`, `WORKFLOW_RUN_ID`, `WORKFLOW_TARGET_ID`를 이번 실행 값으로 주입한다. `notion_write`와 `naver_draft_save`는 승인 단계가 아니라 쓰기 종류 식별자다. 인증 토큰·쿠키는 환경이나 로그에 기록하지 않는다.

`naver-rider`는 Q2 후 Notion 페이지 ID·검증 시각·`notion_roundtrip_digest`·대상 블로그 ID·현재 `artifact_digest`를 대조하고, `naver-input.md`가 Notion raw 원본과 허용된 출처 메타데이터 제거 외에는 같은지 확인한 뒤에만 입력한다. 임시저장 버튼 바로 앞에는 대상 블로그·제목·이미지·저장 동작에 대한 명시적 사용자 확인을 다시 받는다. 확인이 없으면 임시저장하지 않으며, 발행·예약 발행·공개 설정 변경은 수행하지 않는다.

## 6. Notion 호환 규칙

`notion-config.md`의 데이터베이스 ID와 데이터 소스 ID를 바꾸지 않는다. 기존 `모드` 속성은 과거 행·보기와의 호환 및 감사 목적에만 유지하며, 신규 실행에는 고정값 `정식`을 기록한다. 이 속성은 사용자 입력이나 실행 라우팅에 사용하지 않는다. 기존 `베타` 값, 과거 행, 과거 보기와 스키마는 외부에서 변경하지 않는다.

## 7. 공통 시각 계약과 안전

대시보드 QA를 포함한 모든 브라우저 작업은 Aside Browser만 사용한다. 다른 브라우저 제어 도구로 전환하지 않는다.

Aside CLI를 사용하기 전에는 먼저 현재 PATH의 `aside` 명령을 확인하고, 검색되지 않으면 기존 설치 경로 `/Users/beomseok/.local/bin/aside`를 파일·실행 권한과 함께 확인한다. `command not found`만으로 미설치라고 판단하거나 재설치를 요청하지 않는다. 프로그램은 `tools.aside_browser.resolve_aside_cli()`의 PATH 우선·홈 디렉터리 fallback 탐색을 사용한다. 설치 파일이 없을 때만 미설치 가능성을 보고하며, 깨진 링크·실행 권한 오류·CLI 실행 실패·브라우저 연결 실패는 각각 관측된 오류로 구분한다. CLI 위치 확인과 업데이트는 별도 단계로 처리하고, 격리된 단계에서 `aside --update`를 실행하지 않는다.

자동 주제 선정의 Aside 화면 읽기는 호스트가 격리된 Codex 모델 호출 전에 수행하고 원시 관측을 읽기 전용 입력으로 전달한다. 호스트의 결정적 파서가 Creator Advisor 스냅샷을 정규화하고, 중복·분야 정책으로 단일 주제를 확정한 `selection-decision.json`을 모델 호출 전에 저장한다. 모델은 고정 주제의 선정 이유와 글 방향만 작성하며 브라우저를 재호출하거나 주제를 바꾸지 않는다. 검사기는 같은 decision digest와 원문 키워드를 사용한다. 연결 실패 시 모델을 호출하지 않는다. 이를 위해 격리 프로필의 네트워크·파일 접근 제한을 해제하지 않는다.

모든 시각 슬롯은 `visual_slot_id`, `visual_intent`, `asset_type`, `required_by`, `source_policy`, `subject_scope`, `section`, `fallback`을 주제 선정부터 최종 연결표까지 유지한다. 결정 우선순위는 `제목 약속 > 독자 질문 > 정확한 근거 > 독창적 구성 > 장식`이다. 공식 원본 자산을 우선 사용한다. 공식 원본 URL과 공식 출처가 확인되면 네이버 블로그 재사용 허가가 확인되지 않았어도 원본을 그대로 사용할 수 있으며, `source_policy=official_or_licensed`, `origin=official`, 권리 상태 `미확인`과 원본 URL을 내부 자산 원장에 기록한다. 재사용 허가 미확인만을 이유로 생성 자산으로 대체하거나 단계를 중단하지 않는다. 원본의 크롭·합성·AI 보조 편집은 편집 가능 여부까지 별도로 확인한 경우에만 허용한다. 사용한 외부 이미지의 네이버 공개 캡션은 이미지 바로 아래에 `출처: 기관명`처럼 출처명만 간단히 표시한다. 원본 URL·확인 시각·권리 상태·이용약관 검토·사용 또는 미사용 이유·생성이나 편집 방식·사용자 확인 사실은 `research/[키워드].md`와 `image-map.md`에만 보존하고 공개 본문·공개 출처 목록·이미지 대체텍스트에는 적지 않는다. `official_or_licensed`는 공식 원본이거나 별도 허가·라이선스가 있는 자산이라는 출처 정책이며, 그 자체로 재사용 허가 확인 완료를 뜻하지 않는다. 순수 생성 자산은 `origin=generated`로 구분하고 공개 출처 캡션을 붙이지 않으며, 공식 원본에 없는 티켓·예약 화면·승인 문서를 새로 만들거나 공식 발표물처럼 오인시키지 않는다.

researcher의 `research-official`과 `research-supporting-visual` Lane은 별도 하위 에이전트를 만들지 않고 동일 프로세스에서 직렬로 실행한다. 앞 Lane이 끝난 뒤 다음 Lane을 시작하며, 실패 시 통과한 이전 파이프라인 단계는 유지하고 researcher 단계만 재시도한다.

공식 원본 URL·공식/생성 출처·권리 상태 기록·개인정보·광고 표기·대상 데이터베이스·인증·블로그 ID가 확인되지 않으면 해당 단계에서 중단한다. 공식 원본의 권리 상태 `미확인`은 유효한 내부 기록값이며 재사용 허가 미확인만으로 중단하지 않는다. 기존 파일과 Notion 항목을 임의로 덮어쓰거나 삭제하지 않는다.

## 8. 보고

각 담당 단계의 성공 보고는 `진행 상황: <단계명> 완료`, 실패 보고는 `오류: <단계명> - <원인과 필요한 조치>` 형식을 쓴다. 최종 보고에는 주제 입력 방식, 주제, 단계별 결과, 최종 파일 경로, Notion 데이터 소스 ID와 Q2 결과, 네이버 입력·사용자 확인·임시저장 결과, 남은 오류를 포함한다.

## 9. 개발 리뷰와 CI

변경 리뷰는 PR diff와 직접 영향 경로를 기준으로 수행한다. 실제 오류·Gate 위반·중복 외부 쓰기·재시도/캐시 회귀를 우선한다. 심각도는 P0(긴급 장애·보안·데이터 손상), P1(중대한 기능·계약 위반), P2(일반 결함), P3(선택적 정리)로 구분하며, 각 지적에는 위치·재현 조건·실패 영향·최소 수정안을 적는다. 오타와 일반 문서 누락은 자동으로 P0/P1로 올리지 않는다.

`.github/workflows/quality.yml`은 코드·테스트·스키마·의존성·활성 지침 변경 PR에서 revision마다 한 번 실행한다. `contents: read`만 허용하고, `pull_request_target`, 비밀값·운영 서버·외부 계정 세션, 자동 수정 push·merge는 사용하지 않는다. 생성 글·이미지·운영 로그만 바뀐 PR은 실행 대상에서 제외한다. 같은 PR의 새 revision이 오면 이전 실행을 취소한다.

로컬 검증과 원격 CI 성공을 구분해 기록한다. Codex GitHub 네이티브 리뷰 연결, 댓글·수정 요청 게시, PR 생성은 별도 실행 요청과 접근 권한이 있을 때만 수행하며, 현재는 `리뷰 규칙·CI 준비 완료 / 네이티브 자동 리뷰 연결 미완료` 상태로 보고한다.
