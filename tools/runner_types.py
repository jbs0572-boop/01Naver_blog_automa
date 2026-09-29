from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Final, Literal, Protocol

from tools.codex_stage_error import StageFailureType
from tools.contract_types import ContractError, JSONMap
from tools.external_adapter import NotionAdapter
from tools.model_presets import ModelConfigSnapshot
from tools.naver_adapter import NaverBrowserAdapter
from tools.q1_feedback import Q1FailureCode

Q1_MAX_ATTEMPTS: Final = 3
Q1_RETRY_EXHAUSTED_MESSAGE: Final = (
    "Q1 retry limit exhausted; no further producer call permitted"
)
_SENSITIVE_Q1_VALUE: Final = re.compile(
    r"(?i)\b(api[_-]?key|token|secret|password|authorization|cookie)\b\s*(?:=|:)\s*[^\s,;]+"
)
_BEARER_Q1_VALUE: Final = re.compile(r"(?i)bearer\s+[^\s,;]+")


def safe_q1_feedback(message: str | None) -> str:
    normalized = " ".join(message.split()) if message else "Q1 validation failed"
    redacted = _SENSITIVE_Q1_VALUE.sub(r"\1=[redacted]", normalized)
    return _BEARER_Q1_VALUE.sub("Bearer [redacted]", redacted)[:500]


class JobName(StrEnum):
    DAILY_GENERATE = "daily-generate"
    WEEKLY_IMPROVE = "weekly-improve"
    NAVER_PUBLISH = "naver-publish"


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    VALIDATED = "validated"
    FAILED = "failed"
    BLOCKED = "blocked"
    SKIPPED = "skipped"
    LOCAL_ONLY = "local-only"
    READY_FOR_NAVER = "ready_for_naver"
    AWAITING_USER_CONFIRMATION = "awaiting_user_confirmation"
    DRAFT_SAVED = "draft_saved"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class TopicSelectionContext:
    category: str
    audience: str
    publish_purpose: str
    as_of_date: str
    timezone: str = "Asia/Seoul"
    batch_id: str | None = None
    batch_slot: int | None = None
    snapshot_policy: Literal["capture_once", "reuse_only"] | None = None
    capture_id: str | None = None
    snapshot_path: str | None = None
    snapshot_sha256: str | None = None
    excluded_keywords: tuple[str, ...] = ()
    score_version: str | None = None
    score_config_digest: str | None = None
    feedback_manifest_digest: str | None = None
    feedback_selection_mode: Literal["baseline", "shadow", "active"] | None = None

    def as_json(self) -> JSONMap:
        value: JSONMap = {"as_of_date": self.as_of_date, "timezone": self.timezone}
        if self.category or self.audience or self.publish_purpose:
            value.update(
                {
                    "category": self.category,
                    "audience": self.audience,
                    "publish_purpose": self.publish_purpose,
                }
            )
        if self.batch_id is not None:
            value["batch_id"] = self.batch_id
        if self.batch_slot is not None:
            value["batch_slot"] = self.batch_slot
        if self.snapshot_policy is not None:
            value["snapshot_policy"] = self.snapshot_policy
        if self.capture_id is not None:
            value["capture_id"] = self.capture_id
        if self.snapshot_path is not None:
            value["snapshot_path"] = self.snapshot_path
        if self.snapshot_sha256 is not None:
            value["snapshot_sha256"] = self.snapshot_sha256
        if self.excluded_keywords:
            value["excluded_keywords"] = list(self.excluded_keywords)
        if self.score_version is not None:
            value["score_version"] = self.score_version
        if self.score_config_digest is not None:
            value["score_config_digest"] = self.score_config_digest
        if self.feedback_manifest_digest is not None:
            value["feedback_manifest_digest"] = self.feedback_manifest_digest
        if self.feedback_selection_mode is not None:
            value["feedback_selection_mode"] = self.feedback_selection_mode
        return value


class StageExecution(StrEnum):
    NOT_CALLED = "not_called"
    ATTEMPTED = "attempted"
    PRODUCED = "produced"
    VALIDATED = "validated"
    AGGREGATED = "aggregated"


class RunnerBlocked(Exception):
    pass


@dataclass(frozen=True, slots=True)
class StageResult:
    status: RunStatus
    execution: StageExecution
    message: str | None = None
    artifacts: tuple[str, ...] = ()
    resolved_keyword: str | None = None
    run_status: RunStatus | None = None
    details: JSONMap | None = None
    error_type: str | None = None


@dataclass(frozen=True, slots=True)
class StageExecutionContext:
    root: Path
    stage: str
    run_id: str
    topic_id: str
    keyword: str | None
    work_dir: Path
    selection_context: TopicSelectionContext | None = None
    q1_feedback: str | None = None
    q1_preflight_codes: tuple[str, ...] = ()
    model_config: ModelConfigSnapshot | None = None
    stage_attempt: int = 1


class StageExecutor(Protocol):
    def execute(self, context: StageExecutionContext) -> StageResult: ...


STAGE_ORDER = (
    "topic-selector",
    "researcher",
    "writer",
    "image-maker",
    "content-assembler",
    "notion-rider",
    "naver-rider",
)


@dataclass(frozen=True, slots=True)
class RunnerRequest:
    root: Path
    job: str
    keyword: str | None = None
    run_id: str | None = None
    dry_run: bool = False
    state_dir: Path | None = None
    now: datetime | None = None
    auto_topic: bool = False
    selection_context: TopicSelectionContext | None = None
    confirmed: bool = False
    executor: StageExecutor | None = None
    resume: bool = False
    notion_target_id: str | None = None
    notion_adapter: NotionAdapter | None = None
    naver_adapter: NaverBrowserAdapter | None = None
    auto_save_naver: bool = False
    model_config: ModelConfigSnapshot | None = None


def retry_q1_repair(
    operation: Callable[[str | None], tuple[StageResult, int, RunnerRequest]],
    attempts_used: int = 0,
    on_temporary_retry: Callable[[float], None] | None = None,
) -> tuple[StageResult, RunnerRequest, tuple[StageResult, ...]]:
    feedback: str | None = None
    attempts: list[StageResult] = []
    active_request: RunnerRequest | None = None
    temporary_retries = 0
    for _ in range(max(0, Q1_MAX_ATTEMPTS - attempts_used)):
        result, _, active_request = operation(feedback)
        if result.status is RunStatus.FAILED:
            result = replace(result, message=safe_q1_feedback(result.message))
        attempts.append(result)
        if result.status is not RunStatus.FAILED:
            return result, active_request, tuple(attempts)
        error_type = result.error_type
        if error_type == StageFailureType.TEMPORARY_IO.value:
            if temporary_retries >= 1:
                return result, active_request, tuple(attempts)
            temporary_retries += 1
            feedback = None
            if on_temporary_retry is not None:
                on_temporary_retry(1.0)
            continue
        if error_type not in {None, Q1FailureCode.CONTRACT_FAILURE.value}:
            return result, active_request, tuple(attempts)
        feedback = safe_q1_feedback(result.message)
    if active_request is None:
        raise ContractError("Q1 repair loop did not attempt content assembly")
    return (
        replace(
            attempts[-1],
            message=f"Q1 failed after {Q1_MAX_ATTEMPTS} attempts: {feedback}",
        ),
        active_request,
        tuple(attempts),
    )


@dataclass(frozen=True, slots=True)
class RunExecutionContext:
    request: RunnerRequest
    job: JobName
    run_id: str
    state_path: Path
    log_path: Path
    input_hash: str
    allow_existing: bool


@dataclass(frozen=True, slots=True)
class StageRunContext:
    request: RunnerRequest
    job: JobName
    stage: str
    run_id: str
    created_at: str
    q1_feedback: str | None = None
    q1_preflight_codes: tuple[str, ...] = ()
    stage_attempt: int = 1


@dataclass(frozen=True, slots=True)
class StageEventContext:
    request: RunnerRequest
    run_id: str
    batch_id: str
    stage: str
    started_at: str
    ended_at: str
    attempt: int
    duration_ms: float
    depends_on: tuple[str, ...]
    batch_slot: int | None = None


@dataclass(frozen=True, slots=True)
class StageEventOutcome:
    status: RunStatus
    execution: StageExecution
    message: str | None
    details: JSONMap | None = None
    error_type: str | None = None


@dataclass(frozen=True, slots=True)
class ConfirmationInput:
    root: Path
    run_id: str
    action: str
    state_dir: Path | None = None
    actor: str = "operator"
    executor: StageExecutor | None = None
    notion_adapter: NotionAdapter | None = None
    naver_adapter: NaverBrowserAdapter | None = None
    confirmation_nonce: str | None = None


@dataclass(frozen=True, slots=True)
class RunnerResult:
    run_id: str
    status: RunStatus
    state_path: Path
    log_path: Path
    stages: tuple[str, ...]
    message: str

    def as_json(self) -> JSONMap:
        return {
            "run_id": self.run_id,
            "status": self.status.value,
            "state_path": str(self.state_path),
            "log_path": str(self.log_path),
            "stages": list(self.stages),
            "message": self.message,
        }
