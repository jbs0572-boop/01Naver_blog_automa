from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from threading import Event, Thread
from time import monotonic
from typing import Protocol
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from tools.contract_types import JSONMap, JSONValue
from tools.dashboard_manual_run import parse_manual_run_payload
from tools.external_adapter import ExternalWriteRequest
from tools.runner_types import RunnerRequest, RunnerResult, RunStatus
from tools.test_dashboard import (
    DashboardAlreadyRunningError,
    DashboardServer,
    DashboardServerConfig,
    DashboardServerDependencies,
    dashboard_instance_lock,
)


class FixtureNotionAdapter:
    def write_and_verify(self, request: ExternalWriteRequest) -> JSONMap:
        raise AssertionError(f"unexpected external write: {request}")


class FixtureNaverAdapter:
    @property
    def target_blog_id(self) -> str:
        return "blog-fixture"

    def prepare(self, title: str, body: str, artifact_digest: str) -> JSONMap:
        _ = (title, body, artifact_digest)
        raise AssertionError("unexpected Naver preparation")

    def save(self, title: str, artifact_digest: str) -> JSONMap:
        _ = (title, artifact_digest)
        raise AssertionError("unexpected Naver draft save")


def test_dashboard_instance_lock_rejects_a_second_process(tmp_path: Path) -> None:
    with (
        dashboard_instance_lock(tmp_path),
        pytest.raises(DashboardAlreadyRunningError, match="already running"),
        dashboard_instance_lock(tmp_path),
    ):
        raise AssertionError("second dashboard instance must not start")


def test_dashboard_remains_readable_when_external_adapters_are_unavailable(
    tmp_path: Path,
) -> None:
    server, thread, base_url = _start_server(
        tmp_path,
        dependencies=DashboardServerDependencies(
            external_adapter_error="external_adapters_unavailable"
        ),
    )
    try:
        health = _json_request(base_url, "/api/health")
        check = _json_request(base_url, "/api/health/check", method="POST", body={})
        history = _json_request(base_url, "/api/manual-runs")
    finally:
        _stop_server(server, thread)

    assert health.status is HTTPStatus.OK
    assert health.body["status"] == "failed"
    assert health.body["error_code"] == "external_adapters_unavailable"
    assert check.status is HTTPStatus.SERVICE_UNAVAILABLE
    assert check.body["error_code"] == "external_adapters_unavailable"
    assert history.status is HTTPStatus.OK


class JsonReadable(Protocol):
    def read(self, n: int = -1) -> bytes: ...


def _read_json(response: JsonReadable) -> JSONMap:
    value: JSONValue = json.loads(response.read())
    assert isinstance(value, dict)
    return value


@dataclass(frozen=True, slots=True)
class JsonReply:
    status: HTTPStatus
    body: JSONMap


def _start_server(
    root: Path,
    *,
    dependencies: DashboardServerDependencies | None = None,
) -> tuple[DashboardServer, Thread, str]:
    if dependencies is None:
        def local_runner(request: RunnerRequest) -> RunnerResult:
            return RunnerResult(
                request.run_id or "RUN-fixture",
                RunStatus.LOCAL_ONLY,
                root / "state.json",
                root / "run.jsonl",
                (),
                "fixture completed",
            )

        dependencies = DashboardServerDependencies(runner=local_runner)
    server = DashboardServer(
        DashboardServerConfig(("127.0.0.1", 0), root), dependencies
    )
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, f"http://{server.server_address[0]}:{server.server_port}"


def _stop_server(server: DashboardServer, thread: Thread) -> None:
    server.shutdown()
    thread.join(timeout=1)
    server.server_close()


def _dashboard_csrf_token(base_url: str) -> str:
    request = Request(base_url)
    with urlopen(request) as response:
        html = response.read().decode("utf-8")
    match = re.search(
        r'<meta name="dashboard-csrf-token" content="([A-Za-z0-9_-]+)">',
        html,
    )
    if match is None:
        raise AssertionError("dashboard HTML did not expose its CSRF token")
    return match.group(1)


def _json_request(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    body: JSONMap | None = None,
    headers: dict[str, str] | None = None,
    with_csrf: bool = True,
) -> JsonReply:
    encoded = json.dumps(body).encode("utf-8") if body is not None else None
    request_headers = (
        {"Content-Type": "application/json"} if encoded is not None else {}
    )
    request_headers.update(headers or {})
    if method.upper() == "POST" and with_csrf:
        _ = request_headers.setdefault("X-Dashboard-CSRF", _dashboard_csrf_token(base_url))
    request = Request(
        f"{base_url}{path}",
        data=encoded,
        headers=request_headers,
        method=method,
    )
    try:
        with urlopen(request) as response:
            return JsonReply(HTTPStatus(response.status), _read_json(response))
    except HTTPError as error:
        return JsonReply(HTTPStatus(error.code), _read_json(error))


def _write_dashboard_run(root: Path, run_id: str) -> None:
    path = root / "runs" / f"{run_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(
        json.dumps(
            {
                "event_type": "stage",
                "run_id": run_id,
                "topic_id": "TOPIC-http",
                "keyword": "상세 조회",
                "stage": "content-assembler",
                "status": "passed",
                "ended_at": "2026-09-01T00:00:00+09:00",
                "quality": {"q1": "passed"},
            }
        )
        + "\n",
        encoding="utf-8",
    )


def _post_manual_run(base_url: str, body: JSONMap) -> JsonReply:
    return _json_request(
        base_url,
        "/api/manual-run",
        method="POST",
        body={**body, "request_nonce": str(uuid.uuid4())},
    )


def _children(batch: JSONMap) -> list[JSONMap]:
    raw_children = batch["children"]
    assert isinstance(raw_children, list)
    children: list[JSONMap] = []
    for child in raw_children:
        assert isinstance(child, dict)
        children.append(child)
    return children


def _child_id(child: JSONMap) -> str:
    identifier = child["child_id"]
    assert isinstance(identifier, str)
    return identifier


def _batch_id(batch: JSONMap) -> str:
    identifier = batch["batch_id"]
    assert isinstance(identifier, str)
    return identifier


def _action_nonce(child: JSONMap) -> str:
    raw_action = child["next_action"]
    assert isinstance(raw_action, dict)
    nonce = raw_action["nonce"]
    assert isinstance(nonce, str)
    return nonce


def _poll_child_with_action(base_url: str, batch_id: str, child_id: str) -> JSONMap:
    deadline = monotonic() + 1
    while monotonic() < deadline:
        reply = _json_request(base_url, f"/api/manual-run/{batch_id}")
        assert reply.status is HTTPStatus.OK
        for child in _children(reply.body):
            if _child_id(child) == child_id and child["next_action"] is not None:
                return child
    raise AssertionError("manual child action did not become available")


@pytest.mark.parametrize(
    ("headers", "expected_status"),
    [
        (
            {"Origin": "http://attacker.invalid", "Content-Type": "text/plain"},
            HTTPStatus.FORBIDDEN,
        ),
        (
            {"Origin": "http://attacker.invalid", "Content-Type": "application/json"},
            HTTPStatus.FORBIDDEN,
        ),
        (
            {"Host": "attacker.invalid", "Content-Type": "application/json"},
            HTTPStatus.FORBIDDEN,
        ),
        (
            {"Content-Type": "application/json", "X-Dashboard-CSRF": "invalid"},
            HTTPStatus.FORBIDDEN,
        ),
        ({"Content-Type": "text/plain"}, HTTPStatus.UNSUPPORTED_MEDIA_TYPE),
    ],
)
def test_http_rejects_cross_origin_or_non_json_mutations(
    tmp_path: Path,
    headers: dict[str, str],
    expected_status: HTTPStatus,
) -> None:
    server, thread, base_url = _start_server(tmp_path)
    try:
        reply = _json_request(
            base_url,
            "/api/manual-run",
            method="POST",
            body={
                "auto_topic": True,
                "as_of_date": "2026-09-07",
                "request_nonce": str(uuid.uuid4()),
            },
            headers=headers,
            with_csrf=False,
        )
        assert reply.status is expected_status
        listed = _json_request(base_url, "/api/manual-runs")
        assert listed.status is HTTPStatus.OK
        assert listed.body["batches"] == []
    finally:
        _stop_server(server, thread)



def test_http_rejects_non_loopback_host_on_get(tmp_path: Path) -> None:
    server, thread, base_url = _start_server(tmp_path)
    try:
        reply = _json_request(
            base_url,
            "/api/manual-runs",
            headers={"Host": "attacker.invalid"},
        )
        assert reply.status is HTTPStatus.FORBIDDEN
    finally:
        _stop_server(server, thread)


def test_http_accepts_same_origin_json_mutation(tmp_path: Path) -> None:
    server, thread, base_url = _start_server(tmp_path)
    try:
        reply = _json_request(
            base_url,
            "/api/manual-run",
            method="POST",
            body={
                "auto_topic": True,
                "as_of_date": "2026-09-07",
                "request_nonce": str(uuid.uuid4()),
            },
            headers={"Origin": base_url},
        )
        assert reply.status is HTTPStatus.ACCEPTED
        assert len(_children(reply.body)) == 1
    finally:
        _stop_server(server, thread)


def test_http_auto_post_returns_one_child_batch(tmp_path: Path) -> None:
    # Given
    server, thread, base_url = _start_server(tmp_path)
    try:
        # When
        reply = _post_manual_run(
            base_url, {"auto_topic": True, "as_of_date": "2026-09-07"}
        )

        # Then
        assert reply.status is HTTPStatus.ACCEPTED
        assert reply.body["topic_source"] == "auto_selected"
        children = _children(reply.body)
        assert len(children) == 1
        assert [_child_id(child) for child in children] == list(
            dict.fromkeys(_child_id(child) for child in children)
        )
    finally:
        _stop_server(server, thread)


def test_http_plural_manual_runs_post_starts_one_child_batch(tmp_path: Path) -> None:
    # Given
    server, thread, base_url = _start_server(tmp_path)
    try:
        # When
        reply = _json_request(
            base_url,
            "/api/manual-runs",
            method="POST",
            body={"auto_topic": True, "as_of_date": "2026-09-07", "request_nonce": str(uuid.uuid4())},
        )

        # Then
        assert reply.status is HTTPStatus.OK
        assert len(_children(reply.body)) == 1
    finally:
        _stop_server(server, thread)


def test_http_removed_mode_route_is_not_found(tmp_path: Path) -> None:
    server, thread, base_url = _start_server(tmp_path)
    try:
        reply = _json_request(base_url, "/api/mode", method="POST", body={"mode": "demo"})
        assert reply.status is HTTPStatus.NOT_FOUND
        assert reply.body == {"error": "not found"}
    finally:
        _stop_server(server, thread)


def test_single_runtime_path_invokes_injected_runner(tmp_path: Path) -> None:
    entered, release = Event(), Event()
    calls: list[RunnerRequest] = []

    def runner(request: RunnerRequest) -> RunnerResult:
        calls.append(request)
        entered.set()
        _ = release.wait(timeout=3)
        return RunnerResult(request.run_id or "RUN-test", RunStatus.LOCAL_ONLY, tmp_path / "state.json", tmp_path / "log.jsonl", (), "done")

    _ = (tmp_path / "notion-config.md").write_text("- 데이터 소스 ID: `fixture-target`\n", encoding="utf-8")
    server, thread, url = _start_server(tmp_path, dependencies=DashboardServerDependencies(runner=runner, notion_adapter=FixtureNotionAdapter(), naver_adapter=FixtureNaverAdapter()))
    try:
        batch = _post_manual_run(url, {"keyword": "formal test", "as_of_date": "2026-09-10"})
        assert batch.status is HTTPStatus.ACCEPTED
        assert entered.wait(timeout=1)
        assert calls[0].dry_run is False
        assert calls[0].auto_save_naver is False
        assert calls[0].root == tmp_path
    finally:
        release.set()
        _stop_server(server, thread)


def test_http_user_post_returns_one_child_batch(tmp_path: Path) -> None:
    # Given
    server, thread, base_url = _start_server(tmp_path)
    try:
        # When
        reply = _post_manual_run(
            base_url, {"keyword": "HTTP 테스트", "as_of_date": "2026-09-07"}
        )

        # Then
        assert reply.status is HTTPStatus.ACCEPTED
        assert reply.body["topic_source"] == "user_defined"
        children = _children(reply.body)
        assert len(children) == 1
        assert children[0]["keyword"] == "HTTP 테스트"
    finally:
        _stop_server(server, thread)


def test_http_lists_and_gets_persisted_batches_after_restart(tmp_path: Path) -> None:
    # Given
    first_server, first_thread, first_base_url = _start_server(tmp_path)
    try:
        created = _post_manual_run(
            first_base_url, {"auto_topic": True, "as_of_date": "2026-09-07"}
        )
        assert created.status is HTTPStatus.ACCEPTED
        batch_id = _batch_id(created.body)
    finally:
        _stop_server(first_server, first_thread)

    # When
    second_server, second_thread, second_base_url = _start_server(tmp_path)
    try:
        listed = _json_request(second_base_url, "/api/manual-runs?limit=20")
        fetched = _json_request(second_base_url, f"/api/manual-run/{batch_id}")

        # Then
        assert listed.status is HTTPStatus.OK
        batches = listed.body["batches"]
        assert isinstance(batches, list)
        assert any(
            isinstance(batch, dict) and batch.get("batch_id") == batch_id
            for batch in batches
        )
        assert fetched.status is HTTPStatus.OK
        assert _batch_id(fetched.body) == batch_id
    finally:
        _stop_server(second_server, second_thread)


def test_http_run_detail_uses_one_source_scan_and_returns_the_list_shape(
    tmp_path: Path,
) -> None:
    # Given
    _write_dashboard_run(tmp_path, "RUN-detail")
    server, thread, base_url = _start_server(tmp_path)
    try:
        # When
        detail = _json_request(base_url, "/api/runs/RUN-detail")

        # Then: detail serializes the found RunView instead of asking the cache to list again.
        assert detail.status is HTTPStatus.OK
        assert detail.body["run_id"] == "RUN-detail"
        assert detail.body["keyword"] == "상세 조회"
        assert server.run_cache.source_scan_count == 1
    finally:
        _stop_server(server, thread)


def test_http_child_action_requires_exact_nonce_body(tmp_path: Path) -> None:
    # Given
    server, thread, base_url = _start_server(tmp_path)
    try:
        created = _post_manual_run(
            base_url, {"auto_topic": True, "as_of_date": "2026-09-07"}
        )
        initial_child = _children(created.body)[0]
        batch_id = _batch_id(created.body)
        child = _poll_child_with_action(base_url, batch_id, _child_id(initial_child))
        route = f"/api/manual-run/{batch_id}/children/{_child_id(child)}/external"

        # When
        missing = _json_request(base_url, route, method="POST", body={})
        stale = _json_request(base_url, route, method="POST", body={"nonce": "wrong"})

        # Then
        assert missing.status is HTTPStatus.BAD_REQUEST
        assert stale.status is HTTPStatus.CONFLICT
    finally:
        _stop_server(server, thread)


def test_http_replayed_child_action_returns_409(tmp_path: Path) -> None:
    # Given
    server, thread, base_url = _start_server(tmp_path)
    try:
        created = _post_manual_run(
            base_url, {"auto_topic": True, "as_of_date": "2026-09-07"}
        )
        initial_child = _children(created.body)[0]
        batch_id = _batch_id(created.body)
        child = _poll_child_with_action(base_url, batch_id, _child_id(initial_child))
        route = f"/api/manual-run/{batch_id}/children/{_child_id(child)}/external"
        body: JSONMap = {"nonce": _action_nonce(child)}

        # When
        first = _json_request(base_url, route, method="POST", body=body)
        replay = _json_request(base_url, route, method="POST", body=body)

        # Then
        assert first.status is HTTPStatus.ACCEPTED
        assert replay.status is HTTPStatus.CONFLICT
    finally:
        _stop_server(server, thread)


def test_http_accepted_child_action_returns_running_batch_for_polling(
    tmp_path: Path,
) -> None:
    # Given
    server, thread, base_url = _start_server(tmp_path)
    try:
        created = _post_manual_run(
            base_url, {"auto_topic": True, "as_of_date": "2026-09-07"}
        )
        batch_id = _batch_id(created.body)
        child = _poll_child_with_action(
            base_url, batch_id, _child_id(_children(created.body)[0])
        )

        # When
        accepted = _json_request(
            base_url,
            f"/api/manual-run/{batch_id}/children/{_child_id(child)}/external",
            method="POST",
            body={"nonce": _action_nonce(child)},
        )

        # Then
        assert accepted.status is HTTPStatus.ACCEPTED
        assert accepted.body["status"] == "running"
    finally:
        _stop_server(server, thread)


def test_http_unknown_batch_or_child_returns_404(tmp_path: Path) -> None:
    # Given
    server, thread, base_url = _start_server(tmp_path)
    try:
        # When
        missing_batch = _json_request(base_url, "/api/manual-run/BATCH-missing")
        missing_child = _json_request(
            base_url,
            "/api/manual-run/BATCH-missing/children/CHILD-missing/retry",
            method="POST",
            body={"nonce": "irrelevant"},
        )

        # Then
        assert missing_batch.status is HTTPStatus.NOT_FOUND
        assert missing_child.status is HTTPStatus.NOT_FOUND
    finally:
        _stop_server(server, thread)


def test_http_malformed_batch_id_returns_400(tmp_path: Path) -> None:
    # Given
    server, thread, base_url = _start_server(tmp_path)
    try:
        # When
        reply = _json_request(base_url, "/api/manual-run/not-a-batch-id")

        # Then
        assert reply.status is HTTPStatus.BAD_REQUEST
        assert reply.body["error"] == "manual batch_id must be a safe BATCH identifier"
    finally:
        _stop_server(server, thread)


def test_http_has_no_bulk_confirm_route(tmp_path: Path) -> None:
    # Given
    server, thread, base_url = _start_server(tmp_path)
    try:
        created = _post_manual_run(
            base_url, {"auto_topic": True, "as_of_date": "2026-09-07"}
        )

        # When
        reply = _json_request(
            base_url,
            f"/api/manual-run/{_batch_id(created.body)}/confirm",
            method="POST",
            body={"nonce": "not-a-child-nonce"},
        )

        # Then
        assert reply.status is HTTPStatus.NOT_FOUND
    finally:
        _stop_server(server, thread)


def test_http_serves_manual_run_script(tmp_path: Path) -> None:
    # Given
    server, thread, base_url = _start_server(tmp_path)
    try:
        # When
        request = Request(f"{base_url}/manual-run.js")
        with urlopen(request) as response:
            script = response.read().decode("utf-8")

        # Then
        assert response.status == HTTPStatus.OK
        assert script
    finally:
        _stop_server(server, thread)


def test_injected_local_runner_uses_single_project_root(tmp_path: Path) -> None:
    # Given
    def runner(request: RunnerRequest) -> RunnerResult:
        return RunnerResult(request.run_id or "RUN-fixture", RunStatus.LOCAL_ONLY, tmp_path / "state.json", tmp_path / "log.jsonl", (), "fixture completed")

    server, thread, base_url = _start_server(
        tmp_path,
        dependencies=DashboardServerDependencies(
            runner=runner,
            notion_adapter=FixtureNotionAdapter(), naver_adapter=FixtureNaverAdapter()
        ),
    )
    try:
        # When
        reply = _post_manual_run(
            base_url, {"auto_topic": True, "as_of_date": "2026-09-07"}
        )

        # Then
        assert reply.status is HTTPStatus.ACCEPTED
        assert len(_children(reply.body)) == 1
        assert (tmp_path / ".automation/dashboard/manual-batches").is_dir()
        assert not (tmp_path / ".automation/dashboard/demo-sandbox").exists()
    finally:
        _stop_server(server, thread)


@pytest.mark.parametrize(
    ("notion_adapter", "naver_adapter", "expected_dry_run"),
    [
        (FixtureNotionAdapter(), None, False),
        (FixtureNotionAdapter(), FixtureNaverAdapter(), False),
    ],
)
def test_dashboard_live_writes_requires_a_complete_adapter_pair(
    tmp_path: Path,
    notion_adapter: FixtureNotionAdapter,
    naver_adapter: FixtureNaverAdapter | None,
    expected_dry_run: bool,
) -> None:
    _ = (tmp_path / "notion-config.md").write_text(
        "- 데이터 소스 ID: `fixture-target`\n", encoding="utf-8"
    )
    completed = Event()
    dry_runs: list[bool] = []

    def runner(request: RunnerRequest) -> RunnerResult:
        dry_runs.append(request.dry_run)
        completed.set()
        return RunnerResult(
            "RUN-one-sided",
            RunStatus.LOCAL_ONLY,
            request.root / "state.json",
            request.root / "run.jsonl",
            (),
            "local-only",
        )

    server = DashboardServer(
        DashboardServerConfig(("127.0.0.1", 0), tmp_path),
        DashboardServerDependencies(
            runner,
            notion_adapter=notion_adapter,
            naver_adapter=naver_adapter,
        ),
    )

    try:
        _ = server.manual_runs.start(
            parse_manual_run_payload(
                {"keyword": "one-sided-adapter", "as_of_date": "2026-09-01"}
            )
        )
        assert completed.wait(timeout=1)
        assert dry_runs == [expected_dry_run]
    finally:
        server.server_close()
