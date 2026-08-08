"""Escolha da peca na promocao (corrige D13).

O programa promovia sempre a dama, calado. Subpromocao pelo tabuleiro era
impossivel -- e `e8=C` decidindo a partida e o tipo de lance que aparece em
livro de finais o tempo todo.

`Shift` ao soltar a peca continua promovendo a dama direto, sem passar por aqui:
e o que se quer em 95% das vezes, e a SPEC pede as duas coisas.
"""

from __future__ import annotations

import chess
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

#: Ordem de apresentacao: dama primeiro porque e a resposta quase sempre.
PROMOTION_CHOICES: tuple[tuple[int, str, str, str], ...] = (
    (chess.QUEEN, "Dama", "♕", "♛"),
    (chess.KNIGHT, "Cavalo", "♘", "♞"),
    (chess.ROOK, "Torre", "♖", "♜"),
    (chess.BISHOP, "Bispo", "♗", "♝"),
)


class PromotionDialog(QDialog):
    def __init__(self, color: chess.Color, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Promocao")
        self.setModal(True)
        self._piece_type: int = chess.QUEEN

        layout = QVBoxLayout(self)
        label = QLabel("Promover a:")
        layout.addWidget(label)

        row = QHBoxLayout()
        for piece_type, name, white_glyph, black_glyph in PROMOTION_CHOICES:
            glyph = white_glyph if color == chess.WHITE else black_glyph
            button = QPushButton(f"{glyph}\n{name}")
            button.setMinimumSize(72, 72)
            button.setStyleSheet("font-size: 22px;")
            button.setToolTip(name)
            button.clicked.connect(lambda _checked=False, chosen=piece_type: self._choose(chosen))
            row.addWidget(button)
        layout.addLayout(row)

        hint = QLabel("Solte com Shift para promover a dama sem perguntar.")
        hint.setStyleSheet("color: #8a8f98;")
        layout.addWidget(hint)

    def _choose(self, piece_type: int):
        self._piece_type = piece_type
        self.accept()

    @property
    def piece_type(self) -> int:
        return self._piece_type

    @classmethod
    def ask(cls, color: chess.Color, parent=None) -> int | None:
        """Devolve o tipo de peca escolhido, ou `None` se o usuario desistiu."""
        dialog = cls(color, parent)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            return dialog.piece_type
        return None


def wants_default_promotion(modifiers: Qt.KeyboardModifier) -> bool:
    return bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
