from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from tools.contract_types import JSONMap


class JobName(StrEnum):
    DAILY_GENERATE = "daily-generate"
    WEEKLY_IMPROVE = "weekly-improve"
    NAVER_PUBLISH = "naver-publish"


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    BLOCKED = "blocked"
    SKIPPED = "skipped"


class RunnerBlocked(Exception):
    pass


STAGE_ORDER = (
    "topic-selector",
    "researcher",
    "writer",
    "image-maker",
    "content-assembler",
    "notion-rider",
    "naver-rider",
)


@dataclass(frozen=True, slots=True)
class RunnerRequest:
    root: Path
    job: str
    mode: str
    keyword: str | None = None
    run_id: str | None = None
    dry_run: bool = False
    state_dir: Path | None = None
    now: datetime | None = None


@dataclass(frozen=True, slots=True)
class RunnerResult:
    run_id: str
    status: RunStatus
    state_path: Path
    log_path: Path
    stages: tuple[str, ...]
    message: str

    def as_json(self) -> JSONMap:
        return {
            "run_id": self.run_id,
            "status": self.status.value,
            "state_path": str(self.state_path),
            "log_path": str(self.log_path),
            "stages": list(self.stages),
            "message": self.message,
        }
