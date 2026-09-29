from __future__ import annotations

import csv
import io
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from tools.contract_types import ContractError, JSONMap, JSONValue
from tools.topic_feedback_models import parse_artifact

MAPPING_VERSION: Final = "blog-stats-columns-v1"
_CSV_FIELDS: Final = (
    "mapping_version",
    "capture_id",
    "blog_id",
    "blog_post_id",
    "coverage_start",
    "coverage_end",
    "captured_at",
    "status",
    "views",
    "search_inflow",
    "exposure",
    "average_exposure_rank",
    "demographics_json",
)
_READ_FLAGS: Final = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
_DIRECTORY_FLAGS: Final = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
ROW_FIELDS: Final = frozenset(_CSV_FIELDS[1:-1] + ("demographics",))


@dataclass(frozen=True, slots=True)
class BlogStatsInput:
    encoded: bytes
    rows: tuple[JSONMap, ...]


def read_regular(path: Path, label: str) -> bytes:
    if ".." in path.parts:
        raise ContractError(f"{label} must not be a symlink or unsafe traversal path")
    parts = path.parts[1:] if path.is_absolute() else path.parts
    if not parts:
        raise ContractError(f"{label} is unreadable or unsafe")
    descriptors: list[int] = []
    try:
        parent = os.open(path.anchor if path.is_absolute() else ".", _DIRECTORY_FLAGS)
        descriptors.append(parent)
        for component in parts[:-1]:
            if component == ".":
                continue
            if stat.S_ISLNK(
                os.stat(component, dir_fd=parent, follow_symlinks=False).st_mode
            ):
                raise ContractError(
                    f"{label} must not be a symlink or unsafe traversal path"
                )
            parent = os.open(component, _DIRECTORY_FLAGS, dir_fd=parent)
            descriptors.append(parent)
        if stat.S_ISLNK(
            os.stat(parts[-1], dir_fd=parent, follow_symlinks=False).st_mode
        ):
            raise ContractError(
                f"{label} must not be a symlink or unsafe traversal path"
            )
        descriptor = os.open(parts[-1], _READ_FLAGS, dir_fd=parent)
    except ContractError:
        raise
    except OSError as error:
        raise ContractError(f"{label} is unreadable or unsafe") from error
    finally:
        for parent in reversed(descriptors):
            os.close(parent)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ContractError(f"{label} is unreadable or unsafe")
        with os.fdopen(descriptor, "rb") as handle:
            return handle.read()
    except ContractError:
        os.close(descriptor)
        raise


def json_map(encoded: bytes, label: str) -> JSONMap:
    try:
        value: JSONValue = json.loads(encoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ContractError(f"{label} is invalid") from error
    if not isinstance(value, dict):
        raise ContractError(f"{label} is invalid")
    return value


def load_blog_stats_input(path: Path) -> BlogStatsInput:
    encoded = read_regular(path, "blog stats input")
    if path.suffix.casefold() == ".csv":
        try:
            reader = csv.DictReader(io.StringIO(encoded.decode("utf-8-sig")))
            if tuple(reader.fieldnames or ()) != _CSV_FIELDS:
                raise ContractError("blog stats CSV mapping is invalid")
            rows = tuple(dict(row) for row in reader)
        except (UnicodeDecodeError, csv.Error) as error:
            raise ContractError("blog stats CSV is invalid") from error
        if any(frozenset(row) != frozenset(_CSV_FIELDS) for row in rows):
            raise ContractError("blog stats CSV row is invalid")
        if any(row.get("mapping_version") != MAPPING_VERSION for row in rows):
            raise ContractError("blog stats mapping version is invalid")
        converted: list[JSONMap] = []
        for row in rows:
            demographic_text = row.pop("demographics_json", "")
            _ = row.pop("mapping_version", None)
            try:
                demographics: JSONValue = json.loads(demographic_text or "[]")
            except json.JSONDecodeError as error:
                raise ContractError("blog stats demographic data is invalid") from error
            row["demographics"] = demographics
            converted.append(row)
        return BlogStatsInput(encoded, tuple(converted))
    envelope = json_map(encoded, "blog stats JSON")
    required = frozenset({"schema_version", "mapping_version", "observations"})
    if (
        frozenset(envelope) != required
        or envelope.get("schema_version") != "blog-stats-import-v1"
        or envelope.get("mapping_version") != MAPPING_VERSION
    ):
        raise ContractError("blog stats JSON mapping is invalid")
    raw_rows = envelope.get("observations")
    if not isinstance(raw_rows, list) or any(
        not isinstance(row, dict) for row in raw_rows
    ):
        raise ContractError("blog stats JSON observations are invalid")
    return BlogStatsInput(
        encoded, tuple(row for row in raw_rows if isinstance(row, dict))
    )


def load_publication_artifacts(root: Path) -> tuple[JSONMap, ...]:
    base = root / "metadata" / "publication-links"
    if not base.is_dir() or base.is_symlink():
        raise ContractError("publication links are missing or unsafe")
    paths: list[Path] = []
    for run_dir in sorted(base.iterdir()):
        if run_dir.name.startswith("."):
            continue
        if not run_dir.is_dir() or run_dir.is_symlink():
            raise ContractError("publication links are unsafe")
        for path in sorted(run_dir.iterdir()):
            if path.is_symlink() or not path.is_file() or path.suffix != ".json":
                raise ContractError("publication links are unsafe")
            paths.append(path)
    return tuple(
        parse_artifact(
            json_map(read_regular(path, "publication link"), "publication link")
        ).payload
        for path in paths
    )


def load_blog_stats_history(root: Path, blog_id: str) -> tuple[JSONMap, ...]:
    base = root / "metadata" / "blog-stats" / blog_id
    if not base.exists():
        return ()
    if base.is_symlink() or not base.is_dir():
        raise ContractError("blog stats history is unsafe")
    paths = tuple(base.glob("*/*.json"))
    if any(path.is_symlink() or path.parent.is_symlink() for path in paths):
        raise ContractError("blog stats history is unsafe")
    return tuple(
        parse_artifact(
            json_map(read_regular(path, "blog stats history"), "blog stats history")
        ).payload
        for path in sorted(paths)
    )
