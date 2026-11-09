"""Painel da arvore indentada de variantes.

Uma linha por linha de jogo, recolhivel, rotulada como o livro rotula. E a
vista que o preview de PGN nao consegue dar: no texto corrido, uma variante
de terceiro nivel e um par de parenteses no meio de duzentos lances; aqui
ela e uma linha com dois niveis de recuo e um rotulo.

Clicar leva o cursor ao primeiro lance da linha no texto bruto -- que e o
que fecha o ciclo "achei no livro, achei no editor".
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtCore import pyqtSignal as Signal
from PyQt6.QtGui import QBrush, QColor, QFont, QGuiApplication
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.outline import OutlineLine, build_outline, render_outline_text
from ..core.parser import GameAST
from ..services.preferences_service import THEME_DARK
from .theme import palette_of

_SPAN_ROLE = Qt.ItemDataRole.UserRole


class OutlinePanel(QWidget):
    line_selected_signal = Signal(int, int)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)

        self._root: OutlineLine | None = None
        self._ast = GameAST()
        self.theme = THEME_DARK

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        bar = QHBoxLayout()
        self.lbl_summary = QLabel("Nenhuma variante.")
        self.lbl_summary.setStyleSheet("font-weight: bold;")
        bar.addWidget(self.lbl_summary, 1)

        self.btn_expand = QPushButton("Expandir tudo")
        self.btn_expand.clicked.connect(self._toggle_expansion)
        bar.addWidget(self.btn_expand)

        self.btn_copy = QPushButton("Copiar árvore")
        self.btn_copy.setToolTip("Copia a árvore indentada em texto, para conferir ao lado da página impressa")
        self.btn_copy.clicked.connect(self.copy_outline)
        bar.addWidget(self.btn_copy)
        layout.addLayout(bar)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["Variante", "Lances", "Parte de"])
        self.tree.setRootIsDecorated(True)
        self.tree.setUniformRowHeights(True)
        self.tree.itemClicked.connect(self._on_item_clicked)
        header = self.tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.tree, 1)

    # ------------------------------------------------------------------

    def set_theme(self, theme: str):
        self.theme = theme
        self.set_game(self._ast)

    def set_game(self, ast: GameAST):
        self._ast = ast
        self._root = build_outline(ast)
        self.tree.clear()

        root_item = self._build_item(self._root)
        self.tree.addTopLevelItem(root_item)
        root_item.setExpanded(True)
        for index in range(root_item.childCount()):
            root_item.child(index).setExpanded(True)

        total = self._count(self._root) - 1
        self.lbl_summary.setText("Nenhuma variante." if total <= 0 else f"{total} variante(s) nesta partida")

    def _count(self, line: OutlineLine) -> int:
        return 1 + sum(self._count(child) for child in line.children)

    def _build_item(self, line: OutlineLine) -> QTreeWidgetItem:
        preview = line.preview or "(vazia)"
        item = QTreeWidgetItem([f"{line.title}  {preview}", str(line.move_count), line.anchor])
        item.setData(0, _SPAN_ROLE, (line.start_index, line.end_index))
        item.setToolTip(0, preview)

        if line.depth == 0:
            font = item.font(0)
            font.setWeight(QFont.Weight.Bold)
            item.setFont(0, font)
        else:
            # Quanto mais fundo, mais apagada -- e a mesma pista que o realce
            # do editor da, e serve para ler a profundidade de relance. A cor
            # vem da paleta do tema: cinza-claro sobre fundo branco nao se le.
            colors = palette_of(self.theme)
            base = QColor(colors["move_variation"])
            muted = QColor(colors["muted"])
            fade = min(0.28 * (line.depth - 1), 0.8)
            blended = QColor(
                round(base.red() + (muted.red() - base.red()) * fade),
                round(base.green() + (muted.green() - base.green()) * fade),
                round(base.blue() + (muted.blue() - base.blue()) * fade),
            )
            item.setForeground(0, QBrush(blended))

        for child in line.children:
            item.addChild(self._build_item(child))
        return item

    def _on_item_clicked(self, item: QTreeWidgetItem, _column: int):
        span = item.data(0, _SPAN_ROLE)
        if span and span[1] > span[0]:
            self.line_selected_signal.emit(int(span[0]), int(span[1]))

    def _toggle_expansion(self):
        expand = self.btn_expand.text().startswith("Expandir")
        self.tree.expandAll() if expand else self.tree.collapseAll()
        self.btn_expand.setText("Recolher tudo" if expand else "Expandir tudo")

    def copy_outline(self) -> str:
        if self._root is None:
            return ""
        text = render_outline_text(self._root)
        QGuiApplication.clipboard().setText(text)
        return text
