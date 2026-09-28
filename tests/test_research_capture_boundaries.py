from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.contract_types import ContractError, JSONMap, JSONValue
from tools.research_browser_capture import (
    capture_research_sources,
    compact_research_evidence,
)
from tools.research_capture_store import load_ledger
from tools.research_crawler_bridge import (
    SourceProfile,
    instagram_capture_status,
    load_source_profiles,
    profile_for_url,
)
from tools.research_freshness_v2 import digest_observations
from tools.research_readiness import assess_research_readiness


def _copy_capture_config(root: Path) -> None:
    source = Path(__file__).parents[1] / "config"
    destination = root / "config"
    destination.mkdir(exist_ok=True)
    for name in ("research-capture-policy.json", "research-source-profiles.json"):
        _ = (destination / name).write_bytes((source / name).read_bytes())


def test_profiles_allow_only_configured_aside_hosts() -> None:
    profiles = load_source_profiles()
    assert (
        profile_for_url("https://www.instagram.com/example/", profiles).source_id
        == "instagram-public"
    )
    assert (
        profile_for_url(
            "https://www.buan.go.kr/tour/board/list.buan?boardId=BBS_0000295&menuCd=DOM_000000210001005000",
            profiles,
        ).source_id
        == "buan-county"
    )
    assert (
        profile_for_url(
            "https://www.buanmasil.com/buansunset/pages/program/neighbor.php",
            profiles,
        ).source_id
        == "buan-festival"
    )
    assert (
        profile_for_url(
            "https://korean.visitkorea.or.kr/kfes/detail/fstvlDetail.do?fstvlCntntsId=event-id",
            profiles,
        ).source_id
        == "visitkorea-festival"
    )
    assert instagram_capture_status(False, True) == "public_only"


def test_readiness_requires_substantive_source_backed_research(tmp_path: Path) -> None:
    path = tmp_path / "research.md"
    _ = path.write_text("짧은 메모", encoding="utf-8")
    assert assess_research_readiness(path).status == "insufficient"


def test_freshness_v2_separates_search_and_document_digests() -> None:
    evidence: JSONMap = {
        "keyword": "카페",
        "observations": [
            {
                "requested_url": "https://search.naver.com/?query=%EC%B9%B4%ED%8E%98",
                "source_url": "https://example.com",
                "tree": "본문",
            }
        ],
    }
    result = digest_observations("카페", evidence)
    assert result.search_digest != result.document_digest


def test_capture_keeps_explicit_document_observation_separate_from_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: JSONMap = {
        "requested_keyword": "카페",
        "requested_url": "https://search.naver.com/search.naver?query=%EC%B9%B4%ED%8E%98",
        "source_url": "https://search.naver.com/search.naver?query=%EC%B9%B4%ED%8E%98",
        "tree": "검색 결과",
        "document_observations": [
            {
                "source_kind": "official_document",
                "source_url": "https://official.example/cafe",
                "tree": "공식 원문 본문",
            }
        ],
    }

    def fake_capture(_keyword: str) -> JSONMap:
        return observed

    def fake_profiles(_path: Path | None = None) -> tuple[SourceProfile, ...]:
        return (
            SourceProfile(
                "official-example", "official", "official.example", "aside", False
            ),
        )

    monkeypatch.setattr("tools.research_browser_capture._capture", fake_capture)
    monkeypatch.setattr(
        "tools.research_browser_capture.load_source_profiles",
        fake_profiles,
    )

    result = capture_research_sources("카페")

    observations = result["observations"]
    assert isinstance(observations, list)
    first, second = observations
    assert isinstance(first, dict)
    assert isinstance(second, dict)
    assert first["source_kind"] == "search_results"
    assert second["source_kind"] == "official_document"


def test_capture_reuses_only_an_exactly_bound_ledger_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed: JSONMap = {
        "requested_keyword": "카페",
        "requested_url": "https://search.naver.com/search.naver?query=%EC%B9%B4%ED%8E%98",
        "source_url": "https://search.naver.com/search.naver?query=%EC%B9%B4%ED%8E%98",
        "tree": "검색 결과",
        "document_observations": [
            {
                "requested_keyword": "카페",
                "requested_url": "https://www.instagram.com/example/",
                "source_url": "https://www.instagram.com/example/",
                "source_kind": "supporting_document",
                "tree": "공개 원문",
            }
        ],
    }
    calls = 0

    def fake_capture(_keyword: str) -> JSONMap:
        nonlocal calls
        calls += 1
        return observed

    monkeypatch.setattr("tools.research_browser_capture._capture", fake_capture)
    _copy_capture_config(tmp_path)
    selection = tmp_path / "research/topic-selection-카페.md"
    selection.parent.mkdir()
    _ = selection.write_text("first", encoding="utf-8")

    first = capture_research_sources("카페", tmp_path, "RUN-1", "2026-09-13", selection)
    second = capture_research_sources(
        "카페", tmp_path, "RUN-1", "2026-09-13", selection
    )

    assert first == second
    assert calls == 1
    _ = selection.write_text("changed", encoding="utf-8")
    with pytest.raises(ContractError, match="binding mismatch"):
        _ = capture_research_sources("카페", tmp_path, "RUN-1", "2026-09-13", selection)
    assert calls == 1


def test_capture_reservation_survives_interruption_and_exhausts_same_run_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: the one-search policy makes an interrupted host operation consume
    # the complete same-run budget before the operation starts.
    _copy_capture_config(tmp_path)
    policy_path = tmp_path / "config/research-capture-policy.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    policy["max_searches"] = 1
    _ = policy_path.write_text(json.dumps(policy), encoding="utf-8")
    selection = tmp_path / "research/topic-selection-카페.md"
    selection.parent.mkdir()
    _ = selection.write_text("selection", encoding="utf-8")
    calls: list[str] = []

    class InterruptedCapture(BaseException):
        pass

    def interrupt_after_operation_begins(captured_keyword: str) -> JSONMap:
        calls.append(captured_keyword)
        raise InterruptedCapture()

    monkeypatch.setattr(
        "tools.research_browser_capture._capture", interrupt_after_operation_begins
    )

    # When: a process-style interruption escapes the first public capture call.
    with pytest.raises(InterruptedCapture):
        _ = capture_research_sources(
            "카페", tmp_path, "RUN-INTERRUPTED", "2026-09-13", selection
        )

    # Then: a fresh ledger read exposes a durable pre-attempt reservation.
    ledger = load_ledger(tmp_path, "RUN-INTERRUPTED")
    assert len(ledger.entries) == 1
    reservation = ledger.entries[0]
    assert reservation["capture_state"] == "reserved"
    assert reservation["attempt_counters"] == {"searches": 1}

    def unexpected_resume_capture(captured_keyword: str) -> JSONMap:
        calls.append(captured_keyword)
        raise AssertionError("an exhausted same-run budget must block before capture")

    monkeypatch.setattr(
        "tools.research_browser_capture._capture", unexpected_resume_capture
    )

    # And: a fresh public resume observes the reservation and cannot start
    # another host operation after the configured budget is exhausted.
    with pytest.raises(ContractError, match="search budget exhausted"):
        _ = capture_research_sources(
            "카페", tmp_path, "RUN-INTERRUPTED", "2026-09-13", selection
        )
    assert calls == ["카페"]


def test_capture_preserves_two_official_and_one_supporting_originals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _copy_capture_config(tmp_path)
    documents: list[JSONValue] = [
        {
            "requested_url": "https://www.naver.com/",
            "source_url": "https://www.naver.com/",
            "source_kind": "official_document",
            "tree": "공식 원문 1",
        },
        {
            "requested_url": "https://www.naver.com/more.html",
            "source_url": "https://www.naver.com/more.html",
            "source_kind": "official_document",
            "tree": "공식 원문 2",
        },
        {
            "requested_url": "https://www.instagram.com/naver_official/",
            "source_url": "https://www.instagram.com/naver_official/",
            "source_kind": "supporting_document",
            "tree": "보조 원문 1",
        },
    ]
    observed: JSONMap = {
        "requested_keyword": "네이버",
        "requested_url": "https://search.naver.com/search.naver?query=%EB%84%A4%EC%9D%B4%EB%B2%84",
        "source_url": "https://search.naver.com/search.naver?query=%EB%84%A4%EC%9D%B4%EB%B2%84",
        "tree": "검색 결과",
        "document_observations": documents,
    }

    captures: list[str] = []

    def fake_capture(captured_keyword: str) -> JSONMap:
        captures.append(captured_keyword)
        return observed

    monkeypatch.setattr("tools.research_browser_capture._capture", fake_capture)

    result = capture_research_sources("네이버", tmp_path, "RUN-3", "2026-09-13")

    observations = result["observations"]
    assert isinstance(observations, list)
    assert [item["source_kind"] for item in observations if isinstance(item, dict)] == [
        "search_results",
        "official_document",
        "official_document",
        "supporting_document",
    ]
    assert captures == ["네이버"]


def test_compact_evidence_keeps_full_raw_capture_path_and_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tail = "2026-12-31 가격 99000원"
    observed: JSONMap = {
        "requested_keyword": "카페",
        "requested_url": "https://search.naver.com/search.naver?query=%EC%B9%B4%ED%8E%98",
        "source_url": "https://search.naver.com/search.naver?query=%EC%B9%B4%ED%8E%98",
        "tree": "검색 결과",
        "document_observations": [
            {
                "requested_keyword": "카페",
                "requested_url": "https://www.instagram.com/example/",
                "source_url": "https://www.instagram.com/example/",
                "source_kind": "supporting_document",
                "tree": ("본문\n" * 13000) + tail,
            }
        ],
    }

    def fake_capture(_keyword: str) -> JSONMap:
        return observed

    monkeypatch.setattr("tools.research_browser_capture._capture", fake_capture)
    _copy_capture_config(tmp_path)

    evidence = capture_research_sources("카페", tmp_path, "RUN-2", "2026-09-13")
    compact = compact_research_evidence(evidence)

    raw_relative = str(compact["raw_evidence_path"])
    assert raw_relative.startswith("metadata/research-capture/RUN-2-RAW-")
    assert raw_relative.endswith("-raw.json")
    assert str(compact["raw_evidence_sha256"]).startswith("sha256:")
    raw_path = tmp_path / raw_relative
    assert tail in raw_path.read_text(encoding="utf-8")
