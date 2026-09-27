from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from tools.contract_types import JSONMap, JSONValue
from tools.schema_validation import SchemaError, validate_instance


def _write_schema(tmp_path: Path, schema: JSONMap) -> Path:
    path = tmp_path / "schema.json"
    _ = path.write_text(json.dumps(schema), encoding="utf-8")
    return path


def test_standard_draft_combinators_are_enforced(tmp_path: Path) -> None:
    conditional_value: JSONValue = {"kind": "x"}
    contains_value: JSONValue = ["x", 3]
    cases: tuple[tuple[JSONMap, bool, JSONValue], ...] = (
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


def test_unchanged_schema_is_checked_once_and_reloaded_after_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write_schema(tmp_path, {"type": "string"})
    original_check_schema = Draft202012Validator.check_schema
    checked: list[JSONMap] = []

    def count_check_schema(schema: JSONMap) -> None:
        checked.append(schema)
        original_check_schema(schema)

    monkeypatch.setattr(
        Draft202012Validator,
        "check_schema",
        staticmethod(count_check_schema),
    )

    validate_instance("first", path)
    validate_instance("second", path)
    assert len(checked) == 1

    _ = path.write_text(json.dumps({"type": "integer"}), encoding="utf-8")
    validate_instance(3, path)
    assert len(checked) == 2


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


def test_stage_result_schema_is_strict_for_structured_output() -> None:
    schema_path = Path(__file__).parents[1] / "schemas" / "stage-result.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    artifact_pattern = schema["properties"]["artifacts"]["items"].get("pattern")
    assert artifact_pattern is None or "(?" not in artifact_pattern

    def visit(value: JSONValue, path: str) -> None:
        if isinstance(value, dict):
            if value.get("type") == "object":
                assert value.get("additionalProperties") is False, path
                properties = value.get("properties")
                required = value.get("required")
                if isinstance(properties, dict) and isinstance(required, list):
                    assert set(required) == set(properties), path
            for key, child in value.items():
                visit(child, f"{path}.{key}")
            return
        if isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, f"{path}[{index}]")

    visit(schema, "$")
