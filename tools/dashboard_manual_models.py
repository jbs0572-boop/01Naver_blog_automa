from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from tools.contract_types import JSONMap
from tools.external_adapter import NotionAdapter
from tools.model_presets import ModelConfigSnapshot
from tools.naver_adapter import NaverBrowserAdapter
from tools.runner_types import (
    RunnerRequest,
    RunnerResult,
    StageExecutor,
    TopicSelectionContext,
)

AUTO_BATCH_SIZE: Final = 1

type ManualStatus = Literal["queued", "running", "cancelling", "completed", "failed", "cancelled"]
type ManualBatchStatus = Literal["queued", "running", "cancelling", "blocked", "completed", "failed", "cancelled"]
type ManualTopicSource = Literal["user_defined", "auto_selected"]
type CancellationScope = Literal["queued_only", "remaining"]
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
class ManualActionView:
    kind: Literal["retry", "external", "confirm"]
    nonce: str

    def as_json(self) -> JSONMap:
        return {"kind": self.kind, "nonce": self.nonce}


@dataclass(frozen=True, slots=True)
class ManualCancelActionView:
    nonce: str
    scope: CancellationScope

    def as_json(self) -> JSONMap:
        return {"kind": "cancel", "nonce": self.nonce, "scope": self.scope}


@dataclass(frozen=True, slots=True)
class ManualCancellationView:
    scope: CancellationScope
    requested_at: str
    nonce_sha256: str
    completed_at: str | None = None

    def as_json(self) -> JSONMap:
        return {
            "scope": self.scope,
            "requested_at": self.requested_at,
            "nonce_sha256": self.nonce_sha256,
            "completed_at": self.completed_at,
        }


@dataclass(frozen=True, slots=True)
class ManualSnapshotView:
    capture_id: str
    path: str
    sha256: str | None

    def as_json(self) -> JSONMap:
        return {
            "capture_id": self.capture_id,
            "path": self.path,
            "sha256": self.sha256,
        }


@dataclass(frozen=True, slots=True)
class ManualActiveActionView:
    kind: Literal["initial", "retry", "external", "confirm"]
    operation_id: str
    nonce_sha256: str
    accepted_at: str
    state: Literal["queued", "running"]

    def as_json(self) -> JSONMap:
        return {
            "kind": self.kind,
            "operation_id": self.operation_id,
            "nonce_sha256": self.nonce_sha256,
            "accepted_at": self.accepted_at,
            "state": self.state,
        }


@dataclass(frozen=True, slots=True)
class ManualRunInput:
    keyword: str | None
    auto_topic: bool
    selection_context: TopicSelectionContext | None = None
    preset_id: str | None = None
    model_config: ModelConfigSnapshot | None = None
    request_nonce: str | None = None
    scheduled_at: str | None = None

    @property
    def child_count(self) -> int:
        return AUTO_BATCH_SIZE if self.auto_topic else 1


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
    child_id: str | None = None
    slot: int | None = None
    keyword: str | None = None
    next_action: ManualActionView | None = None
    requested_keyword: str | None = None
    resolved_keyword: str | None = None
    selection_context: TopicSelectionContext | None = None
    active_action: ManualActiveActionView | None = None
    model_config: ModelConfigSnapshot | None = None
    cancellation: ManualCancellationView | None = None
    cancel_action: ManualCancelActionView | None = None
    display_id: str | None = None
    scheduled_at: str | None = None
    started_at: str | None = None
    ended_at: str | None = None

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
            "child_id": self.child_id,
            "slot": self.slot,
            "keyword": self.keyword,
            "next_action": (
                self.next_action.as_json() if self.next_action is not None else None
            ),
            "requested_keyword": self.requested_keyword,
            "resolved_keyword": self.resolved_keyword,
            "selection_context": (
                self.selection_context.as_json()
                if self.selection_context is not None
                else None
            ),
            "active_action": (
                self.active_action.as_json() if self.active_action is not None else None
            ),
            "model_config": (
                self.model_config.as_json() if self.model_config is not None else None
            ),
            "cancellation": (
                self.cancellation.as_json() if self.cancellation is not None else None
            ),
            "cancel_action": (
                self.cancel_action.as_json() if self.cancel_action is not None else None
            ),
            "display_id": self.display_id,
            "scheduled_at": self.scheduled_at,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
        }


@dataclass(frozen=True, slots=True)
class ManualBatchView:
    batch_id: str
    status: ManualBatchStatus
    topic_source: ManualTopicSource
    as_of_date: str
    submitted_at: str
    updated_at: str
    snapshot: ManualSnapshotView | None
    children: tuple[ManualRunView, ...]
    request_nonce_sha256: str | None = None
    request_payload_sha256: str | None = None

    def as_json(self) -> JSONMap:
        return {
            "batch_id": self.batch_id,
            "status": self.status,
            "topic_source": self.topic_source,
            "as_of_date": self.as_of_date,
            "submitted_at": self.submitted_at,
            "updated_at": self.updated_at,
            "snapshot": self.snapshot.as_json() if self.snapshot is not None else None,
            "children": [child.as_json() for child in self.children],
            "request_nonce_sha256": self.request_nonce_sha256,
            "request_payload_sha256": self.request_payload_sha256,
        }


@dataclass(frozen=True, slots=True)
class ManualRunContext:
    root: Path
    live_writes: bool


@dataclass(frozen=True, slots=True)
class ManualRunDependencies:
    runner: Runner
    recoverer: Runner | None = None
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
