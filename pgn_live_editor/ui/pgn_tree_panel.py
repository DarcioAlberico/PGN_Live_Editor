"""Arvore de lances ao lado do texto bruto.

Um item por lance; as variantes penduradas no lance de que sao alternativa,
recolhiveis. E a vista em que variante dentro de variante se ve sem ler nada
-- e ela mora **junto do editor**, e nao do outro lado da janela, porque a
pergunta que ela responde ("onde estou?") aparece enquanto se digita.

A cor e a mesma do realce do texto (`theme.DEPTH_COLORS`), pelo mesmo motivo
de sempre: duas vistas da mesma partida discordando de cor custam mais do que
valem. Lance corrigido, duvidoso ou nao entendido mantem a **sua** cor, como
no editor.

A sincronia e nos dois sentidos e tem uma trava: mover o cursor seleciona o
lance, clicar no lance move o cursor, e nenhum dos dois pode disparar o
outro de volta.

**A arvore nasce de novo a cada reanalise, mas nao se comporta como se
nascesse.** Reconstruir e mais barato do que descobrir o que mudou no texto,
so que uma arvore que volta ao estado inicial a cada tecla e inutil: recolhe a
variante que se estava lendo e rola para outro lugar no meio de uma frase. O
que o usuario abriu, fechou, selecionou e onde deixou a rolagem atravessa a
reconstrucao; ramo que nasceu agora -- porque se acabou de digitar a variante
-- e que, por isso, nao tem estado guardado, abre pela regra do padrao.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

from PyQt6.QtCore import Qt
from PyQt6.QtCore import pyqtSignal as Signal
from PyQt6.QtGui import QBrush, QColor, QFont
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.parser import GameAST
from ..core.pgn_tree import (
    MoveItem,
    VariationItem,
    build_pgn_tree,
    count_moves,
    count_variations,
    find_move_at,
)
from ..services.preferences_service import THEME_DARK
from .theme import color_for_depth, palette_of

_SPAN_ROLE = Qt.ItemDataRole.UserRole
_ID_ROLE = Qt.ItemDataRole.UserRole + 1
#: Identidade do item **entre reconstrucoes**. Para um lance e o `node_id`;
#: para uma variante, o rotulo do livro (`B`, `C1`), que e unico na partida.
_KEY_ROLE = Qt.ItemDataRole.UserRole + 2

#: Ate que profundidade a arvore nasce aberta. A linha principal e o primeiro
#: nivel de variante sao o que se olha o tempo todo; do segundo em diante,
#: abrir tudo enche a tela com o que nao se procurou.
DEFAULT_EXPANDED_DEPTH = 1

#: Recuo por nivel. Menor do que o padrao do Qt de proposito: um livro de
#: repertorio chega a quatro ou cinco niveis, e com o recuo padrao o quinto
#: nivel comeca onde a coluna de comentario devia estar.
INDENT_PIXELS = 14


@dataclass
class _TreeState:
    """O que atravessa uma reconstrucao da arvore."""

    known: set[str] = field(default_factory=set)
    expanded: set[str] = field(default_factory=set)
    current: str | None = None
    scroll: int = 0


class PgnTreePanel(QWidget):
    move_selected_signal = Signal(int, int)
    #: `(node_id, antes)` -- o lance escolhido deve receber o comentario que
    #: esta na area de transferencia, antes ou depois dele.
    comment_paste_requested_signal = Signal(str, bool)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)

        self.theme = THEME_DARK
        self._ast = GameAST()
        self._items: list[MoveItem] = []
        self._by_node_id: dict[str, QTreeWidgetItem] = {}
        self._syncing = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)

        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        self.lbl_summary = QLabel("Árvore do PGN")
        self.lbl_summary.setStyleSheet("font-weight: bold;")
        bar.addWidget(self.lbl_summary, 1)

        self.btn_paste_before = QPushButton("Colar antes")
        self.btn_paste_before.setToolTip(
            "Cola a área de transferência como comentário antes do lance selecionado (Ctrl+Alt+A)"
        )
        self.btn_paste_before.clicked.connect(lambda: self._request_comment(before=True))
        bar.addWidget(self.btn_paste_before)

        self.btn_paste_after = QPushButton("Colar depois")
        self.btn_paste_after.setToolTip(
            "Cola a área de transferência como comentário depois do lance selecionado (Ctrl+Alt+D)"
        )
        self.btn_paste_after.clicked.connect(lambda: self._request_comment(before=False))
        bar.addWidget(self.btn_paste_after)

        self.btn_expand = QPushButton("Expandir")
        self.btn_expand.setToolTip("Abre ou fecha todas as variantes")
        self.btn_expand.clicked.connect(self._toggle_expansion)
        bar.addWidget(self.btn_expand)
        layout.addLayout(bar)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels(["Lance", "Comentário"])
        self.tree.setUniformRowHeights(True)
        self.tree.setAlternatingRowColors(True)
        self.tree.setIndentation(INDENT_PIXELS)
        # A linha inteira acende, e nao so a celula do lance: e nela que os
        # gestos de colar comentario agem, e "que lance esta escolhido" precisa
        # ser visivel sem procurar.
        self.tree.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tree.setAllColumnsShowFocus(True)
        self.tree.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.tree.itemClicked.connect(self._on_item_clicked)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._open_context_menu)

        header = self.tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.tree.headerItem().setToolTip(0, "Número, lance e símbolo de avaliação")
        self.tree.headerItem().setToolTip(
            1,
            "O comentário do lance. A dica de cada linha diz o que está antes e o que está depois dele;\n"
            "o botão direito cola a área de transferência num dos dois lados.",
        )
        layout.addWidget(self.tree, 1)

    # ------------------------------------------------------------------

    def set_theme(self, theme: str):
        """A cor de cada item e decidida na construcao: trocar de tema
        reconstroi, que e mais barato do que percorrer a arvore atras de
        quem mudou."""
        self.theme = theme
        self.set_game(self._ast)

    # ------------------------------------------------------------------

    def set_game(self, ast: GameAST):
        state = self._capture_state()

        self._ast = ast
        self._items = build_pgn_tree(ast)
        self._by_node_id = {}
        self.tree.clear()

        for item in self._items:
            self.tree.addTopLevelItem(self._build_move(item))

        moves = count_moves(self._items)
        variations = count_variations(self._items)
        self.lbl_summary.setText(
            "Árvore do PGN" if not moves else f"Árvore do PGN — {moves} lance(s), {variations} variante(s)"
        )
        self._restore_state(state)

    def _color_for(self, kind: str, depth: int) -> str:
        if kind == "valid":
            return color_for_depth(self.theme, depth)
        colors = palette_of(self.theme)
        return {"fixed": colors["fixed"], "review": colors["review"], "error": colors["error_text"]}[kind]

    def _comment_brush(self) -> QBrush:
        """Prosa em tom apagado: ela acompanha o lance, nao disputa com ele."""
        return QBrush(QColor(palette_of(self.theme)["muted"]))

    def _build_move(self, move: MoveItem) -> QTreeWidgetItem:
        color = self._color_for(move.kind, move.depth)
        node = QTreeWidgetItem([move.title, move.comment])
        node.setData(0, _SPAN_ROLE, (move.start_index, move.end_index, color))
        node.setData(0, _ID_ROLE, move.node_id)
        node.setData(0, _KEY_ROLE, move.node_id)
        node.setForeground(0, QBrush(QColor(color)))
        node.setForeground(1, self._comment_brush())
        if move.depth == 0:
            font = node.font(0)
            font.setWeight(QFont.Weight.Bold)
            node.setFont(0, font)

        detail = move.comment_detail
        if detail:
            # Nas duas colunas: quem passa o mouse pelo lance tambem quer saber
            # o que ele carrega, e a coluna de comentario vive elidida.
            node.setToolTip(0, detail)
            node.setToolTip(1, detail)

        self._by_node_id[move.node_id] = node
        for variation in move.variations:
            node.addChild(self._build_variation(variation))
        return node

    def _build_variation(self, variation: VariationItem) -> QTreeWidgetItem:
        color = color_for_depth(self.theme, variation.depth)
        node = QTreeWidgetItem([variation.title, variation.leading_comment])
        node.setData(0, _SPAN_ROLE, (variation.start_index, variation.end_index, color))
        node.setData(0, _KEY_ROLE, f"({variation.label})")
        # Colar comentario "no" cabecalho da variante e colar no primeiro lance
        # dela -- e o que "antes desta variante" quer dizer no texto.
        if variation.moves:
            node.setData(0, _ID_ROLE, variation.moves[0].node_id)
        node.setForeground(0, QBrush(QColor(color)))
        node.setForeground(1, self._comment_brush())
        # Italico: a linha de uma variante e um rotulo, nao um lance. Sem isto
        # ela se le como se fosse mais um lance da linha de cima.
        font = node.font(0)
        font.setItalic(True)
        node.setFont(0, font)
        node.setToolTip(0, f"{variation.preview}\n{variation.move_count} lance(s)")

        for move in variation.moves:
            node.addChild(self._build_move(move))
        return node

    # ------------------------------------------------------------------
    # Estado que atravessa a reconstrucao
    # ------------------------------------------------------------------

    def _iter_items(self) -> Iterator[tuple[QTreeWidgetItem, int]]:
        def visit(item: QTreeWidgetItem, level: int) -> Iterator[tuple[QTreeWidgetItem, int]]:
            yield (item, level)
            for index in range(item.childCount()):
                yield from visit(item.child(index), level + 1)

        for index in range(self.tree.topLevelItemCount()):
            yield from visit(self.tree.topLevelItem(index), 0)

    def _capture_state(self) -> _TreeState:
        state = _TreeState(scroll=self.tree.verticalScrollBar().value())
        current = self.tree.currentItem()
        state.current = current.data(0, _KEY_ROLE) if current is not None else None

        for item, _level in self._iter_items():
            key = item.data(0, _KEY_ROLE)
            if not key:
                continue
            state.known.add(key)
            if item.isExpanded():
                state.expanded.add(key)
        return state

    def _restore_state(self, state: _TreeState):
        current: QTreeWidgetItem | None = None

        for item, level in self._iter_items():
            key = item.data(0, _KEY_ROLE)
            if key in state.expanded:
                item.setExpanded(True)
            elif key in state.known:
                # Fechado de proposito -- continua fechado.
                item.setExpanded(False)
            else:
                item.setExpanded(level <= DEFAULT_EXPANDED_DEPTH)
            if key is not None and key == state.current:
                current = item

        if current is not None:
            self._syncing = True
            try:
                self.tree.setCurrentItem(current)
            finally:
                self._syncing = False

        self.tree.verticalScrollBar().setValue(state.scroll)

    # ------------------------------------------------------------------

    def _expand_to(self, depth: int):
        for item, level in self._iter_items():
            item.setExpanded(level <= depth)
        self.btn_expand.setText("Expandir")

    def _toggle_expansion(self):
        if self.btn_expand.text() == "Expandir":
            self.tree.expandAll()
            self.btn_expand.setText("Recolher")
        else:
            self._expand_to(DEFAULT_EXPANDED_DEPTH)

    def _on_item_clicked(self, item: QTreeWidgetItem, _column: int):
        if self._syncing:
            return
        span = item.data(0, _SPAN_ROLE)
        if span and span[1] > span[0]:
            self.move_selected_signal.emit(int(span[0]), int(span[1]))

    def select_at(self, position: int):
        """Segue o cursor do editor. Nao emite sinal: quem chamou ja sabe."""
        move = find_move_at(self._items, position)
        node = self._by_node_id.get(move.node_id) if move else None
        if node is None:
            return

        self._syncing = True
        try:
            self.tree.setCurrentItem(node)
            self.tree.scrollToItem(node)
        finally:
            self._syncing = False

    # ------------------------------------------------------------------
    # Colar comentario no lance escolhido
    # ------------------------------------------------------------------

    def selected_node_id(self, item: QTreeWidgetItem | None = None) -> str | None:
        """O lance em que os gestos de comentario agem."""
        target = item if item is not None else self.tree.currentItem()
        return target.data(0, _ID_ROLE) if target is not None else None

    def _request_comment(self, *, before: bool, item: QTreeWidgetItem | None = None):
        node_id = self.selected_node_id(item)
        if node_id:
            self.comment_paste_requested_signal.emit(node_id, before)

    def _open_context_menu(self, position):
        item = self.tree.itemAt(position)
        if item is None:
            return

        self.tree.setCurrentItem(item)
        # O rotulo de uma variante e a previa dela inteira: no menu, ele viraria
        # uma linha que atravessa a tela.
        label = item.text(0)
        if len(label) > 24:
            label = f"{label[:24].rstrip()}…"

        menu = QMenu(self)
        for text, before in (
            (f"Colar comentário antes de {label}", True),
            (f"Colar comentário depois de {label}", False),
        ):
            action = menu.addAction(text)
            action.triggered.connect(lambda _checked=False, side=before: self._request_comment(before=side, item=item))
        menu.exec(self.tree.viewport().mapToGlobal(position))
        menu.deleteLater()  # um menu por clique ficava na memoria (U-13)
