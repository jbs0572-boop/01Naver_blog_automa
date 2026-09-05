from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.contract_types import ContractError
from tools.stage_artifact_promotion import promote_stage_artifacts


def test_promotes_only_declared_regular_stage_artifact(tmp_path: Path) -> None:
    # Given: a fresh staging root containing one declared writer artifact.
    project = tmp_path / "project"
    staging = tmp_path / "staging"
    ledger = tmp_path / "ledger.json"
    source = staging / "drafts" / "topic.md"
    source.parent.mkdir(parents=True)
    project.mkdir()
    _ = source.write_text("draft", encoding="utf-8")

    # When: the trusted executor promotes the declared artifact.
    promoted = promote_stage_artifacts(
        stage="writer",
        keyword="topic",
        run_id="RUN-1",
        staging_root=staging,
        project_root=project,
        declared=("drafts/topic.md",),
        ledger_path=ledger,
    )

    # Then: only the canonical destination receives the staged bytes.
    assert promoted == ("drafts/topic.md",)
    assert (project / "drafts" / "topic.md").read_text(encoding="utf-8") == "draft"


@pytest.mark.parametrize("unsafe", ("undeclared", "escape", "symlink"))
def test_rejects_unsafe_staging_content(tmp_path: Path, unsafe: str) -> None:
    # Given: staging contains an undeclared, escaping, or symbolic artifact.
    project = tmp_path / "project"
    staging = tmp_path / "staging"
    ledger = tmp_path / "ledger.json"
    source = staging / "drafts" / "topic.md"
    source.parent.mkdir(parents=True)
    project.mkdir()
    _ = source.write_text("draft", encoding="utf-8")
    declared = ("drafts/topic.md",)
    if unsafe == "undeclared":
        _ = (staging / "notion-config.md").write_text("changed", encoding="utf-8")
    elif unsafe == "escape":
        declared = ("../notion-config.md",)
    else:
        source.unlink()
        source.symlink_to(staging / "missing")

    # When/Then: trusted promotion rejects the staging tree.
    with pytest.raises(ContractError):
        _ = promote_stage_artifacts(
            stage="writer",
            keyword="topic",
            run_id="RUN-1",
            staging_root=staging,
            project_root=project,
            declared=declared,
            ledger_path=ledger,
        )


def test_rejects_existing_historical_destination(tmp_path: Path) -> None:
    # Given: a canonical file exists without this run's ownership record.
    project = tmp_path / "project"
    staging = tmp_path / "staging"
    ledger = tmp_path / "ledger.json"
    source = staging / "drafts" / "topic.md"
    destination = project / "drafts" / "topic.md"
    source.parent.mkdir(parents=True)
    destination.parent.mkdir(parents=True)
    _ = source.write_text("new", encoding="utf-8")
    _ = destination.write_text("historical", encoding="utf-8")

    # When/Then: promotion preserves historical content.
    with pytest.raises(ContractError):
        _ = promote_stage_artifacts(
            stage="writer",
            keyword="topic",
            run_id="RUN-1",
            staging_root=staging,
            project_root=project,
            declared=("drafts/topic.md",),
            ledger_path=ledger,
        )
    assert destination.read_text(encoding="utf-8") == "historical"


def test_same_run_retry_replaces_only_unchanged_owned_destination(tmp_path: Path) -> None:
    # Given: this run owns the canonical file and its hash still matches the ledger.
    project = tmp_path / "project"
    staging = tmp_path / "staging"
    ledger = tmp_path / "ledger.json"
    source = staging / "drafts" / "topic.md"
    source.parent.mkdir(parents=True)
    project.mkdir()
    _ = source.write_text("first", encoding="utf-8")
    _ = promote_stage_artifacts(
        stage="writer",
        keyword="topic",
        run_id="RUN-1",
        staging_root=staging,
        project_root=project,
        declared=("drafts/topic.md",),
        ledger_path=ledger,
    )
    _ = source.write_text("repair", encoding="utf-8")

    # When: the same run promotes a Q1 repair.
    _ = promote_stage_artifacts(
        stage="writer",
        keyword="topic",
        run_id="RUN-1",
        staging_root=staging,
        project_root=project,
        declared=("drafts/topic.md",),
        ledger_path=ledger,
    )

    # Then: the owned file is atomically replaced and ownership remains recorded.
    assert (project / "drafts" / "topic.md").read_text(encoding="utf-8") == "repair"
    assert json.loads(ledger.read_text(encoding="utf-8"))["run_id"] == "RUN-1"


def test_ledger_failure_rolls_back_unowned_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    staging = tmp_path / "staging"
    ledger = tmp_path / "ledger.json"
    source = staging / "drafts" / "topic.md"
    source.parent.mkdir(parents=True)
    project.mkdir()
    _ = source.write_text("draft", encoding="utf-8")

    def failed_ledger(*_args: object, **_kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("tools.stage_artifact_promotion._write_ledger", failed_ledger)

    with pytest.raises(OSError, match="disk full"):
        _ = promote_stage_artifacts(
            stage="writer",
            keyword="topic",
            run_id="RUN-1",
            staging_root=staging,
            project_root=project,
            declared=("drafts/topic.md",),
            ledger_path=ledger,
        )

    assert not (project / "drafts" / "topic.md").exists()


def test_content_assembler_rejects_manifest_and_incomplete_final_set(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    staging = tmp_path / "staging"
    ledger = tmp_path / "ledger.json"
    project.mkdir()
    staged = staging / "final" / "topic.md"
    staged.parent.mkdir(parents=True)
    _ = staged.write_text("final", encoding="utf-8")

    with pytest.raises(ContractError, match="four final files"):
        _ = promote_stage_artifacts(
            stage="content-assembler",
            keyword="topic",
            run_id="RUN-1",
            staging_root=staging,
            project_root=project,
            declared=("final/topic.md",),
            ledger_path=ledger,
        )

    suffixes = (".md", "-naver-layout.md", "-naver-copy.md", "-naver-input.md")
    finals = tuple(f"final/topic{suffix}" for suffix in suffixes)
    for relative in finals[1:]:
        path = staging / relative
        _ = path.write_text("final", encoding="utf-8")
    manifest = staging / "manifests" / "RUN-1-workflow-manifest.json"
    manifest.parent.mkdir()
    _ = manifest.write_text("manifest", encoding="utf-8")

    with pytest.raises(ContractError, match="four final files"):
        _ = promote_stage_artifacts(
            stage="content-assembler",
            keyword="topic",
            run_id="RUN-1",
            staging_root=staging,
            project_root=project,
            declared=(*finals, "manifests/RUN-1-workflow-manifest.json"),
            ledger_path=ledger,
        )
