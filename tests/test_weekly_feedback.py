from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tests.weekly_feedback_fixtures import install_weekly_inputs
from tests.weekly_outcome_fixtures import install_factual_evaluation
from tools.contract_types import ContractError, JSONMap, JSONValue
from tools.feedback_manifest import EvidenceManifestRequest, build_evidence_manifest
from tools.preflight_runner import main as preflight_main
from tools.runner_execution import run_job
from tools.runner_types import JobName, RunnerRequest
from tools.topic_feedback_evaluation import evaluate_outcomes, parse_candidate_outcomes
from tools.topic_feedback_manifest_reader import load_feedback_path
from tools.topic_feedback_models import compute_digest
from tools.weekly_feedback import WeeklyFeedbackRequest, generate_weekly_feedback

AS_OF = "2026-09-08T09:00:00+09:00"
ROLLOUT = (
    Path(__file__).parent
    / "fixtures/feedback/weekly-root/config/topic-feedback-rollout.json"
)
APPROVED_EVALUATION = Path(
    "tests/fixtures/feedback/task10-approval-root/metadata"
) / Path(
    "topic-feedback-evaluations/task10-approved-v2.json"
)


def _install_rollout(root: Path) -> None:
    target = root / "config/topic-feedback-rollout.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    _ = shutil.copyfile(ROLLOUT, target)


def _install_derived_evaluation(
    root: Path,
    *,
    count: int = 30,
    challenger_delta: float = 1.0,
) -> Path:
    payload = json.loads(APPROVED_EVALUATION.read_text(encoding="utf-8"))
    outcomes = payload["outcomes"][:count]
    for outcome in outcomes:
        outcome["challenger_search_inflow_7d"] = (
            outcome["baseline_search_inflow_7d"] + challenger_delta
        )
        outcome["challenger_search_inflow_28d"] = (
            outcome["baseline_search_inflow_28d"] + challenger_delta
        )
    evaluation_as_of = datetime.fromisoformat(payload["evaluation_as_of"])
    payload["outcomes"] = outcomes
    payload["result"] = evaluate_outcomes(
        parse_candidate_outcomes(outcomes),
        evaluation_as_of=evaluation_as_of,
        minimum_mature_samples=30,
    ).as_json()
    payload["digest"] = compute_digest(payload)
    target = root / "metadata/topic-feedback-evaluations/evaluation-v2.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    _ = target.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    return target


def _install_trusted_signal(root: Path) -> None:
    signal: JSONMap = {
        "schema_version": "topic-signal-snapshot-v1",
        "captured_at": "2026-09-07T09:00:00+09:00",
        "as_of_date": "2026-09-07",
        "timezone": "Asia/Seoul",
        "limitations": [],
        "input_digests": [],
        "missing_fields": [],
        "status": "mature",
        "digest": "",
        "source_id": "naver-datalab",
        "source_confidence": "A",
        "access_mode": "official_api",
        "query_period": "2026-09-07",
        "terms_checked_at": "2026-09-07T00:00:00+09:00",
        "raw_payload": {},
        "derived": {"ranking_features": [
            {"keyword": "첫 후보", "unit": "relative_index", "value": 0},
            {"keyword": "둘째 후보", "unit": "relative_index", "value": 100},
        ]},
    }
    signal["digest"] = compute_digest(signal)
    encoded = (
        json.dumps(signal, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode()
    relative = Path("metadata/topic-signals/naver-datalab/2026-09-07/signal.json")
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_bytes(encoded)
    manifest: JSONMap = {
        "schema_version": "feedback-evidence-manifest-v1",
        "feedback_id": "trusted-ranking-input",
        "created_at": "2026-09-07T10:00:00+09:00",
        "data_as_of": "2026-09-07T10:00:00+09:00",
        "files": [{
            "path": relative.as_posix(),
            "size_bytes": len(encoded),
            "sha256": "sha256:" + hashlib.sha256(encoded).hexdigest(),
            "schema_version": "topic-signal-snapshot-v1",
        }],
    }
    unsigned = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    manifest["digest"] = "sha256:" + hashlib.sha256(unsigned).hexdigest()
    manifest_path = root / "metadata/feedback-manifests/trusted-ranking-input.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    _ = manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )


def _rewrite_evidence(path: Path, payload: JSONMap) -> bytes:
    payload["digest"] = compute_digest(payload)
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    _ = path.write_bytes(encoded)
    return encoded


def _last_evaluation_outcome(evaluation: JSONMap) -> JSONMap:
    outcomes = evaluation.get("outcomes")
    if not isinstance(outcomes, list) or not outcomes:
        raise AssertionError("evaluation outcomes missing")
    outcome = outcomes[-1]
    if not isinstance(outcome, dict):
        raise TypeError("evaluation outcome must be an object")
    return outcome


def _evaluation_ref(evaluation: JSONMap, role: str) -> JSONMap:
    reference = _last_evaluation_outcome(evaluation).get(role)
    if not isinstance(reference, dict):
        raise TypeError(f"evaluation {role} missing")
    return reference


def _refresh_evaluation_ref(
    evaluation: JSONMap, role: str, encoded: bytes
) -> None:
    reference = _evaluation_ref(evaluation, role)
    reference["size_bytes"] = len(encoded)
    reference["sha256"] = "sha256:" + hashlib.sha256(encoded).hexdigest()
    decoded: JSONValue = json.loads(encoded)
    if not isinstance(decoded, dict):
        raise TypeError("evidence must be an object")
    reference["digest"] = decoded["digest"]


def test_weekly_bundle_is_noop_for_identical_inputs(tmp_path: Path) -> None:
    # Given
    _ = install_weekly_inputs(tmp_path)
    request = WeeklyFeedbackRequest(tmp_path, AS_OF)

    # When
    first = generate_weekly_feedback(request)
    before = {path: path.read_bytes() for path in first.paths}
    second = generate_weekly_feedback(request)

    # Then
    assert second == first
    assert {path: path.read_bytes() for path in first.paths} == before


def test_direct_later_time_reuses_complete_semantic_bundle(tmp_path: Path) -> None:
    # Given
    _ = install_weekly_inputs(tmp_path)
    first = generate_weekly_feedback(WeeklyFeedbackRequest(tmp_path, AS_OF))
    before = {path: path.read_bytes() for path in first.paths}

    # When
    later = generate_weekly_feedback(
        WeeklyFeedbackRequest(tmp_path, "2026-09-08T17:00:00+09:00")
    )

    # Then
    assert later == first
    assert {path: path.read_bytes() for path in first.paths} == before


def test_changed_input_creates_revision_without_overwrite(tmp_path: Path) -> None:
    # Given
    inputs = install_weekly_inputs(tmp_path)
    first = generate_weekly_feedback(WeeklyFeedbackRequest(tmp_path, AS_OF))
    before = {path: path.read_bytes() for path in first.paths}
    stat = json.loads(inputs[2].read_text(encoding="utf-8"))
    stat["search_inflow"] = 15
    stat["digest"] = compute_digest(stat)
    _ = inputs[2].write_text(
        json.dumps(stat, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    # When
    second = generate_weekly_feedback(WeeklyFeedbackRequest(tmp_path, AS_OF))

    # Then
    assert second.feedback_id != first.feedback_id
    assert second.operational_report.name.startswith("weekly-2026-09-08-")
    assert {path: path.read_bytes() for path in first.paths} == before


def test_missing_inputs_produce_explicit_baseline_bundle(tmp_path: Path) -> None:
    # Given
    request = WeeklyFeedbackRequest(tmp_path, AS_OF)

    # When
    bundle = generate_weekly_feedback(request)
    payload = json.loads(bundle.feedback_json.read_text(encoding="utf-8"))

    # Then
    assert payload["status"] == "missing"
    assert payload["cohorts"] == []
    assert payload["rankings"][0]["selection_mode"] == "baseline"
    assert payload["rankings"][1]["candidates"] == []


def test_failed_bundle_publish_rolls_back_owned_outputs(tmp_path: Path) -> None:
    # Given
    _ = install_weekly_inputs(tmp_path)

    def fail(index: int, phase: str) -> None:
        if index == 2 and phase == "publish":
            raise OSError("injected")

    # When / Then
    with pytest.raises(ContractError, match="bundle write failed safely"):
        _ = generate_weekly_feedback(WeeklyFeedbackRequest(tmp_path, AS_OF), fail)
    assert not list((tmp_path / "metadata").glob("weekly-feedback/**/*.json"))
    assert not list((tmp_path / "metadata").glob("feedback-manifests/*.json"))


def test_manifest_strict_reread_and_companion_structure(tmp_path: Path) -> None:
    # Given
    _ = install_weekly_inputs(tmp_path)
    _install_rollout(tmp_path)

    # When
    bundle = generate_weekly_feedback(WeeklyFeedbackRequest(tmp_path, AS_OF))
    loaded = load_feedback_path(tmp_path, bundle.manifest, AS_OF, ())
    payload = json.loads(bundle.feedback_json.read_text(encoding="utf-8"))

    # Then
    assert loaded.digest == bundle.manifest_digest
    assert payload["cohorts"][0]["horizons"]["7d"]["status"] == "mature"
    assert payload["cohorts"][0]["horizons"]["28d"]["status"] == "mature"
    assert payload["rankings"][1]["candidates"][0]["keyword"] == "첫 후보"
    schemas = {
        item["schema_version"]
        for item in json.loads(bundle.manifest.read_text(encoding="utf-8"))["files"]
    }
    assert "topic-feedback-rollout-v1" in schemas
    assert payload["rankings"][0]["cohort_digest"] in payload["input_digests"]


def test_weekly_bundle_reports_real_shadow_ranking_and_empty_evaluation(
    tmp_path: Path,
) -> None:
    # Given: strict Creator candidates, shadow rollout, and fresh trusted signals.
    _ = install_weekly_inputs(tmp_path)
    target = tmp_path / "config/topic-feedback-rollout.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    _ = shutil.copyfile(Path("tests/fixtures/feedback/shadow.json"), target)
    _install_trusted_signal(tmp_path)

    # When: weekly feedback consumes the Task10 scoring/evaluation loop.
    bundle = generate_weekly_feedback(WeeklyFeedbackRequest(tmp_path, AS_OF))
    payload = json.loads(bundle.feedback_json.read_text(encoding="utf-8"))
    ranking = payload["rankings"][1]

    # Then: shadow really reorders, selection remains baseline, and verdict is computed.
    assert [item["keyword"] for item in ranking["baseline"]] == ["첫 후보", "둘째 후보"]
    assert [item["keyword"] for item in ranking["shadow"]] == ["둘째 후보", "첫 후보"]
    assert ranking["selected"] == ranking["baseline"]
    assert ranking["shadow"][0]["applied_weight"] == 0.5
    assert ranking["evaluation"]["mature_selected_outcomes"] == 0
    assert payload["rankings"][0]["challenger_verdict"] == "insufficient_evidence"


def test_self_authored_v3_without_canonical_sources_is_non_actionable(
    tmp_path: Path,
) -> None:
    # Given: evaluation-v3 binds only locally authored outcome records.
    _ = install_weekly_inputs(tmp_path)
    evaluation = install_factual_evaluation(tmp_path)

    # When: production weekly feedback builds its real evaluator request.
    bundle = generate_weekly_feedback(
        WeeklyFeedbackRequest(tmp_path, "2026-09-08T09:00:00+09:00")
    )
    payload = json.loads(bundle.feedback_json.read_text(encoding="utf-8"))
    result = payload["rankings"][1]["evaluation"]
    manifest = json.loads(bundle.manifest.read_text(encoding="utf-8"))
    reread = load_feedback_path(tmp_path, bundle.manifest, AS_OF, ())

    # Then: self-consistency cannot promote without canonical raw arm evidence.
    assert result["promotion_eligible"] is False
    assert result["mature_selected_outcomes"] == 0
    assert payload["rankings"][0]["challenger_verdict"] == "insufficient_evidence"
    assert evaluation.relative_to(tmp_path).as_posix() in {
        item["path"] for item in manifest["files"]
    }
    schemas = [item["schema_version"] for item in manifest["files"]]
    assert schemas.count("topic-feedback-selection-evidence-v1") == 30
    assert schemas.count("topic-feedback-outcome-observation-v1") == 60
    assert reread.signals == ()
    later = generate_weekly_feedback(
        WeeklyFeedbackRequest(tmp_path, "2026-09-08T17:00:00+09:00")
    )
    assert later == bundle


def test_self_authored_noneligible_v3_remains_non_actionable(tmp_path: Path) -> None:
    # Given: 30 locally authored outcomes whose challenger does not improve.
    _ = install_weekly_inputs(tmp_path)
    _ = install_factual_evaluation(tmp_path, challenger_delta=0.0)

    # When
    bundle = generate_weekly_feedback(WeeklyFeedbackRequest(tmp_path, AS_OF))
    payload = json.loads(bundle.feedback_json.read_text(encoding="utf-8"))
    result = payload["rankings"][1]["evaluation"]

    # Then: absence of canonical arm sources keeps the evaluation non-actionable.
    assert result["mature_selected_outcomes"] == 0
    assert result["promotion_eligible"] is False
    assert payload["rankings"][0]["challenger_verdict"] == "insufficient_evidence"


def test_factual_immature_outcome_is_excluded_from_promotion(tmp_path: Path) -> None:
    _ = install_weekly_inputs(tmp_path)
    _ = install_factual_evaluation(tmp_path, pending_last=True)

    bundle = generate_weekly_feedback(WeeklyFeedbackRequest(tmp_path, AS_OF))
    payload = json.loads(bundle.feedback_json.read_text(encoding="utf-8"))
    result = payload["rankings"][1]["evaluation"]

    assert result["mature_selected_outcomes"] == 0
    assert result["missing_rate"] == 0
    assert payload["rankings"][0]["challenger_verdict"] == "insufficient_evidence"


def test_unbound_v2_evaluation_is_non_actionable(tmp_path: Path) -> None:
    # Given: the legacy wrapper has plausible values but no factual file references.
    _ = install_weekly_inputs(tmp_path)
    _ = _install_derived_evaluation(tmp_path)

    # When
    bundle = generate_weekly_feedback(WeeklyFeedbackRequest(tmp_path, AS_OF))
    payload = json.loads(bundle.feedback_json.read_text(encoding="utf-8"))

    # Then: internal self-consistency alone cannot authorize an eligible verdict.
    assert payload["rankings"][1]["evaluation"]["mature_selected_outcomes"] == 0
    assert payload["rankings"][0]["challenger_verdict"] == "insufficient_evidence"


def test_no_creator_keeps_unbound_historical_evaluation_non_actionable(
    tmp_path: Path,
) -> None:
    # Given: locally authored paired outcomes exist without canonical raw sources.
    _ = install_factual_evaluation(tmp_path)

    # When
    bundle = generate_weekly_feedback(WeeklyFeedbackRequest(tmp_path, AS_OF))
    payload = json.loads(bundle.feedback_json.read_text(encoding="utf-8"))

    # Then: selection and actionable evaluation both remain empty.
    assert payload["rankings"][1]["candidates"] == []
    assert payload["rankings"][1]["evaluation"]["promotion_eligible"] is False
    assert payload["rankings"][1]["evaluation"]["mature_selected_outcomes"] == 0
    assert payload["rankings"][0]["challenger_verdict"] == "insufficient_evidence"


@pytest.mark.parametrize("custom_state", [False, True])
def test_weekly_runner_rejects_parent_swap_after_input_validation(
    tmp_path: Path, custom_state: bool
) -> None:
    _ = install_weekly_inputs(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    state_root = tmp_path / ("custom-state" if custom_state else ".automation")
    state_root.mkdir()
    def swap_then_load() -> Callable[[list[str]], int]:
        _ = state_root.rename(outside / "moved")
        _ = state_root.symlink_to(outside / "moved", target_is_directory=True)
        from tools.runner_cli import main as runner_main

        return runner_main
    arguments = ["preflight-runner", "run", "weekly-improve", "--root", str(tmp_path)]
    if custom_state:
        arguments.extend(("--state-dir", str(state_root)))

    exit_code = preflight_main(arguments, runner_loader=swap_then_load)

    assert exit_code == 2
    assert list((outside / "moved").iterdir()) == []
    assert not list(tmp_path.glob("metadata/weekly-feedback/**/*.json"))


@pytest.mark.parametrize("replacement", ["symlink", "directory"])
def test_weekly_runner_rejects_state_parent_swap_after_runner_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replacement: str
) -> None:
    # Given: validation sees a regular custom state directory.
    _ = install_weekly_inputs(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    state_root = tmp_path / "custom-state"
    state_root.mkdir()

    def swap_after_validation(request: RunnerRequest, _job: JobName) -> RunnerRequest:
        _ = state_root.rename(outside / "moved")
        if replacement == "symlink":
            _ = state_root.symlink_to(outside / "moved", target_is_directory=True)
        else:
            state_root.mkdir()
        return request

    monkeypatch.setattr(
        "tools.runner_execution.pin_topic_feedback_context", swap_after_validation
    )

    # When / Then: no control-plane file follows the replacement parent.
    with pytest.raises(ContractError, match=r"runner path (?:is unsafe|changed)"):
        _ = run_job(RunnerRequest(tmp_path, "weekly-improve", state_dir=state_root))
    assert list((outside / "moved").iterdir()) == []
    if replacement == "directory":
        assert list(state_root.iterdir()) == []
    assert not list(tmp_path.glob("metadata/weekly-feedback/**/*.json"))


@pytest.mark.parametrize(
    "mutation",
    ["value", "digest", "path", "missing", "symlink", "fifo", "future", "duplicate"],
)
def test_factual_evaluation_rejects_unbound_or_unsafe_evidence_before_runner_write(
    tmp_path: Path, mutation: str
) -> None:
    # Given: one factual evidence edge is forged, absent, unsafe, or post-origin.
    _ = install_weekly_inputs(tmp_path)
    evaluation_path = install_factual_evaluation(tmp_path)
    evaluation = json.loads(evaluation_path.read_text(encoding="utf-8"))
    outcome_id = evaluation["outcomes"][-1]["outcome_id"]
    observation_path = (
        tmp_path / "metadata/topic-feedback-outcomes" / outcome_id / "28d.json"
    )
    if mutation == "value":
        observation = json.loads(observation_path.read_text(encoding="utf-8"))
        observation["challenger_search_inflow"] = 999
        _ = _rewrite_evidence(observation_path, observation)
    elif mutation == "digest":
        _evaluation_ref(evaluation, "twenty_eight_day_ref")["digest"] = (
            "sha256:" + "f" * 64
        )
    elif mutation == "path":
        _evaluation_ref(evaluation, "twenty_eight_day_ref")["path"] = "../outside.json"
    elif mutation == "missing":
        observation_path.unlink()
    elif mutation == "symlink":
        observation_path.unlink()
        outside = tmp_path / "outside.json"
        _ = outside.write_text("{}", encoding="utf-8")
        observation_path.symlink_to(outside)
    elif mutation == "fifo":
        observation_path.unlink()
        os.mkfifo(observation_path)
    elif mutation == "future":
        observation = json.loads(observation_path.read_text(encoding="utf-8"))
        observation["observed_at"] = "2027-01-01T09:00:00+09:00"
        encoded = _rewrite_evidence(observation_path, observation)
        _refresh_evaluation_ref(evaluation, "twenty_eight_day_ref", encoded)
    else:
        outcomes: JSONValue = evaluation.get("outcomes")
        if not isinstance(outcomes, list) or len(outcomes) < 2:
            raise AssertionError("evaluation requires duplicate candidates")
        outcomes[1] = outcomes[0]
    _ = _rewrite_evidence(evaluation_path, evaluation)

    # When
    exit_code = preflight_main(
        ["preflight-runner", "run", "weekly-improve", "--root", str(tmp_path)]
    )

    # Then: semantic validation fails before lock, state, log, or report creation.
    assert exit_code == 2
    assert not (tmp_path / ".automation").exists()
    assert not list((tmp_path / "metadata/weekly-feedback").glob("**/*.json"))


def test_future_evaluation_is_validated_but_not_consumed(tmp_path: Path) -> None:
    # Given: a canonical evaluation whose origin is later than this weekly run.
    _ = install_weekly_inputs(tmp_path)
    evaluation = _install_derived_evaluation(tmp_path)
    payload = json.loads(evaluation.read_text(encoding="utf-8"))
    payload["evaluation_as_of"] = "2027-01-01T09:00:00+09:00"
    payload["result"] = evaluate_outcomes(
        parse_candidate_outcomes(payload["outcomes"]),
        evaluation_as_of=datetime.fromisoformat(payload["evaluation_as_of"]),
        minimum_mature_samples=30,
    ).as_json()
    payload["digest"] = compute_digest(payload)
    _ = evaluation.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )

    # When
    bundle = generate_weekly_feedback(WeeklyFeedbackRequest(tmp_path, AS_OF))
    feedback = json.loads(bundle.feedback_json.read_text(encoding="utf-8"))
    manifest = json.loads(bundle.manifest.read_text(encoding="utf-8"))

    # Then: post-origin evidence cannot affect this report or its provenance set.
    assert feedback["rankings"][1]["evaluation"]["mature_selected_outcomes"] == 0
    assert feedback["rankings"][0]["challenger_verdict"] == "insufficient_evidence"
    assert evaluation.relative_to(tmp_path).as_posix() not in {
        item["path"] for item in manifest["files"]
    }
    with pytest.raises(ContractError, match="manifest is from the future"):
        _ = build_evidence_manifest(
            EvidenceManifestRequest(
                tmp_path,
                "future-evaluation",
                AS_OF,
                AS_OF,
                (evaluation,),
            )
        )


@pytest.mark.parametrize("mutation", ["duplicate", "post_origin"])
def test_invalid_evaluation_temporal_evidence_fails_closed(
    tmp_path: Path, mutation: str
) -> None:
    # Given: a canonical wrapper around invalid governed outcome evidence.
    _ = install_weekly_inputs(tmp_path)
    evaluation = _install_derived_evaluation(tmp_path)
    payload = json.loads(evaluation.read_text(encoding="utf-8"))
    if mutation == "duplicate":
        payload["outcomes"][1]["outcome_id"] = payload["outcomes"][0]["outcome_id"]
    else:
        payload["outcomes"][0]["twenty_eight_day_observed_at"] = (
            "2027-01-01T09:00:00+09:00"
        )
    payload["result"] = evaluate_outcomes(
        parse_candidate_outcomes(payload["outcomes"]),
        evaluation_as_of=datetime.fromisoformat(payload["evaluation_as_of"]),
        minimum_mature_samples=30,
    ).as_json()
    payload["digest"] = compute_digest(payload)
    _ = evaluation.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )

    # When/Then: recomputable wrapper bytes do not legitimize invalid evidence.
    with pytest.raises(ContractError, match="feedback input digest mismatch"):
        _ = generate_weekly_feedback(WeeklyFeedbackRequest(tmp_path, AS_OF))
    assert not list((tmp_path / "metadata/weekly-feedback").rglob("*.json"))


def test_evaluation_tamper_fails_before_existing_bundle_mutation(
    tmp_path: Path,
) -> None:
    # Given: a completed runner bundle whose factual observation is later tampered.
    _ = install_weekly_inputs(tmp_path)
    evaluation = install_factual_evaluation(tmp_path)
    assert (
        preflight_main(
            ["preflight-runner", "run", "weekly-improve", "--root", str(tmp_path)]
        )
        == 0
    )
    outputs = tuple((tmp_path / ".automation").rglob("*")) + tuple(
        (tmp_path / "metadata/weekly-feedback").rglob("*")
    ) + tuple((tmp_path / "metadata/feedback-manifests").rglob("*"))
    files = tuple(path for path in outputs if path.is_file())
    before = {path: path.read_bytes() for path in files}
    evaluation_payload = json.loads(evaluation.read_text(encoding="utf-8"))
    outcome_id = _last_evaluation_outcome(evaluation_payload)["outcome_id"]
    observation = (
        tmp_path / "metadata/topic-feedback-outcomes" / str(outcome_id) / "28d.json"
    )
    _ = observation.write_bytes(observation.read_bytes() + b" ")

    # When/Then: semantic validation precedes duplicate reuse and every local write.
    exit_code = preflight_main(
        ["preflight-runner", "run", "weekly-improve", "--root", str(tmp_path)]
    )
    assert exit_code == 2
    assert {path: path.read_bytes() for path in files} == before
    after = tuple((tmp_path / ".automation").rglob("*")) + tuple(
        (tmp_path / "metadata/weekly-feedback").rglob("*")
    ) + tuple((tmp_path / "metadata/feedback-manifests").rglob("*"))
    assert set(after) == set(outputs)


def test_trusted_signal_later_time_does_not_chain_feedback_manifest(
    tmp_path: Path,
) -> None:
    # Given: a first bundle produced from a strict external signal manifest.
    _ = install_weekly_inputs(tmp_path)
    target = tmp_path / "config/topic-feedback-rollout.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    _ = shutil.copyfile(Path("tests/fixtures/feedback/shadow.json"), target)
    _install_trusted_signal(tmp_path)
    first = generate_weekly_feedback(WeeklyFeedbackRequest(tmp_path, AS_OF))
    before = {path: path.read_bytes() for path in first.paths}

    # When: the next invocation sees the newly produced feedback manifest wrapper.
    later = generate_weekly_feedback(
        WeeklyFeedbackRequest(tmp_path, "2026-09-08T17:00:00+09:00")
    )

    # Then: only flattened signal bytes matter, so no self-chained revision appears.
    assert later == first
    assert {path: path.read_bytes() for path in first.paths} == before
    assert len(list((tmp_path / "metadata/weekly-feedback").rglob("*.json"))) == 1


def test_same_input_race_converges_on_one_bundle(tmp_path: Path) -> None:
    # Given
    _ = install_weekly_inputs(tmp_path)
    request = WeeklyFeedbackRequest(tmp_path, AS_OF)

    # When
    with ThreadPoolExecutor(max_workers=2) as executor:
        bundles = tuple(executor.map(generate_weekly_feedback, (request, request)))

    # Then
    assert bundles[0] == bundles[1]
    assert len(list((tmp_path / "metadata/feedback-manifests").glob("*.json"))) == 1


def test_preflight_weekly_tamper_fails_before_report_mutation(tmp_path: Path) -> None:
    # Given
    inputs = install_weekly_inputs(tmp_path)
    _ = inputs[1].write_bytes(inputs[1].read_bytes() + b" ")

    # When
    exit_code = preflight_main(
        ["preflight-runner", "run", "weekly-improve", "--root", str(tmp_path)]
    )

    # Then
    assert exit_code == 2
    assert not list((tmp_path / ".automation").glob("reports/*.md"))
    assert not list((tmp_path / "metadata").glob("weekly-feedback/**/*.json"))
    assert not list((tmp_path / "metadata").glob("feedback-manifests/*.json"))


def test_preflight_weekly_rejects_symlinked_runner_parent_without_outside_write(
    tmp_path: Path,
) -> None:
    # Given
    _ = install_weekly_inputs(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / ".automation").symlink_to(outside, target_is_directory=True)

    # When
    exit_code = preflight_main(
        ["preflight-runner", "run", "weekly-improve", "--root", str(tmp_path)]
    )

    # Then
    assert exit_code == 2
    assert list(outside.iterdir()) == []


def test_preflight_revalidates_inputs_before_duplicate_reuse(tmp_path: Path) -> None:
    # Given
    inputs = install_weekly_inputs(tmp_path)
    assert (
        preflight_main(
            ["preflight-runner", "run", "weekly-improve", "--root", str(tmp_path)]
        )
        == 0
    )
    outputs = (
        tuple((tmp_path / ".automation").glob("**/*.*"))
        + tuple((tmp_path / "metadata").glob("**/FEEDBACK-*.json"))
        + tuple((tmp_path / "manifests").glob("*.json"))
    )
    before = {item: item.read_bytes() for item in outputs}
    payload = json.loads(inputs[2].read_text(encoding="utf-8"))
    payload["search_inflow"] = 999
    _ = inputs[2].write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n",
        encoding="utf-8",
    )

    # When
    exit_code = preflight_main(
        ["preflight-runner", "run", "weekly-improve", "--root", str(tmp_path)]
    )

    # Then
    assert exit_code == 2
    assert {item: item.read_bytes() for item in outputs} == before


def test_runner_changed_input_creates_revision_and_reuses_it(tmp_path: Path) -> None:
    # Given
    inputs = install_weekly_inputs(tmp_path)
    first_time = datetime(2026, 9, 9, tzinfo=UTC)
    first = run_job(RunnerRequest(tmp_path, "weekly-improve", now=first_time))
    original = {
        item: item.read_bytes()
        for item in tuple((tmp_path / ".automation/reports").glob("*.md"))
        + tuple((tmp_path / "metadata").glob("**/FEEDBACK-*.json"))
    }
    payload = json.loads(inputs[2].read_text(encoding="utf-8"))
    payload["search_inflow"] = 15
    payload["digest"] = compute_digest(payload)
    _ = inputs[2].write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n",
        encoding="utf-8",
    )

    # When
    changed = run_job(
        RunnerRequest(
            tmp_path,
            "weekly-improve",
            now=first_time + timedelta(hours=1),
        )
    )
    unchanged_later = run_job(
        RunnerRequest(
            tmp_path,
            "weekly-improve",
            now=first_time + timedelta(hours=2),
        )
    )

    # Then
    assert changed.run_id != first.run_id
    assert unchanged_later.run_id == changed.run_id
    assert "weekly-2026-09-09-" in changed.message
    assert {item: item.read_bytes() for item in original} == original


def test_runner_reuses_bundle_after_state_removal(tmp_path: Path) -> None:
    # Given
    _ = install_weekly_inputs(tmp_path)
    instant = datetime(2026, 9, 9, tzinfo=UTC)
    first = run_job(RunnerRequest(tmp_path, "weekly-improve", now=instant))
    state = json.loads(first.state_path.read_text(encoding="utf-8"))
    bundle_paths = tuple(
        Path(str(state[key]))
        for key in (
            "weekly_operational_report_path",
            "weekly_feedback_json_path",
            "weekly_feedback_markdown_path",
            "weekly_feedback_manifest_path",
        )
    )
    before = {path: path.read_bytes() for path in bundle_paths}
    first.state_path.unlink()

    # When
    rerun = run_job(
        RunnerRequest(tmp_path, "weekly-improve", now=instant + timedelta(hours=1))
    )

    # Then
    rerun_state = json.loads(rerun.state_path.read_text(encoding="utf-8"))
    assert rerun.run_id != first.run_id
    assert Path(str(rerun_state["weekly_feedback_json_path"])) == bundle_paths[1]
    assert {path: path.read_bytes() for path in bundle_paths} == before


def test_runner_same_day_maturity_transition_creates_revision(tmp_path: Path) -> None:
    # Given
    _ = install_weekly_inputs(tmp_path)
    before_maturity = datetime(2026, 8, 28, 23, tzinfo=UTC)
    first = run_job(RunnerRequest(tmp_path, "weekly-improve", now=before_maturity))

    # When
    matured = run_job(
        RunnerRequest(
            tmp_path,
            "weekly-improve",
            now=before_maturity + timedelta(hours=2),
        )
    )

    # Then
    assert matured.run_id != first.run_id
    state = json.loads(matured.state_path.read_text(encoding="utf-8"))
    feedback = json.loads(
        Path(str(state["weekly_feedback_json_path"])).read_text(encoding="utf-8")
    )
    assert feedback["cohorts"][0]["horizons"]["28d"]["status"] == "mature"
