from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from tools.codex_stage_error import failure_policy
from tools.contract_types import JSONMap, JSONValue
from tools.dashboard_usage import usage_by_stage, usage_for

STAGE_ORDER: Final[tuple[str, ...]] = (
    "topic-selector",
    "researcher",
    "writer",
    "image-maker",
    "content-assembler",
    "notion-rider",
    "naver-rider",
)


@dataclass(frozen=True, slots=True)
class AttemptView:
    attempt: int
    status: str
    started_at: str | None
    ended_at: str | None
    duration_ms: float | None
    error_type: str | None
    message: str | None
    next_action: str | None
    retryable: bool | None


@dataclass(frozen=True, slots=True)
class StageView:
    name: str
    status: str
    started_at: str | None = None
    ended_at: str | None = None
    duration_ms: float | None = None
    last_duration_ms: float | None = None
    attempt: int | None = None
    total_attempts: int | None = None
    message: str | None = None
    error_type: str | None = None
    next_action: str | None = None
    model: str | None = None
    reasoning_effort: str | None = None
    usage: JSONMap | None = None
    attempts: tuple[AttemptView, ...] = ()


@dataclass(frozen=True, slots=True)
class RunView:
    run_id: str
    topic_id: str
    keyword: str
    topic_source: str | None
    historical: bool
    status: str
    started_at: str | None
    ended_at: str | None
    updated_at: str | None
    log_path: str
    q1: str
    q2: str
    error: str | None
    stages: tuple[StageView, ...]
    usage: JSONMap | None = None


def _text(value: JSONValue, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _map(value: JSONValue) -> JSONMap:
    return value if isinstance(value, dict) else {}


def _read_events(path: Path) -> tuple[list[JSONMap], str | None]:
    events: list[JSONMap] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                value: JSONValue = json.loads(line)
                events.append(_map(value))
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError) as error:
        return [], f"읽기 실패: {type(error).__name__}"
    return events, None


def _state_for(root: Path, run_id: str) -> JSONMap:
    path = root / ".automation" / "state" / f"{run_id}.json"
    try:
        value: JSONValue = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return _map(value)


def _number(value: JSONValue) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0 else None


def _attempt_view(item: JSONMap) -> AttemptView:
    raw_attempt = item.get("attempt")
    error_type = _text(item.get("error_type")) or None
    policy = failure_policy(error_type)
    retryable = item.get("retryable")
    next_action = _text(item.get("next_action")) or (
        policy.next_action if policy is not None else None
    )
    return AttemptView(
        attempt=raw_attempt if isinstance(raw_attempt, int) and not isinstance(raw_attempt, bool) else 0,
        status=_text(item.get("status"), "pending"),
        started_at=_text(item.get("started_at")) or None,
        ended_at=_text(item.get("ended_at")) or None,
        duration_ms=_number(item.get("duration_ms")),
        error_type=error_type,
        message=_text(item.get("error_message_safe")) or None,
        next_action=next_action,
        retryable=retryable if isinstance(retryable, bool) else policy.retryable if policy is not None else None,
    )


def _attempt_number(item: JSONMap) -> int:
    value = item.get("attempt")
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _duration(item: JSONMap) -> float | None:
    recorded = _number(item.get("duration_ms"))
    if recorded is not None or item.get("status") != "running":
        return recorded
    started_at = _text(item.get("started_at"))
    if not started_at:
        return None
    try:
        started = datetime.fromisoformat(started_at)
    except ValueError:
        return None
    return max(0.0, (datetime.now(UTC) - started.astimezone(UTC)).total_seconds() * 1000)


def _stage_views(
    events: list[JSONMap], usage: dict[str, JSONMap], state_stages: JSONMap
) -> tuple[StageView, ...]:
    grouped: dict[str, list[JSONMap]] = {}
    for item in events:
        if item.get("event_type") == "stage" and isinstance(item.get("stage"), str):
            grouped.setdefault(_text(item["stage"]), []).append(item)
    views: list[StageView] = []
    for stage in STAGE_ORDER:
        items = grouped.get(stage)
        if items is None:
            items = []
        items.sort(key=_attempt_number)
        item = items[-1] if items else {}
        attempts = tuple(_attempt_view(value) for value in items)
        current_duration = _duration(item)
        durations = [value.duration_ms for value in attempts[:-1] if value.duration_ms is not None]
        if current_duration is not None:
            durations.append(current_duration)
        latest_attempt = attempts[-1] if attempts else None
        log_status = _text(item.get("status"))
        state_status = _text(state_stages.get(stage)) or None
        status = state_status or log_status or "pending"
        compatible_terminal_status = {status, log_status} <= {"passed", "validated"}
        current_item = item if not state_status or state_status == log_status or compatible_terminal_status else {}
        stage_usage = usage.get(stage)
        if stage_usage is not None and status in {"passed", "validated", "failed", "skipped"}:
            stage_usage = {**stage_usage, "usage_state": "final"}
        views.append(
            StageView(
                name=stage,
                status=status,
                started_at=_text(items[0].get("started_at")) or None if items else None,
                ended_at=_text(current_item.get("ended_at")) or None,
                duration_ms=sum(durations) if durations else None,
                last_duration_ms=current_duration,
                attempt=latest_attempt.attempt if latest_attempt is not None else None,
                total_attempts=len(attempts) or None,
                message=_text(current_item.get("error_message_safe")) or None,
                error_type=_text(current_item.get("error_type")) or None,
                next_action=latest_attempt.next_action if latest_attempt is not None else None,
                model=_text(current_item.get("model")) or None,
                reasoning_effort=_text(current_item.get("reasoning_effort")) or None,
                usage=stage_usage,
                attempts=attempts,
            )
        )
    return tuple(views)


def _gate(events: list[JSONMap], key: str) -> str:
    for item in reversed(events):
        quality = _map(item.get("quality"))
        value = quality.get(key)
        if isinstance(value, str):
            return value
        if item.get("stage") == key and item.get("status") == "passed":
            return "passed"
    return "not_recorded"


def _run_view(root: Path, path: Path) -> RunView:
    events, read_error = _read_events(path)
    first = events[0] if events else {}
    run_id = _text(first.get("run_id"), path.stem)
    state = _state_for(root, run_id)
    stage_usage = usage_by_stage(root, run_id)
    stages = _stage_views(events, stage_usage, _map(state.get("stages")))
    status = _text(state.get("status"))
    if not status:
        stage_statuses = {stage.status for stage in stages}
        status = "failed" if read_error or "failed" in stage_statuses else "passed"
    error = _text(state.get("message")) or read_error
    explicit_topic_source = _text(state.get("topic_source"))
    auto_topic = state.get("auto_topic")
    topic_source = explicit_topic_source or (
        "auto_selected"
        if auto_topic is True
        else "user_defined"
        if auto_topic is False
        else None
    )
    started_values = [stage.started_at for stage in stages if stage.started_at]
    ended_values = [stage.ended_at for stage in stages if stage.ended_at]
    run_usage = usage_for(root, run_id)
    if run_usage is not None and status in {"passed", "draft_saved", "failed", "cancelled"}:
        run_usage = {**run_usage, "usage_state": "final"}
    return RunView(
        run_id=run_id,
        topic_id=_text(state.get("topic_id"), _text(first.get("topic_id"), "-")),
        keyword=_text(state.get("keyword"), _text(first.get("keyword"), "자동 주제")),
        topic_source=topic_source,
        historical=topic_source is None,
        status=status,
        started_at=min(started_values) if started_values else None,
        ended_at=max(ended_values) if ended_values and status in {"passed", "draft_saved", "failed", "cancelled"} else None,
        updated_at=_text(state.get("updated_at"), _text(first.get("ended_at"))) or None,
        log_path=path.relative_to(root).as_posix(),
        q1=_gate(events, "q1"),
        q2=_gate(events, "q2"),
        error=error,
        stages=stages,
        usage=run_usage,
    )


def run_view(root: Path, path: Path) -> RunView:
    return _run_view(root, path)


def load_runs(root: Path) -> tuple[RunView, ...]:
    paths = sorted({*root.glob("runs/*.jsonl"), *root.glob(".automation/logs/*.jsonl")})
    by_run: dict[str, RunView] = {}
    for path in paths:
        candidate = _run_view(root, path)
        current = by_run.get(candidate.run_id)
        if current is None or (candidate.updated_at or "") >= (current.updated_at or ""):
            by_run[candidate.run_id] = candidate
    return tuple(sorted(by_run.values(), key=lambda item: item.updated_at or "", reverse=True))


def snapshot(root: Path) -> JSONMap:
    runs = load_runs(root)
    counts: dict[str, int] = {}
    for run in runs:
        counts[run.status] = counts.get(run.status, 0) + 1
    status_counts: JSONMap = {key: value for key, value in counts.items()}
    summary: JSONMap = {"total": len(runs), "by_status": status_counts}
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "root": str(root),
        "summary": summary,
        "runs": [asdict(run) for run in runs],
    }


__all__ = ["load_runs", "run_view", "snapshot"]
