from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import chess

from .config import IDX_TO_CLASS, PIECE_TO_IDX

# --------------------------------------------------------------------------------------
# Classificacao de legalidade
#
# O criterio nao e uma lista arbitraria de status, e sim: a violacao depende do lado a
# jogar ou nao?
#
# Um diagrama de livro nao carrega o lado a jogar. O pipeline preenche "w" por padrao
# (ver _normalize_fen), entao qualquer violacao que dependa de quem joga pode ser apenas
# um artefato desse preenchimento -- e nao um erro de reconhecimento.
#
#   FATAL  = viola uma regra independente do lado a jogar (material, contagem de reis,
#            peoes em fila impossivel). E erro de reconhecimento de verdade.
#   XEQUE  = depende de quem joga. Frequentemente indica apenas que o lado a jogar e o
#            oposto do assumido; S-17 usa exatamente esse sinal para inferi-lo.
#
# Statuses ignorados (BAD_CASTLING_RIGHTS, INVALID_EP_SQUARE) sao artefatos do sufixo
# "w - - 0 1" adicionado automaticamente e nao dizem nada sobre a posicao reconhecida.
# --------------------------------------------------------------------------------------

FATAL_STATUSES: tuple[chess.Status, ...] = (
    chess.STATUS_NO_WHITE_KING,
    chess.STATUS_NO_BLACK_KING,
    chess.STATUS_TOO_MANY_KINGS,
    chess.STATUS_TOO_MANY_WHITE_PAWNS,
    chess.STATUS_TOO_MANY_BLACK_PAWNS,
    chess.STATUS_TOO_MANY_WHITE_PIECES,
    chess.STATUS_TOO_MANY_BLACK_PIECES,
    chess.STATUS_PAWNS_ON_BACKRANK,
    chess.STATUS_EMPTY,
)

TURN_DEPENDENT_STATUSES: tuple[chess.Status, ...] = (
    chess.STATUS_OPPOSITE_CHECK,
    chess.STATUS_IMPOSSIBLE_CHECK,
    chess.STATUS_TOO_MANY_CHECKERS,
)

IGNORED_STATUSES: tuple[chess.Status, ...] = (
    chess.STATUS_BAD_CASTLING_RIGHTS,
    chess.STATUS_INVALID_EP_SQUARE,
)

_STATUS_DESCRIPTIONS: dict[chess.Status, str] = {
    chess.STATUS_NO_WHITE_KING: "falta o rei branco",
    chess.STATUS_NO_BLACK_KING: "falta o rei preto",
    chess.STATUS_TOO_MANY_KINGS: "mais de um rei da mesma cor",
    chess.STATUS_TOO_MANY_WHITE_PAWNS: "peões brancos demais",
    chess.STATUS_TOO_MANY_BLACK_PAWNS: "peões pretos demais",
    chess.STATUS_TOO_MANY_WHITE_PIECES: "peças brancas demais",
    chess.STATUS_TOO_MANY_BLACK_PIECES: "peças pretas demais",
    chess.STATUS_PAWNS_ON_BACKRANK: "peão na primeira ou na oitava fila",
    chess.STATUS_EMPTY: "tabuleiro vazio",
    chess.STATUS_OPPOSITE_CHECK: "o lado que não joga está em xeque",
    chess.STATUS_IMPOSSIBLE_CHECK: "configuração de xeque impossível",
    chess.STATUS_TOO_MANY_CHECKERS: "peças demais dando xeque",
    chess.STATUS_BAD_CASTLING_RIGHTS: "direitos de roque incompatíveis com a posição",
    chess.STATUS_INVALID_EP_SQUARE: "casa de en passant inválida",
}


@dataclass(frozen=True)
class PositionCheck:
    """Resultado da checagem de legalidade de uma posição reconhecida."""

    fen: str
    status: chess.Status
    is_legal: bool
    """A posição é legal exatamente como foi informada (incluindo o lado a jogar)."""

    is_fatal: bool
    """Viola uma regra independente do lado a jogar: erro real de reconhecimento."""

    legal_turn: chess.Color | None
    """Cor para a qual a posição é legal, se existir alguma.

    Quando `is_legal` é False mas `legal_turn` não é None, a posição está correta e
    apenas o lado a jogar foi assumido errado -- é o sinal que S-17 usa para inferi-lo.
    """

    problems: tuple[str, ...]
    """Descrições em pt-BR dos problemas encontrados, prontas para exibição."""

    @property
    def needs_side_to_move_flip(self) -> bool:
        """A posição só é ilegal por causa do lado a jogar assumido."""
        return not self.is_legal and not self.is_fatal and self.legal_turn is not None


def describe_status(status: chess.Status) -> tuple[str, ...]:
    """Traduz um `chess.Status` em descrições legíveis, ignorando ruído do preenchimento."""
    problems = [text for flag, text in _STATUS_DESCRIPTIONS.items() if status & flag and flag not in IGNORED_STATUSES]
    return tuple(problems)


def _status_for_turn(board: chess.Board, turn: chess.Color) -> chess.Status:
    probe = board.copy(stack=False)
    probe.turn = turn
    return probe.status()


def check_position(fen: str) -> PositionCheck:
    """Avalia a legalidade de uma posição.

    Diferente de `is_valid_fen`, que só verifica sintaxe, esta função aplica as regras
    do xadrez: contagem de reis e de peças, peões em filas impossíveis e configurações
    de xeque.
    """
    normalized = _normalize_fen(fen)
    try:
        board = chess.Board(normalized)
    except (ValueError, AttributeError, TypeError):
        return PositionCheck(
            fen=normalized,
            status=chess.STATUS_EMPTY,
            is_legal=False,
            is_fatal=True,
            legal_turn=None,
            problems=("FEN não pôde ser interpretada",),
        )

    status = board.status()
    relevant = _relevant_status(status)
    is_fatal = any(relevant & flag for flag in FATAL_STATUSES)

    legal_turn: chess.Color | None = None
    if not is_fatal:
        for turn in (board.turn, not board.turn):
            if _relevant_status(_status_for_turn(board, turn)) == chess.STATUS_VALID:
                legal_turn = turn
                break

    return PositionCheck(
        fen=normalized,
        status=status,
        is_legal=relevant == chess.STATUS_VALID,
        is_fatal=is_fatal,
        legal_turn=legal_turn,
        problems=describe_status(status),
    )


def _relevant_status(status: chess.Status) -> chess.Status:
    """Remove do status os flags que são artefato do preenchimento padrão da FEN."""
    for flag in IGNORED_STATUSES:
        status &= ~flag
    return status


def is_legal_position(fen: str) -> bool:
    """A posição é legal exatamente como informada. Ver `check_position` para o detalhe."""
    return check_position(fen).is_legal


def _normalize_fen(fen: str) -> str:
    fen = str(fen).strip()
    if " " not in fen:
        return f"{fen} w - - 0 1"
    return fen


def is_syntactically_valid_fen(fen: str) -> bool:
    """A FEN é parseável.

    ATENÇÃO: não garante posição legal. Aceita, por exemplo, posições sem rei ou com
    dois reis da mesma cor. Para legalidade use `check_position` / `is_legal_position`.
    """
    try:
        chess.Board(_normalize_fen(fen))
        return True
    except (ValueError, AttributeError, TypeError):
        # AttributeError/TypeError cobrem entrada nao textual vinda da UI ou do CSV.
        return False


# Nome historico, mantido para nao quebrar chamadores. Prefira o nome explicito acima.
is_valid_fen = is_syntactically_valid_fen


def board_from_fen(fen: str) -> chess.Board:
    return chess.Board(_normalize_fen(fen))


def labels_from_fen(fen: str) -> list[int]:
    board_part = fen.split()[0] if " " in fen else fen
    rows = board_part.split("/")
    if len(rows) != 8:
        raise ValueError("A FEN deve ter 8 filas.")

    labels: list[int] = []
    for row in rows:
        expanded: list[str] = []
        for ch in row:
            if ch.isdigit():
                expanded.extend(["empty"] * int(ch))
            else:
                expanded.append(ch)
        if len(expanded) != 8:
            raise ValueError("Cada fila da FEN deve resolver para 8 casas.")
        for peca in expanded:
            # **Caractere desconhecido levanta, e não vira casa vazia (S-361).** O `.get(peca,
            # empty)` de antes transformava qualquer lixo -- um `x` da leitura, uma figurina
            # Unicode, uma letra fora do alfabeto de peça -- numa casa vazia perfeitamente
            # plausível. E o pior cliente disso é a segunda opinião: as duas leituras viravam
            # `empty` na mesma casa e ela anunciava **acordo total** sobre um tabuleiro que
            # nenhuma das duas leu.
            #
            # As FEN válidas não passam por aqui: `PIECE_TO_IDX` tem exatamente as doze peças, e
            # o dígito já foi expandido acima.
            if peca not in PIECE_TO_IDX:
                raise ValueError(
                    f"Caractere de peça desconhecido na FEN: {peca!r}. "
                    f"Os válidos são {''.join(sorted(k for k in PIECE_TO_IDX if k != 'empty'))}."
                )
            labels.append(PIECE_TO_IDX[peca])
    return labels


def square_name(index: int) -> str:
    """Nome algébrico da casa a partir do índice em ordem de leitura (0 = a8, 63 = h1).

    É a mesma ordem de `labels_from_fen` e da saída do modelo. Existe para que mensagens
    e a fila de revisão falem "e4" em vez de "casa 36".
    """
    if not 0 <= index < 64:
        raise ValueError(f"Índice de casa fora do intervalo 0..63: {index}")
    return f"{'abcdefgh'[index % 8]}{8 - index // 8}"


def square_from_reading_index(index: int) -> chess.Square:
    """Índice em ordem de leitura (0 = a8) para casa do `python-chess` (0 = a1).

    As duas numerações convivem no projeto e a conversão estava implícita em cada lugar que
    precisava dela (`app_tkinter._draw_board_canvas` fazia `(7 - row) * 8 + col` na mão). O
    heatmap da S-21 e o painel de legalidade precisam ir e voltar entre elas o tempo todo.
    """
    if not 0 <= index < 64:
        raise ValueError(f"Índice de casa fora do intervalo 0..63: {index}")
    return chess.square(index % 8, 7 - index // 8)


def reading_index_from_square(square: chess.Square) -> int:
    """Inversa de `square_from_reading_index`."""
    if not 0 <= square < 64:
        raise ValueError(f"Casa fora do intervalo 0..63: {square}")
    return (7 - chess.square_rank(square)) * 8 + chess.square_file(square)


def pawn_direction_score(class_indices: Sequence[int]) -> float | None:
    """Quão "de pé" a posição parece, pela fila média dos peões (S-13).

    Peão anda para frente e nunca volta: os brancos ficam nas filas baixas e os pretos nas
    altas. A diferença entre as médias é ~+2 a +4 numa posição de verdade e mede o mesmo
    valor com sinal trocado se o tabuleiro estiver de cabeça para baixo.

    Devolve `None` quando falta peão de alguma cor -- aí o sinal não tem o que dizer, e
    fingir um número seria pior que admitir isso.

    Existe porque o `min_confidence` decide a orientação muito bem quando a leitura é boa e
    **para de decidir** quando não é: medido no `1937 Kemeri.pdf`, dois diagramas cuja
    leitura de pé era claramente correta tinham margem de confiança de 0,001 e 0,019 --
    ruído. Este prior, ao contrário, olha a estrutura da posição e continua informativo
    justamente aí (+3,8 contra -4,2 nos mesmos dois casos).
    """
    if len(class_indices) != 64:
        raise ValueError("Esperadas exatamente 64 casas.")

    white = [8 - index // 8 for index, value in enumerate(class_indices) if value == PIECE_TO_IDX["P"]]
    black = [8 - index // 8 for index, value in enumerate(class_indices) if value == PIECE_TO_IDX["p"]]
    if not white or not black:
        return None
    return sum(black) / len(black) - sum(white) / len(white)


def fen_from_class_indices(class_indices: Iterable[int]) -> str:
    classes = [IDX_TO_CLASS[int(idx)] for idx in class_indices]
    if len(classes) != 64:
        raise ValueError("Esperadas exatamente 64 casas.")

    rows: list[str] = []
    for row_start in range(0, 64, 8):
        row = classes[row_start : row_start + 8]
        chunks: list[str] = []
        empty_count = 0
        for item in row:
            if item == "empty":
                empty_count += 1
                continue
            if empty_count > 0:
                chunks.append(str(empty_count))
                empty_count = 0
            chunks.append(item)
        if empty_count > 0:
            chunks.append(str(empty_count))
        rows.append("".join(chunks))
    return "/".join(rows)
