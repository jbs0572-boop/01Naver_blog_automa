from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tools.blog_stats_import import BlogStatsImportRequest, import_blog_stats
from tools.contract_types import ContractError, JSONMap, JSONValue
from tools.publication_metrics_link import (
    PublicationAttributionRequest,
    link_publication,
)


def _owner(path: Path, *blog_ids: str) -> Path:
    _ = path.write_text(
        json.dumps(
            {
                "schema_version": "blog-stats-owner-v1",
                "allowed_blog_ids": list(blog_ids),
            }
        ),
        encoding="utf-8",
    )
    return path


def _link(root: Path, post_id: str = "POST-001") -> None:
    _ = link_publication(
        PublicationAttributionRequest(
            root=root,
            run_id="RUN-001",
            blog_post_id=post_id,
            published_at="2026-09-01T09:00:00+09:00",
            captured_at="2026-09-08T09:00:00+09:00",
            source_identity="legacy-import",
            topic_id="TOPIC-001",
            keyword="fixture",
            artifact_digest="sha256:" + "a" * 64,
            score_version="topic-baseline-v1",
            legacy_identity="fixture-approved",
        )
    )


def _csv(path: Path, *, post_id: str = "POST-001", blog_id: str = "owner") -> Path:
    header = "mapping_version,capture_id,blog_id,blog_post_id,coverage_start,coverage_end,captured_at,status,views,search_inflow,exposure,average_exposure_rank,demographics_json\n"
    row = f'blog-stats-columns-v1,CAP-001,{blog_id},{post_id},2026-09-01,2026-09-07,2026-09-08T09:00:00+09:00,mature,0,12,,,"[{{""dimension"":""age"",""bucket"":""30-39"",""count"":5}}]"\n'
    _ = path.write_text(
        header + row,
        encoding="utf-8",
    )
    return path


def _row(capture_id: str, search_inflow: int = 12) -> JSONMap:
    return {
        "capture_id": capture_id,
        "blog_id": "owner",
        "blog_post_id": "POST-001",
        "coverage_start": "2026-09-01",
        "coverage_end": "2026-09-07",
        "captured_at": "2026-09-08T09:00:00+09:00",
        "status": "mature",
        "views": 20,
        "search_inflow": search_inflow,
        "exposure": None,
        "average_exposure_rank": None,
        "demographics": [],
    }


def _json_batch(path: Path, *rows: JSONMap) -> Path:
    _ = path.write_text(
        json.dumps(
            {
                "schema_version": "blog-stats-import-v1",
                "mapping_version": "blog-stats-columns-v1",
                "observations": rows,
            }
        ),
        encoding="utf-8",
    )
    return path


def test_import_csv_preserves_zero_null_coverage_and_link_digest(
    tmp_path: Path,
) -> None:
    # Given: one owner export row linked to a known publication.
    _link(tmp_path)
    source = _csv(tmp_path / "stats.csv")
    before = hashlib.sha256(source.read_bytes()).hexdigest()

    # When: the versioned CSV is imported through the domain boundary.
    result = import_blog_stats(
        BlogStatsImportRequest(
            source, _owner(tmp_path / "owner.json", "owner"), tmp_path
        )
    )

    # Then: explicit zero differs from blanks and provenance remains frozen.
    assert len(result.snapshots) == 1
    payload = result.snapshots[0]
    assert payload["views"] == 0
    assert payload["search_inflow"] == 12
    assert payload["exposure"] is None
    assert payload["average_exposure_rank"] is None
    assert payload["missing_fields"] == ["average_exposure_rank", "exposure"]
    assert payload["coverage_end"] == "2026-09-07"
    link_digest = payload["publication_link_digest"]
    assert isinstance(link_digest, str)
    assert link_digest.startswith("sha256:")
    assert payload["demographics"] == [
        {"bucket": "30-39", "count": 5, "dimension": "age"}
    ]
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    assert Path(result.paths[0]).read_bytes()


def test_import_json_supports_delayed_status_and_explicit_zero(tmp_path: Path) -> None:
    # Given: a versioned JSON export with an optional metric supplied as zero.
    _link(tmp_path)
    source = tmp_path / "stats.json"
    _ = source.write_text(
        json.dumps(
            {
                "schema_version": "blog-stats-import-v1",
                "mapping_version": "blog-stats-columns-v1",
                "observations": [
                    {
                        "capture_id": "CAP-JSON",
                        "blog_id": "owner",
                        "blog_post_id": "POST-001",
                        "coverage_start": "2026-09-01",
                        "coverage_end": "2026-09-07",
                        "captured_at": "2026-09-08T09:00:00+09:00",
                        "status": "delayed",
                        "views": 0,
                        "search_inflow": 0,
                        "exposure": 0,
                        "average_exposure_rank": None,
                        "demographics": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    # When: JSON is imported.
    result = import_blog_stats(
        BlogStatsImportRequest(
            source, _owner(tmp_path / "owner.json", "owner"), tmp_path
        )
    )

    # Then: delayed is retained and zero is not treated as absent.
    assert result.snapshots[0]["status"] == "delayed"
    assert result.snapshots[0]["exposure"] == 0


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        ({"blog_id": "other"}, "owner mismatch"),
        ({"blog_post_id": "UNKNOWN"}, "unknown publication"),
        ({"captured_at": "2026-09-08T09:00:00Z"}, "timezone"),
        ({"coverage_start": "2026-09-08"}, "coverage interval"),
        (
            {"demographics_json": '[{"dimension":"age","bucket":"20-29","count":3}]'},
            "privacy violation",
        ),
    ],
)
def test_invalid_batch_rejects_without_snapshots(
    tmp_path: Path, mutate: dict[str, str], message: str
) -> None:
    # Given: a two-row batch where only the second row violates a trust boundary.
    _link(tmp_path)
    source = tmp_path / "stats.json"
    valid: JSONMap = {
        "capture_id": "CAP-001",
        "blog_id": "owner",
        "blog_post_id": "POST-001",
        "coverage_start": "2026-09-01",
        "coverage_end": "2026-09-07",
        "captured_at": "2026-09-08T09:00:00+09:00",
        "status": "mature",
        "views": 10,
        "search_inflow": 5,
        "exposure": None,
        "average_exposure_rank": None,
        "demographics": [],
    }
    invalid = dict(valid)
    invalid["capture_id"] = "CAP-002"
    invalid.update(mutate)
    if "demographics_json" in invalid:
        invalid["demographics"] = json.loads(str(invalid.pop("demographics_json")))
    _ = source.write_text(
        json.dumps(
            {
                "schema_version": "blog-stats-import-v1",
                "mapping_version": "blog-stats-columns-v1",
                "observations": [valid, invalid],
            }
        ),
        encoding="utf-8",
    )

    # When/Then: validation completes for the whole batch before any write.
    with pytest.raises(ContractError, match=message):
        _ = import_blog_stats(
            BlogStatsImportRequest(
                source, _owner(tmp_path / "owner.json", "owner"), tmp_path
            )
        )
    assert not (tmp_path / "metadata/blog-stats").exists()


def test_rejects_non_monotonic_cumulative_observation(tmp_path: Path) -> None:
    # Given: a prior cumulative snapshot and a later lower observation.
    _link(tmp_path)
    owner = _owner(tmp_path / "owner.json", "owner")
    first = _csv(tmp_path / "first.csv")
    _ = import_blog_stats(BlogStatsImportRequest(first, owner, tmp_path))
    second = tmp_path / "second.json"
    value: JSONValue = json.loads(
        (tmp_path / "metadata/blog-stats/owner/2026-09-07/CAP-001.json").read_text(
            encoding="utf-8"
        )
    )
    assert isinstance(value, dict)
    value.update({"capture_id": "CAP-002"})
    observation = {
        key: value[key]
        for key in (
            "capture_id",
            "blog_id",
            "blog_post_id",
            "coverage_start",
            "coverage_end",
            "captured_at",
            "status",
            "views",
            "search_inflow",
            "exposure",
            "average_exposure_rank",
            "demographics",
        )
    }
    observation["coverage_end"] = "2026-09-08"
    observation["captured_at"] = "2026-09-09T09:00:00+09:00"
    observation["search_inflow"] = 11
    _ = second.write_text(
        json.dumps(
            {
                "schema_version": "blog-stats-import-v1",
                "mapping_version": "blog-stats-columns-v1",
                "observations": [observation],
            }
        ),
        encoding="utf-8",
    )

    # When/Then: cumulative metrics cannot decrease across observations.
    with pytest.raises(ContractError, match="cumulative metric decreased"):
        _ = import_blog_stats(BlogStatsImportRequest(second, owner, tmp_path))
    assert len(tuple((tmp_path / "metadata/blog-stats").rglob("*.json"))) == 1


@pytest.mark.parametrize(
    "bad_key",
    [
        "visitor_id",
        "session_id",
        "referrer_query",
        "comments",
        "neighbors",
        "cookie",
        "unknown",
    ],
)
def test_rejects_forbidden_and_unknown_fields(tmp_path: Path, bad_key: str) -> None:
    # Given: an otherwise valid JSON row with an unapproved field.
    _link(tmp_path)
    source = tmp_path / "stats.json"
    row: JSONMap = {
        "capture_id": "CAP",
        "blog_id": "owner",
        "blog_post_id": "POST-001",
        "coverage_start": "2026-09-01",
        "coverage_end": "2026-09-07",
        "captured_at": "2026-09-08T09:00:00+09:00",
        "status": "mature",
        "views": 1,
        "search_inflow": 1,
        "exposure": None,
        "average_exposure_rank": None,
        "demographics": [],
        bad_key: "do-not-store",
    }
    _ = source.write_text(
        json.dumps(
            {
                "schema_version": "blog-stats-import-v1",
                "mapping_version": "blog-stats-columns-v1",
                "observations": [row],
            }
        ),
        encoding="utf-8",
    )

    # When/Then: fail closed without reflecting or persisting the value.
    with pytest.raises(ContractError):
        _ = import_blog_stats(
            BlogStatsImportRequest(
                source, _owner(tmp_path / "owner.json", "owner"), tmp_path
            )
        )
    assert not (tmp_path / "metadata/blog-stats").exists()


def test_owner_config_rejects_secrets_and_symlink(tmp_path: Path) -> None:
    # Given: owner configs with either secret material or indirection.
    _link(tmp_path)
    source = _csv(tmp_path / "stats.csv")
    secret = tmp_path / "secret.json"
    _ = secret.write_text(
        json.dumps(
            {
                "schema_version": "blog-stats-owner-v1",
                "allowed_blog_ids": ["owner"],
                "cookie": "hidden",
            }
        ),
        encoding="utf-8",
    )
    symlink = tmp_path / "owner.json"
    symlink.symlink_to(secret)

    # When/Then: both configs fail before snapshot creation.
    with pytest.raises(ContractError):
        _ = import_blog_stats(BlogStatsImportRequest(source, secret, tmp_path))
    with pytest.raises(ContractError, match="symlink"):
        _ = import_blog_stats(BlogStatsImportRequest(source, symlink, tmp_path))
    assert not (tmp_path / "metadata/blog-stats").exists()


def test_later_preexisting_duplicate_leaves_no_earlier_batch_snapshot(
    tmp_path: Path,
) -> None:
    # Given: CAP-002 exists before a batch ordered CAP-001 then CAP-002.
    _link(tmp_path)
    owner = _owner(tmp_path / "owner.json", "owner")
    _ = import_blog_stats(
        BlogStatsImportRequest(
            _json_batch(tmp_path / "prior.json", _row("CAP-002")), owner, tmp_path
        )
    )
    before = tuple((tmp_path / "metadata/blog-stats").rglob("*.json"))
    prior_bytes = before[0].read_bytes()

    # When: the two-row batch reaches the preexisting destination.
    with pytest.raises(ContractError, match="append-only"):
        _ = import_blog_stats(
            BlogStatsImportRequest(
                _json_batch(tmp_path / "batch.json", _row("CAP-001"), _row("CAP-002")),
                owner,
                tmp_path,
            )
        )

    # Then: the preexisting file is untouched and CAP-001 was never published.
    after = tuple((tmp_path / "metadata/blog-stats").rglob("*.json"))
    assert after == before
    assert after[0].read_bytes() == prior_bytes
    assert not tuple(tmp_path.rglob(".batch-*"))


def test_second_publication_failure_rolls_back_only_new_batch_files(
    tmp_path: Path,
) -> None:
    # Given: a two-row batch and a deterministic failure before its second publish.
    _link(tmp_path)

    def fail_second(index: int, phase: str) -> None:
        if index == 1 and phase == "publish":
            raise OSError("injected second publication failure")

    # When: the transactional store encounters that injected failure.
    with pytest.raises(ContractError, match="batch write failed safely"):
        _ = import_blog_stats(
            BlogStatsImportRequest(
                _json_batch(tmp_path / "batch.json", _row("CAP-001"), _row("CAP-002")),
                _owner(tmp_path / "owner.json", "owner"),
                tmp_path,
                fail_second,
            )
        )

    # Then: neither authoritative file nor staging debris survives.
    assert not tuple((tmp_path / "metadata/blog-stats").rglob("*.json"))
    assert not tuple(tmp_path.rglob(".batch-*"))


def test_post_link_failure_rolls_back_every_owned_batch_file(
    tmp_path: Path,
) -> None:
    # Given: one historical snapshot and a publisher that fails after linking item 2.
    _link(tmp_path)
    owner = _owner(tmp_path / "owner.json", "owner")
    _ = import_blog_stats(
        BlogStatsImportRequest(
            _json_batch(tmp_path / "prior.json", _row("CAP-PRIOR")), owner, tmp_path
        )
    )
    before = tuple((tmp_path / "metadata/blog-stats").rglob("*.json"))
    prior_bytes = before[0].read_bytes()

    def fail_after_second_publish(index: int, phase: str) -> None:
        if index == 1 and phase == "post_publish":
            raise OSError("injected post-link failure")

    # When: the second final exists but control never returns to created.append.
    with pytest.raises(ContractError, match="batch write failed safely"):
        _ = import_blog_stats(
            BlogStatsImportRequest(
                _json_batch(tmp_path / "batch.json", _row("CAP-001"), _row("CAP-002")),
                owner,
                tmp_path,
                fail_after_second_publish,
            )
        )

    # Then: only the historical file remains and all current-batch debris is gone.
    after = tuple((tmp_path / "metadata/blog-stats").rglob("*.json"))
    assert after == before
    assert after[0].read_bytes() == prior_bytes
    assert not tuple(tmp_path.rglob(".batch-*"))


@pytest.mark.parametrize("target", ["input", "owner"])
def test_parent_directory_symlink_is_rejected(tmp_path: Path, target: str) -> None:
    # Given: a regular file is reachable only by traversing a symlinked parent.
    _link(tmp_path)
    real = tmp_path / "real"
    real.mkdir()
    input_path = _json_batch(real / "stats.json", _row("CAP-001"))
    owner_path = _owner(real / "owner.json", "owner")
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    requested_input = alias / "stats.json" if target == "input" else input_path
    requested_owner = alias / "owner.json" if target == "owner" else owner_path

    # When/Then: every parent component is opened no-follow before any write.
    with pytest.raises(ContractError, match="unsafe"):
        _ = import_blog_stats(
            BlogStatsImportRequest(requested_input, requested_owner, tmp_path)
        )
    assert not (tmp_path / "metadata/blog-stats").exists()
