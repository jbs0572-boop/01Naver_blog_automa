from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from tools.contract_types import JSONMap
from tools.external_adapter import NotionAdapter
from tools.naver_adapter import NaverBrowserAdapter


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
    confirmed: bool = False
    executor: StageExecutor | None = None
    resume: bool = False
    notion_adapter: NotionAdapter | None = None
    naver_adapter: NaverBrowserAdapter | None = None


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


@dataclass(frozen=True, slots=True)
class StageEventContext:
    request: RunnerRequest
    run_id: str
    batch_id: str
    stage: str
    started_at: str
    ended_at: str
    attempt: int


@dataclass(frozen=True, slots=True)
class StageEventOutcome:
    status: RunStatus
    execution: StageExecution
    message: str | None


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
