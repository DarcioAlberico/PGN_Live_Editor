"""Dialogo de conversao de texto de livro, com previa e relatorio.

Mesmo desenho do dialogo de limpeza, pelo mesmo motivo: a conversao mexe na
estrutura do documento, e mexer na estrutura sem ver o resultado antes e o
tipo de coisa que so se descobre errada trinta lances depois. As notas do
relatorio aparecem separadas das contagens -- elas sao as decisoes que o
programa tomou sem certeza, e sao o que vale reler.
"""

from __future__ import annotations

from PyQt6.QtGui import QFontDatabase
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..core.book_import import (
    DIAGRAM_COMMENT,
    DIAGRAM_REMOVE,
    BookImportOptions,
    convert_book_text,
)

OPTION_LABELS: tuple[tuple[str, str], ...] = (
    ("map_symbols", "Converter os símbolos da fonte do livro (² ³ µ +– © ‚) em avaliações"),
    ("strip_furniture", "Descartar cabeçalho corrente, número de página e 'Show/Hide Solution'"),
    ("labels_to_variations", "Transformar os rótulos A) B1) B1.2) em variantes aninhadas"),
    ("brackets_to_variations", "Transformar [ ... ; ... ] em variantes"),
    ("prose_to_comments", "Envolver a prosa em { } para não perdê-la no PGN"),
    ("regressions_to_variations", "Ler repetição de número de lance como alternativa (8.O-O-O … 8.f4!?)"),
    ("exercises_to_games", "Separar cada exercício (□ 1.? / ■ 1...?) numa partida"),
    ("repair_broken_hyphens", "Remontar hífens que o extrator de PDF quebrou"),
)

DIAGRAM_CHOICES: tuple[tuple[str, str], ...] = (
    (DIAGRAM_REMOVE, "descartar"),
    (DIAGRAM_COMMENT, "virar marcador {[#]}"),
)


class BookImportDialog(QDialog):
    def __init__(self, source_text: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.source_text = source_text
        self.setWindowTitle("Converter texto de livro")
        self.resize(880, 640)

        layout = QVBoxLayout(self)

        intro = QLabel(
            "Converte a página de um livro de repertório no movetext que o editor analisa: "
            "rótulos de variante viram parênteses, colchetes viram variantes, a prosa vira comentário. "
            "Nada é aplicado até você confirmar, e o Ctrl+Z desfaz tudo de uma vez."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.checkboxes: dict[str, QCheckBox] = {}
        defaults = BookImportOptions()
        for key, label in OPTION_LABELS:
            checkbox = QCheckBox(label)
            checkbox.setChecked(getattr(defaults, key))
            checkbox.toggled.connect(self._refresh_preview)
            self.checkboxes[key] = checkbox
            layout.addWidget(checkbox)

        diagram_row = QHBoxLayout()
        diagram_row.addWidget(QLabel("Legenda 'Position after: …':"))
        self.cmb_diagrams = QComboBox()
        for value, label in DIAGRAM_CHOICES:
            self.cmb_diagrams.addItem(label, value)
        self.cmb_diagrams.currentIndexChanged.connect(self._refresh_preview)
        diagram_row.addWidget(self.cmb_diagrams)
        diagram_row.addStretch()
        layout.addLayout(diagram_row)

        self.lbl_summary = QLabel("")
        self.lbl_summary.setWordWrap(True)
        self.lbl_summary.setStyleSheet("font-weight: bold; padding-top: 6px;")
        layout.addWidget(self.lbl_summary)

        self.lbl_notes = QLabel("")
        self.lbl_notes.setWordWrap(True)
        self.lbl_notes.setStyleSheet("padding-bottom: 4px;")
        layout.addWidget(self.lbl_notes)

        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        layout.addWidget(self.preview, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Aplicar")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._refresh_preview()

    def options(self) -> BookImportOptions:
        values = {key: checkbox.isChecked() for key, checkbox in self.checkboxes.items()}
        return BookImportOptions(diagram_marks=self.cmb_diagrams.currentData(), **values)

    def converted_text(self) -> str:
        return self.preview.toPlainText()

    def _refresh_preview(self):
        report = convert_book_text(self.source_text, self.options())
        self.lbl_summary.setText(report.summary())
        if report.notes:
            self.lbl_notes.setText("Decisões sem certeza:\n• " + "\n• ".join(report.notes))
        else:
            self.lbl_notes.setText("Nenhuma decisão duvidosa.")
        self.preview.setPlainText(report.text)
