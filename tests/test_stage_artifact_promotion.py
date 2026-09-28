from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from tools.contract_types import ContractError, JSONMap
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


@pytest.mark.parametrize(
    "declared_path_source", ("project_destination", "staging_source")
)
def test_rejects_absolute_declared_artifact_path(
    tmp_path: Path, declared_path_source: str
) -> None:
    project = tmp_path / "project"
    staging = tmp_path / "staging"
    ledger = tmp_path / "ledger.json"
    source = staging / "drafts" / "topic.md"
    destination = project / "drafts" / "topic.md"
    source.parent.mkdir(parents=True)
    project.mkdir()
    staged_bytes = b"staged draft"
    _ = source.write_bytes(staged_bytes)
    declared_path = {
        "project_destination": str(destination),
        "staging_source": str(source),
    }[declared_path_source]

    with pytest.raises(ContractError) as error:
        _ = promote_stage_artifacts(
            stage="writer",
            keyword="topic",
            run_id="RUN-1",
            staging_root=staging,
            project_root=project,
            declared=(declared_path,),
            ledger_path=ledger,
        )

    assert str(error.value) == f"stage artifact path is unsafe: {declared_path}"
    assert not destination.exists()
    assert not ledger.exists()
    assert source.read_bytes() == staged_bytes


def test_topic_selector_promotes_creator_advisor_snapshot(tmp_path: Path) -> None:
    project = tmp_path / "project"
    staging = tmp_path / "staging"
    ledger = tmp_path / "ledger.json"
    selection = staging / "research" / "topic-selection-두리랜드.md"
    snapshot = (
        staging
        / "metadata"
        / "creator-advisor"
        / "2026-09-04"
        / "capture.json"
    )
    selection.parent.mkdir(parents=True)
    snapshot.parent.mkdir(parents=True)
    project.mkdir()
    _ = selection.write_text("selection", encoding="utf-8")
    _ = snapshot.write_text("{}", encoding="utf-8")

    promoted = promote_stage_artifacts(
        stage="topic-selector",
        keyword=None,
        run_id="RUN-1",
        staging_root=staging,
        project_root=project,
        declared=(
            "research/topic-selection-두리랜드.md",
            "metadata/creator-advisor/2026-09-04/capture.json",
        ),
        ledger_path=ledger,
    )

    assert promoted == (
        "research/topic-selection-두리랜드.md",
        "metadata/creator-advisor/2026-09-04/capture.json",
    )


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


def test_writer_refresh_rolls_back_archive_with_failed_promotion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    staging = tmp_path / "staging"
    ledger = tmp_path / "ledger.json"
    source = staging / "drafts" / "topic.md"
    destination = project / "drafts" / "topic.md"
    source.parent.mkdir(parents=True)
    destination.parent.mkdir(parents=True)
    _ = source.write_text("fresh", encoding="utf-8")
    _ = destination.write_text("previous", encoding="utf-8")

    def fail_ledger(*_args: object, **_kwargs: object) -> None:
        raise OSError("ledger unavailable")

    monkeypatch.setattr("tools.stage_artifact_promotion._write_ledger", fail_ledger)

    with pytest.raises(OSError, match="ledger unavailable"):
        _ = promote_stage_artifacts(
            stage="writer",
            keyword="topic",
            run_id="RUN-1",
            staging_root=staging,
            project_root=project,
            declared=("drafts/topic.md",),
            ledger_path=ledger,
            replace_existing=True,
        )

    assert destination.read_text(encoding="utf-8") == "previous"
    assert not (
        project
        / ".automation"
        / "archive"
        / "stage-artifacts"
        / "writer"
        / "topic"
        / "RUN-1"
        / "topic.md.previous"
    ).exists()
    assert not ledger.exists()


def test_content_assembler_refreshes_and_archives_all_four_canonical_files(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    staging = tmp_path / "staging"
    ledger = tmp_path / "ledger.json"
    suffixes = (".md", "-naver-layout.md", "-naver-copy.md", "-naver-input.md")
    declared = tuple(f"final/topic{suffix}" for suffix in suffixes)
    for relative in declared:
        source = staging / relative
        destination = project / relative
        source.parent.mkdir(parents=True, exist_ok=True)
        destination.parent.mkdir(parents=True, exist_ok=True)
        _ = source.write_text("fresh " + relative, encoding="utf-8")
        _ = destination.write_text("previous " + relative, encoding="utf-8")

    promoted = promote_stage_artifacts(
        stage="content-assembler",
        keyword="topic",
        run_id="RUN-1",
        staging_root=staging,
        project_root=project,
        declared=declared,
        ledger_path=ledger,
        replace_existing=True,
    )

    assert promoted == declared
    for relative in declared:
        basename = Path(relative).name
        assert (project / relative).read_text(encoding="utf-8") == "fresh " + relative
        assert (
            project
            / ".automation"
            / "archive"
            / "stage-artifacts"
            / "content-assembler"
            / "topic"
            / "RUN-1"
            / f"{basename}.previous"
        ).read_text(encoding="utf-8") == "previous " + relative


def test_content_assembler_retry_reuses_same_run_original_archive(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    staging = tmp_path / "staging"
    ledger = tmp_path / "ledger.json"
    declared = tuple(
        f"final/topic{suffix}"
        for suffix in (".md", "-naver-layout.md", "-naver-copy.md", "-naver-input.md")
    )
    for value in declared:
        source = staging / value
        destination = project / value
        source.parent.mkdir(parents=True, exist_ok=True)
        destination.parent.mkdir(parents=True, exist_ok=True)
        _ = source.write_text("first attempt " + value, encoding="utf-8")
        _ = destination.write_text("original " + value, encoding="utf-8")
    archive = (
        project
        / ".automation"
        / "archive"
        / "stage-artifacts"
        / "content-assembler"
        / "topic"
        / "RUN-1"
        / "topic.md.previous"
    )

    _ = promote_stage_artifacts(
        stage="content-assembler",
        keyword="topic",
        run_id="RUN-1",
        staging_root=staging,
        project_root=project,
        declared=declared,
        ledger_path=ledger,
        replace_existing=True,
    )
    for value in declared:
        _ = (staging / value).write_text("repaired attempt " + value, encoding="utf-8")
    _ = promote_stage_artifacts(
        stage="content-assembler",
        keyword="topic",
        run_id="RUN-1",
        staging_root=staging,
        project_root=project,
        declared=declared,
        ledger_path=ledger,
        replace_existing=True,
    )

    for value in declared:
        assert (project / value).read_text(encoding="utf-8") == "repaired attempt " + value
        previous = archive.with_name(Path(value).name + ".previous")
        assert previous.read_text(encoding="utf-8") == "original " + value


def test_content_assembler_refresh_removes_archives_and_restores_finals_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    staging = tmp_path / "staging"
    ledger = tmp_path / "ledger.json"
    suffixes = (".md", "-naver-layout.md", "-naver-copy.md", "-naver-input.md")
    declared = tuple(f"final/topic{suffix}" for suffix in suffixes)
    for relative in declared:
        source = staging / relative
        destination = project / relative
        source.parent.mkdir(parents=True, exist_ok=True)
        destination.parent.mkdir(parents=True, exist_ok=True)
        _ = source.write_text("fresh " + relative, encoding="utf-8")
        _ = destination.write_text("previous " + relative, encoding="utf-8")

    def fail_ledger(*_args: object, **_kwargs: object) -> None:
        raise OSError("ledger unavailable")

    monkeypatch.setattr("tools.stage_artifact_promotion._write_ledger", fail_ledger)

    with pytest.raises(OSError, match="ledger unavailable"):
        _ = promote_stage_artifacts(
            stage="content-assembler",
            keyword="topic",
            run_id="RUN-1",
            staging_root=staging,
            project_root=project,
            declared=declared,
            ledger_path=ledger,
            replace_existing=True,
        )

    for relative in declared:
        assert (project / relative).read_text(encoding="utf-8") == "previous " + relative
        assert not (
            project
            / ".automation"
            / "archive"
            / "stage-artifacts"
            / "content-assembler"
            / "topic"
            / "RUN-1"
            / f"{Path(relative).name}.previous"
        ).exists()
    assert not ledger.exists()


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


def test_image_stage_archives_prior_run_assets_before_replacing_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def accept_contract(_asset_dir: Path, _draft_path: Path) -> JSONMap:
        return {}

    monkeypatch.setattr(
        "tools.stage_artifact_promotion.validate_image_stage_assets", accept_contract
    )
    project = tmp_path / "project"
    staging = tmp_path / "staging"
    ledger = project / ".automation/work/RUN-2/image-maker/artifact-ownership.json"
    asset_dir = project / "assets/topic"
    asset_dir.mkdir(parents=True)
    old_files = {
        "image-map.md": b"old map",
        "thumbnail.png": b"old thumbnail",
        "image-01.png": b"old image",
        "historical-note.txt": b"keep this too",
    }
    for name, value in old_files.items():
        _ = (asset_dir / name).write_bytes(value)
    staged_files = {
        "image-map.md": b"new map",
        "thumbnail.png": b"new thumbnail",
        "image-01.png": b"new image",
    }
    for name, value in staged_files.items():
        destination = staging / "assets/topic" / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        _ = destination.write_bytes(value)

    _ = promote_stage_artifacts(
        stage="image-maker",
        keyword="topic",
        run_id="RUN-2",
        staging_root=staging,
        project_root=project,
        declared=tuple(f"assets/topic/{name}" for name in staged_files),
        ledger_path=ledger,
    )

    archives = list(
        (project / ".automation/archive/image-assets/topic/RUN-2").iterdir()
    )
    assert len(archives) == 1
    assert {p.name: p.read_bytes() for p in archives[0].iterdir()} == old_files
    assert {
        name: (asset_dir / name).read_bytes() for name in staged_files
    } == staged_files
    assert not (asset_dir / "historical-note.txt").exists()


def test_image_stage_promotion_failure_restores_prior_assets_and_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def accept_contract(_asset_dir: Path, _draft_path: Path) -> JSONMap:
        return {}

    monkeypatch.setattr(
        "tools.stage_artifact_promotion.validate_image_stage_assets", accept_contract
    )
    project = tmp_path / "project"
    staging = tmp_path / "staging"
    ledger = project / ".automation/work/RUN-2/image-maker/artifact-ownership.json"
    asset_dir = project / "assets/topic"
    asset_dir.mkdir(parents=True)
    old_files = {"image-map.md": b"old map", "thumbnail.png": b"old thumbnail"}
    for name, value in old_files.items():
        _ = (asset_dir / name).write_bytes(value)
    staged_files = {"image-map.md": b"new map", "thumbnail.png": b"new thumbnail"}
    for name, value in staged_files.items():
        destination = staging / "assets/topic" / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        _ = destination.write_bytes(value)
    old_ledger = json.dumps(
        {"run_id": "RUN-2", "artifacts": {"assets/topic/old.png": "a" * 64}}
    ).encode()
    ledger.parent.mkdir(parents=True)
    _ = ledger.write_bytes(old_ledger)
    calls = 0

    def fail_on_ledger(
        path: Path, run_id: str, hashes: dict[str, str]
    ) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("disk full")
        _ = path.write_text(
            json.dumps({"run_id": run_id, "artifacts": hashes}), encoding="utf-8"
        )

    monkeypatch.setattr(
        "tools.stage_artifact_promotion._write_ledger", fail_on_ledger
    )

    with pytest.raises(OSError, match="disk full"):
        _ = promote_stage_artifacts(
            stage="image-maker",
            keyword="topic",
            run_id="RUN-2",
            staging_root=staging,
            project_root=project,
            declared=tuple(f"assets/topic/{name}" for name in staged_files),
            ledger_path=ledger,
        )

    assert {p.name: p.read_bytes() for p in asset_dir.iterdir()} == old_files
    assert ledger.read_bytes() == old_ledger


def test_image_stage_restores_archived_assets_when_quarantine_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def accept_contract(_asset_dir: Path, _draft_path: Path) -> JSONMap:
        return {}

    monkeypatch.setattr(
        "tools.stage_artifact_promotion.validate_image_stage_assets", accept_contract
    )
    project = tmp_path / "project"
    staging = tmp_path / "staging"
    ledger = project / ".automation/work/RUN-2/image-maker/artifact-ownership.json"
    asset_dir = project / "assets/topic"
    asset_dir.mkdir(parents=True)
    old_files = {"thumbnail.png": b"old thumbnail", "body.png": b"old body"}
    for name, value in old_files.items():
        _ = (asset_dir / name).write_bytes(value)
    ledger.parent.mkdir(parents=True)
    old_ledger = b'{"run_id":"RUN-2","artifacts":{}}'
    _ = ledger.write_bytes(old_ledger)
    staged_files = {
        "image-map.md": b"[THUMBNAIL] thumbnail.png\nbody.png",
        "thumbnail.png": b"new thumbnail",
        "body.png": b"new body",
    }
    for name, value in staged_files.items():
        destination = staging / "assets/topic" / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        _ = destination.write_bytes(value)
    real_replace = os.replace
    failed_quarantine = False
    failed_archive_restore = False

    def replace(source: str | Path, destination: str | Path) -> None:
        nonlocal failed_archive_restore, failed_quarantine
        if (
            Path(source) == asset_dir
            and Path(destination).name == "failed"
            and not failed_quarantine
        ):
            failed_quarantine = True
            raise OSError("quarantine failed")
        if (
            Path(source).name == "previous"
            and Path(destination) == asset_dir
            and not failed_archive_restore
        ):
            failed_archive_restore = True
            raise OSError("directory restore failed transiently")
        _ = real_replace(source, destination)

    def fail_ledger(path: Path, *_args: object, **_kwargs: object) -> None:
        _ = path.write_bytes(b"corrupt ledger")
        raise OSError("ledger write failed")

    monkeypatch.setattr("tools.stage_artifact_promotion.os.replace", replace)
    monkeypatch.setattr("tools.stage_artifact_promotion._write_ledger", fail_ledger)

    with pytest.raises(OSError, match="ledger write failed"):
        _ = promote_stage_artifacts(
            stage="image-maker",
            keyword="topic",
            run_id="RUN-2",
            staging_root=staging,
            project_root=project,
            declared=tuple(f"assets/topic/{name}" for name in staged_files),
            ledger_path=ledger,
        )

    assert failed_quarantine
    assert failed_archive_restore
    assert {path.name: path.read_bytes() for path in asset_dir.iterdir()} == old_files
    assert ledger.read_bytes() == old_ledger


def test_image_rollback_ignores_partial_rmtree_error_after_exact_directory_restore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def accept_contract(_asset_dir: Path, _draft_path: Path) -> JSONMap:
        return {}

    monkeypatch.setattr(
        "tools.stage_artifact_promotion.validate_image_stage_assets", accept_contract
    )
    project = tmp_path / "project"
    staging = tmp_path / "staging"
    ledger = project / ".automation/work/RUN-2/image-maker/artifact-ownership.json"
    asset_dir = project / "assets/topic"
    asset_dir.mkdir(parents=True)
    old_files = {"thumbnail.png": b"old thumbnail", "body.png": b"old body"}
    for name, value in old_files.items():
        _ = (asset_dir / name).write_bytes(value)
    ledger.parent.mkdir(parents=True)
    old_ledger = b'{"run_id":"RUN-2","artifacts":{}}'
    _ = ledger.write_bytes(old_ledger)
    staged_files = {
        "image-map.md": b"map",
        "thumbnail.png": b"new thumbnail",
        "body.png": b"new body",
    }
    for name, value in staged_files.items():
        destination = staging / "assets/topic" / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        _ = destination.write_bytes(value)
    real_rmtree = shutil.rmtree
    real_replace = os.replace

    def partial_rmtree(path: str | Path) -> None:
        if Path(path) == asset_dir:
            for child in asset_dir.iterdir():
                if child.is_file():
                    child.unlink()
            raise OSError("cleanup failed after deleting contents")
        _ = real_rmtree(path)

    def fail_quarantine(source: str | Path, destination: str | Path) -> None:
        if Path(source) == asset_dir and Path(destination).name == "failed":
            raise OSError("quarantine failed")
        _ = real_replace(source, destination)

    def fail_ledger(path: Path, *_args: object, **_kwargs: object) -> None:
        _ = path.write_bytes(b"corrupt ledger")
        raise OSError("ledger write failed")

    monkeypatch.setattr("tools.stage_artifact_promotion.shutil.rmtree", partial_rmtree)
    monkeypatch.setattr("tools.stage_artifact_promotion.os.replace", fail_quarantine)
    monkeypatch.setattr("tools.stage_artifact_promotion._write_ledger", fail_ledger)

    with pytest.raises(OSError, match="ledger write failed"):
        _ = promote_stage_artifacts(
            stage="image-maker",
            keyword="topic",
            run_id="RUN-2",
            staging_root=staging,
            project_root=project,
            declared=tuple(f"assets/topic/{name}" for name in staged_files),
            ledger_path=ledger,
        )

    assert {path.name: path.read_bytes() for path in asset_dir.iterdir()} == old_files
    assert ledger.read_bytes() == old_ledger


def test_image_stage_rejects_partial_artifacts_before_archiving_prior_assets(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    staging = tmp_path / "staging"
    ledger = project / ".automation/work/RUN-2/image-maker/artifact-ownership.json"
    asset_dir = project / "assets/topic"
    asset_dir.mkdir(parents=True)
    _ = (asset_dir / "thumbnail.png").write_bytes(b"old thumbnail")
    draft = project / "drafts/topic.md"
    draft.parent.mkdir(parents=True)
    _ = draft.write_text("[IMAGE: body image]", encoding="utf-8")
    staged_files = {
        "image-map.md": b"[THUMBNAIL] thumbnail.png\nbody.png",
        "thumbnail.png": b"new thumbnail",
        "body.png": b"new body",
    }
    for name, value in staged_files.items():
        destination = staging / "assets/topic" / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        _ = destination.write_bytes(value)

    with pytest.raises(ContractError, match="missing required records"):
        _ = promote_stage_artifacts(
            stage="image-maker",
            keyword="topic",
            run_id="RUN-2",
            staging_root=staging,
            project_root=project,
            declared=tuple(f"assets/topic/{name}" for name in staged_files),
            ledger_path=ledger,
        )

    assert (asset_dir / "thumbnail.png").read_bytes() == b"old thumbnail"
    assert not (project / ".automation/archive/image-assets/topic/RUN-2").exists()


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


def test_promotion_attempts_ledger_restore_after_file_restore_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    staging = tmp_path / "staging"
    ledger = project / ".automation/work/RUN-1/artifact-ownership.json"
    source = staging / "drafts" / "topic.md"
    source.parent.mkdir(parents=True)
    _ = source.write_text("draft", encoding="utf-8")
    ledger_restore_attempts: list[bytes | None] = []

    def fail_ledger(*_args: object, **_kwargs: object) -> None:
        raise OSError("disk full")

    def fail_file_restore(*_args: object, **_kwargs: object) -> None:
        raise OSError("read-only filesystem")

    def restore_ledger(path: Path, previous: bytes | None) -> None:
        ledger_restore_attempts.append(previous)
        path.unlink(missing_ok=True)

    monkeypatch.setattr("tools.stage_artifact_promotion._write_ledger", fail_ledger)
    monkeypatch.setattr("tools.stage_artifact_promotion._restore", fail_file_restore)
    monkeypatch.setattr(
        "tools.stage_artifact_promotion._restore_ledger", restore_ledger
    )

    with pytest.raises(ContractError, match="rollback is incomplete"):
        _ = promote_stage_artifacts(
            stage="writer",
            keyword="topic",
            run_id="RUN-1",
            staging_root=staging,
            project_root=project,
            declared=("drafts/topic.md",),
            ledger_path=ledger,
        )

    assert ledger_restore_attempts == [None]


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


def test_researcher_can_promote_only_its_run_scoped_revision(tmp_path: Path) -> None:
    project = tmp_path / "project"
    staging = tmp_path / "staging"
    ledger = tmp_path / "ledger.json"
    revision = "research/revisions/RUN-123/topic.md"
    source = staging / revision
    source.parent.mkdir(parents=True)
    project.mkdir()
    _ = source.write_text("fresh research", encoding="utf-8")

    promoted = promote_stage_artifacts(
        stage="researcher",
        keyword="topic",
        run_id="RUN-123",
        staging_root=staging,
        project_root=project,
        declared=(revision,),
        ledger_path=ledger,
    )

    assert promoted == (revision,)
    assert (project / revision).read_text(encoding="utf-8") == "fresh research"
    with pytest.raises(ContractError, match="outside allowed output"):
        _ = promote_stage_artifacts(
            stage="researcher",
            keyword="topic",
            run_id="RUN-456",
            staging_root=staging,
            project_root=project,
            declared=(revision,),
            ledger_path=tmp_path / "other-ledger.json",
        )
