import chess

from PyQt6.QtCore import QByteArray, QRectF, Qt, pyqtSignal as Signal
from PyQt6.QtGui import QColor, QMouseEvent, QPainter, QPaintEvent
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import QWidget


class InteractiveBoardView(QWidget):
    square_pressed_signal = Signal(int, int)
    square_released_signal = Signal(int, int)

    def __init__(self):
        super().__init__()
        self._renderer = QSvgRenderer(self)
        self._board_size = 0
        self._flipped = False
        self.setMouseTracking(True)

    def set_board_geometry(self, board_size: int, flipped: bool = False):
        self._board_size = max(0, board_size)
        self._flipped = flipped
        self.update()

    def set_svg(self, svg: str):
        self._renderer.load(QByteArray(svg.encode("utf-8")))
        self.update()

    def paintEvent(self, event: QPaintEvent):
        _event = event
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#1b1d22"))

        board_rect = self._board_rect()
        if not board_rect.isEmpty() and self._renderer.isValid():
            self._renderer.render(painter, board_rect)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() in {Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton}:
            square = self._square_at(event.position().x(), event.position().y())
            if square is not None:
                self.square_pressed_signal.emit(
                    square,
                    int(event.button().value),
                )
                event.accept()
                return

        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() in {Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton}:
            square = self._square_at(event.position().x(), event.position().y())
            if square is not None:
                self.square_released_signal.emit(
                    square,
                    int(event.button().value),
                )
                event.accept()
                return

        super().mouseReleaseEvent(event)

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
