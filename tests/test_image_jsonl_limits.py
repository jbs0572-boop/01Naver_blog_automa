"""Guard untrusted image proof JSONL from unbounded memory reads."""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.image_quality import (
    MAX_IMAGE_LOG_BYTES,
    validate_image_metadata,
    validate_image_quality,
)
from tools.workflow_contract import ContractError


@pytest.mark.parametrize("kind", ["metadata", "quality"])
def test_oversized_image_proof_log_is_rejected_before_unbounded_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    path = tmp_path / "proof.jsonl"
    # Sparse on Linux/macOS: does not allocate a huge disk payload.
    with path.open("wb") as handle:
        handle.truncate(MAX_IMAGE_LOG_BYTES + 1)

    original_read_text = Path.read_text

    def no_unbounded_text_read(file: Path, *args: object, **kwargs: object) -> str:
        if file == path:
            raise AssertionError("image proof must not be read with unbounded read_text")
        return original_read_text(file, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", no_unbounded_text_read)
    with pytest.raises(ContractError, match="image evidence JSONL exceeds"):
        if kind == "metadata":
            validate_image_metadata(path, "formal")
        else:
            validate_image_quality(path)


@pytest.mark.parametrize("kind", ["metadata", "quality"])
def test_invalid_utf8_image_proof_log_fails_with_contract_error(
    tmp_path: Path, kind: str,
) -> None:
    path = tmp_path / "proof.jsonl"
    path.write_bytes(b'{"bad": "\xff"}\n')
    with pytest.raises(ContractError, match="image evidence JSONL is not UTF-8"):
        if kind == "metadata":
            validate_image_metadata(path, "formal")
        else:
            validate_image_quality(path)


@pytest.mark.parametrize("kind", ["metadata", "quality"])
def test_empty_image_proof_log_still_fails_closed(
    tmp_path: Path, kind: str,
) -> None:
    path = tmp_path / "proof.jsonl"
    path.write_text("   \n", encoding="utf-8")
    with pytest.raises(ContractError, match="image metadata is empty"):
        if kind == "metadata":
            validate_image_metadata(path, "formal")
        else:
            validate_image_quality(path)


@pytest.mark.parametrize("kind", ["metadata", "quality"])
def test_parser_recursion_failure_is_a_contract_rejection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    from tools import image_quality

    path = tmp_path / "proof.jsonl"
    marker = '{"trigger":"parser-recursion"}'
    path.write_text(marker + "\n", encoding="utf-8")
    original_loads = image_quality.json.loads

    def simulated_recursion_error(payload: str, *args: object, **kwargs: object) -> object:
        if payload == marker:
            raise RecursionError("JSON parser exceeded depth limit")
        return original_loads(payload, *args, **kwargs)

    # Actual JSON decoder recursion thresholds differ by interpreter.
    monkeypatch.setattr(image_quality.json, "loads", simulated_recursion_error)
    with pytest.raises(ContractError, match="invalid JSON"):
        if kind == "metadata":
            validate_image_metadata(path, "formal")
        else:
            validate_image_quality(path)


@pytest.mark.parametrize("kind", ["metadata", "quality"])
def test_extreme_nesting_never_creates_accepted_image_proof(
    tmp_path: Path, kind: str,
) -> None:
    path = tmp_path / "proof.jsonl"
    path.write_text("[" * 20000 + "0" + "]" * 20000 + "\n", encoding="utf-8")
    # On other Python versions the decoder may successfully parse this as a
    # list; then the proof-object gate still must reject it.
    with pytest.raises(ContractError):
        if kind == "metadata":
            validate_image_metadata(path, "formal")
        else:
            validate_image_quality(path)
