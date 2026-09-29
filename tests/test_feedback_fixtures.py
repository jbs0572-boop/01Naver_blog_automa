from __future__ import annotations

import socket
import subprocess

import pytest

from tests.feedback_fixtures import (
    FIXTURE_SOURCE_IDS,
    FixtureRequest,
    FrozenFeedbackTransport,
    UnknownFixtureSourceError,
)


def test_replay_returns_every_frozen_source_payload_and_records_requests() -> None:
    # Given: the five synthetic external-source fixtures and an in-memory transport.
    transport = FrozenFeedbackTransport()

    # When: every source is replayed through the one injectable interface.
    responses = tuple(transport.fetch(FixtureRequest(source_id)) for source_id in FIXTURE_SOURCE_IDS)

    # Then: source identities and call observables are preserved without an adapter-specific path.
    assert tuple(response.source_id for response in responses) == FIXTURE_SOURCE_IDS
    assert tuple(response.payload["source_id"] for response in responses) == FIXTURE_SOURCE_IDS
    assert transport.call_count == len(FIXTURE_SOURCE_IDS)
    assert tuple(request.source_id for request in transport.requests) == FIXTURE_SOURCE_IDS


def test_replay_returns_a_fresh_payload_for_each_call() -> None:
    # Given: one frozen DataLab fixture.
    transport = FrozenFeedbackTransport()
    request = FixtureRequest("naver-datalab")

    # When: a consumer mutates its received payload before a second replay.
    first = transport.fetch(request).payload
    first["consumer_only"] = "changed"
    second = transport.fetch(request).payload

    # Then: fixture replay remains deterministic and isolated from consumer mutation.
    assert "consumer_only" not in second
    assert transport.call_count == 2


def test_replay_rejects_an_unknown_source_without_a_network_fallback() -> None:
    # Given: an unknown source request.
    transport = FrozenFeedbackTransport()

    # When/Then: it fails closed instead of inventing a provider request.
    with pytest.raises(UnknownFixtureSourceError, match="unknown frozen fixture source"):
        _ = transport.fetch(FixtureRequest("unknown-provider"))
    assert transport.call_count == 0


def test_replay_uses_no_socket_or_process(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: process and socket seams that would fail if fixture replay crossed the machine boundary.
    socket_calls: list[str] = []
    process_calls: list[str] = []

    def forbidden_socket() -> socket.socket:
        socket_calls.append("socket")
        raise AssertionError("frozen fixture replay must not create sockets")

    def forbidden_run() -> subprocess.CompletedProcess[str]:
        process_calls.append("process")
        raise AssertionError("frozen fixture replay must not start processes")

    monkeypatch.setattr(socket, "socket", forbidden_socket)
    monkeypatch.setattr(subprocess, "run", forbidden_run)
    transport = FrozenFeedbackTransport()

    # When: a known fixture is replayed from the explicit local root.
    _ = transport.fetch(FixtureRequest("naver-datalab"))

    # Then: no network socket or process was invoked.
    assert socket_calls == []
    assert process_calls == []
