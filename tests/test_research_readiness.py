from pathlib import Path

import pytest

from tools.contract_types import ContractError
from tools.research_readiness import (
    assess_research_readiness,
    require_research_readiness,
)


def test_search_only_evidence_cannot_satisfy_official_readiness(tmp_path: Path) -> None:
    path = tmp_path / "research.md"
    text = (
        "---\nofficial_source_required: true\ntitle_claims:\n"
        + "  - claim_id: CLAIM-1\n    claim: 상품 특징\n"
        + "    source_ids: [SRC-SEARCH]\n---\n"
        + "자료 " * 100
        + "\n출처 ID: SRC-SEARCH\n출처 유형: search_results\n"
        + "URL: https://search.naver.com/search.naver?query=상품\n"
    )
    _ = path.write_text(text, encoding="utf-8")

    result = assess_research_readiness(path)

    assert result.status == "insufficient"
    assert "official_document_evidence" in result.missing


def test_official_document_evidence_must_be_connected_to_each_claim(
    tmp_path: Path,
) -> None:
    path = tmp_path / "research.md"
    text = (
        "---\nofficial_source_required: true\ntitle_claims:\n"
        + "  - claim_id: CLAIM-1\n    claim: 상품 특징\n"
        + "    source_ids: [SRC-DOC]\n---\n"
        + "## 주장 원장\n| claim_id | claim | source_ids | evidence_status |\n"
        + "| --- | --- | --- | --- |\n"
        + "| CLAIM-1 | 상품 특징 | SRC-DOC | confirmed |\n"
        + "## 출처 목록\n| source_id | source_kind | url | evidence_status |\n"
        + "| --- | --- | --- | --- |\n"
        + "| SRC-DOC | official_document | https://official.example/item | confirmed |\n"
        + "## 원문 증거\n- source_id: SRC-DOC\n"
        + "  captured_at: 2026-09-13T09:00:00+09:00\n"
        + "  observed_text: 공식 문서의 실제 본문\n"
    )
    _ = path.write_text(text, encoding="utf-8")

    assert assess_research_readiness(path).status == "ready"


def test_unknown_source_id_cannot_make_research_ready(tmp_path: Path) -> None:
    path = tmp_path / "research.md"
    text = (
        "---\nofficial_source_required: true\n---\n# 조사\n## 주장 원장\n| claim_id | claim | source_ids | evidence_status |\n"
        + "| --- | --- | --- | --- |\n"
        + "| CLAIM-1 | 사실 | SRC-MISSING | confirmed |\n"
        + "## 출처 목록\n| source_id | source_kind | url | evidence_status |\n"
        + "| --- | --- | --- | --- |\n"
        + "| SRC-DOC | official_document | https://official.example/item | confirmed |\n"
        + "## 원문 증거\n- source_id: SRC-DOC\n  observed_text: 본문\n"
    )
    _ = path.write_text(text, encoding="utf-8")

    with pytest.raises(ContractError, match="source_id"):
        _ = require_research_readiness(path)


def test_long_prose_and_url_without_claim_linkage_is_not_ready(tmp_path: Path) -> None:
    path = tmp_path / "research.md"
    _ = path.write_text(
        ("설명만 있는 자료 " * 100) + "https://official.example/item",
        encoding="utf-8",
    )

    result = assess_research_readiness(path)

    assert result.status == "insufficient"
    assert "claim_evidence_linkage" in result.missing
    assert "document_evidence" in result.missing
