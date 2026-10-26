"""Offline tests for macOS divergent-main recovery; no external Git requests."""
from __future__ import annotations

import json
import subprocess
import tarfile
from pathlib import Path

import pytest

from scripts.local_main_reconcile import ReconcileError, audit, backup, manifest


def command(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def fixture_repository(tmp_path: Path) -> tuple[Path, Path, str, str]:
    repo = tmp_path / "dirty-original-main"
    repo.mkdir()
    command(repo, "init", "-b", "main")
    command(repo, "config", "user.email", "synthetic@example.invalid")
    command(repo, "config", "user.name", "CI fixture")
    (repo / "app.py").write_text("VERSION = 0\n", encoding="utf8")
    command(repo, "add", ".")
    command(repo, "commit", "-m", "base")
    root = command(repo, "rev-parse", "HEAD")
    command(repo, "switch", "-c", "remote-only")
    for index in (1, 2):
        (repo / "app.py").write_text(f"VERSION = {index}\n", encoding="utf8")
        command(repo, "add", ".")
        command(repo, "commit", "-m", f"remote-{index}")
    remote_sha = command(repo, "rev-parse", "HEAD")
    command(repo, "update-ref", "refs/remotes/origin/main", remote_sha)
    command(repo, "switch", "main")
    assert command(repo, "rev-parse", "HEAD") == root
    for index in (1, 2, 3):
        (repo / f"local_{index}.py").write_text(
            f"def local_{index}():\n    return {index}\n", encoding="utf8"
        )
        command(repo, "add", ".")
        command(repo, "commit", "-m", f"local-only-{index}")
    local_sha = command(repo, "rev-parse", "HEAD")
    (repo / "local_1.py").write_text("def local_1():\n    return 99\n")
    (repo / "untracked notes with space.txt").write_text("private notes\n")
    source = tmp_path / "original-temp"
    (source / "refs").mkdir(parents=True)
    (source / "refs" / "backup.txt").write_bytes(b"original AUG 2026 bytes")
    return repo, source, local_sha, remote_sha


def test_private_snapshot_and_entire_local_commit_inventory_are_preserved(tmp_path: Path) -> None:
    repo, source, head, remote = fixture_repository(tmp_path)
    before = command(repo, "status", "--porcelain=v1")
    output = backup(repo, source, tmp_path / "permanent", head[:8])
    assert command(repo, "status", "--porcelain=v1") == before
    assert command(repo, "rev-parse", "HEAD") == head
    assert command(repo, "rev-parse", "refs/remotes/origin/main") == remote
    assert manifest(source) == manifest(output / "old-temp-snapshot")
    assert (output / "all-git-refs.bundle").is_file()
    assert (output / "bundle-verification.txt").is_file()
    assert b"return 99" in (output / "tracked-vs-HEAD.patch").read_bytes()
    with tarfile.open(output / "untracked.tar.gz") as archive:
        assert archive.extractfile(
            "untracked/untracked notes with space.txt"
        ).read() == b"private notes\n"
    data = json.loads((output / "audit.json").read_text())
    assert data["ahead"] == 3
    assert data["behind"] == 2
    assert data["local_main"] == head and data["remote_main"] == remote
    assert len(data["commits"]) == 3
    assert all(item["classification"] == "NEEDS_SEMANTIC_REVIEW"
               for item in data["commits"])
    assert data["semantic_audit_complete"] is False
    assert (output.stat().st_mode & 0o777) == 0o700
    assert (output / "backup-sha256.json").is_file()


def test_wrong_original_head_blocks_without_creating_any_backup(tmp_path: Path) -> None:
    repo, snapshot, head, remote = fixture_repository(tmp_path)
    target = tmp_path / "permanent"
    with pytest.raises(ReconcileError, match="Wrong original checkout"):
        backup(repo, snapshot, target, "f" * 8)
    assert not target.exists()


def test_missing_old_temp_snapshot_does_not_claim_full_backup(tmp_path: Path) -> None:
    repo, snapshot, head, remote = fixture_repository(tmp_path)
    with pytest.raises(FileNotFoundError):
        backup(repo, tmp_path / "missing", tmp_path / "permanent", head[:8])
    assert not (tmp_path / "permanent").exists()


def test_path_overlap_rejected_and_audit_never_guesses_semantics(tmp_path: Path) -> None:
    repo, snapshot, head, remote = fixture_repository(tmp_path)
    with pytest.raises(ReconcileError, match="cannot overlap"):
        backup(repo, snapshot, repo / "archives", head[:8])
    report = audit(repo)
    assert (report["ahead"], report["behind"]) == (3, 2)
    assert not report["semantic_audit_complete"]
    assert all(x["patch_equivalence"] == "different_patch"
               for x in report["commits"])
