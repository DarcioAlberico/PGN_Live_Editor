from pathlib import Path

import chess

from pgn_live_editor.core.annotations import Arrow, SquareMark
from pgn_live_editor.services.diagram_generator import render_diagram_svg, write_png, write_svg


def test_diagram_svg_contains_position_coordinates_and_annotations(tmp_path: Path):
    board = chess.Board()
    svg = render_diagram_svg(board, arrows=[Arrow(chess.E2, chess.E4)], squares=[SquareMark(chess.D5, "Y")])
    assert "svg" in svg
    assert "e2" in svg
    assert "d5" in svg
    target = tmp_path / "position.svg"
    write_svg(target, svg)
    assert target.read_text(encoding="utf-8").startswith("<svg")


def test_diagram_png_is_written(tmp_path: Path):
    svg = render_diagram_svg(chess.Board(), size=320)
    target = tmp_path / "position.png"
    write_png(target, svg, size=320)
    assert target.read_bytes().startswith(b"\x89PNG")
