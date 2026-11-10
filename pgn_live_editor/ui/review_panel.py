"""Fila de revisão humana dos registros de reconhecimento."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtCore import pyqtSignal as Signal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.recognition_record import RecognitionRecord, ReviewAction
from ..core.review_queue import ReviewFilter, filter_records


class ReviewPanel(QWidget):
    record_selected_signal = Signal(object)
    accept_requested_signal = Signal(object)
    reject_rule_requested_signal = Signal(str)
    comment_requested_signal = Signal(object)

    def __init__(self):
        super().__init__()
        self.records: list[RecognitionRecord] = []
        self.visible_records: list[RecognitionRecord] = []

        layout = QVBoxLayout(self)
        filters = QHBoxLayout()
        self.cmb_game = self._combo(filters, "Partida")
        self.cmb_page = self._combo(filters, "Página")
        self.cmb_confidence = self._combo(filters, "Confiança")
        self.cmb_type = self._combo(filters, "Tipo")
        self.cmb_altered = self._combo(filters, "Alterou")
        self.cmb_action = self._combo(filters, "Estado")
        self.lbl_count = QLabel("0 pendência(s)")
        filters.addWidget(self.lbl_count)
        layout.addLayout(filters)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["", "Trecho", "Interpretação", "Confiança", "Regra", "Origem", "Estado"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.itemSelectionChanged.connect(self._on_selected)
        layout.addWidget(self.table, 1)

        actions = QHBoxLayout()
        self.btn_accept = QPushButton("Aceitar idênticos")
        self.btn_reject_rule = QPushButton("Rejeitar regra")
        self.btn_comment = QPushButton("Virar comentário")
        for button in (self.btn_accept, self.btn_reject_rule, self.btn_comment):
            button.setEnabled(False)
            actions.addWidget(button)
        actions.addStretch()
        layout.addLayout(actions)
        self.btn_accept.clicked.connect(lambda: self.accept_requested_signal.emit(self.selected_records()))
        self.btn_reject_rule.clicked.connect(self._reject_rule)
        self.btn_comment.clicked.connect(lambda: self.comment_requested_signal.emit(self.selected_records()))

        for combo in (
            self.cmb_game,
            self.cmb_page,
            self.cmb_confidence,
            self.cmb_type,
            self.cmb_altered,
            self.cmb_action,
        ):
            combo.currentIndexChanged.connect(self._rebuild_table)

    @staticmethod
    def _combo(layout, label: str) -> QComboBox:
        combo = QComboBox()
        combo.setToolTip(label)
        layout.addWidget(combo)
        return combo

    @staticmethod
    def _fill(combo: QComboBox, values: list[str], all_label: str):
        combo.blockSignals(True)
        combo.clear()
        combo.addItem(all_label, "all")
        for value in values:
            combo.addItem(value, value)
        combo.blockSignals(False)

    def set_records(self, records: list[RecognitionRecord]):
        self.records = records
        games = sorted({str(record.game) for record in records if record.game is not None})
        pages = sorted({str(record.source.page) for record in records if record.source.page is not None})
        types = sorted({record.interpretation for record in records})
        self._fill(self.cmb_game, games, "Todas as partidas")
        self._fill(self.cmb_page, pages, "Todas as páginas")
        self._fill(self.cmb_confidence, ["high", "medium", "low"], "Todas as confianças")
        self._fill(self.cmb_type, types, "Todos os tipos")
        self._fill(self.cmb_altered, ["yes", "no"], "Alteração: todas")
        self._fill(self.cmb_action, [action.value for action in ReviewAction], "Todos os estados")
        self.cmb_action.setCurrentIndex(self.cmb_action.findData("pending"))
        self._rebuild_table()

    def _criteria(self) -> ReviewFilter:
        def value(combo):
            value = combo.currentData()
            return value if value != "all" else None

        return ReviewFilter(
            game=int(value(self.cmb_game)) if value(self.cmb_game) is not None else None,
            page=int(value(self.cmb_page)) if value(self.cmb_page) is not None else None,
            confidence=value(self.cmb_confidence) or "all",
            interpretation=value(self.cmb_type) or "all",
            altered=value(self.cmb_altered) or "all",
            action=value(self.cmb_action) or "all",
        )

    def _rebuild_table(self):
        self.visible_records = filter_records(self.records, self._criteria())
        self.lbl_count.setText(f"{len(self.visible_records)} item(ns)")
        self.table.setRowCount(len(self.visible_records))
        for row, record in enumerate(self.visible_records):
            origin = f"p. {record.source.page}" if record.source.page is not None else "texto"
            values = [
                "⚠" if record.action is ReviewAction.PENDING else "✓",
                record.raw_text,
                record.interpretation,
                record.confidence,
                record.rule_id,
                origin,
                record.action.value,
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, record.source_id)
                self.table.setItem(row, column, item)
        self._update_buttons()

    def selected_records(self) -> list[RecognitionRecord]:
        ids = {
            str(self.table.item(index.row(), 0).data(Qt.ItemDataRole.UserRole))
            for index in self.table.selectionModel().selectedRows()
        }
        return [record for record in self.visible_records if record.source_id in ids]

    def _on_selected(self):
        selected = self.selected_records()
        if selected:
            self.record_selected_signal.emit(selected[0])
        self._update_buttons()

    def _update_buttons(self):
        selected = self.selected_records()
        enabled = bool(selected)
        self.btn_accept.setEnabled(enabled and all(record.altered and record.proposal for record in selected))
        self.btn_reject_rule.setEnabled(enabled and len({record.rule_id for record in selected}) == 1)
        self.btn_comment.setEnabled(enabled)

    def _reject_rule(self):
        selected = self.selected_records()
        if selected:
            self.reject_rule_requested_signal.emit(selected[0].rule_id)
