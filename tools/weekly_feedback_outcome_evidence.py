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
from tools.topic_feedback_models import compute_digest
from tools.topic_feedback_scoring_models import CandidateOutcome
from tools.topic_feedback_store_fs import open_directory, open_root

KST: Final = timedelta(hours=9)
_REF_FIELDS: Final = frozenset(
    {"path", "size_bytes", "sha256", "schema_version", "digest"}
)
_SELECTION_FIELDS: Final = frozenset(
    {
        "schema_version", "outcome_id", "run_id", "keyword", "blog_post_id",
        "selected", "score_version", "prediction_recorded_at", "training_cutoff",
        "published_at", "digest",
    }
)
_OBSERVATION_FIELDS: Final = frozenset(
    {
        "schema_version", "outcome_id", "run_id", "blog_post_id", "horizon_days",
        "observed_at", "status", "baseline_search_inflow",
        "challenger_search_inflow", "digest",
    }
)


@dataclass(frozen=True, slots=True)
class FactualOutcome:
    outcome: CandidateOutcome
    paths: tuple[Path, Path, Path]


def _read(root: Path, relative: Path) -> bytes:
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise ContractError("feedback input digest mismatch")
    descriptors: list[int] = []
    try:
        descriptor = open_root(root)
        descriptors.append(descriptor)
        for part in relative.parts[:-1]:
            descriptor = open_directory(descriptor, part, create=False)
            descriptors.append(descriptor)
        leaf = os.open(
            relative.name,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=descriptor,
        )
        info = os.fstat(leaf)
        if not stat.S_ISREG(info.st_mode):
            os.close(leaf)
            raise ContractError("feedback input digest mismatch")
        with os.fdopen(leaf, "rb") as handle:
            return handle.read()
    except (ContractError, OSError) as error:
        raise ContractError("feedback input digest mismatch") from error
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _map(encoded: bytes) -> JSONMap:
    try:
        value: JSONValue = json.loads(encoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ContractError("feedback input digest mismatch") from error
    if not isinstance(value, dict):
        raise ContractError("feedback input digest mismatch")
    return value


def _sha(encoded: bytes) -> str:
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _text(payload: JSONMap, field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value:
        raise ContractError("feedback input digest mismatch")
    return value


def _time(payload: JSONMap, field: str) -> datetime:
    value = _text(payload, field)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ContractError("feedback input digest mismatch") from error
    if not value.endswith("+09:00") or parsed.utcoffset() != KST:
        raise ContractError("feedback input digest mismatch")
    return parsed


def _number(payload: JSONMap, field: str) -> float:
    value = payload.get(field)
    if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
        raise ContractError("feedback input digest mismatch")
    return float(value)


def _payload(root: Path, reference: JSONMap, path: Path, schema: str) -> JSONMap:
    if frozenset(reference) != _REF_FIELDS or reference.get("path") != path.as_posix():
        raise ContractError("feedback input digest mismatch")
    encoded = _read(root, path)
    payload = _map(encoded)
    if (
        reference.get("size_bytes") != len(encoded)
        or reference.get("sha256") != _sha(encoded)
        or reference.get("schema_version") != schema
        or payload.get("schema_version") != schema
        or reference.get("digest") != payload.get("digest")
        or payload.get("digest") != compute_digest(payload)
        or json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        != encoded.rstrip(b"\n")
    ):
        raise ContractError("feedback input digest mismatch")
    return payload


def load_factual_outcome(root: Path, reference: JSONMap) -> FactualOutcome:
    if frozenset(reference) != frozenset({
        "outcome_id", "selection_ref", "seven_day_ref", "twenty_eight_day_ref"
    }):
        raise ContractError("feedback input digest mismatch")
    outcome_id = _text(reference, "outcome_id")
    if Path(outcome_id).name != outcome_id or outcome_id in {".", ".."}:
        raise ContractError("feedback input digest mismatch")
    base = Path("metadata/topic-feedback-outcomes") / outcome_id
    selection_ref = reference.get("selection_ref")
    seven_ref = reference.get("seven_day_ref")
    twenty_eight_ref = reference.get("twenty_eight_day_ref")
    if not (
        isinstance(selection_ref, dict)
        and isinstance(seven_ref, dict)
        and isinstance(twenty_eight_ref, dict)
    ):
        raise ContractError("feedback input digest mismatch")
    selection = _payload(root, selection_ref, base / "selection.json", "topic-feedback-selection-evidence-v1")
    seven = _payload(root, seven_ref, base / "7d.json", "topic-feedback-outcome-observation-v1")
    twenty_eight = _payload(root, twenty_eight_ref, base / "28d.json", "topic-feedback-outcome-observation-v1")
    if frozenset(selection) != _SELECTION_FIELDS:
        raise ContractError("feedback input digest mismatch")
    for observation, days in ((seven, 7), (twenty_eight, 28)):
        if frozenset(observation) != _OBSERVATION_FIELDS or observation.get("horizon_days") != days:
            raise ContractError("feedback input digest mismatch")
        if any(
            observation.get(field) != selection.get(field)
            for field in ("outcome_id", "run_id", "blog_post_id")
        ):
            raise ContractError("feedback input digest mismatch")
    if selection.get("outcome_id") != outcome_id or selection.get("score_version") != "topic-feedback-v1":
        raise ContractError("feedback input digest mismatch")
    selected = selection.get("selected")
    if not isinstance(selected, bool):
        raise ContractError("feedback input digest mismatch")
    published = _time(selection, "published_at")
    prediction = _time(selection, "prediction_recorded_at")
    cutoff = _time(selection, "training_cutoff")
    seven_at = _time(seven, "observed_at")
    twenty_eight_at = _time(twenty_eight, "observed_at")
    if not (
        cutoff <= prediction < published
        and published + timedelta(days=7) <= seven_at <= published + timedelta(days=7, hours=48)
        and published + timedelta(days=28) <= twenty_eight_at <= published + timedelta(days=28, hours=48)
        and seven_at <= twenty_eight_at
    ):
        raise ContractError("feedback input digest mismatch")
    statuses = (seven.get("status"), twenty_eight.get("status"))
    if any(item not in {"mature", "pending", "missing", "delayed", "invalid"} for item in statuses):
        raise ContractError("feedback input digest mismatch")
    status = "mature" if statuses == ("mature", "mature") else "pending"
    outcome = CandidateOutcome(
        outcome_id, selected, status,
        _number(seven, "baseline_search_inflow"), _number(seven, "challenger_search_inflow"),
        _number(twenty_eight, "baseline_search_inflow"), _number(twenty_eight, "challenger_search_inflow"),
        published.isoformat(), prediction.isoformat(), cutoff.isoformat(),
        _text(selection, "digest"), "topic-feedback-v1",
        seven_at.isoformat(), _text(seven, "digest"),
        twenty_eight_at.isoformat(), _text(twenty_eight, "digest"),
    )
    return FactualOutcome(outcome, (root / base / "selection.json", root / base / "7d.json", root / base / "28d.json"))


def validate_outcome_evidence_file(root: Path, path: Path, schema: str) -> None:
    relative = path.relative_to(root)
    encoded = _read(root, relative)
    payload = _map(encoded)
    if schema == "topic-feedback-selection-evidence-v1":
        fields = _SELECTION_FIELDS
        for field in ("outcome_id", "run_id", "keyword", "blog_post_id", "score_version"):
            _ = _text(payload, field)
        for field in ("prediction_recorded_at", "training_cutoff", "published_at"):
            _ = _time(payload, field)
        if not isinstance(payload.get("selected"), bool):
            raise ContractError("feedback input digest mismatch")
    elif schema == "topic-feedback-outcome-observation-v1":
        fields = _OBSERVATION_FIELDS
        for field in ("outcome_id", "run_id", "blog_post_id"):
            _ = _text(payload, field)
        _ = _time(payload, "observed_at")
        _ = _number(payload, "baseline_search_inflow")
        _ = _number(payload, "challenger_search_inflow")
        if payload.get("horizon_days") not in {7, 28} or payload.get("status") not in {
            "mature", "pending", "missing", "delayed", "invalid"
        }:
            raise ContractError("feedback input digest mismatch")
    else:
        raise ContractError("feedback input digest mismatch")
    if (
        frozenset(payload) != fields
        or payload.get("schema_version") != schema
        or payload.get("digest") != compute_digest(payload)
        or json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        != encoded.rstrip(b"\n")
    ):
        raise ContractError("feedback input digest mismatch")


__all__ = ["FactualOutcome", "load_factual_outcome", "validate_outcome_evidence_file"]
