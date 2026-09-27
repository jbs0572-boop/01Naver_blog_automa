from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from tools.contract_types import ContractError, JSONMap, JSONValue
from tools.feedback_manifest import canonical_json, safe_read, sha256
from tools.topic_feedback_config import (
    BASELINE_VERSION,
    FEEDBACK_VERSION,
    RolloutPin,
    load_rollout_config,
)
from tools.topic_feedback_evaluation import evaluate_outcomes
from tools.topic_feedback_manifest_reader import latest_feedback_evidence
from tools.topic_feedback_models import compute_digest
from tools.topic_feedback_scoring_models import EvaluationResult
from tools.topic_metadata import CreatorAdvisorSnapshot
from tools.topic_performance import compute_cohorts
from tools.topic_performance_input import (
    PerformanceInputs,
    load_performance_inputs,
)
from tools.weekly_feedback_evaluation_input import latest_governed_evaluation
from tools.weekly_feedback_ranking import (
    WeeklyRankingRequest,
    WeeklyRankingResult,
    build_weekly_ranking,
)
from tools.weekly_feedback_sources import (
    artifact_paths,
    latest_creator,
    source_freshness,
)


@dataclass(frozen=True, slots=True)
class PreparedFeedbackInputs:
    performance: PerformanceInputs
    cohorts: JSONMap
    paths: tuple[Path, ...]
    candidates: list[JSONValue]
    freshness: list[JSONValue]
    score_version: str
    selection_mode: str
    config_digest: str | None
    evaluation_digest: str | None
    creator: CreatorAdvisorSnapshot | None
    rollout: RolloutPin
    ranking: WeeklyRankingResult | None
    evaluation: EvaluationResult
    evaluation_source_path: Path | None
    evaluation_source_digest: str | None


def prepare_feedback_inputs(
    root: Path, data_as_of: datetime, data_as_of_value: str
) -> PreparedFeedbackInputs:
    links_root = root / "metadata" / "publication-links"
    if links_root.exists():
        try:
            performance = load_performance_inputs(root, data_as_of)
            cohorts = compute_cohorts(root, data_as_of_value)
        except ContractError as error:
            if "digest" in str(error):
                raise ContractError("feedback input digest mismatch") from error
            raise
    else:
        performance = PerformanceInputs((), {})
        empty_values: list[JSONValue] = []
        cohorts: JSONMap = {
            "schema_version": "topic-performance-cohorts-v1",
            "as_of": data_as_of_value,
            "timezone": "Asia/Seoul",
            "input_digests": empty_values,
            "cohorts": empty_values,
            "digest": "",
        }
        cohorts["digest"] = compute_digest(cohorts)
    digest_values = cohorts.get("input_digests")
    if not isinstance(digest_values, list):
        raise ContractError("cohort result is invalid")
    artifacts = artifact_paths(root, frozenset(str(item) for item in digest_values))
    if len(artifacts) != len(digest_values):
        raise ContractError("feedback input digest mismatch")
    creator = latest_creator(root, data_as_of)
    paths = artifacts + (() if creator is None else (creator[0],))
    config_path = root / "config" / "topic-feedback-rollout.json"
    if config_path.exists():
        pin = load_rollout_config(root, config_path)
        selection_mode = (
            "active"
            if pin.selection_mutation
            else "shadow"
            if pin.feedback_enabled
            else "baseline"
        )
        score_version = (
            pin.active_score_version if pin.selection_mutation else BASELINE_VERSION
        )
        config_digest = pin.config_digest
        evaluation_digest = pin.evaluation_digest
        paths += (config_path,)
    else:
        pin = RolloutPin(
            False,
            BASELINE_VERSION,
            FEEDBACK_VERSION,
            30,
            "off",
            BASELINE_VERSION,
            (),
            "sha256:" + "0" * 64,
            None,
            False,
            None,
            False,
        )
        selection_mode = "baseline"
        score_version = BASELINE_VERSION
        config_digest = None
        evaluation_digest = None
    enabled_sources = tuple(
        item.source_id for item in pin.source_modes if item.enabled
    )
    try:
        governed_evaluation = latest_governed_evaluation(root, data_as_of)
        evidence = (
            latest_feedback_evidence(root, data_as_of.isoformat(), enabled_sources)
            if enabled_sources
            else None
        )
    except ContractError as error:
        raise ContractError("feedback input digest mismatch") from error
    if governed_evaluation is not None:
        paths += governed_evaluation.evidence_paths
    evidence_manifests: tuple[Path, ...] = ()
    if evidence is not None and evidence.signals:
        evidence_manifests = (root / evidence.manifest_path,)
        paths += tuple(root / item for item in evidence.signal_paths)
    ranking = (
        build_weekly_ranking(
            WeeklyRankingRequest(
                root,
                creator[1],
                evidence_manifests,
                pin,
                data_as_of,
                None if governed_evaluation is None else governed_evaluation.outcomes,
            )
        )
        if creator is not None
        else None
    )
    evaluation = (
        ranking.evaluation
        if ranking is not None
        else evaluate_outcomes(
            () if governed_evaluation is None else governed_evaluation.outcomes,
            evaluation_as_of=data_as_of,
            minimum_mature_samples=pin.minimum_mature_samples,
        )
    )
    candidates: list[JSONValue] = []
    if creator is not None:
        candidates.extend(
            {"keyword": item.keyword, "rank": item.rank}
            for item in sorted(creator[1].candidates, key=lambda item: item.rank)
        )
    return PreparedFeedbackInputs(
        performance,
        cohorts,
        paths,
        candidates,
        source_freshness(root, paths, data_as_of),
        score_version,
        selection_mode,
        config_digest,
        evaluation_digest,
        None if creator is None else creator[1],
        pin,
        ranking,
        evaluation,
        None if governed_evaluation is None else governed_evaluation.path,
        None if governed_evaluation is None else governed_evaluation.digest,
    )


def weekly_input_identity(
    root: Path, data_as_of: datetime, data_as_of_value: str
) -> str:
    prepared = prepare_feedback_inputs(root, data_as_of, data_as_of_value)
    cohort_semantics = dict(prepared.cohorts)
    _ = cohort_semantics.pop("as_of", None)
    _ = cohort_semantics.pop("digest", None)
    material: JSONMap = {
        "inputs": [
            {
                "path": path.relative_to(root).as_posix(),
                "sha256": sha256(safe_read(root, path)[1]),
            }
            for path in prepared.paths
        ],
        "cohorts": cohort_semantics,
        "score_version": prepared.score_version,
        "selection_mode": prepared.selection_mode,
        "config_digest": prepared.config_digest,
        "evaluation_digest": prepared.evaluation_digest,
        "ranking": ranking_semantics(prepared.ranking),
        "evaluation": _evaluation_semantics(prepared.evaluation),
        "evaluation_source_digest": prepared.evaluation_source_digest,
    }
    return sha256(canonical_json(material))


def ranking_semantics(ranking: WeeklyRankingResult | None) -> JSONValue:
    if ranking is None:
        return None
    value = ranking.as_json()
    _ = value.pop("evidence_digests", None)
    evaluation = value.get("evaluation")
    if isinstance(evaluation, dict):
        evaluation = dict(evaluation)
        _ = evaluation.pop("evaluation_as_of", None)
        value["evaluation"] = evaluation
    return value


def _evaluation_semantics(evaluation: EvaluationResult) -> JSONMap:
    value = evaluation.as_json()
    _ = value.pop("evaluation_as_of", None)
    return value


__all__ = [
    "PreparedFeedbackInputs",
    "prepare_feedback_inputs",
    "ranking_semantics",
    "weekly_input_identity",
]
