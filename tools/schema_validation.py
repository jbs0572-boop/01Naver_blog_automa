from __future__ import annotations

# pyright: reportAny=false, reportUnknownMemberType=false
import json
from collections.abc import Iterable
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Final, Protocol

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError as JsonSchemaError
from jsonschema.exceptions import ValidationError

from tools.contract_types import JSONMap, JSONValue, SchemaError

SCHEMA_PATH: Final = (
    Path(__file__).resolve().parents[1] / "schemas" / "workflow-contract.schema.json"
)
FORMAT_CHECKER: Final = FormatChecker()
__all__ = ["FORMAT_CHECKER", "SCHEMA_PATH", "SchemaError", "validate_instance"]


def _timezone_aware_datetime(value: object) -> bool:
    if not isinstance(value, str):
        raise TypeError("date-time must be a string")
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("date-time must include a timezone")
    return True


_ = FORMAT_CHECKER.checks("date-time", raises=(TypeError, ValueError))(
    _timezone_aware_datetime
)


class _ValidatorProtocol(Protocol):
    def iter_errors(self, instance: JSONValue) -> Iterable[ValidationError]: ...


def _map(value: JSONValue, label: str) -> JSONMap:
    if not isinstance(value, dict):
        raise SchemaError(f"schema object required: {label}")
    return value


def _load(path: Path) -> JSONValue:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as error:
        raise SchemaError(f"could not load schema: {path}") from error


@lru_cache(maxsize=128)
def _validator_for_file(
    path: Path,
    inode: int,
    modified_ns: int,
    changed_ns: int,
    size: int,
) -> _ValidatorProtocol:
    _ = (inode, modified_ns, changed_ns, size)
    schema = _map(_load(path), str(path))
    try:
        Draft202012Validator.check_schema(schema)
    except JsonSchemaError as error:
        raise SchemaError(f"invalid schema: {error.message}") from error
    return Draft202012Validator(schema, format_checker=FORMAT_CHECKER)


def _validator_for(path: Path) -> _ValidatorProtocol:
    resolved = path.resolve()
    try:
        stat = resolved.stat()
    except OSError as error:
        raise SchemaError(f"could not load schema: {resolved}") from error
    return _validator_for_file(
        resolved,
        stat.st_ino,
        stat.st_mtime_ns,
        stat.st_ctime_ns,
        stat.st_size,
    )


def _path(parts: Iterable[object]) -> tuple[str, ...]:
    return tuple(str(part) for part in parts)


def _error_key(
    error: ValidationError,
) -> tuple[tuple[str, ...], tuple[str, ...], str, str]:
    return (
        _path(error.absolute_path),
        _path(error.absolute_schema_path),
        str(error.validator),
        error.message,
    )


def validate_instance(value: JSONValue, schema_path: Path = SCHEMA_PATH) -> None:
    validator = _validator_for(schema_path)
    errors = sorted(validator.iter_errors(value), key=_error_key)
    if errors:
        details = "; ".join(
            f"{tuple(error.absolute_path) or '$'}: {error.message}"
            for error in errors[:5]
        )
        raise SchemaError(details)
