
import chess
import chess.svg
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtCore import pyqtSignal as Signal
from PyQt6.QtCore import pyqtSlot as Slot
from PyQt6.QtGui import QGuiApplication, QResizeEvent
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ..core.annotations import (
    ANNOTATION_COLORS,
    DEFAULT_COLOR,
    Arrow,
    SquareMark,
    VisualAnnotations,
    parse_annotations,
)
from ..core.move_tree import (
    MoveEntry,
    build_entry_maps,
    build_move_entries,
    find_entry_for_insertion_position,
    render_game_html,
    resolve_selection_key,
)
from ..core.parser import GameAST
from ..services.clipboard_service import COPY_FORMATS, copy_text
from .board_view import InteractiveBoardView
from .promotion_dialog import PromotionDialog, wants_default_promotion
from .variant_builder_panel import VariantBuilderPanel


class ChessPanel(QWidget):
    current_move_span_signal = Signal(int, int)
    move_focused_signal = Signal(int, int)
    move_insert_requested_signal = Signal(str, bool, int)
    excerpt_copied_signal = Signal(str)
    start_fen_requested_signal = Signal()
    #: `(node_id, VisualAnnotations)` -- a janela grava no comentario do lance.
    annotations_changed_signal = Signal(str, object)

    def __init__(self):
        super().__init__()

        self.game_ast = GameAST()
        self.start_fen = chess.STARTING_FEN
        self.entries: list[MoveEntry] = []
        self.entry_by_node_id: dict[str, MoveEntry] = {}
        self.current_entry_index = -1
        self.current_selection_key: str | None = None
        self.current_last_move = None
        self.board = chess.Board()
        self.board_flipped = False
        # Setas e casas coloridas do lance atual (SPEC 4.4).
        self.annotations = VisualAnnotations([], [])
        self.selected_square: chess.Square | None = None
        self.input_mode: str | None = None
        self.annotate_mode = False
        self.input_origin_label: str | None = None
        self._suppress_next_editor_sync = False

        layout = QVBoxLayout(self)

        controls = QHBoxLayout()
        self.btn_prev = QPushButton("<")
        self.btn_next = QPushButton(">")
        self.btn_prev.clicked.connect(self.go_prev)
        self.btn_next.clicked.connect(self.go_next)

        self.lbl_status = QLabel("Start Position")
        self.lbl_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_meta = QLabel("0 moves | 0 issues")
        self.lbl_meta.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        # Fragmento de livro que parte de um diagrama: montar a posicao no
        # tabuleiro e um caminho muito mais curto do que escrever a FEN a mao.
        self.btn_start_fen = QPushButton("Posição inicial")
        self.btn_start_fen.setToolTip("Grava a posição do tabuleiro como [FEN] inicial desta partida")
        self.btn_start_fen.clicked.connect(self.start_fen_requested_signal.emit)

        # O botao direito ja tem dono (entrar em variante). Anotar visualmente e
        # um modo a parte, e explicito, em vez de mais um significado escondido
        # no mesmo clique.
        self.btn_annotate = QPushButton("Setas")
        self.btn_annotate.setCheckable(True)
        self.btn_annotate.setToolTip(
            "Modo anotação: arrastar com o botao direito desenha seta, clicar marca a casa. "
            "Alt vermelho, Ctrl amarelo, Shift azul. Sai como [%cal] e [%csl] no comentário."
        )
        self.btn_annotate.toggled.connect(self._on_annotate_toggled)

        controls.addWidget(self.btn_prev)
        controls.addWidget(self.lbl_status, 1)
        controls.addWidget(self.lbl_meta)
        controls.addWidget(self.btn_annotate)
        controls.addWidget(self.btn_start_fen)
        controls.addWidget(self.btn_next)
        layout.addLayout(controls)

        self.content_splitter = QSplitter(Qt.Orientation.Vertical)
        layout.addWidget(self.content_splitter, 1)

        self.board_view = InteractiveBoardView()
        self.board_view.setMinimumHeight(380)
        self.board_view.square_pressed_signal.connect(self._on_board_square_pressed)
        self.board_view.square_released_signal.connect(self._on_board_square_released)
        self.board_view.drag_finished_signal.connect(self._on_board_drag_finished)
        self.content_splitter.addWidget(self.board_view)

        preview_container = QWidget()
        preview_layout = QVBoxLayout(preview_container)
        preview_layout.setContentsMargins(0, 0, 0, 0)

        self.lbl_preview = QLabel("PGN Preview")
        self.lbl_preview.setStyleSheet("font-weight: bold; padding: 4px 0;")
        preview_layout.addWidget(self.lbl_preview)

        self.pgn_view = QTextBrowser()
        self.pgn_view.setOpenLinks(False)
        self.pgn_view.anchorClicked.connect(self._on_pgn_anchor_clicked)
        self.pgn_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.pgn_view.customContextMenuRequested.connect(self._show_copy_menu)
        preview_layout.addWidget(self.pgn_view, 1)

        self.variant_builder = VariantBuilderPanel()
        self.variant_builder.anchor_current_requested_signal.connect(self._anchor_variant_builder_to_current)
        self.variant_builder.anchor_previous_requested_signal.connect(self._anchor_variant_builder_to_previous)

        self.content_splitter.addWidget(preview_container)
        self.content_splitter.addWidget(self.variant_builder)
        self.content_splitter.setStretchFactor(0, 2)
        self.content_splitter.setStretchFactor(1, 1)
        self.content_splitter.setStretchFactor(2, 1)

        self.render_board()
        self._refresh_preview()
        self._update_controls()

    def resizeEvent(self, event: QResizeEvent):
        super().resizeEvent(event)
        self.render_board(self.current_last_move)

    def _board_svg_size(self) -> int:
        width = max(self.board_view.width(), 420)
        height = max(self.board_view.height(), 420)
        return max(280, min(width - 24, height - 24, 560))

    def flip_board(self):
        self.board_flipped = not self.board_flipped
        self.render_board(self.current_last_move)

    def render_board(self, last_move=None):
        self.current_last_move = last_move
        board_size = self._board_svg_size()
        self.board_view.set_board_geometry(board_size, flipped=self.board_flipped)
        svg = chess.svg.board(
            self.board,
            size=board_size,
            lastmove=last_move,
            orientation=chess.BLACK if self.board_flipped else chess.WHITE,
            coordinates=True,
            borders=False,
            fill=self._board_fill(),
            arrows=self._board_arrows(),
        ).encode('utf-8')
        self.board_view.set_svg(svg.decode('utf-8'))

    def _board_fill(self) -> dict[chess.Square, str]:
        fill: dict[chess.Square, str] = {}
        # As casas marcadas pelo livro entram primeiro: a selecao em curso e
        # mais importante e pode cobri-las.
        for mark in self.annotations.squares:
            fill[mark.square] = ANNOTATION_COLORS.get(mark.color, ANNOTATION_COLORS["G"]) + "88"

        if self.selected_square is not None:
            fill[self.selected_square] = "#f1c75b88"
            for target in self._legal_targets_for(self.selected_square):
                fill[target] = "#6ecf8b88"
        return fill

    def _board_arrows(self) -> list[chess.svg.Arrow]:
        return [
            chess.svg.Arrow(
                arrow.tail,
                arrow.head,
                color=ANNOTATION_COLORS.get(arrow.color, ANNOTATION_COLORS["G"]),
            )
            for arrow in self.annotations.arrows
        ]

    def _current_entry(self) -> MoveEntry | None:
        if 0 <= self.current_entry_index < len(self.entries):
            return self.entries[self.current_entry_index]
        return None

    def current_entry(self) -> MoveEntry | None:
        return self._current_entry()

    def go_first(self):
        """Home: volta a posicao inicial da partida."""
        self._reset_board_position()

    def go_last(self):
        """End: vai ao ultimo lance da linha que esta sendo percorrida."""
        if self.entries:
            self._apply_entry(self.entries[-1], focus_editor=True)

    def variation_owner_of(self, entry: MoveEntry):
        """(lance dono, indice da variante) do lance selecionado, ou (None, -1).

        O `node_id` alterna indice de lance e indice de variante -- `2.0.1` e o
        lance 1 da variante 0 do lance 2 da linha principal.
        """
        parts = [int(part) for part in entry.node_id.split(".")]
        if len(parts) < 3:
            return (None, -1)

        moves = self.game_ast.moves
        owner = None
        index = 0
        while index + 2 < len(parts):
            owner = moves[parts[index]]
            variation_index = parts[index + 1]
            moves = owner.variations[variation_index]
            index += 2

        return (owner, parts[-2])

    def _reset_board_position(self):
        self.current_entry_index = -1
        self.current_selection_key = None
        self.selected_square = None
        self.input_mode = None
        self.input_origin_label = None
        self.annotations = VisualAnnotations([], [])
        self.board.set_fen(self.start_fen)
        self.render_board(None)
        self._restore_status_label()
        self._refresh_preview()
        self._update_controls()
        self.current_move_span_signal.emit(-1, -1)

    def _update_controls(self):
        self.btn_prev.setEnabled(self.current_entry_index >= 0)
        self.btn_next.setEnabled(self.current_entry_index < len(self.entries) - 1)

        issue_count = len(self.game_ast.issues)
        self.lbl_meta.setText(f"{len(self.entries)} moves | {issue_count} issues")
        self.lbl_preview.setText("PGN Preview" if issue_count == 0 else f"PGN Preview | {issue_count} issue(s)")

    def _refresh_preview(self):
        current_entry = self._current_entry()
        current_node_id = current_entry.node_id if current_entry else None
        self.pgn_view.setHtml(render_game_html(self.game_ast, self.entries, current_node_id))
        if current_node_id:
            QTimer.singleShot(0, lambda node_id=current_node_id: self.pgn_view.scrollToAnchor(node_id))

    def _turn_marker(self) -> str:
        return f"{self.board.fullmove_number}..." if self.board.turn == chess.BLACK else f"{self.board.fullmove_number}."

    def _entry_label(self, entry: MoveEntry) -> str:
        label_parts = []
        if entry.node.number:
            label_parts.append(entry.node.number)
        elif entry.node.parent_fen:
            parent_board = chess.Board(entry.node.parent_fen)
            if parent_board.turn == chess.BLACK:
                label_parts.append(f"{parent_board.fullmove_number}...")
        label_parts.append(entry.node.display_san)
        if not entry.node.is_valid:
            label_parts.append("[INVALID]")
        return " ".join(label_parts)

    def _current_position_label(self, entry: MoveEntry) -> str:
        return f"após {self._entry_label(entry)}"

    def _restore_status_label(self):
        if self.input_mode == "variation":
            origin = self.input_origin_label or "lance atual"
            self.lbl_status.setText(f"Variante de {origin} | {self._turn_marker()}")
            return

        if self.input_mode == "current":
            self.lbl_status.setText(f"Digitacao pelo tabuleiro | {self._turn_marker()}")
            return

        current_entry = self._current_entry()
        if current_entry is None:
            self.lbl_status.setText("Start Position")
            return

        self.lbl_status.setText(self._entry_label(current_entry))

    def _anchor_variant_builder_to_current(self):
        current_entry = self._current_entry()
        if current_entry is None:
            self.variant_builder.set_anchor(self.start_fen, "posição inicial")
            return

        board = chess.Board(self.start_fen)
        for move in current_entry.path:
            if not move.is_valid:
                break
            try:
                board.push_san(move.display_san)
            except ValueError:
                break

        self.variant_builder.set_anchor(board.fen(), self._current_position_label(current_entry))

    def _anchor_variant_builder_to_previous(self):
        current_entry = self._current_entry()
        if current_entry is None:
            self.variant_builder.set_anchor(self.start_fen, "posição inicial")
            return

        board = chess.Board(self.start_fen)
        for move in current_entry.path[:-1]:
            if not move.is_valid:
                break
            try:
                board.push_san(move.display_san)
            except ValueError:
                break

        self.variant_builder.set_anchor(board.fen(), f"antes de {self._entry_label(current_entry)}")

    def _set_board_from_path(self, path) -> chess.Move | None:
        self.board.set_fen(self.start_fen)
        last_move = None
        for move in path:
            if not move.is_valid:
                break
            try:
                last_move = self.board.push_san(move.display_san)
            except ValueError:
                break
        self.selected_square = None
        self.render_board(last_move)
        return last_move

    def _restore_synced_board(self):
        self.input_mode = None
        self.input_origin_label = None
        restored_entry = resolve_selection_key(self.entries, self.current_selection_key)
        if restored_entry:
            self._apply_entry(restored_entry, focus_editor=False)
        else:
            self.current_entry_index = -1
            self.current_selection_key = None
            self.board.set_fen(self.start_fen)
            self.selected_square = None
            self.render_board(None)
            self._restore_status_label()
            self._refresh_preview()
            self._update_controls()
            self.current_move_span_signal.emit(-1, -1)

    def _begin_variation_input_mode(self) -> bool:
        current_entry = self._current_entry()
        if current_entry is None:
            self.lbl_status.setText("Selecione um lance antes de iniciar uma variante")
            return False

        self.input_mode = "variation"
        self.input_origin_label = self._entry_label(current_entry)
        self._set_board_from_path(current_entry.path[:-1])
        self._restore_status_label()
        return True

    def _ensure_current_input_mode(self):
        if self.input_mode is None:
            self.input_mode = "current"
            self.input_origin_label = None
            self._restore_status_label()

    def _apply_entry(self, entry: MoveEntry, focus_editor: bool = False):
        self.current_entry_index = self.entries.index(entry)
        self.current_selection_key = entry.selection_key
        self.input_mode = None
        self.input_origin_label = None
        self._load_annotations_of(entry)
        self._set_board_from_path(entry.path)
        self._restore_status_label()
        self._refresh_preview()
        self._update_controls()
        self.current_move_span_signal.emit(entry.start_index, entry.end_index)

        if focus_editor:
            self.move_focused_signal.emit(entry.start_index, entry.end_index)

    def go_prev(self):
        if self.current_entry_index < 0:
            if self.entries:
                self._apply_entry(self.entries[0], focus_editor=False)
            return

        new_index = max(-1, self.current_entry_index - 1)
        if new_index == -1:
            self._reset_board_position()
            return

        self._apply_entry(self.entries[new_index], focus_editor=True)

    def go_next(self):
        if not self.entries:
            return

        new_index = 0 if self.current_entry_index < 0 else self.current_entry_index + 1
        if new_index < len(self.entries):
            self._apply_entry(self.entries[new_index], focus_editor=True)

    def _show_copy_menu(self, point):
        """Menu de contexto na arvore de lances: recortar trechos da partida."""
        menu = QMenu(self)
        entry = self._current_entry()

        for copy_format in COPY_FORMATS:
            action = menu.addAction(copy_format.label)
            action.setEnabled(entry is not None or not copy_format.needs_entry)
            action.triggered.connect(
                lambda _checked=False, key=copy_format.key: self.copy_excerpt(key)
            )

        menu.exec(self.pgn_view.mapToGlobal(point))

    def copy_excerpt(self, key: str) -> str:
        text = copy_text(
            key,
            self.game_ast,
            self._current_entry(),
            self.start_fen,
            dict(self.game_ast.headers),
        )
        if not text:
            self.lbl_status.setText("Não ha o que copiar nesta posição.")
            return ""

        QGuiApplication.clipboard().setText(text)
        self.excerpt_copied_signal.emit(text)
        return text

    def _on_pgn_anchor_clicked(self, url):
        node_id = url.toString()
        if node_id.startswith("move:"):
            node_id = node_id[len("move:"):]
        entry = self.entry_by_node_id.get(node_id)
        if entry:
            self._apply_entry(entry, focus_editor=True)

    def _legal_targets_for(self, square: chess.Square) -> list[chess.Square]:
        return [
            move.to_square
            for move in self.board.legal_moves
            if move.from_square == square
        ]

    def _matching_moves(self, from_square: chess.Square, to_square: chess.Square) -> list[chess.Move]:
        return [
            move
            for move in self.board.legal_moves
            if move.from_square == from_square and move.to_square == to_square
        ]

    def _select_square(self, square: chess.Square | None):
        self.selected_square = square
        self.render_board(self.current_last_move)

    def _resolve_candidate_move(self, moves: list[chess.Move], force_queen: bool = False) -> chess.Move | None:
        """Um so lance possivel: e ele. Varios: e promocao, e quem decide e o usuario.

        `force_queen` (soltar com Shift) atalha a pergunta -- e a resposta em
        quase toda partida, e ninguem quer um dialogo a cada peao que chega.
        Devolve `None` se o usuario fechou o dialogo sem escolher.
        """
        if len(moves) == 1:
            return moves[0]

        def with_promotion(piece_type: int) -> chess.Move | None:
            return next((move for move in moves if move.promotion == piece_type), None)

        if force_queen:
            return with_promotion(chess.QUEEN) or moves[0]

        chosen = PromotionDialog.ask(self.board.turn, self)
        if chosen is None:
            return None
        return with_promotion(chosen) or moves[0]

    def _commit_board_move(self, move: chess.Move):
        san = self.board.san(move)
        is_white_turn = self.board.turn == chess.WHITE
        fullmove_number = self.board.fullmove_number
        last_move = self.board.push(move)
        self.selected_square = None
        self.render_board(last_move)
        self._ensure_current_input_mode()
        self._restore_status_label()
        self._suppress_next_editor_sync = True
        self.move_insert_requested_signal.emit(san, is_white_turn, fullmove_number)

    def _try_commit_move_to_square(self, target_square: chess.Square, force_queen: bool = False) -> bool:
        if self.selected_square is None:
            return False

        matching_moves = self._matching_moves(self.selected_square, target_square)
        if not matching_moves:
            return False

        move = self._resolve_candidate_move(matching_moves, force_queen)
        if move is None:
            # Promocao cancelada: a peca volta para onde estava.
            self._select_square(None)
            self._restore_status_label()
            return True

        self._commit_board_move(move)
        return True

    def _on_board_square_pressed(self, square: chess.Square, button_value: int):
        button = Qt.MouseButton(button_value)

        if button == Qt.MouseButton.RightButton:
            if self.annotate_mode:
                self._toggle_square_mark(SquareMark(square, DEFAULT_COLOR))
                return
            if self.input_mode is None:
                self._begin_variation_input_mode()
            else:
                self._restore_synced_board()
            return

        piece = self.board.piece_at(square)
        # A peca fantasma so aparece se o arrasto realmente comecar.
        self.board_view.set_ghost_piece(piece if piece and piece.color == self.board.turn else None)

        if self.selected_square is None:
            if piece and piece.color == self.board.turn:
                self._select_square(square)
                self.lbl_status.setText(f"Selecionado {chess.square_name(square)}")
            return

        if square == self.selected_square:
            self._select_square(None)
            self._restore_status_label()
            return

        if self._try_commit_move_to_square(square):
            return

        if piece and piece.color == self.board.turn:
            self._select_square(square)
            self.lbl_status.setText(f"Selecionado {chess.square_name(square)}")
            return

        self.lbl_status.setText("Destino ilegal para a peça selecionada")

    def _on_board_drag_finished(self, origin: int, target: int, button_value: int, modifiers: int):
        """Arrastar da origem ao destino: lance com o botao esquerdo, seta com o direito."""
        button = Qt.MouseButton(button_value)

        if button == Qt.MouseButton.RightButton:
            if self.annotate_mode:
                self._toggle_arrow(Arrow(origin, target, self._annotation_color(modifiers)))
            return

        if self.selected_square != origin:
            self._select_square(origin)
        self._try_commit_move_to_square(target, force_queen=wants_default_promotion(Qt.KeyboardModifier(modifiers)))

    # ------------------------------------------------------------------
    # Setas e casas coloridas (SPEC 4.4)
    # ------------------------------------------------------------------

    @staticmethod
    def _annotation_color(modifiers: int) -> str:
        """Verde e o padrao; Alt vermelho, Ctrl amarelo, Shift azul."""
        flags = Qt.KeyboardModifier(modifiers)
        if flags & Qt.KeyboardModifier.AltModifier:
            return "R"
        if flags & Qt.KeyboardModifier.ControlModifier:
            return "Y"
        if flags & Qt.KeyboardModifier.ShiftModifier:
            return "B"
        return DEFAULT_COLOR

    def _on_annotate_toggled(self, enabled: bool):
        self.annotate_mode = enabled
        if enabled:
            self.lbl_status.setText("Modo anotação: direito arrasta seta, direito clica marca casa")
        else:
            self._restore_status_label()

    def current_annotations(self) -> VisualAnnotations:
        return self.annotations

    def _toggle_arrow(self, arrow: Arrow):
        self.annotations = self.annotations.toggled_arrow(arrow)
        self.render_board(self.current_last_move)
        self._emit_annotations()

    def _toggle_square_mark(self, mark: SquareMark):
        self.annotations = self.annotations.toggled_square(mark)
        self.render_board(self.current_last_move)
        self._emit_annotations()

    def _emit_annotations(self):
        """Quem grava no texto bruto e a janela: aqui so se desenha."""
        entry = self._current_entry()
        if entry is not None:
            self.annotations_changed_signal.emit(entry.node_id, self.annotations)

    def _load_annotations_of(self, entry):
        """As anotacoes vem do comentario do lance -- e onde o PGN as guarda."""
        comments = list(entry.node.comments_after) + list(entry.node.comments_before) if entry else []
        self.annotations = parse_annotations(" ".join(comments))

    def _on_board_square_released(self, square: chess.Square, button_value: int):
        button = Qt.MouseButton(button_value)
        if button != Qt.MouseButton.LeftButton:
            return

        if self.selected_square is None or square == self.selected_square:
            return

        self._try_commit_move_to_square(square)

    @Slot(int)
    def on_editor_cursor_moved(self, position: int):
        if self._suppress_next_editor_sync:
            self._suppress_next_editor_sync = False
            return

        if self.input_mode is not None:
            self.input_mode = None
            self.input_origin_label = None

        entry = find_entry_for_insertion_position(self.entries, position)
        if not entry:
            if self.current_entry_index >= 0 or self.selected_square is not None:
                self._reset_board_position()
            return

        current_entry = self._current_entry()
        if current_entry and current_entry.node_id == entry.node_id:
            return

        self._apply_entry(entry, focus_editor=False)

    @Slot(object)
    def on_game_parsed(self, ast: GameAST):
        self.game_ast = ast
        self.entries = build_move_entries(ast)
        self.entry_by_node_id, _by_move_identity = build_entry_maps(self.entries)

        if self.input_mode is not None:
            restored_entry = resolve_selection_key(self.entries, self.current_selection_key)
            if restored_entry:
                self.current_entry_index = self.entries.index(restored_entry)
                self.current_selection_key = restored_entry.selection_key
            else:
                self.current_entry_index = -1
                self.current_selection_key = None

            self._refresh_preview()
            self._update_controls()
            self._restore_status_label()
            return

        restored_entry = resolve_selection_key(self.entries, self.current_selection_key)
        if restored_entry:
            self._apply_entry(restored_entry, focus_editor=False)
        else:
            self._reset_board_position()
