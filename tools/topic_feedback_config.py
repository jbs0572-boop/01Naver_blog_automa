from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Final

from tools.contract_types import ContractError, JSONMap, JSONValue
from tools.topic_feedback_approval import approved_evaluation
from tools.topic_feedback_policy import load_registry
from tools.topic_feedback_rollback_store import load_rollback_fence, store_rollback
from tools.topic_feedback_source_rules import AccessMode

BASELINE_VERSION: Final = "topic-baseline-v1"
FEEDBACK_VERSION: Final = "topic-feedback-v1"
SOURCE_CONFIG_PATH: Final = Path(__file__).resolve().parents[1] / "config/topic-feedback-sources.json"  # fmt: skip
_CONFIG_FIELDS: Final = frozenset(["schema_version", "feedback_enabled", "active_score_version", "shadow_score_version", "minimum_mature_samples", "rollout_tier", "previous_stable_version", "source_modes"])  # fmt: skip
_SOURCE_FIELDS: Final = frozenset(["enabled", "access_mode", "automatic_candidate_provider"])  # fmt: skip


@dataclass(frozen=True, slots=True)
class SourceMode:
    source_id: str
    enabled: bool
    access_mode: AccessMode
    automatic_candidate_provider: bool


@dataclass(frozen=True, slots=True)
class RolloutPin:
    feedback_enabled: bool
    active_score_version: str
    shadow_score_version: str
    minimum_mature_samples: int
    rollout_tier: str
    previous_stable_version: str
    source_modes: tuple[SourceMode, ...]
    config_digest: str
    evaluation_digest: str | None
    selection_mutation: bool
    rollback_digest: str | None
    new_feedback_artifacts_enabled: bool


@dataclass(frozen=True, slots=True)
class RollbackResult:
    path: Path
    digest: str
    pin: RolloutPin


def _canonical(value: JSONMap) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ContractError("rollout config contains non-JSON data") from error


def _digest(encoded: bytes) -> str:
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _read_map(path: Path, label: str) -> tuple[JSONMap, bytes]:
    if path.is_symlink():
        raise ContractError(f"{label} must not be a symlink")
    try:
        encoded = path.read_bytes()
        value: JSONValue = json.loads(encoded)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ContractError(f"{label} is unreadable") from error
    if not isinstance(value, dict):
        raise ContractError(f"{label} must be a JSON object")
    _ = _canonical(value)
    return value, encoded


def _bool(raw: JSONMap, field: str) -> bool:
    value = raw.get(field)
    if not isinstance(value, bool):
        raise ContractError("rollout config field type is invalid")
    return value


def _text(raw: JSONMap, field: str) -> str:
    value = raw.get(field)
    if not isinstance(value, str) or not value:
        raise ContractError("rollout config field type is invalid")
    return value


def _source_modes(raw: JSONMap) -> tuple[SourceMode, ...]:
    value = raw.get("source_modes")
    if not isinstance(value, dict):
        raise ContractError("rollout source modes are invalid")
    registry = load_registry(SOURCE_CONFIG_PATH)
    trusted = {source.source_id: source for source in registry.sources}
    if frozenset(value) != frozenset(trusted):
        raise ContractError("rollout source modes are invalid")
    modes: list[SourceMode] = []
    for source_id in sorted(value):
        item = value[source_id]
        source = trusted[source_id]
        if not isinstance(item, dict) or frozenset(item) != _SOURCE_FIELDS:
            raise ContractError("rollout source modes are invalid")
        enabled = item.get("enabled")
        provider = item.get("automatic_candidate_provider")
        access = item.get("access_mode")
        if (
            not isinstance(enabled, bool)
            or not isinstance(provider, bool)
            or not isinstance(access, str)
        ):
            raise ContractError("rollout source modes are invalid")
        try:
            mode = AccessMode(access)
        except ValueError as error:
            raise ContractError("rollout source modes are invalid") from error
        if enabled is not source.enabled or mode is not source.access_mode or provider:
            raise ContractError(
                "rollout source mode conflicts with trusted source policy"
            )
        modes.append(SourceMode(source_id, enabled, mode, provider))
    return tuple(modes)


def _config_path(root: Path, path: Path) -> Path:
    if root.is_symlink():
        raise ContractError("rollback root must not be a symlink")
    if path.is_symlink():
        raise ContractError("rollout config path is unsafe")
    try:
        resolved_root = root.resolve(strict=True)
        resolved_path = path.resolve(strict=True)
    except OSError as error:
        raise ContractError("rollout config is unreadable") from error
    if not resolved_path.is_relative_to(resolved_root):
        raise ContractError("rollout config must be inside project root")
    return resolved_path


def _load_raw_rollout_config(
    root: Path, path: Path, *, require_approval: bool
) -> RolloutPin:
    path = _config_path(root, path)
    raw, _ = _read_map(path, "rollout config")
    if (
        frozenset(raw) != _CONFIG_FIELDS
        or raw.get("schema_version") != "topic-feedback-rollout-v1"
    ):
        raise ContractError("rollout config fields are invalid")
    feedback_enabled = _bool(raw, "feedback_enabled")
    active = _text(raw, "active_score_version")
    shadow = _text(raw, "shadow_score_version")
    previous = _text(raw, "previous_stable_version")
    if active not in {BASELINE_VERSION, FEEDBACK_VERSION} or shadow not in {
        BASELINE_VERSION,
        FEEDBACK_VERSION,
    }:
        raise ContractError("score version is invalid")
    raw_minimum = raw.get("minimum_mature_samples")
    if (
        not isinstance(raw_minimum, int)
        or isinstance(raw_minimum, bool)
        or raw_minimum != 30
    ):
        raise ContractError("minimum mature samples must be 30")
    minimum = raw_minimum
    tier = _text(raw, "rollout_tier")
    if tier not in {"off", "shadow", "canary", "full"}:
        raise ContractError("rollout tier is invalid")
    if previous != BASELINE_VERSION or shadow != FEEDBACK_VERSION:
        raise ContractError("rollout score version progression is invalid")
    if tier == "off" and (feedback_enabled or active != BASELINE_VERSION):
        raise ContractError("off rollout must use the baseline")
    if tier == "shadow" and (not feedback_enabled or active != BASELINE_VERSION):
        raise ContractError("shadow rollout must preserve baseline selection")
    if tier in {"canary", "full"} and (
        not feedback_enabled or active != FEEDBACK_VERSION
    ):
        raise ContractError("active rollout must use the feedback score")
    decision = (
        approved_evaluation(active, minimum)
        if require_approval and active == FEEDBACK_VERSION
        else None
    )
    if require_approval and active == FEEDBACK_VERSION and decision is None:
        raise ContractError("active score version requires approved evaluation")
    approval = decision.evaluation_digest if decision is not None else None
    modes = _source_modes(raw)
    return RolloutPin(
        feedback_enabled,
        active,
        shadow,
        minimum,
        tier,
        previous,
        modes,
        _digest(_canonical(raw)),
        approval,
        active != BASELINE_VERSION,
        None,
        feedback_enabled,
    )


def load_rollout_config(root: Path, path: Path) -> RolloutPin:
    fence = load_rollback_fence(root)
    pin = _load_raw_rollout_config(root, path, require_approval=fence is None)
    if fence is None:
        return pin
    disabled = tuple(
        SourceMode(item.source_id, False, item.access_mode, False)
        for item in pin.source_modes
    )
    return replace(
        pin,
        feedback_enabled=False,
        active_score_version=BASELINE_VERSION,
        rollout_tier="off",
        source_modes=disabled,
        evaluation_digest=None,
        selection_mutation=False,
        rollback_digest=fence.registry_digest,
        new_feedback_artifacts_enabled=False,
    )


def create_rollback(
    root: Path, config_path: Path, rollback_id: str, captured_at: str
) -> RollbackResult:
    existing_fence = load_rollback_fence(root)
    config = _load_raw_rollout_config(
        root, config_path, require_approval=existing_fence is None
    )
    try:
        parsed = datetime.fromisoformat(captured_at)
    except ValueError as error:
        raise ContractError("rollback captured_at is invalid") from error
    if parsed.utcoffset() is None or not captured_at.endswith("+09:00"):
        raise ContractError("rollback captured_at is invalid")
    payload: JSONMap = {
        "schema_version": "topic-feedback-rollback-v1",
        "rollback_id": rollback_id,
        "captured_at": captured_at,
        "source_config_digest": config.config_digest,
        "feedback_enabled": False,
        "active_score_version": BASELINE_VERSION,
        "shadow_score_version": FEEDBACK_VERSION,
        "selection_mutation": False,
        "new_feedback_artifacts_enabled": False,
    }
    payload["digest"] = _digest(_canonical(payload))
    rollback_digest = str(payload["digest"])
    encoded = _canonical(payload) + b"\n"
    path = store_rollback(root, rollback_id, encoded)
    fence = load_rollback_fence(root)
    if fence is None or rollback_digest not in fence.record_digests:
        raise ContractError("rollback registry record is invalid")
    pin = load_rollout_config(root, config_path)
    return RollbackResult(path, rollback_digest, pin)
