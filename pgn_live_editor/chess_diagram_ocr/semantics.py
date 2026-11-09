"""Lado a jogar e direitos de roque: o que a posição responde sozinha (S-17).

A S-16 resolve os livros que declaram de quem é a vez. Medido, são **3 dos 27** do acervo:
12 não têm camada de texto nenhuma e 12 têm texto sem declarar o lado. Para os outros 24 a
única fonte que resta é a própria posição -- e ela responde com mais frequência do que
parece, porque uma regra do xadrez é decisiva aqui: *o lado que não está na vez não pode
estar em xeque*.

Então um diagrama que sai ilegal com `w` e legal com `b` não é um erro de leitura: é a vez
das pretas, e o palpite padrão é que estava errado. É a assinatura dos 51 rótulos
`OPPOSITE_CHECK` que a Fase 1 corrigiu à mão no `labels.csv`.

A cascata, com a fonte sempre registrada -- `[SideToMoveSource]` no PGN existe para que
"pretas jogam" possa ser conferido, e não só acreditado:

    1. texto (S-16)      declaração da legenda
    2. legalidade (S-17) prova pela posição
    3. padrão            brancas, e assumido como tal

A heurística de "quem tem ameaça costuma ser quem joga" fica de fora: precisa de motor, é
da S-33, e um palpite de motor registrado como inferência valeria menos que o padrão
honesto.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import chess

from .fen_utils import check_position

if TYPE_CHECKING:
    from .pdf_text import DiagramContext

logger = logging.getLogger(__name__)

SideSource = Literal[
    "text",
    "ocr",
    "glifo",
    "text-page-scope",
    "ocr-page-scope",
    "glifo-page-scope",
    "legality",
    "database",
    "manual",
    "default",
]

_SOURCE_LABELS: dict[SideSource, str] = {
    "text": "declarado no texto do PDF",
    "ocr": "lido por OCR da legenda",
    "glifo": "lido pelo classificador de caractere deste projeto, na legenda",
    "text-page-scope": "declarado no cabeçalho da página",
    "ocr-page-scope": "lido por OCR do cabeçalho da página",
    "glifo-page-scope": "lido pelo classificador deste projeto, no cabeçalho da página",
    "legality": "deduzido da legalidade da posição",
    "database": "da partida que a base casou",
    "manual": "declarado à mão na galeria",
    "default": "assumido (nada no PDF diz de quem é a vez)",
}
"""As seis procedências textuais não são preciosismo de rótulo (S-43, S-207).

A S-16 tinha uma fonte de texto e podia chamá-la de `"text"`. Com o OCR são quatro
respostas de valores diferentes, e colapsá-las faria o header `[SideToMoveSource "text"]`
significar tanto "está escrito na legenda deste diagrama, no arquivo" quanto "um motor leu
0,62 de confiança num cabeçalho que vale para a página inteira". Distinguir é o mínimo que
a Fase 3 pede: o header existe para que um palpite pareça um palpite.
"""

_TEXT_SOURCES: frozenset[str] = frozenset(
    {"text", "ocr", "glifo", "text-page-scope", "ocr-page-scope", "glifo-page-scope"}
)
"""As procedências que vêm de texto lido, seja qual for a fonte. A cascata as trata igual --
o que muda é só o que fica registrado."""


@dataclass(frozen=True)
class SideToMove:
    """De quem é a vez, de onde saiu essa resposta e o quanto ela vale."""

    color: chess.Color
    source: SideSource
    reason: str = ""

    conflicting: bool = False
    """Texto e posição discordaram. A posição venceu, mas isto vai para revisão.

    Não é preciosismo: a discordância significa que ou o OCR leu uma peça errada, ou a
    legenda foi associada ao diagrama vizinho. Nos dois casos há algo para um humano olhar,
    e nos dois casos o que sai é uma FEN legal -- só não necessariamente a certa.
    """

    @property
    def is_assumed(self) -> bool:
        return self.source == "default"

    @property
    def label(self) -> str:
        return "brancas" if self.color == chess.WHITE else "pretas"

    @property
    def source_label(self) -> str:
        return _SOURCE_LABELS[self.source]


def board_placement(fen: str) -> str:
    """Só o campo de peças da FEN, aceitando tanto `8/8/...` quanto a FEN completa."""
    return str(fen).strip().split(" ")[0]


def infer_side_to_move(placement: str, context: DiagramContext | None = None) -> SideToMove:
    """Cascata texto → legalidade → padrão. Nunca devolve `None`: devolve a fonte junto.

    A ordem entre as duas primeiras não é hierarquia de confiança, é ordem de aplicação. O
    texto responde primeiro por ser a única fonte capaz de dizer o lado numa posição em que
    as duas vezes são legais -- que é a maioria. Mas se o texto levar a uma posição ilegal e
    a outra vez for legal, quem manda é a legalidade: emitir FEN que se sabe ilegal seria
    pior de todos os jeitos, e é justamente o que o gate da S-15 rejeitaria depois.
    """
    placement = board_placement(placement)
    declared = context.side_to_move if context is not None else None
    proven = _turn_proven_by_legality(placement)

    if declared is not None:
        source = _declared_source(context)
        if proven is None or proven == declared:
            evidence = context.side_to_move_evidence if context is not None else ""
            reason = f"{_SOURCE_LABELS[source]}: {evidence!r}" if evidence else _SOURCE_LABELS[source]
            return SideToMove(color=declared, source=source, reason=reason)

        logger.info(
            "texto diz %s mas a posição só é legal com %s; a legalidade decide",
            "brancas" if declared == chess.WHITE else "pretas",
            "brancas" if proven == chess.WHITE else "pretas",
        )
        return SideToMove(
            color=proven,
            source="legality",
            reason="o texto do PDF dizia o contrário, mas a posição só fecha assim",
            conflicting=True,
        )

    if proven is not None:
        return SideToMove(
            color=proven,
            source="legality",
            reason="o lado que não joga não pode estar em xeque",
        )

    return SideToMove(color=chess.WHITE, source="default", reason="nada no PDF diz de quem é a vez")


def _declared_source(context: DiagramContext | None) -> SideSource:
    """Qual das quatro fontes textuais declarou. `"text"` quando o contexto não diz.

    O padrão é `"text"` e não um valor novo por compatibilidade honesta: um `DiagramContext`
    de antes da S-43 -- vindo de um `.partial.jsonl` gravado por uma versão anterior, ou
    construído à mão por um teste -- declarou pela camada de texto, que era a única que
    existia. Inventar `"unknown"` para ele seria perder informação que se tem.
    """
    origin = getattr(context, "side_to_move_origin", None)
    return origin if origin in _TEXT_SOURCES else "text"  # type: ignore[return-value]


def _turn_proven_by_legality(placement: str) -> chess.Color | None:
    """A vez que a posição impõe, quando só uma das duas é legal. `None` se ambas são.

    `None` também quando nenhuma das duas fecha: aí o problema não é o lado a jogar, é a
    leitura -- e inventar uma vez para uma posição impossível não ajudaria ninguém.
    """
    legal = [turn for turn in (chess.WHITE, chess.BLACK) if check_position(_with_turn(placement, turn)).is_legal]
    if len(legal) != 1:
        return None
    return legal[0]


def _with_turn(placement: str, turn: chess.Color) -> str:
    return f"{placement} {'w' if turn == chess.WHITE else 'b'} - - 0 1"


def infer_castling_rights(placement: str) -> str:
    """Direitos de roque compatíveis com onde as peças estão. `-` quando nenhum é possível.

    Conservador por natureza e por construção: a posição não diz se o rei já se moveu, só
    diz que ele *poderia* não ter se movido. Vale mesmo assim porque o erro tem lado -- num
    meio-jogo com o rei ainda em e1 e as torres nos cantos, `KQkq` é quase sempre certo, e
    num final onde isso seria falso a informação é irrelevante para a solução. O `-` fixo de
    hoje, ao contrário, erra em todas as posições iniciais de abertura.

    Registrado como `[CastlingSource "inferred"]`: derivado, não lido.
    """
    try:
        board = chess.Board(_with_turn(board_placement(placement), chess.WHITE))
    except (ValueError, AttributeError, TypeError):
        return "-"

    rights = ""
    for color, king_square, symbol in (
        (chess.WHITE, chess.E1, "KQ"),
        (chess.BLACK, chess.E8, "kq"),
    ):
        if board.piece_at(king_square) != chess.Piece(chess.KING, color):
            continue
        king_side, queen_side = (chess.H1, chess.A1) if color == chess.WHITE else (chess.H8, chess.A8)
        rook = chess.Piece(chess.ROOK, color)
        if board.piece_at(king_side) == rook:
            rights += symbol[0]
        if board.piece_at(queen_side) == rook:
            rights += symbol[1]
    return rights or "-"


def compose_fen(
    placement: str,
    side: SideToMove | chess.Color,
    *,
    castling: str | None = None,
    infer_castling: bool = True,
    fullmove: int = 1,
) -> str:
    """FEN completa a partir do campo de peças e do lado a jogar decidido.

    Substitui o `f"{fen} w - - 0 1"` espalhado pelo pipeline, que era o ponto exato onde a
    informação de lado a jogar se perdia (`fen_utils._normalize_fen`).

    `fullmove` é o número do lance, que o diagrama de livro não carrega e a pessoa pode
    declarar na galeria (S-67). O padrão continua 1, que é o que o PGN tinha antes -- e
    valores abaixo de 1 viram 1, porque a FEN não admite lance zero e recusar a exportação
    inteira por causa de um campo digitado errado seria desproporcional.
    """
    placement = board_placement(placement)
    color = side.color if isinstance(side, SideToMove) else side
    if castling is None:
        castling = infer_castling_rights(placement) if infer_castling else "-"
    return f"{placement} {'w' if color == chess.WHITE else 'b'} {castling} - 0 {max(1, int(fullmove))}"
