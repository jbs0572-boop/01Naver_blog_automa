from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.contract_types import PIPELINE_VERSION, ContractError, JSONMap
from tools.gate import GateRequest, authorize_external_write
from tools.manifest import ManifestBuildInput, build_manifest


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path, str]:
    keyword = "notion-gate"
    run_id = "RUN-notion-gate"
    final_dir = tmp_path / "final"
    asset_dir = tmp_path / "assets" / keyword
    final_dir.mkdir(parents=True)
    asset_dir.mkdir(parents=True)
    _ = (asset_dir / "body.png").write_bytes(b"body")
    _ = (asset_dir / "thumbnail.png").write_bytes(b"thumbnail")
    _ = (asset_dir / "image-map.md").write_text("# map\n", encoding="utf-8")
    _ = (tmp_path / "notion-config.md").write_text(
        "- 데이터 소스 ID: `datasource-notion-gate`\n", encoding="utf-8"
    )
    _ = (final_dir / f"{keyword}.md").write_text(
        "![body](../assets/notion-gate/body.png)\n", encoding="utf-8"
    )
    _ = (final_dir / f"{keyword}-naver-layout.md").write_text(
        "# layout\n", encoding="utf-8"
    )
    _ = (final_dir / f"{keyword}-naver-copy.md").write_text(
        "# copy\n", encoding="utf-8"
    )
    manifest = build_manifest(ManifestBuildInput(
        tmp_path,
        keyword,
        run_id,
        "TOPIC-notion-gate",
        "2026-08-27T00:00:00+00:00",
    ))
    manifest_path = tmp_path / "manifest.json"
    _ = manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    run_log = tmp_path / "run.jsonl"
    return tmp_path, manifest_path, run_log, run_id


def _stage(run_id: str, topic_id: str = "TOPIC-notion-gate") -> JSONMap:
    return {
        "event_type": "stage",
        "pipeline_version": PIPELINE_VERSION,
        "batch_id": "BATCH-notion-gate",
        "run_id": run_id,
        "topic_id": topic_id,
        "stage": "content-assembler",
        "started_at": "2026-08-27T00:00:00+00:00",
        "ended_at": "2026-08-27T00:01:00+00:00",
        "status": "passed",
        "attempt": 1,
    }


def _write_log(path: Path, events: list[JSONMap]) -> None:
    _ = path.write_text(
        "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
    )


def _request(
    root: Path,
    manifest_path: Path,
    run_log: Path,
    *,
    run_id: str = "RUN-notion-gate",
    target_id: str = "datasource-notion-gate",
    resource_id: str = "datasource-notion-gate",
) -> GateRequest:
    return GateRequest(
        root=root,
        manifest_path=manifest_path,
        run_log=run_log,
        gate="notion_write",
        run_id=run_id,
        target_id=target_id,
        notion_connector=True,
        notion_operation="create_pages",
        notion_resource_id=resource_id,
    )


def test_notion_authorization_does_not_require_approval(tmp_path: Path) -> None:
    root, manifest_path, run_log, run_id = _fixture(tmp_path)
    _write_log(run_log, [_stage(run_id)])

    result = authorize_external_write(_request(root, manifest_path, run_log))

    assert result["decision"] == "not_required"


def test_notion_authorization_requires_q1(tmp_path: Path) -> None:
    root, manifest_path, run_log, _ = _fixture(tmp_path)
    _write_log(run_log, [])

    with pytest.raises(ContractError, match="Q1"):
        _ = authorize_external_write(_request(root, manifest_path, run_log))


def test_notion_authorization_requires_configured_target(tmp_path: Path) -> None:
    root, manifest_path, run_log, run_id = _fixture(tmp_path)
    _write_log(run_log, [_stage(run_id)])

    with pytest.raises(ContractError, match="target_id"):
        _ = authorize_external_write(
            _request(
                root,
                manifest_path,
                run_log,
                target_id="wrong-data-source",
                resource_id="wrong-data-source",
            )
        )


def test_notion_authorization_binds_actual_write_target(tmp_path: Path) -> None:
    root, manifest_path, run_log, run_id = _fixture(tmp_path)
    _write_log(run_log, [_stage(run_id)])

    with pytest.raises(ContractError, match="write target"):
        _ = authorize_external_write(
            _request(
                root,
                manifest_path,
                run_log,
                resource_id="another-data-source",
            )
        )


def test_notion_authorization_binds_manifest_to_run(tmp_path: Path) -> None:
    root, manifest_path, run_log, _ = _fixture(tmp_path)
    _write_log(run_log, [_stage("RUN-other")])

    with pytest.raises(ContractError, match="manifest run_id"):
        _ = authorize_external_write(
            _request(root, manifest_path, run_log, run_id="RUN-other")
        )


def test_notion_authorization_binds_q1_to_manifest_topic(tmp_path: Path) -> None:
    root, manifest_path, run_log, run_id = _fixture(tmp_path)
    _write_log(run_log, [_stage(run_id, "TOPIC-other")])

    with pytest.raises(ContractError, match="Q1"):
        _ = authorize_external_write(_request(root, manifest_path, run_log))


def test_notion_authorization_scope_is_production(tmp_path: Path) -> None:
    root, manifest_path, run_log, run_id = _fixture(tmp_path)
    _write_log(run_log, [_stage(run_id)])

    result = authorize_external_write(_request(root, manifest_path, run_log))

    assert result["decision"] == "not_required"
    assert result["scope"] == "production"
