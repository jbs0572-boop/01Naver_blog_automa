"""Keep coverage inventory honest about nested and unexercised functions."""

import json
from pathlib import Path

from scripts.coverage_audit import inventory, own_statements
import ast


def test_own_statements_excludes_nested_function_body():
    tree = ast.parse("def outer():\n    def inner():\n        return 7\n    return inner\n")
    outer = tree.body[0]
    assert isinstance(outer, ast.FunctionDef)
    assert own_statements(outer) == {4}


def test_inventory_separates_imported_definition_from_invoked_function(tmp_path, monkeypatch):
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    (tools_dir / "module.py").write_text(
        "def tested():\n    return 1\n\n"
        "def untouched():\n    return 2\n\n"
        "def factory():\n    def nested():\n        return 4\n    return nested\n",
        encoding="utf-8",
    )
    coverage_json = tmp_path / "coverage.json"
    coverage_json.write_text(json.dumps({
        "totals": {"percent_covered": 42.5},
        "files": {"tools/module.py": {
            "executed_lines": [2, 10],
            "missing_lines": [5, 9],
        }},
    }), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    results = inventory(tools_dir, coverage_json)
    assert results["coverage_percent"] == 42.5
    by_name = {r["function"]: r for r in results["function_details"]}
    assert by_name["tested"]["covered"] is True
    assert by_name["untouched"]["covered"] is False
    assert by_name["factory"]["covered"] is True
    assert by_name["factory.nested"]["covered"] is False
    assert results["functions_exercised"] == 2


def test_inventory_includes_unmeasured_functions(tmp_path, monkeypatch):
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    (tools_dir / "unused.py").write_text("def idle():\n    return 1\n", encoding="utf-8")
    coverage_json = tmp_path / "coverage.json"
    coverage_json.write_text(json.dumps({
        "totals": {"percent_covered": 0.0}, "files": {}
    }), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    result = inventory(tools_dir, coverage_json)
    assert result["functions_exercised"] == 0
    assert result["functions_unexercised"][0]["function"] == "idle"


def test_same_line_declaration_cannot_be_counted_from_import_only(tmp_path, monkeypatch):
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    (tools_dir / "same.py").write_text("def idle(): return 1\n", encoding="utf-8")
    report = tmp_path / "coverage.json"
    report.write_text(json.dumps({"totals": {"percent_covered": 100},
        "files": {"tools/same.py": {"executed_lines": [1], "missing_lines": []}}}),
        encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    result = inventory(tools_dir, report)
    assert result["functions_total"] == 1
    assert result["functions_exercised"] == 0
    assert result["functions_unexercised"][0]["status"] == "ambiguous_same_line"


def test_lambda_including_nested_lambda_appears_in_inventory(tmp_path, monkeypatch):
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    (tools_dir / "lambda_mod.py").write_text(
        "f = lambda x: x + 1\n"
        "def factory():\n    f = lambda z: (lambda q: q * z)\n    return f\n",
        encoding="utf-8")
    report = tmp_path / "coverage.json"
    report.write_text(json.dumps({"totals": {"percent_covered": 75},
        "files": {"tools/lambda_mod.py": {"executed_lines": [1, 2, 3, 4], "missing_lines": []}}}),
        encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    result = inventory(tools_dir, report)
    assert result["functions_total"] == 4
    assert len(result["functions_ambiguous"]) == 3
    names = [item["function"] for item in result["function_details"]]
    assert "factory" in names
    assert len(set(names)) == 4
    assert any("<lambda@" in name for name in names)
    assert result["functions_exercised"] == 1
