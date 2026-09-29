from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Final, Literal

from tools.contract_types import ContractError, JSONMap
from tools.runner_types import RunnerRequest, TopicSelectionContext
from tools.topic_feedback_config import (
    BASELINE_VERSION,
    SOURCE_CONFIG_PATH,
    RolloutPin,
    SourceMode,
    load_rollout_config,
)
from tools.topic_feedback_manifest_reader import (
    feedback_evidence_by_digest,
    latest_feedback_evidence,
)
from tools.topic_feedback_policy import load_registry
from tools.topic_feedback_scoring import scoring_rule_payload
from tools.topic_feedback_scoring_models import AuxiliarySignal
from tools.topic_metadata import read_snapshot, snapshot_sha256

NO_FEEDBACK_MANIFEST_DIGEST: Final = (
    "sha256:" + hashlib.sha256(b"topic-feedback-manifest-absent-v1").hexdigest()
)
ROLLOUT_PATH: Final = Path("config/topic-feedback-rollout.json")


def _digest(payload: JSONMap) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _baseline_pin() -> RolloutPin:
    modes = tuple(
        SourceMode(item.source_id, False, item.access_mode, False)
        for item in load_registry(SOURCE_CONFIG_PATH).sources
    )
    config_payload: JSONMap = {
        "schema_version": "topic-feedback-missing-config-baseline-v1",
        "feedback_enabled": False,
        "active_score_version": BASELINE_VERSION,
        "source_modes": [
            {
                "source_id": item.source_id,
                "enabled": False,
                "access_mode": item.access_mode.value,
            }
            for item in modes
        ],
    }
    return RolloutPin(
        False,
        BASELINE_VERSION,
        "topic-feedback-v1",
        30,
        "off",
        BASELINE_VERSION,
        modes,
        _digest(config_payload),
        None,
        False,
        None,
        False,
    )


def _rollout(root: Path) -> RolloutPin:
    path = root / ROLLOUT_PATH
    return load_rollout_config(root, path) if path.exists() else _baseline_pin()


def _mode(pin: RolloutPin) -> Literal["baseline", "shadow", "active"]:
    if pin.selection_mutation:
        return "active"
    if pin.feedback_enabled:
        return "shadow"
    return "baseline"


def _enabled(pin: RolloutPin) -> tuple[str, ...]:
    return tuple(item.source_id for item in pin.source_modes if item.enabled)


def _manifest_digest(
    root: Path, context: TopicSelectionContext, pin: RolloutPin
) -> str:
    evidence = latest_feedback_evidence(
        root, f"{context.as_of_date}T23:59:59+09:00", _enabled(pin)
    )
    return evidence.digest if evidence is not None else NO_FEEDBACK_MANIFEST_DIGEST


def _config_digest(
    pin: RolloutPin,
    mode: Literal["baseline", "shadow", "active"],
    manifest_digest: str,
) -> str:
    payload: JSONMap = {
        "schema_version": "topic-score-pin-v2",
        "rollout_config_digest": pin.config_digest,
        "score_version": (
            pin.active_score_version if mode == "active" else BASELINE_VERSION
        ),
        "selection_mode": mode,
        "source_modes": [
            {
                "source_id": item.source_id,
                "enabled": item.enabled,
                "access_mode": item.access_mode.value,
                "automatic_candidate_provider": item.automatic_candidate_provider,
            }
            for item in pin.source_modes
        ],
        "scoring_rules": scoring_rule_payload(),
        "evaluation_digest": pin.evaluation_digest,
        "rollback_digest": pin.rollback_digest,
        "feedback_manifest_digest": manifest_digest,
    }
    return _digest(payload)


def _expected(
    root: Path, context: TopicSelectionContext
) -> tuple[str, str, str, Literal["baseline", "shadow", "active"]]:
    pin = _rollout(root)
    mode = _mode(pin)
    manifest = _manifest_digest(root, context, pin)
    version = pin.active_score_version if mode == "active" else BASELINE_VERSION
    return version, _config_digest(pin, mode, manifest), manifest, mode


def _pinned_context(
    root: Path, context: TopicSelectionContext
) -> TopicSelectionContext:
    expected = _expected(root, context)
    existing = (
        context.score_version,
        context.score_config_digest,
        context.feedback_manifest_digest,
        context.feedback_selection_mode,
    )
    if all(value is None for value in existing):
        return replace(
            context,
            score_version=expected[0],
            score_config_digest=expected[1],
            feedback_manifest_digest=expected[2],
            feedback_selection_mode=expected[3],
        )
    if existing != expected:
        raise ContractError("topic feedback pins changed during the run")
    return context


def signals_for_pinned_context(
    root: Path, context: TopicSelectionContext
) -> tuple[AuxiliarySignal, ...]:
    pin = _rollout(root)
    if _expected(root, context) != (
        context.score_version,
        context.score_config_digest,
        context.feedback_manifest_digest,
        context.feedback_selection_mode,
    ):
        raise ContractError("topic feedback pins changed during the run")
    if context.feedback_manifest_digest == NO_FEEDBACK_MANIFEST_DIGEST:
        return ()
    return feedback_evidence_by_digest(
        root,
        str(context.feedback_manifest_digest),
        f"{context.as_of_date}T23:59:59+09:00",
        _enabled(pin),
    ).signals


def pin_daily_request(request: RunnerRequest) -> RunnerRequest:
    context = request.selection_context
    if context is None:
        raise ContractError("daily-generate requires topic selection context")
    if request.resume and context.score_version is None:
        raise ContractError("persisted topic feedback pins are missing")
    pinned = _pinned_context(request.root, context)
    _validate_snapshot(request.root, pinned)
    return replace(request, selection_context=pinned)


def _validate_snapshot(root: Path, context: TopicSelectionContext) -> None:
    if context.snapshot_sha256 is None:
        return
    if context.snapshot_path is None or context.capture_id is None:
        raise ContractError("pinned Creator Advisor snapshot identity is incomplete")
    expected = (
        f"metadata/creator-advisor/{context.as_of_date}/{context.capture_id}.json"
    )
    if context.snapshot_path != expected:
        raise ContractError("pinned Creator Advisor snapshot identity changed")
    path = root / expected
    _ = read_snapshot(
        path,
        expected_capture_id=context.capture_id,
        expected_as_of_date=context.as_of_date,
    )
    actual = "sha256:" + snapshot_sha256(path)
    expected_digest = (
        context.snapshot_sha256
        if context.snapshot_sha256.startswith("sha256:")
        else "sha256:" + context.snapshot_sha256
    )
    if actual != expected_digest:
        raise ContractError("pinned Creator Advisor snapshot changed")


__all__ = [
    "NO_FEEDBACK_MANIFEST_DIGEST",
    "pin_daily_request",
    "signals_for_pinned_context",
]
