"""Painel de partidas: o documento visto como livro (SPEC 4.1).

Um capítulo de livro tem 8, 20, 64 partidas no mesmo texto. Sem esta lista, a
unica forma de saber onde uma acaba e outra comeca era rolar o editor. Aqui
cada partida tem titulo, marca de concluida e o que a analise ja soube dizer
dela -- e um clique leva o cursor para la.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtCore import pyqtSignal as Signal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..services.parse_worker import SegmentSummary


class GamesPanel(QWidget):
    game_selected_signal = Signal(int)
    completion_toggled_signal = Signal(int, bool)
    analyze_book_requested_signal = Signal()

    def __init__(self):
        super().__init__()

        self.summaries: list[SegmentSummary] = []
        self.completed: set[int] = set()
        self.active_index = 0
        self._updating = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 6, 0)

        header = QHBoxLayout()
        self.lbl_title = QLabel("Partidas")
        self.lbl_title.setStyleSheet("font-weight: bold;")
        header.addWidget(self.lbl_title)
        header.addStretch()
        layout.addLayout(header)

        self.lbl_progress = QLabel("1 partida")
        self.lbl_progress.setStyleSheet("color: #9aa6b8;")
        layout.addWidget(self.lbl_progress)

        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list_widget.setAlternatingRowColors(True)
        self.list_widget.setMinimumWidth(190)
        self.list_widget.currentRowChanged.connect(self._on_row_changed)
        self.list_widget.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self.list_widget, 1)

        self.btn_analyze = QPushButton("Analisar livro")
        self.btn_analyze.setToolTip("Analisa todas as partidas para preencher lances e alertas da lista")
        self.btn_analyze.clicked.connect(self.analyze_book_requested_signal.emit)
        layout.addWidget(self.btn_analyze)

    # ------------------------------------------------------------------

    def set_completed(self, indexes) -> None:
        self.completed = {int(index) for index in indexes}
        self._rebuild()

    def set_summaries(self, summaries: list[SegmentSummary], active_index: int = 0) -> None:
        self.summaries = list(summaries)
        self.active_index = active_index
        self._rebuild()

    # ------------------------------------------------------------------

    def _label_for(self, summary: SegmentSummary) -> str:
        if not summary.analyzed:
            return summary.title

        parts = [summary.title, f"{summary.move_count} lances"]
        if summary.error_count:
            parts.append(f"✖ {summary.error_count}")
        elif summary.warning_count:
            parts.append(f"⚠ {summary.warning_count}")
        return "  ·  ".join(parts)

    def _rebuild(self) -> None:
        self._updating = True
        try:
            self.list_widget.clear()

            for summary in self.summaries:
                item = QListWidgetItem(self._label_for(summary))
                item.setData(Qt.ItemDataRole.UserRole, summary.index)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(
                    Qt.CheckState.Checked if summary.index in self.completed else Qt.CheckState.Unchecked
                )
                item.setToolTip(summary.title)

                if summary.index == self.active_index:
                    font = item.font()
                    font.setWeight(QFont.Weight.Bold)
                    item.setFont(font)
                if summary.error_count:
                    item.setForeground(QColor("#ff8c8c"))
                elif summary.index in self.completed:
                    item.setForeground(QColor("#9ce1a5"))

                self.list_widget.addItem(item)

            if 0 <= self.active_index < self.list_widget.count():
                self.list_widget.setCurrentRow(self.active_index)
        finally:
            self._updating = False

        self._refresh_progress()

    def _refresh_progress(self) -> None:
        total = len(self.summaries)
        if total <= 1:
            self.lbl_progress.setText("1 partida")
        else:
            done = len([index for index in self.completed if index < total])
            self.lbl_progress.setText(f"partida {self.active_index + 1}/{total}  ·  {done} concluida(s)")

        self.btn_analyze.setEnabled(any(not summary.analyzed for summary in self.summaries))

    # ------------------------------------------------------------------

    def _on_row_changed(self, row: int) -> None:
        if self._updating or row < 0 or row >= len(self.summaries):
            return
        self.game_selected_signal.emit(self.summaries[row].index)

    def _on_item_changed(self, item: QListWidgetItem) -> None:
        if self._updating:
            return

        index = item.data(Qt.ItemDataRole.UserRole)
        completed = item.checkState() == Qt.CheckState.Checked
        if completed:
            self.completed.add(index)
        else:
            self.completed.discard(index)

        self._refresh_progress()
        self.completion_toggled_signal.emit(index, completed)

    # ------------------------------------------------------------------

    def toggle_current_completion(self) -> None:
        row = self.list_widget.currentRow()
        item = self.list_widget.item(row)
        if item is None:
            return
        item.setCheckState(
            Qt.CheckState.Unchecked if item.checkState() == Qt.CheckState.Checked else Qt.CheckState.Checked
        )
