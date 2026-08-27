from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.contract_types import PIPELINE_VERSION, ContractError
from tools.log_contract import validate_log

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_historical_logs_remain_readable() -> None:
    assert validate_log(PROJECT_ROOT / "runs" / "beta-2026-08-26-energy-day.jsonl") == 8
    assert (
        validate_log(PROJECT_ROOT / "runs" / "beta-gate0-2026-08-26-9topics.jsonl")
        == 66
    )


def test_legacy_status_aliases_remain_supported(tmp_path: Path) -> None:
    events = [
        {"event_type": "stage", "stage": "researcher", "status": "success"},
        {"event_type": "stage", "stage": "writer", "status": "completed"},
        {"event_type": "stage", "stage": "image-maker", "status": "not-run"},
    ]
    path = tmp_path / "legacy.jsonl"
    _ = path.write_text(
        "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
    )

    assert validate_log(path) == 3


def test_malformed_jsonl_and_unknown_event_are_rejected(tmp_path: Path) -> None:
    malformed = tmp_path / "malformed.jsonl"
    _ = malformed.write_text("not-json\n", encoding="utf-8")
    with pytest.raises(ContractError, match="invalid JSON"):
        _ = validate_log(malformed)

    unknown = tmp_path / "unknown.jsonl"
    _ = unknown.write_text(
        json.dumps({"event_type": "unknown"}) + "\n", encoding="utf-8"
    )
    with pytest.raises(ContractError, match="invalid event_type"):
        _ = validate_log(unknown)


def test_optimized_event_requires_timezone(tmp_path: Path) -> None:
    event = {
        "event_type": "stage",
        "pipeline_version": PIPELINE_VERSION,
        "batch_id": "BATCH-log",
        "run_id": "RUN-log",
        "topic_id": "TOPIC-log",
        "stage": "researcher",
        "started_at": "2026-08-27T00:00:00",
        "ended_at": "2026-08-27T00:01:00+00:00",
        "status": "passed",
        "attempt": 1,
    }
    path = tmp_path / "optimized.jsonl"
    _ = path.write_text(json.dumps(event) + "\n", encoding="utf-8")

    with pytest.raises(ContractError, match="schema"):
        _ = validate_log(path)
