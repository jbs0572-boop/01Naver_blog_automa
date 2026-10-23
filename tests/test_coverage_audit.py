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
