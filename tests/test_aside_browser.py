from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Literal, final

import pytest

from tools.aside_browser import AsideCliConfig, AsideReplSession, resolve_aside_cli
from tools.contract_types import ContractError


def _missing_which(_name: str) -> None:
    return None

type _HarnessMode = Literal[
    "success", "first_eof", "always_eof", "semantic", "timeout"
]


@final
class _FakeStdin:
    def __init__(self) -> None:
        self.commands: list[bytes] = []

    def write(self, command: bytes) -> int:
        self.commands.append(command)
        return len(command)

    def flush(self) -> None:
        pass


@final
class _FakeStdout:
    def __init__(self, descriptor: int) -> None:
        self._descriptor = descriptor

    def fileno(self) -> int:
        return self._descriptor


@final
class _FakeProcess:
    def __init__(self, descriptor: int) -> None:
        self.stdin = _FakeStdin()
        self.stdout = _FakeStdout(descriptor)
        self.terminated = False

    def poll(self) -> int | None:
        return -15 if self.terminated else None

    def terminate(self) -> None:
        self.terminated = True


def _end_marker(process: _FakeProcess) -> bytes:
    command = process.stdin.commands[-1]
    matches = re.findall(rb"console\.log\('([^']+)' \+ '([^']+)'\)", command)
    assert matches
    prefix, suffix = matches[-1]
    return prefix + suffix


def _successful_read(process: _FakeProcess) -> bytes:
    return b'__ASIDE_RESULT__{"status":"ok"}\n' + _end_marker(process)


@final
class _PersistentReplHarness:
    mode: _HarnessMode

    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mode: _HarnessMode,
    ) -> None:
        self.mode = mode
        self.processes: list[_FakeProcess] = []
        monkeypatch.setattr("tools.aside_browser.subprocess.Popen", self.popen)
        monkeypatch.setattr("tools.aside_browser.select.select", self.select)
        monkeypatch.setattr("tools.aside_browser.os.read", self.read)

    def popen(
        self, _arguments: list[str], *, stdin: int, stdout: int, stderr: int
    ) -> _FakeProcess:
        _ = (stdin, stdout, stderr)
        process = _FakeProcess(len(self.processes) + 10)
        self.processes.append(process)
        return process

    def select(
        self,
        reads: list[_FakeStdout],
        _writes: list[_FakeStdout],
        _errors: list[_FakeStdout],
        _timeout: float,
    ) -> tuple[list[_FakeStdout], list[_FakeStdout], list[_FakeStdout]]:
        return ([], [], []) if self.mode == "timeout" else (reads, [], [])

    def read(self, descriptor: int, _size: int) -> bytes:
        process = next(
            item for item in self.processes if item.stdout.fileno() == descriptor
        )
        mode = self.mode
        match mode:
            case "success":
                return _successful_read(process)
            case "first_eof":
                return (
                    b""
                    if process is self.processes[0]
                    else _successful_read(process)
                )
            case "always_eof":
                return b""
            case "semantic":
                return b"__ASIDE_ERROR__semantic failure\n" + _end_marker(process)
            case "timeout":
                raise AssertionError("timeout mode must not read stdout")


def test_repl_session_runs_deterministic_browser_script() -> None:
    calls: list[tuple[str, ...]] = []

    def runner(arguments: tuple[str, ...], _timeout: float) -> str:
        calls.append(arguments)
        return '__ASIDE_RESULT__"로그인됨"\n'

    session = AsideReplSession(
        AsideCliConfig(Path("/usr/local/bin/aside"), account="work"), runner
    )

    session.goto("https://blog.naver.com/sola_note", timeout_ms=12_000)
    assert session.text("#auth") == "로그인됨"
    assert calls[0][1:4] == ("repl", "--account", "work")
    assert "page.goto" in calls[0][-1]
    assert "page.locator" in calls[1][-1]


def test_resolver_uses_executable_fallback_when_aside_is_missing_from_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Given: PATH has no Aside command, but the user installation is executable.
    fallback = tmp_path / ".local" / "bin" / "aside"
    fallback.parent.mkdir(parents=True)
    fallback.touch(mode=0o700)
    monkeypatch.setattr(shutil, "which", _missing_which)
    monkeypatch.setattr("tools.aside_browser.Path.home", lambda: tmp_path)

    # When: the project resolves the Aside executable.
    resolved = resolve_aside_cli()

    # Then: the existing installation is returned without requiring installation.
    assert resolved == fallback


def test_resolver_rejects_non_executable_fallback_as_invalid_installation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Given: PATH has no Aside command and the fallback path is not executable.
    fallback = tmp_path / ".local" / "bin" / "aside"
    fallback.parent.mkdir(parents=True)
    fallback.touch(mode=0o600)
    monkeypatch.setattr(shutil, "which", _missing_which)
    monkeypatch.setattr("tools.aside_browser.Path.home", lambda: tmp_path)

    # When/Then: resolution reports the invalid installation boundary.
    with pytest.raises(ContractError, match="not executable"):
        _ = resolve_aside_cli()


def test_repl_session_rejects_failed_cli() -> None:
    def runner(_arguments: tuple[str, ...], _timeout: float) -> str:
        raise ContractError("Aside CLI command failed")

    session = AsideReplSession(AsideCliConfig(Path("aside")), runner)

    with pytest.raises(ContractError, match="Aside CLI command failed"):
        session.click("button.save")


def test_repl_session_runs_json_without_active_tab_prelude() -> None:
    calls: list[tuple[str, ...]] = []

    def runner(arguments: tuple[str, ...], _timeout: float) -> str:
        calls.append(arguments)
        return '__ASIDE_RESULT__{"status":"ok"}\n__ASIDE_RESULT____ok\n'

    session = AsideReplSession(AsideCliConfig(Path("aside")), runner)

    assert session.run_json(
        "console.log('__ASIDE_RESULT__' + JSON.stringify({status: 'ok'}));"
    ) == {"status": "ok"}
    assert "attachActiveBrowserTab" not in calls[0][-1]


def test_repl_session_surfaces_aside_javascript_errors() -> None:
    def runner(_arguments: tuple[str, ...], _timeout: float) -> str:
        return "\x1b[31mError: Naver table control was not found\n    at repl.js:1:7\x1b[0m\n"

    session = AsideReplSession(AsideCliConfig(Path("aside")), runner)

    with pytest.raises(ContractError, match="Naver table control was not found"):
        _ = session.run_json("throw new Error('failure')")


def test_repl_session_places_full_access_permission_before_repl_command() -> None:
    calls: list[tuple[str, ...]] = []

    def runner(arguments: tuple[str, ...], _timeout: float) -> str:
        calls.append(arguments)
        return '__ASIDE_RESULT__{"status":"ok"}\n'

    session = AsideReplSession(
        AsideCliConfig(Path("aside"), full_access=True), runner
    )

    assert session.run_json(
        "console.log('__ASIDE_RESULT__' + JSON.stringify({status: 'ok'}));"
    ) == {"status": "ok"}
    assert calls[0][1:4] == ("--permission", "full-access", "repl")


def test_persistent_repl_reuses_one_process_for_multiple_operations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: one healthy persistent Aside process.
    harness = _PersistentReplHarness(monkeypatch, "success")
    session = AsideReplSession(AsideCliConfig(Path("aside")))

    # When: two operations run in sequence.
    first = session.run_json("const operation = 'first';")
    second = session.run_json("const operation = 'second';")

    # Then: both operations share one live process until explicit cleanup.
    assert first == second == {"status": "ok"}
    assert len(harness.processes) == 1
    assert len(harness.processes[0].stdin.commands) == 2
    assert not harness.processes[0].terminated
    session.close()
    assert harness.processes[0].terminated


def test_persistent_repl_reconnects_once_after_stale_process_eof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the daemon has discarded the first persistent REPL session.
    harness = _PersistentReplHarness(monkeypatch, "first_eof")
    session = AsideReplSession(AsideCliConfig(Path("aside")))

    # When: the operation encounters EOF from that stale process.
    result = session.run_json("const operation = 'save';")

    # Then: the exact operation succeeds once on a replacement and both are cleaned up.
    assert result == {"status": "ok"}
    assert len(harness.processes) == 2
    assert b"const operation = 'save';" in harness.processes[0].stdin.commands[0]
    assert b"const operation = 'save';" in harness.processes[1].stdin.commands[0]
    assert harness.processes[0].terminated
    session.close()
    assert harness.processes[1].terminated


def test_persistent_repl_does_not_replay_non_idempotent_operation_after_eof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a save operation may have completed before its stale REPL disappears.
    harness = _PersistentReplHarness(monkeypatch, "first_eof")
    session = AsideReplSession(AsideCliConfig(Path("aside")))

    # When/Then: an uncertain save is surfaced without issuing it a second time.
    with pytest.raises(ContractError, match="outcome is uncertain"):
        _ = session.run_json(
            "const operation = 'save';", replay_on_stale=False
        )
    assert len(harness.processes) == 1
    assert len(harness.processes[0].stdin.commands) == 1
    assert b"const operation = 'save';" in harness.processes[0].stdin.commands[0]
    assert harness.processes[0].terminated


def test_persistent_repl_does_not_reconnect_after_semantic_browser_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a healthy transport returns a semantic browser error.
    harness = _PersistentReplHarness(monkeypatch, "semantic")
    session = AsideReplSession(AsideCliConfig(Path("aside")))

    # When/Then: the semantic error is surfaced without replaying the operation.
    with pytest.raises(ContractError, match="semantic failure"):
        _ = session.run_json("throw new Error('semantic failure');")
    assert len(harness.processes) == 1
    session.close()
    assert harness.processes[0].terminated


def test_persistent_repl_does_not_reconnect_after_command_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a live persistent process never produces readable output.
    harness = _PersistentReplHarness(monkeypatch, "timeout")
    session = AsideReplSession(AsideCliConfig(Path("aside")))

    # When/Then: the timeout is surfaced and its process is cleaned without replay.
    with pytest.raises(ContractError, match="Aside CLI command timed out"):
        _ = session.run_json("const operation = 'slow';")
    assert len(harness.processes) == 1
    assert harness.processes[0].terminated


def test_persistent_repl_reconnects_at_most_once_after_repeated_eof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: both the original and replacement daemon sessions terminate transport.
    harness = _PersistentReplHarness(monkeypatch, "always_eof")
    session = AsideReplSession(AsideCliConfig(Path("aside")))

    # When/Then: the second EOF is surfaced after exactly one reconnect.
    with pytest.raises(ContractError, match="Aside CLI persistent REPL stopped"):
        _ = session.run_json("const operation = 'save';")
    assert len(harness.processes) == 2
    assert all(process.terminated for process in harness.processes)


def test_repl_session_stages_uploads_under_configured_root(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    _ = source.write_bytes(b"image")
    upload_root = tmp_path / "uploads"
    session = AsideReplSession(
        AsideCliConfig(Path("aside"), upload_root=upload_root),
        lambda _arguments, _timeout: "",
    )

    staged = session.stage_uploads((source,), "digest123")

    assert staged["source.png"].read_bytes() == b"image"
    assert staged["source.png"].parent == upload_root / "digest123"
    assert session.evidence_path("digest123", "prepared.png") == (
        upload_root / "digest123" / "prepared.png"
    )
