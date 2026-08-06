"""Parser tolerante: tokens -> arvore anotada da partida.

Mudanca central da Fase 2: nenhum token com cara de lance pode mais desaparecer
em silencio, e nenhuma correcao automatica acontece sem virar um `Issue`
visivel com o grau de confianca que a motivou.
"""

from __future__ import annotations

from collections.abc import Iterable

import chess

from .candidates import (
    CONFIDENCE_AUTO_APPLY,
    CONFIDENCE_PROVISIONAL,
    CandidateResolver,
)
from .issues import (
    ACTION_ACCEPT,
    ACTION_IGNORE,
    ACTION_LEARN,
    ACTION_TO_COMMENT,
    AMBIGUOUS_MOVE,
    AUTO_CORRECTED,
    ORPHAN_VARIATION,
    UNBALANCED_PAREN,
    UNCLOSED_BRACE,
    UNRECOGNIZED_MOVE,
    Candidate,
    Issue,
)
from .move_validator import MoveValidator
from .tokenizer import Token, comment_is_unclosed

MOVE_TOKEN_TYPES = {'SAN_MOVE', 'MOVE_CANDIDATE'}


class ASTNode:
    """Base class for parsed objects in the text."""

    def __init__(self, token: Token):
        self.start_index = token.start_index
        self.end_index = token.end_index
        self.raw_text = token.value
        self.is_valid = True


class MoveNode(ASTNode):
    def __init__(self, san: Token, number: Token | None = None):
        super().__init__(san)
        self.number = number.value if number else None
        self.san = san.value

        if number:
            self.start_index = number.start_index

        self.comments_before: list[str] = []
        self.comments_after: list[str] = []
        self.variations: list[list[MoveNode]] = []
        self.variation_anchor_after: list[bool] = []
        self.nags: list[str] = []
        self.corrected_san: str | None = None
        self.parent_fen: str | None = None

        # Fase 2: rastreio de confianca.
        self.confidence: float = 1.0
        self.needs_review: bool = False
        self.candidates: list[Candidate] = []
        self.is_null_move: bool = False

    @property
    def display_san(self) -> str:
        return self.corrected_san or self.san

    def __repr__(self):
        return f"{self.number or ''} {self.display_san}".strip()


class CommentNode(ASTNode):
    pass


class GameAST:
    def __init__(self):
        self.moves: list[MoveNode] = []
        self.result: str | None = None
        self.headers: list[tuple[str, str]] = []
        self.issues: list[Issue] = []


def iter_moves(moves: list[MoveNode]) -> Iterable[MoveNode]:
    for move in moves:
        yield move
        for variation in move.variations:
            yield from iter_moves(variation)


class TolerantParser:
    """Consome tokens, valida contra o tabuleiro e monta a arvore da partida."""

    def __init__(self, locale: str = 'pt', dictionary: dict[str, str] | None = None):
        self.validator = MoveValidator()
        self.resolver = CandidateResolver(locale=locale, dictionary=dictionary)
        self.ast = GameAST()
        self.issues: list[Issue] = []
        self._desynchronized = False
        self._ignored_tokens: set[str] = set()

    # ------------------------------------------------------------------
    # Configuracao
    # ------------------------------------------------------------------

    def set_locale(self, locale: str) -> None:
        self.resolver.set_locale(locale)

    def set_dictionary(self, dictionary: dict[str, str]) -> None:
        self.resolver.dictionary = dict(dictionary)

    def set_ignored_tokens(self, tokens: Iterable[str]) -> None:
        self._ignored_tokens = set(tokens)

    # ------------------------------------------------------------------
    # Alertas
    # ------------------------------------------------------------------

    def _add_issue(
        self,
        severity: str,
        code: str,
        message: str,
        token: Token,
        candidates: list[Candidate] | None = None,
        actions: list[str] | None = None,
    ) -> None:
        self.issues.append(Issue(
            severity=severity,
            code=code,
            message=message,
            raw_start=token.start_index,
            raw_end=token.end_index,
            raw_text=token.value,
            candidates=list(candidates or []),
            actions=list(actions or []),
            is_cascade=self._desynchronized,
        ))

    # ------------------------------------------------------------------
    # Heuristicas de variante
    # ------------------------------------------------------------------

    def _side_to_move(self, fen: str | None) -> chess.Color | None:
        if not fen:
            return None
        return chess.Board(fen).turn

    def _append_comment(self, comments: list[str], value: str, token_type: str):
        if not value:
            return

        if token_type == 'TEXT' and comments:
            comments[-1] = f"{comments[-1]} {value}".strip()
            return

        comments.append(value)

    def _fen_after_move(self, move: MoveNode) -> str | None:
        if not move.parent_fen or not move.is_valid:
            return None

        board = chess.Board(move.parent_fen)
        if move.is_null_move:
            board.push(chess.Move.null())
            return board.fen()

        try:
            board.push_san(move.display_san)
        except ValueError:
            return None
        return board.fen()

    def _collect_variant_preview_tokens(
        self, tokens: list[Token], start_index: int, max_san_tokens: int = 8
    ) -> list[Token]:
        preview_tokens: list[Token] = []
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

    def _variation_has_leading_annotation(self, preview_tokens: list[Token]) -> bool:
        for token in preview_tokens:
            if token.type in {'COMMENT', 'TEXT'}:
                return True
            if token.type in {'MOVE_NUMBER', 'SAN_MOVE', 'RESULT'}:
                return False
        return False

    def _score_variant_candidate(self, fen: str | None, preview_tokens: list[Token]) -> tuple[int, int]:
        if not fen:
            return (-10_000, 0)

        informative_tokens = [token for token in preview_tokens if token.type in {'MOVE_NUMBER', 'SAN_MOVE'}]
        if not informative_tokens:
            return (-10_000, 0)

        board = chess.Board(fen)
        score = 0
        legal_san_count = 0

        for token in preview_tokens:
            if token.type in {'COMMENT', 'TEXT', 'NAG', 'MOVE_CANDIDATE', 'NULL_MOVE'}:
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
        active_list: list[MoveNode],
        tokens: list[Token],
        start_index: int
    ) -> tuple[MoveNode, bool, str | None]:
        preview_tokens = self._collect_variant_preview_tokens(tokens, start_index)
        if not preview_tokens:
            last_move = active_list[-1]
            return (last_move, True, self._fen_after_move(last_move))

        candidates: list[tuple[MoveNode, bool, str | None, int]] = []

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

    def _parse_move_number_value(self, value: str | None) -> tuple[int, bool] | None:
        if not value:
            return None

        digits = "".join(char for char in value if char.isdigit())
        if not digits:
            return None

        return (int(digits), '...' in value)

    def _move_identity(self, move: MoveNode) -> tuple[int, bool] | None:
        parsed_number = self._parse_move_number_value(move.number)
        if parsed_number:
            return parsed_number

        if not move.parent_fen:
            return None

        board = chess.Board(move.parent_fen)
        return (board.fullmove_number, board.turn == chess.BLACK)

    def _is_backward_reference(self, active_list: list[MoveNode], token: Token) -> bool:
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

    def _collect_backward_reference_comment(self, tokens: list[Token], start_index: int) -> tuple[str, int]:
        parts: list[str] = []
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

    # ------------------------------------------------------------------
    # Resolucao de um token de lance
    # ------------------------------------------------------------------

    def _build_move_node(self, token: Token, number_token: Token | None) -> MoveNode:
        node = MoveNode(san=token, number=number_token)
        node.parent_fen = self.validator.board.fen()

        if self.validator.is_legal(token.value):
            self.validator.push_san(token.value)
            node.confidence = 1.0
            self._desynchronized = False
            return node

        candidates = self.resolver.resolve(token.value, self.validator.board)
        node.candidates = candidates
        best = candidates[0] if candidates else None

        if best is not None and best.confidence >= CONFIDENCE_AUTO_APPLY:
            node.corrected_san = best.san
            node.confidence = best.confidence
            self.validator.push_san(best.san)
            self._add_issue(
                'info',
                AUTO_CORRECTED,
                f"'{token.value}' foi lido como '{best.san}' ({best.rationale}).",
                token,
                candidates,
                [ACTION_ACCEPT, ACTION_IGNORE, ACTION_LEARN],
            )
            self._desynchronized = False
            return node

        if best is not None and best.confidence >= CONFIDENCE_PROVISIONAL:
            # Aplicado em carater provisorio: sem isso, toda a linha depois daqui
            # viraria erro em cascata. Fica amarelo e pendente de revisao.
            node.corrected_san = best.san
            node.confidence = best.confidence
            node.needs_review = True
            self.validator.push_san(best.san)
            self._add_issue(
                'warning',
                AMBIGUOUS_MOVE,
                f"'{token.value}' provavelmente e '{best.san}', mas nao ha certeza ({best.rationale}). Confira.",
                token,
                candidates,
                [ACTION_ACCEPT, ACTION_IGNORE, ACTION_LEARN, ACTION_TO_COMMENT],
            )
            self._desynchronized = False
            return node

        node.is_valid = False
        node.confidence = best.confidence if best else 0.0
        if best is not None:
            message = f"'{token.value}' nao foi reconhecido. Hipotese fraca: {best.san} ({best.confidence:.0%})."
        else:
            message = f"'{token.value}' nao e um lance legal nesta posicao e nao foi possivel adivinhar."
        self._add_issue(
            'error',
            UNRECOGNIZED_MOVE,
            message,
            token,
            candidates,
            [ACTION_ACCEPT, ACTION_IGNORE, ACTION_TO_COMMENT] if best else [ACTION_IGNORE, ACTION_TO_COMMENT],
        )
        self._desynchronized = True
        return node

    def _build_null_move_node(self, token: Token, number_token: Token | None) -> MoveNode:
        node = MoveNode(san=token, number=number_token)
        node.parent_fen = self.validator.board.fen()
        node.is_null_move = True
        node.corrected_san = "--"
        self.validator.push_null()
        return node

    def _absorb_as_text(
        self,
        value: str,
        active_list: list[MoveNode],
        pending_comments: list[str],
        comments_since_last_move: list[str],
    ) -> None:
        if not value:
            return
        if not active_list:
            self._append_comment(pending_comments, value, 'TEXT')
        else:
            self._append_comment(active_list[-1].comments_after, value, 'TEXT')
            self._append_comment(comments_since_last_move, value, 'TEXT')

    # ------------------------------------------------------------------
    # Parse
    # ------------------------------------------------------------------

    def parse(self, tokens: list[Token], start_fen: str = chess.STARTING_FEN) -> GameAST:
        self.validator.set_board_fen(start_fen)
        self.ast = GameAST()
        self.issues = []
        self._desynchronized = False

        current_move_number_token: Token | None = None
        pending_comments: list[str] = []
        comments_since_last_move: list[str] = []

        active_list = self.ast.moves
        stack: list[tuple[list[MoveNode], str, list[str], list[str], Token | None]] = []

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

                    var_line: list[MoveNode] = []
                    anchor_move.variations.append(var_line)
                    anchor_move.variation_anchor_after.append(anchor_after_current)

                    # Depois que a primeira variante irma abre, estes comentarios
                    # seguem colados ao lance pai, salvo transferencia explicita.
                    parent_comments_since_last_move: list[str] = []
                    transferred_comments: list[str] = []

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
                        self._desynchronized = False

                    active_list = var_line
                else:
                    self._add_issue(
                        'warning',
                        ORPHAN_VARIATION,
                        "Variante ignorada: nao ha lance anterior para ancora-la.",
                        token,
                    )

            elif token.type == 'VARIANT_CLOSE':
                if stack:
                    active_list, old_fen, pending_comments, comments_since_last_move, current_move_number_token = stack.pop()
                    self.validator.set_board_fen(old_fen)
                    self._desynchronized = False
                else:
                    self._add_issue(
                        'warning',
                        UNBALANCED_PAREN,
                        "Encontrado ')' sem variante aberta.",
                        token,
                    )

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
                if token.type == 'COMMENT' and comment_is_unclosed(token.value):
                    self._add_issue(
                        'warning',
                        UNCLOSED_BRACE,
                        "Comentario sem '}' de fechamento; vale ate o fim da linha.",
                        token,
                    )

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

            elif token.type in MOVE_TOKEN_TYPES:
                # Candidato solto no meio da prosa, ou silenciado pelo usuario:
                # segue como texto, sem virar alerta.
                if token.type == 'MOVE_CANDIDATE' and (
                    not token.in_notation_context or token.value in self._ignored_tokens
                ):
                    self._absorb_as_text(
                        token.value.strip(), active_list, pending_comments, comments_since_last_move
                    )
                    index += 1
                    continue

                node = self._build_move_node(token, current_move_number_token)

                if pending_comments:
                    node.comments_before.extend(pending_comments)
                    pending_comments = []

                active_list.append(node)
                current_move_number_token = None
                comments_since_last_move = []

            elif token.type == 'NULL_MOVE':
                node = self._build_null_move_node(token, current_move_number_token)
                if pending_comments:
                    node.comments_before.extend(pending_comments)
                    pending_comments = []
                active_list.append(node)
                current_move_number_token = None
                comments_since_last_move = []

            index += 1

        self.ast.issues = list(self.issues)
        return self.ast
