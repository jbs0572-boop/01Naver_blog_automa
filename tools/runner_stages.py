from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path

from tools.contract_types import ContractError, JSONMap
from tools.runner_types import (
    JobName,
    RunnerRequest,
    StageEventContext,
    StageEventOutcome,
)


def now(request: RunnerRequest) -> datetime:
    value = request.now if request.now is not None else datetime.now().astimezone()
    if value.tzinfo is None or value.utcoffset() is None:
        raise ContractError("runner timestamps must include a timezone")
    return value


def monotonic_ns(_request: RunnerRequest) -> int:
    return time.monotonic_ns()


def sleep(seconds: float) -> None:
    time.sleep(seconds)


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
    if request.run_id is not None:
        _safe_component(request.run_id, "run_id")
    if job is JobName.DAILY_GENERATE and request.keyword is not None:
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


def _topic_id(request: RunnerRequest, run_id: str) -> str:
    return f"TOPIC-{request.keyword}" if request.keyword else f"TOPIC-{run_id}"


def _quality(context: StageEventContext, outcome: StageEventOutcome) -> JSONMap:
    quality: JSONMap = {
        "runner": "local",
        "external_call": False,
        "dry_run": context.request.dry_run,
        "execution": outcome.execution.value,
    }
    if outcome.details is not None:
        external_call = outcome.details.get("external_call")
        if isinstance(external_call, bool):
            quality["external_call"] = external_call
    if context.stage == "content-assembler" and outcome.details is not None:
        artifact_digest = outcome.details.get("artifact_digest")
        if isinstance(artifact_digest, str):
            quality["artifact_digest"] = artifact_digest
    if context.stage != "notion-rider" or outcome.details is None:
        return quality
    for key in (
        "storage_integrity",
        "notion_page_id",
        "notion_last_verified_at",
        "notion_target_id",
        "expected_notion_content_digest",
        "notion_content_digest",
        "notion_roundtrip_digest",
        "artifact_digest",
        "first_image_block",
    ):
        value = outcome.details.get(key)
        if isinstance(value, str):
            quality[key] = value
    return quality


def event(context: StageEventContext, outcome: StageEventOutcome) -> JSONMap:
    request = context.request
    return {
        "event_type": "stage",
        "pipeline_version": "workflow-optimized-v1",
        "telemetry_version": 2,
        "batch_id": context.batch_id,
        "run_id": context.run_id,
        "topic_id": _topic_id(request, context.run_id),
        "topic_source": "auto_selected" if request.auto_topic else "user_defined",
        "stage": context.stage,
        "started_at": context.started_at,
        "ended_at": context.ended_at,
        "duration_ms": context.duration_ms,
        "depends_on": list(context.depends_on),
        "status": outcome.status.value,
        "attempt": context.attempt,
        "quality": _quality(context, outcome),
        "error_type": None,
        "error_message_safe": outcome.message,
    }
