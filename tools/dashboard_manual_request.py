from __future__ import annotations

import uuid
from datetime import date
from typing import Final

from tools.contract_types import ContractError, JSONMap
from tools.dashboard_manual_models import ConfirmationPreview, ManualRunInput
from tools.runner_state import read_state
from tools.runner_types import (
    RunnerResult,
    RunStatus,
    TopicSelectionContext,
)

MANUAL_PAYLOAD_KEYS: Final[frozenset[str]] = frozenset(
    {"keyword", "auto_topic", "as_of_date", "preset_id", "request_nonce"}
)


def _date_selection_context(as_of_date: str) -> TopicSelectionContext:
    normalized = as_of_date.strip()
    if not normalized:
        raise ContractError("as_of_date is required")
    try:
        _ = date.fromisoformat(normalized)
    except ValueError as error:
        raise ContractError("as_of_date is invalid") from error
    return TopicSelectionContext("", "", "", normalized, "Asia/Seoul")
IMAGE_SUFFIXES: Final[tuple[str, ...]] = (".gif", ".jpeg", ".jpg", ".png", ".webp")


def parse_manual_run_payload(payload: JSONMap) -> ManualRunInput:
    unknown_keys = set(payload) - MANUAL_PAYLOAD_KEYS
    if unknown_keys:
        raise ContractError("manual daily-generate payload has unknown keys")
    preset_id = payload.get("preset_id")
    if preset_id is not None and (not isinstance(preset_id, str) or not preset_id):
        raise ContractError("manual model preset is invalid")
    request_nonce = payload.get("request_nonce")
    if request_nonce is not None:
        if not isinstance(request_nonce, str):
            raise ContractError("manual request_nonce must be a UUID")
        try:
            parsed_nonce = uuid.UUID(request_nonce)
        except ValueError as error:
            raise ContractError("manual request_nonce must be a UUID") from error
        if str(parsed_nonce) != request_nonce.lower():
            raise ContractError("manual request_nonce must use canonical UUID format")
        request_nonce = request_nonce.lower()
    optional: set[str] = {"request_nonce"} if request_nonce is not None else set()
    if preset_id is not None:
        optional.add("preset_id")
    fields = set(payload) - optional
    if fields == {"keyword", "as_of_date"}:
        keyword = payload["keyword"]
        as_of_date = payload["as_of_date"]
        if isinstance(keyword, str) and keyword.strip() and isinstance(as_of_date, str):
            return ManualRunInput(
                keyword.strip(), False, _date_selection_context(as_of_date), preset_id,
                request_nonce=request_nonce,
            )
    if fields == {"auto_topic", "as_of_date"} and payload["auto_topic"] is True:
        as_of_date = payload["as_of_date"]
        if isinstance(as_of_date, str):
            return ManualRunInput(
                None, True, _date_selection_context(as_of_date), preset_id,
                request_nonce=request_nonce,
            )
    raise ContractError("manual daily-generate requires exactly one topic source")


def confirmation_preview(result: RunnerResult) -> ConfirmationPreview | None:
    if result.status is not RunStatus.AWAITING_USER_CONFIRMATION:
        return None
    state = read_state(result.state_path)
    target_blog_id = state.get("target_blog_id")
    title = state.get("naver_title")
    artifact_digest = state.get("artifact_digest")
    artifact_paths = state.get("artifact_paths")
    if (
        not isinstance(target_blog_id, str)
        or not isinstance(title, str)
        or not isinstance(artifact_digest, str)
        or not isinstance(artifact_paths, list)
    ):
        raise ContractError("Naver confirmation preview is incomplete")
    images = tuple(
        value
        for value in artifact_paths
        if isinstance(value, str) and value.lower().endswith(IMAGE_SUFFIXES)
    )
    if not images:
        raise ContractError("Naver confirmation preview has no images")
    return ConfirmationPreview(
        "naver-draft-save", target_blog_id, title, images, artifact_digest
    )


__all__ = ["confirmation_preview", "parse_manual_run_payload"]
