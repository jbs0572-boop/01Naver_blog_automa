from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tools.article_quality import (
    QUALITY_REPORT_VERSION,
    RUBRIC_VERSION,
    SCORE_MAXIMA,
    assessment_digest,
    assessment_path,
)
from tools.contract_types import PIPELINE_VERSION, JSONMap, JSONValue
from tools.manifest import ManifestBuildInput, build_manifest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BLOG_ID = "blog-hook"
WRITE_URL = f"https://blog.naver.com/{BLOG_ID}/postwrite"
DRAFTS_URL = f"https://blog.naver.com/{BLOG_ID}/drafts"
NAVER_INPUT = "[TITLE]\nHook title\n\n[TEXT]\nHook body\n"


def _stage(run_id: str, topic_id: str, digest: str, stage: str) -> JSONMap:
    quality: JSONMap = {"artifact_digest": digest}
    if stage == "notion-rider":
        quality = {
            "storage_integrity": "passed",
            "notion_page_id": "page-hook",
            "notion_last_verified_at": "2026-08-31T00:03:00+00:00",
            "expected_notion_content_digest": digest,
            "notion_content_digest": digest,
            "notion_roundtrip_digest": digest,
            "artifact_digest": digest,
            "notion_target_id": "datasource-hook",
        }
    return {
        "event_type": "stage",
        "pipeline_version": PIPELINE_VERSION,
        "telemetry_version": 2,
        "batch_id": "BATCH-hook",
        "run_id": run_id,
        "topic_id": topic_id,
        "stage": stage,
        "started_at": (
            "2026-08-31T00:02:00+00:00"
            if stage == "notion-rider"
            else "2026-08-31T00:00:00+00:00"
        ),
        "ended_at": (
            "2026-08-31T00:03:00+00:00"
            if stage == "notion-rider"
            else "2026-08-31T00:01:00+00:00"
        ),
        "duration_ms": 60_000,
        "depends_on": (
            ["content-assembler"] if stage == "notion-rider" else ["image-maker"]
        ),
        "status": "passed",
        "attempt": 1,
        "quality": quality,
    }


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path, str]:
    keyword = "naver-hook"
    run_id = "RUN-naver-hook"
    topic_id = "TOPIC-naver-hook"
    final_dir = tmp_path / "final"
    asset_dir = tmp_path / "assets" / keyword
    final_dir.mkdir(parents=True)
    asset_dir.mkdir(parents=True)
    _ = (asset_dir / "body.png").write_bytes(b"body")
    _ = (asset_dir / "thumbnail.png").write_bytes(b"thumbnail")
    _ = (asset_dir / "image-map.md").write_text("# map\n", encoding="utf-8")
    _ = (final_dir / f"{keyword}.md").write_text(
        "![body](../assets/naver-hook/body.png)\n", encoding="utf-8"
    )
    for suffix in ("naver-layout", "naver-copy"):
        _ = (final_dir / f"{keyword}-{suffix}.md").write_text(
            f"# {suffix}\n", encoding="utf-8"
        )
    _ = (final_dir / f"{keyword}-naver-input.md").write_text(
        NAVER_INPUT, encoding="utf-8"
    )
    _ = (tmp_path / "AGENTS.md").write_text("test root\n", encoding="utf-8")
    _ = (tmp_path / "notion-config.md").write_text(
        "- 데이터 소스 ID: `datasource-hook`\n", encoding="utf-8"
    )
    _ = (tmp_path / "naver-config.md").write_text(
        "\n".join(
            (
                f"- blog_id: `{BLOG_ID}`",
                "- browser_connector: `browser`",
                f"- write_url: `{WRITE_URL}`",
                f"- drafts_url: `{DRAFTS_URL}`",
                "- viewport: `390x844`",
                "- auth_locator: `#auth-ok`",
                "- save_locator: `#save-draft`",
                "- title_locator: `#title`",
                "- body_locator: `#body`",
                "- draft_list_locator: `#draft-list`",
                "- selector_status: `verified`",
                "",
            )
        ),
        encoding="utf-8",
    )
    manifest = build_manifest(
        ManifestBuildInput(
            tmp_path,
            keyword,
            run_id,
            topic_id,
            "2026-08-31T00:00:00+00:00",
        )
    )
    manifest_path = tmp_path / "manifest.json"
    _ = manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    digest = manifest["artifact_digest"]
    assert isinstance(digest, str)
    quality_report: JSONMap = {
        "report_version": QUALITY_REPORT_VERSION,
        "rubric_version": RUBRIC_VERSION,
        "run_id": run_id,
        "topic_id": topic_id,
        "artifact_digest": digest,
        "reviewer": "fixture-reviewer",
        "reviewed_at": "2026-08-31T00:03:00+00:00",
        "scores": dict(SCORE_MAXIMA),
        "evidence": {name: "fixture evidence" for name in SCORE_MAXIMA},
        "immediate_failures": [],
        "cause_type": None,
        "failure_stage": None,
        "next_action": "none",
    }
    quality_report["report_digest"] = assessment_digest(quality_report)
    quality_path = assessment_path(tmp_path, run_id)
    quality_path.parent.mkdir(parents=True, exist_ok=True)
    _ = quality_path.write_text(json.dumps(quality_report), encoding="utf-8")
    run_log = tmp_path / "run.jsonl"
    confirmation_request_digest = "f" * 64
    events = [
        _stage(run_id, topic_id, digest, "content-assembler"),
        _stage(run_id, topic_id, digest, "notion-rider"),
        {
            **_stage(run_id, topic_id, digest, "naver-rider"),
            "quality": {
                "artifact_digest": digest,
                "confirmation_request_digest": confirmation_request_digest,
            },
        },
        {
            "event_type": "confirmation",
            "pipeline_version": PIPELINE_VERSION,
            "run_id": run_id,
            "action": "naver-draft-save",
            "target_blog_id": BLOG_ID,
            "title": "Hook title",
            "artifact_digest": digest,
            "confirmation_request_digest": confirmation_request_digest,
            "requested_at": "2026-08-31T00:04:00+00:00",
            "confirmed_at": "2026-08-31T00:05:00+00:00",
            "actor": "operator",
        },
    ]
    _ = run_log.write_text(
        "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
    )
    return tmp_path, manifest_path, run_log, run_id


def _hook(
    root: Path,
    manifest_path: Path,
    run_log: Path,
    run_id: str,
    tool_name: str,
    tool_input: JSONMap,
    phase: str,
    *,
    include_confirmation: bool = True,
) -> str:
    if not include_confirmation:
        events = run_log.read_text(encoding="utf-8").splitlines()
        _ = run_log.write_text("\n".join(events[:-1]) + "\n", encoding="utf-8")
    env = os.environ.copy()
    env.update(
        {
            "WORKFLOW_GATE": "naver_draft_save",
            "WORKFLOW_MANIFEST": str(manifest_path),
            "WORKFLOW_RUN_LOG": str(run_log),
            "WORKFLOW_RUN_ID": run_id,
            "WORKFLOW_TARGET_ID": BLOG_ID,
            "WORKFLOW_BLOG_ID": BLOG_ID,
            "WORKFLOW_NOTION_PAGE_ID": "page-hook",
            "WORKFLOW_NOTION_VERIFIED_AT": "2026-08-31T00:03:00+00:00",
            "WORKFLOW_EXPECTED_NOTION_CONTENT_DIGEST": _manifest_digest(
                manifest_path
            ),
            "WORKFLOW_NOTION_CONTENT_DIGEST": _manifest_digest(manifest_path),
            "WORKFLOW_NOTION_ROUNDTRIP_DIGEST": _manifest_digest(manifest_path),
            "WORKFLOW_Q2_ARTIFACT_DIGEST": _manifest_digest(manifest_path),
            "WORKFLOW_NAVER_PHASE": phase,
        }
    )
    completed = subprocess.run(
        [sys.executable, "-m", "tools.workflow_verifier", "hook"],
        cwd=PROJECT_ROOT,
        env=env,
        input=json.dumps(
            {"tool_name": tool_name, "tool_input": tool_input, "cwd": str(root)}
        ),
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    parsed: JSONValue = json.loads(completed.stdout)
    assert isinstance(parsed, dict)
    output = parsed["hookSpecificOutput"]
    assert isinstance(output, dict)
    decision = output["permissionDecision"]
    assert isinstance(decision, str)
    if decision == "deny":
        reason = output.get("permissionDecisionReason")
        assert isinstance(reason, str)
        return f"deny: {reason}"
    return decision


def _manifest_digest(path: Path) -> str:
    value: JSONValue = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    digest = value["artifact_digest"]
    assert isinstance(digest, str)
    return digest


@pytest.mark.parametrize(
    ("tool_name", "tool_input"),
    [
        ("browser.goto", {"url": WRITE_URL}),
        ("browser.text", {"selector": "#auth-ok"}),
        ("browser.fill", {"selector": "#title", "value": "Hook title"}),
        ("browser.fill", {"selector": "#body", "value": NAVER_INPUT}),
    ],
)
def test_naver_prepare_allows_only_exact_configured_targets(
    tmp_path: Path, tool_name: str, tool_input: JSONMap
) -> None:
    root, manifest_path, run_log, run_id = _fixture(tmp_path)

    decision = _hook(
        root, manifest_path, run_log, run_id, tool_name, tool_input, "prepare"
    )

    assert decision == "allow"


@pytest.mark.parametrize(
    ("tool_name", "tool_input"),
    [
        ("browser.click", {"selector": "#save-draft"}),
        ("browser.goto", {"url": DRAFTS_URL}),
        ("browser.text", {"selector": "#draft-list"}),
    ],
)
def test_naver_save_allows_exact_targets_after_confirmation(
    tmp_path: Path, tool_name: str, tool_input: JSONMap
) -> None:
    root, manifest_path, run_log, run_id = _fixture(tmp_path)

    decision = _hook(
        root, manifest_path, run_log, run_id, tool_name, tool_input, "save"
    )

    assert decision == "allow"


@pytest.mark.parametrize(
    ("tool_name", "tool_input", "phase"),
    [
        ("browser.publish", {}, "save"),
        ("browser.delete", {}, "save"),
        ("browser.evaluate", {"expression": "document.body.innerHTML=''"}, "prepare"),
        ("browser.javascript", {"script": "location='https://evil.example'"}, "prepare"),
        ("browser.click", {"selector": "#publish"}, "save"),
        ("browser.click", {"selector": "#settings"}, "prepare"),
        ("browser.goto", {"url": "https://evil.example/postwrite"}, "prepare"),
        (
            "browser.goto",
            {"url": "https://blog.naver.com/another-blog/postwrite"},
            "prepare",
        ),
        ("chrome.fill", {"selector": "#title", "value": "Hook title"}, "prepare"),
        ("computer.click", {"selector": "#save-draft"}, "save"),
        ("browser.click", {"selector": "#save-draft"}, "prepare"),
        ("browser.fill", {"selector": "#title", "value": "Hook title"}, "save"),
        ("browser.fill", {"selector": "#title", "value": "Wrong title"}, "prepare"),
        ("browser.fill", {"selector": "#body", "value": "Wrong body"}, "prepare"),
        ("browser.snapshot", {}, "prepare"),
    ],
)
def test_naver_gate_denies_adversarial_browser_operations(
    tmp_path: Path, tool_name: str, tool_input: JSONMap, phase: str
) -> None:
    root, manifest_path, run_log, run_id = _fixture(tmp_path)

    decision = _hook(
        root, manifest_path, run_log, run_id, tool_name, tool_input, phase
    )

    assert decision.startswith("deny:")


def test_naver_save_denies_exact_locator_without_confirmation(tmp_path: Path) -> None:
    root, manifest_path, run_log, run_id = _fixture(tmp_path)

    decision = _hook(
        root,
        manifest_path,
        run_log,
        run_id,
        "browser.click",
        {"selector": "#save-draft"},
        "save",
        include_confirmation=False,
    )

    assert decision.startswith("deny:")


def test_naver_prepare_and_save_are_denied_for_missing_or_low_quality_review(
    tmp_path: Path,
) -> None:
    root, manifest_path, run_log, run_id = _fixture(tmp_path)
    report_path = assessment_path(root, run_id)
    raw_report: JSONValue = json.loads(report_path.read_text(encoding="utf-8"))
    assert isinstance(raw_report, dict)
    report: JSONMap = raw_report
    assert isinstance(report, dict)

    report_path.unlink()
    missing_decision = _hook(
        root,
        manifest_path,
        run_log,
        run_id,
        "browser.goto",
        {"url": WRITE_URL},
        "prepare",
    )
    assert missing_decision.startswith("deny:")
    assert "quality report" in missing_decision

    raw_scores = report.get("scores")
    assert isinstance(raw_scores, dict)
    scores: JSONMap = raw_scores
    scores["factual_accuracy"] = 0
    scores["source_completeness"] = 8
    report["cause_type"] = "topic_unsuitable"
    report["failure_stage"] = "topic-selector"
    report["next_action"] = "discard this topic and select another"
    report["report_digest"] = assessment_digest(report)
    _ = report_path.write_text(json.dumps(report), encoding="utf-8")
    low_score_prepare = _hook(
        root,
        manifest_path,
        run_log,
        run_id,
        "browser.goto",
        {"url": WRITE_URL},
        "prepare",
    )
    low_score_save = _hook(
        root,
        manifest_path,
        run_log,
        run_id,
        "browser.click",
        {"selector": "#save-draft"},
        "save",
    )
    assert low_score_prepare.startswith("deny:")
    assert "quality_failed" in low_score_prepare
    assert low_score_save.startswith("deny:")
    assert "quality_failed" in low_score_save


def test_naver_save_denies_confirmation_for_wrong_title(tmp_path: Path) -> None:
    root, manifest_path, run_log, run_id = _fixture(tmp_path)
    events = [json.loads(line) for line in run_log.read_text(encoding="utf-8").splitlines()]
    confirmation = events[-1]
    assert isinstance(confirmation, dict)
    confirmation["title"] = "Wrong title"
    _ = run_log.write_text(
        "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
    )

    decision = _hook(
        root,
        manifest_path,
        run_log,
        run_id,
        "browser.click",
        {"selector": "#save-draft"},
        "save",
    )

    assert decision.startswith("deny:")


def test_naver_save_requires_confirmation_for_latest_preparation(tmp_path: Path) -> None:
    root, manifest_path, run_log, run_id = _fixture(tmp_path)
    events = [
        json.loads(line) for line in run_log.read_text(encoding="utf-8").splitlines()
    ]
    renewed_digest = "e" * 64
    renewed = {
        **events[-2],
        "started_at": "2026-08-31T00:04:00+00:00",
        "ended_at": "2026-08-31T00:05:00+00:00",
        "quality": {
            **events[-2]["quality"],
            "confirmation_request_digest": renewed_digest,
        },
    }
    events.append(renewed)
    _ = run_log.write_text(
        "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
    )

    stale_decision = _hook(
        root,
        manifest_path,
        run_log,
        run_id,
        "browser.click",
        {"selector": "#save-draft"},
        "save",
    )
    assert stale_decision.startswith("deny:")

    fresh_confirmation = {
        **events[-2],
        "confirmation_request_digest": renewed_digest,
        "requested_at": "2026-08-31T00:05:00+00:00",
        "confirmed_at": "2026-08-31T00:06:00+00:00",
    }
    events.append(fresh_confirmation)
    _ = run_log.write_text(
        "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
    )
    assert (
        _hook(
            root,
            manifest_path,
            run_log,
            run_id,
            "browser.click",
            {"selector": "#save-draft"},
            "save",
        )
        == "allow"
    )
    completed = {
        **renewed,
        "started_at": "2026-08-31T00:06:00+00:00",
        "ended_at": "2026-08-31T00:07:00+00:00",
        "quality": {"artifact_digest": _manifest_digest(manifest_path)},
    }
    events.append(completed)
    _ = run_log.write_text(
        "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
    )
    repeated_decision = _hook(
        root,
        manifest_path,
        run_log,
        run_id,
        "browser.click",
        {"selector": "#save-draft"},
        "save",
    )
    assert repeated_decision.startswith("deny:")


@pytest.mark.parametrize("confirmation_digest", [None, "not-a-sha256"])
def test_naver_save_denies_unbound_historical_or_malformed_confirmation(
    tmp_path: Path, confirmation_digest: str | None
) -> None:
    root, manifest_path, run_log, run_id = _fixture(tmp_path)
    events = [
        json.loads(line) for line in run_log.read_text(encoding="utf-8").splitlines()
    ]
    confirmation = events[-1]
    if confirmation_digest is None:
        _ = confirmation.pop("confirmation_request_digest")
    else:
        confirmation["confirmation_request_digest"] = confirmation_digest
    _ = run_log.write_text(
        "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
    )

    decision = _hook(
        root,
        manifest_path,
        run_log,
        run_id,
        "browser.click",
        {"selector": "#save-draft"},
        "save",
    )

    assert decision.startswith("deny:")
    if confirmation_digest is None:
        assert "invalid confirmation event schema" not in decision


def test_naver_gate_denies_all_browser_access_without_config(tmp_path: Path) -> None:
    root, manifest_path, run_log, run_id = _fixture(tmp_path)
    (root / "naver-config.md").unlink()

    decision = _hook(
        root,
        manifest_path,
        run_log,
        run_id,
        "browser.goto",
        {"url": WRITE_URL},
        "prepare",
    )

    assert decision.startswith("deny:")


def test_naver_gate_rejects_non_naver_domain_even_when_configured(tmp_path: Path) -> None:
    root, manifest_path, run_log, run_id = _fixture(tmp_path)
    config_path = root / "naver-config.md"
    config = config_path.read_text(encoding="utf-8").replace(
        WRITE_URL, "https://evil.example/blog-hook/postwrite"
    )
    _ = config_path.write_text(config, encoding="utf-8")

    decision = _hook(
        root,
        manifest_path,
        run_log,
        run_id,
        "browser.goto",
        {"url": "https://evil.example/blog-hook/postwrite"},
        "prepare",
    )

    assert decision.startswith("deny:")
