from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.contract_types import PIPELINE_VERSION, ContractError, JSONMap
from tools.gate import GateRequest, authorize_external_write, verify_gate
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


def _stage(
    run_id: str,
    artifact_digest: str,
    topic_id: str = "TOPIC-notion-gate",
) -> JSONMap:
    return {
        "event_type": "stage",
        "pipeline_version": PIPELINE_VERSION,
        "telemetry_version": 2,
        "batch_id": "BATCH-notion-gate",
        "run_id": run_id,
        "topic_id": topic_id,
        "stage": "content-assembler",
        "started_at": "2026-08-27T00:00:00+00:00",
        "ended_at": "2026-08-27T00:01:00+00:00",
        "duration_ms": 60_000,
        "depends_on": ["image-maker"],
        "status": "passed",
        "attempt": 1,
        "quality": {"artifact_digest": artifact_digest},
    }


def _q2_stage(
    run_id: str,
    artifact_digest: str,
    *,
    status: str = "passed",
    page_id: str = "page-notion-gate",
    target_id: str = "datasource-notion-gate",
    attempt: int = 1,
    started_at: str = "2026-08-27T00:02:00+00:00",
    ended_at: str = "2026-08-27T00:03:00+00:00",
) -> JSONMap:
    content_digest = "sha256:" + "c" * 64
    return {
        "event_type": "stage",
        "pipeline_version": PIPELINE_VERSION,
        "telemetry_version": 2,
        "batch_id": "BATCH-notion-gate",
        "run_id": run_id,
        "topic_id": "TOPIC-notion-gate",
        "stage": "notion-rider",
        "started_at": started_at,
        "ended_at": ended_at,
        "duration_ms": 60_000,
        "depends_on": ["content-assembler"],
        "status": status,
        "attempt": attempt,
        "quality": {
            "storage_integrity": "passed",
            "notion_page_id": page_id,
            "notion_last_verified_at": "2026-08-27T00:03:00+00:00",
            "expected_notion_content_digest": content_digest,
            "notion_content_digest": content_digest,
            "notion_roundtrip_digest": content_digest,
            "artifact_digest": artifact_digest,
            "notion_target_id": target_id,
        },
    }


def _write_log(path: Path, events: list[JSONMap]) -> None:
    _ = path.write_text(
        "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
    )


def _manifest_digest(path: Path) -> str:
    digest = json.loads(path.read_text(encoding="utf-8"))["artifact_digest"]
    assert isinstance(digest, str)
    return digest


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


def _naver_request(
    root: Path, manifest_path: Path, run_log: Path, run_id: str
) -> GateRequest:
    content_digest = "sha256:" + "c" * 64
    return GateRequest(
        root=root,
        manifest_path=manifest_path,
        run_log=run_log,
        gate="naver_draft_save",
        run_id=run_id,
        target_id="blog-notion-gate",
        notion_page_id="page-notion-gate",
        notion_verified_at="2026-08-27T00:03:00+00:00",
        expected_notion_content_digest=content_digest,
        notion_content_digest=content_digest,
        notion_roundtrip_digest=content_digest,
        q2_artifact_digest=_manifest_digest(manifest_path),
        blog_id="blog-notion-gate",
    )


def test_notion_authorization_does_not_require_approval(tmp_path: Path) -> None:
    root, manifest_path, run_log, run_id = _fixture(tmp_path)
    _write_log(run_log, [_stage(run_id, _manifest_digest(manifest_path))])

    result = authorize_external_write(_request(root, manifest_path, run_log))

    assert result["decision"] == "not_required"


def test_notion_authorization_requires_q1(tmp_path: Path) -> None:
    root, manifest_path, run_log, _ = _fixture(tmp_path)
    _write_log(run_log, [])

    with pytest.raises(ContractError, match="Q1"):
        _ = authorize_external_write(_request(root, manifest_path, run_log))


def test_notion_authorization_requires_configured_target(tmp_path: Path) -> None:
    root, manifest_path, run_log, run_id = _fixture(tmp_path)
    _write_log(run_log, [_stage(run_id, _manifest_digest(manifest_path))])

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
    _write_log(run_log, [_stage(run_id, _manifest_digest(manifest_path))])

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
    _write_log(run_log, [_stage("RUN-other", _manifest_digest(manifest_path))])

    with pytest.raises(ContractError, match="manifest run_id"):
        _ = authorize_external_write(
            _request(root, manifest_path, run_log, run_id="RUN-other")
        )


def test_notion_authorization_binds_q1_to_manifest_topic(tmp_path: Path) -> None:
    root, manifest_path, run_log, run_id = _fixture(tmp_path)
    _write_log(run_log, [_stage(run_id, _manifest_digest(manifest_path), "TOPIC-other")])

    with pytest.raises(ContractError, match="Q1"):
        _ = authorize_external_write(_request(root, manifest_path, run_log))


def test_notion_authorization_scope_is_production(tmp_path: Path) -> None:
    root, manifest_path, run_log, run_id = _fixture(tmp_path)
    _write_log(run_log, [_stage(run_id, _manifest_digest(manifest_path))])

    result = authorize_external_write(_request(root, manifest_path, run_log))

    assert result["decision"] == "not_required"
    assert result["scope"] == "production"


def test_naver_gate_rejects_a_later_failed_q2_after_an_older_pass(
    tmp_path: Path,
) -> None:
    # Given: an earlier Q2 pass followed by a failed retry for the same run.
    root, manifest_path, run_log, run_id = _fixture(tmp_path)
    digest = _manifest_digest(manifest_path)
    failed_q2 = _q2_stage(
        run_id,
        digest,
        status="failed",
        attempt=2,
        started_at="2026-08-27T00:04:00+00:00",
        ended_at="2026-08-27T00:05:00+00:00",
    )
    failed_q2["quality"] = {"storage_integrity": "failed"}
    _write_log(run_log, [_stage(run_id, digest), _q2_stage(run_id, digest), failed_q2])

    # When / Then: Naver is denied; an old pass cannot outlive the failed Q2.
    with pytest.raises(ContractError, match="latest Notion Q2"):
        _ = verify_gate(_naver_request(root, manifest_path, run_log, run_id))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("notion_page_id", "page-other"),
        ("notion_roundtrip_digest", "sha256:" + "0" * 64),
        ("notion_target_id", "datasource-other"),
    ],
)
def test_naver_gate_rejects_latest_q2_with_a_bound_identity_mismatch(
    tmp_path: Path, field: str, value: str
) -> None:
    # Given: a valid old Q2 pass and a later pass bound to the wrong identity.
    root, manifest_path, run_log, run_id = _fixture(tmp_path)
    digest = _manifest_digest(manifest_path)
    latest = _q2_stage(
        run_id,
        digest,
        attempt=2,
        started_at="2026-08-27T00:04:00+00:00",
        ended_at="2026-08-27T00:05:00+00:00",
    )
    quality = latest["quality"]
    assert isinstance(quality, dict)
    quality[field] = value
    _write_log(run_log, [_stage(run_id, digest), _q2_stage(run_id, digest), latest])

    # When / Then: Naver requires the most recent Q2 identity, not the older pass.
    with pytest.raises(ContractError, match="Notion Q2"):
        _ = verify_gate(_naver_request(root, manifest_path, run_log, run_id))


def test_naver_gate_rejects_latest_v2_q2_missing_identity_binding(
    tmp_path: Path,
) -> None:
    # Given: a current telemetry event with its target binding omitted.
    root, manifest_path, run_log, run_id = _fixture(tmp_path)
    digest = _manifest_digest(manifest_path)
    latest = _q2_stage(run_id, digest)
    quality = latest["quality"]
    assert isinstance(quality, dict)
    _ = quality.pop("notion_target_id")
    _write_log(run_log, [_stage(run_id, digest), latest])

    # When / Then: v2 cannot claim a Q2 pass without its Notion target.
    with pytest.raises(ContractError, match="target ID"):
        _ = verify_gate(_naver_request(root, manifest_path, run_log, run_id))


def test_naver_gate_rejects_a_future_requested_q2_verification_time(
    tmp_path: Path,
) -> None:
    # Given: the latest Q2 has a recorded verification instant.
    root, manifest_path, run_log, run_id = _fixture(tmp_path)
    digest = _manifest_digest(manifest_path)
    _write_log(run_log, [_stage(run_id, digest), _q2_stage(run_id, digest)])
    request = _naver_request(root, manifest_path, run_log, run_id)
    future_request = GateRequest(
        root=request.root,
        manifest_path=request.manifest_path,
        run_log=request.run_log,
        gate=request.gate,
        run_id=request.run_id,
        target_id=request.target_id,
        notion_page_id=request.notion_page_id,
        notion_verified_at="2026-08-27T00:04:00+00:00",
        expected_notion_content_digest=request.expected_notion_content_digest,
        notion_content_digest=request.notion_content_digest,
        notion_roundtrip_digest=request.notion_roundtrip_digest,
        q2_artifact_digest=request.q2_artifact_digest,
        blog_id=request.blog_id,
    )

    # When / Then: a later timestamp cannot be substituted for the Q2 result.
    with pytest.raises(ContractError, match="verification time"):
        _ = verify_gate(future_request)


def test_notion_gate_rejects_a_later_failed_q1_after_an_older_pass(
    tmp_path: Path,
) -> None:
    # Given: a valid Q1 followed by a failed content-assembler retry.
    root, manifest_path, run_log, run_id = _fixture(tmp_path)
    digest = _manifest_digest(manifest_path)
    failed_q1 = _stage(
        run_id,
        digest,
    )
    failed_q1["status"] = "failed"
    failed_q1["attempt"] = 2
    failed_q1["started_at"] = "2026-08-27T00:02:00+00:00"
    failed_q1["ended_at"] = "2026-08-27T00:03:00+00:00"
    _write_log(run_log, [_stage(run_id, digest), failed_q1])

    # When / Then: prior Q1 success cannot authorize a subsequent Notion write.
    with pytest.raises(ContractError, match="latest content-assembler Q1"):
        _ = authorize_external_write(_request(root, manifest_path, run_log))


def test_notion_gate_rejects_latest_q1_with_an_old_manifest_digest(
    tmp_path: Path,
) -> None:
    # Given: the latest Q1 is bound to an artifact digest other than the manifest.
    _root, manifest_path, run_log, run_id = _fixture(tmp_path)
    _write_log(run_log, [_stage(run_id, "sha256:" + "0" * 64)])

    # When / Then: external storage remains closed until current Q1 is recorded.
    with pytest.raises(ContractError, match="artifact digest"):
        _ = authorize_external_write(_request(_root, manifest_path, run_log))
