from __future__ import annotations

import json
from datetime import datetime
from math import isfinite
from pathlib import Path

from tools.contract_types import ContractError, JSONValue


def run_metrics(
    path: Path, *, completed_by: str | None = None
) -> tuple[str, int, str, float | None]:
    cutoff = (
        aware_datetime(completed_by, "Notion page created_time")
        if completed_by is not None
        else None
    )
    batch_id: str | None = None
    attempts_by_stage: dict[str, int] = {}
    assembler_completed: str | None = None
    telemetry_duration_ms = 0.0
    has_telemetry_duration = False
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise ContractError("Notion run log could not be read") from error
    for line in lines:
        try:
            value: JSONValue = json.loads(line)
        except json.JSONDecodeError as error:
            raise ContractError("Notion run log is malformed") from error
        if not isinstance(value, dict):
            raise ContractError("Notion run log event is malformed")
        raw_batch = value.get("batch_id")
        if isinstance(raw_batch, str):
            if batch_id is not None and raw_batch != batch_id:
                raise ContractError("Notion run log has conflicting batch_id values")
            batch_id = raw_batch
        attempt = value.get("attempt")
        stage = value.get("stage")
        include_event = True
        if cutoff is not None:
            raw_ended_at = value.get("ended_at")
            include_event = (
                isinstance(raw_ended_at, str)
                and aware_datetime(raw_ended_at, "Notion run log ended_at") <= cutoff
            )
        if isinstance(attempt, bool) or (
            attempt is not None and (not isinstance(attempt, int) or attempt < 1)
        ):
            raise ContractError("Notion run log attempt is malformed")
        if include_event and isinstance(attempt, int):
            key = stage if isinstance(stage, str) and stage else "__pipeline__"
            attempts_by_stage[key] = max(attempt, attempts_by_stage.get(key, 1))
        if (
            include_event
            and stage == "content-assembler"
            and value.get("status") == "passed"
        ):
            assembler_completed = completion_timestamp(value.get("ended_at"))
        if value.get("telemetry_version") == 2:
            raw_duration = value.get("duration_ms")
            if (
                isinstance(raw_duration, bool)
                or not isinstance(raw_duration, (int, float))
                or raw_duration < 0
                or not isfinite(float(raw_duration))
            ):
                raise ContractError("Notion run log duration_ms is malformed")
            if include_event:
                telemetry_duration_ms += float(raw_duration)
                has_telemetry_duration = True
    if batch_id is None:
        raise ContractError("Notion run log is missing batch_id")
    if assembler_completed is None:
        raise ContractError("passed content-assembler completion is missing")
    duration = telemetry_duration_ms / 1000 if has_telemetry_duration else None
    return (
        batch_id,
        sum(attempt - 1 for attempt in attempts_by_stage.values()),
        assembler_completed,
        duration,
    )


def completion_timestamp(value: JSONValue | None) -> str:
    if not isinstance(value, str):
        raise ContractError("content-assembler completion timestamp is missing")
    try:
        completed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ContractError(
            "content-assembler completion timestamp is invalid"
        ) from error
    if completed.tzinfo is None or completed.utcoffset() is None:
        raise ContractError("content-assembler completion timestamp must be aware")
    return value


def aware_datetime(value: str, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ContractError(f"{label} timestamp is invalid") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ContractError(f"{label} timestamp must be aware")
    return parsed
