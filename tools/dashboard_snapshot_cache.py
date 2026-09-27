from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import final
from zoneinfo import ZoneInfo

from tools.contract_types import ContractError, JSONMap, JSONValue
from tools.dashboard_data import RunView, run_view

KST = ZoneInfo("Asia/Seoul")


@dataclass(frozen=True, slots=True)
class FileIdentity:
    size: int
    mtime_ns: int
    state_size: int
    state_mtime_ns: int
    state_ctime_ns: int
    usage_files: tuple[InputFileIdentity, ...]


@dataclass(frozen=True, slots=True)
class InputFileIdentity:
    path: str
    size: int
    mtime_ns: int
    ctime_ns: int


@final
class RunSnapshotCache:

    def __init__(self, root: Path) -> None:
        self.root: Path = root
        self._entries: dict[Path, tuple[FileIdentity, RunView]] = {}
        self._lock: RLock = RLock()
        self.parse_count: int = 0
        self.source_scan_count: int = 0

    def _identity(self, path: Path) -> FileIdentity:
        stat = path.stat()
        state = self.root / ".automation" / "state" / f"{path.stem}.json"
        try:
            state_stat = state.stat()
        except OSError:
            return FileIdentity(
                stat.st_size,
                stat.st_mtime_ns,
                -1,
                -1,
                -1,
                self._usage_files(path.stem),
            )
        return FileIdentity(
            stat.st_size,
            stat.st_mtime_ns,
            state_stat.st_size,
            state_stat.st_mtime_ns,
            state_stat.st_ctime_ns,
            self._usage_files(path.stem),
        )

    def _usage_files(self, run_id: str) -> tuple[InputFileIdentity, ...]:
        work = self.root / ".automation" / "work" / run_id
        if not work.is_dir():
            return ()
        identities: list[InputFileIdentity] = []
        for stage_dir in sorted(path for path in work.iterdir() if path.is_dir()):
            nested = sorted(stage_dir.glob("attempt-*/codex-attempt-*.jsonl"))
            paths = nested or sorted(stage_dir.glob("codex-attempt-*.jsonl"))
            for path in paths:
                try:
                    stat = path.stat()
                except OSError:
                    continue
                identities.append(
                    InputFileIdentity(
                        path.relative_to(self.root).as_posix(),
                        stat.st_size,
                        stat.st_mtime_ns,
                        stat.st_ctime_ns,
                    )
                )
        return tuple(identities)

    def runs(self) -> tuple[RunView, ...]:
        paths = sorted(
            {*self.root.glob("runs/*.jsonl"), *self.root.glob(".automation/logs/*.jsonl")}
        )
        with self._lock:
            self.source_scan_count += 1
            current = set(paths)
            self._entries = {
                path: cached for path, cached in self._entries.items() if path in current
            }
            for path in paths:
                identity = self._identity(path)
                cached = self._entries.get(path)
                if cached is None or cached[0] != identity:
                    self._entries[path] = (identity, run_view(self.root, path))
                    self.parse_count += 1
            deduplicated: dict[str, RunView] = {}
            for path in paths:
                run = self._entries[path][1]
                previous = deduplicated.get(run.run_id)
                if previous is None or (run.updated_at or "") > (previous.updated_at or ""):
                    deduplicated[run.run_id] = run
            return tuple(
                sorted(
                    deduplicated.values(),
                    key=lambda item: (item.updated_at or "", item.run_id),
                    reverse=True,
                )
            )

    def query(
        self,
        *,
        query: str = "",
        status: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> JSONMap:
        if limit < 1 or limit > 100:
            raise ContractError("run list limit must be between 1 and 100")
        runs = self.runs()
        revision = self._revision(runs)
        offset = self._cursor_offset(cursor, revision)
        needle = query.casefold().strip()
        filtered = tuple(
            run
            for run in runs
            if (not needle or needle in f"{run.run_id} {run.topic_id} {run.keyword}".casefold())
            and (status is None or run.status == status)
            and self._within_dates(run, date_from, date_to)
        )
        page = filtered[offset : offset + limit]
        next_offset = offset + len(page)
        counts: dict[str, int] = {}
        for run in runs:
            counts[run.status] = counts.get(run.status, 0) + 1
        status_counts: JSONMap = {}
        for key, value in counts.items():
            status_counts[key] = value
        items: list[JSONValue] = [asdict(run) for run in page]
        result: JSONMap = {
            "items": items,
            "filtered_total": len(filtered),
            "global_summary": {
                "total": len(runs),
                "by_status": status_counts,
            },
            "global_usage": self._usage(runs),
            "next_cursor": (
                self._encode_cursor(revision, next_offset)
                if next_offset < len(filtered)
                else None
            ),
            "snapshot_revision": revision,
            "parse_count": self.parse_count,
        }
        return result

    def get(self, run_id: str) -> RunView | None:
        return next((run for run in self.runs() if run.run_id == run_id), None)

    @staticmethod
    def _usage(runs: tuple[RunView, ...]) -> JSONMap:
        totals = {"input_tokens": 0, "output_tokens": 0, "cached_input_tokens": 0, "total_tokens": 0}
        recorded = 0
        for run in runs:
            if run.usage is None:
                continue
            recorded += 1
            for key in totals:
                value = run.usage.get(key)
                if isinstance(value, int):
                    totals[key] += value
        return {**totals, "recorded_runs": recorded}

    @staticmethod
    def _within_dates(run: RunView, date_from: str | None, date_to: str | None) -> bool:
        if run.updated_at is None:
            return date_from is None and date_to is None
        day = datetime.fromisoformat(run.updated_at).astimezone(KST).date().isoformat()
        return (date_from is None or day >= date_from) and (date_to is None or day <= date_to)

    @staticmethod
    def _revision(runs: tuple[RunView, ...]) -> str:
        source = "\n".join(f"{run.run_id}:{run.updated_at}" for run in runs)
        return hashlib.sha256(source.encode()).hexdigest()[:16]

    @staticmethod
    def _encode_cursor(revision: str, offset: int) -> str:
        raw = json.dumps({"revision": revision, "offset": offset}, separators=(",", ":"))
        return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")

    @staticmethod
    def _cursor_offset(cursor: str | None, revision: str) -> int:
        if cursor is None:
            return 0
        try:
            padded = cursor + "=" * (-len(cursor) % 4)
            raw: JSONValue = json.loads(base64.urlsafe_b64decode(padded).decode())
        except (ValueError, UnicodeError, json.JSONDecodeError) as error:
            raise ContractError("run list cursor is invalid") from error
        if not isinstance(raw, dict) or raw.get("revision") != revision:
            raise ContractError("run list cursor is stale; reload the first page")
        offset = raw.get("offset")
        if not isinstance(offset, int) or offset < 0:
            raise ContractError("run list cursor is invalid")
        return offset


__all__ = ["RunSnapshotCache"]
