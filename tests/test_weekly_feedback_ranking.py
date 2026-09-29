from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from tools.contract_types import JSONMap, JSONValue
from tools.topic_feedback_config import (
    FEEDBACK_VERSION,
    RolloutPin,
    load_rollout_config,
)
from tools.topic_feedback_models import compute_digest
from tools.topic_metadata import CreatorAdvisorSnapshot, read_snapshot
from tools.weekly_feedback_ranking import (
    WeeklyRankingRequest,
    build_weekly_ranking,
)

FIXTURES = Path("tests/fixtures/feedback")


def _canonical(payload: JSONMap) -> bytes:
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()


def _signal(
    source_id: str,
    confidence: str,
    access_mode: str,
    as_of: str,
    values: tuple[tuple[str, float], ...],
) -> JSONMap:
    payload: JSONMap = {
        "schema_version": "topic-signal-snapshot-v1",
        "captured_at": f"{as_of}T09:00:00+09:00",
        "as_of_date": as_of,
        "timezone": "Asia/Seoul",
        "limitations": [],
        "input_digests": [],
        "missing_fields": [],
        "status": "mature",
        "digest": "",
        "source_id": source_id,
        "source_confidence": confidence,
        "access_mode": access_mode,
        "query_period": as_of,
        "terms_checked_at": "2026-09-08T00:00:00+09:00",
        "raw_payload": {},
        "derived": {
            "ranking_features": [
                {"keyword": keyword, "unit": "relative_index", "value": value}
                for keyword, value in values
            ]
        },
    }
    payload["digest"] = compute_digest(payload)
    return payload


def _manifest(root: Path, name: str, signals: tuple[JSONMap, ...]) -> Path:
    files: list[JSONValue] = []
    for index, signal in enumerate(signals):
        relative = Path("metadata/topic-signals") / name / f"{index}.json"
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = _canonical(signal)
        _ = path.write_bytes(encoded)
        files.append(
            {
                "path": relative.as_posix(),
                "size_bytes": len(encoded),
                "sha256": "sha256:" + hashlib.sha256(encoded).hexdigest(),
                "schema_version": signal["schema_version"],
            }
        )
    payload: JSONMap = {
        "schema_version": "feedback-evidence-manifest-v1",
        "feedback_id": name,
        "created_at": "2026-09-09T09:00:00+09:00",
        "data_as_of": "2026-09-09T09:00:00+09:00",
        "files": files,
    }
    payload["digest"] = "sha256:" + hashlib.sha256(_canonical(payload)).hexdigest()
    path = root / "metadata/feedback-manifests" / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_bytes(_canonical(payload))
    return path


def _inputs(root: Path) -> tuple[Path, CreatorAdvisorSnapshot, RolloutPin]:
    config = root / "config/topic-feedback-rollout.json"
    config.parent.mkdir(parents=True)
    _ = shutil.copy(FIXTURES / "shadow.json", config)
    snapshot_path = root / "snapshot.json"
    _ = shutil.copy(FIXTURES / "rank-only.json", snapshot_path)
    snapshot = read_snapshot(
        snapshot_path,
        expected_capture_id="rank-only",
        expected_as_of_date="2026-09-07",
    )
    return config, snapshot, load_rollout_config(root, config)


def test_trusted_fresh_signal_reorders_shadow_but_preserves_selected_baseline(
    tmp_path: Path,
) -> None:
    # Given: a strict Creator snapshot, validated shadow pin, and trusted evidence.
    _, snapshot, pin = _inputs(tmp_path)
    manifest = _manifest(
        tmp_path,
        "fresh",
        (
            _signal(
                "naver-datalab",
                "A",
                "official_api",
                "2026-09-09",
                (("첫 후보", 0.0), ("둘째 후보", 100.0)),
            ),
        ),
    )

    # When: the weekly ranking is built from the real Task10 contracts.
    result = build_weekly_ranking(
        WeeklyRankingRequest(
            tmp_path,
            snapshot,
            (manifest,),
            pin,
            datetime.fromisoformat("2026-09-09T23:59:59+09:00"),
        )
    )

    # Then: shadow moves, but shadow rollout selects the exact baseline order.
    assert [item.keyword for item in result.baseline] == ["첫 후보", "둘째 후보"]
    assert [item.keyword for item in result.shadow] == ["둘째 후보", "첫 후보"]
    assert result.selected == result.baseline
    payload = result.as_json()
    serialized_shadow = payload["shadow"]
    assert isinstance(serialized_shadow, list)
    first = serialized_shadow[0]
    assert isinstance(first, dict)
    features = first["features"]
    assert isinstance(features, list)
    feature = features[0]
    assert isinstance(feature, dict)
    assert feature["source_id"] == "naver-datalab"
    assert first["applied_weight"] == 0.5


def test_active_selection_uses_shadow_only_from_active_rollout_pin(tmp_path: Path) -> None:
    # Given: the same trusted scoring evidence and an upstream-validated active pin.
    _, snapshot, shadow_pin = _inputs(tmp_path)
    active_pin = replace(
        shadow_pin,
        active_score_version=FEEDBACK_VERSION,
        rollout_tier="full",
        selection_mutation=True,
    )
    manifest = _manifest(
        tmp_path,
        "active",
        (
            _signal(
                "naver-datalab",
                "A",
                "official_api",
                "2026-09-09",
                (("첫 후보", 0.0), ("둘째 후보", 100.0)),
            ),
        ),
    )

    # When: the builder receives that immutable active pin.
    result = build_weekly_ranking(
        WeeklyRankingRequest(
            tmp_path,
            snapshot,
            (manifest,),
            active_pin,
            datetime.fromisoformat("2026-09-09T23:59:59+09:00"),
        )
    )

    # Then: selected follows the computed challenger and records active mode.
    assert result.selected == result.shadow
    assert result.selection_mode == "active"


def test_stale_disabled_and_unknown_sources_cannot_affect_ranking(
    tmp_path: Path,
) -> None:
    # Given: stale trusted, disabled, and unknown signal artifacts.
    _, snapshot, pin = _inputs(tmp_path)
    manifest = _manifest(
        tmp_path,
        "excluded",
        (
            _signal(
                "naver-datalab",
                "A",
                "official_api",
                "2026-08-01",
                (("첫 후보", 0.0), ("둘째 후보", 100.0)),
            ),
            _signal(
                "blackkiwi",
                "B",
                "manual_import",
                "2026-09-09",
                (("첫 후보", 0.0), ("둘째 후보", 100.0)),
            ),
            _signal(
                "unknown-source",
                "A",
                "official_api",
                "2026-09-09",
                (("첫 후보", 0.0), ("둘째 후보", 100.0)),
            ),
        ),
    )

    # When: source policy, rollout modes, and scorer freshness are applied.
    result = build_weekly_ranking(
        WeeklyRankingRequest(
            tmp_path,
            snapshot,
            (manifest,),
            pin,
            datetime.fromisoformat("2026-09-09T23:59:59+09:00"),
        )
    )

    # Then: only scorer-visible stale signals are reported; none alter the baseline.
    assert result.shadow == result.baseline
    assert result.excluded_signals == (
        "둘째 후보:stale_signal",
        "첫 후보:stale_signal",
    )
    assert all(not item.features for item in result.shadow)


def test_no_signal_is_exact_baseline_and_empty_evaluation_is_computed(
    tmp_path: Path,
) -> None:
    # Given: a strict snapshot and no governed signal or evaluation input.
    _, snapshot, pin = _inputs(tmp_path)

    # When: the weekly ranking is built without evidence manifests or outcomes.
    result = build_weekly_ranking(
        WeeklyRankingRequest(
            tmp_path,
            snapshot,
            (),
            pin,
            datetime.fromisoformat("2026-09-09T23:59:59+09:00"),
        )
    )

    # Then: selected/shadow are exact baseline projections and evaluator counts are real.
    assert result.shadow == result.baseline
    assert result.selected == result.baseline
    assert result.evaluation.mature_selected_outcomes == 0
    assert result.evaluation.validation_outcomes == 0
    assert result.evaluation.promotion_eligible is False
    assert result.challenger_verdict == "insufficient_evidence"
