# 로컬 전용 31개 커밋 전수 감사

기준 시점: 2026-10-26 KST
비교 기준: GitHub `origin/main` `97eb6c228336ea227c9417ab450b5d5c9a853907`
로컬 원본: `main` `044dde45d3e0b2c1410dc63a59b46e7ec95d8e51`

## 안전 상태와 재현 가능한 백업

- 원본 dirty checkout을 조사 대상으로 삼았으며 경로는 개인정보 보호를 위해 공개하지 않는다. 조사 중 HEAD와 파일 변경을 수정하지 않았다. HEAD는 전후 동일하고 staged 변경은 없었다. tracked 변경 106개(삭제 2개 포함), 미추적 파일 481개였다.
- 최신 원격을 별도 worktree에서 fetch한 시점 분기는 로컬 전용 31개, 원격 전용 214개다. 이전 보고서의 207 behind에서 원격이 7개 더 진행했다. `main`에 checkout, reset, rebase, merge, cherry-pick, pull, push를 수행하지 않았다.
- 영구 복구 백업은 확인된 로컬 Documents 복구 폴더에 보관했다(정확한 개인 경로는 공개 문서에서 생략, GitHub 업로드 금지). 기존 임시 스냅샷도 실행 시 존재했다.
- 백업 전용 스크립트는 PR #15의 `scripts/local_main_reconcile.py`다. 실행 전 PR #15의 코드/테스트를 확인했으며 `tests/test_local_main_reconcile.py`는 16개 통과했다.
- 기존 임시 스냅샷의 bundle/patch/tar/status 각 SHA-256이 이전 `SHA256SUMS`와 같았다. 그 스냅샷에는 `AUDIT.md` 및 `audit.json`이 없었으며, 이 실행에서 있다고 가정하지 않았다.
- 새 백업은 기존 스냅샷 복제 해시, refs bundle 생성 및 별도 bare 저장소 import/fsck, 원시 Git index 복사, staged/unstaged patch, tracked 작업 파일 바이트 197개, 미추적 파일 481개의 archive 검증을 완료했다. 백업 manifest의 19개 payload SHA-256이 모두 일치했다. source-state 전후 값(HEAD, 상태, 실제 tracked 바이트, index 해시)이 동일했다.
- 중단 조건도 확인했다: unmerged index 없음, split-index 없음, gitlink submodule 없음, 중첩 `.git` 없음. 원본 객체 형식은 SHA-1이며 bundle은 refs 17개를 복원했다. `/tmp` 원본은 유지했다.

## 분류 요약

| 분류 | 개수 | 의미 |
|---|---:|---|
| `already_implemented` | 1 | 최신 원격에서 입력/출력 동작과 테스트가 확인됨 |
| `partly_implemented` | 11 | 목표 일부만 현재 다른 흐름에서 제공되거나 경계가 다름 |
| `not_implemented` | 2 | 원격 기준으로 현행 코드에 동작이 없었음 |
| `retired` | 17 | 현행 v2/v3 경로에서 더는 사용하지 않는 legacy 흐름 |
| **합계** | **31** | 모든 SHA를 검토함 |

분류는 `origin/main` 기준이다. 이 PR에서 고친 누락도 기준 시점 분류는 그대로 두고 포팅 결과를 별도로 표시한다. `git cherry`와 경로 일치는 보조 검색일 뿐 기능 동등성 판정 근거로 사용하지 않았다.

## 8월 커밋 7개 — PR #12 결과를 최신 원격에서 재확인

| SHA / 목적 | 로컬 동작·경계 | 최신 원격 대응 코드 및 테스트 | 판정·결정 |
|---|---|---|---|
| `8aa1a91d3bc643d03160904152c8f61dd81c2043` — beta Notion write 예외 | workflow hook 및 tool policy에 좁은 beta Gate A Notion 쓰기 예외 추가. `tools/workflow_hook.py`, `tools/gate.py`, `tools/tool_policy.py`와 hook/policy 테스트가 대상. | 원격 `tools/external_adapter.py::plan_external_write`는 계획만 출력하고 실행하지 않는다. `tests/test_external_adapter.py::test_adapter_rejects_non_dry_run_before_external_execution` 및 `tests/test_tool_policy.py`가 외부 실행을 차단한다. v2의 승인은 `tools/blog_v2.py::approve_bundle`/`export_copy`로 로컬 복사만 허용. | `retired` — 현재의 외부 계정 쓰기 금지와 양립하지 않는 구형 예외. 재도입하지 않음. |
| `3c5d34a7541c9e6d03535dc540f750aaeaba27a0` — 7단계 생산 workflow | Dashboard와 runner에서 Notion Q2 및 Naver draft save로 이어지는 온라인 workflow를 통합. `tools/runner_execution.py`, `tools/gate.py`, 대시보드 및 launchd 경로를 포함. | `tools/blog_v2.py::validate_pack/build_bundle/verify_bundle/approve_bundle/export_copy`; `editorial_studio/v3/engine.py::generate`. `tests/test_blog_v2.py`, `tests/test_v3_engine.py`, `tests/test_naver_search_v2.py`가 입력 검증·해시·출처 및 이미지 경계를 확인한다. | `partly_implemented` — 근거팩→검수 초안→사람 승인→로컬 복사와 편집 preview는 구현. Notion/Naver 쓰기 및 실제 게시 부분은 폐기. |
| `3eeb123033c74235a63814dfccb515e219bb656f` — Q1 생성 재시도 | 구형 Codex `content-assembler` 재생성을 feedback으로 반복하는 `tools/codex_stage_executor.py` 경로 및 Q1 retry 테스트. | 원격 v2는 제공된 evidence pack을 결정적으로 조립하며, v3도 검토된 source pack만 입력으로 받는다. `tests/test_blog_v2.py`, `tests/test_v3_engine.py`에는 생성 producer 재시도가 없다. `docs/REBUILD_PLAN.md`는 실시간 수집/LLM 집필을 차기로 분리한다. | `retired` — 재시도할 생성/Q1 producer가 현행 엔진에 없음. |
| `765df543d213dbcaf08e5cfb61f08b34b48ffc50` — 외부 retry action | 실패한 Notion/Naver write를 dashboard에서 다시 수행하는 action 및 retry 처리. | 외부 호출을 비활성화한 `tools/external_adapter.py`; 로컬 검수만 하는 `tools/review_site_v2.py`. 외부 실행 차단은 `tests/test_external_adapter.py`에서 확인. | `retired` — 외부 저장 action 자체가 현행 제품 경로에 없음. |
| `c24d4ddcb6527f4d35e395da4eab0fb9b7c9b9d8` — Naver preview title 파싱 | legacy Naver preview의 제목 markup을 browser 입력용 title로 추출. | `tools/blog_v2.py::validate_pack/render_draft`는 구조화된 `title`을 검증·사용한다. `tests/test_blog_v2.py`의 pack/초안 테스트가 이를 확인한다. | `retired` — editor title parser가 아닌 구조화 source pack을 입력으로 사용. |
| `eb49caf5d6e7421081e0d3dd460955a417ba7c7f` — 닫히지 않은 title block 처리 | `[TITLE]` 블록이 닫히지 않았을 때 legacy parser 예외 경로 처리. | 원격 v2 source pack에는 `[TITLE]` block parser가 없다. `tests/test_blog_v2.py`는 필드/스키마 입력을 검증한다. | `retired` — legacy 문법이 사라짐. |
| `c3e19a73bc3757016089a132272c61cbaf2f6351` — read-only browser research | Aside로 원문 페이지를 탐색·관찰하고 researcher stage가 읽기 전용임을 분류. | `tools/naver_search_v2.py::fetch_search`의 단건 후보 검색. `tests/test_naver_search_v2.py`는 기본 네트워크 0, 고정 host, 요청 한도, redirect·retry 거부, 응답 제한 및 비밀값 비노출을 확인. `workflows/v2/evidence-packet/intend.md`는 원문 자동 수집을 미구현으로 명시. | `partly_implemented` — 후보 검색은 있으나 원문 browser capture는 차기 범위. |

## 나머지 24개 — 개별 SHA 의미 감사

| # / SHA / 목적 | 로컬 코드·실제 동작 | 원격 대응 및 테스트 근거 | 판정·포팅 |
|---|---|---|---|
| 8 `053e48ae9fd6186e69cbb1f7cdfe64328a72b45d` — workflow 실행·write gate 강화 | 대규모 변경: `tools/codex_stage_executor.py`, `tools/stage_artifact_promotion.py`, `tools/startup_preflight.py`, `tools/notion_api*.py`, `tools/naver_gate.py`, `tools/gate.py`, credential redaction, Q1/Q2/target binding·Notion resume. 외부 계정 write 권한과 stage 실행에 영향. | 원격 v2 `tools/blog_v2.py::verify_bundle/approve_bundle/export_copy`, `tools/external_adapter.py`는 일부 무결성·승인·external-write 차단 제공. legacy `tools/gate.py::verify_gate` 및 `tests/test_gate_and_manifest_regressions.py`는 Gate 동작을 유지했지만 최신 Q1/run binding 회귀가 없었음. | `partly_implemented` — 구형 Codex/Notion/Naver 실행·resume 전체는 이식하지 않음. **현재 Gate의 최신 Q1 및 manifest/run 바인딩 누락만 이 PR에서 수정**하고 재현 테스트 8개를 추가. |
| 9 `32e74f103f2f24c98f07877f453237c5da63ff80` — 개발/실행 계약 분리 | `EXECUTION_AGENT.md` 신설; `tools/codex_stage_command.py::stage_prompt`가 AGENTS, execution contract, stage 문서를 읽고 staged `artifacts/` 경로만 반환. 계약 분리 테스트를 추가. | 원격 `AGENTS.md`와 `workflows/README.md`/각 `intend.md`가 프로젝트 지침과 workflow의 입력·출력·금지를 분리 기록한다. `tests/test_workflow_intend_docs.py`; 다만 Codex stage prompt producer는 없음. | `partly_implemented` — 의도/단계 문서의 분리는 있으나 구형 stage-prompt runtime은 없음. 현행 v2/v3 경로에 구형 runtime을 복구하지 않음. |
| 10 `3895dde8e00f892ff5ebfde1ea168dde02f6d0dc` — utility 회귀 테스트 | 테스트 전용: tool action 분류, external plan serialization, publication link fallback, Notion resource parsing 및 OS credential probe 경계. | 일부 목적은 `tests/test_tool_policy_boundaries.py`, `tests/test_unexercised_public_functions.py`, `tests/test_blog_v2.py`에서 대응. Credential probe, Notion resource transport, publication-link helper는 원격 코드에 없음. | `partly_implemented` — 현재 tool policy·로컬 승인 경계는 대응, 퇴역한 adapter helper는 미이식. |
| 11 `4cffe20ff8cca6ff4cff50389c13161faf927e85` — helper/policy 미실행 테스트 | 테스트 전용: Aside form/screenshot, transient failure classification, startup preflight, Notion response/resource parsing, Naver target handling. | `tools/runner_execution.py::is_transient_error`/`tests/test_unexercised_public_functions.py`; `tools/naver_search_v2.py`의 네트워크 경계 테스트가 일부 목적을 다른 인터페이스로 충족. Aside/Notion browser transport는 없음. | `partly_implemented` — 오류 분류·검색 보안은 존재. 구형 live adapter/preflight 테스트는 이식하지 않음. |
| 12 `443a6e23352e0d0a72216385f2e694981b2c0e26` — browser adapter 경계 | 테스트 전용: Naver editor 준비와 저장 순서, Aside target 제한·페이지 추출 경계. | `tools/external_adapter.py`는 외부 write를 실행하지 않고, `tests/test_external_adapter.py::test_adapter_rejects_non_dry_run_before_external_execution`가 이를 확인. | `retired` — Naver browser write adapter는 연결하지 않는 것이 명시된 현재 경계. |
| 13 `cbf36b74acb032301c079b43680db3a3b2bcd718` — adapter 실행/image metadata mapping | 테스트 전용: Notion payload `asset_metadata`와 이미지 파일명 매핑 및 Aside subprocess 출력. | `tools/manifest.py::build_manifest/verify_manifest`는 로컬 asset role·순서·hash를 검사하고 `tests/test_manifest_parsing_boundaries.py`가 manifest 구조를 검증한다. Notion upload payload 생성은 원격에 없음. | `retired` — 로컬 image provenance는 v3에서 검증하지만 Notion upload adapter는 사용하지 않음. |
| 14 `a4ce0276203ed99a2c7002ff20a28ac3ce1808ee` — runner status path | 테스트 전용: 상태/manifest 조회, 누락된 환경 변수일 때 hook deny, model snapshot stage 계약. | `tools/runner_execution.py::get_status`, `tests/test_unexercised_public_functions.py`, `tests/test_runner_cli_boundaries.py`, `tools/workflow_verifier.py`와 `tests/test_workflow_hook.py`가 로컬 조회·fail-closed behavior를 검사. | `partly_implemented` — status 및 fail-closed 일부 대응, model snapshot producer는 없음. |
| 15 `7369452e885a15fc7ecf95d3fcf37de208fef4cc` — cancellation scope policy | `tools/run_cancellation.py` 신규 기능: run/batch/child ID와 nonce hash로 immutable `O_EXCL/O_NOFOLLOW` cancellation record를 만들고 scope/status를 조회. 관련 취소 전 테스트 포함. | 원격 v2/v3에는 child queue/cancellation 상태가 없다. `tools/runner_types.py::RunStatus`에는 cancellation state가 없고 `tests/test_v3_engine.py`는 결정적 preview만 검사. | `retired` — 현행 batch/child 실행 제품 흐름이 없음. |
| 16 `62928267c6e1818574d33246924ae7e19c08523c` — research freshness digest | `tools/research_freshness.py` 신규 기능: keyword/date, research bytes, selection/instruction digest로 같은 run 재사용을 고정하고 다른 run의 검색/관찰 해시를 대조. | `tools/blog_v2.py::validate_pack/verify_bundle`, `editorial_studio/v3/engine.py::_source_category`가 기준일·팩/사진 hash를 검증. `tests/test_blog_v2.py` tamper tests 및 `tests/test_v3_engine.py::test_refuse_tampered_original_without_claiming_success`가 실행됨. 실시간 재관찰/cache reuse는 없음. | `partly_implemented` — 입력·산출물 freshness 일부는 검증되나 원문 재관찰은 미구현. v2/v3에는 해당 producer/cache가 없어 이식 보류. |
| 17 `fbde33339c6b1090ba2ee72f0d8bea35c3cca0d3` — workflow performance report | `tools/workflow_performance_report.py` 신규 기능 및 테스트: run log에서 stage p50/p90, retry/failure, Codex usage completeness·cache, malformed 기록을 집계해 report 생성. | 원격 `editorial_studio/benchmark.py`는 고정 샘플 편집 점수 측정이며 process usage/runtime 리포트와 다르다. 동등 report/producer/테스트 없음. | `not_implemented` — 원격에 동작은 없지만 active v2/v3는 해당 Codex usage log를 만들지 않는다. 관측 producer와 목적 합의 전 포팅 보류. |
| 18 `1baf4f9e12c058adc4e2bb02d38150a14a198107` — topic selection decision CLI | `tools/topic_selection_decision_cli.py`가 capture ID/날짜에 맞는 Creator Advisor snapshot을 읽고 예약(reserve) 결정을 출력. | `tools/naver_search_v2.py`는 검색 후보를 반환하나 주제를 예약·자동 승인하지 않는다. `tests/test_naver_search_v2.py`는 조회만 검증. | `retired` — Creator Advisor snapshot/reservation 생산자가 현행 v2/v3에 없음. |
| 19 `62e015318b87b1f7846bc22e086ba18f2e7425fc` — child retry/recovery | `tools/dashboard_manual_actions.py`가 confirmation nonce, 기존 run ID, retry exhaustion, resume 및 후속 action lifecycle을 처리. | `tools/runner_execution.py::recover_job`은 변경된 input hash를 거부하는 동기 local recovery (`tests/test_automation_runner.py::test_recovery_refuses_changed_input_hash`). persisted dashboard child/action lifecycle과 외부 저장 재개는 없음. | `retired` — dashboard child execution과 외부 write recovery는 현행 범위가 아님. |
| 20 `7509fab0eff200dc08433038d043fe39b63eb3c1` — manual task lifecycle/history | `tools/dashboard_manual_batch.py`, `tools/dashboard_manual_store.py`, `tools/dashboard_snapshot_cache.py`, `tools/dashboard_tasks.py`, schema로 batch/child/history/cursor 상태를 영속화. | 원격 `tools/runner_types.py::RunStatus`는 로컬 단계 상태만 표현. `README.md` 및 `workflows/v2/*/intend.md`는 수동 review/export를 정의하고 batch dashboard는 없음. | `retired` — 현행 v2/v3에는 장기 실행 manual batch manager가 없음. |
| 21 `b2e95dc995ce40c9d9c5f3a2382ec95e5fd1735d` — local quality/copy normalization | 테스트 전용: 최신 article quality event의 local integrity 실패 차단, `[TABLE]/[ROW]` copy format 유지. | `tools/blog_v2.py::verify_bundle/approve_bundle/export_copy`; `tests/test_blog_v2.py::test_tampering_invalidates_both_approval_and_export`, `test_modified_evidence_is_blocked`, `test_fake_or_stale_approval_is_rejected`. 구형 table tag normalizer는 없음. | `partly_implemented` — 해시/승인/수동 export integrity는 대응, 구형 editor copy grammar는 미이식. |
| 22 `626b0fe0b644017bfb40dbd17f6d247efea0b63f` — legacy runner adapter factory | 테스트 전용: recovery 경로의 Notion adapter factory 및 `draft_saved` 결과. | 원격 `tools/runner_cli.py`/`tools/runner_execution.py`는 로컬 request/state만 다룬다. `tests/test_runner_cli_boundaries.py`는 로컬 dispatch만 검사. | `retired` — 외부 Notion adapter recovery가 없음. |
| 23 `7ecdaaf739eb9ce2094ec7f8dff0724fe6d000b1` — cancellation before stage | 테스트 전용: cancellation record가 있으면 stage 실행 전 run을 cancelled 상태로 저장. | 원격 runner는 child cancellation control record를 읽지 않고 `RunStatus`에 cancelled가 없다. 현행 v2/v3 preview CI에도 취소 흐름 없음. | `retired` — commit #15의 legacy cancellation manager와 함께 제거. |
| 24 `73779507964da12c813546a2deea20f223f769a9` — secure state rollback | `tools/runner_secure_fs.py/core.py`와 테스트 추가: directory descriptor, `O_NOFOLLOW`, parent identity 확인, 경로 교체 시 이전 상태 rollback. 예외/경쟁 경로에서 상태 파일 오기록을 막음. | 기준 원격 `tools/runner_state.py::atomic_write_json`은 pathname 기반 `mkstemp/os.replace`였고 parent 교체를 검사하지 않았음. `tests/test_automation_runner.py`는 정상 쓰기/입력 변경 복구만 검증. | `not_implemented` — **이 PR에서 안전한 descriptor 기반 state write와 parent-swap/symlink 및 오류 정리 회귀 테스트 5개를 별도 구현.** dirty-only helper나 전체 legacy secure filesystem module은 포팅하지 않음. |
| 25 `ce5c6cb2a0e0d7e763dc97e926f95f8c00aeb42e` — dashboard routes/health | 테스트 전용: verified preview에 title/images/table/Notion link를 표시하고 `/api/runs`, image route, health probe를 검사. | `tools/review_site_v2.py::write_preview/render_preview`, `tests/test_review_site_v2.py`는 evidence digest 검증·HTML escape·CSP·symlink/overwrite/tamper 방지를 검사. HTTP dashboard API/health/Notion link는 없음. | `partly_implemented` — 안전한 local preview는 구현, dashboard API 및 Notion 연결은 제외. |
| 26 `d7b53d0e88283337132a9ac39d0211eb92b4fa6b` — dashboard CLI help | 테스트 전용: server를 시작하지 않고 구형 `tools.test_dashboard --help`만 반환. | `tools.test_dashboard`는 원격에 없음. `tools.review_site_v2`는 정적 preview 생성 CLI로 다른 동작. | `retired` — dashboard server CLI 제거. |
| 27 `0b213fecb6ac57fadb7f10a700fae8c7c1c8ec58` — manifest output writer | 테스트 전용: nested output 경로를 생성해 manifest JSON을 쓰고 digest/path를 출력. | `tools/workflow_verifier.py::main/_write_json`; `tests/test_workflow_verifier_dispatch.py::test_manifest_command_writes_json_and_reports_digest`가 nested path, digest 및 stdout을 검증. | `already_implemented` — 입력 인자는 바뀌었지만 관찰되는 동작이 동일하게 테스트됨. |
| 28 `ab340e76ca37ca1aaa9d1dd6b1521a13c0a57d17` — default startup preflight | 테스트 전용: 기본 dependency가 Notion 접근 확인과 locked/offline `uv sync`를 실행하고 timeout을 적용. | 현재 실행 경로는 v2/v3 preview이며 Notion startup access probe가 없다. `workflows/v2/evidence-packet/intend.md`는 외부 source/account access 한계를 명시. | `retired` — 계정 연동 preflight가 없는 현재 로컬/offline 엔진에 맞지 않음. |
| 29 `0fe8187152f735ef4d3758e53291e5d3afee88f1` — research browser capture | `tools/research_browser_capture.py`, `research_capture_policy.py`, `research_crawler_bridge.py`, `research_official_images.py` 추가: Aside 관찰, source profile/budget, bounded official image download 및 capture ledger. | `tools/naver_search_v2.py`는 후보만 조회. `tools/blog_v2.py`는 출처/주장을 source pack에 고정하고 `editorial_studio/v3/engine.py::_source_category`는 원본 사진 hash·credit·license·page URL을 검증. `tests/test_naver_search_v2.py`, `tests/test_v3_engine.py`가 대응 경계 테스트. 브라우저 원문 자동 수집은 `docs/REBUILD_PLAN.md`상 차기. | `partly_implemented` — 출처 후보·팩·원본 이미지 provenance 일부는 있지만 browser capture는 미구현. 원문 수집 범위·권리 검토 전 포팅 보류. |
| 30 `1cdb929ccbe2c3e2c2570b448176e1702db53bbb` — persisted dashboard recovery | 테스트 전용: 재시작 후 confirmation active action을 복구하고 LOCAL_ONLY 결과로 clear. | v2 `tools/blog_v2.py::approve_bundle`는 immutable local approval을 만들고 export 시 digest를 다시 확인. `tests/test_blog_v2.py` stale/tamper tests가 검증. 외부 confirmation/recovery manager는 없음. | `retired` — local approval file은 dashboard action resume와 다른 계약. |
| 31 `044dde45d3e0b2c1410dc63a59b46e7ec95d8e51` — Notion operation authorization | 테스트 전용: Notion API `write_and_verify`의 create-page operation authorizer와 manifest digest binding을 검사. | 원격 `tools/tool_policy.py`/`tests/test_tool_policy.py`는 일반 external tool call을 기본 거부하며, `tools/external_adapter.py`에 live Notion transport는 없음. | `retired` — Notion API write가 current product에 연결되지 않음. |

## 포팅·검증 결과

- 포팅 1 — **승인 무결성 (로컬 SHA #8, `053e48ae9fd6186e69cbb1f7cdfe64328a72b45d`)**: 기준 원격 Gate는 과거 Q1/Q2 성공 하나만 있어도 허용하고 manifest `run_id`와 요청 run을 묶지 않았다. `tools/gate.py::verify_gate`는 이제 정확한 manifest run을 확인하고 최신 같은 run/topic Q1 및 Gate B Q2가 성공이며 해당 성공 뒤 승인되었는지 확인한다. 실패 전/후 승인, 다른 run/topic, 재시도 후 새 승인 동작을 회귀 테스트 16개로 고정했다.
- 포팅 2 — **상태파일 경로 무결성 (로컬 SHA #24, `73779507964da12c813546a2deea20f223f769a9`)**: `tools/runner_state.py::atomic_write_json`은 directory descriptor 기반 no-follow 열기/교체, parent chain identity 재검증, 교체 경쟁 시 이전 bytes rollback을 사용한다. 실제 parent rename/swap 및 symlink 거부, 정상 JSON writer, fdopen 오류 후 descriptor 소유권 및 fstat 오류 정리를 5개 테스트로 고정했다. 로컬 legacy 모듈 전체를 복사하지 않았다.
- 포팅 보류: research browser capture, Creator Advisor reservation, performance telemetry producer, child/batch dashboard, Naver/Notion live adapter, stage Q1 generation retry. 현행 v2/v3에서 producer나 승인된 external workflow가 없거나 명시적으로 다음 단계로 남아 있다.
- 원본 로컬 전용 커밋을 cherry-pick하거나 merge하지 않았다. 이 PR은 최신 원격 기반의 새 feature 브랜치에서 보고서와 선택한 두 보안 동작만 구현한다.

## 테스트·품질·CI

- 검증 대상 소스 revision: `01494b66ad72ea60f38cd7ecc74bfa1906ab6e35` (보고서 문서와 이후 CI 결과 기록 커밋은 별도).
- 전체 테스트 명령 `./.venv/bin/coverage run --branch -m pytest -q`: **462 passed, 1 skipped, 15 subtests passed, 22 warnings**. 유일한 skip은 저장소에 포함되지 않은 authentic 2026-08-26 실행 로그가 필요한 테스트로, 해당 과거 로그와의 호환성은 미확인이다. synthetic fixture는 별도 검사되며 실제 과거 로그로 가장하지 않았다.
- 전체 branch/line coverage: `coverage json` + `scripts/coverage_audit.py` 결과 **88.02%**. `coverage report` 정수 표시 **88%**. Callable 본문 실행 입증 **128/132**; 미확인 4개는 3 lambda의 line trace 모호성과 Protocol 선언 1개다.
- 변경 단위 테스트: state writer/runner **13 passed**; Gate/manifest/workflow 관련 집중 선택 **51 passed**. 변경 파일 Ruff와 basedpyright 통과, `git diff --check` 통과.
- 변경 파일 Ruff와 basedpyright는 로컬에서 통과했다. 저장소 전체의 원격 기준 lint/type 수치는 동일 revision의 clean-base 측정 자료가 없어 보고하지 않는다. 둘은 현재 `python-quality.yml` 필수 job이 아니다.
- Python quality GitHub Actions는 PR에서 테스트 전체·branch coverage·function inventory를 실행한다. 원격 run ID와 Codex review 상태는 PR 생성 후 최종 revision evidence 확인 후 기록한다. CI/review 성공은 운영 배포가 아니다.

## 커밋·PR

- Feature 브랜치: `codex/reconcile-local-main-security-20261026` (base `origin/main` SHA 위에서 생성).
- 커밋: `c09c40c` Gate 승인 무결성, `a3ccc25` 상태파일 경로 보호, `01494b6` 오류 경로 descriptor 소유권 수정.
- 보고서 커밋, PR URL, 최신 PR revision CI 및 Codex review 결과는 PR 생성 후 검증해 기록한다.
- CI 및 Codex review: PR revision 기준 상태를 생성/완료 후 기록한다. 아직 운영 배포나 외부 Notion/Naver 저장을 수행하지 않았다.

## 안전한 로컬 동기화 절차

1. 원본 `main`과 백업을 그대로 유지하고, 이 PR 및 필요한 변경이 원하는 최신 `origin/main`에 반영되었는지 확인한다.
2. 당시에도 31개 과거 커밋을 통째로 push/cherry-pick하지 않는다. 남은 변경은 해당 시점 원격 기준 새 브랜치에서 동작 단위로 다시 검토한다.
3. 사용자가 로컬 동기화를 선택할 때만 원본 `main`의 staged/unstaged/untracked 변경을 별도로 분류·백업하고, 로컬 전용 31개 변경을 기능 단위로 적용할 계획을 확정한다.
4. 원본 변경과 필요한 이력이 검증된 뒤에도 이 작업의 안전 절차는 reset/rebase/force push를 쓰지 않는다. 작업 tree를 보존한 채 새 remote-main checkout을 기본값으로 하고, 원본 `main` 자체 갱신은 별도 승인 후 non-force fast-forward 가능 여부부터 확인한다. fast-forward 불가 시 중단하고 재검토한다.

원본 `main`은 본 요청의 끝까지 변경하지 않는다. 영구 백업과 임시 스냅샷은 복구 가능성을 사용자가 확인할 때까지 보존한다.
