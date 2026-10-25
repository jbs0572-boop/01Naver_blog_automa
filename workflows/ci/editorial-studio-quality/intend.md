---
workflow_id: ci/editorial-studio-quality
kind: offline-readonly-quality
source: .github/workflows/editorial-studio-quality.yml
---

# editorial-studio-quality — intend.md

이 문서는 실행 명령이나 권한 승인서가 아니며 기존 AGENTS.md 및 승인 Gate 규칙이 우선합니다. 독립 편집 스튜디오의 오프라인 검증에만 사용합니다.

## 목적
다섯 독립 카테고리의 문체·논조·이미지 규칙이 다른 주제의 글에서도 동일하게 유지되고, 타 카테고리와 섞이지 않는지 평가합니다.

## 시작 조건
- 정확히 다섯 개 카테고리 프로필 MD가 모두 존재합니다.
- 카테고리마다 두 개씩, 총 10개 공개 근거팩 예제와 오프라인 생성기가 준비돼 있습니다.
- 인증이나 유료 모델 호출, 네이버·Notion 게시 권한이 필요하지 않습니다.

## 입력
- `editorial_studio/profiles/*.md`, `editorial_studio/engine.py`, `editorial_studio/quality.py`, `editorial_studio/benchmarks/cases.json`.
- `tests/test_editorial_studio.py`, `tests/test_editorial_engine.py`.

## 처리 및 산출물
- pytest에서 실제 이미지 픽셀 검사와 외부 API 모의 응답을 검증합니다.
- 비과금 fixture 글 생성기에서 주제별 10개 원고, 출처 링크, 이미지 제작 지시문, HTML 미리보기를 만듭니다.
- 100점 루브릭 v1/v2, 동일 카테고리 MD 해시, 출력 다양성, 중복 이미지 차단 결과를 기록합니다.
- 읽기 전용 다운로드 아티팩트 `editorial-5x2-offline-quality`만 업로드합니다.

## 검증·완료 조건
- 카테고리 5개마다 서로 다른 주제 두 개가 동일 스타일 해시와 다른 글·이미지 프롬프트를 갖습니다.
- 저해상도·단색 이미지, 출처 링크 오류, 개인정보를 지어낸 글, 가짜 체험 후기, 카테고리 오선택을 거부합니다.
- 이미지가 생성되지 않은 상태를 완료라고 표시하지 않고 사람 검수를 대체하지 않습니다.
- 검증 실패를 성공으로 바꾸기 위해 과거 로그나 필수 테스트를 변경하지 않습니다.

## 금지·중단 조건
- Notion·네이버 임시저장·게시·발행, 브라우저 계정 변경, 유료 리소스 생성, 외부 AI 호출을 하지 않습니다.
- 마케팅 성과나 실제 검색 유입이 발생했다고 주장하지 않습니다.
- 사람이 원문·이미지 품질을 확인하지 않은 경우 게시 준비 완료로 보고하지 않습니다.

## 현재 구현 상태
별도 오프라인 테스트, 실제 Pillow 픽셀 검사, 카테고리 반복 벤치마크 및 아티팩트 업로드를 구성했습니다. 실 유료 모델 응답 및 네이버 유입 자료 검증은 아직 이 CI의 범위에 포함되지 않습니다.

## 관련 원본
- [GitHub Actions 파일](../../../.github/workflows/editorial-studio-quality.yml)
- [편집 스튜디오](../../../editorial_studio/README.md)
- [실험 루브릭](../../../editorial_studio/QUALITY_RUBRIC.md)
