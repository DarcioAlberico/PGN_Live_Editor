"""Setas e casas coloridas: `[%cal ...]` e `[%csl ...]` (SPEC 4.4).

Livro de xadrez moderno desenha em cima do diagrama -- seta do lance planejado,
casa marcada como fraca. Em PGN isso vive **dentro do comentario**, no formato
que o ChessBase criou e todo mundo passou a ler:

    {[%cal Ge2e4,Rd1h5] [%csl Yd5]}   seta verde e2->e4, seta vermelha d1->h5,
                                       casa d5 amarela

As cores sao uma letra: `G` verde, `R` vermelho, `Y` amarelo, `B` azul.

Este modulo so converte entre o texto do comentario e a lista de anotacoes. Nao
sabe desenhar nem editar -- quem desenha e o painel de xadrez, quem edita e o
texto bruto, que continua sendo a fonte da verdade.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import chess

CAL_TAG = "cal"
CSL_TAG = "csl"

#: Letra de cor -> cor de desenho. As quatro que o formato define.
ANNOTATION_COLORS: dict[str, str] = {
    "G": "#5bb974",
    "R": "#e05c5c",
    "Y": "#f0c05a",
    "B": "#5b8dd9",
}
DEFAULT_COLOR = "G"

_COMMAND_RE = re.compile(r"\[%(cal|csl)\s+([^\]]*)\]")
_ARROW_RE = re.compile(r"^([GRYB])([a-h][1-8])([a-h][1-8])$")
_SQUARE_RE = re.compile(r"^([GRYB])([a-h][1-8])$")


@dataclass(frozen=True)
class Arrow:
    tail: chess.Square
    head: chess.Square
    color: str = DEFAULT_COLOR

    def to_token(self) -> str:
        return f"{self.color}{chess.square_name(self.tail)}{chess.square_name(self.head)}"


@dataclass(frozen=True)
class SquareMark:
    square: chess.Square
    color: str = DEFAULT_COLOR

    def to_token(self) -> str:
        return f"{self.color}{chess.square_name(self.square)}"


@dataclass
class VisualAnnotations:
    arrows: list[Arrow]
    squares: list[SquareMark]

    def __bool__(self) -> bool:
        return bool(self.arrows or self.squares)

    def toggled_arrow(self, arrow: Arrow) -> VisualAnnotations:
        """Desenhar a mesma seta duas vezes a apaga -- e como se espera."""
        same = [item for item in self.arrows if item.tail == arrow.tail and item.head == arrow.head]
        arrows = [item for item in self.arrows if item not in same]
        if not same:
            arrows.append(arrow)
        return VisualAnnotations(arrows, list(self.squares))

    def toggled_square(self, mark: SquareMark) -> VisualAnnotations:
        same = [item for item in self.squares if item.square == mark.square]
        squares = [item for item in self.squares if item not in same]
        if not same:
            squares.append(mark)
        return VisualAnnotations(list(self.arrows), squares)


def parse_annotations(comment: str) -> VisualAnnotations:
    """Le as anotacoes de um comentario. Token invalido e simplesmente ignorado."""
    arrows: list[Arrow] = []
    squares: list[SquareMark] = []

    for tag, payload in _COMMAND_RE.findall(comment or ""):
        for token in (part.strip() for part in payload.split(",")):
            if not token:
                continue

            if tag == CAL_TAG:
                match = _ARROW_RE.match(token)
                if match:
                    color, tail, head = match.groups()
                    arrows.append(Arrow(chess.parse_square(tail), chess.parse_square(head), color))
                continue

            match = _SQUARE_RE.match(token)
            if match:
                color, square = match.groups()
                squares.append(SquareMark(chess.parse_square(square), color))

    return VisualAnnotations(arrows, squares)


def strip_annotations(comment: str) -> str:
    """O comentario sem os comandos visuais, com o espacamento arrumado."""
    cleaned = _COMMAND_RE.sub("", comment or "")
    return re.sub(r"\s{2,}", " ", cleaned).strip()


def format_annotations(annotations: VisualAnnotations) -> str:
    """Os comandos `[%cal]`/`[%csl]`, na ordem canonica, ou vazio."""
    parts: list[str] = []
    if annotations.arrows:
        parts.append(f"[%{CAL_TAG} " + ",".join(arrow.to_token() for arrow in annotations.arrows) + "]")
    if annotations.squares:
        parts.append(f"[%{CSL_TAG} " + ",".join(mark.to_token() for mark in annotations.squares) + "]")
    return " ".join(parts)


def apply_annotations(comment: str, annotations: VisualAnnotations) -> str:
    """Troca as anotacoes de um comentario, preservando a prosa que havia nele.

    A prosa vem primeiro e os comandos no fim, que e como o ChessBase escreve e
    como o leitor humano espera -- ninguem quer comecar a ler um comentario por
    `[%cal Ge2e4]`.
    """
    prose = strip_annotations(comment)
    commands = format_annotations(annotations)
    return " ".join(part for part in (prose, commands) if part)
