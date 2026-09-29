from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Final, Protocol, cast, final
from zoneinfo import ZoneInfo

from tools.contract_types import ContractError, JSONMap, JSONValue
from tools.dashboard_manual_models import ManualBatchView, ManualRunView
from tools.dashboard_snapshot_cache import RunSnapshotCache

KST = ZoneInfo("Asia/Seoul")
TASK_STATUSES: Final[frozenset[str]] = frozenset({"active", "attention", "history", "queued", "running", "cancelling", "awaiting_user_confirmation", "ready_for_naver", "local-only", "blocked", "failed", "cancelled", "draft_saved"})
RUN_TERMINAL_STATUSES: Final[frozenset[str]] = frozenset({"awaiting_user_confirmation", "ready_for_naver", "local-only", "blocked", "failed", "cancelled", "draft_saved"})


def effective_status(child: ManualRunView | None, run: JSONMap | None = None) -> str:
    run_status = str((run or {}).get("status") or "")
    if child is not None:
        if child.status in {"cancelling", "cancelled"}:
            return child.status
        if child.active_action is not None and child.status in {"queued", "running"}:
            return child.status
        if child.result_status:
            return child.result_status
        if run_status in RUN_TERMINAL_STATUSES:
            return run_status
        return child.status
    return run_status or "not_recorded"


def _duration(started: str | None, ended: str | None, now: datetime) -> int | None:
    if not started:
        return None
    try:
        begin = datetime.fromisoformat(started).astimezone(UTC)
        finish = datetime.fromisoformat(ended).astimezone(UTC) if ended else now
    except (TypeError, ValueError):
        return None
    return max(0, int((finish - begin).total_seconds()))


def _date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).astimezone(KST).date().isoformat()
    except ValueError:
        return None


def _string_or_none(value: JSONValue) -> str | None:
    return value if isinstance(value, str) else None


def _stage_maps(value: JSONValue) -> list[JSONMap]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, (list, tuple)) else []


def _run_item(run: JSONMap) -> JSONMap:
    stages = _stage_maps(run.get("stages"))
    complete = sum(1 for stage in stages if stage.get("status") in {"passed", "validated"})
    current = next((stage.get("name") for stage in stages if stage.get("status") in {"running", "failed", "blocked"}), None)
    status = str(run.get("status") or "not_recorded")
    return cast(JSONMap, {
        "task_id": run.get("run_id"), "run_id": run.get("run_id"), "batch_id": None, "child_id": None,
        "topic_source": run.get("topic_source"), "keyword": run.get("keyword") or "자동 주제",
        "as_of_date": None, "submitted_at": None, "started_at": None, "ended_at": run.get("updated_at"),
        "effective_status": status, "current_stage": current, "completed_stage_count": complete,
        "queue_position": None, "next_action": None, "cancel_action": None, "usage": run.get("usage"),
        "has_preview": status == "awaiting_user_confirmation", "duration_seconds": None,
        "run": run,
    })


def task_item(child: ManualRunView, batch: ManualBatchView, run: JSONMap | None, *, queue_position: int | None, now: datetime) -> JSONMap:
    source = run or {}
    stages = _stage_maps(source.get("stages"))
    complete = sum(1 for stage in stages if stage.get("status") in {"passed", "validated"})
    current = next((stage.get("name") for stage in stages if stage.get("status") in {"running", "failed", "blocked"}), None)
    status = effective_status(child, source)
    started = _string_or_none(source.get("started_at")) or child.started_at
    ended = (
        _string_or_none(source.get("ended_at")) or child.ended_at
        if status in {"draft_saved", "failed", "cancelled"}
        else None
    )
    return cast(JSONMap, {
        "task_id": child.task_id, "display_id": child.display_id, "run_id": child.run_id, "batch_id": batch.batch_id, "child_id": child.child_id,
        "topic_source": batch.topic_source, "keyword": child.resolved_keyword or child.requested_keyword or "주제 선정 대기",
        "as_of_date": batch.as_of_date, "scheduled_at": child.scheduled_at, "submitted_at": child.submitted_at, "started_at": started,
        "ended_at": ended, "effective_status": status, "current_stage": current,
        "completed_stage_count": complete, "queue_position": queue_position,
        "next_action": child.next_action.as_json() if child.next_action else None,
        "cancel_action": child.cancel_action.as_json() if child.cancel_action else None,
        "usage": source.get("usage"), "has_preview": child.confirmation_preview is not None,
        "duration_seconds": _duration(started, ended, now), "run": source or None,
    })


def _status_matches(value: JSONValue, requested: str | None) -> bool:
    if requested is None or requested == "all":
        return True
    actual = str(value)
    if requested == "running":
        return actual in {"running", "cancelling"}
    if requested == "awaiting_user_confirmation":
        return actual in {"awaiting_user_confirmation", "ready_for_naver", "local-only", "blocked"}
    if requested == "active":
        return actual in {"queued", "running", "cancelling"}
    if requested == "attention":
        return actual in {"awaiting_user_confirmation", "ready_for_naver", "local-only", "blocked", "failed"}
    if requested == "history":
        return actual in {"draft_saved", "cancelled"}
    return actual == requested


class _TaskManager(Protocol):
    def snapshot(self) -> tuple[ManualBatchView, ...]: ...


@final
class DashboardTasks:
    def __init__(self, manager: _TaskManager, run_cache: RunSnapshotCache) -> None:
        self.manager: _TaskManager = manager
        self.run_cache: RunSnapshotCache = run_cache
        self.revision: int = 0
        self._last_digest: str = ""

    def query(self, *, q: str = "", status: str | None = None, topic_source: str | None = None, date_from: str | None = None, date_to: str | None = None, limit: int = 20, cursor: str | None = None) -> JSONMap:
        if limit < 1 or limit > 100:
            raise ContractError("task limit must be between 1 and 100")
        batches = tuple(self.manager.snapshot())
        run_values: dict[str, JSONMap] = {run.run_id: cast(JSONMap, asdict(run)) for run in self.run_cache.runs()}
        children: list[tuple[ManualRunView, ManualBatchView]] = [(child, batch) for batch in batches for child in batch.children]
        known = {child.run_id for child, _ in children if child.run_id}
        items: list[JSONMap] = []
        now = datetime.now(UTC)
        queue = [child.task_id for child, _ in sorted(children, key=lambda item: item[0].submitted_at) if child.status == "queued"]
        positions = {task_id: index + 1 for index, task_id in enumerate(queue)}
        for child, batch in children:
            item = task_item(child, batch, run_values.get(child.run_id or ""), queue_position=positions.get(child.task_id), now=now)
            items.append(item)
        items.extend(_run_item(run) for run_id, run in run_values.items() if run_id not in known)
        needle = q.casefold().strip()
        filtered = [item for item in items if (not needle or needle in f"{item.get('run_id')} {item.get('task_id')} {item.get('keyword')}".casefold()) and _status_matches(item.get("effective_status"), status) and (topic_source is None or item.get("topic_source") == topic_source) and (date_from is None or (_date(_string_or_none(item.get("submitted_at"))) or "") >= date_from) and (date_to is None or (_date(_string_or_none(item.get("submitted_at"))) or "") <= date_to)]
        priority = {"cancelling": 0, "running": 1, "queued": 2, "awaiting_user_confirmation": 3, "ready_for_naver": 3, "local-only": 3, "blocked": 4, "failed": 4, "cancelled": 5, "draft_saved": 6}
        filtered.sort(key=lambda item: (priority.get(str(item.get("effective_status")), 7), item.get("queue_position") or 999999, str(item.get("ended_at") or item.get("submitted_at") or "")), reverse=False)
        offset = _cursor_offset(cursor, self.revision_for(items))
        page = filtered[offset:offset + limit]
        revision = self.revision_for(items)
        counts: dict[str, int] = {}
        for item in items:
            key = str(item.get("effective_status")); counts[key] = counts.get(key, 0) + 1
        digest = self.revision_for(items)
        if digest != self._last_digest:
            self.revision += 1; self._last_digest = digest
        summary: JSONMap = {"total": len(items), "by_status": cast(JSONValue, counts), "today_draft_saved": sum(1 for item in items if item.get("effective_status") == "draft_saved" and _date(cast(str | None, item.get("ended_at"))) == now.astimezone(KST).date().isoformat())}
        return {"items": cast(JSONValue, page), "filtered_total": len(filtered), "global_summary": summary, "next_cursor": _cursor(revision, offset + len(page)) if offset + len(page) < len(filtered) else None, "snapshot_revision": revision, "server_now": now.isoformat()}

    def revision_for(self, items: list[JSONMap]) -> str:
        stable = [{key: value for key, value in item.items() if key != "duration_seconds"} for item in items]
        return hashlib.sha256(json.dumps(stable, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _cursor(revision: str, offset: int) -> str:
    import base64
    return base64.urlsafe_b64encode(json.dumps({"revision": revision, "offset": offset}, separators=(",", ":")).encode()).decode().rstrip("=")


def _cursor_offset(cursor: str | None, revision: str) -> int:
    if cursor is None:
        return 0
    import base64
    try:
        raw_value: JSONValue = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
    except (ValueError, UnicodeError, json.JSONDecodeError) as error:
        raise ContractError("task cursor is invalid") from error
    offset = raw_value.get("offset") if isinstance(raw_value, dict) else None
    if not isinstance(raw_value, dict) or raw_value.get("revision") != revision or not isinstance(offset, int) or offset < 0:
        raise ContractError("task cursor is stale; reload the first page")
    return offset


__all__ = ["DashboardTasks", "effective_status", "task_item"]
