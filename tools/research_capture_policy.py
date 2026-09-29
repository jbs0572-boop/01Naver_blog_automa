from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from tools.contract_types import ContractError, JSONValue

_DEFAULT_PATH: Final[Path] = Path("config/research-capture-policy.json")


@dataclass(frozen=True, slots=True)
class ResearchCapturePolicy:
    max_searches: int = 3
    max_documents: int = 5
    default_documents: int = 3
    max_social_sources: int = 1
    link_depth: int = 1
    same_url_retry_limit: int = 1
    host_budget_seconds: int = 300
    read_timeout_seconds: int = 30
    instagram_enabled: bool = True
    instagram_max_posts: int = 1


def load_policy(path: Path = _DEFAULT_PATH) -> ResearchCapturePolicy:
    try:
        raw: JSONValue = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ContractError(f"research capture policy unavailable: {path}") from error
    if not isinstance(raw, dict):
        raise ContractError("research capture policy must be an object")
    instagram = raw.get("instagram")
    if not isinstance(instagram, dict):
        raise ContractError("research capture policy instagram section is missing")
    values = {
        key: raw.get(key)
        for key in (
            "max_searches", "max_documents", "default_documents", "max_social_sources",
            "link_depth", "same_url_retry_limit", "host_budget_seconds", "read_timeout_seconds",
        )
    }
    if not all(isinstance(value, int) and not isinstance(value, bool) and value >= 0 for value in values.values()):
        raise ContractError("research capture policy limits must be non-negative integers")
    limits = {key: value for key, value in values.items() if isinstance(value, int) and not isinstance(value, bool)}
    if limits["default_documents"] > limits["max_documents"]:
        raise ContractError("default_documents exceeds max_documents")
    enabled = instagram.get("enabled")
    max_posts = instagram.get("max_posts")
    if not isinstance(enabled, bool) or not isinstance(max_posts, int) or max_posts < 0:
        raise ContractError("instagram policy is malformed")
    return ResearchCapturePolicy(
        max_searches=limits["max_searches"], max_documents=limits["max_documents"],
        default_documents=limits["default_documents"], max_social_sources=limits["max_social_sources"],
        link_depth=limits["link_depth"], same_url_retry_limit=limits["same_url_retry_limit"],
        host_budget_seconds=limits["host_budget_seconds"], read_timeout_seconds=limits["read_timeout_seconds"],
        instagram_enabled=enabled, instagram_max_posts=max_posts,
    )


__all__ = ["ResearchCapturePolicy", "load_policy"]
