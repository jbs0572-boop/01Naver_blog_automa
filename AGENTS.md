# 네이버 블로그 자동화 시스템 정식 모드 총괄 지침

> 계획 문서 리뷰의 기준은 아래 `Code Review Rules`의 적용 범위를 먼저 확인한다. 이 checkout의 운영 코드 계약과 최신 로컬 프로젝트를 대상으로 한 개선 계획의 계약은 아직 동기화되지 않았다.

## 1. 목적과 범위

Codex만 사용해 네이버 블로그 글을 만들고, 검수한 결과를 지정된 Notion 데이터베이스에 저장한다. Claude Code와 n8n은 사용하지 않는다. 이 파일과 프로젝트 루트의 지정된 지침 파일이 정식 모드의 기준이다.

`agents/topic-selector.md`처럼 지정 목록에 없는 기존 파일은 참고하거나 수정하지 않는다. 이 문서가 지정한 루트 파일을 우선한다.

## 2. 폴더와 파일 역할

```text
.
├── AGENTS.md              # 정식 모드 총괄
├── BETA-AGENTS.md         # 베타 모드 총괄
├── topic-selector.md      # 주제 선정
├── researcher.md          # 근거 조사
├── writer.md              # 본문 초안
├── image-maker.md         # 이미지 제작·검수
├── content-assembler.md   # 본문과 이미지 조립
├── notion-rider.md         # Notion 데이터베이스 저장
├── naver-rider.md          # 네이버 에디터 입력·임시저장
├── notion-config.md        # Notion 운영 DB·속성·첨부 방식 설정
├── style-guide.md         # 문체
├── seo-guide.md           # SEO·AEO·GEO
├── image-style-guide.md   # 이미지 스타일
├── research/              # 주제 선정 및 리서치
├── drafts/                # 초안
├── assets/                # 이미지
└── final/                 # 완성 글
```

## 3. 실행 전 확인

- 모드가 입력되지 않았으면 실행 전에 정식 모드인지 베타 모드인지 사용자에게 확인한다.
- 정식 모드에서는 실제 게시할 글을 만든다.
- `notion-config.md`를 먼저 읽고 Notion 데이터베이스 URL·ID, 데이터 소스 ID, 속성명을 확인한다.
- 현재 운영 대상은 `notion-config.md`에 기록된 `네이버 블로그 자동화 운영` 데이터베이스다. 설정과 실제 조회 결과가 다르면 추측하지 말고 중단한다.
- Notion 데이터베이스 연결, 대상 데이터베이스, 블로그 주제와 필요한 인증 상태가 확인되지 않으면 추측하지 말고 중단한다.
- 각 단계의 지침 파일을 해당 담당 에이전트가 읽었는지 확인한다.

## 4. 파이프라인

`topic-selector → researcher → writer → image-maker → content-assembler → notion-rider → naver-rider`

모든 신규 실행은 `pipeline_version=workflow-optimized-v1`을 사용한다. `schemas/workflow-contract.schema.json`이 단계 이벤트·승인·시각 슬롯·이미지 생성 메타데이터·품질 기록·canonical manifest의 단일 계약이며, 실행 검증기는 다음 명령으로 호출한다.

```text
python3 -m tools.workflow_verifier manifest --root . --keyword <키워드> --run-id <run_id> --topic-id <topic_id> --mode <beta|formal> --created-at <KST ISO-8601> --output manifests/<run_id>-workflow-manifest.json
python3 -m tools.workflow_verifier verify-manifest --root . --manifest manifests/<run_id>-workflow-manifest.json
python3 -m tools.workflow_verifier gate --root . --manifest manifests/<run_id>-workflow-manifest.json --run-log runs/<run_id>.jsonl --gate notion_write --run-id <run_id> --target-id <데이터 소스 ID>
python3 -m tools.workflow_verifier validate-schema --input <JSON 또는 JSONL 계약 기록>
```

canonical manifest 파일 순서는 `final/[키워드].md → final/[키워드]-naver-layout.md → final/[키워드]-naver-copy.md → assets/[키워드]/image-map.md → 본문 Markdown에 등장하는 이미지 순서 → 전용 썸네일`이다. 각 파일은 실제 byte의 `size_bytes`와 SHA-256을 기록하며, `artifact_digest`는 자기 필드를 제외한 canonical JSON을 UTF-8·정렬 key·무공백으로 직렬화해 계산한다. 이 세 최종 Markdown 파일과 image-map·참조 이미지·썸네일은 승인 범위에서 제외할 수 없다.

| 단계 | 입력 | 출력 | 통과 조건 |
|---|---|---|---|
| topic-selector | 사용자 주제 또는 자동 선정 요청 | `research/topic-selection-[키워드].md` | 주제·우선순위·검색 의도·최종 답·총정리 범위·독창적 구성·정보 유효기간·확인일과 근거가 있음 |
| researcher | 주제 선정 결과 | `research/[키워드].md` | 채택 주장마다 원문 URL·기준일·본문 사용 위치가 있고 제목 항목·인물관계·독창적 구성 요소별 조사 상태가 있음 |
| writer | 리서치, 문체·SEO 가이드 | `drafts/[키워드].md` | 근거 기반 본문, 총정리 범위 충족, 제목 약속·독자 질문·독창적 구성 충족, 반복 제거, 시각 슬롯 메타데이터가 있는 `[IMAGE:]` 마커가 있음 |
| image-maker | 초안, 리서치의 시각 슬롯, 이미지 가이드, 마커 | `assets/[키워드]/` 및 연결표 | 시각 슬롯의 의도·유형·출처 정책과 실제 자산이 일치하고, 각 이미지가 본문에 새로운 정보를 추가하며 썸네일·본문 이미지의 역할이 중복되지 않음 |
| content-assembler | 초안, 이미지, 연결표 | `final/[키워드].md`, `final/[키워드]-naver-layout.md`, `final/[키워드]-naver-copy.md` | 시각 계약, 마커·이미지 수·순서·경로·썸네일·본문 보존과 네이버 블록 배치 검사가 통과됨 |
| notion-rider | 완성 글, 이미지, 모드·차수 | Notion 데이터베이스 새 항목 | 저장 후 다시 열어 본문과 이미지가 확인됨 |
| naver-rider | 완성 글, 네이버 배치 명세, 이미지, 정식 모드 | 네이버 블로그 임시저장 글 | 제목·본문·이미지·출처·대표 이미지가 확인되고 임시저장됨. 발행하지 않음 |

이전 단계의 출력이 없거나 검수를 통과하지 못하면 다음 단계를 호출하지 않는다. 총괄 에이전트는 직접 주제 선정, 조사, 글쓰기, 이미지 제작·조립을 하지 않고 담당 에이전트를 호출하고 결과만 확인한다.

각 단계는 저장 전 자동 품질 Gate를 수행한다. 최신성, 제목이 약속한 항목의 충족 여부, 총정리 범위의 커버리지, 같은 정보의 반복, 독자가 해결하려는 질문에 대한 답변, 독창적 구성 요소의 근거와 정보 기여를 검사하고, 기준 미달이면 다음 단계를 호출하지 않는다. 미확인 정보는 무조건 삭제하지 않고 `공식 확인`, `신뢰 가능한 보조 출처 확인`, `자료 기반 해석`, `미확인` 상태를 구분한다. 이 검사는 기존 구조·출처·이미지·Notion 검수를 대체하지 않고 추가한다.

## 4-1. 공통 시각 의도 계약

모든 주제의 이미지는 담당 에이전트의 임의 판단으로 결정하지 않고, `topic-selector → researcher → writer → image-maker → content-assembler` 순서로 다음 계약을 전달한다. 영화 출연진뿐 아니라 제품·장소·절차·비교·정책 글에도 같은 규칙을 적용한다.

시각 슬롯마다 다음 필드를 기록한다.

```yaml
visual_slot_id: "VIS-01"
visual_intent: "identify | explain | compare | sequence | relate | evidence | experience | represent"
asset_type: "official_asset | portrait_grid | character_cards | side_by_side | timeline | process_flow | relationship_map | official_screenshot | map | chart | original_photo | generated_illustration | text_only"
required_by: "title_promise | reader_question | evidence | differentiation | optional"
source_policy: "official_or_licensed | verified_source | generated_allowed | no_image"
subject_scope: "본문에서 확인된 대상·주장·항목의 범위"
section: "본문 배치 위치"
fallback: "대체 유형 또는 중단 조건"
```

결정 우선순위는 `제목 약속 > 독자 질문 > 정확한 근거 제시 > 독창적 구성 > 장식`이다. 제목이 시각적으로 설명 가능한 항목을 약속하면 해당 시각 슬롯을 먼저 만든다. 공식·허가 자산을 우선 사용하되, 해당 자산을 확보하지 못한 실존 인물 얼굴 슬롯은 `source_policy=generated_allowed`로 전환해 생성 이미지로 대체할 수 있다. 이 경우 생성·합성 사실, 확인된 인물명·역할을 근거로 삼은 범위, 사용 범위를 연결표와 캡션에 기록한다. 제품·공식 화면·문서·로고의 정체성은 생성 이미지로 대체하지 않는다. 레이아웃 조립과 사실·자산 생성은 분리한다.

시각 슬롯이 최종 이미지와 연결되지 않거나 계약 필드가 실제 이미지와 다르면 다음 단계로 넘기지 않는다. `image-map.md`에는 슬롯 ID와 실제 파일·자산별 출처·증거 상태·권리 상태를 기록한다.

## 4-2. 불변 파이프라인과 병렬 처리 경계

승인된 병렬화는 기존 파이프라인의 단계 순서·입출력 경로·품질 Gate를 바꾸지 않는다. 병렬 작업은 읽기·준비 결과를 주 담당자에게 반환하는 보조 작업이며, 정식 산출물은 단계별 주 담당자 한 명만 기록한다.

- researcher 내부에서는 research-official과 research-supporting-visual, 최대 2개의 읽기 전용 Lane만 동시에 실행할 수 있다. 두 Lane은 리서치 파일·실행 로그·자산을 직접 수정하지 않고 주 담당자에게 Lane ID, 완료 상태, 확인 시각, 채택 주장·근거, 시각 자산의 출처·권리 상태, 한계를 반환한다.
- image-maker 내부에서는 시각 슬롯의 출처·권리 검증, 생성 프롬프트·자산 브리프 작성, 슬롯 계약과 후보 적합성 검토를 최대 2개의 준비·검증 Lane으로 병렬화할 수 있다. 실제 이미지 생성·파일 기록·image-map 작성·최종 판정은 주 담당자가 직렬로 수행한다.
- writer, content-assembler, Notion 페이지 생성·파일 업로드·재조회, 네이버 에디터 입력·이미지 업로드·임시저장은 한 실행 안에서 병렬화하지 않는다. 같은 정식 산출물 파일을 동시에 쓰지 않는다.
- 독립 이미지 슬롯의 실제 생성을 최대 2개까지 병렬화하는 것은 베타 10회에서 파일 충돌·필수 자산 누락이 0건이고, 품질 Gate 저하가 없으며, 중앙값 소요 시간이 기준선보다 감소하고, 추가 비용이 수용 가능한 경우에만 별도 승인 후 허용한다. 조건 하나라도 충족하지 못하면 직렬 생성을 유지한다.
- 병렬 Lane은 고유한 Lane ID와 담당 범위를 가져야 하며 주 담당자만 병합·기록·최종 통과 판정을 한다. 이 경계를 지키지 못하면 해당 단계는 성공으로 보고하지 않는다.

## 4-3. 외부 저장 승인 Gate

승인은 새 파이프라인 단계나 새 에이전트가 아니라 기존 Rider가 외부 상태를 변경하기 전에 확인하는 선행 조건이다. 승인 기록이 없거나 조건이 맞지 않으면 연결 도구를 호출해 쓰기·업로드·에디터 변경을 시작하지 않는다.

### Gate A: Notion 쓰기

notion-rider는 다음 조건이 모두 맞을 때만 페이지 생성과 파일 업로드를 시작한다.

- content-assembler의 기존 검수 결과가 통과다.
- 승인 대상 데이터 소스 ID가 notion-config.md의 설정과 실제 조회 결과에 모두 일치한다.
- 최종 Markdown, image-map.md, 본문에 참조된 이미지의 고정 순서 manifest로 계산한 SHA-256 artifact_digest가 승인 기록과 일치한다.
- 승인 결정이 approved이고, 승인 범위가 per-run이면 해당 run_id와 일치하며, batch이면 정확한 실행 목록·최대 건수·만료 시각의 범위 안에 있다.
- 승인 결정이 rejected 또는 expired가 아니며, 승인 이후 산출물·대상 데이터 소스·참조 이미지가 변경되지 않았다.

품질 판정 시점은 새 단계를 만들지 않고 다음 결과로 분리한다.

- Q1: `content-assembler`가 근거·최신성·제목 약속·독자 질문·시각 계약·세 최종 파일·canonical manifest를 자동 검사한다. Q1 실패 시 Gate A를 요청하지 않는다.
- Q2: `notion-rider`가 저장 후 제목·본문·목록·표·링크·이미지·썸네일 순서, 첨부 완료, 중복 `run_id`, 정규화 구조 digest를 재조회해 검사한다. Q2 실패는 콘텐츠 품질과 별도로 `storage_integrity=failed`다.
- Q3: 사용자 또는 승인된 검수자가 원본과 모바일 렌더링을 함께 평가한다. `사람 검수=통과`와 이미지 5개 항목 총점 16/20 이상·개별 3점 미만 없음이 확인되기 전에는 Gate B를 요청하지 않는다.

외부 Notion·네이버·브라우저·컴퓨터 도구의 쓰기는 `.codex/hooks.json`의 Codex `PreToolUse` Hook이 같은 manifest·로그·run_id·대상·Gate를 검증한 뒤에만 허용한다. Hook 자체는 승인을 만들지 않으며, 승인 이벤트가 없거나 digest가 다르면 도구 호출을 차단한다.
Hook 실행 환경에는 `WORKFLOW_GATE`, `WORKFLOW_MANIFEST`, `WORKFLOW_RUN_LOG`, `WORKFLOW_RUN_ID`, `WORKFLOW_TARGET_ID`를 이번 실행 값으로 주입해야 한다. 하나라도 없으면 외부 쓰기는 차단되며, 인증 토큰·쿠키는 이 환경이나 로그에 기록하지 않는다.

### Gate B: 네이버 에디터 입력·임시저장

naver-rider는 Notion 저장 후 재조회와 사람 검수가 끝난 뒤, 제목·본문·이미지를 입력하거나 업로드하기 전에 다음 조건을 모두 확인한다.

- 이번 실행에서 저장한 Notion 페이지를 다시 열어 본문·소제목·목록·링크·이미지·썸네일 순서를 검증했다.
- Notion 속성 사람 검수가 통과다.
- Notion 페이지 ID와 마지막 검증 시각이 승인 기록에 연결되어 있다.
- 승인 대상 블로그 ID와 artifact_digest가 승인 기록과 일치한다.
- Notion 페이지 재조회 결과의 `notion_roundtrip_digest`가 승인된 `artifact_digest`와 일치한다.
- 승인 결정이 approved이고 만료되지 않았으며, 승인 이후 Notion 복사 원본·최종 산출물·이미지가 변경되지 않았다.

Gate B 승인 후에도 임시저장 버튼 직전의 기존 사용자 확인을 다시 받는다. 두 확인 중 하나라도 없으면 임시저장하지 않는다. 정식 모드는 Gate A와 Gate B를 모두 사용하고, 베타 모드는 Gate A만 사용하며 naver-rider를 호출하지 않는다. CLI 명령이나 샌드박스 실행 승인은 업무상 Gate A·Gate B 승인으로 간주하지 않는다.

## 5. 단계별 호출과 보고

담당 에이전트는 작업이 끝날 때 다음 한 줄을 보고한다.

`진행 상황: <단계명> 완료`

실패 시 다음 단계로 넘어가지 않고 다음 형식으로 보고한다.

`오류: <단계명> - <원인>`

보고에는 저장 경로, 검수 결과, 남은 조치가 필요한 경우 그 조치를 포함한다. 임시 파일·빈 파일·실패한 이미지·검수 전 Notion 항목을 성공으로 보고하지 않는다.

## 6. 정식 모드 검수 기준

- 주제: 사용자 의도와 실제 확인된 검색·시기 정보가 일치한다.
- 품질: 정보 유효기간과 발행 기준일이 일치하고, 제목 약속·독자 질문·차별화 포인트가 최종 글에 반영되어 있으며, 반복 정보가 자동 점검을 통과한다.
- 총정리: 제목에 `총정리`를 사용할 수 있으며, 이는 모든 것을 단정한다는 뜻이 아니라 제목이 약속한 기본정보·출연진·인물관계·관전 포인트 등 범위를 빠짐없이 구조화했다는 뜻이다. 각 항목은 확인 상태를 표시한다.
- 독창성: 공식 사실을 창작하지 않고, 출처가 있는 인물관계·갈등 구조·관전 포인트·확실도 표·정보 도식 중 하나 이상을 독자에게 새로운 구조로 제공한다.
- 리서치: 확인되지 않은 사실, 임의의 수치, 출처 없는 최신 정보가 없다.
- 초안: `style-guide.md`와 `seo-guide.md`를 읽고 적용했으며, 리서치 밖의 주장을 만들지 않았다.
- 이미지: 공식 화면이 필요한 곳에 생성 이미지를 쓰지 않았고, 모든 파일이 실제로 열리며 본문과 관련된다.
- 이미지: 제목 약속·독자 질문에 필요한 시각 슬롯이 적절한 `asset_type`으로 충족되고, 공식·허가·검증·생성 자산의 `source_policy`가 지켜진다.
- 이미지: 관계도·정보 도식은 주장별 출처와 확실도를 연결표에 기록하고, 도식이 실제 본문 정보를 추가하는지 확인한다.
- 이미지: 본문 이미지와 별도로 전용 썸네일 1개가 있으며, `[THUMBNAIL]` 항목·썸네일 파일·검수 결과가 `image-map.md`에 기록되어 있다.
- 조립: 원문은 이미지 마커 치환 외에 바뀌지 않았고, 이미지 누락·중복·순서 오류가 없다.
- Notion: `notion-config.md`의 지정 데이터베이스와 실제 스키마를 다시 확인한 뒤 새 항목에 저장하고, 저장 후 본문·소제목·목록·링크·이미지 순서를 확인했다.
- Notion 이미지는 로컬 경로만 기록하지 않고 실제 첨부 업로드 후 `file-upload://...` 이미지 블록으로 표시한다.
- 네이버 정식 모드: `naver-rider`가 네이버 에디터를 모바일 모드로 전환한 뒤 Notion의 검수 완료 페이지를 복사 원본으로 사용해 새 글을 입력하고, 저장 직전 사용자 확인 후 임시저장했다. 발행하지 않았다.

## 7. 실패·안전 기준

- 접근하지 못한 자료나 수치를 추정해 채우지 않는다.
- 권리·개인정보·광고 표기·공식 화면 여부가 불분명하면 해당 단계에서 중단한다.
- Notion 인증 토큰·시크릿은 파일에 저장하지 않는다. 설정 파일에는 비밀값이 아닌 DB 식별자와 운영 규칙만 기록한다.
- 기존 파일과 기존 Notion 항목을 임의로 덮어쓰거나 삭제하지 않는다.
- 사용자가 삭제를 명시한 경우에도 정식 모드에서 이번 실행으로 만든 자료만 대상으로 삼고, 대상을 먼저 기록·확인한다.
- 사용자가 지정하지 않은 파일은 수정하거나 삭제하지 않는다.

## 8. 최종 보고

`naver-rider`까지 정상 완료한 뒤 다음을 보고한다.

- 실행한 주제
- 단계별 성공·실패 여부
- 생성 또는 업데이트한 파일과 최종 파일 경로
- Notion 운영 설정 파일과 데이터베이스 식별자
- Notion 데이터베이스 저장 결과와 페이지 확인 결과
- 네이버 블로그 임시저장 결과와 제목·이미지·대표 이미지 확인 결과
- 남아 있는 오류 또는 없음

## Code Review Rules

### 검토 대상에 따른 계약 기준

- `Plan.md`, 최적화 제안서와 예비시험 문서는 최신 로컬 프로젝트를 대상으로 한다. 이 문서들의 목표 계약은 [2026-09-27 로컬 운영 계약](docs/reference/local-operating-contract-2026-09-27.md)이다. 위 1~8절의 이전 운영 규칙을 계획의 목표 계약으로 간주하지 않는다.
- 목표 계약은 Q3를 선택적 기록으로 두고, `naver-input.md`를 포함한 최종 Markdown 네 개를 canonical manifest에 포함한다. Q1·Q2·대상/digest 검증·임시저장 직전 사용자 확인은 필수다. 이 요구가 계획에 보존되는지 검토한다.
- Notion 쓰기는 Q1·현재 manifest·같은 run의 로그·설정·실제 데이터 소스 ID 일치와 `notion_write` Hook 뒤 신규 페이지·첨부로 제한하며, 저장 후 Q2를 재조회한다. 목표 계약은 Notion에 별도 사람 승인을 요구하지 않는다. 네이버 입력은 Q2/digest·페이지·블로그·산출물 검증과 Hook을 통과해야 하며 임시저장 직전 명시적 사용자 확인을 요구한다.
- 실제 코드·스키마·실행 문법의 변경은 해당 PR revision의 구현과 테스트를 기준으로 검토한다. 목표 계약 snapshot만으로 구현 완료나 안전성을 인정하지 않는다. 코드가 최신 계약으로 전환되면 루트 및 단계별 지침·schema·manifest·검사기·회귀 검사를 함께 일치시켜야 한다.
- GitHub의 이전 코드와 최신 로컬 계약 사이의 차이는 T00의 선행 작업이다. 이전 코드에서 계획을 바로 실행할 수 있다고 주장하거나 필요한 동기화·검증을 생략하면 지적한다. 계획에서 의도한 계약 자체를 예전 동작으로 되돌리는 것은 해결책이 아니다.
- snapshot은 검토용 역사 자료이며 외부 쓰기 권한이나 이 checkout의 실행 지침이 아니다. 캡처 시점의 연결 상태 문구는 [검토 범위](docs/review-package.md)에 기록한 실제 리뷰 상태와 구분한다.

### 변경 범위와 실제 결함

- PR diff와 직접 영향 경로를 검토한다. 위치·재현 조건·실패 영향·최소 수정안을 제시하고 실제 결함과 선택적 정리를 구분한다. 오타나 일반 문서 누락을 중대한 오류로 올리지 않는다.
- 계획 문서와 벤치마크 보고서에서는 측정값·예상치·미구현 항목을 구분한다. 실패한 실행을 성공 기준선으로 삼은 절감률, 품질 검사 제외를 숨긴 속도 주장, 같은 입력 반복을 다양한 사례의 성공률로 표현한 경우를 지적한다.

### 데이터와 외부 쓰기

- 외부 저장을 변경하면 대상 검증·산출물 무결성·기존 항목 보존·저장 직전 사용자 확인의 회귀를 우선 확인한다. 재시도나 재개가 중복 외부 쓰기를 만드는지 검토한다.
- 운영 로그·생성 자산·인증 자료는 PR 검증을 이유로 일괄 업로드하지 않는다. 공개 증거는 비밀값을 포함하지 않는 관련 요약·수치로 제한한다.

### 계획 검토의 기준

- Plan.md의 로컬 조사와 원격 저장소의 차이는 docs/review-package.md를 먼저 확인한다. 로컬에만 있는 구현을 원격에서 실행·검증했다고 주장하지 않는다.
- 후속 구현에서는 실제 운영 입력과 동일한 품질 검사로 비교한다. 프로그램 변환 시간만으로 전체 생성 시간 개선을 입증했다고 보지 않는다.
