from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from tools.contract_types import ContractError, JSONMap
from tools.log_contract import read_events
from tools.manifest import build_manifest, verify_manifest
from tools.runner_state import atomic_write_json, file_digest
from tools.runner_types import (
    STAGE_ORDER,
    JobName,
    RunnerBlocked,
    RunnerRequest,
    RunnerResult,
    RunStatus,
)


def now(request: RunnerRequest) -> datetime:
    value = request.now if request.now is not None else datetime.now().astimezone()
    if value.tzinfo is None or value.utcoffset() is None:
        raise ContractError("runner timestamps must include a timezone")
    return value


def _safe_component(value: str, label: str) -> None:
    parts = Path(value).parts
    if (
        not value
        or Path(value).is_absolute()
        or len(parts) != 1
        or parts[0] in {".", ".."}
    ):
        raise ContractError(f"{label} must be a single safe path component")


def validated_job(request: RunnerRequest) -> JobName:
    try:
        job = JobName(request.job)
    except ValueError as error:
        raise ContractError(f"unsupported runner job: {request.job}") from error
    if request.mode not in {"beta", "formal"}:
        raise ContractError("mode must be beta or formal")
    if request.run_id is not None:
        _safe_component(request.run_id, "run_id")
    if job is JobName.DAILY_GENERATE:
        if request.keyword is None:
            raise ContractError("daily-generate requires keyword")
        _safe_component(request.keyword, "keyword")
    if job is JobName.NAVER_PUBLISH and request.run_id is None:
        raise ContractError("naver-publish requires run_id")
    return job


def append_event(path: Path, event: JSONMap) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("a", encoding="utf-8") as handle:
            _ = handle.write(
                json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n"
            )
            _ = handle.flush()
            os.fsync(handle.fileno())
    except OSError as error:
        raise ContractError(f"runner log cannot be written: {path}") from error


def set_stage(state: JSONMap, stage: str, status: RunStatus) -> None:
    stages = state.get("stages")
    if not isinstance(stages, dict):
        raise ContractError("runner state stages are invalid")
    stages[stage] = status.value


def _topic_id(request: RunnerRequest, run_id: str) -> str:
    return f"TOPIC-{request.keyword}" if request.keyword else f"TOPIC-{run_id}"


def event(
    request: RunnerRequest,
    run_id: str,
    batch_id: str,
    stage: str,
    status: RunStatus,
    started_at: str,
    ended_at: str,
    attempt: int,
    message: str | None = None,
) -> JSONMap:
    return {
        "event_type": "stage",
        "pipeline_version": "workflow-optimized-v1",
        "batch_id": batch_id,
        "run_id": run_id,
        "topic_id": _topic_id(request, run_id),
        "stage": stage,
        "started_at": started_at,
        "ended_at": ended_at,
        "status": status.value,
        "attempt": attempt,
        "quality": {
            "runner": "local",
            "external_call": False,
            "dry_run": request.dry_run,
        },
        "error_type": None,
        "error_message_safe": message,
    }


def with_retry[T](operation: Callable[[], T]) -> tuple[T, int]:
    for attempt in (1, 2):
        try:
            return operation(), attempt
        except (OSError, TimeoutError):
            if attempt == 2:
                raise
            time.sleep(0.05 * (2 ** (attempt - 1)))
    raise ContractError("runner retry loop did not complete")


def _manifest_for(request: RunnerRequest, run_id: str, created_at: str) -> Path:
    if request.keyword is None:
        raise ContractError("manifest creation requires keyword")
    path = request.root / "manifests" / f"{run_id}-workflow-manifest.json"
    if path.is_file():
        manifest = verify_manifest(request.root, path)
        if (
            manifest.run_id != run_id
            or manifest.topic_id != f"TOPIC-{request.keyword}"
            or manifest.mode != request.mode
        ):
            raise ContractError("existing manifest identity does not match the run")
        return path
    data = build_manifest(
        request.root,
        request.keyword,
        run_id,
        f"TOPIC-{request.keyword}",
        request.mode,
        created_at,
    )
    atomic_write_json(path, data)
    _ = verify_manifest(request.root, path)
    return path


def stage_action(
    request: RunnerRequest, job: JobName, stage: str, run_id: str, created_at: str
) -> tuple[RunStatus, str | None]:
    if job is JobName.DAILY_GENERATE:
        if stage == "content-assembler":
            _ = _manifest_for(request, run_id, created_at)
            return RunStatus.PASSED, "canonical manifest verified"
        if stage in {"notion-rider", "naver-rider"}:
            return RunStatus.SKIPPED, "external integration not called by local runner"
        return RunStatus.PASSED, "stage order recorded; producer remains local"
    if job is JobName.WEEKLY_IMPROVE:
        if stage != "researcher":
            return RunStatus.SKIPPED, "weekly-improve is read-only aggregation"
        event_count = sum(
            len(read_events(path))
            for path in sorted((request.root / "runs").glob("*.jsonl"))
        )
        artifact_count = sum(
            1
            for directory in ("research", "drafts", "final", "assets")
            for path in sorted((request.root / directory).rglob("*"))
            if path.is_file()
        )
        return RunStatus.PASSED, (
            f"read-only improvement inputs aggregated: {event_count} log events, "
            f"{artifact_count} artifacts"
        )
    if stage != "naver-rider":
        return RunStatus.SKIPPED, "naver-publish does not execute upstream stages"
    manifest_path = request.root / "manifests" / f"{run_id}-workflow-manifest.json"
    if manifest_path.is_file():
        _ = verify_manifest(request.root, manifest_path)
    if not request.dry_run:
        raise RunnerBlocked(
            "naver-publish requires --dry-run; external writes are disabled"
        )
    raise RunnerBlocked("Gate B approval is required; dry-run made no external call")


def state_output_hash(state: JSONMap, request: RunnerRequest, run_id: str) -> str:
    manifest_path = request.root / "manifests" / f"{run_id}-workflow-manifest.json"
    if manifest_path.is_file():
        return "sha256:" + file_digest(manifest_path)
    value = json.dumps(state.get("stages"), ensure_ascii=False, sort_keys=True).encode(
        "utf-8"
    )
    return "sha256:" + hashlib.sha256(value).hexdigest()


def initial_state(
    request: RunnerRequest, run_id: str, input_hash: str, now: str
) -> JSONMap:
    return {
        "run_id": run_id,
        "job": request.job,
        "mode": request.mode,
        "keyword": request.keyword,
        "topic_id": _topic_id(request, run_id),
        "manifest_path": None,
        "artifact_digest": None,
        "artifact_paths": [],
        "status": RunStatus.PENDING.value,
        "stages": {stage: RunStatus.PENDING.value for stage in STAGE_ORDER},
        "input_hash": input_hash,
        "output_hash": None,
        "created_at": now,
        "updated_at": now,
        "message": "run created",
    }


def record_manifest(state: JSONMap, request: RunnerRequest, run_id: str) -> None:
    manifest_path = request.root / "manifests" / f"{run_id}-workflow-manifest.json"
    if not manifest_path.is_file():
        return
    manifest = verify_manifest(request.root, manifest_path)
    state["topic_id"] = manifest.topic_id
    state["manifest_path"] = (
        manifest_path.resolve().relative_to(request.root.resolve()).as_posix()
    )
    state["artifact_digest"] = manifest.artifact_digest
    state["artifact_paths"] = [entry.path for entry in manifest.files]


def blocked_result(
    run_id: str, state_path: Path, log_path: Path, message: str
) -> RunnerResult:
    return RunnerResult(run_id, RunStatus.BLOCKED, state_path, log_path, (), message)
