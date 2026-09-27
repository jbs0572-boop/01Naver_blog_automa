from __future__ import annotations

import json
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from tools.contract_types import JSONMap
from tools.dashboard_manual_batch import new_batch
from tools.dashboard_manual_request import parse_manual_run_payload
from tools.dashboard_manual_store import ManualBatchStore
from tools.test_dashboard import DashboardServer, DashboardServerConfig


@dataclass(frozen=True, slots=True)
class Reply:
    status: HTTPStatus
    body: JSONMap


def request_json(base_url: str, path: str, *, method: str = "GET", body: JSONMap | None = None) -> Reply:
    encoded = json.dumps(body).encode() if body is not None else None
    request = Request(f"{base_url}{path}", data=encoded, method=method, headers={"Content-Type": "application/json"} if encoded else {})
    try:
        with urlopen(request) as response:
            return Reply(HTTPStatus(response.status), json.loads(response.read()))
    except HTTPError as error:
        return Reply(HTTPStatus(error.code), json.loads(error.read()))


def test_cancel_endpoint_is_idempotent_and_rejects_stale_nonce(tmp_path: Path) -> None:
    server = DashboardServer(DashboardServerConfig(("127.0.0.1", 0), tmp_path))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://{server.server_address[0]}:{server.server_port}"
    try:
        batch = new_batch(parse_manual_run_payload({"keyword": "API 취소", "as_of_date": "2026-09-10"}))
        ManualBatchStore(tmp_path).save(batch)
        child = batch.children[0]
        assert child.cancel_action is not None
        path = f"/api/manual-run/{batch.batch_id}/children/{child.child_id}/cancel"
        body: JSONMap = {
            "nonce": child.cancel_action.nonce,
            "scope": "queued_only",
        }
        stale_body: JSONMap = {"nonce": "stale", "scope": "queued_only"}
        first = request_json(base_url, path, method="POST", body=body)
        second = request_json(base_url, path, method="POST", body=body)
        stale = request_json(base_url, path, method="POST", body=stale_body)
        assert first.status == HTTPStatus.OK
        assert second.status == HTTPStatus.OK
        assert first.body["children"] == second.body["children"]
        assert stale.status == HTTPStatus.CONFLICT
        tasks = request_json(base_url, "/api/tasks")
        assert tasks.status == HTTPStatus.OK
        items = tasks.body["items"]
        assert isinstance(items, list) and items
        first_item = items[0]
        assert isinstance(first_item, dict)
        assert first_item["effective_status"] == "cancelled"
    finally:
        server.shutdown()
        thread.join(timeout=1)
        server.server_close()
