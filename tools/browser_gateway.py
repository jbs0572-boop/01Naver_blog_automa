from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final, Protocol, final

from tools.aside_browser import AsideCliConfig, AsideReplSession, resolve_aside_cli
from tools.contract_types import ContractError
from tools.naver_adapter import (
    AsideNaverAdapter,
    AsideSmartEditorSession,
    NaverBrowserAdapter,
    NaverConfig,
    load_naver_config,
)

ASIDE_GATEWAY_TIMEOUT_SECONDS: Final = 120.0
ASIDE_GATEWAY_FULL_ACCESS: Final = True


class BrowserCapability(StrEnum):
    CREATOR_ADVISOR_READ = "creator_advisor_read"
    NAVER_DRAFT_WRITE = "naver_draft_write"


class AsideGatewaySession(AsideSmartEditorSession, Protocol):
    def close(self) -> None: ...


type AsideSessionFactory = Callable[[AsideCliConfig], AsideGatewaySession]


@dataclass(frozen=True, slots=True)
class BrowserGatewayDependencies:
    resolve_cli: Callable[[], Path] = resolve_aside_cli
    load_config: Callable[[Path], NaverConfig] = load_naver_config
    session_factory: AsideSessionFactory = AsideReplSession


class NaverDraftBrowserGateway(Protocol):
    def create_naver_adapter(
        self, *, discard_recovery: bool
    ) -> NaverBrowserAdapter: ...

    def close(self) -> None: ...


@final
class AsideBrowserGateway:
    _session: AsideGatewaySession
    _config: NaverConfig
    _closed: bool

    def __init__(self, session: AsideGatewaySession, config: NaverConfig) -> None:
        self._session = session
        self._config = config
        self._closed = False

    def create_naver_adapter(self, *, discard_recovery: bool) -> AsideNaverAdapter:
        if self._closed:
            raise ContractError("Aside Browser gateway is closed")
        return AsideNaverAdapter(
            self._session,
            self._config,
            discard_recovery=discard_recovery,
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._session.close()


def load_aside_browser_gateway(
    root: Path,
    *,
    required_capabilities: frozenset[BrowserCapability],
    dependencies: BrowserGatewayDependencies | None = None,
) -> AsideBrowserGateway:
    supported = frozenset({BrowserCapability.NAVER_DRAFT_WRITE})
    if required_capabilities != supported:
        raise ContractError("Aside Browser capability set is unsupported")
    resolved_dependencies = dependencies or BrowserGatewayDependencies()
    executable = resolved_dependencies.resolve_cli()
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise ContractError("Aside CLI executable is unavailable")
    config = resolved_dependencies.load_config(root / "naver-config.md")
    session = resolved_dependencies.session_factory(
        AsideCliConfig(
            executable=executable,
            timeout_seconds=ASIDE_GATEWAY_TIMEOUT_SECONDS,
            full_access=ASIDE_GATEWAY_FULL_ACCESS,
        )
    )
    return AsideBrowserGateway(session, config)


__all__ = [
    "AsideBrowserGateway",
    "BrowserCapability",
    "BrowserGatewayDependencies",
    "NaverDraftBrowserGateway",
    "load_aside_browser_gateway",
]
