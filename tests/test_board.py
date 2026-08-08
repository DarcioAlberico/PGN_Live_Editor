"""Fase 6: o tabuleiro deixa de esconder decisao do usuario.

Promocao (D13), arrastar-e-soltar e as anotacoes visuais `[%cal]`/`[%csl]`.
"""

from __future__ import annotations

import os
import unittest
from unittest import mock

import chess

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPointF, Qt  # noqa: E402
from PyQt6.QtGui import QMouseEvent  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from pgn_live_editor.core.annotations import (  # noqa: E402
    Arrow,
    SquareMark,
    VisualAnnotations,
    apply_annotations,
    format_annotations,
    parse_annotations,
    strip_annotations,
)
from pgn_live_editor.core.text_ops import apply_edits, set_move_annotations  # noqa: E402
from pgn_live_editor.ui.board_view import DRAG_THRESHOLD, InteractiveBoardView  # noqa: E402
from pgn_live_editor.ui.main_window import MainWindow  # noqa: E402
from pgn_live_editor.ui.promotion_dialog import PromotionDialog  # noqa: E402

_app = QApplication.instance() or QApplication([])


class AnnotationFormatTests(unittest.TestCase):
    """O formato que o ChessBase criou e todo mundo passou a ler."""

    def test_arrows_and_squares_are_read_from_a_comment(self):
        annotations = parse_annotations("Ideia das brancas. [%cal Ge2e4,Rd1h5] [%csl Yd5]")

        self.assertEqual(
            [(chess.E2, chess.E4, "G"), (chess.D1, chess.H5, "R")],
            [(a.tail, a.head, a.color) for a in annotations.arrows],
        )
        self.assertEqual([(chess.D5, "Y")], [(s.square, s.color) for s in annotations.squares])

    def test_a_comment_without_commands_has_no_annotations(self):
        self.assertFalse(parse_annotations("so prosa aqui"))
        self.assertFalse(parse_annotations(""))

    def test_a_malformed_token_is_ignored_not_crashed_on(self):
        annotations = parse_annotations("[%cal Ge2e4,ZZZZ,Xa1a2] [%csl 99]")

        self.assertEqual(1, len(annotations.arrows))
        self.assertEqual([], annotations.squares)

    def test_the_prose_survives_when_the_commands_are_replaced(self):
        original = "Ameaca mate. [%cal Ge2e4]"

        updated = apply_annotations(original, VisualAnnotations([Arrow(chess.D1, chess.H5, "R")], []))

        self.assertEqual("Ameaca mate. [%cal Rd1h5]", updated)
        self.assertEqual("Ameaca mate.", strip_annotations(updated))

    def test_removing_every_annotation_leaves_only_the_prose(self):
        self.assertEqual("Ameaca mate.", apply_annotations("Ameaca mate. [%cal Ge2e4]", VisualAnnotations([], [])))

    def test_drawing_the_same_arrow_twice_erases_it(self):
        annotations = VisualAnnotations([], [])
        arrow = Arrow(chess.E2, chess.E4)

        once = annotations.toggled_arrow(arrow)
        twice = once.toggled_arrow(arrow)

        self.assertEqual(1, len(once.arrows))
        self.assertEqual([], twice.arrows)

    def test_marking_the_same_square_twice_erases_it(self):
        marked = VisualAnnotations([], []).toggled_square(SquareMark(chess.D5))

        self.assertEqual([], marked.toggled_square(SquareMark(chess.D5, "R")).squares)

    def test_the_written_form_survives_a_round_trip(self):
        annotations = VisualAnnotations(
            [Arrow(chess.E2, chess.E4, "G")], [SquareMark(chess.D5, "Y")]
        )

        restored = parse_annotations(format_annotations(annotations))

        self.assertEqual(annotations.arrows, restored.arrows)
        self.assertEqual(annotations.squares, restored.squares)


class AnnotationsInTheRawTextTests(unittest.TestCase):
    """Nao existe anotacao que viva fora do texto bruto."""

    def setUp(self):
        self.window = MainWindow()
        self.addCleanup(self.window.deleteLater)
        self.addCleanup(self.window.editor_panel.shutdown)

    def _first_move(self, text: str):
        self.window.editor_panel.load_document_text(text)
        return self.window.editor_panel.current_ast.moves[0]

    def test_an_arrow_becomes_a_comment_after_the_move(self):
        text = "1.e4 e5"
        move = self._first_move(text)

        result = set_move_annotations(text, move, VisualAnnotations([Arrow(chess.E2, chess.E4)], []))

        self.assertEqual("1.e4 {[%cal Ge2e4]} e5", apply_edits(text, result.edits))

    def test_an_existing_comment_keeps_its_prose(self):
        text = "1.e4 {abertura de rei} e5"
        move = self._first_move(text)

        result = set_move_annotations(text, move, VisualAnnotations([], [SquareMark(chess.D5, "Y")]))

        self.assertEqual("1.e4 {abertura de rei [%csl Yd5]} e5", apply_edits(text, result.edits))

    def test_erasing_the_last_annotation_removes_an_empty_comment(self):
        text = "1.e4 {[%cal Ge2e4]} e5"
        move = self._first_move(text)

        result = set_move_annotations(text, move, VisualAnnotations([], []))

        self.assertEqual("1.e4 e5", apply_edits(text, result.edits))

    def test_the_board_draws_what_the_book_wrote(self):
        self.window.editor_panel.load_document_text("1.e4 {[%cal Ge2e4] [%csl Rd5]} e5")
        panel = self.window.chess_panel

        panel.go_next()

        self.assertEqual([(chess.E2, chess.E4)], [(a.tail, a.head) for a in panel.annotations.arrows])
        self.assertEqual([chess.D5], [s.square for s in panel.annotations.squares])

    def test_drawing_on_the_board_writes_into_the_document(self):
        self.window.editor_panel.load_document_text("1.e4 e5")
        panel = self.window.chess_panel
        panel.go_next()

        panel._toggle_arrow(Arrow(chess.E2, chess.E4))

        self.assertIn("[%cal Ge2e4]", self.window.editor_panel.document_text())

    def test_the_drawing_is_a_single_undo(self):
        self.window.editor_panel.load_document_text("1.e4 e5")
        panel = self.window.chess_panel
        panel.go_next()
        panel._toggle_arrow(Arrow(chess.E2, chess.E4))

        self.window.editor_panel.text_edit.undo()

        self.assertEqual("1.e4 e5", self.window.editor_panel.document_text())


class PromotionTests(unittest.TestCase):
    """D13: promovia sempre a dama, calado."""

    PROMOTION_FEN = "8/4P3/8/8/8/8/8/K6k w - - 0 1"

    def setUp(self):
        self.window = MainWindow()
        self.addCleanup(self.window.deleteLater)
        self.addCleanup(self.window.editor_panel.shutdown)
        self.window.editor_panel.load_document_text(
            f'[FEN "{self.PROMOTION_FEN}"]\n[SetUp "1"]\n\n'
        )
        self.panel = self.window.chess_panel
        self.panel.board.set_fen(self.PROMOTION_FEN)

    def _promotion_moves(self):
        return [move for move in self.panel.board.legal_moves if move.from_square == chess.E7]

    def test_the_dialog_decides_which_piece(self):
        moves = self._promotion_moves()

        with mock.patch.object(PromotionDialog, "ask", return_value=chess.KNIGHT):
            chosen = self.panel._resolve_candidate_move(moves)

        self.assertEqual(chess.KNIGHT, chosen.promotion)

    def test_shift_promotes_to_queen_without_asking(self):
        moves = self._promotion_moves()

        with mock.patch.object(PromotionDialog, "ask") as asked:
            chosen = self.panel._resolve_candidate_move(moves, force_queen=True)

        self.assertEqual(chess.QUEEN, chosen.promotion)
        asked.assert_not_called()

    def test_closing_the_dialog_plays_nothing(self):
        moves = self._promotion_moves()

        with mock.patch.object(PromotionDialog, "ask", return_value=None):
            self.assertIsNone(self.panel._resolve_candidate_move(moves))

    def test_a_move_that_is_not_a_promotion_never_asks(self):
        self.panel.board.set_fen(chess.STARTING_FEN)
        moves = [move for move in self.panel.board.legal_moves if move.uci() == "e2e4"]

        with mock.patch.object(PromotionDialog, "ask") as asked:
            chosen = self.panel._resolve_candidate_move(moves)

        self.assertEqual("e2e4", chosen.uci())
        asked.assert_not_called()

    def test_cancelling_a_promotion_leaves_the_position_alone(self):
        self.panel.selected_square = chess.E7
        before = self.panel.board.fen()

        with mock.patch.object(PromotionDialog, "ask", return_value=None):
            handled = self.panel._try_commit_move_to_square(chess.E8)

        self.assertTrue(handled)
        self.assertEqual(before, self.panel.board.fen())


class DragTests(unittest.TestCase):
    """Arrastar a peca, com fantasma -- sem deixar de aceitar clique-clique."""

    def setUp(self):
        self.view = InteractiveBoardView()
        self.addCleanup(self.view.deleteLater)
        self.view.resize(400, 400)
        self.view.set_board_geometry(400)

    def _press(self, x: float, y: float, button=Qt.MouseButton.LeftButton):
        self.view.mousePressEvent(self._event(QMouseEvent.Type.MouseButtonPress, x, y, button))

    def _move(self, x: float, y: float, button=Qt.MouseButton.LeftButton):
        self.view.mouseMoveEvent(self._event(QMouseEvent.Type.MouseMove, x, y, button))

    def _release(self, x: float, y: float, button=Qt.MouseButton.LeftButton):
        self.view.mouseReleaseEvent(self._event(QMouseEvent.Type.MouseButtonRelease, x, y, button))

    @staticmethod
    def _event(kind, x: float, y: float, button):
        return QMouseEvent(
            kind,
            QPointF(x, y),
            QPointF(x, y),
            button,
            button,
            Qt.KeyboardModifier.NoModifier,
        )

    def test_a_drag_reports_origin_and_target(self):
        drags: list[tuple] = []
        self.view.drag_finished_signal.connect(lambda *args: drags.append(args))

        # e2 -> e4 num tabuleiro de 400px: casa de 50px.
        self._press(225, 325)
        self._move(225, 300)
        self._move(225, 225)
        self._release(225, 225)

        self.assertEqual(1, len(drags))
        origin, target, _button, _modifiers = drags[0]
        self.assertEqual(chess.E2, origin)
        self.assertEqual(chess.E4, target)

    def test_a_click_is_not_a_drag(self):
        drags: list[tuple] = []
        releases: list[tuple] = []
        self.view.drag_finished_signal.connect(lambda *args: drags.append(args))
        self.view.square_released_signal.connect(lambda *args: releases.append(args))

        self._press(225, 325)
        self._release(225, 325)

        self.assertEqual([], drags)
        self.assertEqual(1, len(releases))

    def test_a_tiny_wobble_is_still_a_click(self):
        """Sem folga, mao tremida em cima da casa viraria arrasto."""
        drags: list[tuple] = []
        self.view.drag_finished_signal.connect(lambda *args: drags.append(args))

        self._press(225, 325)
        self._move(225 + DRAG_THRESHOLD // 3, 325)
        self._release(225, 325)

        self.assertEqual([], drags)

    def test_the_ghost_piece_only_exists_while_dragging(self):
        self.view.set_ghost_piece(chess.Piece(chess.PAWN, chess.WHITE))
        self.assertTrue(self.view._has_ghost)

        self._press(225, 325)
        self._move(225, 225)
        self._release(225, 225)

        self.assertFalse(self.view._has_ghost)


if __name__ == "__main__":
    unittest.main()
