from __future__ import annotations

import json
import socket
from collections.abc import Callable
from dataclasses import dataclass, field
from math import isfinite
from time import monotonic, sleep
from typing import Final

import httpx2

from tools.contract_types import ContractError, JSONMap, JSONValue
from tools.notion_keychain import NotionApiToken
from tools.notion_transport import NotionCreateUncertain

NOTION_VERSION: Final = "2026-03-11"
_LIMITS: Final = httpx2.Limits(
    max_connections=200,
    max_keepalive_connections=40,
    keepalive_expiry=30.0,
)
_TIMEOUT: Final = httpx2.Timeout(connect=5.0, read=30.0, write=10.0, pool=10.0)


@dataclass(frozen=True, slots=True)
class NotionClientConfig:
    token: NotionApiToken = field(repr=False)


def create_notion_client(
    token: NotionApiToken, *, transport: httpx2.BaseTransport | None = None
) -> httpx2.Client:
    config = NotionClientConfig(token)
    resolved = transport or httpx2.HTTPTransport(
        http2=True,
        retries=0,
        limits=_LIMITS,
        socket_options=[(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)],
    )
    return httpx2.Client(
        transport=resolved,
        timeout=_TIMEOUT,
        base_url="https://api.notion.com",
        headers={
            "Authorization": f"Bearer {config.token}",
            "Notion-Version": NOTION_VERSION,
            "Accept": "application/json",
        },
        follow_redirects=True,
    )


@dataclass(frozen=True, slots=True)
class NotionHttp:
    client: httpx2.Client
    sleeper: Callable[[float], None] = sleep
    clock: Callable[[], float] = monotonic

    def read(
        self,
        method: str,
        path: str,
        timeout_seconds: float,
        *,
        params: dict[str, str] | None = None,
        json_body: JSONMap | None = None,
    ) -> JSONMap:
        deadline = self.clock() + timeout_seconds
        for attempt in range(4):
            try:
                response = self.client.request(
                    method,
                    path,
                    params=params,
                    json=json_body,
                    timeout=max(deadline - self.clock(), 0.001),
                )
            except httpx2.RequestError:
                if attempt == 3:
                    raise ContractError("Notion API read request failed") from None
                continue
            if response.status_code in {429, 500, 502, 503, 504, 529} and attempt < 3:
                self._wait(response, deadline)
                continue
            if not 200 <= response.status_code < 300:
                raise ContractError(
                    f"Notion API read failed with HTTP {response.status_code}"
                )
            return response_map(response)
        raise ContractError("Notion API read request failed")

    def create(
        self,
        method: str,
        path: str,
        timeout_seconds: float,
        *,
        json_body: JSONMap,
        resource: str,
        authorize: Callable[[], None] | None = None,
    ) -> JSONMap:
        deadline = self.clock() + timeout_seconds
        for attempt in range(4):
            try:
                if authorize is not None:
                    authorize()
                response = self.client.request(
                    method,
                    path,
                    json=json_body,
                    timeout=max(deadline - self.clock(), 0.001),
                )
            except httpx2.RequestError:
                raise NotionCreateUncertain(resource=resource) from None
            if response.status_code in {429, 529} and attempt < 3:
                self._wait(response, deadline)
                continue
            return created_response(response, resource)
        raise NotionCreateUncertain(resource=resource)

    def send(
        self,
        timeout_seconds: float,
        resource: str,
        request: Callable[[float], httpx2.Response],
        authorize: Callable[[], None] | None = None,
    ) -> JSONMap:
        deadline = self.clock() + timeout_seconds
        for attempt in range(4):
            try:
                if authorize is not None:
                    authorize()
                response = request(max(deadline - self.clock(), 0.001))
            except (OSError, httpx2.RequestError):
                raise NotionCreateUncertain(resource=resource) from None
            if response.status_code in {429, 529} and attempt < 3:
                self._wait(response, deadline)
                continue
            return created_response(response, resource)
        raise NotionCreateUncertain(resource=resource)

    def _wait(self, response: httpx2.Response, deadline: float) -> None:
        raw = response.headers.get("Retry-After", "1")
        try:
            delay = max(float(raw), 0.0)
        except ValueError:
            delay = 1.0
        if not isfinite(delay):
            delay = 1.0
        remaining = deadline - self.clock()
        if remaining <= 0 or delay >= remaining:
            raise ContractError("Notion API retry deadline is exhausted")
        self.sleeper(delay)


def created_response(response: httpx2.Response, resource: str) -> JSONMap:
    if response.status_code == 408 or response.status_code >= 500:
        raise NotionCreateUncertain(resource=resource)
    if not 200 <= response.status_code < 300:
        raise ContractError(f"Notion {resource} create request was rejected")
    return response_map(response)


def response_map(response: httpx2.Response) -> JSONMap:
    try:
        value: JSONValue = json.loads(response.content)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ContractError("Notion API returned malformed JSON") from error
    return as_map(value, "response")


def as_map(value: JSONValue | None, label: str) -> JSONMap:
    if not isinstance(value, dict):
        raise ContractError(f"Notion API {label} must be an object")
    return value


def array(value: JSONMap, key: str) -> list[JSONValue]:
    result = value.get(key)
    if not isinstance(result, list):
        raise ContractError(f"Notion API {key} must be an array")
    return result


def text(value: JSONMap, key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result:
        raise ContractError(f"Notion API {key} is missing")
    return result


__all__ = [
    "NOTION_VERSION",
    "NotionClientConfig",
    "NotionHttp",
    "array",
    "as_map",
    "create_notion_client",
    "created_response",
    "text",
]
