from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tools.contract_types import ContractError, JSONMap
from tools.research_freshness import (
    ResearchFreshnessRequest,
    assess_research_reuse,
    record_research_freshness,
)
from tools.research_freshness_v2 import digest_observations


def _digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _request(root: Path, *, run_id: str = "RUN-one", date: str = "2026-09-11") -> ResearchFreshnessRequest:
    research = root / "research/topic.md"
    selection = root / "research/topic-selection-topic.md"
    instruction = root / "researcher.md"
    return ResearchFreshnessRequest(
        root=root,
        run_id=run_id,
        keyword="topic",
        as_of_date=date,
        research_path=research,
        selection_path=selection,
        instruction_path=instruction,
    )


def _files(root: Path) -> None:
    (root / "research").mkdir()
    _ = (root / "research/topic.md").write_text("facts", encoding="utf-8")
    _ = (root / "research/topic-selection-topic.md").write_text("input", encoding="utf-8")
    _ = (root / "researcher.md").write_text("instruction", encoding="utf-8")


def _observation(tree: str = "공식 정보") -> JSONMap:
    return {
        "capture_id": "RAW-test",
        "captured_at": "2026-09-11T09:00:00+09:00",
        "timezone": "Asia/Seoul",
        "keyword": "topic",
        "observations": [
            {
                "requested_keyword": "topic",
                "requested_url": "https://search.naver.com/search.naver?query=topic",
                "source_url": "https://search.naver.com/search.naver?query=topic",
                "tree": tree,
            }
        ],
    }


def test_same_run_reuses_only_matching_metadata_without_new_observation(tmp_path: Path) -> None:
    _files(tmp_path)
    request = _request(tmp_path)
    _ = record_research_freshness(request, _observation())

    result = assess_research_reuse(request, None)

    assert result.decision == "reuse_allowed"
    assert result.metadata_path.name == "RUN-one.json"


def test_new_run_requires_unchanged_exact_query_observation(tmp_path: Path) -> None:
    _files(tmp_path)
    _ = record_research_freshness(_request(tmp_path), _observation())

    result = assess_research_reuse(
        _request(tmp_path, run_id="RUN-two"), _observation()
    )
    assert result.decision == "reuse_allowed"

    with pytest.raises(ContractError, match="research_refresh_required"):
        _ = assess_research_reuse(
            _request(tmp_path, run_id="RUN-three"), _observation("변경된 공식 정보")
        )


@pytest.mark.parametrize("mutation", ["missing", "file", "date"])
def test_missing_or_changed_research_never_reuses(tmp_path: Path, mutation: str) -> None:
    _files(tmp_path)
    request = _request(tmp_path)
    _ = record_research_freshness(request, _observation())
    before = _digest((tmp_path / "research/topic.md").read_bytes())
    if mutation == "missing":
        (tmp_path / "metadata/research-freshness/RUN-one.json").unlink()
    elif mutation == "file":
        _ = (tmp_path / "research/topic.md").write_text("changed", encoding="utf-8")
    else:
        request = _request(tmp_path, date="2026-09-12")

    with pytest.raises(ContractError, match="research_refresh_required"):
        _ = assess_research_reuse(request, _observation())

    if mutation != "file":
        assert _digest((tmp_path / "research/topic.md").read_bytes()) == before


def test_malformed_metadata_is_refresh_required(tmp_path: Path) -> None:
    _files(tmp_path)
    path = tmp_path / "metadata/research-freshness/RUN-one.json"
    path.parent.mkdir(parents=True)
    _ = path.write_text(json.dumps({"schema_version": "broken"}), encoding="utf-8")

    with pytest.raises(ContractError, match="research_refresh_required"):
        _ = assess_research_reuse(_request(tmp_path), None)


def test_queryless_official_document_is_valid_but_search_query_remains_exact() -> None:
    evidence: JSONMap = {
        "keyword": "topic",
        "observations": [
            {
                "source_kind": "search_results",
                "requested_url": "https://search.naver.com/search.naver?query=topic",
                "source_url": "https://search.naver.com/search.naver?query=topic",
                "tree": "검색 결과",
            },
            {
                "source_kind": "official_document",
                "requested_url": "https://official.example/item?view=full",
                "source_url": "https://official.example/item?view=full",
                "tree": "실제 원문 본문",
            },
        ],
    }

    result = digest_observations("topic", evidence)

    assert result.source_urls == (
        "https://search.naver.com/search.naver?query=topic",
        "https://official.example/item?view=full",
    )


def test_wrong_search_query_is_rejected_without_rejecting_document_url() -> None:
    evidence: JSONMap = {
        "keyword": "topic",
        "observations": [
            {
                "source_kind": "search_results",
                "requested_url": "https://search.naver.com/search.naver?query=other",
                "source_url": "https://search.naver.com/search.naver?query=other",
                "tree": "검색 결과",
            }
        ],
    }

    with pytest.raises(ContractError, match="exact query"):
        _ = digest_observations("topic", evidence)
