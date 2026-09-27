from __future__ import annotations

import codecs
import os
import re
import selectors
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, override

from tools.codex_stage_error import StageFailureType, failure_policy

MAX_ATTEMPTS: Final = 3
INITIAL_BACKOFF_SECONDS: Final = 1.0
TIMEOUT_BACKOFF_SECONDS: Final = 10.0
NOTION_TOKEN_PATTERN: Final = re.compile(r"(?:ntn_|secret_)[A-Za-z0-9_-]+")
OUTPUT_TAIL_LIMIT: Final = 1_000_000
_ORIGINAL_RUN = subprocess.run


@dataclass(frozen=True, slots=True)
class CodexProcessError(Exception):
    error_type: str
    reason: str
    retryable: bool
    next_action: str
    attempts: int

    @override
    def __str__(self) -> str:
        return self.reason


@dataclass(frozen=True, slots=True)
class SensitiveValueRedactor:
    values: tuple[str, ...] = field(repr=False)

    def redact(self, output: str) -> str:
        redacted = output
        for value in sorted(
            (value for value in self.values if value), key=len, reverse=True
        ):
            redacted = redacted.replace(value, "[redacted-notion-token]")
        return NOTION_TOKEN_PATTERN.sub("[redacted-notion-token]", redacted)

    def contains(self, value: str) -> bool:
        return any(sensitive and sensitive in value for sensitive in self.values)


def _is_rate_limited(output: str) -> bool:
    normalized = output.casefold()
    return (
        "429" in normalized
        or "too many requests" in normalized
        or "rate limit" in normalized
    )


def _is_invalid_configuration(output: str) -> bool:
    normalized = output.casefold()
    return any(
        marker in normalized
        for marker in (
            "invalid config",
            "unknown model",
            "profile not found",
            "configuration error",
        )
    )


def _error(
    error_type: StageFailureType, reason: str, attempts: int
) -> CodexProcessError:
    policy = failure_policy(error_type.value)
    if policy is None:
        raise AssertionError(f"missing failure policy: {error_type}")
    return CodexProcessError(
        error_type.value,
        reason,
        policy.retryable,
        policy.next_action,
        attempts,
    )


def _timeout_output(error: subprocess.TimeoutExpired) -> str:
    output = error.stdout
    if isinstance(output, bytes):
        return output.decode("utf-8", errors="replace")
    return output or ""


def _write_attempt_log(
    path: Path, output: str, redactor: SensitiveValueRedactor
) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8") as handle:
            _ = handle.write(redactor.redact(output))
            handle.flush()
    except OSError as error:
        raise _error(
            StageFailureType.TEMPORARY_IO,
            f"codex attempt log cannot be written: {path}",
            0,
        ) from error


def _kill_process_group(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except OSError:
        process.kill()


def _stream_attempt(
    command: list[str],
    *,
    root: Path,
    environment: dict[str, str],
    timeout: int,
    path: Path,
    redactor: SensitiveValueRedactor,
) -> tuple[subprocess.CompletedProcess[str], bool]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        process = subprocess.Popen(
            command,
            cwd=root,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        assert process.stdout is not None
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        pending = ""
        output_tail = ""
        deadline = time.monotonic() + timeout
        selector = selectors.DefaultSelector()
        _ = selector.register(process.stdout, selectors.EVENT_READ)
        timed_out = False
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                _kill_process_group(process)
                remaining = 0
            events = selector.select(min(0.1, max(0, remaining)))
            for key, _mask in events:
                chunk = os.read(key.fd, 65_536)
                if not chunk:
                    _ = selector.unregister(key.fileobj)
                    continue
                pending += decoder.decode(chunk)
                lines = pending.splitlines(keepends=True)
                pending = ""
                if lines and not lines[-1].endswith(("\n", "\r")):
                    pending = lines.pop()
                for line in lines:
                    clean = redactor.redact(line)
                    _ = handle.write(clean)
                    handle.flush()
                    output_tail = (output_tail + clean)[-OUTPUT_TAIL_LIMIT:]
            if timed_out and process.poll() is not None and not events:
                break
        pending += decoder.decode(b"", final=True)
        if pending:
            clean = redactor.redact(pending)
            _ = handle.write(clean)
            handle.flush()
            output_tail = (output_tail + clean)[-OUTPUT_TAIL_LIMIT:]
        returncode = process.wait()
    return subprocess.CompletedProcess(command, returncode, output_tail, ""), timed_out


def run_codex(
    command: list[str],
    *,
    root: Path,
    environment: dict[str, str],
    timeout: int,
    work_dir: Path,
    sensitive_values: tuple[str, ...] = (),
    stage_attempt: int = 1,
    max_attempts: int = MAX_ATTEMPTS,
) -> None:
    redactor = SensitiveValueRedactor(sensitive_values)
    if any(redactor.contains(argument) for argument in command) or any(
        redactor.contains(value) for value in environment.values()
    ):
        raise _error(
            StageFailureType.CONTRACT_FAILED,
            "sensitive value cannot be passed to a Codex process",
            0,
        )
    bounded_attempts = max(1, min(max_attempts, MAX_ATTEMPTS))
    attempt_dir = work_dir / f"attempt-{stage_attempt}"
    prior_attempts = [
        int(match.group(1))
        for path in attempt_dir.glob("codex-attempt-*.jsonl")
        if (
            match := re.fullmatch(
                r"codex-attempt-(\d+)\.jsonl", path.name
            )
        )
    ]
    first_process_attempt = max(prior_attempts, default=0) + 1
    timeout_retried = False
    for call_index in range(1, bounded_attempts + 1):
        process_attempt = first_process_attempt + call_index - 1
        log_path = attempt_dir / f"codex-attempt-{process_attempt}.jsonl"
        streamed_timeout = False
        try:
            if subprocess.run is _ORIGINAL_RUN:
                completed, streamed_timeout = _stream_attempt(
                    command,
                    root=root,
                    environment=environment,
                    timeout=timeout,
                    path=log_path,
                    redactor=redactor,
                )
            else:
                completed = subprocess.run(
                    command,
                    cwd=root,
                    env=environment,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    timeout=timeout,
                    check=False,
                )
        except subprocess.TimeoutExpired as error:
            _write_attempt_log(log_path, _timeout_output(error), redactor)
            if not timeout_retried and call_index < bounded_attempts:
                timeout_retried = True
                time.sleep(TIMEOUT_BACKOFF_SECONDS)
                continue
            raise _error(
                StageFailureType.PROCESS_TIMEOUT,
                f"codex process timed out after {call_index} attempts; output: {log_path}",
                call_index,
            ) from error
        except FileNotFoundError as error:
            raise _error(
                StageFailureType.PROCESS_UNAVAILABLE,
                "codex process is unavailable",
                call_index,
            ) from error
        except OSError as error:
            raise _error(
                StageFailureType.TEMPORARY_IO,
                f"codex process could not start: {error}",
                call_index,
            ) from error
        if streamed_timeout:
            if not timeout_retried and call_index < bounded_attempts:
                timeout_retried = True
                time.sleep(TIMEOUT_BACKOFF_SECONDS)
                continue
            raise _error(
                StageFailureType.PROCESS_TIMEOUT,
                f"codex process timed out after {call_index} attempts; output: {log_path}",
                call_index,
            )
        if subprocess.run is not _ORIGINAL_RUN:
            _write_attempt_log(log_path, completed.stdout, redactor)
        if completed.returncode == 0:
            return
        rate_limited = _is_rate_limited(completed.stdout)
        if rate_limited and call_index < bounded_attempts:
            time.sleep(INITIAL_BACKOFF_SECONDS * (2 ** (call_index - 1)))
            continue
        if rate_limited:
            raise _error(
                StageFailureType.RATE_LIMITED,
                f"codex rate limited (429) after {call_index} attempts; output: {log_path}",
                call_index,
            )
        error_type = (
            StageFailureType.INVALID_CONFIGURATION
            if _is_invalid_configuration(completed.stdout)
            else StageFailureType.CONTRACT_FAILED
        )
        raise _error(
            error_type,
            f"codex exited with code {completed.returncode}; output: {log_path}",
            call_index,
        )
    raise _error(
        StageFailureType.CONTRACT_FAILED,
        "codex retry loop did not complete",
        bounded_attempts,
    )


__all__ = ["CodexProcessError", "SensitiveValueRedactor", "run_codex"]
