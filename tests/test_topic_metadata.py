from datetime import UTC, datetime
from pathlib import Path

import pytest

from tools.contract_types import ContractError
from tools.topic_metadata import (
    CREATOR_ADVISOR_URL,
    CreatorAdvisorCandidate,
    CreatorAdvisorSnapshot,
    write_snapshot,
)


def test_creator_advisor_snapshot_is_written_under_date_and_capture_id(tmp_path: Path) -> None:
    snapshot = CreatorAdvisorSnapshot(
        as_of_date="2026-09-01",
        captured_at=datetime(2026, 9, 1, 9, 0, tzinfo=UTC).isoformat(),
        capture_id="capture-001",
        candidates=(CreatorAdvisorCandidate("가을 여행", 1, trend_index=92.0),),
    )

    path = write_snapshot(tmp_path, snapshot)

    assert path == tmp_path / "metadata" / "creator-advisor" / "2026-09-01" / "capture-001.json"
    assert '"source_url": "' + CREATOR_ADVISOR_URL + '"' in path.read_text(encoding="utf-8")
    with pytest.raises(ContractError):
        _ = write_snapshot(tmp_path, snapshot)
