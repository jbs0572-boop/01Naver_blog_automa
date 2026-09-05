from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import parse_qs, urlsplit

from tools.contract_types import ContractError, JSONMap


@dataclass(frozen=True, slots=True)
class NaverConfig:
    blog_id: str
    browser_connector: str
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
    @property
    def target_blog_id(self) -> str: ...

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
        "browser_connector",
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
    if values["browser_connector"] != "browser":
        raise ContractError("Naver browser connector must be browser")
    if values["selector_status"] != "verified":
        raise ContractError("Naver selectors require a supervised canary")
    blog_id = values["blog_id"]
    if re.fullmatch(r"[A-Za-z0-9._-]+", blog_id) is None:
        raise ContractError("Naver blog ID is invalid")
    for key in ("write_url", "drafts_url"):
        try:
            parsed = urlsplit(values[key])
            port = parsed.port
        except ValueError as error:
            raise ContractError(f"Naver {key} is invalid") from error
        query_blog_ids = parse_qs(parsed.query).get("blogId", [])
        path_parts = tuple(part for part in parsed.path.split("/") if part)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "blog.naver.com"
            or parsed.username is not None
            or parsed.password is not None
            or port is not None
            or parsed.fragment
            or (blog_id not in path_parts and blog_id not in query_blog_ids)
        ):
            raise ContractError(f"Naver {key} is not bound to the configured blog")
    return NaverConfig(*(values[key] for key in required))


@dataclass(frozen=True, slots=True)
class PlaywrightNaverAdapter:
    session: PlaywrightSession
    config: NaverConfig

    @property
    def target_blog_id(self) -> str:
        return self.config.blog_id

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
