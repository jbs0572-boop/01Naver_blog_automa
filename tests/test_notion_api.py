from __future__ import annotations

import hashlib
import json
from pathlib import Path

import httpx2
import pytest

from tools.contract_types import ContractError, JSONMap, JSONValue
from tools.external_adapter import (
    ExternalSystem,
    ExternalWriteRequest,
    verify_notion_round_trip,
)
from tools.manifest import Manifest, ManifestFile
from tools.notion_api import (
    NOTION_VERSION,
    NotionApiAdapter,
    NotionApiTransport,
    create_notion_client,
    existing_identity,
    probe_notion_access,
)
from tools.notion_api_http import NotionHttp
from tools.notion_api_metrics import run_metrics
from tools.notion_api_payload import canonical_block, page_properties
from tools.notion_api_roundtrip import verify_bytes, verify_image
from tools.notion_content import AssetMetadata, parse_naver_copy
from tools.notion_keychain import NotionApiToken
from tools.notion_transport import AttachmentSpec, NotionCreateUncertain


def _schema_properties() -> JSONMap:
    from tools.notion_api_payload import REQUIRED_PROPERTIES

    return {name: {"type": kind} for name, kind in REQUIRED_PROPERTIES.items()}


def _client(handler: httpx2.MockTransport) -> httpx2.Client:
    return create_notion_client(NotionApiToken("secret-token"), transport=handler)


def _uploaded(upload_id: str, filename: str, content: bytes) -> JSONMap:
    return {
        "id": upload_id,
        "status": "uploaded",
        "filename": filename,
        "content_type": "image/png",
        "content_length": len(content),
    }


def test_find_attachments_paginates_and_matches_exact_filename() -> None:
    # Given
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        assert request.headers["Notion-Version"] == NOTION_VERSION
        cursor = request.url.params.get("start_cursor")
        if cursor is None:
            return httpx2.Response(
                200,
                json={
                    "results": [
                        {
                            "type": "file_upload",
                            "file_upload": {
                                "id": "x",
                                "filename": "other",
                                "status": "uploaded",
                            },
                        },
                        {
                            "type": "file_upload",
                            "file_upload": {
                                "id": "expired",
                                "filename": "exact.png",
                                "status": "expired",
                            },
                        },
                        {
                            "type": "file_upload",
                            "file_upload": {
                                "id": "failed",
                                "filename": "exact.png",
                                "status": "failed",
                            },
                        },
                    ],
                    "has_more": True,
                    "next_cursor": "next",
                },
            )
        return httpx2.Response(
            200,
            json={
                "results": [
                    {
                        "type": "file_upload",
                        "file_upload": {
                            "id": "wanted",
                            "filename": "exact.png",
                            "status": "uploaded",
                            "content_type": "image/png",
                            "content_length": 3,
                        },
                    }
                ],
                "has_more": False,
            },
        )

    transport = httpx2.MockTransport(handler)

    # When
    with _client(transport) as client:
        found = NotionApiTransport(client).find_attachments(
            "ds", "exact.png", timeout_seconds=3
        )

    # Then
    assert found == (_uploaded("wanted", "exact.png", b"png"),)
    assert len(requests) == 2


def test_find_attachments_rejects_legacy_direct_list_shape() -> None:
    # Given
    response = {
        "results": [{"id": "legacy", "filename": "exact.png", "status": "uploaded"}],
        "has_more": False,
    }

    # When / Then
    with (
        _client(
            httpx2.MockTransport(lambda _request: httpx2.Response(200, json=response))
        ) as client,
        pytest.raises(ContractError, match="envelope"),
    ):
        _ = NotionApiTransport(client).find_attachments(
            "ds", "exact.png", timeout_seconds=2
        )


def test_create_attachment_uses_single_part_then_multipart_send(tmp_path: Path) -> None:
    # Given
    image = tmp_path / "image.png"
    _ = image.write_bytes(b"png")
    seen: list[tuple[str, str, bytes]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append((request.method, request.url.path, request.content))
        if request.url.path == "/v1/file_uploads":
            assert json.loads(request.content) == {
                "mode": "single_part",
                "filename": "safe.png",
                "content_type": "image/png",
            }
            return httpx2.Response(200, json={"id": "upload-1"})
        assert request.url.path == "/v1/file_uploads/upload-1/send"
        assert b'filename="safe.png"' in request.content
        return httpx2.Response(200, json=_uploaded("upload-1", "safe.png", b"png"))

    # When
    with _client(httpx2.MockTransport(handler)) as client:
        created = NotionApiTransport(client).create_attachment(
            "ds",
            AttachmentSpec(
                image,
                "safe.png",
                "thumbnail",
                hashlib.sha256(b"png").hexdigest(),
                1,
            ),
            timeout_seconds=4,
        )

    # Then
    assert created == _uploaded("upload-1", "safe.png", b"png")
    assert [item[:2] for item in seen] == [
        ("POST", "/v1/file_uploads"),
        ("POST", "/v1/file_uploads/upload-1/send"),
    ]


def test_create_attachment_rejects_over_20mb_before_network(tmp_path: Path) -> None:
    # Given
    image = tmp_path / "large.png"
    with image.open("wb") as stream:
        _ = stream.truncate(20 * 1024 * 1024 + 1)
    called = False

    def handler(_request: httpx2.Request) -> httpx2.Response:
        nonlocal called
        called = True
        return httpx2.Response(500)

    # When / Then
    with (
        _client(httpx2.MockTransport(handler)) as client,
        pytest.raises(ContractError, match="20 MB"),
    ):
        _ = NotionApiTransport(client).create_attachment(
            "ds",
            AttachmentSpec(image, "large.png", "thumbnail", "abc", 1),
            timeout_seconds=4,
        )
    assert called is False


def test_create_timeout_is_uncertain_and_secret_is_redacted(tmp_path: Path) -> None:
    # Given
    image = tmp_path / "image.png"
    _ = image.write_bytes(b"png")

    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("secret-token", request=request)

    # When / Then
    with _client(httpx2.MockTransport(handler)) as client:
        api = NotionApiTransport(client)
        with pytest.raises(NotionCreateUncertain) as raised:
            _ = api.create_attachment(
                "ds",
                AttachmentSpec(
                    image,
                    "safe.png",
                    "thumbnail",
                    hashlib.sha256(b"png").hexdigest(),
                    1,
                ),
                timeout_seconds=4,
            )
    assert "secret-token" not in str(raised.value)


def test_find_pages_extracts_digest_from_review_note() -> None:
    # Given
    digest = "sha256:" + "a" * 64

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/v1/data_sources/ds/query"
        assert json.loads(request.content)["filter"]["property"] == "실행 ID"
        return httpx2.Response(
            200,
            json={
                "results": [
                    {
                        "id": "page",
                        "parent": {"data_source_id": "ds"},
                        "properties": {
                            "제목": {
                                "title": [
                                    {
                                        "plain_text": "네이버-블로그-글쓰기-2026-08-31-2차수"
                                    }
                                ]
                            },
                            "실행일": {"date": {"start": "2026-08-31"}},
                            "차수": {"number": 2},
                            "실행 ID": {"rich_text": [{"plain_text": "run"}]},
                            "검수 메모": {
                                "rich_text": [
                                    {"plain_text": f"artifact_digest={digest}"}
                                ]
                            },
                        },
                    }
                ],
                "has_more": False,
            },
        )

    # When
    with _client(httpx2.MockTransport(handler)) as client:
        pages = NotionApiTransport(client).find_pages(
            "ds", "run", digest, timeout_seconds=3
        )

    # Then
    assert pages == (
        {
            "id": "page",
            "target_id": "ds",
            "run_id": "run",
            "artifact_digest": digest,
            "title": "네이버-블로그-글쓰기-2026-08-31-2차수",
            "execution_date": "2026-08-31",
            "sequence": 2,
        },
    )


def test_find_pages_returns_same_run_page_with_another_artifact_digest() -> None:
    # Given
    current = "sha256:" + "a" * 64
    stale = "sha256:" + "b" * 64

    def handler(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            json={
                "results": [
                    {
                        "id": "stale-page",
                        "properties": {
                            "제목": {"title": [{"plain_text": "title"}]},
                            "실행일": {"date": {"start": "2026-08-31"}},
                            "차수": {"number": 1},
                            "검수 메모": {
                                "rich_text": [
                                    {"plain_text": f"artifact_digest={stale}; Q1=passed"}
                                ]
                            },
                        },
                    }
                ],
                "has_more": False,
            },
        )

    # When
    with _client(httpx2.MockTransport(handler)) as client:
        pages = NotionApiTransport(client).find_pages(
            "ds", "run", current, timeout_seconds=3
        )

    # Then
    assert len(pages) == 1
    assert pages[0]["artifact_digest"] == stale


def test_page_create_and_append_use_root_batches_of_at_most_100() -> None:
    # Given
    roots: tuple[JSONMap, ...] = tuple(
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": []},
            "index": index,
        }
        for index in range(205)
    )
    counts: list[int] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/v1/pages/page":
            return httpx2.Response(
                200,
                json={
                    "parent": {"type": "data_source_id", "data_source_id": "ds"}
                },
            )
        body = json.loads(request.content)
        counts.append(len(body["children"]))
        return httpx2.Response(200, json={"id": "page"})

    # When
    with _client(httpx2.MockTransport(handler)) as client:
        api = NotionApiTransport(client, root_blocks=roots, target_id="ds")
        _ = api.create_page("ds", "run", "digest", (), timeout_seconds=3)
        _ = api.append_page("page", 100, timeout_seconds=3)
        _ = api.append_page("page", 200, timeout_seconds=3)

    # Then
    assert counts == [100, 100, 5]


def test_append_parent_verification_checks_schema_before_reading_page() -> None:
    # Given
    calls: list[tuple[str, str]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append((request.method, request.url.path))
        if request.url.path == "/v1/data_sources/ds":
            return httpx2.Response(
                200, json={"id": "ds", "properties": _schema_properties()}
            )
        if request.url.path == "/v1/pages/page":
            return httpx2.Response(
                200,
                json={
                    "parent": {
                        "type": "data_source_id",
                        "data_source_id": "other-source",
                    }
                },
            )
        pytest.fail(f"unexpected Notion request: {request.method} {request.url.path}")

    # When / Then
    with (
        _client(httpx2.MockTransport(handler)) as client,
        pytest.raises(ContractError, match="does not match expected target"),
    ):
        NotionApiTransport(
            client, target_id="ds", enforce_schema=True
        ).verify_page_parent("page", "ds", timeout_seconds=3)
    assert calls == [
        ("GET", "/v1/data_sources/ds"),
        ("GET", "/v1/pages/page"),
    ]


def test_append_retry_fails_closed_when_parent_changes_after_rate_limit() -> None:
    # Given
    schema_reads = 0
    parent_reads = 0
    authorizations = 0
    writes = 0

    def authorize(operation: str) -> None:
        nonlocal authorizations
        assert operation == "create_page"
        authorizations += 1

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal schema_reads, parent_reads, writes
        if request.url.path == "/v1/data_sources/ds":
            schema_reads += 1
            return httpx2.Response(
                200, json={"id": "ds", "properties": _schema_properties()}
            )
        if request.url.path == "/v1/pages/page":
            parent_reads += 1
            parent_id = "ds" if parent_reads == 1 else "other-source"
            return httpx2.Response(
                200,
                json={
                    "parent": {
                        "type": "data_source_id",
                        "data_source_id": parent_id,
                    }
                },
            )
        assert request.url.path == "/v1/blocks/page/children"
        writes += 1
        return httpx2.Response(429, headers={"Retry-After": "0"})

    # When / Then
    with (
        _client(httpx2.MockTransport(handler)) as client,
        pytest.raises(ContractError, match="does not match expected target"),
    ):
        _ = NotionApiTransport(
            client,
            root_blocks=(
                {
                    "type": "paragraph",
                    "paragraph": {"rich_text": []},
                },
            ),
            target_id="ds",
            enforce_schema=True,
            write_authorizer=authorize,
        ).append_page("page", 0, timeout_seconds=3)
    assert (schema_reads, parent_reads, authorizations, writes) == (2, 2, 1, 1)


def test_append_retry_rechecks_parent_and_authorizes_each_patch_attempt() -> None:
    # Given
    schema_reads = 0
    parent_reads = 0
    authorizations = 0
    writes = 0

    def authorize(operation: str) -> None:
        nonlocal authorizations
        assert operation == "create_page"
        authorizations += 1

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal schema_reads, parent_reads, writes
        if request.url.path == "/v1/data_sources/ds":
            schema_reads += 1
            return httpx2.Response(
                200, json={"id": "ds", "properties": _schema_properties()}
            )
        if request.url.path == "/v1/pages/page":
            parent_reads += 1
            return httpx2.Response(
                200,
                json={"parent": {"type": "data_source_id", "data_source_id": "ds"}},
            )
        assert request.url.path == "/v1/blocks/page/children"
        writes += 1
        if writes == 1:
            return httpx2.Response(429, headers={"Retry-After": "0"})
        return httpx2.Response(200, json={"id": "page"})

    # When
    with _client(httpx2.MockTransport(handler)) as client:
        result = NotionApiTransport(
            client,
            root_blocks=(
                {
                    "type": "paragraph",
                    "paragraph": {"rich_text": []},
                },
            ),
            target_id="ds",
            enforce_schema=True,
            write_authorizer=authorize,
        ).append_page("page", 0, timeout_seconds=3)

    # Then
    assert result == {"id": "page"}
    assert (schema_reads, parent_reads, authorizations, writes) == (2, 2, 2, 2)


def test_page_create_retry_stops_when_schema_changes_after_rate_limit() -> None:
    # Given
    schema_reads = 0
    authorizations = 0
    writes = 0

    def authorize(operation: str) -> None:
        nonlocal authorizations
        assert operation == "create_page"
        authorizations += 1

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal schema_reads, writes
        if request.url.path == "/v1/data_sources/ds":
            schema_reads += 1
            properties = _schema_properties()
            if schema_reads == 2:
                properties["제목"] = {"type": "rich_text"}
            return httpx2.Response(200, json={"id": "ds", "properties": properties})
        assert request.url.path == "/v1/pages"
        writes += 1
        return httpx2.Response(429, headers={"Retry-After": "0"})

    # When / Then
    with (
        _client(httpx2.MockTransport(handler)) as client,
        pytest.raises(ContractError, match="제목"),
    ):
        _ = NotionApiTransport(
            client,
            target_id="ds",
            enforce_schema=True,
            write_authorizer=authorize,
        ).create_page("ds", "run", "digest", (), timeout_seconds=3)
    assert (schema_reads, authorizations, writes) == (2, 1, 1)


def test_page_batches_respect_500kb_request_limit_for_korean_text() -> None:
    roots: tuple[JSONMap, ...] = tuple(
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [{"type": "text", "text": {"content": "가" * 2_000}}]
            },
        }
        for _index in range(100)
    )
    counts: list[int] = []
    sizes: list[int] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/v1/pages/page":
            return httpx2.Response(
                200,
                json={
                    "parent": {"type": "data_source_id", "data_source_id": "ds"}
                },
            )
        body = json.loads(request.content)
        counts.append(len(body["children"]))
        sizes.append(len(request.content))
        return httpx2.Response(200, json={"id": "page"})

    with _client(httpx2.MockTransport(handler)) as client:
        api = NotionApiTransport(client, root_blocks=roots, target_id="ds")
        _ = api.create_page("ds", "run", "digest", (), timeout_seconds=3)
        _ = api.append_page("page", counts[0], timeout_seconds=3)

    assert sum(counts) == 100
    assert counts[0] < 100
    assert all(size <= 500 * 1024 for size in sizes)


def test_page_rejects_single_root_over_1000_block_elements_before_network() -> None:
    rows: list[JSONValue] = [
        {
            "object": "block",
            "type": "table_row",
            "table_row": {"cells": []},
        }
        for _index in range(1_000)
    ]
    root: JSONMap = {
        "object": "block",
        "type": "table",
        "table": {"children": rows},
    }
    called = False

    def handler(_request: httpx2.Request) -> httpx2.Response:
        nonlocal called
        called = True
        return httpx2.Response(500)

    with (
        _client(httpx2.MockTransport(handler)) as client,
        pytest.raises(ContractError, match="request size or element limits"),
    ):
        _ = NotionApiTransport(client, root_blocks=(root,)).create_page(
            "ds", "run", "digest", (), timeout_seconds=3
        )
    assert called is False


def test_probe_reads_data_source_and_file_upload_capability() -> None:
    # Given
    paths: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        paths.append(request.url.path)
        if request.url.path == "/v1/data_sources/ds":
            return httpx2.Response(
                200, json={"id": "ds", "properties": _schema_properties()}
            )
        return httpx2.Response(200, json={"results": [], "has_more": False})

    transport = httpx2.MockTransport(handler)

    def factory(token: NotionApiToken) -> httpx2.Client:
        return create_notion_client(token, transport=transport)

    # When
    probe_notion_access(NotionApiToken("secret-token"), "ds", client_factory=factory)

    # Then
    assert paths == ["/v1/data_sources/ds", "/v1/file_uploads"]


def test_deferred_adapter_rejects_dry_run_before_loading_credentials(
    tmp_path: Path,
) -> None:
    # Given
    loaded = False

    def loader() -> NotionApiToken:
        nonlocal loaded
        loaded = True
        return NotionApiToken("secret-token")

    request = ExternalWriteRequest(
        root=tmp_path,
        manifest_path=tmp_path / "missing.json",
        run_log=tmp_path / "missing.jsonl",
        system=ExternalSystem.NOTION,
        gate="notion_write",
        run_id="run",
        target_id="ds",
        dry_run=True,
    )

    # When / Then
    with pytest.raises(ContractError, match="dry-run"):
        _ = NotionApiAdapter(token_loader=loader).write_and_verify(request)
    assert loaded is False


def test_read_retries_529_and_honors_retry_after() -> None:
    calls = 0
    waits: list[float] = []

    def handler(_request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx2.Response(529, headers={"Retry-After": "0.25"})
        return httpx2.Response(200, json={"ok": True})

    with _client(httpx2.MockTransport(handler)) as client:
        result = NotionHttp(client, sleeper=waits.append, clock=lambda: 0.0).read(
            "GET", "/v1/data_sources/ds", 2.0
        )
    assert result == {"ok": True}
    assert calls == 2
    assert waits == [0.25]


def test_create_retries_429_and_529_within_budget() -> None:
    responses = iter(
        [
            httpx2.Response(429, headers={"Retry-After": "0"}),
            httpx2.Response(200, json={"id": "created"}),
        ]
    )
    with _client(httpx2.MockTransport(lambda _request: next(responses))) as client:
        value = NotionHttp(
            client, sleeper=lambda _delay: None, clock=lambda: 0.0
        ).create("POST", "/v1/pages", 2.0, json_body={}, resource="page")
    assert value == {"id": "created"}

    calls = 0

    def uncertain(_request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        return httpx2.Response(529, headers={"Retry-After": "0"})

    with (
        _client(httpx2.MockTransport(uncertain)) as client,
        pytest.raises(NotionCreateUncertain),
    ):
        _ = NotionHttp(client, sleeper=lambda _delay: None, clock=lambda: 0.0).create(
            "POST", "/v1/pages", 2.0, json_body={}, resource="page"
        )
    assert calls == 4


def test_upload_send_retries_explicit_429_with_retry_after(tmp_path: Path) -> None:
    image = tmp_path / "image.png"
    _ = image.write_bytes(b"png")
    send_calls = 0
    waits: list[float] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal send_calls
        if request.url.path == "/v1/file_uploads":
            return httpx2.Response(200, json={"id": "upload"})
        send_calls += 1
        if send_calls == 1:
            return httpx2.Response(429, headers={"Retry-After": "0.2"})
        return httpx2.Response(200, json=_uploaded("upload", "image.png", b"png"))

    with _client(httpx2.MockTransport(handler)) as client:
        http = NotionHttp(client, sleeper=waits.append, clock=lambda: 0.0)
        result = NotionApiTransport(client, http=http).create_attachment(
            "ds",
            AttachmentSpec(
                image,
                "image.png",
                "thumbnail",
                hashlib.sha256(b"png").hexdigest(),
                1,
            ),
            timeout_seconds=2,
        )
    assert result == _uploaded("upload", "image.png", b"png")
    assert send_calls == 2
    assert waits == [0.2]


def test_upload_initialize_revalidates_schema_before_retry(tmp_path: Path) -> None:
    # Given
    image = tmp_path / "image.png"
    _ = image.write_bytes(b"png")
    schema_reads = 0
    initialize_posts = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal schema_reads, initialize_posts
        if request.url.path == "/v1/data_sources/ds":
            schema_reads += 1
            properties = _schema_properties()
            if schema_reads == 2:
                properties["검수 완료일"] = {"type": "rich_text"}
            return httpx2.Response(200, json={"id": "ds", "properties": properties})
        initialize_posts += 1
        return httpx2.Response(429, headers={"Retry-After": "0"})

    # When / Then
    with (
        _client(httpx2.MockTransport(handler)) as client,
        pytest.raises(ContractError, match="검수 완료일"),
    ):
        _ = NotionApiTransport(client, enforce_schema=True).initialize_attachment(
            "ds",
            AttachmentSpec(
                image,
                "image.png",
                "thumbnail",
                hashlib.sha256(b"png").hexdigest(),
                1,
            ),
            timeout_seconds=2,
        )
    assert schema_reads == 2
    assert initialize_posts == 1


def test_pending_upload_is_completed_without_duplicate_create(tmp_path: Path) -> None:
    # Given
    image = tmp_path / "image.png"
    _ = image.write_bytes(b"original")
    paths: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        paths.append(request.url.path)
        assert b"original" in request.content
        return httpx2.Response(
            200, json=_uploaded("pending", "image.png", b"original")
        )

    # When
    with _client(httpx2.MockTransport(handler)) as client:
        completed = NotionApiTransport(client).complete_attachment(
            "ds",
            "pending",
            AttachmentSpec(
                image,
                "image.png",
                "thumbnail",
                hashlib.sha256(b"original").hexdigest(),
                1,
            ),
            timeout_seconds=2,
        )

    # Then
    assert completed == _uploaded("pending", "image.png", b"original")
    assert paths == ["/v1/file_uploads/pending/send"]


def test_pending_upload_requires_uploaded_status_after_send(tmp_path: Path) -> None:
    # Given
    image = tmp_path / "image.png"
    _ = image.write_bytes(b"original")

    # When / Then
    with (
        _client(
            httpx2.MockTransport(
                lambda _request: httpx2.Response(
                    200, json={"id": "pending", "status": "pending"}
                )
            )
        ) as client,
        pytest.raises(ContractError, match="did not complete"),
    ):
        _ = NotionApiTransport(client).complete_attachment(
            "ds",
            "pending",
            AttachmentSpec(
                image,
                "image.png",
                "thumbnail",
                hashlib.sha256(b"original").hexdigest(),
                1,
            ),
            timeout_seconds=2,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("filename", "other.png"),
        ("content_type", "application/octet-stream"),
        ("content_length", 999),
        ("content_length", True),
    ],
)
def test_completed_upload_rejects_metadata_mismatch_before_use(
    tmp_path: Path, field: str, value: str | int | bool
) -> None:
    # Given
    image = tmp_path / "image.png"
    _ = image.write_bytes(b"original")
    payload = _uploaded("pending", "image.png", b"original")
    payload[field] = value

    # When / Then
    with (
        _client(
            httpx2.MockTransport(
                lambda _request: httpx2.Response(200, json=payload)
            )
        ) as client,
        pytest.raises(ContractError, match="metadata"),
    ):
        _ = NotionApiTransport(client).complete_attachment(
            "ds",
            "pending",
            AttachmentSpec(
                image,
                "image.png",
                "thumbnail",
                hashlib.sha256(b"original").hexdigest(),
                1,
            ),
            timeout_seconds=2,
        )


def test_uncertain_pending_send_is_completed_on_retry_without_create(
    tmp_path: Path,
) -> None:
    # Given
    image = tmp_path / "image.png"
    _ = image.write_bytes(b"original")
    sends = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal sends
        assert request.url.path == "/v1/file_uploads/pending/send"
        sends += 1
        if sends == 1:
            return httpx2.Response(500)
        return httpx2.Response(
            200, json=_uploaded("pending", "image.png", b"original")
        )

    spec = AttachmentSpec(
        image,
        "image.png",
        "thumbnail",
        hashlib.sha256(b"original").hexdigest(),
        1,
    )

    # When / Then
    with _client(httpx2.MockTransport(handler)) as client:
        transport = NotionApiTransport(client)
        with pytest.raises(NotionCreateUncertain):
            _ = transport.complete_attachment("ds", "pending", spec, timeout_seconds=2)
        completed = transport.complete_attachment(
            "ds", "pending", spec, timeout_seconds=2
        )
    assert completed == _uploaded("pending", "image.png", b"original")
    assert sends == 2


def test_pending_send_revalidates_authorizes_and_uses_verified_bytes_on_retry(
    tmp_path: Path,
) -> None:
    # Given
    image = tmp_path / "image.png"
    _ = image.write_bytes(b"original")
    authorizations = 0
    schema_reads = 0
    sends = 0

    def authorize(operation: str) -> None:
        nonlocal authorizations
        assert operation == "create_attachment"
        authorizations += 1
        _ = image.write_bytes(b"mutated")

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal schema_reads, sends
        if request.url.path == "/v1/data_sources/ds":
            schema_reads += 1
            return httpx2.Response(
                200, json={"id": "ds", "properties": _schema_properties()}
            )
        assert request.url.path == "/v1/file_uploads/pending/send"
        assert b"original" in request.content
        assert b"mutated" not in request.content
        sends += 1
        if sends == 1:
            return httpx2.Response(429, headers={"Retry-After": "0"})
        return httpx2.Response(
            200, json=_uploaded("pending", "image.png", b"original")
        )

    # When
    with _client(httpx2.MockTransport(handler)) as client:
        completed = NotionApiTransport(
            client, enforce_schema=True, write_authorizer=authorize
        ).complete_attachment(
            "ds",
            "pending",
            AttachmentSpec(
                image,
                "image.png",
                "thumbnail",
                hashlib.sha256(b"original").hexdigest(),
                1,
            ),
            timeout_seconds=2,
        )

    # Then
    assert completed == _uploaded("pending", "image.png", b"original")
    assert authorizations == 2
    assert schema_reads == 2


def test_schema_validation_fails_closed_on_required_type_mismatch() -> None:
    properties = _schema_properties()
    properties["검수 완료일"] = {"type": "rich_text"}
    with (
        _client(
            httpx2.MockTransport(
                lambda _request: httpx2.Response(
                    200, json={"id": "ds", "properties": properties}
                )
            )
        ) as client,
        pytest.raises(ContractError, match="검수 완료일"),
    ):
        NotionApiTransport(client).validate_schema("ds", timeout_seconds=2)


def test_prefix_comparison_ignores_response_default_rich_text_fields() -> None:
    request: JSONMap = {
        "type": "paragraph",
        "paragraph": {"rich_text": [{"type": "text", "text": {"content": "same"}}]},
    }
    response: JSONMap = {
        "type": "paragraph",
        "paragraph": {
            "rich_text": [
                {
                    "type": "text",
                    "plain_text": "same",
                    "href": None,
                    "annotations": {
                        "bold": False,
                        "italic": False,
                        "strikethrough": False,
                    },
                    "text": {"content": "same", "link": None},
                }
            ]
        },
    }
    assert canonical_block(request) == canonical_block(response)


def test_reused_upload_identity_is_rendered_into_created_page(tmp_path: Path) -> None:
    copy = tmp_path / "copy.md"
    _ = copy.write_text(
        '[TITLE] 제목 [/TITLE]\n[IMAGE file="thumbnail.png" alt="대표" representative=true]',
        encoding="utf-8",
    )
    parsed = parse_naver_copy(copy)
    posted: JSONMap = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        posted.update(json.loads(request.content))
        return httpx2.Response(200, json={"id": "page"})

    with _client(httpx2.MockTransport(handler)) as client:
        _ = NotionApiTransport(
            client, parsed=parsed, image_filenames=("thumbnail.png",)
        ).create_page("ds", "run", "digest", ("reused-upload",), timeout_seconds=2)
    rendered = posted["children"]
    assert isinstance(rendered, list)
    image = rendered[0]
    assert isinstance(image, dict)
    image_payload = image.get("image")
    assert isinstance(image_payload, dict)
    upload = image_payload.get("file_upload")
    assert isinstance(upload, dict)
    assert upload.get("id") == "reused-upload"


def test_fetch_page_rejects_wrong_signed_image_bytes() -> None:
    digest = "0" * 64

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/v1/pages/page":
            return httpx2.Response(200, json={"properties": {"제목": {"title": []}}})
        if request.url.path == "/v1/blocks/page/children":
            return httpx2.Response(
                200,
                json={
                    "results": [
                        {
                            "type": "image",
                            "image": {
                                "type": "file",
                                "file": {"url": "https://files.example/signed"},
                                "caption": [],
                            },
                        }
                    ],
                    "has_more": False,
                },
            )
        return httpx2.Response(200, content=b"wrong")

    with (
        _client(httpx2.MockTransport(handler)) as client,
        pytest.raises(ContractError, match="hash"),
    ):
        _ = NotionApiTransport(
            client,
            asset_metadata=(AssetMetadata("thumbnail", digest, 1),),
            image_filenames=("thumbnail.png",),
        ).fetch_page("page", timeout_seconds=2)


def test_run_metrics_count_per_stage_retries_and_reject_conflicting_batch(
    tmp_path: Path,
) -> None:
    log = tmp_path / "run.jsonl"
    _ = log.write_text(
        "\n".join(
            json.dumps(value)
            for value in (
                {"batch_id": "batch", "stage": "researcher", "attempt": 1},
                {"batch_id": "batch", "stage": "researcher", "attempt": 2},
                {"batch_id": "batch", "stage": "researcher", "attempt": 3},
                {"batch_id": "batch", "stage": "writer", "attempt": 2},
                {
                    "batch_id": "batch",
                    "stage": "content-assembler",
                    "status": "passed",
                    "attempt": 1,
                    "telemetry_version": 2,
                    "duration_ms": 1250.0,
                    "started_at": "2026-08-31T23:00:00+09:00",
                    "ended_at": "2026-08-31T23:59:58+09:00",
                },
            )
        ),
        encoding="utf-8",
    )
    assert run_metrics(log) == (
        "batch",
        3,
        "2026-08-31T23:59:58+09:00",
        1.25,
    )
    _ = log.write_text('{"batch_id":"one"}\n{"batch_id":"two"}', encoding="utf-8")
    with pytest.raises(ContractError, match="conflicting batch_id"):
        _ = run_metrics(log)


@pytest.mark.parametrize("duration", [float("nan"), float("inf"), float("-inf")])
def test_run_metrics_rejects_nonfinite_duration(
    tmp_path: Path, duration: float
) -> None:
    log = tmp_path / "run.jsonl"
    _ = log.write_text(
        json.dumps(
            {
                "batch_id": "batch",
                "stage": "content-assembler",
                "status": "passed",
                "ended_at": "2026-08-31T23:59:58+09:00",
                "telemetry_version": 2,
                "duration_ms": duration,
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ContractError, match="duration_ms is malformed"):
        _ = run_metrics(log)


def test_page_properties_use_measured_operational_inputs(tmp_path: Path) -> None:
    del tmp_path
    files = (
        ManifestFile("final_markdown", "final/topic.md", 1, 12, "a" * 64),
        ManifestFile("naver_layout", "final/topic-naver-layout.md", 2, 1, "b" * 64),
        ManifestFile("naver_copy", "final/topic-naver-copy.md", 3, 1, "c" * 64),
        ManifestFile("naver_input", "final/topic-naver-input.md", 4, 1, "d" * 64),
        ManifestFile("image_map", "assets/topic/image-map.md", 5, 1, "e" * 64),
        ManifestFile("thumbnail", "assets/topic/thumbnail.png", 6, 3, "f" * 64),
    )
    manifest = Manifest(
        "workflow-contract-v1",
        "workflow-optimized-v1",
        "run",
        "topic",
        "formal",
        "2026-08-31T09:00:00+09:00",
        files,
        "sha256:" + "0" * 64,
    )
    request = ExternalWriteRequest(
        root=Path("."),
        manifest_path=Path("manifest.json"),
        run_log=Path("run.jsonl"),
        system=ExternalSystem.NOTION,
        gate="notion_write",
        run_id="run",
        target_id="ds",
        dry_run=False,
    )

    properties = page_properties(
        manifest,
        request,
        "네이버-블로그-글쓰기-2026-08-31-2차수",
        2,
        4321,
        batch_id="batch-7",
        retries=3,
        completed_at="2026-08-31T23:59:58+09:00",
        duration_seconds=1.25,
    )

    assert properties["검수 완료일"] == {"date": {"start": "2026-08-31T23:59:58+09:00"}}
    assert properties["본문 문자수"] == {"number": 4321}
    assert properties["배치 ID"] == {
        "rich_text": [{"type": "text", "text": {"content": "batch-7"}}]
    }
    assert properties["재시도 횟수"] == {"number": 3}
    assert properties["전체 처리 시간(초)"] == {"number": 1.25}
    assert properties["검수 메모"] == {
        "rich_text": [
            {
                "type": "text",
                "text": {
                    "content": (
                        f"artifact_digest={manifest.artifact_digest}; "
                        "Q1=passed; stage=content-assembler"
                    )
                },
            }
        ]
    }


def test_restart_reuses_existing_operational_identity() -> None:
    match: JSONMap = {
        "id": "page",
        "title": "네이버-블로그-글쓰기-2026-08-31-7차수",
        "execution_date": "2026-08-31",
        "sequence": 7,
    }
    assert existing_identity((match,), "2026-08-31") == (
        "네이버-블로그-글쓰기-2026-08-31-7차수",
        7,
    )
    match["sequence"] = 8
    with pytest.raises(ContractError, match="identity is malformed"):
        _ = existing_identity((match,), "2026-08-31")
    match["sequence"] = True
    with pytest.raises(ContractError, match="identity is malformed"):
        _ = existing_identity((match,), "2026-08-31")


def test_q2_rejects_actual_remote_property_mismatch() -> None:
    properties: JSONMap = {
        "제목": {"title": [{"type": "text", "text": {"content": "title"}}]},
        "모드": {"select": {"name": "정식"}},
        "실행 ID": {"rich_text": [{"type": "text", "text": {"content": "run"}}]},
        "차수": {"number": 1},
    }

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/v1/pages/page":
            return httpx2.Response(
                200,
                json={
                    "properties": {
                        "제목": {"title": [{"plain_text": "title"}]},
                        "모드": {"select": {"name": "베타"}},
                        "실행 ID": {"rich_text": [{"plain_text": "wrong"}]},
                        "차수": {"number": 2},
                    }
                },
            )
        return httpx2.Response(200, json={"results": [], "has_more": False})

    with _client(httpx2.MockTransport(handler)) as client:
        actual = NotionApiTransport(client, page_properties=properties).fetch_page(
            "page", timeout_seconds=2
        )
    expected: JSONMap = {"title": "title", "properties": properties, "blocks": []}
    with pytest.raises(ContractError):
        _ = verify_notion_round_trip(
            expected, actual, "page", "2026-08-31T23:59:59+09:00", "digest"
        )


def test_attachment_hash_mismatch_fails_before_network(tmp_path: Path) -> None:
    image = tmp_path / "changed.png"
    _ = image.write_bytes(b"changed")
    called = False

    def handler(_request: httpx2.Request) -> httpx2.Response:
        nonlocal called
        called = True
        return httpx2.Response(500)

    with (
        _client(httpx2.MockTransport(handler)) as client,
        pytest.raises(ContractError, match="hash does not match"),
    ):
        _ = NotionApiTransport(client).create_attachment(
            "ds",
            AttachmentSpec(image, "changed.png", "thumbnail", "0" * 64, 1),
            timeout_seconds=2,
        )
    assert called is False


def test_signed_image_download_strips_api_credentials() -> None:
    content = b"verified"

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert "Authorization" not in request.headers
        assert "Notion-Version" not in request.headers
        return httpx2.Response(200, content=content)

    with _client(httpx2.MockTransport(handler)) as client:
        verify_bytes(
            client,
            "https://storage.example/signed",
            AssetMetadata("thumbnail", hashlib.sha256(content).hexdigest(), 1),
            2,
        )


def test_q2_rejects_file_upload_image_without_download_url() -> None:
    block: JSONMap = {
        "type": "image",
        "image": {"type": "file_upload", "file_upload": {"id": "upload"}},
    }

    with (
        _client(httpx2.MockTransport(lambda _request: httpx2.Response(500))) as client,
        pytest.raises(ContractError, match="not finalized for Q2"),
    ):
        verify_image(
            client,
            block,
            AssetMetadata("thumbnail", hashlib.sha256(b"verified").hexdigest(), 1),
            2,
        )


def test_upload_revalidates_schema_immediately_before_send(tmp_path: Path) -> None:
    image = tmp_path / "image.png"
    _ = image.write_bytes(b"png")
    paths: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        paths.append(request.url.path)
        if request.url.path == "/v1/data_sources/ds":
            return httpx2.Response(
                200, json={"id": "ds", "properties": _schema_properties()}
            )
        if request.url.path == "/v1/file_uploads":
            return httpx2.Response(200, json={"id": "upload"})
        return httpx2.Response(200, json=_uploaded("upload", "image.png", b"png"))

    with _client(httpx2.MockTransport(handler)) as client:
        _ = NotionApiTransport(client, enforce_schema=True).create_attachment(
            "ds",
            AttachmentSpec(
                image,
                "image.png",
                "thumbnail",
                hashlib.sha256(b"png").hexdigest(),
                1,
            ),
            timeout_seconds=2,
        )
    assert paths == [
        "/v1/data_sources/ds",
        "/v1/file_uploads",
        "/v1/data_sources/ds",
        "/v1/file_uploads/upload/send",
    ]


def test_upload_rejects_path_mutation_between_initialize_and_send(
    tmp_path: Path,
) -> None:
    image = tmp_path / "image.png"
    _ = image.write_bytes(b"original")
    sent = False

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal sent
        if request.url.path == "/v1/file_uploads":
            _ = image.write_bytes(b"mutated")
            return httpx2.Response(200, json={"id": "upload"})
        sent = True
        assert b"original" in request.content
        assert b"mutated" not in request.content
        return httpx2.Response(
            200, json=_uploaded("upload", "image.png", b"original")
        )

    with (
        _client(httpx2.MockTransport(handler)) as client,
        pytest.raises(ContractError, match="hash does not match"),
    ):
        _ = NotionApiTransport(client).create_attachment(
            "ds",
            AttachmentSpec(
                image,
                "image.png",
                "thumbnail",
                hashlib.sha256(b"original").hexdigest(),
                1,
            ),
            timeout_seconds=2,
        )
    assert sent is False


def test_concrete_authorizer_failure_before_send_prevents_send(tmp_path: Path) -> None:
    image = tmp_path / "image.png"
    _ = image.write_bytes(b"original")
    authorizations = 0
    sent = False

    def authorize(_operation: str) -> None:
        nonlocal authorizations
        authorizations += 1
        if authorizations == 2:
            raise ContractError("authorization denied")

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal sent
        if request.url.path == "/v1/file_uploads":
            return httpx2.Response(200, json={"id": "upload"})
        sent = True
        return httpx2.Response(200, json={"id": "upload"})

    with (
        _client(httpx2.MockTransport(handler)) as client,
        pytest.raises(ContractError, match="authorization denied"),
    ):
        _ = NotionApiTransport(client, write_authorizer=authorize).create_attachment(
            "ds",
            AttachmentSpec(
                image,
                "image.png",
                "thumbnail",
                hashlib.sha256(b"original").hexdigest(),
                1,
            ),
            timeout_seconds=2,
        )
    assert authorizations == 2
    assert sent is False


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
