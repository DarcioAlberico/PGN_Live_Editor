"""dryer: funcoes duplicadas, por semelhanca de tokens normalizados.

Uso:
    uv run python tools/quality/dryer.py pgn_live_editor --min-rank 0.8

Nomes viram `N` e literais viram `L` (palavras-chave ficam), entao uma copia
renomeada ainda casa. A nota e o `ratio()` do difflib sobre as duas sequencias.
Funcoes com menos de `--min-tokens` tokens ficam de fora (getters e afins).

Com `--fail`, sai com codigo 1 se algum par passar da nota (portao de CI).
"""

from __future__ import annotations

import argparse
import ast
import io
import textwrap
import tokenize
from difflib import SequenceMatcher
from pathlib import Path

KEYWORDS = {
    "def", "return", "if", "elif", "else", "for", "while", "in", "not", "and", "or", "is",
    "None", "True", "False", "try", "except", "finally", "with", "as", "class", "lambda",
    "yield", "break", "continue", "pass", "raise", "self", "import", "from",
}  # fmt: skip
SKIPPED = (tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT, tokenize.ENDMARKER)


def normalized_tokens(source: str) -> list[str]:
    tokens = []
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type in SKIPPED:
            continue
        if token.type == tokenize.NAME:
            tokens.append(token.string if token.string in KEYWORDS else "N")
        elif token.type in (tokenize.NUMBER, tokenize.STRING):
            tokens.append("L")
        else:
            tokens.append(token.string)
    return tokens


def units(root: Path, min_tokens: int) -> list[tuple[str, str, list[str]]]:
    found = []
    for path in sorted(root.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        lines = source.splitlines()
        for function in ast.walk(ast.parse(source)):
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            body = textwrap.dedent("\n".join(lines[function.lineno - 1 : function.end_lineno]))
            try:
                tokens = normalized_tokens(body)
            except (tokenize.TokenError, IndentationError):
                continue
            if len(tokens) >= min_tokens:
                found.append((f"{path.as_posix()}:{function.lineno}", function.name, tokens))
    return found


def similar_pairs(found, min_rank: float):
    pairs = []
    for i, first in enumerate(found):
        for second in found[i + 1 :]:
            shorter, longer = sorted((len(first[2]), len(second[2])))
            if shorter / longer < min_rank:
                continue
            matcher = SequenceMatcher(None, first[2], second[2], autojunk=False)
            if matcher.real_quick_ratio() < min_rank or matcher.quick_ratio() < min_rank:
                continue
            rank = matcher.ratio()
            if rank >= min_rank:
                pairs.append((rank, shorter, first, second))
    pairs.sort(key=lambda pair: (-pair[0], -pair[1]))
    return pairs


def main() -> int:
    parser = argparse.ArgumentParser(description="pares de funcoes duplicadas")
    parser.add_argument("root", type=Path)
    parser.add_argument("--min-rank", type=float, default=0.8)
    parser.add_argument("--min-tokens", type=int, default=40)
    parser.add_argument("--fail", action="store_true", help="codigo 1 se houver par acima da nota")
    args = parser.parse_args()

    found = units(args.root, args.min_tokens)
    pairs = similar_pairs(found, args.min_rank)
    print(f"{len(found)} funcoes, {len(pairs)} pares com nota >= {args.min_rank:g}")
    for rank, size, first, second in pairs:
        print(f"{rank:.2f} tok={size:4d}  {first[1]} @ {first[0]}  <->  {second[1]} @ {second[0]}")
    return 1 if args.fail and pairs else 0


if __name__ == "__main__":
    raise SystemExit(main())
