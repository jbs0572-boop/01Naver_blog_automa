from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests._notion_resume_test_support import _request
from tools.contract_types import ContractError
from tools.external_adapter import ExternalAction, ExternalSystem, ExternalWritePlan
from tools.notion_checkpoint import load_checkpoint


def test_checkpoint_rejects_boolean_appended_root_count(tmp_path: Path) -> None:
    # Given
    request, manifest = _request(tmp_path)
    assert request.checkpoint_path is not None
    request.checkpoint_path.parent.mkdir(parents=True)
    digest = manifest["artifact_digest"]
    assert isinstance(digest, str)
    _ = request.checkpoint_path.write_text(
        json.dumps(
            {
                "version": 1,
                "run_id": request.run_id,
                "target_id": request.target_id,
                "artifact_digest": digest,
                "attachments": {},
                "appended_root_count": True,
            }
        ),
        encoding="utf-8",
    )
    plan = ExternalWritePlan(
        ExternalSystem.NOTION,
        ExternalAction.NOTION_WRITE,
        request.run_id,
        request.target_id,
        digest,
        (),
        False,
        True,
    )

    # When / Then
    with pytest.raises(ContractError, match="appended root count is invalid"):
        _ = load_checkpoint(request.checkpoint_path, plan)


def test_checkpoint_converts_non_utf8_json_to_contract_error(tmp_path: Path) -> None:
    request, _ = _request(tmp_path)
    assert request.checkpoint_path is not None
    request.checkpoint_path.parent.mkdir(parents=True)
    _ = request.checkpoint_path.write_bytes(b"\xff")
    plan = ExternalWritePlan(
        ExternalSystem.NOTION,
        ExternalAction.NOTION_WRITE,
        request.run_id,
        request.target_id,
        "digest",
        (),
        False,
        True,
    )
    with pytest.raises(ContractError, match="could not read Notion checkpoint"):
        _ = load_checkpoint(request.checkpoint_path, plan)
