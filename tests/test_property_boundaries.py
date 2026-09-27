from __future__ import annotations

import hashlib
import tempfile
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tools.contract_types import ContractError
from tools.gate import parse_aware_datetime
from tools.runner_state import file_digest, state_paths

SAFE_TEXT = st.text(
    alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Nd")),
    min_size=1,
    max_size=24,
)


@given(st.binary(max_size=256))
def test_file_digest_matches_sha256_for_generated_bytes(
    content: bytes,
) -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "input.bin"
        _ = path.write_bytes(content)

        assert file_digest(path) == hashlib.sha256(content).hexdigest()


@given(st.integers(min_value=-1439, max_value=1439))
def test_aware_timestamp_offsets_normalize_to_utc(offset_minutes: int) -> None:
    instant = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)
    local = instant.astimezone(timezone(timedelta(minutes=offset_minutes)))

    assert parse_aware_datetime(local.isoformat(), "decided_at") == instant


@given(SAFE_TEXT)
def test_generated_safe_run_ids_stay_in_runner_directories(
    run_id: str,
) -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        state_path, log_path, lock_path = state_paths(root, run_id)

        assert state_path == root / ".automation" / "state" / f"{run_id}.json"
        assert log_path == root / ".automation" / "logs" / f"{run_id}.jsonl"
        assert lock_path == root / ".automation" / "locks" / f"{run_id}.lock"


@pytest.mark.parametrize("run_id", ("", ".", "..", "nested/run", "/tmp/run"))
def test_unsafe_run_ids_are_rejected(tmp_path: Path, run_id: str) -> None:
    with pytest.raises(ContractError, match="safe path"):
        _ = state_paths(tmp_path, run_id)
