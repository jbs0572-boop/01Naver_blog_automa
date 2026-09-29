from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.contract_types import ContractError, JSONMap
from tools.external_adapter import ExternalSystem, ExternalWriteRequest
from tools.manifest import Manifest, ManifestFile
from tools.notion_api import existing_identity
from tools.notion_api_metrics import run_metrics
from tools.notion_api_payload import page_properties


def test_run_metrics_count_per_stage_retries_and_reject_conflicting_batch(
    tmp_path: Path,
) -> None:
    log = tmp_path / "run.jsonl"
    _ = log.write_text(
        "\n".join(
            json.dumps(value)
            for value in (
                {"batch_id": "batch", "stage": "researcher", "attempt": 1},
                {"batch_id": "batch", "stage": "researcher", "attempt": 2},
                {"batch_id": "batch", "stage": "researcher", "attempt": 3},
                {"batch_id": "batch", "stage": "writer", "attempt": 2},
                {
                    "batch_id": "batch",
                    "stage": "content-assembler",
                    "status": "passed",
                    "attempt": 1,
                    "telemetry_version": 2,
                    "duration_ms": 1250.0,
                    "started_at": "2026-08-31T23:00:00+09:00",
                    "ended_at": "2026-08-31T23:59:58+09:00",
                },
            )
        ),
        encoding="utf-8",
    )
    assert run_metrics(log) == (
        "batch",
        3,
        "2026-08-31T23:59:58+09:00",
        1.25,
    )
    _ = log.write_text('{"batch_id":"one"}\n{"batch_id":"two"}', encoding="utf-8")
    with pytest.raises(ContractError, match="conflicting batch_id"):
        _ = run_metrics(log)


def test_run_metrics_freezes_at_existing_page_creation(tmp_path: Path) -> None:
    log = tmp_path / "run.jsonl"
    _ = log.write_text(
        "\n".join(
            json.dumps(value)
            for value in (
                {
                    "batch_id": "batch",
                    "stage": "content-assembler",
                    "status": "passed",
                    "attempt": 1,
                    "telemetry_version": 2,
                    "duration_ms": 1250.0,
                    "ended_at": "2026-08-31T23:59:00+09:00",
                },
                {
                    "batch_id": "batch",
                    "stage": "notion-rider",
                    "status": "failed",
                    "attempt": 1,
                    "telemetry_version": 2,
                    "duration_ms": 5000.0,
                    "ended_at": "2026-09-01T00:00:30+09:00",
                },
            )
        ),
        encoding="utf-8",
    )

    assert run_metrics(log, completed_by="2026-08-31T15:00:00Z") == (
        "batch",
        0,
        "2026-08-31T23:59:00+09:00",
        1.25,
    )


@pytest.mark.parametrize("duration", [float("nan"), float("inf"), float("-inf")])
def test_run_metrics_rejects_nonfinite_duration(
    tmp_path: Path, duration: float
) -> None:
    log = tmp_path / "run.jsonl"
    _ = log.write_text(
        json.dumps(
            {
                "batch_id": "batch",
                "stage": "content-assembler",
                "status": "passed",
                "ended_at": "2026-08-31T23:59:58+09:00",
                "telemetry_version": 2,
                "duration_ms": duration,
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ContractError, match="duration_ms is malformed"):
        _ = run_metrics(log)


def test_page_properties_use_measured_operational_inputs(tmp_path: Path) -> None:
    del tmp_path
    files = (
        ManifestFile("final_markdown", "final/topic.md", 1, 12, "a" * 64),
        ManifestFile("naver_layout", "final/topic-naver-layout.md", 2, 1, "b" * 64),
        ManifestFile("naver_copy", "final/topic-naver-copy.md", 3, 1, "c" * 64),
        ManifestFile("naver_input", "final/topic-naver-input.md", 4, 1, "d" * 64),
        ManifestFile("image_map", "assets/topic/image-map.md", 5, 1, "e" * 64),
        ManifestFile("thumbnail", "assets/topic/thumbnail.png", 6, 3, "f" * 64),
    )
    manifest = Manifest(
        "workflow-contract-v1",
        "workflow-optimized-v1",
        "run",
        "topic",
        "formal",
        "2026-08-31T09:00:00+09:00",
        files,
        "sha256:" + "0" * 64,
    )
    request = ExternalWriteRequest(
        root=Path("."),
        manifest_path=Path("manifest.json"),
        run_log=Path("run.jsonl"),
        system=ExternalSystem.NOTION,
        gate="notion_write",
        run_id="run",
        target_id="ds",
        dry_run=False,
    )

    properties = page_properties(
        manifest,
        request,
        "네이버-블로그-글쓰기-2026-08-31-2차수",
        2,
        4321,
        batch_id="batch-7",
        retries=3,
        completed_at="2026-08-31T23:59:58+09:00",
        duration_seconds=1.25,
    )

    assert properties["검수 완료일"] == {
        "date": {"start": "2026-08-31T23:59:00.000+09:00"}
    }
    assert properties["본문 문자수"] == {"number": 4321}
    assert properties["배치 ID"] == {
        "rich_text": [{"type": "text", "text": {"content": "batch-7"}}]
    }
    assert properties["재시도 횟수"] == {"number": 3}
    assert properties["전체 처리 시간(초)"] == {"number": 1.25}
    assert properties["검수 메모"] == {
        "rich_text": [
            {
                "type": "text",
                "text": {
                    "content": (
                        f"artifact_digest={manifest.artifact_digest}; "
                        "Q1=passed; stage=content-assembler"
                    )
                },
            }
        ]
    }


def test_restart_reuses_existing_operational_identity() -> None:
    match: JSONMap = {
        "id": "page",
        "title": "네이버-블로그-글쓰기-2026-08-31-7차수",
        "execution_date": "2026-08-31",
        "sequence": 7,
    }
    assert existing_identity((match,), "2026-08-31") == (
        "네이버-블로그-글쓰기-2026-08-31-7차수",
        7,
    )
    match["sequence"] = 8
    with pytest.raises(ContractError, match="identity is malformed"):
        _ = existing_identity((match,), "2026-08-31")
    match["sequence"] = True
    with pytest.raises(ContractError, match="identity is malformed"):
        _ = existing_identity((match,), "2026-08-31")
