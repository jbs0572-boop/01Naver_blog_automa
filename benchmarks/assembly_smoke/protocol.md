# 조립 단계 소규모 벤치마크

목적: 완성된 초안과 이미지를 네 최종 파일로 변환하는 반복 작업에서
모델 호출을 프로그램으로 대체할 때 시간과 내용 보존을 측정한다.
운영 전체 content-assembler나 Q1을 대체하는 시험은 아니다.

입력은 `한정선 찹쌀떡` 초안, image-map, 기존 이미지 4개의 별도 복사본이다.
역사 자료의 현재 사실성이나 이미지 디자인을 새로 평가하지 않는다.

- A: 현재 기본 조립 모델 gpt-5.6-luna / low, 기존 격리 프로필, 1회, 300초 제한.
- B: 좁은 Markdown 문법만 지원하는 결정적 변환기, 새 Python 프로세스 5회.
- 두 방식 모두 아래 같은 변환 규칙과 입력을 사용한다. 모델은 변환 코드를
  스스로 작성해도 된다. 미리 만들어 둔 변환기나 기존 최종 파일은 제공하지 않는다.
- 복사본 준비는 양쪽 측정에서 제외한다. 모델 시작/추론/도구/출력과 프로그램
  시작/import/입출력은 각각 포함한다. 양쪽 모두 기존 parser 및 manifest
  작성·검증 비용을 포함한다. 기대 결과와의 비교 비용은 별도 검증이다.
- 미리 정한 성공 조건: final Markdown byte 동일, 태그 파일 3개의 파싱 구조
  동일(빈 블록 개수만 제외), 이미지 파일 해시 동일, manifest 검증 통과.
- 실패한 A/B 조합은 성공 속도 향상률을 계산하지 않는다. 실패 시간도 기록한다.
- 절감률 = (A 초 - B 중앙값 초) / A 초 × 100. 단일 사례 탐색 결과이며
  통계적 일반화, 전체 생성 시간 절감률, Q1 품질 통과율을 뜻하지 않는다.

## 양쪽에 적용할 변환 규칙

1. draft의 첫 `# ` 제목과 모든 본문 문구를 그대로 보존한다.
2. final/<keyword>.md는 IMAGE 마커만 image-map의 파일과 마커의 alt로 치환한다.
   형식은 `![alt](../assets/<keyword>/<filename>)`이다. 다른 byte는 유지한다.
3. 나머지 세 파일은 같은 태그 문법으로 작성한다. TITLE이 맨 앞이고,
   image-map의 썸네일 역할 문구를 alt로 사용한 대표 IMAGE가 그 다음이다.
4. 문단은 TEXT, ##/### 제목은 HEADING level=2/3, 연속 목록은 LIST/ITEM,
   Markdown 표는 TABLE title="본문 표"와 ROW로 변환한다. ROW는
   `열이름=값; 열이름=값`이고 separator 행은 제외한다. 링크·강조 문법은 유지한다.
5. 이미지: `[IMAGE file="..." alt="..." representative=true/false]` 후
   `[ALT]...[/ALT]`. 생성 이미지에 CAPTION은 붙이지 않는다.
6. 닫는 태그는 `[/TITLE]`, `[/TEXT]`, `[/HEADING]`, `[/LIST]`, `[/ITEM]`,
   `[/TABLE]`, `[/ROW]`이다. ---는 `[BLANK]`로 표현한다. 추가 빈 블록은 허용한다.
   LIST 내부는 ITEM 행만, TABLE 내부는 ROW 행만 허용한다. 그 내부에는
   빈 줄이나 BLANK를 넣지 않는다. TEXT 내부 줄바꿈은 원문 그대로 보존한다.
7. copy는 출처 메타데이터를 그대로 유지한다. layout/input의 `정보 출처` 목록만
   Markdown 링크 뒤 ` — `부터 시작하는 확인 메타데이터를 제거한다.
   제목, 본문 및 링크 자체는 바꾸지 않는다. 이 차이는 기존 산출물의 규칙이다.
8. 파일은 final/<keyword>.md, final/<keyword>-naver-layout.md,
   final/<keyword>-naver-copy.md, final/<keyword>-naver-input.md이다.

## 실행

프로젝트 루트에서 새 결과 디렉터리만 지정한다. 첫 명령은 실제 모델 1회를 호출한다.

```sh
uv run python -m benchmarks.assembly_smoke.run baseline artifacts/workflow-optimization/<new-id>
uv run python -m benchmarks.assembly_smoke.run compare artifacts/workflow-optimization/<same-id>
uv run pytest benchmarks/assembly_smoke/test_render.py -q
```

Notion/네이버 저장, 리서치, 집필, 이미지 생성, 의미 검수, 모바일 렌더 평가,
운영 전략 변경은 이 실험에 포함하지 않는다. Plan.md의 전체 T11 구현도 아니다.

## 첫 예비 시험과 지침 보완

`assembly-smoke-20260927-01`은 모델이 목록·표 내부에 빈 줄을 넣어
52.938초 뒤 파서 검증에 실패했다. 그 결과로 성공 절감률을 계산하지 않는다.
원본 prompt.txt와 로그는 보존한다. 위 6번에 기존 파서의 빈 줄 규칙을
명시한 후 `assembly-smoke-20260927-02`에서 A/B를 새로 실행한다.
이는 첫 성공률 100%를 뜻하지 않는다. 두 모델 호출의 결과를 함께 보고한다.
