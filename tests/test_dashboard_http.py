from __future__ import annotations

import json
from http.client import HTTPResponse
from pathlib import Path
from threading import Event, Thread
from urllib.request import Request, urlopen

import pytest

from tools.contract_types import JSONMap, JSONValue
from tools.dashboard_manual_run import parse_manual_run_payload
from tools.external_adapter import ExternalWriteRequest
from tools.runner_types import RunnerRequest, RunnerResult, RunStatus
from tools.test_dashboard import (
    DashboardServer,
    DashboardServerConfig,
    DashboardServerDependencies,
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


def _read_json(response: HTTPResponse) -> JSONMap:
    value: JSONValue = json.loads(response.read())
    assert isinstance(value, dict)
    return value


def test_dashboard_runs_workflow_then_exposes_external_storage_button(
    tmp_path: Path,
) -> None:
    completed = Event()

    def runner(request: RunnerRequest) -> RunnerResult:
        completed.set()
        return RunnerResult(
            "RUN-http",
            RunStatus.LOCAL_ONLY,
            request.root / "state.json",
            request.root / "run.jsonl",
            (),
            "content workflow completed; external storage is pending",
        )

    server = DashboardServer(
        DashboardServerConfig(("127.0.0.1", 0), tmp_path, False),
        DashboardServerDependencies(runner),
    )
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        start_request = Request(
            f"{base_url}/api/manual-run",
            data=json.dumps({"keyword": "HTTP 테스트", "as_of_date": "2026-09-01"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(start_request) as response:
            task = _read_json(response)
        assert response.status == 202
        assert completed.wait(timeout=1)

        with urlopen(f"{base_url}/api/manual-run/{task['task_id']}") as response:
            current = _read_json(response)
        assert current["status"] == "completed"
        assert current["result_status"] == "local-only"

        external_request = Request(
            f"{base_url}/api/manual-run/{task['task_id']}/external",
            method="POST",
        )
        with urlopen(external_request) as response:
            pending = _read_json(response)
        assert response.status == 200
        assert pending["message"] == "외부 저장 대기 · Notion/Naver 연결이 필요합니다"
    finally:
        server.shutdown()
        thread.join(timeout=1)
        server.server_close()


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
        DashboardServerConfig(("127.0.0.1", 0), tmp_path, False),
        DashboardServerDependencies(
            runner,
            notion_adapter=notion_adapter,
            naver_adapter=naver_adapter,
        ),
    )

    try:
        _ = server.manual_runs.start(
            parse_manual_run_payload({"keyword": "one-sided-adapter", "as_of_date": "2026-09-01"})
        )
        assert completed.wait(timeout=1)
        assert dry_runs == [expected_dry_run]
    finally:
        server.server_close()
