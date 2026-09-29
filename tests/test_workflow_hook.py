from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from tools.contract_types import PIPELINE_VERSION, JSONMap, JSONValue
from tools.manifest import ManifestBuildInput, build_manifest

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path, JSONMap, str]:
    keyword = "hook-topic"
    run_id = "RUN-hook"
    final_dir = tmp_path / "final"
    asset_dir = tmp_path / "assets" / keyword
    final_dir.mkdir(parents=True)
    asset_dir.mkdir(parents=True)
    _ = (asset_dir / "body.png").write_bytes(b"body")
    _ = (asset_dir / "thumbnail.png").write_bytes(b"thumbnail")
    _ = (asset_dir / "image-map.md").write_text("# map\n", encoding="utf-8")
    _ = (final_dir / f"{keyword}.md").write_text(
        "![body](../assets/hook-topic/body.png)\n", encoding="utf-8"
    )
    _ = (final_dir / f"{keyword}-naver-layout.md").write_text(
        "# layout\n", encoding="utf-8"
    )
    _ = (final_dir / f"{keyword}-naver-copy.md").write_text(
        "# copy\n", encoding="utf-8"
    )
    _ = (final_dir / f"{keyword}-naver-input.md").write_text(
        "# copy\n", encoding="utf-8"
    )
    _ = (tmp_path / "notion-config.md").write_text(
        "- 데이터 소스 ID: `datasource-hook`\n", encoding="utf-8"
    )
    _ = (tmp_path / "AGENTS.md").write_text("temporary test root\n", encoding="utf-8")
    manifest = build_manifest(ManifestBuildInput(
        tmp_path, keyword, run_id, "TOPIC-hook", "2026-08-27T00:00:00+00:00"
    ))
    manifest_path = tmp_path / "manifest.json"
    _ = manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    run_log = tmp_path / "run.jsonl"
    stage: JSONMap = {
        "event_type": "stage",
        "pipeline_version": PIPELINE_VERSION,
        "batch_id": "BATCH-hook",
        "run_id": run_id,
        "topic_id": "TOPIC-hook",
        "stage": "content-assembler",
        "started_at": "2026-08-27T00:00:00+00:00",
        "ended_at": "2026-08-27T00:01:00+00:00",
        "status": "passed",
        "attempt": 1,
    }
    _ = run_log.write_text(json.dumps(stage) + "\n", encoding="utf-8")
    return tmp_path, manifest_path, run_log, manifest, run_id


def _hook(tmp_path: Path, tool_name: str, **environment: str) -> JSONMap:
    env = os.environ.copy()
    for key in (
        "WORKFLOW_GATE",
        "WORKFLOW_MANIFEST",
        "WORKFLOW_RUN_LOG",
        "WORKFLOW_RUN_ID",
        "WORKFLOW_TARGET_ID",
        "WORKFLOW_LOCK",
    ):
        _ = env.pop(key, None)
    env.update(environment)
    raw: JSONMap = {"tool_name": tool_name, "tool_input": {}, "cwd": str(tmp_path)}
    completed = subprocess.run(
        [sys.executable, "-m", "tools.workflow_verifier", "hook"],
        cwd=PROJECT_ROOT,
        env=env,
        input=json.dumps(raw),
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    if not completed.stdout.strip():
        return {}
    parsed: JSONValue = json.loads(completed.stdout)
    assert isinstance(parsed, dict)
    return parsed


def _assert_deny(result: JSONMap) -> None:
    output = result["hookSpecificOutput"]
    assert isinstance(output, dict)
    assert output["permissionDecision"] == "deny"


def test_hook_dry_run_matrix_denies_without_external_call(tmp_path: Path) -> None:
    root, manifest_path, run_log, _, run_id = _fixture(tmp_path)
    cases: list[dict[str, str]] = [
        {"name": "missing-env"},
        {
            "name": "missing-manifest",
            "WORKFLOW_GATE": "notion_write",
            "WORKFLOW_MANIFEST": str(root / "missing.json"),
            "WORKFLOW_RUN_LOG": str(run_log),
            "WORKFLOW_RUN_ID": run_id,
            "WORKFLOW_TARGET_ID": "datasource-hook",
        },
        {
            "name": "missing-q1",
            "WORKFLOW_GATE": "notion_write",
            "WORKFLOW_MANIFEST": str(manifest_path),
            "WORKFLOW_RUN_LOG": str(run_log),
            "WORKFLOW_RUN_ID": run_id,
            "WORKFLOW_TARGET_ID": "datasource-hook",
        },
        {
            "name": "wrong-target",
            "WORKFLOW_GATE": "notion_write",
            "WORKFLOW_MANIFEST": str(manifest_path),
            "WORKFLOW_RUN_LOG": str(run_log),
            "WORKFLOW_RUN_ID": run_id,
            "WORKFLOW_TARGET_ID": "another-data-source",
        },
        {"name": "unknown-tool"},
        {
            "name": "lock",
            "WORKFLOW_GATE": "notion_write",
            "WORKFLOW_MANIFEST": str(manifest_path),
            "WORKFLOW_RUN_LOG": str(run_log),
            "WORKFLOW_RUN_ID": run_id,
            "WORKFLOW_TARGET_ID": "datasource-hook",
            "WORKFLOW_LOCK": str(root / "active.lock"),
        },
    ]
    _ = (root / "active.lock").write_text("active\n", encoding="utf-8")
    for case in cases:
        if case["name"] == "missing-q1":
            _ = run_log.write_text("", encoding="utf-8")
        result = _hook(
            root,
            "mcp__unknown__do"
            if case["name"] == "unknown-tool"
            else "mcp__notion__create_page",
            **{key: value for key, value in case.items() if key != "name"},
        )
        _assert_deny(result)


def test_hook_read_query_ignores_write_words(tmp_path: Path) -> None:
    result = _hook(tmp_path, "mcp__notion__search")

    assert result == {}
