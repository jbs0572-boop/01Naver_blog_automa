from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import override

import pytest

from tests.test_dashboard_manual_store import persist_legacy_pending_batch
from tools.codex_stage_executor import CodexStageExecutor
from tools.contract_types import JSONMap
from tools.dashboard_manual_batch import new_batch, prepare_child
from tools.dashboard_manual_models import ManualRunDependencies
from tools.dashboard_manual_request import parse_manual_run_payload
from tools.dashboard_manual_run import ManualRunContext, ManualRunManager
from tools.dashboard_manual_store import ManualBatchStore
from tools.runner_execution import run_job
from tools.runner_types import (
    RunStatus,
    StageExecution,
    StageExecutionContext,
    StageResult,
    TopicSelectionContext,
)
from tools.topic_metadata import (
    CreatorAdvisorCandidate,
    CreatorAdvisorSnapshot,
    snapshot_sha256,
    write_snapshot,
)


class BatchStageExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int | None]] = []

    def execute(self, context: StageExecutionContext) -> StageResult:
        selection = context.selection_context
        slot = selection.batch_slot if selection is not None else None
        self.calls.append((context.stage, slot))
        keyword = context.keyword
        if context.stage == "topic-selector":
            keyword = self._select(context)
            selection_path = context.root / "research" / f"topic-selection-{keyword}.md"
            selection_path.parent.mkdir(parents=True, exist_ok=True)
            _ = selection_path.write_text(f"# {keyword}\n", encoding="utf-8")
            details: JSONMap | None = None
            if selection is not None and selection.snapshot_path is not None:
                snapshot = context.root / selection.snapshot_path
                assert selection.capture_id is not None
                details = {
                    "selection_snapshot_path": selection.snapshot_path,
                    "selection_snapshot_sha256": snapshot_sha256(snapshot),
                    "capture_id": selection.capture_id,
                }
            return StageResult(
                RunStatus.PASSED,
                StageExecution.PRODUCED,
                artifacts=(selection_path.relative_to(context.root).as_posix(),),
                resolved_keyword=keyword,
                details=details,
            )
        assert keyword is not None
        artifacts = self._write_stage(context.root, context.stage, keyword)
        return StageResult(
            RunStatus.PASSED,
            StageExecution.PRODUCED,
            artifacts=artifacts,
        )

    @staticmethod
    def _select(context: StageExecutionContext) -> str:
        selection = context.selection_context
        if selection is None or selection.batch_slot is None:
            assert context.keyword is not None
            return context.keyword
        keywords = ("가을 여행", "서울 축제", "제철 음식")
        keyword = keywords[selection.batch_slot - 1]
        if selection.batch_slot == 1:
            snapshot = CreatorAdvisorSnapshot(
                as_of_date=selection.as_of_date,
                captured_at="2026-09-07T09:00:00+09:00",
                capture_id=str(selection.capture_id),
                candidates=tuple(
                    CreatorAdvisorCandidate(value, rank)
                    for rank, value in enumerate(keywords, start=1)
                ),
            )
            _ = write_snapshot(context.root, snapshot)
        else:
            assert selection.snapshot_path is not None
            digest = snapshot_sha256(context.root / selection.snapshot_path)
            assert selection.snapshot_sha256 == f"sha256:{digest}"
            assert selection.excluded_keywords == keywords[: selection.batch_slot - 1]
        return keyword

    @staticmethod
    def _write_stage(root: Path, stage: str, keyword: str) -> tuple[str, ...]:
        if stage == "researcher":
            path = root / "research" / f"{keyword}.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            _ = path.write_text("# research\n", encoding="utf-8")
            return (path.relative_to(root).as_posix(),)
        if stage == "writer":
            path = root / "drafts" / f"{keyword}.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            _ = path.write_text("# draft\n", encoding="utf-8")
            return (path.relative_to(root).as_posix(),)
        if stage == "image-maker":
            asset_dir = root / "assets" / keyword
            asset_dir.mkdir(parents=True, exist_ok=True)
            _ = (asset_dir / "body.png").write_bytes(b"body")
            _ = (asset_dir / "thumbnail.png").write_bytes(b"thumbnail")
            _ = (asset_dir / "image-map.md").write_text("# map\n", encoding="utf-8")
            return tuple(
                f"assets/{keyword}/{name}"
                for name in ("body.png", "thumbnail.png", "image-map.md")
            )
        final_dir = root / "final"
        final_dir.mkdir(parents=True, exist_ok=True)
        paths = tuple(
            final_dir / f"{keyword}{suffix}"
            for suffix in (".md", "-naver-layout.md", "-naver-copy.md", "-naver-input.md")
        )
        for path in paths:
            body = (
                f"# {keyword}\n\n![body](../assets/{keyword}/body.png)\n"
                if path.name == f"{keyword}.md"
                else f"# {keyword}\n"
            )
            _ = path.write_text(body, encoding="utf-8")
        return tuple(path.relative_to(root).as_posix() for path in paths)


class FailOnceBatchStageExecutor(BatchStageExecutor):
    @override
    def execute(self, context: StageExecutionContext) -> StageResult:
        selection = context.selection_context
        slot = selection.batch_slot if selection is not None else None
        if context.stage == "topic-selector" and slot == 1 and not self.calls:
            self.calls.append((context.stage, slot))
            return StageResult(
                RunStatus.FAILED,
                StageExecution.ATTEMPTED,
                message="selector failed",
            )
        return super().execute(context)


def _manager(root: Path, executor: BatchStageExecutor) -> ManualRunManager:
    return ManualRunManager(
        ManualRunContext(root, False),
        ManualRunDependencies(run_job, executor=executor),
    )


def test_retry_recovers_failed_preallocated_run_and_calls_executor_again(
    tmp_path: Path,
) -> None:
    # Given: the preallocated first child has one persisted failed selector attempt.
    executor = FailOnceBatchStageExecutor()
    manager = _manager(tmp_path, executor)
    created = manager.start(
        parse_manual_run_payload({"auto_topic": True, "as_of_date": "2026-09-07"})
    )
    manager.close()
    failed = manager.get(created.batch_id)
    assert failed is not None
    child = failed.children[0]
    assert child.child_id is not None and child.next_action is not None
    identities = created.batch_id, tuple(item.run_id for item in failed.children)

    # When: the dashboard accepts the child retry action.
    retried = _manager(tmp_path, executor)
    _ = retried.submit_action(
        created.batch_id, child.child_id, "retry", child.next_action.nonce
    )
    retried.close()
    settled = retried.get(created.batch_id)

    # Then: recovery reruns the selector without allocating another batch or run.
    assert settled is not None
    assert (
        settled.batch_id,
        tuple(item.run_id for item in settled.children),
    ) == identities
    assert executor.calls.count(("topic-selector", 1)) == 2


def test_legacy_three_child_batch_remains_read_only_across_restarts(
    tmp_path: Path,
) -> None:
    # Given: a persisted batch from the retired three-child envelope.
    created = persist_legacy_pending_batch(tmp_path)
    batch_path = (
        tmp_path
        / ".automation/dashboard/manual-batches"
        / f"{created.batch_id}.json"
    )
    original = batch_path.read_bytes()
    executor = BatchStageExecutor()
    manager = _manager(tmp_path, executor)

    # When: two current dashboard managers scan the historical batch.
    manager.close()
    restarted = _manager(tmp_path, executor)
    restarted.close()
    reloaded = restarted.get(created.batch_id)

    # Then: the record is readable and byte-stable without executing any child.
    assert reloaded is not None
    assert len(reloaded.children) == 3
    assert reloaded == created
    assert executor.calls == []
    assert batch_path.read_bytes() == original
    assert not (tmp_path / ".automation/state").exists()


def test_real_runner_user_batch_remains_single_run(tmp_path: Path) -> None:
    # Given: the real runner has a local executor and no external adapters.
    executor = BatchStageExecutor()
    manager = _manager(tmp_path, executor)

    # When: one user-defined request completes.
    created = manager.start(
        parse_manual_run_payload({"keyword": "지정 주제", "as_of_date": "2026-09-07"})
    )
    manager.close()
    settled = manager.get(created.batch_id)

    # Then: exactly one persisted child and runner execution exist without a snapshot.
    assert settled is not None
    assert len(settled.children) == 1
    assert settled.children[0].resolved_keyword == "지정 주제"
    assert settled.snapshot is None
    assert len(list((tmp_path / ".automation/state").glob("*.json"))) == 1
    assert len(list((tmp_path / ".automation/logs").glob("*.jsonl"))) == 1
    assert not (tmp_path / "metadata/creator-advisor").exists()
    assert all(stage not in {"notion-rider", "naver-rider"} for stage, _slot in executor.calls)


def test_auto_batch_persists_historical_exclusions(tmp_path: Path) -> None:
    research = tmp_path / "research"
    research.mkdir()
    _ = (research / "topic-selection-kfc 1+1.md").write_text("# prior\n", encoding="utf-8")
    _ = (research / "topic-selection-한정선 찹쌀떡.md").write_text("# prior\n", encoding="utf-8")
    request = parse_manual_run_payload({"auto_topic": True, "as_of_date": "2026-09-11"})

    batch = new_batch(request, tmp_path)
    first = prepare_child(batch, 1)

    assert first.selection_context is not None
    assert first.selection_context.excluded_keywords == (
        "kfc 1+1",
        "한정선 찹쌀떡",
    )


def test_auto_batch_accepts_all_historical_exclusions_when_saved(tmp_path: Path) -> None:
    research = tmp_path / "research"
    research.mkdir()
    for keyword in ("첫 주제", "둘째 주제", "셋째 주제"):
        _ = (research / f"topic-selection-{keyword}.md").write_text(
            "# prior\n", encoding="utf-8"
        )

    request = parse_manual_run_payload({"auto_topic": True, "as_of_date": "2026-09-11"})
    batch = new_batch(request, tmp_path)
    ManualBatchStore(tmp_path).save(batch)

    loaded = ManualBatchStore(tmp_path).get(batch.batch_id)
    assert loaded is not None
    selection = loaded.children[0].selection_context
    assert selection is not None
    assert set(selection.excluded_keywords) == {"첫 주제", "둘째 주제", "셋째 주제"}


def test_context_snapshot_accepts_persisted_prefixed_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: the dashboard-persisted selection context uses the schema's sha256 prefix.
    snapshot = CreatorAdvisorSnapshot(
        as_of_date="2026-09-07",
        captured_at="2026-09-07T09:00:00+09:00",
        capture_id="CAPTURE-prefixed",
        candidates=(CreatorAdvisorCandidate("가을 여행", 1),),
    )
    path = write_snapshot(tmp_path, snapshot)
    selection = TopicSelectionContext(
        "",
        "",
        "",
        "2026-09-07",
        batch_id="BATCH-prefixed",
        batch_slot=2,
        snapshot_policy="reuse_only",
        capture_id="CAPTURE-prefixed",
        snapshot_path=path.relative_to(tmp_path).as_posix(),
        snapshot_sha256=f"sha256:{snapshot_sha256(path)}",
    )
    context = StageExecutionContext(
        tmp_path,
        "topic-selector",
        "RUN-prefixed",
        "TOPIC-prefixed",
        None,
        tmp_path / ".automation/work/RUN-prefixed/topic-selector",
        selection,
    )
    _ = (tmp_path / "topic-selector.md").write_text("instruction", encoding="utf-8")
    schema_dir = tmp_path / "schemas"
    schema_dir.mkdir()
    _ = (schema_dir / "stage-result.schema.json").write_text(
        (Path(__file__).parents[1] / "schemas/stage-result.schema.json").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )

    def fake_run(
        args: list[str],
        **_kwargs: str | Path | list[str] | int | bool | None,
    ) -> subprocess.CompletedProcess[str]:
        workspace = Path(args[args.index("--cd") + 1])
        relative = "research/topic-selection-가을 여행.md"
        artifact = workspace / "artifacts" / relative
        artifact.parent.mkdir(parents=True)
        _ = artifact.write_text("# 가을 여행\n", encoding="utf-8")
        output = Path(args[args.index("--output-last-message") + 1])
        _ = output.write_text(
            json.dumps(
                {
                    "stage": "topic-selector",
                    "status": "passed",
                    "execution": "produced",
                    "artifacts": [relative],
                    "resolved_keyword": "가을 여행",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr("tools.codex_process.subprocess.run", fake_run)

    # When: the trusted executor resolves the immutable shared snapshot.
    result = CodexStageExecutor().execute(context)

    # Then: the raw file digest is returned after accepting the persisted prefix.
    assert result.details is not None
    assert result.details["selection_snapshot_sha256"] == snapshot_sha256(path)
