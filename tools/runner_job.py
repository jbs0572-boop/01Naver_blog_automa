from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date
from pathlib import Path
from typing import assert_never
from zoneinfo import ZoneInfo

from tools.contract_types import ContractError, JSONMap
from tools.runner_path_safety import validate_weekly_runner_paths
from tools.runner_secure_fs import secure_json_paths, secure_storage_active
from tools.runner_stages import now, validated_job
from tools.runner_state import read_state, state_paths
from tools.runner_types import JobName, RunnerRequest, RunStatus
from tools.weekly_feedback_inputs import weekly_input_identity


def validate_job_request(request: RunnerRequest) -> JobName:
    if request.naver_adapter is not None and request.notion_adapter is None:
        raise ContractError("Naver adapter requires a Notion adapter")
    job = validated_job(request)
    match job:
        case JobName.DAILY_GENERATE:
            invalid = (request.keyword is None) == (not request.auto_topic)
            if not request.resume and invalid:
                raise ContractError(
                    "daily-generate requires exactly one of --keyword or --auto-topic"
                )
            selection = request.selection_context
            if selection is None:
                raise ContractError("daily-generate requires --as-of-date")
            try:
                normalized_date = date.fromisoformat(selection.as_of_date).isoformat()
            except ValueError as error:
                raise ContractError(
                    "daily-generate requires a valid KST --as-of-date"
                ) from error
            if (
                normalized_date != selection.as_of_date
                or selection.timezone != "Asia/Seoul"
            ):
                raise ContractError("daily-generate requires a valid KST --as-of-date")
            return job
        case JobName.WEEKLY_IMPROVE:
            if request.keyword is not None or request.auto_topic:
                raise ContractError("weekly-improve does not accept a topic option")
            validate_weekly_runner_paths(request)
            return job
        case JobName.NAVER_PUBLISH:
            if request.auto_topic or request.keyword is not None:
                raise ContractError("naver-publish requires run_id only")
            return job
        case _:
            assert_never(job)


def job_key(request: RunnerRequest) -> str:
    timestamp = now(request).astimezone(ZoneInfo("Asia/Seoul"))
    parts = [
        request.job,
        timestamp.date().isoformat(),
        request.keyword or "auto-topic",
        request.selection_context.as_of_date
        if request.selection_context is not None
        else "",
        (
            json.dumps(
                request.selection_context.as_json(),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            if request.selection_context is not None
            else ""
        ),
        "workflow-optimized-v1",
    ]
    if request.model_config is not None:
        parts.append(request.model_config.digest)
    if request.job == JobName.WEEKLY_IMPROVE.value:
        parts.append(
            weekly_input_identity(request.root, timestamp, timestamp.isoformat())
        )
    material = "|".join(parts)
    return "sha256:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


def new_run_id(request: RunnerRequest) -> str:
    timestamp = now(request).strftime("%Y%m%d-%H%M%S")
    return f"RUN-{timestamp}-{uuid.uuid4().hex[:12]}"


def codex_project_is_configured(root: Path) -> bool:
    return all(
        (root / name).is_file()
        for name in (
            "topic-selector.md",
            "researcher.md",
            "writer.md",
            "image-maker.md",
            "content-assembler.md",
            "notion-rider.md",
            "naver-rider.md",
        )
    )


def find_duplicate_job(
    request: RunnerRequest, job: str, state_dir: Path | None
) -> tuple[str, JSONMap, Path, Path] | None:
    base = state_dir if state_dir is not None else request.root / ".automation"
    target_key = job_key(request)
    active = {
        RunStatus.RUNNING.value,
        RunStatus.PASSED.value,
        RunStatus.READY_FOR_NAVER.value,
        RunStatus.AWAITING_USER_CONFIRMATION.value,
        RunStatus.DRAFT_SAVED.value,
    }
    directory = base / "state"
    paths = secure_json_paths(directory) if secure_storage_active() else tuple(
        sorted(directory.glob("*.json"))
    )
    for path in paths:
        try:
            state = read_state(path)
        except ContractError:
            continue
        if (
            state.get("job") == job
            and state.get("job_key") == target_key
            and state.get("status") in active
        ):
            run_id = state.get("run_id")
            if isinstance(run_id, str):
                _, log_path, _ = state_paths(request.root, run_id, state_dir)
                return run_id, state, path, log_path
    return None


__all__ = [
    "codex_project_is_configured",
    "find_duplicate_job",
    "job_key",
    "new_run_id",
    "validate_job_request",
]
