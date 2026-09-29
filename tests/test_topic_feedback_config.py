from __future__ import annotations

import hashlib
import json
from dataclasses import FrozenInstanceError, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tools.contract_types import ContractError, JSONMap, JSONValue
from tools.topic_scoring import TopicObservation, rank_topics

DEFAULT_CONFIG = Path("config/topic-feedback-rollout.json")
SHADOW_CONFIG = Path("tests/fixtures/feedback/shadow.json")
APPROVAL_FIXTURE_ROOT = Path("tests/fixtures/feedback/task10-approval-root")


def _read_map(path: Path) -> JSONMap:
    value: JSONValue = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _write_map(path: Path, value: JSONMap) -> None:
    _ = path.write_text(json.dumps(value), encoding="utf-8")


def _evaluation(
    *,
    count: int = 30,
    challenger_7d: float = 11.0,
    challenger_28d: float = 21.0,
) -> tuple[JSONMap, bytes, str]:
    first = datetime(2023, 1, 1, 12, tzinfo=timezone(timedelta(hours=9)))
    outcomes: list[JSONValue] = [
        {
            "outcome_id": f"OUT-{index:03d}",
            "selected": True,
            "status": "mature",
            "baseline_search_inflow_7d": 10.0,
            "challenger_search_inflow_7d": challenger_7d,
            "baseline_search_inflow_28d": 20.0,
            "challenger_search_inflow_28d": challenger_28d,
            "published_at": (first + timedelta(days=35 * index)).isoformat(),
            "prediction_recorded_at": (
                first + timedelta(days=35 * index, hours=-1)
            ).isoformat(),
            "training_cutoff": (
                first + timedelta(days=35 * index, hours=-2)
            ).isoformat(),
            "selection_input_digest": "sha256:" + f"{index:064x}",
            "score_version": "topic-feedback-v1",
            "seven_day_observed_at": (
                first + timedelta(days=35 * index + 7, hours=1)
            ).isoformat(),
            "seven_day_observation_digest": "sha256:" + f"{index + 100:064x}",
            "twenty_eight_day_observed_at": (
                first + timedelta(days=35 * index + 28, hours=1)
            ).isoformat(),
            "twenty_eight_day_observation_digest": "sha256:" + f"{index + 200:064x}",
        }
        for index in range(count)
    ]
    payload: JSONMap = {
        "schema_version": "topic-feedback-evaluation-v2",
        "baseline_score_version": "topic-baseline-v1",
        "challenger_score_version": "topic-feedback-v1",
        "evaluation_parameters": {
            "minimum_mature_samples": 30,
            "metric": "search_inflow",
            "horizons_days": [7, 28],
            "aggregation": "median",
            "comparison": "strict_improvement",
            "validation": "publication_time_expanding_origin_v2",
        },
        "evaluation_as_of": (first + timedelta(days=35 * (count - 1) + 30)).isoformat(),
        "outcomes": outcomes,
        "result": {
            "schema_version": "topic-feedback-evaluation-candidate-v2",
            "evaluation_as_of": (
                first + timedelta(days=35 * (count - 1) + 30)
            ).isoformat(),
            "promotion_eligible": count >= 30
            and challenger_7d > 10.0
            and challenger_28d > 20.0,
            "mature_selected_outcomes": count,
            "training_outcomes": max(0, count - 1),
            "validation_outcomes": max(0, count - 1),
            "search_inflow_7d_improvement": challenger_7d - 10.0,
            "search_inflow_28d_improvement": challenger_28d - 20.0,
            "missing_rate": 0.0,
            "duplicate_rate": 0.0,
            "rolling_origin_folds": max(0, count - 1),
            "ndcg_usage": "diagnostic_only",
        },
        "ndcg_delta": None,
        "digest": "",
    }
    unsigned = dict(payload)
    _ = unsigned.pop("digest")
    payload["digest"] = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(
                unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
    )
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return payload, encoded, str(payload["digest"])


def test_default_config_is_off_and_pins_baseline() -> None:
    # Given: the shipped rollout config.
    from tools.topic_feedback_config import load_rollout_config

    # When: it crosses the validated config boundary and is pinned.
    pin = load_rollout_config(Path("."), DEFAULT_CONFIG)

    # Then: feedback cannot mutate selection by default.
    assert pin.feedback_enabled is False
    assert pin.active_score_version == "topic-baseline-v1"
    assert pin.shadow_score_version == "topic-feedback-v1"
    assert pin.minimum_mature_samples == 30
    assert pin.rollout_tier == "off"
    assert pin.previous_stable_version == "topic-baseline-v1"
    assert pin.selection_mutation is False


def test_shadow_tier_records_challenger_without_selection_mutation() -> None:
    # Given: a local shadow-only rollout.
    from tools.topic_feedback_config import load_rollout_config

    # When: it is validated and pinned.
    pin = load_rollout_config(Path("."), SHADOW_CONFIG)

    # Then: only the existing baseline remains active.
    assert pin.feedback_enabled is True
    assert pin.rollout_tier == "shadow"
    assert pin.selection_mutation is False


def test_active_challenger_requires_external_approved_evaluation(
    tmp_path: Path,
) -> None:
    # Given: a canary config with no separately supplied approval.
    from tools.topic_feedback_config import load_rollout_config

    config = _read_map(SHADOW_CONFIG)
    config["rollout_tier"] = "canary"
    config["active_score_version"] = "topic-feedback-v1"
    path = tmp_path / "canary.json"
    _write_map(path, config)

    # When/Then: a string in config cannot self-authorize promotion.
    with pytest.raises(
        ContractError,
        match="^active score version requires approved evaluation$",
    ):
        _ = load_rollout_config(tmp_path, path)


def test_fixed_registry_temporal_v2_evidence_authorizes_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the checked-in registry points to digest-bound temporal v2 evidence.
    import tools.topic_feedback_approval as approval
    from tools.topic_feedback_config import load_rollout_config

    monkeypatch.setattr(
        approval,
        "APPROVAL_REGISTRY_PATH",
        APPROVAL_FIXTURE_ROOT / "config/topic-feedback-approved-evaluations.json",
    )

    # When: the real fixed-registry approval boundary loads an active rollout.
    pin = load_rollout_config(
        Path("."), Path("tests/fixtures/feedback/active-without-evaluation.json")
    )

    # Then: the validated digest authorizes only the configured challenger.
    assert pin.selection_mutation is True
    assert pin.evaluation_digest == (
        "sha256:d0931fec2ae6ed165f6c21495f3a914d5b6231d1f72e8dd2f7761e81e75b39d1"
    )


def test_caller_crafted_evidence_and_digest_cannot_self_authorize(
    tmp_path: Path,
) -> None:
    # Given: locally crafted passing evidence and its matching caller-known digest.
    from tools.topic_feedback_approval import evaluate_promotion
    from tools.topic_feedback_config import load_rollout_config

    payload, encoded, digest = _evaluation()
    decision = evaluate_promotion(payload, encoded, digest, 30)
    config = _read_map(SHADOW_CONFIG)
    config["rollout_tier"] = "canary"
    config["active_score_version"] = "topic-feedback-v1"
    path = tmp_path / "canary.json"
    _write_map(path, config)

    # When/Then: pure evaluation success is not a trusted rollout approval.
    assert decision.evaluation_digest == digest
    with pytest.raises(
        ContractError,
        match="^active score version requires approved evaluation$",
    ):
        _ = load_rollout_config(tmp_path, path)


@pytest.mark.parametrize(
    ("case", "count", "challenger_7d", "challenger_28d"),
    [
        ("twenty_nine", 29, 11.0, 21.0),
        ("equal_7d", 31, 10.0, 21.0),
        ("equal_28d", 31, 11.0, 20.0),
        ("worse_7d", 31, 9.0, 21.0),
        ("worse_28d", 31, 11.0, 19.0),
    ],
)
def test_evaluation_requires_thirty_and_strict_improvement_at_both_horizons(
    case: str, count: int, challenger_7d: float, challenger_28d: float
) -> None:
    # Given: explicit selected outcomes failing one promotion condition.
    from tools.topic_feedback_approval import evaluate_promotion

    payload, encoded, digest = _evaluation(
        count=count,
        challenger_7d=challenger_7d,
        challenger_28d=challenger_28d,
    )

    # When/Then: each insufficiency fails closed independently of NDCG.
    assert case
    with pytest.raises(ContractError, match="approved evaluation"):
        _ = evaluate_promotion(payload, encoded, digest, 30)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("outcome_id", "OUT-001"),
        ("selected", False),
        ("status", "pending"),
        ("status", "missing"),
        ("challenger_search_inflow_7d", True),
        ("challenger_search_inflow_7d", -1.0),
        ("challenger_search_inflow_28d", None),
        ("challenger_search_inflow_28d", float("nan")),
    ],
)
def test_evaluation_rejects_duplicate_unusable_or_invalid_outcomes(
    field: str, value: JSONValue
) -> None:
    # Given: one malformed or unusable member in an explicit outcome set.
    from tools.topic_feedback_approval import evaluate_promotion

    payload, _, _ = _evaluation()
    outcomes = payload["outcomes"]
    assert isinstance(outcomes, list)
    first = outcomes[0]
    assert isinstance(first, dict)
    first[field] = value
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=True,
    ).encode()
    digest = "sha256:" + hashlib.sha256(encoded).hexdigest()

    # When/Then: no invalid record contributes to sample count or improvement.
    with pytest.raises(ContractError, match="approved evaluation"):
        _ = evaluate_promotion(payload, encoded, digest, 30)


def test_evaluation_decision_is_frozen_against_payload_mutation() -> None:
    # Given: a qualifying explicit outcome set.
    from tools.topic_feedback_approval import evaluate_promotion

    payload, encoded, digest = _evaluation()
    decision = evaluate_promotion(payload, encoded, digest, 30)

    # When: the caller mutates its original nested payload after parsing.
    outcomes = payload["outcomes"]
    assert isinstance(outcomes, list)
    outcomes.clear()

    # Then: the frozen decision retains computed evidence and digest.
    assert decision.mature_selected_outcomes == 30
    assert decision.search_inflow_7d_improvement == 1.0
    assert decision.search_inflow_28d_improvement == 1.0
    assert decision.evaluation_digest == digest


@pytest.mark.parametrize("target", ["outcome", "parameters"])
def test_evaluation_digest_covers_outcomes_and_parameters(target: str) -> None:
    # Given: qualifying evidence and its digest before a nested mutation.
    from tools.topic_feedback_approval import evaluate_promotion

    payload, encoded, digest = _evaluation()
    if target == "outcome":
        outcomes = payload["outcomes"]
        assert isinstance(outcomes, list)
        outcome = outcomes[0]
        assert isinstance(outcome, dict)
        outcome["challenger_search_inflow_7d"] = 1000.0
    else:
        parameters = payload["evaluation_parameters"]
        assert isinstance(parameters, dict)
        parameters["minimum_mature_samples"] = 31

    # When/Then: the original digest cannot authorize either mutation.
    with pytest.raises(ContractError, match="approved evaluation"):
        _ = evaluate_promotion(payload, encoded, digest, 30)


def test_fixed_approval_registry_rejects_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: the fixed registry location is replaced by a symlink in an isolated test.
    import tools.topic_feedback_approval as approval
    from tools.topic_feedback_config import load_rollout_config

    target = tmp_path / "registry.json"
    _write_map(
        target,
        {
            "schema_version": "topic-feedback-approved-evaluations-v1",
            "approved_evaluations": [],
        },
    )
    link = tmp_path / "registry-link.json"
    link.symlink_to(target)
    monkeypatch.setattr(approval, "APPROVAL_REGISTRY_PATH", link)

    # When/Then: active rollout never follows registry symlinks.
    with pytest.raises(ContractError, match="approval registry must not be a symlink"):
        _ = load_rollout_config(
            Path("."), Path("tests/fixtures/feedback/active-without-evaluation.json")
        )


def test_fixed_approval_registry_rejects_evidence_path_escape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: an isolated fixed registry entry attempts to escape the trusted root.
    import tools.topic_feedback_approval as approval
    from tools.topic_feedback_config import load_rollout_config

    registry = tmp_path / "registry.json"
    _write_map(
        registry,
        {
            "schema_version": "topic-feedback-approved-evaluations-v1",
            "approved_evaluations": [
                {
                    "challenger_score_version": "topic-feedback-v1",
                    "evaluation_path": "../caller-evidence.json",
                    "evaluation_sha256": "sha256:caller",
                    "approved_at": "2026-09-09T09:00:00+09:00",
                }
            ],
        },
    )
    monkeypatch.setattr(approval, "APPROVAL_REGISTRY_PATH", registry)

    # When/Then: the registry cannot authorize evidence outside its fixed namespace.
    with pytest.raises(ContractError, match="approved evaluation registry is invalid"):
        _ = load_rollout_config(
            Path("."), Path("tests/fixtures/feedback/active-without-evaluation.json")
        )


def test_pin_is_immutable_when_config_file_changes(tmp_path: Path) -> None:
    # Given: a validated shadow snapshot pinned at run start.
    from tools.topic_feedback_config import load_rollout_config

    config_path = tmp_path / "rollout.json"
    _ = config_path.write_bytes(SHADOW_CONFIG.read_bytes())
    pin = load_rollout_config(tmp_path, config_path)

    # When: the mutable source file is replaced after run start.
    config = _read_map(config_path)
    config["feedback_enabled"] = False
    config["rollout_tier"] = "off"
    _write_map(config_path, config)

    # Then: the in-memory run boundary retains the original version and digest.
    assert pin.feedback_enabled is True
    assert pin.rollout_tier == "shadow"
    field = "feedback_enabled"
    with pytest.raises(FrozenInstanceError):
        setattr(pin, field, False)


@pytest.mark.parametrize("field", ["active_score_version", "shadow_score_version"])
def test_unknown_score_version_is_rejected(tmp_path: Path, field: str) -> None:
    # Given: an otherwise valid config with an arbitrary score version.
    from tools.topic_feedback_config import load_rollout_config

    config = _read_map(DEFAULT_CONFIG)
    config[field] = "topic-user-weights-v99"
    path = tmp_path / "invalid-version.json"
    _write_map(path, config)

    # When/Then: runtime config cannot introduce scoring code or weights.
    with pytest.raises(ContractError, match="score version is invalid"):
        _ = load_rollout_config(tmp_path, path)


def test_rollback_is_append_only_idempotent_and_restores_baseline(
    tmp_path: Path,
) -> None:
    # Given: historical feedback bytes and a frozen Creator Advisor input.
    from tools.topic_feedback_config import create_rollback

    history = tmp_path / "metadata" / "feedback" / "historic.json"
    history.parent.mkdir(parents=True)
    _ = history.write_bytes(b"historic-feedback")
    observations = (
        TopicObservation("first", 1, None, ()),
        TopicObservation("second", 2, None, ()),
    )
    expected = json.dumps(
        [asdict(score) for score in rank_topics(observations)],
        sort_keys=True,
        separators=(",", ":"),
    ).encode()

    # When: rollback is committed and exactly replayed.
    config_path = tmp_path / "config" / "rollout.json"
    config_path.parent.mkdir()
    _ = config_path.write_bytes(SHADOW_CONFIG.read_bytes())
    first = create_rollback(
        tmp_path, config_path, "RB-001", "2026-09-09T09:00:00+09:00"
    )
    second = create_rollback(
        tmp_path, config_path, "RB-001", "2026-09-09T09:00:00+09:00"
    )
    actual = json.dumps(
        [asdict(score) for score in rank_topics(observations)],
        sort_keys=True,
        separators=(",", ":"),
    ).encode()

    # Then: future configuration is baseline-only and history is untouched.
    assert first == second
    assert first.pin.active_score_version == "topic-baseline-v1"
    assert first.pin.feedback_enabled is False
    assert first.pin.selection_mutation is False
    assert expected == actual
    assert history.read_bytes() == b"historic-feedback"


def test_rollback_divergent_collision_and_symlink_fail_closed(tmp_path: Path) -> None:
    # Given: one committed rollback identity and a symlinked alternate root.
    from tools.topic_feedback_config import create_rollback

    config_path = tmp_path / "config" / "rollout.json"
    config_path.parent.mkdir()
    _ = config_path.write_bytes(SHADOW_CONFIG.read_bytes())
    _ = create_rollback(tmp_path, config_path, "RB-001", "2026-09-09T09:00:00+09:00")

    # When/Then: changing the same identity cannot overwrite prior bytes.
    with pytest.raises(ContractError, match="rollback identity collision"):
        _ = create_rollback(
            tmp_path, config_path, "RB-001", "2026-09-09T10:00:00+09:00"
        )
    unsafe = tmp_path / "unsafe"
    unsafe.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ContractError, match="rollback root must not be a symlink"):
        _ = create_rollback(unsafe, config_path, "RB-002", "2026-09-09T09:00:00+09:00")


def test_fresh_load_observes_durable_rollback_after_config_replacement(
    tmp_path: Path,
) -> None:
    # Given: a shadow config under an explicit project root.
    from tools.topic_feedback_config import create_rollback, load_rollout_config

    config_path = tmp_path / "config" / "rollout.json"
    config_path.parent.mkdir()
    _ = config_path.write_bytes(SHADOW_CONFIG.read_bytes())
    assert load_rollout_config(tmp_path, config_path).feedback_enabled is True

    # When: rollback is stored and the mutable config is later replaced.
    _ = create_rollback(
        tmp_path, config_path, "RB-FENCE-001", "2026-09-09T09:00:00+09:00"
    )
    changed = _read_map(SHADOW_CONFIG)
    changed["rollout_tier"] = "full"
    changed["active_score_version"] = "topic-feedback-v1"
    _write_map(config_path, changed)
    pin = load_rollout_config(tmp_path, config_path)

    # Then: the durable fence forces every future load fully off.
    assert pin.feedback_enabled is False
    assert pin.active_score_version == "topic-baseline-v1"
    assert pin.rollout_tier == "off"
    assert pin.selection_mutation is False
    assert pin.new_feedback_artifacts_enabled is False


@pytest.mark.parametrize("record_kind", ["corrupt", "partial", "symlink", "unknown"])
def test_rollback_record_corruption_blocks_effective_load(
    tmp_path: Path, record_kind: str
) -> None:
    # Given: a fixed rollback registry containing an unsafe record.
    from tools.topic_feedback_config import load_rollout_config

    config_path = tmp_path / "config" / "rollout.json"
    config_path.parent.mkdir()
    _ = config_path.write_bytes(SHADOW_CONFIG.read_bytes())
    registry = tmp_path / "metadata" / "topic-feedback-rollbacks"
    registry.mkdir(parents=True)
    record = registry / (".tmp-partial" if record_kind == "unknown" else "RB-BAD.json")
    if record_kind == "corrupt":
        _ = record.write_text('{"schema_version":"wrong"}', encoding="utf-8")
    elif record_kind == "partial":
        _ = record.write_text('{"schema_version":', encoding="utf-8")
    elif record_kind == "symlink":
        target = tmp_path / "outside.json"
        _ = target.write_text("{}", encoding="utf-8")
        record.symlink_to(target)
    else:
        _ = record.write_text("{}", encoding="utf-8")

    # When/Then: no malformed registry state can be ignored.
    with pytest.raises(ContractError, match="rollback registry record"):
        _ = load_rollout_config(tmp_path, config_path)


def test_explicit_root_mismatch_cannot_bypass_rollback(tmp_path: Path) -> None:
    # Given: a config outside the declared project root.
    from tools.topic_feedback_config import load_rollout_config

    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.json"
    _ = outside.write_bytes(SHADOW_CONFIG.read_bytes())

    # When/Then: the effective loader rejects the mismatched boundary.
    with pytest.raises(
        ContractError, match="rollout config must be inside project root"
    ):
        _ = load_rollout_config(root, outside)
