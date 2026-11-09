"""Prévia e escolha da ordem de colunas de uma página PDF."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QListWidget, QPushButton, QVBoxLayout

from ..core.pdf_columns import PdfTextBlock, order_columns


class PdfColumnsDialog(QDialog):
    def __init__(self, columns: list[list[PdfTextBlock]], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Ordenar colunas do PDF")
        self.resize(520, 420)
        self.columns = columns
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Arraste ou selecione uma coluna e use os botões para definir a ordem de leitura:"))
        self.list = QListWidget()
        for index, column in enumerate(columns, 1):
            sample = " ".join(block.text.split() for block in column)[:100]
            self.list.addItem(f"Coluna {index}: {sample}")
            self.list.item(self.list.count() - 1).setData(Qt.ItemDataRole.UserRole, index - 1)
        self.list.setCurrentRow(0)
        layout.addWidget(self.list)
        up = QPushButton("Subir coluna")
        down = QPushButton("Descer coluna")
        up.clicked.connect(lambda: self._move(-1))
        down.clicked.connect(lambda: self._move(1))
        layout.addWidget(up)
        layout.addWidget(down)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _move(self, delta: int):
        row = self.list.currentRow()
        target = row + delta
        if not 0 <= target < self.list.count():
            return
        item = self.list.takeItem(row)
        self.list.insertItem(target, item)
        self.list.setCurrentRow(target)

    def ordered_text(self) -> str:
        # A QListWidget item has moved visually; keep the original index in its
        # user data so text ordering remains independent of its label.
        order = [int(self.list.item(row).data(32)) for row in range(self.list.count())]
        return order_columns(self.columns, order)
