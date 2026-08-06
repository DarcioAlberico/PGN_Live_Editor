"""Testes de fumaca da interface (rodam sem tela, com QT_QPA_PLATFORM=offscreen)."""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from pgn_live_editor.ui.editor_panel import EditorPanel  # noqa: E402
from pgn_live_editor.ui.main_window import MainWindow  # noqa: E402

BOOK_TEXT = "{Defesa Siciliana} 1.e4 c5 2.Nf3 e6 3.d4 cxd4 (3... Nf6 4.e5) 4.Nxd4"

_app = QApplication.instance() or QApplication([])


class EditorPanelEditingTests(unittest.TestCase):
    def setUp(self):
        self.panel = EditorPanel()
        self.addCleanup(self.panel.deleteLater)

    def test_join_lines_keeps_undo_history(self):
        """Antes, `setPlainText` apagava a pilha de Ctrl+Z."""
        self.panel.load_document_text("1.e4 e5\n2.Nf3 Nc6")

        self.panel.action_join_lines()
        self.assertEqual("1.e4 e5 2.Nf3 Nc6", self.panel.document_text())

        self.assertTrue(self.panel.text_edit.document().isUndoAvailable())
        self.panel.text_edit.undo()
        self.assertEqual("1.e4 e5\n2.Nf3 Nc6", self.panel.document_text())

    def test_insert_headers_keeps_undo_history(self):
        self.panel.load_document_text("1.e4 e5")

        self.panel.insert_default_headers()
        self.assertIn('[Event "PGN Live Editor Export"]', self.panel.document_text())

        self.panel.text_edit.undo()
        self.assertEqual("1.e4 e5", self.panel.document_text())

    def test_replace_all_text_keeps_the_cursor_where_it_was(self):
        self.panel.load_document_text("1.e4 e5 2.Nf3 Nc6")
        cursor = self.panel.text_edit.textCursor()
        cursor.setPosition(8)
        self.panel.text_edit.setTextCursor(cursor)

        self.panel.replace_all_text("1.e4 e5 2.Nf3 Nc6 3.Bb5")

        self.assertEqual(8, self.panel.cursor_position())

    def test_replace_span_is_undoable_and_reparses(self):
        self.panel.load_document_text("1.e4 e5 2.Nf3 Nc6")

        self.panel.replace_span(0, 4, "1.d4")

        self.assertTrue(self.panel.document_text().startswith("1.d4"))
        self.panel.text_edit.undo()
        self.assertTrue(self.panel.document_text().startswith("1.e4"))

    def test_fen_header_sets_the_starting_position(self):
        """Fragmento de livro que parte de um diagrama."""
        self.panel.load_document_text('[FEN "8/8/8/4k3/8/8/4P3/4K3 w - - 0 1"]\n[SetUp "1"]\n\n1.e4 Kf6 2.e5+ Kf5')

        self.assertEqual("8/8/8/4k3/8/8/4P3/4K3 w - - 0 1", self.panel.current_start_fen)
        self.assertEqual(["e4", "Kf6", "e5+", "Kf5"], [move.display_san for move in self.panel.current_ast.moves])
        self.assertTrue(all(move.is_valid for move in self.panel.current_ast.moves))

    def test_invalid_fen_falls_back_to_the_initial_position(self):
        self.panel.load_document_text('[FEN "isto nao e um fen"]\n\n1.e4 e5')

        self.assertEqual(["e4", "e5"], [move.display_san for move in self.panel.current_ast.moves])


class MainWindowSmokeTests(unittest.TestCase):
    def setUp(self):
        self.window = MainWindow()
        self.addCleanup(self.window.deleteLater)

    def test_window_builds_and_wires_the_panels(self):
        self.window.editor_panel.load_document_text(BOOK_TEXT)

        self.assertEqual(9, len(self.window.chess_panel.entries))
        self.assertEqual("*", self.window.chess_panel.game_ast.result or "*")

    def test_board_navigation_moves_the_position(self):
        self.window.editor_panel.load_document_text(BOOK_TEXT)
        panel = self.window.chess_panel

        panel.go_next()
        panel.go_next()

        self.assertEqual("e4", panel.entries[panel.current_entry_index - 1].node.display_san)
        self.assertGreater(panel.board.fullmove_number, 1)

    def test_flip_board_toggles_orientation(self):
        panel = self.window.chess_panel
        self.assertFalse(panel.board_flipped)

        panel.flip_board()
        self.assertTrue(panel.board_flipped)

    def test_issues_panel_shows_what_the_parser_did(self):
        self.window.editor_panel.load_document_text("1.e4 e5 2.4Jf3 Cc6 3.Bh5")
        panel = self.window.issues_panel

        codes = [issue.code for issue in panel.issues]
        self.assertIn("AUTO_CORRECTED", codes)
        self.assertIn("AMBIGUOUS_MOVE", codes)
        self.assertGreater(panel.table.rowCount(), 0)
        self.assertIn("aviso", panel.lbl_summary.text())

    def test_accepting_a_correction_rewrites_the_raw_text_and_clears_the_alert(self):
        self.window.editor_panel.load_document_text("1.e4 e5 2.Nf3 Nc6 3.Bh5")
        panel = self.window.issues_panel

        issue = next(issue for issue in panel.issues if issue.code == "AMBIGUOUS_MOVE")
        self.window._accept_issue(issue)

        self.assertEqual("1.e4 e5 2.Nf3 Nc6 3.Bb5", self.window.editor_panel.document_text())
        self.assertEqual([], panel.issues)

    def test_turning_an_unrecognized_token_into_a_comment(self):
        self.window.editor_panel.load_document_text("1.e4 e5 2.Nf3 Xf6z")
        panel = self.window.issues_panel

        issue = next(issue for issue in panel.issues if issue.code == "UNRECOGNIZED_MOVE")
        self.window._comment_issue(issue)

        self.assertEqual("1.e4 e5 2.Nf3 {Xf6z}", self.window.editor_panel.document_text())
        self.assertEqual([], panel.issues)

    def test_learning_a_correction_applies_it_from_then_on(self):
        self.window.editor_panel.load_document_text("1.e4 e5 2.Nf3 Nc6 3.Bh5")
        panel = self.window.issues_panel

        issue = next(issue for issue in panel.issues if issue.code == "AMBIGUOUS_MOVE")
        self.window._learn_issue(issue)

        self.assertEqual({"Bh5": "Bb5"}, self.window.project.dictionary)
        # Agora a mesma leitura entra com confianca de dicionario, sem pedir revisao.
        self.assertEqual([], [i for i in panel.issues if i.severity == "warning"])
        self.assertFalse(self.window.editor_panel.current_ast.moves[-1].needs_review)

    def test_ignoring_a_token_silences_it_for_the_project(self):
        self.window.editor_panel.load_document_text("1.e4 e5 2.Nf3 Xf6z")
        panel = self.window.issues_panel

        issue = next(issue for issue in panel.issues if issue.code == "UNRECOGNIZED_MOVE")
        self.window._ignore_issue(issue)

        self.assertIn("Xf6z", self.window.project.state.ignored_issue_tokens)
        self.assertEqual([], panel.issues)

    def test_invalid_date_header_is_reported(self):
        self.window.editor_panel.load_document_text('[Date "1945"]\n\n1.e4 e5')

        codes = [issue.code for issue in self.window.issues_panel.issues]
        self.assertIn("INVALID_DATE", codes)

    def test_dirty_flag_tracks_unsaved_work(self):
        self.assertFalse(self.window._is_dirty())

        self.window.editor_panel.load_document_text(BOOK_TEXT)
        self.assertTrue(self.window._is_dirty())

        self.window.project.raw_text = BOOK_TEXT
        self.assertFalse(self.window._is_dirty())


if __name__ == "__main__":
    unittest.main()
