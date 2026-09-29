from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path

from tools.contract_types import JSONMap, JSONValue


def _map(value: JSONValue) -> JSONMap | None:
    return value if isinstance(value, dict) else None


class Q1FailureCode(StrEnum):
    CONTRACT_FAILURE = "q1_contract_failure"
    INPUT_UNREPAIRABLE = "q1_input_unrepairable"


_GUIDANCE: dict[str, str] = {
    Q1FailureCode.CONTRACT_FAILURE.value: (
        "Q1 사전 점검: 제목 약속, 독자 질문, 근거·최신성, 시각 계약, "
        "최종 파일과 canonical manifest를 모두 다시 확인하세요."
    )
}


def guidance_for(code: str) -> str:
    return _GUIDANCE.get(code, "")


def _config(root: Path) -> tuple[str, int, int, int]:
    path = root / "config" / "q1-feedback-rollout.json"
    try:
        raw_value: JSONValue = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "off", 90, 3, 10
    raw = _map(raw_value)
    if raw is None or raw.get("schema_version") != "q1-feedback-rollout-v1":
        return "off", 90, 3, 10
    mode = raw.get("mode")
    lookback = raw.get("lookback_days")
    minimum = raw.get("minimum_repaired_runs")
    maximum = raw.get("max_active_codes")
    if (
        mode not in {"off", "shadow", "active"}
        or not isinstance(lookback, int)
        or isinstance(lookback, bool)
        or lookback < 1
        or not isinstance(minimum, int)
        or isinstance(minimum, bool)
        or minimum < 1
        or not isinstance(maximum, int)
        or isinstance(maximum, bool)
        or maximum < 1
    ):
        return "off", 90, 3, 10
    return mode, lookback, minimum, maximum


def _events(root: Path, cutoff: datetime) -> list[JSONMap]:
    events: list[JSONMap] = []
    for path in sorted((root / "runs").glob("*.jsonl")):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                value: JSONValue = json.loads(line)
            except json.JSONDecodeError:
                continue
            event = _map(value)
            if event is None:
                continue
            if event.get("event_type") != "stage" or event.get("stage") != "content-assembler":
                continue
            ended_at = event.get("ended_at")
            if not isinstance(ended_at, str):
                continue
            try:
                timestamp = datetime.fromisoformat(ended_at)
            except ValueError:
                continue
            if timestamp.tzinfo is None or timestamp < cutoff:
                continue
            events.append(event)
    return events


def active_q1_codes(root: Path, *, now: datetime | None = None) -> tuple[str, ...]:
    mode, lookback, minimum, maximum = _config(root)
    if mode != "active":
        return ()
    reference = now or datetime.now(UTC)
    if reference.tzinfo is None or reference.utcoffset() is None:
        return ()
    cutoff = reference - timedelta(days=lookback)
    repaired_runs: dict[str, set[str]] = defaultdict(set)
    pending: dict[str, set[str]] = defaultdict(set)
    for event in _events(root, cutoff):
        run_id = event.get("run_id")
        if not isinstance(run_id, str):
            continue
        status = event.get("status")
        code = event.get("error_type")
        if status == "failed" and isinstance(code, str) and code in _GUIDANCE:
            pending[run_id].add(code)
        elif status in {"passed", "validated"}:
            repaired_runs[run_id].update(pending.pop(run_id, set()))
    counts: dict[str, int] = defaultdict(int)
    for codes in repaired_runs.values():
        for code in codes:
            counts[code] += 1
    promoted = [code for code, count in counts.items() if count >= minimum]
    promoted.sort(key=lambda code: (-counts[code], code))
    return tuple(promoted[:maximum])


__all__ = ["Q1FailureCode", "active_q1_codes", "guidance_for"]
