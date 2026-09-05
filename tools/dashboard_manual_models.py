from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from tools.contract_types import JSONMap
from tools.external_adapter import NotionAdapter
from tools.naver_adapter import NaverBrowserAdapter
from tools.runner_types import (
    RunnerRequest,
    RunnerResult,
    StageExecutor,
    TopicSelectionContext,
)

type ManualStatus = Literal["queued", "running", "completed", "failed"]
Runner = Callable[[RunnerRequest], RunnerResult]


@dataclass(frozen=True, slots=True)
class ConfirmationPreview:
    action: str
    target_blog_id: str
    title: str
    images: tuple[str, ...]
    artifact_digest: str

    def as_json(self) -> JSONMap:
        return {
            "action": self.action,
            "target_blog_id": self.target_blog_id,
            "title": self.title,
            "images": list(self.images),
            "artifact_digest": self.artifact_digest,
        }


@dataclass(frozen=True, slots=True)
class ManualRunInput:
    keyword: str | None
    auto_topic: bool
    selection_context: TopicSelectionContext | None = None


@dataclass(frozen=True, slots=True)
class ManualRunView:
    task_id: str
    status: ManualStatus
    submitted_at: str
    updated_at: str
    run_id: str | None = None
    result_status: str | None = None
    message: str | None = None
    error: str | None = None
    retryable: bool = False
    confirmation_preview: ConfirmationPreview | None = None

    def as_json(self) -> JSONMap:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "submitted_at": self.submitted_at,
            "updated_at": self.updated_at,
            "run_id": self.run_id,
            "result_status": self.result_status,
            "message": self.message,
            "error": self.error,
            "retryable": self.retryable,
            "confirmation_preview": (
                self.confirmation_preview.as_json()
                if self.confirmation_preview is not None
                else None
            ),
        }


@dataclass(frozen=True, slots=True)
class ManualRunContext:
    root: Path
    demo: bool
    live_writes: bool


@dataclass(frozen=True, slots=True)
class ManualRunDependencies:
    runner: Runner
    executor: StageExecutor | None = None
    notion_adapter: NotionAdapter | None = None
    naver_adapter: NaverBrowserAdapter | None = None


@dataclass(frozen=True, slots=True)
class ManualRunUpdate:
    status: ManualStatus | None = None
    run_id: str | None = None
    result_status: str | None = None
    message: str | None = None
    error: str | None = None
    retryable: bool | None = None
    confirmation_preview: ConfirmationPreview | None = None
