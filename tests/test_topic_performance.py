from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.contract_types import ContractError, JSONMap, JSONValue
from tools.topic_feedback_models import compute_digest
from tools.topic_performance import compute_cohorts, serialize_cohorts


def _write_artifact(root: Path, relative: str, payload: JSONMap) -> None:
    payload["digest"] = compute_digest(payload)
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")), encoding="utf-8"
    )


def _link(
    root: Path,
    *,
    run_id: str = "RUN-001",
    post_id: str = "POST-001",
    published_at: str = "2026-09-01T09:00:00+09:00",
) -> str:
    payload: JSONMap = {
        "schema_version": "publication-link-v1",
        "captured_at": published_at,
        "as_of_date": published_at[:10],
        "timezone": "Asia/Seoul",
        "limitations": [],
        "input_digests": ["sha256:" + "a" * 64],
        "missing_fields": ["legacy_identity"],
        "status": "mature",
        "digest": "",
        "run_id": run_id,
        "topic_id": f"TOPIC-{run_id}",
        "keyword": f"keyword-{run_id}",
        "blog_post_id": post_id,
        "published_at": published_at,
        "artifact_digest": "sha256:" + "a" * 64,
        "score_version": "topic-baseline-v1",
        "source_identity": "current-run",
        "legacy_identity": None,
    }
    _write_artifact(
        root, f"metadata/publication-links/{run_id}/link.json", payload
    )
    return str(payload["digest"])


def _stat(
    root: Path,
    *,
    link_digest: str,
    capture_id: str,
    captured_at: str,
    views: int,
    search_inflow: int,
    run_id: str = "RUN-001",
    post_id: str = "POST-001",
    exposure: int | None = None,
) -> None:
    day = captured_at[:10]
    missing: list[JSONValue] = (
        [] if exposure is not None else ["average_exposure_rank", "exposure"]
    )
    if exposure is not None:
        missing = ["average_exposure_rank"]
    payload: JSONMap = {
        "schema_version": "blog-stat-snapshot-v2",
        "captured_at": captured_at,
        "as_of_date": day,
        "timezone": "Asia/Seoul",
        "limitations": ["manual_import"],
        "input_digests": [link_digest],
        "missing_fields": missing,
        "status": "mature",
        "digest": "",
        "mapping_version": "blog-stats-columns-v1",
        "capture_id": capture_id,
        "blog_id": "owner",
        "blog_post_id": post_id,
        "publication_run_id": run_id,
        "publication_link_digest": link_digest,
        "coverage_start": "2026-09-01",
        "coverage_end": day,
        "views": views,
        "search_inflow": search_inflow,
        "exposure": exposure,
        "average_exposure_rank": None,
        "demographics": [],
    }
    _write_artifact(
        root,
        f"metadata/blog-stats/owner/{day}/{capture_id}.json",
        payload,
    )


def _horizon(result: JSONMap, name: str, index: int = 0) -> JSONMap:
    cohorts = result["cohorts"]
    assert isinstance(cohorts, list)
    cohort = cohorts[index]
    assert isinstance(cohort, dict)
    horizons = cohort["horizons"]
    assert isinstance(horizons, dict)
    horizon = horizons[name]
    assert isinstance(horizon, dict)
    return horizon


@pytest.mark.parametrize(
    ("as_of", "expected"),
    [
        ("2026-09-08T08:59:59+09:00", "pending"),
        ("2026-09-08T09:00:00+09:00", "delayed"),
        ("2026-09-10T09:00:00+09:00", "delayed"),
        ("2026-09-10T09:00:01+09:00", "missing"),
    ],
)
def test_status_boundaries_use_fixed_kst_and_inclusive_grace(
    tmp_path: Path, as_of: str, expected: str
) -> None:
    # Given: a KST publication with no observations.
    _ = _link(tmp_path)

    # When: the cohort is evaluated on an exact boundary.
    result = compute_cohorts(tmp_path, as_of)

    # Then: pending, delayed, and missing boundaries are unambiguous.
    assert _horizon(result, "7d")["status"] == expected


def test_exact_cutoff_observation_is_mature_and_preserves_identity(
    tmp_path: Path,
) -> None:
    # Given: one observation captured exactly at the seven-day cutoff.
    link_digest = _link(tmp_path)
    _stat(
        tmp_path,
        link_digest=link_digest,
        capture_id="CAP-CUTOFF",
        captured_at="2026-09-08T09:00:00+09:00",
        views=10,
        search_inflow=0,
    )

    # When: the result is computed after the observation became available.
    result = compute_cohorts(tmp_path, "2026-09-15T23:30:00+09:00")

    # Then: exact-cutoff zero is mature and its provenance is retained.
    horizon = _horizon(result, "7d")
    assert horizon["status"] == "mature"
    assert horizon["cumulative"] == {
        "average_exposure_rank": None,
        "exposure": None,
        "search_inflow": 0,
        "views": 10,
    }
    assert horizon["observation_id"] == "CAP-CUTOFF"
    input_digests = result["input_digests"]
    assert isinstance(input_digests, list)
    assert horizon["observation_digest"] in input_digests


@pytest.mark.parametrize(
    ("captured_at", "expected"),
    [
        ("2026-09-10T09:00:00+09:00", "mature"),
        ("2026-09-10T09:00:01+09:00", "missing"),
    ],
)
def test_observation_grace_is_inclusive_only_at_exact_forty_eight_hours(
    tmp_path: Path, captured_at: str, expected: str
) -> None:
    # Given: an observation on one side of the exact grace boundary.
    link_digest = _link(tmp_path)
    _stat(
        tmp_path,
        link_digest=link_digest,
        capture_id="CAP-GRACE",
        captured_at=captured_at,
        views=1,
        search_inflow=1,
    )

    # When: evaluation happens after the observation is available.
    result = compute_cohorts(tmp_path, "2026-09-11T09:00:00+09:00")

    # Then: only the observation at +48h is label-eligible.
    assert _horizon(result, "7d")["status"] == expected


def test_prior_observation_supplies_separate_window_delta(tmp_path: Path) -> None:
    # Given: cumulative observations immediately before and at the cutoff.
    link_digest = _link(tmp_path)
    _stat(
        tmp_path,
        link_digest=link_digest,
        capture_id="CAP-PRIOR",
        captured_at="2026-09-08T08:00:00+09:00",
        views=8,
        search_inflow=2,
        exposure=5,
    )
    _stat(
        tmp_path,
        link_digest=link_digest,
        capture_id="CAP-LABEL",
        captured_at="2026-09-08T10:00:00+09:00",
        views=12,
        search_inflow=5,
        exposure=9,
    )

    # When: the seven-day cohort is calculated.
    result = compute_cohorts(tmp_path, "2026-09-09T00:00:00+09:00")

    # Then: cumulative label and prior-to-label delta stay separate.
    horizon = _horizon(result, "7d")
    assert horizon["window_delta"] == {
        "exposure": 4,
        "search_inflow": 3,
        "views": 4,
    }
    assert horizon["prior_observation_id"] == "CAP-PRIOR"


def test_future_observation_is_excluded_until_as_of(tmp_path: Path) -> None:
    # Given: an otherwise eligible observation captured after as_of.
    link_digest = _link(tmp_path)
    _stat(
        tmp_path,
        link_digest=link_digest,
        capture_id="CAP-FUTURE",
        captured_at="2026-09-08T10:00:00+09:00",
        views=1,
        search_inflow=1,
    )

    # When: evaluation happens thirty minutes after cutoff.
    result = compute_cohorts(tmp_path, "2026-09-08T09:30:00+09:00")

    # Then: future data does not leak into the label.
    assert _horizon(result, "7d")["status"] == "delayed"
    input_digests = result["input_digests"]
    assert isinstance(input_digests, list)
    assert len(input_digests) == 1


def test_future_publication_duplicate_is_byte_invisible(tmp_path: Path) -> None:
    # Given: an earlier result followed by a duplicate post link from the future.
    _ = _link(tmp_path)
    before = serialize_cohorts(
        compute_cohorts(tmp_path, "2026-09-11T09:00:00+09:00")
    )
    _ = _link(
        tmp_path,
        run_id="RUN-FUTURE",
        post_id="POST-001",
        published_at="2026-10-01T09:00:00+09:00",
    )

    # When: the earlier as_of is recomputed.
    after = serialize_cohorts(
        compute_cohorts(tmp_path, "2026-09-11T09:00:00+09:00")
    )

    # Then: future publication ambiguity cannot alter historical bytes.
    assert after == before


def test_future_invalid_stat_is_byte_invisible(tmp_path: Path) -> None:
    # Given: a stable earlier result and a future stat with bad digest and identity.
    link_digest = _link(tmp_path)
    before = serialize_cohorts(
        compute_cohorts(tmp_path, "2026-09-11T09:00:00+09:00")
    )
    _stat(
        tmp_path,
        link_digest=link_digest,
        capture_id="CAP-FUTURE-INVALID",
        captured_at="2026-10-01T09:00:00+09:00",
        views=0,
        search_inflow=0,
    )
    path = next((tmp_path / "metadata/blog-stats").glob("*/*/*.json"))
    value: JSONValue = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    value["publication_link_digest"] = "sha256:" + "b" * 64
    value["digest"] = "sha256:" + "c" * 64
    _ = path.write_text(json.dumps(value), encoding="utf-8")

    # When: the earlier as_of is recomputed.
    after = serialize_cohorts(
        compute_cohorts(tmp_path, "2026-09-11T09:00:00+09:00")
    )

    # Then: future invalid metadata neither fails nor enters the digest chain.
    assert after == before


def test_future_reset_and_conflict_are_byte_invisible(tmp_path: Path) -> None:
    # Given: a mature 7d label and future reset/conflicting cumulative samples.
    link_digest = _link(tmp_path)
    _stat(
        tmp_path,
        link_digest=link_digest,
        capture_id="CAP-7D-STABLE",
        captured_at="2026-09-08T09:00:00+09:00",
        views=10,
        search_inflow=10,
    )
    before = serialize_cohorts(
        compute_cohorts(tmp_path, "2026-09-11T09:00:00+09:00")
    )
    for capture_id, views in (("CAP-FUTURE-A", 1), ("CAP-FUTURE-B", 2)):
        _stat(
            tmp_path,
            link_digest=link_digest,
            capture_id=capture_id,
            captured_at="2026-10-01T09:00:00+09:00",
            views=views,
            search_inflow=views,
        )

    # When: evaluation remains earlier than both new samples.
    after = serialize_cohorts(
        compute_cohorts(tmp_path, "2026-09-11T09:00:00+09:00")
    )

    # Then: neither reset nor same-instant conflict poisons the earlier label.
    assert after == before


def test_future_shaped_non_kst_timestamp_fails_closed(tmp_path: Path) -> None:
    # Given: an invalid stat that resembles a future observation but uses UTC.
    link_digest = _link(tmp_path)
    _stat(
        tmp_path,
        link_digest=link_digest,
        capture_id="CAP-BAD-TIME",
        captured_at="2026-10-01T09:00:00+09:00",
        views=1,
        search_inflow=1,
    )
    path = next((tmp_path / "metadata/blog-stats").glob("*/*/*.json"))
    value: JSONValue = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    value["captured_at"] = "2026-10-01T00:00:00Z"
    _ = path.write_text(json.dumps(value), encoding="utf-8")

    # When/Then: malformed temporal metadata cannot bypass canonical validation.
    with pytest.raises(ContractError, match="KST timestamp"):
        _ = compute_cohorts(tmp_path, "2026-09-11T09:00:00+09:00")


def test_late_arrival_changes_only_new_computation(tmp_path: Path) -> None:
    # Given: a first computation after grace with no data.
    link_digest = _link(tmp_path)
    before = compute_cohorts(tmp_path, "2026-09-11T09:00:00+09:00")
    before_bytes = serialize_cohorts(before)
    _stat(
        tmp_path,
        link_digest=link_digest,
        capture_id="CAP-LATE",
        captured_at="2026-09-09T09:00:00+09:00",
        views=0,
        search_inflow=0,
    )

    # When: the same as_of is recomputed with the newly arrived immutable input.
    after = compute_cohorts(tmp_path, "2026-09-11T09:00:00+09:00")

    # Then: the old in-memory result is unchanged and only the new result matures.
    assert _horizon(before, "7d")["status"] == "missing"
    assert serialize_cohorts(before) == before_bytes
    assert _horizon(after, "7d")["status"] == "mature"


def test_missing_vs_zero_remain_distinct(tmp_path: Path) -> None:
    # Given: one post with an explicit zero and another with no observation.
    first = _link(tmp_path, run_id="RUN-ZERO", post_id="POST-ZERO")
    _ = _link(tmp_path, run_id="RUN-MISSING", post_id="POST-MISSING")
    _stat(
        tmp_path,
        link_digest=first,
        run_id="RUN-ZERO",
        post_id="POST-ZERO",
        capture_id="CAP-ZERO",
        captured_at="2026-09-08T09:00:00+09:00",
        views=0,
        search_inflow=0,
    )

    # When: both cohorts are evaluated after grace.
    result = compute_cohorts(tmp_path, "2026-09-11T09:00:00+09:00")

    # Then: an explicit zero is mature while absence remains missing.
    cohorts = result["cohorts"]
    assert isinstance(cohorts, list)
    by_post: dict[str, JSONMap] = {}
    for cohort in cohorts:
        assert isinstance(cohort, dict)
        by_post[str(cohort["blog_post_id"])] = cohort
    zero = _horizon({"cohorts": [by_post["POST-ZERO"]]}, "7d")
    missing = _horizon({"cohorts": [by_post["POST-MISSING"]]}, "7d")
    cumulative = zero["cumulative"]
    assert isinstance(cumulative, dict)
    assert cumulative["search_inflow"] == 0
    assert zero["status"] == "mature"
    assert missing["cumulative"] is None
    assert missing["status"] == "missing"


def test_cumulative_reset_marks_horizons_invalid(tmp_path: Path) -> None:
    # Given: a later observation whose cumulative counter decreased.
    link_digest = _link(tmp_path)
    _stat(
        tmp_path,
        link_digest=link_digest,
        capture_id="CAP-HIGH",
        captured_at="2026-09-08T08:00:00+09:00",
        views=10,
        search_inflow=10,
    )
    _stat(
        tmp_path,
        link_digest=link_digest,
        capture_id="CAP-RESET",
        captured_at="2026-09-08T09:00:00+09:00",
        views=9,
        search_inflow=9,
    )

    # When: cohort evaluation inspects the complete available sequence.
    result = compute_cohorts(tmp_path, "2026-09-11T09:00:00+09:00")

    # Then: the reset is explicit and not converted to a negative label.
    horizon = _horizon(result, "7d")
    assert horizon["status"] == "invalid"
    assert horizon["invalid_reason"] == "cumulative_reset"
    assert horizon["window_delta"] is None


def test_conflicting_duplicate_observation_marks_horizons_invalid(
    tmp_path: Path,
) -> None:
    # Given: two different cumulative payloads at the same observation instant.
    link_digest = _link(tmp_path)
    for capture_id, views in (("CAP-A", 1), ("CAP-B", 2)):
        _stat(
            tmp_path,
            link_digest=link_digest,
            capture_id=capture_id,
            captured_at="2026-09-08T09:00:00+09:00",
            views=views,
            search_inflow=views,
        )

    # When: deterministic cohort evaluation sees the conflict.
    result = compute_cohorts(tmp_path, "2026-09-11T09:00:00+09:00")

    # Then: no arbitrary duplicate wins.
    assert _horizon(result, "7d")["invalid_reason"] == "conflicting_duplicate"
    assert _horizon(result, "7d")["status"] == "invalid"


def test_deterministic_bytes_and_twenty_eight_day_subset(tmp_path: Path) -> None:
    # Given: a 7d label and an eligible 28d label for one post.
    link_digest = _link(tmp_path)
    _stat(
        tmp_path,
        link_digest=link_digest,
        capture_id="CAP-7D",
        captured_at="2026-09-08T09:00:00+09:00",
        views=7,
        search_inflow=3,
    )
    _stat(
        tmp_path,
        link_digest=link_digest,
        capture_id="CAP-28D",
        captured_at="2026-09-29T09:00:00+09:00",
        views=28,
        search_inflow=13,
    )

    # When: identical inputs and as_of are computed twice.
    first = compute_cohorts(tmp_path, "2026-10-01T09:00:00+09:00")
    second = compute_cohorts(tmp_path, "2026-10-01T09:00:00+09:00")

    # Then: canonical bytes/digest match and 28d mature implies 7d mature.
    assert serialize_cohorts(first) == serialize_cohorts(second)
    assert first["digest"] == second["digest"]
    assert _horizon(first, "28d")["status"] == "mature"
    assert _horizon(first, "7d")["status"] == "mature"


def test_input_json_key_order_does_not_change_result(tmp_path: Path) -> None:
    # Given: a valid observation and its first canonical cohort result.
    link_digest = _link(tmp_path)
    _stat(
        tmp_path,
        link_digest=link_digest,
        capture_id="CAP-ORDER",
        captured_at="2026-09-08T09:00:00+09:00",
        views=1,
        search_inflow=1,
    )
    first = compute_cohorts(tmp_path, "2026-09-11T09:00:00+09:00")
    path = next((tmp_path / "metadata/blog-stats").glob("*/*/*.json"))
    value: JSONValue = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    reordered = {key: value[key] for key in reversed(tuple(value))}
    _ = path.write_text(json.dumps(reordered), encoding="utf-8")

    # When: the same semantic input is parsed with reversed JSON key order.
    second = compute_cohorts(tmp_path, "2026-09-11T09:00:00+09:00")

    # Then: canonical bytes and digest remain identical.
    assert serialize_cohorts(first) == serialize_cohorts(second)


def test_stats_publication_digest_mismatch_is_rejected(tmp_path: Path) -> None:
    # Given: a v2 observation that claims a different publication-link digest.
    link_digest = _link(tmp_path)
    _stat(
        tmp_path,
        link_digest=link_digest,
        capture_id="CAP-MISMATCH",
        captured_at="2026-09-08T09:00:00+09:00",
        views=1,
        search_inflow=1,
    )
    path = next((tmp_path / "metadata/blog-stats").glob("*/*/*.json"))
    value: JSONValue = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    value["publication_link_digest"] = "sha256:" + "b" * 64
    value["digest"] = compute_digest(value)
    _ = path.write_text(json.dumps(value), encoding="utf-8")

    # When/Then: the strict identity join fails closed.
    with pytest.raises(ContractError, match="identity mismatch"):
        _ = compute_cohorts(tmp_path, "2026-09-11T09:00:00+09:00")


def test_twenty_eight_day_without_seven_day_is_invalid(tmp_path: Path) -> None:
    # Given: only a 28d cutoff observation exists.
    link_digest = _link(tmp_path)
    _stat(
        tmp_path,
        link_digest=link_digest,
        capture_id="CAP-28D",
        captured_at="2026-09-29T09:00:00+09:00",
        views=28,
        search_inflow=13,
    )

    # When: both horizons have passed.
    result = compute_cohorts(tmp_path, "2026-10-01T09:00:00+09:00")

    # Then: the 28d evaluation cannot mature outside the 7d mature set.
    assert _horizon(result, "7d")["status"] == "missing"
    assert _horizon(result, "28d")["status"] == "invalid"
    assert _horizon(result, "28d")["invalid_reason"] == "prior_horizon_not_mature"


def test_dot_namespace_is_not_followed(tmp_path: Path) -> None:
    # Given: a dot namespace symlink beside valid input directories.
    _ = _link(tmp_path)
    base = tmp_path / "metadata/blog-stats"
    base.mkdir(parents=True)
    target = tmp_path / "outside"
    target.mkdir()
    (base / ".hidden").symlink_to(target, target_is_directory=True)

    # When: the cohort reader traverses the input root.
    result = compute_cohorts(tmp_path, "2026-09-11T09:00:00+09:00")

    # Then: it does not follow or include the dot namespace.
    assert _horizon(result, "7d")["status"] == "missing"


def test_visible_symlink_namespace_is_rejected(tmp_path: Path) -> None:
    # Given: a visible symlink in the stats input hierarchy.
    _ = _link(tmp_path)
    base = tmp_path / "metadata/blog-stats"
    base.mkdir(parents=True)
    target = tmp_path / "outside"
    target.mkdir()
    (base / "owner").symlink_to(target, target_is_directory=True)

    # When/Then: root traversal fails closed instead of following it.
    with pytest.raises(ContractError, match="unsafe"):
        _ = compute_cohorts(tmp_path, "2026-09-11T09:00:00+09:00")
