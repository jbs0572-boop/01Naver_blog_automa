from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from tools.contract_types import ContractError, JSONValue


@dataclass(frozen=True, slots=True)
class SourceProfile:
    source_id: str
    kind: str
    host: str
    fetcher: str
    requires_credentials: bool


def load_source_profiles(path: Path | None = None) -> tuple[SourceProfile, ...]:
    selected_path = path or Path("config/research-source-profiles.json")
    try:
        raw: JSONValue = json.loads(selected_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ContractError("research source profiles unavailable") from error
    if not isinstance(raw, dict) or not isinstance(raw.get("sources"), list):
        raise ContractError("research source profiles malformed")
    source_items = raw["sources"]
    if not isinstance(source_items, list):
        raise ContractError("research source profiles malformed")
    profiles: list[SourceProfile] = []
    for item in source_items:
        if not isinstance(item, dict):
            raise ContractError("research source profile malformed")
        source_id = item.get("id")
        kind = item.get("kind")
        host = item.get("host")
        fetcher = item.get("fetcher")
        credentials = item.get("requires_credentials", False)
        if not isinstance(source_id, str) or not isinstance(kind, str) or not isinstance(host, str) or not isinstance(fetcher, str) or not isinstance(credentials, bool):
            raise ContractError("research source profile malformed")
        if fetcher != "aside":
            raise ContractError("research source profile must use Aside")
        if kind not in {"search", "official", "supporting"}:
            raise ContractError("research source profile kind is invalid")
        profiles.append(SourceProfile(source_id, kind, host, fetcher, credentials))
    return tuple(profiles)


def profile_for_url(url: str, profiles: tuple[SourceProfile, ...]) -> SourceProfile:
    host = urlparse(url).netloc.lower()
    for profile in profiles:
        if host == profile.host:
            return profile
    raise ContractError(f"research source host is outside configured profiles: {host}")


def instagram_capture_status(credentials_present: bool, public_account: bool) -> str:
    if not public_account:
        return "skipped_non_public"
    if credentials_present:
        return "eligible"
    return "public_only"


__all__ = ["SourceProfile", "instagram_capture_status", "load_source_profiles", "profile_for_url"]
