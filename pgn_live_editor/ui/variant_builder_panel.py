import chess
import chess.svg
from PyQt6.QtCore import Qt
from PyQt6.QtCore import pyqtSignal as Signal
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core.variation_builder import build_variation_text
from .board_view import InteractiveBoardView


class VariantBuilderPanel(QWidget):
    anchor_current_requested_signal = Signal()
    anchor_previous_requested_signal = Signal()
    insert_requested_signal = Signal(str)

    def __init__(self):
        super().__init__()

        self.start_fen = chess.STARTING_FEN
        self.board = chess.Board(self.start_fen)
        self.moves_san: list[str] = []
        self.selected_square = None
        self.current_last_move = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        header = QHBoxLayout()
        self.lbl_title = QLabel("Construtor de Variantes")
        self.lbl_title.setStyleSheet("font-weight: bold; padding: 4px 0;")
        header.addWidget(self.lbl_title)
        header.addStretch()

        self.btn_anchor_current = QPushButton("Posição Atual")
        self.btn_anchor_previous = QPushButton("Antes do Lance")
        self.btn_clear = QPushButton("Limpar")
        header.addWidget(self.btn_anchor_current)
        header.addWidget(self.btn_anchor_previous)
        header.addWidget(self.btn_clear)
        layout.addLayout(header)

        self.lbl_anchor = QLabel("Base: posição inicial")
        self.lbl_anchor.setStyleSheet("color: #8fa2b9;")
        layout.addWidget(self.lbl_anchor)

        self.board_view = InteractiveBoardView()
        self.board_view.setMinimumHeight(260)
        self.board_view.square_pressed_signal.connect(self._on_square_pressed)
        self.board_view.square_released_signal.connect(self._on_square_released)
        layout.addWidget(self.board_view)

        actions = QHBoxLayout()
        self.btn_copy = QPushButton("Copiar")
        self.btn_insert = QPushButton("Inserir no Cursor")
        actions.addStretch()
        actions.addWidget(self.btn_copy)
        actions.addWidget(self.btn_insert)
        layout.addLayout(actions)

        self.text_box = QPlainTextEdit()
        self.text_box.setPlaceholderText("A variante montada no tabuleiro aparece aqui.")
        self.text_box.setMinimumHeight(88)
        layout.addWidget(self.text_box)

        self.lbl_status = QLabel("Clique nas casas do tabuleiro auxiliar para montar a variante.")
        self.lbl_status.setStyleSheet("color: #8fa2b9;")
        layout.addWidget(self.lbl_status)

        self.btn_anchor_current.clicked.connect(self.anchor_current_requested_signal.emit)
        self.btn_anchor_previous.clicked.connect(self.anchor_previous_requested_signal.emit)
        self.btn_clear.clicked.connect(self.clear_moves)
        self.btn_copy.clicked.connect(self.copy_text)
        self.btn_insert.clicked.connect(self._emit_insert_requested)

        self.render_board()

    def set_anchor(self, start_fen: str, label: str):
        self.start_fen = start_fen
        self.lbl_anchor.setText(f"Base: {label}")
        self.clear_moves()

    def clear_moves(self):
        self.board.set_fen(self.start_fen)
        self.moves_san = []
        self.selected_square = None
        self.current_last_move = None
        self.text_box.setPlainText("")
        self.render_board()
        self.lbl_status.setText("Variante limpa. Monte a linha no tabuleiro auxiliar.")

    def copy_text(self):
        text = self.text_box.toPlainText().strip()
        if not text:
            self.lbl_status.setText("Não ha variante para copiar.")
            return

        clipboard = QGuiApplication.clipboard()
        clipboard.setText(text)
        self.lbl_status.setText("Variante copiada para a área de transferencia.")

    def _emit_insert_requested(self):
        text = self.text_box.toPlainText().strip()
        if not text:
            self.lbl_status.setText("Não ha variante para inserir.")
            return

        self.insert_requested_signal.emit(text)
        self.lbl_status.setText("Variante enviada para inserção no editor.")

    def render_board(self):
        width = max(self.board_view.width(), 360)
        height = max(self.board_view.height(), 260)
        board_size = max(220, min(width - 12, height - 12, 420))
        self.board_view.set_board_geometry(board_size, flipped=False)

        fill = {}
        if self.selected_square is not None:
            fill[self.selected_square] = "#f1c75b88"
            for target in self._legal_targets_for(self.selected_square):
                fill[target] = "#6ecf8b88"

        svg = chess.svg.board(
            self.board,
            size=board_size,
            lastmove=self.current_last_move,
            coordinates=False,
            borders=False,
            fill=fill,
        )
        self.board_view.set_svg(svg)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.render_board()

    def _legal_targets_for(self, square: chess.Square):
        return [move.to_square for move in self.board.legal_moves if move.from_square == square]

    def _matching_moves(self, from_square: chess.Square, to_square: chess.Square):
        return [
            move for move in self.board.legal_moves if move.from_square == from_square and move.to_square == to_square
        ]

    def _resolve_candidate_move(self, moves):
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

    def _commit_move(self, move: chess.Move):
        san = self.board.san(move)
        self.current_last_move = self.board.push(move)
        self.moves_san.append(san)
        self.selected_square = None
        self.text_box.setPlainText(build_variation_text(self.start_fen, self.moves_san))
        self.render_board()
        self.lbl_status.setText("Lance adicionado a variante.")

    def _try_commit_to_square(self, square: chess.Square) -> bool:
        if self.selected_square is None:
            return False

        matching_moves = self._matching_moves(self.selected_square, square)
        if not matching_moves:
            return False

        self._commit_move(self._resolve_candidate_move(matching_moves))
        return True

    def _on_square_pressed(self, square: chess.Square, button_value: int):
        if Qt.MouseButton(button_value) != Qt.MouseButton.LeftButton:
            return

        piece = self.board.piece_at(square)

        if self.selected_square is None:
            if piece and piece.color == self.board.turn:
                self.selected_square = square
                self.render_board()
                self.lbl_status.setText(f"Selecionado {chess.square_name(square)}.")
            return

        if square == self.selected_square:
            self.selected_square = None
            self.render_board()
            self.lbl_status.setText("Seleção cancelada.")
            return

        if self._try_commit_to_square(square):
            return

        if piece and piece.color == self.board.turn:
            self.selected_square = square
            self.render_board()
            self.lbl_status.setText(f"Selecionado {chess.square_name(square)}.")
            return

        self.lbl_status.setText("Destino ilegal para a peça selecionada.")

    def _on_square_released(self, square: chess.Square, button_value: int):
        if Qt.MouseButton(button_value) != Qt.MouseButton.LeftButton:
            return

        if self.selected_square is None or square == self.selected_square:
            return

        self._try_commit_to_square(square)
