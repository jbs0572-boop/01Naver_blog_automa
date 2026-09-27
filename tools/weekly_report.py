from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import isfinite
from pathlib import Path
from zoneinfo import ZoneInfo

from tools.contract_types import ContractError, JSONMap
from tools.log_contract import read_events


def _window(as_of: datetime) -> tuple[datetime, datetime]:
    local = as_of.astimezone(ZoneInfo("Asia/Seoul"))
    start = local - timedelta(days=local.weekday())
    beginning = start.replace(hour=0, minute=0, second=0, microsecond=0)
    ending = beginning + timedelta(days=7)
    return beginning.astimezone(UTC), ending.astimezone(UTC)


def _events(root: Path, start: datetime, end: datetime) -> list[JSONMap]:
    paths = sorted((root / ".automation" / "logs").glob("*.jsonl"))
    paths.extend(sorted((root / "runs").glob("*.jsonl")))
    selected: list[JSONMap] = []
    for path in paths:
        for event in read_events(path):
            value = event.get("ended_at") or event.get("confirmed_at")
            if not isinstance(value, str):
                continue
            try:
                timestamp = datetime.fromisoformat(value).astimezone(UTC)
            except ValueError as error:
                raise ContractError(f"invalid event timestamp in {path}") from error
            if start <= timestamp < end:
                selected.append(event)
    return selected


def _duration_seconds(event: JSONMap) -> float:
    if event.get("telemetry_version") == 2:
        value = event.get("duration_ms")
        if (
            not isinstance(value, int | float)
            or isinstance(value, bool)
            or not isfinite(value)
            or value < 0
        ):
            raise ContractError("invalid telemetry-v2 duration_ms")
        return float(value) / 1000.0
    return (
        datetime.fromisoformat(str(event["ended_at"]))
        - datetime.fromisoformat(str(event["started_at"]))
    ).total_seconds()


def render_weekly_report(root: Path, as_of: datetime) -> tuple[str, bytes]:
    start, end = _window(as_of)
    events = _events(root, start, end)
    statuses = Counter(
        str(event["status"]) for event in events if isinstance(event.get("status"), str)
    )
    failures = Counter(
        str(event["stage"])
        for event in events
        if event.get("status") in {"failed", "blocked"}
        and isinstance(event.get("stage"), str)
    )
    retries = sum(
        (
            int(attempt > 1)
            if event.get("telemetry_version") == 2
            else max(attempt - 1, 0)
        )
        for event in events
        for attempt in [event.get("attempt")]
        if isinstance(attempt, int) and not isinstance(attempt, bool)
    )
    durations = [
        _duration_seconds(event)
        for event in events
        if isinstance(event.get("started_at"), str)
        and isinstance(event.get("ended_at"), str)
    ]
    report_date = as_of.astimezone(ZoneInfo("Asia/Seoul")).date().isoformat()
    text = "\n".join(
        [
            f"# Weekly workflow report ({report_date})",
            "",
            f"- window: {start.isoformat()} to {end.isoformat()}",
            f"- events: {len(events)}",
            f"- statuses: {dict(sorted(statuses.items()))}",
            f"- failed_or_blocked_by_stage: {dict(sorted(failures.items()))}",
            f"- retries: {retries}",
            f"- stage_duration_seconds: {sum(durations):.3f}",
            "",
        ]
    )
    return report_date, text.encode()


@dataclass(frozen=True, slots=True)
class WeeklyReportRevision:
    root: Path
    report_date: str
    encoded: bytes
    digest: str


def weekly_report_path(revision: WeeklyReportRevision) -> Path:
    base = (
        revision.root
        / ".automation"
        / "reports"
        / f"weekly-{revision.report_date}.md"
    )
    if not base.exists() or (
        base.is_file() and base.read_bytes() == revision.encoded
    ):
        return base
    return base.with_name(
        f"weekly-{revision.report_date}-{revision.digest[7:19]}.md"
    )


def write_weekly_report(root: Path, as_of: datetime) -> Path:
    report_date, encoded = render_weekly_report(root, as_of)
    digest = "sha256:" + hashlib.sha256(encoded).hexdigest()
    path = weekly_report_path(WeeklyReportRevision(root, report_date, encoded, digest))
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as handle:
            _ = handle.write(encoded)
    except FileExistsError:
        if path.is_symlink() or path.read_bytes() != encoded:
            raise ContractError("weekly report is append-only") from None
    return path


__all__ = [
    "WeeklyReportRevision",
    "render_weekly_report",
    "weekly_report_path",
    "write_weekly_report",
]
