from __future__ import annotations

import hashlib
import json
from pathlib import Path

from tools.contract_types import ContractError, JSONMap
from tools.manifest import verify_manifest
from tools.runner_state import file_digest
from tools.runner_types import (
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
    return {
        "run_id": run_id,
        "job": request.job,
        "topic_source": "auto_selected" if request.auto_topic else "user_defined",
        "dry_run": request.dry_run,
        "keyword": request.keyword,
        "auto_topic": request.auto_topic,
        "selection_context": (
            request.selection_context.as_json()
            if request.selection_context is not None
            else None
        ),
        "notion_target_id": request.notion_target_id,
        "job_key": None,
        "confirmation": None,
        "topic_id": topic_id,
        "manifest_path": None,
        "artifact_digest": None,
        "artifact_paths": [],
        "status": RunStatus.PENDING.value,
        "stages": {stage: RunStatus.PENDING.value for stage in STAGE_ORDER},
        "stage_execution": {
            stage: StageExecution.NOT_CALLED.value for stage in STAGE_ORDER
        },
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
    "record_manifest",
    "set_stage",
    "set_stage_execution",
    "state_output_hash",
]
