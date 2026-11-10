"""O fim do reconhecimento do PDF inteiro pergunta antes de mexer no texto (U-04 / R-106).

Antes ele trocava o documento inteiro por `setPlainText` -- sem perguntar e
apagando o Ctrl+Z -- mesmo com o usuario editando enquanto o reconhecimento
corria.
"""

from __future__ import annotations

import unittest
from unittest import mock

import _isolation  # noqa: F401
from PyQt6.QtWidgets import QApplication, QMessageBox

from pgn_live_editor.ui.main_window import MainWindow

PAGES = [
    {
        "page": 3,
        "text": "1. e4 e5 2. Nf3 Nc6",
        "diagrams": [
            {
                "diagram": 0,
                "page": 3,
                "fen": "8/8/8/8/8/8/8/K6k w - - 0 1",
                "review": "pending",
                "min_confidence": 0.5,
                "source_id": "abc",
            }
        ],
    }
]


class FullPdfResultTests(unittest.TestCase):
    def setUp(self):
        self.app = QApplication.instance() or QApplication([])
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
            self.window = MainWindow()
        self.addCleanup(self.window.deleteLater)
        self.window.editor_panel.load_document_text("1. d4 {o que eu estava editando}")

    def deliver(self, choice: str):
        with mock.patch.object(MainWindow, "_ask_full_pdf_destination", return_value=choice) as asked:
            self.window._on_full_pdf_recognized(PAGES)
        return asked

    def test_it_asks_when_there_is_text_in_the_editor(self):
        asked = self.deliver("cancel")

        asked.assert_called_once()

    def test_cancelling_keeps_the_text_and_the_project(self):
        self.deliver("cancel")

        self.assertEqual("1. d4 {o que eu estava editando}", self.window.editor_panel.document_text())
        self.assertEqual([], self.window.project.state.diagram_records)

    def test_replacing_can_be_undone(self):
        self.deliver("replace")
        self.assertIn("1. e4 e5 2. Nf3 Nc6", self.window.editor_panel.document_text())
        self.assertEqual(1, len(self.window.project.state.diagram_records))

        self.window.editor_panel.text_edit.undo()

        self.assertEqual("1. d4 {o que eu estava editando}", self.window.editor_panel.document_text())

    def test_a_new_project_gets_the_result_and_its_diagrams(self):
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Discard):
            self.deliver("new")

        self.assertIn("1. e4 e5 2. Nf3 Nc6", self.window.editor_panel.document_text())
        self.assertEqual(1, len(self.window.project.state.diagram_records))
        self.assertTrue(self.window._is_dirty(), "o resultado ainda nao foi salvo")

    def test_an_empty_editor_takes_the_result_without_asking(self):
        self.window.editor_panel.load_document_text("")
        self.window.project.raw_text = ""

        asked = self.deliver("cancel")

        asked.assert_not_called()
        self.assertIn("1. e4 e5 2. Nf3 Nc6", self.window.editor_panel.document_text())


if __name__ == "__main__":
    unittest.main()
