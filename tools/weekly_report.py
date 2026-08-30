from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
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


def write_weekly_report(root: Path, as_of: datetime) -> Path:
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
        max(attempt - 1, 0)
        for event in events
        for attempt in [event.get("attempt")]
        if isinstance(attempt, int)
    )
    durations = [
        (
            datetime.fromisoformat(str(event["ended_at"]))
            - datetime.fromisoformat(str(event["started_at"]))
        ).total_seconds()
        for event in events
        if isinstance(event.get("started_at"), str)
        and isinstance(event.get("ended_at"), str)
    ]
    report_date = as_of.astimezone(ZoneInfo("Asia/Seoul")).date().isoformat()
    path = root / ".automation" / "reports" / f"weekly-{report_date}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
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
    _ = path.write_text(text, encoding="utf-8")
    return path


__all__ = ["write_weekly_report"]
