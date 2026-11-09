"""Escolha rápida de um lance legal para inserir no texto."""

from __future__ import annotations

from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QListWidget, QVBoxLayout


class LegalMoveDialog(QDialog):
    def __init__(self, suggestions: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Inserir lance legal")
        self.resize(320, 420)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Escolha o lance legal para inserir no ponto do cursor:"))
        self.moves = QListWidget()
        self.moves.addItems(suggestions)
        self.moves.setCurrentRow(0)
        self.moves.itemDoubleClicked.connect(lambda _item: self.accept())
        layout.addWidget(self.moves)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_move(self) -> str:
        item = self.moves.currentItem()
        return item.text() if item is not None else ""
