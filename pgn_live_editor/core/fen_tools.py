"""Posicao inicial, `SetUp` e marcadores de diagrama (SPEC 2.6 -- corrige D10).

Meio livro de xadrez nao comeca na posicao inicial: exercicios, finais e
posicoes de estudo partem de um diagrama. Para o programa, isso e um cabecalho
`FEN` coerente com `SetUp` -- e um marcador de diagrama que o livro imprime e o
OCR traz junto (`[D]`, `(diagrama)`), que precisa virar comentario em vez de
lance nao reconhecido.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import chess

from .delimiters import comment_spans, in_comment
from .pgn_headers import extract_pgn_headers, upsert_headers
from .text_ops import OperationResult, TextEdit

# Marcador padrao ChessBase; e para ele que os demais convergem.
DIAGRAM_MARK = "{[#]}"

_DIAGRAM_PATTERNS: tuple[str, ...] = (
    r"\{\s*\[#\]\s*\}",
    r'(?<![\w"])\[\s*[Dd]\s*\](?![\w"])',
    r'(?<![\w"])[\[(]\s*(?:diagramas?|diagrams?)\s*[\])](?![\w"])',
)

DIAGRAM_RE = re.compile("|".join(f"(?:{pattern})" for pattern in _DIAGRAM_PATTERNS), re.IGNORECASE)


@dataclass(frozen=True)
class DiagramMark:
    start: int
    end: int
    raw: str

    @property
    def is_canonical(self) -> bool:
        return self.raw == DIAGRAM_MARK


def find_diagram_marks(text: str) -> list[DiagramMark]:
    """Marcas de diagrama **fora** de comentario (`{[#]}` e o proprio comentario, entao vale).

    Um `[D]` dentro de um comentario e texto do autor; reescreve-lo como
    `{[#]}` abria uma chave dentro de outra e quebrava o comentario (N-07).
    """
    spans = comment_spans(text)
    marks = []
    for match in DIAGRAM_RE.finditer(text):
        if text[match.start()] != "{" and in_comment(spans, match.start()):
            continue
        marks.append(DiagramMark(match.start(), match.end(), match.group(0)))
    return marks


def normalize_diagram_marks(text: str) -> OperationResult:
    """Todo marcador de diagrama vira `{[#]}` -- um comentario, nao um alerta.

    Sem isto, um `[D]` solto no meio da notacao entra como candidato a lance e
    enche o painel de erros que nao sao erros do usuario.
    """
    edits = [TextEdit(mark.start, mark.end, DIAGRAM_MARK) for mark in find_diagram_marks(text) if not mark.is_canonical]
    if not edits:
        return OperationResult([], "Nenhum marcador de diagrama a converter.")
    return OperationResult(edits, f"{len(edits)} marcador(es) de diagrama convertido(s) em {DIAGRAM_MARK}.")


def is_valid_fen(value: str) -> bool:
    try:
        chess.Board(value.strip())
    except ValueError:
        return False
    return True


def start_fen_of(headers: dict[str, str]) -> str:
    """FEN inicial declarado pelos cabecalhos, com queda para a posicao inicial."""
    fen = (headers.get("FEN") or "").strip()
    return fen if fen and is_valid_fen(fen) else chess.STARTING_FEN


def set_start_fen(
    raw_text: str,
    fen: str,
    region_start: int = 0,
    region_end: int | None = None,
) -> OperationResult:
    """Grava `FEN` + `SetUp "1"` no cabecalho da partida indicada.

    E o que sustenta o botao *usar a posicao atual como posicao inicial*: o
    usuario monta o diagrama no tabuleiro e o fragmento passa a partir dali.
    """
    fen = (fen or "").strip()
    if not is_valid_fen(fen):
        return OperationResult([], "FEN inválida; a posição inicial não foi alterada.")

    if fen == chess.STARTING_FEN:
        return clear_start_fen(raw_text, region_start, region_end)

    change = upsert_headers(raw_text, {"FEN": fen, "SetUp": "1"}, region_start, region_end)
    if change is None:
        return OperationResult([], "Esta partida já parte desta posição.")

    start, end, block = change
    return OperationResult([TextEdit(start, end, block)], "Posição inicial da partida atualizada.")


def clear_start_fen(
    raw_text: str,
    region_start: int = 0,
    region_end: int | None = None,
) -> OperationResult:
    """Tira `FEN` e `SetUp`: a partida volta a comecar do inicio."""
    region_end = len(raw_text) if region_end is None else region_end
    existing, _body, _consumed = extract_pgn_headers(raw_text[region_start:region_end])
    if not existing.keys() & {"FEN", "SetUp"}:
        # Sem cabecalho de posicao não há o que remover -- e criar um bloco de
        # cabecalhos inteiro seria o oposto do que foi pedido.
        return OperationResult([], "Esta partida já comeca na posição inicial.")

    change = upsert_headers(raw_text, {"FEN": None, "SetUp": None}, region_start, region_end)
    if change is None:
        return OperationResult([], "Esta partida já comeca na posição inicial.")

    start, end, block = change
    return OperationResult([TextEdit(start, end, block)], "A partida volta a comecar da posição inicial.")
