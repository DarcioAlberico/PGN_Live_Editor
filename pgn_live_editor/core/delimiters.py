"""Onde comeca e termina cada comentario e cada variante -- decidido num lugar so (R-201).

A regra de comentario e a de `normalizer.COMMENT_RE`, a mesma que o parser usa:

- `{...}` fechado pode ocupar varias linhas, mas nao atravessa linha em branco;
- `{` sem fechamento vale ate o fim da linha.

Antes, `check_balance`, `scope_end`, `find_enclosing_delimiter`, a troca de
marcas de diagrama e a conversao de colchetes do livro tinham cada uma a sua
regra (N-07, L-16): um comentario valido de duas linhas era acusado de aberto,
um `)` dentro de comentario fechava a variante, um `{` aberto engolia a `}` de
outro comentario.

Tudo aqui e linear no tamanho do texto e puro (sem estado).
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass

from .normalizer import COMMENT_RE

RESULT_TAIL_RE = re.compile(r"\s*(?:1-0|0-1|1/2-1/2|\*)\s*$")


@dataclass(frozen=True)
class BalanceProblem:
    position: int
    message: str


def comment_spans(text: str) -> list[tuple[int, int, bool]]:
    """`(inicio, fim_exclusivo, fechado)` de cada comentario, na ordem do texto."""
    spans: list[tuple[int, int, bool]] = []
    index = text.find("{")
    while index != -1:
        match = COMMENT_RE.match(text, index)
        end = match.end() if match else index + 1
        spans.append((index, end, text[end - 1] == "}" and end - index > 1))
        index = text.find("{", end)
    return spans


def structural_chars(text: str, chars: str = "(){}") -> Iterator[tuple[int, str]]:
    """Os caracteres de `chars` que ficam **fora** de comentario.

    O `{` que abre um comentario sai como `{` (com o comentario inteiro pulado);
    uma `}` fora de comentario sai como `}` -- e uma chave sem par.
    """
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char == "{":
            match = COMMENT_RE.match(text, index)
            if "{" in chars:
                yield index, "{"
            index = match.end() if match else index + 1
            continue
        if char in chars:
            yield index, char
        index += 1


def check_balance(text: str) -> list[BalanceProblem]:
    """Delimitadores abertos e nao fechados, ou fechados sem abrir."""
    problems: list[BalanceProblem] = []
    for start, _end, closed in comment_spans(text):
        if not closed:
            problems.append(BalanceProblem(start, "Comentário aberto com '{' e nunca fechado."))
    open_parens: list[int] = []
    for index, char in structural_chars(text, "()}"):
        if char == "}":
            problems.append(BalanceProblem(index, "'}' sem '{' correspondente."))
        elif char == "(":
            open_parens.append(index)
        elif open_parens:
            open_parens.pop()
        else:
            problems.append(BalanceProblem(index, "')' sem '(' correspondente."))
    problems.extend(BalanceProblem(index, "'(' sem ')' correspondente.") for index in open_parens)
    problems.sort(key=lambda problem: problem.position)
    return problems


def find_enclosing_delimiter(text: str, position: int) -> tuple[int, int, str] | None:
    """Menor par `{}` ou `()` que contem `position`. Devolve (abre, fecha, tipo).

    `fecha` e o indice do caractere de fechamento. Comentario sem `}` nao e par.
    """
    best: tuple[int, int, str] | None = None

    def consider(open_index: int, close_index: int, kind: str) -> None:
        nonlocal best
        if open_index <= position <= close_index and (best is None or close_index - open_index < best[1] - best[0]):
            best = (open_index, close_index, kind)

    for start, end, closed in comment_spans(text):
        if closed:
            consider(start, end - 1, "{}")
    stack: list[int] = []
    for index, char in structural_chars(text, "()"):
        if char == "(":
            stack.append(index)
        elif stack:
            consider(stack.pop(), index, "()")
    return best


def scope_end(text: str, position: int) -> int:
    """Fim do escopo atual: o `)` que fecha a variante em volta, ou o fim do texto.

    O resultado final (`1-0`, `*`) fica de fora -- ele pertence a partida, nao
    a linha que esta sendo movida.
    """
    depth = 0
    for index, char in structural_chars(text[position:], "()"):
        if char == "(":
            depth += 1
        elif depth == 0:
            return position + index
        else:
            depth -= 1
    tail = RESULT_TAIL_RE.search(text, position)
    return tail.start() if tail else len(text)


def in_comment(spans: list[tuple[int, int, bool]], position: int) -> bool:
    """`position` cai dentro de um comentario (as chaves contam como dentro)?"""
    return any(start <= position < end for start, end, _closed in spans)
