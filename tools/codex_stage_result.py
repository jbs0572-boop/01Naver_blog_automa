from __future__ import annotations

from tools.codex_stage_error import StageExecutionError
from tools.contract_types import JSONMap, JSONValue


def result_object(value: JSONValue, stage: str) -> JSONMap:
    if not isinstance(value, dict):
        raise StageExecutionError(stage, "structured response must be an object")
    return value


def declared_artifacts(raw: JSONMap, stage: str) -> tuple[str, ...]:
    values = raw.get("artifacts")
    if not isinstance(values, list):
        raise StageExecutionError(stage, "artifacts must be a string array")
    declared: list[str] = []
    for value in values:
        if not isinstance(value, str):
            raise StageExecutionError(stage, "artifacts must be a string array")
        declared.append(value)
    return tuple(declared)


__all__ = ["declared_artifacts", "result_object"]
