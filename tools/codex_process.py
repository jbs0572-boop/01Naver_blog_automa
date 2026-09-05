from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, override

MAX_ATTEMPTS: Final = 3
INITIAL_BACKOFF_SECONDS: Final = 1.0
NOTION_TOKEN_PATTERN: Final = re.compile(r"(?:ntn_|secret_)[A-Za-z0-9_-]+")


@dataclass(frozen=True, slots=True)
class CodexProcessError(Exception):
    reason: str

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


def run_codex(
    command: list[str],
    *,
    root: Path,
    environment: dict[str, str],
    timeout: int,
    work_dir: Path,
    sensitive_values: tuple[str, ...] = (),
) -> None:
    redactor = SensitiveValueRedactor(sensitive_values)
    if any(redactor.contains(argument) for argument in command) or any(
        redactor.contains(value) for value in environment.values()
    ):
        raise CodexProcessError("sensitive value cannot be passed to a Codex process")
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
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
        except (OSError, subprocess.TimeoutExpired):
            raise CodexProcessError("codex process failed or timed out") from None
        log_path = work_dir / f"codex-attempt-{attempt}.jsonl"
        try:
            _ = log_path.write_text(
                redactor.redact(completed.stdout), encoding="utf-8"
            )
        except OSError:
            raise CodexProcessError(
                f"codex attempt log cannot be written: {log_path}"
            ) from None
        if completed.returncode == 0:
            return
        rate_limited = _is_rate_limited(completed.stdout)
        if rate_limited and attempt < MAX_ATTEMPTS:
            time.sleep(INITIAL_BACKOFF_SECONDS * (2 ** (attempt - 1)))
            continue
        if rate_limited:
            raise CodexProcessError(
                f"codex rate limited (429) after {attempt} attempts; output: {log_path}"
            )
        raise CodexProcessError(
            f"codex exited with code {completed.returncode}; output: {log_path}"
        )
    raise CodexProcessError("codex retry loop did not complete")


__all__ = ["CodexProcessError", "SensitiveValueRedactor", "run_codex"]
