"""O resultado do OCR de diagramas vai para onde foi pedido (R-306).

U-14: o resultado era inserido no cursor e no PDF do momento em que
**chegava**: trocar de PDF com o OCR em andamento punha os diagramas do livro
A no texto do livro B, gravados como sendo do B. E um duplo clique num
diagrama durante a leitura do botao substituia o pedido -- o lote sumia.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # noqa: F401
from pdf_fixture import write_pdf
from PyQt6.QtWidgets import QApplication, QMessageBox

from pgn_live_editor.services.diagram_ocr import DiagramRecognition
from pgn_live_editor.ui.main_window import MainWindow

_app = QApplication.instance() or QApplication([])

START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR"


def diagram(index: int = 1, bbox=(10.0, 10.0, 200.0, 200.0)) -> DiagramRecognition:
    return DiagramRecognition(
        index=index,
        placement=START,
        side_to_move="w",
        fen=f"{START} w KQkq - 0 1",
        min_confidence=0.99,
        mean_confidence=0.99,
        uncertain_squares=(),
        rotation=0,
        orientation_ambiguous=False,
        is_legal=True,
        problems=(),
        bbox_pdf=bbox,
        detection_source="teste",
        source_root="",
    )


class DiagramDestinationTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        tmp = Path(self._temp.name)
        self.book_a = write_pdf(tmp / "a.pdf", [["Diagrama 1"], ["Diagrama 2"]])
        self.book_b = write_pdf(tmp / "b.pdf", [["Outro livro"]])
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
            self.window = MainWindow()
        self.addCleanup(self.window.deleteLater)
        self.addCleanup(self.window.pdf_panel.close_pdf)
        self.requests = mock.patch.object(self.window.diagram_ocr, "request", return_value=1).start()
        self.addCleanup(mock.patch.stopall)
        self.window.pdf_panel.open_pdf(self.book_a)
        self.window.editor_panel.load_document_text("1. e4 e5")

    def arrive(self, *results: DiagramRecognition, page: int = 0, answer=QMessageBox.StandardButton.No):
        with mock.patch.object(QMessageBox, "question", return_value=answer) as asked:
            self.window._on_diagrams_read((page, list(results)))
        return asked

    def test_switching_pdf_while_reading_inserts_nothing_without_asking(self):
        self.window._read_diagrams_from_pdf()
        self.window.pdf_panel.open_pdf(self.book_b)

        asked = self.arrive(diagram())

        self.assertTrue(asked.called)
        self.assertEqual("1. e4 e5", self.window.editor_panel.document_text())
        self.assertEqual([], self.window.project.state.diagram_records)

    def test_confirmed_results_are_recorded_as_from_the_pdf_that_was_read(self):
        self.window._read_diagrams_from_pdf()
        self.window.pdf_panel.open_pdf(self.book_b)

        self.arrive(diagram(), answer=QMessageBox.StandardButton.Yes)

        self.assertIn("FEN", self.window.editor_panel.document_text())
        recorded = self.window.project.state.diagram_records
        self.assertEqual(["a.pdf"], [Path(str(record["pdf_path"])).name for record in recorded])

    def test_the_same_pdf_inserts_where_the_cursor_was_when_asked(self):
        editor = self.window.editor_panel
        cursor = editor.text_edit.textCursor()
        cursor.setPosition(len("1. e4 e5"))
        editor.text_edit.setTextCursor(cursor)
        self.window._read_diagrams_from_pdf()
        # Enquanto o OCR roda, o usuario vai ao comeco e digita.
        cursor.setPosition(0)
        editor.text_edit.setTextCursor(cursor)
        cursor.insertText("{nota} ")

        asked = self.arrive(diagram())

        self.assertFalse(asked.called)
        text = self.window.editor_panel.document_text()
        self.assertTrue(text.startswith("{nota} 1. e4 e5"), text[:40])
        self.assertIn("FEN", text)

    def test_a_click_during_the_button_reading_keeps_the_batch(self):
        self.window._read_diagrams_from_pdf()
        self.requests.reset_mock()
        with mock.patch.object(type(self.window.diagram_ocr), "busy", new_callable=mock.PropertyMock) as busy:
            busy.return_value = True
            self.window._on_diagram_clicked(0, 50.0, 50.0)

        self.assertFalse(self.requests.called, "o clique nao pode substituir a leitura em andamento")
        with mock.patch.object(self.window, "_open_diagram_record") as opened:
            self.arrive(diagram(1), diagram(2, bbox=(300.0, 300.0, 400.0, 400.0)))

        self.assertEqual(2, self.window.editor_panel.document_text().count("FEN"))
        self.assertEqual(1, opened.call_count)


if __name__ == "__main__":
    unittest.main()
