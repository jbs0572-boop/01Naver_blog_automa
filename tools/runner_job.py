from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from typing import assert_never
from zoneinfo import ZoneInfo

from tools.contract_types import ContractError, JSONMap
from tools.runner_stages import now, validated_job
from tools.runner_state import read_state, state_paths
from tools.runner_types import JobName, RunnerRequest, RunStatus


def validate_job_request(request: RunnerRequest) -> JobName:
    if (request.notion_adapter is None) != (request.naver_adapter is None):
        raise ContractError("Notion and Naver adapters must be configured together")
    job = validated_job(request)
    match job:
        case JobName.DAILY_GENERATE:
            invalid = (request.keyword is None) == (not request.auto_topic)
            if not request.resume and invalid:
                raise ContractError(
                    "daily-generate requires exactly one of --keyword or --auto-topic"
                )
            return job
        case JobName.WEEKLY_IMPROVE:
            if request.keyword is not None or request.auto_topic:
                raise ContractError("weekly-improve does not accept a topic option")
            return job
        case JobName.NAVER_PUBLISH:
            if request.auto_topic or request.keyword is not None:
                raise ContractError("naver-publish requires run_id only")
            return job
        case _:
            assert_never(job)


def job_key(request: RunnerRequest) -> str:
    timestamp = now(request).astimezone(ZoneInfo("Asia/Seoul")).date().isoformat()
    material = "|".join(
        (
            request.job,
            timestamp,
            request.keyword or "auto-topic",
            "workflow-optimized-v1",
        )
    )
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
    for path in sorted((base / "state").glob("*.json")):
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
