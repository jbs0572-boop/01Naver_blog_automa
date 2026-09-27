from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from tests.test_publication_attribution import frozen_run_fixture, publication_request
from tools.contract_types import ContractError
from tools.publication_metrics_link import link_publication
from tools.publication_metrics_store import PublicationCommit, PublicationMetricsStore


def _commit_fixture(root: Path, post_id: str | None = "POST-001") -> PublicationCommit:
    source = root / "source"
    source.mkdir()
    _ = frozen_run_fixture(source, "RUN-fixture", "fixture", "111")
    linked = link_publication(publication_request(source, "RUN-fixture", post_id))
    return PublicationCommit(
        "RUN-fixture",
        post_id,
        Path(str(linked["path"])).read_bytes(),
    )


@pytest.mark.parametrize("fault_stage", ["write", "flush", "fsync"])
def test_staged_reservation_fault_never_exposes_partial_final_and_retry_recovers(
    tmp_path: Path,
    fault_stage: str,
) -> None:
    commit = _commit_fixture(tmp_path)
    target = tmp_path / "target"
    target.mkdir()

    def fail(stage: str) -> None:
        if stage == fault_stage:
            raise RuntimeError(f"injected {stage} failure")

    with (
        pytest.raises(RuntimeError, match=f"injected {fault_stage} failure"),
        PublicationMetricsStore(target, staged_fault=fail) as store,
    ):
        _ = store.commit(commit)

    base = target / "metadata" / "publication-links"
    identities = base / ".identities"
    assert tuple(identities.iterdir()) == ()
    assert not (base / "RUN-fixture").exists()

    with PublicationMetricsStore(target) as store:
        recovered = store.commit(commit)

    reservation = next(identities.glob("*.json"))
    assert recovered.path.read_bytes() == reservation.read_bytes() == commit.encoded
    assert not tuple(base.rglob(".tmp-*"))


@pytest.mark.parametrize("fault_stage", ["write", "flush", "fsync"])
def test_staged_unlinked_run_fault_never_exposes_partial_final_and_retry_recovers(
    tmp_path: Path,
    fault_stage: str,
) -> None:
    commit = _commit_fixture(tmp_path, None)
    target = tmp_path / "target"
    target.mkdir()

    def fail(stage: str) -> None:
        if stage == fault_stage:
            raise RuntimeError(f"injected {stage} failure")

    with (
        pytest.raises(RuntimeError, match=f"injected {fault_stage} failure"),
        PublicationMetricsStore(target, staged_fault=fail) as store,
    ):
        _ = store.commit(commit)

    run_dir = target / "metadata/publication-links/RUN-fixture"
    assert tuple(run_dir.iterdir()) == ()
    with PublicationMetricsStore(target) as store:
        recovered = store.commit(commit)
    assert recovered.path.read_bytes() == commit.encoded
    assert not tuple(run_dir.glob(".tmp-*"))


def test_linked_run_file_is_same_complete_reservation_inode(tmp_path: Path) -> None:
    _ = frozen_run_fixture(tmp_path, "RUN-fixture", "fixture", "111")

    linked = link_publication(publication_request(tmp_path, "RUN-fixture"))

    run_path = Path(str(linked["path"]))
    reservation = next(
        (tmp_path / "metadata/publication-links/.identities").glob("*.json")
    )
    run_stat = os.stat(run_path, follow_symlinks=False)
    reservation_stat = os.stat(reservation, follow_symlinks=False)
    assert (run_stat.st_dev, run_stat.st_ino) == (
        reservation_stat.st_dev,
        reservation_stat.st_ino,
    )
    assert run_path.read_bytes() == reservation.read_bytes()


def test_existing_authoritative_file_is_never_overwritten(tmp_path: Path) -> None:
    commit = _commit_fixture(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    with PublicationMetricsStore(target):
        pass
    identities = target / "metadata/publication-links/.identities"
    reservation = identities / (
        hashlib.sha256(b"POST-001").hexdigest() + ".json"
    )
    sentinel = b"existing-authoritative-bytes"
    _ = reservation.write_bytes(sentinel)

    with (
        pytest.raises(ContractError, match="invalid"),
        PublicationMetricsStore(target) as store,
    ):
        _ = store.commit(commit)

    assert reservation.read_bytes() == sentinel
    assert not tuple(identities.glob(".tmp-*"))
    assert not (target / "metadata/publication-links/RUN-fixture").exists()


def test_hard_link_unavailable_fails_closed_without_final(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commit = _commit_fixture(tmp_path)
    target = tmp_path / "target"
    target.mkdir()

    def unavailable(
        source: str,
        target_name: str,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> None:
        del source, target_name, src_dir_fd, dst_dir_fd, follow_symlinks
        raise OSError("hard links unavailable")

    monkeypatch.setattr(os, "link", unavailable)
    with (
        pytest.raises(ContractError, match="requires safe hard links"),
        PublicationMetricsStore(target) as store,
    ):
        _ = store.commit(commit)

    base = target / "metadata/publication-links"
    assert tuple((base / ".identities").iterdir()) == ()
    assert not (base / "RUN-fixture").exists()
