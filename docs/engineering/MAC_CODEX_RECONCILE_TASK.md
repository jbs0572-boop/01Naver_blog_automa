# 로컬 Mac Codex 실행 지시 — 31개 커밋 전수 비교

이 작업은 로컬 Mac 컴퓨터에서만 실행합니다. 외부 원격 GitHub 작업만 수행하는 AI는 실제 로컬 파일과 /tmp 스냅샷을 백업했다고 주장할 수 없습니다.

## 최우선: 임시 백업의 영구 보존

1. 실제 구형 작업 폴더에서 checkout된 브랜치가 main이고 HEAD가 044dde4로 시작하는지 확인합니다. 원래 미커밋 변경이 있다는 사실을 유지합니다.
2. /tmp/naver-main-reconcile-20261026 경로가 아직 존재하는지 확인합니다. 없다면 보고하고 원본이 존재한다고 가장하지 마세요.
3. 최신 원격 기준의 별도 worktree에서 scripts/local_main_reconcile.py를 찾아 다음 입력으로 실행합니다:
   - --repo: 실제 기존 dirty main 경로
   - --expected-main 044dde4
   - --snapshot /tmp/naver-main-reconcile-20261026
   - --destination ~/Documents/naver-blog-recovery
4. 실행 전후 원본 main SHA, status, staged/tracked/untracked 해시가 동일해야 합니다. Git ref bundle은 별도 임시 bare repository에 실제 unbundle하고 fsck로 확인합니다.
5. 프라이빗 폴더의 SHA manifest 및 실제 tar의 byte hash가 일치할 때만 백업을 완료로 기록합니다.
6. 중첩 Git 저장소 등이 있어 백업이 중단되면 해당 디렉터리를 안전하게 별도로 아카이브하고 원본 파일을 누락하지 않은 상태에서 재실행합니다. 실패한 백업을 성공으로 보고하지 마세요.

## 31개 로컬 전용 커밋 전수 비교

1. 영구 백업의 audit.json과 AUDIT.md를 로컬에서 읽습니다. 로컬 전용 커밋 31개를 실제 git rev-list로 다시 확인합니다. 숫자가 다르면 최신 기준이 바뀐 것이므로 시점과 SHA를 기록합니다.
2. SHA별로 git show --stat, 변경된 함수/클래스, 과거 동작의 입력/출력, 예외 처리와 보안·승인 경계를 분석합니다.
3. 최신 origin/main의 현재 구현과 관련 회귀 테스트를 살펴봅니다. 같은 파일명이 아니라도 동작이 있는지 찾습니다. Git cherry 결과가 +여도 동일 기능의 별도 구현 가능성을 조사합니다.
4. 아래 4개 분류 중 하나로 각 SHA를 판정하고 양쪽의 파일:줄번호/테스트 근거를 표에 기록합니다.
   - already_implemented
   - partly_implemented
   - not_implemented
   - retired
5. 이전 PR #12에 있는 8월 커밋 7개 판단을 기준으로 재확인하되, 나머지 24개도 모두 직접 검토합니다. 미검토 항목이 남으면 완료라고 보고하지 않습니다.
6. 로컬 전체 diff, 개인정보, 미커밋 secret, Git bundle, tar는 공개 GitHub에 업로드하지 않습니다. 공유 가능한 보고서는 코드 위치·기능·판정·관련 테스트의 최소 근거만 포함합니다.

## 포팅 원칙

- 필요한 미반영 기능만 새로운 origin/main 기반 worktree/브랜치에 구현합니다.
- 구형 Notion/Naver 온라인 저장을 현재 v2/v3 승인 Gate로 무리하게 되돌리지 않습니다.
- 보안·근거 검증·이미지 품질 기준을 낮추지 않습니다.
- 각 기능별 경계 조건 회귀 테스트를 추가하고 전체 Python CI를 실행합니다.
- 테스트를 통과한 변경에만 새 PR을 엽니다. 중요 리뷰 지적이 있으면 해결한 후 병합합니다.
- 원래 구형 main과 미커밋 파일은 사용자가 별도 정리 승인할 때까지 손대지 않습니다.

## 최종 출력

- 실제 영구 백업 위치, 파일 개수, 검증된 SHA manifest와 git bundle 복구 결과
- 원본 main HEAD 044dde4 보존 여부와 dirty worktree 변경 여부
- 최신 원격 main SHA, ahead/behind, 전수 검토 커밋 개수
- 네 가지 분류별 커밋 개수, 각 SHA의 실제 근거와 검토하지 못한 항목
- 필요 기능의 새 원격 브랜치/PR 링크, 테스트 및 리뷰 결과
- 운영 배포 여부와 명시적인 미해결 장애

로컬 명령을 실행하지 못했다면 준비만 완료했다고 보고해야 합니다.
