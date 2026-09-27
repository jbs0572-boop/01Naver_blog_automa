from __future__ import annotations

from pathlib import Path

import pytest

from tools.browser_gateway import BrowserCapability
from tools.contract_types import JSONMap
from tools.dashboard_adapters import load_dashboard_external_adapters
from tools.external_adapter import ExternalWriteRequest
from tools.naver_adapter import NaverBrowserAdapter


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
    monkeypatch.setenv("NAVER_E2E_DISCARD_RECOVERY", "1")

    # When
    adapters = load_dashboard_external_adapters(tmp_path)
    adapters.close()

    # Then
    assert requested == [frozenset({BrowserCapability.NAVER_DRAFT_WRITE})]
    assert adapters.naver is gateway.naver
    assert gateway.discard_recovery is True
    assert gateway.close_count == 1
