# 네이버 블로그 자동화 시스템 베타 모드 총괄 지침

## 목적

사용자가 제시한 테스트 주제로 전체 파이프라인이 끝까지 실행되는지 확인한다. Codex만 사용하며 Claude Code와 n8n은 사용하지 않는다. 베타라는 이유로 출처 확인, 이미지 제작, 누락 검사를 생략하지 않는다.

## 실행 규칙

- 모드가 베타로 명시된 경우에만 이 파일을 사용한다. 모드가 없으면 먼저 사용자에게 확인한다.
- 대량 베타 실행 전 `beta-test-plan.md`, `evaluation-rubric.md`, `metrics-spec.md`를 읽고, 실행 기준선과 Gate를 고정한다.
- 총괄 에이전트는 직접 조사·글쓰기·이미지 제작·조립을 하지 않고 담당 에이전트를 순서대로 호출한다.
- 순서는 `topic-selector → researcher → writer → image-maker → content-assembler → notion-rider`다. `notion-rider`는 모든 베타 실행의 필수 마지막 단계이며 생략할 수 없다.
- 각 단계의 입력과 출력 경로를 확인하고, 이전 단계가 성공하지 않으면 다음 단계로 넘어가지 않는다.
- 결과 파일과 Notion 제목에는 테스트 결과임을 명확히 표시한다.
- 사용자가 기존 주제의 재베타·품질 비교를 명시한 경우에는 기존 산출물을 중복으로 폐기하지 않는다. 새 `batch_id`, `run_id`, 실행 차수와 비교 목적을 발급하고 기존 파일·Notion 항목은 보존한다.
- 빈 파일, 누락 출처, 임시 이미지, 깨진 이미지, 미완성 조립 결과를 성공으로 처리하지 않는다.
- 병렬 작업은 researcher의 research-official·research-supporting-visual 읽기 전용 Lane 최대 2개와 image-maker의 준비·검증 Lane 최대 2개로 제한한다. Lane은 정식 산출물·실행 로그·자산을 직접 쓰지 않고, 주 담당자 한 명만 병합·기록·최종 판정을 수행한다.
- image-maker의 실제 이미지 생성은 직렬로 수행한다. 베타 10회 결과에서 파일 충돌 0건, 필수 이미지·연결표 누락 0건, 품질 Gate 저하 없음, 기준선 대비 중앙값 시간 감소, 추가 비용 수용 가능 조건을 모두 확인하기 전에는 실제 이미지 슬롯 병렬 생성을 허용하지 않는다.
- notion-rider를 호출하기 전마다 content-assembler Q1 통과, 실제 조회한 데이터 소스 ID와 notion-config.md의 일치, 최종 Markdown·image-map.md·참조 이미지 canonical manifest의 SHA-256 일치를 확인한다. 하나라도 맞지 않으면 Notion 쓰기·업로드를 시작하지 않는다.
- 베타에서는 naver-rider를 사용하지 않는다. Hook이 manifest와 Q1의 `run_id`·`topic_id`, 설정 데이터 소스와 실제 `create-pages` 부모를 대조한 뒤 Notion 첨부 생성과 지정 데이터 소스의 새 페이지 생성만 허용한다. 기존 페이지 수정·복제·이동·삭제 및 브라우저·네이버 쓰기로 우회하지 않는다.
- 베타 실행의 판정 순서는 `Q1 자동 품질 → Notion 저장 → Q2 저장 무결성`으로 고정한다. Q3 품질 기록이 있더라도 베타 완료를 차단하지 않는다. `notion-rider`는 Q1과 manifest 검증 직후 호출하고, 저장 후 Q2가 통과되면 베타 완료로 보고한다.
- Q1과 manifest는 `python3 -m tools.workflow_verifier verify-manifest --root . --manifest manifests/<run_id>-workflow-manifest.json`으로 재검증한다. 외부 쓰기 Hook의 `WORKFLOW_GATE=notion_write`는 쓰기 종류 식별자다.
- 베타 manifest에도 `final/[키워드]-naver-layout.md`와 `final/[키워드]-naver-copy.md`를 포함한다. 네이버를 호출하지 않더라도 두 파일·image-map·본문 참조 이미지·썸네일이 바뀌면 현재 manifest 검증 실패로 Notion 쓰기를 차단하고 manifest를 다시 생성한다.
- 각 단계는 자동 품질 Gate를 통과해야 다음 단계로 진행한다. 최신성, 제목 약속 충족, 총정리 범위 커버리지, 독자 질문 답변, 반복 제거, 독창적 구성과 차별화 포인트 반영 여부를 검사하고 결과와 실패 사유를 실행 기록에 남긴다. `미확인`은 즉시 삭제하지 않고 공식 확인·보조 출처 확인·자료 기반 해석·미확인 상태를 기록한다. 사람 검수를 추가하는 것이 아니라 기존 자동 파이프라인의 차단 조건을 강화한다.
- 베타 품질 기록에는 최소한 `coverage_score`, `evidence_status`, `originality_score`, `relationship_map_check`, `image_information_gain_check`를 포함한다. `총정리` 제목을 사용한 글은 제목에 약속한 각 항목의 상태와 본문 위치가 있어야 한다.
- 각 글에는 본문 이미지와 별도로 전용 썸네일 1개가 있어야 하며, `assets/[키워드]/image-map.md`에 `[THUMBNAIL]` 항목으로 기록한다. 썸네일은 모든 주제·차수에서 `content-assembler`의 복사 원본과 Notion 본문의 첫 번째 이미지 블록으로 고정한다.
- 썸네일은 반드시 주제에 맞춘 생성형 이미지 또는 주제와 직접 연결된 공식자료를 기준으로만 제작한다.
- 단순 텍스트 카드·범용 인포그래픽·출처 불명 이미지는 통과시키지 않으며, 이전 베타 자산은 품질 재검수 없이 재사용하지 않는다.
- 모든 본문 이미지는 `AGENTS.md`의 공통 시각 의도 계약을 따르며, 주제별 예외 유형을 새로 만들지 않는다. `visual_slot_id`, `visual_intent`, `asset_type`, `required_by`, `source_policy`, `subject_scope`, `section`, `fallback`을 단계 간에 전달한다.
- 로컬 `final/[키워드].md`가 생성되었더라도 `notion-rider`가 지정 데이터베이스에 새 항목을 만들고 저장 후 재조회하기 전에는 베타 실행을 완료로 보고하지 않는다. Notion 연결·권한·스키마·이미지 업로드·페이지 재조회 중 하나라도 실패하면 베타 실행은 실패로 보고한다.
- 네이버 로그인·임시저장·게시를 실행하지 않는다. 베타의 최종 표면은 Notion 데이터베이스와 `베타 테스트 검수` 보기다.
- `naver-rider.md`는 정식 모드 전용이며 베타 파이프라인에서 호출하지 않는다.
- 대량 실행은 `manifests/beta-topic-manifest-template.csv` 형식의 실제 manifest가 있고, 각 행에 고유 `topic_id`와 `duplicate_key`가 있을 때만 시작한다. 실제 2026년 1~7월 주제 모집단을 확보하지 못하면 추정으로 채우지 않고 중단한다.
- 각 작업은 `batch_id`, `run_id`, `topic_id`, 단계별 상태와 시각을 `runs/*.jsonl`에 기록한다. 비밀값·토큰·쿠키·개인정보는 기록하지 않는다.
- 신규 이벤트에는 `pipeline_version=workflow-optimized-v1`과 해당 manifest 경로·`artifact_digest`를 기록한다. 기존 로그는 수정하지 않고 `success/completed→passed`, `not-run→skipped`로 조회 시에만 호환 해석한다.

## 입력과 결과 기록

정식 모드와 같은 경로를 사용한다. 주제 선정 결과는 `research/topic-selection-[키워드].md`, 리서치는 `research/[키워드].md`, 초안은 `drafts/[키워드].md`, 이미지는 `assets/[키워드]/`, 완성 글은 `final/[키워드].md`에 둔다.

Notion 단계에서는 먼저 `notion-rider.md`와 `notion-config.md`를 읽고 `네이버 블로그 자동화 운영` 데이터베이스 URL·ID, 데이터 소스 ID, 속성명을 실제 조회로 대조한다. 베타 실행 차수, 실행일, 생성한 로컬 파일, 이미지 연결표, Notion 항목을 기록한다. 기존 정식 자료와 기존 항목은 수정하거나 삭제하지 않는다.

베타 테스트용 연결 확인 페이지는 운영 차수에 포함하지 않고 `상태=테스트`, `차수=0`으로 표시한다. 실제 베타 글은 같은 날짜·모드의 기존 항목을 조회한 뒤 가장 높은 `차수` 다음 번호를 사용한다.

Notion 본문은 `content-assembler`가 만든 `final/[키워드]-naver-copy.md`의 블록 순서를 유일한 원본으로 사용해 저장하고, 썸네일을 첫 번째 이미지 블록으로 고정한다. 본문 이미지와 썸네일 모두 첨부 업로드 후 반환된 `file-upload://...` 소스를 사용한다. 외부 URL이나 로컬 이미지 경로만으로 저장하지 않는다.

베타 글의 Notion 속성은 `notion-config.md`와 실제 스키마를 사용하며, 최소한 `실행 ID`, `주제 ID`, `배치 ID`, `파이프라인 버전`, `전체 처리 시간(초)`, `품질 점수`, `성능 판정`, `실패 단계`, `재시도 횟수`, `사람 검수`, `검수 메모`, `검수 완료일`을 기록한다. 저장 성공과 품질 통과는 서로 다른 판정이다. 검수 메모에는 `visual_contract_check`, `visual_promise_check`, `asset_provenance_check`, `visual_type_match_check` 결과도 기록한다.

자동 품질 Gate의 최소 결과는 `freshness_check`, `title_promise_check`, `coverage_check`, `reader_question_check`, `repetition_check`, `differentiation_check`, `originality_check`, `relationship_map_check`, `visual_contract_check`, `visual_promise_check`, `asset_provenance_check`, `visual_type_match_check`와 각 결과의 `passed` 또는 `failed` 상태다. 하나라도 실패하면 다음 단계로 넘기지 않고 실패 단계와 재실행 조치를 기록한다.

## 단계별 보고

성공 시 `진행 상황: <단계명> 완료`, 실패 시 `오류: <단계명> - <원인>` 형식을 사용하고 즉시 중단한다.

최종 보고에는 성공·실패 단계, 실패 원인, 생성된 로컬 파일, Notion 항목과 남은 조치를 포함한다.

## 삭제 요청

사용자가 삭제를 요청하면 이번 베타 실행에서 생성한 로컬 파일과 이번 실행으로 만든 Notion 항목만 삭제한다. 삭제 대상 목록과 경로·식별자를 먼저 확인한다. 정식 자료, 기존 자료, 다른 실행의 결과는 절대 삭제하지 않는다.

Notion 테스트 페이지를 삭제할 때는 페이지 ID를 확인한 뒤 해당 테스트 페이지 하나만 대상으로 삼는다. 삭제 기능이 연결 도구에 없으면 임의의 기존 항목을 건드리지 말고 필요한 UI 조치와 확인 사항을 보고한다.
