"""Conservative AST and coverage.py inventory of Python callable bodies.

Line-level traces cannot prove that a one-line function or same-line lambda
executed: importing the module can execute its declaration line. Such cases
are explicitly UNVERIFIED, not counted as called.
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
                    # Import alone marks a one-line declaration as executed;
                    # ignore its line, which does not prove that body ran.
                    independent_body = {line for line in own if line > child.lineno}
                    covered = bool(independent_body & executed)
                    status = (
                        "exercised" if covered else
                        "ambiguous_same_line" if own and not independent_body else
                        "unexecuted"
                    )
                    results.append({
                        "file": relative,
                        "function": identifier,
                        "line": child.lineno,
                        "covered": covered,
                        "status": status,
                        "executable_lines": len(independent_body),
                        "executed_lines": len(independent_body & executed),
                    })
                    descend(child, f"{identifier}.")
                elif isinstance(child, ast.Lambda):
                    # Lambda's expression usually shares its declaration line.
                    # Treat as unverified rather than misattribute line traces.
                    identifier = f"{prefix}<lambda@{child.lineno}:{child.col_offset}>"
                    results.append({
                        "file": relative,
                        "function": identifier,
                        "line": child.lineno,
                        "covered": False,
                        "status": "ambiguous_lambda",
                        "executable_lines": 0,
                        "executed_lines": 0,
                    })
                    descend(child, f"{identifier}.")
                elif isinstance(child, ast.ClassDef):
                    descend(child, f"{prefix}{child.name}.")
                else:
                    descend(child, prefix)
        descend(tree)
    untested = [item for item in results if not item["covered"]]
    ambiguous = [item for item in untested if item["status"].startswith("ambiguous")]
    return {
        "coverage_percent": report["totals"]["percent_covered"],
        "functions_total": len(results),
        "functions_exercised": len(results) - len(untested),
        "functions_unexercised": untested,
        "functions_ambiguous": ambiguous,
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
    print(f"Function bodies verifiably exercised: {result['functions_exercised']}/{result['functions_total']}")
    print(f"Function call-site ambiguity: {len(result['functions_ambiguous'])}")
    for item in result["functions_unexercised"]:
        print(f"UNVERIFIED {item['file']}:{item['line']} {item['function']} ({item['status']})")
    return int(args.fail_untested and bool(result["functions_unexercised"]))


if __name__ == "__main__":
    raise SystemExit(main())
