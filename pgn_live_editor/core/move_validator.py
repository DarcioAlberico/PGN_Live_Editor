import chess

from .positions import position_snapshot

#: Lances guardados por posicao numa analise. Passou disto, recomeca: e um
#: cache, nao uma memoria.
PARSED_MOVES_LIMIT = 50_000


class MoveValidator:
    """Valida tokens contra o tabuleiro corrente.

    O resgate por semelhanca deixou de morar aqui: a geracao de hipoteses,
    com confianca e explicacao, e responsabilidade de `candidates.py`.
    """

    def __init__(self) -> None:
        self.board = chess.Board()
        # (posicao, SAN) -> o lance legal, ou None. O parser confere um lance
        # (`is_legal`), o joga (`push_san`) e, antes, ja o tinha conferido como
        # continuacao do lance anterior: era o mesmo SAN interpretado ate quatro
        # vezes, a ~20 us cada (R-401).
        self._parsed: dict[tuple, chess.Move | None] = {}

    def forget_parsed_moves(self) -> None:
        self._parsed.clear()

    def set_board_fen(self, fen: str):
        """Reset the internal board state to a given FEN (sem historico de lances)."""
        self.board.set_fen(fen)

    def set_board(self, board: chess.Board) -> None:
        """Troca o tabuleiro inteiro, historico de lances incluido."""
        self.board = board

    def board_at(self, fen: str) -> chess.Board:
        """A posicao `fen` **com o historico** que levou ate ela (R-207).

        Variante parte de uma posicao ja vista na linha atual: desfazer lances
        do tabuleiro corrente ate chega-la preserva o `move_stack`, que a
        notacao descritiva usa para saber qual peca e "do rei" e qual foi o
        ultimo lance. Montar a posicao so pelo FEN zerava esse historico (L-02).
        Se a posicao nao estiver na linha (texto dessincronizado), vale o FEN.
        """
        fields = fen.split()
        turn = fields[1] == "w"
        fullmove = int(fields[5])
        board = self.board.copy(stack=True)
        while True:
            # Vez e numero do lance antes do `fen()`, que custa ~33 us por lance desfeito.
            if board.turn == turn and board.fullmove_number == fullmove and board.fen() == fen:
                return board
            if not board.move_stack:
                return chess.Board(fen)
            board.pop()

    def legal_move_on(self, board: chess.Board, san_move: str) -> chess.Move | None:
        """O lance que o SAN e na posicao de `board`, se for legal (com cache por posicao)."""
        key = (position_snapshot(board), san_move)
        if key in self._parsed:
            return self._parsed[key]
        try:
            move: chess.Move | None = board.parse_san(san_move)
        except ValueError:
            move = None
        # `parse_san` so devolve lance legal -- menos o nulo (`--`), que nao e.
        if move is not None and not move:
            move = None
        if len(self._parsed) >= PARSED_MOVES_LIMIT:
            self._parsed.clear()
        self._parsed[key] = move
        return move

    def legal_move(self, san_move: str) -> chess.Move | None:
        """O lance que o SAN e na posicao corrente, se for legal."""
        return self.legal_move_on(self.board, san_move)

    def is_legal(self, san_move: str) -> bool:
        """Strict check if python-chess considers the SAN valid right now."""
        return self.legal_move(san_move) is not None

    def push_san(self, san_move: str) -> chess.Move:
        """Push a known valid move onto the board."""
        move = self.legal_move(san_move)
        if move is None:
            return self.board.push_san(san_move)  # levanta o erro de sempre
        self.board.push(move)
        return move

    def pop(self):
        """Pop the last move."""
        self.board.pop()

    def get_legal_sans(self) -> list[str]:
        """Returns all legal moves in SAN format for the current position."""
        return [self.board.san(move) for move in self.board.legal_moves]

    def push_null(self) -> chess.Move:
        """Lance nulo (`--`), usado em posicoes de estudo."""
        move = chess.Move.null()
        self.board.push(move)
        return move
