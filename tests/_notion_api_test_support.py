from __future__ import annotations

import httpx2

from tools.contract_types import JSONMap
from tools.notion_api import create_notion_client
from tools.notion_api_payload import REQUIRED_PROPERTIES
from tools.notion_keychain import NotionApiToken


def schema_properties() -> JSONMap:
    return {name: {"type": kind} for name, kind in REQUIRED_PROPERTIES.items()}


def client(handler: httpx2.MockTransport) -> httpx2.Client:
    return create_notion_client(NotionApiToken("secret-token"), transport=handler)


def uploaded(upload_id: str, filename: str, content: bytes) -> JSONMap:
    return {
        "id": upload_id,
        "status": "uploaded",
        "filename": filename,
        "content_type": "image/png",
        "content_length": len(content),
    }
