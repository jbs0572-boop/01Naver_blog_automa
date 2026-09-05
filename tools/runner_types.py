from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Final, Protocol

from tools.contract_types import ContractError, JSONMap
from tools.external_adapter import NotionAdapter
from tools.naver_adapter import NaverBrowserAdapter

Q1_MAX_ATTEMPTS: Final = 3
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


@dataclass(frozen=True, slots=True)
class TopicSelectionContext:
    category: str
    audience: str
    publish_purpose: str
    as_of_date: str
    timezone: str = "Asia/Seoul"

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


def retry_q1_repair(
    operation: Callable[[str | None], tuple[StageResult, int, RunnerRequest]],
) -> tuple[StageResult, RunnerRequest, tuple[StageResult, ...]]:
    feedback: str | None = None
    attempts: list[StageResult] = []
    active_request: RunnerRequest | None = None
    for _ in range(Q1_MAX_ATTEMPTS):
        result, _, active_request = operation(feedback)
        if result.status is RunStatus.FAILED:
            result = replace(result, message=safe_q1_feedback(result.message))
        attempts.append(result)
        if result.status is not RunStatus.FAILED:
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


@dataclass(frozen=True, slots=True)
class StageEventOutcome:
    status: RunStatus
    execution: StageExecution
    message: str | None
    details: JSONMap | None = None


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
