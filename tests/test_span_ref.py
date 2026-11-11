"""Gestos a partir de um alerta conferem o trecho antes de editar (R-302).

U-05: aceitar/comentar aplicavam `issue.raw_start/raw_end` da analise que
gerou o alerta. Digitar antes do alerta e aceitar dentro do debounce editava
o lugar errado: `5.O-O Be7` virava `5.O-Bb5Be7`. Os gestos estruturais
usavam a arvore velha sobre o texto novo, com a mesma raiz.
"""

from __future__ import annotations

import unittest

import _isolation  # noqa: F401
from PyQt6.QtWidgets import QApplication

from pgn_live_editor.core.revision import SpanRef

RUY = (
    "1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 4. Ba4 Nf6 5.O-O Be7 6. Re1 b5 7. Bb3 d6 8. c3 O-O 9. h3 Nb8 "
    "10. d4 Nbd7 11. Nbd2 Bb7 12. Bc2 Re8 13. Nf1 Bf8 14. Ng3 g6 15. a4 c5 16. d5 c4 17. Bg5 h6 "
    "18. Be3 Nc5 19. Qd2 h5 20. Bg5 Be7 21. Ra1"
)


class SpanRefTests(unittest.TestCase):
    def test_unchanged_text_keeps_the_span(self):
        self.assertEqual((3, 5), SpanRef("1. e4 e5", 3, 5).locate("1. e4 e5"))

    def test_an_edit_before_the_span_shifts_it(self):
        self.assertEqual((8, 10), SpanRef("1. e4 e5", 6, 8).locate("1. e4!? e5"))

    def test_an_edit_after_the_span_does_not_move_it(self):
        self.assertEqual((3, 5), SpanRef("1. e4 e5", 3, 5).locate("1. e4 e5 2. Nf3"))

    def test_an_edit_inside_the_span_is_refused(self):
        self.assertIsNone(SpanRef("1. e4 e5", 3, 5).locate("1. e3 e5"))
        self.assertIsNone(SpanRef("1. e4 e5", 3, 5).locate("1. ex4 e5"))

    def test_expected_is_the_origin_slice(self):
        self.assertEqual("e4", SpanRef("1. e4 e5", 3, 5).expected)


class IssueGestureTests(unittest.TestCase):
    def setUp(self):
        from pgn_live_editor.ui.main_window import MainWindow

        self.app = QApplication.instance() or QApplication([])
        self.window = MainWindow()
        self.addCleanup(self.window.deleteLater)
        self.addCleanup(self.window.editor_panel.shutdown)
        self.editor = self.window.editor_panel
        self.editor.load_document_text(RUY)
        self.issue = next(i for i in self.editor.current_ast.issues if i.raw_text == "Ra1")

    def type_before_the_alert(self, text: str = " ") -> None:
        cursor = self.editor.text_edit.textCursor()
        cursor.setPosition(RUY.index("5.O-O"))
        cursor.insertText(text)

    def test_accepting_after_typing_before_the_alert_edits_the_right_place(self):
        self.type_before_the_alert()

        self.window._accept_issue(self.issue)

        text = self.editor.document_text()
        self.assertIn(" 5.O-O Be7 ", text)
        self.assertTrue(text.endswith("21. Ra2"), text[-20:])

    def test_commenting_after_typing_before_the_alert_wraps_the_right_place(self):
        self.type_before_the_alert()

        self.window._comment_issue(self.issue)

        self.assertTrue(self.editor.document_text().endswith("21. {Ra1}"))

    def test_a_changed_span_is_not_edited(self):
        cursor = self.editor.text_edit.textCursor()
        cursor.setPosition(len(RUY) - 1)
        cursor.insertText("x")
        before = self.editor.document_text()

        self.window._accept_issue(self.issue)

        self.assertEqual(before, self.editor.document_text())
        self.assertIn("mudou", self.window.statusBar().currentMessage())


class StructuralGestureTests(unittest.TestCase):
    TEXT = "1. e4 e5 2. Nf3 (2. Bc4 Nf6) 2... Nc6"

    def promoted(self, text: str, *, typed_before: str = "") -> str:
        from pgn_live_editor.ui.main_window import MainWindow

        self.app = QApplication.instance() or QApplication([])
        window = MainWindow()
        self.addCleanup(window.deleteLater)
        self.addCleanup(window.editor_panel.shutdown)
        editor = window.editor_panel
        editor.load_document_text(text)
        cursor = editor.text_edit.textCursor()
        cursor.setPosition(text.index("Bc4"))
        editor.text_edit.setTextCursor(cursor)
        if typed_before:
            # Digitado no comeco, sem esperar a analise.
            start = editor.text_edit.textCursor()
            start.setPosition(0)
            start.insertText(typed_before)
        window._promote_variation()
        return editor.document_text()

    def test_promoting_after_typing_uses_the_current_tree(self):
        expected = self.promoted("{Ruy} " + self.TEXT)
        self.assertNotEqual("{Ruy} " + self.TEXT, expected, "o gesto precisa mudar algo")

        self.assertEqual(expected, self.promoted(self.TEXT, typed_before="{Ruy} "))


class IssueSelectionTests(unittest.TestCase):
    """U-15: a selecao do painel ficava no indice; depois da reanalise, Aceitar caia noutro alerta."""

    def setUp(self):
        from pgn_live_editor.ui.issues_panel import IssuesPanel

        self.app = QApplication.instance() or QApplication([])
        self.panel = IssuesPanel()
        self.addCleanup(self.panel.deleteLater)

    @staticmethod
    def issue(text: str, start: int):
        from pgn_live_editor.core.issues import UNRECOGNIZED_MOVE, Issue

        return Issue("error", UNRECOGNIZED_MOVE, f"'{text}'", start, start + len(text), text)

    def test_the_selection_follows_the_issue_not_the_row(self):
        self.panel.set_issues([self.issue("Xa1", 0), self.issue("Xb2", 10)])
        self.panel.table.selectRow(1)

        self.panel.set_issues([self.issue("Xc3", 5), self.issue("Xa1", 8), self.issue("Xb2", 12)])

        self.assertEqual("Xb2", self.panel.selected_issue().raw_text)

    def test_a_vanished_issue_leaves_nothing_selected(self):
        self.panel.set_issues([self.issue("Xa1", 0), self.issue("Xb2", 10)])
        self.panel.table.selectRow(1)

        self.panel.set_issues([self.issue("Xa1", 0), self.issue("Xc3", 10)])

        self.assertIsNone(self.panel.selected_issue())


class ReviewSelectionTests(unittest.TestCase):
    def test_the_review_selection_follows_the_record_not_the_row(self):
        from pgn_live_editor.core.recognition_record import RecognitionRecord, RecordSource
        from pgn_live_editor.ui.review_panel import ReviewPanel

        self.app = QApplication.instance() or QApplication([])
        panel = ReviewPanel()
        self.addCleanup(panel.deleteLater)

        def record(source_id: str, start: int) -> RecognitionRecord:
            return RecognitionRecord(source_id, "Xa1", "Xa1", "MOVE_CANDIDATE", "low", RecordSource(start, start + 3))

        panel.set_records([record("a", 0), record("b", 10)])
        panel.table.selectRow(1)

        panel.set_records([record("c", 5), record("b", 12)])
        self.assertEqual(["b"], [r.source_id for r in panel.selected_records()])

        panel.set_records([record("c", 5), record("d", 12)])
        self.assertEqual([], panel.selected_records())


if __name__ == "__main__":
    unittest.main()
