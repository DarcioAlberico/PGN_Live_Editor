"""A partida como arvore de **lances**, para o painel ao lado do editor.

Ha tres vistas da mesma estrutura, e cada uma responde a uma pergunta
diferente. Vale dizer qual e qual, porque a diferenca entre elas nao e de
enfeite:

- `move_tree.render_game_html` -- **como a partida sai.** Texto corrido, do
  jeito que vai para o arquivo.
- `outline` -- **quantas linhas existem e onde cada uma comeca.** Uma linha
  de jogo inteira por item, recolhivel. Serve para achar `B1.2` no livro.
- este modulo -- **onde cada lance esta.** Um item por lance, com as
  variantes penduradas no lance de que sao alternativa. E a vista que mostra
  variante dentro de variante sem precisar ler nada.

O rotulo das variantes vem de `outline.child_label`, o mesmo das outras
vistas: duas arvores da mesma partida chamando a mesma variante por nomes
diferentes seriam piores do que uma arvore so.

O `node_id` segue a convencao de `move_tree.build_move_entries` -- indices de
lance e de variante alternados --, e e o que faz clicar aqui, clicar no
preview e mover o cursor no editor caírem todos no mesmo lance.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from .outline import child_label
from .parser import GameAST, MoveNode
from .text_ops import move_number_for

#: Quantos lances aparecem na previa de uma variante recolhida.
PREVIEW_MOVES = 6


@dataclass
class MoveItem:
    """Um lance da arvore, com as variantes que saem dele."""

    label: str
    san: str
    depth: int
    node_id: str
    start_index: int
    end_index: int
    #: `valid` | `fixed` | `review` | `error` -- a mesma classificacao do
    #: realce do editor, para que as duas contem a mesma historia.
    kind: str
    nags: str = ""
    #: Separados, e nao um texto so: o painel oferece colar comentario antes e
    #: depois do lance, e a dica de tela precisa poder dizer de que lado esta o
    #: que ja existe.
    comments_before: tuple[str, ...] = ()
    comments_after: tuple[str, ...] = ()
    variations: list[VariationItem] = field(default_factory=list)

    @property
    def title(self) -> str:
        return f"{self.label} {self.nags}".strip()

    @property
    def comment(self) -> str:
        """Tudo que o lance carrega, na ordem em que esta no texto."""
        return " ".join(part for part in (*self.comments_before, *self.comments_after) if part).strip()

    @property
    def comment_detail(self) -> str:
        """O mesmo, dizendo de que lado de cada lance cada trecho esta."""
        sides = (("Antes do lance", self.comments_before), ("Depois do lance", self.comments_after))
        return "\n".join(f"{name}: {' '.join(parts)}" for name, parts in sides if any(parts))


@dataclass
class VariationItem:
    """Uma variante inteira, pendurada no lance de que e alternativa."""

    label: str
    depth: int
    preview: str
    move_count: int
    start_index: int
    end_index: int
    moves: list[MoveItem] = field(default_factory=list)

    @property
    def title(self) -> str:
        return f"{self.label}) {self.preview}"

    @property
    def leading_comment(self) -> str:
        """A prosa com que a variante abre, se ela abrir com prosa.

        E o unico comentario que a linha recolhida pode mostrar sem mentir: o
        resto esta nos lances, que aparecem quando ela abre.
        """
        return " ".join(self.moves[0].comments_before) if self.moves else ""


def _kind_of(move: MoveNode) -> str:
    if not move.is_valid:
        return "error"
    if move.needs_review:
        return "review"
    if move.corrected_san:
        return "fixed"
    return "valid"


def _label_of(move: MoveNode, force_number: bool) -> str:
    number = move.number or (move_number_for(move) if force_number else None)
    return " ".join(part for part in (number, move.display_san) if part)


def _preview_of(moves: Sequence[MoveNode]) -> str:
    shown = [_label_of(move, force_number=index == 0) for index, move in enumerate(moves[:PREVIEW_MOVES])]
    text = " ".join(shown)
    return f"{text} …" if len(moves) > PREVIEW_MOVES else text


def _owner_of(move: MoveNode, variation_index: int, move_index: int, line_length: int) -> int:
    """Sob qual lance a variante deve aparecer.

    A AST guarda a variante no lance **de onde a posicao parte**; o leitor a
    procura no lance de que ela e **alternativa**. Quando `Variation.anchor_after`
    e verdadeiro, os dois nao sao o mesmo: `7.Qd2 ( 7.Be2 ... )` fica guardada
    em `6...e6` -- a posicao de onde ela sai -- mas quem abre a arvore atras da
    alternativa a `7.Qd2` olha debaixo de `7.Qd2`. Aqui ela e mostrada la.
    """
    if not move.variations[variation_index].anchor_after:
        return move_index
    # No fim da linha nao ha lance seguinte: a variante continua a partir
    # dela mesma, e o dono e o proprio lance.
    return min(move_index + 1, line_length - 1)


def build_pgn_tree(ast: GameAST) -> list[MoveItem]:
    """Os lances da linha principal, cada um com as suas variantes."""

    def walk(moves: Sequence[MoveNode], label: str, depth: int, path: tuple[int, ...]) -> list[MoveItem]:
        items = [
            MoveItem(
                # Numero em **todo** lance, e nao so no primeiro da linha: aqui
                # cada lance e uma linha da arvore, e `Nc6` sozinho numa linha
                # nao diz onde esta. Na previa de uma variante recolhida, que e
                # uma corrida horizontal, ele so atrapalharia -- ali vale a
                # regra oposta.
                label=_label_of(move, force_number=True),
                san=move.display_san,
                depth=depth,
                node_id=".".join(str(part) for part in (*path, move_index)),
                start_index=move.start_index,
                end_index=move.end_index,
                kind=_kind_of(move),
                nags="".join(move.nags),
                comments_before=tuple(comment for comment in move.comments_before if comment),
                comments_after=tuple(comment for comment in move.comments_after if comment),
            )
            for move_index, move in enumerate(moves)
        ]

        # O contador corre pela linha inteira, e nao por lance: duas
        # ramificacoes na mesma linha nao podem gerar dois `B)`. E ele corre na
        # ordem em que as variantes estao **guardadas**, que e a mesma que o
        # `outline` percorre -- e o que faz as duas arvores chamarem a mesma
        # variante pelo mesmo nome.
        ordinal = 1
        for move_index, move in enumerate(moves):
            for variation_index, variation in enumerate(move.variations):
                if not variation:
                    continue

                variation_label = child_label(label, ordinal)
                ordinal += 1
                item = VariationItem(
                    label=variation_label,
                    depth=depth + 1,
                    preview=_preview_of(variation),
                    move_count=len(variation),
                    start_index=variation[0].start_index,
                    end_index=variation[-1].end_index,
                    moves=walk(
                        variation,
                        variation_label,
                        depth + 1,
                        (*path, move_index, variation_index),
                    ),
                )
                items[_owner_of(move, variation_index, move_index, len(items))].variations.append(item)

        return items

    return walk(ast.moves, "", 0, ())


def find_move_at(items: Sequence[MoveItem], position: int) -> MoveItem | None:
    """O lance que contem `position`, o mais fundo deles.

    O mais fundo, e nao o primeiro: o span de um lance da linha principal
    nao envolve o das variantes dele, mas um erro de span num texto meio
    editado pode faze-lo -- e nesse caso a resposta util e a variante.
    """
    best: MoveItem | None = None

    def visit(nodes: Sequence[MoveItem]) -> None:
        nonlocal best
        for item in nodes:
            if item.start_index <= position <= item.end_index:
                if best is None or item.depth > best.depth:
                    best = item
            for variation in item.variations:
                visit(variation.moves)

    visit(items)
    return best


def count_moves(items: Sequence[MoveItem]) -> int:
    """Lances da arvore inteira, variantes incluidas."""
    total = 0
    for item in items:
        total += 1
        for variation in item.variations:
            total += count_moves(variation.moves)
    return total


def count_variations(items: Sequence[MoveItem]) -> int:
    total = 0
    for item in items:
        for variation in item.variations:
            total += 1 + count_variations(variation.moves)
    return total
