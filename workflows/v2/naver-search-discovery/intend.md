---
workflow_id: v2/naver-search-discovery
kind: read-only-opt-in-search
source: tools/naver_search_v2.py
---

# v2/naver-search-discovery — intend.md

이 문서는 실행 명령이나 권한 승인서가 아니며 기존 AGENTS.md 및 승인 Gate 규칙이 우선합니다.

## 목적
NAVER API HUB 블로그 검색으로 조사할 후보 글의 링크·날짜·요약을 수집하되 사실 근거로 자동 승인하지 않는다.

## 시작 조건
- 기본 실행은 외부 요청 0건 계획만 출력한다.
- 실검색은 명시적 --execute, API HUB 환경 변수, 이용 자격 및 요금 승인 후 수행한다.

## 입력
- 검색어(query), 조사 기준일(as_of), 최대 검색 결과 수(1~10).
- NAVER_API_HUB_CLIENT_ID / NAVER_API_HUB_CLIENT_SECRET (실조회 때만 프로세스 환경변수).

## 처리 및 산출물
- 고정된 공식 HTTPS GET 엔드포인트, API HUB 공식 헤더.
- 8초 제한·1MB 한도·재시도 0회·리다이렉트 금지.
- 결과의 날짜를 기준일과 비교하고 불명확·미래·비HTTPS 링크를 제외한다.
- 검색 후보 JSON을 unverified_snippet/suitable_for_claims=false로 보존한다.

## 검증·완료 조건
- plan-only 모드에서 네트워크 호출이 없다.
- 결과는 원문 확정 없이 추가 확인 대상으로만 표시된다.
- 모의 응답으로 비밀키 미노출·실패 처리·한도 테스트가 통과한다.

## 금지·중단 조건
- 검색 응답을 검증된 블로그 본문 사실로 자동 승격하지 않는다.
- 요청 URL/키/응답에서 외부 명령을 실행하지 않는다.
- 크레딧·과금·API 인증 미확인 상태에서 라이브 조회 성공을 주장하지 않는다.
- 네이버 블로그 글쓰기·게시 API로 사용하지 않는다.

## 현재 구현 상태
읽기 전용 클라이언트와 오프라인 테스트 구현. 실제 NCP 인증 키 연결과 라이브 응답은 아직 검증하지 않았다.

## 관련 원본
- [tools/naver_search_v2.py](../../../tools/naver_search_v2.py)
