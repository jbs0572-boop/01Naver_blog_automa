"""Read-only diagnostics for the immutable historical-log regression gate.

A valid shape/count is NOT proof of historical origin. This command never writes
or repairs logs and does not replace the original full-suite assertions.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from tools.contract_types import ContractError
from tools.log_contract import validate_log

REQUIRED_LOGS = {
    "runs/beta-2026-08-26-energy-day.jsonl": 8,
    "runs/beta-gate0-2026-08-26-9topics.jsonl": 66,
}


def inspect_logs(root: Path) -> dict:
    root = root.resolve()
    results = []
    for relative, expected in REQUIRED_LOGS.items():
        path = root / relative
        item = {"path": relative, "expected_records": expected,
                "provenance": "unverified", "sha256": None}
        results.append(item)
        # Do not inspect a substituted log (or runs directory) outside the tree.
        if path.resolve().is_relative_to(root) is False:
            item["status"] = "outside_root"
            continue
        if path.is_symlink() or path.parent.is_symlink():
            item["status"] = "symlink_rejected"
            continue
        if not path.exists():
            item["status"] = "missing"
            continue
        if not path.is_file():
            item["status"] = "not_regular_file"
            continue
        try:
            before = path.read_bytes()
            item["sha256"] = hashlib.sha256(before).hexdigest()
            count = validate_log(path)
            if path.read_bytes() != before:
                item["status"] = "changed_during_check"
                continue
            item["actual_records"] = count
            item["status"] = "readable_expected_count" if count == expected else "count_mismatch"
        except (OSError, UnicodeError, ContractError) as error:
            item["status"] = "unreadable_or_invalid"
            item["error_type"] = type(error).__name__
    ready = all(item["status"] == "readable_expected_count" for item in results)
    return {"diagnostic_status": "inputs_readable" if ready else "blocked",
            "historical_authenticity": "not_established",
            "full_suite": "not_run_by_this_command", "logs": results}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    result = inspect_logs(args.root)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["diagnostic_status"] == "inputs_readable" else 1


if __name__ == "__main__":
    raise SystemExit(main())
