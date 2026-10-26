from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.contract_types import PIPELINE_VERSION, ContractError
from tools.log_contract import read_events, validate_log

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_historical_logs_remain_readable() -> None:
    """Check real historical logs only when both original, untracked files exist.

    The repository deliberately ignores runs/, so a clean GitHub checkout has
    no original August 2026 records. Do NOT fabricate those records, replace
    their contents, or report that historical files were actually examined.
    The mandatory portable legacy-format regression below always runs.
    """
    originals = (
        (PROJECT_ROOT / "runs" / "beta-2026-08-26-energy-day.jsonl", 8),
        (PROJECT_ROOT / "runs" / "beta-gate0-2026-08-26-9topics.jsonl", 66),
    )
    available = [path.is_file() for path, _ in originals]
    if not any(available):
        assert "runs/" in (PROJECT_ROOT / ".gitignore").read_text(
            encoding="utf-8"
        ).splitlines(), "missing files must be a documented ignore policy"
        pytest.skip(
            "Authentic 2026-08-26 run logs are local-only and not checked in; "
            "their compatibility is unverified in CI (portable fixtures run separately)"
        )
    assert all(available), "partial historical logs: cannot verify either run as complete"
    for path, expected_count in originals:
        assert validate_log(path) == expected_count


def test_portable_synthetic_legacy_formats_are_readable(tmp_path: Path) -> None:
    """Mandatory deterministic offline regression; never authentic history.

    Keep two batch sizes from the historical-test contract, but generate
    explicitly SYNTHETIC records to test the old JSONL reader without claiming
    access to files excluded by .gitignore.
    """
    stages = (
        "topic-selector", "researcher", "writer", "image-maker",
        "content-assembler", "notion-rider", "naver-rider",
    )
    statuses = (
        "success", "completed", "not-run", "passed", "failed",
        "pending", "running", "blocked",
    )
    for count in (8, 66):
        events: list[dict[str, object]] = []
        for index in range(count):
            common: dict[str, object] = {
                "fixture_provenance": "SYNTHETIC_FOR_TESTING_NOT_HISTORICAL",
                "run_id": f"SYNTHETIC-{count}-{index}",
            }
            kind = index % 11
            if kind in (0, 1, 2, 3, 4, 5):
                event = {
                    **common,
                    "event_type": "lane" if kind == 5 else "stage",
                    "stage": stages[index % len(stages)],
                    "status": statuses[index % len(statuses)],
                }
            elif kind == 6:
                event = {**common, "event_type": "baseline"}
            elif kind == 7:
                event = {**common, "event_type": "batch_preparation"}
            elif kind == 8:
                event = {**common, "event_type": "batch_completion"}
            elif kind == 9:
                event = {
                    **common, "event_type": "approval",
                    "gate": "notion_write", "decision": "approved",
                    "scope": "per-run",
                }
            else:
                event = {**common, "event_type": None}
            events.append(event)
        path = tmp_path / f"SYNTHETIC-legacy-{count}.jsonl"
        path.write_text(
            "".join(json.dumps(event, ensure_ascii=False) + "\n"
                    for event in events),
            encoding="utf-8",
        )
        assert validate_log(path) == count
        assert read_events(path) == events


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


def test_oversized_run_log_rejected_before_json_parse(tmp_path: Path, monkeypatch) -> None:
    import tools.log_contract as log_contract

    monkeypatch.setattr(log_contract, "MAX_RUN_LOG_BYTES", 40)
    path = tmp_path / "oversized.jsonl"
    _ = path.write_bytes((b'{"event_type": "baseline"}\\n'.replace(b"\\\\n", b"\\n")) * 3)
    with pytest.raises(ContractError, match="run log exceeds"):
        _ = read_events(path)


def test_run_log_invalid_utf8_returns_contract_error(tmp_path: Path) -> None:
    path = tmp_path / "invalid-utf8.jsonl"
    _ = path.write_bytes(b'{"event_type": "baseline"}\\n\\xff'.replace(b"\\\\", b"\\"))
    with pytest.raises(ContractError, match="not valid UTF-8"):
        _ = read_events(path)
