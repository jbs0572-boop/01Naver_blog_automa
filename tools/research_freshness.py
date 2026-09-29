from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, NoReturn
from urllib.parse import parse_qs, urlparse

from tools.contract_types import ContractError, JSONMap, JSONValue
from tools.runner_state import atomic_write_json

SCHEMA_VERSION: Final = "research-freshness-v1"
_IGNORED_LINE: Final = re.compile(r"^(광고|파워링크|AD\b)", re.IGNORECASE)
type ReuseDecision = Literal["reuse_allowed", "refresh_required", "evidence_unavailable"]


@dataclass(frozen=True, slots=True)
class ResearchFreshnessRequest:
    root: Path
    run_id: str
    keyword: str
    as_of_date: str
    research_path: Path
    selection_path: Path
    instruction_path: Path


@dataclass(frozen=True, slots=True)
class ResearchReuseAssessment:
    decision: ReuseDecision
    metadata_path: Path
    source_observation_digest: str


def assess_research_reuse(
    request: ResearchFreshnessRequest,
    observation: JSONMap | None,
) -> ResearchReuseAssessment:
    current_path = _metadata_path(request)
    current = _read_metadata(current_path)
    if current is not None:
        _require_static_match(request, current)
        return ResearchReuseAssessment(
            "reuse_allowed",
            current_path,
            _string(current, "source_observation_digest"),
        )
    if observation is None:
        _refresh_required(request, "metadata missing")
    observation_digest = source_observation_digest(request.keyword, observation)
    candidates = _candidate_metadata(request.root)
    if not candidates:
        _refresh_required(request, "metadata missing")
    failures: list[str] = []
    for path in candidates:
        metadata = _read_metadata(path)
        if metadata is None:
            continue
        try:
            _require_static_match(request, metadata)
        except ContractError as error:
            failures.append(str(error))
            continue
        if _string(metadata, "source_observation_digest") != observation_digest:
            failures.append("source observations changed")
            continue
        return ResearchReuseAssessment("reuse_allowed", path, observation_digest)
    _refresh_required(request, failures[0] if failures else "metadata malformed")


def record_research_freshness(
    request: ResearchFreshnessRequest, observation: JSONMap
) -> Path:
    digest = source_observation_digest(request.keyword, observation)
    captured_at = observation.get("captured_at")
    observations = observation.get("observations")
    if not isinstance(captured_at, str) or not isinstance(observations, list):
        raise ContractError("research evidence unavailable")
    source_urls: list[JSONValue] = [
        _string(_map(value, "research observation"), "source_url")
        for value in observations
    ]
    payload: JSONMap = {
        "schema_version": SCHEMA_VERSION,
        "run_id": request.run_id,
        "keyword": request.keyword,
        "as_of_date": request.as_of_date,
        "file_path": request.research_path.relative_to(request.root).as_posix(),
        "file_sha256": _file_digest(request.research_path),
        "selection_input_digest": _file_digest(request.selection_path),
        "instruction_digest": _file_digest(request.instruction_path),
        "observed_at": captured_at,
        "source_urls": source_urls,
        "source_observation_digest": digest,
    }
    path = _metadata_path(request)
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    if path.is_file():
        if path.read_bytes() != encoded:
            raise ContractError("research freshness metadata already exists")
        return path
    atomic_write_json(path, payload)
    return path


def source_observation_digest(keyword: str, evidence: JSONMap) -> str:
    if evidence.get("keyword") != keyword:
        raise ContractError("research evidence keyword mismatch")
    observations = evidence.get("observations")
    if not isinstance(observations, list) or not observations:
        raise ContractError("research evidence unavailable")
    relevant: list[JSONMap] = []
    for raw in observations:
        observation = _map(raw, "research observation")
        requested_keyword = _string(observation, "requested_keyword")
        requested_url = _string(observation, "requested_url")
        source_url = _string(observation, "source_url")
        tree = _string(observation, "tree")
        if requested_keyword != keyword:
            raise ContractError("research evidence keyword mismatch")
        source_kind = observation.get("source_kind", "search_results")
        if source_kind == "search_results":
            for url in (requested_url, source_url):
                query = parse_qs(urlparse(url).query).get("query")
                if query != [keyword]:
                    raise ContractError("research evidence exact query mismatch")
        elif source_kind not in {"official_document", "supporting_document"}:
            raise ContractError("research evidence source_kind is invalid")
        relevant.append(
            {
                "source_kind": source_kind,
                "source_url": source_url,
                "content": _relevant_tree(tree),
            }
        )
    encoded = json.dumps(
        relevant, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _require_static_match(
    request: ResearchFreshnessRequest, metadata: JSONMap
) -> None:
    expected: JSONMap = {
        "keyword": request.keyword,
        "as_of_date": request.as_of_date,
        "file_path": request.research_path.relative_to(request.root).as_posix(),
        "file_sha256": _file_digest(request.research_path),
        "selection_input_digest": _file_digest(request.selection_path),
        "instruction_digest": _file_digest(request.instruction_path),
    }
    for key, value in expected.items():
        if metadata.get(key) != value:
            _refresh_required(request, f"{key} mismatch")


def _read_metadata(path: Path) -> JSONMap | None:
    if not path.is_file():
        return None
    try:
        raw: JSONValue = json.loads(path.read_text(encoding="utf-8"))
        value = _map(raw, "research freshness metadata")
        required = {
            "schema_version", "run_id", "keyword", "as_of_date", "file_path",
            "file_sha256", "selection_input_digest", "instruction_digest",
            "observed_at", "source_urls", "source_observation_digest",
        }
        if set(value) != required or value.get("schema_version") != SCHEMA_VERSION:
            raise ContractError("research freshness metadata malformed")
        for key in required - {"source_urls"}:
            _ = _string(value, key)
        urls = value.get("source_urls")
        if not isinstance(urls, list) or not urls or not all(
            isinstance(item, str) for item in urls
        ):
            raise ContractError("research freshness metadata malformed")
        for key in (
            "file_sha256", "selection_input_digest", "instruction_digest",
            "source_observation_digest",
        ):
            if re.fullmatch(r"sha256:[0-9a-f]{64}", _string(value, key)) is None:
                raise ContractError("research freshness metadata malformed")
        return value
    except (OSError, json.JSONDecodeError, ContractError) as error:
        raise ContractError(
            f"research_refresh_required: malformed metadata at {path}"
        ) from error


def _candidate_metadata(root: Path) -> tuple[Path, ...]:
    directory = root / "metadata/research-freshness"
    return tuple(sorted(directory.glob("*.json"))) if directory.is_dir() else ()


def _metadata_path(request: ResearchFreshnessRequest) -> Path:
    if Path(request.run_id).name != request.run_id:
        raise ContractError("research run_id is unsafe")
    return request.root / "metadata/research-freshness" / f"{request.run_id}.json"


def _file_digest(path: Path) -> str:
    try:
        value = path.read_bytes()
    except OSError as error:
        raise ContractError(f"research_refresh_required: input missing at {path}") from error
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _relevant_tree(tree: str) -> str:
    lines = (
        " ".join(line.split()) for line in tree.splitlines()
        if line.strip() and _IGNORED_LINE.match(line.strip()) is None
    )
    return "\n".join(lines)


def _refresh_required(request: ResearchFreshnessRequest, cause: str) -> NoReturn:
    raise ContractError(
        f"research_refresh_required: {cause}; original={request.research_path}"
    )


def _map(value: JSONValue, label: str) -> JSONMap:
    if not isinstance(value, dict):
        raise ContractError(f"{label} must be an object")
    return value


def _string(value: JSONMap, key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item:
        raise ContractError(f"research metadata {key} is invalid")
    return item


__all__ = [
    "ResearchFreshnessRequest",
    "ResearchReuseAssessment",
    "assess_research_reuse",
    "record_research_freshness",
    "source_observation_digest",
]
