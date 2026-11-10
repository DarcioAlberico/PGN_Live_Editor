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
    as_dictionary,
)
from .issues import (
    ACTION_ACCEPT,
    ACTION_IGNORE,
    ACTION_LEARN,
    ACTION_TO_COMMENT,
    AMBIGUOUS_MOVE,
    AUTO_CORRECTED,
    CITATION_COLLECTED,
    CITED_MOVE,
    INVALID_CHARACTER,
    MOVE_NUMBER_MISMATCH,
    ORPHAN_VARIATION,
    UNBALANCED_PAREN,
    UNCLOSED_BRACE,
    UNRECOGNIZED_MOVE,
    Candidate,
    Issue,
    Severity,
)
from .move_validator import MoveValidator
from .text_hygiene import REPLACEMENT_CHARACTER
from .tokenizer import Token, comment_is_unclosed

MOVE_TOKEN_TYPES = {"SAN_MOVE", "MOVE_CANDIDATE"}

# Palavras que anunciam um lance **citado**, nao jogado: "con la amenaza T1T",
# "threatening N-B6", "com a ideia de Cf3". Sem numero de lance na frente, o
# lance depois delas e parte da frase.
CITATION_WORDS = frozenset(
    {
        "amenaza", "amenazas", "amenazando", "amenazar",
        "ameaça", "ameaças", "ameaçando", "ameaçar", "ameaca", "ameacando",
        "threat", "threats", "threatening", "threatens", "threaten",
        "idea", "ideia", "idéia", "intending", "menace", "menaçant", "drohung",
    }
)  # fmt: skip

# Vizinhos que fazem de `,` e `;` pontuacao entre lances, e nao prosa.
SEPARATOR_NEIGHBOURS = {"SAN_MOVE", "MOVE_CANDIDATE", "MOVE_NUMBER", "NAG", "NULL_MOVE", "VARIANT_CLOSE"}

# Quantos lances seguintes o desempate olha. Dois bastam para `PxP PxP` e para
# `N-B3 ... N-Q2`; mais que isso encarece a analise sem ganho visivel.
LOOKAHEAD_PLIES = 2
# Abaixo de `CONFIDENCE_AUTO_APPLY` de proposito? Nao: a continuacao e evidencia
# forte, mas continua sendo inferencia -- o alerta `info` mostra de onde veio.
CONTINUATION_CONFIDENCE = 0.93
# Janela maior para achar o lance que o OCR engoliu (ver `_missing_move_candidates`).
MISSING_MOVE_LOOKAHEAD = 14


class ASTNode:
    """Base class for parsed objects in the text."""

    def __init__(self, token: Token):
        self.start_index = token.start_index
        self.end_index = token.end_index
        self.raw_text = token.value
        self.is_valid = True


class _TextRun(list):
    """Partes de um comentario de prosa ainda sendo juntadas (ver `_append_comment`)."""

    def text(self) -> str:
        return " ".join(self).strip()


class Variation(list):
    """Uma variante: os lances (a propria lista), de onde ela parte e onde esta no texto.

    Antes eram tres listas paralelas no `MoveNode` (`variations`,
    `variation_anchor_after`, `variation_spans`), casadas pelo indice -- e
    quatro lugares do programa supunham padroes diferentes para o
    `anchor_after` ausente (R-213 do docs/ROADMAP_V3.md). Herdar de `list`
    mantem os usos que so percorrem os lances.

    `anchor_after`: a variante parte da posicao **depois** do lance dono (e
    alternativa ao lance seguinte), e nao da posicao antes dele.
    `span`: do `(` ao `)` inclusive, no texto bruto.
    """

    def __init__(self, moves=(), *, anchor_after: bool = False, span: tuple[int, int] = (0, 0)):
        super().__init__(moves)
        self.anchor_after = anchor_after
        self.span = span

    @property
    def moves(self) -> list[MoveNode]:
        return self


class MoveNode(ASTNode):
    def __init__(self, san: Token, number: Token | None = None):
        super().__init__(san)
        self.number = number.value if number else None
        self.san = san.value

        # O span do SAN e guardado a parte porque `start_index` passa a apontar
        # para o numero de lance. A regiao [start_index, san_start_index) e
        # exatamente o que a renumeracao reescreve.
        self.san_start_index = san.start_index
        self.san_end_index = san.end_index

        if number:
            self.start_index = number.start_index

        self.comments_before: list[str] = []
        self.comments_after: list[str] = []
        # Cada variante carrega de onde parte e o span dos parenteses: e o que
        # permite promover, rebaixar e copiar uma linha sem reescrever o texto.
        self.variations: list[Variation] = []
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

    def __init__(self, locale: str = "pt", dictionary: dict[str, str] | None = None):
        self.validator = MoveValidator()
        self.resolver = CandidateResolver(locale=locale, dictionary=dictionary)
        self.ast = GameAST()
        self.issues: list[Issue] = []
        self._desynchronized = False
        self._ignored_tokens: set[str] = set()
        # Diferenca entre o numero impresso no livro e o do tabuleiro (ver
        # `_check_move_number`), e se o desencontro atual ja foi avisado.
        self._number_offset: int | None = None
        self._number_warned = False

    # ------------------------------------------------------------------
    # Configuracao
    # ------------------------------------------------------------------

    def set_locale(self, locale: str) -> None:
        self.resolver.set_locale(locale)

    def set_dictionary(self, dictionary) -> None:
        self.resolver.dictionary = as_dictionary(dictionary)

    def set_ignored_tokens(self, tokens: Iterable[str]) -> None:
        self._ignored_tokens = set(tokens)

    # ------------------------------------------------------------------
    # Alertas
    # ------------------------------------------------------------------

    def _add_issue(
        self,
        severity: Severity,
        code: str,
        message: str,
        token: Token,
        candidates: list[Candidate] | None = None,
        actions: list[str] | None = None,
    ) -> None:
        self.issues.append(
            Issue(
                severity=severity,
                code=code,
                message=message,
                raw_start=token.start_index,
                raw_end=token.end_index,
                raw_text=token.value,
                candidates=list(candidates or []),
                actions=list(actions or []),
                is_cascade=self._desynchronized,
            )
        )

    # ------------------------------------------------------------------
    # Heuristicas de variante
    # ------------------------------------------------------------------

    def _side_to_move(self, fen: str | None) -> chess.Color | None:
        if not fen:
            return None
        return chess.Board(fen).turn

    def _append_comment(self, comments: list[str], value: str, token_type: str, floor: int = 0):
        """Acrescenta um comentario; prosa solta se junta ao anterior **depois de `floor`**.

        `floor` e a fronteira do que veio depois do ultimo lance ou da ultima
        variante: a prosa nunca se cola num comentario de antes dela. Antes
        havia uma segunda lista (`comments_since_last_move`) que divergia desta
        quando a prosa se colava, e o `del [-n:]` da transferencia para a
        variante apagava o comentario errado (N-03).

        A juncao acumula partes (`_TextRun`) e so vira texto em
        `_finish_comments`: juntar string a string era quadratico numa prosa
        longa (N-16).
        """
        if not value:
            return

        if token_type == "TEXT" and len(comments) > floor:
            last = comments[-1]
            if isinstance(last, _TextRun):
                last.append(value)
            else:
                comments[-1] = _TextRun([last, value])  # type: ignore[call-overload]
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
            if token.type == "VARIANT_OPEN":
                nested_depth += 1
                continue

            if token.type == "VARIANT_CLOSE":
                if nested_depth == 0:
                    break
                nested_depth -= 1
                continue

            if nested_depth > 0:
                continue

            preview_tokens.append(token)
            if token.type in MOVE_TOKEN_TYPES:
                san_count += 1
                if san_count >= max_san_tokens:
                    break

        return preview_tokens

    def _variation_has_leading_annotation(self, preview_tokens: list[Token]) -> bool:
        for token in preview_tokens:
            if token.type in {"COMMENT", "TEXT"}:
                return True
            if token.type in {"MOVE_NUMBER", "SAN_MOVE", "RESULT"}:
                return False
        return False

    def _preview_move_san(self, token: Token, board: chess.Board) -> str | None:
        """O lance que este token representa nesta posicao, se houver um so claro.

        Num livro em portugues **nenhum** lance chega aqui como `SAN_MOVE`:
        `Cf3` e `Dxc2` sao candidatos. Sem resolve-los, o tabuleiro nao avancava
        durante a pontuacao e a ancora da variante era escolhida as cegas -- o
        numero de lance seguinte era comparado com uma posicao parada.
        """
        if token.type == "SAN_MOVE":
            return token.value

        candidates = self.resolver.resolve(token.value, board, fuzzy=False)
        if candidates and candidates[0].confidence >= CONFIDENCE_AUTO_APPLY:
            return candidates[0].san
        return None

    def _score_variant_candidate(self, fen: str | None, preview_tokens: list[Token]) -> tuple[int, int]:
        if not fen:
            return (-10_000, 0)

        informative_tokens = [
            token for token in preview_tokens if token.type == "MOVE_NUMBER" or token.type in MOVE_TOKEN_TYPES
        ]
        if not informative_tokens:
            return (-10_000, 0)

        board = chess.Board(fen)
        score = 0
        legal_san_count = 0

        for token in preview_tokens:
            if token.type in {"COMMENT", "TEXT", "NAG", "NULL_MOVE"}:
                continue

            if token.type == "MOVE_NUMBER":
                parsed = self._parse_move_number_value(token.value)
                if not parsed:
                    continue

                number, is_black = parsed
                expected_is_black = board.turn == chess.BLACK

                if number == board.fullmove_number and is_black == expected_is_black:
                    score += 30
                elif number == board.fullmove_number:
                    score -= 50
                else:
                    score -= 50 + min(20, abs(number - board.fullmove_number) * 5)
                continue

            if token.type in MOVE_TOKEN_TYPES:
                san = self._preview_move_san(token, board)
                if san is None:
                    # Candidato que nao resolve nao diz nada sobre a ancora --
                    # pode ser prosa. Nao pontua nem para bem nem para mal.
                    if token.type == "SAN_MOVE":
                        score -= 10
                        break
                    continue

                try:
                    board.push_san(san)
                    legal_san_count += 1
                    score += 10
                except ValueError:
                    score -= 10
                    break

        return (score, legal_san_count)

    def _choose_variation_anchor(
        self, active_list: list[MoveNode], tokens: list[Token], start_index: int
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

        return (int(digits), "..." in value)

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

    def _collect_backward_reference_comment(
        self, tokens: list[Token], start_index: int, active_list: list[MoveNode]
    ) -> tuple[str, int]:
        """O trecho citado ("Compare 1. d4 d5 ...") como comentario, e onde ele termina (R-204).

        A citacao termina no primeiro destes: `)`, resultado, ou um numero de
        lance que **retoma a partida** -- vem depois do ultimo lance jogado e o
        lance seguinte a ele e legal na posicao atual. Antes ela so parava no
        `)` ou no resultado e engolia os lances de verdade que viessem depois
        (N-02). Chaves no trecho viram parenteses: dentro do comentario elas o
        quebrariam. O trecho sai num alerta `info` para o usuario conferir.
        """
        parts: list[str] = []
        index = start_index

        while index < len(tokens):
            token = tokens[index]
            if token.type in {"VARIANT_CLOSE", "RESULT"}:
                break
            if index > start_index and token.type == "MOVE_NUMBER" and self._resumes_game(active_list, tokens, index):
                break

            part = token.value.strip("{}").strip() if token.type == "COMMENT" else token.value
            if part:
                parts.append(part.replace("{", "(").replace("}", ")"))
            index += 1

        comment = " ".join(parts).strip()
        if comment and index > start_index:
            first, last = tokens[start_index], tokens[index - 1]
            self._add_issue(
                "info",
                CITATION_COLLECTED,
                "Trecho com lances de outra linha guardado como comentário; confira se não é a partida.",
                Token(
                    type="TEXT",
                    value=comment,
                    start_index=first.start_index,
                    end_index=last.end_index,
                    normalized_start_index=first.normalized_start_index,
                    normalized_end_index=last.normalized_end_index,
                ),
            )
        return (comment, index)

    def _resumes_game(self, active_list: list[MoveNode], tokens: list[Token], index: int) -> bool:
        """O numero em `tokens[index]` e a partida voltando depois de uma citacao?

        So se for o **proximo meio-lance** da partida (`2...` depois de `2.`;
        `3.` depois de `2...`) e o lance seguinte a ele for legal agora. Um
        `2. c4` da linha citada depois de `2...Nc6` vem depois no texto, mas e
        anterior na partida.
        """
        if not active_list:
            return False
        identity = self._parse_move_number_value(tokens[index].value)
        last = self._move_identity(active_list[-1])
        if not identity or not last:
            return False
        token_ply = 2 * (identity[0] - 1) + int(identity[1])
        last_ply = 2 * (last[0] - 1) + int(last[1])
        if token_ply != last_ply + 1:
            return False
        following = next((t for t in tokens[index + 1 :] if t.type in MOVE_TOKEN_TYPES), None)
        if following is None:
            return False
        return self.validator.is_legal(following.value)

    # ------------------------------------------------------------------
    # Resolucao de um token de lance
    # ------------------------------------------------------------------

    def _reading_is_disputed(self, token: Token) -> bool:
        """O token e legal como esta, mas a letra muda de sentido entre idiomas.

        `Rc7` num final de torre e legal como Torre **e** como Rei. Ser legal
        nao basta para decidir: se o documento provou estar em portugues, quem
        decide e o resolvedor, com o desconto da leitura inglesa.
        """
        return self.resolver.notation_confirmed and self.resolver.collides_with_english(token.value)

    def _build_move_node(
        self,
        token: Token,
        number_token: Token | None,
        contextual_san: str | None = None,
        lookahead: tuple[list[Token], int] | None = None,
    ) -> MoveNode:
        node = MoveNode(san=token, number=number_token)
        node.parent_fen = self.validator.board.fen()

        if contextual_san is not None:
            node.corrected_san = contextual_san
            node.confidence = 0.93
            self.validator.push_san(contextual_san)
            candidate = Candidate(
                contextual_san,
                node.confidence,
                "contexto",
                "único lance da mesma peça que permite os dois lances seguintes",
            )
            node.candidates = [candidate]
            self._add_issue(
                "info",
                AUTO_CORRECTED,
                f"'{token.value}' foi lido como '{contextual_san}' para manter a continuação legal da partida.",
                token,
                [candidate],
                [ACTION_ACCEPT, ACTION_IGNORE, ACTION_LEARN],
            )
            self._desynchronized = False
            return node

        if self.validator.is_legal(token.value) and not self._reading_is_disputed(token):
            self.validator.push_san(token.value)
            node.confidence = 1.0
            self._desynchronized = False
            return node

        candidates = self.resolver.resolve(token.value, self.validator.board)
        if len(candidates) > 1 and lookahead is not None and candidates[0].confidence < CONFIDENCE_AUTO_APPLY:
            candidates = self._break_tie_by_continuation(candidates, self._following_move_tokens(*lookahead))
        node.candidates = candidates
        best = candidates[0] if candidates else None

        if best is not None and best.confidence >= CONFIDENCE_AUTO_APPLY:
            node.corrected_san = best.san
            node.confidence = best.confidence
            self.validator.push_san(best.san)
            self._add_issue(
                "info",
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
                "warning",
                AMBIGUOUS_MOVE,
                f"'{token.value}' provavelmente e '{best.san}', mas não ha certeza ({best.rationale}). Confira.",
                token,
                candidates,
                [ACTION_ACCEPT, ACTION_IGNORE, ACTION_LEARN, ACTION_TO_COMMENT],
            )
            self._desynchronized = False
            return node

        node.is_valid = False
        node.confidence = best.confidence if best else 0.0
        if best is not None:
            message = f"'{token.value}' não foi reconhecido. Hipotese fraca: {best.san} ({best.confidence:.0%})."
        else:
            message = f"'{token.value}' não e um lance legal nesta posição e não foi possível adivinhar."
        self._add_issue(
            "error",
            UNRECOGNIZED_MOVE,
            message,
            token,
            candidates,
            [ACTION_ACCEPT, ACTION_IGNORE, ACTION_TO_COMMENT] if best else [ACTION_IGNORE, ACTION_TO_COMMENT],
        )
        self._desynchronized = True
        return node

    # ------------------------------------------------------------------
    # Desempate pela continuacao
    # ------------------------------------------------------------------

    def _check_move_number(self, number_token: Token, tokens: list[Token] | None = None, index: int = 0) -> None:
        """O numero impresso bate com a vez de jogar?

        E o unico sinal de um lance que sumiu **antes** de virar token: o OCR
        leu `P-K'` ou `B-IH`, a palavra foi para a prosa, e dali em diante as
        brancas jogam os lances das pretas. Um aviso por desencontro -- os
        lances seguintes herdam o mesmo problema e nao precisam repeti-lo.

        O numero e comparado **relativo** ao primeiro lance numerado: um FEN de
        diagrama costuma trazer `1` como numero de lance enquanto o livro esta
        no lance 24.
        """
        if self._desynchronized:
            return
        identity = self._parse_move_number_value(number_token.value)
        if identity is None:
            return
        number, is_black = identity
        board = self.validator.board
        board_is_black = board.turn == chess.BLACK
        if self._number_offset is None:
            if is_black == board_is_black:
                self._number_offset = number - board.fullmove_number
            return
        expected = board.fullmove_number + self._number_offset
        if number == expected and is_black == board_is_black:
            self._number_warned = False
            return
        if self._number_warned:
            return
        self._number_warned = True
        side = "pretas" if board_is_black else "brancas"
        printed = f"{number}..." if is_black else f"{number}."
        message = (
            f"O livro numera '{printed}', mas pela posição é a vez das {side} no lance {expected}: "
            "provavelmente falta (ou sobra) um lance antes daqui."
        )
        candidates: list[Candidate] = []
        printed_ply = 2 * number + int(is_black)
        board_ply = 2 * expected + int(board_is_black)
        if tokens is not None and printed_ply - board_ply == 1:
            candidates = self._missing_move_candidates(tokens, index, number_token.value.strip())
            if candidates:
                message += f" O lance que falta parece ser '{candidates[0].san}'."
        self._add_issue(
            "warning",
            MOVE_NUMBER_MISMATCH,
            message,
            number_token,
            candidates,
            [ACTION_ACCEPT, ACTION_IGNORE] if candidates else [ACTION_IGNORE],
        )

    def _missing_move_candidates(self, tokens: list[Token], index: int, printed_number: str) -> list[Candidate]:
        """O unico lance que, jogado antes daqui, torna legais os lances seguintes do livro.

        Nunca e aplicado sozinho: vai para o alerta como candidato, e "Aceitar"
        insere o lance no texto antes do numero impresso.
        """
        # Dois lances bastam para desempatar uma leitura, mas nao para achar um
        # lance que sumiu: quase tudo cabe no buraco. Oito lances ja exigem que o
        # lance perdido seja o que o livro usa depois (`P-K4` antes de `P-K5`).
        following = [tokens[index], *self._following_move_tokens(tokens, index, limit=MISSING_MOVE_LOOKAHEAD)]
        board = self.validator.board.copy()
        scores: dict[str, int] = {}
        for move in list(board.legal_moves):
            san = board.san(move)
            board.push(move)
            try:
                scores[san] = self._continuation_depth(board, following)
            finally:
                board.pop()
        if not scores:
            return []
        best = max(scores.values())
        winners = [san for san, score in scores.items() if score == best]
        if best < min(4, len(following)) or len(winners) != 1:
            return []
        san = winners[0]
        return [
            Candidate(
                san,
                0.75,
                "lance-que-falta",
                f"único lance que torna legais os {best} lance(s) seguinte(s) do livro",
                replacement=f"{san} {printed_number}",
            )
        ]

    @staticmethod
    def _is_cited_move(tokens: list[Token], index: int) -> bool:
        """O lance vem logo depois de "amenaza", "threatening", "ideia de"...?"""
        previous = index - 1
        words: list[str] = []
        while previous >= 0 and len(words) < 3:
            token = tokens[previous]
            if token.type != "TEXT":
                break
            words.append(token.value.strip(".,;:()").lower())
            previous -= 1
        if not words:
            return False
        if words[0] in CITATION_WORDS:
            return True
        # "ameaça de Cf3", "idea de T1T", "threat of N-B6"
        return words[0] in {"de", "of", "do", "da"} and len(words) > 1 and words[1] in CITATION_WORDS

    def _is_move_separator(self, tokens: list[Token], index: int) -> bool:
        """`,` ou `;` solto entre dois lances (estilo dos livros espanhois)."""
        if tokens[index].value not in {",", ";"}:
            return False
        previous = tokens[index - 1].type if index > 0 else None
        # Colada num lance, a virgula e do lance mesmo quando a prosa vem depois
        # (`8. AxC, ganando una pieza`): sem isso o comentario nascia `{, ganando...}`.
        return previous in SEPARATOR_NEIGHBOURS

    @staticmethod
    def _following_move_tokens(tokens: list[Token], index: int, limit: int = LOOKAHEAD_PLIES) -> list[Token]:
        """Os proximos lances da **mesma linha**, pulando variantes e comentarios."""
        found: list[Token] = []
        depth = 0
        for following in tokens[index + 1 :]:
            if following.type == "VARIANT_OPEN":
                depth += 1
                continue
            if following.type == "VARIANT_CLOSE":
                if depth == 0:
                    break
                depth -= 1
                continue
            if depth or following.type in {"MOVE_NUMBER", "NAG", "COMMENT"}:
                continue
            if following.type == "TEXT" and following.value in {",", ";"}:
                continue
            if following.type == "SAN_MOVE" or (following.type == "MOVE_CANDIDATE" and following.in_notation_context):
                found.append(following)
                if len(found) == limit:
                    break
                continue
            break
        return found

    def _legal_readings(self, value: str, board: chess.Board) -> list[str]:
        try:
            return [board.san(board.parse_san(value))]
        except (ValueError, AssertionError):
            pass
        return [
            candidate.san
            for candidate in self.resolver.resolve(value, board, fuzzy=False)
            if candidate.confidence >= CONFIDENCE_PROVISIONAL
        ]

    def _continuation_depth(self, board: chess.Board, following: list[Token]) -> int:
        """Quantos dos lances seguintes continuam legais depois deste."""
        if not following:
            return 0
        best = 0
        for san in self._legal_readings(following[0].value, board)[:3]:
            board.push_san(san)
            try:
                best = max(best, 1 + self._continuation_depth(board, following[1:]))
            finally:
                board.pop()
            if best == len(following):
                break
        return best

    def _break_tie_by_continuation(self, candidates: list[Candidate], following: list[Token]) -> list[Candidate]:
        """Entre leituras igualmente provaveis, fica a unica que nao quebra a partida.

        `N-B3` com os dois cavalos podendo ir, ou `PxP` com dois peoes capturando:
        a notacao descritiva e ambigua por natureza e o livro conta com o leitor
        para desempatar pelo que vem depois. O programa faz o mesmo -- e so
        decide quando **uma unica** leitura deixa os lances seguintes legais.
        """
        if not following:
            return candidates
        contenders = [
            candidate
            for candidate in candidates
            if candidate.confidence >= CONFIDENCE_PROVISIONAL and candidate.source != "levenshtein"
        ]
        if len(contenders) < 2:
            return candidates

        board = self.validator.board.copy()
        scores: dict[str, int] = {}
        for candidate in contenders:
            board.push_san(candidate.san)
            try:
                scores[candidate.san] = self._continuation_depth(board, following)
            finally:
                board.pop()

        best = max(scores.values())
        winners = [san for san, score in scores.items() if score == best]
        if best == 0 or len(winners) != 1:
            return candidates

        winner = next(candidate for candidate in contenders if candidate.san == winners[0])
        chosen = Candidate(
            winner.san,
            CONTINUATION_CONFIDENCE,
            f"{winner.source}+continuação",
            f"{winner.rationale}; única leitura que mantém legal(is) o(s) {best} lance(s) seguinte(s)",
        )
        return [chosen] + [candidate for candidate in candidates if candidate.san != winner.san]

    def _contextual_san_alternative(self, token: Token, tokens: list[Token], index: int) -> str | None:
        """Repair one legal-but-inconsistent SAN typo using two-ply evidence.

        Old book transcriptions occasionally contain a legal capture on the
        wrong square, which makes the following recapture impossible. Only a
        unique alternative by the same piece and with the same capture status
        is accepted, and only when it makes the next two SAN moves legal.
        """
        if token.type != "SAN_MOVE" or not self.validator.is_legal(token.value):
            return None
        if self._reading_is_disputed(token):
            return None

        continuation: list[str] = []
        for following in tokens[index + 1 :]:
            if following.type in {"VARIANT_OPEN", "VARIANT_CLOSE", "RESULT", "COMMENT", "TEXT"}:
                break
            if following.type in {"MOVE_NUMBER", "NAG"}:
                continue
            if following.type == "SAN_MOVE" or (following.type == "MOVE_CANDIDATE" and following.in_notation_context):
                continuation.append(following.value)
                if len(continuation) == 2:
                    break
        if len(continuation) < 2:
            return None

        board = self.validator.board.copy()
        original_move = board.parse_san(token.value)
        moving_piece = board.piece_at(original_move.from_square)
        if moving_piece is None:
            return None

        def legal_continuation(first_move: chess.Move) -> int:
            board.push(first_move)
            matched = 0
            try:
                for san in continuation:
                    try:
                        move = board.parse_san(san)
                    except ValueError:
                        break
                    if move not in board.legal_moves:
                        break
                    board.push(move)
                    matched += 1
            finally:
                for _ in range(matched + 1):
                    board.pop()
            return matched

        original_score = legal_continuation(original_move)
        if original_score == len(continuation):
            return None

        alternatives: list[str] = []
        was_capture = board.is_capture(original_move)
        for move in board.legal_moves:
            if move == original_move or board.is_capture(move) != was_capture:
                continue
            piece = board.piece_at(move.from_square)
            if piece is None or piece.piece_type != moving_piece.piece_type:
                continue
            if legal_continuation(move) == len(continuation):
                alternatives.append(board.san(move))
        return alternatives[0] if len(alternatives) == 1 else None

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
        comment_floor: int,
    ) -> None:
        if not value:
            return
        if not active_list:
            self._append_comment(pending_comments, value, "TEXT")
        else:
            self._append_comment(active_list[-1].comments_after, value, "TEXT", comment_floor)

    # ------------------------------------------------------------------
    # Parse
    # ------------------------------------------------------------------

    def _detect_notation_language(self, tokens: list[Token]) -> bool:
        """O documento prova estar escrito na notacao do idioma configurado?

        Basta um lance comecando por uma letra que **nao existe** no SAN ingles
        (`Cf3`, `Dc7`, `Tf1`, `Ld3`). Achando um, `Rc7` passa a ser lido como
        Rei; nao achando nenhum -- um PGN ingles importado, por exemplo -- `Rc7`
        continua sendo a torre, como o padrao PGN manda.
        """
        letters = self.resolver.proof_letters()
        if not letters:
            return False

        for token in tokens:
            if token.type != "MOVE_CANDIDATE" or not token.in_notation_context:
                continue
            if token.value[:1] in letters and len(token.value) > 2:
                return True
        return False

    def parse(self, tokens: list[Token], start_fen: str = chess.STARTING_FEN) -> GameAST:
        self.validator.set_board_fen(start_fen)
        self.ast = GameAST()
        self.issues = []
        self._desynchronized = False
        self._number_offset = None
        self._number_warned = False
        self.resolver.set_notation_confirmed(self._detect_notation_language(tokens))

        current_move_number_token: Token | None = None
        pending_comments: list[str] = []
        # Onde comecam, em `active_list[-1].comments_after`, os comentarios
        # vindos depois do ultimo lance ou da ultima variante fechada.
        comment_floor = 0

        active_list = self.ast.moves
        stack: list[tuple[list[MoveNode], str, list[str], Token | None, tuple[MoveNode, int]]] = []

        index = 0
        while index < len(tokens):
            token = tokens[index]

            if token.type == "VARIANT_OPEN":
                if active_list:
                    preview_tokens = self._collect_variant_preview_tokens(tokens, index + 1)
                    anchor_move, anchor_after_current, anchor_fen = self._choose_variation_anchor(
                        active_list, tokens, index + 1
                    )

                    var_line = Variation(anchor_after=anchor_after_current, span=(token.start_index, token.end_index))
                    anchor_move.variations.append(var_line)
                    open_variation = (anchor_move, len(anchor_move.variations) - 1)

                    # Depois que a primeira variante irma abre, estes comentarios
                    # seguem colados ao lance pai, salvo transferencia explicita.
                    transferred_comments: list[str] = []

                    if not (anchor_move is active_list[-1] and anchor_after_current):
                        if not self._variation_has_leading_annotation(preview_tokens):
                            transferred_comments = list(active_list[-1].comments_after[comment_floor:])
                        if transferred_comments:
                            del active_list[-1].comments_after[comment_floor:]

                    stack.append(
                        (
                            active_list,
                            self.validator.board.fen(),
                            pending_comments,
                            current_move_number_token,
                            open_variation,
                        )
                    )

                    pending_comments = transferred_comments
                    comment_floor = 0
                    current_move_number_token = None

                    if anchor_fen:
                        self.validator.set_board_fen(anchor_fen)
                        self._desynchronized = False

                    active_list = var_line
                else:
                    self._add_issue(
                        "warning",
                        ORPHAN_VARIATION,
                        "Variante ignorada: não ha lance anterior para ancora-la.",
                        token,
                    )

            elif token.type == "VARIANT_CLOSE":
                if stack:
                    (
                        active_list,
                        old_fen,
                        pending_comments,
                        current_move_number_token,
                        open_variation,
                    ) = stack.pop()
                    # Depois do `)`, prosa nova nao se cola no que veio antes dele.
                    comment_floor = len(active_list[-1].comments_after) if active_list else 0
                    owner, variation_index = open_variation
                    variation = owner.variations[variation_index]
                    variation.span = (variation.span[0], token.end_index)
                    self.validator.set_board_fen(old_fen)
                    self._desynchronized = False
                else:
                    self._add_issue(
                        "warning",
                        UNBALANCED_PAREN,
                        "Encontrado ')' sem variante aberta.",
                        token,
                    )

            elif token.type == "MOVE_NUMBER":
                if self._is_backward_reference(active_list, token):
                    comment_value, index = self._collect_backward_reference_comment(tokens, index, active_list)
                    if active_list:
                        self._append_comment(active_list[-1].comments_after, comment_value, "TEXT", comment_floor)
                    else:
                        self._append_comment(pending_comments, comment_value, "TEXT")
                    continue

                current_move_number_token = token

            elif token.type == "TEXT" and self._is_move_separator(tokens, index):
                # `3. P5R, C4D; 4. C3A`: a pontuacao do livro entre lances nao e
                # comentario. Antes ela ia para o PGN como `{,}` e `{;}`.
                pass

            elif token.type in {"COMMENT", "TEXT"}:
                if token.type == "COMMENT" and comment_is_unclosed(token.value):
                    self._add_issue(
                        "warning",
                        UNCLOSED_BRACE,
                        "Comentário sem '}' de fechamento; vale até o fim da linha.",
                        token,
                    )

                value = token.value.strip("{}").strip()
                if value:
                    if not active_list:
                        self._append_comment(pending_comments, value, token.type)
                    else:
                        self._append_comment(active_list[-1].comments_after, value, token.type, comment_floor)

            elif token.type == "NAG":
                if active_list:
                    active_list[-1].nags.append(token.value)

            elif token.type == "RESULT":
                self.ast.result = token.value

            elif token.type in MOVE_TOKEN_TYPES:
                # Candidato solto no meio da prosa, ou silenciado pelo usuario:
                # segue como texto, sem virar alerta.
                if token.type == "MOVE_CANDIDATE" and (
                    not token.in_notation_context or token.value in self._ignored_tokens
                ):
                    self._absorb_as_text(token.value.strip(), active_list, pending_comments, comment_floor)
                    index += 1
                    continue

                if current_move_number_token is None and self._is_cited_move(tokens, index):
                    self._absorb_as_text(token.value.strip(), active_list, pending_comments, comment_floor)
                    self._add_issue(
                        "info",
                        CITED_MOVE,
                        f"'{token.value}' foi mantido no comentário: a frase o cita (ameaça, ideia), "
                        "não é um lance jogado. Se for, ponha o número do lance na frente.",
                        token,
                    )
                    index += 1
                    continue

                if current_move_number_token is not None:
                    self._check_move_number(current_move_number_token, tokens, index)

                contextual_san = self._contextual_san_alternative(token, tokens, index)
                node = self._build_move_node(
                    token,
                    current_move_number_token,
                    contextual_san,
                    lookahead=(tokens, index),
                )

                if pending_comments:
                    node.comments_before.extend(pending_comments)
                    pending_comments = []

                active_list.append(node)
                current_move_number_token = None
                comment_floor = 0

            elif token.type == "NULL_MOVE":
                node = self._build_null_move_node(token, current_move_number_token)
                if pending_comments:
                    node.comments_before.extend(pending_comments)
                    pending_comments = []
                active_list.append(node)
                current_move_number_token = None
                comment_floor = 0

            index += 1

        self._finish_comments(self.ast.moves)
        self._flag_invalid_characters(tokens)
        self.ast.issues = list(self.issues)
        return self.ast

    def _finish_comments(self, moves: list[MoveNode]) -> None:
        """Junta, uma vez so, a prosa acumulada em `_TextRun` (ver `_append_comment`)."""
        for move in iter_moves(moves):
            for comments in (move.comments_before, move.comments_after):
                for position, comment in enumerate(comments):
                    if isinstance(comment, _TextRun):
                        comments[position] = comment.text()

    def _flag_invalid_characters(self, tokens: list[Token]) -> None:
        """U+FFFD no texto e um caractere que nao era texto ao entrar (R-107)."""
        for token in tokens:
            if REPLACEMENT_CHARACTER in token.value:
                self._add_issue(
                    "warning",
                    INVALID_CHARACTER,
                    "Caractere inválido trocado por '�' ao entrar no editor; confira o original.",
                    token,
                )
