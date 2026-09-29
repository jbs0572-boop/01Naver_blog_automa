from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Final
from zoneinfo import ZoneInfo

from tools.contract_types import ContractError, JSONMap, JSONValue
from tools.dashboard_data import snapshot
from tools.dashboard_manual_store import ManualBatchStore
from tools.dashboard_model_settings import ModelSettingsStore
from tools.model_presets import ModelConfigSnapshot, parse_model_config_snapshot
from tools.runner_state import atomic_write_json

KST: Final = ZoneInfo("Asia/Seoul")


class DailySchedule:
    """Persistent daily occurrences; caller serializes updates and ticks."""

    def __init__(self, root: Path, model_settings: ModelSettingsStore | None = None) -> None:
        self._model_settings: ModelSettingsStore = (
            model_settings or ModelSettingsStore(root)
        )
        self.path: Path = root / ".automation/dashboard/schedule.json"
        pinned = self._model_settings.snapshot()
        self.data: JSONMap = {
            "version": 2,
            "enabled": False,
            "entries": [
                _entry(value, pinned) for value in ("08:00", "12:00", "18:00")
            ],
            "since": datetime.now(KST).isoformat(),
            "history": [],
        }
        self._legacy_bytes: bytes | None = None
        if self.path.exists():
            self._legacy_bytes = self.path.read_bytes()
            raw: JSONValue = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ContractError("예약 설정 파일이 올바르지 않습니다.")
            self.data = raw
            if self.data.get("mode") == "demo":
                self.data = {**self.data, "enabled": False, "retired_mode": "demo"}

    def save(self, payload: JSONMap, now: datetime) -> JSONMap:
        enabled = payload.get("enabled")
        if not set(payload).issubset({"entries", "times", "enabled", "preset_id"}):
            raise ContractError("예약 요청에 지원하지 않는 필드가 있습니다.")
        if "enabled" not in payload or not isinstance(enabled, bool):
            raise ContractError("예약 시간과 시작 여부를 입력하세요.")
        raw_entries = payload.get("entries")
        times = payload.get("times")
        if raw_entries is None and isinstance(times, list):
            preset_id = payload.get("preset_id")
            if preset_id is not None and not isinstance(preset_id, str):
                raise ContractError("예약 모델 프리셋을 선택하세요.")
            pinned = self._model_settings.snapshot(preset_id)
            raw_entries = [_entry(value, pinned) for value in times if isinstance(value, str)]
        if not isinstance(raw_entries, list) or not 1 <= len(raw_entries) <= 12:
            raise ContractError("예약 시간은 1~12개를 입력하세요.")
        normalized: list[JSONValue] = []
        seen_times: set[str] = set()
        seen_ids: set[str] = set()
        for raw_entry in raw_entries:
            if not isinstance(raw_entry, dict):
                raise ContractError("예약 항목이 올바르지 않습니다.")
            value = raw_entry.get("time")
            entry_id = raw_entry.get("entry_id")
            entry_enabled = raw_entry.get("enabled", True)
            preset_id = raw_entry.get("preset_id")
            if not isinstance(value, str) or re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", value) is None:
                raise ContractError("예약 시간은 HH:MM 형식이어야 합니다.")
            if entry_id is None:
                entry_id = _entry_id(value)
            if not isinstance(entry_id, str) or not entry_id or not isinstance(entry_enabled, bool):
                raise ContractError("예약 항목 식별자가 올바르지 않습니다.")
            if value in seen_times or entry_id in seen_ids:
                raise ContractError("같은 시간을 두 번 예약할 수 없습니다.")
            if not isinstance(preset_id, str):
                raise ContractError("예약 모델 프리셋을 선택하세요.")
            pinned = self._model_settings.snapshot(preset_id)
            seen_times.add(value)
            seen_ids.add(entry_id)
            normalized.append(
                {
                    "entry_id": entry_id,
                    "time": value,
                    "enabled": entry_enabled,
                    "preset_id": pinned.preset_id,
                    "model_config": pinned.as_json(),
                }
            )
        updated: JSONMap = {
            **{
                key: value
                for key, value in self.data.items()
                if key not in {"mode", "retired_mode", "times", "preset_id", "model_config"}
            },
            "version": 2,
            "enabled": enabled,
            "last_observed_date": now.astimezone(KST).date().isoformat(),
            "entries": sorted(
                normalized,
                key=lambda item: str(item.get("time")) if isinstance(item, dict) else "",
            ),
            "since": now.isoformat(),
        }
        self._backup_legacy()
        atomic_write_json(self.path, updated)
        self.data = updated
        return self.view(now)

    def saved_model_config(self, entry_id: str | None = None) -> ModelConfigSnapshot:
        entries = self._entries()
        selected = next(
            (
                item
                for item in entries
                if entry_id is None or item.get("entry_id") == entry_id
            ),
            None,
        )
        if selected is None:
            raise ContractError("예약 항목을 찾지 못했습니다.")
        return parse_model_config_snapshot(selected.get("model_config"))

    def view(self, now: datetime) -> JSONMap:
        candidates: list[str] = []
        entries = self._entries()
        if self.data.get("enabled"):
            for entry in entries:
                value = entry.get("time")
                if entry.get("enabled") is True and isinstance(value, str):
                    due = datetime.fromisoformat(f"{now.astimezone(KST).date()}T{value}:00+09:00")
                    if due <= now:
                        due += timedelta(days=1)
                    candidates.append(due.isoformat())
        serialized_entries: list[JSONValue] = [item for item in entries]
        return {
            **self.data,
            "version": 2,
            "entries": serialized_entries,
            "times": [item["time"] for item in entries if isinstance(item.get("time"), str)],
            "timezone": "Asia/Seoul",
            "next_run": min(candidates) if candidates else None,
        }

    def tick(
        self,
        now: datetime,
        busy: Callable[[], bool],
        launch: Callable[[str, str, str, ModelConfigSnapshot], str],
    ) -> None:
        _ = busy
        history = self.data.get("history", [])
        since = self.data.get("since")
        if not isinstance(history, list) or not isinstance(since, str):
            raise ContractError("예약 설정 파일이 올바르지 않습니다.")
        claimed_items: list[JSONMap] = [
            item
            for item in history
            if isinstance(item, dict) and item.get("status") == "claimed"
        ]
        for item in claimed_items:
            self._submit_claim(item, now, launch)
        if not self.data.get("enabled"):
            return
        entries = self._entries()
        today = now.astimezone(KST).date()
        last_observed = self.data.get("last_observed_date")
        if last_observed is None:
            first_day = datetime.fromisoformat(since).astimezone(KST).date()
        elif isinstance(last_observed, str):
            try:
                first_day = date.fromisoformat(last_observed)
            except ValueError as error:
                raise ContractError("예약 관찰 날짜가 올바르지 않습니다.") from error
        else:
            raise ContractError("예약 관찰 날짜가 올바르지 않습니다.")
        first_day = min(first_day, today)
        for entry in entries:
            value = entry.get("time")
            entry_id = entry.get("entry_id")
            if entry.get("enabled") is not True or not isinstance(value, str) or not isinstance(entry_id, str):
                continue
            day = first_day
            while day <= today:
                key = f"{day.isoformat()}T{value}:00+09:00"
                due = datetime.fromisoformat(key)
                if due <= datetime.fromisoformat(since) or due > now:
                    day += timedelta(days=1)
                    continue
                occurrence_id = "schedule-" + hashlib.sha256(
                    f"{entry_id}\0{key}".encode()
                ).hexdigest()[:16]
                if any(
                    isinstance(item, dict)
                    and (
                        item.get("occurrence_id") == occurrence_id
                        or item.get("at") == key and item.get("entry_id") in {None, entry_id}
                    )
                    for item in history
                ):
                    day += timedelta(days=1)
                    continue
                item: JSONMap = {
                    "occurrence_id": occurrence_id,
                    "entry_id": entry_id,
                    "at": key,
                    "observed_at": now.isoformat(),
                    "preset_id": entry.get("preset_id"),
                    "model_config": entry.get("model_config"),
                    "status": "missed" if now - due >= timedelta(minutes=5) else "claimed",
                    "reason_code": "server_unobserved_or_delayed" if now - due >= timedelta(minutes=5) else None,
                }
                history.append(item)
                self.data["history"] = history[-100:]
                atomic_write_json(self.path, self.data)
                if item["status"] != "missed":
                    self._submit_claim(item, now, launch)
                day += timedelta(days=1)
        if self.data.get("last_observed_date") != today.isoformat():
            self.data["last_observed_date"] = today.isoformat()
            atomic_write_json(self.path, self.data)

    def _submit_claim(
        self,
        item: JSONMap,
        now: datetime,
        launch: Callable[[str, str, str, ModelConfigSnapshot], str],
    ) -> None:
        try:
            occurrence_id = item.get("occurrence_id")
            scheduled_at = item.get("at")
            if not isinstance(occurrence_id, str) or not isinstance(scheduled_at, str):
                raise ContractError("예약 접수 기록이 올바르지 않습니다.")
            day = datetime.fromisoformat(scheduled_at).astimezone(KST).date().isoformat()
            batch_id = launch(
                day,
                scheduled_at,
                occurrence_id,
                parse_model_config_snapshot(item.get("model_config")),
            )
            if not batch_id:
                raise ContractError("예약 실행 접수 결과가 올바르지 않습니다.")
            item["batch_id"] = batch_id
            item["status"] = "submitted"
            item["submitted_at"] = now.isoformat()
        except (ContractError, OSError):
            _ = item.pop("batch_id", None)
            item["status"] = "failed"
            item["error"] = "예약 실행 연결에 실패했습니다. 연결 설정을 확인하세요."
        atomic_write_json(self.path, self.data)

    def retry(
        self,
        occurrence_id: str,
        nonce: str,
        launch: Callable[[str, str, str, ModelConfigSnapshot], str],
    ) -> JSONMap:
        if not occurrence_id or not nonce:
            raise ContractError("예약 재실행 요청이 올바르지 않습니다.")
        history = self.data.get("history")
        if not isinstance(history, list):
            raise ContractError("예약 설정 파일이 올바르지 않습니다.")
        occurrence = next(
            (
                item
                for item in history
                if isinstance(item, dict) and item.get("occurrence_id") == occurrence_id
            ),
            None,
        )
        if occurrence is None or occurrence.get("status") not in {"missed", "failed"}:
            raise ContractError("재실행할 수 있는 예약을 찾지 못했습니다.")
        if occurrence.get("mode") == "demo":
            raise ContractError("이전 데모 예약은 읽기 전용이며 재실행할 수 없습니다.")
        recovery_key = hashlib.sha256(f"{occurrence_id}\0{nonce}".encode()).hexdigest()
        recoveries = self.data.get("recoveries")
        if recoveries is None:
            recoveries = []
            self.data["recoveries"] = recoveries
        if not isinstance(recoveries, list):
            raise ContractError("예약 복구 기록이 올바르지 않습니다.")
        existing = next(
            (
                item
                for item in recoveries
                if isinstance(item, dict) and item.get("recovery_key") == recovery_key
            ),
            None,
        )
        if existing is not None and existing.get("status") != "accepted":
            return existing
        at = occurrence.get("at")
        if not isinstance(at, str):
            raise ContractError("예약 예정 시각이 올바르지 않습니다.")
        if existing is None:
            recovery: JSONMap = {
                "recovery_key": recovery_key,
                "occurrence_id": occurrence_id,
                "as_of_date": datetime.fromisoformat(at).astimezone(KST).date().isoformat(),
                "status": "accepted",
                "submitted_at": None,
                "batch_id": None,
            }
            recoveries.append(recovery)
            atomic_write_json(self.path, self.data)
        else:
            recovery = existing
        try:
            model_config = parse_model_config_snapshot(occurrence.get("model_config"))
            recovery["batch_id"] = launch(
                str(recovery["as_of_date"]), at, occurrence_id, model_config
            )
            recovery["status"] = "submitted"
            recovery["submitted_at"] = datetime.now(KST).isoformat()
        except (ContractError, OSError):
            recovery["status"] = "failed"
            recovery["error"] = "예약 재실행 접수에 실패했습니다. 연결을 확인하세요."
        atomic_write_json(self.path, self.data)
        return recovery

    def progress(self, root: Path, now: datetime) -> JSONMap:
        history = self.data.get("history", [])
        executions: list[JSONValue] = []
        seen: set[str] = set()
        runs: JSONValue = None
        if not isinstance(history, list):
            return {**self.view(now), "executions": executions}
        latest_recoveries: dict[str, JSONMap] = {}
        recoveries = self.data.get("recoveries")
        if isinstance(recoveries, list):
            for recovery in recoveries:
                if isinstance(recovery, dict):
                    occurrence_id = recovery.get("occurrence_id")
                    if isinstance(occurrence_id, str):
                        latest_recoveries[occurrence_id] = recovery
        displayed_history: list[JSONValue] = []
        for item in history:
            if not isinstance(item, dict):
                displayed_history.append(item)
                continue
            displayed: JSONMap = dict(item)
            occurrence_id = displayed.get("occurrence_id")
            recovery = (
                latest_recoveries.get(occurrence_id)
                if isinstance(occurrence_id, str)
                else None
            )
            if recovery is not None:
                status = recovery.get("status")
                batch_id = recovery.get("batch_id")
                if isinstance(status, str):
                    displayed["status"] = status
                if isinstance(batch_id, str):
                    displayed["batch_id"] = batch_id
            displayed_history.append(displayed)
        for item in reversed(displayed_history):
            if not isinstance(item, dict) or not isinstance(item.get("at"), str):
                continue
            at = item["at"]
            assert isinstance(at, str)
            time = datetime.fromisoformat(at).astimezone(KST).strftime("%H:%M")
            if time in seen:
                continue
            seen.add(time)
            execution: JSONMap = {**item, "time": time, "child": None, "run": None}
            batch_id = item.get("batch_id")
            if isinstance(batch_id, str):
                batch = ManualBatchStore(root).get(batch_id)
                if batch is not None and batch.children:
                    child = batch.children[0]
                    execution["child"] = child.as_json()
                    if runs is None:
                        runs = snapshot(root).get("runs", [])
                    if isinstance(runs, list):
                        run_maps: list[JSONMap] = [
                            value for value in runs if isinstance(value, dict)
                        ]
                        execution["run"] = next(
                            (
                                run
                                for run in run_maps
                                if run.get("run_id") == child.run_id
                            ),
                            None,
                        )
            executions.append(execution)
        return {
            **self.view(now),
            "history": displayed_history,
            "executions": executions,
        }

    def _entries(self) -> list[JSONMap]:
        entries = self.data.get("entries")
        if isinstance(entries, list):
            return [item for item in entries if isinstance(item, dict)]
        times = self.data.get("times")
        if not isinstance(times, list):
            raise ContractError("예약 설정 파일이 올바르지 않습니다.")
        config = parse_model_config_snapshot(self.data.get("model_config"))
        return [_entry(value, config) for value in times if isinstance(value, str)]

    def _backup_legacy(self) -> None:
        if self._legacy_bytes is None or self.data.get("version") == 2:
            return
        digest = hashlib.sha256(self._legacy_bytes).hexdigest()[:16]
        backup = self.path.with_name(f"schedule.legacy-{digest}.json")
        if not backup.exists():
            backup.parent.mkdir(parents=True, exist_ok=True)
            with backup.open("xb") as stream:
                _ = stream.write(self._legacy_bytes)


def _entry_id(value: str) -> str:
    return "entry-" + hashlib.sha256(value.encode()).hexdigest()[:12]


def _entry(value: str, config: ModelConfigSnapshot) -> JSONMap:
    return {
        "entry_id": _entry_id(value),
        "time": value,
        "enabled": True,
        "preset_id": config.preset_id,
        "model_config": config.as_json(),
    }
