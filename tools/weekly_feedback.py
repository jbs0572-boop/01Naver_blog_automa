from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Final

from tools.contract_types import ContractError, JSONMap, JSONValue
from tools.feedback_manifest import (
    BundleFault,
    BundleOutput,
    EvidenceManifestRequest,
    build_evidence_manifest,
    canonical_json,
    publish_bundle,
    safe_read,
    sha256,
)
from tools.topic_feedback_models import WeeklyFeedback, compute_digest, parse_artifact
from tools.topic_performance_input import parse_kst_timestamp
from tools.weekly_feedback_inputs import (
    prepare_feedback_inputs,
    ranking_semantics,
    weekly_input_identity,
)
from tools.weekly_feedback_support import (
    WeeklyFeedbackBundle,
    WeeklyFeedbackRequest,
    cohort_counts,
    existing_bundle,
    render_feedback_markdown,
    search_results,
)
from tools.weekly_report import (
    WeeklyReportRevision,
    render_weekly_report,
    weekly_report_path,
)

KST: Final = timedelta(hours=9)


def generate_weekly_feedback(
    request: WeeklyFeedbackRequest,
    fault: BundleFault | None = None,
) -> WeeklyFeedbackBundle:
    as_of = parse_kst_timestamp(request.data_as_of, "data_as_of")
    if as_of.utcoffset() != KST:
        raise ContractError("data_as_of must be a KST timestamp")
    prepared = prepare_feedback_inputs(request.root, as_of, request.data_as_of)
    identity = weekly_input_identity(request.root, as_of, request.data_as_of)
    feedback_id = f"FEEDBACK-{as_of.date().isoformat()}-{identity[7:19]}"
    existing = existing_bundle(request, feedback_id, identity)
    if existing is not None:
        return existing
    performance = prepared.performance
    cohorts_payload = prepared.cohorts
    cohorts_value = cohorts_payload.get("cohorts")
    if not isinstance(cohorts_value, list):
        raise ContractError("cohort result is invalid")
    digest_values = cohorts_payload.get("input_digests")
    if not isinstance(digest_values, list):
        raise ContractError("cohort result is invalid")
    input_paths = prepared.paths
    input_material: list[JSONValue] = [
        sha256(safe_read(request.root, path)[1]) for path in input_paths
    ]
    cohort_digest = cohorts_payload.get("digest")
    if not isinstance(cohort_digest, str):
        raise ContractError("cohort result is invalid")
    input_material.append(cohort_digest)
    ranking_payload: JSONMap
    if prepared.ranking is None:
        evaluation_verdict = (
            "promotion_eligible"
            if prepared.evaluation.promotion_eligible
            else "not_eligible"
            if prepared.evaluation.mature_selected_outcomes
            >= prepared.rollout.minimum_mature_samples
            else "insufficient_evidence"
        )
        ranking_payload = {
            "schema_version": "weekly-feedback-ranking-v1",
            "selection_mode": prepared.selection_mode,
            "baseline": [],
            "shadow": [],
            "selected": [],
            "excluded_signals": [],
            "evaluation": prepared.evaluation.as_json(),
            "challenger_verdict": evaluation_verdict,
            "evidence_digests": [],
        }
    else:
        ranking_payload = prepared.ranking.as_json()
    selected_value = ranking_payload.get("selected")
    candidates = selected_value if isinstance(selected_value, list) else []
    stable_ranking: JSONMap = {"ranking": ranking_semantics(prepared.ranking)}
    ranking_digest = sha256(canonical_json(stable_ranking))
    evaluation_digest = sha256(canonical_json(prepared.evaluation.as_json()))
    input_material.extend((ranking_digest, evaluation_digest))
    limitations: list[JSONValue] = [
        "manual_blog_stats_only",
        "approval_not_modified",
    ]
    if not performance.links:
        limitations.append("no_publication_performance_inputs")
    if not candidates:
        limitations.append("no_creator_advisor_snapshot")
    rankings: list[JSONValue] = [
        {
            "kind": "score_provenance",
            "selection_mode": prepared.selection_mode,
            "score_version": prepared.score_version,
            "score_config_digest": prepared.config_digest,
            "approval_evaluation_digest": prepared.evaluation_digest,
            "evaluation_source_path": (
                None
                if prepared.evaluation_source_path is None
                else prepared.evaluation_source_path.relative_to(request.root).as_posix()
            ),
            "evaluation_source_digest": prepared.evaluation_source_digest,
            "evaluation_result_digest": evaluation_digest,
            "ranking_digest": ranking_digest,
            "cohort_digest": cohort_digest,
            "challenger_verdict": ranking_payload["challenger_verdict"],
            "search_inflow": search_results(cohorts_value),
            "source_freshness": prepared.freshness,
            "data_as_of": request.data_as_of,
            "semantic_input_digest": identity,
        },
        {"kind": "creator_advisor_ranking", **ranking_payload, "candidates": candidates},
        {"kind": "cohort_status", "status_counts": cohort_counts(cohorts_value)},
    ]
    payload: JSONMap = {
        "schema_version": "weekly-feedback-v1",
        "captured_at": request.data_as_of,
        "as_of_date": as_of.date().isoformat(),
        "timezone": "Asia/Seoul",
        "limitations": limitations,
        "input_digests": input_material,
        "missing_fields": [],
        "status": "mature" if performance.links else "missing",
        "digest": "",
        "feedback_id": feedback_id,
        "score_version": prepared.score_version,
        "cohorts": cohorts_value,
        "rankings": rankings,
    }
    payload["digest"] = compute_digest(payload)
    artifact = WeeklyFeedback(payload)
    feedback_bytes = canonical_json(parse_artifact(artifact.payload).payload) + b"\n"
    manifest = build_evidence_manifest(
        EvidenceManifestRequest(
            request.root,
            feedback_id,
            request.data_as_of,
            request.data_as_of,
            input_paths,
        )
    )
    report_date, operational_bytes = render_weekly_report(request.root, as_of)
    operational = weekly_report_path(
        WeeklyReportRevision(request.root, report_date, operational_bytes, identity)
    )
    feedback_relative = Path("metadata/weekly-feedback") / report_date / f"{feedback_id}.json"
    markdown_relative = Path(".automation/reports") / f"weekly-feedback-{feedback_id}.md"
    manifest_relative = Path("metadata/feedback-manifests") / f"{feedback_id}.json"
    if operational.exists() and not (request.root / feedback_relative).exists():
        operational = operational.with_name(
            f"weekly-{report_date}-{identity[7:19]}.md"
        )
    publish_bundle(
        request.root,
        (
            BundleOutput(operational.relative_to(request.root), operational_bytes),
            BundleOutput(feedback_relative, feedback_bytes),
            BundleOutput(markdown_relative, render_feedback_markdown(payload)),
            BundleOutput(manifest_relative, manifest.encoded),
        ),
        fault,
    )
    return WeeklyFeedbackBundle(
        feedback_id,
        operational,
        request.root / feedback_relative,
        request.root / markdown_relative,
        request.root / manifest_relative,
        str(payload["digest"]),
        manifest.digest,
    )


__all__ = [
    "WeeklyFeedbackBundle",
    "WeeklyFeedbackRequest",
    "generate_weekly_feedback",
]
