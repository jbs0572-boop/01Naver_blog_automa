from __future__ import annotations

from pathlib import Path

import pytest

from tools.browser_gateway import BrowserCapability
from tools.contract_types import ContractError, JSONMap
from tools.dashboard_adapters import load_dashboard_external_adapters
from tools.external_adapter import ExternalWriteRequest
from tools.naver_adapter import NaverBrowserAdapter
from tools.test_dashboard import load_server_dependencies


class FixtureNotionAdapter:
    def write_and_verify(self, request: ExternalWriteRequest) -> JSONMap:
        raise AssertionError(f"unexpected Notion write: {request}")


class FixtureNaverAdapter:
    @property
    def target_blog_id(self) -> str:
        return "blog-fixture"

    def prepare(self, title: str, body: str, artifact_digest: str) -> JSONMap:
        _ = (title, body, artifact_digest)
        raise AssertionError("unexpected Naver preparation")

    def save(self, title: str, artifact_digest: str) -> JSONMap:
        _ = (title, artifact_digest)
        raise AssertionError("unexpected Naver save")


class FixtureGateway:
    discard_recovery: bool | None
    close_count: int
    naver: NaverBrowserAdapter

    def __init__(self) -> None:
        self.discard_recovery = None
        self.close_count = 0
        self.naver = FixtureNaverAdapter()

    def create_naver_adapter(self, *, discard_recovery: bool) -> NaverBrowserAdapter:
        self.discard_recovery = discard_recovery
        return self.naver

    def close(self) -> None:
        self.close_count += 1


def test_dashboard_factory_requests_only_naver_draft_capability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    gateway = FixtureGateway()
    requested: list[frozenset[BrowserCapability]] = []

    def load_gateway(
        root: Path, *, required_capabilities: frozenset[BrowserCapability]
    ) -> FixtureGateway:
        assert root == tmp_path
        requested.append(required_capabilities)
        return gateway

    monkeypatch.setattr(
        "tools.dashboard_adapters.load_aside_browser_gateway", load_gateway
    )
    monkeypatch.setattr(
        "tools.dashboard_adapters.NotionApiAdapter", FixtureNotionAdapter
    )

    # When
    adapters = load_dashboard_external_adapters(tmp_path)
    adapters.close()

    # Then
    assert requested == [frozenset({BrowserCapability.NAVER_DRAFT_WRITE})]
    assert adapters.naver is gateway.naver
    assert gateway.discard_recovery is False
    assert gateway.close_count == 1


def test_adapter_startup_failure_disables_writes_without_disabling_dashboard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_to_load(_root: Path) -> None:
        raise ContractError("browser session unavailable")

    monkeypatch.setattr("tools.test_dashboard.load_dashboard_external_adapters", fail_to_load)

    dependencies, adapters = load_server_dependencies(tmp_path)

    assert adapters is None
    assert dependencies.notion_adapter is None
    assert dependencies.naver_adapter is None
    assert dependencies.external_adapter_error == "external_adapters_unavailable"
