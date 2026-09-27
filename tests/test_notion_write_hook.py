from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tools.contract_types import PIPELINE_VERSION, JSONMap, JSONValue
from tools.manifest import ManifestBuildInput, build_manifest

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path, str]:
    keyword = "notion-hook"
    run_id = "RUN-notion-hook"
    final_dir = tmp_path / "final"
    asset_dir = tmp_path / "assets" / keyword
    final_dir.mkdir(parents=True)
    asset_dir.mkdir(parents=True)
    _ = (asset_dir / "body.png").write_bytes(b"body")
    _ = (asset_dir / "thumbnail.png").write_bytes(b"thumbnail")
    _ = (asset_dir / "image-map.md").write_text("# map\n", encoding="utf-8")
    _ = (final_dir / f"{keyword}.md").write_text(
        "![body](../assets/notion-hook/body.png)\n", encoding="utf-8"
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
        "- 데이터 소스 ID: `datasource-notion-hook`\n", encoding="utf-8"
    )
    _ = (tmp_path / "AGENTS.md").write_text("test root\n", encoding="utf-8")
    manifest = build_manifest(ManifestBuildInput(
        tmp_path,
        keyword,
        run_id,
        "TOPIC-notion-hook",
        "2026-08-27T00:00:00+00:00",
    ))
    manifest_path = tmp_path / "manifest.json"
    _ = manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    run_log = tmp_path / "run.jsonl"
    digest = manifest["artifact_digest"]
    assert isinstance(digest, str)
    _ = run_log.write_text(json.dumps(_stage(run_id, digest)) + "\n", encoding="utf-8")
    return tmp_path, manifest_path, run_log, run_id


def _stage(run_id: str, artifact_digest: str) -> JSONMap:
    return {
        "event_type": "stage",
        "pipeline_version": PIPELINE_VERSION,
        "telemetry_version": 2,
        "batch_id": "BATCH-notion-hook",
        "run_id": run_id,
        "topic_id": "TOPIC-notion-hook",
        "stage": "content-assembler",
        "started_at": "2026-08-27T00:00:00+00:00",
        "ended_at": "2026-08-27T00:01:00+00:00",
        "duration_ms": 60_000,
        "depends_on": ["image-maker"],
        "status": "passed",
        "attempt": 1,
        "quality": {"artifact_digest": artifact_digest},
    }


def _hook(
    root: Path,
    tool_name: str,
    tool_input: JSONMap,
    **environment: str,
) -> JSONMap:
    env = os.environ.copy()
    for key in (
        "WORKFLOW_GATE",
        "WORKFLOW_MANIFEST",
        "WORKFLOW_RUN_LOG",
        "WORKFLOW_RUN_ID",
        "WORKFLOW_TARGET_ID",
        "WORKFLOW_LOCK",
        "WORKFLOW_NOTION_PAGE_ID",
        "WORKFLOW_NOTION_VERIFIED_AT",
        "WORKFLOW_BLOG_ID",
    ):
        _ = env.pop(key, None)
    env.update(environment)
    raw: JSONMap = {"tool_name": tool_name, "tool_input": tool_input, "cwd": str(root)}
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
    parsed: JSONValue = json.loads(completed.stdout)
    assert isinstance(parsed, dict)
    return parsed


def _decision(result: JSONMap) -> JSONValue:
    output = result["hookSpecificOutput"]
    assert isinstance(output, dict)
    return output["permissionDecision"]


@pytest.mark.parametrize(
    ("tool_name", "tool_input", "expected"),
    [
        (
            "mcp__codex_apps__notion_notion_create_pages",
            {"parent": {"data_source_id": "datasource-notion-hook"}},
            "allow",
        ),
        (
            "mcp__notion__create_page",
            {"parent": {"data_source_id": "datasource-notion-hook"}},
            "allow",
        ),
        ("mcp__codex_apps__notion_notion_create_attachment", {}, "allow"),
        ("browser.click", {}, "deny"),
    ],
)
def test_approval_free_write_is_limited_to_notion(
    tmp_path: Path, tool_name: str, tool_input: JSONMap, expected: str
) -> None:
    root, manifest_path, run_log, run_id = _fixture(tmp_path)

    result = _hook(
        root,
        tool_name,
        tool_input,
        WORKFLOW_GATE="notion_write",
        WORKFLOW_MANIFEST=str(manifest_path),
        WORKFLOW_RUN_LOG=str(run_log),
        WORKFLOW_RUN_ID=run_id,
        WORKFLOW_TARGET_ID="datasource-notion-hook",
    )

    assert _decision(result) == expected


@pytest.mark.parametrize(
    ("tool_name", "tool_input"),
    [
        (
            "mcp__codex_apps__notion_notion_create_pages",
            {"parent": {"data_source_id": "another-data-source"}},
        ),
        (
            "mcp__codex_apps__notion_notion_update_page",
            {"page_id": "unrelated-page", "command": "update_properties"},
        ),
        ("mcp__codex_apps__notion_notion_duplicate_page", {"page_id": "other"}),
    ],
)
def test_approval_free_write_denies_unbound_notion_targets(
    tmp_path: Path, tool_name: str, tool_input: JSONMap
) -> None:
    root, manifest_path, run_log, run_id = _fixture(tmp_path)

    result = _hook(
        root,
        tool_name,
        tool_input,
        WORKFLOW_GATE="notion_write",
        WORKFLOW_MANIFEST=str(manifest_path),
        WORKFLOW_RUN_LOG=str(run_log),
        WORKFLOW_RUN_ID=run_id,
        WORKFLOW_TARGET_ID="datasource-notion-hook",
    )

    assert _decision(result) == "deny"


def test_hook_binds_manifest_and_q1_to_same_run(tmp_path: Path) -> None:
    root, manifest_path, run_log, _ = _fixture(tmp_path)
    digest = json.loads(manifest_path.read_text(encoding="utf-8"))["artifact_digest"]
    assert isinstance(digest, str)
    _ = run_log.write_text(json.dumps(_stage("RUN-other", digest)) + "\n", encoding="utf-8")

    result = _hook(
        root,
        "mcp__codex_apps__notion_notion_create_pages",
        {"parent": {"data_source_id": "datasource-notion-hook"}},
        WORKFLOW_GATE="notion_write",
        WORKFLOW_MANIFEST=str(manifest_path),
        WORKFLOW_RUN_LOG=str(run_log),
        WORKFLOW_RUN_ID="RUN-other",
        WORKFLOW_TARGET_ID="datasource-notion-hook",
    )

    assert _decision(result) == "deny"


def test_approval_free_write_never_updates_existing_page(tmp_path: Path) -> None:
    root, manifest_path, run_log, run_id = _fixture(tmp_path)

    result = _hook(
        root,
        "mcp__codex_apps__notion_notion_update_page",
        {"page_id": "unrelated-page", "command": "update_properties"},
        WORKFLOW_GATE="notion_write",
        WORKFLOW_MANIFEST=str(manifest_path),
        WORKFLOW_RUN_LOG=str(run_log),
        WORKFLOW_RUN_ID=run_id,
        WORKFLOW_TARGET_ID="datasource-notion-hook",
        WORKFLOW_NOTION_PAGE_ID="unrelated-page",
    )

    assert _decision(result) == "deny"


def test_production_notion_write_does_not_require_approval(tmp_path: Path) -> None:
    root, manifest_path, run_log, run_id = _fixture(tmp_path)

    result = _hook(
        root,
        "mcp__codex_apps__notion_notion_create_pages",
        {"parent": {"data_source_id": "datasource-notion-hook"}},
        WORKFLOW_GATE="notion_write",
        WORKFLOW_MANIFEST=str(manifest_path),
        WORKFLOW_RUN_LOG=str(run_log),
        WORKFLOW_RUN_ID=run_id,
        WORKFLOW_TARGET_ID="datasource-notion-hook",
    )

    assert _decision(result) == "allow"
