"""mutator: teste de mutacao por AST que nunca toca o projeto de verdade.

Uso:
    uv run python tools/quality/mutator.py pgn_live_editor/core/candidates.py \
        --tests tests/test_regressions.py tests/test_historic_guards.py --max 25

Copia o pacote, os testes e `Trabalhos Salvos/` para uma pasta temporaria,
aplica UMA mutacao por vez na copia e roda os testes indicados com `-x`.
Mutante que os testes nao matam e "sobrevivente": uma regra sem teste que a
segure. Mutante que trava alem de `--timeout` conta como morto (um laco
infinito e uma falha visivel).

Mutacoes: troca de comparacao (< <=, == !=, in/not in, is/is not), + <-> -,
* <-> //, and <-> or, remove `not`, inteiro +1, booleano invertido e
`return x` -> `return None` (pulado quando x ja e None).

A amostra e deterministica (`--seed`), para que dois relatorios sejam comparaveis.
"""

from __future__ import annotations

import argparse
import ast
import copy
import random
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

CMP_SWAP = {
    ast.Lt: ast.LtE,
    ast.LtE: ast.Lt,
    ast.Gt: ast.GtE,
    ast.GtE: ast.Gt,
    ast.Eq: ast.NotEq,
    ast.NotEq: ast.Eq,
    ast.In: ast.NotIn,
    ast.NotIn: ast.In,
    ast.Is: ast.IsNot,
    ast.IsNot: ast.Is,
}
BIN_SWAP = {ast.Add: ast.Sub, ast.Sub: ast.Add, ast.Mult: ast.FloorDiv, ast.FloorDiv: ast.Mult}


def candidates(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            for index, op in enumerate(node.ops):
                if type(op) in CMP_SWAP:
                    yield ("cmp", node, index)
        elif isinstance(node, ast.BinOp) and type(node.op) in BIN_SWAP:
            yield ("bin", node, None)
        elif isinstance(node, ast.BoolOp):
            yield ("bool", node, None)
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            yield ("not", node, None)
        elif isinstance(node, ast.Constant) and type(node.value) is int:
            yield ("int", node, None)
        elif isinstance(node, ast.Constant) and type(node.value) is bool:
            yield ("boolc", node, None)
        elif isinstance(node, ast.Return) and node.value is not None:
            already_none = isinstance(node.value, ast.Constant) and node.value.value is None
            if not already_none:
                yield ("ret", node, None)


class Apply(ast.NodeTransformer):
    def __init__(self, target, kind, index):
        self.target, self.kind, self.index = target, kind, index

    def generic_visit(self, node):
        node = super().generic_visit(node)
        if node is not self.target:
            return node
        if self.kind == "cmp":
            node.ops[self.index] = CMP_SWAP[type(node.ops[self.index])]()
        elif self.kind == "bin":
            node.op = BIN_SWAP[type(node.op)]()
        elif self.kind == "bool":
            node.op = ast.Or() if isinstance(node.op, ast.And) else ast.And()
        elif self.kind == "not":
            return node.operand
        elif self.kind == "int":
            node.value = node.value + 1
        elif self.kind == "boolc":
            node.value = not node.value
        elif self.kind == "ret":
            node.value = ast.Constant(value=None)
        return node


def copy_project(work: Path) -> None:
    shutil.copytree(ROOT / "pgn_live_editor", work / "pgn_live_editor", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "tests", work / "tests", ignore=shutil.ignore_patterns("__pycache__"))
    if (ROOT / "Trabalhos Salvos").exists():
        shutil.copytree(ROOT / "Trabalhos Salvos", work / "Trabalhos Salvos")
    shutil.copy(ROOT / "pyproject.toml", work / "pyproject.toml")


def run_tests(work: Path, tests: list[str], timeout: float) -> str:
    command = [sys.executable, "-m", "pytest", "-x", "-q", "-p", "no:cacheprovider", *tests]
    try:
        result = subprocess.run(command, cwd=work, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return "timeout"
    return "pass" if result.returncode == 0 else "fail"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("module", help="caminho do modulo a mutar, relativo a raiz do projeto")
    parser.add_argument("--tests", nargs="+", required=True)
    parser.add_argument("--max", type=int, default=25, help="quantos mutantes amostrar")
    parser.add_argument("--seed", type=int, default=1234)
    parser.add_argument("--timeout", type=float, default=300.0, help="segundos por mutante")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="mutator-") as tmp:
        work = Path(tmp)
        copy_project(work)
        target = work / args.module
        original = target.read_text(encoding="utf-8")
        tree = ast.parse(original)
        found = list(candidates(tree))
        random.Random(args.seed).shuffle(found)
        sample = found[: args.max]

        if run_tests(work, args.tests, args.timeout) != "pass":
            print("A suite ja falha sem mutacao nenhuma; nada a medir.")
            return 2

        order = list(ast.walk(tree))
        lines = original.splitlines()
        killed = timeouts = 0
        survivors: list[str] = []
        for kind, node, index in sample:
            mutated_tree = copy.deepcopy(tree)
            twin = list(ast.walk(mutated_tree))[order.index(node)]
            mutated = ast.fix_missing_locations(Apply(twin, kind, index).visit(mutated_tree))
            target.write_text(ast.unparse(mutated), encoding="utf-8")
            outcome = run_tests(work, args.tests, args.timeout)
            if outcome == "pass":
                line = getattr(node, "lineno", 0)
                survivors.append(f"{args.module}:{line} [{kind}] {lines[line - 1].strip()[:100]}")
            else:
                killed += 1
                timeouts += outcome == "timeout"
        target.write_text(original, encoding="utf-8")

    total = killed + len(survivors)
    score = killed / total if total else 0.0
    print(
        f"{args.module}: {total} mutantes, {killed} mortos ({timeouts} por timeout), "
        f"{len(survivors)} sobreviventes, placar {score:.0%}"
    )
    for survivor in survivors:
        print("  SOBREVIVEU", survivor)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
