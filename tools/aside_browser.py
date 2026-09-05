from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from tools.contract_types import ContractError

type AsideCommandRunner = Callable[[tuple[str, ...], float], str]

_RESULT_MARKER = "__ASIDE_RESULT__"


@dataclass(frozen=True, slots=True)
class AsideCliConfig:
    executable: Path
    account: str | None = None
    timeout_seconds: float = 30.0


def resolve_aside_cli() -> Path:
    configured = shutil.which("aside")
    if configured is not None:
        return Path(configured)
    fallback = Path.home() / ".local" / "bin" / "aside"
    if fallback.is_file():
        return fallback
    raise ContractError("Aside CLI executable was not found")


def _run_command(arguments: tuple[str, ...], timeout_seconds: float) -> str:
    try:
        completed = subprocess.run(
            arguments,
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout_seconds,
        )
    except FileNotFoundError as error:
        raise ContractError("Aside CLI executable was not found") from error
    except subprocess.TimeoutExpired as error:
        raise ContractError("Aside CLI command timed out") from error
    if completed.returncode != 0:
        raise ContractError("Aside CLI command failed")
    return completed.stdout


@dataclass(frozen=True, slots=True)
class AsideReplSession:
    config: AsideCliConfig
    command_runner: AsideCommandRunner = _run_command

    def goto(self, url: str, timeout_ms: int) -> None:
        _ = self._execute(
            f"await page.goto({json.dumps(url)}, {{waitUntil: 'domcontentloaded', timeout: {timeout_ms}}});"
        )

    def fill(self, selector: str, value: str) -> None:
        _ = self._execute(
            f"await page.locator({json.dumps(selector)}).fill({json.dumps(value)});"
        )

    def click(self, selector: str) -> None:
        _ = self._execute(
            f"await page.locator({json.dumps(selector)}).click();"
        )

    def text(self, selector: str) -> str:
        output = self._execute(
            "const value = await page.locator("
            + f"{json.dumps(selector)}).textContent();"
            + f"console.log({_RESULT_MARKER!r} + JSON.stringify(value ?? ''));"
        )
        marker = _RESULT_MARKER
        for line in output.splitlines():
            if line.startswith(marker):
                value = json.loads(line.removeprefix(marker))
                if isinstance(value, str):
                    return value
                raise ContractError("Aside CLI returned non-text browser content")
        raise ContractError("Aside CLI returned no browser text")

    def screenshot(self, path: str) -> None:
        _ = self._execute(
            f"await page.screenshot({{path: {json.dumps(path)}, fullPage: true}});"
        )

    def _execute(self, operation: str) -> str:
        script = (
            "var page = await attachActiveBrowserTab();\n"
            + f"{operation}\n"
            + "console.log('"
            + _RESULT_MARKER
            + "__ok');"
        )
        arguments = [str(self.config.executable), "repl"]
        if self.config.account is not None:
            arguments.extend(("--account", self.config.account))
        arguments.append(script)
        return self.command_runner(tuple(arguments), self.config.timeout_seconds)


__all__ = ["AsideCliConfig", "AsideReplSession", "resolve_aside_cli"]
