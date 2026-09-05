from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import override
from urllib.parse import urlparse

from tools.contract_types import ContractError, JSONValue
from tools.dashboard_adapters import load_dashboard_external_adapters
from tools.dashboard_data import snapshot
from tools.dashboard_manual_models import (
    ManualRunContext,
    ManualRunDependencies,
    Runner,
)
from tools.dashboard_manual_run import ManualRunManager, parse_manual_run_payload
from tools.external_adapter import NotionAdapter
from tools.naver_adapter import NaverBrowserAdapter
from tools.runner_execution import run_job
from tools.runner_types import StageExecutor


@dataclass(frozen=True, slots=True)
class DashboardServerConfig:
    address: tuple[str, int]
    root: Path
    demo: bool


@dataclass(frozen=True, slots=True)
class DashboardServerDependencies:
    runner: Runner = run_job
    executor: StageExecutor | None = None
    notion_adapter: NotionAdapter | None = None
    naver_adapter: NaverBrowserAdapter | None = None


class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        route = urlparse(self.path).path
        server = self._dashboard_server()
        if route == "/api/snapshot":
            self._json(snapshot(server.project_root, server.demo))
            return
        if route.startswith("/api/manual-run/"):
            task_id = route.rsplit("/", 1)[-1]
            task = server.manual_runs.get(task_id)
            if task is None:
                self.send_error(HTTPStatus.NOT_FOUND, "task not found")
                return
            self._json(task.as_json())
            return
        if route in {"/", "/index.html", "/showcase.html"}:
            filename = "showcase.html" if route == "/showcase.html" else "index.html"
            self._file(filename, "text/html; charset=utf-8")
            return
        if route in {"/styles.css", "/app.js"}:
            content_type = (
                "text/css; charset=utf-8"
                if route.endswith("css")
                else "text/javascript; charset=utf-8"
            )
            self._file(route[1:], content_type)
            return
        self.send_error(HTTPStatus.NOT_FOUND, "not found")

    def do_POST(self) -> None:
        route = urlparse(self.path).path
        server = self._dashboard_server()
        if route.endswith("/confirm") and route.startswith("/api/manual-run/"):
            task_id = route.removeprefix("/api/manual-run/").removesuffix("/confirm")
            try:
                task = server.manual_runs.confirm(task_id)
            except ContractError as error:
                self._json({"error": str(error)}, HTTPStatus.CONFLICT)
                return
            self._json(task.as_json())
            return
        if route.endswith("/external") and route.startswith("/api/manual-run/"):
            task_id = route.removeprefix("/api/manual-run/").removesuffix("/external")
            try:
                task = server.manual_runs.continue_external(task_id)
            except ContractError as error:
                self._json({"error": str(error)}, HTTPStatus.CONFLICT)
                return
            self._json(task.as_json())
            return
        if route != "/api/manual-run":
            self.send_error(HTTPStatus.NOT_FOUND, "not found")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 8192:
                raise ContractError(
                    "manual-run payload must be between 1 and 8192 bytes"
                )
            raw: JSONValue = json.loads(self.rfile.read(length))
            if not isinstance(raw, dict):
                raise ContractError("manual-run payload must be an object")
            request = parse_manual_run_payload(raw)
            task = server.manual_runs.start(request)
        except (ContractError, ValueError, json.JSONDecodeError) as error:
            self._json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
            return
        self._json(task.as_json(), HTTPStatus.ACCEPTED)

    def _file(self, filename: str, content_type: str) -> None:
        server = self._dashboard_server()
        path = server.dashboard_root / filename
        try:
            body = path.read_bytes()
        except OSError:
            self.send_error(HTTPStatus.NOT_FOUND, "asset not found")
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        _ = self.wfile.write(body)

    def _json(self, value: JSONValue, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        _ = self.wfile.write(body)

    @override
    def log_message(self, format: str, *args: str) -> None:
        _ = (format, args)

    def _dashboard_server(self) -> DashboardServer:
        assert isinstance(self.server, DashboardServer)
        return self.server


class DashboardServer(ThreadingHTTPServer):
    project_root: Path
    dashboard_root: Path
    demo: bool
    manual_runs: ManualRunManager

    def __init__(
        self,
        config: DashboardServerConfig,
        dependencies: DashboardServerDependencies | None = None,
    ) -> None:
        resolved_dependencies = dependencies or DashboardServerDependencies()
        self.project_root = config.root
        self.dashboard_root = config.root / "dashboard"
        self.demo = config.demo
        self.manual_runs = ManualRunManager(
            ManualRunContext(
                root=config.root,
                demo=config.demo,
                live_writes=(
                    not config.demo
                    and (
                        resolved_dependencies.notion_adapter is not None
                        and resolved_dependencies.naver_adapter is not None
                    )
                ),
            ),
            ManualRunDependencies(
                runner=resolved_dependencies.runner,
                executor=resolved_dependencies.executor,
                notion_adapter=resolved_dependencies.notion_adapter,
                naver_adapter=resolved_dependencies.naver_adapter,
            ),
        )
        super().__init__(config.address, DashboardHandler)

    @override
    def server_close(self) -> None:
        self.manual_runs.close()
        super().server_close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="serve the local workflow QA dashboard"
    )
    _ = parser.add_argument("--root", type=Path, default=Path.cwd())
    _ = parser.add_argument("--host", default="127.0.0.1")
    _ = parser.add_argument("--port", type=int, default=8765)
    _ = parser.add_argument(
        "--demo",
        action="store_true",
        help="show in-memory fixture runs when no logs exist",
    )
    _ = parser.add_argument(
        "--live-writes",
        action="store_true",
        help="enable Notion writes and supervised Naver draft saving",
    )
    args = parser.parse_args()
    root = args.root.resolve()
    dependencies: DashboardServerDependencies | None = None
    if args.live_writes:
        try:
            adapters = load_dashboard_external_adapters(root)
        except ContractError as error:
            _ = print(f"dashboard: {error}")
            return
        dependencies = DashboardServerDependencies(
            notion_adapter=adapters.notion,
            naver_adapter=adapters.naver,
        )
    with DashboardServer(
        DashboardServerConfig((args.host, args.port), root, args.demo),
        dependencies,
    ) as server:
        _ = print(f"QA dashboard: http://{args.host}:{args.port}")
        _ = print(
            "Manual runs use the same daily-generate workflow; external writes require configured adapters."
        )
        try:
            _ = server.serve_forever()
        except KeyboardInterrupt:
            return


if __name__ == "__main__":
    main()
