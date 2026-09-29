from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

from tools.contract_types import ContractError, JSONMap, JSONValue


@dataclass(frozen=True, slots=True)
class FreshnessEvidence:
    search_digest: str
    document_digest: str
    source_urls: tuple[str, ...]


def digest_observations(keyword: str, evidence: JSONMap) -> FreshnessEvidence:
    if evidence.get("keyword") != keyword:
        raise ContractError("research evidence keyword mismatch")
    observations = evidence.get("observations")
    if not isinstance(observations, list) or not observations:
        raise ContractError("research evidence unavailable")
    searches: list[JSONMap] = []
    documents: list[JSONMap] = []
    urls: list[str] = []
    for raw in observations:
        item = _observation(raw)
        kind = item.get("source_kind", "legacy_document")
        requested_url = _url(item, "requested_url") or _url(item, "source_url")
        source_url = _url(item, "source_url")
        tree = _text(item, "tree")
        if kind == "search_results":
            _require_query(requested_url, keyword)
            _require_query(source_url, keyword)
            searches.append({"url": requested_url, "source_url": source_url, "content": tree})
        elif kind in {"official_document", "supporting_document", "legacy_document"}:
            documents.append({"url": source_url, "content": tree, "kind": kind})
        else:
            raise ContractError("research observation source_kind is invalid")
        urls.append(source_url)
    return FreshnessEvidence(_digest(searches), _digest(documents), tuple(urls))


def _observation(value: JSONValue) -> JSONMap:
    if not isinstance(value, dict):
        raise ContractError("research observation malformed")
    return value


def _url(item: JSONMap, key: str) -> str:
    value = item.get(key)
    if isinstance(value, str) and value:
        return value
    raise ContractError("research observation URL is invalid")


def _text(item: JSONMap, key: str) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ContractError("research observation content is invalid")
    return value


def _require_query(url: str, keyword: str) -> None:
    if parse_qs(urlparse(url).query).get("query") != [keyword]:
        raise ContractError("research evidence exact query mismatch")


def _digest(value: list[JSONMap]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


__all__ = ["FreshnessEvidence", "digest_observations"]
