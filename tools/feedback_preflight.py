from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from tools.contract_types import ContractError

type BrowserFailureReason = Literal["staging_mismatch", "cdp_timeout"]
type OperationalBrowserStatus = Literal["ready", "browser_preflight_failed"]


@dataclass(frozen=True, slots=True)
class OperationalBrowserPreflight:
    operational_browser_status: OperationalBrowserStatus
    failure_reason: BrowserFailureReason | None
    schema_manual_import_ready: bool
    real_browser_enabled: bool
    browser_attempts: int = 0
    browser_retries: int = 0

    def as_json(self) -> dict[str, str | bool | int | None]:
        return {
            "browser_attempts": self.browser_attempts,
            "browser_retries": self.browser_retries,
            "failure_reason": self.failure_reason,
            "operational_browser_status": self.operational_browser_status,
            "real_browser_enabled": self.real_browser_enabled,
            "schema_manual_import_ready": self.schema_manual_import_ready,
        }


def classify_operational_browser_status(
    error_message: str | None,
) -> OperationalBrowserPreflight:
    if error_message is None:
        return OperationalBrowserPreflight("ready", None, True, True)
    if error_message == "staging contains undeclared or missing artifacts":
        reason: BrowserFailureReason = "staging_mismatch"
    elif error_message.startswith(
        "Aside Browser execution failed: CDP command timeout: "
    ) and error_message.removeprefix(
        "Aside Browser execution failed: CDP command timeout: "
    ):
        reason = "cdp_timeout"
    else:
        raise ContractError("operational browser evidence is unrecognized")
    return OperationalBrowserPreflight(
        "browser_preflight_failed", reason, True, False
    )


def operational_browser_preflight(root: Path) -> OperationalBrowserPreflight:
    relative = os.environ.get("NAVER_OPERATIONAL_BROWSER_FAILURE")
    if relative is None:
        return classify_operational_browser_status(None)
    path = Path(relative)
    if not relative or path.is_absolute() or ".." in path.parts:
        raise ContractError("operational browser evidence path is unsafe")
    try:
        evidence_path = (root.resolve() / path).resolve()
        _ = evidence_path.relative_to(root.resolve())
        message = evidence_path.read_text(encoding="utf-8").strip()
    except (OSError, ValueError) as error:
        raise ContractError("operational browser evidence is unreadable") from error
    return classify_operational_browser_status(message)


def daily_launchd_contract_error(path: Path) -> str | None:
    try:
        plist_text = path.read_text(encoding="utf-8")
    except OSError:
        return "daily_schedule_unreadable"
    if "<string>--as-of-date</string>" not in plist_text:
        return "daily_schedule_requires_human_as_of_date"
    return None
