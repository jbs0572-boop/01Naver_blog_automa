from __future__ import annotations

import json
import multiprocessing
import subprocess
import sys
from pathlib import Path
from typing import Protocol

import pytest

from tests.test_publication_attribution import frozen_run_fixture, publication_request
from tools.contract_types import ContractError, JSONMap, JSONValue
from tools.publication_metrics_link import (
    PublicationAttributionRequest,
    link_publication,
)
from tools.publication_metrics_store import PublicationCommit, PublicationMetricsStore
from tools.topic_feedback_models import (
    compute_digest,
    parse_artifact,
    serialize_artifact,
)


class _ProcessBarrier(Protocol):
    def wait(self) -> int: ...


class _ResultQueue(Protocol):
    def put(
        self,
        obj: tuple[int, str],
        block: bool = True,
        timeout: float | None = None,
    ) -> None: ...

    def get(
        self,
        block: bool = True,
        timeout: float | None = None,
    ) -> tuple[int, str]: ...


class _Signal(Protocol):
    def set(self) -> None: ...

    def wait(self, timeout: float | None = None) -> bool: ...


def _concurrent_link_worker(
    root: str,
    run_id: str,
    gate: _ProcessBarrier,
    results: _ResultQueue,
) -> None:
    _ = gate.wait()
    try:
        linked = link_publication(publication_request(Path(root), run_id, "POST-RACE"))
    except ContractError as error:
        results.put((2, str(error)))
    else:
        results.put((0, str(linked["digest"])))


def _store_worker(
    root: str,
    commit: PublicationCommit,
    opened: _Signal,
    results: _ResultQueue,
    reserved: _Signal | None = None,
    release: _Signal | None = None,
) -> None:
    def hold_after_reservation() -> None:
        if reserved is not None:
            reserved.set()
        if release is not None:
            _ = release.wait(10)

    try:
        with PublicationMetricsStore(
            Path(root),
            after_reservation=hold_after_reservation if reserved is not None else None,
        ) as store:
            opened.set()
            stored = store.commit(commit)
    except ContractError as error:
        results.put((2, str(error)))
    else:
        results.put((0, str(stored.path)))


def test_concurrent_runs_reserve_one_exact_post_identity(tmp_path: Path) -> None:
    _ = frozen_run_fixture(tmp_path, "RUN-first", "first", "111")
    _ = frozen_run_fixture(tmp_path, "RUN-other", "other", "222")
    context = multiprocessing.get_context("spawn")
    gate: _ProcessBarrier = context.Barrier(3)
    results: _ResultQueue = context.Queue()
    workers = [
        context.Process(
            target=_concurrent_link_worker,
            args=(str(tmp_path), run_id, gate, results),
        )
        for run_id in ("RUN-first", "RUN-other")
    ]
    for worker in workers:
        worker.start()
    _ = gate.wait()
    for worker in workers:
        worker.join(timeout=10)

    outcomes = sorted(results.get(timeout=1) for _ in workers)
    assert [code for code, _ in outcomes] == [0, 2]
    assert outcomes[1][1] == "blog post identity collision"
    links = tuple((tmp_path / "metadata" / "publication-links").glob("RUN-*/*.json"))
    assert len(links) == 1
    winner_before = links[0].read_bytes()
    winner_run = links[0].parent.name
    repeated = link_publication(publication_request(tmp_path, winner_run, "POST-RACE"))
    assert links[0].read_bytes() == winner_before
    assert repeated["path"] == str(links[0])
    assert all(worker.exitcode == 0 for worker in workers)


def test_open_store_rejects_parent_replaced_by_symlink_without_outside_write(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    with PublicationMetricsStore(tmp_path) as store:
        base = tmp_path / "metadata" / "publication-links"
        moved = tmp_path / "metadata" / "publication-links-moved"
        _ = base.rename(moved)
        base.symlink_to(outside, target_is_directory=True)
        commit = PublicationCommit(
            run_id="RUN-fixture",
            blog_post_id="POST-001",
            encoded=b"{}",
        )

        with pytest.raises(ContractError, match="store parent changed"):
            _ = store.commit(commit)

    assert tuple(outside.iterdir()) == ()


def test_store_has_no_replaceable_lock_inode(tmp_path: Path) -> None:
    _ = frozen_run_fixture(tmp_path, "RUN-fixture", "fixture", "111")

    _ = link_publication(publication_request(tmp_path, "RUN-fixture"))

    assert not (
        tmp_path / "metadata" / "publication-links" / ".store.lock"
    ).exists()


def test_replacing_decoy_lock_file_cannot_split_directory_lock(tmp_path: Path) -> None:
    source_first = tmp_path / "source-first"
    source_other = tmp_path / "source-other"
    target = tmp_path / "target"
    for root, run_id, keyword in (
        (source_first, "RUN-first", "first"),
        (source_other, "RUN-other", "other"),
    ):
        root.mkdir()
        _ = frozen_run_fixture(root, run_id, keyword, "111")
    target.mkdir()
    first = link_publication(publication_request(source_first, "RUN-first", "POST-LOCK"))
    other = link_publication(publication_request(source_other, "RUN-other", "POST-LOCK"))
    commits = tuple(
        PublicationCommit(
            run_id,
            "POST-LOCK",
            Path(str(linked["path"])).read_bytes(),
        )
        for run_id, linked in (("RUN-first", first), ("RUN-other", other))
    )
    context = multiprocessing.get_context("spawn")
    opened_first: _Signal = context.Event()
    opened_other: _Signal = context.Event()
    reserved: _Signal = context.Event()
    release: _Signal = context.Event()
    results: _ResultQueue = context.Queue()
    first_worker = context.Process(
        target=_store_worker,
        args=(str(target), commits[0], opened_first, results, reserved, release),
    )
    first_worker.start()
    assert opened_first.wait(5) and reserved.wait(5)
    decoy = target / "metadata" / "publication-links" / ".store.lock"
    _ = decoy.write_bytes(b"first inode")
    other_worker = context.Process(
        target=_store_worker,
        args=(str(target), commits[1], opened_other, results),
    )
    other_worker.start()
    assert opened_other.wait(5)
    decoy.unlink()
    _ = decoy.write_bytes(b"replacement inode")
    release.set()
    first_worker.join(timeout=10)
    other_worker.join(timeout=10)

    outcomes = sorted(results.get(timeout=1) for _ in range(2))
    assert [code for code, _ in outcomes] == [0, 2]
    assert outcomes[1][1] == "blog post identity collision"
    assert decoy.read_bytes() == b"replacement inode"
    assert len(tuple((target / "metadata" / "publication-links").glob("RUN-*/*.json"))) == 1
    assert first_worker.exitcode == other_worker.exitcode == 0


@pytest.mark.parametrize("source", ["current-run", "legacy-import"])
def test_reserved_run_id_real_cli_fails_before_store_mutation(
    tmp_path: Path,
    source: str,
) -> None:
    command = [
        sys.executable,
        "-m",
        "tools.topic_feedback_cli",
        "link-publication",
        "--root",
        str(tmp_path),
        "--run-id",
        ".identities",
        "--blog-post-id",
        "POST-CLI",
        "--published-at",
        "2026-09-01T09:00:00+09:00",
        "--source",
        source,
    ]
    if source == "legacy-import":
        command.extend(
            (
                "--topic-id",
                "TOPIC-legacy",
                "--keyword",
                "legacy",
                "--artifact-digest",
                "sha256:" + "a" * 64,
                "--legacy-identity",
                "legacy-row-cli",
            )
        )

    result = subprocess.run(command, check=False, capture_output=True, text=True)

    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == "run_id uses a reserved internal namespace\n"
    assert not (tmp_path / "metadata").exists()


def test_reservation_recovers_exact_payload_after_injected_crash(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _ = frozen_run_fixture(source, "RUN-fixture", "fixture", "111")
    linked = link_publication(publication_request(source, "RUN-fixture"))
    source_path = Path(str(linked["path"]))
    encoded = source_path.read_bytes()
    commit = PublicationCommit(
        run_id="RUN-fixture",
        blog_post_id="POST-001",
        encoded=encoded,
    )
    recovery = tmp_path / "recovery"
    recovery.mkdir()

    def crash() -> None:
        raise RuntimeError("injected crash after reservation")

    with (
        pytest.raises(RuntimeError, match="injected crash"),
        PublicationMetricsStore(recovery, after_reservation=crash) as store,
    ):
        _ = store.commit(commit)
    reservations = tuple(
        (recovery / "metadata" / "publication-links" / ".identities").glob("*.json")
    )
    assert len(reservations) == 1
    assert not (recovery / "metadata" / "publication-links" / "RUN-fixture").exists()

    with PublicationMetricsStore(recovery) as store:
        stored = store.commit(commit)

    assert stored.encoded == encoded
    assert stored.path.read_bytes() == encoded == reservations[0].read_bytes()


def test_same_run_divergent_reservation_replay_rejects(tmp_path: Path) -> None:
    _ = frozen_run_fixture(tmp_path, "RUN-fixture", "fixture", "111")
    first = link_publication(publication_request(tmp_path, "RUN-fixture"))
    legacy = PublicationAttributionRequest(
        root=tmp_path,
        run_id="RUN-fixture",
        blog_post_id="POST-001",
        published_at="2026-09-01T09:00:00+09:00",
        captured_at="2026-09-08T09:00:00+09:00",
        source_identity="legacy-import",
        topic_id="TOPIC-fixture",
        keyword="fixture",
        artifact_digest="sha256:" + "b" * 64,
        score_version="legacy-score-v2",
        legacy_identity="legacy-row-divergent",
    )
    before = Path(str(first["path"])).read_bytes()

    with pytest.raises(ContractError, match="replay diverges"):
        _ = link_publication(legacy)

    assert Path(str(first["path"])).read_bytes() == before


@pytest.mark.parametrize("changed_field", ["artifact_digest", "score_version", "source_identity"])
def test_each_immutable_identity_field_rejects_divergent_replay(
    tmp_path: Path,
    changed_field: str,
) -> None:
    _ = frozen_run_fixture(tmp_path, "RUN-fixture", "fixture", "111")
    first = link_publication(publication_request(tmp_path, "RUN-fixture"))
    path = Path(str(first["path"]))
    raw_value: JSONValue = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(raw_value, dict)
    raw: JSONMap = raw_value
    if changed_field == "artifact_digest":
        raw["artifact_digest"] = "sha256:" + "b" * 64
        raw["input_digests"] = [raw["artifact_digest"]]
    elif changed_field == "score_version":
        raw["score_version"] = "challenger-v2"
    else:
        raw["source_identity"] = "legacy-import"
        raw["legacy_identity"] = "legacy-row-divergent"
        raw["missing_fields"] = []
    raw["digest"] = compute_digest(raw)
    encoded = serialize_artifact(parse_artifact(raw)).encode("utf-8")
    commit = PublicationCommit(
        "RUN-fixture",
        "POST-001",
        encoded,
    )

    with (
        pytest.raises(ContractError, match="replay diverges"),
        PublicationMetricsStore(tmp_path) as store,
    ):
        _ = store.commit(commit)

    assert path.read_bytes() != encoded
