from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from tools.contract_types import JSONMap, JSONValue
from tools.dashboard_data import RunView, StageView, load_runs

TOKEN_FIELDS: Final = (
    "input_tokens",
    "output_tokens",
    "cached_input_tokens",
    "reasoning_output_tokens",
    "cache_write_input_tokens",
)


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(quantile * len(ordered)) - 1)]


def _process_paths(root: Path, run_ids: set[str]) -> list[tuple[str, str, Path]]:
    work_root = root / ".automation" / "work"
    paths: list[tuple[str, str, Path]] = []
    if not work_root.is_dir():
        return paths
    for run_dir in sorted(work_root.iterdir()):
        if run_dir.name not in run_ids or not run_dir.is_dir():
            continue
        for stage_dir in sorted(run_dir.iterdir()):
            if not stage_dir.is_dir():
                continue
            nested = sorted(stage_dir.glob("attempt-*/codex-attempt-*.jsonl"))
            selected = nested or sorted(stage_dir.glob("codex-attempt-*.jsonl"))
            paths.extend((run_dir.name, stage_dir.name, path) for path in selected)
    return paths


def _usage_summary(root: Path, runs: tuple[RunView, ...]) -> JSONMap:
    run_ids = {run.run_id for run in runs}
    paths = _process_paths(root, run_ids)
    totals = dict.fromkeys(TOKEN_FIELDS, 0)
    coverage = dict.fromkeys(TOKEN_FIELDS, 0)
    event_count = 0
    usage_event_count = 0
    missing_usage_events = 0
    malformed_lines = 0
    partial_lines = 0
    observed_runs: set[str] = set()
    stage_attempts: Counter[str] = Counter()
    unknown_fields: Counter[str] = Counter()

    for run_id, stage, path in paths:
        stage_attempts[stage] += 1
        try:
            lines = path.read_bytes().splitlines(keepends=True)
        except OSError:
            malformed_lines += 1
            continue
        for raw in lines:
            if not raw.endswith(b"\n"):
                partial_lines += 1
                continue
            event_count += 1
            try:
                event: JSONValue = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError):
                malformed_lines += 1
                continue
            if not isinstance(event, dict) or event.get("type") != "turn.completed":
                continue
            usage_value = event.get("usage")
            if not isinstance(usage_value, dict):
                missing_usage_events += 1
                continue
            usage_event_count += 1
            observed_runs.add(run_id)
            unknown_fields.update(
                key for key in usage_value if key not in TOKEN_FIELDS
            )
            for field in TOKEN_FIELDS:
                value = usage_value.get(field)
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    totals[field] += value
                    coverage[field] += 1

    fields: JSONMap = {}
    for field in TOKEN_FIELDS:
        count = coverage[field]
        fields[field] = {
            "value": totals[field] if count == usage_event_count and count > 0 else None,
            "observed_events": count,
            "coverage_percent": round(count * 100 / usage_event_count, 1)
            if usage_event_count
            else None,
        }
    input_tokens = fields["input_tokens"]
    cached_tokens = fields["cached_input_tokens"]
    non_cached: int | None = None
    if isinstance(input_tokens, dict) and isinstance(cached_tokens, dict):
        input_value = input_tokens.get("value")
        cached_value = cached_tokens.get("value")
        if isinstance(input_value, int) and isinstance(cached_value, int) and cached_value <= input_value:
            non_cached = input_value - cached_value
    return {
        "process_attempt_count": len(paths),
        "process_attempts_by_stage": dict(stage_attempts),
        "complete_jsonl_records": event_count,
        "malformed_records": malformed_lines,
        "incomplete_final_records": partial_lines,
        "turns_without_usage": missing_usage_events,
        "usage_turns": usage_event_count,
        "runs_with_observed_usage": len(observed_runs),
        "run_usage_coverage_percent": round(len(observed_runs) * 100 / len(runs), 1)
        if runs
        else None,
        "fields": fields,
        "non_cached_input_tokens": non_cached,
        "unknown_usage_fields": dict(unknown_fields),
        "usage_state": "observed" if usage_event_count else "unknown",
        "cached_input_is_included_in_input_tokens": True,
    }


def _stage_summary(runs: tuple[RunView, ...]) -> list[JSONValue]:
    names = dict.fromkeys(stage.name for run in runs for stage in run.stages)
    summaries: list[JSONValue] = []
    for name in names:
        rows = [
            stage
            for run in runs
            for stage in run.stages
            if stage.name == name
        ]
        durations = [stage.duration_ms for stage in rows if stage.duration_ms is not None]
        attempts = [attempt for stage in rows for attempt in stage.attempts]
        summaries.append(
            {
                "stage": name,
                "runs": len(rows),
                "duration_observed_runs": len(durations),
                "p50_duration_ms": _percentile(durations, 0.50),
                "p90_duration_ms": _percentile(durations, 0.90),
                "failed_attempts": sum(attempt.status == "failed" for attempt in attempts),
                "attempts": len(attempts),
                "retry_attempts": sum(max(0, len(stage.attempts) - 1) for stage in rows),
            }
        )
    return summaries


def build_report(root: Path, *, limit: int = 30) -> JSONMap:
    if limit < 1:
        raise ValueError("limit must be greater than zero")
    all_runs = load_runs(root)
    runs = all_runs[:limit]
    status_counts = Counter(run.status for run in runs)
    updated = [run.updated_at for run in runs if run.updated_at]
    return {
        "schema_version": "workflow-performance-report-v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "scope": {"limit": limit, "available_runs": len(all_runs), "included_runs": len(runs)},
        "source_freshness": {"newest_run_updated_at": max(updated) if updated else None},
        "outcomes": dict(status_counts),
        "stages": _stage_summary(runs),
        "usage": _usage_summary(root, runs),
        "model_observations": [
            {
                "stage": stage.name,
                "model": stage.model,
                "reasoning_effort": stage.reasoning_effort,
                "runs": sum(
                    1
                    for run in runs
                    if any(
                        row.name == stage.name
                        and row.model == stage.model
                        and row.reasoning_effort == stage.reasoning_effort
                        for row in run.stages
                    )
                ),
            }
            for stage in _unique_model_observations(runs)
        ],
        "limitations": [
            "Descriptive retained-history aggregates; not a matched benchmark or causal estimate.",
            "Missing token fields remain unknown; totals are null unless every observed usage turn has that field.",
            "Failed and incomplete runs remain in outcome counts; stage percentiles include recorded attempts only.",
        ],
    }


def _unique_model_observations(runs: tuple[RunView, ...]) -> tuple[StageView, ...]:
    observed: dict[tuple[str, str, str], StageView] = {}
    for run in runs:
        for stage in run.stages:
            if stage.model:
                key = (stage.name, stage.model, stage.reasoning_effort or "unknown")
                _ = observed.setdefault(key, stage)
    return tuple(observed.values())


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a read-only workflow performance report.")
    _ = parser.add_argument("--root", type=Path, default=Path.cwd())
    _ = parser.add_argument("--limit", type=int, default=30)
    _ = parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = build_report(args.root, limit=args.limit)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as stream:
            _ = stream.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(f"report created: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
