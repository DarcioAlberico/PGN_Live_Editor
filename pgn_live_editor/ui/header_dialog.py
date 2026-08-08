"""Cabecalhos da partida, com template por livro (SPEC 4.1 / fase 4).

Num livro, `Event`, `Site`, `Date` e `Annotator` sao os mesmos das 64 partidas.
Digita-los uma vez e reaproveita-los e a diferenca entre transcrever um livro e
transcrever a mesma linha de cabecalho 64 vezes.
"""

from __future__ import annotations

import json
from pathlib import Path

from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Cabecalhos editaveis, na ordem em que o PGN os espera.
EDITABLE_TAGS: tuple[tuple[str, str], ...] = (
    ("Event", "Evento"),
    ("Site", "Local"),
    ("Date", "Data (AAAA.MM.DD)"),
    ("Round", "Rodada"),
    ("White", "Brancas"),
    ("Black", "Pretas"),
    ("Result", "Resultado"),
    ("ECO", "ECO"),
    ("Annotator", "Comentarista"),
)

# O que vale para o livro inteiro, e nao para uma partida so.
BOOK_TAGS: tuple[str, ...] = ("Event", "Site", "Date", "Round", "Annotator")


def load_builtin_templates() -> dict[str, dict[str, str]]:
    try:
        payload = json.loads((DATA_DIR / "header_templates.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return {
        str(name): {str(tag): str(value) for tag, value in values.items()}
        for name, values in payload.items()
        if isinstance(values, dict)
    }


class HeaderDialog(QDialog):
    """Edita os cabecalhos **de uma partida** e o template do livro."""

    def __init__(
        self,
        headers: dict[str, str],
        book_template: dict[str, str] | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Cabeçalhos da partida")
        self.setModal(True)
        self.resize(520, 420)

        self._book_template = dict(book_template or {})
        self._save_as_template = False
        self._fields: dict[str, QLineEdit] = {}

        layout = QVBoxLayout(self)

        form = QFormLayout()
        for tag, label in EDITABLE_TAGS:
            field = QLineEdit(headers.get(tag, ""))
            self._fields[tag] = field
            form.addRow(label, field)
        layout.addLayout(form)

        fen = (headers.get("FEN") or "").strip()
        self.lbl_fen = QLabel(
            f"Posição inicial: {fen}" if fen else "Posição inicial: padrão (sem FEN)"
        )
        self.lbl_fen.setWordWrap(True)
        self.lbl_fen.setStyleSheet("color: #9aa6b8;")
        layout.addWidget(self.lbl_fen)

        template_row = QHBoxLayout()
        template_row.addWidget(QLabel("Template:"))

        self.combo_templates = QComboBox()
        self._templates = load_builtin_templates()
        if self._book_template:
            self._templates = {"Template deste livro": dict(self._book_template)} | self._templates
        self.combo_templates.addItems(self._templates.keys())
        self.combo_templates.setEnabled(bool(self._templates))
        template_row.addWidget(self.combo_templates, 1)

        btn_apply = QPushButton("Aplicar")
        btn_apply.setToolTip("Preenche evento, local, data, rodada e comentarista")
        btn_apply.clicked.connect(self._apply_template)
        btn_apply.setEnabled(bool(self._templates))
        template_row.addWidget(btn_apply)

        btn_save = QPushButton("Guardar como template do livro")
        btn_save.setToolTip("As proximas partidas deste projeto já nascem com estes valores")
        btn_save.clicked.connect(self._remember_template)
        template_row.addWidget(btn_save)

        layout.addLayout(template_row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # ------------------------------------------------------------------

    def _apply_template(self):
        template = self._templates.get(self.combo_templates.currentText(), {})
        for tag, value in template.items():
            field = self._fields.get(tag)
            # Um template preenche o que esta vazio; nao apaga o que ja foi
            # digitado para esta partida.
            if field is not None and value and not field.text().strip():
                field.setText(value)

    def _remember_template(self):
        self._save_as_template = True
        self._book_template = self.book_template_values()
        self.accept()

    # ------------------------------------------------------------------

    def values(self) -> dict[str, str]:
        """Cabecalhos preenchidos. Campo vazio nao vira cabecalho vazio."""
        return {
            tag: field.text().strip()
            for tag, field in self._fields.items()
            if field.text().strip()
        }

    def book_template_values(self) -> dict[str, str]:
        values = self.values()
        return {tag: values[tag] for tag in BOOK_TAGS if tag in values}

    @property
    def should_save_template(self) -> bool:
        return self._save_as_template
