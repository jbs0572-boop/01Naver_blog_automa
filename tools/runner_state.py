from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

from tools.contract_types import ContractError, JSONMap, JSONValue
from tools.runner_types import (
    RunnerRequest,
    RunnerResult,
    RunStatus,
    TopicSelectionContext,
)


def state_paths(
    root: Path, run_id: str, state_dir: Path | None = None
) -> tuple[Path, Path, Path]:
    parts = Path(run_id).parts
    if (
        not run_id
        or Path(run_id).is_absolute()
        or len(parts) != 1
        or parts[0] in {".", ".."}
    ):
        raise ContractError("run_id must be a single safe path component")
    base = state_dir if state_dir is not None else root / ".automation"
    return (
        base / "state" / f"{run_id}.json",
        base / "logs" / f"{run_id}.jsonl",
        base / "locks" / f"{run_id}.lock",
    )


def _safe_now(now: datetime | None) -> datetime:
    value = now if now is not None else datetime.now().astimezone()
    if value.tzinfo is None or value.utcoffset() is None:
        raise ContractError("runner timestamps must include a timezone")
    return value


def stable_run_id(request: RunnerRequest) -> str:
    now = _safe_now(request.now)
    topic = "auto-topic" if request.auto_topic else request.keyword or ""
    material = "|".join((request.job, topic, now.date().isoformat()))
    return "RUN-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


def _digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def file_digest(path: Path) -> str:
    return _digest_bytes(path.read_bytes())


def input_fingerprint(request: RunnerRequest) -> str:
    records: list[JSONValue] = []
    if request.keyword is not None and not request.auto_topic:
        keyword_parts = Path(request.keyword).parts
        if (
            not request.keyword
            or Path(request.keyword).is_absolute()
            or len(keyword_parts) != 1
            or keyword_parts[0] in {".", ".."}
        ):
            raise ContractError("keyword must be a single safe path component")
        keyword_dir = request.root / "assets" / request.keyword
        candidates = [
            request.root / "research" / f"{request.keyword}.md",
            request.root / "research" / f"topic-selection-{request.keyword}.md",
            request.root / "drafts" / f"{request.keyword}.md",
            request.root / "final" / f"{request.keyword}.md",
            request.root / "final" / f"{request.keyword}-naver-layout.md",
            request.root / "final" / f"{request.keyword}-naver-copy.md",
        ]
        if keyword_dir.is_dir():
            candidates.extend(
                path for path in sorted(keyword_dir.rglob("*")) if path.is_file()
            )
        for path in sorted(set(candidates)):
            record: JSONMap = {
                "path": path.relative_to(request.root).as_posix(),
                "sha256": file_digest(path) if path.is_file() else None,
            }
            records.append(record)
    else:
        directories = (
            ()
            if request.auto_topic
            else ("research", "drafts", "final", "assets", "runs")
        )
        for directory in directories:
            path = request.root / directory
            files = (
                [item for item in sorted(path.rglob("*")) if item.is_file()]
                if path.is_dir()
                else []
            )
            records.extend(
                {
                    "path": item.relative_to(request.root).as_posix(),
                    "sha256": file_digest(item),
                }
                for item in files
            )
        if request.auto_topic:
            for name in (
                "AGENTS.md",
                "notion-config.md",
                "naver-config.md",
                "schemas/workflow-contract.schema.json",
            ):
                path = request.root / name
                if path.is_file():
                    records.append({"path": name, "sha256": file_digest(path)})
    material: JSONMap = {
        "job": request.job,
        "keyword": "auto-topic" if request.auto_topic else request.keyword,
        "notion_target_id": request.notion_target_id,
        "selection_context": (
            request.selection_context.as_json()
            if request.selection_context is not None
            else None
        ),
        "inputs": records,
    }
    encoded = json.dumps(
        material, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + _digest_bytes(encoded)


def atomic_write_json(path: Path, value: JSONMap) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temporary = Path(temporary_name)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            _ = handle.write(
                json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
            )
            _ = handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
        directory_fd = os.open(
            path.parent,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError as error:
        raise ContractError(f"could not atomically write state: {path}") from error
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError as error:
                raise ContractError(
                    f"could not remove temporary state: {temporary}"
                ) from error


def read_state(path: Path) -> JSONMap:
    try:
        raw: JSONValue = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as error:
        raise ContractError(f"could not read runner state: {path}") from error
    if not isinstance(raw, dict):
        raise ContractError(f"runner state must be an object: {path}")
    return raw


def result_from_state(state: JSONMap, state_path: Path, log_path: Path) -> RunnerResult:
    run_id = state.get("run_id")
    status = state.get("status")
    message = state.get("message")
    stages = state.get("stages")
    if (
        not isinstance(run_id, str)
        or not isinstance(status, str)
        or not isinstance(message, str)
        or not isinstance(stages, dict)
    ):
        raise ContractError(f"runner state has invalid result fields: {state_path}")
    completed = tuple(
        key
        for key, value in stages.items()
        if value in {RunStatus.PASSED.value, RunStatus.VALIDATED.value}
    )
    try:
        parsed_status = RunStatus(status)
    except ValueError as error:
        raise ContractError(f"runner state has invalid status: {status}") from error
    return RunnerResult(run_id, parsed_status, state_path, log_path, completed, message)


def request_from_state(
    state: JSONMap, root: Path, state_dir: Path | None = None
) -> RunnerRequest:
    job = state.get("job")
    keyword = state.get("keyword")
    run_id = state.get("run_id")
    notion_target_id = state.get("notion_target_id")
    if (
        not isinstance(job, str)
        or (keyword is not None and not isinstance(keyword, str))
        or not isinstance(run_id, str)
        or (notion_target_id is not None and not isinstance(notion_target_id, str))
    ):
        raise ContractError(
            "runner state cannot be recovered: request fields are invalid"
        )
    context_value = state.get("selection_context")
    selection_context = None
    if isinstance(context_value, dict):
        fields: dict[str, str] = {}
        for key in ("category", "audience", "publish_purpose", "as_of_date", "timezone"):
            value = context_value.get(key)
            if not isinstance(value, str):
                break
            fields[key] = value
        else:
            selection_context = TopicSelectionContext(
                fields["category"],
                fields["audience"],
                fields["publish_purpose"],
                fields["as_of_date"],
                fields["timezone"],
            )
    return RunnerRequest(
        root=root,
        job=job,
        keyword=keyword,
        run_id=run_id,
        dry_run=state.get("dry_run") is True,
        state_dir=state_dir,
        auto_topic=state.get("auto_topic") is True,
        selection_context=selection_context,
        confirmed=state.get("confirmation") is not None,
        resume=True,
        notion_target_id=notion_target_id,
    )


__all__ = [
    "atomic_write_json",
    "file_digest",
    "input_fingerprint",
    "read_state",
    "request_from_state",
    "result_from_state",
    "stable_run_id",
    "state_paths",
]
