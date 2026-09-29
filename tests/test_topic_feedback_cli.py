from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tools.contract_types import JSONValue

CONFIG_PATH = Path("config/topic-feedback-sources.json")
ROLLOUT_PATH = Path("config/topic-feedback-rollout.json")
SHADOW_PATH = Path("tests/fixtures/feedback/shadow.json")
SIGNAL_FIXTURE = Path("tests/fixtures/feedback/datalab.json")
BLOG_STATS_FIXTURE = Path("tests/fixtures/feedback/blog-stats-week1.csv")
BLOG_OWNER_FIXTURE = Path("tests/fixtures/feedback/owner.json")
RANK_FIXTURE = Path("tests/fixtures/feedback/rank-only.json")
SIGNAL_MANIFEST_ROOT = Path("tests/fixtures/feedback/task10-signal-root")


def _create_legacy_publication(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.topic_feedback_cli",
            "link-publication",
            "--root",
            str(root),
            "--run-id",
            "RUN-BLOG-STATS",
            "--blog-post-id",
            "POST-001",
            "--published-at",
            "2026-09-01T09:00:00+09:00",
            "--captured-at",
            "2026-09-08T09:00:00+09:00",
            "--source",
            "legacy-import",
            "--target-blog-id",
            "owner",
            "--topic-id",
            "TOPIC-001",
            "--keyword",
            "fixture",
            "--artifact-digest",
            "sha256:" + "a" * 64,
            "--score-version",
            "topic-baseline-v1",
            "--legacy-identity",
            "fixture-approved",
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def test_rank_cli_is_read_only_and_excludes_unknown_related_keyword(
    tmp_path: Path,
) -> None:
    # Given: a baseline rollout, immutable Creator snapshot, and one related signal.
    config = tmp_path / "config"
    config.mkdir()
    _ = shutil.copy(ROLLOUT_PATH, config)
    _ = shutil.copytree(SIGNAL_MANIFEST_ROOT / "metadata", tmp_path / "metadata")
    signals = tmp_path / "metadata/feedback-manifests/task10-cli-signals.json"
    registry_before = CONFIG_PATH.read_bytes()
    rollout_before = (config / ROLLOUT_PATH.name).read_bytes()

    # When: the real rank CLI evaluates the local files.
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.topic_feedback_cli",
            "rank",
            "--root",
            str(tmp_path),
            "--snapshot",
            str(RANK_FIXTURE),
            "--signals",
            str(signals),
            "--as-of",
            "2026-09-09",
            "--json",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    # Then: selection remains baseline and no config or registry is changed.
    assert result.returncode == 0
    payload: JSONValue = json.loads(result.stdout)
    assert isinstance(payload, dict)
    assert payload["resolved_keyword"] == "첫 후보"
    assert payload["excluded_signals"] == ["연관어:not_a_creator_candidate"]
    assert CONFIG_PATH.read_bytes() == registry_before
    assert (config / ROLLOUT_PATH.name).read_bytes() == rollout_before


def test_evaluate_cli_blocks_twenty_nine_outcomes_without_writing(
    tmp_path: Path,
) -> None:
    # Given: 29 pre-publication predictions with mature measured outcomes.
    path = tmp_path / "evaluation.json"
    first = datetime(2023, 1, 1, 12, tzinfo=timezone(timedelta(hours=9)))
    outcomes = [
        {
            "outcome_id": f"OUT-{index:02d}",
            "selected": True,
            "status": "mature",
            "baseline_search_inflow_7d": 10,
            "challenger_search_inflow_7d": 11,
            "baseline_search_inflow_28d": 20,
            "challenger_search_inflow_28d": 22,
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
        for index in range(29)
    ]
    _ = path.write_text(
        json.dumps(
            {
                "schema_version": "topic-feedback-evaluation-input-v2",
                "evaluation_as_of": (first + timedelta(days=35 * 28 + 30)).isoformat(),
                "outcomes": outcomes,
            }
        ),
        encoding="utf-8",
    )

    # When: evaluation runs through the public CLI.
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.topic_feedback_cli",
            "evaluate",
            "--input",
            str(path),
            "--json",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    # Then: promotion is blocked and the input remains byte-stable.
    assert result.returncode == 0
    payload: JSONValue = json.loads(result.stdout)
    assert isinstance(payload, dict)
    assert payload["promotion_eligible"] is False
    assert payload["mature_selected_outcomes"] == 29


def test_rank_cli_rejects_raw_caller_asserted_signal_trust(tmp_path: Path) -> None:
    # Given: a raw array that self-labels an arbitrary signal as confidence A.
    config = tmp_path / "config"
    config.mkdir()
    _ = shutil.copy(ROLLOUT_PATH, config)
    signals = tmp_path / "signals.json"
    _ = signals.write_text(
        '[{"keyword":"둘째 후보","source_id":"arbitrary","source_confidence":"A",'
        + '"as_of_date":"2026-09-09","unit":"index","value":999}]',
        encoding="utf-8",
    )

    # When: raw caller data is supplied to the public rank boundary.
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.topic_feedback_cli",
            "rank",
            "--root",
            str(tmp_path),
            "--snapshot",
            str(RANK_FIXTURE),
            "--signals",
            str(signals),
            "--as-of",
            "2026-09-09",
            "--json",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    # Then: it fails before ranking and creates no feedback artifact.
    assert result.returncode == 2
    assert result.stdout == ""
    assert "feedback evidence manifest is invalid" in result.stderr


def test_sources_cli_outputs_deterministic_json_for_conservative_registry() -> None:
    # Given: the checked-in source registry and the real module CLI.
    command = [
        sys.executable,
        "-m",
        "tools.topic_feedback_cli",
        "sources",
        "--config",
        str(CONFIG_PATH),
        "--format",
        "json",
    ]

    # When: sources is invoked twice without any network-capable arguments.
    first = subprocess.run(command, check=False, capture_output=True, text=True)
    second = subprocess.run(command, check=False, capture_output=True, text=True)

    # Then: the byte-stable registry confirms conservative defaults.
    assert first.returncode == 0
    assert second.returncode == 0
    assert first.stderr == ""
    assert first.stdout == second.stdout
    payload: JSONValue = json.loads(first.stdout)
    assert isinstance(payload, dict)
    assert payload["candidate_source"] == "creator_advisor_only"
    terms_policy = payload["terms_policy"]
    assert isinstance(terms_policy, dict)
    assert terms_policy["terms_checked_at"] == "2026-09-08"
    assert terms_policy["effective_date"] == "2025-07-10"
    sources = payload["sources"]
    assert isinstance(sources, list)
    by_id = {
        source["id"]: source
        for source in sources
        if isinstance(source, dict) and isinstance(source.get("id"), str)
    }
    assert by_id["naver-datalab"]["access_mode"] == "official_api"
    assert by_id["naver-blog-statistics"]["access_mode"] == "manual_import"
    assert by_id["ecommerce-ai-extension"]["access_mode"] == "disabled"
    assert by_id["n-supporter"]["access_mode"] == "disabled"


def test_sources_cli_rejects_forced_unknown_extension_without_network(
    tmp_path: Path,
) -> None:
    # Given: a local config that force-enables an extension lacking product/terms proof.
    raw_value: JSONValue = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    assert isinstance(raw_value, dict)
    sources = raw_value["sources"]
    assert isinstance(sources, list)
    extension = next(
        source
        for source in sources
        if isinstance(source, dict) and source.get("id") == "ecommerce-ai-extension"
    )
    extension["enabled"] = True
    path = tmp_path / "invalid-extension.json"
    _ = path.write_text(json.dumps(raw_value), encoding="utf-8")
    command = [
        sys.executable,
        "-m",
        "tools.topic_feedback_cli",
        "sources",
        "--config",
        str(path),
        "--format",
        "json",
    ]

    # When: the real CLI parses the local file.
    result = subprocess.run(command, check=False, capture_output=True, text=True)

    # Then: parsing stops before any source adapter or network path exists.
    assert result.returncode == 2
    assert result.stdout == ""
    assert (
        result.stderr
        == "source activation requires identified product and reviewed terms\n"
    )


def test_sources_cli_rejects_untrusted_source_identity_without_output(
    tmp_path: Path,
) -> None:
    # Given: an untrusted config redirects the known DataLab identity.
    raw_value: JSONValue = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    assert isinstance(raw_value, dict)
    sources = raw_value["sources"]
    assert isinstance(sources, list)
    datalab = next(
        source
        for source in sources
        if isinstance(source, dict) and source.get("id") == "naver-datalab"
    )
    datalab["official_url"] = "https://evil.invalid/datalab"
    path = tmp_path / "identity-bypass.json"
    _ = path.write_text(json.dumps(raw_value), encoding="utf-8")
    command = [
        sys.executable,
        "-m",
        "tools.topic_feedback_cli",
        "sources",
        "--config",
        str(path),
        "--format",
        "json",
    ]

    # When: the real CLI parses the redirected source definition.
    result = subprocess.run(command, check=False, capture_output=True, text=True)

    # Then: it fails closed without reflecting the substituted URL.
    assert result.returncode == 2
    assert result.stdout == ""
    assert "evil.invalid" not in result.stderr


def test_sources_cli_rejects_search_ads_self_asserted_proof_without_output(
    tmp_path: Path,
) -> None:
    # Given: local config claims an untrusted Search Ads license opt-in.
    raw_value: JSONValue = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    assert isinstance(raw_value, dict)
    sources = raw_value["sources"]
    assert isinstance(sources, list)
    search_ads = next(
        source
        for source in sources
        if isinstance(source, dict)
        and source.get("id") == "naver-search-ads-keyword-tool"
    )
    search_ads["enabled"] = True
    search_ads["activation_proof"] = "reviewed_opt_in_license"
    path = tmp_path / "search-ads-self-proof.json"
    _ = path.write_text(json.dumps(raw_value), encoding="utf-8")
    command = [
        sys.executable,
        "-m",
        "tools.topic_feedback_cli",
        "sources",
        "--config",
        str(path),
        "--format",
        "json",
    ]

    # When: the real CLI evaluates the self-asserted activation request.
    result = subprocess.run(command, check=False, capture_output=True, text=True)

    # Then: it denies activation without emitting the local proof or a payload.
    assert result.returncode == 2
    assert result.stdout == ""
    assert (
        result.stderr
        == "source activation requires identified product and reviewed terms\n"
    )
    assert "reviewed_opt_in_license" not in result.stderr


@pytest.mark.parametrize(
    "safari_path",
    [
        "/Users/person/Library/Safari/History.db",
        "／Ｕｓｅｒｓ／person／Library／Safari／History.db",
    ],
)
def test_sources_cli_rejects_nested_safari_data_path_without_output(
    tmp_path: Path, safari_path: str
) -> None:
    # Given: a nested policy value carries an absolute Safari data location.
    raw_value: JSONValue = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    assert isinstance(raw_value, dict)
    terms_policy = raw_value["terms_policy"]
    assert isinstance(terms_policy, dict)
    terms_policy["conclusion"] = safari_path
    path = tmp_path / "safari-data-path.json"
    _ = path.write_text(json.dumps(raw_value), encoding="utf-8")
    command = [
        sys.executable,
        "-m",
        "tools.topic_feedback_cli",
        "sources",
        "--config",
        str(path),
        "--format",
        "json",
    ]

    # When: the real CLI parses the nested local value.
    result = subprocess.run(command, check=False, capture_output=True, text=True)

    # Then: it fails closed without JSON output or reflected path data.
    assert result.returncode == 2
    assert result.stdout == ""
    assert safari_path not in result.stderr


@pytest.mark.parametrize(
    "conclusion",
    [
        "Safari users may read the public policy overview.",
        "/Users/person/Library/Reference.pdf",
    ],
)
def test_sources_cli_allows_benign_safari_and_library_values(
    tmp_path: Path, conclusion: str
) -> None:
    # Given: a policy conclusion has benign Safari prose or a generic Library path.
    raw_value: JSONValue = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    assert isinstance(raw_value, dict)
    terms_policy = raw_value["terms_policy"]
    assert isinstance(terms_policy, dict)
    terms_policy["conclusion"] = conclusion
    path = tmp_path / "safari-prose.json"
    _ = path.write_text(json.dumps(raw_value), encoding="utf-8")
    command = [
        sys.executable,
        "-m",
        "tools.topic_feedback_cli",
        "sources",
        "--config",
        str(path),
        "--format",
        "json",
    ]

    # When: the real CLI parses the benign prose.
    result = subprocess.run(command, check=False, capture_output=True, text=True)

    # Then: ordinary product prose remains valid.
    assert result.returncode == 0
    assert result.stderr == ""
    assert json.loads(result.stdout)["candidate_source"] == "creator_advisor_only"


@pytest.mark.parametrize("section", ["registry", "terms_policy"])
def test_sources_cli_rejects_unknown_config_field_without_output(
    tmp_path: Path, section: str
) -> None:
    # Given: a config carries an unknown root or terms-policy field.
    raw_value: JSONValue = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    assert isinstance(raw_value, dict)
    target = raw_value if section == "registry" else raw_value["terms_policy"]
    assert isinstance(target, dict)
    target["unrecognized_field"] = "blocked"
    path = tmp_path / f"{section}-unknown.json"
    _ = path.write_text(json.dumps(raw_value), encoding="utf-8")
    command = [
        sys.executable,
        "-m",
        "tools.topic_feedback_cli",
        "sources",
        "--config",
        str(path),
        "--format",
        "json",
    ]

    # When: the real CLI receives the malformed local config.
    result = subprocess.run(command, check=False, capture_output=True, text=True)

    # Then: it fails closed without producing a registry or calling a provider.
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr


def test_config_check_cli_explains_shadow_without_selection_mutation() -> None:
    # Given: the synthetic shadow rollout and the real module CLI.
    command = [
        sys.executable,
        "-m",
        "tools.topic_feedback_cli",
        "config-check",
        "--root",
        ".",
        "--config",
        str(SHADOW_PATH),
        "--explain",
    ]

    # When: the rollout is checked twice.
    first = subprocess.run(command, check=False, capture_output=True, text=True)
    second = subprocess.run(command, check=False, capture_output=True, text=True)

    # Then: the stable explanation proves shadow cannot change selection.
    assert first.returncode == 0
    assert first.stderr == ""
    assert first.stdout == second.stdout
    assert "active=topic-baseline-v1" in first.stdout
    assert "shadow=topic-feedback-v1" in first.stdout
    assert "selection_mutation=false" in first.stdout


def test_config_check_cli_rejects_challenger_without_evaluation(tmp_path: Path) -> None:
    # Given: a canary config without an external approval argument.
    raw_value: JSONValue = json.loads(SHADOW_PATH.read_text(encoding="utf-8"))
    assert isinstance(raw_value, dict)
    raw_value["rollout_tier"] = "canary"
    raw_value["active_score_version"] = "topic-feedback-v1"
    path = tmp_path / "canary.json"
    _ = path.write_text(json.dumps(raw_value), encoding="utf-8")

    # When: the real CLI checks the unapproved promotion.
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.topic_feedback_cli",
            "config-check",
            "--root",
            str(tmp_path),
            "--config",
            str(path),
            "--explain",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    # Then: the trust-boundary message and exit code are exact.
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == "active score version requires approved evaluation\n"


def test_config_check_cli_has_no_caller_controlled_approval_override() -> None:
    # Given: a caller tries to pair crafted evidence with its own digest.
    command = [
        sys.executable,
        "-m",
        "tools.topic_feedback_cli",
        "config-check",
        "--root",
        ".",
        "--config",
        str(Path("tests/fixtures/feedback/active-without-evaluation.json")),
        "--evaluation",
        "/tmp/caller.json",
        "--evaluation-digest",
        "sha256:caller",
    ]

    # When: the real CLI parses the attempted trust override.
    result = subprocess.run(command, check=False, capture_output=True, text=True)

    # Then: argparse rejects both unsupported arguments before config loading.
    assert result.returncode == 2
    assert result.stdout == ""
    assert "unrecognized arguments" in result.stderr


def test_rollback_cli_reports_append_only_record_and_digest(tmp_path: Path) -> None:
    # Given: an explicit temporary root and rollback identity.
    config_path = tmp_path / "config" / "rollout.json"
    config_path.parent.mkdir()
    _ = config_path.write_bytes(SHADOW_PATH.read_bytes())
    command = [
        sys.executable,
        "-m",
        "tools.topic_feedback_cli",
        "rollback",
        "--root",
        str(tmp_path),
        "--config",
        str(config_path),
        "--rollback-id",
        "RB-CLI-001",
        "--captured-at",
        "2026-09-09T09:00:00+09:00",
    ]

    # When: the exact rollback command is replayed.
    first = subprocess.run(command, check=False, capture_output=True, text=True)
    second = subprocess.run(command, check=False, capture_output=True, text=True)

    # Then: one immutable record is reported identically without historical deletion.
    assert first.returncode == 0
    assert first.stderr == ""
    assert first.stdout == second.stdout
    payload: JSONValue = json.loads(first.stdout)
    assert isinstance(payload, dict)
    assert payload["path"] == str(
        tmp_path / "metadata/topic-feedback-rollbacks/RB-CLI-001.json"
    )
    assert isinstance(payload["digest"], str)
    assert payload["active_score_version"] == "topic-baseline-v1"
    assert payload["new_feedback_artifacts_enabled"] is False


def test_config_check_subprocess_observes_existing_rollback_fence(
    tmp_path: Path,
) -> None:
    # Given: a rollback created by one CLI process under an explicit root.
    config_path = tmp_path / "config" / "rollout.json"
    config_path.parent.mkdir()
    _ = config_path.write_bytes(SHADOW_PATH.read_bytes())
    rollback = subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.topic_feedback_cli",
            "rollback",
            "--root",
            str(tmp_path),
            "--config",
            str(config_path),
            "--rollback-id",
            "RB-FRESH-001",
            "--captured-at",
            "2026-09-09T09:00:00+09:00",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    # When: a fresh process checks the same shadow config.
    checked = subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.topic_feedback_cli",
            "config-check",
            "--root",
            str(tmp_path),
            "--config",
            str(config_path),
            "--explain",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    # Then: the persisted fence forces baseline selection and artifact disablement.
    assert rollback.returncode == 0
    assert checked.returncode == 0
    assert checked.stderr == ""
    assert checked.stdout == (
        "active=topic-baseline-v1 shadow=topic-feedback-v1 "
        "selection_mutation=false new_feedback_artifacts_enabled=false\n"
    )


def test_import_cli_canonicalizes_equivalent_json_and_rejects_duplicate(
    tmp_path: Path,
) -> None:
    # Given: equivalent explicit files with different JSON key ordering.
    value: JSONValue = json.loads(SIGNAL_FIXTURE.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    first_input = tmp_path / "first.json"
    second_input = tmp_path / "second.json"
    _ = first_input.write_text(json.dumps(value), encoding="utf-8")
    _ = second_input.write_text(
        json.dumps(dict(reversed(tuple(value.items())))), encoding="utf-8"
    )
    first_input_bytes = first_input.read_bytes()
    second_input_bytes = second_input.read_bytes()
    root = tmp_path / "project"
    root.mkdir()
    base = [
        sys.executable,
        "-m",
        "tools.topic_feedback_cli",
        "import",
        "--source",
        "naver-datalab",
        "--as-of-date",
        "2026-09-01",
        "--root",
        str(root),
        "--json",
    ]

    # When: the two inputs target the same capture identity.
    first = subprocess.run(
        [*base, "--input", str(first_input)],
        check=False,
        capture_output=True,
        text=True,
    )
    equivalent = subprocess.run(
        [*base, "--input", str(second_input), "--dry-run"],
        check=False,
        capture_output=True,
        text=True,
    )
    second = subprocess.run(
        [*base, "--input", str(second_input)],
        check=False,
        capture_output=True,
        text=True,
    )

    # Then: the canonical digest is stable and identity replay is rejected.
    assert first.returncode == 0
    assert first.stderr == ""
    first_output: JSONValue = json.loads(first.stdout)
    assert isinstance(first_output, dict)
    output_digest = first_output["digest"]
    assert isinstance(output_digest, str)
    assert output_digest.startswith("sha256:")
    equivalent_output: JSONValue = json.loads(equivalent.stdout)
    assert isinstance(equivalent_output, dict)
    assert equivalent_output["digest"] == output_digest
    assert second.returncode == 2
    assert second.stdout == ""
    assert second.stderr == "snapshot is append-only\n"
    snapshots = tuple(root.glob("metadata/topic-signals/**/*.json"))
    assert len(snapshots) == 1
    assert not tuple(root.rglob(".tmp-*"))
    assert first_input.read_bytes() == first_input_bytes
    assert second_input.read_bytes() == second_input_bytes


def test_import_cli_dry_run_reports_path_and_digest_without_writes(
    tmp_path: Path,
) -> None:
    # Given: a valid local fixture and an empty existing root.
    root = tmp_path / "project"
    root.mkdir()

    # When: import is invoked in dry-run mode.
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.topic_feedback_cli",
            "import",
            "--source",
            "naver-datalab",
            "--input",
            str(SIGNAL_FIXTURE),
            "--as-of-date",
            "2026-09-01",
            "--root",
            str(root),
            "--dry-run",
            "--json",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    # Then: the planned immutable target is observable but no byte is written.
    assert result.returncode == 0
    assert result.stderr == ""
    payload: JSONValue = json.loads(result.stdout)
    assert isinstance(payload, dict)
    assert payload["dry_run"] is True
    output_path = payload["path"]
    output_digest = payload["digest"]
    assert isinstance(output_path, str)
    assert isinstance(output_digest, str)
    assert output_path.endswith(
        "metadata/topic-signals/naver-datalab/2026-09-01/synthetic-datalab-001.json"
    )
    assert output_digest.startswith("sha256:")
    assert list(root.iterdir()) == []


def test_import_cli_rejects_url_and_secret_without_reflection(tmp_path: Path) -> None:
    # Given: one forbidden URL input and one explicit file containing a secret field.
    root = tmp_path / "project"
    root.mkdir()
    secret = tmp_path / "secret.json"
    value: JSONValue = json.loads(SIGNAL_FIXTURE.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    raw_payload = value["raw_payload"]
    assert isinstance(raw_payload, dict)
    raw_payload["Authorization"] = "Bearer must-not-leak"
    _ = secret.write_text(json.dumps(value), encoding="utf-8")
    base = [
        sys.executable,
        "-m",
        "tools.topic_feedback_cli",
        "import",
        "--source",
        "naver-datalab",
        "--as-of-date",
        "2026-09-01",
        "--root",
        str(root),
        "--json",
    ]

    # When: each untrusted boundary is invoked through the real CLI.
    remote = subprocess.run(
        [*base, "--input", "https://example.invalid/data.json"],
        check=False,
        capture_output=True,
        text=True,
    )
    leaked = subprocess.run(
        [*base, "--input", str(secret)],
        check=False,
        capture_output=True,
        text=True,
    )

    # Then: both fail locally without output or secret reflection.
    assert remote.returncode == leaked.returncode == 2
    assert remote.stdout == leaked.stdout == ""
    assert "URL" in remote.stderr
    assert "must-not-leak" not in leaked.stderr
    assert list(root.iterdir()) == []


def test_import_cli_allows_dry_run_but_blocks_write_after_rollback(
    tmp_path: Path,
) -> None:
    # Given: a durable rollback fence under an isolated project root.
    root = tmp_path / "project"
    config_path = root / "config/rollout.json"
    config_path.parent.mkdir(parents=True)
    _ = config_path.write_bytes(SHADOW_PATH.read_bytes())
    rollback = subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.topic_feedback_cli",
            "rollback",
            "--root",
            str(root),
            "--config",
            str(config_path),
            "--rollback-id",
            "RB-IMPORT-001",
            "--captured-at",
            "2026-09-09T09:00:00+09:00",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    base = [
        sys.executable,
        "-m",
        "tools.topic_feedback_cli",
        "import",
        "--source",
        "naver-datalab",
        "--input",
        str(SIGNAL_FIXTURE),
        "--as-of-date",
        "2026-09-01",
        "--root",
        str(root),
        "--json",
    ]

    # When: validation-only and mutating imports run against the same fence.
    dry_run = subprocess.run(
        [*base, "--dry-run"], check=False, capture_output=True, text=True
    )
    write = subprocess.run(base, check=False, capture_output=True, text=True)

    # Then: planning remains available but no new snapshot can be committed.
    assert rollback.returncode == 0
    assert dry_run.returncode == 0
    assert write.returncode == 2
    assert write.stderr == "new feedback artifacts are disabled by rollback\n"
    assert not (root / "metadata/topic-signals").exists()


def test_import_cli_rejects_capture_path_escape_before_write(tmp_path: Path) -> None:
    # Given: an otherwise valid explicit file whose capture ID traverses upward.
    value: JSONValue = json.loads(SIGNAL_FIXTURE.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    value["capture_id"] = "../escape"
    input_path = tmp_path / "traversal.json"
    _ = input_path.write_text(json.dumps(value), encoding="utf-8")
    root = tmp_path / "project"
    root.mkdir()

    # When: the real import command derives its destination.
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.topic_feedback_cli",
            "import",
            "--source",
            "naver-datalab",
            "--input",
            str(input_path),
            "--as-of-date",
            "2026-09-01",
            "--root",
            str(root),
            "--json",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    # Then: the safe relative-path boundary rejects it without a partial tree.
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == "snapshot path escapes root\n"
    assert list(root.iterdir()) == []


def test_import_cli_rejects_fifo_promptly_without_artifacts(tmp_path: Path) -> None:
    # Given: an explicit local input path is a FIFO with no writer attached.
    root = tmp_path / "project"
    root.mkdir()
    fifo = tmp_path / "blocked-input"
    os.mkfifo(fifo)

    # When: the real CLI attempts to import it under a strict timeout.
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.topic_feedback_cli",
            "import",
            "--source",
            "naver-datalab",
            "--input",
            str(fifo),
            "--as-of-date",
            "2026-09-01",
            "--root",
            str(root),
            "--json",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=2,
    )

    # Then: the non-regular input is rejected without revealing its path or writing.
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == "import input file is unreadable or unsafe\n"
    assert str(fifo) not in result.stderr
    assert list(root.iterdir()) == []


def test_import_blog_stats_cli_links_and_persists_canonical_snapshot(
    tmp_path: Path,
) -> None:
    # Given: an isolated project with a known publication and owner allowlist.
    linked = _create_legacy_publication(tmp_path)
    command = [
        sys.executable,
        "-m",
        "tools.topic_feedback_cli",
        "import-blog-stats",
        "--input",
        str(BLOG_STATS_FIXTURE),
        "--owner-config",
        str(BLOG_OWNER_FIXTURE),
        "--root",
        str(tmp_path),
        "--json",
    ]

    # When: the real CLI imports the manual export.
    result = subprocess.run(command, check=False, capture_output=True, text=True)

    # Then: one linked v2 snapshot is returned and strictly persisted.
    assert linked.returncode == result.returncode == 0
    assert result.stderr == ""
    payload: JSONValue = json.loads(result.stdout)
    assert isinstance(payload, dict)
    assert payload["count"] == 1
    snapshots = payload["snapshots"]
    assert isinstance(snapshots, list)
    snapshot = snapshots[0]
    assert isinstance(snapshot, dict)
    assert snapshot["publication_run_id"] == "RUN-BLOG-STATS"
    assert snapshot["coverage_end"] == "2026-09-08"
    paths = payload["paths"]
    assert isinstance(paths, list)
    assert len(paths) == 1
    assert Path(str(paths[0])).read_bytes()


def test_import_blog_stats_cli_rejects_fifo_promptly_without_artifacts(
    tmp_path: Path,
) -> None:
    # Given: a known publication and an explicit stats input path that is a FIFO.
    root = tmp_path / "project"
    root.mkdir()
    linked = _create_legacy_publication(root)
    fifo = tmp_path / "blocked-stats"
    os.mkfifo(fifo)

    # When: the real import CLI reads the path under a strict timeout.
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.topic_feedback_cli",
            "import-blog-stats",
            "--input",
            str(fifo),
            "--owner-config",
            str(BLOG_OWNER_FIXTURE),
            "--root",
            str(root),
            "--json",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=2,
    )

    # Then: it fails closed without path disclosure, hanging, or partial writes.
    assert linked.returncode == 0
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == "blog stats input is unreadable or unsafe\n"
    assert str(fifo) not in result.stderr
    assert not (root / "metadata/blog-stats").exists()


@pytest.mark.parametrize(
    ("fixture", "message"),
    [
        (
            Path("tests/fixtures/feedback/blog-stats-owner-mismatch.csv"),
            "owner mismatch",
        ),
        (Path("tests/fixtures/feedback/blog-stats-sparse.csv"), "privacy violation"),
    ],
)
def test_import_blog_stats_cli_rejects_whole_invalid_batch(
    tmp_path: Path, fixture: Path, message: str
) -> None:
    # Given: a known post but an invalid owner or sparse aggregate bucket.
    linked = _create_legacy_publication(tmp_path)

    # When: the fixture crosses the real CLI boundary.
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.topic_feedback_cli",
            "import-blog-stats",
            "--input",
            str(fixture),
            "--owner-config",
            str(BLOG_OWNER_FIXTURE),
            "--root",
            str(tmp_path),
            "--json",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    # Then: redacted exit 2 leaves no snapshot from the batch.
    assert linked.returncode == 0
    assert result.returncode == 2
    assert result.stdout == ""
    assert message in result.stderr
    assert not (tmp_path / "metadata/blog-stats").exists()


def test_import_blog_stats_cli_obeys_durable_rollback_fence(tmp_path: Path) -> None:
    # Given: a known post and a rollback fence created before import.
    linked = _create_legacy_publication(tmp_path)
    config_path = tmp_path / "config/rollout.json"
    config_path.parent.mkdir()
    _ = config_path.write_bytes(SHADOW_PATH.read_bytes())
    rollback = subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.topic_feedback_cli",
            "rollback",
            "--root",
            str(tmp_path),
            "--config",
            str(config_path),
            "--rollback-id",
            "RB-BLOG-001",
            "--captured-at",
            "2026-09-09T09:00:00+09:00",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    # When: a valid manual import reaches the actual write fence.
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.topic_feedback_cli",
            "import-blog-stats",
            "--input",
            str(BLOG_STATS_FIXTURE),
            "--owner-config",
            str(BLOG_OWNER_FIXTURE),
            "--root",
            str(tmp_path),
            "--json",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    # Then: validation may complete but no new feedback artifact is written.
    assert linked.returncode == rollback.returncode == 0
    assert result.returncode == 2
    assert result.stderr == "new feedback artifacts are disabled by rollback\n"
    assert not (tmp_path / "metadata/blog-stats").exists()


def test_cohorts_cli_outputs_canonical_mixed_horizons(tmp_path: Path) -> None:
    # Given: one publication with a cumulative observation at its 7d cutoff.
    linked = _create_legacy_publication(tmp_path)
    imported = subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.topic_feedback_cli",
            "import-blog-stats",
            "--input",
            str(BLOG_STATS_FIXTURE),
            "--owner-config",
            str(BLOG_OWNER_FIXTURE),
            "--root",
            str(tmp_path),
            "--json",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    command = [
        sys.executable,
        "-m",
        "tools.topic_feedback_cli",
        "cohorts",
        "--as-of",
        "2026-09-15T23:30:00+09:00",
        "--root",
        str(tmp_path),
        "--json",
    ]

    # When: the derived cohort command is run twice.
    first = subprocess.run(command, check=False, capture_output=True, text=True)
    second = subprocess.run(command, check=False, capture_output=True, text=True)

    # Then: stdout is canonical and the 7d/28d lifecycle is mixed as expected.
    assert linked.returncode == imported.returncode == first.returncode == 0
    assert first.stderr == second.stderr == ""
    assert first.stdout == second.stdout
    payload: JSONValue = json.loads(first.stdout)
    assert isinstance(payload, dict)
    cohorts = payload["cohorts"]
    assert isinstance(cohorts, list)
    cohort = cohorts[0]
    assert isinstance(cohort, dict)
    horizons = cohort["horizons"]
    assert isinstance(horizons, dict)
    seven, twenty_eight = horizons["7d"], horizons["28d"]
    assert isinstance(seven, dict) and isinstance(twenty_eight, dict)
    assert seven["status"] == "mature"
    assert twenty_eight["status"] == "pending"
    assert seven["observation_id"] == "WEEK1-POST-001"
    input_digests = payload["input_digests"]
    assert isinstance(input_digests, list)
    assert seven["observation_digest"] in input_digests
