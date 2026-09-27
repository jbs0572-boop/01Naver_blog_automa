from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx2

from tools.contract_types import ContractError, JSONMap
from tools.external_adapter import (
    ExternalSystem,
    ExternalWriteRequest,
    NotionWriteOperation,
    authorize_notion_operation,
)
from tools.manifest import verify_manifest
from tools.notion_api_http import (
    NOTION_VERSION,
    NotionClientConfig,
    create_notion_client,
)
from tools.notion_api_metrics import run_metrics
from tools.notion_api_payload import asset_metadata, kst_date, page_properties
from tools.notion_api_transport import NotionApiTransport
from tools.notion_content import expected_blocks, parse_naver_copy
from tools.notion_keychain import NotionApiToken, load_notion_api_token
from tools.notion_resume import ResumableNotionAdapter


def probe_notion_access(
    token: NotionApiToken,
    target_id: str,
    *,
    client_factory: Callable[[NotionApiToken], httpx2.Client] = create_notion_client,
) -> None:
    with client_factory(token) as client:
        transport = NotionApiTransport(client)
        transport.validate_schema(target_id, timeout_seconds=10.0)
        _ = transport.find_attachments(
            target_id, "__capability_probe__", timeout_seconds=10.0
        )


@dataclass(frozen=True, slots=True)
class NotionApiAdapter:
    token_loader: Callable[[], NotionApiToken] = load_notion_api_token
    client_factory: Callable[[NotionApiToken], httpx2.Client] = create_notion_client

    def write_and_verify(self, request: ExternalWriteRequest) -> JSONMap:
        if request.dry_run:
            raise ContractError("Notion API adapter rejects dry-run before credentials")
        if request.system is not ExternalSystem.NOTION:
            raise ContractError("Notion API adapter only accepts Notion writes")
        manifest = verify_manifest(request.root, request.manifest_path)
        copy_entry = next(
            (entry for entry in manifest.files if entry.role == "naver_copy"), None
        )
        if copy_entry is None:
            raise ContractError("manifest is missing naver_copy")
        parsed = parse_naver_copy(request.root / copy_entry.path)
        assets = asset_metadata(manifest)
        final_entry = next(
            entry for entry in manifest.files if entry.role == "final_markdown"
        )
        try:
            body_character_count = len(
                (request.root / final_entry.path).read_text(encoding="utf-8")
            )
        except OSError as error:
            raise ContractError("final markdown could not be read") from error
        token = self.token_loader()
        with self.client_factory(token) as client:
            bootstrap = NotionApiTransport(client)
            bootstrap.validate_schema(
                request.target_id, timeout_seconds=request.notion_timeout_seconds
            )
            date = kst_date(manifest.created_at)
            matches = bootstrap.find_pages(
                request.target_id,
                request.run_id,
                manifest.artifact_digest,
                timeout_seconds=request.notion_timeout_seconds,
            )
            completed_by: str | None = None
            if matches:
                title, sequence = existing_identity(matches, date)
                created_time = matches[0].get("created_time")
                if not isinstance(created_time, str) or not created_time:
                    raise ContractError("Notion reconciled page created_time is missing")
                completed_by = created_time
            else:
                sequence = bootstrap.next_sequence(
                    request.target_id,
                    date,
                    timeout_seconds=request.notion_timeout_seconds,
                )
                title = f"네이버-블로그-글쓰기-{date}-{sequence}차수"
            batch_id, retries, completed_at, duration = run_metrics(
                request.run_log, completed_by=completed_by
            )
            properties = page_properties(
                manifest,
                request,
                title,
                sequence,
                body_character_count,
                batch_id=batch_id,
                retries=retries,
                completed_at=completed_at,
                duration_seconds=duration,
            )

            def authorize(operation: NotionWriteOperation) -> None:
                verified = authorize_notion_operation(request, operation)
                if verified.get("verified_artifact_digest") != manifest.artifact_digest:
                    raise ContractError(
                        "Notion API authorization digest does not match manifest"
                    )

            transport = NotionApiTransport(
                client,
                page_properties=properties,
                parsed=parsed,
                target_id=request.target_id,
                enforce_schema=True,
                asset_metadata=tuple(assets.values()),
                image_filenames=tuple(assets),
                write_authorizer=authorize,
            )
            expected = expected_blocks(parsed, assets)
            body = expected.get("blocks")
            if not isinstance(body, list):
                raise ContractError("expected Notion blocks are invalid")
            expected["title"] = title
            expected["properties"] = properties
            expected["blocks"] = body
            return ResumableNotionAdapter(
                transport,
                expected,
                lambda: datetime.now(tz=ZoneInfo("Asia/Seoul")),
                attachment_binder=transport.bind_attachment_ids,
            ).write_and_verify(request)


def existing_identity(matches: tuple[JSONMap, ...], date: str) -> tuple[str, int]:
    if len(matches) != 1:
        raise ContractError("Notion page reconciliation is ambiguous")
    match = matches[0]
    title, sequence, execution_date = (
        match.get("title"),
        match.get("sequence"),
        match.get("execution_date"),
    )
    pattern = rf"네이버-블로그-글쓰기-{re.escape(date)}-([1-9][0-9]*)차수"
    if isinstance(sequence, bool):
        raise ContractError("Notion reconciled page identity is malformed")
    found = re.fullmatch(pattern, title) if isinstance(title, str) else None
    if (
        found is None
        or not isinstance(sequence, int)
        or sequence < 1
        or int(found.group(1)) != sequence
        or execution_date != date
    ):
        raise ContractError("Notion reconciled page identity is malformed")
    assert isinstance(title, str)
    assert isinstance(sequence, int) and not isinstance(sequence, bool)
    return title, sequence


__all__ = [
    "NOTION_VERSION",
    "NotionApiAdapter",
    "NotionApiTransport",
    "NotionClientConfig",
    "create_notion_client",
    "existing_identity",
    "probe_notion_access",
    "run_metrics",
]
