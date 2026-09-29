from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tests._notion_resume_test_support import _adapter, _request
from tests._notion_resume_transport import MemoryTransport, _page
from tools.contract_types import ContractError
from tools.notion_resume import ResumableNotionAdapter


def test_passed_q2_resume_revalidates_remote_page_without_recreating_resources(
    tmp_path: Path,
) -> None:
    # Given
    request, _ = _request(tmp_path)
    transport = MemoryTransport()
    adapter = _adapter(transport)
    first = adapter.write_and_verify(request)
    attachment_creates = transport.attachment_create_attempts
    page_creates = transport.create_page_calls
    remote_calls = transport.transport_calls

    # When
    resumed = adapter.write_and_verify(request)

    # Then
    assert resumed["notion_page_id"] == first["notion_page_id"]
    assert transport.attachment_create_attempts == attachment_creates
    assert transport.create_page_calls == page_creates
    assert transport.transport_calls > remote_calls


def test_passed_q2_resume_rejects_missing_recorded_remote_page_identity(
    tmp_path: Path,
) -> None:
    # Given
    request, _ = _request(tmp_path)
    transport = MemoryTransport()
    adapter = _adapter(transport)
    _ = adapter.write_and_verify(request)
    transport.pages.clear()
    attachment_creates = transport.attachment_create_attempts
    page_creates = transport.create_page_calls

    # When / Then
    with pytest.raises(ContractError, match="recorded page"):
        _ = adapter.write_and_verify(request)
    assert transport.attachment_create_attempts == attachment_creates
    assert transport.create_page_calls == page_creates


def test_transport_calls_receive_remaining_overall_timeout_and_fail_closed(
    tmp_path: Path,
) -> None:
    # Given
    request, _ = _request(tmp_path)
    transport = MemoryTransport()
    adapter = _adapter(transport)

    # When
    _ = adapter.write_and_verify(request)

    # Then
    assert transport.timeouts
    assert all(
        0 < timeout <= request.notion_timeout_seconds for timeout in transport.timeouts
    )

    # Given: elapsed monotonic time consumes the overall deadline before any call.
    exhausted_request = replace(
        request,
        checkpoint_path=tmp_path / "state" / "exhausted.json",
        notion_timeout_seconds=1.0,
    )
    ticks = iter((0.0, 1.0))
    exhausted_transport = MemoryTransport()
    exhausted_adapter = ResumableNotionAdapter(
        exhausted_transport,
        _page("topic"),
        lambda: datetime(2026, 8, 31, 9, 5, tzinfo=UTC),
        monotonic_clock=lambda: next(ticks),
    )

    # When / Then
    with pytest.raises(ContractError, match="deadline"):
        _ = exhausted_adapter.write_and_verify(exhausted_request)
    assert exhausted_transport.transport_calls == 0
