"""Editor de comentário PGN com NAGs e prévia dos comandos visuais."""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
)

from ..core.annotations import VisualAnnotations, apply_annotations
from .nag_bar import NagBar


class CommentEditorDialog(QDialog):
    def __init__(self, text: str, annotations: VisualAnnotations, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Editar comentário")
        self.resize(680, 420)
        self._annotations = annotations

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Texto do comentário — os comandos visuais permanecem no fim do comentário."))
        self.editor = QTextEdit()
        self.editor.setPlainText(text)
        self.editor.setAcceptRichText(False)
        self.editor.textChanged.connect(self._refresh_preview)
        layout.addWidget(self.editor, 1)

        self.nag_bar = NagBar()
        self.nag_bar.symbol_requested_signal.connect(self._insert_symbol)
        layout.addWidget(self.nag_bar)
        self.lbl_visual = QLabel(
            "Anotações visuais: desenhe setas e marque casas no tabuleiro antes de abrir este editor."
        )
        self.lbl_visual.setWordWrap(True)
        layout.addWidget(self.lbl_visual)

        self.preview = QTextBrowser()
        self.preview.setMaximumHeight(90)
        self.preview.setOpenExternalLinks(False)
        layout.addWidget(self.preview)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._refresh_preview()

    def _insert_symbol(self, value: str):
        self.editor.insertPlainText(" " + value)
        self.editor.setFocus()

    def _refresh_preview(self):
        value = apply_annotations(self.editor.toPlainText(), self._annotations)
        self.preview.setPlainText("Prévia PGN:\n{" + value + "}" if value else "Prévia PGN:\n{}")

    def comment_text(self) -> str:
        return apply_annotations(self.editor.toPlainText(), self._annotations)
