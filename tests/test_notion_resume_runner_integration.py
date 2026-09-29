from __future__ import annotations

from pathlib import Path

from tools.contract_types import JSONMap
from tools.external_adapter import ExternalWriteRequest
from tools.runner_actions import stage_action
from tools.runner_types import JobName, RunnerRequest, StageRunContext


class CapturingAdapter:
    def __init__(self) -> None:
        self.request: ExternalWriteRequest | None = None

    def write_and_verify(self, request: ExternalWriteRequest) -> JSONMap:
        self.request = request
        content_digest = "sha256:" + "1" * 64
        return {
            "storage_integrity": "passed",
            "notion_page_id": "page",
            "notion_last_verified_at": "2026-08-31T09:05:00+00:00",
            "expected_notion_content_digest": content_digest,
            "notion_content_digest": content_digest,
            "notion_roundtrip_digest": content_digest,
            "artifact_digest": "sha256:" + "2" * 64,
        }


def test_runner_passes_deterministic_checkpoint_under_configured_state_dir(
    tmp_path: Path,
) -> None:
    # Given
    state_dir = tmp_path / "custom-state"
    adapter = CapturingAdapter()
    _ = (tmp_path / "notion-config.md").write_text(
        "- 데이터 소스 ID: `datasource-resume`\n", encoding="utf-8"
    )
    request = RunnerRequest(
        root=tmp_path,
        job="daily-generate",
        keyword="topic",
        state_dir=state_dir,
        notion_target_id="datasource-resume",
        notion_adapter=adapter,
    )

    # When
    _ = stage_action(
        StageRunContext(
            request,
            JobName.DAILY_GENERATE,
            "notion-rider",
            "RUN-checkpoint",
            "2026-08-31T09:00:00+09:00",
        )
    )

    # Then
    assert adapter.request is not None
    assert adapter.request.checkpoint_path == (
        state_dir / "notion" / "RUN-checkpoint.json"
    )
