"""Dialogo de limpeza do texto colado do PDF, com previa antes de aplicar."""

from __future__ import annotations

from PyQt6.QtGui import QFontDatabase
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..core.paste_cleanup import CleanupOptions, clean_pdf_text

OPTION_LABELS: tuple[tuple[str, str], ...] = (
    ("dehyphenate", "Remontar palavras cortadas no fim da linha (ma-\\nnobra)"),
    ("join_wrapped_lines", "Juntar linhas quebradas pela largura da página"),
    ("remove_page_numbers", "Remover números de página"),
    ("remove_repeated_headers", "Remover cabeçalhos e rodapés que se repetem"),
    ("remove_footnote_marks", "Remover marcas de nota de rodape coladas em lances"),
    ("remove_invisible", "Remover caracteres invisiveis do PDF"),
    ("collapse_whitespace", "Normalizar espacos"),
)


class CleanupDialog(QDialog):
    def __init__(self, source_text: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.source_text = source_text
        self.setWindowTitle("Limpar texto colado")
        self.resize(760, 560)

        layout = QVBoxLayout(self)

        intro = QLabel(
            "Escolha o que limpar. A previa abaixo mostra o resultado; nada e aplicado "
            "até você confirmar, e o Ctrl+Z desfaz tudo de uma vez."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.checkboxes: dict[str, QCheckBox] = {}
        defaults = CleanupOptions()
        for key, label in OPTION_LABELS:
            checkbox = QCheckBox(label)
            checkbox.setChecked(getattr(defaults, key))
            checkbox.toggled.connect(self._refresh_preview)
            self.checkboxes[key] = checkbox
            layout.addWidget(checkbox)

        self.lbl_summary = QLabel("")
        self.lbl_summary.setStyleSheet("font-weight: bold; padding-top: 6px;")
        layout.addWidget(self.lbl_summary)

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

    def options(self) -> CleanupOptions:
        return CleanupOptions(**{key: checkbox.isChecked() for key, checkbox in self.checkboxes.items()})

    def _refresh_preview(self):
        report = clean_pdf_text(self.source_text, self.options())
        self.lbl_summary.setText(report.summary())
        self.preview.setPlainText(report.text)
