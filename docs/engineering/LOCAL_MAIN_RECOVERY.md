# 로컬 Mac main 백업 및 31개 전용 커밋 감사

대상은 이전 로컬 main(HEAD 044dde4)과 새로운 origin/main이 분기된 상황입니다. 기존 [PR #12 조사](https://github.com/jbs0572-boop/01Naver_blog_automa/pull/12)는 8월 7개 커밋을 비교했지만 나머지 24개 커밋은 아직 의미 검토를 완료하지 않았습니다.

## 자동 수행

Python 스크립트 scripts/local_main_reconcile.py는 원본 로컬 main을 checkout/reset/rebase/fetch/push/cherry-pick/merge하지 않습니다. Mac에서 본인이 실행해야 하며 ChatGPT 서버와 GitHub Actions는 Mac 파일시스템에 접근하지 않습니다.

- /tmp/naver-main-reconcile-20261026의 기존 복구 스냅샷 복제, SHA-256 대조
- 모든 Git refs를 all-git-refs.bundle 파일로 저장하고 git bundle verify 수행
- Tracked/staged 변경의 변환되지 않은 바이너리 diff와, **모든 tracked 작업트리 파일의 실제 바이트 사본**, Git 비무시 untracked 파일의 개인용 tar.gz 아카이브
- 백업 전후 HEAD 및 작업 트리 상태 해시 검증
- origin/main..main에 있는 모든 로컬 커밋 SHA, 제목, 날짜, 변경 경로, 동일 경로의 원격 최근 이력, git cherry 패치 동등성 기록
- 개인용 AUDIT.md, audit.json, backup-sha256.json 생성
- 충돌·자료 누락·HEAD 변경·검증 실패가 있으면 성공으로 처리하지 않음

## Mac의 원본 main에서 실행

반드시 현재 원본 main 프로젝트 폴더로 이동합니다. 조사용 별도 ...-reconcile worktree가 아닙니다. 현재 원격 main에서 스크립트 파일을 내려받아 별도 폴더에 보관하세요. 기존 main에 git pull을 수행하면 안 됩니다.

    cd "/path/to/original/dirty-main"
    git branch --show-current
    git rev-parse --short HEAD
    test -d /tmp/naver-main-reconcile-20261026
    python3 "/path/to/fresh-checkout/scripts/local_main_reconcile.py" \
      --repo "$PWD" \
      --expected-main 044dde4 \
      --snapshot /tmp/naver-main-reconcile-20261026 \
      --destination "$HOME/Documents/naver-blog-recovery"

위 원본 저장소 경로와 스크립트 경로는 Mac의 실제 위치로 바꿉니다. 로컬 MAIN HEAD가 이미 달라졌다면 원인을 확인하기 전까지 실행을 중단합니다.

개인용 백업 데이터에는 미커밋 비밀값이 포함될 수 있습니다. 폴더 권한은 0700입니다. snapshot, bundle, tracked diff, untracked archive는 GitHub나 공개 공유 폴더에 올리지 마세요. 원래 /tmp 스냅샷도 자동 삭제하지 않습니다.

## 31개 기능별 판단

AUDIT.md와 audit.json을 Mac Codex가 모두 검토합니다. 자동 수집된 경로 일치나 git cherry 결과만으로 기능의 반영 여부를 단정하지 않습니다.

- already_implemented: 원격 동작과 회귀 테스트가 이미 동일
- partly_implemented: 현행 인터페이스/권한/예외 처리 중 일부 차이
- not_implemented: 현재 필요한 기능인데 원격에 빠져 있음
- retired: 구형 온라인 Notion/Naver 경로 등 현재 구조에서 의도적으로 폐기

7개 8월 커밋의 선행 결론은 PR #12에 있고, 나머지는 기본으로 NEEDS_SEMANTIC_REVIEW입니다. 미구현이 확인된 것만 현재 origin/main에서 새 worktree·새 브랜치를 만들어 고립된 기능 단위로 포팅·테스트·PR 생성합니다. 31개를 일괄 cherry-pick하거나 force push하지 않습니다.

## 완료 기준

1. 영구 보관 복사본의 원본 바이트/해시 일치와 bundle 검증
2. Mac 원본 main의 변경 전후 HEAD 및 dirty 상태 일치
3. 31개 전용 커밋 모두 감사 자료에 포함
4. 나머지 24개 기능 의미 검토 완료 및 원격 코드/테스트 근거 제시
5. 현행 제품에 실제 필요한 누락 사항만 새로운 PR로 구현·CI 검증

GitHub Actions에서 Python 코드 자체의 재현성은 검증할 수 있지만, Mac에서 스크립트를 실행하기 전까지는 1~3 완료라고 주장할 수 없습니다.

## Git index 숨김 플래그와 submodule 보존

Git의 assume-unchanged 또는 skip-worktree 플래그가 있으면 git diff가 변경된 실제 바이트를 놓칠 수 있습니다. 이 백업 도구는 Git의 상태 보고와 무관하게 모든 tracked 작업트리 파일을 직접 읽고 tracked-worktree.tar.gz에 별도 보존하며, 아카이브를 다시 읽어 SHA-256을 검증합니다. 해당 검사는 리포지터리의 플래그나 index를 변경하지 않습니다.

부모 Git bundle은 자식 submodule의 객체나 dirty 파일을 포함하지 않습니다. 따라서 tracked submodule(gitlink)이 발견되면 **백업 성공을 거부**합니다. Mac Codex가 그 submodule 저장소의 refs/작업트리/미커밋 및 미추적 파일을 별도 백업한 뒤 통합 복구 절차를 결정해야 합니다. untracked 내부 Git 저장소도 조용히 누락시키지 않고 중단합니다.

Git bundle 검증은 단순 header 확인 외에 **독립 bare 저장소의 실제 unbundle·객체 fsck 및 원본 main ref 비교**를 수행합니다.

Git index의 숨김 플래그 등 stage metadata는 별도의 git-index.raw에 바이트 그대로 보존하고 백업 전 index SHA와 비교합니다. 모든 원격/로컬 ref도 독립 bundle unbundle의 반환 refs 목록과 직접 대조합니다. 복구 용도로만 보존하며 git-index.raw를 원본 checkout에 자동 복원하거나 적용하지는 않습니다.
