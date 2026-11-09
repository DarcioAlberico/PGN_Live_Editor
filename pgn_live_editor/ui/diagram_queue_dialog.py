"""Fila de revisão persistente dos diagramas OCR."""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal as Signal
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..core.diagram_review import record_key, set_review_state
from .diagram_review_dialog import DiagramReviewDialog


class DiagramQueueDialog(QDialog):
    page_requested_signal = Signal(int)
    fen_requested_signal = Signal(str)

    def __init__(self, records: list[dict], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Fila de revisão de diagramas")
        self.resize(760, 420)
        self.records = records
        layout = QVBoxLayout(self)
        filters = QHBoxLayout()
        filters.addWidget(QLabel("Estado:"))
        self.cmb_state = QComboBox()
        self.cmb_state.addItem("Pendentes", "pending")
        self.cmb_state.addItem("Todos", "all")
        self.cmb_state.currentIndexChanged.connect(self.refresh)
        filters.addWidget(self.cmb_state)
        filters.addWidget(QLabel("Confiança mínima:"))
        self.spn_confidence = QDoubleSpinBox()
        self.spn_confidence.setRange(0.0, 100.0)
        self.spn_confidence.setValue(0.0)
        self.spn_confidence.setSuffix("%")
        self.spn_confidence.valueChanged.connect(self.refresh)
        filters.addWidget(self.spn_confidence)
        self.txt_filter = QLineEdit()
        self.txt_filter.setPlaceholderText("Buscar PDF, página, diagrama ou problema…")
        self.txt_filter.textChanged.connect(self.refresh)
        filters.addWidget(self.txt_filter, 1)
        layout.addLayout(filters)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(("PDF", "Página", "Diagrama", "Confiança", "Lado", "Legal", "Problemas"))
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.doubleClicked.connect(lambda _index: self.review_selected())
        layout.addWidget(self.table)

        actions = QHBoxLayout()
        self.btn_review = QPushButton("Revisar selecionado")
        self.btn_review.clicked.connect(self.review_selected)
        self.btn_accept = QPushButton("Aceitar selecionado")
        self.btn_accept.clicked.connect(self.accept_selected)
        self.btn_reject = QPushButton("Rejeitar selecionado")
        self.btn_reject.clicked.connect(self.reject_selected)
        self.btn_ignore = QPushButton("Ignorar selecionado")
        self.btn_ignore.clicked.connect(self.ignore_selected)
        self.btn_copy = QPushButton("Copiar FEN")
        self.btn_copy.clicked.connect(self.copy_fen)
        self.btn_apply = QPushButton("Usar FEN na partida")
        self.btn_apply.clicked.connect(self.apply_fen)
        self.btn_next = QPushButton("Revisar próximo")
        self.btn_next.clicked.connect(self.review_next)
        self.lbl_count = QLabel()
        actions.addWidget(self.btn_review)
        actions.addWidget(self.btn_accept)
        actions.addWidget(self.btn_reject)
        actions.addWidget(self.btn_ignore)
        actions.addWidget(self.btn_copy)
        actions.addWidget(self.btn_apply)
        actions.addWidget(self.btn_next)
        actions.addWidget(self.lbl_count)
        actions.addStretch()
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        actions.addWidget(close)
        layout.addLayout(actions)
        self.refresh()

    def refresh(self):
        rows = self._visible_records()
        self.table.setRowCount(len(rows))
        for row, record in enumerate(rows):
            values = (
                str(record.get("pdf_path", "")),
                str(record.get("page", "")),
                str(record.get("diagram", "")),
                f"{float(record.get('min_confidence', 0)):.1%}",
                str(record.get("side_to_move", "?")),
                "sim" if record.get("is_legal") is True else "não" if record.get("is_legal") is False else "?",
                ", ".join(str(item) for item in record.get("problems", [])) or "revisão manual",
            )
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(value))
        self.table.resizeColumnsToContents()
        self.lbl_count.setText(f"{len(rows)} pendência(s)")
        self.btn_review.setEnabled(bool(rows))
        self.btn_accept.setEnabled(bool(rows))
        self.btn_reject.setEnabled(bool(rows))
        self.btn_ignore.setEnabled(bool(rows))
        self.btn_copy.setEnabled(bool(rows))
        self.btn_apply.setEnabled(bool(rows))
        self.btn_next.setEnabled(bool(rows))

    def _visible_records(self) -> list[dict]:
        state = self.cmb_state.currentData()
        query = self.txt_filter.text().strip().casefold()
        minimum = self.spn_confidence.value() / 100.0
        result = []
        seen = set()
        for record in self.records:
            key = record_key(record)
            if key in seen:
                continue
            seen.add(key)
            if state != "all" and str(record.get("review", "pending")) != state:
                continue
            if float(record.get("min_confidence", 0.0)) < minimum:
                continue
            haystack = " ".join(
                str(record.get(field, ""))
                for field in ("pdf_path", "page", "diagram", "side_to_move", "problems", "review")
            ).casefold()
            if query and query not in haystack:
                continue
            result.append(record)
        return result

    def _selected_record(self) -> dict | None:
        row = self.table.currentRow()
        rows = self._visible_records()
        return rows[row] if 0 <= row < len(rows) else None

    def review_selected(self):
        record = self._selected_record()
        if record is None:
            return
        self.page_requested_signal.emit(max(0, int(record.get("page", 1)) - 1))
        dialog = DiagramReviewDialog(
            str(record.get("fen", "8/8/8/8/8/8/4K3/4k3 w - - 0 1")),
            uncertain_squares=tuple(record.get("uncertain_squares", [])),
            title=f"Revisar diagrama {record.get('diagram', '')}",
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        placement, fen, legal, problems = dialog.result_data()
        record.update(
            placement=placement,
            fen=fen,
            side_to_move=fen.split()[1],
            is_legal=legal,
            problems=list(problems),
            uncertain_squares=[],
            review="accepted" if legal else "pending",
        )
        self.refresh()

    def accept_selected(self):
        record = self._selected_record()
        if record is not None:
            set_review_state(self.records, record_key(record), "accepted")
            self.refresh()

    def reject_selected(self):
        record = self._selected_record()
        if record is not None:
            set_review_state(self.records, record_key(record), "rejected")
            self.refresh()

    def review_next(self):
        if self.table.rowCount():
            self.table.selectRow(0)
            self.review_selected()

    def copy_fen(self):
        record = self._selected_record()
        if record is not None:
            QGuiApplication.clipboard().setText(str(record.get("fen", "")))

    def apply_fen(self):
        record = self._selected_record()
        if record is not None and record.get("fen"):
            self.fen_requested_signal.emit(str(record["fen"]))

    def ignore_selected(self):
        record = self._selected_record()
        if record is not None:
            set_review_state(self.records, record_key(record), "ignored")
            self.refresh()
