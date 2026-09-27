import json
from collections.abc import Callable
from pathlib import Path

import pytest

from tools.contract_types import ContractError
from tools.preflight_runner import main as preflight_main
from tools.startup_preflight import (
    classify_operational_browser_status,
    daily_launchd_contract_error,
)
from tools.topic_metadata import canonicalize_snapshot_observations
from tools.topic_scoring import TopicObservation, rank_topics

FIXTURES = Path(__file__).parent / "fixtures" / "feedback"


def test_rank_only_snapshot_stays_cold_start_and_byte_immutable() -> None:
    # Given: a frozen Creator Advisor fixture with rank but no trend value.
    path = FIXTURES / "rank-only.json"
    raw_bytes = path.read_bytes()

    # When: preflight parses and ranks the raw observations.
    snapshot = canonicalize_snapshot_observations(
        path,
        expected_capture_id="rank-only",
        expected_as_of_date="2026-09-07",
    )
    ranked = rank_topics(
        tuple(
            TopicObservation(candidate.keyword, candidate.rank, candidate.trend_index, ())
            for candidate in snapshot.candidates
        )
    )

    # Then: rank order and missingness survive without rewriting the fixture.
    assert [item.keyword for item in ranked] == ["첫 후보", "둘째 후보"]
    assert all(item.stage == "cold-start" for item in ranked)
    assert snapshot.candidates[0].trend_index is None
    assert snapshot.limitations == ("trend index unavailable",)
    assert path.read_bytes() == raw_bytes


@pytest.mark.parametrize(
    ("fixture_name", "failure_reason"),
    (
        ("staging-mismatch.txt", "staging_mismatch"),
        ("cdp-timeout.txt", "cdp_timeout"),
    ),
)
def test_browser_preflight_failure_is_operational_and_disables_live_rollout(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    fixture_name: str,
    failure_reason: str,
) -> None:
    # Given: a captured failure and a runner boundary with observable call counts.
    message = (FIXTURES / fixture_name).read_text(encoding="utf-8").strip()
    runner_loads: list[str] = []
    diagnostic = tmp_path / "operational-browser-failure.txt"
    _ = diagnostic.write_text(message, encoding="utf-8")
    monkeypatch.setenv(
        "NAVER_OPERATIONAL_BROWSER_FAILURE", diagnostic.relative_to(tmp_path).as_posix()
    )
    before = tuple(tmp_path.rglob("*"))

    def forbidden_loader() -> Callable[[list[str]], int]:
        runner_loads.append("loader")
        return lambda _arguments: 0

    # When: the real daily preflight entry consumes the frozen typed probe result.
    exit_code = preflight_main(
        [
            "preflight-runner",
            "run",
            "daily-generate",
            "--auto-topic",
            "--as-of-date",
            "2026-09-08",
            "--root",
            str(tmp_path),
        ],
        runner_loader=forbidden_loader,
    )
    output = json.loads(capsys.readouterr().out)

    # Then: failure stops before startup/runner/writes and exposes zero attempts/retries.
    assert exit_code == 2
    assert output == {
        "browser_attempts": 0,
        "browser_retries": 0,
        "failure_reason": failure_reason,
        "operational_browser_status": "browser_preflight_failed",
        "real_browser_enabled": False,
        "schema_manual_import_ready": True,
    }
    assert runner_loads == []
    assert tuple(tmp_path.rglob("*")) == before


def test_browser_preflight_rejects_unrelated_text() -> None:
    # Given: text which is not a frozen staging or CDP failure signature.
    # When/Then: the classifier rejects it instead of silently guessing.
    with pytest.raises(ContractError, match="unrecognized"):
        _ = classify_operational_browser_status("network hiccup from another component")


def test_date_less_daily_plist_is_explicitly_fail_closed() -> None:
    # Given: the repository's intentionally unloaded date-less schedule.
    path = Path(__file__).parents[1] / "launchd/com.naverblog.daily-generate.plist"

    # When/Then: rollout preflight returns an explicit contract error.
    assert daily_launchd_contract_error(path) == "daily_schedule_requires_human_as_of_date"
