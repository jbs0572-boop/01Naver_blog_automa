from __future__ import annotations

from typing import Final

type JSONValue = (
    None | bool | int | float | str | list[JSONValue] | dict[str, JSONValue]
)
type JSONMap = dict[str, JSONValue]


class SchemaError(Exception):
    pass


class ContractError(Exception):
    pass


PIPELINE_VERSION: Final = "workflow-optimized-v1"
SCHEMA_VERSION: Final = "1.0"
STAGES: Final = frozenset(
    {
        "topic-selector",
        "researcher",
        "writer",
        "image-maker",
        "content-assembler",
        "notion-rider",
        "naver-rider",
    }
)
STATUSES: Final = frozenset(
    {"pending", "running", "passed", "validated", "failed", "blocked", "skipped"}
)
STATUS_COMPATIBILITY: Final = {
    "success": "passed",
    "completed": "passed",
    "not-run": "skipped",
}
