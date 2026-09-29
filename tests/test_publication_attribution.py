from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from tools.contract_types import ContractError, JSONMap
from tools.manifest import ManifestBuildInput, build_manifest
from tools.publication_metrics_link import (
    PublicationAttributionRequest,
    link_publication,
)


def _write_json(path: Path, value: JSONMap) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(json.dumps(value), encoding="utf-8")


def frozen_run_fixture(root: Path, run_id: str, keyword: str, post_id: str) -> JSONMap:
    topic_id = f"TOPIC-{keyword}"
    final = root / "final"
    assets = root / "assets" / keyword
    final.mkdir(parents=True, exist_ok=True)
    assets.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-naver-layout", "-naver-copy", "-naver-input"):
        _ = (final / f"{keyword}{suffix}.md").write_text("# fixture\n", encoding="utf-8")
    _ = (assets / "image-map.md").write_text("fixture\n", encoding="utf-8")
    _ = (assets / "thumbnail.png").write_bytes(b"fixture-thumbnail")
    manifest = build_manifest(
        ManifestBuildInput(
            root,
            keyword,
            run_id,
            topic_id,
            "2026-09-01T08:00:00+09:00",
        )
    )
    manifest_path = root / "manifests" / f"{run_id}-workflow-manifest.json"
    _write_json(manifest_path, manifest)
    state: JSONMap = {
        "run_id": run_id,
        "status": "draft_saved",
        "topic_id": topic_id,
        "keyword": keyword,
        "artifact_digest": manifest["artifact_digest"],
        "manifest_path": f"manifests/{run_id}-workflow-manifest.json",
        "score_version": "topic-baseline-v1",
        "target_blog_id": "owner",
    }
    _write_json(root / ".automation" / "state" / f"{run_id}.json", state)
    _write_json(
        root / ".automation" / "publications" / f"{run_id}.json",
        {
            "run_id": run_id,
            "topic_id": topic_id,
            "primary_keyword": keyword,
            "publication_link_status": "matched",
            "naver_post_url": f"https://blog.naver.com/owner/{post_id}",
            "published_at": "2026-09-01T09:00:00+09:00",
        },
    )
    return state


def publication_request(root: Path, run_id: str, post_id: str | None = "POST-001") -> PublicationAttributionRequest:
    return PublicationAttributionRequest(
        root=root,
        run_id=run_id,
        blog_post_id=post_id,
        published_at="2026-09-01T09:00:00+09:00",
        captured_at="2026-09-08T09:00:00+09:00",
        source_identity="current-run",
        score_version="topic-baseline-v1",
    )


def test_current_run_link_is_append_only_and_idempotent(tmp_path: Path) -> None:
    state = frozen_run_fixture(tmp_path, "RUN-fixture", "fixture", "12345")

    first = link_publication(publication_request(tmp_path, "RUN-fixture"))
    output = Path(str(first["path"]))
    before = output.read_bytes()
    second = link_publication(publication_request(tmp_path, "RUN-fixture"))

    assert first == second
    assert output.read_bytes() == before
    payload = first["publication_link"]
    assert isinstance(payload, dict)
    assert payload["run_id"] == "RUN-fixture"
    assert payload["blog_post_id"] == "POST-001"
    assert payload["artifact_digest"] == state["artifact_digest"]
    assert payload["score_version"] == "topic-baseline-v1"
    assert payload["target_blog_id"] == "owner"


def test_current_run_rejects_stale_caller_artifact_digest(tmp_path: Path) -> None:
    _ = frozen_run_fixture(tmp_path, "RUN-fixture", "fixture", "12345")
    request = replace(
        publication_request(tmp_path, "RUN-fixture"),
        artifact_digest="sha256:" + "f" * 64,
    )

    with pytest.raises(ContractError, match="stale artifact digest"):
        _ = link_publication(request)


def test_same_post_on_different_run_rejects_without_changing_existing_bytes(
    tmp_path: Path,
) -> None:
    _ = frozen_run_fixture(tmp_path, "RUN-first", "first", "12345")
    _ = frozen_run_fixture(tmp_path, "RUN-other", "other", "12345")
    first = link_publication(publication_request(tmp_path, "RUN-first"))
    path = Path(str(first["path"]))
    before = hashlib.sha256(path.read_bytes()).hexdigest()

    with pytest.raises(ContractError, match="blog post identity collision"):
        _ = link_publication(publication_request(tmp_path, "RUN-other"))

    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_url_digits_are_unlinked_without_trusted_rule(tmp_path: Path) -> None:
    _ = frozen_run_fixture(tmp_path, "RUN-fixture", "fixture", "998877")

    result = link_publication(publication_request(tmp_path, "RUN-fixture", None))

    payload = result["publication_link"]
    assert isinstance(payload, dict)
    assert payload["blog_post_id"] is None
    assert payload["status"] == "pending"
    missing = payload["missing_fields"]
    assert isinstance(missing, list)
    assert "blog_post_id" in missing


def test_explicit_id_has_priority_and_approved_url_conflict_rejects(
    tmp_path: Path,
) -> None:
    _ = frozen_run_fixture(tmp_path, "RUN-fixture", "fixture", "12345")
    approval = tmp_path / "approved-url-rule.json"
    _write_json(
        approval,
        {
            "schema_version": "naver-url-rule-approval-v1",
            "approval_status": "approved",
            "official_reference_url": "https://help.naver.com/service/5593",
            "host": "blog.naver.com",
            "path_template": "/{blog_id}/{blog_post_id}",
            "approved_at": "2026-09-08T09:00:00+09:00",
        },
    )
    approval_digest = "sha256:" + hashlib.sha256(approval.read_bytes()).hexdigest()
    request = replace(
        publication_request(tmp_path, "RUN-fixture", "12345"),
        url_rule_approval=approval,
        url_rule_approval_sha256=approval_digest,
    )

    result = link_publication(request)
    payload = result["publication_link"]
    assert isinstance(payload, dict)
    assert payload["blog_post_id"] == "12345"

    with pytest.raises(ContractError, match="conflicts with approved URL identity"):
        _ = link_publication(replace(request, blog_post_id="DIFFERENT"))

    with pytest.raises(ContractError, match="does not match frozen target_blog_id"):
        _ = link_publication(
            replace(
                request,
                naver_post_url="https://blog.naver.com/another-owner/12345",
            )
        )


@pytest.mark.parametrize(
    "url",
    [
        "https://blog.naver.com/owner/123%2Fextra",
        "https://blog.naver.com/owner/123%5Cextra",
        "https://blog.naver.com/owner/123%252fextra",
        "https://blog.naver.com/owner//123",
        "https://blog.naver.com/owner/123/",
        "https://blog.naver.com:8443/owner/123",
        "https://attacker@blog.naver.com/owner/123",
    ],
)
def test_approved_url_rejects_ambiguous_authority_and_path(
    tmp_path: Path, url: str
) -> None:
    # Given: an approved URL rule for the exact host and two path segments.
    _ = frozen_run_fixture(tmp_path, "RUN-fixture", "fixture", "12345")
    approval = tmp_path / "approved-url-rule.json"
    _write_json(
        approval,
        {
            "schema_version": "naver-url-rule-approval-v1",
            "approval_status": "approved",
            "official_reference_url": "https://help.naver.com/service/5593",
            "host": "blog.naver.com",
            "path_template": "/{blog_id}/{blog_post_id}",
            "approved_at": "2026-09-08T09:00:00+09:00",
        },
    )
    request = replace(
        publication_request(tmp_path, "RUN-fixture", None),
        naver_post_url=url,
        url_rule_approval=approval,
        url_rule_approval_sha256=(
            "sha256:" + hashlib.sha256(approval.read_bytes()).hexdigest()
        ),
    )

    # When / Then: ambiguous URL forms cannot establish a publication identity.
    with pytest.raises(ContractError, match="does not exactly match"):
        _ = link_publication(request)


def test_real_cli_links_current_run_with_deterministic_json(tmp_path: Path) -> None:
    _ = frozen_run_fixture(tmp_path, "RUN-fixture", "fixture", "12345")
    command = [
        sys.executable,
        "-m",
        "tools.topic_feedback_cli",
        "link-publication",
        "--run-id",
        "RUN-fixture",
        "--blog-post-id",
        "POST-001",
        "--published-at",
        "2026-09-01T09:00:00+09:00",
        "--root",
        str(tmp_path),
    ]

    first = subprocess.run(command, check=False, capture_output=True, text=True)
    second = subprocess.run(command, check=False, capture_output=True, text=True)

    assert first.returncode == second.returncode == 0
    assert first.stderr == second.stderr == ""
    assert first.stdout == second.stdout
    payload: JSONMap = json.loads(first.stdout)
    link = payload["publication_link"]
    assert isinstance(link, dict)
    assert link["blog_post_id"] == "POST-001"


def test_legacy_import_requires_explicit_identity_and_published_at(tmp_path: Path) -> None:
    request = PublicationAttributionRequest(
        root=tmp_path,
        run_id="LEGACY-2020-001",
        blog_post_id="POST-LEGACY",
        published_at="2020-01-02T10:00:00+09:00",
        captured_at="2026-09-08T09:00:00+09:00",
        source_identity="legacy-import",
        topic_id="TOPIC-legacy",
        keyword="legacy topic",
        artifact_digest="sha256:" + "a" * 64,
        score_version="topic-baseline-v1",
        legacy_identity="legacy-export-row-1",
    )

    result = link_publication(request)
    payload = result["publication_link"]
    assert isinstance(payload, dict)
    assert payload["source_identity"] == "legacy-import"
    assert payload["legacy_identity"] == "legacy-export-row-1"
    assert payload["target_blog_id"] is None
    assert payload["status"] == "pending"

    with pytest.raises(ContractError, match="published_at is required"):
        _ = link_publication(replace(request, published_at=None))


def test_attribution_store_rejects_symlink(tmp_path: Path) -> None:
    _ = frozen_run_fixture(tmp_path, "RUN-fixture", "fixture", "12345")
    outside = tmp_path / "outside"
    outside.mkdir()
    metadata = tmp_path / "metadata"
    metadata.mkdir()
    (metadata / "publication-links").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ContractError, match="symlink"):
        _ = link_publication(publication_request(tmp_path, "RUN-fixture"))


def test_current_run_rejects_mutated_state_score_version(tmp_path: Path) -> None:
    _ = frozen_run_fixture(tmp_path, "RUN-fixture", "fixture", "12345")
    state_path = tmp_path / ".automation" / "state" / "RUN-fixture.json"
    state: JSONMap = json.loads(state_path.read_text(encoding="utf-8"))
    state["score_version"] = "untrusted-challenger-v9"
    _write_json(state_path, state)

    with pytest.raises(ContractError, match="trusted baseline"):
        _ = link_publication(publication_request(tmp_path, "RUN-fixture"))


@pytest.mark.parametrize("score_version", [None, "untrusted-challenger-v9"])
def test_current_run_requires_trusted_caller_score_version(
    tmp_path: Path,
    score_version: str | None,
) -> None:
    _ = frozen_run_fixture(tmp_path, "RUN-fixture", "fixture", "12345")
    request = replace(
        publication_request(tmp_path, "RUN-fixture"),
        score_version=score_version,
    )

    with pytest.raises(ContractError, match="caller score_version.*trusted baseline"):
        _ = link_publication(request)


def test_logical_replay_returns_original_capture_bytes(tmp_path: Path) -> None:
    _ = frozen_run_fixture(tmp_path, "RUN-fixture", "fixture", "12345")
    first = link_publication(publication_request(tmp_path, "RUN-fixture"))
    replay = replace(
        publication_request(tmp_path, "RUN-fixture"),
        captured_at="2026-09-09T10:00:00+09:00",
    )

    second = link_publication(replay)

    assert second == first
    path = Path(str(first["path"]))
    reservation = next(
        (tmp_path / "metadata" / "publication-links" / ".identities").glob("*.json")
    )
    assert reservation.read_bytes() == path.read_bytes()


@pytest.mark.parametrize("run_id", [".identities", ".store.lock", ".internal"])
def test_reserved_run_id_rejects_before_store_mutation(
    tmp_path: Path,
    run_id: str,
) -> None:
    request = PublicationAttributionRequest(
        root=tmp_path,
        run_id=run_id,
        blog_post_id="POST-LEGACY",
        published_at="2020-01-02T10:00:00+09:00",
        captured_at="2026-09-08T09:00:00+09:00",
        source_identity="legacy-import",
        topic_id="TOPIC-legacy",
        keyword="legacy topic",
        artifact_digest="sha256:" + "a" * 64,
        score_version="topic-baseline-v1",
        legacy_identity="legacy-export-row-1",
    )

    with pytest.raises(ContractError, match="reserved"):
        _ = link_publication(request)

    assert not (tmp_path / "metadata").exists()
