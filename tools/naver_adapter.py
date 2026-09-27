from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable
from urllib.parse import parse_qs, urlsplit

from tools.contract_types import ContractError, JSONMap, JSONValue
from tools.naver_smarteditor import (
    build_prepare_script,
    build_save_script,
    expected_signature,
    image_files,
    layout_digest,
    plan_payload,
    require_json_map,
)
from tools.notion_copy_parser import parse_naver_copy


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


@runtime_checkable
class StructuredNaverBrowserAdapter(NaverBrowserAdapter, Protocol):
    def prepare_document(
        self, input_path: Path, asset_dir: Path, artifact_digest: str
    ) -> JSONMap: ...


class AsideSmartEditorSession(Protocol):
    def run_json(
        self, operation: str, *, replay_on_stale: bool = True
    ) -> JSONValue: ...

    def stage_uploads(
        self, files: tuple[Path, ...], namespace: str
    ) -> dict[str, Path]: ...

    def evidence_path(self, namespace: str, filename: str) -> Path: ...


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


def _aside_payload(config: NaverConfig) -> JSONMap:
    return {
        "writeUrl": config.write_url,
        "authLocator": config.auth_locator,
        "saveLocator": config.save_locator,
        "titleLocator": config.title_locator,
        "bodyLocator": config.body_locator,
        "draftListLocator": config.draft_list_locator,
    }


def _aside_namespace(artifact_digest: str) -> str:
    if not artifact_digest.startswith("sha256:"):
        raise ContractError("Naver artifact digest is invalid")
    value = artifact_digest.removeprefix("sha256:")
    if re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ContractError("Naver artifact digest is invalid")
    return value[:24]


@dataclass(frozen=True, slots=True)
class AsideNaverAdapter:
    session: AsideSmartEditorSession
    config: NaverConfig
    discard_recovery: bool = True

    @property
    def target_blog_id(self) -> str:
        return self.config.blog_id

    def prepare(self, title: str, body: str, artifact_digest: str) -> JSONMap:
        del title, body, artifact_digest
        raise ContractError("Aside Browser requires structured Naver input")

    def prepare_document(
        self, input_path: Path, asset_dir: Path, artifact_digest: str
    ) -> JSONMap:
        document = parse_naver_copy(input_path)
        signature = expected_signature(document)
        namespace = _aside_namespace(artifact_digest)
        sources = image_files(document, asset_dir)
        staged = self.session.stage_uploads(tuple(sources.values()), namespace)
        plan = plan_payload(document, staged)
        evidence_path = self.session.evidence_path(namespace, "prepared.png")
        payload: JSONMap = {
            "config": _aside_payload(self.config),
            "discardRecovery": self.discard_recovery,
            "plan": plan,
            "expected": signature,
            "evidencePath": str(evidence_path),
        }
        result = require_json_map(self.session.run_json(build_prepare_script(payload)))
        target_id = result.get("target_id")
        actual = result.get("signature")
        if not isinstance(target_id, str) or actual != signature:
            raise ContractError(
                "Aside Browser did not verify the prepared Naver layout"
            )
        prepared_path = self.session.evidence_path(namespace, "prepared.json")
        prepared: JSONMap = {
            "artifact_digest": artifact_digest,
            "blog_id": self.config.blog_id,
            "title": document.title,
            "target_id": target_id,
            "expected": signature,
        }
        _ = prepared_path.write_text(
            json.dumps(prepared, ensure_ascii=False, sort_keys=True), encoding="utf-8"
        )
        return {
            "target_blog_id": self.config.blog_id,
            "naver_title": document.title,
            "confirmation_requested_at": "pending",
            "artifact_digest": artifact_digest,
            "naver_layout_digest": layout_digest(signature),
            "naver_tab_target_id": target_id,
            "naver_prepared_path": str(prepared_path),
            "naver_evidence_path": str(evidence_path),
        }

    def save(self, title: str, artifact_digest: str) -> JSONMap:
        namespace = _aside_namespace(artifact_digest)
        prepared_path = self.session.evidence_path(namespace, "prepared.json")
        if not prepared_path.is_file():
            raise ContractError("prepared Naver layout evidence is missing")
        value: JSONValue = json.loads(prepared_path.read_text(encoding="utf-8"))
        prepared = require_json_map(value)
        expected = prepared.get("expected")
        target_id = prepared.get("target_id")
        if (
            prepared.get("artifact_digest") != artifact_digest
            or prepared.get("blog_id") != self.config.blog_id
            or prepared.get("title") != title
            or not isinstance(expected, dict)
            or not isinstance(target_id, str)
        ):
            raise ContractError("prepared Naver layout evidence is stale")
        evidence_path = self.session.evidence_path(namespace, "saved.png")
        payload: JSONMap = {
            "config": _aside_payload(self.config),
            "discardRecovery": self.discard_recovery,
            "title": title,
            "targetId": target_id,
            "expected": expected,
            "evidencePath": str(evidence_path),
        }
        result = require_json_map(
            self.session.run_json(
                build_save_script(payload), replay_on_stale=False
            )
        )
        if result.get("draft_status") != "saved" or result.get("signature") != expected:
            raise ContractError("Aside Browser did not verify the saved Naver draft")
        return {
            "draft_status": "saved",
            "naver_title": title,
            "naver_layout_digest": layout_digest(expected),
            "naver_saved_evidence_path": str(evidence_path),
        }


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
    "AsideNaverAdapter",
    "AsideSmartEditorSession",
    "NaverBrowserAdapter",
    "NaverConfig",
    "PlaywrightNaverAdapter",
    "PlaywrightSession",
    "StructuredNaverBrowserAdapter",
    "load_naver_config",
]
