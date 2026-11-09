from __future__ import annotations

from PyQt6.QtCore import pyqtSignal as Signal
from PyQt6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QLabel, QLineEdit, QListWidget, QVBoxLayout, QWidget

from ..core.search import SearchHit, search_fen, search_san_sequence, search_text


class SearchDialog(QDialog):
    hit_selected_signal = Signal(int, int)

    def __init__(self, raw_text: str, analyses: list[object], parent: QWidget | None = None):
        super().__init__(parent)
        self._raw_text = raw_text
        self._analyses = analyses
        self._hits: list[SearchHit] = []
        self.setWindowTitle("Buscar no documento")
        self.resize(560, 390)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Procure texto, sequência SAN ou uma FEN:"))
        self.mode = QComboBox()
        self.mode.addItems(["Texto", "Sequência SAN", "FEN"])
        layout.addWidget(self.mode)
        self.query = QLineEdit()
        self.query.setPlaceholderText("ex.: e4 e5 Cf3 ou r1bqkbnr/pppp1ppp/...")
        self.query.returnPressed.connect(self._run)
        self.mode.currentIndexChanged.connect(self._run)
        layout.addWidget(self.query)
        self.results = QListWidget()
        self.results.itemActivated.connect(self._activate)
        self.results.itemDoubleClicked.connect(self._activate)
        layout.addWidget(self.results, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _run(self) -> None:
        query = self.query.text()
        self._hits = [
            search_text(self._raw_text, query),
            search_san_sequence(self._analyses, query),
            search_fen(self._analyses, query),
        ][self.mode.currentIndex()]
        self.results.clear()
        self.results.addItems([hit.label for hit in self._hits])
        if self._hits:
            self.results.setCurrentRow(0)

    def _activate(self) -> None:
        row = self.results.currentRow()
        if 0 <= row < len(self._hits):
            hit = self._hits[row]
            self.hit_selected_signal.emit(hit.start, hit.end)
            self.accept()
