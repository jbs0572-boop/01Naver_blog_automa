from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Final

from tools.contract_types import JSONMap, JSONValue

USAGE_KEYS: Final = ("input_tokens", "output_tokens", "cached_input_tokens")


@dataclass(slots=True)
class _FileUsageState:
    """Mutable cursor retained so dashboard polling only reads appended bytes."""

    identity: tuple[int, int, int, int]
    offset: int = 0
    remainder: str = ""
    totals: dict[str, int] = field(
        default_factory=lambda: {key: 0 for key in USAGE_KEYS}
    )
    turns: int = 0
    observed_at: str | None = None


_CACHE: dict[Path, _FileUsageState] = {}
_CACHE_LOCK = Lock()


def _valid_usage(value: JSONValue) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    parsed: dict[str, int] = {}
    for key in USAGE_KEYS:
        item = value.get(key, 0)
        if type(item) is not int or item < 0:
            return None
        parsed[key] = item
    return parsed


def _consume_line(state: _FileUsageState, line: str) -> None:
    try:
        event: JSONValue = json.loads(line)
    except json.JSONDecodeError:
        return
    if not isinstance(event, dict) or event.get("type") != "turn.completed":
        return
    usage = _valid_usage(event.get("usage"))
    if usage is None:
        return
    for key, value in usage.items():
        state.totals[key] += value
    state.turns += 1
    state.observed_at = datetime.now(UTC).isoformat()


def _usage_from(path: Path) -> JSONMap | None:
    try:
        metadata = path.stat()
        identity = (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_mtime_ns,
            metadata.st_ctime_ns,
        )
        with _CACHE_LOCK:
            state = _CACHE.get(path)
            if (
                state is None
                or state.identity[:2] != identity[:2]
                or metadata.st_size < state.offset
                or (metadata.st_size == state.offset and state.identity != identity)
            ):
                state = _FileUsageState(identity)
                _CACHE[path] = state
            else:
                state.identity = identity
            with path.open(encoding="utf-8") as stream:
                _ = stream.seek(state.offset)
                appended = stream.read()
                state.offset = stream.tell()
            material = state.remainder + appended
            lines = material.split("\n")
            state.remainder = lines.pop()
            for line in lines:
                if line:
                    _consume_line(state, line)
            if state.remainder:
                try:
                    json.loads(state.remainder)
                except json.JSONDecodeError:
                    pass
                else:
                    _consume_line(state, state.remainder)
                    state.remainder = ""
            if state.turns == 0:
                return None
            return {
                **state.totals,
                "total_tokens": state.totals["input_tokens"]
                + state.totals["output_tokens"],
                "recorded_turns": state.turns,
                "usage_state": "observed",
                "usage_observed_at": state.observed_at,
            }
    except (OSError, UnicodeError):
        return None


def usage_by_stage(root: Path, run_id: str) -> dict[str, JSONMap]:
    if re.fullmatch(r"RUN-[A-Za-z0-9_-]+", run_id) is None:
        return {}
    work = root / ".automation" / "work" / run_id
    result: dict[str, JSONMap] = {}
    if not work.is_dir():
        return result
    for stage_dir in (path for path in work.iterdir() if path.is_dir()):
        nested = sorted(stage_dir.glob("attempt-*/codex-attempt-*.jsonl"))
        paths = nested or sorted(stage_dir.glob("codex-attempt-*.jsonl"))
        totals = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cached_input_tokens": 0,
            "total_tokens": 0,
            "recorded_turns": 0,
        }
        observed_at: str | None = None
        process_attempts = 0
        for path in paths:
            if not path.resolve().is_relative_to(work.resolve()):
                continue
            usage = _usage_from(path)
            if usage is None:
                continue
            for key in totals:
                value = usage.get(key)
                if isinstance(value, int):
                    totals[key] += value
            candidate = usage.get("usage_observed_at")
            if isinstance(candidate, str) and (observed_at is None or candidate > observed_at):
                observed_at = candidate
            process_attempts += 1
        if process_attempts:
            result[stage_dir.name] = {
                **totals,
                "process_attempts": process_attempts,
                "usage_state": "observed",
                "usage_observed_at": observed_at,
            }
    return result


def usage_for(root: Path, run_id: str) -> JSONMap | None:
    stages = usage_by_stage(root, run_id)
    if not stages:
        return None
    keys = (
        "input_tokens",
        "output_tokens",
        "cached_input_tokens",
        "total_tokens",
        "recorded_turns",
    )
    observed = tuple(
        value
        for stage in stages.values()
        if isinstance((value := stage.get("usage_observed_at")), str)
    )
    return {
        **{
            key: sum(
                value
                for stage in stages.values()
                if isinstance((value := stage.get(key)), int)
            )
            for key in keys
        },
        "usage_state": "observed",
        "usage_observed_at": max(observed) if observed else None,
    }


__all__ = ["usage_by_stage", "usage_for"]
