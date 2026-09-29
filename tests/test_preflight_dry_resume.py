from __future__ import annotations

import importlib
import json
from collections.abc import Callable
from pathlib import Path

import pytest

from tools import preflight_runner
from tools.notion_keychain import NotionApiToken
from tools.startup_preflight import PreflightDependencies
from tools.startup_project_contract import REQUIRED_FILES

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_VERSIONS = {
    "httpx2": "2.12.0",
    "jsonschema": "4.26.0",
    "jsonschema-specifications": "2025.9.1",
    "packaging": "26.3",
}


def _root(tmp_path: Path) -> Path:
    for relative_path in REQUIRED_FILES:
        source = PROJECT_ROOT / relative_path
        destination = tmp_path / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        _ = destination.write_bytes(source.read_bytes())
    _ = (tmp_path / "notion-config.md").write_text(
        "- 데이터 소스 ID: `target-123`\n", encoding="utf-8"
    )
    profile = tmp_path / ".codex" / "naver-automation.config.toml"
    profile.parent.mkdir()
    _ = profile.write_text(
        'model = "gpt-5.6-sol"\n'
        + 'model_reasoning_effort = "high"\n'
        + 'approval_policy = "never"\n'
        + 'default_permissions = "naver-stage-isolated"\n'
        + 'allow_login_shell = false\n'
        + 'web_search = "disabled"\n'
        + '[permissions.naver-stage-isolated]\n'
        + 'extends = ":workspace"\n'
        + '[permissions.naver-stage-isolated.filesystem]\n'
        + '":root" = "deny"\n'
        + '":minimal" = "read"\n'
        + '"/opt/homebrew" = "read"\n'
        + '"~/.local/bin" = "read"\n'
        + '"~/.local/share/uv/python" = "read"\n'
        + '"~/.codex" = "deny"\n'
        + '"~/Library/Keychains" = "deny"\n'
        + '"/Users/beomseok/00_AI/01_ NAVER_BLOG_AUTOMATE" = "read"\n'
        + '":workspace_roots" = { "." = "write" }\n'
        + '[permissions.naver-stage-isolated.network]\n'
        + 'enabled = false\n'
        + 'allow_local_binding = false\n'
        + '[features]\n'
        + 'request_permissions_tool = false\n'
        + 'multi_agent = false\n'
        + '[agents]\n'
        + 'enabled = false\n',
        encoding="utf-8",
    )
    return tmp_path


def _dependencies(
    root: Path,
    credential_loader: Callable[[], NotionApiToken],
    notion_access_probe: Callable[[NotionApiToken, str], None],
    isolation_probe: Callable[[str, Callable[[], bool]], bool] | None = None,
) -> PreflightDependencies:
    return PreflightDependencies(
        python_version=(3, 13, 0),
        import_module=importlib.import_module,
        package_version=lambda name: PACKAGE_VERSIONS[name],
        find_executable=lambda _name: "codex",
        locked_environment=lambda _path: True,
        codex_home=root / ".codex",
        credential_loader=credential_loader,
        notion_access_probe=notion_access_probe,
        keychain_isolation_probe=(
            (lambda _binary, trusted: trusted())
            if isolation_probe is None
            else isolation_probe
        ),
    )


def _state(root: Path, run_id: str, dry_run: bool) -> None:
    path = root / ".automation" / "state" / f"{run_id}.json"
    path.parent.mkdir(parents=True)
    _ = path.write_text(
        json.dumps(
            {
                "run_id": run_id,
                "job": "daily-generate",
                "keyword": "fixture",
                "dry_run": dry_run,
            }
        ),
        encoding="utf-8",
    )


@pytest.mark.parametrize("command", ("recover", "resume"))
def test_persisted_dry_run_skips_credentials_and_probe_before_delegation(
    tmp_path: Path, command: str
) -> None:
    # Given: a resumable dry-run with sentinels for all external Notion access.
    root = _root(tmp_path)
    run_id = "RUN-dry"
    _state(root, run_id, dry_run=True)
    delegated: list[list[str]] = []

    def forbidden_credentials() -> NotionApiToken:
        raise AssertionError("dry-run must not load Keychain credentials")

    def forbidden_probe(_token: NotionApiToken, _target_id: str) -> None:
        raise AssertionError("dry-run must not probe the Notion API")

    arguments = [
        "preflight-runner",
        command,
        "--run-id",
        run_id,
        "--root",
        str(root),
    ]

    # When: the wrapper prepares the resumed command.
    def forbidden_isolation(_binary: str, _trusted: Callable[[], bool]) -> bool:
        raise AssertionError("dry-run must not probe Keychain isolation")

    exit_code = preflight_runner.main(
        arguments,
        dependencies=_dependencies(
            root, forbidden_credentials, forbidden_probe, forbidden_isolation
        ),
        runner_loader=lambda: lambda candidate: delegated.append(candidate) or 0,
    )

    # Then: it delegates without reaching Keychain or the network probe.
    assert exit_code == 0
    assert delegated == [arguments]


def test_explicit_dry_run_skips_credentials_and_probe_without_persisted_state(
    tmp_path: Path,
) -> None:
    # Given: an explicit dry-run whose state will be checked by the delegated runner.
    root = _root(tmp_path)
    delegated: list[list[str]] = []

    def forbidden_credentials() -> NotionApiToken:
        raise AssertionError("explicit dry-run must not load Keychain credentials")

    def forbidden_probe(_token: NotionApiToken, _target_id: str) -> None:
        raise AssertionError("explicit dry-run must not probe the Notion API")

    def forbidden_isolation(_binary: str, _trusted: Callable[[], bool]) -> bool:
        raise AssertionError("explicit dry-run must not probe Keychain isolation")

    arguments = [
        "preflight-runner",
        "recover",
        "--run-id",
        "RUN-explicit-dry",
        "--dry-run",
        "--root",
        str(root),
    ]

    # When: the wrapper prepares the explicit dry-run.
    exit_code = preflight_runner.main(
        arguments,
        dependencies=_dependencies(
            root, forbidden_credentials, forbidden_probe, forbidden_isolation
        ),
        runner_loader=lambda: lambda candidate: delegated.append(candidate) or 0,
    )

    # Then: it does not need the persisted state to avoid external access.
    assert exit_code == 0
    assert delegated == [arguments]


def test_persisted_live_resume_requires_credentials_and_read_only_probe(
    tmp_path: Path,
) -> None:
    # Given: a persisted live run and observable credential/probe seams.
    root = _root(tmp_path)
    run_id = "RUN-live"
    _state(root, run_id, dry_run=False)
    calls: list[str] = []
    delegated: list[list[str]] = []

    def credentials() -> NotionApiToken:
        calls.append("credentials")
        return NotionApiToken("fixture-token")

    def probe(_token: NotionApiToken, target_id: str) -> None:
        calls.append(f"probe:{target_id}")

    def isolation(_binary: str, trusted: Callable[[], bool]) -> bool:
        calls.append(f"isolation:{trusted()}")
        return True

    arguments = [
        "preflight-runner",
        "resume",
        "--run-id",
        run_id,
        "--root",
        str(root),
    ]

    # When: the wrapper prepares the live resume.
    exit_code = preflight_runner.main(
        arguments,
        dependencies=_dependencies(root, credentials, probe, isolation),
        runner_loader=lambda: lambda candidate: delegated.append(candidate) or 0,
    )

    # Then: the credential and read-only probe both run before delegation.
    assert exit_code == 0
    assert calls == ["credentials", "isolation:True", "probe:target-123"]
    assert delegated == [arguments]


@pytest.mark.parametrize("state", (None, "not-json", '{"run_id":"RUN-other","dry_run":true}'))
def test_resume_with_untrusted_state_fails_before_credentials_or_probe(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], state: str | None
) -> None:
    # Given: state that is missing, malformed, or belongs to another run.
    root = _root(tmp_path)
    run_id = "RUN-untrusted"
    if state is not None:
        path = root / ".automation" / "state" / f"{run_id}.json"
        path.parent.mkdir(parents=True)
        _ = path.write_text(state, encoding="utf-8")

    def forbidden_credentials() -> NotionApiToken:
        raise AssertionError("untrusted state must not trigger Keychain access")

    def forbidden_probe(_token: NotionApiToken, _target_id: str) -> None:
        raise AssertionError("untrusted state must not trigger a network probe")

    arguments = [
        "preflight-runner",
        "resume",
        "--run-id",
        run_id,
        "--root",
        str(root),
    ]

    # When: the wrapper prepares the resume.
    exit_code = preflight_runner.main(
        arguments,
        dependencies=_dependencies(root, forbidden_credentials, forbidden_probe),
        runner_loader=lambda: lambda _candidate: 0,
    )

    # Then: it fails closed without delegating or accessing Notion.
    assert exit_code == 2
    assert json.loads(capsys.readouterr().out)["error_code"] == "preflight_resume_state"
