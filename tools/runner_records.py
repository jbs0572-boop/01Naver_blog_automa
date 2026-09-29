from __future__ import annotations

import hashlib
import json
from pathlib import Path

from tools.contract_types import ContractError, JSONMap, JSONValue
from tools.manifest import verify_manifest
from tools.runner_state import file_digest
from tools.runner_types import (
    Q1_MAX_ATTEMPTS,
    Q1_RETRY_EXHAUSTED_MESSAGE,
    STAGE_ORDER,
    RunnerRequest,
    RunnerResult,
    RunStatus,
    StageExecution,
)


def set_stage(state: JSONMap, stage: str, status: RunStatus) -> None:
    stages = state.get("stages")
    if not isinstance(stages, dict):
        raise ContractError("runner state stages are invalid")
    stages[stage] = status.value


def set_stage_execution(state: JSONMap, stage: str, execution: StageExecution) -> None:
    executions = state.get("stage_execution")
    if not isinstance(executions, dict):
        raise ContractError("runner state stage_execution is invalid")
    executions[stage] = execution.value


def next_stage_attempt(
    state: JSONMap, root: Path, run_id: str, stage: str
) -> int:
    attempts = state.get("stage_attempts")
    recorded = 0
    if isinstance(attempts, dict):
        value = attempts.get(stage)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            recorded = value
    work_dir = root / ".automation" / "work" / run_id / stage
    stored = [
        int(path.name.removeprefix("attempt-"))
        for path in work_dir.glob("attempt-*")
        if path.is_dir() and path.name.removeprefix("attempt-").isdigit()
    ]
    return max([recorded, *stored], default=0) + 1


def q1_attempts_used(state: JSONMap, root: Path, run_id: str) -> int:
    stage_attempts = state.get("stage_attempts")
    recorded = state.get("q1_attempts_used")
    stage_recorded = (
        stage_attempts.get("content-assembler")
        if isinstance(stage_attempts, dict)
        else None
    )
    values = [
        value
        for value in (recorded, stage_recorded)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0
    ]
    work_dir = root / ".automation" / "work" / run_id / "content-assembler"
    values.extend(
        int(path.name.removeprefix("attempt-"))
        for path in work_dir.glob("attempt-*")
        if path.is_dir() and path.name.removeprefix("attempt-").isdigit()
    )
    log_path = root / ".automation" / "logs" / f"{run_id}.jsonl"
    try:
        lines = log_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    for line in lines:
        try:
            raw_value: JSONValue = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(raw_value, dict):
            continue
        value: JSONMap = {key: item for key, item in raw_value.items()}
        if value.get("stage") == "content-assembler":
            attempt = value.get("attempt")
            if isinstance(attempt, int) and not isinstance(attempt, bool) and attempt >= 0:
                values.append(attempt)
    return min(max(values, default=0), Q1_MAX_ATTEMPTS)


def q1_retry_exhausted(state: JSONMap, root: Path, run_id: str) -> bool:
    return q1_attempts_used(state, root, run_id) >= Q1_MAX_ATTEMPTS


def q1_retry_exhausted_message() -> str:
    return Q1_RETRY_EXHAUSTED_MESSAGE


def record_stage_attempt(state: JSONMap, stage: str, attempt: int) -> None:
    attempts = state.get("stage_attempts")
    if not isinstance(attempts, dict):
        attempts = {}
        state["stage_attempts"] = attempts
    attempts[stage] = attempt
    if stage == "content-assembler":
        previous = state.get("q1_attempts_used")
        prior = previous if isinstance(previous, int) and not isinstance(previous, bool) else 0
        state["q1_attempts_used"] = max(prior, attempt)


def state_output_hash(state: JSONMap, request: RunnerRequest, run_id: str) -> str:
    manifest_path = request.root / "manifests" / f"{run_id}-workflow-manifest.json"
    if manifest_path.is_file():
        return "sha256:" + file_digest(manifest_path)
    value = json.dumps(state.get("stages"), ensure_ascii=False, sort_keys=True).encode(
        "utf-8"
    )
    return "sha256:" + hashlib.sha256(value).hexdigest()


def initial_state(
    request: RunnerRequest, run_id: str, input_hash: str, timestamp: str
) -> JSONMap:
    topic_id = f"TOPIC-{request.keyword}" if request.keyword else f"TOPIC-{run_id}"
    selection = request.selection_context
    return {
        "run_id": run_id,
        "job": request.job,
        "topic_source": "auto_selected" if request.auto_topic else "user_defined",
        "dry_run": request.dry_run,
        "auto_save_naver": request.auto_save_naver,
        "keyword": request.keyword,
        "auto_topic": request.auto_topic,
        "selection_context": (selection.as_json() if selection is not None else None),
        "model_config": (
            request.model_config.as_json() if request.model_config is not None else None
        ),
        "model_config_digest": (
            request.model_config.digest if request.model_config is not None else None
        ),
        "score_version": selection.score_version if selection is not None else None,
        "score_config_digest": (
            selection.score_config_digest if selection is not None else None
        ),
        "feedback_manifest_digest": (
            selection.feedback_manifest_digest if selection is not None else None
        ),
        "feedback_selection_mode": (
            selection.feedback_selection_mode if selection is not None else None
        ),
        "notion_target_id": request.notion_target_id,
        "job_key": None,
        "confirmation": None,
        "naver_save_authorization": None,
        "topic_id": topic_id,
        "manifest_path": None,
        "artifact_digest": None,
        "artifact_paths": [],
        "status": RunStatus.PENDING.value,
        "stages": {stage: RunStatus.PENDING.value for stage in STAGE_ORDER},
        "stage_execution": {
            stage: StageExecution.NOT_CALLED.value for stage in STAGE_ORDER
        },
        "stage_attempts": {stage: 0 for stage in STAGE_ORDER},
        "input_hash": input_hash,
        "output_hash": None,
        "created_at": timestamp,
        "updated_at": timestamp,
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


__all__ = [
    "blocked_result",
    "initial_state",
    "next_stage_attempt",
    "q1_attempts_used",
    "q1_retry_exhausted",
    "q1_retry_exhausted_message",
    "record_manifest",
    "record_stage_attempt",
    "set_stage",
    "set_stage_execution",
    "state_output_hash",
]
