from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path

from tools.codex_stage_error import failure_policy
from tools.contract_types import ContractError, JSONMap
from tools.runner_secure_fs import secure_append, secure_storage_active
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
    encoded = (
        json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n"
    ).encode()
    if secure_storage_active():
        try:
            secure_append(path, encoded)
        except ContractError as error:
            raise ContractError(f"runner log cannot be written: {path}") from error
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("ab") as handle:
            _ = handle.write(encoded)
            handle.flush()
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
    if context.stage == "naver-rider" and outcome.details is not None:
        article_quality = outcome.details.get("article_quality")
        if isinstance(article_quality, dict):
            quality["article_quality"] = article_quality
        confirmation_request_digest = outcome.details.get(
            "confirmation_request_digest"
        )
        if isinstance(confirmation_request_digest, str):
            quality["confirmation_request_digest"] = confirmation_request_digest
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
    payload: JSONMap = {
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
    payload["error_type"] = outcome.error_type
    model_config = request.model_config
    if model_config is not None:
        payload["model_config_digest"] = model_config.digest
        setting = model_config.setting_for(context.stage)
        if setting is not None:
            payload["model"] = setting.model
            payload["reasoning_effort"] = setting.reasoning_effort
    policy = failure_policy(outcome.error_type)
    if policy is not None:
        payload["retryable"] = policy.retryable
        payload["next_action"] = policy.next_action
        payload["retry_stage"] = context.stage
    if outcome.details is not None:
        for key in ("retryable", "next_action", "retry_stage", "process_attempts"):
            value = outcome.details.get(key)
            if isinstance(value, (str, bool, int)):
                payload[key] = value
    if context.batch_slot is not None:
        payload["batch_slot"] = context.batch_slot
    selection = request.selection_context
    if selection is not None and selection.score_version is not None:
        payload["score_version"] = selection.score_version
        payload["score_config_digest"] = selection.score_config_digest
        payload["feedback_manifest_digest"] = selection.feedback_manifest_digest
        payload["feedback_selection_mode"] = selection.feedback_selection_mode
    return payload
