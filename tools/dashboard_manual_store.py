from __future__ import annotations

import fcntl
import hashlib
import json
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Final, Literal

from tools.contract_types import ContractError, JSONMap, JSONValue, SchemaError
from tools.dashboard_manual_models import (
    CancellationScope,
    ConfirmationPreview,
    ManualActionView,
    ManualActiveActionView,
    ManualBatchStatus,
    ManualBatchView,
    ManualCancelActionView,
    ManualCancellationView,
    ManualRunInput,
    ManualRunView,
    ManualSnapshotView,
    ManualStatus,
    ManualTopicSource,
)
from tools.model_presets import (
    ModelConfigSnapshot,
    parse_model_config_snapshot,
)
from tools.runner_state import atomic_write_json
from tools.runner_types import TopicSelectionContext
from tools.schema_validation import validate_instance

SCHEMA_VERSION: Final = "manual-batch-v1"
MESSAGE_LIMIT: Final = 500
SCHEMA_PATH: Final = Path(__file__).resolve().parents[1] / "schemas" / "manual-batch.schema.json"
type ManualActionKind = Literal["retry", "external", "confirm"]


class ManualBatchStore:
    def __init__(self, root: Path) -> None:
        self._directory: Path = root / ".automation" / "dashboard" / "manual-batches"
        self._lock_path: Path = root / ".automation" / "dashboard" / "manual-batches.lock"

    def store_path(self, batch_id: str) -> Path:
        return self._directory / f"{_batch_id(batch_id)}.json"

    def save(self, view: ManualBatchView) -> None:
        path = self.store_path(view.batch_id)
        payload = _payload(view)
        _validate(payload)
        _validate_batch(payload, view.batch_id)
        atomic_write_json(path, payload)

    def get(self, batch_id: str) -> ManualBatchView | None:
        path = self.store_path(batch_id)
        if not path.is_file():
            return None
        return _decode(_read(path), batch_id)

    def list(self, limit: int) -> tuple[ManualBatchView, ...]:
        if limit < 1:
            raise ContractError("manual batch limit must be positive")
        if not self._directory.exists():
            return ()
        try:
            paths = tuple(path for path in self._directory.glob("*.json") if path.is_file())
        except OSError as error:
            raise ContractError("could not list manual batches") from error
        batches = tuple(_decode(_read(path), path.stem) for path in paths)
        return tuple(sorted(batches, key=lambda batch: batch.submitted_at, reverse=True)[:limit])

    def snapshot(self) -> tuple[ManualBatchView, ...]:
        return self.list(1_000_000)

    def accept(
        self,
        request: ManualRunInput,
        factory: Callable[[str], ManualBatchView],
    ) -> tuple[ManualBatchView, bool]:
        if request.request_nonce is None or request.selection_context is None:
            raise ContractError("manual request_nonce is required for acceptance")
        nonce_sha256 = "sha256:" + hashlib.sha256(request.request_nonce.encode()).hexdigest()
        payload_sha256 = _request_payload_sha256(request)
        with self._locked():
            batches = self.snapshot()
            existing = next(
                (batch for batch in batches if batch.request_nonce_sha256 == nonce_sha256),
                None,
            )
            if existing is not None:
                if existing.request_payload_sha256 != payload_sha256:
                    raise ContractError("manual request_nonce was reused with a different payload")
                return existing, False
            prefix = f"{request.selection_context.as_of_date}_"
            sequences = tuple(
                int(child.display_id[len(prefix) :])
                for batch in batches
                for child in batch.children
                if child.display_id is not None
                and child.display_id.startswith(prefix)
                and child.display_id[len(prefix) :].isdecimal()
            )
            display_id = f"{prefix}{max(sequences, default=0) + 1:03d}"
            batch = replace(
                factory(display_id),
                request_nonce_sha256=nonce_sha256,
                request_payload_sha256=payload_sha256,
            )
            self.save(batch)
            return batch, True

    @contextmanager
    def _locked(self) -> Generator[None, None, None]:
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock_path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _request_payload_sha256(request: ManualRunInput) -> str:
    context = request.selection_context
    if context is None:
        raise ContractError("manual selection context is required")
    material: JSONMap = {
        "keyword": request.keyword,
        "auto_topic": request.auto_topic,
        "as_of_date": context.as_of_date,
        "preset_id": request.preset_id,
        "model_config": request.model_config.as_json() if request.model_config else None,
        "scheduled_at": request.scheduled_at,
    }
    encoded = json.dumps(
        material, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _batch_id(value: str) -> str:
    parts = Path(value).parts
    if (
        not value.startswith("BATCH-")
        or Path(value).is_absolute()
        or len(parts) != 1
        or parts[0] in {".", ".."}
    ):
        raise ContractError("manual batch_id must be a safe BATCH identifier")
    return value


def _payload(view: ManualBatchView) -> JSONMap:
    children: list[JSONValue] = []
    for child in view.children:
        children.append(_child_payload(child))
    return {
        "schema_version": SCHEMA_VERSION,
        "batch_id": view.batch_id,
        "status": view.status,
        "topic_source": view.topic_source,
        "as_of_date": view.as_of_date,
        "submitted_at": view.submitted_at,
        "updated_at": view.updated_at,
        "snapshot": view.snapshot.as_json() if view.snapshot is not None else None,
        "children": children,
        "request_nonce_sha256": view.request_nonce_sha256,
        "request_payload_sha256": view.request_payload_sha256,
    }


def _child_payload(child: ManualRunView) -> JSONMap:
    payload = child.as_json()
    for key in ("message", "error"):
        value = payload.get(key)
        if isinstance(value, str):
            payload[key] = " ".join(value.split())[:MESSAGE_LIMIT]
    return payload


def _read(path: Path) -> JSONMap:
    try:
        value: JSONValue = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as error:
        raise ContractError(f"could not read manual batch: {path.name}") from error
    return _json_map(value, "manual batch")


def _validate(value: JSONMap) -> None:
    try:
        validate_instance(value, SCHEMA_PATH)
    except SchemaError as error:
        raise ContractError(f"manual batch schema invalid: {error}") from error


def _decode(value: JSONMap, expected_batch_id: str) -> ManualBatchView:
    _validate(value)
    _validate_batch(value, expected_batch_id)
    snapshot_value = value["snapshot"]
    snapshot = _snapshot(snapshot_value) if isinstance(snapshot_value, dict) else None
    children_value = value["children"]
    if not isinstance(children_value, list):
        raise ContractError("manual batch children must be a list")
    children = tuple(_child(_json_map(item, "manual batch child")) for item in children_value)
    return ManualBatchView(
        batch_id=_string(value, "batch_id"),
        status=_batch_status(_string(value, "status")),
        topic_source=_topic_source(_string(value, "topic_source")),
        as_of_date=_string(value, "as_of_date"),
        submitted_at=_string(value, "submitted_at"),
        updated_at=_string(value, "updated_at"),
        snapshot=snapshot,
        children=children,
        request_nonce_sha256=_optional_string(value, "request_nonce_sha256"),
        request_payload_sha256=_optional_string(value, "request_payload_sha256"),
    )


def _validate_batch(value: JSONMap, expected_batch_id: str) -> None:
    batch_id = _string(value, "batch_id")
    if batch_id != _batch_id(expected_batch_id):
        raise ContractError("manual batch filename does not match batch_id")
    topic_source = _topic_source(_string(value, "topic_source"))
    children_value = value.get("children")
    if not isinstance(children_value, list):
        raise ContractError("manual batch children must be a list")
    allowed_counts = {1, 3} if topic_source == "auto_selected" else {1}
    if len(children_value) not in allowed_counts:
        raise ContractError("manual batch has an invalid child count")
    snapshot = value.get("snapshot")
    if topic_source == "auto_selected" and not isinstance(snapshot, dict):
        raise ContractError("auto-selected manual batch requires a snapshot")
    if topic_source == "user_defined" and snapshot is not None:
        raise ContractError("user-defined manual batch cannot have a snapshot")
    for slot, child_value in enumerate(children_value, start=1):
        child = _json_map(child_value, "manual batch child")
        if (
            _integer(child, "slot") != slot
            or _string(child, "child_id") != f"CHILD-{slot:02d}"
            or _string(child, "task_id") != f"{batch_id}-{slot}"
        ):
            raise ContractError("manual batch child identity is invalid")


def _snapshot(value: JSONMap) -> ManualSnapshotView:
    sha256_value = value.get("sha256")
    if sha256_value is not None and not isinstance(sha256_value, str):
        raise ContractError("manual batch snapshot sha256 is invalid")
    return ManualSnapshotView(
        capture_id=_string(value, "capture_id"),
        path=_string(value, "path"),
        sha256=sha256_value,
    )


def _child(value: JSONMap) -> ManualRunView:
    preview_value = value["confirmation_preview"]
    action_value = value["next_action"]
    run_id = _string(value, "run_id")
    return ManualRunView(
        task_id=_string(value, "task_id"),
        status=_manual_status(_string(value, "status")),
        submitted_at=_string(value, "submitted_at"),
        updated_at=_string(value, "updated_at"),
        run_id=run_id,
        result_status=_optional_string(value, "result_status"),
        message=_optional_string(value, "message"),
        error=_optional_string(value, "error"),
        retryable=_boolean(value, "retryable"),
        confirmation_preview=(
            _preview(_json_map(preview_value, "confirmation preview"))
            if isinstance(preview_value, dict)
            else None
        ),
        child_id=_string(value, "child_id"),
        slot=_integer(value, "slot"),
        keyword=_optional_string(value, "keyword"),
        next_action=(
            _action(_json_map(action_value, "next action"))
            if isinstance(action_value, dict)
            else None
        ),
        requested_keyword=_optional_string(value, "requested_keyword"),
        resolved_keyword=_optional_string(value, "resolved_keyword"),
        selection_context=(
            _selection_context(_json_map(value["selection_context"], "selection context"))
            if isinstance(value["selection_context"], dict)
            else None
        ),
        active_action=(
            _active_action(_json_map(value["active_action"], "active action"))
            if isinstance(value["active_action"], dict)
            else None
        ),
        model_config=_model_config(value.get("model_config")),
        cancellation=(
            _cancellation(_json_map(value["cancellation"], "cancellation"))
            if isinstance(value.get("cancellation"), dict)
            else None
        ),
        cancel_action=(
            _cancel_action(_json_map(value["cancel_action"], "cancel action"))
            if isinstance(value.get("cancel_action"), dict)
            else None
        ),
        display_id=_optional_string(value, "display_id"),
        scheduled_at=_optional_string(value, "scheduled_at"),
        started_at=_optional_string(value, "started_at"),
        ended_at=_optional_string(value, "ended_at"),
    )


def _model_config(value: JSONValue | None) -> ModelConfigSnapshot | None:
    if value is None:
        return None
    return parse_model_config_snapshot(value)


def _preview(value: JSONMap) -> ConfirmationPreview:
    images_value = value.get("images")
    if not isinstance(images_value, list):
        raise ContractError("manual batch confirmation preview images are invalid")
    images = tuple(image for image in images_value if isinstance(image, str))
    if len(images) != len(images_value):
        raise ContractError("manual batch confirmation preview images are invalid")
    return ConfirmationPreview(
        action=_string(value, "action"),
        target_blog_id=_string(value, "target_blog_id"),
        title=_string(value, "title"),
        images=images,
        artifact_digest=_string(value, "artifact_digest"),
    )


def _action(value: JSONMap) -> ManualActionView:
    return ManualActionView(_action_kind(_string(value, "kind")), _string(value, "nonce"))


def _active_action(value: JSONMap) -> ManualActiveActionView:
    return ManualActiveActionView(
        _active_action_kind(_string(value, "kind")),
        _string(value, "operation_id"),
        _string(value, "nonce_sha256"),
        _string(value, "accepted_at"),
        _active_action_state(_string(value, "state")),
    )


def _cancellation(value: JSONMap) -> ManualCancellationView:
    scope = _cancellation_scope(value.get("scope"))
    completed = value.get("completed_at")
    if not isinstance(completed, str) and completed is not None:
        raise ContractError("manual batch cancellation completed_at is invalid")
    return ManualCancellationView(
        scope,
        _string(value, "requested_at"),
        _string(value, "nonce_sha256"),
        completed,
    )


def _cancel_action(value: JSONMap) -> ManualCancelActionView:
    if value.get("kind") != "cancel":
        raise ContractError("manual batch cancel action kind is invalid")
    scope = _cancellation_scope(value.get("scope"))
    return ManualCancelActionView(_string(value, "nonce"), scope)


def _cancellation_scope(value: JSONValue) -> CancellationScope:
    match value:
        case "queued_only" | "remaining":
            return value
        case _:
            raise ContractError("manual batch cancellation scope is invalid")


def _selection_context(value: JSONMap) -> TopicSelectionContext:
    excluded_value = value.get("excluded_keywords", [])
    if not isinstance(excluded_value, list):
        raise ContractError("manual batch selection context exclusions are invalid")
    excluded_keywords = tuple(item for item in excluded_value if isinstance(item, str))
    if len(excluded_keywords) != len(excluded_value):
        raise ContractError("manual batch selection context exclusions are invalid")
    batch_slot_value = value.get("batch_slot")
    if batch_slot_value is not None and (isinstance(batch_slot_value, bool) or not isinstance(batch_slot_value, int)):
        raise ContractError("manual batch selection context batch_slot is invalid")
    return TopicSelectionContext(
        _optional_string(value, "category") or "",
        _optional_string(value, "audience") or "",
        _optional_string(value, "publish_purpose") or "",
        _string(value, "as_of_date"),
        _string(value, "timezone"),
        _optional_string(value, "batch_id"),
        batch_slot_value,
        _snapshot_policy(_optional_string(value, "snapshot_policy")),
        _optional_string(value, "capture_id"),
        _optional_string(value, "snapshot_path"),
        _optional_string(value, "snapshot_sha256"),
        excluded_keywords,
    )


def _json_map(value: JSONValue, label: str) -> JSONMap:
    if not isinstance(value, dict):
        raise ContractError(f"{label} must be an object")
    return value


def _string(value: JSONMap, key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str):
        raise ContractError(f"manual batch {key} is invalid")
    return item


def _optional_string(value: JSONMap, key: str) -> str | None:
    item = value.get(key)
    if item is not None and not isinstance(item, str):
        raise ContractError(f"manual batch {key} is invalid")
    return item


def _integer(value: JSONMap, key: str) -> int:
    item = value.get(key)
    if isinstance(item, bool) or not isinstance(item, int):
        raise ContractError(f"manual batch {key} is invalid")
    return item


def _boolean(value: JSONMap, key: str) -> bool:
    item = value.get(key)
    if not isinstance(item, bool):
        raise ContractError(f"manual batch {key} is invalid")
    return item


def _manual_status(value: str) -> ManualStatus:
    match value:
        case "queued" | "running" | "cancelling" | "completed" | "failed" | "cancelled":
            return value
        case _:
            raise ContractError("manual batch child status is invalid")


def _batch_status(value: str) -> ManualBatchStatus:
    match value:
        case "queued" | "running" | "cancelling" | "blocked" | "completed" | "failed" | "cancelled":
            return value
        case _:
            raise ContractError("manual batch status is invalid")


def _topic_source(value: str) -> ManualTopicSource:
    match value:
        case "user_defined" | "auto_selected":
            return value
        case _:
            raise ContractError("manual batch topic_source is invalid")


def _action_kind(value: str) -> ManualActionKind:
    match value:
        case "retry" | "external" | "confirm":
            return value
        case _:
            raise ContractError("manual batch action kind is invalid")


def _active_action_kind(value: str) -> Literal["initial", "retry", "external", "confirm"]:
    match value:
        case "initial" | "retry" | "external" | "confirm":
            return value
        case _:
            raise ContractError("manual batch active action kind is invalid")


def _active_action_state(value: str) -> Literal["queued", "running"]:
    match value:
        case "queued" | "running":
            return value
        case _:
            raise ContractError("manual batch active action state is invalid")


def _snapshot_policy(value: str | None) -> Literal["capture_once", "reuse_only"] | None:
    match value:
        case "capture_once" | "reuse_only" | None:
            return value
        case _:
            raise ContractError("manual batch snapshot_policy is invalid")


__all__ = ["ManualBatchStore"]
