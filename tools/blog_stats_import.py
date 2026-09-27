from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from tools import blog_stats_input
from tools.contract_types import ContractError, JSONMap, JSONValue
from tools.topic_feedback_batch_store import (
    BatchFault,
    BatchItem,
    TopicFeedbackBatchStore,
)
from tools.topic_feedback_models import compute_digest, parse_artifact
from tools.topic_feedback_redaction import (
    ForbiddenFeedbackFieldError,
    sanitize_feedback_payload,
)
from tools.topic_feedback_store import SnapshotLocation


@dataclass(frozen=True, slots=True)
class BlogStatsImportRequest:
    input_path: Path
    owner_config_path: Path
    root: Path
    batch_fault: BatchFault | None = None


@dataclass(frozen=True, slots=True)
class BlogStatsImportResult:
    paths: tuple[str, ...]
    snapshots: tuple[JSONMap, ...]
    source_digest: str


def _owners(path: Path) -> frozenset[str]:
    value = blog_stats_input.json_map(
        blog_stats_input.read_regular(path, "owner config"), "owner config"
    )
    try:
        _ = sanitize_feedback_payload(value)
    except ForbiddenFeedbackFieldError as error:
        raise ContractError("owner config violates redaction policy") from error
    if (
        frozenset(value) != frozenset({"schema_version", "allowed_blog_ids"})
        or value.get("schema_version") != "blog-stats-owner-v1"
    ):
        raise ContractError("owner config is invalid")
    raw_ids = value.get("allowed_blog_ids")
    if not isinstance(raw_ids, list):
        raise ContractError("owner config is invalid")
    owners = frozenset(item for item in raw_ids if isinstance(item, str) and item)
    if len(owners) != len(raw_ids) or not owners:
        raise ContractError("owner config is invalid")
    for owner in owners:
        _ = SnapshotLocation.blog_stat(owner, "2000-01-01", "probe")
    return owners


def _text(row: JSONMap, field: str) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"blog stats {field} is invalid")
    return value


def _day(row: JSONMap, field: str) -> date:
    try:
        return date.fromisoformat(_text(row, field))
    except ValueError as error:
        raise ContractError("blog stats coverage interval is invalid") from error


def _number(row: JSONMap, field: str, *, optional: bool) -> int | float | None:
    value = row.get(field)
    if value is None or value == "":
        if optional:
            return None
        raise ContractError(f"blog stats {field} is invalid")
    if isinstance(value, bool):
        raise ContractError(f"blog stats {field} is invalid")
    try:
        number = float(value) if isinstance(value, str) else value
    except ValueError as error:
        raise ContractError(f"blog stats {field} is invalid") from error
    if not isinstance(number, int | float) or not math.isfinite(number) or number < 0:
        raise ContractError(f"blog stats {field} is invalid")
    if field == "average_exposure_rank" and number == 0:
        raise ContractError("blog stats average_exposure_rank is invalid")
    return int(number) if float(number).is_integer() else float(number)


def _demographics(value: JSONValue) -> list[JSONValue]:
    if not isinstance(value, list):
        raise ContractError("blog stats demographic data is invalid")
    output: list[JSONValue] = []
    for bucket in value:
        if not isinstance(bucket, dict) or frozenset(bucket) != frozenset(
            {"dimension", "bucket", "count"}
        ):
            raise ContractError("blog stats demographic data is invalid")
        dimension, name, count = (
            bucket.get("dimension"),
            bucket.get("bucket"),
            bucket.get("count"),
        )
        if (
            dimension not in {"age", "gender"}
            or not isinstance(name, str)
            or not name
            or isinstance(count, bool)
            or not isinstance(count, int)
        ):
            raise ContractError("blog stats demographic data is invalid")
        if count < 5:
            raise ContractError("blog stats demographic privacy violation")
        output.append({"dimension": dimension, "bucket": name, "count": count})
    return output


def _publication_links(root: Path) -> dict[str, JSONMap]:
    links: dict[str, JSONMap] = {}
    for payload in blog_stats_input.load_publication_artifacts(root):
        post_id = payload.get("blog_post_id")
        if (
            payload.get("schema_version") != "publication-link-v1"
            or payload.get("status") != "mature"
            or not isinstance(post_id, str)
        ):
            continue
        if post_id in links:
            raise ContractError("publication post identity is ambiguous")
        links[post_id] = payload
    return links


def _payload(
    row: JSONMap, owners: frozenset[str], links: dict[str, JSONMap], source_digest: str
) -> JSONMap:
    try:
        _ = sanitize_feedback_payload(row)
    except ForbiddenFeedbackFieldError as error:
        raise ContractError("blog stats payload violates redaction policy") from error
    if frozenset(row) != blog_stats_input.ROW_FIELDS:
        raise ContractError("blog stats row contains unknown fields")
    blog_id, post_id = _text(row, "blog_id"), _text(row, "blog_post_id")
    if blog_id not in owners:
        raise ContractError("blog stats owner mismatch")
    link = links.get(post_id)
    if link is None:
        raise ContractError("blog stats unknown publication")
    start, end = _day(row, "coverage_start"), _day(row, "coverage_end")
    if start > end:
        raise ContractError("blog stats coverage interval is invalid")
    captured_at = _text(row, "captured_at")
    try:
        captured = datetime.fromisoformat(captured_at)
    except ValueError as error:
        raise ContractError("blog stats captured_at timezone is invalid") from error
    if (
        captured.utcoffset() is None
        or not captured_at.endswith("+09:00")
        or captured.date() < end
    ):
        raise ContractError("blog stats captured_at timezone is invalid")
    status = _text(row, "status")
    if status not in {"mature", "delayed"}:
        raise ContractError("blog stats status is invalid")
    metrics = {
        name: _number(row, name, optional=name in {"exposure", "average_exposure_rank"})
        for name in ("views", "search_inflow", "exposure", "average_exposure_rank")
    }
    link_digest = _text(link, "digest")
    limitations: list[JSONValue] = [
        "owner_export_only",
        "manual_import",
        "aggregate_demographics_only",
    ]
    input_digests: list[JSONValue] = [source_digest, link_digest]
    missing_fields: list[JSONValue] = []
    for name in sorted(metrics):
        if metrics[name] is None:
            missing_fields.append(name)
    payload: JSONMap = {
        "schema_version": "blog-stat-snapshot-v2",
        "captured_at": captured_at,
        "as_of_date": end.isoformat(),
        "timezone": "Asia/Seoul",
        "limitations": limitations,
        "input_digests": input_digests,
        "missing_fields": missing_fields,
        "status": status,
        "digest": "",
        "mapping_version": blog_stats_input.MAPPING_VERSION,
        "capture_id": _text(row, "capture_id"),
        "blog_id": blog_id,
        "blog_post_id": post_id,
        "publication_run_id": _text(link, "run_id"),
        "publication_link_digest": link_digest,
        "coverage_start": start.isoformat(),
        "coverage_end": end.isoformat(),
        **metrics,
        "demographics": _demographics(row.get("demographics")),
    }
    payload["digest"] = compute_digest(payload)
    return parse_artifact(payload).payload


def _prior(root: Path, blog_id: str) -> tuple[JSONMap, ...]:
    return blog_stats_input.load_blog_stats_history(root, blog_id)


def _monotonic(rows: tuple[JSONMap, ...], prior: tuple[JSONMap, ...]) -> None:
    metrics = ("views", "search_inflow", "exposure")
    combined = sorted(
        (*prior, *rows),
        key=lambda item: (
            str(item["coverage_end"]),
            str(item["captured_at"]),
            str(item["capture_id"]),
        ),
    )
    latest: dict[tuple[str, str], int | float] = {}
    for row in combined:
        post_id = str(row["blog_post_id"])
        for metric in metrics:
            value = row.get(metric)
            if isinstance(value, int | float) and not isinstance(value, bool):
                key = (post_id, metric)
                if key in latest and value < latest[key]:
                    raise ContractError("blog stats cumulative metric decreased")
                latest[key] = value


def import_blog_stats(request: BlogStatsImportRequest) -> BlogStatsImportResult:
    owners = _owners(request.owner_config_path)
    source = blog_stats_input.load_blog_stats_input(request.input_path)
    encoded, rows = source.encoded, source.rows
    if not rows:
        raise ContractError("blog stats batch is empty")
    source_digest = "sha256:" + hashlib.sha256(encoded).hexdigest()
    links = _publication_links(request.root)
    payloads = tuple(_payload(row, owners, links, source_digest) for row in rows)
    if len({str(payload["capture_id"]) for payload in payloads}) != len(payloads):
        raise ContractError("blog stats capture identity is duplicated")
    for owner in {str(payload["blog_id"]) for payload in payloads}:
        _monotonic(
            tuple(payload for payload in payloads if payload["blog_id"] == owner),
            _prior(request.root, owner),
        )
    items = tuple(
        BatchItem(
            SnapshotLocation.blog_stat(
                str(payload["blog_id"]),
                str(payload["as_of_date"]),
                str(payload["capture_id"]),
            ),
            parse_artifact(payload),
        )
        for payload in payloads
    )
    with TopicFeedbackBatchStore(request.root, request.batch_fault) as store:
        stored = store.store(items)
    return BlogStatsImportResult(
        tuple(str(snapshot.path) for snapshot in stored), payloads, source_digest
    )
