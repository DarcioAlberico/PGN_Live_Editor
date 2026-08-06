"""Copiar trechos da partida em formatos uteis.

O fluxo de transcricao de livro e feito de recortes: pegar uma variante inteira
para colar noutro ponto, tirar a FEN de um diagrama, exportar so a linha
principal para conferir contra o texto impresso.
"""

from __future__ import annotations

from dataclasses import dataclass

import chess

from ..core.move_tree import MoveEntry
from ..core.parser import GameAST, MoveNode


@dataclass
class CopyFormat:
    key: str
    label: str
    needs_entry: bool = True


COPY_FORMATS: tuple[CopyFormat, ...] = (
    # "Esta linha" e a variante quando o lance esta dentro de uma; na linha
    # principal, e a propria linha principal.
    CopyFormat("variation", "Copiar esta linha (entre parenteses)"),
    CopyFormat("from_here", "Copiar daqui ate o fim"),
    CopyFormat("no_comments", "Copiar sem comentarios"),
    CopyFormat("fen", "Copiar FEN"),
    CopyFormat("game_from_here", "Copiar como partida (com FEN)"),
    CopyFormat("mainline", "Copiar linha principal", needs_entry=False),
)


def _line_containing(ast: GameAST, node: MoveNode) -> list[MoveNode] | None:
    """A lista de lances (principal ou variante) a que este lance pertence."""

    def search(moves: list[MoveNode]) -> list[MoveNode] | None:
        if any(move is node for move in moves):
            return moves
        for move in moves:
            for variation in move.variations:
                found = search(variation)
                if found is not None:
                    return found
        return None

    return search(ast.moves)


def _format_line(start_fen: str, moves: list[MoveNode], include_comments: bool = True) -> str:
    board = chess.Board(start_fen)
    parts: list[str] = []
    previous_was_white = False

    for index, move in enumerate(moves):
        if include_comments:
            parts.extend(f"{{{comment}}}" for comment in move.comments_before if comment)

        if board.turn == chess.WHITE:
            parts.append(f"{board.fullmove_number}. {move.display_san}")
            previous_was_white = True
        elif index == 0 or not previous_was_white:
            parts.append(f"{board.fullmove_number}... {move.display_san}")
            previous_was_white = False
        else:
            parts.append(move.display_san)
            previous_was_white = False

        if include_comments and move.nags:
            parts.extend(move.nags)

        try:
            board.push(chess.Move.null() if move.is_null_move else board.parse_san(move.display_san))
        except ValueError:
            break

        if include_comments:
            parts.extend(f"{{{comment}}}" for comment in move.comments_after if comment)

    return " ".join(parts)


def board_at(entry: MoveEntry, start_fen: str, *, before_move: bool = False) -> chess.Board:
    """Tabuleiro na posicao do lance selecionado."""
    board = chess.Board(start_fen)
    path = entry.path[:-1] if before_move else entry.path
    for move in path:
        if not move.is_valid:
            break
        try:
            board.push(chess.Move.null() if move.is_null_move else board.parse_san(move.display_san))
        except ValueError:
            break
    return board


def copy_text(
    key: str,
    ast: GameAST,
    entry: MoveEntry | None,
    start_fen: str = chess.STARTING_FEN,
    headers: dict[str, str] | None = None,
) -> str:
    if key == "mainline":
        return _format_line(start_fen, ast.moves, include_comments=False)

    if entry is None:
        return ""

    line = _line_containing(ast, entry.node)
    if line is None:
        return ""

    position = next(index for index, move in enumerate(line) if move is entry.node)
    line_start_fen = entry.node.parent_fen or start_fen

    if key == "variation":
        base_fen = line[0].parent_fen or start_fen
        return f"( {_format_line(base_fen, line)} )"

    if key == "from_here":
        return _format_line(line_start_fen, line[position:])

    if key == "no_comments":
        return _format_line(line_start_fen, line[position:], include_comments=False)

    if key == "fen":
        return board_at(entry, start_fen).fen()

    if key == "game_from_here":
        board = board_at(entry, start_fen, before_move=True)
        tags = dict(headers or {})
        lines = [
            f'[Event "{tags.get("Event", "Trecho do PGN Live Editor")}"]',
            f'[Site "{tags.get("Site", "?")}"]',
            f'[Date "{tags.get("Date", "????.??.??")}"]',
            f'[Round "{tags.get("Round", "?")}"]',
            f'[White "{tags.get("White", "?")}"]',
            f'[Black "{tags.get("Black", "?")}"]',
            '[Result "*"]',
            '[SetUp "1"]',
            f'[FEN "{board.fen()}"]',
            "",
            f"{_format_line(board.fen(), line[position:])} *",
        ]
        return "\n".join(lines)

    return ""
