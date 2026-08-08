"""Editor do dicionario de OCR (SPEC 5.2).

A tabela mostra as tres camadas juntas, na ordem em que o programa as consulta,
com a coluna **Alcance** dizendo quantos tokens do texto aberto agora cada regra
pega. E o "teste ao vivo": antes de gravar uma regra, ver onde ela encosta neste
livro -- que e a unica forma de perceber que `1 -> l` no escopo livre vai mexer
em trinta lances em vez de um.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..core.substitutions import (
    LAYER_BASE,
    LAYER_GLOBAL,
    LAYER_LABEL,
    LAYER_PROJECT,
    SCOPE_FREE,
    SCOPE_LABEL,
    SCOPES,
)
from ..services.dictionary_service import DictionaryService

LAYER_COLOR = {
    LAYER_BASE: "#8a8f98",
    LAYER_GLOBAL: "#9ce1a5",
    LAYER_PROJECT: "#f0c05a",
}


class DictionaryDialog(QDialog):
    def __init__(self, service: DictionaryService, document_text: str = "", parent=None):
        super().__init__(parent)
        self.service = service
        self.document_text = document_text

        self.setWindowTitle("Dicionário de OCR")
        self.resize(760, 520)

        layout = QVBoxLayout(self)

        intro = QLabel(
            "As três camadas na ordem em que são consultadas. A de baixo vence:\n"
            "embutido → meu dicionário → deste projeto."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Camada", "Escopo", "Encontra", "Vira", "Alcance"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemSelectionChanged.connect(self._update_buttons)
        layout.addWidget(self.table)

        form = QHBoxLayout()
        self.txt_pattern = QLineEdit()
        self.txt_pattern.setPlaceholderText("o que o OCR escreveu (ex.: 4Jf3)")
        self.txt_replacement = QLineEdit()
        self.txt_replacement.setPlaceholderText("o que deveria ser (ex.: Nf3)")

        self.cmb_scope = QComboBox()
        for scope in SCOPES:
            self.cmb_scope.addItem(SCOPE_LABEL[scope], scope)
        self.cmb_scope.currentIndexChanged.connect(self._update_scope_warning)

        self.cmb_layer = QComboBox()
        self.cmb_layer.addItem(LAYER_LABEL[LAYER_PROJECT], LAYER_PROJECT)
        self.cmb_layer.addItem(LAYER_LABEL[LAYER_GLOBAL], LAYER_GLOBAL)

        self.btn_add = QPushButton("Gravar")
        self.btn_add.clicked.connect(self._add_entry)

        form.addWidget(QLabel("Encontra:"))
        form.addWidget(self.txt_pattern, 2)
        form.addWidget(QLabel("Vira:"))
        form.addWidget(self.txt_replacement, 2)
        form.addWidget(self.cmb_scope)
        form.addWidget(self.cmb_layer)
        form.addWidget(self.btn_add)
        layout.addLayout(form)

        self.lbl_warning = QLabel("")
        self.lbl_warning.setWordWrap(True)
        self.lbl_warning.setStyleSheet("color: #f0c05a;")
        layout.addWidget(self.lbl_warning)

        actions = QHBoxLayout()
        self.btn_remove = QPushButton("Remover")
        self.btn_remove.clicked.connect(self._remove_selected)
        self.btn_promote = QPushButton("Valer para todos os livros")
        self.btn_promote.setToolTip("Move a regra do projeto para o meu dicionário")
        self.btn_promote.clicked.connect(self._promote_selected)
        actions.addWidget(self.btn_remove)
        actions.addWidget(self.btn_promote)
        actions.addStretch()
        layout.addLayout(actions)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.accept)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

        self._rows: list = []
        self.refresh()
        self._update_scope_warning()

    # ------------------------------------------------------------------

    def refresh(self):
        uses = self.service.preview(self.document_text)
        merged_keys = {entry.key for entry in self.service.merged()}

        rows = []
        for layer in (LAYER_BASE, LAYER_GLOBAL, LAYER_PROJECT):
            for entry in sorted(self.service.layer(layer), key=lambda item: (item.scope, item.pattern)):
                use = uses.get(entry.key)
                # Uma regra de camada de baixo pode ter sido substituida por
                # outra de cima: a de baixo continua listada, mas apagada.
                overridden = entry.key in merged_keys and entry not in self.service.merged().entries
                rows.append((entry, use.count if use else 0, overridden))

        self._rows = rows
        self.table.setRowCount(len(rows))
        for row, (entry, count, overridden) in enumerate(rows):
            cells = [
                QTableWidgetItem(LAYER_LABEL.get(entry.layer, entry.layer)),
                QTableWidgetItem(SCOPE_LABEL.get(entry.scope, entry.scope)),
                QTableWidgetItem(entry.pattern),
                QTableWidgetItem(entry.replacement),
                QTableWidgetItem(str(count) if count else "-"),
            ]
            cells[0].setForeground(QColor(LAYER_COLOR.get(entry.layer, "#cccccc")))
            cells[4].setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            if overridden:
                for cell in cells:
                    font = cell.font()
                    font.setStrikeOut(True)
                    cell.setFont(font)
                cells[0].setToolTip("Uma camada mais especifica substituiu esta regra")

            for column, cell in enumerate(cells):
                self.table.setItem(row, column, cell)

        self._update_buttons()

    def _selected_row(self):
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows:
            return None
        index = rows[0].row()
        return self._rows[index] if 0 <= index < len(self._rows) else None

    def _update_buttons(self):
        selected = self._selected_row()
        entry = selected[0] if selected else None
        editable = bool(entry and entry.layer in (LAYER_GLOBAL, LAYER_PROJECT))
        self.btn_remove.setEnabled(editable)
        self.btn_promote.setEnabled(bool(entry and entry.layer == LAYER_PROJECT))

    def _update_scope_warning(self):
        if self.cmb_scope.currentData() == SCOPE_FREE:
            self.lbl_warning.setText(
                "Escopo livre troca o trecho em qualquer lugar do lance. Dentro de um token de "
                "notação isso e seguro -- prosa nunca chega aqui -- mas e o escopo que mais "
                "produz lance legal errado. Confira a coluna Alcance antes de gravar."
            )
        else:
            self.lbl_warning.setText("")

    # ------------------------------------------------------------------

    def _add_entry(self):
        pattern = self.txt_pattern.text().strip()
        replacement = self.txt_replacement.text().strip()
        if not pattern:
            return

        self.service.learn(
            pattern=pattern,
            replacement=replacement,
            scope=self.cmb_scope.currentData(),
            layer=self.cmb_layer.currentData(),
        )
        self.txt_pattern.clear()
        self.txt_replacement.clear()
        self.refresh()

    def _remove_selected(self):
        selected = self._selected_row()
        if not selected:
            return
        entry = selected[0]
        self.service.forget(entry.key, entry.layer)
        self.refresh()

    def _promote_selected(self):
        selected = self._selected_row()
        if not selected:
            return
        self.service.move_to_global(selected[0].key)
        self.refresh()
