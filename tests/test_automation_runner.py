from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tools.automation_runner import recover_job, run_job, stable_run_id
from tools.contract_types import ContractError
from tools.log_contract import read_events
from tools.runner_state import read_state, state_paths
from tools.runner_types import RunnerRequest, RunStatus

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)


def _root(
    tmp_path: Path, keyword: str = "runner-topic", include_copy: bool = True
) -> Path:
    final_dir = tmp_path / "final"
    asset_dir = tmp_path / "assets" / keyword
    final_dir.mkdir(parents=True)
    asset_dir.mkdir(parents=True)
    _ = (asset_dir / "body.png").write_bytes(b"body")
    _ = (asset_dir / "thumbnail.png").write_bytes(b"thumbnail")
    _ = (asset_dir / "image-map.md").write_text("# map\n", encoding="utf-8")
    _ = (final_dir / f"{keyword}.md").write_text(
        f"![body](../assets/{keyword}/body.png)\n", encoding="utf-8"
    )
    _ = (final_dir / f"{keyword}-naver-layout.md").write_text(
        "# layout\n", encoding="utf-8"
    )
    if include_copy:
        _ = (final_dir / f"{keyword}-naver-copy.md").write_text(
            "# copy\n", encoding="utf-8"
        )
    return tmp_path


def _request(
    root: Path, keyword: str = "runner-topic", run_id: str | None = None
) -> RunnerRequest:
    return RunnerRequest(
        root=root,
        job="daily-generate",
        keyword=keyword,
        run_id=run_id,
        now=NOW,
    )


def test_daily_runner_writes_manifest_state_and_optimized_log(tmp_path: Path) -> None:
    root = _root(tmp_path)

    result = run_job(_request(root))

    assert result.status is RunStatus.LOCAL_ONLY
    state = read_state(result.state_path)
    assert isinstance(state["input_hash"], str) and state["input_hash"].startswith(
        "sha256:"
    )
    assert isinstance(state["output_hash"], str)
    assert state["topic_id"] == "TOPIC-runner-topic"
    assert state["manifest_path"] == (
        f"manifests/{result.run_id}-workflow-manifest.json"
    )
    assert isinstance(state["artifact_digest"], str)
    artifact_paths = state["artifact_paths"]
    assert isinstance(artifact_paths, list)
    assert "final/runner-topic.md" in artifact_paths
    stages = state["stages"]
    assert isinstance(stages, dict)
    assert stages["content-assembler"] == "validated"
    assert stages["notion-rider"] == "skipped"
    assert (root / "manifests" / f"{result.run_id}-workflow-manifest.json").is_file()
    assert len(read_events(result.log_path)) == 7


def test_daily_runner_distinguishes_local_validation_from_execution(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)

    result = run_job(_request(root))

    assert result.status is RunStatus.LOCAL_ONLY
    state = read_state(result.state_path)
    stages = state["stages"]
    execution = state["stage_execution"]
    assert isinstance(stages, dict)
    assert isinstance(execution, dict)
    assert stages["topic-selector"] == "skipped"
    assert execution["topic-selector"] == "not_called"
    assert stages["content-assembler"] == "validated"
    assert execution["content-assembler"] == "validated"
    assert stages["notion-rider"] == "skipped"
    assert execution["notion-rider"] == "not_called"
    events = read_events(result.log_path)
    content_event = next(
        event for event in events if event.get("stage") == "content-assembler"
    )
    assert content_event["status"] == "validated"
    quality = content_event["quality"]
    assert isinstance(quality, dict)
    assert quality["execution"] == "validated"
    assert "external storage is pending" in result.message


def test_weekly_runner_aggregates_logs_and_artifacts_read_only(tmp_path: Path) -> None:
    root = _root(tmp_path)
    _ = (root / "runs").mkdir()
    _ = (root / "runs" / "existing.jsonl").write_text(
        json.dumps({"event_type": "baseline"}) + "\n", encoding="utf-8"
    )
    request = RunnerRequest(root=root, job="weekly-improve", now=NOW)

    result = run_job(request)

    assert result.status is RunStatus.PASSED
    assert "1 log events" in result.message
    assert "artifacts" in result.message
    assert (
        not (root / "runs" / "existing.jsonl")
        .read_text(encoding="utf-8")
        .endswith("updated\n")
    )


def test_failed_stage_short_circuits_downstream_stages(tmp_path: Path) -> None:
    root = _root(tmp_path, include_copy=False)

    result = run_job(_request(root))

    assert result.status is RunStatus.FAILED
    state = read_state(result.state_path)
    stages = state["stages"]
    assert isinstance(stages, dict)
    assert stages["content-assembler"] == "failed"
    execution = state["stage_execution"]
    assert isinstance(execution, dict)
    assert execution["content-assembler"] == "attempted"
    assert stages["notion-rider"] == "skipped"
    events = read_events(result.log_path)
    assert all(event.get("stage") != "notion-rider" for event in events)
    assert all(event.get("stage") != "naver-rider" for event in events)


def test_live_lock_blocks_duplicate_and_stale_lock_is_recovered(tmp_path: Path) -> None:
    root = _root(tmp_path)
    request = _request(root, run_id="RUN-lock")
    state_path, log_path, lock_path = state_paths(root, request.run_id or "", None)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    _ = lock_path.write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")

    blocked = run_job(request)

    assert blocked.status is RunStatus.BLOCKED
    assert lock_path.is_file()
    _ = lock_path.write_text(json.dumps({"pid": 999_999_999}), encoding="utf-8")
    local_only = run_job(request)

    assert local_only.status is RunStatus.LOCAL_ONLY
    assert not lock_path.exists()
    assert state_path.is_file()
    assert log_path.is_file()


def test_recovery_refuses_changed_input_hash(tmp_path: Path) -> None:
    root = _root(tmp_path, include_copy=False)
    request = _request(root, run_id="RUN-recovery")
    failed = run_job(request)
    _ = (root / "final" / "runner-topic-naver-copy.md").write_text(
        "# fixed\n", encoding="utf-8"
    )

    recovered = recover_job(request)

    assert failed.status is RunStatus.FAILED
    assert recovered.status is RunStatus.BLOCKED
    assert "input hash changed" in recovered.message
    assert read_state(recovered.state_path)["status"] == "blocked"


def test_naver_publish_is_dry_run_only(tmp_path: Path) -> None:
    root = _root(tmp_path)
    request = RunnerRequest(
        root=root,
        job="naver-publish",
        run_id="RUN-publish",
        dry_run=True,
        now=NOW,
    )

    result = run_job(request)

    assert result.status is RunStatus.BLOCKED
    assert "Q1/Q2" in result.message
    assert "external_call" not in result.message


def test_invalid_runner_request_does_not_write_state(tmp_path: Path) -> None:
    request = RunnerRequest(
        root=tmp_path, job="daily-generate", keyword="../unsafe", now=NOW
    )

    with pytest.raises(ContractError):
        _ = run_job(request)
    assert not (tmp_path / ".automation").exists()


@given(
    st.text(
        alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Nd")),
        min_size=1,
        max_size=20,
    )
)
def test_stable_run_id_is_deterministic_for_generated_keywords(keyword: str) -> None:
    request = RunnerRequest(
        root=Path("."), job="daily-generate", keyword=keyword, now=NOW
    )

    assert stable_run_id(request) == stable_run_id(request)
    assert stable_run_id(request).startswith("RUN-")
