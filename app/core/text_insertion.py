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
    white_marker = f"{fullmove_number}."
    black_marker = f"{fullmove_number}..."

    if trimmed.endswith(white_marker) or trimmed.endswith(black_marker):
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
