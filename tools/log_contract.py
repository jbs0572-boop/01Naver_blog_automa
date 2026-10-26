from __future__ import annotations

import json
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

# Bounds allocations when a corrupted or untrusted run log is inspected.
# Authentic historical logs remain untouched; a file over this limit fails closed.
MAX_RUN_LOG_BYTES = 64 * 1024 * 1024


def _map(value: JSONValue, label: str) -> JSONMap:
    if not isinstance(value, dict):
        raise ContractError(f"JSON object required: {label}")
    return value


def read_events(path: Path) -> list[JSONMap]:
    try:
        with path.open("rb") as handle:
            payload = handle.read(MAX_RUN_LOG_BYTES + 1)
    except OSError as error:
        raise ContractError(f"could not read run log: {path}") from error
    if len(payload) > MAX_RUN_LOG_BYTES:
        raise ContractError(f"run log exceeds {MAX_RUN_LOG_BYTES} bytes: {path}")
    try:
        lines = payload.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise ContractError(f"run log is not valid UTF-8: {path}") from error
    events: list[JSONMap] = []
    for line_no, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            raw: JSONValue = json.loads(line)
        except json.JSONDecodeError as error:
            raise ContractError(f"invalid JSON at {path}:{line_no}") from error
        event = _map(raw, f"{path}:{line_no}")
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
            if optimized_event:
                try:
                    validate_instance(event, SCHEMA_PATH)
                except SchemaError as error:
                    raise ContractError(
                        f"invalid approval event schema at {path}:{line_no}: {error}"
                    ) from error
            if event.get("gate") not in {"notion_write", "naver_draft_save"}:
                raise ContractError(f"invalid approval gate at {path}:{line_no}")
            if event.get("decision") not in {"approved", "rejected", "expired"}:
                raise ContractError(f"invalid approval decision at {path}:{line_no}")
            if event.get("scope") not in {"per-run", "batch"}:
                raise ContractError(f"invalid approval scope at {path}:{line_no}")
        elif event_type not in {
            "batch_preparation",
            "baseline",
            "batch_completion",
            None,
        }:
            raise ContractError(f"invalid event_type at {path}:{line_no}")
        events.append(event)
    return events


def validate_log(path: Path) -> int:
    return len(read_events(path))


__all__ = ["read_events", "validate_log"]
