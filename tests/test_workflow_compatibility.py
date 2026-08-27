from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import tools.workflow_contract as facade
from tools import contract_types


def test_facade_exports_preserve_symbol_identity() -> None:
    assert facade.ContractError is contract_types.ContractError
    assert facade.SchemaError is contract_types.SchemaError
    assert facade.JSONMap is contract_types.JSONMap
    assert facade.JSONValue is contract_types.JSONValue
    assert facade.PIPELINE_VERSION is contract_types.PIPELINE_VERSION
    assert facade.SCHEMA_VERSION is contract_types.SCHEMA_VERSION
    assert set(facade.__all__) >= {
        "ContractError",
        "JSONMap",
        "JSONValue",
        "PIPELINE_VERSION",
        "build_manifest",
        "validate_instance",
        "validate_log",
        "verify_gate",
        "verify_manifest",
    }


def test_verifier_contract_error_exit_code_is_preserved() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "tools.workflow_verifier", "unknown-command"],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 2
    assert "unknown command" in completed.stderr
