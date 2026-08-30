from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Final, override

from tools.codex_process import CodexProcessError, run_codex
from tools.contract_types import ContractError, JSONValue
from tools.runner_types import (
    RunStatus,
    StageExecution,
    StageExecutionContext,
    StageResult,
    safe_q1_feedback,
)

STAGE_INSTRUCTIONS: Final[dict[str, str]] = {
    "topic-selector": "topic-selector.md",
    "researcher": "researcher.md",
    "writer": "writer.md",
    "image-maker": "image-maker.md",
    "content-assembler": "content-assembler.md",
    "notion-rider": "notion-rider.md",
    "naver-rider": "naver-rider.md",
}
STAGE_TIMEOUTS: Final[dict[str, int]] = {
    "topic-selector": 45 * 60,
    "researcher": 45 * 60,
    "writer": 45 * 60,
    "image-maker": 90 * 60,
    "content-assembler": 45 * 60,
    "notion-rider": 30,
    "naver-rider": 30 * 60,
}
ALLOWED_ENVIRONMENT: Final[tuple[str, ...]] = (
    "PATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "TMPDIR",
    "CODEX_HOME",
)


@dataclass(frozen=True, slots=True)
class StageExecutionError(ContractError):
    stage: str
    reason: str

    @override
    def __str__(self) -> str:
        return f"{self.stage} stage result invalid: {self.reason}"


def _safe_relative(value: str, root: Path) -> Path:
    path = Path(value)
    if not value:
        raise ContractError(f"stage artifact path is unsafe: {value}")
    if path.is_absolute():
        try:
            return path.resolve().relative_to(root.resolve())
        except ValueError as error:
            raise ContractError(f"stage artifact path is unsafe: {value}") from error
    if ".." in path.parts:
        raise ContractError(f"stage artifact path is unsafe: {value}")
    return path


def _artifact_prefix(stage: str, keyword: str | None) -> str | None:
    if stage == "topic-selector":
        return "research/topic-selection-"
    if keyword is None:
        return None
    if stage == "researcher":
        return f"research/{keyword}.md"
    if stage == "writer":
        return f"drafts/{keyword}.md"
    if stage == "image-maker":
        return f"assets/{keyword}/"
    return None


def _artifact_is_allowed(
    stage: str, relative: str, keyword: str | None, run_id: str
) -> bool:
    if stage == "content-assembler":
        if keyword is None:
            return False
        final_paths = {
            f"final/{keyword}{suffix}"
            for suffix in (
                ".md",
                "-naver-layout.md",
                "-naver-copy.md",
                "-naver-input.md",
            )
        }
        return relative in final_paths or relative == (
            f"manifests/{run_id}-workflow-manifest.json"
        )
    prefix = _artifact_prefix(stage, keyword)
    return prefix is None or relative.startswith(prefix)


def _parse_result(
    stage: str,
    raw: JSONValue,
    root: Path,
    keyword: str | None,
    run_id: str,
    result_path: Path,
) -> StageResult:
    if not isinstance(raw, dict):
        raise StageExecutionError(stage, "structured response must be an object")
    result_stage = raw.get("stage")
    status = raw.get("status")
    execution = raw.get("execution")
    artifacts = raw.get("artifacts")
    if result_stage not in {None, stage}:
        raise StageExecutionError(stage, "stage name does not match request")
    if status not in {RunStatus.PASSED.value, RunStatus.VALIDATED.value}:
        raise StageExecutionError(stage, "status must be passed or validated")
    if execution not in {
        StageExecution.PRODUCED.value,
        StageExecution.VALIDATED.value,
    }:
        raise StageExecutionError(stage, "execution must be produced or validated")
    if not isinstance(artifacts, list) or not artifacts:
        raise StageExecutionError(stage, "artifacts must be a non-empty array")
    paths: list[str] = []
    for value in artifacts:
        if not isinstance(value, str):
            raise StageExecutionError(stage, "artifact path must be a string")
        path = _safe_relative(value, root)
        relative = path.as_posix()
        absolute = (root / path).resolve()
        if absolute == result_path.resolve():
            continue
        if not _artifact_is_allowed(stage, relative, keyword, run_id):
            raise StageExecutionError(
                stage, f"artifact is outside allowed output: {relative}"
            )
        try:
            _ = absolute.relative_to(root.resolve())
        except ValueError as error:
            raise StageExecutionError(
                stage, f"artifact escapes project: {relative}"
            ) from error
        if not absolute.is_file() or absolute.stat().st_size == 0:
            raise StageExecutionError(
                stage, f"artifact is missing or empty: {relative}"
            )
        paths.append(relative)
    if not paths:
        raise StageExecutionError(stage, "artifacts contain no stage outputs")
    resolved_keyword = raw.get("resolved_keyword")
    if resolved_keyword is not None and not isinstance(resolved_keyword, str):
        raise StageExecutionError(stage, "resolved_keyword must be a string")
    message = raw.get("message")
    run_status = raw.get("run_status")
    if run_status is not None and run_status not in {
        RunStatus.READY_FOR_NAVER.value,
        RunStatus.AWAITING_USER_CONFIRMATION.value,
        RunStatus.DRAFT_SAVED.value,
    }:
        raise StageExecutionError(stage, "run_status is invalid")
    details = raw.get("details")
    if details is not None and not isinstance(details, dict):
        raise StageExecutionError(stage, "details must be an object")
    return StageResult(
        RunStatus(status),
        StageExecution(execution),
        message if isinstance(message, str) else None,
        tuple(paths),
        resolved_keyword,
        RunStatus(run_status) if isinstance(run_status, str) else None,
        details if isinstance(details, dict) else None,
    )


@dataclass(frozen=True, slots=True)
class CodexStageExecutor:
    codex_binary: str = "codex"
    profile: str = "naver-automation"

    def execute(
        self, context: StageExecutionContext, result_path: Path | None = None
    ) -> StageResult:
        root = context.root
        stage = context.stage
        run_id = context.run_id
        keyword = context.keyword
        instruction = STAGE_INSTRUCTIONS.get(stage)
        timeout = STAGE_TIMEOUTS.get(stage)
        if instruction is None or timeout is None:
            raise ContractError(f"unsupported stage: {stage}")
        instruction_path = root / instruction
        if not instruction_path.is_file():
            raise ContractError(f"stage instruction is missing: {instruction_path}")
        _ = context.work_dir.mkdir(parents=True, exist_ok=True)
        output_path = result_path or context.work_dir / "stage-result.json"
        schema_path = root / "schemas" / "stage-result.schema.json"
        if not schema_path.is_file():
            raise ContractError(f"stage result schema is missing: {schema_path}")
        prompt = (
            f"Execute stage {stage} for run_id={run_id}, topic_id={context.topic_id}, "
            f"keyword={keyword or 'auto-topic'}. Read only {instruction_path}. "
            f"Write declared artifacts to the project canonical paths and return structured result. "
            f"Do not include the internal protocol file {output_path} in artifacts. "
            f"Work directory: {context.work_dir}. Do not call external write tools unless this stage permits it."
        )
        if stage == "researcher":
            prompt += (
                " Do not spawn subagents. Execute the official and supporting-visual "
                "research lanes serially in this process, starting the next lane only "
                "after the previous lane completes. Read-only browser access is "
                "authorized for this stage through Aside; use it to open and inspect "
                "the original pages, including JavaScript-rendered pages and the "
                "user-supplied supporting URLs. Do not click, fill, submit, save, "
                "download, or otherwise write through the browser. Record the URL, "
                "access time, observed facts, and rights limitations in the research "
                "artifact."
            )
        if context.q1_feedback is not None:
            prompt += (
                " Repair the prior Q1 failure using this safe feedback: "
                f"{safe_q1_feedback(context.q1_feedback)}"
            )
        command = [
            self.codex_binary,
            "exec",
            "--strict-config",
            "--profile",
            self.profile,
            "--sandbox",
            "workspace-write",
            "--json",
            "--output-schema",
            str(schema_path),
            "--output-last-message",
            str(output_path),
            "--cd",
            str(root),
            prompt,
        ]
        environment = {
            key: value for key in ALLOWED_ENVIRONMENT if (value := os.environ.get(key))
        }
        try:
            run_codex(
                command,
                root=root,
                environment=environment,
                timeout=timeout,
                work_dir=context.work_dir,
            )
        except CodexProcessError as error:
            raise StageExecutionError(stage, str(error)) from error
        try:
            raw = json.loads(output_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise StageExecutionError(
                stage, "structured response file is missing or invalid"
            ) from error
        return _parse_result(stage, raw, root, keyword, run_id, output_path)


__all__ = ["CodexStageExecutor", "StageExecutionError"]
