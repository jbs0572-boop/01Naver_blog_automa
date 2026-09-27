from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tools.codex_process import CodexProcessError, SensitiveValueRedactor, run_codex


def test_attempt_log_redacts_an_exact_opaque_keychain_token_with_punctuation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: the approved Keychain item is an opaque printable ASCII value rather
    # than a token with a recognizable vendor prefix.
    token = r"notion!opaque$token[]{}^.,;:=?/\\|~`\"<>"

    def completed(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess([], 1, f"stdout={token}; stderr={token}", "")

    monkeypatch.setattr("tools.codex_process.subprocess.run", completed)

    # When: Codex output is persisted after a failed stage.
    with pytest.raises(CodexProcessError):
        run_codex(
            ["codex", "exec"],
            root=tmp_path,
            environment={},
            timeout=1,
            work_dir=tmp_path,
            sensitive_values=(token,),
        )

    # Then: no original token or punctuation-only suffix remains on disk.
    persisted = (tmp_path / "attempt-1" / "codex-attempt-1.jsonl").read_text(encoding="utf-8")
    assert token not in persisted
    assert token[7:] not in persisted
    assert persisted.count("[redacted-notion-token]") == 2


def test_stage_rejects_loaded_token_in_command_or_environment_before_exec(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a loaded opaque token accidentally reaches a process boundary.
    token = "opaque!token-with-punctuation?"

    def should_not_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise AssertionError("credential-bearing command must not execute")

    monkeypatch.setattr("tools.codex_process.subprocess.run", should_not_run)

    # When: the token appears in an argv element or an environment value.
    with pytest.raises(CodexProcessError) as command_error:
        run_codex(
            ["codex", "exec", token],
            root=tmp_path,
            environment={},
            timeout=1,
            work_dir=tmp_path,
            sensitive_values=(token,),
        )
    with pytest.raises(CodexProcessError) as environment_error:
        run_codex(
            ["codex", "exec"],
            root=tmp_path,
            environment={"NOTION_TOKEN": token},
            timeout=1,
            work_dir=tmp_path,
            sensitive_values=(token,),
        )

    # Then: neither failure can reveal the token or leave an attempt artifact.
    assert token not in str(command_error.value)
    assert token not in str(environment_error.value)
    assert not (tmp_path / "attempt-1" / "codex-attempt-1.jsonl").exists()


def test_exact_value_redactor_hides_values_from_its_representation() -> None:
    # Given: a redactor holding an opaque credential.
    token = "opaque!token-with-punctuation?"

    # When: diagnostic rendering occurs.
    rendered = repr(SensitiveValueRedactor((token,)))

    # Then: the value cannot escape through the redactor representation.
    assert token not in rendered


def test_redactor_replaces_overlapping_sensitive_values_without_suffix_leak() -> None:
    # Given: one credential is a strict prefix of another credential.
    short = "opaque!token"
    long = "opaque!token-with-punctuation?"

    # When: output contains the longer credential.
    redacted = SensitiveValueRedactor((short, long)).redact(long)

    # Then: the prefix replacement cannot leave the longer-value suffix behind.
    assert redacted == "[redacted-notion-token]"
    assert "-with-punctuation?" not in redacted


def test_attempt_log_redacts_notion_tokens_before_persisting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: Codex emits both current and legacy Notion token shapes.
    raw = "ntn_currentToken123 secret_legacyToken456\n"

    def completed(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess([], 1, raw, "")

    monkeypatch.setattr("tools.codex_process.subprocess.run", completed)

    # When: the failed attempt is captured.
    with pytest.raises(CodexProcessError) as captured:
        run_codex(
            ["codex", "exec"],
            root=tmp_path,
            environment={},
            timeout=1,
            work_dir=tmp_path,
        )

    # Then: neither the disk artifact nor the surfaced error contains a token.
    persisted = (tmp_path / "attempt-1" / "codex-attempt-1.jsonl").read_text(encoding="utf-8")
    assert "ntn_" not in persisted
    assert "secret_" not in persisted
    assert "currentToken123" not in persisted
    assert "legacyToken456" not in persisted
    assert "[redacted-notion-token]" in persisted
    assert raw.strip() not in str(captured.value)


def test_rate_limit_detection_uses_raw_output_before_redaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: the first raw response contains a rate limit and a token.
    responses = iter(
        (
            subprocess.CompletedProcess([], 1, "429 ntn_sensitiveValue", ""),
            subprocess.CompletedProcess([], 0, "ok", ""),
        )
    )

    def completed(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return next(responses)

    def skip_backoff(_seconds: float) -> None:
        return None

    monkeypatch.setattr("tools.codex_process.subprocess.run", completed)
    monkeypatch.setattr("tools.codex_process.time.sleep", skip_backoff)

    # When: Codex retries the rate-limited attempt.
    run_codex(
        ["codex", "exec"],
        root=tmp_path,
        environment={},
        timeout=1,
        work_dir=tmp_path,
    )

    # Then: a retry occurred, while the raw token was never persisted.
    first = (tmp_path / "attempt-1" / "codex-attempt-1.jsonl").read_text(encoding="utf-8")
    second = (tmp_path / "attempt-1" / "codex-attempt-2.jsonl").read_text(encoding="utf-8")
    assert first == "429 [redacted-notion-token]"
    assert second == "ok"
