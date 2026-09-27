from __future__ import annotations

import importlib
import json
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest

from tools import preflight_runner, startup_project_contract
from tools.contract_types import ContractError
from tools.notion_keychain import NotionApiToken, NotionCredentialsUnavailable
from tools.startup_preflight import PreflightDependencies, run_preflight

REQUIRED_FILES = (
    "topic-selector.md",
    "researcher.md",
    "writer.md",
    "image-maker.md",
    "content-assembler.md",
    "notion-rider.md",
    "naver-rider.md",
    "schemas/workflow-contract.schema.json",
    "schemas/stage-result.schema.json",
    "pyproject.toml",
    "uv.lock",
)


def test_project_contract_requires_only_runtime_imported_packages() -> None:
    # Given / When: the startup package contract is inspected.
    required = {name for name, _module in startup_project_contract.REQUIRED_PACKAGES}

    # Then: the image metadata provider name does not create a Pillow dependency.
    assert "pillow" not in required


def _root(tmp_path: Path) -> Path:
    for relative_path in REQUIRED_FILES:
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix == ".json":
            _ = path.write_text('{"type":"object"}', encoding="utf-8")
        elif path.name == "pyproject.toml":
            _ = path.write_text(
                _pyproject(),
                encoding="utf-8",
            )
        else:
            _ = path.write_text("required\n", encoding="utf-8")
    lock = (
        "version = 1\n"
        + 'requires-python = ">=3.12"\n\n'
        + "[[package]]\n"
        + 'name = "naver-blog-workflow"\n'
        + 'dependencies = [{ name = "httpx2" }, { name = "jsonschema" }]\n'
        + 'version = "0.1.0"\n\n'
        + "[[package]]\n"
        + 'name = "httpx2"\n'
        + 'version = "2.12.0"\n\n'
        + "[[package]]\n"
        + 'name = "jsonschema"\n'
        + 'version = "4.26.0"\n\n'
        + "[[package]]\n"
        + 'name = "jsonschema-specifications"\n'
        + 'version = "2025.9.1"\n'
        + "\n[[package]]\n"
        + 'name = "packaging"\n'
        + 'version = "26.3"\n'
    )
    _ = (tmp_path / "uv.lock").write_text(lock, encoding="utf-8")
    profile = tmp_path / ".codex" / "naver-automation.config.toml"
    profile.parent.mkdir()
    _ = profile.write_text(_codex_profile(), encoding="utf-8")
    _ = (tmp_path / "notion-config.md").write_text(
        "- 데이터 소스 ID: `target-123`\n", encoding="utf-8"
    )
    return tmp_path


def _dependencies(
    root: Path,
    *,
    importer: Callable[[str], ModuleType] | None = None,
    version: Callable[[str], str] | None = None,
    executable: Callable[[str], str | None] | None = None,
    locked_environment: Callable[[Path], bool] | None = None,
    credential_loader: Callable[[], NotionApiToken] | None = None,
    notion_access_probe: Callable[[NotionApiToken, str], None] | None = None,
    keychain_isolation_probe: Callable[[str, Callable[[], bool]], bool] | None = None,
) -> PreflightDependencies:
    return PreflightDependencies(
        python_version=(3, 13, 0),
        import_module=importlib.import_module if importer is None else importer,
        package_version=_installed_version if version is None else version,
        find_executable=_codex_executable if executable is None else executable,
        locked_environment=(
            (lambda _root: True)
            if locked_environment is None
            else locked_environment
        ),
        codex_home=root / ".codex",
        credential_loader=(
            (lambda: NotionApiToken("test-notion-credential"))
            if credential_loader is None
            else credential_loader
        ),
        notion_access_probe=(
            (lambda _token, _target_id: None)
            if notion_access_probe is None
            else notion_access_probe
        ),
        keychain_isolation_probe=(
            (lambda _binary, trusted: trusted())
            if keychain_isolation_probe is None
            else keychain_isolation_probe
        ),
    )


def _installed_version(name: str) -> str:
    return {
        "httpx2": "2.12.0",
        "jsonschema": "4.26.0",
        "jsonschema-specifications": "2025.9.1",
        "packaging": "26.3",
    }[name]


def _pyproject(
    *,
    python_requirement: str = ">=3.12",
    dependencies: str = "jsonschema[format-nongpl]>=4.26,<5",
) -> str:
    return (
        "[project]\n"
        + 'name = "naver-blog-workflow"\n'
        + 'version = "0.1.0"\n'
        + f'requires-python = "{python_requirement}"\n'
        + f'dependencies = ["{dependencies}", "httpx2[http2,brotli,zstd]>=2.12,<3", "packaging>=26.3,<27"]\n'
    )


def _codex_profile() -> str:
    return (
        'model = "gpt-5.6-sol"\n'
        + 'model_reasoning_effort = "high"\n'
        + 'approval_policy = "never"\n'
        + 'default_permissions = "naver-stage-isolated"\n'
        + "allow_login_shell = false\n"
        + 'web_search = "disabled"\n\n'
        + "[permissions.naver-stage-isolated]\n"
        + 'extends = ":workspace"\n\n'
        + "[permissions.naver-stage-isolated.filesystem]\n"
        + '":root" = "deny"\n'
        + '":minimal" = "read"\n'
        + '"/opt/homebrew" = "read"\n'
        + '"~/.local/bin" = "read"\n'
        + '"~/.local/share/uv/python" = "read"\n'
        + '"~/.codex" = "deny"\n'
        + '"~/Library/Keychains" = "deny"\n\n'
        + '"/Users/beomseok/00_AI/01_ NAVER_BLOG_AUTOMATE" = "read"\n'
        + '[permissions.naver-stage-isolated.filesystem.":workspace_roots"]\n'
        + '"." = "write"\n\n'
        + "[permissions.naver-stage-isolated.network]\n"
        + "enabled = false\n"
        + "allow_local_binding = false\n\n"
        + "[features]\n"
        + "request_permissions_tool = false\n"
        + "multi_agent = false\n\n"
        + "[agents]\n"
        + "enabled = false\n"
    )


def _codex_executable(_name: str) -> str:
    return "codex"


def _missing_executable(_name: str) -> None:
    return None


def test_preflight_passes_without_creating_workspace_files(tmp_path: Path) -> None:
    root = _root(tmp_path)
    before = {path.relative_to(root) for path in root.rglob("*")}

    result = run_preflight(root, dependencies=_dependencies(root))

    assert result.ok is True
    assert result.connector_write_capability == "unknown_until_q1"
    assert result.error_code is None
    assert {path.relative_to(root) for path in root.rglob("*")} == before
    assert not (root / ".automation").exists()


def test_preflight_stops_before_heavy_runner_when_dependency_is_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _root(tmp_path)
    runner_imported = False

    def missing_dependency(name: str) -> ModuleType:
        raise ImportError(name)

    def heavy_runner() -> Callable[[list[str]], int]:
        nonlocal runner_imported
        runner_imported = True
        return lambda _arguments: 0

    exit_code = preflight_runner.main(
        ["preflight-runner", "run", "daily-generate", "--root", str(root)],
        dependencies=_dependencies(root, importer=missing_dependency),
        runner_loader=heavy_runner,
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert payload["error_code"] == "dependency_import"
    assert runner_imported is False


def test_preflight_rejects_installed_version_not_in_lock(tmp_path: Path) -> None:
    root = _root(tmp_path)

    result = run_preflight(
        root,
        dependencies=_dependencies(root, version=lambda _name: "0.0.0"),
    )

    assert result.ok is False
    assert result.error_code == "dependency_version"


def test_preflight_rejects_invalid_pyproject_toml(tmp_path: Path) -> None:
    root = _root(tmp_path)
    _ = (root / "pyproject.toml").write_text("[project\n", encoding="utf-8")

    result = run_preflight(root, dependencies=_dependencies(root))

    assert result.ok is False
    assert result.error_code == "pyproject"


def test_preflight_rejects_project_python_requirement_that_disagrees_with_lock(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    _ = (root / "pyproject.toml").write_text(
        _pyproject(python_requirement=">=3.13"),
        encoding="utf-8",
    )

    result = run_preflight(root, dependencies=_dependencies(root))

    assert result.ok is False
    assert result.error_code == "project_python"


def test_preflight_rejects_current_python_outside_declared_requirement(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    _ = (root / "pyproject.toml").write_text(
        _pyproject(python_requirement=">=3.14"),
        encoding="utf-8",
    )
    lock = (root / "uv.lock").read_text(encoding="utf-8").replace(
        'requires-python = ">=3.12"', 'requires-python = ">=3.14"'
    )
    _ = (root / "uv.lock").write_text(lock, encoding="utf-8")

    result = run_preflight(root, dependencies=_dependencies(root))

    assert result.ok is False
    assert result.error_code == "project_python"


def test_preflight_rejects_project_without_required_runtime_dependency(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    _ = (root / "pyproject.toml").write_text(
        _pyproject(dependencies=""),
        encoding="utf-8",
    )

    result = run_preflight(root, dependencies=_dependencies(root))

    assert result.ok is False
    assert result.error_code == "project_dependencies"


def test_preflight_rejects_project_dependency_incompatible_with_locked_version(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    _ = (root / "pyproject.toml").write_text(
        _pyproject(dependencies="jsonschema>=5"),
        encoding="utf-8",
    )

    result = run_preflight(root, dependencies=_dependencies(root))

    assert result.ok is False
    assert result.error_code == "project_dependencies"


def test_preflight_rejects_missing_codex_executable(tmp_path: Path) -> None:
    root = _root(tmp_path)
    result = run_preflight(
        root,
        dependencies=_dependencies(root, executable=_missing_executable),
    )

    assert result.ok is False
    assert result.error_code == "codex_executable"


def test_preflight_rejects_environment_that_differs_from_lock(tmp_path: Path) -> None:
    root = _root(tmp_path)
    result = run_preflight(
        root,
        dependencies=_dependencies(
            root, locked_environment=lambda _candidate: False
        ),
    )

    assert result.ok is False
    assert result.error_code == "lock_environment"


def test_preflight_rejects_missing_codex_profile(tmp_path: Path) -> None:
    root = _root(tmp_path)
    (root / ".codex" / "naver-automation.config.toml").unlink()
    result = run_preflight(
        root,
        dependencies=_dependencies(root),
    )

    assert result.ok is False
    assert result.error_code == "codex_profile"


def test_preflight_rejects_profile_that_can_override_the_dedicated_contract(
    tmp_path: Path,
) -> None:
    # Given: a profile that carries an unapproved configuration key.
    root = _root(tmp_path)
    _ = (root / ".codex" / "naver-automation.config.toml").write_text(
        _codex_profile() + 'developer_instructions = "override"\n',
        encoding="utf-8",
    )

    # When: startup preflight validates the dedicated profile.
    result = run_preflight(root, dependencies=_dependencies(root))

    # Then: the profile is rejected before a runner could consume it.
    assert result.ok is False
    assert result.error_code == "codex_profile"


def test_profile_contract_accepts_only_the_approved_dedicated_values() -> None:
    # Given: the approved Codex profile contents.
    profile = _codex_profile()

    # When: the contract parses it.
    error = startup_project_contract.codex_profile_contract(profile)

    # Then: its exact closed-read permission profile is accepted.
    assert error is None


def test_preflight_rejects_unavailable_notion_keychain_credential(tmp_path: Path) -> None:
    # Given: all local prerequisites except the approved Keychain item.
    root = _root(tmp_path)

    def unavailable_credential() -> NotionApiToken:
        raise NotionCredentialsUnavailable()

    # When: startup preflight reaches credential validation.
    result = run_preflight(
        root,
        dependencies=_dependencies(root, credential_loader=unavailable_credential),
    )

    # Then: it stops with the stable credential error code.
    assert result.ok is False
    assert result.error_code == "notion_credentials_unavailable"


def test_read_only_run_preflight_does_not_touch_notion_credentials(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    calls = 0

    def forbidden_credential_lookup() -> NotionApiToken:
        nonlocal calls
        calls += 1
        raise AssertionError("read-only preflight must not read Keychain")

    result = run_preflight(
        root,
        dependencies=_dependencies(
            root, credential_loader=forbidden_credential_lookup
        ),
        require_notion_credentials=False,
    )

    assert result.ok is True
    assert calls == 0


def test_live_preflight_probes_the_configured_notion_target_without_writing(
    tmp_path: Path,
) -> None:
    # Given: a deterministic credential and a read-only probe seam.
    root = _root(tmp_path)
    observed: list[tuple[NotionApiToken, str]] = []

    def probe(token: NotionApiToken, target_id: str) -> None:
        observed.append((token, target_id))

    # When: live preflight succeeds.
    result = run_preflight(
        root,
        dependencies=_dependencies(root, notion_access_probe=probe),
    )

    # Then: it probes exactly the configured data source after Keychain loading.
    assert result.ok is True
    assert observed == [(NotionApiToken("test-notion-credential"), "target-123")]


def test_live_preflight_checks_actual_keychain_isolation_before_notion_probe(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    calls: list[str] = []

    def credential() -> NotionApiToken:
        calls.append("credential")
        return NotionApiToken("fixture-token")

    def isolation(binary: str, trusted: Callable[[], bool]) -> bool:
        calls.append(f"isolation:{binary}:{trusted()}")
        return False

    def notion_probe(_token: NotionApiToken, _target: str) -> None:
        calls.append("notion")

    result = run_preflight(
        root,
        dependencies=_dependencies(
            root,
            credential_loader=credential,
            notion_access_probe=notion_probe,
            keychain_isolation_probe=isolation,
        ),
    )

    assert result.error_code == "codex_keychain_isolation"
    assert calls == ["credential", "isolation:codex:True"]


def test_read_only_preflight_skips_credentials_and_notion_probe(tmp_path: Path) -> None:
    # Given: read-only invocation seams that fail if touched.
    root = _root(tmp_path)

    def forbidden_credential_lookup() -> NotionApiToken:
        raise AssertionError("read-only preflight must not read Keychain")

    def forbidden_probe(_token: NotionApiToken, _target_id: str) -> None:
        raise AssertionError("read-only preflight must not touch Notion")

    # When: dry-run-style preflight bypasses external capability checks.
    result = run_preflight(
        root,
        dependencies=_dependencies(
            root,
            credential_loader=forbidden_credential_lookup,
            notion_access_probe=forbidden_probe,
        ),
        require_notion_credentials=False,
    )

    # Then: both secret and network boundaries remain untouched.
    assert result.ok is True


def test_live_preflight_reports_a_safe_notion_access_failure(tmp_path: Path) -> None:
    # Given: a capability probe that rejects the configured data source.
    root = _root(tmp_path)

    def unavailable_probe(_token: NotionApiToken, _target_id: str) -> None:
        raise ContractError("Notion API read request failed")

    # When: live preflight performs its read-only Notion capability check.
    result = run_preflight(
        root,
        dependencies=_dependencies(root, notion_access_probe=unavailable_probe),
    )

    # Then: it redacts the underlying API error behind the stable safe code.
    assert result.ok is False
    assert result.error_code == "notion_access_unavailable"


def test_preflight_rejects_python_before_supported_minimum(tmp_path: Path) -> None:
    root = _root(tmp_path)
    dependencies = PreflightDependencies(
        python_version=(3, 11, 9),
        import_module=importlib.import_module,
        package_version=_installed_version,
        find_executable=_codex_executable,
        locked_environment=lambda _root: True,
        codex_home=root / ".codex",
        credential_loader=lambda: NotionApiToken("test-notion-credential"),
    )

    result = run_preflight(root, dependencies=dependencies)

    assert result.ok is False
    assert result.error_code == "python_version"


@pytest.mark.parametrize(
    ("relative_path", "content", "expected_code"),
    (
        ("writer.md", None, "required_file"),
        ("schemas/stage-result.schema.json", '{"type":"impossible"}', "schema"),
    ),
)
def test_preflight_rejects_missing_or_invalid_required_input(
    tmp_path: Path, relative_path: str, content: str | None, expected_code: str
) -> None:
    root = _root(tmp_path)
    path = root / relative_path
    if content is None:
        path.unlink()
    else:
        _ = path.write_text(content, encoding="utf-8")

    result = run_preflight(root, dependencies=_dependencies(root))

    assert result.ok is False
    assert result.error_code == expected_code


def test_preflight_rejects_target_id_that_differs_from_notion_config(tmp_path: Path) -> None:
    root = _root(tmp_path)
    result = run_preflight(
        root, target_id="other-target", dependencies=_dependencies(root)
    )

    assert result.ok is False
    assert result.error_code == "target_id"


def test_runner_delegates_original_arguments_only_after_preflight_passes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _root(tmp_path)
    delegated: list[list[str]] = []

    def heavy_runner() -> Callable[[list[str]], int]:
        return lambda arguments: delegated.append(arguments) or 7

    arguments = ["preflight-runner", "run", "daily-generate", "--root", str(root)]
    exit_code = preflight_runner.main(
        arguments,
        dependencies=_dependencies(root),
        runner_loader=heavy_runner,
    )

    assert exit_code == 7
    assert delegated == [arguments]
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize(
    "arguments",
    (
        ["preflight-runner", "run", "daily-generate", "--auto-topic", "--dry-run"],
        ["preflight-runner", "run", "weekly-improve"],
        ["preflight-runner", "status", "--run-id", "RUN-read-only"],
        [
            "preflight-runner",
            "confirm",
            "--run-id",
            "RUN-read-only",
            "--action",
            "naver-draft-save",
        ],
        ["preflight-runner", "recover", "--run-id", "RUN-read-only", "--dry-run"],
    ),
)
def test_read_only_runner_commands_skip_keychain_before_delegation(
    tmp_path: Path,
    arguments: list[str],
) -> None:
    root = _root(tmp_path)
    delegated: list[list[str]] = []

    def forbidden_credential_lookup() -> NotionApiToken:
        raise AssertionError("read-only runner command must not read Keychain")

    full_arguments = [*arguments, "--root", str(root)]
    exit_code = preflight_runner.main(
        full_arguments,
        dependencies=_dependencies(
            root, credential_loader=forbidden_credential_lookup
        ),
        runner_loader=lambda: (
            lambda candidate: delegated.append(candidate) or 0
        ),
    )

    assert exit_code == 0
    assert delegated == [full_arguments]


def test_weekly_read_only_dispatch_skips_full_project_preflight(tmp_path: Path) -> None:
    # Given: an evidence-only root has none of the publish pipeline project files.
    delegated: list[list[str]] = []
    arguments = [
        "preflight-runner",
        "run",
        "weekly-improve",
        "--root",
        str(tmp_path),
    ]

    # When: the exact read-only command is dispatched.
    exit_code = preflight_runner.main(
        arguments,
        runner_loader=lambda: lambda value: delegated.append(value) or 9,
    )

    # Then: local runner validation owns the request without external preflight.
    assert exit_code == 9
    assert delegated == [arguments]


@pytest.mark.parametrize("job", ["daily-generate", "recover", "resume"])
def test_non_weekly_commands_keep_full_preflight(tmp_path: Path, job: str) -> None:
    # Given
    delegated: list[list[str]] = []
    arguments = ["preflight-runner", "run", job, "--root", str(tmp_path)]

    # When
    exit_code = preflight_runner.main(
        arguments,
        runner_loader=lambda: lambda value: delegated.append(value) or 0,
    )

    # Then
    assert exit_code == 2
    assert delegated == []


def test_malformed_weekly_request_is_rejected_by_local_runner(tmp_path: Path) -> None:
    # Given
    arguments = [
        "preflight-runner",
        "run",
        "weekly-improve",
        "--root",
        str(tmp_path),
        "--unknown",
        "value",
    ]

    # When
    exit_code = preflight_runner.main(arguments)

    # Then
    assert exit_code == 2
    assert not (tmp_path / ".automation").exists()


def test_explicit_preflight_command_is_read_only_and_reports_capability(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _root(tmp_path)

    exit_code = preflight_runner.main(
        ["preflight-runner", "preflight", "--root", str(root)],
        dependencies=_dependencies(root),
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload == {
        "connector_write_capability": "unknown_until_q1",
        "error_code": None,
        "ok": True,
    }
