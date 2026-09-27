from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from threading import Event, Lock, Thread
from typing import final

from tools.contract_types import JSONMap

type HealthProbe = Callable[[Path], tuple[bool, str | None]]


@final
class DashboardHealth:

    def __init__(self, root: Path, probe: HealthProbe) -> None:
        self.root: Path = root
        self.probe: HealthProbe = probe
        self._lock: Lock = Lock()
        self._done: Event = Event()
        self._thread: Thread | None = None
        self._revision: int = 0
        self._result: JSONMap = {
            "status": "unknown",
            "revision": 0,
            "checked_at": None,
            "error_code": None,
            "next_action": "연결 상태 다시 확인을 실행하세요.",
        }

    def start(self) -> JSONMap:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return dict(self._result)
            self._done.clear()
            self._revision += 1
            self._result = {
                "status": "checking",
                "revision": self._revision,
                "checked_at": None,
                "error_code": None,
                "next_action": "읽기 전용 연결 점검 중입니다.",
            }
            self._thread = Thread(target=self._run, daemon=True)
            self._thread.start()
            return dict(self._result)

    def _run(self) -> None:
        ok, error_code = self.probe(self.root)
        with self._lock:
            self._result = {
                "status": "ready" if ok else "failed",
                "revision": self._revision,
                "checked_at": datetime.now(UTC).isoformat(),
                "error_code": error_code,
                "next_action": (
                    "모든 읽기 전용 점검이 통과했습니다."
                    if ok
                    else "설정과 연결을 확인한 뒤 다시 점검하세요."
                ),
            }
            self._done.set()

    def view(self) -> JSONMap:
        with self._lock:
            return dict(self._result)

    def wait(self, timeout: float) -> bool:
        return self._done.wait(timeout)


__all__ = ["DashboardHealth", "HealthProbe"]
