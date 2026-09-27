from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tools.contract_types import ContractError, JSONMap
from tools.topic_feedback_manifest_reader import (
    load_feedback_evidence,
)
from tools.topic_feedback_manifest_types import FeedbackEvidenceRequest


def _canonical(value: JSONMap) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()


def _manifest(root: Path, signal: JSONMap) -> tuple[Path, str]:
    signal_bytes = _canonical(signal)
    signal_path = root / "metadata/topic-signals/naver-datalab/2026-09-01/s.json"
    signal_path.parent.mkdir(parents=True)
    _ = signal_path.write_bytes(signal_bytes)
    payload: JSONMap = {
        "schema_version": "feedback-evidence-manifest-v1",
        "feedback_id": "F-001",
        "created_at": "2026-09-02T09:00:00+09:00",
        "data_as_of": "2026-09-01T23:59:00+09:00",
        "files": [
            {
                "path": signal_path.relative_to(root).as_posix(),
                "size_bytes": len(signal_bytes),
                "sha256": "sha256:" + hashlib.sha256(signal_bytes).hexdigest(),
                "schema_version": signal["schema_version"],
            }
        ],
    }
    payload["digest"] = "sha256:" + hashlib.sha256(_canonical(payload)).hexdigest()
    path = root / "metadata/feedback-manifests/F-001.json"
    path.parent.mkdir(parents=True)
    _ = path.write_bytes(_canonical(payload))
    return path, str(payload["digest"])


def _signal() -> JSONMap:
    return {
        "schema_version": "topic-signal-snapshot-v1",
        "captured_at": "2026-09-01T09:00:00+09:00",
        "as_of_date": "2026-09-01",
        "timezone": "Asia/Seoul",
        "limitations": ["relative_index_only"],
        "input_digests": [],
        "missing_fields": [],
        "status": "mature",
        "digest": "sha256:" + "0" * 64,
        "source_id": "naver-datalab",
        "source_confidence": "A",
        "access_mode": "official_api",
        "query_period": "2026-08-01/2026-09-01",
        "terms_checked_at": "2026-09-08T00:00:00+09:00",
        "raw_payload": {},
        "derived": {
            "ranking_features": [
                {"keyword": "둘째 후보", "unit": "relative_index", "value": 100}
            ]
        },
    }


def test_manifest_derives_signal_trust_from_registry_and_rollout(
    tmp_path: Path,
) -> None:
    # Given: a digest-bound signal whose trust labels match the fixed source registry.
    signal = _signal()
    unsigned = dict(signal)
    _ = unsigned.pop("digest")
    signal["digest"] = "sha256:" + hashlib.sha256(_canonical(unsigned)).hexdigest()
    path, digest = _manifest(tmp_path, signal)

    # When: the exact pinned manifest crosses the trusted reader boundary.
    evidence = load_feedback_evidence(
        FeedbackEvidenceRequest(
            tmp_path,
            path,
            digest,
            "2026-09-09T00:00:00+09:00",
            ("naver-datalab",),
        )
    )

    # Then: the signal is typed and confidence is taken from policy.
    assert evidence.digest == digest
    assert evidence.signals[0].source_confidence == "A"
    assert evidence.signals[0].keyword == "둘째 후보"
    assert evidence.signal_paths == (
        "metadata/topic-signals/naver-datalab/2026-09-01/s.json",
    )


def test_manifest_rejects_tamper_and_parent_symlink(tmp_path: Path) -> None:
    # Given: a valid manifest and then a digest-changing file mutation.
    signal = _signal()
    unsigned = dict(signal)
    _ = unsigned.pop("digest")
    signal["digest"] = "sha256:" + hashlib.sha256(_canonical(unsigned)).hexdigest()
    path, digest = _manifest(tmp_path, signal)
    entry = next((tmp_path / "metadata/topic-signals").rglob("*.json"))
    _ = entry.write_text("{}", encoding="utf-8")

    # When/Then: neither tampering nor a symlinked manifest parent is followed.
    with pytest.raises(ContractError, match="feedback evidence file mismatch"):
        _ = load_feedback_evidence(
            FeedbackEvidenceRequest(
                tmp_path,
                path,
                digest,
                "2026-09-09T00:00:00+09:00",
                ("naver-datalab",),
            )
        )
    unsafe_root = tmp_path / "unsafe"
    unsafe_root.mkdir()
    (unsafe_root / "metadata").symlink_to(tmp_path / "metadata")
    with pytest.raises(ContractError, match="feedback evidence path is unsafe"):
        _ = load_feedback_evidence(
            FeedbackEvidenceRequest(
                unsafe_root,
                unsafe_root / path.relative_to(tmp_path),
                digest,
                "2026-09-09T00:00:00+09:00",
                ("naver-datalab",),
            )
        )


def test_manifest_rejects_unknown_hashed_provenance_schema(tmp_path: Path) -> None:
    # Given: an internally consistent manifest hashes an unapproved JSON schema.
    unknown: JSONMap = {"schema_version": "unapproved-provenance-v1", "value": 1}
    path, digest = _manifest(tmp_path, unknown)

    # When / Then: the allowlist rejects it instead of silently ignoring it.
    with pytest.raises(ContractError, match="unsupported topic feedback schema_version"):
        _ = load_feedback_evidence(
            FeedbackEvidenceRequest(
                tmp_path,
                path,
                digest,
                "2026-09-09T00:00:00+09:00",
                (),
            )
        )
