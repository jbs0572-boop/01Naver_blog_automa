from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from tools.contract_types import (
    PIPELINE_VERSION,
    STAGES,
    STATUS_COMPATIBILITY,
    STATUSES,
    ContractError,
    JSONMap,
    JSONValue,
)
from tools.schema_validation import SCHEMA_PATH, SchemaError, validate_instance


def _map(value: JSONValue, label: str) -> JSONMap:
    if not isinstance(value, dict):
        raise ContractError(f"JSON object required: {label}")
    return value


@dataclass(frozen=True, slots=True)
class _TelemetryInterval:
    run_id: str
    stage: str
    started_at: datetime
    ended_at: datetime
    dependencies: tuple[str, ...]


def _telemetry_v2(event: JSONMap) -> bool:
    if "telemetry_version" not in event:
        return False
    version = event["telemetry_version"]
    if isinstance(version, int) and not isinstance(version, bool) and version == 2:
        return True
    raise ContractError("unsupported telemetry_version")


def _required_string(event: JSONMap, field: str) -> str:
    value = event.get(field)
    if not isinstance(value, str):
        raise ContractError(f"invalid telemetry v2 {field}")
    return value


def _aware_datetime(event: JSONMap, field: str) -> datetime:
    value = _required_string(event, field)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ContractError(f"invalid telemetry v2 {field}") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ContractError(f"invalid telemetry v2 {field}")
    return parsed


def _duration_ms(event: JSONMap) -> None:
    value = event.get("duration_ms")
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
    ):
        raise ContractError("invalid telemetry v2 duration_ms")


def _dependencies(event: JSONMap) -> tuple[str, ...]:
    value = event.get("depends_on")
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ContractError("invalid telemetry v2 depends_on")
    dependencies: list[str] = []
    for dependency in value:
        if not isinstance(dependency, str):
            raise ContractError("invalid telemetry v2 depends_on")
        dependencies.append(dependency)
    return tuple(dependencies)


def _interval(event: JSONMap) -> _TelemetryInterval:
    _duration_ms(event)
    started_at = _aware_datetime(event, "started_at")
    ended_at = _aware_datetime(event, "ended_at")
    if ended_at < started_at:
        raise ContractError("invalid telemetry v2 chronology: ended_at precedes started_at")
    return _TelemetryInterval(
        run_id=_required_string(event, "run_id"),
        stage=_required_string(event, "stage"),
        started_at=started_at,
        ended_at=ended_at,
        dependencies=_dependencies(event),
    )


def _validate_telemetry_chronology(events: list[JSONMap]) -> None:
    prior_stage_end: dict[tuple[str, str], datetime] = {}
    latest_stage_end: dict[tuple[str, str], datetime] = {}
    for event in events:
        if not _telemetry_v2(event):
            continue
        interval = _interval(event)
        key = (interval.run_id, interval.stage)
        previous_end = prior_stage_end.get(key)
        if previous_end is not None and interval.started_at < previous_end:
            raise ContractError("invalid telemetry v2 chronology: same-stage intervals overlap")
        for dependency in interval.dependencies:
            dependency_end = latest_stage_end.get((interval.run_id, dependency))
            if dependency_end is not None and interval.started_at < dependency_end:
                raise ContractError(
                    "invalid telemetry v2 dependency chronology: dependency overlaps"
                )
        prior_stage_end[key] = interval.ended_at
        latest_end = latest_stage_end.get(key)
        if latest_end is None or interval.ended_at > latest_end:
            latest_stage_end[key] = interval.ended_at


def read_events(path: Path) -> list[JSONMap]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise ContractError(f"could not read run log: {path}") from error
    events: list[JSONMap] = []
    for line_no, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            raw: JSONValue = json.loads(line)
        except json.JSONDecodeError as error:
            raise ContractError(f"invalid JSON at {path}:{line_no}") from error
        event = _map(raw, f"{path}:{line_no}")
        _ = _telemetry_v2(event)
        event_type = event.get("event_type")
        optimized_event = event.get("pipeline_version") == PIPELINE_VERSION
        if event_type in {"stage", "lane"}:
            if optimized_event:
                try:
                    validate_instance(event, SCHEMA_PATH)
                except SchemaError as error:
                    raise ContractError(
                        f"invalid stage event schema at {path}:{line_no}: {error}"
                    ) from error
            stage = event.get("stage")
            status = event.get("status")
            mapped_status = (
                STATUS_COMPATIBILITY.get(status, status)
                if isinstance(status, str)
                else status
            )
            if stage not in STAGES or mapped_status not in STATUSES:
                raise ContractError(f"invalid stage event at {path}:{line_no}")
        elif event_type == "approval":
            pass
        elif event_type == "confirmation":
            if optimized_event:
                try:
                    validate_instance(event, SCHEMA_PATH)
                except SchemaError as error:
                    raise ContractError(
                        f"invalid confirmation event schema at {path}:{line_no}: {error}"
                    ) from error
        elif event_type not in {
            "batch_preparation",
            "baseline",
            "batch_completion",
            None,
        }:
            raise ContractError(f"invalid event_type at {path}:{line_no}")
        events.append(event)
    _validate_telemetry_chronology(events)
    return events


def validate_log(path: Path) -> int:
    return len(read_events(path))


__all__ = ["read_events", "validate_log"]
