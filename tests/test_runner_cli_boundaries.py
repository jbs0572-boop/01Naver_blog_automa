"""Unit tests for runner CLI option contracts and side-effect dispatch."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import runner_cli
from tools.contract_types import ContractError
from tools.runner_types import RunStatus


def test_options_parse_key_values_and_dry_run():
    assert runner_cli._options(["--mode", "beta", "--dry-run", "--root", "/tmp/path"]) == {
        "mode": "beta", "dry-run": True, "root": "/tmp/path"
    }


@pytest.mark.parametrize("arguments", [["positional"], ["--root"], ["--mode", "beta", "unexpected"]])
def test_options_reject_missing_value_and_positional_inputs(arguments):
    with pytest.raises(ContractError, match="expected --key value"):
        runner_cli._options(arguments)


def test_required_option_rejects_missing_empty_and_boolean():
    for bad in ({}, {"mode": ""}, {"mode": True}):
        with pytest.raises(ContractError, match="missing option"):
            runner_cli._required(bad, "mode")
    assert runner_cli._required({"mode": "formal"}, "mode") == "formal"


def test_path_option_accepts_default_and_rejects_flag(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert runner_cli._path_option({}, "root", "relative") == (tmp_path / "relative")
    with pytest.raises(ContractError, match="invalid option"):
        runner_cli._path_option({"root": True}, "root", ".")


def test_state_option_defaults_none_and_supports_both_spellings(tmp_path):
    assert runner_cli._state_option({}, tmp_path) is None
    assert runner_cli._state_option({"state-root": "root"}, tmp_path) == Path("root").resolve()
    assert runner_cli._state_option({"state-root": "old", "state-dir": "new"}, tmp_path) == Path("new").resolve()


def test_cli_run_dispatches_all_relevant_arguments(tmp_path, monkeypatch, capsys):
    seen = []
    def fake_run(request):
        seen.append(request)
        return SimpleNamespace(status=RunStatus.PASSED, as_json=lambda: {"status": "passed"})
    monkeypatch.setattr(runner_cli, "run_job", fake_run)
    assert runner_cli._cli(["runner", "run", "daily-generate", "--root", str(tmp_path), "--mode", "beta", "--keyword", "topic", "--run-id", "one", "--dry-run", "--state-dir", str(tmp_path / "state")]) == 0
    request = seen[0]
    assert (request.job, request.mode, request.keyword, request.run_id, request.dry_run) == ("daily-generate", "beta", "topic", "one", True)
    assert request.root == tmp_path
    assert request.state_dir == tmp_path / "state"
    assert json.loads(capsys.readouterr().out) == {"status": "passed"}


@pytest.mark.parametrize("status,expected", [(RunStatus.SKIPPED, 0), (RunStatus.BLOCKED, 2)])
def test_cli_returns_nonzero_for_blocked_but_not_skipped(monkeypatch, status, expected):
    monkeypatch.setattr(runner_cli, "run_job", lambda request: SimpleNamespace(status=status, as_json=lambda: {"status": status.value}))
    assert runner_cli._cli(["runner", "run", "weekly-improve", "--mode", "beta"]) == expected


def test_cli_status_is_read_only_and_accepts_state_root(tmp_path, monkeypatch, capsys):
    seen = []
    def fake_status(root, run_id, state_dir):
        seen.append((root, run_id, state_dir))
        return {"run_id": run_id, "status": "blocked"}
    monkeypatch.setattr(runner_cli, "get_status", fake_status)
    assert runner_cli._cli(["runner", "status", "--run-id", "xyz", "--root", str(tmp_path), "--state-root", str(tmp_path / "state")]) == 0
    assert seen == [(tmp_path, "xyz", tmp_path / "state")]
    assert json.loads(capsys.readouterr().out)["run_id"] == "xyz"


def test_cli_recover_dispatches_run_id_and_dry_run(monkeypatch):
    seen = []
    def fake_recover(request):
        seen.append(request)
        return SimpleNamespace(status=RunStatus.SKIPPED, as_json=lambda: {"status": "skipped"})
    monkeypatch.setattr(runner_cli, "recover_job", fake_recover)
    assert runner_cli._cli(["runner", "recover", "--run-id", "abc", "--dry-run"]) == 0
    assert seen[0].run_id == "abc"
    assert seen[0].dry_run is True


@pytest.mark.parametrize("args,message", [
    (["runner"], "command required"),
    (["runner", "run"], "requires a job name"),
    (["runner", "run", "daily-generate"], "missing option: --mode"),
    (["runner", "status"], "missing option: --run-id"),
    (["runner", "recover"], "missing option: --run-id"),
    (["runner", "invalid"], "unknown runner command"),
])
def test_cli_main_catches_contract_errors_without_traceback(args, message, capsys):
    assert runner_cli.main(args) == 2
    assert message in capsys.readouterr().err


def test_cli_main_without_arguments_uses_sys_argv(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["runner", "invalid"])
    assert runner_cli.main() == 2
