# 독립 편집 스튜디오 — 5개 카테고리, 5개 고정 스타일

이 디렉터리는 이전 복잡한 Gate/Notion/Naver 체계와 연결되지 않은 독립적인 새 편집 모듈입니다.

## 다섯 개는 서로 다른 스타일입니다

- life_health (생활·건강): 친절한 원리 설명, 해요체, 상황별 실천 도식
- news_policy (뉴스·정책·이슈): 날짜·출처 우선, 합니다체, 시행 일정 정보 그래픽
- culture_people (영화·드라마·인물): 서사 해설, 해요체, 상징적인 무드보드
- real_estate (부동산): 위험·계약 조건 우선, 점검형 합니다체, 서류 체크리스트
- economy (경제): 숫자와 인과 설명 우선, 분석형 합니다체, 데이터 그래프

카테고리 내부에서는 서로 다른 주제라도 같은 MD 원본, 같은 문체·관점·고정 5개 단락·이미지 정책을 적용합니다.
다른 카테고리의 지침을 혼합하지 않습니다.

## 실행

저장소 루트에서 파이썬 표준 라이브러리만 사용합니다.

    python -m editorial_studio build --topic "아파트 창문 환기 방법" --output-root editorial-preview

선정이 모호하면 오류와 카테고리별 근거 점수를 내보내므로 명시적으로 선택합니다.

    python -m editorial_studio build --topic "전세 기준금리" --category real_estate --output-root editorial-preview

출력 폴더에는 style-lock.json, writer-brief.md, image-brief.md, layout-template.md가 있습니다.
각 작업 지시서는 선택된 스타일 1개만 포함합니다. 프로필 원본 SHA-256을 저장하고 변경·훼손을 감지합니다.

    python -m editorial_studio check --bundle editorial-preview/life_health-<해시>

실제 초안을 작성한 뒤 지정된 카테고리 제목과 금지 표현을 검사하려면:

    python -m editorial_studio check --bundle <생성된폴더> --draft <본문.md>
    python -m unittest discover -s tests -p test_editorial_studio.py -v

## 전체 작업 원칙: 3단계

1. 검색·출처 검증·주제 선택: 정확한 수치/날짜/공식 근거 확인 → 5개 중 하나 선택.
2. 스타일 고정 글·이미지 제작: 해당 카테고리 MD만 불러와 글과 이미지를 만들고 개별 품질 검수.
3. 글+이미지 조립·미리보기: 이미지가 실제로 생성되었을 때만 배치하고 사람이 확인.

### 구현 범위와 한계

현재 동작하는 것은 5개 독립 스타일의 선택·잠금·글/이미지 지시서·빈 배치 템플릿·기계적으로 확인 가능한 일부 구조 검사입니다.
검색 API 연동, 실제 AI 글 생성, 실제 AI 이미지 생성, 시각적 품질 평가, 최종 조립은 이 모듈에서 아직 실행하지 않습니다.
기계 검사만으로 논조·문체의 모든 미세한 차이와 사실관계를 검증할 수 없습니다.
출처 확인과 글·이미지 육안 검수는 필수이며, 생성하지 않은 이미지를 생성 완료로 기록하지 않습니다.

외부 Notion·Naver 계정 입력, 게시·배포, API 비용 발생은 없습니다.
