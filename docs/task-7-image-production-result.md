# T07 이미지 제작 이력 결과 및 GPT Luna 인계

## 범위

2026-09-13 KST 현재 T07 변경은 이미지 제작 메타데이터의 `ai_generation`·`local_render` 분기 필드와 Pillow 경로용 renderer·입력 해시 필드를 스키마와 validator에 추가했다. 기존 이미지 원장과 실행 기록은 수정하지 않았다.

## 확인된 결과

| 시나리오 | 실행 | 관측된 결과 | 근거 |
|---|---|---|---|
| 기존 AI metadata 계약·품질 계약 | `uv run --no-sync pytest -q tests/test_image_quality.py tests/test_workflow_contract.py tests/test_codex_stage_executor.py` | 70 passed, exit 0 | `.omo/evidence/luna-workflow-bottleneck-optimization/task-7-docs-handoff/focused-integration-pytest.txt` |
| 변경 파일 정적 검사 | `uv run --no-sync ruff check tools/image_quality.py tests/test_image_quality.py` 및 `uv run --no-sync basedpyright tools/image_quality.py tests/test_image_quality.py` | Ruff exit 0; basedpyright 0 errors, 0 warnings, 0 notes | `.omo/evidence/luna-workflow-bottleneck-optimization/task-7-docs-handoff/ruff.txt`, `basedpyright.txt` |
| 스키마 문법 | `python3 -m json.tool schemas/workflow-contract.schema.json` | exit 0 | `.omo/evidence/luna-workflow-bottleneck-optimization/task-7-docs-handoff/schema-json-check.txt` |
| 실제 Pillow local-render probe | `PYTHONPATH=. uv run --no-sync python .omo/evidence/luna-workflow-bottleneck-optimization/task-7-docs-handoff/local-render-probe.py` | exit 0; local-render record와 renderer hash 누락 record가 모두 `unsupported locked provider/model`로 차단됨 | `.omo/evidence/luna-workflow-bottleneck-optimization/task-7-docs-handoff/local-render-probe.jsonl` |
| AI 호출 증거 부재 probe | 같은 probe의 `ai_locked_without_call_evidence` 시나리오 | `production_ready=true`로 수용됨 | `.omo/evidence/luna-workflow-bottleneck-optimization/task-7-docs-handoff/local-render-probe.jsonl` |

## 판정과 제한

T07은 구현·단위 회귀 검사까지 완료됐지만 운영 승격 기준은 아직 충족하지 않는다. 실제 Pillow 산출물과 renderer·입력·출력 해시를 연결한 레코드가 현재 validator에서 통과하지 않아 `local_render` 운영 실행은 미검증이다. 또한 현재 validator에는 AI 호출 증거 필드나 호출 증거 검사가 없어, 호출 없이 작성한 OpenAI `locked` 레코드가 `production_ready=true`가 되는 결함이 남아 있다. 따라서 이 결과는 전체 운영 E2E, Notion 저장, 네이버 입력·임시저장, 전체 pytest green을 의미하지 않는다.

GPT Luna 후속 작업:

1. `tools/image_quality.py`의 local-render snapshot/version 비교를 실제 `pillow-<version>` 형식과 일치시킨다.
2. AI metadata에 호스트가 검증한 호출 증거를 연결하고, self-report만으로 `production_ready`가 되지 않도록 차단한다.
3. renderer·폰트·입력·출력 해시 변조, 기존 레코드 호환, 저품질 자산 승격 차단을 회귀 테스트로 추가한다.
4. 위 수정 후 같은 probe와 T07 focused suite를 재실행하고, 그 결과를 새 evidence artifact에 남긴다. 전체 pytest 결과는 별도 실행 결과가 있을 때만 보고한다.

## 완료 범위

- 구현: 스키마와 validator에 제작 방식 분기 필드가 존재한다.
- 로컬 검증: focused pytest·Ruff·basedpyright·schema JSON 문법은 통과했다.
- 운영 검증: 실제 운영 local-render 승격, AI 호출 증명, Notion·네이버 외부 쓰기는 수행하지 않았고 미검증으로 남긴다.
