"""Revisão manual de uma posição reconhecida pelo OCR de diagramas."""

from __future__ import annotations

import chess
from PyQt6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QLabel, QVBoxLayout

from .board_view import InteractiveBoardView


class DiagramReviewDialog(QDialog):
    """Editor simples casa a casa para confirmar ou corrigir uma FEN."""

    def __init__(
        self,
        fen: str,
        *,
        uncertain_squares: tuple[int, ...] = (),
        title: str = "Revisar diagrama",
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(520, 620)
        self.board = chess.Board(fen)
        self.uncertain_squares = set(int(square) for square in uncertain_squares)

        layout = QVBoxLayout(self)
        self.lbl_status = QLabel()
        layout.addWidget(self.lbl_status)

        self.piece_picker = QComboBox()
        choices = [("Apagar", None)]
        choices += [
            (f"Brancas: {name}", chess.Piece(kind, chess.WHITE))
            for kind, name in (
                (chess.PAWN, "peão"),
                (chess.KNIGHT, "cavalo"),
                (chess.BISHOP, "bispo"),
                (chess.ROOK, "torre"),
                (chess.QUEEN, "dama"),
                (chess.KING, "rei"),
            )
        ]
        choices += [
            (f"Pretas: {name}", chess.Piece(kind, chess.BLACK))
            for kind, name in (
                (chess.PAWN, "peão"),
                (chess.KNIGHT, "cavalo"),
                (chess.BISHOP, "bispo"),
                (chess.ROOK, "torre"),
                (chess.QUEEN, "dama"),
                (chess.KING, "rei"),
            )
        ]
        for label, piece in choices:
            self.piece_picker.addItem(label, piece)
        layout.addWidget(self.piece_picker)

        self.side_picker = QComboBox()
        self.side_picker.addItem("Brancas jogam", "w")
        self.side_picker.addItem("Pretas jogam", "b")
        self.side_picker.setCurrentIndex(0 if self.board.turn == chess.WHITE else 1)
        self.side_picker.currentIndexChanged.connect(self._refresh_status)
        layout.addWidget(self.side_picker)

        self.board_view = InteractiveBoardView()
        self.board_view.setMinimumSize(440, 440)
        self.board_view.square_released_signal.connect(self._set_square)
        layout.addWidget(self.board_view, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._render()

    def _set_square(self, square: int, _button: int):
        piece = self.piece_picker.currentData()
        self.board.set_piece_at(square, piece)
        # Uma casa alterada manualmente deixa de ser uma pendência do OCR.
        self.uncertain_squares.discard(self._reading_index(square))
        self._render()

    @staticmethod
    def _reading_index(square: int) -> int:
        """Converte a ordem FEN (a8…h1) para o índice usado pelo OCR."""
        return (7 - chess.square_rank(square)) * 8 + chess.square_file(square)

    def _refresh_status(self):
        validity = "posição legal" if self.board.is_valid() else "posição precisa de revisão"
        pending = f" — {len(self.uncertain_squares)} casa(s) incerta(s)" if self.uncertain_squares else ""
        self.lbl_status.setText(f"{self.board.board_fen()} — {validity}{pending}")

    def _render(self):
        size = min(max(self.board_view.width(), 440), 520)
        self.board_view.set_board_geometry(size)
        fill = {
            chess.square(index % 8, 7 - index // 8): "#d59b3f" for index in self.uncertain_squares if 0 <= index < 64
        }
        self.board_view.set_svg(chess.svg.board(self.board, size=size, coordinates=True, fill=fill).decode("utf-8"))
        self._refresh_status()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._render()

    def result_data(self) -> tuple[str, str, bool, tuple[str, ...]]:
        side = str(self.side_picker.currentData())
        board_fen = self.board.board_fen()
        full_fen = f"{board_fen} {side} - - 0 1"
        problems = () if self.board.is_valid() else ("posição ilegal",)
        return board_fen, full_fen, self.board.is_valid(), problems
