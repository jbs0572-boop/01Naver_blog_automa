from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from tools.contract_types import ContractError, JSONMap
from tools.runner_state import atomic_write_json, read_state, state_paths


class PublicationLinkStatus(StrEnum):
    MATCHED = "matched"
    NOT_FOUND = "not_found"
    AMBIGUOUS = "ambiguous"
    COLLECTION_FAILED = "collection_failed"


@dataclass(frozen=True, slots=True)
class PublicationCandidate:
    blog_id: str
    url: str
    published_at: str
    title: str
    body: str


def body_digest(body: str) -> str:
    normalized = re.sub(r"\s+", " ", body).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _aware(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ContractError(f"{field} is not a valid ISO-8601 timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ContractError(f"{field} must include a timezone")
    return parsed.astimezone(UTC)


def _title(body: str) -> str:
    return next(
        (line[2:].strip() for line in body.splitlines() if line.startswith("# ")), ""
    )


def collect_publication_link(
    root: Path,
    run_id: str,
    candidates: tuple[PublicationCandidate, ...],
    observed_at: str,
    state_dir: Path | None = None,
) -> JSONMap:
    state_path, _, _ = state_paths(root, run_id, state_dir)
    state = read_state(state_path)
    if state.get("status") != "draft_saved":
        raise ContractError("publication linking requires a draft_saved run")
    target_blog_id = state.get("target_blog_id")
    keyword = state.get("keyword")
    topic_id = state.get("topic_id")
    draft_title = state.get("naver_title")
    input_path = root / "final" / f"{keyword}-naver-input.md"
    if not all(isinstance(value, str) for value in (target_blog_id, keyword, topic_id)):
        raise ContractError("publication link source identity is incomplete")
    if not isinstance(draft_title, str):
        draft_title = _title(input_path.read_text(encoding="utf-8"))
    draft_time = _aware(str(state["updated_at"]), "updated_at")
    observed = _aware(observed_at, "observed_at")
    draft_body_digest = body_digest(input_path.read_text(encoding="utf-8"))
    matches: list[tuple[PublicationCandidate, str]] = []
    for candidate in candidates:
        published = _aware(candidate.published_at, "published_at")
        if (
            candidate.blog_id != target_blog_id
            or not draft_time < published <= observed
        ):
            continue
        title_match = candidate.title.strip() == draft_title.strip()
        body_match = body_digest(candidate.body) == draft_body_digest
        if title_match and body_match:
            matches.append((candidate, "title_and_body"))
        elif title_match:
            matches.append((candidate, "title_exact"))
        elif body_match:
            matches.append((candidate, "body_digest"))
    if len(matches) == 1:
        candidate, method = matches[0]
        result: JSONMap = {
            "run_id": run_id,
            "topic_id": topic_id,
            "naver_post_url": candidate.url,
            "published_at": candidate.published_at,
            "published_title": candidate.title,
            "primary_keyword": keyword,
            "publication_link_status": PublicationLinkStatus.MATCHED.value,
            "observed_at": observed_at,
            "target_blog_id": target_blog_id,
            "match_method": method,
            "published_body_digest": body_digest(candidate.body),
            "collector_version": "publication-link-v1",
        }
    else:
        status = (
            PublicationLinkStatus.NOT_FOUND
            if not matches
            else PublicationLinkStatus.AMBIGUOUS
        )
        result = {
            "run_id": run_id,
            "topic_id": topic_id,
            "naver_post_url": None,
            "published_at": None,
            "published_title": None,
            "primary_keyword": None,
            "publication_link_status": status.value,
            "observed_at": observed_at,
            "target_blog_id": target_blog_id,
            "match_method": None,
            "published_body_digest": None,
            "collector_version": "publication-link-v1",
        }
    output = root / ".automation" / "publications" / f"{run_id}.json"
    if output.is_file():
        existing = read_state(output)
        if (
            existing.get("publication_link_status")
            == PublicationLinkStatus.MATCHED.value
        ):
            return existing
    atomic_write_json(output, result)
    return result


__all__ = [
    "PublicationCandidate",
    "PublicationLinkStatus",
    "body_digest",
    "collect_publication_link",
]
