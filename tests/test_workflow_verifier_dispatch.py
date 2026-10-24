"""Exercise verifier CLI and fail-closed hook dispatch without external writes."""

import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import workflow_verifier as verifier
from tools.contract_types import ContractError, SchemaError


def test_verifier_options_validate_pairs_and_required_values():
    assert verifier._options(["--mode", "beta", "--key", "value"]) == {"mode": "beta", "key": "value"}
    for args in (["positional"], ["--mode"], ["--mode", "beta", "unexpected"]):
        with pytest.raises(ContractError, match="expected --key value"):
            verifier._options(args)
    with pytest.raises(ContractError, match="missing option"):
        verifier._required({}, "run-id")
    assert verifier._required({"run-id": "one"}, "run-id") == "one"


def test_verifier_environment_root_and_serialization(tmp_path, monkeypatch):
    monkeypatch.delenv("REQUIRED_FOR_TEST", raising=False)
    with pytest.raises(ContractError, match="missing environment variable"):
        verifier._required_env("REQUIRED_FOR_TEST")
    monkeypatch.setenv("REQUIRED_FOR_TEST", "ok")
    assert verifier._required_env("REQUIRED_FOR_TEST") == "ok"
    monkeypatch.chdir(tmp_path)
    assert verifier._root({}) == tmp_path
    assert verifier._root({"root": "elsewhere"}) == tmp_path / "elsewhere"
    path = tmp_path / "nested" / "manifest.json"
    verifier._write_json(path, {"한글": "값"})
    assert json.loads(path.read_text(encoding="utf-8")) == {"한글": "값"}


def test_workspace_discovery_walks_parents_and_fails_closed(tmp_path):
    nested = tmp_path / "deep" / "deeper"
    nested.mkdir(parents=True)
    with pytest.raises(ContractError, match="workspace root could not be found"):
        verifier._workspace_from_cwd(str(nested))
    (tmp_path / "AGENTS.md").write_text("rules")
    (tmp_path / "workflow-optimization-implementation-plan.md").write_text("plan")
    assert verifier._workspace_from_cwd(str(nested)) == tmp_path


def hook(monkeypatch, capsys, payload):
    monkeypatch.setattr(verifier.sys, "stdin", io.StringIO(payload if isinstance(payload, str) else json.dumps(payload)))
    assert verifier._hook() == 0
    output = capsys.readouterr().out.strip()
    return json.loads(output)["hookSpecificOutput"] if output else None


@pytest.mark.parametrize("payload, expected", [
    ("not-json", "Expecting value"),
    ([], "hook input must be a JSON object"),
    ({}, "hook tool_name must be a string"),
    ({"tool_name": "browser.open", "tool_input": []}, "hook tool_input must be an object"),
    ({"tool_name": "mcp__n__danger"}, "external tool/action is not registered"),
])
def test_hook_denies_malformed_or_unknown_inputs(monkeypatch, capsys, payload, expected):
    response = hook(monkeypatch, capsys, payload)
    assert response["permissionDecision"] == "deny"
    assert expected in response["permissionDecisionReason"]


@pytest.mark.parametrize("name", ["browser.open", "mcp__Notion__query", "local.python"])
def test_hook_passes_read_or_internal_calls_without_external_gate(monkeypatch, capsys, name):
    assert hook(monkeypatch, capsys, {"tool_name": name}) is None


def test_hook_blocks_write_with_live_execution_lock(tmp_path, monkeypatch, capsys):
    path = tmp_path / "busy.lock"
    path.touch()
    monkeypatch.setenv("WORKFLOW_LOCK", str(path))
    result = hook(monkeypatch, capsys, {"tool_name": "browser.click"})
    assert result["permissionDecision"] == "deny"
    assert "lock is active" in result["permissionDecisionReason"]


def test_hook_requires_workspace_and_every_approval_env_var(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("WORKFLOW_LOCK", raising=False)
    result = hook(monkeypatch, capsys, {"tool_name": "browser.click", "cwd": str(tmp_path)})
    assert "workspace root" in result["permissionDecisionReason"]
    (tmp_path / "AGENTS.md").touch()
    (tmp_path / "workflow-optimization-implementation-plan.md").touch()
    for key in ("WORKFLOW_GATE", "WORKFLOW_MANIFEST", "WORKFLOW_RUN_LOG", "WORKFLOW_RUN_ID", "WORKFLOW_TARGET_ID"):
        monkeypatch.delenv(key, raising=False)
    result = hook(monkeypatch, capsys, {"tool_name": "browser.click", "cwd": str(tmp_path)})
    assert "WORKFLOW_GATE" in result["permissionDecisionReason"]


def test_hook_allows_write_only_after_positive_gate_check(tmp_path, monkeypatch, capsys):
    (tmp_path / "AGENTS.md").touch()
    (tmp_path / "workflow-optimization-implementation-plan.md").touch()
    monkeypatch.delenv("WORKFLOW_LOCK", raising=False)
    for key, value in {
        "WORKFLOW_GATE": "notion_write", "WORKFLOW_MANIFEST": "manifest.json",
        "WORKFLOW_RUN_LOG": "run.jsonl", "WORKFLOW_RUN_ID": "run-1", "WORKFLOW_TARGET_ID": "ds-1",
    }.items():
        monkeypatch.setenv(key, value)
    calls = []
    def approve(**kwargs):
        calls.append(kwargs)
        return {"verified_artifact_digest": "digest-1"}
    monkeypatch.setattr(verifier, "verify_gate", approve)
    payload = {"tool_name": "mcp__notion__create_page", "cwd": str(tmp_path)}
    result = hook(monkeypatch, capsys, payload)
    assert result["permissionDecision"] == "allow"
    assert "digest-1" in result["permissionDecisionReason"]
    assert len(calls) == 1
    assert calls[0]["run_id"] == "run-1"
    monkeypatch.setattr(verifier, "verify_gate", lambda **kwargs: (_ for _ in ()).throw(ContractError("gate denied")))
    result = hook(monkeypatch, capsys, payload)
    assert result["permissionDecision"] == "deny"
    assert "gate denied" in result["permissionDecisionReason"]


def test_main_handles_invalid_command_and_required_flags():
    with pytest.raises(ContractError, match="command required"):
        verifier.main(["verifier"])
    with pytest.raises(ContractError, match="unknown command"):
        verifier.main(["verifier", "bogus"])
    with pytest.raises(ContractError, match="missing option"):
        verifier.main(["verifier", "gate", "--root", "."])


def test_manifest_command_writes_json_and_reports_digest(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(verifier, "build_manifest", lambda **kwargs: {"artifact_digest": "A", "run_id": kwargs["run_id"]})
    destination = tmp_path / "out" / "manifest.json"
    args = ["verifier", "manifest", "--root", str(tmp_path), "--keyword", "topic", "--run-id", "r", "--topic-id", "t", "--mode", "beta", "--created-at", "2026-10-24T00:00:00+09:00", "--output", str(destination)]
    assert verifier.main(args) == 0
    assert json.loads(destination.read_text())["artifact_digest"] == "A"
    assert json.loads(capsys.readouterr().out)["artifact_digest"] == "A"


def test_verify_manifest_and_validate_log_routes(monkeypatch, capsys):
    monkeypatch.setattr(verifier, "verify_manifest", lambda *args: SimpleNamespace(pipeline_version="v", files=[1, 2], artifact_digest="B"))
    assert verifier.main(["v", "verify-manifest", "--manifest", "m.json"]) == 0
    assert json.loads(capsys.readouterr().out)["artifact_count"] == 2
    monkeypatch.setattr(verifier, "validate_log", lambda path: 8)
    assert verifier.main(["v", "validate-log", "--run-log", "events.jsonl"]) == 0
    assert json.loads(capsys.readouterr().out)["events"] == 8


def test_validate_schema_jsonl_ignores_empty_lines_and_checks_each_record(tmp_path, monkeypatch, capsys):
    path = tmp_path / "records.jsonl"
    path.write_text('\n{"ok": true}\n\n{"ok": false}\n', encoding="utf-8")
    seen = []
    monkeypatch.setattr(verifier, "validate_instance", lambda value, schema: seen.append(value))
    assert verifier.main(["v", "validate-schema", "--input", str(path)]) == 0
    assert seen == [{"ok": True}, {"ok": False}]
    assert json.loads(capsys.readouterr().out)["records"] == 2


def test_validate_schema_rejects_bad_json_and_invalid_schema(tmp_path, monkeypatch):
    path = tmp_path / "record.json"
    path.write_text("invalid", encoding="utf-8")
    with pytest.raises(ContractError, match="invalid JSON"):
        verifier.main(["v", "validate-schema", "--input", str(path)])
    path.write_text('{"ok": true}', encoding="utf-8")
    monkeypatch.setattr(verifier, "validate_instance", lambda *args: (_ for _ in ()).throw(SchemaError("bad")))
    with pytest.raises(ContractError, match="schema validation failed"):
        verifier.main(["v", "validate-schema", "--input", str(path)])


def test_image_validation_and_gate_outputs_are_consistent(monkeypatch, capsys):
    monkeypatch.setattr(verifier, "validate_image_metadata", lambda path, mode: {"mode": mode})
    assert verifier.main(["v", "validate-image-metadata", "--metadata", "m.json", "--mode", "formal"]) == 0
    assert json.loads(capsys.readouterr().out)["mode"] == "formal"
    monkeypatch.setattr(verifier, "validate_image_quality", lambda path: {"score": 20})
    assert verifier.main(["v", "validate-image-quality", "--quality", "q.json"]) == 0
    assert json.loads(capsys.readouterr().out)["score"] == 20
    seen = []
    def verified(**kwargs):
        seen.append(kwargs)
        return {"gate": "notion_write", "decision": "approved", "verified_artifact_digest": "A"}
    monkeypatch.setattr(verifier, "verify_gate", verified)
    assert verifier.main(["v", "gate", "--manifest", "m.json", "--run-log", "r.jsonl", "--gate", "notion_write", "--run-id", "id", "--target-id", "target", "--blog-id", "blog"]) == 0
    assert seen[0]["blog_id"] == "blog"
    assert json.loads(capsys.readouterr().out)["artifact_digest"] == "A"


def test_hook_entrypoint_dispatches_without_options(monkeypatch, capsys):
    result = hook(monkeypatch, capsys, {"tool_name": "browser.open"})
    assert result is None
    monkeypatch.setattr(verifier.sys, "stdin", io.StringIO('{"tool_name": "browser.open"}'))
    assert verifier.main(["v", "hook"]) == 0
