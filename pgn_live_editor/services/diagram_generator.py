"""Geração de diagramas a partir de uma posição já validada pelo tabuleiro."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import chess
import chess.svg

from ..core.annotations import Arrow, SquareMark


def render_diagram_svg(
    board: chess.Board,
    *,
    arrows: Iterable[Arrow] = (),
    squares: Iterable[SquareMark] = (),
    orientation: chess.Color = chess.WHITE,
    size: int = 900,
    lastmove: chess.Move | None = None,
) -> str:
    """Render a self-contained SVG suitable for books and web previews."""
    fills = {
        mark.square: {"G": "#5bb974", "R": "#e05c5c", "Y": "#f0c05a", "B": "#5b8dd9"}.get(mark.color, "#5bb974")
        for mark in squares
    }
    svg_arrows = [
        chess.svg.Arrow(
            arrow.tail,
            arrow.head,
            color={"G": "#5bb974", "R": "#e05c5c", "Y": "#f0c05a", "B": "#5b8dd9"}.get(arrow.color, "#5bb974"),
        )
        for arrow in arrows
    ]
    rendered = chess.svg.board(
        board,
        size=size,
        coordinates=True,
        orientation=orientation,
        lastmove=lastmove,
        arrows=svg_arrows,
        fill=fills,
    )
    return rendered.decode("utf-8") if isinstance(rendered, bytes) else str(rendered)


def write_svg(path: Path, svg: str) -> None:
    path.write_text(svg, encoding="utf-8")


def write_png(path: Path, svg: str, size: int = 900) -> None:
    """Rasterize using Qt, already required by the desktop application."""
    from PyQt6.QtCore import QByteArray, QRectF
    from PyQt6.QtGui import QImage, QPainter
    from PyQt6.QtSvg import QSvgRenderer

    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(0xFFFFFFFF)
    painter = QPainter(image)
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()
    if not image.save(str(path), "PNG"):
        raise OSError(f"Não foi possível gravar o diagrama em {path}.")
