# 리팩터링과 n8n 대체 실행 명세

> 상태: 실행 준비 완료
>
> 기준일: 2026-08-26 Asia/Seoul
>
> 목적: 현재 콘텐츠 파이프라인을 유지하면서 검증 코드와 로컬 실행 계층을 안전하게 정리한다.

## 0. 실행 방법

이 문서를 Codex에게 전달하고 다음 문장으로 실행한다.

> `refactor-and-n8n-replacement-runbook.md`를 읽고 이 문서의 순서와 중단 조건을 지켜 구현하라. 기존 콘텐츠·이미지·실행 로그는 삭제하거나 덮어쓰지 말고, 외부 Notion·Naver·브라우저 쓰기는 수행하지 말라. 각 단계 후 검증하고 마지막에 변경 파일, 테스트 결과, 미구현 항목을 보고하라.

이 문서는 설계 설명이 아니라 구현 작업 명세다. 단, 외부 서비스의 실제 저장·임시저장·게시와 macOS 작업 등록은 별도 승인 전까지 실행하지 않는다.

## 1. 고정 제약

### 보존할 것

- `AGENTS.md`와 루트 단계 지침 파일
- `research/`, `drafts/`, `assets/`, `final/`, `runs/`, `baseline/`, `manifests/`, `.omo/evidence/`의 기존 파일
- 기존 `n8n-replacement-plan.md`
- 기존 CLI 명령의 이름과 성공·실패 종료 코드
- 콘텐츠 생성 순서

~~~text
topic-selector → researcher → writer → image-maker
→ content-assembler → notion-rider → naver-rider
~~~

### 금지할 것

- 기존 글·이미지·로그·기준선의 삭제, 이동, 자동 아카이브, 덮어쓰기
- `git reset`, `git checkout`, 광범위한 `rm` 명령
- Notion 페이지 생성·수정·복제·첨부 업로드
- Naver 입력·임시저장·게시
- 브라우저·Chrome·Computer 도구를 통한 외부 상태 변경
- 확인하지 않은 n8n export와 동등하다고 주장하는 것
- 확인하지 않은 Codex CLI 옵션을 추정해서 하드코딩하는 것

### 구현 범위

이번 작업에서 구현할 것은 다음 두 층이다.

1. `tools/`의 계약·manifest·Gate·Hook 검증 계층
2. n8n을 대신할 로컬 실행·상태·로그·재시도·dry-run 계층

콘텐츠 작성 로직과 이미지 생성 로직을 새로 만들지 않는다. 실행기는 각 단계의 지침과 기존 산출물을 호출·검증하는 역할만 한다.

## 2. 현재 상태와 사전 정리 판정

소스 수정 전에 다음을 읽기 전용으로 확인한다.

- `tools/workflow_contract.py`: Schema, manifest, log, Gate가 한 파일에 있음
- `tools/workflow_verifier.py`: CLI와 외부 Hook이 한 파일에 있음
- `pyproject.toml`: 프로젝트 의존성이 비어 있음
- `manifests/`: canonical JSON manifest가 없는지 확인
- 루트가 Git 저장소인지 확인하고, 아니면 오류로 간주하지 말고 보고만 함
- `__pycache__/`는 생성 캐시로 분류하고 자동 삭제하지 않음
- `tools/__init__.py`가 비어 있어도 삭제하지 않음
- `reports/`가 비어 있어도 삭제하지 않음

정리 판정은 다음과 같다.

~~~text
즉시 삭제할 파일: 없음
즉시 이동할 파일: 없음
추적성 정리가 필요한 항목: 실행 로그, legacy 산출물, manifest, 이미지 업로드 기록
구현을 막는 항목: uv가 없으면 설치 방법을 보고하고 소스 수정 전 중단
~~~

기존 파일의 이름과 내용이 서로 어긋나면 삭제하지 말고 `legacy`로 읽기 전용 취급한다. 새 실행부터 안정적인 `run_id`와 manifest를 사용한다.

## 3. 기준선과 검증 환경

### 3.1 기준선

소스 수정 전에 다음을 실행하고 결과를 기록한다. 기존 파일은 수정하지 않는다.

~~~bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q tools tests
python3 -m tools.workflow_verifier validate-log --run-log runs/beta-2026-08-26-energy-day.jsonl
python3 -m tools.workflow_verifier validate-log --run-log runs/beta-gate0-2026-08-26-9topics.jsonl
~~~

기준선에서 통과한 항목과 실패한 항목을 구분한다. 기존 run log의 strict schema 실패, batch digest 누락, energy digest 불일치는 기존 데이터로 보존한다. 성공으로 바꾸기 위해 과거 로그를 조작하지 않는다.

### 3.2 uv 환경

`uv`를 프로젝트 실행기와 lockfile 관리자로 사용한다. `uv`가 없으면 `pip`로 대체하지 말고 설치 필요 상태로 중단한다.

개발 dependency group에는 다음 역할을 포함한다.

- `pytest`
- `hypothesis`
- `ruff`
- `basedpyright`

QA group에는 필요할 때만 `playwright`를 포함한다. runtime에는 `jsonschema`를 추가한다. 실제 버전은 실행 시점에 호환 범위로 해석하고 `uv.lock`에 고정한다. lockfile은 직접 편집하지 않는다.

uv dependency groups와 lock/sync를 사용한다.
- https://docs.astral.sh/uv/concepts/projects/dependencies/
- https://docs.astral.sh/uv/concepts/projects/sync/

## 4. 계약 검증 리팩터링

### 4.1 단일 기준

`schemas/workflow-contract.schema.json`을 외부 계약의 단일 기준으로 유지한다.

- 새 JSON Schema 언어를 만들지 않는다.
- 현재 `_schema_errors()`가 담당하는 표준 규칙 해석을 제거한다.
- 표준 검증기와 프로젝트 고유 규칙을 분리한다.
- `additionalProperties`, `oneOf`, `anyOf`, `allOf`, `if/then`, `contains`, `$ref`, `format`을 임의로 축약하지 않는다.
- legacy log는 별도 호환 파서로 읽되, 새 optimized log는 strict schema를 통과해야 한다.

`jsonschema.Draft202012Validator`를 사용한다.

- 시작 시 `check_schema()`로 schema 자체를 검증한다.
- `iter_errors()`로 모든 오류를 수집하고 deterministic하게 정렬한다.
- `FormatChecker()`로 `date-time` 검사를 켠다.
- timezone 없는 timestamp는 Gate에서 승인으로 취급하지 않는다.

참고: https://python-jsonschema.readthedocs.io/en/stable/validate/

### 4.2 모듈 분리

다음 책임으로 분리한다. 기존 public CLI 동작과 함수 의미를 먼저 보존한다.

~~~text
tools/
├── contract_types.py       # JSON 경계 타입
├── schema_validation.py    # Draft 2020-12 검증과 오류 정렬
├── manifest.py             # ManifestFile, Manifest, build/verify
├── log_contract.py         # JSONL event와 legacy/optimized 판정
├── gate.py                 # 승인, 만료, target, digest, Gate A/B
├── tool_policy.py          # 외부 도구별 read/write/destructive 정책
├── automation_runner.py    # 상태, lock, retry, recovery, dry-run
├── image_quality.py        # 기존 이미지 검증
└── workflow_verifier.py    # CLI와 Hook 진입점, dispatch만 담당
~~~

분리 후에도 다음 명령을 유지한다.

~~~text
manifest
verify-manifest
gate
validate-log
validate-schema
validate-image-metadata
validate-image-quality
hook
~~~

Pydantic으로 JSON Schema를 다시 생성하는 전면 전환은 이번 작업에서 하지 않는다. 외부 계약과 내부 타입의 이중 기준을 만들지 않도록 기존 dataclass를 우선 유지한다. Pydantic v2 `TypeAdapter`는 내부 타입 변환이 실제로 필요해진 뒤 parity test를 먼저 추가하고 검토한다.
참고: https://pydantic.dev/docs/validation/latest/concepts/type_adapter/

### 4.3 Gate 승인 시각

승인 timestamp는 문자열로 정렬하지 않는다.

1. ISO-8601 문자열을 aware `datetime`으로 파싱한다.
2. timezone 없는 값은 무효로 처리한다.
3. 비교할 때 UTC로 정규화한다.
4. 가장 최근 승인·거부·만료 이벤트를 실제 시각 순서로 선택한다.
5. KST 표시가 필요할 때만 `ZoneInfo("Asia/Seoul")`를 사용한다.

Batch 승인에는 모든 `run_id`에 대한 `per_run_artifact_digests`가 있어야 한다. 승인 digest가 현재 manifest digest와 다르면 차단한다.

## 5. 외부 쓰기 Hook

현재 payload 문자열에 `create`, `update`, `save` 같은 단어가 있는지 추측하는 방식을 제거한다.

### 정책

- 실제 도구명과 명시적 action을 기준으로 판정한다.
- 읽기 도구의 검색어에 쓰기 단어가 들어 있어도 읽기로 처리한다.
- `duplicate_page`, `create_page`, `update_page`, `create_attachment`, `move_page`는 쓰기 또는 위험 작업으로 등록한다.
- Naver submit/save/post와 browser form fill/evaluate/submit도 등록한다.
- 등록되지 않은 외부 도구는 기본 거부한다.
- 외부 작업으로 분류된 모든 호출은 `WORKFLOW_GATE`, `WORKFLOW_MANIFEST`, `WORKFLOW_RUN_LOG`, `WORKFLOW_RUN_ID`, `WORKFLOW_TARGET_ID`를 확인한다.
- 검증 중 예외가 나도 구조화된 `deny`를 반환한다.
- 읽기 호출에는 불필요한 승인 검사를 하지 않는다.

Hook 결과는 외부 작업마다 다음 중 하나여야 한다.

~~~json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "allow | deny",
    "permissionDecisionReason": "safe explanation without secrets"
  }
}
~~~

## 6. canonical manifest와 로그

새 실행은 항상 다음을 지킨다.

- `pipeline_version=workflow-optimized-v1`
- 모든 optimized event에 `pipeline_version`
- `manifests/<run_id>-workflow-manifest.json` 보존
- manifest 파일 순서와 SHA-256 보존
- `artifact_digest`는 자기 필드를 제외한 canonical JSON으로 계산
- `run_id`, `topic_id`, 파일 경로의 연결표를 상태에 기록
- 업로드한 파일이 local manifest와 다르면 성공으로 기록하지 않음

Batch approval은 하나의 공통 digest로 여러 실행을 덮지 않는다. `run_ids`와 `per_run_artifact_digests`의 key set이 정확히 일치해야 한다.

기존 legacy 로그는 수정하지 않는다. `validate-log`는 읽을 수 있지만, 새 optimized 실행은 strict schema를 통과하지 않으면 다음 단계로 넘어가지 않는다.

## 7. n8n 대체 로컬 실행기

실제 n8n 서버를 만들지 않는다. 현재 프로젝트에 맞는 얇은 로컬 실행기를 만든다.

~~~text
macOS launchd
  └─ 실행만 시작
       └─ python -m tools.automation_runner
            ├─ run_id 생성
            ├─ 중복 실행 lock
            ├─ 단계별 상태 전이
            ├─ 입력·출력 hash 확인
            ├─ dry-run / retry / recovery
            ├─ JSONL 로그
            └─ 종료 코드·상태 요약
~~~

### 7.1 runner 명령

최소 명령을 구현한다.

~~~bash
python -m tools.automation_runner run daily-generate --mode beta --keyword <키워드>
python -m tools.automation_runner run weekly-improve --mode beta
python -m tools.automation_runner run naver-publish --mode formal --run-id <run_id> --dry-run
python -m tools.automation_runner status --run-id <run_id>
python -m tools.automation_runner recover --run-id <run_id> --dry-run
~~~

### 7.2 책임

- `daily-generate`: 로컬 콘텐츠 단계의 순서를 확인하고 결과를 기록한다.
- `weekly-improve`: 실행 로그와 산출물을 읽기 전용으로 집계한다. 지침과 콘텐츠를 자동 수정하지 않는다.
- `naver-publish`: Gate B가 없으면 dry-run으로만 종료한다. 최종 게시 동작은 구현하지 않는다.
- 실패한 단계 이후의 단계는 실행하지 않는다.
- 일시적 오류만 제한된 지수 백오프로 재시도한다.
- 검수 실패, 인증, 사용자 승인, 외부 쓰기 응답 불확실성은 자동 재시도하지 않는다.
- 완료된 단계의 입력 hash가 바뀌면 재개하지 않고 다시 검수한다.
- 외부 요청 후 응답이 끊기면 재전송하지 말고 `blocked` 상태로 남긴다.

상태는 다음으로 제한한다.

~~~text
pending → running → passed
                   ├→ failed
                   ├→ blocked
                   └→ skipped
~~~

상태 파일은 임시 경로에 쓴 뒤 원자적으로 교체한다. 로그에는 token, cookie, secret, 개인 메시지를 기록하지 않는다.

### 7.3 launchd

다음 plist 파일을 생성하되 자동 등록하지 않는다.

~~~text
launchd/com.naverblog.daily-generate.plist
launchd/com.naverblog.weekly-improve.plist
launchd/com.naverblog.naver-publish.plist
~~~

각 plist는 절대 실행 경로, `WorkingDirectory`, `ProgramArguments`, 표준 출력·오류 경로를 명시한다. `launchctl bootstrap`과 `kickstart`는 별도 승인 전에는 호출하지 않는다.

실제 Codex CLI 옵션은 설치된 버전의 `codex --help`로 확인한다.

## 8. 테스트 계획

기존 `unittest`는 임시로 유지하고, 새 기준 실행기는 `pytest`로 전환한다.

### 필수 단위 테스트

- Draft 2020-12의 `oneOf`, `anyOf`, `allOf`, `$ref`, `if/then`, `contains`
- timezone 필수와 `FormatChecker`
- mixed offset 승인 timestamp의 실제 순서
- approved 뒤 rejected가 오면 차단
- batch digest map 누락·추가 key·오염 digest
- manifest 파일 변경·경로 탈출·thumbnail/body 중복
- `duplicate_page`와 Naver submit의 Gate 없는 차단
- 검색어에 쓰기 단어가 들어간 경우 오탐 방지
- 알 수 없는 외부 도구의 기본 거부
- lock 중복 실행과 stale lock 회복
- 실패 단계 이후 단계 미실행

### Property-based 테스트

`Hypothesis`로 digest 입력, timezone offset, run id, 안전 경로, batch key set을 생성한다. 각 전략의 범위를 명시한다.
참고: https://hypothesis.readthedocs.io/en/latest/

### Hook dry-run 8종

1. Gate 환경변수 누락
2. manifest 누락
3. manifest digest 불일치
4. 승인 rejected
5. 승인 expired
6. batch `per_run_artifact_digests` 누락
7. 외부 쓰기 도구 미등록
8. 실행 lock 충돌

모든 경우 외부 호출 없이 구조화된 `deny` 또는 `blocked`가 나와야 한다.

## 9. 실제 화면 QA

QA dependency가 설치되고 안전한 읽기 전용 브라우저 표면이 제공될 때만 `Playwright`를 사용한다.

- 390×844 모바일 viewport
- 제목·소제목·목록·링크·본문 이미지 순서
- SVG/PNG 글자 깨짐과 font fallback
- screenshot 및 screenshot SHA-256 보존
- Notion/Naver 외부 쓰기 없이 렌더링만 확인

Playwright device emulation과 screenshot API를 사용한다.
- https://playwright.dev/python/docs/emulation
- https://playwright.dev/python/docs/screenshots

브라우저 표면이 없으면 QA를 통과로 표시하지 말고 `unavailable`로 기록한다.

## 10. 구현 순서와 통과 조건

### Phase A. 기준선

- 사전 정리 판정 완료
- 기존 테스트·컴파일 결과 기록
- 기존 자료 삭제·이동 없음

### Phase B. 계약 검증

- 표준 `jsonschema` 검증기 연결
- custom schema evaluator 제거
- legacy log adapter와 strict optimized log 분리
- 기존 public CLI 동작 보존

### Phase C. Gate와 Hook

- explicit tool policy 적용
- 기본 거부 적용
- timezone-aware 승인 선택
- batch digest 검증
- Hook dry-run 8종 통과

### Phase D. manifest와 runner

- 새 실행마다 JSON manifest 보존
- `automation_runner` 상태·lock·retry·recovery 구현
- 모든 로컬 단계 dry-run 통과
- 단계 실패 시 다음 단계가 실행되지 않음

### Phase E. 도구 체계

~~~bash
uv run pytest -q
uv run ruff check tools tests
uv run ruff format --check tools tests
uv run basedpyright
python3 -m compileall -q tools tests
~~~

모든 명령을 실행할 수 없으면 성공으로 보고하지 말고 누락 도구를 보고한다. `ty`는 선택적으로 shadow check를 실행하되 첫 구현의 유일한 타입 Gate로 교체하지 않는다.

### Phase F. launchd 파일

- plist 문법 검증
- 절대 경로와 로그 경로 확인
- `launchctl bootstrap`은 호출하지 않음
- dry-run 명령으로 실행기와 plist 인자만 검증

### Phase G. 외부 연동 준비

- Notion/Naver adapter의 request/response 타입과 dry-run만 구현
- 베타 Notion 쓰기 조건과 Gate A/B 입력을 검증하는 테스트 작성
- 베타 Notion 쓰기는 Q1·manifest·지정 데이터 소스 검증 후 허용하고, 정식 외부 쓰기는 사용자 승인 전까지 호출하지 않음
- Naver 최종 게시 기능은 구현하지 않음

## 11. 중단 조건

다음 중 하나이면 해당 단계에서 중단하고 원인과 파일을 보고한다.

- 기존 산출물을 덮어써야만 진행할 수 있음
- `uv` 없이 의존성 재현이 불가능함
- 완전한 n8n 동등성이 요구되지만 export·화면 계약이 없음
- 테스트 실패 원인을 고치지 않고 다음 단계로 가야 함
- 외부 쓰기 Hook이 명시적 `deny`를 내리지 않음
- manifest digest와 실제 파일이 다름
- 외부 인증·승인·응답 불확실성을 추정으로 통과시켜야 함
- 파일 삭제·이동·Git 강제 복원이 필요함

## 12. 최종 보고 형식

실행자는 마지막에 다음을 보고한다.

~~~text
진행 상황: 리팩터링·n8n 대체 실행 계층 구현 완료

변경 파일:
- <path>: <역할>

검증:
- pytest: <pass/fail/not-run>
- ruff: <pass/fail/not-run>
- basedpyright: <pass/fail/not-run>
- compileall: <pass/fail>
- Hook dry-run 8종: <pass/fail>
- external write: 반드시 not-run

보류:
- 실제 Notion 저장
- 실제 Naver 임시저장·게시
- launchd 등록
- 확인하지 못한 n8n 동등성

오류: <없음 또는 원인>
~~~

`진행 상황: ... 완료`라고 보고하려면 Phase A부터 Phase G까지의 통과 조건과 외부 쓰기 금지를 모두 확인해야 한다.
