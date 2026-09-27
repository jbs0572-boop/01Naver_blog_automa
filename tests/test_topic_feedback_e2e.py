from __future__ import annotations

import errno
import fcntl
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from tools.contract_types import ContractError
from tools.topic_feedback_config import create_rollback, load_rollout_config
from tools.topic_feedback_replay import ReplayRequest, replay

FIXTURE = Path(__file__).parent / "fixtures/feedback/e2e-root"


def _tree(root: Path) -> tuple[tuple[str, str], ...]:
    return tuple(
        (path.relative_to(root).as_posix(), hashlib.sha256(path.read_bytes()).hexdigest())
        for path in sorted(root.rglob("*"))
        if path.is_file()
    )


def _content_objects(evidence: Path) -> list[Path]:
    return sorted(
        path
        for path in evidence.iterdir()
        if path.is_file()
        and path.name.startswith(".replay-object-")
        and path.suffix == ".json"
    )


def _assert_intentional_object_without_output(evidence: Path) -> None:
    assert not (evidence / "task-13-e2e.json").exists()
    objects = _content_objects(evidence)
    assert len(objects) == 1
    assert objects[0].is_file()


def test_content_object_is_created_as_one_atomic_regular_file(tmp_path: Path) -> None:
    # Given: an empty, retained evidence directory.
    import tools.topic_feedback_replay_store as replay_store
    from tools.topic_feedback_replay_fs import (
        close_replay_directory,
        open_replay_directory,
    )

    evidence = tmp_path / "evidence"
    evidence.mkdir()
    binding = open_replay_directory(evidence, "evidence directory")

    # When: canonical replay evidence is published.
    try:
        result = replay_store.publish_evidence(binding, b"{}\n")
    finally:
        close_replay_directory(binding)

    # Then: O_EXCL attribution yields one file which is the final output inode.
    objects = sorted(evidence.glob(".replay-object-*.json"))
    assert result.created is True
    assert len(objects) == 1
    assert objects[0].is_file()
    assert objects[0].stat().st_ino == (evidence / "task-13-e2e.json").stat().st_ino


def test_content_object_creation_has_no_mkdir_open_gap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a trap for the obsolete two-step object-directory creation path.
    import tools.topic_feedback_replay_store as replay_store
    from tools.topic_feedback_replay_fs import (
        close_replay_directory,
        open_replay_directory,
    )

    evidence = tmp_path / "evidence"
    evidence.mkdir()
    binding = open_replay_directory(evidence, "evidence directory")
    original_mkdir = os.mkdir

    def reject_object_directory(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> None:
        if isinstance(path, str) and path.startswith(".replay-object-"):
            raise AssertionError("object mkdir/open attribution gap")
        original_mkdir(path, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "mkdir", reject_object_directory)

    # When / Then: a single-file object publication never invokes mkdir.
    try:
        result = replay_store.publish_evidence(binding, b"{}\n")
    finally:
        close_replay_directory(binding)
    assert result.created is True


def test_replay_emits_truthful_frozen_loop_when_five_week_fixture(tmp_path: Path) -> None:
    # Given: an immutable five-week publication-to-Creator fixture copied for replay.
    root = tmp_path / "root"
    _ = shutil.copytree(FIXTURE, root)
    evidence = tmp_path / "evidence"
    evidence.mkdir()

    # When: the real CLI replays the integrated feedback loop.
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.topic_feedback_cli",
            "replay",
            "--root",
            str(root),
            "--as-of",
            "2026-09-07",
            "--evidence-dir",
            str(evidence),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    # Then: the machine result exposes real cohorts and a non-actionable shadow.
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["external_calls"] == 0
    assert payload["external_writes"] == 0
    assert payload["cohorts"] == {"7d": {"mature": 1}, "28d": {"mature": 1}}
    assert payload["ranking"]["challenger_verdict"] == "insufficient_evidence"
    assert payload["ranking"]["selected"] == payload["ranking"]["baseline"]
    assert set(payload["candidate_keywords"]) == set(payload["creator_candidates"])
    assert payload["digest_chain_verified"] is True
    assert payload["live_sources_enabled"] is False
    assert {
        item["source_id"]: item["status"] for item in payload["source_status"]
    }["naver-datalab"] == "blocked_missing_credentials"
    assert payload["signal_provenance"]["accepted_count"] == 0
    assert (evidence / "task-13-e2e.json").read_text(encoding="utf-8") == completed.stdout


def test_replay_fails_before_output_when_snapshot_is_tampered(tmp_path: Path) -> None:
    # Given: a copied fixture whose digest-bound Blog Stats input is modified.
    root = tmp_path / "root"
    _ = shutil.copytree(FIXTURE, root)
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    stats = next((root / "metadata/blog-stats").rglob("*.json"))
    _ = stats.write_bytes(stats.read_bytes().replace(b'"views":30', b'"views":31'))

    # When: replay validates the full chain before publishing evidence.
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.topic_feedback_cli",
            "replay",
            "--root",
            str(root),
            "--as-of",
            "2026-09-07",
            "--evidence-dir",
            str(evidence),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    # Then: the failure is explicit and leaves no partial replay artifact.
    assert completed.returncode == 2
    assert "digest" in completed.stderr
    assert list(evidence.iterdir()) == []


def test_replay_is_deterministic_and_reuses_identical_output(tmp_path: Path) -> None:
    # Given: two independent copies of the same frozen inputs.
    first_root = tmp_path / "first-root"
    second_root = tmp_path / "second-root"
    _ = shutil.copytree(FIXTURE, first_root)
    _ = shutil.copytree(FIXTURE, second_root)
    first_evidence = tmp_path / "first-evidence"
    second_evidence = tmp_path / "second-evidence"
    first_evidence.mkdir()
    second_evidence.mkdir()

    # When: both copies are replayed at the same human-provided date.
    first = replay(ReplayRequest(first_root, "2026-09-07", first_evidence))
    second = replay(ReplayRequest(second_root, "2026-09-07", second_evidence))

    # Then: bytes match and the identical destination is an idempotent byte no-op.
    assert first == second
    repeated = replay(ReplayRequest(first_root, "2026-09-07", first_evidence))
    assert repeated == first
    assert (first_evidence / "task-13-e2e.json").read_bytes() == first
    assert len(_content_objects(first_evidence)) == 1
    assert len(_content_objects(second_evidence)) == 1


def test_replay_leaves_source_root_byte_identical(tmp_path: Path) -> None:
    # Given: a copied source root with no generated weekly outputs.
    root = tmp_path / "root"
    _ = shutil.copytree(FIXTURE, root)
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    before = _tree(root)

    # When: replay exercises weekly generation.
    _ = replay(ReplayRequest(root, "2026-09-07", evidence))

    # Then: generation was isolated from the caller-owned root.
    assert _tree(root) == before
    assert not (root / ".automation").exists()
    assert not (root / "metadata/weekly-feedback").exists()
    assert not (root / "metadata/feedback-manifests").exists()


def test_replay_rejects_divergent_existing_output_without_overwrite(
    tmp_path: Path,
) -> None:
    # Given: a valid source and a conflicting evidence destination.
    root = tmp_path / "root"
    _ = shutil.copytree(FIXTURE, root)
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    output = evidence / "task-13-e2e.json"
    _ = output.write_bytes(b"foreign-evidence\n")

    # When / Then: replay rejects the collision and preserves prior bytes.
    with pytest.raises(ContractError, match="output collision"):
        _ = replay(ReplayRequest(root, "2026-09-07", evidence))
    assert output.read_bytes() == b"foreign-evidence\n"


@pytest.mark.parametrize("entry_type", ["symlink", "fifo"])
def test_replay_rejects_non_regular_source_entry(
    tmp_path: Path, entry_type: str
) -> None:
    # Given: an unsafe object inside the copied metadata subtree.
    root = tmp_path / "root"
    _ = shutil.copytree(FIXTURE, root)
    unsafe = root / "metadata/unsafe-entry"
    if entry_type == "symlink":
        unsafe.symlink_to(root / "config/topic-feedback-rollout.json")
    else:
        os.mkfifo(unsafe)
    evidence = tmp_path / "evidence"
    evidence.mkdir()

    # When / Then: snapshotting fails before evidence or source mutation.
    with pytest.raises(ContractError, match="non-regular"):
        _ = replay(ReplayRequest(root, "2026-09-07", evidence))
    assert list(evidence.iterdir()) == []


def test_replay_uses_open_root_when_path_is_renamed_and_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: validated source bytes and a distinct valid replacement tree.
    import tools.topic_feedback_replay as replay_module
    from tools.topic_feedback_replay_fs import ReplayDirectory

    root = tmp_path / "root"
    moved = tmp_path / "moved"
    replacement = tmp_path / "replacement"
    _ = shutil.copytree(FIXTURE, root)
    _ = shutil.copytree(FIXTURE, replacement)
    creator = next((replacement / "metadata/creator-advisor").rglob("*.json"))
    _ = creator.write_bytes(creator.read_bytes().replace("첫 후보".encode(), "교체 후보".encode()))
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    original_bytes = _tree(root)
    replacement_bytes = _tree(replacement)
    original_directory = replay_module.open_replay_directory

    def swap_after_open(path: Path, label: str) -> ReplayDirectory:
        opened = original_directory(path, label)
        if label == "evidence directory":
            _ = root.rename(moved)
            _ = replacement.rename(root)
        return opened

    monkeypatch.setattr(replay_module, "open_replay_directory", swap_after_open)

    # When: replay continues after the caller path has been replaced.
    payload = json.loads(replay(ReplayRequest(root, "2026-09-07", evidence)))

    # Then: only descriptor-anchored original bytes were read and neither tree changed.
    assert payload["creator_candidates"][0] == "첫 후보"
    assert _tree(moved) == original_bytes
    assert _tree(root) == replacement_bytes


@pytest.mark.parametrize(
    "swap_stage", ["after_open", "pre_publish", "post_link", "before_return"]
)
def test_replay_rejects_replaced_evidence_path_at_every_publish_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, swap_stage: str
) -> None:
    # Given: an empty evidence directory whose requested path can be atomically replaced.
    import tools.topic_feedback_replay as replay_module
    import tools.topic_feedback_replay_store as replay_store
    from tools.topic_feedback_replay_fs import ReplayDirectory

    root = tmp_path / "root"
    _ = shutil.copytree(FIXTURE, root)
    evidence = tmp_path / "evidence"
    replacement = tmp_path / "replacement"
    moved = tmp_path / "moved"
    evidence.mkdir()
    replacement.mkdir()
    source_before = _tree(root)
    descriptor_count = len(os.listdir("/dev/fd"))

    def swap() -> None:
        _ = evidence.rename(moved)
        _ = replacement.rename(evidence)

    if swap_stage == "after_open":
        original_open = replay_module.open_replay_directory

        def open_then_swap(path: Path, label: str) -> ReplayDirectory:
            opened = original_open(path, label)
            if label == "evidence directory":
                swap()
            return opened

        monkeypatch.setattr(replay_module, "open_replay_directory", open_then_swap)
    else:
        if swap_stage == "before_return":
            verifier_owner = replay_module
            original_verify = replay_module.verify_replay_directory
        else:
            verifier_owner = replay_store
            original_verify = replay_store.verify_replay_directory
        evidence_checks = 0

        def verify_with_swap(binding: ReplayDirectory, label: str) -> None:
            nonlocal evidence_checks
            if label == "evidence directory":
                evidence_checks += 1
                expected = {"pre_publish": 1, "post_link": 2, "before_return": 1}
                if evidence_checks == expected[swap_stage]:
                    swap()
            _ = original_verify(binding, label)

        monkeypatch.setattr(verifier_owner, "verify_replay_directory", verify_with_swap)

    # When / Then: no pathname replacement may produce success or leave output behind.
    with pytest.raises(ContractError, match="evidence directory changed"):
        _ = replay(ReplayRequest(root, "2026-09-07", evidence))
    assert _tree(root) == source_before
    assert list(evidence.iterdir()) == []
    moved_output = moved / "task-13-e2e.json"
    if swap_stage in {"post_link", "before_return"}:
        assert json.loads(moved_output.read_text(encoding="utf-8"))[
            "digest_chain_verified"
        ] is True
        objects = _content_objects(moved)
        assert len(objects) == 1
        assert objects[0].read_bytes() == moved_output.read_bytes()
        assert set(moved.iterdir()) == {moved_output, objects[0]}
    else:
        assert list(moved.iterdir()) == []
    assert len(os.listdir("/dev/fd")) == descriptor_count


def test_replay_path_swap_preserves_replacement_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: post-link path replacement contains unrelated evidence bytes.
    import tools.topic_feedback_replay_store as replay_store
    from tools.topic_feedback_replay_fs import ReplayDirectory

    root = tmp_path / "root"
    _ = shutil.copytree(FIXTURE, root)
    evidence = tmp_path / "evidence"
    replacement = tmp_path / "replacement"
    moved = tmp_path / "moved"
    evidence.mkdir()
    replacement.mkdir()
    replacement_output = replacement / "task-13-e2e.json"
    _ = replacement_output.write_bytes(b"replacement-owned\n")
    original_verify = replay_store.verify_replay_directory
    checks = 0

    def swap_after_link(binding: ReplayDirectory, label: str) -> None:
        nonlocal checks
        if label == "evidence directory":
            checks += 1
            if checks == 2:
                _ = evidence.rename(moved)
                _ = replacement.rename(evidence)
        _ = original_verify(binding, label)

    monkeypatch.setattr(replay_store, "verify_replay_directory", swap_after_link)

    # When / Then: failure preserves both complete owned bytes and the replacement.
    with pytest.raises(ContractError, match="evidence directory changed"):
        _ = replay(ReplayRequest(root, "2026-09-07", evidence))
    assert json.loads((moved / "task-13-e2e.json").read_text(encoding="utf-8"))[
        "digest_chain_verified"
    ] is True
    objects = _content_objects(moved)
    assert len(objects) == 1
    assert objects[0].read_bytes() == (moved / "task-13-e2e.json").read_bytes()
    assert set(moved.iterdir()) == {moved / "task-13-e2e.json", objects[0]}
    assert (evidence / "task-13-e2e.json").read_bytes() == b"replacement-owned\n"


def test_replay_rejects_replaced_evidence_ancestor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: an ancestor component is replaced immediately after directory binding.
    import tools.topic_feedback_replay as replay_module
    from tools.topic_feedback_replay_fs import ReplayDirectory

    root = tmp_path / "root"
    _ = shutil.copytree(FIXTURE, root)
    parent = tmp_path / "parent"
    evidence = parent / "evidence"
    replacement_parent = tmp_path / "replacement-parent"
    replacement_evidence = replacement_parent / "evidence"
    moved_parent = tmp_path / "moved-parent"
    evidence.mkdir(parents=True)
    replacement_evidence.mkdir(parents=True)
    original_open = replay_module.open_replay_directory

    def replace_ancestor(path: Path, label: str) -> ReplayDirectory:
        opened = original_open(path, label)
        if label == "evidence directory":
            _ = parent.rename(moved_parent)
            _ = replacement_parent.rename(parent)
        return opened

    monkeypatch.setattr(replay_module, "open_replay_directory", replace_ancestor)

    # When / Then: component identity mismatch fails before either tree gets output.
    with pytest.raises(ContractError, match="evidence directory changed"):
        _ = replay(ReplayRequest(root, "2026-09-07", evidence))
    assert list((moved_parent / "evidence").iterdir()) == []
    assert list(evidence.iterdir()) == []


@pytest.mark.parametrize("link_scope", ["external", "internal"])
def test_replay_rejects_hardlinked_source_file_without_mutation(
    tmp_path: Path, link_scope: str
) -> None:
    # Given: a source snapshot containing a regular file with more than one link.
    root = tmp_path / "root"
    _ = shutil.copytree(FIXTURE, root)
    creator = next((root / "metadata/creator-advisor").rglob("*.json"))
    if link_scope == "external":
        external = tmp_path / "external.json"
        _ = shutil.copyfile(creator, external)
        creator.unlink()
        os.link(external, creator)
    else:
        os.link(creator, creator.with_name("internal-hardlink.json"))
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    source_before = _tree(root)
    descriptor_count = len(os.listdir("/dev/fd"))

    # When / Then: descriptor snapshotting rejects aliases before scratch/output writes.
    with pytest.raises(ContractError, match="hardlink"):
        _ = replay(ReplayRequest(root, "2026-09-07", evidence))
    assert _tree(root) == source_before
    assert list(evidence.iterdir()) == []
    assert len(os.listdir("/dev/fd")) == descriptor_count


def test_replay_rejects_device_source_entry_without_mutation(tmp_path: Path) -> None:
    # Given: a source tree containing a device node instead of a regular entry.
    root = tmp_path / "root"
    _ = shutil.copytree(FIXTURE, root)
    device = root / "metadata/device-node"
    try:
        os.mknod(device, stat.S_IFCHR | 0o600, os.makedev(1, 3))
    except OSError as error:
        unavailable = {errno.EACCES, errno.ENOSYS, errno.ENOTSUP, errno.EPERM}
        if error.errno in unavailable:
            pytest.skip(f"device-node creation unavailable: errno={error.errno}")
        raise
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    source_before = _tree(root)

    # When / Then: snapshotting rejects the device before output or source mutation.
    with pytest.raises(ContractError, match="non-regular"):
        _ = replay(ReplayRequest(root, "2026-09-07", evidence))
    assert _tree(root) == source_before
    assert list(evidence.iterdir()) == []


@pytest.mark.parametrize("write_path", ["scratch", "publish"])
def test_replay_retries_positive_short_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, write_path: str
) -> None:
    # Given: the selected storage boundary writes exactly one positive byte per call.
    import tools.topic_feedback_replay_fs as replay_fs
    import tools.topic_feedback_replay_store as replay_store

    root = tmp_path / "root"
    _ = shutil.copytree(FIXTURE, root)
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    calls = 0

    def short_write(descriptor: int, encoded: memoryview) -> int:
        nonlocal calls
        calls += 1
        return os.write(descriptor, encoded[:1])

    owner = replay_fs if write_path == "scratch" else replay_store
    monkeypatch.setattr(owner, f"_{write_path}_write", short_write)

    # When / Then: the loop completes the exact canonical output without truncation.
    encoded = replay(ReplayRequest(root, "2026-09-07", evidence))
    assert calls > 1
    assert (evidence / "task-13-e2e.json").read_bytes() == encoded


@pytest.mark.parametrize("write_path", ["scratch", "publish"])
def test_replay_retries_interrupted_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, write_path: str
) -> None:
    # Given: the selected write syscall is interrupted once before making progress.
    import tools.topic_feedback_replay_fs as replay_fs
    import tools.topic_feedback_replay_store as replay_store

    root = tmp_path / "root"
    _ = shutil.copytree(FIXTURE, root)
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    calls = 0

    def interrupted_write(descriptor: int, encoded: memoryview) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise InterruptedError
        return os.write(descriptor, encoded)

    owner = replay_fs if write_path == "scratch" else replay_store
    monkeypatch.setattr(owner, f"_{write_path}_write", interrupted_write)

    # When / Then: EINTR is retried and canonical evidence remains complete.
    encoded = replay(ReplayRequest(root, "2026-09-07", evidence))
    assert calls >= 2
    assert (evidence / "task-13-e2e.json").read_bytes() == encoded


@pytest.mark.parametrize("write_path", ["scratch", "publish"])
def test_replay_rejects_zero_progress_write_without_partial_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, write_path: str
) -> None:
    # Given: the selected storage boundary reports a zero-byte write.
    import tools.topic_feedback_replay_fs as replay_fs
    import tools.topic_feedback_replay_store as replay_store

    root = tmp_path / "root"
    _ = shutil.copytree(FIXTURE, root)
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    source_before = _tree(root)
    descriptor_count = len(os.listdir("/dev/fd"))
    def zero_write(_descriptor: int, _encoded: memoryview) -> int:
        return 0

    owner = replay_fs if write_path == "scratch" else replay_store
    monkeypatch.setattr(owner, f"_{write_path}_write", zero_write)

    # When / Then: zero progress fails closed and cleans writer-owned bytes.
    with pytest.raises(ContractError, match="write made no progress"):
        _ = replay(ReplayRequest(root, "2026-09-07", evidence))
    assert _tree(root) == source_before
    if write_path == "publish":
        _assert_intentional_object_without_output(evidence)
    else:
        assert list(evidence.iterdir()) == []
    assert len(os.listdir("/dev/fd")) == descriptor_count


@pytest.mark.parametrize("write_path", ["scratch", "publish"])
@pytest.mark.parametrize("operation", ["sync"])
def test_replay_retries_interrupted_storage_operations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    write_path: str,
    operation: str,
) -> None:
    # Given: the selected fsync/close boundary is interrupted exactly once.
    import tools.topic_feedback_replay_fs as replay_fs
    import tools.topic_feedback_replay_store as replay_store

    root = tmp_path / "root"
    _ = shutil.copytree(FIXTURE, root)
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    calls = 0

    def interrupted(descriptor: int) -> None:
        nonlocal calls
        if (
            write_path == "scratch"
            and operation == "close"
            and fcntl.fcntl(descriptor, fcntl.F_GETFL) & os.O_ACCMODE
            != os.O_WRONLY
        ):
            os.close(descriptor)
            return
        calls += 1
        if calls == 1:
            raise InterruptedError
        (os.fsync if operation == "sync" else os.close)(descriptor)

    owner = replay_fs if write_path == "scratch" else replay_store
    monkeypatch.setattr(owner, f"_{write_path}_{operation}", interrupted)

    # When / Then: EINTR is retried and the real replay completes once.
    encoded = replay(ReplayRequest(root, "2026-09-07", evidence))
    assert calls >= 2
    assert (evidence / "task-13-e2e.json").read_bytes() == encoded


@pytest.mark.parametrize("write_path", ["scratch", "publish"])
def test_replay_never_retries_ambiguous_completed_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, write_path: str
) -> None:
    # Given: close succeeds, its FD number is reused, then the wrapper reports EINTR.
    import tools.topic_feedback_replay_fs as replay_fs
    import tools.topic_feedback_replay_store as replay_store

    root = tmp_path / "root"
    _ = shutil.copytree(FIXTURE, root)
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    readable = root / "config/topic-feedback-rollout.json"
    reused_descriptors: list[int] = []

    def completed_close_then_interrupt(descriptor: int) -> None:
        writable = (
            fcntl.fcntl(descriptor, fcntl.F_GETFL) & os.O_ACCMODE != os.O_RDONLY
        )
        if not reused_descriptors and writable:
            os.close(descriptor)
            reused_descriptors.append(os.open(readable, os.O_RDONLY))
            assert reused_descriptors[0] == descriptor
            raise InterruptedError
        os.close(descriptor)

    owner = replay_fs if write_path == "scratch" else replay_store
    monkeypatch.setattr(owner, f"_{write_path}_close", completed_close_then_interrupt)

    # When / Then: replay fails typed and never closes the unrelated reused FD.
    with pytest.raises(ContractError, match="close failed safely"):
        _ = replay(ReplayRequest(root, "2026-09-07", evidence))
    assert len(reused_descriptors) == 1
    reused_descriptor = reused_descriptors[0]
    assert os.read(reused_descriptor, 1) == b"{"
    os.close(reused_descriptor)
    if write_path == "publish":
        output = evidence / "task-13-e2e.json"
        objects = _content_objects(evidence)
        assert len(objects) == 1
        assert output.read_bytes() == objects[0].read_bytes()
        assert output.stat().st_ino == objects[0].stat().st_ino
    else:
        assert list(evidence.iterdir()) == []


def test_close_eintr_is_not_retried_when_outcome_is_ambiguous(tmp_path: Path) -> None:
    # Given: an injectable close reports EINTR before closing its descriptor.
    from tools.topic_feedback_replay_fs import close_descriptor_once

    target = tmp_path / "readable"
    _ = target.write_bytes(b"ok")
    descriptor = os.open(target, os.O_RDONLY)
    calls = 0

    def interrupted_close(_descriptor: int) -> None:
        nonlocal calls
        calls += 1
        raise InterruptedError

    # When / Then: ownership is abandoned after one call; test cleanup closes it.
    with pytest.raises(ContractError, match="close failed safely"):
        close_descriptor_once(descriptor, interrupted_close, "close failed safely")
    assert calls == 1
    assert os.read(descriptor, 1) == b"o"
    os.close(descriptor)


@pytest.mark.parametrize("write_path", ["scratch", "publish"])
def test_replay_converts_permanent_write_error_without_partial_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, write_path: str
) -> None:
    # Given: the selected write boundary raises a permanent OS error.
    import tools.topic_feedback_replay_fs as replay_fs
    import tools.topic_feedback_replay_store as replay_store

    root = tmp_path / "root"
    _ = shutil.copytree(FIXTURE, root)
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    source_before = _tree(root)

    def fail_write(_descriptor: int, _encoded: memoryview) -> int:
        raise OSError("injected write error")

    owner = replay_fs if write_path == "scratch" else replay_store
    monkeypatch.setattr(owner, f"_{write_path}_write", fail_write)

    # When / Then: the boundary returns a stable typed error and preserves inputs.
    with pytest.raises(ContractError, match="write failed safely"):
        _ = replay(ReplayRequest(root, "2026-09-07", evidence))
    assert _tree(root) == source_before
    if write_path == "publish":
        _assert_intentional_object_without_output(evidence)
    else:
        assert list(evidence.iterdir()) == []


def test_replay_converts_link_error_without_partial_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: final atomic publication raises a permanent OS error.
    import tools.topic_feedback_replay_store as replay_store

    root = tmp_path / "root"
    _ = shutil.copytree(FIXTURE, root)
    evidence = tmp_path / "evidence"
    evidence.mkdir()

    def fail_link(_source_fd: int, _destination_fd: int) -> None:
        raise OSError("injected link error")

    monkeypatch.setattr(replay_store, "_publish_link", fail_link)

    # When / Then: replay fails typed without final or staging artifacts.
    with pytest.raises(ContractError, match="link failed safely"):
        _ = replay(ReplayRequest(root, "2026-09-07", evidence))
    _assert_intentional_object_without_output(evidence)


@pytest.mark.parametrize("write_path", ["scratch", "publish"])
@pytest.mark.parametrize("operation", ["sync", "close"])
def test_replay_cleans_partial_state_when_storage_operation_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    write_path: str,
    operation: str,
) -> None:
    # Given: the selected fsync/close boundary fails after any written bytes.
    import tools.topic_feedback_replay_fs as replay_fs
    import tools.topic_feedback_replay_store as replay_store

    root = tmp_path / "root"
    _ = shutil.copytree(FIXTURE, root)
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    source_before = _tree(root)
    descriptor_count = len(os.listdir("/dev/fd"))

    def fail_operation(descriptor: int) -> None:
        if (
            write_path == "scratch"
            and operation == "close"
            and fcntl.fcntl(descriptor, fcntl.F_GETFL) & os.O_ACCMODE
            != os.O_WRONLY
        ):
            os.close(descriptor)
            return
        if operation == "close":
            os.close(descriptor)
        raise OSError("injected storage failure")

    owner = replay_fs if write_path == "scratch" else replay_store
    monkeypatch.setattr(owner, f"_{write_path}_{operation}", fail_operation)

    # When / Then: the CLI boundary fails and no writer-owned output survives.
    with pytest.raises(ContractError, match="failed safely"):
        _ = replay(ReplayRequest(root, "2026-09-07", evidence))
    assert _tree(root) == source_before
    if write_path == "publish":
        if operation == "close":
            output = evidence / "task-13-e2e.json"
            objects = _content_objects(evidence)
            assert len(objects) == 1
            assert output.read_bytes() == objects[0].read_bytes()
            assert output.stat().st_ino == objects[0].stat().st_ino
        else:
            _assert_intentional_object_without_output(evidence)
    else:
        assert list(evidence.iterdir()) == []
    assert len(os.listdir("/dev/fd")) == descriptor_count


def test_replay_rejects_symlinked_evidence_directory(tmp_path: Path) -> None:
    # Given: a valid copied root and an evidence path redirected outside it.
    root = tmp_path / "root"
    _ = shutil.copytree(FIXTURE, root)
    outside = tmp_path / "outside"
    outside.mkdir()
    evidence = tmp_path / "evidence"
    evidence.symlink_to(outside, target_is_directory=True)

    # When / Then: path validation fails before any outside artifact appears.
    with pytest.raises(ContractError, match="safe directory"):
        _ = replay(ReplayRequest(root, "2026-09-07", evidence))
    assert list(outside.iterdir()) == []


def test_concurrent_replay_commits_one_complete_evidence_file(tmp_path: Path) -> None:
    # Given: one copied root and one empty evidence directory shared by two callers.
    root = tmp_path / "root"
    _ = shutil.copytree(FIXTURE, root)
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    request = ReplayRequest(root, "2026-09-07", evidence)

    # When: both callers race through the real replay boundary.
    def invoke() -> bytes | str:
        try:
            return replay(request)
        except ContractError as error:
            return str(error)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = (pool.submit(invoke), pool.submit(invoke))
        outcomes = [future.result() for future in futures]

    # Then: both callers receive the same canonical bytes without partial output.
    successes = [item for item in outcomes if isinstance(item, bytes)]
    failures = [item for item in outcomes if isinstance(item, str)]
    assert len(successes) == 2, failures
    assert failures == []
    assert successes[0] == successes[1]
    assert (evidence / "task-13-e2e.json").read_bytes() == successes[0]
    assert len(_content_objects(evidence)) == 1


def test_publish_never_removes_content_object_by_mutable_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a bound evidence directory and deletion traps for mutable names.
    import tools.topic_feedback_replay_store as replay_store
    from tools.topic_feedback_replay_fs import (
        close_replay_directory,
        open_replay_directory,
    )

    evidence = tmp_path / "evidence"
    evidence.mkdir()
    binding = open_replay_directory(evidence, "evidence directory")

    def reject_rmdir(_path: str, *, dir_fd: int) -> None:
        del dir_fd
        raise AssertionError("mutable-name rmdir invoked")

    def reject_unlink(_path: str, *, dir_fd: int) -> None:
        del dir_fd
        raise AssertionError("mutable-name unlink invoked")

    monkeypatch.setattr(os, "rmdir", reject_rmdir)
    monkeypatch.setattr(os, "unlink", reject_unlink)

    # When / Then: publication succeeds without any name-based removal syscall.
    try:
        publication = replay_store.publish_evidence(binding, b"{}\n")
        assert publication.created is True
    finally:
        close_replay_directory(binding)
    assert (evidence / "task-13-e2e.json").read_bytes() == b"{}\n"


@pytest.mark.parametrize("foreign_bytes", [b"", b"foreign"])
def test_atomic_object_creation_never_adopts_racing_foreign_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, foreign_bytes: bytes
) -> None:
    # Given: a foreign file wins the object name immediately before O_EXCL creation.
    import tools.topic_feedback_replay_store as replay_store

    root = tmp_path / "root"
    _ = shutil.copytree(FIXTURE, root)
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    seen_names: list[str] = []
    def racing_create(parent_fd: int, object_name: str) -> int | None:
        seen_names.append(object_name)
        descriptor = os.open(
            object_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=parent_fd,
        )
        _ = os.write(descriptor, foreign_bytes)
        os.close(descriptor)
        return None

    monkeypatch.setattr(replay_store, "_create_object", racing_create)

    # When / Then: the foreign inode is rejected unchanged and no output is linked.
    with pytest.raises(ContractError, match="content object collision"):
        _ = replay(ReplayRequest(root, "2026-09-07", evidence))
    assert len(seen_names) == 1
    object_path = evidence / seen_names[0]
    assert object_path.read_bytes() == foreign_bytes
    assert not (evidence / "task-13-e2e.json").exists()


def test_object_name_swap_before_final_verification_preserves_foreign_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a content object file replaced immediately before final verification.
    import tools.topic_feedback_replay_store as replay_store
    from tools.topic_feedback_replay_fs import (
        close_replay_directory,
        open_replay_directory,
    )

    evidence = tmp_path / "evidence"
    evidence.mkdir()
    moved = tmp_path / "moved-object.json"
    binding = open_replay_directory(evidence, "evidence directory")
    original_stat = os.stat
    checks = 0

    def swap_before_final(
        path: str | bytes | int | os.PathLike[str] | os.PathLike[bytes],
        *args: int,
        **kwargs: int | bool,
    ) -> os.stat_result:
        nonlocal checks
        if isinstance(path, str) and path.startswith(".replay-object-"):
            checks += 1
            if checks == 2:
                _ = (evidence / path).rename(moved)
                _ = (evidence / path).write_bytes(b"untouched")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(os, "stat", swap_before_final)

    # When / Then: replay fails typed without deleting either file identity.
    try:
        with pytest.raises(ContractError, match="content object changed"):
            _ = replay_store.publish_evidence(binding, b"{}\n")
    finally:
        close_replay_directory(binding)
    foreign = next(
        path
        for path in evidence.iterdir()
        if path.name.startswith(".replay-object-")
    )
    assert foreign.read_bytes() == b"untouched"
    assert moved.read_bytes() == b"{}\n"


@pytest.mark.parametrize("existing_payload", [b"", b"partial", b'{"complete":true}\n'])
def test_preexisting_incomplete_content_object_is_rejected_unchanged(
    tmp_path: Path, existing_payload: bytes
) -> None:
    # Given: a deterministic object name that existed before this publication.
    import tools.topic_feedback_replay_store as replay_store
    from tools.topic_feedback_replay_fs import (
        close_replay_directory,
        open_replay_directory,
    )

    encoded = b'{"complete":true}\n'
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    object_path = evidence / (
        f".replay-object-{hashlib.sha256(encoded).hexdigest()}.json"
    )
    _ = object_path.write_bytes(existing_payload)
    before = object_path.stat(), object_path.read_bytes()
    binding = open_replay_directory(evidence, "evidence directory")

    # When / Then: pre-existing incomplete bytes poison the digest without mutation.
    try:
        with pytest.raises(ContractError, match="content object collision"):
            _ = replay_store.publish_evidence(binding, encoded)
    finally:
        close_replay_directory(binding)
    after = object_path.stat(), object_path.read_bytes()
    assert (after[0].st_dev, after[0].st_ino, after[1]) == (
        before[0].st_dev,
        before[0].st_ino,
        before[1],
    )
    assert not (evidence / "task-13-e2e.json").exists()


@pytest.mark.parametrize("entry_kind", ["symlink", "hardlink"])
def test_preexisting_linked_content_object_is_rejected_unchanged(
    tmp_path: Path, entry_kind: str
) -> None:
    # Given: the deterministic object name is occupied by an external link.
    import tools.topic_feedback_replay_store as replay_store
    from tools.topic_feedback_replay_fs import (
        close_replay_directory,
        open_replay_directory,
    )

    encoded = b'{"complete":true}\n'
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    external = tmp_path / "external.json"
    _ = external.write_bytes(encoded)
    object_path = evidence / (
        f".replay-object-{hashlib.sha256(encoded).hexdigest()}.json"
    )
    if entry_kind == "symlink":
        object_path.symlink_to(external)
    else:
        os.link(external, object_path)
    external_before = external.stat(), external.read_bytes()
    binding = open_replay_directory(evidence, "evidence directory")

    # When / Then: linked foreign storage is rejected without changing either name.
    try:
        with pytest.raises(ContractError, match="content object collision"):
            _ = replay_store.publish_evidence(binding, encoded)
    finally:
        close_replay_directory(binding)
    external_after = external.stat(), external.read_bytes()
    assert (external_after[0].st_ino, external_after[1]) == (
        external_before[0].st_ino,
        external_before[1],
    )
    assert not (evidence / "task-13-e2e.json").exists()


@pytest.mark.parametrize(
    "attack", ["replace_payload", "mutate_payload", "replace_final"]
)
def test_payload_is_bound_to_verified_inode_and_bytes_through_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, attack: str
) -> None:
    # Given: a same-user mutation injected after the first retained-FD verification.
    import tools.topic_feedback_replay_store as replay_store
    from tools.topic_feedback_replay_fs import (
        close_replay_directory,
        open_replay_directory,
    )

    encoded = b'{"complete":true}\n'
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    binding = open_replay_directory(evidence, "evidence directory")

    def link_payload(parent_fd: int, object_name: str) -> None:
        os.link(
            object_name,
            "task-13-e2e.json",
            src_dir_fd=parent_fd,
            dst_dir_fd=parent_fd,
            follow_symlinks=False,
        )

    def attack_then_link(parent_fd: int, object_name: str) -> None:
        if attack == "replace_payload":
            os.rename(
                object_name,
                "verified-object.json",
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
            )
            replacement = os.open(
                object_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=parent_fd,
            )
            _ = os.write(replacement, b"foreign-payload!!\n")
            os.close(replacement)
            link_payload(parent_fd, object_name)
        elif attack == "mutate_payload":
            mutator = os.open(object_name, os.O_WRONLY, dir_fd=parent_fd)
            _ = os.pwrite(mutator, b"[", 0)
            os.close(mutator)
            link_payload(parent_fd, object_name)
        else:
            replacement = os.open(
                "task-13-e2e.json",
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=parent_fd,
            )
            _ = os.write(replacement, b"foreign-final\n")
            os.close(replacement)

    monkeypatch.setattr(replay_store, "_publish_link", attack_then_link)

    # When / Then: no mutation path can return success or alter foreign bytes.
    try:
        with pytest.raises(ContractError, match="(payload|output).*(changed|collision)"):
            _ = replay_store.publish_evidence(binding, encoded)
    finally:
        close_replay_directory(binding)
    if attack == "replace_payload":
        assert (evidence / "task-13-e2e.json").read_bytes() == b"foreign-payload!!\n"
        assert (evidence / "verified-object.json").read_bytes() == encoded
    elif attack == "mutate_payload":
        assert (evidence / "task-13-e2e.json").read_bytes().startswith(b"[")
    else:
        assert (evidence / "task-13-e2e.json").read_bytes() == b"foreign-final\n"


def test_third_hardlink_after_final_byte_check_prevents_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a foreign hardlink appears after the final retained-FD byte reread.
    import tools.topic_feedback_replay_store as replay_store
    from tools.topic_feedback_replay_fs import (
        close_replay_directory,
        open_replay_directory,
    )
    from tools.topic_feedback_replay_verified import VerifiedFile, reverify_file

    encoded = b'{"complete":true}\n'
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    foreign = tmp_path / "foreign-link.json"
    binding = open_replay_directory(evidence, "evidence directory")
    calls = 0

    def reverify_then_link(
        verified: VerifiedFile, expected: bytes, label: str
    ) -> None:
        nonlocal calls
        reverify_file(verified, expected, label)
        calls += 1
        if calls == 2:
            os.link(evidence / "task-13-e2e.json", foreign)

    monkeypatch.setattr(replay_store, "reverify_file", reverify_then_link)

    # When / Then: the last topology check fails typed and preserves all bytes.
    try:
        with pytest.raises(ContractError, match="content object collision"):
            _ = replay_store.publish_evidence(binding, encoded)
    finally:
        close_replay_directory(binding)
    assert foreign.read_bytes() == encoded
    assert (evidence / "task-13-e2e.json").read_bytes() == encoded


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [(403, "blocked_missing_credentials"), (429, "blocked_rate_limited")],
)
def test_optional_probe_fails_closed_without_artifact(
    tmp_path: Path, status_code: int, expected: str
) -> None:
    # Given: a bounded fake transport and an unchanged source root.
    from tools.topic_feedback_probe import ProbeResponse, probe_optional_source

    class FakeTransport:
        def __init__(self) -> None:
            self.calls: int = 0

        def read(self, source_id: str) -> ProbeResponse:
            self.calls += 1
            return ProbeResponse(source_id, status_code, b"")

    root = tmp_path / "root"
    _ = shutil.copytree(FIXTURE, root)
    before = _tree(root)
    transport = FakeTransport()

    # When: the optional capability receives an authorization or rate-limit failure.
    result = probe_optional_source("naver-datalab", transport)

    # Then: collection and promotion stop without producing signal bytes.
    assert result.status == expected
    assert result.transport_calls == transport.calls == 1
    assert result.promotion_allowed is False
    assert result.signal_payload is None
    assert _tree(root) == before


def test_rollback_fence_restores_baseline_without_touching_history(tmp_path: Path) -> None:
    # Given: a shadow replay whose weekly history has already been recorded.
    root = tmp_path / "root"
    _ = shutil.copytree(FIXTURE, root)
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    before = json.loads(replay(ReplayRequest(root, "2026-09-07", evidence)))
    history = {
        path.relative_to(root): path.read_bytes()
        for base in ("metadata/blog-stats", "metadata/publication-links")
        for path in (root / base).rglob("*.json")
    }
    weekly_history = {
        path.relative_to(root): path.read_bytes()
        for base in ("metadata/weekly-feedback", "metadata/feedback-manifests")
        for path in (root / base).rglob("*")
        if path.is_file()
    }

    # When: the existing durable rollback command creates its append-only fence.
    result = create_rollback(
        root,
        root / "config/topic-feedback-rollout.json",
        "RB-E2E-001",
        "2026-09-07T23:59:59+09:00",
    )
    pin = load_rollout_config(root, root / "config/topic-feedback-rollout.json")
    after_evidence = tmp_path / "after-evidence"
    after_evidence.mkdir()
    after = json.loads(replay(ReplayRequest(root, "2026-09-07", after_evidence)))

    # Then: selection is baseline-only and historical telemetry bytes are unchanged.
    assert before["ranking"]["selected"] == before["ranking"]["baseline"]
    assert result.pin.selection_mutation is False
    assert pin.active_score_version == "topic-baseline-v1"
    assert pin.feedback_enabled is False
    assert pin.new_feedback_artifacts_enabled is False
    assert after["ranking"]["selection_mode"] == "baseline"
    assert after["ranking"]["selected"] == after["ranking"]["baseline"]
    assert all((root / path).read_bytes() == encoded for path, encoded in history.items())
    assert {
        path.relative_to(root): path.read_bytes()
        for base in ("metadata/weekly-feedback", "metadata/feedback-manifests")
        for path in (root / base).rglob("*")
        if path.is_file()
    } == weekly_history
