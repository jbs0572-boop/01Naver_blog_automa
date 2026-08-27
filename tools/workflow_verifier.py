from __future__ import annotations

# pyright: reportAny=false
import json
import os
import sys
from pathlib import Path

from tools.image_quality import validate_image_metadata, validate_image_quality
from tools.tool_policy import ToolAction, classify_tool_call
from tools.workflow_contract import (
    ContractError,
    JSONMap,
    JSONValue,
    SchemaError,
    build_manifest,
    validate_instance,
    validate_log,
    verify_gate,
    verify_manifest,
)


def _options(arguments: list[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    index = 0
    while index < len(arguments):
        name = arguments[index]
        if not name.startswith("--") or index + 1 >= len(arguments):
            raise ContractError(f"expected --key value, got: {name}")
        values[name[2:]] = arguments[index + 1]
        index += 2
    return values


def _required(values: dict[str, str], key: str) -> str:
    value = values.get(key)
    if not value:
        raise ContractError(f"missing option: --{key}")
    return value


def _required_env(key: str) -> str:
    value = os.environ.get(key)
    if not value:
        raise ContractError(f"missing environment variable: {key}")
    return value


def _root(values: dict[str, str]) -> Path:
    return Path(values.get("root", ".")).resolve()


def _write_json(path: Path, value: JSONMap) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _workspace_from_cwd(cwd: str) -> Path:
    current = Path(cwd).resolve()
    for candidate in (current, *current.parents):
        if (candidate / "AGENTS.md").is_file() and (
            candidate / "workflow-optimization-implementation-plan.md"
        ).is_file():
            return candidate
    raise ContractError("workflow workspace root could not be found from hook cwd")


def _hook() -> int:
    try:
        raw_value: JSONValue = json.loads(sys.stdin.read())
        if not isinstance(raw_value, dict):
            raise ContractError("hook input must be a JSON object")
        raw: JSONMap = raw_value
        tool_name_value = raw.get("tool_name")
        if not isinstance(tool_name_value, str):
            raise ContractError("hook tool_name must be a string")
        tool_input_value = raw.get("tool_input", {})
        if not isinstance(tool_input_value, dict):
            raise ContractError("hook tool_input must be an object")
        decision = classify_tool_call(tool_name_value, tool_input_value)
        if decision.action in {ToolAction.INTERNAL, ToolAction.READ}:
            return 0
        if decision.action is ToolAction.DENY:
            print(
                json.dumps(
                    {
                        "hookSpecificOutput": {
                            "hookEventName": "PreToolUse",
                            "permissionDecision": "deny",
                            "permissionDecisionReason": decision.reason,
                        }
                    }
                )
            )
            return 0
        lock_value = os.environ.get("WORKFLOW_LOCK")
        if lock_value and Path(lock_value).is_file():
            raise ContractError("workflow execution lock is active")
        root = _workspace_from_cwd(str(raw.get("cwd", os.getcwd())))
        gate = _required_env("WORKFLOW_GATE")
        manifest = Path(_required_env("WORKFLOW_MANIFEST"))
        run_log = Path(_required_env("WORKFLOW_RUN_LOG"))
        run_id = _required_env("WORKFLOW_RUN_ID")
        target_id = _required_env("WORKFLOW_TARGET_ID")
        result = verify_gate(
            root=root,
            manifest_path=manifest,
            run_log=run_log,
            gate=gate,
            run_id=run_id,
            target_id=target_id,
            notion_page_id=os.environ.get("WORKFLOW_NOTION_PAGE_ID"),
            notion_verified_at=os.environ.get("WORKFLOW_NOTION_VERIFIED_AT"),
            blog_id=os.environ.get("WORKFLOW_BLOG_ID"),
        )
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "allow",
                        "permissionDecisionReason": f"verified {result['verified_artifact_digest']}",
                    }
                }
            )
        )
        return 0
    except (ContractError, json.JSONDecodeError, OSError) as error:
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": str(error),
                    }
                }
            )
        )
        return 0


def main(arguments: list[str]) -> int:
    if len(arguments) < 2:
        raise ContractError(
            "command required: manifest, verify-manifest, gate, validate-log, validate-schema, validate-image-metadata, validate-image-quality, or hook"
        )
    command = arguments[1]
    if command == "hook":
        return _hook()
    values = _options(arguments[2:])
    root = _root(values)
    if command == "manifest":
        manifest = build_manifest(
            root=root,
            keyword=_required(values, "keyword"),
            run_id=_required(values, "run-id"),
            topic_id=_required(values, "topic-id"),
            mode=_required(values, "mode"),
            created_at=_required(values, "created-at"),
        )
        output = Path(_required(values, "output"))
        _write_json(output, manifest)
        print(
            json.dumps(
                {
                    "manifest": str(output),
                    "artifact_digest": manifest["artifact_digest"],
                },
                ensure_ascii=False,
            )
        )
        return 0
    if command == "verify-manifest":
        manifest = verify_manifest(root, Path(_required(values, "manifest")))
        print(
            json.dumps(
                {
                    "pipeline_version": manifest.pipeline_version,
                    "artifact_count": len(manifest.files),
                    "artifact_digest": manifest.artifact_digest,
                },
                ensure_ascii=False,
            )
        )
        return 0
    if command == "validate-log":
        events = validate_log(Path(_required(values, "run-log")))
        print(json.dumps({"events": events, "valid": True}, ensure_ascii=False))
        return 0
    if command == "validate-schema":
        input_path = Path(_required(values, "input"))
        source = input_path.read_text(encoding="utf-8")
        lines = (
            [line for line in source.splitlines() if line.strip()]
            if input_path.suffix == ".jsonl"
            else [source]
        )
        for line_no, line in enumerate(lines, 1):
            try:
                value: JSONValue = json.loads(line)
            except json.JSONDecodeError as error:
                raise ContractError(
                    f"invalid JSON at {input_path}:{line_no}"
                ) from error
            try:
                validate_instance(
                    value,
                    Path(__file__).resolve().parents[1]
                    / "schemas"
                    / "workflow-contract.schema.json",
                )
            except SchemaError as error:
                raise ContractError(
                    f"schema validation failed at {input_path}:{line_no}: {error}"
                ) from error
        print(
            json.dumps(
                {"input": str(input_path), "records": len(lines), "valid": True},
                ensure_ascii=False,
            )
        )
        return 0
    if command == "validate-image-metadata":
        result = validate_image_metadata(
            Path(_required(values, "metadata")), _required(values, "mode")
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if command == "validate-image-quality":
        result = validate_image_quality(Path(_required(values, "quality")))
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if command == "gate":
        result = verify_gate(
            root=root,
            manifest_path=Path(_required(values, "manifest")),
            run_log=Path(_required(values, "run-log")),
            gate=_required(values, "gate"),
            run_id=_required(values, "run-id"),
            target_id=_required(values, "target-id"),
            notion_page_id=values.get("notion-page-id"),
            notion_verified_at=values.get("notion-verified-at"),
            blog_id=values.get("blog-id"),
        )
        print(
            json.dumps(
                {
                    "gate": result.get("gate"),
                    "decision": result.get("decision"),
                    "artifact_digest": result["verified_artifact_digest"],
                },
                ensure_ascii=False,
            )
        )
        return 0
    raise ContractError(f"unknown command: {command}")


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv))
    except ContractError as error:
        print(f"workflow-verifier: {error}", file=sys.stderr)
        raise SystemExit(2) from error
