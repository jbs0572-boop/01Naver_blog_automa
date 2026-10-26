# August local commits compared with current main

Checked: 2026-10-26 KST

Comparison base: `origin/main` at `7b4dbaa` (after PR #11)

Local branch preserved: `main` at `044dde4`; it was 31 commits ahead and 207 behind after fetch.

## Result

The seven August commits are not ancestors of `origin/main`, and `git cherry -v origin/main HEAD` reports `+` for each one, so Git did not find patch-identical commits on the remote. That does not mean their old behavior should be copied onto current main: PR #3 replaced the active workflow with an evidence-first, offline v2 flow. Current `README.md` explicitly says the old Notion/Naver account-write path is not connected and that automated publishing is not active.

No August commit was cherry-picked. The distinctions below are behavioral, based on current source, tests, workflow intent, and merged PRs; they do not claim that different implementations have identical diffs.

- **Fully reflected as the same feature:** none of the seven. The SHA-level comparison also found no patch-identical remote commit.
- **Partially reflected:** `3c5d34a`'s local workflow outcome is now handled by v2's offline evidence-pack/export path, without the old external writes; `c3e19a7`'s discovery goal has a bounded Naver API search, but not automatic browser reading of source pages.
- **Not implemented in the active v2 scope:** beta Notion writes, generated Q1 retries, external dashboard retries, and Naver editor title parsing. They depend on the retired online runner or producer; current plans explicitly keep AI generation, external-site collection, and account writes outside this v2 preview.

| Local August commit | Behavior in that commit | Current `origin/main` evidence | Classification and action |
|---|---|---|---|
| `8aa1a91` — `feat: enforce beta Notion write policy` | Allows a narrow beta Notion write after Q1 while denying unapproved external writes; adds workflow hook and policy tests. | Current v2 approval only creates a local manual-copy export (`tools/blog_v2.py::approve_bundle`, `export_copy`; `tests/test_blog_v2.py`). README says there is no Notion account connection. | **Retired/replaced.** The beta write exception is absent by design; current v2 has no matching Notion operation to authorize. Do not restore the exception or old hook. |
| `3c5d34a` — `feat: unify production blog workflow` | Adds the seven-stage runner, dashboard submission, Notion Q2, and Naver draft-save path. | PR #3 adds source-pack validation, deterministic draft/manifest generation, human local-export approval, safe preview, and read-only search; `tools/automation_runner.py` is documented as a legacy status recorder. `tests/test_blog_v2.py`, `tests/test_naver_search_v2.py`, and PR #3 CI cover the replacement path. | **Partially reflected, then superseded.** Current local source-pack → draft → verify → approve → copy flow replaces the old online workflow; Notion/Naver writes are intentionally not carried forward. |
| `3eeb123` — `feat: retry failed Q1 content assembly` | Retries the generated `content-assembler` with Q1 feedback in the old staged runner. | The v2 builder validates a supplied source pack and renders a deterministic review draft. The v2 intent explicitly says AI expansion and live source collection are not implemented; there is no generated content-assembler attempt to retry. | **Not applicable to current implementation.** No retry loop was added because its producer and Q1 stage do not exist in v2. |
| `765df54` — `fix: preserve dashboard external retry action` | Retains a dashboard action to retry failed external Notion/Naver operations. | Current v2 output is a local copy; README states no account connection or external write. There is no active external-operation dashboard route in this workflow. | **Retired with external writes.** No corresponding retry action belongs in the current v2 flow. |
| `c24d4dd` — `fix: parse canonical Naver preview title` | Extracts a title block from the legacy Naver input before browser preparation. | `tools/blog_v2.py::validate_pack` validates a structured `title`; `render_draft` writes it directly as the first-level heading. There is no Naver browser preparation path. | **Replaced.** Structured title input removes the legacy preview-parsing boundary. |
| `eb49caf` — `fix: support unclosed Naver title blocks` | Falls back safely when a legacy `[TITLE]` block is unclosed. | The current source-pack format has no `[TITLE]` block parser or Naver editor adapter. `tests/test_blog_v2.py` checks source-pack and export behavior instead. | **Retired with the legacy parser.** No parser behavior was ported. |
| `c3e19a7` — `feat: allow read-only browser research` | Allows Aside browser reads in the old `researcher` stage and classifies browser navigation/extraction as read-only. | `tools/naver_search_v2.py` provides one explicitly authorized, bounded, read-only Naver API request; `tests/test_naver_search_v2.py` checks no-network planning, fixed host, one request, no redirect/retry, output limits, and credential redaction. The v2 evidence-packet intent says automated external-site reading is not performed; README puts original-source collection in a future phase. | **Partially reflected, browser part deferred.** API candidate discovery exists, but automatic browser inspection of original pages does not. The current workflow deliberately requires human source review, so the old browser permission was not reintroduced. |

## PR and test evidence

- GitHub API confirmed PRs #2, #3, #4, #5, and #6 were merged. PR #2 adds the current function-coverage audit and synthetic legacy-format test; PR #3 adds the replacement v2 workflow; PRs #4–#6 add the editorial/image workflows. PRs #8, #9, and #11 further harden the v3 image and CI boundaries. PR #7 was closed without merge.
- The current `origin/main` Python quality run for `7b4dbaa` is successful: [Actions run 40265338395](https://github.com/jbs0572-boop/01Naver_blog_automa/actions/runs/40265338395).
- Local full-suite run on Python 3.13.15 at `7b4dbaa`: **424 passed, 1 skipped, 15 subtests passed**. The skipped test requires two authentic 2026-08-26 JSONL files excluded by `runs/`; `tests/test_log_contract.py` instead runs a clearly marked synthetic compatibility test. No historical log was fabricated or claimed as checked.
- Branch coverage for `tools`: **86.66%**. The repository audit found **122/126 callable bodies exercised**; three lambdas are unverified because line tracing is ambiguous, and one Protocol declaration is not an executable body.
- Supplemental local checks on this unchanged base reported 45 Ruff findings and 1,913 basedpyright findings. Neither tool is part of `.github/workflows/python-quality.yml`; no unrelated lint/type cleanup was folded into this reconciliation.

## Safe synchronization procedure

The original `main`, its 31 local-only commits, and its dirty worktree were left untouched. Before this comparison, a local recovery snapshot captured all refs, the tracked diff from `HEAD`, the non-ignored untracked files, and the status inventory. Work was performed in a separate branch based on the fetched remote main.

Do not push the old `main` directly. Review this comparison PR first. For any later local-only change that remains desired, port only the reviewed behavior into a fresh branch from the then-current `origin/main`, test it, and open a separate PR. Preserve the original local branch and snapshot until the desired changes have been confirmed on remote main. Only then may the user choose a fast-forward or other explicit local synchronization step; no reset, rebase, or force push is part of this procedure.
