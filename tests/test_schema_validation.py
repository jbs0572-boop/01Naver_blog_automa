from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.contract_types import JSONValue
from tools.schema_validation import SchemaError, validate_instance


def _write_schema(tmp_path: Path, schema: dict[str, object]) -> Path:
    path = tmp_path / "schema.json"
    _ = path.write_text(json.dumps(schema), encoding="utf-8")
    return path


def test_standard_draft_combinators_are_enforced(tmp_path: Path) -> None:
    conditional_value: JSONValue = {"kind": "x"}
    contains_value: JSONValue = ["x", 3]
    cases: tuple[tuple[dict[str, object], bool, JSONValue], ...] = (
        ({"oneOf": [{"type": "string"}, {"type": "integer"}]}, True, "text"),
        (
            {
                "anyOf": [
                    {"type": "string", "minLength": 2},
                    {"type": "integer", "minimum": 3},
                ]
            },
            True,
            4,
        ),
        ({"allOf": [{"type": "integer"}, {"minimum": 3}]}, True, 4),
        (
            {
                "$defs": {"positive": {"type": "integer", "minimum": 1}},
                "$ref": "#/$defs/positive",
            },
            True,
            2,
        ),
        (
            {
                "if": {"properties": {"kind": {"const": "x"}}, "required": ["kind"]},
                "then": {"required": ["value"]},
            },
            False,
            conditional_value,
        ),
        (
            {"type": "array", "contains": {"type": "integer", "minimum": 3}},
            True,
            contains_value,
        ),
    )
    for index, (schema, valid, value) in enumerate(cases):
        path = tmp_path / f"schema-{index}.json"
        _ = path.write_text(json.dumps(schema), encoding="utf-8")
        if valid:
            validate_instance(value, path)
        else:
            with pytest.raises(SchemaError):
                validate_instance(value, path)


def test_format_checker_requires_timezone_aware_date_time(tmp_path: Path) -> None:
    path = _write_schema(tmp_path, {"type": "string", "format": "date-time"})

    validate_instance("2026-08-27T12:00:00+09:00", path)
    with pytest.raises(SchemaError, match="date-time"):
        validate_instance("2026-08-27T12:00:00", path)


def test_invalid_schema_is_rejected_at_validation_start(tmp_path: Path) -> None:
    path = _write_schema(tmp_path, {"type": "not-a-json-schema-type"})

    with pytest.raises(SchemaError, match="invalid schema"):
        validate_instance("value", path)


def test_validation_errors_have_deterministic_order(tmp_path: Path) -> None:
    path = _write_schema(
        tmp_path,
        {
            "type": "object",
            "required": ["a", "b"],
            "properties": {"a": {"type": "integer"}, "b": {"type": "string"}},
        },
    )

    messages: list[str] = []
    for _ in range(2):
        with pytest.raises(SchemaError) as caught:
            validate_instance({"a": "wrong"}, path)
        messages.append(str(caught.value))
    assert messages[0] == messages[1]
