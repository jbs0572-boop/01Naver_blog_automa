"""Report executed and unexecuted Python functions from coverage.py JSON.

A function is counted as exercised only when coverage recorded an executable
statement within its own body, not merely its import-time declaration.
"""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path


def own_statements(function: ast.FunctionDef | ast.AsyncFunctionDef) -> set[int]:
    """Find statement lines, excluding nested function/class bodies."""
    lines: set[int] = set()

    def visit(node: ast.AST) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            return
        if isinstance(node, ast.stmt) and not (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            lines.add(node.lineno)
        for child in ast.iter_child_nodes(node):
            visit(child)

    for statement in function.body:
        visit(statement)
    return lines


def inventory(source_dir: Path, coverage_json: Path) -> dict:
    report = json.loads(coverage_json.read_text(encoding="utf-8"))
    files = report["files"]
    results = []
    for source_file in sorted(source_dir.glob("*.py")):
        if source_file.name == "__init__.py":
            continue
        try:
            relative = source_file.relative_to(Path.cwd()).as_posix()
        except ValueError:
            relative = f"tools/{source_file.name}"
        measured = files.get(relative, {})
        executed = set(measured.get("executed_lines", []))
        executable = executed | set(measured.get("missing_lines", []))
        tree = ast.parse(source_file.read_text(encoding="utf-8"), filename=str(source_file))

        def descend(node: ast.AST, prefix: str = "") -> None:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    identifier = f"{prefix}{child.name}"
                    own = own_statements(child) & executable
                    # Bodies with no executable statements (protocol stubs) remain unverified.
                    covered = bool(own & executed)
                    results.append({
                        "file": relative,
                        "function": identifier,
                        "line": child.lineno,
                        "covered": covered,
                        "executable_lines": len(own),
                        "executed_lines": len(own & executed),
                    })
                    descend(child, f"{identifier}.")
                elif isinstance(child, ast.ClassDef):
                    descend(child, f"{prefix}{child.name}.")
                else:
                    descend(child, prefix)
        descend(tree)
    untested = [item for item in results if not item["covered"]]
    return {
        "coverage_percent": report["totals"]["percent_covered"],
        "functions_total": len(results),
        "functions_exercised": len(results) - len(untested),
        "functions_unexercised": untested,
        "function_details": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=Path("tools"))
    parser.add_argument("--coverage-json", type=Path, default=Path("coverage.json"))
    parser.add_argument("--out", type=Path, default=Path("function-coverage.json"))
    parser.add_argument("--fail-untested", action="store_true")
    args = parser.parse_args()
    result = inventory(args.source_dir, args.coverage_json)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Line/branch coverage: {result['coverage_percent']:.2f}%")
    print(f"Functions exercised: {result['functions_exercised']}/{result['functions_total']}")
    for item in result["functions_unexercised"]:
        print(f"UNTESTED {item['file']}:{item['line']} {item['function']}")
    return int(args.fail_untested and bool(result["functions_unexercised"]))


if __name__ == "__main__":
    raise SystemExit(main())
