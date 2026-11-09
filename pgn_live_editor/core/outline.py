"""Arvore indentada de variantes, com os rotulos que o livro usa.

O preview de PGN e uma parede de texto corrido. Numa partida com cinco
niveis de variante -- que e o normal num livro de repertorio -- achar "a
sub-variante depois de 13.Nd5" ali dentro custa mais do que ler a pagina
impressa, que era o que se queria evitar.

Este modulo produz a outra vista: uma **linha por linha de jogo**, indentada
pela profundidade, rotulada com a mesma convencao da Thinkers/Quality/
Everyman -- `A)`, `B)`, `C1)`, `B1.2)`. O rotulo e o ponto: o usuario le
`B1.2` no livro, procura `B1.2` na arvore e cai no texto bruto exato.

A correspondencia com o livro e literal. Num ponto de ramificacao com N
alternativas, o livro chama a **continuacao** de `A)` e as variantes de
`B)`, `C)`... -- e e assim que sao rotuladas aqui, e nao com a variante 0
virando `A)`. Sem isso, todo rotulo sairia deslocado de um em relacao a
pagina impressa, que e pior do que nao ter rotulo nenhum.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from .parser import GameAST, MoveNode
from .text_ops import move_number_for

#: Alfabeto do primeiro nivel. Passando de `Z`, segue `AA`, `AB`...
_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

#: Quantos lances aparecem na previa de cada linha.
PREVIEW_MOVES = 8


def _letter(index: int) -> str:
    if index < len(_LETTERS):
        return _LETTERS[index]
    return _LETTERS[index // len(_LETTERS) - 1] + _LETTERS[index % len(_LETTERS)]


def child_label(parent: str, index: int) -> str:
    """`""`->`A`, `A`->`A1`, `A1`->`A1.1`, `A1.1`->`A1.1.1`.

    Publica de proposito: a arvore de linhas e a arvore de lances (`pgn_tree`)
    tem de rotular a **mesma** variante com o mesmo nome, senao as duas vistas
    da mesma partida discordam e nenhuma serve para localizar.
    """
    if not parent:
        return _letter(index)
    if parent.isalpha():
        return f"{parent}{index + 1}"
    return f"{parent}.{index + 1}"


@dataclass
class OutlineLine:
    """Uma linha de jogo inteira: da ramificacao ate onde ela morre."""

    label: str
    depth: int
    #: O lance de onde esta linha se desprende (`8. Be3`), ou vazio na principal.
    anchor: str
    preview: str
    move_count: int
    #: Span no texto bruto -- clicar navega para ca.
    start_index: int
    end_index: int
    #: `node_id` do primeiro lance, para casar com as entradas de `move_tree`.
    node_id: str
    children: list[OutlineLine] = field(default_factory=list)

    @property
    def title(self) -> str:
        return f"{self.label})" if self.label else "Linha principal"


def _move_label(move: MoveNode, force_number: bool = False) -> str:
    """`12... Be6`.

    O numero so e deduzido do tabuleiro quando faz falta -- no primeiro
    lance da linha e na ancora. No meio da previa ele atrapalha: repetir
    `1. e4 1... c5 2. Nf3` gasta o dobro do espaco para dizer o mesmo.
    """
    number = move.number or (move_number_for(move) if force_number else None)
    return " ".join(part for part in (number, move.display_san) if part)


def _preview_of(moves: Sequence[MoveNode]) -> str:
    shown = [_move_label(move, force_number=index == 0) for index, move in enumerate(moves[:PREVIEW_MOVES]) if move]
    text = " ".join(shown)
    return f"{text} …" if len(moves) > PREVIEW_MOVES else text


def _line_of(
    moves: Sequence[MoveNode],
    label: str,
    depth: int,
    anchor: MoveNode | None,
    node_id: str,
) -> OutlineLine:
    return OutlineLine(
        label=label,
        depth=depth,
        anchor=_move_label(anchor, force_number=True) if anchor else "",
        preview=_preview_of(moves),
        move_count=sum(1 for move in moves if move is not None),
        start_index=moves[0].start_index if moves else 0,
        end_index=moves[-1].end_index if moves else 0,
        node_id=node_id,
    )


def build_outline(ast: GameAST) -> OutlineLine:
    """A linha principal como raiz, com as variantes penduradas nela.

    O `node_id` segue a mesma convencao de `move_tree.build_move_entries` --
    indices de lance e de variante alternados -- para que clicar na arvore e
    clicar no preview cheguem ao mesmo lance.
    """

    def walk(moves: Sequence[MoveNode], label: str, depth: int, anchor: MoveNode | None, path: tuple[int, ...]):
        first_id = ".".join(str(part) for part in (*path, 0)) if moves else ".".join(str(part) for part in path)
        line = _line_of(moves, label, depth, anchor, first_id)

        # Todo ponto de ramificacao da linha vira um grupo de irmas. O livro
        # chama **esta** linha de `A)` naquele ponto, entao a primeira
        # variante e `B)`, a segunda `C)` -- deslocadas de um, de proposito.
        # E o contador corre pela linha inteira, nao por lance: duas
        # ramificacoes na mesma linha nao podem gerar dois `B)`, ou o rotulo
        # deixa de localizar, que e a unica coisa que ele serve para fazer.
        ordinal = 1
        for move_index, move in enumerate(moves):
            for variation_index, variation in enumerate(move.variations):
                if not variation:
                    continue
                line.children.append(
                    walk(
                        variation,
                        child_label(label, ordinal),
                        depth + 1,
                        move,
                        (*path, move_index, variation_index),
                    )
                )
                ordinal += 1
        return line

    return walk(ast.moves, "", 0, None, ())


def flatten_outline(root: OutlineLine) -> list[OutlineLine]:
    """A arvore em ordem de leitura -- e a ordem em que o livro imprime."""
    flat: list[OutlineLine] = []

    def visit(line: OutlineLine):
        flat.append(line)
        for child in line.children:
            visit(child)

    visit(root)
    return flat


def render_outline_text(root: OutlineLine, indent: str = "    ") -> str:
    """A mesma arvore em texto, para copiar ou conferir ao lado da pagina."""
    rows: list[str] = []
    for line in flatten_outline(root):
        if not line.preview:
            continue
        head = f"{indent * line.depth}{line.title}"
        anchor = f" [após {line.anchor}]" if line.anchor else ""
        rows.append(f"{head}{anchor} {line.preview}".rstrip())
    return "\n".join(rows)
