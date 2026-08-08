"""Painel de alertas: o que o parser corrigiu, o que ficou em duvida, o que nao entendeu.

Antes, `ast.issues` era calculado e jogado fora -- a interface so mostrava a
contagem. Toda correcao automatica acontecia sem que o usuario pudesse ve-la,
conferi-la ou desfaze-la.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtCore import pyqtSignal as Signal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.issues import (
    ACTION_ACCEPT,
    ACTION_IGNORE,
    ACTION_LEARN,
    ACTION_TO_COMMENT,
    Issue,
    count_by_severity,
    sort_issues,
)

SEVERITY_ICON = {"error": "✖", "warning": "⚠", "info": "ℹ"}
SEVERITY_COLOR = {"error": "#ff8c8c", "warning": "#f0c05a", "info": "#9ce1a5"}


class IssuesPanel(QWidget):
    issue_selected_signal = Signal(int, int)
    accept_requested_signal = Signal(object)
    ignore_requested_signal = Signal(object)
    learn_requested_signal = Signal(object)
    comment_requested_signal = Signal(object)

    def __init__(self):
        super().__init__()

        self.issues: list[Issue] = []
        self._show_cascade = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 6, 0, 0)

        header = QHBoxLayout()
        self.lbl_summary = QLabel("Nenhum alerta.")
        self.lbl_summary.setStyleSheet("font-weight: bold;")
        header.addWidget(self.lbl_summary)
        header.addStretch()

        self.btn_cascade = QPushButton("Mostrar cascata")
        self.btn_cascade.setCheckable(True)
        self.btn_cascade.setToolTip("Erros que so existem porque um lance anterior não foi entendido")
        self.btn_cascade.toggled.connect(self._on_cascade_toggled)

        self.btn_accept = QPushButton("Aceitar correção")
        self.btn_accept.setToolTip("Grava a correção no texto bruto (F4)")
        self.btn_comment = QPushButton("Virar comentário")
        self.btn_ignore = QPushButton("Ignorar")
        self.btn_learn = QPushButton("Aprender")
        self.btn_learn.setToolTip("Grava esta correção no dicionário do projeto")

        for button in (self.btn_accept, self.btn_comment, self.btn_learn, self.btn_ignore):
            button.setEnabled(False)
            header.addWidget(button)
        header.addWidget(self.btn_cascade)
        layout.addLayout(header)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["", "Trecho", "O que aconteceu"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 28)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        layout.addWidget(self.table)

        self.btn_accept.clicked.connect(lambda: self._emit_for_selection(self.accept_requested_signal))
        self.btn_ignore.clicked.connect(lambda: self._emit_for_selection(self.ignore_requested_signal))
        self.btn_learn.clicked.connect(lambda: self._emit_for_selection(self.learn_requested_signal))
        self.btn_comment.clicked.connect(lambda: self._emit_for_selection(self.comment_requested_signal))

    # ------------------------------------------------------------------

    def set_issues(self, issues: list[Issue]):
        self.issues = sort_issues(list(issues))
        self._rebuild_table()

    def _visible_issues(self) -> list[Issue]:
        if self._show_cascade:
            return self.issues
        return [issue for issue in self.issues if not issue.is_cascade]

    def _on_cascade_toggled(self, checked: bool):
        self._show_cascade = checked
        self._rebuild_table()

    def _rebuild_table(self):
        visible = self._visible_issues()
        counts = count_by_severity(self.issues)
        hidden = len(self.issues) - len(visible)

        if not self.issues:
            self.lbl_summary.setText("Nenhum alerta.")
        else:
            parts = []
            if counts["error"]:
                parts.append(f"{counts['error']} erro(s)")
            if counts["warning"]:
                parts.append(f"{counts['warning']} aviso(s)")
            if counts["info"]:
                parts.append(f"{counts['info']} correção(oes)")
            summary = " | ".join(parts)
            if hidden:
                summary += f"  (+{hidden} em cascata)"
            self.lbl_summary.setText(summary)

        self.btn_cascade.setVisible(any(issue.is_cascade for issue in self.issues))

        self.table.setRowCount(len(visible))
        for row, issue in enumerate(visible):
            icon_item = QTableWidgetItem(SEVERITY_ICON.get(issue.severity, "?"))
            icon_item.setForeground(QColor(SEVERITY_COLOR.get(issue.severity, "#cccccc")))
            icon_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            icon_item.setData(Qt.ItemDataRole.UserRole, row)

            snippet_item = QTableWidgetItem(issue.raw_text or "-")
            message_item = QTableWidgetItem(issue.message)
            if issue.candidates:
                options = ", ".join(str(candidate) for candidate in issue.candidates[:3])
                message_item.setToolTip(f"Hipoteses: {options}")

            self.table.setItem(row, 0, icon_item)
            self.table.setItem(row, 1, snippet_item)
            self.table.setItem(row, 2, message_item)

        self._update_buttons()

    # ------------------------------------------------------------------

    def selected_issue(self) -> Issue | None:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows:
            return None
        visible = self._visible_issues()
        index = rows[0].row()
        return visible[index] if 0 <= index < len(visible) else None

    def _update_buttons(self):
        issue = self.selected_issue()
        actions = set(issue.actions) if issue else set()
        self.btn_accept.setEnabled(ACTION_ACCEPT in actions and bool(issue and issue.best_candidate))
        self.btn_ignore.setEnabled(ACTION_IGNORE in actions)
        self.btn_learn.setEnabled(ACTION_LEARN in actions and bool(issue and issue.best_candidate))
        self.btn_comment.setEnabled(ACTION_TO_COMMENT in actions)

    def _on_selection_changed(self):
        self._update_buttons()
        issue = self.selected_issue()
        if issue:
            self.issue_selected_signal.emit(issue.raw_start, issue.raw_end)

    def _emit_for_selection(self, signal):
        issue = self.selected_issue()
        if issue:
            signal.emit(issue)

    # ------------------------------------------------------------------

    def go_to_next_issue(self, step: int = 1):
        visible = self._visible_issues()
        if not visible:
            return

        current = self.table.currentRow()
        next_row = (current + step) % len(visible) if current >= 0 else (0 if step > 0 else len(visible) - 1)
        self.table.selectRow(next_row)
        self.table.setFocus()

    def accept_selected(self):
        if self.btn_accept.isEnabled():
            self._emit_for_selection(self.accept_requested_signal)
