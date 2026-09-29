from __future__ import annotations

import hashlib
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from tools.dashboard_manual_models import (
    ManualBatchView,
    ManualCancelActionView,
    ManualRunInput,
    ManualRunView,
    ManualSnapshotView,
)
from tools.runner_types import TopicSelectionContext
from tools.topic_metadata import normalize_keyword


def _historical_exclusions(root: Path | None) -> tuple[str, ...]:
    if root is None:
        return ()
    research_root = root / "research"
    if not research_root.is_dir():
        return ()
    values: list[str] = []
    seen: set[str] = set()
    for path in sorted(research_root.glob("topic-selection-*.md")):
        keyword = path.name[len("topic-selection-") : -len(".md")]
        normalized = normalize_keyword(keyword)
        if normalized and normalized not in seen:
            seen.add(normalized)
            values.append(keyword)
    return tuple(values)


def new_batch(
    request: ManualRunInput,
    root: Path | None = None,
    *,
    display_id: str | None = None,
) -> ManualBatchView:
    now = datetime.now(UTC)
    timestamp = now.isoformat()
    token = uuid.uuid4().hex[:12]
    batch_id = f"BATCH-{now.strftime('%Y%m%d-%H%M%S')}-{token}"
    base = request.selection_context
    assert base is not None
    snapshot = None
    if request.auto_topic:
        capture_id = f"CAPTURE-{token}"
        snapshot = ManualSnapshotView(
            capture_id,
            (Path("metadata") / "creator-advisor" / base.as_of_date / f"{capture_id}.json").as_posix(),
            None,
        )
    children = tuple(
        _new_child(
            batch_id,
            slot,
            timestamp,
            request,
            snapshot,
            _historical_exclusions(root) if request.auto_topic else (),
            display_id,
        )
        for slot in range(1, request.child_count + 1)
    )
    return ManualBatchView(
        batch_id,
        "queued",
        "auto_selected" if request.auto_topic else "user_defined",
        base.as_of_date,
        timestamp,
        timestamp,
        snapshot,
        children,
        request_nonce_sha256=(
            "sha256:" + hashlib.sha256(request.request_nonce.encode()).hexdigest()
            if request.request_nonce is not None
            else None
        ),
    )


def _new_child(
    batch_id: str,
    slot: int,
    timestamp: str,
    request: ManualRunInput,
    snapshot: ManualSnapshotView | None,
    historical_exclusions: tuple[str, ...],
    display_id: str | None,
) -> ManualRunView:
    run_id = f"RUN-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:12]}"
    base = request.selection_context
    assert base is not None
    selection = TopicSelectionContext(
        base.category,
        base.audience,
        base.publish_purpose,
        base.as_of_date,
        base.timezone,
        batch_id if request.auto_topic else None,
        slot if request.auto_topic else None,
        "capture_once" if request.auto_topic and slot == 1 else ("reuse_only" if request.auto_topic else None),
        snapshot.capture_id if snapshot else None,
        snapshot.path if snapshot else None,
        None,
        historical_exclusions,
    )
    return ManualRunView(
        f"{batch_id}-{slot}",
        "queued",
        timestamp,
        timestamp,
        run_id=run_id,
        child_id=f"CHILD-{slot:02d}",
        slot=slot,
        keyword=request.keyword,
        requested_keyword=request.keyword,
        selection_context=selection,
        model_config=request.model_config,
        cancel_action=ManualCancelActionView(uuid.uuid4().hex, "queued_only"),
        display_id=display_id,
        scheduled_at=request.scheduled_at,
    )


def prepare_child(batch: ManualBatchView, slot: int) -> ManualRunView:
    child = batch.children[slot - 1]
    if batch.topic_source == "user_defined":
        return child
    previous = batch.children[: slot - 1]
    selection_context = child.selection_context
    assert selection_context is not None
    previous_exclusions = tuple(
        item.resolved_keyword for item in previous if item.resolved_keyword
    )
    excluded: list[str] = list(selection_context.excluded_keywords)
    seen = {normalize_keyword(value) for value in excluded}
    for value in previous_exclusions:
        if normalize_keyword(value) not in seen:
            excluded.append(value)
            seen.add(normalize_keyword(value))
    snapshot = batch.snapshot
    assert snapshot is not None and child.selection_context is not None
    context = replace(
        child.selection_context,
        snapshot_sha256=snapshot.sha256,
        excluded_keywords=tuple(excluded),
    )
    return replace(child, selection_context=context)


def aggregate_status(batch: ManualBatchView) -> str:
    if any(child.status == "cancelling" for child in batch.children):
        return "cancelling"
    if any(child.status == "running" for child in batch.children):
        return "running"
    if any(child.status == "queued" for child in batch.children):
        prior_failed = any(
            child.status == "failed" and child.resolved_keyword is None
            for child in batch.children
        )
        return "blocked" if prior_failed else "running"
    if any(child.status == "failed" for child in batch.children):
        return "failed"
    if any(child.status == "cancelled" for child in batch.children):
        return "cancelled"
    return "completed"


__all__ = ["aggregate_status", "new_batch", "prepare_child"]
