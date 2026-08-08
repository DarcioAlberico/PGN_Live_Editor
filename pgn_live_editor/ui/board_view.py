"""O widget do tabuleiro: desenha o SVG e traduz mouse em casas.

Duas coisas moram aqui e em nenhum outro lugar: a conta que transforma pixel em
casa (com o tabuleiro invertido ou nao) e o arrasto. O arrasto e feito com peca
fantasma -- a peca segue o cursor com transparencia -- porque sem ela nao ha
como saber se o programa entendeu de onde a peca saiu.

Clique-clique continua funcionando: quem prefere apontar origem e destino nao
precisa arrastar nada.
"""

from __future__ import annotations

import chess
from PyQt6.QtCore import QByteArray, QPointF, QRectF, Qt
from PyQt6.QtCore import pyqtSignal as Signal
from PyQt6.QtGui import QColor, QMouseEvent, QPainter, QPaintEvent
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import QWidget

#: Arrasto so comeca depois disto, em pixels: sem folga, todo clique vira arrasto.
DRAG_THRESHOLD = 6


class InteractiveBoardView(QWidget):
    square_pressed_signal = Signal(int, int)
    square_released_signal = Signal(int, int)
    #: `(origem, destino, botao, modificadores)` -- o arrasto completo.
    drag_finished_signal = Signal(int, int, int, int)

    def __init__(self):
        super().__init__()
        self._renderer = QSvgRenderer(self)
        self._board_size = 0
        self._flipped = False

        self._press_square: int | None = None
        self._press_pos: QPointF | None = None
        self._press_button = Qt.MouseButton.NoButton
        self._dragging = False
        self._cursor_pos: QPointF | None = None
        self._ghost = QSvgRenderer(self)
        self._has_ghost = False

        self.setMouseTracking(True)

    def set_board_geometry(self, board_size: int, flipped: bool = False):
        self._board_size = max(0, board_size)
        self._flipped = flipped
        self.update()

    def set_svg(self, svg: str):
        self._renderer.load(QByteArray(svg.encode("utf-8")))
        self.update()

    # ------------------------------------------------------------------
    # Desenho
    # ------------------------------------------------------------------

    def paintEvent(self, event: QPaintEvent):
        _event = event
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#1b1d22"))

        board_rect = self._board_rect()
        if not board_rect.isEmpty() and self._renderer.isValid():
            self._renderer.render(painter, board_rect)

        if self._dragging and self._has_ghost and self._cursor_pos is not None:
            self._paint_ghost(painter, board_rect)

    def _paint_ghost(self, painter: QPainter, board_rect: QRectF):
        square_size = board_rect.width() / 8
        target = QRectF(
            self._cursor_pos.x() - square_size / 2,
            self._cursor_pos.y() - square_size / 2,
            square_size,
            square_size,
        )
        painter.setOpacity(0.75)
        self._ghost.render(painter, target)
        painter.setOpacity(1.0)

    def set_ghost_piece(self, piece: chess.Piece | None):
        """A peca que segue o cursor durante o arrasto."""
        if piece is None:
            self._has_ghost = False
            return

        self._ghost.load(QByteArray(chess.svg.piece(piece).encode("utf-8")))
        self._has_ghost = self._ghost.isValid()

    # ------------------------------------------------------------------
    # Mouse
    # ------------------------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() in {Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton}:
            square = self._square_at(event.position().x(), event.position().y())
            if square is not None:
                self._press_square = square
                self._press_pos = event.position()
                self._press_button = event.button()
                self._dragging = False
                self._cursor_pos = event.position()
                self.square_pressed_signal.emit(square, int(event.button().value))
                event.accept()
                return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._press_pos is not None and self._press_square is not None:
            self._cursor_pos = event.position()
            if not self._dragging:
                travelled = (event.position() - self._press_pos).manhattanLength()
                if travelled >= DRAG_THRESHOLD:
                    self._dragging = True
            if self._dragging:
                self.update()
                event.accept()
                return

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() in {Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton}:
            square = self._square_at(event.position().x(), event.position().y())
            was_dragging = self._dragging
            origin = self._press_square

            self._press_square = None
            self._press_pos = None
            self._dragging = False
            self._has_ghost = False
            self.update()

            if square is not None:
                if was_dragging and origin is not None and origin != square:
                    self.drag_finished_signal.emit(
                        origin,
                        square,
                        int(event.button().value),
                        int(event.modifiers().value),
                    )
                else:
                    self.square_released_signal.emit(square, int(event.button().value))
                event.accept()
                return

        super().mouseReleaseEvent(event)

    # ------------------------------------------------------------------

    def _board_rect(self) -> QRectF:
        size = min(float(self._board_size), float(self.width()), float(self.height()))
        if size <= 0:
            return QRectF()

        left = (self.width() - size) / 2
        top = (self.height() - size) / 2
        return QRectF(left, top, size, size)

    def _square_at(self, x: float, y: float):
        board_rect = self._board_rect()
        if board_rect.isEmpty():
            return None

        if not board_rect.contains(x, y):
            return None

        square_size = board_rect.width() / 8
        file_index = int((x - board_rect.left()) // square_size)
        rank_from_top = int((y - board_rect.top()) // square_size)

        if self._flipped:
            file_index = 7 - file_index
            rank_index = rank_from_top
        else:
            rank_index = 7 - rank_from_top

        return chess.square(file_index, rank_index)
