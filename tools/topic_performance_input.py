from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from tools.contract_types import ContractError, JSONMap, JSONValue
from tools.topic_feedback_models import parse_artifact
from tools.topic_feedback_store_fs import open_directory, open_root, read_file


@dataclass(frozen=True, slots=True)
class Observation:
    payload: JSONMap
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class PerformanceInputs:
    links: tuple[JSONMap, ...]
    observations: dict[str, tuple[Observation, ...]]


def parse_kst_timestamp(value: JSONValue, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("+09:00"):
        raise ContractError(f"{label} must be a KST timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ContractError(f"{label} must be a KST timestamp") from error
    if parsed.utcoffset() != timedelta(hours=9):
        raise ContractError(f"{label} must be a KST timestamp")
    return parsed


def _raw_json(encoded: bytes, label: str) -> JSONMap:
    try:
        value: JSONValue = json.loads(encoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ContractError(f"{label} is invalid") from error
    if not isinstance(value, dict):
        raise ContractError(f"{label} is invalid")
    return value


def _walk(parent_fd: int, depth: int) -> tuple[bytes, ...]:
    output: list[bytes] = []
    for name in sorted(os.listdir(parent_fd)):
        if name.startswith("."):
            continue
        try:
            info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except OSError as error:
            raise ContractError("cohort input tree is unsafe") from error
        if depth == 0:
            if not stat.S_ISREG(info.st_mode) or not name.endswith(".json"):
                raise ContractError("cohort input tree is unsafe")
            output.append(read_file(parent_fd, name))
        else:
            if not stat.S_ISDIR(info.st_mode):
                raise ContractError("cohort input tree is unsafe")
            child = open_directory(parent_fd, name, create=False)
            try:
                output.extend(_walk(child, depth - 1))
            finally:
                os.close(child)
    return tuple(output)


def _files(root: Path, components: tuple[str, ...], depth: int) -> tuple[bytes, ...]:
    root_fd = open_root(root)
    opened: list[int] = []
    try:
        parent = root_fd
        for component in components:
            parent = open_directory(parent, component, create=False)
            opened.append(parent)
        return _walk(parent, depth)
    finally:
        for descriptor in reversed(opened):
            os.close(descriptor)
        os.close(root_fd)


def _links(root: Path, as_of: datetime) -> tuple[JSONMap, ...]:
    selected: dict[str, JSONMap] = {}
    runs: dict[str, str] = {}
    for encoded in _files(root, ("metadata", "publication-links"), 1):
        raw = _raw_json(encoded, "publication link")
        captured_at = parse_kst_timestamp(raw.get("captured_at"), "captured_at")
        published_value = raw.get("published_at")
        published_at = (
            parse_kst_timestamp(published_value, "published_at")
            if published_value is not None
            else None
        )
        if captured_at > as_of or (published_at is not None and published_at > as_of):
            continue
        payload = parse_artifact(raw).payload
        post_id, run_id = payload.get("blog_post_id"), payload.get("run_id")
        if (
            payload.get("schema_version") != "publication-link-v1"
            or payload.get("status") != "mature"
            or not isinstance(post_id, str)
            or not isinstance(run_id, str)
            or payload.get("published_at") is None
        ):
            continue
        digest = str(payload["digest"])
        if post_id in selected and selected[post_id] != payload:
            raise ContractError("publication identity is ambiguous")
        if run_id in runs and runs[run_id] != digest:
            raise ContractError("publication run identity is ambiguous")
        selected[post_id], runs[run_id] = payload, digest
    return tuple(selected[key] for key in sorted(selected))


def load_performance_inputs(root: Path, as_of: datetime) -> PerformanceInputs:
    links = _links(root, as_of)
    by_post = {str(link["blog_post_id"]): link for link in links}
    grouped: dict[str, list[Observation]] = {post_id: [] for post_id in by_post}
    stats_root = root / "metadata" / "blog-stats"
    if stats_root.is_symlink():
        raise ContractError("cohort input tree is unsafe")
    encoded_stats = (
        _files(root, ("metadata", "blog-stats"), 2) if stats_root.exists() else ()
    )
    for encoded in encoded_stats:
        raw = _raw_json(encoded, "blog stats")
        observed_at = parse_kst_timestamp(raw.get("captured_at"), "captured_at")
        if observed_at > as_of:
            continue
        payload = parse_artifact(raw).payload
        if payload.get("schema_version") != "blog-stat-snapshot-v2":
            continue
        post_id = payload.get("blog_post_id")
        if not isinstance(post_id, str) or post_id not in by_post:
            raise ContractError("blog stats publication identity is unknown")
        link = by_post[post_id]
        if (
            payload.get("publication_run_id") != link.get("run_id")
            or payload.get("publication_link_digest") != link.get("digest")
        ):
            raise ContractError("blog stats publication identity mismatch")
        published_at = parse_kst_timestamp(link.get("published_at"), "published_at")
        if observed_at < published_at:
            raise ContractError("blog stats predates publication")
        grouped[post_id].append(Observation(payload, observed_at))
    observations = {
        post_id: tuple(
            sorted(
                values,
                key=lambda item: (
                    item.observed_at,
                    str(item.payload["coverage_end"]),
                    str(item.payload["capture_id"]),
                    str(item.payload["digest"]),
                ),
            )
        )
        for post_id, values in grouped.items()
    }
    return PerformanceInputs(links, observations)
