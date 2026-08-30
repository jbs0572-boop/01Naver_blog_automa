from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from tools.contract_types import JSONMap, JSONValue

STAGE_ORDER: Final[tuple[str, ...]] = (
    "topic-selector",
    "researcher",
    "writer",
    "image-maker",
    "content-assembler",
    "notion-rider",
    "naver-rider",
)


@dataclass(frozen=True, slots=True)
class StageView:
    name: str
    status: str
    started_at: str | None = None
    ended_at: str | None = None
    attempt: int = 0
    message: str | None = None


@dataclass(frozen=True, slots=True)
class RunView:
    run_id: str
    topic_id: str
    keyword: str
    topic_source: str | None
    historical: bool
    status: str
    updated_at: str | None
    log_path: str
    q1: str
    q2: str
    error: str | None
    stages: tuple[StageView, ...]


def _text(value: JSONValue, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _map(value: JSONValue) -> JSONMap:
    return value if isinstance(value, dict) else {}


def _read_events(path: Path) -> tuple[list[JSONMap], str | None]:
    events: list[JSONMap] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                value: JSONValue = json.loads(line)
                events.append(_map(value))
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError) as error:
        return [], f"읽기 실패: {type(error).__name__}"
    return events, None


def _state_for(root: Path, run_id: str) -> JSONMap:
    path = root / ".automation" / "state" / f"{run_id}.json"
    try:
        value: JSONValue = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return _map(value)


def _stage_views(events: list[JSONMap]) -> tuple[StageView, ...]:
    latest: dict[str, JSONMap] = {}
    for item in events:
        if item.get("event_type") == "stage" and isinstance(item.get("stage"), str):
            latest[_text(item["stage"])] = item
    views: list[StageView] = []
    for stage in STAGE_ORDER:
        item = latest.get(stage, {})
        attempt = item.get("attempt")
        views.append(
            StageView(
                name=stage,
                status=_text(item.get("status"), "pending"),
                started_at=_text(item.get("started_at")) or None,
                ended_at=_text(item.get("ended_at")) or None,
                attempt=attempt if isinstance(attempt, int) else 0,
                message=_text(item.get("error_message_safe")) or None,
            )
        )
    return tuple(views)


def _gate(events: list[JSONMap], key: str) -> str:
    for item in reversed(events):
        quality = _map(item.get("quality"))
        value = quality.get(key)
        if isinstance(value, str):
            return value
        if item.get("stage") == key and item.get("status") == "passed":
            return "passed"
    return "not_recorded"


def _run_view(root: Path, path: Path) -> RunView:
    events, read_error = _read_events(path)
    first = events[0] if events else {}
    run_id = _text(first.get("run_id"), path.stem)
    state = _state_for(root, run_id)
    stages = _stage_views(events)
    status = _text(state.get("status"))
    if not status:
        stage_statuses = {stage.status for stage in stages}
        status = "failed" if read_error or "failed" in stage_statuses else "passed"
    error = _text(state.get("message")) or read_error
    explicit_topic_source = _text(state.get("topic_source"))
    auto_topic = state.get("auto_topic")
    topic_source = explicit_topic_source or (
        "auto_selected"
        if auto_topic is True
        else "user_defined"
        if auto_topic is False
        else None
    )
    return RunView(
        run_id=run_id,
        topic_id=_text(state.get("topic_id"), _text(first.get("topic_id"), "-")),
        keyword=_text(state.get("keyword"), _text(first.get("keyword"), "자동 주제")),
        topic_source=topic_source,
        historical="mode" in state,
        status=status,
        updated_at=_text(state.get("updated_at"), _text(first.get("ended_at"))) or None,
        log_path=path.relative_to(root).as_posix(),
        q1=_gate(events, "q1"),
        q2=_gate(events, "q2"),
        error=error,
        stages=stages,
    )


def demo_runs() -> tuple[RunView, ...]:
    now = "2026-08-29T09:30:00+09:00"

    def stage_views(last: int, failed: bool = False) -> tuple[StageView, ...]:
        return tuple(
            StageView(name, "failed" if failed and index == last else "passed")
            if index <= last
            else StageView(name, "pending")
            for index, name in enumerate(STAGE_ORDER)
        )

    return (
        RunView(
            "RUN-demo-ready",
            "TOPIC-naver-ai",
            "네이버 AI 검색 노출",
            "auto_selected",
            False,
            "ready_for_naver",
            now,
            "demo/RUN-demo-ready.jsonl",
            "passed",
            "passed",
            None,
            stage_views(5),
        ),
        RunView(
            "RUN-demo-waiting",
            "TOPIC-blog-format",
            "블로그 글쓰기 형식",
            "auto_selected",
            False,
            "awaiting_user_confirmation",
            now,
            "demo/RUN-demo-waiting.jsonl",
            "passed",
            "passed",
            "네이버 임시저장 전 사용자 확인 대기",
            stage_views(6),
        ),
        RunView(
            "RUN-demo-failed",
            "TOPIC-image-license",
            "이미지 출처 검수",
            "user_defined",
            False,
            "failed",
            now,
            "demo/RUN-demo-failed.jsonl",
            "failed",
            "not_recorded",
            "image-maker: 권리 상태 미확인",
            stage_views(3, True),
        ),
    )


def load_runs(root: Path, demo: bool = False) -> tuple[RunView, ...]:
    if demo:
        return demo_runs()
    paths = sorted({*root.glob("runs/*.jsonl"), *root.glob(".automation/logs/*.jsonl")})
    return tuple(
        sorted(
            (_run_view(root, path) for path in paths),
            key=lambda item: item.updated_at or "",
            reverse=True,
        )
    )


def snapshot(root: Path, demo: bool = False) -> JSONMap:
    runs = load_runs(root, demo)
    counts: dict[str, int] = {}
    for run in runs:
        counts[run.status] = counts.get(run.status, 0) + 1
    status_counts: JSONMap = {key: value for key, value in counts.items()}
    summary: JSONMap = {"total": len(runs), "by_status": status_counts}
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "root": str(root),
        "summary": summary,
        "runs": [asdict(run) for run in runs],
    }


__all__ = ["demo_runs", "load_runs", "snapshot"]
