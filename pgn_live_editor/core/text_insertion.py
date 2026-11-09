import re

import chess


def build_board_move_insertion(
    existing_text: str,
    start: int,
    end: int,
    san: str,
    turn: chess.Color,
    fullmove_number: int,
) -> str:
    left_context = existing_text[:start]
    move_prefix = _move_prefix(left_context, turn, fullmove_number)
    move_text = f"{move_prefix}{san}"

    prefix = _space_before(left_context)
    suffix = _space_after(existing_text[end:])
    return f"{prefix}{move_text}{suffix}"


def _move_prefix(left_context: str, turn: chess.Color, fullmove_number: int) -> str:
    trimmed = left_context.rstrip()
    # O numero tem de ser o token inteiro: `Game 12.` termina em "2." mas nao
    # e o numero do lance 2 (N-15).
    if re.search(rf"(?<![\w.]){fullmove_number}\.(?:\.\.)?$", trimmed):
        return ""

    if turn == chess.WHITE:
        return f"{fullmove_number}. "
    return f"{fullmove_number}... "


def _space_before(left_context: str) -> str:
    if not left_context or left_context[-1].isspace():
        return ""
    return " "


def _space_after(right_context: str) -> str:
    if not right_context:
        return ""

    leading = right_context[0]
    if leading.isspace() or leading in ")]},.;:!?":
        return ""
    return " "
