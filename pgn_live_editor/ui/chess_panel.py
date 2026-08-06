
import chess
import chess.svg
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtCore import pyqtSignal as Signal
from PyQt6.QtCore import pyqtSlot as Slot
from PyQt6.QtGui import QResizeEvent
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
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
from .board_view import InteractiveBoardView
from .variant_builder_panel import VariantBuilderPanel


class ChessPanel(QWidget):
    current_move_span_signal = Signal(int, int)
    move_focused_signal = Signal(int, int)
    move_insert_requested_signal = Signal(str, bool, int)

    def __init__(self):
        super().__init__()

        self.game_ast = GameAST()
        self.entries: list[MoveEntry] = []
        self.entry_by_node_id: dict[str, MoveEntry] = {}
        self.current_entry_index = -1
        self.current_selection_key: str | None = None
        self.current_last_move = None
        self.board = chess.Board()
        self.board_flipped = False
        self.selected_square: chess.Square | None = None
        self.input_mode: str | None = None
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

        controls.addWidget(self.btn_prev)
        controls.addWidget(self.lbl_status, 1)
        controls.addWidget(self.lbl_meta)
        controls.addWidget(self.btn_next)
        layout.addLayout(controls)

        self.content_splitter = QSplitter(Qt.Orientation.Vertical)
        layout.addWidget(self.content_splitter, 1)

        self.board_view = InteractiveBoardView()
        self.board_view.setMinimumHeight(380)
        self.board_view.square_pressed_signal.connect(self._on_board_square_pressed)
        self.board_view.square_released_signal.connect(self._on_board_square_released)
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
        ).encode('utf-8')
        self.board_view.set_svg(svg.decode('utf-8'))

    def _board_fill(self) -> dict[chess.Square, str]:
        fill: dict[chess.Square, str] = {}
        if self.selected_square is not None:
            fill[self.selected_square] = "#f1c75b88"
            for target in self._legal_targets_for(self.selected_square):
                fill[target] = "#6ecf8b88"
        return fill

    def _current_entry(self) -> MoveEntry | None:
        if 0 <= self.current_entry_index < len(self.entries):
            return self.entries[self.current_entry_index]
        return None

    def _reset_board_position(self):
        self.current_entry_index = -1
        self.current_selection_key = None
        self.selected_square = None
        self.input_mode = None
        self.input_origin_label = None
        self.board.reset()
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
        return f"apos {self._entry_label(entry)}"

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
            self.variant_builder.set_anchor(chess.STARTING_FEN, "posicao inicial")
            return

        board = chess.Board()
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
            self.variant_builder.set_anchor(chess.STARTING_FEN, "posicao inicial")
            return

        board = chess.Board()
        for move in current_entry.path[:-1]:
            if not move.is_valid:
                break
            try:
                board.push_san(move.display_san)
            except ValueError:
                break

        self.variant_builder.set_anchor(board.fen(), f"antes de {self._entry_label(current_entry)}")

    def _set_board_from_path(self, path) -> chess.Move | None:
        self.board.reset()
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
            self.board.reset()
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

    def _resolve_candidate_move(self, moves: list[chess.Move]) -> chess.Move:
        if len(moves) == 1:
            return moves[0]

        preferred_promotions = [
            chess.QUEEN,
            chess.KNIGHT,
            chess.ROOK,
            chess.BISHOP,
        ]
        for promotion in preferred_promotions:
            candidate = next((move for move in moves if move.promotion == promotion), None)
            if candidate:
                return candidate
        return moves[0]

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

    def _try_commit_move_to_square(self, target_square: chess.Square) -> bool:
        if self.selected_square is None:
            return False

        matching_moves = self._matching_moves(self.selected_square, target_square)
        if not matching_moves:
            return False

        move = self._resolve_candidate_move(matching_moves)
        self._commit_board_move(move)
        return True

    def _on_board_square_pressed(self, square: chess.Square, button_value: int):
        button = Qt.MouseButton(button_value)

        if button == Qt.MouseButton.RightButton:
            if self.input_mode is None:
                self._begin_variation_input_mode()
            else:
                self._restore_synced_board()
            return

        piece = self.board.piece_at(square)

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

        self.lbl_status.setText("Destino ilegal para a peca selecionada")

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
