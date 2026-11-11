"""Posicao do tabuleiro como valor barato (R-401).

`board.fen()` custa ~33 us; montar um `chess.Board(fen)`, outro tanto. A
analise guarda a posicao de cada lance e consulta lances por posicao: com
estes 15 inteiros, guardar custa ~0,4 us e a posicao serve de chave de dicionario.
"""

from __future__ import annotations

import chess

Snapshot = tuple


def position_snapshot(board: chess.Board) -> Snapshot:
    return (
        board.pawns,
        board.knights,
        board.bishops,
        board.rooks,
        board.queens,
        board.kings,
        board.occupied_co[chess.WHITE],
        board.occupied_co[chess.BLACK],
        board.occupied,
        board.promoted,
        board.turn,
        board.castling_rights,
        board.ep_square,
        board.halfmove_clock,
        board.fullmove_number,
    )


def board_from_snapshot(snapshot: Snapshot) -> chess.Board:
    """Um tabuleiro novo na posicao (sem historico de lances)."""
    board = chess.Board(None)
    (
        board.pawns,
        board.knights,
        board.bishops,
        board.rooks,
        board.queens,
        board.kings,
        white,
        black,
        board.occupied,
        board.promoted,
        board.turn,
        board.castling_rights,
        board.ep_square,
        board.halfmove_clock,
        board.fullmove_number,
    ) = snapshot
    board.occupied_co[chess.WHITE] = white
    board.occupied_co[chess.BLACK] = black
    return board
