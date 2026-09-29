from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Final

from tools.contract_types import ContractError, JSONMap, JSONValue
from tools.topic_feedback_evaluation import evaluate_outcomes, parse_candidate_outcomes

PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]
APPROVAL_REGISTRY_PATH: Final = (
    PROJECT_ROOT / "config/topic-feedback-approved-evaluations.json"
)
_REGISTRY_FIELDS: Final = frozenset({"schema_version", "approved_evaluations"})
_ENTRY_FIELDS: Final = frozenset(
    {"challenger_score_version", "evaluation_path", "evaluation_sha256", "approved_at"}
)
_EVALUATION_FIELDS: Final = frozenset(
    {
        "schema_version",
        "baseline_score_version",
        "challenger_score_version",
        "evaluation_as_of",
        "evaluation_parameters",
        "outcomes",
        "result",
        "ndcg_delta",
        "digest",
    }
)
_PARAMETERS: Final[JSONMap] = {
    "minimum_mature_samples": 30,
    "metric": "search_inflow",
    "horizons_days": [7, 28],
    "aggregation": "median",
    "comparison": "strict_improvement",
    "validation": "publication_time_expanding_origin_v2",
}


@dataclass(frozen=True, slots=True)
class ApprovalDecision:
    evaluation_digest: str
    mature_selected_outcomes: int
    search_inflow_7d_improvement: float
    search_inflow_28d_improvement: float


def _canonical(payload: JSONMap) -> bytes:
    try:
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    except (TypeError, ValueError) as error:
        raise ContractError(
            "active score version requires approved evaluation"
        ) from error


def _read_map(path: Path, label: str) -> tuple[JSONMap, bytes]:
    if path.is_symlink():
        raise ContractError(f"{label} must not be a symlink")
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        descriptor = os.open(path, flags)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            os.close(descriptor)
            raise ContractError(f"{label} is unreadable")
        with os.fdopen(descriptor, "rb") as handle:
            encoded = handle.read()
        value: JSONValue = json.loads(encoded)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ContractError(f"{label} is unreadable") from error
    if not isinstance(value, dict):
        raise ContractError(f"{label} must be a JSON object")
    return value, encoded


def _sha(encoded: bytes) -> str:
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def evaluate_promotion(
    payload: JSONMap, encoded: bytes, expected_digest: str, minimum: int
) -> ApprovalDecision:
    unsigned = dict(payload)
    digest = unsigned.pop("digest", None)
    if (
        frozenset(payload) != _EVALUATION_FIELDS
        or payload.get("schema_version") != "topic-feedback-evaluation-v2"
        or payload.get("baseline_score_version") != "topic-baseline-v1"
        or payload.get("challenger_score_version") != "topic-feedback-v1"
        or payload.get("evaluation_parameters") != _PARAMETERS
        or minimum != 30
        or digest != expected_digest
        or digest != _sha(_canonical(unsigned))
        or _canonical(payload) != encoded.rstrip(b"\n")
    ):
        raise ContractError("active score version requires approved evaluation")
    evaluation_as_of = payload.get("evaluation_as_of")
    if not isinstance(evaluation_as_of, str) or not evaluation_as_of.endswith("+09:00"):
        raise ContractError("active score version requires approved evaluation")
    try:
        parsed_as_of = datetime.fromisoformat(evaluation_as_of)
    except ValueError as error:
        raise ContractError(
            "active score version requires approved evaluation"
        ) from error
    if parsed_as_of.utcoffset() != timedelta(hours=9):
        raise ContractError("active score version requires approved evaluation")
    result = evaluate_outcomes(
        parse_candidate_outcomes(payload.get("outcomes")),
        evaluation_as_of=parsed_as_of,
        minimum_mature_samples=minimum,
    )
    if payload.get("result") != result.as_json() or not result.promotion_eligible:
        raise ContractError("active score version requires approved evaluation")
    gain_7d = result.search_inflow_7d_improvement
    gain_28d = result.search_inflow_28d_improvement
    if gain_7d is None or gain_28d is None:
        raise ContractError("active score version requires approved evaluation")
    return ApprovalDecision(
        str(digest), result.mature_selected_outcomes, gain_7d, gain_28d
    )


def _safe_evidence_path(raw: JSONValue) -> Path:
    if not isinstance(raw, str):
        raise ContractError("approved evaluation registry is invalid")
    relative = Path(raw)
    root = APPROVAL_REGISTRY_PATH.parent.parent
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or relative.parts[:2]
        != (
            "metadata",
            "topic-feedback-evaluations",
        )
    ):
        raise ContractError("approved evaluation registry is invalid")
    current = root
    for part in relative.parts[:-1]:
        current /= part
        if current.is_symlink():
            raise ContractError("approved evaluation registry is invalid")
    return root / relative


def approved_evaluation(challenger: str, minimum: int) -> ApprovalDecision | None:
    registry, _ = _read_map(APPROVAL_REGISTRY_PATH, "approval registry")
    if (
        frozenset(registry) != _REGISTRY_FIELDS
        or registry.get("schema_version") != "topic-feedback-approved-evaluations-v1"
    ):
        raise ContractError("approved evaluation registry is invalid")
    entries = registry.get("approved_evaluations")
    if not isinstance(entries, list):
        raise ContractError("approved evaluation registry is invalid")
    matches: list[tuple[Path, str]] = []
    for entry in entries:
        if not isinstance(entry, dict) or frozenset(entry) != _ENTRY_FIELDS:
            raise ContractError("approved evaluation registry is invalid")
        approved_at, digest = entry.get("approved_at"), entry.get("evaluation_sha256")
        if not isinstance(approved_at, str) or not isinstance(digest, str):
            raise ContractError("approved evaluation registry is invalid")
        try:
            approval_time = datetime.fromisoformat(approved_at)
        except ValueError as error:
            raise ContractError("approved evaluation registry is invalid") from error
        if approval_time.utcoffset() != timedelta(hours=9):
            raise ContractError("approved evaluation registry is invalid")
        if entry.get("challenger_score_version") == challenger:
            matches.append((_safe_evidence_path(entry.get("evaluation_path")), digest))
    if len(matches) > 1:
        raise ContractError("approved evaluation registry is invalid")
    if not matches:
        return None
    evidence, encoded = _read_map(matches[0][0], "approved evaluation")
    return evaluate_promotion(evidence, encoded, matches[0][1], minimum)


__all__ = ["ApprovalDecision", "approved_evaluation", "evaluate_promotion"]
