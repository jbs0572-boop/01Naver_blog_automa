from __future__ import annotations

import atexit
import json
import os
import re
import select
import shutil
import subprocess
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from tools.contract_types import ContractError, JSONValue

type AsideCommandRunner = Callable[[tuple[str, ...], float], str]

_RESULT_MARKER = "__ASIDE_RESULT__"
_ERROR_MARKER = "__ASIDE_ERROR__"
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")
_REPL_ERROR = re.compile(r"^(?:Error|TimeoutError|ReferenceError|TypeError):\s*(.+)$")


class _StaleReplError(ContractError): ...


@dataclass(frozen=True, slots=True)
class AsideCliConfig:
    executable: Path
    account: str | None = None
    timeout_seconds: float = 30.0
    upload_root: Path | None = None
    full_access: bool = False


def resolve_aside_cli() -> Path:
    configured = shutil.which("aside")
    if configured is not None:
        return Path(configured)
    fallback = Path.home() / ".local" / "bin" / "aside"
    if fallback.is_symlink() and not fallback.exists():
        raise ContractError("Aside CLI installation link is broken")
    if fallback.is_file() and os.access(fallback, os.X_OK):
        return fallback
    if fallback.exists():
        raise ContractError("Aside CLI installation is not executable")
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


@dataclass(slots=True)
class AsideReplSession:
    config: AsideCliConfig
    command_runner: AsideCommandRunner = _run_command
    _process: subprocess.Popen[bytes] | None = field(
        default=None, init=False, repr=False
    )
    _process_lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False
    )

    def __post_init__(self) -> None:
        if self.command_runner is _run_command:
            _ = atexit.register(self.close)

    def close(self) -> None:
        with self._process_lock:
            self._stop_persistent_process()

    def goto(self, url: str, timeout_ms: int) -> None:
        _ = self._execute(
            f"await page.goto({json.dumps(url)}, {{waitUntil: 'domcontentloaded', timeout: {timeout_ms}}});"
        )

    def fill(self, selector: str, value: str) -> None:
        _ = self._execute(
            f"await page.locator({json.dumps(selector)}).fill({json.dumps(value)});"
        )

    def click(self, selector: str) -> None:
        _ = self._execute(f"await page.locator({json.dumps(selector)}).click();")

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

    def run_json(
        self, operation: str, *, replay_on_stale: bool = True
    ) -> JSONValue:
        output = self._execute(
            operation, attach_active=False, replay_on_stale=replay_on_stale
        )
        saw_result_marker = False
        for line in output.splitlines():
            cleaned_line = _ANSI_ESCAPE.sub("", line)
            marker_index = cleaned_line.find(_RESULT_MARKER)
            if marker_index < 0:
                continue
            saw_result_marker = True
            raw = cleaned_line[marker_index + len(_RESULT_MARKER) :]
            if raw == "__ok":
                continue
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                continue
        cleaned = _ANSI_ESCAPE.sub("", output)
        error_index = cleaned.find(_ERROR_MARKER)
        if error_index >= 0:
            detail = cleaned[error_index + len(_ERROR_MARKER) :].splitlines()[0]
            if detail.startswith("Naver layout mismatch:"):
                detail = "Naver layout mismatch"
            raise ContractError(f"Aside Browser execution failed: {detail[:400]}")
        for line in cleaned.splitlines():
            match = _REPL_ERROR.match(line.strip())
            if match is not None:
                detail = match.group(1)
                if detail.startswith("Naver layout mismatch:"):
                    detail = "Naver layout mismatch"
                raise ContractError(
                    f"Aside Browser execution failed: {detail[:400]}"
                )
        if saw_result_marker:
            raise ContractError("Aside CLI returned invalid JSON")
        raise ContractError("Aside CLI returned no JSON result")

    def stage_uploads(self, files: tuple[Path, ...], namespace: str) -> dict[str, Path]:
        if re.fullmatch(r"[A-Za-z0-9._-]+", namespace) is None:
            raise ContractError("Aside upload namespace is invalid")
        root = self.config.upload_root or (
            Path.home()
            / ".aside"
            / "u"
            / (self.config.account or "0")
            / "tmp"
            / "naver-uploads"
        )
        destination = root / namespace
        destination.mkdir(parents=True, exist_ok=True)
        staged: dict[str, Path] = {}
        for source in files:
            if not source.is_file() or source.name in staged:
                raise ContractError("Aside upload source is missing or duplicated")
            target = destination / source.name
            _ = shutil.copy2(source, target)
            staged[source.name] = target
        return staged

    def evidence_path(self, namespace: str, filename: str) -> Path:
        if (
            re.fullmatch(r"[A-Za-z0-9._-]+", namespace) is None
            or Path(filename).name != filename
        ):
            raise ContractError("Aside evidence path is invalid")
        root = self.config.upload_root or (
            Path.home()
            / ".aside"
            / "u"
            / (self.config.account or "0")
            / "tmp"
            / "naver-uploads"
        )
        destination = root / namespace
        destination.mkdir(parents=True, exist_ok=True)
        return destination / filename

    def _execute(
        self,
        operation: str,
        *,
        attach_active: bool = True,
        replay_on_stale: bool = True,
    ) -> str:
        prelude = (
            "var page = await attachActiveBrowserTab();\n" if attach_active else ""
        )
        script = (
            prelude + f"{operation}\n" + "console.log('" + _RESULT_MARKER + "__ok');"
        )
        arguments = [str(self.config.executable)]
        if self.config.full_access:
            arguments.extend(("--permission", "full-access"))
        arguments.append("repl")
        if self.config.account is not None:
            arguments.extend(("--account", self.config.account))
        if self.command_runner is not _run_command:
            arguments.append(script)
            return self.command_runner(tuple(arguments), self.config.timeout_seconds)
        return self._execute_persistent(
            arguments, script, replay_on_stale=replay_on_stale
        )

    def _execute_persistent(
        self,
        arguments: list[str],
        script: str,
        *,
        replay_on_stale: bool,
    ) -> str:
        with self._process_lock:
            try:
                return self._execute_persistent_once(arguments, script)
            except _StaleReplError as error:
                if not replay_on_stale:
                    raise ContractError(
                        "Aside Browser outcome is uncertain; operation was not replayed"
                    ) from error
                return self._execute_persistent_once(arguments, script)

    def _execute_persistent_once(self, arguments: list[str], script: str) -> str:
        process = self._process
        if process is None or process.poll() is not None:
            process = subprocess.Popen(
                arguments,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            self._process = process
        if process.stdin is None or process.stdout is None:
            raise ContractError("Aside CLI persistent REPL is unavailable")
        end_marker = "__ASIDE_END__" + uuid.uuid4().hex
        marker_prefix, marker_suffix = end_marker[:12], end_marker[12:]
        try:
            program = (
                "(async()=>{try{\n"
                + script
                + "\n}catch(error){console.log('__ASIDE_' + 'ERROR__' + "
                + "String(error?.message ?? error));}finally{"
                + f"console.log('{marker_prefix}' + '{marker_suffix}');}}}})()"
            )
            command = ("await eval(" + json.dumps(program) + ")\n").encode()
            _ = process.stdin.write(command)
            process.stdin.flush()
        except (BrokenPipeError, OSError) as error:
            self._stop_persistent_process()
            raise _StaleReplError("Aside CLI persistent REPL stopped") from error
        output = bytearray()
        marker = end_marker.encode()
        deadline = time.monotonic() + self.config.timeout_seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._stop_persistent_process()
                raise ContractError("Aside CLI command timed out")
            readable, _, _ = select.select([process.stdout], [], [], remaining)
            if not readable:
                self._stop_persistent_process()
                raise ContractError("Aside CLI command timed out")
            try:
                chunk = os.read(process.stdout.fileno(), 65536)
            except OSError as error:
                self._stop_persistent_process()
                raise _StaleReplError("Aside CLI persistent REPL stopped") from error
            if not chunk:
                self._stop_persistent_process()
                raise _StaleReplError("Aside CLI persistent REPL stopped")
            output.extend(chunk)
            marker_index = output.find(marker)
            if marker_index >= 0:
                return output[:marker_index].decode(errors="replace")

    def _stop_persistent_process(self) -> None:
        process = self._process
        self._process = None
        if process is not None and process.poll() is None:
            process.terminate()


__all__ = ["AsideCliConfig", "AsideReplSession", "resolve_aside_cli"]
