from __future__ import annotations

import httpx2
import pytest

from tests._notion_api_test_support import client as _client
from tools.contract_types import ContractError
from tools.notion_api import NotionApiTransport


def test_next_sequence_rejects_boolean_number() -> None:
    def handler(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            json={
                "results": [
                    {
                        "properties": {
                            "상태": {"select": {"name": "검수 대기"}},
                            "차수": {"number": True},
                        }
                    }
                ],
                "has_more": False,
            },
        )

    with (
        _client(httpx2.MockTransport(handler)) as client,
        pytest.raises(ContractError, match="sequence is malformed"),
    ):
        _ = NotionApiTransport(client).next_sequence(
            "ds", "2026-08-31", timeout_seconds=2
        )
