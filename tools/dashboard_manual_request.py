from __future__ import annotations

from datetime import date
from typing import Final

from tools.contract_types import ContractError, JSONMap
from tools.dashboard_manual_models import ConfirmationPreview, ManualRunInput
from tools.runner_state import read_state
from tools.runner_types import (
    RunnerRequest,
    RunnerResult,
    RunStatus,
    TopicSelectionContext,
)

MANUAL_PAYLOAD_KEYS: Final[frozenset[str]] = frozenset(
    {"keyword", "auto_topic", "as_of_date", "selection_context"}
)


def _selection_context(payload: JSONMap) -> TopicSelectionContext:
    value = payload.get("selection_context")
    if not isinstance(value, dict):
        raise ContractError("auto topic selection context is required")
    expected = {"category", "audience", "publish_purpose", "as_of_date", "timezone"}
    if set(value) != expected:
        raise ContractError("auto topic selection context is incomplete")
    fields: dict[str, str] = {}
    for key in expected:
        item = value[key]
        if not isinstance(item, str) or not item.strip():
            raise ContractError("auto topic selection context has invalid values")
        fields[key] = item.strip()
    if fields["timezone"] != "Asia/Seoul":
        raise ContractError("auto topic selection context must use Asia/Seoul")
    try:
        _ = date.fromisoformat(fields["as_of_date"])
    except ValueError as error:
        raise ContractError("auto topic selection as_of_date is invalid") from error
    return TopicSelectionContext(
        fields["category"],
        fields["audience"],
        fields["publish_purpose"],
        fields["as_of_date"],
        fields["timezone"],
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


def demo_runner(request: RunnerRequest) -> RunnerResult:
    return RunnerResult(
        "RUN-demo-manual",
        RunStatus.LOCAL_ONLY,
        request.root / ".automation" / "state" / "RUN-demo-manual.json",
        request.root / ".automation" / "logs" / "RUN-demo-manual.jsonl",
        (),
        "프로젝트 파일과 외부 저장소를 변경하지 않은 데모 실행입니다",
    )


def parse_manual_run_payload(payload: JSONMap) -> ManualRunInput:
    unknown_keys = set(payload) - MANUAL_PAYLOAD_KEYS
    if unknown_keys:
        raise ContractError("manual daily-generate payload has unknown keys")
    if set(payload) == {"keyword", "as_of_date"}:
        keyword = payload["keyword"]
        as_of_date = payload["as_of_date"]
        if isinstance(keyword, str) and keyword.strip() and isinstance(as_of_date, str):
            return ManualRunInput(
                keyword.strip(), False, _date_selection_context(as_of_date)
            )
    if set(payload) == {"auto_topic", "as_of_date"} and payload["auto_topic"] is True:
        as_of_date = payload["as_of_date"]
        if isinstance(as_of_date, str):
            return ManualRunInput(None, True, _date_selection_context(as_of_date))
    if set(payload) == {"auto_topic", "selection_context"} and payload["auto_topic"] is True:
        return ManualRunInput(None, True, _selection_context(payload))
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


__all__ = ["confirmation_preview", "demo_runner", "parse_manual_run_payload"]
