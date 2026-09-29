from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Final

from tools.contract_types import ContractError, JSONMap, JSONValue
from tools.topic_feedback_models import compute_digest
from tools.topic_performance_input import (
    Observation,
    load_performance_inputs,
    parse_kst_timestamp,
)

_CUMULATIVE: Final = ("views", "search_inflow", "exposure")
_METRICS: Final = (*_CUMULATIVE, "average_exposure_rank")
_HORIZONS: Final = (("7d", 7), ("28d", 28))

def _invalid(observations: tuple[Observation, ...], through: datetime) -> str | None:
    relevant = tuple(item for item in observations if item.observed_at <= through)
    seen_ids: dict[str, str] = {}
    seen_instants: dict[tuple[datetime, str], tuple[JSONValue, ...]] = {}
    latest: dict[str, int | float] = {}
    for item in relevant:
        payload = item.payload
        capture_id, digest = str(payload["capture_id"]), str(payload["digest"])
        if capture_id in seen_ids and seen_ids[capture_id] != digest:
            return "conflicting_duplicate"
        seen_ids[capture_id] = digest
        identity = (item.observed_at, str(payload["coverage_end"]))
        values = tuple(payload.get(metric) for metric in _METRICS)
        if identity in seen_instants and seen_instants[identity] != values:
            return "conflicting_duplicate"
        seen_instants[identity] = values
        for metric in _CUMULATIVE:
            value = payload.get(metric)
            if isinstance(value, int | float) and not isinstance(value, bool):
                if metric in latest and value < latest[metric]:
                    return "cumulative_reset"
                latest[metric] = value
    return None


def _metrics(payload: JSONMap) -> JSONMap:
    return {metric: payload.get(metric) for metric in _METRICS}


def _delta(current: JSONMap, prior: JSONMap | None) -> JSONMap | None:
    if prior is None:
        return None
    result: JSONMap = {}
    for metric in _CUMULATIVE:
        current_value, prior_value = current.get(metric), prior.get(metric)
        if (
            isinstance(current_value, int | float)
            and not isinstance(current_value, bool)
            and isinstance(prior_value, int | float)
            and not isinstance(prior_value, bool)
        ):
            result[metric] = current_value - prior_value
    return result


def _covers_cutoff(item: Observation, cutoff: datetime) -> bool:
    coverage_end = item.payload.get("coverage_end")
    if not isinstance(coverage_end, str):
        return False
    try:
        return date.fromisoformat(coverage_end) >= cutoff.date()
    except ValueError:
        return False


def _horizon(observations: tuple[Observation, ...], as_of: datetime, cutoff: datetime) -> JSONMap:
    grace_end = cutoff + timedelta(hours=48)
    eligible = tuple(
        item
        for item in observations
        if cutoff <= item.observed_at <= grace_end and _covers_cutoff(item, cutoff)
    )
    selected = eligible[0] if eligible else None
    through = selected.observed_at if selected is not None else min(as_of, grace_end)
    invalid_reason = _invalid(observations, through)
    if invalid_reason is not None:
        status = "invalid"
    elif selected is not None:
        status = "mature"
    elif as_of < cutoff:
        status = "pending"
    elif as_of <= grace_end:
        status = "delayed"
    else:
        status = "missing"
    prior_items = tuple(item for item in observations if item.observed_at < cutoff)
    prior = prior_items[-1] if prior_items else None
    return {
        "cutoff": cutoff.isoformat(timespec="seconds"),
        "grace_end": grace_end.isoformat(timespec="seconds"),
        "status": status,
        "invalid_reason": invalid_reason,
        "observation_id": None if selected is None else selected.payload["capture_id"],
        "observation_digest": None if selected is None else selected.payload["digest"],
        "observation_captured_at": None if selected is None else selected.payload["captured_at"],
        "observation_coverage_end": None if selected is None else selected.payload["coverage_end"],
        "cumulative": None if selected is None else _metrics(selected.payload),
        "prior_observation_id": None if prior is None else prior.payload["capture_id"],
        "prior_observation_digest": None if prior is None else prior.payload["digest"],
        "window_delta": None if selected is None or invalid_reason is not None else _delta(selected.payload, None if prior is None else prior.payload),
    }


def compute_cohorts(root: Path, as_of_value: str) -> JSONMap:
    as_of = parse_kst_timestamp(as_of_value, "as_of")
    inputs = load_performance_inputs(root, as_of)
    links, observations = inputs.links, inputs.observations
    cohorts: list[JSONValue] = []
    input_digests: set[str] = {str(link["digest"]) for link in links}
    for values in observations.values():
        input_digests.update(str(item.payload["digest"]) for item in values)
    for link in links:
        published_at = parse_kst_timestamp(link.get("published_at"), "published_at")
        post_id = str(link["blog_post_id"])
        horizons: JSONMap = {
            name: _horizon(observations[post_id], as_of, published_at + timedelta(days=days))
            for name, days in _HORIZONS
        }
        seven, twenty_eight = horizons["7d"], horizons["28d"]
        if isinstance(seven, dict) and isinstance(twenty_eight, dict) and twenty_eight.get("status") == "mature" and seven.get("status") != "mature":
            twenty_eight["status"] = "invalid"
            twenty_eight["invalid_reason"] = "prior_horizon_not_mature"
            twenty_eight["window_delta"] = None
        cohorts.append(
            {
                "run_id": link["run_id"],
                "topic_id": link["topic_id"],
                "keyword": link["keyword"],
                "blog_post_id": post_id,
                "published_at": link["published_at"],
                "publication_link_digest": link["digest"],
                "artifact_digest": link["artifact_digest"],
                "score_version": link["score_version"],
                "horizons": horizons,
            }
        )
    digests: list[JSONValue] = []
    digests.extend(sorted(input_digests))
    result: JSONMap = {
        "schema_version": "topic-performance-cohorts-v1",
        "as_of": as_of.isoformat(timespec="seconds"),
        "timezone": "Asia/Seoul",
        "input_digests": digests,
        "cohorts": cohorts,
        "digest": "",
    }
    result["digest"] = compute_digest(result)
    return result


def serialize_cohorts(result: JSONMap) -> str:
    if result.get("digest") != compute_digest(result):
        raise ContractError("cohort digest mismatch")
    try:
        return json.dumps(
            result,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise ContractError("cohort result contains non-JSON data") from error
