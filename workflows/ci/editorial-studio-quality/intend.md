---
workflow_id: ci/editorial-studio-quality
kind: offline-readonly-quality
source: .github/workflows/editorial-studio-quality.yml
---

# editorial-studio-quality — intend.md

## 목적
독립적인 다섯 편집 카테고리의 프로필·글·이미지 지시서가 섞이지 않는지, 2개씩 총 10개 서로 다른 주제로 반복 시험한다.

## 범위와 입력
- `editorial_studio/profiles/*.md`, `engine.py`, `quality.py`, `benchmarks/cases.json`
- 테스트용 텍스트 생성은 `fixture`로 표시한다. 실 OpenAI 유료 호출이 아니다.
- 단위 테스트에는 모의 모델 응답 및 Pillow 합성 테스트 이미지가 들어간다.

## 결과물
- 생성 근거팩, 초안·이미지 제작 지시서, 이미지를 실제로 생성하지 않았음을 드러내는 모바일 미리보기, 점수표 v1/v2 및 10개 반복 검증 보고서
- Actions 다운로드 아티팩트 `editorial-5x2-offline-quality`

## 완료 조건
- 이미지가 없을 때 완성으로 표기하지 않으며, 5개 카테고리 각각 2개 주제가 같은 프로필 해시를 사용한다.
- 주제에 따라 제목·사실·이미지 프롬프트가 바뀌어도 스타일 고유의 말투/논조/구성 역할이 유지되게 한다.
- 낮은 화질·단색 이미지·중복 커버·근거 연결 오류·프로필 혼합을 차단한다.
- 사람의 미감·원문 사실성 판단을 자동 검증 통과로 둔갑시키지 않는다.

## 금지 사항
Notion 및 네이버 쓰기·임시저장·발행, 외부 API 호출·유료 모델 실행, GitHub 자동 병합·운영 배포, 과거 로그 조작·필수 테스트 제거. PR 체크아웃은 읽기 전용 권한으로 수행한다.

## 한계
본 테스트 결과는 실제 검색 유입 증가의 실증도, 실모델 API 요청 성공의 실증도 아니다. 별도의 유료 호출 승인/자격증명과 사용자 발행 후 통계가 필요하다.
