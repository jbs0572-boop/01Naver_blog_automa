import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tools.contract_types import ContractError, JSONValue
from tools.topic_metadata import (
    CREATOR_ADVISOR_URL,
    CreatorAdvisorCandidate,
    CreatorAdvisorSnapshot,
    candidates_from_creator_tree,
    canonicalize_snapshot_observations,
    normalize_keyword,
    read_snapshot,
    snapshot_sha256,
    write_snapshot,
)


def test_creator_tree_parser_separates_categories_ranks_and_badges() -> None:
    tree = (
        '- link "주제별 인기유입검색어" [ref=e1]\n'
        '- heading "맛집" [level=3]\n'
        '- list:\n'
        '  - listitem:\n'
        '    - link "한정선 찹쌀떡-" [ref=e2]\n'
        '  - listitem:\n'
        '    - link "동네의 명장들 주물럭 new" [ref=e3]\n'
        '- heading "국내여행" [level=3]\n'
        '- list:\n'
        '  - listitem:\n'
        '    - link "노원 맥주축제 2026 29" [ref=e4]\n'
        '- text: "블로그 게시글로 유입이 많이 된 검색어를 제공합니다."\n'
        '- heading "30-34세 여자" [level=3]\n'
        '- list:\n'
        '  - listitem:\n'
        '    - link "포함하면 안 됨 2" [ref=e5]'
    )

    candidates = candidates_from_creator_tree(tree)

    assert [candidate.keyword for candidate in candidates] == [
        "한정선 찹쌀떡-",
        "동네의 명장들 주물럭",
        "노원 맥주축제 2026",
    ]
    assert [(candidate.category_key, candidate.category_rank) for candidate in candidates] == [
        ("맛집", 1),
        ("맛집", 2),
        ("국내여행", 1),
    ]
    assert candidates[1].raw_observation == {
        "badge": "new",
        "keyword_text": "동네의 명장들 주물럭",
    }


def test_creator_advisor_snapshot_is_written_under_date_and_capture_id(tmp_path: Path) -> None:
    snapshot = CreatorAdvisorSnapshot(
        as_of_date="2026-09-01",
        captured_at=datetime(2026, 9, 1, 9, 0, tzinfo=UTC).isoformat(),
        capture_id="capture-001",
        candidates=(CreatorAdvisorCandidate("가을 여행", 1, trend_index=92.0),),
    )

    path = write_snapshot(tmp_path, snapshot)

    assert path == tmp_path / "metadata" / "creator-advisor" / "2026-09-01" / "capture-001.json"
    assert '"source_url": "' + CREATOR_ADVISOR_URL + '"' in path.read_text(encoding="utf-8")
    with pytest.raises(ContractError):
        _ = write_snapshot(tmp_path, snapshot)


def test_snapshot_reader_validates_creator_advisor_identity(tmp_path: Path) -> None:
    # Given: one valid snapshot and variants with a forged identity field.
    snapshot = CreatorAdvisorSnapshot(
        as_of_date="2026-09-07",
        captured_at=datetime(2026, 9, 7, 9, 0, tzinfo=UTC).isoformat(),
        capture_id="batch-capture",
        candidates=(CreatorAdvisorCandidate("  가을   여행  ", 1),),
    )
    path = write_snapshot(tmp_path, snapshot)

    # When: the trusted reader parses the context-addressed snapshot.
    parsed = read_snapshot(
        path,
        expected_capture_id="batch-capture",
        expected_as_of_date="2026-09-07",
    )

    # Then: identity, candidate spelling, normalization, and bytes are trustworthy.
    assert parsed.capture_id == "batch-capture"
    assert parsed.candidates[0].keyword == "  가을   여행  "
    assert normalize_keyword(parsed.candidates[0].keyword) == "가을 여행"
    assert len(snapshot_sha256(path)) == 64

    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["source_url"] = "https://attacker.invalid/"
    _ = path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ContractError, match="source_url"):
        _ = read_snapshot(
            path,
            expected_capture_id="batch-capture",
            expected_as_of_date="2026-09-07",
        )


def test_snapshot_canonicalization_preserves_known_observations_with_trusted_identity(
    tmp_path: Path,
) -> None:
    # Given: legitimate observations are mixed with hostile identity-bearing fields.
    path = tmp_path / "snapshot.json"
    payload = CreatorAdvisorSnapshot(
        as_of_date="1999-01-01",
        captured_at=datetime(2026, 9, 8, 9, 0, tzinfo=UTC).isoformat(),
        capture_id="hostile-capture",
        channel_id="custom-channel",
        query_period="2026-09-01/2026-09-07",
        access_status="read_only_partial",
        limitations=("로그인 화면 일부만 확인",),
        candidates=(
            CreatorAdvisorCandidate(
                keyword="서울 축제",
                rank=2,
                trend_index=91.5,
                channel_inflow=12.25,
                exposure=4421.0,
                average_exposure_rank=3.75,
                competition_index=0.42,
                duplicate_key="서울축제",
                raw_observation={"display_rank": "2위", "featured": True},
                missing_fields=("historical_median",),
            ),
        ),
    ).as_json()
    payload["schema_version"] = "hostile-schema"
    payload["timezone"] = "UTC"
    payload["source_url"] = "https://attacker.invalid"
    payload["unknown_identity"] = "do-not-preserve"
    _ = path.write_text(json.dumps(payload), encoding="utf-8")
    raw_bytes = path.read_bytes()

    # When: the host canonicalizes identity and the trusted reader parses the result.
    canonicalized = canonicalize_snapshot_observations(
        path,
        expected_capture_id="trusted-capture",
        expected_as_of_date="2026-09-08",
    )

    # Then: the raw file is byte-immutable while the parsed projection has trusted identity.
    assert path.read_bytes() == raw_bytes
    assert canonicalized.capture_id == "trusted-capture"
    assert canonicalized.as_of_date == "2026-09-08"
    assert canonicalized.channel_id == "custom-channel"
    assert canonicalized.query_period == "2026-09-01/2026-09-07"
    assert canonicalized.access_status == "read_only_partial"
    assert canonicalized.limitations == ("로그인 화면 일부만 확인",)
    assert canonicalized.candidates == (
        CreatorAdvisorCandidate(
            keyword="서울 축제",
            rank=2,
            trend_index=91.5,
            channel_inflow=12.25,
            exposure=4421.0,
            average_exposure_rank=3.75,
            competition_index=0.42,
            duplicate_key="서울축제",
            raw_observation={"display_rank": "2위", "featured": True},
            missing_fields=("historical_median",),
        ),
    )


def test_rank_only_snapshot_preserves_missing_trend_as_null_and_limitations(
    tmp_path: Path,
) -> None:
    # Given: a raw rank-only observation explicitly records its limitation.
    path = tmp_path / "rank-only.json"
    payload = CreatorAdvisorSnapshot(
        as_of_date="2026-09-07",
        captured_at=datetime(2026, 9, 7, 9, 0, tzinfo=UTC).isoformat(),
        capture_id="rank-only",
        candidates=(CreatorAdvisorCandidate("첫 후보", 1, trend_index=None),),
        limitations=("trend index unavailable",),
    ).as_json()
    _ = path.write_text(json.dumps(payload), encoding="utf-8")
    raw_bytes = path.read_bytes()

    # When: the observation is parsed for canonical downstream use.
    snapshot = canonicalize_snapshot_observations(
        path,
        expected_capture_id="rank-only",
        expected_as_of_date="2026-09-07",
    )

    # Then: missing trend stays missing and the raw evidence remains untouched.
    assert snapshot.candidates[0].trend_index is None
    assert snapshot.limitations == ("trend index unavailable",)
    assert path.read_bytes() == raw_bytes


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("trend_index", "high"),
        ("channel_inflow", False),
        ("exposure", []),
        ("average_exposure_rank", "third"),
        ("competition_index", {}),
        ("duplicate_key", 3),
        ("raw_observation", []),
        ("missing_fields", ["known", 3]),
    ],
)
def test_snapshot_canonicalization_rejects_malformed_candidate_known_fields(
    tmp_path: Path, field_name: str, invalid_value: JSONValue
) -> None:
    # Given: one known candidate field has a value outside its contract.
    path = tmp_path / "snapshot.json"
    payload = CreatorAdvisorSnapshot(
        as_of_date="2026-09-08",
        captured_at=datetime(2026, 9, 8, 9, 0, tzinfo=UTC).isoformat(),
        capture_id="capture",
        candidates=(CreatorAdvisorCandidate("서울 축제", 1),),
    ).as_json()
    candidates = payload["candidates"]
    assert isinstance(candidates, list)
    candidate = candidates[0]
    assert isinstance(candidate, dict)
    candidate[field_name] = invalid_value
    _ = path.write_text(json.dumps(payload), encoding="utf-8")

    # When/Then: the typed boundary rejects the malformed known observation.
    with pytest.raises(ContractError, match=field_name):
        _ = canonicalize_snapshot_observations(
            path,
            expected_capture_id="capture",
            expected_as_of_date="2026-09-08",
        )


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("channel_id", 1),
        ("query_period", []),
        ("access_status", False),
        ("limitations", ["known", 1]),
    ],
)
def test_snapshot_canonicalization_rejects_malformed_snapshot_known_fields(
    tmp_path: Path, field_name: str, invalid_value: JSONValue
) -> None:
    # Given: one known snapshot field has a malformed value.
    path = tmp_path / "snapshot.json"
    payload = CreatorAdvisorSnapshot(
        as_of_date="2026-09-08",
        captured_at=datetime(2026, 9, 8, 9, 0, tzinfo=UTC).isoformat(),
        capture_id="capture",
        candidates=(CreatorAdvisorCandidate("서울 축제", 1),),
    ).as_json()
    payload[field_name] = invalid_value
    _ = path.write_text(json.dumps(payload), encoding="utf-8")

    # When/Then: the typed boundary rejects malformed snapshot metadata.
    with pytest.raises(ContractError, match=field_name):
        _ = canonicalize_snapshot_observations(
            path,
            expected_capture_id="capture",
            expected_as_of_date="2026-09-08",
        )
