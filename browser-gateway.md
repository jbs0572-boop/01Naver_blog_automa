# 브라우저 Gateway 운영 계약

대시보드와 runner는 Aside의 raw JavaScript·활성 탭·CLI 수명을 직접 다루지 않는다. 외부 브라우저 작업은 `tools/browser_gateway.py`가 생성한 단일 Gateway를 통해서만 수행한다.

## 책임 경계

```text
dashboard / runner
  -> DashboardExternalAdapters
  -> NaverDraftBrowserGateway
  -> AsideBrowserGateway
  -> AsideReplSession
  -> 로그인된 Aside Browser 탭
```

- `AsideBrowserGateway`: CLI 해석, Naver 설정 검증, 세션 생성·종료를 소유한다.
- `AsideNaverAdapter`: canonical Naver 문서의 구조화 입력과 SmartEditor layout 검증을 소유한다.
- `runner_naver_action`: Q1·Q2·manifest·사용자 확인 Gate를 소유한다.
- `NotionApiAdapter`: Notion 데이터 소스의 신규 페이지·첨부와 Q2 왕복 검증만 수행한다.

## 허용된 동작

현재 Gateway가 제공하는 capability는 `naver_draft_write` 하나다. Creator Advisor 읽기 capability는 enum으로 예약되어 있지만 구현 전까지 요청하면 fail-closed 한다. provider 자동 fallback은 사용하지 않는다.

Gateway 생성 시 다음을 먼저 확인한다.

1. Aside CLI가 존재하고 실행 가능하다.
2. `naver-config.md`가 `selector_status=verified`인 유효한 설정이다.
3. 이후에만 persistent REPL 세션을 만든다.

Gateway 종료는 idempotent하며, 대시보드 프로세스 종료 시 `DashboardExternalAdapters.close()`가 세션을 닫는다.

## 안전 규칙

- 초기 콘텐츠 생성은 외부 adapter 없이 실행한다.
- Q1·Q2 통과 후에만 Gateway를 통한 Naver 준비를 호출한다.
- 준비 결과는 현재 artifact/layout 검증과 `prepared.json` 증거를 거쳐 확인 대기로 전환한다.
- 사용자 확인 이후에만 임시저장 adapter를 호출한다.
- 저장 호출은 Aside transport stale 결과를 자동 재실행하지 않는다.
- 발행·예약 발행·공개 설정 변경·기존 글 수정·bulk confirmation은 제공하지 않는다.

## 향후 확장 순서

Creator Advisor는 동일 Gateway 안에 읽기 전용 `capture_creator_advisor` capability로 추가한다. 그 다음 준비 증거에 `run_id`, child ID, Q2 page/digest, browser session generation, preparation ID를 묶고, 저장 응답 유실 시 읽기 전용 draft-list reconciliation을 추가한다. 새 provider가 필요할 때만 같은 구조화 adapter Protocol을 구현하며 조용한 fallback은 추가하지 않는다.
