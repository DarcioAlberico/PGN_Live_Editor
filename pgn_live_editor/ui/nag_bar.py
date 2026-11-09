"""Rodape de simbolos: todos os glifos e NAG de xadrez, a um clique.

A paleta de `Ctrl+D` cobria treze simbolos e exigia abrir um dialogo. Quem
transcreve um livro anota `!`, `?!`, `⩲`, `±` a cada dois lances -- o custo
nao esta em achar o simbolo, esta em abrir e fechar a janela. Entao os
simbolos moram no rodape do editor, sempre visiveis, agrupados como a chave
de simbolos de um livro: lance, avaliacao, dinamica, intencao.

O interruptor `$n` troca o que se insere: o glifo (que e o que se le) ou o
codigo numerico (que e o que o PGN guarda). Os dois sao equivalentes na
exportacao -- `render_game_movetext` converte o glifo no `$n` canonico --
mas quem revisa um PGN de outra pessoa quer digitar como o arquivo esta.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtCore import pyqtSignal as Signal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..core.nag_table import Nag, nags_by_group

BUTTON_WIDTH = 38
BUTTON_HEIGHT = 26


class NagBar(QWidget):
    """Barra horizontal de simbolos; emite o texto a inserir no cursor."""

    symbol_requested_signal = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)

        self._buttons: list[tuple[QPushButton, Nag]] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 2, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setFixedHeight(BUTTON_HEIGHT + 26)

        content = QWidget()
        row = QHBoxLayout(content)
        row.setContentsMargins(2, 2, 2, 2)
        row.setSpacing(10)

        glyph_font = QFont()
        glyph_font.setPointSize(11)

        for group, nags in nags_by_group():
            row.addWidget(self._build_group(group, nags, glyph_font))
            row.addWidget(self._separator())

        self.chk_numeric = QCheckBox("$n")
        self.chk_numeric.setToolTip(
            "Inserir o código numérico ($16) em vez do glifo (±).\nOs dois viram o mesmo NAG na exportação."
        )
        self.chk_numeric.toggled.connect(self._refresh_tooltips)
        row.addWidget(self.chk_numeric)
        row.addStretch()

        scroll.setWidget(content)
        outer.addWidget(scroll)
        self._refresh_tooltips()

    # ------------------------------------------------------------------

    def _separator(self) -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.VLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        return line

    def _build_group(self, title: str, nags: list[Nag], glyph_font: QFont) -> QWidget:
        box = QWidget()
        column = QVBoxLayout(box)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(1)

        caption = QLabel(title)
        caption.setStyleSheet("font-size: 9px; opacity: 0.7;")
        column.addWidget(caption)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(2)

        for nag in nags:
            button = QPushButton(nag.glyph)
            button.setFont(glyph_font)
            button.setFixedSize(BUTTON_WIDTH, BUTTON_HEIGHT)
            button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            button.clicked.connect(lambda _checked=False, item=nag: self._emit(item))
            buttons.addWidget(button)
            self._buttons.append((button, nag))

        column.addLayout(buttons)
        return box

    def _emit(self, nag: Nag):
        self.symbol_requested_signal.emit(self.text_for(nag))

    def text_for(self, nag: Nag) -> str:
        """O que vai para o editor. `N` e `RR` so existem na forma numerica:
        um `N` solto no meio da notacao seria lido como cavalo."""
        if self.chk_numeric.isChecked() or not nag.safe_glyph:
            return nag.code
        return nag.glyph

    def _refresh_tooltips(self):
        for button, nag in self._buttons:
            inserted = self.text_for(nag)
            suffix = "" if inserted == nag.glyph else f"  (insere {inserted})"
            button.setToolTip(f"{nag.name} — {nag.code}{suffix}")
