from __future__ import annotations

from pathlib import Path

import httpx2
import pytest

from tests._notion_api_test_support import schema_properties as _schema_properties
from tools.contract_types import ContractError
from tools.external_adapter import ExternalSystem, ExternalWriteRequest
from tools.notion_api import NotionApiAdapter, create_notion_client, probe_notion_access
from tools.notion_keychain import NotionApiToken


def test_probe_reads_data_source_and_file_upload_capability() -> None:
    # Given
    paths: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        paths.append(request.url.path)
        if request.url.path == "/v1/data_sources/ds":
            return httpx2.Response(
                200, json={"id": "ds", "properties": _schema_properties()}
            )
        return httpx2.Response(200, json={"results": [], "has_more": False})

    transport = httpx2.MockTransport(handler)

    def factory(token: NotionApiToken) -> httpx2.Client:
        return create_notion_client(token, transport=transport)

    # When
    probe_notion_access(NotionApiToken("secret-token"), "ds", client_factory=factory)

    # Then
    assert paths == ["/v1/data_sources/ds", "/v1/file_uploads"]


def test_deferred_adapter_rejects_dry_run_before_loading_credentials(
    tmp_path: Path,
) -> None:
    # Given
    loaded = False

    def loader() -> NotionApiToken:
        nonlocal loaded
        loaded = True
        return NotionApiToken("secret-token")

    request = ExternalWriteRequest(
        root=tmp_path,
        manifest_path=tmp_path / "missing.json",
        run_log=tmp_path / "missing.jsonl",
        system=ExternalSystem.NOTION,
        gate="notion_write",
        run_id="run",
        target_id="ds",
        dry_run=True,
    )

    # When / Then
    with pytest.raises(ContractError, match="dry-run"):
        _ = NotionApiAdapter(token_loader=loader).write_and_verify(request)
    assert loaded is False
