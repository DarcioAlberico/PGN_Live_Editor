import chess


class MoveValidator:
    """Valida tokens contra o tabuleiro corrente.

    O resgate por semelhanca deixou de morar aqui: a geracao de hipoteses,
    com confianca e explicacao, e responsabilidade de `candidates.py`.
    """

    def __init__(self):
        self.board = chess.Board()

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
        board = self.board.copy(stack=True)
        while True:
            if board.fen() == fen:
                return board
            if not board.move_stack:
                return chess.Board(fen)
            board.pop()

    def is_legal(self, san_move: str) -> bool:
        """Strict check if python-chess considers the SAN valid right now."""
        try:
            return self.board.parse_san(san_move) in self.board.legal_moves
        except ValueError:
            return False

    def push_san(self, san_move: str) -> chess.Move:
        """Push a known valid move onto the board."""
        return self.board.push_san(san_move)

    def pop(self):
        """Pop the last move."""
        self.board.pop()

    def get_legal_sans(self) -> list[str]:
        """Returns all legal moves in SAN format for the current position."""
        return [self.board.san(move) for move in self.board.legal_moves]

    def push_null(self) -> chess.Move:
        """Lance nulo (`--`), usado em posicoes de estudo."""
        return self.board.push(chess.Move.null())
