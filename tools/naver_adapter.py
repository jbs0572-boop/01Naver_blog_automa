from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from tools.contract_types import ContractError, JSONMap


@dataclass(frozen=True, slots=True)
class NaverConfig:
    blog_id: str
    write_url: str
    drafts_url: str
    viewport: str
    auth_locator: str
    save_locator: str
    title_locator: str
    body_locator: str
    draft_list_locator: str
    selector_status: str


class PlaywrightSession(Protocol):
    def goto(self, url: str, timeout_ms: int) -> None: ...

    def fill(self, selector: str, value: str) -> None: ...

    def click(self, selector: str) -> None: ...

    def text(self, selector: str) -> str: ...

    def screenshot(self, path: str) -> None: ...


class NaverBrowserAdapter(Protocol):
    def prepare(self, title: str, body: str, artifact_digest: str) -> JSONMap: ...

    def save(self, title: str, artifact_digest: str) -> JSONMap: ...


def load_naver_config(path: Path) -> NaverConfig:
    if not path.is_file():
        raise ContractError(f"Naver config is missing: {path}")
    values: dict[str, str] = {}
    for key, value in re.findall(
        r"^-\s*([a-z_]+):\s*`([^`]+)`\s*$",
        path.read_text(encoding="utf-8"),
        re.MULTILINE,
    ):
        values[key] = value
    required = (
        "blog_id",
        "write_url",
        "drafts_url",
        "viewport",
        "auth_locator",
        "save_locator",
        "title_locator",
        "body_locator",
        "draft_list_locator",
        "selector_status",
    )
    if any(key not in values for key in required):
        raise ContractError("Naver config is incomplete")
    if values["viewport"] != "390x844":
        raise ContractError("Naver viewport must be 390x844")
    if values["selector_status"] != "verified":
        raise ContractError("Naver selectors require a supervised canary")
    return NaverConfig(*(values[key] for key in required))


@dataclass(frozen=True, slots=True)
class PlaywrightNaverAdapter:
    session: PlaywrightSession
    config: NaverConfig

    def prepare(self, title: str, body: str, artifact_digest: str) -> JSONMap:
        if not title or not body or not artifact_digest.startswith("sha256:"):
            raise ContractError("Naver preview input is incomplete")
        self.session.goto(self.config.write_url, timeout_ms=30_000)
        if not self.session.text(self.config.auth_locator):
            raise ContractError("Naver authentication locator is not confirmed")
        self.session.fill(self.config.title_locator, title)
        self.session.fill(self.config.body_locator, body)
        return {
            "target_blog_id": self.config.blog_id,
            "naver_title": title,
            "confirmation_requested_at": "pending",
            "artifact_digest": artifact_digest,
        }

    def save(self, title: str, artifact_digest: str) -> JSONMap:
        if not title or not artifact_digest.startswith("sha256:"):
            raise ContractError("Naver save input is incomplete")
        self.session.click(self.config.save_locator)
        self.session.goto(self.config.drafts_url, timeout_ms=30_000)
        if title not in self.session.text(self.config.draft_list_locator):
            raise ContractError("Naver draft title was not found after save")
        return {"draft_status": "saved", "naver_title": title}


__all__ = [
    "NaverBrowserAdapter",
    "NaverConfig",
    "PlaywrightNaverAdapter",
    "PlaywrightSession",
    "load_naver_config",
]
