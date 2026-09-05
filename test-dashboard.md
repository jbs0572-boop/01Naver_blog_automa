# 로컬 QA 대시보드

첨부된 `1_2강.md`의 문제 정의·상태 설계·검증 원칙을 이 프로젝트의 단일 실행 계약에 맞춰 적용한 로컬 실행·검수 화면이다. 외부 쓰기는 연결된 어댑터가 준비된 경우에만 실행한다.

## 실행

프로젝트 루트에서 실행한다.

```bash
uv run python -m tools.test_dashboard --root . --demo
```

브라우저에서 `http://127.0.0.1:8765`를 연다. `--demo`는 실제 로그와 관계없이 메모리 fixture만 표시한다. `--demo`를 빼면 `runs/*.jsonl`과 `.automation/logs/*.jsonl`을 읽고, 로그가 없을 때는 빈 상태가 표시된다.

Notion과 네이버 외부 쓰기를 연결하려면 인증과 감독형 선택자 검증이 끝난 `naver-config.md`에서 다음처럼 실행한다.

```bash
uv run python -m tools.test_dashboard --root . --live-writes
```

이 옵션은 Aside CLI의 결정적 `repl` 경로를 네이버 브라우저 어댑터에 연결한다. 설정 파일이 없거나 선택자 상태가 `verified`가 아니면 외부 쓰기를 시작하지 않는다. Aside CLI의 `exec -m` 모델 선택은 자연어 에이전트 실행용이고, 이 `repl` 경로에는 모델 선택이 필요하지 않다.

화면의 `MANUAL RUN`에서 주제 입력 방식을 하나 선택하고 KST 정보 기준일을 입력한다. `자동 선정`은 `{auto_topic:true, as_of_date:"YYYY-MM-DD"}`, `직접 입력`은 `{keyword:"...", as_of_date:"YYYY-MM-DD"}`를 사용한다. 분야·주요 독자·발행목적은 입력하지 않는다. 두 경우 모두 동일한 파이프라인을 호출하며, 실행 중에는 task 상태를 polling하고 완료 후 실행 목록을 갱신한다.

최초 실행은 외부 어댑터가 주입되어 있어도 콘텐츠 생성 단계만 수행하며 외부 어댑터를 전달하지 않는다. 결과는 `local-only`와 `외부 저장 대기`로 표시하고, 이후 `외부 저장 실행` 버튼을 눌렀을 때만 `DashboardServer`에 주입된 Notion connector adapter와 Naver browser adapter로 재개한다. Naver는 Q1·Q2 이후 화면의 `네이버 임시저장 확인` 버튼을 눌러야 저장한다. 발행은 지원하지 않는다.

## 확인 시나리오

- `ready_for_naver`: Q1·Q2 통과 후 네이버 단계 대기
- `awaiting_user_confirmation`: 네이버 임시저장 직전 확인 대기
- `failed`: 단계 실패와 다음 단계 차단
- 검색·주제 입력 방식·상태 필터
- 실행 선택 후 Q1·Q2·단계 타임라인·안전한 오류 메시지 확인
- 수동 실행 버튼의 자동 선정/직접 입력 상호배타 검증, task polling, 외부 저장 연결 없음 표시
- Q1·Q2 이후 네이버 임시저장 사용자 확인 경로
- `/showcase.html`에서 로딩·빈 상태·오류·성공·대기·차단 표현 확인

대시보드는 원시 로그 전체를 브라우저로 보내지 않고, `run_id`, 상태, 단계, Gate 요약, 안전한 오류 메시지만 노출한다.
