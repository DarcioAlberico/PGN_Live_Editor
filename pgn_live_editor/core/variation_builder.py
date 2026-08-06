from collections.abc import Sequence

import chess


def build_variation_text(start_fen: str, sans: Sequence[str]) -> str:
    if not sans:
        return ""

    board = chess.Board(start_fen)
    parts = []
    previous_turn = None

    for san in sans:
        current_turn = board.turn
        move_number = board.fullmove_number

        if current_turn == chess.WHITE:
            parts.append(f"{move_number}. {san}")
        elif previous_turn != chess.WHITE:
            parts.append(f"{move_number}... {san}")
        else:
            parts.append(san)

        board.push_san(san)
        previous_turn = current_turn

    return f"( {' '.join(parts)} )"
