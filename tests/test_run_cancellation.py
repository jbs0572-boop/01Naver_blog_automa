from pathlib import Path

import pytest

from tools.contract_types import ContractError
from tools.run_cancellation import create_cancellation, read_cancellation


def test_cancellation_is_immutable_and_idempotent(tmp_path: Path) -> None:
    first = create_cancellation(tmp_path, run_id="RUN-1", batch_id="BATCH-1", child_id="CHILD-01", scope="queued_only", nonce="one", requested_at="2026-09-12T00:00:00+00:00")
    second = create_cancellation(tmp_path, run_id="RUN-1", batch_id="BATCH-1", child_id="CHILD-01", scope="queued_only", nonce="one", requested_at="2026-09-12T00:00:01+00:00")
    assert second == first
    assert read_cancellation(tmp_path, "RUN-1") == first
    with pytest.raises(ContractError):
        _ = create_cancellation(tmp_path, run_id="RUN-1", batch_id="BATCH-1", child_id="CHILD-01", scope="remaining", nonce="two")


def test_cancellation_rejects_symlink_control_directory(tmp_path: Path) -> None:
    (tmp_path / ".automation").mkdir()
    (tmp_path / ".automation" / "control").symlink_to("/tmp", target_is_directory=True)
    with pytest.raises(ContractError):
        _ = create_cancellation(tmp_path, run_id="RUN-1", batch_id="BATCH-1", child_id="CHILD-01", scope="queued_only", nonce="one")
