from __future__ import annotations

import argparse
import fcntl
import ipaddress
import json
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import RLock
from typing import Final, Literal, cast, override
from urllib.parse import parse_qs, urlparse

from tools.contract_types import ContractError, JSONMap, JSONValue
from tools.dashboard_adapters import (
    DashboardExternalAdapters,
    load_dashboard_external_adapters,
)
from tools.dashboard_data import snapshot
from tools.dashboard_health import DashboardHealth
from tools.dashboard_manual_models import (
    ManualRunContext,
    ManualRunDependencies,
    Runner,
)
from tools.dashboard_manual_run import ManualRunManager, parse_manual_run_payload
from tools.dashboard_model_settings import ModelSettingsStore
from tools.dashboard_notifications import DashboardNotifications
from tools.dashboard_preview import preview_asset, run_preview
from tools.dashboard_schedule import KST, DailySchedule
from tools.dashboard_snapshot_cache import RunSnapshotCache
from tools.dashboard_tasks import TASK_STATUSES, DashboardTasks
from tools.external_adapter import NotionAdapter
from tools.model_presets import ModelConfigSnapshot
from tools.naver_adapter import NaverBrowserAdapter
from tools.run_cancellation import CancellationScope
from tools.runner_execution import run_job
from tools.runner_types import StageExecutor
from tools.startup_preflight import run_preflight

DASHBOARD_PORT: Final = 8765
DASHBOARD_HOST: Final = "127.0.0.1"
DASHBOARD_URL: Final = f"http://{DASHBOARD_HOST}:{DASHBOARD_PORT}"
DEFAULT_BATCH_LIST_LIMIT: Final = 20
MAX_BATCH_LIST_LIMIT: Final = 100
type ManualActionKind = Literal["retry", "external", "confirm"]


def _health_probe(root: Path, *, require_notion: bool) -> tuple[bool, str | None]:
    result = run_preflight(root, require_notion_credentials=require_notion)
    return result.ok, result.error_code


class DashboardAlreadyRunningError(RuntimeError):
    pass


@contextmanager
def dashboard_instance_lock(root: Path) -> Generator[None, None, None]:
    lock_path = root / ".automation" / "state" / "dashboard.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise DashboardAlreadyRunningError(
                f"dashboard already running on {DASHBOARD_URL}"
            ) from error
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


@dataclass(frozen=True, slots=True)
class DashboardServerConfig:
    address: tuple[str, int]
    root: Path


@dataclass(frozen=True, slots=True)
class DashboardServerDependencies:
    runner: Runner = run_job
    executor: StageExecutor | None = None
    notion_adapter: NotionAdapter | None = None
    naver_adapter: NaverBrowserAdapter | None = None
    external_adapter_error: str | None = None


@dataclass(frozen=True, slots=True)
class RunListQuery:
    query: str
    status: str | None
    date_from: str | None
    date_to: str | None
    limit: int
    cursor: str | None


class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        with self._dashboard_server().operation_lock:
            self._get()

    def _get(self) -> None:
        parsed = urlparse(self.path)
        route = parsed.path
        server = self._dashboard_server()
        if route == "/api/schedule":
            self._json(server.schedule.view(datetime.now(KST)))
            return
        if route == "/api/model-settings":
            self._json(server.model_settings.view())
            return
        if route == "/api/schedule-status":
            self._json(server.schedule.progress(server.project_root, datetime.now(KST)))
            return
        if route == "/api/snapshot":
            self._json(snapshot(server.project_root))
            return
        if route == "/api/runs":
            try:
                query = self._run_query(parsed.query)
                self._json(
                    server.run_cache.query(
                        query=query.query,
                        status=query.status,
                        date_from=query.date_from,
                        date_to=query.date_to,
                        limit=query.limit,
                        cursor=query.cursor,
                    )
                )
            except ContractError as error:
                self._error(error, HTTPStatus.BAD_REQUEST)
            return
        if route == "/api/tasks":
            try:
                task_query = self._task_query(parsed.query)
                self._json(server.tasks.query(q=str(task_query["q"]), status=cast(str | None, task_query["status"]), topic_source=cast(str | None, task_query["topic_source"]), date_from=cast(str | None, task_query["date_from"]), date_to=cast(str | None, task_query["date_to"]), limit=cast(int, task_query["limit"]), cursor=cast(str | None, task_query["cursor"])))
            except ContractError as error:
                self._error(error, HTTPStatus.BAD_REQUEST)
            return
        run_route = self._run_route(route)
        if run_route is not None:
            run_id, action, asset_id = run_route
            try:
                if action == "detail":
                    run = server.run_cache.get(run_id)
                    if run is None:
                        self._error(ContractError("run not found"), HTTPStatus.NOT_FOUND)
                    else:
                        self._json(asdict(run))
                elif action == "preview":
                    self._json(run_preview(server.project_root, run_id))
                else:
                    assert asset_id is not None
                    body, content_type = preview_asset(server.project_root, run_id, asset_id)
                    self._bytes(body, content_type)
            except ContractError as error:
                self._error(error, HTTPStatus.CONFLICT)
            return
        if route == "/api/health":
            self._json(server.health_view())
            return
        if route == "/api/notifications":
            server.sync_notifications()
            self._json(server.notifications.view())
            return
        if route == "/api/manual-runs":
            try:
                limit = self._batch_limit(parsed.query)
                batches = server.manual_runs.list(limit)
            except ContractError as error:
                self._error(error, HTTPStatus.BAD_REQUEST)
                return
            self._json({"batches": [batch.as_json() for batch in batches]})
            return
        batch_id = self._batch_id_from_route(route)
        if batch_id is not None:
            try:
                batch = server.manual_runs.get(batch_id)
            except ContractError as error:
                self._error(error, HTTPStatus.BAD_REQUEST)
                return
            if batch is None:
                self._error(
                    ContractError("manual batch not found"), HTTPStatus.NOT_FOUND
                )
                return
            self._json(batch.as_json())
            return
        if route in {"/", "/index.html", "/showcase.html"}:
            filename = "showcase.html" if route == "/showcase.html" else "index.html"
            self._file(filename, "text/html; charset=utf-8")
            return
        if route in {"/styles.css", "/app.js", "/manual-run.js", "/progress.js", "/schedule.js", "/model-settings.js", "/preview.js", "/health.js", "/notifications.js", "/navigation.js", "/tasks.js"}:
            content_type = (
                "text/css; charset=utf-8"
                if route.endswith("css")
                else "text/javascript; charset=utf-8"
            )
            self._file(route[1:], content_type)
            return
        self.send_error(HTTPStatus.NOT_FOUND, "not found")

    def _validate_post_request(self) -> bool:
        server = self._dashboard_server()
        host_headers = self.headers.get_all("Host", [])
        try:
            if len(host_headers) != 1:
                raise ValueError("invalid Host header")
            parsed_host = urlparse(f"//{host_headers[0]}")
            hostname = parsed_host.hostname
            port = parsed_host.port
            if (
                hostname is None
                or parsed_host.username is not None
                or parsed_host.password is not None
                or parsed_host.path
                or parsed_host.params
                or parsed_host.query
                or parsed_host.fragment
                or port != server.server_address[1]
            ):
                raise ValueError("invalid Host header")
            is_loopback = hostname == "localhost"
            if not is_loopback:
                is_loopback = ipaddress.ip_address(hostname).is_loopback
            if not is_loopback:
                raise ValueError("dashboard Host must be loopback")
        except ValueError:
            self._error(
                ContractError("dashboard mutations require a loopback Host"),
                HTTPStatus.FORBIDDEN,
            )
            return False

        origin_headers = self.headers.get_all("Origin", [])
        if len(origin_headers) > 1:
            self._error(
                ContractError("dashboard mutations reject multiple Origin headers"),
                HTTPStatus.FORBIDDEN,
            )
            return False
        if origin_headers:
            try:
                origin = urlparse(origin_headers[0])
                if (
                    origin.scheme != "http"
                    or origin.hostname != hostname
                    or origin.port != server.server_address[1]
                    or origin.username is not None
                    or origin.password is not None
                    or origin.path
                    or origin.params
                    or origin.query
                    or origin.fragment
                ):
                    raise ValueError("cross-origin dashboard mutation")
            except ValueError:
                self._error(
                    ContractError("dashboard mutations require a same-origin request"),
                    HTTPStatus.FORBIDDEN,
                )
                return False

        if self.headers.get_content_type().lower() != "application/json":
            self._error(
                ContractError("dashboard mutations require application/json"),
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
            )
            return False
        return True

    def do_POST(self) -> None:
        if not self._validate_post_request():
            return
        with self._dashboard_server().operation_lock:
            self._post()

    def _post(self) -> None:
        route = urlparse(self.path).path
        server = self._dashboard_server()
        cancel_route = self._cancel_route(route)
        if cancel_route is not None:
            batch_id, child_id = cancel_route
            try:
                payload = self._payload()
                if set(payload) != {"nonce", "scope"}:
                    raise ContractError("cancellation request requires exactly nonce and scope")
                nonce = payload.get("nonce")
                scope = payload.get("scope")
                if not isinstance(nonce, str) or not nonce or scope not in {"queued_only", "remaining"}:
                    raise ContractError("cancellation request fields are invalid")
                batch, accepted = server.manual_runs.cancel(batch_id, child_id, nonce, cast(CancellationScope, scope))
            except ContractError as error:
                message = str(error)
                status = HTTPStatus.NOT_FOUND if "not found" in message else HTTPStatus.BAD_REQUEST if "fields" in message or "requires exactly" in message else HTTPStatus.CONFLICT
                self._error(error, status)
                return
            self._json(batch.as_json(), HTTPStatus.ACCEPTED if accepted else HTTPStatus.OK)
            return
        if route == "/api/schedule":
            try:
                self._json(server.schedule.save(self._payload(), datetime.now(KST)))
            except (ContractError, ValueError, OSError) as error:
                self._error(error, HTTPStatus.BAD_REQUEST)
            return
        if route == "/api/model-settings":
            try:
                self._json(server.model_settings.save(self._payload()))
            except (ContractError, ValueError, OSError) as error:
                self._error(error, HTTPStatus.CONFLICT)
            return
        if route == "/api/schedule/retry":
            try:
                payload = self._payload()
                if set(payload) != {"occurrence_id", "nonce"}:
                    raise ContractError("예약 재실행 요청 필드가 올바르지 않습니다.")
                occurrence_id = payload.get("occurrence_id")
                nonce = payload.get("nonce")
                if not all(isinstance(value, str) for value in (occurrence_id, nonce)):
                    raise ContractError("예약 재실행 요청이 올바르지 않습니다.")
                assert isinstance(occurrence_id, str) and isinstance(nonce, str)
                result = server.schedule.retry(occurrence_id, nonce, server.launch_scheduled)
                _ = server.notifications.record(occurrence_id, str(result["status"]), str(result["recovery_key"]), "놓친 예약 재실행 상태가 변경되었습니다.")
                self._json(result, HTTPStatus.ACCEPTED)
            except (ContractError, ValueError, json.JSONDecodeError) as error:
                self._error(error, HTTPStatus.CONFLICT)
            return
        if route == "/api/health/check":
            if server.dependencies.external_adapter_error is not None:
                self._json(server.health_view(), HTTPStatus.SERVICE_UNAVAILABLE)
            else:
                self._json(server.health.start(), HTTPStatus.ACCEPTED)
            return
        if route.startswith("/api/notifications/") and route.endswith("/read"):
            notice_id = route.removeprefix("/api/notifications/").removesuffix("/read").strip("/")
            try:
                self._json(server.notifications.mark_read(notice_id))
            except ContractError as error:
                self._error(error, HTTPStatus.NOT_FOUND)
            return
        action_route = self._child_action_route(route)
        if action_route is not None:
            batch_id, child_id, action = action_route
            batch = server.manual_runs.get(batch_id)
            if batch is None:
                self._error(
                    ContractError("manual batch not found"), HTTPStatus.NOT_FOUND
                )
                return
            if not any(child.child_id == child_id for child in batch.children):
                self._error(
                    ContractError("manual batch child not found"), HTTPStatus.NOT_FOUND
                )
                return
            try:
                nonce = self._action_nonce()
            except (ContractError, ValueError, json.JSONDecodeError) as error:
                self._error(error, HTTPStatus.BAD_REQUEST)
                return
            try:
                updated = server.manual_runs.submit_action(
                    batch_id, child_id, action, nonce
                )
            except ContractError as error:
                self._error(error, HTTPStatus.CONFLICT)
                return
            self._json(updated.as_json(), HTTPStatus.ACCEPTED)
            return
        if route not in {"/api/manual-run", "/api/manual-runs"}:
            if route.startswith("/api/"):
                self._error(ContractError("not found"), HTTPStatus.NOT_FOUND)
                return
            self.send_error(HTTPStatus.NOT_FOUND, "not found")
            return
        try:
            payload = self._payload()
            if "request_nonce" not in payload:
                raise ContractError("manual request_nonce is required")
            request = parse_manual_run_payload(payload)
            request = replace(
                request,
                model_config=server.model_settings.snapshot(request.preset_id),
            )
            batch = server.manual_runs.start(request)
        except (ContractError, ValueError, json.JSONDecodeError) as error:
            status = (
                HTTPStatus.CONFLICT
                if "different payload" in str(error)
                else HTTPStatus.BAD_REQUEST
            )
            self._error(error, status)
            return
        status = HTTPStatus.OK if route == "/api/manual-runs" else HTTPStatus.ACCEPTED
        self._json(batch.as_json(), status)

    def _payload(self) -> JSONMap:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise ContractError(
                "manual-run payload Content-Length is invalid"
            ) from error
        if length <= 0 or length > 8192:
            raise ContractError("manual-run payload must be between 1 and 8192 bytes")
        raw: JSONValue = json.loads(self.rfile.read(length))
        if not isinstance(raw, dict):
            raise ContractError("manual-run payload must be an object")
        return raw

    def _action_nonce(self) -> str:
        payload = self._payload()
        if set(payload) != {"nonce"}:
            raise ContractError("manual child action requires exactly a nonce")
        nonce = payload["nonce"]
        if not isinstance(nonce, str) or not nonce:
            raise ContractError("manual child action nonce is invalid")
        return nonce

    def _batch_limit(self, query: str) -> int:
        values = parse_qs(query, keep_blank_values=True)
        if not values:
            return DEFAULT_BATCH_LIST_LIMIT
        if set(values) != {"limit"} or len(values["limit"]) != 1:
            raise ContractError("manual batch list query is invalid")
        raw_limit = values["limit"][0]
        if not raw_limit.isdecimal():
            raise ContractError("manual batch list limit is invalid")
        limit = int(raw_limit)
        if limit < 1 or limit > MAX_BATCH_LIST_LIMIT:
            raise ContractError("manual batch list limit is out of range")
        return limit

    def _run_query(self, query: str) -> RunListQuery:
        values = parse_qs(query, keep_blank_values=True)
        allowed = {"query", "status", "date_from", "date_to", "limit", "cursor"}
        if not set(values).issubset(allowed) or any(len(value) != 1 for value in values.values()):
            raise ContractError("run list query is invalid")
        raw_limit = values.get("limit", ["20"])[0]
        if not raw_limit.isdecimal():
            raise ContractError("run list limit is invalid")
        return RunListQuery(
            values.get("query", [""])[0],
            values.get("status", [None])[0] or None,
            values.get("date_from", [None])[0] or None,
            values.get("date_to", [None])[0] or None,
            int(raw_limit),
            values.get("cursor", [None])[0] or None,
        )

    def _task_query(self, query: str) -> dict[str, str | int | None]:
        values = parse_qs(query, keep_blank_values=True)
        allowed = {"q", "status", "topic_source", "date_from", "date_to", "limit", "cursor"}
        if not set(values).issubset(allowed) or any(len(item) != 1 for item in values.values()):
            raise ContractError("task list query is invalid")
        raw_limit = values.get("limit", ["20"])[0]
        if not raw_limit.isdecimal() or not 1 <= int(raw_limit) <= 100:
            raise ContractError("task list limit is out of range")
        status = values.get("status", [None])[0] or None
        if status is not None and status != "all" and status not in TASK_STATUSES:
            raise ContractError("task status is invalid")
        source = values.get("topic_source", [None])[0] or None
        if source is not None and source not in {"user_defined", "auto_selected"}:
            raise ContractError("task topic_source is invalid")
        date_from = values.get("date_from", [None])[0] or None
        date_to = values.get("date_to", [None])[0] or None
        for value in (date_from, date_to):
            if value is not None:
                try:
                    _ = date.fromisoformat(value)
                except ValueError as error:
                    raise ContractError("task date filter is invalid") from error
        if date_from is not None and date_to is not None and date_from > date_to:
            raise ContractError("task date range is invalid")
        return {"q": values.get("q", [""])[0], "status": status, "topic_source": source, "date_from": date_from, "date_to": date_to, "limit": int(raw_limit), "cursor": values.get("cursor", [None])[0] or None}

    def _run_route(self, route: str) -> tuple[str, str, str | None] | None:
        parts = route.strip("/").split("/")
        if len(parts) < 3 or parts[:2] != ["api", "runs"] or not parts[2]:
            return None
        if len(parts) == 3:
            return parts[2], "detail", None
        if len(parts) == 4 and parts[3] == "preview":
            return parts[2], "preview", None
        if len(parts) == 5 and parts[3] == "assets" and parts[4]:
            return parts[2], "asset", parts[4]
        return None

    def _batch_id_from_route(self, route: str) -> str | None:
        prefix = "/api/manual-run/"
        if not route.startswith(prefix):
            return None
        batch_id = route.removeprefix(prefix)
        if not batch_id or "/" in batch_id:
            return None
        return batch_id

    def _child_action_route(
        self, route: str
    ) -> tuple[str, str, ManualActionKind] | None:
        parts = route.split("/")
        if len(parts) != 7 or parts[1:3] != ["api", "manual-run"]:
            return None
        if parts[4] != "children":
            return None
        batch_id, child_id, raw_action = parts[3], parts[5], parts[6]
        if not batch_id or not child_id:
            return None
        match raw_action:
            case "retry":
                return batch_id, child_id, "retry"
            case "external":
                return batch_id, child_id, "external"
            case "confirm":
                return batch_id, child_id, "confirm"
            case _:
                return None

    def _cancel_route(self, route: str) -> tuple[str, str] | None:
        parts = route.split("/")
        if len(parts) == 7 and parts[1:3] == ["api", "manual-run"] and parts[4] == "children" and parts[6] == "cancel":
            batch_id, child_id = parts[3], parts[5]
            if batch_id and child_id:
                return batch_id, child_id
        return None

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

    def _bytes(self, body: bytes, content_type: str) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        _ = self.wfile.write(body)

    def _error(self, error: Exception, status: HTTPStatus) -> None:
        self._json({"error": str(error)}, status)

    @override
    def log_message(self, format: str, *args: str) -> None:
        _ = (format, args)

    def _dashboard_server(self) -> DashboardServer:
        assert isinstance(self.server, DashboardServer)
        return self.server


class DashboardServer(ThreadingHTTPServer):
    project_root: Path
    dashboard_root: Path
    live_writes_enabled: bool
    manual_runs: ManualRunManager
    model_settings: ModelSettingsStore
    tasks: DashboardTasks

    def __init__(
        self,
        config: DashboardServerConfig,
        dependencies: DashboardServerDependencies | None = None,
    ) -> None:
        resolved_dependencies = dependencies or DashboardServerDependencies()
        self.operation_lock: RLock = RLock()
        self.dependencies: DashboardServerDependencies = resolved_dependencies
        self.loaded_adapters: DashboardExternalAdapters | None = None
        self.project_root = config.root
        self.dashboard_root = Path(__file__).resolve().parents[1] / "dashboard"
        self.live_writes_enabled = (
            resolved_dependencies.notion_adapter is not None
            and resolved_dependencies.naver_adapter is not None
        )
        self.manual_runs = ManualRunManager(
            ManualRunContext(
                root=config.root,
                live_writes=self.live_writes_enabled,
            ),
            ManualRunDependencies(
                runner=resolved_dependencies.runner,
                executor=resolved_dependencies.executor,
                notion_adapter=resolved_dependencies.notion_adapter,
                naver_adapter=resolved_dependencies.naver_adapter,
            ),
        )
        self.model_settings = ModelSettingsStore(config.root)
        self.schedule: DailySchedule = DailySchedule(config.root, self.model_settings)
        self.run_cache: RunSnapshotCache = RunSnapshotCache(config.root)
        self.tasks = DashboardTasks(self.manual_runs, self.run_cache)
        self.notifications: DashboardNotifications = DashboardNotifications(config.root)
        self.health: DashboardHealth = DashboardHealth(
            config.root,
            lambda root: _health_probe(root, require_notion=True),
        )
        self._notification_states: dict[str, str] = {}
        self._remember_notification_states()
        super().__init__(config.address, DashboardHandler)

    @override
    def service_actions(self) -> None:
        with self.operation_lock:
            self.schedule.tick(datetime.now(KST), self.manual_runs.has_active_work, self.launch_scheduled)
            self.sync_notifications()

    def _remember_notification_states(self) -> None:
        for batch in self.manual_runs.list(100):
            for child in batch.children:
                self._notification_states[child.task_id] = child.result_status or child.status

    def health_view(self) -> JSONMap:
        result = self.health.view()
        if self.dependencies.external_adapter_error is not None:
            result.update(
                status="failed",
                error_code="external_adapters_unavailable",
                next_action="외부 저장 연결을 사용할 수 없습니다. 설정을 확인하세요.",
            )
        return result

    def sync_notifications(self) -> None:
        for batch in self.manual_runs.list(100):
            for child in batch.children:
                status = child.result_status or child.status
                previous = self._notification_states.get(child.task_id)
                self._notification_states[child.task_id] = status
                if previous == status or status not in {"failed", "awaiting_user_confirmation", "draft_saved"}:
                    continue
                _ = self.notifications.record(child.task_id, status, child.updated_at, child.message or status)

    def launch_scheduled(
        self,
        day: str,
        scheduled_at: str,
        occurrence_id: str,
        model_config: ModelConfigSnapshot,
    ) -> str:
        request = parse_manual_run_payload(
            {
                "auto_topic": True,
                "as_of_date": day,
                "preset_id": model_config.preset_id,
                "request_nonce": str(uuid.uuid5(uuid.NAMESPACE_URL, occurrence_id)),
            }
        )
        batch = self.manual_runs.start(
            replace(
                request,
                model_config=model_config,
                scheduled_at=scheduled_at,
            )
        )
        return batch.batch_id

    @override
    def server_close(self) -> None:
        self.manual_runs.close()
        if self.loaded_adapters is not None:
            self.loaded_adapters.close()
        super().server_close()


def load_server_dependencies(
    root: Path,
) -> tuple[DashboardServerDependencies, DashboardExternalAdapters | None]:
    try:
        external_adapters = load_dashboard_external_adapters(root)
    except (ContractError, OSError):
        return (
            DashboardServerDependencies(
                external_adapter_error="external_adapters_unavailable"
            ),
            None,
        )
    return (
        DashboardServerDependencies(
            notion_adapter=external_adapters.notion,
            naver_adapter=external_adapters.naver,
        ),
        external_adapters,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="serve the local workflow QA dashboard"
    )
    _ = parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    root = args.root.resolve()
    dependencies, external_adapters = load_server_dependencies(root)
    if external_adapters is None:
        _ = print("dashboard: external adapters unavailable; local dashboard remains available")
    try:
        with (
            dashboard_instance_lock(root),
            DashboardServer(
                DashboardServerConfig((DASHBOARD_HOST, DASHBOARD_PORT), root),
                dependencies,
            ) as server,
        ):
            _ = print(f"QA dashboard: {DASHBOARD_URL}")
            _ = print(
                "Manual runs use the same daily-generate workflow; external writes require configured adapters."
            )
            try:
                _ = server.serve_forever()
            except KeyboardInterrupt:
                return
    except DashboardAlreadyRunningError as error:
        _ = print(f"dashboard: {error}")
    finally:
        if external_adapters is not None:
            external_adapters.close()


if __name__ == "__main__":
    main()
