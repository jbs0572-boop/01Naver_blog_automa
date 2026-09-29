from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from tests.weekly_feedback_fixtures import install_weekly_inputs
from tools.contract_types import ContractError
from tools.feedback_manifest import (
    BundleOutput,
    EvidenceManifestRequest,
    build_evidence_manifest,
    publish_bundle,
)
from tools.topic_feedback_manifest_reader import load_feedback_path


def test_manifest_round_trip_binds_every_input(tmp_path: Path) -> None:
    # Given
    paths = install_weekly_inputs(tmp_path)
    request = EvidenceManifestRequest(
        tmp_path,
        "FEEDBACK-001",
        "2026-09-08T09:00:00+09:00",
        "2026-09-08T09:00:00+09:00",
        paths[:-1],
    )

    # When
    manifest = build_evidence_manifest(request)
    manifest_path = tmp_path / "manifest.json"
    _ = manifest_path.write_bytes(manifest.encoded)
    loaded = load_feedback_path(tmp_path, manifest_path, request.data_as_of, ())

    # Then
    assert loaded.digest == manifest.digest
    assert len(manifest.files) == 3


@pytest.mark.parametrize("unsafe", ["symlink", "escape"])
def test_manifest_rejects_unsafe_input_paths(tmp_path: Path, unsafe: str) -> None:
    # Given
    outside = tmp_path.parent / f"{tmp_path.name}-outside.json"
    _ = outside.write_text("{}", encoding="utf-8")
    candidate = outside
    if unsafe == "symlink":
        candidate = tmp_path / "linked.json"
        os.symlink(outside, candidate)
    request = EvidenceManifestRequest(
        tmp_path,
        "FEEDBACK-001",
        "2026-09-08T09:00:00+09:00",
        "2026-09-08T09:00:00+09:00",
        (candidate,),
    )

    # When / Then
    with pytest.raises(ContractError, match="unsafe"):
        _ = build_evidence_manifest(request)


def test_manifest_detects_input_tamper_before_build(tmp_path: Path) -> None:
    # Given
    paths = install_weekly_inputs(tmp_path)
    payload = json.loads(paths[0].read_text(encoding="utf-8"))
    payload["keyword"] = "digest를 갱신하지 않은 변조"
    _ = paths[0].write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n",
        encoding="utf-8",
    )

    # When / Then
    with pytest.raises(ContractError, match="feedback input digest mismatch"):
        _ = build_evidence_manifest(
            EvidenceManifestRequest(
                tmp_path,
                "FEEDBACK-001",
                "2026-09-08T09:00:00+09:00",
                "2026-09-08T09:00:00+09:00",
                paths[:-1],
            )
        )


@pytest.mark.parametrize("symlink_component", ["first_parent", "second_parent", "leaf"])
def test_publish_bundle_rejects_symlink_at_every_component(
    tmp_path: Path,
    symlink_component: str,
) -> None:
    # Given
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    first = root / "first"
    if symlink_component == "first_parent":
        first.symlink_to(outside, target_is_directory=True)
    else:
        first.mkdir()
        second = first / "second"
        if symlink_component == "second_parent":
            second.symlink_to(outside, target_is_directory=True)
        else:
            second.mkdir()
            _ = (outside / "result.json").write_bytes(b"outside")
            (second / "result.json").symlink_to(outside / "result.json")

    # When / Then
    with pytest.raises(ContractError):
        publish_bundle(
            root,
            (BundleOutput(Path("first/second/result.json"), b"inside"),),
        )
    assert sorted(path.name for path in outside.iterdir()) == (
        ["result.json"] if symlink_component == "leaf" else []
    )
    if symlink_component == "leaf":
        assert (outside / "result.json").read_bytes() == b"outside"


def test_publish_bundle_rejects_parent_swap_without_outside_write(
    tmp_path: Path,
) -> None:
    # Given
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "first").mkdir()

    def swap_parent(index: int, phase: str) -> None:
        if index == 0 and phase == "stage":
            _ = (root / "first").rename(outside / "moved")
            (root / "first").symlink_to(outside / "moved", target_is_directory=True)

    # When / Then
    with pytest.raises(ContractError):
        publish_bundle(
            root,
            (BundleOutput(Path("first/result.json"), b"inside"),),
            swap_parent,
        )
    assert list((outside / "moved").iterdir()) == []


def test_publish_bundle_is_idempotent_under_concurrent_same_bundle(
    tmp_path: Path,
) -> None:
    # Given
    root = tmp_path / "root"
    root.mkdir()
    outputs = (
        BundleOutput(Path("same/one.json"), b"one"),
        BundleOutput(Path("same/two.json"), b"two"),
    )

    def publish_same(_index: int) -> None:
        publish_bundle(root, outputs)

    # When
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = tuple(executor.map(publish_same, range(8)))

    # Then
    assert results == (None,) * 8
    assert (root / "same/one.json").read_bytes() == b"one"
    assert (root / "same/two.json").read_bytes() == b"two"


def test_publish_bundle_preserves_concurrent_different_bundles(
    tmp_path: Path,
) -> None:
    # Given
    root = tmp_path / "root"
    root.mkdir()
    bundles = tuple(
        (BundleOutput(Path(f"bundle-{index}/result.json"), str(index).encode()),)
        for index in range(8)
    )

    def publish_different(bundle: tuple[BundleOutput, ...]) -> None:
        publish_bundle(root, bundle)

    # When
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = tuple(executor.map(publish_different, bundles))

    # Then
    assert results == (None,) * 8
    assert {
        (root / f"bundle-{index}/result.json").read_bytes() for index in range(8)
    } == {str(index).encode() for index in range(8)}
