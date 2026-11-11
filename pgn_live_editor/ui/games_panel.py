"""Painel de partidas: o documento visto como livro (SPEC 4.1).

Um capítulo de livro tem 8, 20, 64 partidas no mesmo texto. Sem esta lista, a
unica forma de saber onde uma acaba e outra comeca era rolar o editor. Aqui
cada partida tem titulo, marca de concluida e o que a analise ja soube dizer
dela -- e um clique leva o cursor para la.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
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
        self._in_row_change = False
        self._rebuild_scheduled = False
        # O que cada linha mostra agora: (resumo, concluida?, ativa?).
        self._shown: list[tuple | None] = []

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
        completed = {int(index) for index in indexes}
        if completed != self.completed:
            self.completed = completed
            self._rebuild()

    def set_summaries(self, summaries: list[SegmentSummary], active_index: int = 0) -> None:
        summaries = list(summaries)
        if summaries == self.summaries and active_index == self.active_index:
            return
        self.summaries = summaries
        self.active_index = active_index
        self._rebuild()

    # ------------------------------------------------------------------

    def _paint(self, item: QListWidgetItem, summary: SegmentSummary, completed: bool, active: bool) -> None:
        item.setText(self._label_for(summary))
        item.setData(Qt.ItemDataRole.UserRole, summary.index)
        item.setCheckState(Qt.CheckState.Checked if completed else Qt.CheckState.Unchecked)
        item.setToolTip(summary.title)
        font = item.font()
        font.setWeight(QFont.Weight.Bold if active else QFont.Weight.Normal)
        item.setFont(font)
        if summary.error_count:
            item.setForeground(QColor("#ff8c8c"))
        elif completed:
            item.setForeground(QColor("#9ce1a5"))
        else:
            item.setData(Qt.ItemDataRole.ForegroundRole, None)

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
        if self._in_row_change:
            # A navegacao que este clique dispara reanalisa e reconstroi a
            # lista -- dentro do `currentRowChanged` da propria lista. `clear()`
            # ali e reentrante (U-15): reconstruir logo depois, fora do sinal.
            if not self._rebuild_scheduled:
                self._rebuild_scheduled = True
                QTimer.singleShot(0, self._deferred_rebuild)
            return
        self._updating = True
        try:
            if self.list_widget.count() != len(self.summaries):
                self.list_widget.clear()
                self._shown = []
                for _summary in self.summaries:
                    item = QListWidgetItem()
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    self.list_widget.addItem(item)
                    self._shown.append(None)

            # So as linhas que mudaram: num livro de 4 400 partidas, refazer a
            # lista inteira a cada analise que chegava custava ~0,2 s (R-401).
            for row, summary in enumerate(self.summaries):
                state = (summary, summary.index in self.completed, summary.index == self.active_index)
                if self._shown[row] == state:
                    continue
                self._shown[row] = state
                self._paint(self.list_widget.item(row), *state)

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

    def _deferred_rebuild(self) -> None:
        self._rebuild_scheduled = False
        self._rebuild()

    def _on_row_changed(self, row: int) -> None:
        if self._updating or row < 0 or row >= len(self.summaries):
            return
        self._in_row_change = True
        try:
            self.game_selected_signal.emit(self.summaries[row].index)
        finally:
            self._in_row_change = False

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
