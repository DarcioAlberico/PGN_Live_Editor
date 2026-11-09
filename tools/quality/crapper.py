"""crapper: CRAP por funcao = cc² · (1 − cobertura)³ + cc.

Uso:
    uv run coverage run -m pytest -q
    uv run coverage json -o coverage.json
    uv run python tools/quality/crapper.py coverage.json pgn_live_editor/core --threshold 10

A complexidade ciclomatica vem da AST (1 + pontos de decisao, estilo McCabe); a
cobertura de cada funcao, das linhas executadas/faltantes do JSON do coverage.py
dentro do intervalo da funcao, sem as funcoes aninhadas. Funcao de modulo que o
coverage nunca viu conta como 0 % coberta.

Com `--fail`, sai com codigo 1 se alguma funcao passar do limite (portao de CI).
"""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path

DECISIONS = (
    ast.If,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.IfExp,
    ast.ExceptHandler,
    ast.Assert,
    ast.comprehension,
    ast.match_case,
)
FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)


def complexity(function: ast.AST) -> int:
    value = 1
    for node in ast.walk(function):
        if node is not function and isinstance(node, (*FUNCTIONS, ast.Lambda)):
            continue
        if isinstance(node, DECISIONS):
            value += 1
            if isinstance(node, ast.comprehension):
                value += len(node.ifs)
        elif isinstance(node, ast.BoolOp):
            value += len(node.values) - 1
    return value


def nested_lines(function: ast.AST) -> set[int]:
    lines: set[int] = set()
    for node in ast.walk(function):
        if node is not function and isinstance(node, FUNCTIONS):
            lines.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))
    return lines


def crap_rows(coverage_json: Path, root: Path) -> list[tuple[float, int, float, str, str]]:
    coverage = json.loads(coverage_json.read_text(encoding="utf-8"))
    files = {Path(name).as_posix().lower(): data for name, data in coverage["files"].items()}
    rows = []
    for path in sorted(root.rglob("*.py")):
        relative = path.as_posix().lower()
        key = next((name for name in files if relative.endswith(name) or name.endswith(relative)), None)
        data = files.get(key) if key else None
        executed = set(data["executed_lines"]) if data else set()
        missing = set(data["missing_lines"]) if data else None
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for function in ast.walk(tree):
            if not isinstance(function, FUNCTIONS):
                continue
            span = set(range(function.lineno, (function.end_lineno or function.lineno) + 1)) - nested_lines(function)
            if missing is None:
                covered = 0.0
            else:
                hit, miss = len(span & executed), len(span & missing)
                covered = hit / (hit + miss) if hit + miss else 1.0
            cc = complexity(function)
            crap = cc**2 * (1 - covered) ** 3 + cc
            rows.append((crap, cc, covered, f"{path.as_posix()}:{function.lineno}", function.name))
    rows.sort(reverse=True)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="CRAP por funcao")
    parser.add_argument("coverage_json", type=Path)
    parser.add_argument("root", type=Path)
    parser.add_argument("--threshold", type=float, default=10.0)
    parser.add_argument("--fail", action="store_true", help="codigo 1 se alguma funcao passar do limite")
    args = parser.parse_args()

    rows = crap_rows(args.coverage_json, args.root)
    over = [row for row in rows if row[0] >= args.threshold]
    print(f"{len(rows)} funcoes, {len(over)} com CRAP >= {args.threshold:g}")
    for crap, cc, covered, location, name in over:
        print(f"{crap:8.1f}  cc={cc:3d}  cob={covered:5.0%}  {name:40s} {location}")
    return 1 if args.fail and over else 0


if __name__ == "__main__":
    raise SystemExit(main())
