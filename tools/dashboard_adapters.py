from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from tools.aside_browser import AsideCliConfig, AsideReplSession, resolve_aside_cli
from tools.external_adapter import NotionAdapter
from tools.naver_adapter import (
    NaverBrowserAdapter,
    PlaywrightNaverAdapter,
    load_naver_config,
)
from tools.notion_api import NotionApiAdapter


@dataclass(frozen=True, slots=True)
class DashboardExternalAdapters:
    notion: NotionAdapter
    naver: NaverBrowserAdapter


def load_dashboard_external_adapters(root: Path) -> DashboardExternalAdapters:
    config = load_naver_config(root / "naver-config.md")
    session = AsideReplSession(AsideCliConfig(resolve_aside_cli()))
    return DashboardExternalAdapters(
        notion=NotionApiAdapter(),
        naver=PlaywrightNaverAdapter(session, config),
    )


__all__ = ["DashboardExternalAdapters", "load_dashboard_external_adapters"]
