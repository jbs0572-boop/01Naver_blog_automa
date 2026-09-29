from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tools.contract_types import JSONMap, JSONValue
from tools.topic_feedback_evaluation import evaluate_outcomes
from tools.topic_feedback_models import compute_digest
from tools.topic_feedback_scoring_models import CandidateOutcome


def _write(path: Path, payload: JSONMap) -> tuple[str, int, str]:
    payload["digest"] = compute_digest(payload)
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_bytes(encoded)
    return str(payload["digest"]), len(encoded), "sha256:" + hashlib.sha256(encoded).hexdigest()


def install_factual_evaluation(
    root: Path,
    *,
    count: int = 30,
    challenger_delta: float = 1.0,
    pending_last: bool = False,
) -> Path:
    kst = timezone(timedelta(hours=9))
    first = datetime(2023, 1, 1, 12, tzinfo=kst)
    outcomes: list[CandidateOutcome] = []
    references: list[JSONValue] = []
    for index in range(count):
        outcome_id = f"OUT-{index:03d}"
        published = first + timedelta(days=35 * index)
        directory = Path("metadata/topic-feedback-outcomes") / outcome_id
        selection_path = directory / "selection.json"
        seven_path = directory / "7d.json"
        twenty_eight_path = directory / "28d.json"
        selection: JSONMap = {
            "schema_version": "topic-feedback-selection-evidence-v1",
            "outcome_id": outcome_id,
            "run_id": f"RUN-{index:03d}",
            "keyword": f"후보-{index:03d}",
            "blog_post_id": f"POST-{index:03d}",
            "selected": True,
            "score_version": "topic-feedback-v1",
            "prediction_recorded_at": (published - timedelta(hours=1)).isoformat(),
            "training_cutoff": (published - timedelta(hours=2)).isoformat(),
            "published_at": published.isoformat(),
            "digest": "",
        }
        selection_digest, selection_size, selection_sha = _write(
            root / selection_path, selection
        )
        observation_refs: list[JSONMap] = []
        observation_values: list[tuple[str, str]] = []
        outcome_status = "pending" if pending_last and index == count - 1 else "mature"
        for days, path, baseline in (
            (7, seven_path, 10.0),
            (28, twenty_eight_path, 20.0),
        ):
            observation: JSONMap = {
                "schema_version": "topic-feedback-outcome-observation-v1",
                "outcome_id": outcome_id,
                "run_id": f"RUN-{index:03d}",
                "blog_post_id": f"POST-{index:03d}",
                "horizon_days": days,
                "observed_at": (published + timedelta(days=days, hours=1)).isoformat(),
                "status": outcome_status,
                "baseline_search_inflow": baseline,
                "challenger_search_inflow": baseline + challenger_delta,
                "digest": "",
            }
            logical, size, byte_sha = _write(root / path, observation)
            observation_refs.append(
                {
                    "path": path.as_posix(),
                    "size_bytes": size,
                    "sha256": byte_sha,
                    "schema_version": observation["schema_version"],
                    "digest": logical,
                }
            )
            observation_values.append((str(observation["observed_at"]), logical))
        references.append(
            {
                "outcome_id": outcome_id,
                "selection_ref": {
                    "path": selection_path.as_posix(),
                    "size_bytes": selection_size,
                    "sha256": selection_sha,
                    "schema_version": selection["schema_version"],
                    "digest": selection_digest,
                },
                "seven_day_ref": observation_refs[0],
                "twenty_eight_day_ref": observation_refs[1],
            }
        )
        outcomes.append(
            CandidateOutcome(
                outcome_id,
                True,
                outcome_status,
                10.0,
                10.0 + challenger_delta,
                20.0,
                20.0 + challenger_delta,
                published.isoformat(),
                str(selection["prediction_recorded_at"]),
                str(selection["training_cutoff"]),
                selection_digest,
                "topic-feedback-v1",
                observation_values[0][0],
                observation_values[0][1],
                observation_values[1][0],
                observation_values[1][1],
            )
        )
    evaluation_as_of = first + timedelta(days=35 * (count - 1) + 30)
    payload: JSONMap = {
        "schema_version": "topic-feedback-evaluation-v3",
        "baseline_score_version": "topic-baseline-v1",
        "challenger_score_version": "topic-feedback-v1",
        "evaluation_as_of": evaluation_as_of.isoformat(),
        "evaluation_parameters": {
            "minimum_mature_samples": 30,
            "metric": "search_inflow",
            "horizons_days": [7, 28],
            "aggregation": "median",
            "comparison": "strict_improvement",
            "validation": "publication_time_expanding_origin_v2",
        },
        "outcomes": references,
        "result": evaluate_outcomes(
            tuple(outcomes),
            evaluation_as_of=evaluation_as_of,
            minimum_mature_samples=30,
        ).as_json(),
        "ndcg_delta": None,
        "digest": "",
    }
    target = root / "metadata/topic-feedback-evaluations/evaluation-v3.json"
    _ = _write(target, payload)
    return target


__all__ = ["install_factual_evaluation"]
