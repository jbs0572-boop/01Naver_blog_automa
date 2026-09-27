from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from tools.contract_types import ContractError, JSONMap, JSONValue
from tools.feedback_manifest import safe_read
from tools.topic_feedback_manifest_reader import load_feedback_path
from tools.topic_feedback_models import WeeklyFeedback, parse_artifact


@dataclass(frozen=True, slots=True)
class WeeklyFeedbackRequest:
    root: Path
    data_as_of: str


@dataclass(frozen=True, slots=True)
class WeeklyFeedbackBundle:
    feedback_id: str
    operational_report: Path
    feedback_json: Path
    feedback_markdown: Path
    manifest: Path
    feedback_digest: str
    manifest_digest: str

    @property
    def paths(self) -> tuple[Path, ...]:
        return (
            self.operational_report,
            self.feedback_json,
            self.feedback_markdown,
            self.manifest,
        )


def cohort_counts(cohorts: list[JSONValue]) -> JSONMap:
    counts: Counter[str] = Counter()
    for item in cohorts:
        if not isinstance(item, dict):
            continue
        horizons = item.get("horizons")
        if not isinstance(horizons, dict):
            continue
        for horizon in horizons.values():
            if isinstance(horizon, dict) and isinstance(horizon.get("status"), str):
                counts[str(horizon["status"])] += 1
    return {
        key: counts[key]
        for key in ("mature", "pending", "delayed", "missing", "invalid")
    }


def search_results(cohorts: list[JSONValue]) -> JSONMap:
    output: JSONMap = {}
    for name in ("7d", "28d"):
        values: list[int | float] = []
        for item in cohorts:
            horizons = item.get("horizons") if isinstance(item, dict) else None
            horizon = horizons.get(name) if isinstance(horizons, dict) else None
            cumulative = horizon.get("cumulative") if isinstance(horizon, dict) else None
            value = cumulative.get("search_inflow") if isinstance(cumulative, dict) else None
            if isinstance(value, int | float) and not isinstance(value, bool):
                values.append(value)
        output[name] = {"mature_count": len(values), "search_inflow_total": sum(values)}
    return output


def render_feedback_markdown(payload: JSONMap) -> bytes:
    cohorts = payload.get("cohorts")
    rankings_value = payload.get("rankings")
    rankings = rankings_value if isinstance(rankings_value, list) else []
    counts_value = rankings[2] if len(rankings) > 2 else None
    counts = counts_value if isinstance(counts_value, dict) else {}
    next_value = rankings[1] if len(rankings) > 1 else None
    next_rank = next_value if isinstance(next_value, dict) else {}
    score_value = rankings[0] if rankings else None
    score = score_value if isinstance(score_value, dict) else {}
    limitations_value = payload.get("limitations")
    limitations = limitations_value if isinstance(limitations_value, list) else []
    def shown(value: JSONValue) -> str:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
    lines = [
        f"# Weekly content feedback ({payload['as_of_date']})",
        "",
        "## Operational KPIs",
        "",
        "- See immutable workflow operational report.",
        "",
        "## Content KPIs",
        "",
        f"- data_as_of: {score.get('data_as_of')}",
        f"- cohort_count: {len(cohorts) if isinstance(cohorts, list) else 0}",
        f"- status_counts: {shown(counts.get('status_counts'))}",
        f"- search_inflow: {shown(score.get('search_inflow'))}",
        f"- selection_mode: {score.get('selection_mode')}",
        f"- challenger_verdict: {score.get('challenger_verdict')}",
        f"- source_freshness: {shown(score.get('source_freshness'))}",
        f"- next_candidates: {shown(next_rank.get('candidates'))}",
        "",
        "## Limitations",
        "",
        *(f"- {item}" for item in limitations if isinstance(item, str)),
        "",
    ]
    return "\n".join(lines).encode()


def existing_bundle(
    request: WeeklyFeedbackRequest, feedback_id: str, identity: str
) -> WeeklyFeedbackBundle | None:
    report_date = request.data_as_of[:10]
    feedback = request.root / "metadata/weekly-feedback" / report_date / f"{feedback_id}.json"
    markdown = request.root / ".automation/reports" / f"weekly-feedback-{feedback_id}.md"
    manifest = request.root / "metadata/feedback-manifests" / f"{feedback_id}.json"
    present = tuple(path.exists() for path in (feedback, markdown, manifest))
    if not any(present):
        return None
    if not all(present):
        raise ContractError("feedback bundle is incomplete")
    _, encoded = safe_read(request.root, feedback)
    try:
        raw: JSONValue = json.loads(encoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ContractError("feedback bundle is invalid") from error
    if not isinstance(raw, dict):
        raise ContractError("feedback bundle is invalid")
    artifact = parse_artifact(raw)
    rankings = raw.get("rankings")
    provenance = rankings[0] if isinstance(rankings, list) and rankings else None
    if (
        not isinstance(artifact, WeeklyFeedback)
        or raw.get("feedback_id") != feedback_id
        or not isinstance(provenance, dict)
        or provenance.get("semantic_input_digest") != identity
        or safe_read(request.root, markdown)[1] != render_feedback_markdown(raw)
    ):
        raise ContractError("feedback bundle is invalid")
    evidence = load_feedback_path(request.root, manifest, request.data_as_of, ())
    revision = request.root / ".automation/reports" / f"weekly-{report_date}-{identity[7:19]}.md"
    operational = revision if revision.exists() else request.root / ".automation/reports" / f"weekly-{report_date}.md"
    _ = safe_read(request.root, operational)
    return WeeklyFeedbackBundle(
        feedback_id,
        operational,
        feedback,
        markdown,
        manifest,
        str(raw["digest"]),
        evidence.digest,
    )


__all__ = [
    "WeeklyFeedbackBundle",
    "WeeklyFeedbackRequest",
    "cohort_counts",
    "existing_bundle",
    "render_feedback_markdown",
    "search_results",
]
