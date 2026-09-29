# 로컬 QA 대시보드

첨부된 `1_2강.md`의 문제 정의·상태 설계·검증 원칙을 이 프로젝트의 단일 실행 계약에 맞춰 적용한 로컬 실행·검수 화면이다. 현재 대시보드는 production workflow만 실행하며, 외부 쓰기는 연결된 어댑터가 준비된 경우에만 실행한다.

## 작업 중심 화면 검수

- 기본 `/#tasks` 화면에는 Job 표가 하나만 있어야 하며 별도 `RUN INVENTORY` 목록을 렌더링하지 않는다.
- 요약은 실행 중, 대기, 확인 필요, 오늘 완료, 다음 예약을 표시한다. 다음 예약은 예정 시각이며 실제 시작으로 표현하지 않는다.
- Job 행은 표시 ID·주제, 예정/실제 시작, 7단계 진행, 상태와 기록된 사유, 경과, 확인된 토큰과 관측 시각을 표시한다. 기록되지 않은 값은 `—` 또는 `집계 전`이다.
- 행 선택 전에는 `/api/runs/<run_id>`를 호출하지 않는다. 선택 변경 중 이전 상세 응답이 늦게 도착해도 현재 선택을 덮어쓰지 않는다.
- 빠른 보기는 기존 상태를 `active`, `attention`, `history` 서버 필터로 묶으며 상태나 로그를 변경하지 않는다.
- 예약 중지와 재개는 새 예약 접수에만 적용한다. 각 시간 행의 켜짐, 시간, 모델 프리셋을 독립적으로 저장한다.
- 수동 작업 상태 폴링은 문서가 숨겨지면 타이머를 중단하고 복귀 시 활성 batch를 한 번 즉시 조회한다. POST 동작은 자동 재전송하지 않는다.
- Aside Browser에서 390×844, 768×1024, 1440×900을 확인하고 Job 선택, 검색, 빠른 보기, 예약 행 편집, 실패·복구·자동 임시저장 진행 상태를 조작한다.

## 실행

프로젝트 루트에서 실행한다.

```bash
uv run python -m tools.test_dashboard --root .
```

CLI는 `--root`만 받으며, 대시보드는 지정한 프로젝트의 `runs/*.jsonl`과 `.automation/logs/*.jsonl`을 읽는다. 로그가 없을 때는 빈 상태가 표시된다. 테스트용 fixture는 현재 production 계약과 같은 저장소·API 경로로 주입한다.
브라우저에서 `http://127.0.0.1:8765`를 연다.
대시보드는 프로젝트별로 한 프로세스만 실행되며, 호스트와 포트는 `127.0.0.1:8765`로 고정된다. 이미 실행 중이면 새 프로세스를 만들지 않고 기존 주소를 안내한다.
화면은 탭이 보이는 동안 10초마다 자동 갱신하고, 숨겨졌던 탭으로 돌아오면 즉시 다시 연결한다. 자동 갱신은 진행 중인 요청과 겹치지 않으며, 연결 상태와 마지막으로 읽은 시각을 화면에 표시한다.

## 로그인 시 자동 실행

대시보드를 macOS 사용자 로그인 시 시작하고 비정상 종료 후 다시 실행하려면 전용 LaunchAgent를 등록한다. CLI를 직접 실행할 때 허용되는 플래그는 `--root`뿐이며, 외부 쓰기 여부는 설정된 어댑터로 결정된다. Q1·Q2·Q3, manifest, 쓰기 Hook을 통과한 작업은 대시보드가 네이버 임시저장까지 자동 진행한다. 임시저장 외의 발행·예약 발행·공개 설정 변경은 하지 않는다. 별도 `daily-generate` LaunchAgent는 비활성 상태를 유지한다.

먼저 로그 디렉터리와 사용자 LaunchAgents 디렉터리를 준비하고 plist를 복사한다.

```bash
mkdir -p ".automation/logs/launchd"
mkdir -p "$HOME/Library/LaunchAgents"
cp "launchd/com.naverblog.dashboard.plist" "$HOME/Library/LaunchAgents/com.naverblog.dashboard.plist"
```

처음 등록할 때 실행한다.

```bash
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.naverblog.dashboard.plist"
launchctl kickstart -k "gui/$(id -u)/com.naverblog.dashboard"
```

상태와 로그는 다음 명령으로 확인한다.

```bash
launchctl print "gui/$(id -u)/com.naverblog.dashboard"
tail -f ".automation/logs/launchd/dashboard.stderr.log"
```

상시 실행을 중지할 때는 다음 명령을 사용한다.

```bash
launchctl bootout "gui/$(id -u)/com.naverblog.dashboard"
```

Notion과 네이버 외부 쓰기를 연결하려면 인증과 감독형 선택자 검증이 끝난 `naver-config.md`를 준비한 뒤 위 명령으로 실행한다. 대시보드가 `AsideBrowserGateway`의 결정적 `repl` 경로와 설정을 확인하며, 조건이 맞지 않으면 외부 쓰기를 시작하지 않는다. 대시보드는 Gateway 세션을 직접 만들거나 raw JavaScript를 전달하지 않는다. Aside CLI의 `exec -m` 모델 선택은 자연어 에이전트 실행용이고, 이 `repl` 경로에는 모델 선택이 필요하지 않다.

CLI 사전 점검은 `command -v aside`를 먼저 실행하고, 결과가 없으면 `/Users/beomseok/.local/bin/aside`의 파일·실행 권한을 확인한다. PATH 검색 실패만으로 재설치하지 않으며, 대시보드와 Gateway는 `resolve_aside_cli()`의 기존 설치 탐색을 사용한다.

화면의 `MANUAL RUN`에서 주제 입력 방식을 하나 선택하고 KST 정보 기준일을 입력한다. 자동 선정 `{auto_topic:true, as_of_date:"YYYY-MM-DD"}`과 직접 입력 `{keyword:"...", as_of_date:"YYYY-MM-DD"}`은 모두 `daily-generate` 자식 1개를 실행한다. 과거 3슬롯 배치는 읽기와 재개 호환을 유지한다. 분야·주요 독자·발행목적은 입력하지 않는다. Q1·Q2·Q3와 임시저장 Hook을 통과하면 자동 저장되며, 완료 상태를 polling한다.

예약은 대시보드 상단 '매일 자동 작성'에서 설정한다. 시간 추가·삭제 후 '저장하고 매일 실행'을 한 번 누르면 매일 반복한다. '변경 저장'으로 시간을 바꾸고 '예약 중지'로 멈춘다. 다음 실행 시각은 KST로 표시한다. 기본 시간은 08:00·12:00·18:00이다. 설정과 실행 접수 기록은 `.automation/dashboard/schedule.json`에 원자적으로 저장하며 날짜+시간별 중복 접수를 방지한다. 실행일의 KST 날짜가 기준일이다. 브라우저를 닫아도 서버 타이머가 실행하며 launchd `com.naverblog.dashboard`가 로그인 시 서버를 유지한다. Mac이 꺼지거나 잠든 동안 5분 이상 놓친 예약은 미실행으로 기록하고 몰아서 재실행하지 않는다. 작업은 기존 단일 작업 큐로 순차 처리한다. 기존 Codex 자동화 3개는 대시보드 예약으로 교체했으며 daily-generate launchd는 비활성으로 유지한다.

최초 실행은 외부 어댑터가 주입되어 있어도 콘텐츠 생성 단계만 수행하며 외부 어댑터를 전달하지 않는다. 결과는 `local-only`와 `외부 저장 대기`로 표시하고, 이후 선택한 child의 `외부 저장 실행` 버튼을 눌렀을 때만 `DashboardServer`에 주입된 Notion connector adapter와 Naver browser adapter로 재개한다. Q1·Q2·Q3와 쓰기 Hook을 통과한 Naver 임시저장은 자동 진행한다. 발행과 bulk confirmation은 지원하지 않는다.

## 확인 시나리오

현재 화면에는 실행 모드 선택이나 모드 전환 API가 없다. 모든 새 작업은 현재 프로젝트 루트와 production workflow를 사용하며, 서버 재시작 뒤에도 같은 계약을 따른다. 과거 데모 모드로 기록된 예약·실행 이력은 화면에서 역사 기록으로만 표시하고, 새 작업 접수·재실행·외부 쓰기를 허용하지 않는다. 기존 파일은 읽기 전용으로 보존한다.

실행 목록과 상세 화면의 진행률은 7개 단계 중 통과한 단계 수 기준이다. 네이버 단계는 실제 임시저장이 완료되어야 포함하며, 남은 소요 시간의 추정값이 아니다. 각 단계는 한국어 이름과 대기·실패 이유를 함께 표시한다.

누적 토큰과 작업별 입력·출력·캐시 토큰은 `.automation/work/<run_id>/*/codex-attempt-*.jsonl`의 `turn.completed.usage`를 집계한다. 입력+출력을 합산하며 입력에 포함된 캐시는 중복 합산하지 않는다. 기록 없는 작업은 `집계 전`이다. 현재 프로세스는 단계 호출 종료 시 로그를 저장하므로 실행 중인 호출의 사용량은 종료 후 반영된다. 같은 실행 ID의 로그가 중복되어도 누적 합계에서는 한 번만 집계한다.

- `ready_for_naver`: Q1·Q2 통과 후 네이버 단계 대기
- `awaiting_user_confirmation`: 품질 Gate 통과 후 네이버 자동 임시저장 진행 중
- `failed`: 단계 실패와 다음 단계 차단
- 검색·주제 입력 방식·상태 필터
- 실행 선택 후 Q1·Q2·단계 타임라인·안전한 오류 메시지 확인
- 수동 실행 버튼의 자동 선정/직접 입력 상호배타 검증, task polling, 외부 저장 연결 없음 표시
- Q1·Q2·Q3와 Hook 통과 후 자동 네이버 임시저장 진행·완료 경로
- `/showcase.html`에서 로딩·빈 상태·오류·성공·대기·차단 표현 확인

대시보드는 원시 로그 전체를 브라우저로 보내지 않고, `run_id`, 상태, 단계, Gate 요약, 안전한 오류 메시지만 노출한다. 실행 목록은 서버에서 검색·상태·KST 날짜 필터와 페이지 나누기를 적용하고, 변경되지 않은 로그는 서버 메모리 캐시를 사용한다.

미실행 또는 접수 실패 이력은 날짜가 포함된 예정 시각으로 구분하며 `이 예약 다시 실행`으로 해당 예약일 기준 작업 1건만 접수할 수 있다. 원래 이력은 그대로 남고 복구 batch가 별도로 연결된다. 완료·실패·미실행·확인 대기는 로컬 알림함에 저장되며, 알림을 읽는 동작은 네이버 저장 확인과 무관하다.

실행 상세의 `완성 글 보기`는 현재 manifest 전체를 다시 검증한 뒤 canonical 네이버 입력 블록을 표시한다. 모바일 폭 전환은 실제 네이버 렌더링 인증이 아니다. `Notion에서 열기`는 같은 artifact digest의 Q2 통과와 검증된 원본 URL이 모두 있을 때만 나타난다. 상단 `연결 다시 점검`은 마지막 읽기 전용 진단 결과와 revision을 갱신하며 Q1/Q2 또는 외부 쓰기 권한을 대신하지 않는다.
