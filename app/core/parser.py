from typing import Iterable, List, Optional, Tuple

import chess

from .move_validator import MoveValidator
from .tokenizer import Token


class ASTNode:
    """Base class for parsed objects in the text."""

    def __init__(self, token: Token):
        self.start_index = token.start_index
        self.end_index = token.end_index
        self.raw_text = token.value
        self.is_valid = True


class MoveNode(ASTNode):
    def __init__(self, san: Token, number: Optional[Token] = None):
        super().__init__(san)
        self.number = number.value if number else None
        self.san = san.value

        if number:
            self.start_index = number.start_index

        self.comments_before: List[str] = []
        self.comments_after: List[str] = []
        self.variations: List[List["MoveNode"]] = []
        self.variation_anchor_after: List[bool] = []
        self.nags: List[str] = []
        self.corrected_san: Optional[str] = None
        self.parent_fen: Optional[str] = None

    @property
    def display_san(self) -> str:
        return self.corrected_san or self.san

    def __repr__(self):
        return f"{self.number or ''} {self.display_san}".strip()


class CommentNode(ASTNode):
    pass


class GameAST:
    def __init__(self):
        self.moves: List[MoveNode] = []
        self.result: Optional[str] = None
        self.headers: List[Tuple[str, str]] = []
        self.issues: List[Tuple[int, str, str]] = []


def iter_moves(moves: List[MoveNode]) -> Iterable[MoveNode]:
    for move in moves:
        yield move
        for variation in move.variations:
            yield from iter_moves(variation)


class TolerantParser:
    """
    Consumes tokens and runs them through MoveValidator to build an annotated
    AST representing the game. Includes basic variation recovery and OCR rescue.
    """

    def __init__(self):
        self.validator = MoveValidator()
        self.ast = GameAST()
        self.issues: List[Tuple[int, str, str]] = []

    def _is_legal_in_fen(self, fen: Optional[str], san: str) -> bool:
        if not fen:
            return False

        board = chess.Board(fen)
        try:
            board.parse_san(san)
        except ValueError:
            return False
        return True

    def _side_to_move(self, fen: Optional[str]) -> Optional[chess.Color]:
        if not fen:
            return None
        return chess.Board(fen).turn

    def _append_comment(self, comments: List[str], value: str, token_type: str):
        if not value:
            return

        if token_type == 'TEXT' and comments:
            comments[-1] = f"{comments[-1]} {value}".strip()
            return

        comments.append(value)

    def _fen_after_move(self, move: MoveNode) -> Optional[str]:
        if not move.parent_fen or not move.is_valid:
            return None

        board = chess.Board(move.parent_fen)
        try:
            board.push_san(move.display_san)
        except ValueError:
            return None
        return board.fen()

    def _collect_variant_preview_tokens(self, tokens: List[Token], start_index: int, max_san_tokens: int = 8) -> List[Token]:
        preview_tokens: List[Token] = []
        nested_depth = 0
        san_count = 0

        for token in tokens[start_index:]:
            if token.type == 'VARIANT_OPEN':
                nested_depth += 1
                continue

            if token.type == 'VARIANT_CLOSE':
                if nested_depth == 0:
                    break
                nested_depth -= 1
                continue

            if nested_depth > 0:
                continue

            preview_tokens.append(token)
            if token.type == 'SAN_MOVE':
                san_count += 1
                if san_count >= max_san_tokens:
                    break

        return preview_tokens

    def _variation_has_leading_annotation(self, preview_tokens: List[Token]) -> bool:
        for token in preview_tokens:
            if token.type in {'COMMENT', 'TEXT'}:
                return True
            if token.type in {'MOVE_NUMBER', 'SAN_MOVE', 'RESULT'}:
                return False
        return False

    def _score_variant_candidate(self, fen: Optional[str], preview_tokens: List[Token]) -> Tuple[int, int]:
        if not fen:
            return (-10_000, 0)

        informative_tokens = [token for token in preview_tokens if token.type in {'MOVE_NUMBER', 'SAN_MOVE'}]
        if not informative_tokens:
            return (-10_000, 0)

        board = chess.Board(fen)
        score = 0
        legal_san_count = 0

        for token in preview_tokens:
            if token.type in {'COMMENT', 'TEXT', 'NAG'}:
                continue

            if token.type == 'MOVE_NUMBER':
                parsed = self._parse_move_number_value(token.value)
                if not parsed:
                    continue

                number, is_black = parsed
                expected_is_black = board.turn == chess.BLACK

                if number == board.fullmove_number and is_black == expected_is_black:
                    score += 5
                elif number == board.fullmove_number:
                    score -= 4
                else:
                    score -= min(6, abs(number - board.fullmove_number) + 2)
                continue

            if token.type == 'SAN_MOVE':
                try:
                    board.push_san(token.value)
                    legal_san_count += 1
                    score += 10
                except ValueError:
                    score -= 10
                    break

        return (score, legal_san_count)

    def _choose_variation_anchor(
        self,
        active_list: List[MoveNode],
        tokens: List[Token],
        start_index: int
    ) -> Tuple[MoveNode, bool, Optional[str]]:
        preview_tokens = self._collect_variant_preview_tokens(tokens, start_index)
        if not preview_tokens:
            last_move = active_list[-1]
            return (last_move, True, self._fen_after_move(last_move))

        candidates: List[Tuple[MoveNode, bool, Optional[str], int]] = []

        first_move = active_list[0]
        if first_move.parent_fen:
            candidates.append((first_move, False, first_move.parent_fen, 0))

        for position_index, move in enumerate(active_list, start=1):
            candidates.append((move, True, self._fen_after_move(move), position_index))

        best_candidate = candidates[-1]
        best_key = (-10_000, -1, -1)

        for move, anchor_after, fen, position_index in candidates:
            score, legal_san_count = self._score_variant_candidate(fen, preview_tokens)
            key = (score, legal_san_count, position_index)
            if key > best_key:
                best_key = key
                best_candidate = (move, anchor_after, fen, position_index)

        return (best_candidate[0], best_candidate[1], best_candidate[2])

    def _parse_move_number_value(self, value: Optional[str]) -> Optional[Tuple[int, bool]]:
        if not value:
            return None

        digits = "".join(char for char in value if char.isdigit())
        if not digits:
            return None

        return (int(digits), '...' in value)

    def _move_identity(self, move: MoveNode) -> Optional[Tuple[int, bool]]:
        parsed_number = self._parse_move_number_value(move.number)
        if parsed_number:
            return parsed_number

        if not move.parent_fen:
            return None

        board = chess.Board(move.parent_fen)
        return (board.fullmove_number, board.turn == chess.BLACK)

    def _is_backward_reference(self, active_list: List[MoveNode], token: Token) -> bool:
        token_identity = self._parse_move_number_value(token.value)
        if not token_identity or not active_list:
            return False

        last_move_identity = self._move_identity(active_list[-1])
        if not last_move_identity:
            return False

        token_number, token_is_black = token_identity
        last_number, last_is_black = last_move_identity

        if token_number < last_number:
            return True
        if token_number == last_number and token_is_black == last_is_black:
            return True
        return False

    def _collect_backward_reference_comment(self, tokens: List[Token], start_index: int) -> Tuple[str, int]:
        parts: List[str] = []
        index = start_index

        while index < len(tokens):
            token = tokens[index]
            if token.type in {'VARIANT_CLOSE', 'RESULT'}:
                break

            part = token.value.strip('{}').strip() if token.type == 'COMMENT' else token.value
            if part:
                parts.append(part)
            index += 1

        return (" ".join(parts).strip(), index)

    def parse(self, tokens: List[Token], start_fen: str = chess.STARTING_FEN) -> GameAST:
        self.validator.set_board_fen(start_fen)
        self.ast = GameAST()
        self.issues = []

        current_move_number_token: Optional[Token] = None
        pending_comments: List[str] = []
        comments_since_last_move: List[str] = []

        active_list = self.ast.moves
        stack: List[Tuple[List[MoveNode], str, List[str], List[str], Optional[Token]]] = []

        index = 0
        while index < len(tokens):
            token = tokens[index]

            if token.type == 'VARIANT_OPEN':
                if active_list:
                    preview_tokens = self._collect_variant_preview_tokens(tokens, index + 1)
                    anchor_move, anchor_after_current, anchor_fen = self._choose_variation_anchor(
                        active_list,
                        tokens,
                        index + 1
                    )

                    var_line: List[MoveNode] = []
                    anchor_move.variations.append(var_line)
                    anchor_move.variation_anchor_after.append(anchor_after_current)

                    # After the first sibling variation opens, these comments should stay
                    # attached to the parent move unless explicitly transferred now.
                    parent_comments_since_last_move: List[str] = []
                    transferred_comments: List[str] = []

                    if not (anchor_move is active_list[-1] and anchor_after_current):
                        if not self._variation_has_leading_annotation(preview_tokens):
                            transferred_comments = list(comments_since_last_move)
                        if transferred_comments:
                            del active_list[-1].comments_after[-len(transferred_comments):]

                    stack.append((
                        active_list,
                        self.validator.board.fen(),
                        pending_comments,
                        parent_comments_since_last_move,
                        current_move_number_token
                    ))

                    pending_comments = transferred_comments
                    comments_since_last_move = []
                    current_move_number_token = None

                    if anchor_fen:
                        self.validator.set_board_fen(anchor_fen)

                    active_list = var_line
                else:
                    self.issues.append((token.start_index, "Ignorando variante: nao ha lance anterior.", 'warning'))

            elif token.type == 'VARIANT_CLOSE':
                if stack:
                    active_list, old_fen, pending_comments, comments_since_last_move, current_move_number_token = stack.pop()
                    self.validator.set_board_fen(old_fen)
                else:
                    self.issues.append((token.start_index, "Encontrado ')' sem variante aberta.", 'warning'))

            elif token.type == 'MOVE_NUMBER':
                if self._is_backward_reference(active_list, token):
                    comment_value, index = self._collect_backward_reference_comment(tokens, index)
                    if active_list:
                        self._append_comment(active_list[-1].comments_after, comment_value, 'TEXT')
                    else:
                        self._append_comment(pending_comments, comment_value, 'TEXT')
                    continue

                current_move_number_token = token

            elif token.type in {'COMMENT', 'TEXT'}:
                value = token.value.strip('{}').strip()
                if value:
                    if not active_list:
                        self._append_comment(pending_comments, value, token.type)
                    else:
                        self._append_comment(active_list[-1].comments_after, value, token.type)
                        self._append_comment(comments_since_last_move, value, token.type)

            elif token.type == 'NAG':
                if active_list:
                    active_list[-1].nags.append(token.value)

            elif token.type == 'RESULT':
                self.ast.result = token.value

            elif token.type == 'SAN_MOVE':
                node = MoveNode(san=token, number=current_move_number_token)
                node.parent_fen = self.validator.board.fen()

                if self.validator.is_legal(token.value):
                    self.validator.push_san(token.value)
                else:
                    success, rescued_san = self.validator.attempt_levenshtein_rescue(token.value)
                    if success:
                        node.corrected_san = rescued_san
                        self.issues.append((
                            token.start_index,
                            f"O lance '{token.value}' foi corrigido para '{rescued_san}'.",
                            'info'
                        ))
                        self.validator.push_san(rescued_san)
                    else:
                        node.is_valid = False
                        self.issues.append((
                            token.start_index,
                            f"O lance '{token.value}' eh ilegal ou ambiguo demais.",
                            'error'
                        ))

                if pending_comments:
                    node.comments_before.extend(pending_comments)
                    pending_comments = []

                active_list.append(node)
                current_move_number_token = None
                comments_since_last_move = []

            index += 1

        self.ast.issues = list(self.issues)
        return self.ast
