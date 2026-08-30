from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Final, override

MAX_ATTEMPTS: Final = 3
INITIAL_BACKOFF_SECONDS: Final = 1.0


@dataclass(frozen=True, slots=True)
class CodexProcessError(Exception):
    reason: str

    @override
    def __str__(self) -> str:
        return self.reason


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
) -> None:
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
        except (OSError, subprocess.TimeoutExpired) as error:
            raise CodexProcessError("codex process failed or timed out") from error
        log_path = work_dir / f"codex-attempt-{attempt}.jsonl"
        try:
            _ = log_path.write_text(completed.stdout, encoding="utf-8")
        except OSError as error:
            raise CodexProcessError(
                f"codex attempt log cannot be written: {log_path}"
            ) from error
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


__all__ = ["CodexProcessError", "run_codex"]
