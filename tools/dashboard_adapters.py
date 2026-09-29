from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from tools.browser_gateway import (
    BrowserCapability,
    NaverDraftBrowserGateway,
    load_aside_browser_gateway,
)
from tools.external_adapter import NotionAdapter
from tools.naver_adapter import NaverBrowserAdapter
from tools.notion_api import NotionApiAdapter


@dataclass(frozen=True, slots=True)
class DashboardExternalAdapters:
    notion: NotionAdapter
    naver: NaverBrowserAdapter
    browser_gateway: NaverDraftBrowserGateway

    def close(self) -> None:
        self.browser_gateway.close()


def load_dashboard_external_adapters(root: Path) -> DashboardExternalAdapters:
    gateway = load_aside_browser_gateway(
        root,
        required_capabilities=frozenset({BrowserCapability.NAVER_DRAFT_WRITE}),
    )
    return DashboardExternalAdapters(
        notion=NotionApiAdapter(),
        naver=gateway.create_naver_adapter(
            discard_recovery=False,
        ),
        browser_gateway=gateway,
    )


__all__ = ["DashboardExternalAdapters", "load_dashboard_external_adapters"]
