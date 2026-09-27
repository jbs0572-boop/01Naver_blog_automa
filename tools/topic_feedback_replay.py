from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Final

from tools.contract_types import ContractError, JSONMap, JSONValue
from tools.feedback_manifest import canonical_json, safe_read
from tools.topic_feedback_manifest_reader import load_feedback_path
from tools.topic_feedback_models import compute_digest, parse_artifact
from tools.topic_feedback_policy import load_registry
from tools.topic_feedback_replay_fs import (
    ReplayDirectory,
    close_replay_directory,
    open_replay_directory,
    replay_scratch,
    verify_replay_directory,
)
from tools.topic_feedback_replay_store import (
    Publication,
    publish_evidence,
    rollback_publication,
)
from tools.weekly_feedback import generate_weekly_feedback
from tools.weekly_feedback_inputs import prepare_feedback_inputs
from tools.weekly_feedback_support import WeeklyFeedbackRequest

KST: Final = timezone(timedelta(hours=9))


@dataclass(frozen=True, slots=True)
class ReplayRequest:
    root: Path
    as_of: str
    evidence_dir: Path


def _as_of(value: str) -> datetime:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise ContractError("as_of must be YYYY-MM-DD") from error
    if parsed.isoformat() != value:
        raise ContractError("as_of must be YYYY-MM-DD")
    return datetime.combine(parsed, time(23, 59, 59), tzinfo=KST)


def _horizon_counts(cohorts: JSONValue) -> JSONMap:
    result: JSONMap = {}
    if not isinstance(cohorts, list):
        raise ContractError("cohort result is invalid")
    for horizon_name in ("7d", "28d"):
        counts: Counter[str] = Counter()
        for cohort in cohorts:
            horizons = cohort.get("horizons") if isinstance(cohort, dict) else None
            horizon = horizons.get(horizon_name) if isinstance(horizons, dict) else None
            status = horizon.get("status") if isinstance(horizon, dict) else None
            if isinstance(status, str):
                counts[status] += 1
        result[horizon_name] = dict(sorted(counts.items()))
    return result


def _source_status() -> list[JSONValue]:
    registry = load_registry(
        Path(__file__).resolve().parents[1] / "config/topic-feedback-sources.json"
    )
    output: list[JSONValue] = []
    for source in registry.sources:
        status = "disabled"
        if source.enabled and source.access_mode.value == "official_api":
            status = "blocked_missing_credentials"
        elif source.enabled:
            status = "manual_input_only"
        output.append({"source_id": source.source_id, "status": status})
    return output


def _ranking(payload: JSONMap) -> JSONMap:
    rankings = payload.get("rankings")
    value = rankings[1] if isinstance(rankings, list) and len(rankings) > 1 else None
    if not isinstance(value, dict):
        raise ContractError("weekly feedback ranking is invalid")
    return value


def _close_bindings(
    root: ReplayDirectory,
    evidence: ReplayDirectory,
    publication: Publication | None,
) -> None:
    first_error: ContractError | None = None
    for binding in (root, evidence):
        try:
            close_replay_directory(binding)
        except ContractError as error:
            if publication is not None:
                rollback_publication(publication)
            if first_error is None:
                first_error = error
    if first_error is not None:
        raise first_error


def replay(request: ReplayRequest) -> bytes:
    root_binding = open_replay_directory(request.root, "replay root")
    try:
        evidence_binding = open_replay_directory(
            request.evidence_dir, "evidence directory"
        )
    except ContractError:
        close_replay_directory(root_binding)
        raise
    publication: Publication | None = None
    completed = False
    try:
        as_of = _as_of(request.as_of)
        timestamp = as_of.isoformat(timespec="seconds")
        with replay_scratch(root_binding.descriptor) as root:
            prepared = prepare_feedback_inputs(root, as_of, timestamp)
            bundle = generate_weekly_feedback(WeeklyFeedbackRequest(root, timestamp))
            _, feedback_bytes = safe_read(root, bundle.feedback_json)
            try:
                raw_value: JSONValue = json.loads(feedback_bytes)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise ContractError("weekly feedback output is invalid") from error
            if not isinstance(raw_value, dict):
                raise ContractError("weekly feedback output is invalid")
            raw = raw_value
            manifest = bundle.manifest
            manifest_digest = bundle.manifest_digest
            artifact = parse_artifact(raw)
            evidence = load_feedback_path(root, manifest, timestamp, ())
            historical_ranking = _ranking(raw)
            ranking = (
                historical_ranking
                if prepared.ranking is None
                else prepared.ranking.as_json()
            )
            ranking["candidates"] = ranking.get("selected", [])
            creator = prepared.creator
            if creator is None:
                raise ContractError("replay requires a Creator Advisor snapshot")
            creator_keywords = [item.keyword for item in creator.candidates]
            selected = ranking.get("selected")
            if not isinstance(selected, list):
                raise ContractError("weekly feedback ranking is invalid")
            selected_keywords: list[str] = []
            for item in selected:
                keyword = item.get("keyword") if isinstance(item, dict) else None
                if not isinstance(keyword, str):
                    raise ContractError("weekly feedback ranking is invalid")
                selected_keywords.append(keyword)
            if not set(selected_keywords).issubset(creator_keywords):
                raise ContractError("ranking contains a non-Creator candidate")
            cohort_digest = prepared.cohorts.get("digest")
            if cohort_digest != compute_digest(prepared.cohorts):
                raise ContractError("cohort digest mismatch")
            accepted_count = (
                0
                if prepared.ranking is None
                else sum(len(item.features) for item in prepared.ranking.selected)
            )
            result: JSONMap = {
                "schema_version": "topic-feedback-replay-v1",
                "as_of_date": request.as_of,
                "candidate_source": "creator_advisor_only",
                "creator_candidates": list[JSONValue](creator_keywords),
                "candidate_keywords": list[JSONValue](selected_keywords),
                "cohorts": _horizon_counts(prepared.cohorts.get("cohorts")),
                "cohort_digest": cohort_digest,
                "ranking": ranking,
                "signal_provenance": {
                    "accepted_count": accepted_count,
                    "excluded": ranking.get("excluded_signals", []),
                    "absence": "no_trusted_signal_manifest",
                },
                "feedback_id": raw["feedback_id"],
                "feedback_digest": artifact.payload["digest"],
                "feedback_manifest_digest": evidence.digest,
                "digest_chain_verified": evidence.digest == manifest_digest,
                "source_status": _source_status(),
                "live_sources_enabled": False,
                "selection_mutation": prepared.rollout.selection_mutation,
                "external_calls": 0,
                "external_writes": 0,
                "source_root_writes": 0,
                "scratch_writes": 4,
            }
            encoded = canonical_json(result) + b"\n"
        publication = publish_evidence(evidence_binding, encoded)
        verify_replay_directory(evidence_binding, "evidence directory")
        completed = True
    finally:
        if not completed and publication is not None:
            rollback_publication(publication)
        _close_bindings(
            root_binding,
            evidence_binding,
            publication if completed else None,
        )
    return encoded


__all__ = [
    "ReplayRequest",
    "open_replay_directory",
    "replay",
    "verify_replay_directory",
]
