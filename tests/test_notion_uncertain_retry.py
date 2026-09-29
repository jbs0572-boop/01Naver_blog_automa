from __future__ import annotations

from collections.abc import Callable

import httpx2
import pytest

from tools.contract_types import ContractError
from tools.notion_api_http import NotionHttp, create_notion_client
from tools.notion_keychain import NotionApiToken
from tools.notion_transport import NotionCreateUncertain


def _client(handler: Callable[[httpx2.Request], httpx2.Response]) -> httpx2.Client:
    return create_notion_client(
        NotionApiToken("secret-token"), transport=httpx2.MockTransport(handler)
    )


def test_read_retries_rate_limit_without_replaying_a_write() -> None:
    # Given
    calls = 0
    waits: list[float] = []

    def handler(_request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx2.Response(429, headers={"Retry-After": "0"})
        return httpx2.Response(200, json={"id": "data-source"})

    # When
    with _client(handler) as client:
        result = NotionHttp(client, sleeper=waits.append, clock=lambda: 0.0).read(
            "GET", "/v1/data_sources/data-source", 2.0
        )

    # Then
    assert result == {"id": "data-source"}
    assert calls == 2
    assert waits == [0.0]


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_read_retries_transient_server_errors_within_deadline(status: int) -> None:
    # Given
    calls = 0
    waits: list[float] = []

    def handler(_request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx2.Response(status, headers={"Retry-After": "0"})
        return httpx2.Response(200, json={"ok": True})

    # When
    with _client(handler) as client:
        result = NotionHttp(client, sleeper=waits.append, clock=lambda: 0.0).read(
            "GET", "/v1/data_sources/data-source", 2.0
        )

    # Then
    assert result == {"ok": True}
    assert calls == 2
    assert waits == [0.0]


def test_read_converts_non_utf8_json_to_contract_error() -> None:
    # Given
    def handler(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, content=b"\xff")

    # When / Then
    with _client(handler) as client, pytest.raises(
        ContractError, match="malformed JSON"
    ):
        _ = NotionHttp(client).read("GET", "/v1/data_sources/data-source", 2.0)


def test_create_rate_limit_retries_within_the_retry_budget() -> None:
    # Given
    calls = 0

    def handler(_request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx2.Response(429, headers={"Retry-After": "0"})
        return httpx2.Response(200, json={"id": "page"})

    # When
    with _client(handler) as client:
        result = NotionHttp(client).create(
            "POST", "/v1/pages", 2.0, json_body={}, resource="page"
        )

    # Then
    assert result == {"id": "page"}
    assert calls == 2


def test_uncertain_create_escapes_client_context_without_traceback_failure() -> None:
    # Given
    def handler(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(408)

    # When / Then
    with pytest.raises(NotionCreateUncertain), _client(handler) as client:
        _ = NotionHttp(client).create(
            "POST", "/v1/pages", 2.0, json_body={}, resource="page"
        )


@pytest.mark.parametrize("status", [429, 529])
def test_upload_send_retryable_limit_retries_within_the_retry_budget(
    status: int,
) -> None:
    # Given
    calls = 0

    def handler(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json={})

    def send_once(_timeout: float) -> httpx2.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx2.Response(status, headers={"Retry-After": "0"})
        return httpx2.Response(200, json={"id": "attachment"})

    # When
    with _client(handler) as client:
        result = NotionHttp(client).send(2.0, "attachment", send_once)

    # Then
    assert result == {"id": "attachment"}
    assert calls == 2


@pytest.mark.parametrize("status", [408, 500])
def test_create_server_failure_is_uncertain_after_one_request(status: int) -> None:
    # Given
    calls = 0

    def handler(_request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        return httpx2.Response(status)

    # When / Then
    with _client(handler) as client, pytest.raises(NotionCreateUncertain):
        _ = NotionHttp(client).create(
            "POST", "/v1/file_uploads", 2.0, json_body={}, resource="attachment"
        )
    assert calls == 1


def test_create_transport_failure_is_uncertain_after_one_request() -> None:
    # Given
    calls = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        raise httpx2.ReadTimeout("connection interrupted", request=request)

    # When / Then
    with _client(handler) as client, pytest.raises(NotionCreateUncertain):
        _ = NotionHttp(client).create(
            "POST", "/v1/file_uploads", 2.0, json_body={}, resource="attachment"
        )
    assert calls == 1


def test_upload_send_server_failure_is_uncertain_after_one_call() -> None:
    # Given
    calls = 0

    def handler(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json={})

    def send_once(_timeout: float) -> httpx2.Response:
        nonlocal calls
        calls += 1
        return httpx2.Response(500)

    # When / Then
    with _client(handler) as client, pytest.raises(NotionCreateUncertain):
        _ = NotionHttp(client).send(2.0, "attachment", send_once)
    assert calls == 1


def test_upload_send_transport_failure_is_uncertain_after_one_call() -> None:
    # Given
    calls = 0

    def handler(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json={})

    def send_once(_timeout: float) -> httpx2.Response:
        nonlocal calls
        calls += 1
        raise OSError("connection interrupted")

    # When / Then
    with _client(handler) as client, pytest.raises(NotionCreateUncertain):
        _ = NotionHttp(client).send(2.0, "attachment", send_once)
    assert calls == 1


@pytest.mark.parametrize(
    ("raw_retry_after", "expected_wait"),
    [("nan", 1.0), ("inf", 1.0), ("-1", 0.0)],
)
def test_read_sanitizes_retry_after_before_waiting(
    raw_retry_after: str, expected_wait: float
) -> None:
    # Given
    calls = 0
    waits: list[float] = []

    def handler(_request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx2.Response(429, headers={"Retry-After": raw_retry_after})
        return httpx2.Response(200, json={"ok": True})

    # When
    with _client(handler) as client:
        result = NotionHttp(client, sleeper=waits.append, clock=lambda: 0.0).read(
            "GET", "/v1/data_sources/data-source", 2.0
        )

    # Then
    assert result == {"ok": True}
    assert calls == 2
    assert waits == [expected_wait]
