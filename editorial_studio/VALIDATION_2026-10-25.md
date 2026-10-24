# Editorial studio — 실제 테스트 및 품질 증빙

**검증일:** 2026-10-25 KST. **대상:** `jbs0572-boop/01Naver_blog_automa` PR #4. 기존 `2026-10-22` 역사 조사와는 별개의 구현·검증입니다.

## 구현된 3단계
1. 주제·출처팩 → 다섯 카테고리 중 하나 고정. 상대 관심도를 확인할 필요가 있다면 네이버 데이터랩 API를 **명시적으로 읽기 전용** 호출합니다. 지금은 실인증 호출하지 않았습니다.
2. 해당 프로필 MD만 사용해 글 생성기(오프라인 fixture 또는 명시적 유료 OpenAI Responses API), 이미지 생성기(개인 파일 import 또는 명시적 유료 Images API) 연결. 프로필 해시, 출처 근거, 이미지 고유의 premium visual DNA를 보존합니다.
3. 근거 링크와 이미지가 실제 존재하는지 확인한 뒤 Markdown, 모바일 HTML, 수정 사유 및 버전별 점수 출력. 네이버·Notion 쓰기/임시저장/발행 없음.

## 대표 CI 검증 수치
- **전용 오프라인 테스트:** [GitHub Actions run 40141697405](https://github.com/jbs0572-boop/01Naver_blog_automa/actions/runs/40141697405) — **43 passed, warnings 11, subtests 15**, 실행된 편집 엔진 줄·분기 포함 **82%**. [10개 원고/프롬프트/루브릭 비교 보고서 및 커버리지 아티팩트](https://github.com/jbs0572-boop/01Naver_blog_automa/actions/runs/40141697405/artifacts/12578813636).
- **반복 주제:** 5카테고리 × 2주제 = **10개**. 동일 카테고리 MD SHA-256 동일성, 서로 다른 생성 프롬프트와 본문, 출처 연결, 이미지 미존재 차단, 중복 이미지 차단 검증.
- **오프라인 fixture(유료 모델이 아님):** v1 100점 기준 평균 **80.2점**, v2 평균 **75.1점**. v2는 이미지 비중을 높였고 10개 모두 이미지를 아직 만들지 않았으므로 **이미지 미완료** 상태입니다. 두 버전 점수를 품질 증가·감소로 직접 비교하면 안 됩니다.
- **이미지 픽셀 검사:** 1×1·저해상도·단색·가짜 메타데이터·무의미한 랜덤 노이즈를 차단하는 회귀 시험. 같은 카테고리 다른 글에서 똑같은 이미지를 재사용하면 벤치마크 실패.
- **기존 전체 테스트:** 다른 작업의 과거 2026-08-26 원본 실행 로그가 없어 `test_historical_logs_remain_readable`가 실패합니다. 이 테스트는 삭제·무시하지 않았고, 누락된 기록을 임의로 만들지 않았습니다.

## 실제 이미지 기술 검증 참고
기존 대화에서 이미 생성했던 카테고리별 이미지 **5장**은 로컬에서 PNG 디코딩, 해상도, 색상 빈 수와 대비를 점검해 **5/5 모두 기술적 최소 요건을 만족**했습니다. 이것은 *새 유료 API 연결의 실호출 결과가 아닙니다*. 이미지의 예술적 완성도·주제 정확성·기계 도식의 사실성·권리 검수는 완료로 주장하지 않습니다.

## 변경 가능한 기준과 변경 불가능한 것
- `rubrics/v1.json`은 고정 기준선, `rubrics/v2.json`은 검색 의도·이미지 비중을 높인 보강안.
- `evolution.py`는 최소 15개의 사람 검수 사례 + 5개 홀드아웃 결과에서 품질 오판이 줄 때만 v3 **제안 파일**을 만듭니다. 자동 승인·배포하지 않습니다.
- 모델이 실제 동작했다고 말하려면 API 키를 가진 환경에서 명시적 유료 호출, 반환 이미지 픽셀, 원고 출처 대조가 확인돼야 합니다. 현재 CI는 요금이 발생하는 API를 호출하지 않습니다.
- 검색 유입 최대가 궁극 목표지만 게시 전 글에는 검색 노출수·클릭수·CTR·유입 세션이라는 실측값이 없습니다. 수동 게시 이후 실제 분석 데이터를 입력하는 `traffic.py`를 별도로 두었습니다.

## 공식 API 및 검색 가이드
- https://developers.openai.com/api/docs/guides/image-generation
- https://developers.openai.com/api/reference/resources/responses/methods/create
- https://developers.naver.com/docs/serviceapi/datalab/search/search.md
- https://searchadvisor.naver.com/guide/content-basic
- https://searchadvisor.naver.com/guide/content-abusing
